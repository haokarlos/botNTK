import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


DATABASE_URL = os.getenv("DATABASE_URL")
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

app = FastAPI(title="BotNTK API", version="0.1.0")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def get_database_url():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required to run the API.")
    return DATABASE_URL


@contextmanager
def get_conn():
    conn = psycopg.connect(get_database_url())
    try:
        yield conn
    finally:
        conn.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/game")
def game_page():
    return FileResponse(WEB_DIR / "game.html")


@app.get("/storefronts")
def list_storefronts():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select sf.slug, sf.name, sf.region, sf.device_type, p.slug as platform_slug
                from storefronts sf
                join platforms p on p.id = sf.platform_id
                where sf.is_active = true
                order by p.slug asc, sf.name asc
                """
            )
            rows = cur.fetchall()

    return {
        "storefronts": [
            {
                "slug": row[0],
                "name": row[1],
                "region": row[2],
                "device_type": row[3],
                "platform_slug": row[4],
            }
            for row in rows
        ]
    }


@app.get("/leaderboards")
def get_leaderboard(
    storefront: str = Query(..., description="Storefront slug"),
    view: str = Query("current", pattern="^(current|avg7|avg30)$"),
    limit: int = Query(20, ge=1, le=100),
):
    window_days = {"current": 1, "avg7": 7, "avg30": 30}[view]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                with target_storefront as (
                    select id, slug, name
                    from storefronts
                    where slug = %s
                ),
                latest_snapshot as (
                    select max(rs.capture_date) as latest_date
                    from ranking_snapshots rs
                    join target_storefront ts on ts.id = rs.storefront_id
                ),
                bounds as (
                    select
                        latest_date,
                        (latest_date - (%s::int - 1) * interval '1 day')::date as current_start,
                        (latest_date - (%s::int * 2 - 1) * interval '1 day')::date as previous_start,
                        (latest_date - %s::int * interval '1 day')::date as previous_end
                    from latest_snapshot
                ),
                current_rows as (
                    select
                        g.id as game_id,
                        g.canonical_name,
                        ga.title as alias_title,
                        avg(re.rank)::numeric(10, 2) as metric_value
                    from ranking_entries re
                    join ranking_snapshots rs on rs.id = re.snapshot_id
                    join game_aliases ga on ga.id = re.game_alias_id
                    join games g on g.id = ga.game_id
                    join target_storefront ts on ts.id = rs.storefront_id
                    join bounds b on true
                    where rs.capture_date between b.current_start and b.latest_date
                    group by g.id, g.canonical_name, ga.title
                ),
                game_first_seen as (
                    select
                        g.id as game_id,
                        min(rs.capture_date) as first_seen_date
                    from ranking_entries re
                    join ranking_snapshots rs on rs.id = re.snapshot_id
                    join game_aliases ga on ga.id = re.game_alias_id
                    join games g on g.id = ga.game_id
                    join target_storefront ts on ts.id = rs.storefront_id
                    group by g.id
                ),
                previous_rows as (
                    select
                        g.id as game_id,
                        avg(re.rank)::numeric(10, 2) as metric_value
                    from ranking_entries re
                    join ranking_snapshots rs on rs.id = re.snapshot_id
                    join game_aliases ga on ga.id = re.game_alias_id
                    join games g on g.id = ga.game_id
                    join target_storefront ts on ts.id = rs.storefront_id
                    join bounds b on true
                    where rs.capture_date between b.previous_start and b.previous_end
                    group by g.id
                ),
                current_ranked as (
                    select
                        cr.*,
                        row_number() over (order by cr.metric_value asc, cr.canonical_name asc) as position
                    from current_rows cr
                ),
                previous_ranked as (
                    select
                        pr.*,
                        row_number() over (order by pr.metric_value asc, pr.game_id asc) as position
                    from previous_rows pr
                )
                select
                    ts.slug,
                    ts.name,
                    b.latest_date,
                    cr.position,
                    cr.game_id,
                    cr.canonical_name,
                    cr.alias_title,
                    cr.metric_value,
                    pr.position as previous_position,
                    gfs.first_seen_date
                from current_ranked cr
                join target_storefront ts on true
                join bounds b on true
                join game_first_seen gfs on gfs.game_id = cr.game_id
                left join previous_ranked pr on pr.game_id = cr.game_id
                order by cr.position asc
                limit %s
                """,
                (storefront, window_days, window_days, window_days, limit),
            )
            rows = cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No leaderboard found for that storefront.")

    return {
        "storefront": {"slug": rows[0][0], "name": rows[0][1]},
        "latest_date": str(rows[0][2]),
        "view": view,
        "entries": [
            {
                "position": row[3],
                "game_id": str(row[4]),
                "canonical_name": row[5],
                "alias_title": row[6],
                "metric_value": float(row[7]) if row[7] is not None else None,
                "previous_position": row[8],
                "is_new": row[9] is not None and (rows[0][2] - row[9]).days <= 30,
                "movement": None if row[8] is None else row[8] - row[3],
            }
            for row in rows
        ],
    }


