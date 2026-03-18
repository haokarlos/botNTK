import argparse
import math
import os
from dataclasses import dataclass
from decimal import Decimal

import psycopg


DATABASE_URL = os.getenv("DATABASE_URL")
DEFAULT_MODEL_VERSION = "nutaku_rank_baseline_v1"
TARGET_STOREFRONT_SLUG = "nutaku-all-games"
Z_SCORE_80 = 1.2815515655446004
BATCH_SIZE = 1000


@dataclass
class ModelFit:
    intercept: float
    slope: float
    residual_stddev: float
    sample_size: int


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL es obligatorio para entrenar el estimador.")
    return psycopg.connect(DATABASE_URL, options="-c statement_timeout=0")


def linear_regression_log_log(pairs: list[tuple[int, float]]) -> ModelFit:
    if len(pairs) < 2:
        raise ValueError("No hay suficientes filas para entrenar el modelo.")

    xs = [math.log(rank) for rank, _ in pairs]
    ys = [math.log(value) for _, value in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)

    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        raise ValueError("No hay variacion suficiente en los ranks para entrenar el modelo.")

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x

    if len(xs) > 2:
        residual_variance = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys)) / (len(xs) - 2)
        residual_stddev = math.sqrt(max(residual_variance, 0.0))
    else:
        residual_stddev = 0.0

    return ModelFit(
        intercept=intercept,
        slope=slope,
        residual_stddev=residual_stddev,
        sample_size=len(pairs),
    )


def fetch_training_pairs(conn, target_metric: str) -> list[tuple[int, float]]:
    metric_column = {
        "revenue_usd": "revenue_usd",
        "downloads": "downloads",
    }.get(target_metric)
    if not metric_column:
        raise ValueError(f"Target metric no soportado: {target_metric}")

    with conn.cursor() as cur:
        cur.execute(
            f"""
            select nutaku_all_rank, {metric_column}
            from internal_rank_calibration_daily
            where nutaku_all_rank is not null
              and {metric_column} is not null
              and {metric_column} > 0
            order by metric_date asc
            """
        )
        rows = cur.fetchall()

    print(f"Training rows for {target_metric}: {len(rows)}", flush=True)
    return [(int(row[0]), float(row[1])) for row in rows]


def save_model(conn, model_version: str, target_metric: str, fit: ModelFit):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into nutaku_rank_estimation_models (
                model_version,
                target_metric,
                storefront_slug,
                sample_size,
                intercept,
                slope,
                residual_stddev,
                notes
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (model_version) do update
                set target_metric = excluded.target_metric,
                    storefront_slug = excluded.storefront_slug,
                    sample_size = excluded.sample_size,
                    intercept = excluded.intercept,
                    slope = excluded.slope,
                    residual_stddev = excluded.residual_stddev,
                    trained_at = now(),
                    notes = excluded.notes
            """,
            (
                model_version,
                target_metric,
                TARGET_STOREFRONT_SLUG,
                fit.sample_size,
                Decimal(str(fit.intercept)),
                Decimal(str(fit.slope)),
                Decimal(str(fit.residual_stddev)),
                "Baseline log-log model trained from internal Nutaku actuals.",
            ),
        )


def predict_mid_low_high(rank: int, fit: ModelFit) -> tuple[float, float, float]:
    log_rank = math.log(rank)
    log_mid = fit.intercept + fit.slope * log_rank
    log_low = log_mid - Z_SCORE_80 * fit.residual_stddev
    log_high = log_mid + Z_SCORE_80 * fit.residual_stddev
    return (math.exp(log_low), math.exp(log_mid), math.exp(log_high))


def fetch_target_rank_rows(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            with target_storefront as (
                select id
                from storefronts
                where slug = %s
            ),
            latest_dates as (
                select
                    rs.capture_date,
                    max(rs.captured_at) as latest_captured_at
                from ranking_snapshots rs
                join target_storefront ts on ts.id = rs.storefront_id
                group by rs.capture_date
            ),
            latest_snapshots as (
                select rs.id, rs.capture_date, rs.storefront_id
                from ranking_snapshots rs
                join target_storefront ts on ts.id = rs.storefront_id
                join latest_dates ld
                  on ld.capture_date = rs.capture_date
                 and ld.latest_captured_at = rs.captured_at
            )
            select
                ga.game_id,
                ls.storefront_id,
                ls.capture_date,
                re.rank
            from latest_snapshots ls
            join ranking_entries re on re.snapshot_id = ls.id
            join game_aliases ga on ga.id = re.game_alias_id
            order by ls.capture_date asc, re.rank asc
            """,
            (TARGET_STOREFRONT_SLUG,),
        )
        rows = cur.fetchall()
    print(f"Target rank rows to estimate: {len(rows)}", flush=True)
    return rows


