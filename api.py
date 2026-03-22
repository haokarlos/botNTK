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

STOREFRONT_DISPLAY_NAMES = {
    "nutaku-all-games": "Nutaku",
    "erolabs-home-ranking": "EroLabs",
    "nutaku-browser-ranking": "Nutaku PC",
    "nutaku-mobile-ranking": "Nutaku Android",
}


def get_database_url():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required to run the API.")
    return DATABASE_URL


def display_storefront_name(slug: str, fallback: str | None = None) -> str:
    return STOREFRONT_DISPLAY_NAMES.get(slug, fallback or slug)


def maybe_float(value):
    return float(value) if value is not None else None


def normalize_taxonomy_value(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.split()).strip(" ,;:")
    return cleaned or None


def normalize_taxonomy_list(values):
    deduped = []
    seen = set()
    for value in values or []:
        normalized = normalize_taxonomy_value(value)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def platform_label_for_storefront_slug(slug: str) -> str | None:
    return {
        "nutaku-all-games": "Browser",
        "nutaku-browser-ranking": "Browser",
        "nutaku-mobile-ranking": "Android",
        "erolabs-home-ranking": None,
    }.get(slug)


def derive_platforms_for_game_aliases(slugs):
    platforms = []
    seen = set()
    for slug in slugs:
        label = platform_label_for_storefront_slug(slug)
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        platforms.append(label)
    return platforms


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


