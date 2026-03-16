import os
from contextlib import contextmanager

import psycopg
from fastapi import FastAPI, HTTPException, Query


DATABASE_URL = os.getenv("DATABASE_URL")

app = FastAPI(title="BotNTK API", version="0.1.0")


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
                    select rs.id, rs.capture_date, sf.slug as storefront_slug, sf.name as storefront_name
                    from ranking_snapshots rs
                    join storefronts sf on sf.id = rs.storefront_id
                    where sf.slug = %s
                    order by rs.capture_date desc, rs.captured_at desc
                    limit 1
                )
                select
                    ls.capture_date,
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
    storefront_slug = rows[0][1]
    storefront_name = rows[0][2]

    return {
        "storefront": {"slug": storefront_slug, "name": storefront_name},
        "capture_date": str(capture_date),
        "entries": [
            {
                "rank": row[3],
                "game_id": str(row[4]),
                "canonical_name": row[5],
                "game_alias_id": str(row[6]),
                "alias_title": row[7],
            }
            for row in rows
        ],
    }


@app.get("/games/search")
def search_games(
    q: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=100),
):
    query = f"%{' '.join(q.casefold().split())}%"

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
                where g.canonical_name_normalized like %s
                   or ga.title_normalized like %s
                group by g.id, g.canonical_name
                order by g.canonical_name asc
                limit %s
                """,
                (query, query, limit),
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
                        ga.title
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
                        ga.title
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
            }
            for row in rows
        ],
    }
