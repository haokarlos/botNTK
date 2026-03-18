import argparse
import os
from decimal import Decimal

import psycopg


DATABASE_URL = os.getenv("DATABASE_URL")
DEFAULT_MODEL_VERSION = "nutaku_rank_baseline_v1"
TARGET_STOREFRONT_SLUG = "nutaku-all-games"


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL es obligatorio para construir los rollups de estimaciones.")
    return psycopg.connect(DATABASE_URL, options="-c statement_timeout=0")


def sum_window(rows, key: str, days: int | None):
    if days is None:
        subset = rows
    else:
        subset = rows[:days]
    total = sum((row[key] or Decimal("0")) for row in subset)
    return Decimal(total).quantize(Decimal("0.01"))


def sum_window_int(rows, key: str, days: int | None):
    if days is None:
        subset = rows
    else:
        subset = rows[:days]
    return int(sum((row[key] or 0) for row in subset))


def build_rollups(conn, model_version: str):
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            """
            select sf.id
            from storefronts sf
            where sf.slug = %s
            """,
            (TARGET_STOREFRONT_SLUG,),
        )
        storefront_row = cur.fetchone()
        if not storefront_row:
            raise ValueError(f"No existe el storefront {TARGET_STOREFRONT_SLUG}.")
        storefront_id = storefront_row["id"]

        cur.execute(
            """
            select
                game_id,
                storefront_id,
                metric_date,
                estimated_revenue_low,
                estimated_revenue_mid,
                estimated_revenue_high,
                estimated_downloads_low,
                estimated_downloads_mid,
                estimated_downloads_high
            from nutaku_rank_estimates_daily
            where storefront_id = %s
              and model_version = %s
            order by game_id asc, metric_date desc
            """,
            (storefront_id, model_version),
        )
        rows = cur.fetchall()

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["game_id"]), []).append(row)

    print(f"Building rollups for {len(grouped)} games", flush=True)

    with conn.cursor() as cur:
        processed = 0
        for game_id, game_rows in grouped.items():
            as_of_date = game_rows[0]["metric_date"]
            cur.execute(
                """
                insert into nutaku_rank_estimate_rollups (
                    game_id,
                    storefront_id,
                    as_of_date,
                    model_version,
                    revenue_1d_low,
                    revenue_1d_mid,
                    revenue_1d_high,
                    revenue_7d_low,
                    revenue_7d_mid,
                    revenue_7d_high,
                    revenue_30d_low,
                    revenue_30d_mid,
                    revenue_30d_high,
                    revenue_365d_low,
                    revenue_365d_mid,
                    revenue_365d_high,
                    revenue_total_low,
                    revenue_total_mid,
                    revenue_total_high,
                    downloads_1d_low,
                    downloads_1d_mid,
                    downloads_1d_high,
                    downloads_7d_low,
                    downloads_7d_mid,
                    downloads_7d_high,
                    downloads_30d_low,
                    downloads_30d_mid,
                    downloads_30d_high,
                    downloads_365d_low,
                    downloads_365d_mid,
                    downloads_365d_high,
                    downloads_total_low,
                    downloads_total_mid,
                    downloads_total_high
                )
                values (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                on conflict (game_id, storefront_id, as_of_date, model_version) do update
                    set revenue_1d_low = excluded.revenue_1d_low,
                        revenue_1d_mid = excluded.revenue_1d_mid,
                        revenue_1d_high = excluded.revenue_1d_high,
                        revenue_7d_low = excluded.revenue_7d_low,
                        revenue_7d_mid = excluded.revenue_7d_mid,
                        revenue_7d_high = excluded.revenue_7d_high,
                        revenue_30d_low = excluded.revenue_30d_low,
                        revenue_30d_mid = excluded.revenue_30d_mid,
                        revenue_30d_high = excluded.revenue_30d_high,
                        revenue_365d_low = excluded.revenue_365d_low,
                        revenue_365d_mid = excluded.revenue_365d_mid,
                        revenue_365d_high = excluded.revenue_365d_high,
                        revenue_total_low = excluded.revenue_total_low,
                        revenue_total_mid = excluded.revenue_total_mid,
                        revenue_total_high = excluded.revenue_total_high,
                        downloads_1d_low = excluded.downloads_1d_low,
                        downloads_1d_mid = excluded.downloads_1d_mid,
                        downloads_1d_high = excluded.downloads_1d_high,
                        downloads_7d_low = excluded.downloads_7d_low,
                        downloads_7d_mid = excluded.downloads_7d_mid,
                        downloads_7d_high = excluded.downloads_7d_high,
                        downloads_30d_low = excluded.downloads_30d_low,
                        downloads_30d_mid = excluded.downloads_30d_mid,
                        downloads_30d_high = excluded.downloads_30d_high,
                        downloads_365d_low = excluded.downloads_365d_low,
                        downloads_365d_mid = excluded.downloads_365d_mid,
                        downloads_365d_high = excluded.downloads_365d_high,
                        downloads_total_low = excluded.downloads_total_low,
                        downloads_total_mid = excluded.downloads_total_mid,
                        downloads_total_high = excluded.downloads_total_high,
                        updated_at = now()
                """,
                (
                    game_id,
                    storefront_id,
                    as_of_date,
                    model_version,
                    sum_window(game_rows, "estimated_revenue_low", 1),
                    sum_window(game_rows, "estimated_revenue_mid", 1),
                    sum_window(game_rows, "estimated_revenue_high", 1),
                    sum_window(game_rows, "estimated_revenue_low", 7),
                    sum_window(game_rows, "estimated_revenue_mid", 7),
                    sum_window(game_rows, "estimated_revenue_high", 7),
                    sum_window(game_rows, "estimated_revenue_low", 30),
                    sum_window(game_rows, "estimated_revenue_mid", 30),
                    sum_window(game_rows, "estimated_revenue_high", 30),
                    sum_window(game_rows, "estimated_revenue_low", 365),
                    sum_window(game_rows, "estimated_revenue_mid", 365),
                    sum_window(game_rows, "estimated_revenue_high", 365),
                    sum_window(game_rows, "estimated_revenue_low", None),
                    sum_window(game_rows, "estimated_revenue_mid", None),
                    sum_window(game_rows, "estimated_revenue_high", None),
                    sum_window_int(game_rows, "estimated_downloads_low", 1),
                    sum_window_int(game_rows, "estimated_downloads_mid", 1),
                    sum_window_int(game_rows, "estimated_downloads_high", 1),
                    sum_window_int(game_rows, "estimated_downloads_low", 7),
                    sum_window_int(game_rows, "estimated_downloads_mid", 7),
                    sum_window_int(game_rows, "estimated_downloads_high", 7),
                    sum_window_int(game_rows, "estimated_downloads_low", 30),
                    sum_window_int(game_rows, "estimated_downloads_mid", 30),
                    sum_window_int(game_rows, "estimated_downloads_high", 30),
                    sum_window_int(game_rows, "estimated_downloads_low", 365),
                    sum_window_int(game_rows, "estimated_downloads_mid", 365),
                    sum_window_int(game_rows, "estimated_downloads_high", 365),
                    sum_window_int(game_rows, "estimated_downloads_low", None),
                    sum_window_int(game_rows, "estimated_downloads_mid", None),
                    sum_window_int(game_rows, "estimated_downloads_high", None),
                ),
            )
            processed += 1
            if processed % 100 == 0:
                conn.commit()
                print(f"Stored rollups for {processed}/{len(grouped)} games", flush=True)

        conn.commit()
        print(f"Stored rollups for {processed}/{len(grouped)} games", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Build Nutaku estimate rollups by game and window.")
    parser.add_argument("--model-version", default=DEFAULT_MODEL_VERSION, help="Model version to aggregate.")
    args = parser.parse_args()

    with get_conn() as conn:
        build_rollups(conn, args.model_version)


if __name__ == "__main__":
    main()
