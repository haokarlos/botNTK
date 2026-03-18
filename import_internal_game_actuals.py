import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path

import psycopg


DATABASE_URL = os.getenv("DATABASE_URL")
DEFAULT_STOREFRONT_SLUG = "nutaku-all-games"
DEFAULT_SOURCE = "gameyond"
GOLD_TO_USD = Decimal("0.01")


@dataclass
class GameActualRow:
    metric_date: datetime.date
    raw_game_name: str
    downloads: int
    dau: int
    gold_spent: int
    revenue_usd: Decimal


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL es obligatorio para importar los actuals internos.")
    return psycopg.connect(DATABASE_URL, options="-c statement_timeout=0")


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def parse_int(value: str) -> int:
    cleaned = (value or "").strip()
    return int(cleaned) if cleaned else 0


def load_actual_rows(csv_path: Path) -> list[GameActualRow]:
    rows: list[GameActualRow] = []

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle, delimiter=";")
        header = None
        for raw_row in reader:
            if not raw_row:
                continue
            if raw_row[0].strip().casefold() == "date":
                header = raw_row
                continue
            if header is None:
                # Some exports include a banner row like "report 45".
                continue

            metric_date = datetime.strptime(raw_row[0].strip(), "%Y-%m-%d").date()
            raw_game_name = raw_row[1].strip()
            downloads = parse_int(raw_row[2])
            dau = parse_int(raw_row[3])
            gold_spent = parse_int(raw_row[4])
            revenue_usd = (Decimal(gold_spent) * GOLD_TO_USD).quantize(Decimal("0.01"))
            rows.append(
                GameActualRow(
                    metric_date=metric_date,
                    raw_game_name=raw_game_name,
                    downloads=downloads,
                    dau=dau,
                    gold_spent=gold_spent,
                    revenue_usd=revenue_usd,
                )
            )

    return rows


def get_storefront_id(conn, storefront_slug: str):
    with conn.cursor() as cur:
        cur.execute("select id from storefronts where slug = %s", (storefront_slug,))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"No existe el storefront {storefront_slug}.")
    return row[0]


def resolve_game_id(conn, raw_game_name: str):
    normalized = normalize_name(raw_game_name)

    with conn.cursor() as cur:
        cur.execute(
            """
            with candidates as (
                select g.id, g.canonical_name, 0 as priority
                from games g
                where g.canonical_name_normalized = %s
                union all
                select ga.game_id, g.canonical_name, 1 as priority
                from game_aliases ga
                join games g on g.id = ga.game_id
                where ga.title_normalized = %s
            )
            select id, canonical_name
            from candidates
            order by priority asc, canonical_name asc
            limit 1
            """,
            (normalized, normalized),
        )
        row = cur.fetchone()

    if not row:
        raise ValueError(f"No se pudo mapear el juego '{raw_game_name}' a un game_id.")

    return row[0], row[1]


def upsert_actual_row(conn, storefront_id, game_id, row: GameActualRow, source: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into internal_game_actuals_daily (
                game_id,
                storefront_id,
                metric_date,
                source,
                raw_game_name,
                downloads,
                dau,
                gold_spent,
                revenue_usd
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (game_id, storefront_id, metric_date, source) do update
                set raw_game_name = excluded.raw_game_name,
                    downloads = excluded.downloads,
                    dau = excluded.dau,
                    gold_spent = excluded.gold_spent,
                    revenue_usd = excluded.revenue_usd,
                    updated_at = now()
            """,
            (
                game_id,
                storefront_id,
                row.metric_date,
                source,
                row.raw_game_name,
                row.downloads,
                row.dau,
                row.gold_spent,
                row.revenue_usd,
            ),
        )


def import_csv(conn, csv_path: Path, storefront_slug: str, source: str):
    rows = load_actual_rows(csv_path)
    storefront_id = get_storefront_id(conn, storefront_slug)

    print(f"Importing {csv_path.name}: {len(rows)} daily rows", flush=True)
    imported = 0

    for row in rows:
        game_id, canonical_name = resolve_game_id(conn, row.raw_game_name)
        upsert_actual_row(conn, storefront_id, game_id, row, source)
        imported += 1

    conn.commit()
    print(f"Imported {imported} rows from {csv_path.name}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Import internal daily actuals into PostgreSQL.")
    parser.add_argument("csv_paths", nargs="+", help="One or more CSV files exported from Gameyond.")
    parser.add_argument("--storefront", default=DEFAULT_STOREFRONT_SLUG, help="Storefront slug to associate with these actuals.")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Source label for the imported actuals.")
    args = parser.parse_args()

    with get_conn() as conn:
        for csv_path in args.csv_paths:
            import_csv(conn, Path(csv_path), args.storefront, args.source)


if __name__ == "__main__":
    main()