@app.get("/storefront")
def storefront_page():
    return FileResponse(WEB_DIR / "storefront.html")


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
                "name": display_storefront_name(row[0], row[1]),
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
    publisher: str | None = Query(default=None, description="Publisher text filter"),
    genre: str | None = Query(default=None, description="Genre text filter"),
    tag: str | None = Query(default=None, description="Tag text filter"),
    platform: str | None = Query(default=None, description="Platform text filter"),
):
    window_days = {"current": 1, "avg7": 7, "avg30": 30}[view]
    publisher_filter = publisher.strip() if publisher and publisher.strip() else None
    genre_filter = genre.strip() if genre and genre.strip() else None
    tag_filter = tag.strip() if tag and tag.strip() else None
    platform_filter = platform.strip() if platform and platform.strip() else None

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
                metadata_candidates as (
                    select distinct on (ga.game_id)
                        ga.game_id,
                        array_remove(
                            array[
                                case
                                    when exists (
                                        select 1
                                        from game_aliases gax
                                        join storefronts sfx on sfx.id = gax.storefront_id
                                        where gax.game_id = ga.game_id
                                          and sfx.slug in ('nutaku-all-games', 'nutaku-browser-ranking')
                                    ) then 'Browser'
                                    else null
                                end,
                                case
                                    when exists (
                                        select 1
                                        from game_aliases gax
                                        join storefronts sfx on sfx.id = gax.storefront_id
                                        where gax.game_id = ga.game_id
                                          and sfx.slug = 'nutaku-mobile-ranking'
                                    ) then 'Android'
                                    else null
                                end
                            ],
                            null
                        ) as platforms,
                        coalesce(gms.publisher, ga.publisher_raw) as publisher,
                        coalesce(gms.genres, '[]'::jsonb) as genres,
                        coalesce(gms.tags, '[]'::jsonb) as tags
                    from current_rows cr
                    join game_aliases ga on ga.game_id = cr.game_id
                    join storefronts sf on sf.id = ga.storefront_id
                    join target_storefront ts on true
                    left join lateral (
                        select
                            gms.captured_at,
                            gms.publisher,
                            gms.genres,
                            gms.tags
                        from game_metadata_snapshots gms
                        where gms.game_alias_id = ga.id
                        order by gms.captured_at desc
                        limit 1
                    ) gms on true
                    where (
                        (ts.slug like 'nutaku-%%' and sf.slug like 'nutaku-%%')
                        or sf.slug = ts.slug
                    )
                    order by
                        ga.game_id,
                        case when gms.publisher is not null then 0 else 1 end,
                        case when jsonb_array_length(coalesce(gms.genres, '[]'::jsonb)) > 0 then 0 else 1 end,
                        case when jsonb_array_length(coalesce(gms.tags, '[]'::jsonb)) > 0 then 0 else 1 end,
                        gms.captured_at desc nulls last,
                        ga.updated_at desc
                ),
                filtered_rows as (
                    select
                        cr.*,
                        mc.publisher,
                        mc.platforms,
                        mc.genres,
                        mc.tags
                    from current_rows cr
                    left join metadata_candidates mc on mc.game_id = cr.game_id
                    where (%s::text is null or coalesce(mc.publisher, '') ilike '%%' || %s::text || '%%')
                      and (
                        %s::text is null
                        or exists (
                            select 1
                            from unnest(coalesce(mc.platforms, array[]::text[])) platform_value(value)
                            where platform_value.value ilike '%%' || %s::text || '%%'
                        )
                      )
                      and (
                        %s::text is null
                        or exists (
                            select 1
                            from jsonb_array_elements_text(coalesce(mc.genres, '[]'::jsonb)) genre_value(value)
                            where genre_value.value ilike '%%' || %s::text || '%%'
                        )
                      )
                      and (
                        %s::text is null
                        or exists (
                            select 1
                            from jsonb_array_elements_text(coalesce(mc.tags, '[]'::jsonb)) tag_value(value)
                            where tag_value.value ilike '%%' || %s::text || '%%'
                        )
                      )
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
                        fr.*,
                        row_number() over (order by fr.metric_value asc, fr.canonical_name asc) as position
                    from filtered_rows fr
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
                (
                    storefront,
                    window_days,
                    window_days,
                    window_days,
                    publisher_filter,
                    publisher_filter,
                    platform_filter,
                    platform_filter,
                    genre_filter,
                    genre_filter,
                    tag_filter,
                    tag_filter,
                    limit,
                ),
            )
            rows = cur.fetchall()

    if not rows:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select slug, name from storefronts where slug = %s", (storefront,))
                storefront_row = cur.fetchone()
        if not storefront_row:
            raise HTTPException(status_code=404, detail="No leaderboard found for that storefront.")
        return {
            "storefront": {"slug": storefront_row[0], "name": display_storefront_name(storefront_row[0], storefront_row[1])},
            "latest_date": None,
            "view": view,
            "entries": [],
        }

    return {
        "storefront": {"slug": rows[0][0], "name": display_storefront_name(rows[0][0], rows[0][1])},
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


@app.get("/leaderboard-facets")
def get_leaderboard_facets(
    storefront: str = Query(..., description="Storefront slug"),
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                with target_storefront as (
                    select id, slug
                    from storefronts
                    where slug = %s
                ),
                metadata_candidates as (
                    select distinct on (ga.game_id)
                        ga.game_id,
                        array_remove(
                            array[
                                case
                                    when exists (
                                        select 1
                                        from game_aliases gax
                                        join storefronts sfx on sfx.id = gax.storefront_id
                                        where gax.game_id = ga.game_id
                                          and sfx.slug in ('nutaku-all-games', 'nutaku-browser-ranking')
                                    ) then 'Browser'
                                    else null
                                end,
                                case
                                    when exists (
                                        select 1
                                        from game_aliases gax
                                        join storefronts sfx on sfx.id = gax.storefront_id
                                        where gax.game_id = ga.game_id
                                          and sfx.slug = 'nutaku-mobile-ranking'
                                    ) then 'Android'
                                    else null
                                end
                            ],
                            null
                        ) as platforms,
                        coalesce(gms.publisher, ga.publisher_raw) as publisher,
                        coalesce(gms.genres, '[]'::jsonb) as genres,
                        coalesce(gms.tags, '[]'::jsonb) as tags
                    from game_aliases ga
                    join storefronts sf on sf.id = ga.storefront_id
                    join target_storefront ts on true
                    left join lateral (
                        select
                            gms.captured_at,
                            gms.publisher,
                            gms.genres,
                            gms.tags
                        from game_metadata_snapshots gms
                        where gms.game_alias_id = ga.id
                        order by gms.captured_at desc
                        limit 1
                    ) gms on true
                    where (
                        (ts.slug like 'nutaku-%%' and sf.slug like 'nutaku-%%')
                        or sf.slug = ts.slug
                    )
                    order by
                        ga.game_id,
                        case when gms.publisher is not null then 0 else 1 end,
                        case when jsonb_array_length(coalesce(gms.genres, '[]'::jsonb)) > 0 then 0 else 1 end,
                        case when jsonb_array_length(coalesce(gms.tags, '[]'::jsonb)) > 0 then 0 else 1 end,
                        gms.captured_at desc nulls last,
                        ga.updated_at desc
                ),
                publishers as (
                    select distinct publisher
                    from metadata_candidates
                    where publisher is not null and publisher <> ''
                ),
                platforms as (
                    select distinct trim(value) as value
                    from metadata_candidates mc,
                    lateral unnest(coalesce(mc.platforms, array[]::text[])) platform(value)
                    where trim(value) <> ''
                ),
                genres as (
                    select distinct trim(value) as value
                    from metadata_candidates mc,
                    lateral jsonb_array_elements_text(mc.genres) genre(value)
                    where trim(value) <> ''
                ),
                tags as (
                    select distinct trim(value) as value
                    from metadata_candidates mc,
                    lateral jsonb_array_elements_text(mc.tags) tag(value)
                    where trim(value) <> ''
                )
                select
                    array(select publisher from publishers order by publisher asc),
                    array(select value from platforms order by value asc),
                    array(select value from genres order by value asc),
                    array(select value from tags order by value asc)
                """
                ,
                (storefront,),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No facets found for that storefront.")

    publishers = normalize_taxonomy_list(row[0] or [])
    platforms = normalize_taxonomy_list(row[1] or [])
    genres = normalize_taxonomy_list(row[2] or [])
    tags = normalize_taxonomy_list(row[3] or [])

    return {
        "storefront": storefront,
        "publishers": publishers,
        "platforms": platforms,
        "genres": genres,
        "tags": tags,
    }


@app.get("/market-trends")
def get_market_trends(
    storefront: str = Query("nutaku-all-games", description="Storefront slug"),
    window: int = Query(7, ge=7, le=90),
    limit: int = Query(8, ge=1, le=20),
    mode: str = Query("all", pattern="^(all|main)$"),
    top_limit: int = Query(20, ge=5, le=50),
    publisher: str | None = Query(default=None, description="Publisher text filter"),
    genre: str | None = Query(default=None, description="Genre text filter"),
    tag: str | None = Query(default=None, description="Tag text filter"),
    platform: str | None = Query(default=None, description="Platform text filter"),
):
    if window not in {7, 30, 90}:
        raise HTTPException(status_code=400, detail="window must be one of 7, 30, or 90")
    if top_limit not in {5, 10, 20, 50}:
        raise HTTPException(status_code=400, detail="top_limit must be one of 5, 10, 20, or 50")

    publisher_filter = publisher.strip() if publisher and publisher.strip() else None
    genre_filter = genre.strip() if genre and genre.strip() else None
    tag_filter = tag.strip() if tag and tag.strip() else None
    platform_filter = platform.strip() if platform and platform.strip() else None

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
                metadata_candidates as (
                    select distinct on (ga.game_id)
                        ga.game_id,
                        array_remove(
                            array[
                                case
                                    when exists (
                                        select 1
                                        from game_aliases gax
                                        join storefronts sfx on sfx.id = gax.storefront_id
                                        where gax.game_id = ga.game_id
                                          and sfx.slug in ('nutaku-all-games', 'nutaku-browser-ranking')
                                    ) then 'Browser'
                                    else null
                                end,
                                case
                                    when exists (
                                        select 1
                                        from game_aliases gax
                                        join storefronts sfx on sfx.id = gax.storefront_id
                                        where gax.game_id = ga.game_id
                                          and sfx.slug = 'nutaku-mobile-ranking'
                                    ) then 'Android'
                                    else null
                                end
                            ],
                            null
                        ) as platforms,
                        coalesce(gms.publisher, ga.publisher_raw) as publisher,
                        coalesce(gms.genres, '[]'::jsonb) as genres,
                        coalesce(gms.tags, '[]'::jsonb) as tags
                    from game_aliases ga
                    join storefronts sf on sf.id = ga.storefront_id
                    join target_storefront ts on true
                    left join lateral (
                        select
                            gms.captured_at,
                            gms.publisher,
                            gms.genres,
                            gms.tags
                        from game_metadata_snapshots gms
                        where gms.game_alias_id = ga.id
                        order by gms.captured_at desc
                        limit 1
                    ) gms on true
                    where (
                        (ts.slug like 'nutaku-%%' and sf.slug like 'nutaku-%%')
                        or sf.slug = ts.slug
                    )
                    order by
                        ga.game_id,
                        case when gms.publisher is not null then 0 else 1 end,
                        case when jsonb_array_length(coalesce(gms.genres, '[]'::jsonb)) > 0 then 0 else 1 end,
                        case when jsonb_array_length(coalesce(gms.tags, '[]'::jsonb)) > 0 then 0 else 1 end,
                        gms.captured_at desc nulls last,
                        ga.updated_at desc
                ),
                filtered_metadata as (
                    select *
                    from metadata_candidates mc
                    where (%s::text is null or coalesce(mc.publisher, '') ilike '%%' || %s::text || '%%')
                      and (
                        %s::text is null
                        or exists (
                            select 1
                            from unnest(coalesce(mc.platforms, array[]::text[])) platform_value(value)
                            where platform_value.value ilike '%%' || %s::text || '%%'
                        )
                      )
                      and (
                        %s::text is null
                        or exists (
                            select 1
                            from jsonb_array_elements_text(coalesce(mc.genres, '[]'::jsonb)) genre_value(value)
                            where genre_value.value ilike '%%' || %s::text || '%%'
                        )
                      )
                      and (
                        %s::text is null
                        or exists (
                            select 1
                            from jsonb_array_elements_text(coalesce(mc.tags, '[]'::jsonb)) tag_value(value)
                            where tag_value.value ilike '%%' || %s::text || '%%'
                        )
                      )
                ),
                ranked_rows as (
                    select
                        case
                            when rs.capture_date between b.current_start and b.latest_date then 'current'
                            when rs.capture_date between b.previous_start and b.previous_end then 'previous'
                            else null
                        end as period,
                        fm.game_id,
                        greatest((%s::int + 1) - re.rank, 1)::numeric as rank_weight,
                        fm.genres
                    from ranking_entries re
                    join ranking_snapshots rs on rs.id = re.snapshot_id
                    join target_storefront ts on ts.id = rs.storefront_id
                    join bounds b on true
                    join game_aliases ga on ga.id = re.game_alias_id
                    join filtered_metadata fm on fm.game_id = ga.game_id
                    where rs.capture_date between b.previous_start and b.latest_date
                      and re.rank <= %s::int
                      and jsonb_array_length(coalesce(fm.genres, '[]'::jsonb)) > 0
                ),
                expanded as (
                    select
                        rr.period,
                        trim(genre_value.value) as genre,
                        case
                            when %s::text = 'main' then rr.rank_weight
                            else rr.rank_weight / nullif(jsonb_array_length(rr.genres), 0)::numeric
                        end as split_weight
                    from ranked_rows rr
                    cross join lateral jsonb_array_elements_text(
                        case
                            when %s::text = 'main' then jsonb_build_array(rr.genres ->> 0)
                            else rr.genres
                        end
                    ) genre_value(value)
                    where rr.period is not null
                      and trim(genre_value.value) <> ''
                ),
                aggregated as (
                    select
                        genre,
                        coalesce(sum(split_weight) filter (where period = 'current'), 0)::numeric as current_score,
                        coalesce(sum(split_weight) filter (where period = 'previous'), 0)::numeric as previous_score
                    from expanded
                    group by genre
                ),
                totals as (
                    select
                        coalesce(sum(current_score), 0)::numeric as current_total,
                        coalesce(sum(previous_score), 0)::numeric as previous_total
                    from aggregated
                )
                select
                    ts.slug,
                    ts.name,
                    b.latest_date,
                    a.genre,
                    a.current_score,
                    a.previous_score,
                    case
                        when t.current_total > 0 then a.current_score / t.current_total
                        else 0
                    end as current_share,
                    case
                        when t.previous_total > 0 then a.previous_score / t.previous_total
                        else 0
                    end as previous_share
                from aggregated a
                join target_storefront ts on true
                join bounds b on true
                join totals t on true
                where a.current_score > 0 or a.previous_score > 0
                order by a.current_score desc, a.genre asc
                limit %s
                """,
                (
                    storefront,
                    window,
                    window,
                    window,
                    publisher_filter,
                    publisher_filter,
                    platform_filter,
                    platform_filter,
                    genre_filter,
                    genre_filter,
                    tag_filter,
                    tag_filter,
                    top_limit,
                    top_limit,
                    mode,
                    mode,
                    limit,
                ),
            )
            rows = cur.fetchall()

    if not rows:
        return {
            "storefront": {"slug": storefront, "name": display_storefront_name(storefront)},
            "latest_date": None,
            "window_days": window,
            "mode": mode,
            "top_limit": top_limit,
            "entries": [],
        }

    return {
        "storefront": {"slug": rows[0][0], "name": display_storefront_name(rows[0][0], rows[0][1])},
        "latest_date": str(rows[0][2]),
        "window_days": window,
        "mode": mode,
        "top_limit": top_limit,
        "entries": [
            {
                "genre": normalize_taxonomy_value(row[3]),
                "current_score": maybe_float(row[4]),
                "previous_score": maybe_float(row[5]),
                "current_share": maybe_float(row[6]),
                "previous_share": maybe_float(row[7]),
                "delta_share": maybe_float((row[6] or 0) - (row[7] or 0)),
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
        "storefront": {"slug": storefront_slug, "name": display_storefront_name(storefront_slug, storefront_name)},
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
                )
                select
                    sc.storefront_slug,
                    sc.storefront_name,
                    min(orx.rank) as best_rank,
                    sc.first_seen_date,
                    sc.last_snapshot_date,
                    count(*) as tracked_days,
                    count(orx.rank) as observed_days,
                    avg(orx.rank)::numeric(10, 2) as observed_avg_rank,
                    avg(coalesce(orx.rank, sc.penalty_rank))::numeric(10, 2) as adjusted_avg_rank,
                    case
                        when count(*) = 0 then null
                        else round(count(orx.rank)::numeric / count(*)::numeric, 4)
                    end as coverage_ratio
                from storefront_calendar sc
                left join observed_ranks orx
                    on orx.storefront_id = sc.storefront_id
                   and orx.capture_date = sc.metric_date
                group by sc.storefront_slug, sc.storefront_name, sc.first_seen_date, sc.last_snapshot_date
                order by sc.storefront_name asc
                """,
                (game_id, game_id, game_id),
            )
            storefront_metrics = cur.fetchall()

            cur.execute(
                """
                select distinct
                    sf.slug,
                    sf.name,
                    ga.title,
                    ga.url
                from game_aliases ga
                join storefronts sf on sf.id = ga.storefront_id
                where ga.game_id = %s
                order by sf.name asc, ga.title asc
                """,
                (game_id,),
            )
            aliases = cur.fetchall()

            cur.execute(
                """
                with alias_candidates as (
                    select
                        ga.url,
                        ga.developer_raw,
                        ga.publisher_raw,
                        sf.slug as storefront_slug,
                        sf.name as storefront_name,
                        gms.image_url,
                        gms.developer as metadata_developer,
                        gms.publisher as metadata_publisher,
                        gms.description
                    from game_aliases ga
                    left join storefronts sf on sf.id = ga.storefront_id
                    left join lateral (
                        select
                            gms.image_url,
                            gms.developer,
                            gms.publisher,
                            gms.description
                        from game_metadata_snapshots gms
                        where gms.game_alias_id = ga.id
                        order by gms.captured_at desc
                        limit 1
                    ) gms on true
                    where ga.game_id = %s
                    order by
                        case when ga.url is not null then 0 else 1 end,
                        case when gms.image_url is not null then 0 else 1 end,
                        ga.updated_at desc
                    limit 1
                )
                select
                    coalesce(g.developer, ac.metadata_developer, ac.developer_raw),
                    coalesce(g.publisher, ac.metadata_publisher, ac.publisher_raw),
                    ac.image_url,
                    ac.url,
                    ac.storefront_slug,
                    ac.storefront_name,
                    ac.description
                from games g
                left join alias_candidates ac on true
                where g.id = %s
                """,
                (game_id, game_id),
            )
            metadata_row = cur.fetchone()

            cur.execute(
                """
                select distinct on (sf.slug)
                    sf.slug,
                    sf.name,
                    ga.title,
                    ga.url,
                    coalesce(gms.developer, ga.developer_raw) as developer,
                    coalesce(gms.publisher, ga.publisher_raw) as publisher,
                    gms.image_url,
                    gms.description,
                    coalesce(gms.genres, '[]'::jsonb) as genres,
                    coalesce(gms.tags, '[]'::jsonb) as tags,
                    gms.captured_at as metadata_captured_at
                from game_aliases ga
                join storefronts sf on sf.id = ga.storefront_id
                left join lateral (
                    select
                        gms.captured_at,
                            gms.developer,
                            gms.publisher,
                            gms.image_url,
                            gms.description,
                            gms.genres,
                            gms.tags
                        from game_metadata_snapshots gms
                        where gms.game_alias_id = ga.id
                        order by gms.captured_at desc
                    limit 1
                ) gms on true
                where ga.game_id = %s
                    order by
                        sf.slug,
                        case
                            when gms.image_url is not null then 0
                            when gms.description is not null then 1
                            when gms.developer is not null then 2
                            when ga.developer_raw is not null then 3
                            else 4
                        end,
                        metadata_captured_at desc nulls last,
                        ga.updated_at desc
                """,
                (game_id,),
            )
            storefront_metadata_rows = cur.fetchall()

            cur.execute(
                """
                with target_storefront as (
                    select id, slug, name
                    from storefronts
                    where slug = 'nutaku-all-games'
                )
                select
                    ned.metric_date,
                    ts.slug,
                    ts.name,
                    ned.rank,
                    ned.model_version,
                    ned.estimated_revenue_low,
                    ned.estimated_revenue_mid,
                    ned.estimated_revenue_high,
                    ned.estimated_downloads_low,
                    ned.estimated_downloads_mid,
                    ned.estimated_downloads_high,
                    ned.confidence,
                    ned.training_sample_size
                from nutaku_rank_estimates_daily ned
                join target_storefront ts on ts.id = ned.storefront_id
                where ned.game_id = %s
                order by ned.metric_date desc, ned.updated_at desc
                limit 1
                """,
                (game_id,),
            )
            nutaku_estimate_row = cur.fetchone()

            cur.execute(
                """
                with target_storefront as (
                    select id, slug, name
                    from storefronts
                    where slug = 'nutaku-all-games'
                )
                select
                    ner.as_of_date,
                    ts.slug,
                    ts.name,
                    ner.model_version,
                    ner.revenue_1d_low,
                    ner.revenue_1d_mid,
                    ner.revenue_1d_high,
                    ner.revenue_7d_low,
                    ner.revenue_7d_mid,
                    ner.revenue_7d_high,
                    ner.revenue_30d_low,
                    ner.revenue_30d_mid,
                    ner.revenue_30d_high,
                    ner.revenue_365d_low,
                    ner.revenue_365d_mid,
                    ner.revenue_365d_high,
                    ner.revenue_total_low,
                    ner.revenue_total_mid,
                    ner.revenue_total_high,
                    ner.downloads_1d_low,
                    ner.downloads_1d_mid,
                    ner.downloads_1d_high,
                    ner.downloads_7d_low,
                    ner.downloads_7d_mid,
                    ner.downloads_7d_high,
                    ner.downloads_30d_low,
                    ner.downloads_30d_mid,
                    ner.downloads_30d_high,
                    ner.downloads_365d_low,
                    ner.downloads_365d_mid,
                    ner.downloads_365d_high,
                    ner.downloads_total_low,
                    ner.downloads_total_mid,
                    ner.downloads_total_high
                from nutaku_rank_estimate_rollups ner
                join target_storefront ts on ts.id = ner.storefront_id
                where ner.game_id = %s
                order by ner.as_of_date desc, ner.updated_at desc
                limit 1
                """,
                (game_id,),
            )
            nutaku_rollup_row = cur.fetchone()

    storefront_metric_payload = [
        {
            "storefront_slug": row[0],
            "storefront_name": display_storefront_name(row[0], row[1]),
            "best_rank": row[2],
            "first_seen_date": str(row[3]) if row[3] else None,
            "last_seen_date": str(row[4]) if row[4] else None,
            "tracked_days": row[5],
            "observed_days": row[6],
            "observed_avg_rank": float(row[7]) if row[7] is not None else None,
            "adjusted_avg_rank": float(row[8]) if row[8] is not None else None,
            "coverage_ratio": float(row[9]) if row[9] is not None else None,
        }
        for row in storefront_metrics
    ]
    default_storefront = (
        next((row for row in storefront_metric_payload if row["storefront_slug"] == "nutaku-all-games"), None)
        or max(storefront_metric_payload, key=lambda row: row["tracked_days"], default=None)
    )
    storefront_metadata_payload = {}
    game_platforms = derive_platforms_for_game_aliases([row[0] for row in storefront_metadata_rows])
    for row in storefront_metadata_rows:
        slug = row[0]
        if slug in storefront_metadata_payload:
            continue
        storefront_metadata_payload[slug] = {
            "storefront_slug": slug,
            "storefront_name": display_storefront_name(row[0], row[1]),
            "alias_title": row[2],
            "url": row[3],
            "developer": row[4],
            "publisher": row[5],
            "image_url": row[6],
            "description": row[7],
            "genres": normalize_taxonomy_list(row[8] or []),
            "tags": normalize_taxonomy_list(row[9] or []),
            "platforms": derive_platforms_for_game_aliases([slug]) if slug.startswith("nutaku-") else [],
        }

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
        "developer": metadata_row[0] if metadata_row else None,
        "publisher": metadata_row[1] if metadata_row else None,
        "image_url": metadata_row[2] if metadata_row else None,
        "game_url": metadata_row[3] if metadata_row else None,
        "metadata_storefront_slug": metadata_row[4] if metadata_row else None,
        "metadata_storefront_name": display_storefront_name(metadata_row[4], metadata_row[5]) if metadata_row else None,
        "description": metadata_row[6] if metadata_row else None,
        "default_storefront_slug": default_storefront["storefront_slug"] if default_storefront else None,
        "default_storefront_name": default_storefront["storefront_name"] if default_storefront else None,
        "storefront_metrics": storefront_metric_payload,
        "storefront_metadata": storefront_metadata_payload,
        "platforms": game_platforms,
        "nutaku_estimate": (
            {
                "metric_date": str(nutaku_estimate_row[0]) if nutaku_estimate_row[0] else None,
                "storefront_slug": nutaku_estimate_row[1],
                "storefront_name": display_storefront_name(nutaku_estimate_row[1], nutaku_estimate_row[2]),
                "rank": nutaku_estimate_row[3],
                "model_version": nutaku_estimate_row[4],
                "estimated_revenue_low": float(nutaku_estimate_row[5]) if nutaku_estimate_row[5] is not None else None,
                "estimated_revenue_mid": float(nutaku_estimate_row[6]) if nutaku_estimate_row[6] is not None else None,
                "estimated_revenue_high": float(nutaku_estimate_row[7]) if nutaku_estimate_row[7] is not None else None,
                "estimated_downloads_low": nutaku_estimate_row[8],
                "estimated_downloads_mid": nutaku_estimate_row[9],
                "estimated_downloads_high": nutaku_estimate_row[10],
                "confidence": nutaku_estimate_row[11],
                "training_sample_size": nutaku_estimate_row[12],
            }
            if nutaku_estimate_row
            else None
        ),
        "nutaku_rollup": (
            {
                "as_of_date": str(nutaku_rollup_row[0]) if nutaku_rollup_row[0] else None,
                "storefront_slug": nutaku_rollup_row[1],
                "storefront_name": display_storefront_name(nutaku_rollup_row[1], nutaku_rollup_row[2]),
                "model_version": nutaku_rollup_row[3],
                "revenue": {
                    "1d": {"low": maybe_float(nutaku_rollup_row[4]), "mid": maybe_float(nutaku_rollup_row[5]), "high": maybe_float(nutaku_rollup_row[6])},
                    "7d": {"low": maybe_float(nutaku_rollup_row[7]), "mid": maybe_float(nutaku_rollup_row[8]), "high": maybe_float(nutaku_rollup_row[9])},
                    "30d": {"low": maybe_float(nutaku_rollup_row[10]), "mid": maybe_float(nutaku_rollup_row[11]), "high": maybe_float(nutaku_rollup_row[12])},
                    "365d": {"low": maybe_float(nutaku_rollup_row[13]), "mid": maybe_float(nutaku_rollup_row[14]), "high": maybe_float(nutaku_rollup_row[15])},
                    "total": {"low": maybe_float(nutaku_rollup_row[16]), "mid": maybe_float(nutaku_rollup_row[17]), "high": maybe_float(nutaku_rollup_row[18])},
                },
                "downloads": {
                    "1d": {"low": nutaku_rollup_row[19], "mid": nutaku_rollup_row[20], "high": nutaku_rollup_row[21]},
                    "7d": {"low": nutaku_rollup_row[22], "mid": nutaku_rollup_row[23], "high": nutaku_rollup_row[24]},
                    "30d": {"low": nutaku_rollup_row[25], "mid": nutaku_rollup_row[26], "high": nutaku_rollup_row[27]},
                    "365d": {"low": nutaku_rollup_row[28], "mid": nutaku_rollup_row[29], "high": nutaku_rollup_row[30]},
                    "total": {"low": nutaku_rollup_row[31], "mid": nutaku_rollup_row[32], "high": nutaku_rollup_row[33]},
                },
            }
            if nutaku_rollup_row
            else None
        ),
        "aliases": [
            {
                "storefront_slug": row[0],
                "storefront_name": display_storefront_name(row[0], row[1]),
                "alias_title": row[2],
                "url": row[3],
            }
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
                        sf.name,
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
                        sf.name,
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
                "storefront_name": display_storefront_name(row[1], row[2]),
                "rank": row[3],
                "alias_title": row[4],
                "data_source": row[5],
                "notes": row[6],
            }
            for row in rows
        ],
    }