@app.get("/rankings/current")
def get_current_rankings(
    storefront: str = Query(..., description="Storefront slug, e.g. nutaku-browser-ranking"),
    limit: int = Query(50, ge=1, le=200),
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                with latest_snapshot as (
                    select
                        rs.id,
                        rs.capture_date,
                        rs.data_source,
                        rs.notes,
                        sf.slug as storefront_slug,
                        sf.name as storefront_name
                    from ranking_snapshots rs
                    join storefronts sf on sf.id = rs.storefront_id
                    where sf.slug = %s
                    order by rs.capture_date desc, rs.captured_at desc
                    limit 1
                )
                select
                    ls.capture_date,
                    ls.data_source,
                    ls.notes,
                    ls.storefront_slug,
                    ls.storefront_name,
                    re.rank,
                    g.id as game_id,
                    g.canonical_name,
                    ga.id as game_alias_id,
                    ga.title as alias_title
                from latest_snapshot ls
                join ranking_entries re on re.snapshot_id = ls.id
                join game_aliases ga on ga.id = re.game_alias_id
                join games g on g.id = ga.game_id
                order by re.rank asc
                limit %s
                """,
                (storefront, limit),
            )
            rows = cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No rankings found for that storefront.")

    capture_date = rows[0][0]
    data_source = rows[0][1]
    notes = rows[0][2]
    storefront_slug = rows[0][3]
    storefront_name = rows[0][4]

    return {
        "storefront": {"slug": storefront_slug, "name": storefront_name},
        "capture_date": str(capture_date),
        "data_source": data_source,
        "notes": notes,
        "entries": [
            {
                "rank": row[5],
                "game_id": str(row[6]),
                "canonical_name": row[7],
                "game_alias_id": str(row[8]),
                "alias_title": row[9],
            }
            for row in rows
        ],
    }


@app.get("/games/search")
def search_games(
    q: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=100),
):
    normalized_query = f"%{' '.join(q.casefold().split())}%"
    raw_query = f"%{q.strip()}%"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    g.id,
                    g.canonical_name,
                    min(ga.title) as example_alias,
                    count(distinct ga.platform_id) as platform_count
                from games g
                left join game_aliases ga on ga.game_id = g.id
                where g.canonical_name ilike %s
                   or ga.title ilike %s
                   or g.canonical_name_normalized like %s
                   or ga.title_normalized like %s
                group by g.id, g.canonical_name
                order by g.canonical_name asc
                limit %s
                """,
                (raw_query, raw_query, normalized_query, normalized_query, limit),
            )
            rows = cur.fetchall()

    return {
        "query": q,
        "results": [
            {
                "game_id": str(row[0]),
                "canonical_name": row[1],
                "example_alias": row[2],
                "platform_count": row[3],
            }
            for row in rows
        ],
    }


