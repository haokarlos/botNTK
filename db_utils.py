from datetime import UTC, datetime
import json


def normalize_title(value):
    return ' '.join(value.casefold().split())


def get_storefront_id(conn, storefront_slug):
    with conn.cursor() as cur:
        cur.execute(
            """
            select id
            from storefronts
            where slug = %s
            """,
            (storefront_slug,),
        )
        row = cur.fetchone()

    if not row:
        raise ValueError(f'No existe el storefront {storefront_slug} en la base de datos.')

    return row[0]


def upsert_game_alias(conn, storefront_id, title):
    normalized_title = normalize_title(title)

    with conn.cursor() as cur:
        cur.execute(
            """
            with storefront_platform as (
                select s.id as storefront_id, s.platform_id
                from storefronts s
                where s.id = %s
            ),
            existing_alias as (
                select ga.id, ga.game_id, sp.platform_id
                from game_aliases ga
                join storefront_platform sp on sp.platform_id = ga.platform_id
                where ga.title_normalized = %s
                order by ga.created_at asc
                limit 1
            ),
            inserted_game as (
                insert into games (canonical_name, canonical_name_normalized)
                select %s, %s
                where not exists (select 1 from existing_alias)
                on conflict (canonical_name_normalized) do update
                    set updated_at = now()
                returning id
            ),
            resolved_game as (
                select game_id as id from existing_alias
                union all
                select id from inserted_game
                union all
                select g.id
                from games g
                where g.canonical_name_normalized = %s
                limit 1
            )
            insert into game_aliases (
                game_id,
                platform_id,
                storefront_id,
                title,
                title_normalized,
                first_seen_at,
                last_seen_at
            )
            select
                rg.id,
                sp.platform_id,
                sp.storefront_id,
                %s,
                %s,
                now(),
                now()
            from resolved_game rg
            cross join storefront_platform sp
            on conflict (storefront_id, title_normalized) do nothing
            returning id
            """,
            (
                storefront_id,
                normalized_title,
                title,
                normalized_title,
                normalized_title,
                title,
                normalized_title,
            ),
        )
        row = cur.fetchone()
        if row:
            return row[0]

        cur.execute(
            """
            update game_aliases
            set last_seen_at = now(),
                updated_at = now(),
                title = %s
            where storefront_id = %s
              and title_normalized = %s
            returning id
            """,
            (title, storefront_id, normalized_title),
        )
        row = cur.fetchone()

    if not row:
        raise ValueError(f'No se pudo crear o actualizar el alias para {title}.')

    return row[0]


def save_snapshot_to_postgres(
    conn,
    storefront_slug,
    source_url,
    game_names,
    *,
    capture_date=None,
    captured_at=None,
    data_source='observed',
    copied_from_snapshot_id=None,
    notes=None,
    status='success',
):
    captured_at = captured_at or datetime.now(UTC)
    capture_date = capture_date or captured_at.date()
    storefront_id = get_storefront_id(conn, storefront_slug)

    payload = {
        'games': game_names,
        'data_source': data_source,
        'notes': notes,
    }
    if copied_from_snapshot_id:
        payload['copied_from_snapshot_id'] = str(copied_from_snapshot_id)

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into ranking_snapshots (
                storefront_id,
                captured_at,
                capture_date,
                source_url,
                status,
                raw_payload,
                data_source,
                copied_from_snapshot_id,
                notes
            )
            values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            on conflict (storefront_id, capture_date) do update
                set captured_at = excluded.captured_at,
                    source_url = excluded.source_url,
                    status = excluded.status,
                    raw_payload = excluded.raw_payload,
                    data_source = excluded.data_source,
                    copied_from_snapshot_id = excluded.copied_from_snapshot_id,
                    notes = excluded.notes
            returning id
            """,
            (
                storefront_id,
                captured_at,
                capture_date,
                source_url,
                status,
                json.dumps(payload),
                data_source,
                copied_from_snapshot_id,
                notes,
            ),
        )
        snapshot_id = cur.fetchone()[0]
        cur.execute(
            """
            delete from ranking_entries
            where snapshot_id = %s
            """,
            (snapshot_id,),
        )

    for rank, game_name in enumerate(game_names, start=1):
        game_alias_id = upsert_game_alias(conn, storefront_id, game_name)
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into ranking_entries (snapshot_id, game_alias_id, rank)
                values (%s, %s, %s)
                """,
                (snapshot_id, game_alias_id, rank),
            )

    return snapshot_id