def save_estimates(conn, model_version: str, revenue_fit: ModelFit, downloads_fit: ModelFit):
    rows = fetch_target_rank_rows(conn)
    statement = """
        insert into nutaku_rank_estimates_daily (
            game_id,
            storefront_id,
            metric_date,
            rank,
            model_version,
            estimated_revenue_low,
            estimated_revenue_mid,
            estimated_revenue_high,
            estimated_downloads_low,
            estimated_downloads_mid,
            estimated_downloads_high,
            confidence,
            training_sample_size
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (game_id, storefront_id, metric_date, model_version) do update
            set rank = excluded.rank,
                estimated_revenue_low = excluded.estimated_revenue_low,
                estimated_revenue_mid = excluded.estimated_revenue_mid,
                estimated_revenue_high = excluded.estimated_revenue_high,
                estimated_downloads_low = excluded.estimated_downloads_low,
                estimated_downloads_mid = excluded.estimated_downloads_mid,
                estimated_downloads_high = excluded.estimated_downloads_high,
                confidence = excluded.confidence,
                training_sample_size = excluded.training_sample_size,
                updated_at = now()
    """

    params: list[tuple] = []
    confidence = "low" if min(revenue_fit.sample_size, downloads_fit.sample_size) < 180 else "medium"
    sample_size = min(revenue_fit.sample_size, downloads_fit.sample_size)

    with conn.cursor() as cur:
        for index, (game_id, storefront_id, metric_date, rank) in enumerate(rows, start=1):
            revenue_low, revenue_mid, revenue_high = predict_mid_low_high(rank, revenue_fit)
            downloads_low, downloads_mid, downloads_high = predict_mid_low_high(rank, downloads_fit)
            params.append(
                (
                    game_id,
                    storefront_id,
                    metric_date,
                    rank,
                    model_version,
                    Decimal(f"{revenue_low:.2f}"),
                    Decimal(f"{revenue_mid:.2f}"),
                    Decimal(f"{revenue_high:.2f}"),
                    max(0, round(downloads_low)),
                    max(0, round(downloads_mid)),
                    max(0, round(downloads_high)),
                    confidence,
                    sample_size,
                )
            )

            if len(params) >= BATCH_SIZE:
                cur.executemany(statement, params)
                conn.commit()
                print(f"Stored {index}/{len(rows)} estimate rows", flush=True)
                params.clear()

        if params:
            cur.executemany(statement, params)
            conn.commit()
            print(f"Stored {len(rows)}/{len(rows)} estimate rows", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Train baseline Nutaku rank estimator and persist daily estimates.")
    parser.add_argument("--model-version", default=DEFAULT_MODEL_VERSION, help="Version label for this model fit.")
    args = parser.parse_args()

    with get_conn() as conn:
        print("Fetching training pairs...", flush=True)
        revenue_pairs = fetch_training_pairs(conn, "revenue_usd")
        downloads_pairs = fetch_training_pairs(conn, "downloads")

        print("Fitting log-log models...", flush=True)
        revenue_fit = linear_regression_log_log(revenue_pairs)
        downloads_fit = linear_regression_log_log(downloads_pairs)

        print("Saving model coefficients...", flush=True)
        save_model(conn, f"{args.model_version}_revenue", "revenue_usd", revenue_fit)
        save_model(conn, f"{args.model_version}_downloads", "downloads", downloads_fit)
        conn.commit()
        print("Generating daily Nutaku estimates...", flush=True)
        save_estimates(conn, args.model_version, revenue_fit, downloads_fit)

    print(f"Revenue model: intercept={revenue_fit.intercept:.4f}, slope={revenue_fit.slope:.4f}, samples={revenue_fit.sample_size}")
    print(f"Downloads model: intercept={downloads_fit.intercept:.4f}, slope={downloads_fit.slope:.4f}, samples={downloads_fit.sample_size}")
    print(f"Stored Nutaku estimates with model version {args.model_version}")


if __name__ == "__main__":
    main()