@app.get("/games/{game_id}")
def get_game_summary(game_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                with game_stats as (
                    select
                        g.id,
                        g.canonical_name,
                        min(re.rank) as best_rank,
                        avg(re.rank)::numeric(10, 2) as observed_avg_rank_overall,
                        count(*) as ranking_points,
                        min(rs.capture_date) as first_seen_date,
                        max(rs.capture_date) as last_seen_date
                    from games g
                    left join game_aliases ga on ga.game_id = g.id
                    left join ranking_entries re on re.game_alias_id = ga.id
                    left join ranking_snapshots rs on rs.id = re.snapshot_id
                    where g.id = %s
                    group by g.id, g.canonical_name
                )
                select
                    id,
                    canonical_name,
                    best_rank,
                    observed_avg_rank_overall,
                    ranking_points,
                    first_seen_date,
                    last_seen_date
                from game_stats
                """,
                (game_id,),
            )
            game_row = cur.fetchone()

            if not game_row:
                raise HTTPException(status_code=404, detail="Game not found.")

            cur.execute(
                """
                with game_storefronts as (
                    select distinct
                        sf.id as storefront_id,
                        sf.slug as storefront_slug,
                        sf.name as storefront_name,
                        case
                            when sf.slug = 'erolabs-home-ranking' then 25
                            else 60
                        end as penalty_rank
                    from game_aliases ga
                    join storefronts sf on sf.id = ga.storefront_id
                    where ga.game_id = %s
                ),
                storefront_ranges as (
                    select
                        gs.storefront_id,
                        gs.storefront_slug,
                        gs.storefront_name,
                        gs.penalty_rank,
                        min(rs.capture_date) as first_seen_date,
                        (
                            select max(rs2.capture_date)
                            from ranking_snapshots rs2
                            where rs2.storefront_id = gs.storefront_id
                        ) as last_snapshot_date
                    from game_storefronts gs
                    join game_aliases ga on ga.storefront_id = gs.storefront_id and ga.game_id = %s
                    join ranking_entries re on re.game_alias_id = ga.id
                    join ranking_snapshots rs on rs.id = re.snapshot_id
                    group by gs.storefront_id, gs.storefront_slug, gs.storefront_name, gs.penalty_rank
                ),
                storefront_calendar as (
                    select
                        sr.storefront_id,
                        sr.storefront_slug,
                        sr.storefront_name,
                        sr.penalty_rank,
                        sr.first_seen_date,
                        sr.last_snapshot_date,
                        generate_series(sr.first_seen_date, sr.last_snapshot_date, interval '1 day')::date as metric_date
                    from storefront_ranges sr
                ),
                observed_ranks as (
                    select
                        sf.id as storefront_id,
                        rs.capture_date,
                        min(re.rank) as rank
                    from ranking_entries re
                    join ranking_snapshots rs on rs.id = re.snapshot_id
                    join game_aliases ga on ga.id = re.game_alias_id
                    join storefronts sf on sf.id = rs.storefront_id
                    where ga.game_id = %s
                    group by sf.id, rs.capture_date
                ),
                storefront_metrics as (
                    select
                        sc.storefront_slug,
                        sc.storefront_name,
                        sc.first_seen_date,
                        sc.last_snapshot_date,
                        count(*) as tracked_days,
                        count(orx.rank) as observed_days,
                        avg(orx.rank)::numeric(10, 2) as observed_avg_rank,
                        avg(coalesce(orx.rank, sc.penalty_rank))::numeric(10, 2) as adjusted_avg_rank
                    from storefront_calendar sc
                    left join observed_ranks orx
                        on orx.storefront_id = sc.storefront_id
                       and orx.capture_date = sc.metric_date
                    group by sc.storefront_slug, sc.storefront_name, sc.first_seen_date, sc.last_snapshot_date
                ),
                overall_metrics as (
                    select
                        sum(observed_days) as observed_days,
                        sum(tracked_days) as tracked_days,
                        case
                            when sum(tracked_days) = 0 then null
                            else round(sum(observed_days)::numeric / sum(tracked_days)::numeric, 4)
                        end as coverage_ratio,
                        case
                            when sum(observed_days) = 0 then null
                            else round(sum(observed_avg_rank * observed_days)::numeric / sum(observed_days)::numeric, 2)
                        end as observed_avg_rank_overall,
                        case
                            when sum(tracked_days) = 0 then null
                            else round(sum(adjusted_avg_rank * tracked_days)::numeric / sum(tracked_days)::numeric, 2)
                        end as adjusted_avg_rank_overall
                    from storefront_metrics
                )
                select
                    observed_days,
                    tracked_days,
                    coverage_ratio,
                    observed_avg_rank_overall,
                    adjusted_avg_rank_overall
                from overall_metrics
                """,
                (game_id, game_id, game_id),
            )
            overall_metrics = cur.fetchone()

            cur.execute(
                """
                select distinct
                    sf.slug,
                    sf.name,
                    ga.title
                from game_aliases ga
                join storefronts sf on sf.id = ga.storefront_id
                where ga.game_id = %s
                order by sf.name asc, ga.title asc
                """,
                (game_id,),
            )
            aliases = cur.fetchall()

    return {
        "game_id": str(game_row[0]),
        "canonical_name": game_row[1],
        "best_rank": game_row[2],
        "observed_avg_rank_overall": float(overall_metrics[3]) if overall_metrics and overall_metrics[3] is not None else None,
        "adjusted_avg_rank_overall": float(overall_metrics[4]) if overall_metrics and overall_metrics[4] is not None else None,
        "ranking_points": game_row[4],
        "first_seen_date": str(game_row[5]) if game_row[5] else None,
        "last_seen_date": str(game_row[6]) if game_row[6] else None,
        "observed_days": overall_metrics[0] if overall_metrics else 0,
        "tracked_days": overall_metrics[1] if overall_metrics else 0,
        "coverage_ratio": float(overall_metrics[2]) if overall_metrics and overall_metrics[2] is not None else None,
        "aliases": [
            {"storefront_slug": row[0], "storefront_name": row[1], "alias_title": row[2]}
            for row in aliases
        ],
    }


@app.get("/games/{game_id}/history")
def get_game_history(game_id: str, storefront: str | None = Query(default=None)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, canonical_name
                from games
                where id = %s
                """,
                (game_id,),
            )
            game_row = cur.fetchone()

            if not game_row:
                raise HTTPException(status_code=404, detail="Game not found.")

            if storefront:
                cur.execute(
                    """
                    select
                        rs.capture_date,
                        sf.slug,
                        re.rank,
                        ga.title,
                        rs.data_source,
                        rs.notes
                    from ranking_entries re
                    join ranking_snapshots rs on rs.id = re.snapshot_id
                    join storefronts sf on sf.id = rs.storefront_id
                    join game_aliases ga on ga.id = re.game_alias_id
                    where ga.game_id = %s
                      and sf.slug = %s
                    order by rs.capture_date asc, re.rank asc
                    """,
                    (game_id, storefront),
                )
            else:
                cur.execute(
                    """
                    select
                        rs.capture_date,
                        sf.slug,
                        re.rank,
                        ga.title,
                        rs.data_source,
                        rs.notes
                    from ranking_entries re
                    join ranking_snapshots rs on rs.id = re.snapshot_id
                    join storefronts sf on sf.id = rs.storefront_id
                    join game_aliases ga on ga.id = re.game_alias_id
                    where ga.game_id = %s
                    order by rs.capture_date asc, sf.slug asc, re.rank asc
                    """,
                    (game_id,),
                )
            rows = cur.fetchall()

    return {
        "game_id": str(game_row[0]),
        "canonical_name": game_row[1],
        "history": [
            {
                "capture_date": str(row[0]),
                "storefront": row[1],
                "rank": row[2],
                "alias_title": row[3],
                "data_source": row[4],
                "notes": row[5],
            }
            for row in rows
        ],
    }
