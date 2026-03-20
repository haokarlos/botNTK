import json
import os
import re

import psycopg


DATABASE_URL = os.getenv("DATABASE_URL")
BATCH_SIZE = 500
GENERIC_GENRES = {"pc games", "mobile games"}


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL es obligatorio para normalizar genres y tags.")
    return psycopg.connect(DATABASE_URL, options="-c statement_timeout=0")


def normalize_taxonomy_value(value):
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    cleaned = re.sub(r"[\s,;:]+$", "", cleaned)
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


def normalize_genre_list(values):
    normalized = normalize_taxonomy_list(values)
    return [value for value in normalized if value.casefold() not in GENERIC_GENRES]


def fetch_rows(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            select id, genres, tags
            from game_metadata_snapshots
            order by created_at asc
            """
        )
        return cur.fetchall()


def normalize_existing_metadata(conn):
    rows = fetch_rows(conn)
    print(f"Metadata snapshots to inspect: {len(rows)}", flush=True)

    updated = 0
    pending = []

    with conn.cursor() as cur:
        for row_id, genres, tags in rows:
            normalized_genres = normalize_genre_list(genres)
            normalized_tags = normalize_taxonomy_list(tags)

            genres_changed = list(genres or []) != normalized_genres
            tags_changed = list(tags or []) != normalized_tags
            if not genres_changed and not tags_changed:
                continue

            pending.append(
                (
                    json.dumps(normalized_genres),
                    json.dumps(normalized_tags),
                    row_id,
                )
            )
            updated += 1

            if len(pending) >= BATCH_SIZE:
                cur.executemany(
                    """
                    update game_metadata_snapshots
                    set genres = %s::jsonb,
                        tags = %s::jsonb
                    where id = %s
                    """,
                    pending,
                )
                conn.commit()
                print(f"Updated {updated} snapshots so far", flush=True)
                pending.clear()

        if pending:
            cur.executemany(
                """
                update game_metadata_snapshots
                set genres = %s::jsonb,
                    tags = %s::jsonb
                where id = %s
                """,
                pending,
            )
            conn.commit()

    print(f"Finished. Updated {updated} metadata snapshots.", flush=True)


def main():
    with get_conn() as conn:
        normalize_existing_metadata(conn)


if __name__ == "__main__":
    main()
