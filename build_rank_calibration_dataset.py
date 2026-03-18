import argparse
import csv
import os
from pathlib import Path

import psycopg


DATABASE_URL = os.getenv("DATABASE_URL")
DEFAULT_EXPORT_PATH = Path("exports/rank_calibration_dataset.csv")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL es obligatorio para construir el dataset de calibracion.")
    return psycopg.connect(DATABASE_URL, options="-c statement_timeout=0")


def ensure_export_dir(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def rebuild_calibration_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            with daily_ranks as (
                select
                    ga.game_id,
                    rs.capture_date as metric_date,
                    sf.slug as storefront_slug,
                    min(re.rank) as rank
                from ranking_entries re
                join ranking_snapshots rs on rs.id = re.snapshot_id
                join game_aliases ga on ga.id = re.game_alias_id
                join storefronts sf on sf.id = rs.storefront_id
                where sf.slug in ('nutaku-all-games', 'nutaku-browser-ranking', 'nutaku-mobile-ranking')
                group by ga.game_id, rs.capture_date, sf.slug
            ),
            pivoted_ranks as (
                select
                    game_id,
                    metric_date,
                    max(rank) filter (where storefront_slug = 'nutaku-all-games') as nutaku_all_rank,
                    max(rank) filter (where storefront_slug = 'nutaku-browser-ranking') as nutaku_browser_rank,
                    max(rank) filter (where storefront_slug = 'nutaku-mobile-ranking') as nutaku_mobile_rank
                from daily_ranks
                group by game_id, metric_date
            )
            insert into internal_rank_calibration_daily (
                game_id,
                metric_date,
                actual_storefront_id,
                actual_storefront_slug,
                actual_storefront_name,
                raw_game_name,
                downloads,
                dau,
                gold_spent,
                revenue_usd,
                nutaku_all_rank,
                nutaku_browser_rank,
                nutaku_mobile_rank
            )
            select
                iga.game_id,
                iga.metric_date,
                iga.storefront_id,
                sf.slug,
                sf.name,
                iga.raw_game_name,
                iga.downloads,
                iga.dau,
                iga.gold_spent,
                iga.revenue_usd,
                pr.nutaku_all_rank,
                pr.nutaku_browser_rank,
                pr.nutaku_mobile_rank
            from internal_game_actuals_daily iga
            left join storefronts sf on sf.id = iga.storefront_id
            left join pivoted_ranks pr
                on pr.game_id = iga.game_id
               and pr.metric_date = iga.metric_date
            on conflict (game_id, metric_date) do update
                set actual_storefront_id = excluded.actual_storefront_id,
                    actual_storefront_slug = excluded.actual_storefront_slug,
                    actual_storefront_name = excluded.actual_storefront_name,
                    raw_game_name = excluded.raw_game_name,
                    downloads = excluded.downloads,
                    dau = excluded.dau,
                    gold_spent = excluded.gold_spent,
                    revenue_usd = excluded.revenue_usd,
                    nutaku_all_rank = excluded.nutaku_all_rank,
                    nutaku_browser_rank = excluded.nutaku_browser_rank,
                    nutaku_mobile_rank = excluded.nutaku_mobile_rank,
                    updated_at = now()
            """
        )
    conn.commit()


def fetch_dataset_rows(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                ircd.metric_date,
                g.canonical_name,
                ircd.raw_game_name,
                ircd.downloads,
                ircd.dau,
                ircd.gold_spent,
                ircd.revenue_usd,
                ircd.nutaku_all_rank,
                ircd.nutaku_browser_rank,
                ircd.nutaku_mobile_rank
            from internal_rank_calibration_daily ircd
            join games g on g.id = ircd.game_id
            order by ircd.metric_date asc, g.canonical_name asc
            """
        )
        rows = cur.fetchall()
    return rows


def export_dataset(rows, export_path: Path):
    ensure_export_dir(export_path)
    with export_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "metric_date",
                "canonical_name",
                "raw_game_name",
                "downloads",
                "dau",
                "gold_spent",
                "revenue_usd",
                "nutaku_all_rank",
                "nutaku_browser_rank",
                "nutaku_mobile_rank",
            ]
        )
        writer.writerows(rows)


def print_summary(rows):
    total = len(rows)
    with_any_rank = sum(1 for row in rows if row[7] is not None or row[8] is not None or row[9] is not None)
    with_all_rank = sum(1 for row in rows if row[7] is not None)
    print(f"Calibration rows: {total}")
    print(f"Rows with any Nutaku rank: {with_any_rank}")
    print(f"Rows with Nutaku All rank: {with_all_rank}")


def main():
    parser = argparse.ArgumentParser(description="Build internal daily calibration dataset by crossing actuals with Nutaku ranks.")
    parser.add_argument("--export", default=str(DEFAULT_EXPORT_PATH), help="CSV path to export the calibration dataset.")
    args = parser.parse_args()

    export_path = Path(args.export)
    with get_conn() as conn:
        rebuild_calibration_table(conn)
        rows = fetch_dataset_rows(conn)

    export_dataset(rows, export_path)
    print_summary(rows)
    print(f"Exported calibration dataset to {export_path}")


if __name__ == "__main__":
    main()
