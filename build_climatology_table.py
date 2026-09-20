"""
build_climatology_table.py

Builds a lga_month_climatology table: one row per LGA per calendar
month (105 x 12 = 1,260 rows), storing the historical baselines that
rainfall_anomaly_index and normalised_discharge_ratio are computed
against:

    rain_month_mean       - mean rainfall_mm for that LGA, that
                             calendar month, across all 25 years
    rain_month_std        - std dev of the same
    discharge_month_mean  - mean river_discharge_cumecs for that LGA,
                             that calendar month, across all 25 years

This must match compute_engineered_features.py's grouping exactly
(group by lga_id + month) so that live-forecast predictions are
computed against the identical baseline the model was trained on.

USAGE
-----
    python build_climatology_table.py
"""

import os

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def main():
    conn = get_connection()
    try:
        print("Loading historical monthly features...")
        df = pd.read_sql("""
            SELECT lga_id, month, rainfall_mm, river_discharge_cumecs
            FROM lga_monthly_features;
        """, conn)
        print(f"  {len(df)} rows loaded.")

        print("Computing per-LGA per-month climatology...")
        climatology = df.groupby(["lga_id", "month"]).agg(
            rain_month_mean=("rainfall_mm", "mean"),
            rain_month_std=("rainfall_mm", "std"),
            discharge_month_mean=("river_discharge_cumecs", "mean"),
        ).reset_index()

        # std can be NaN if a group somehow has only one data point, or 0
        # if rainfall was identical every year for that LGA/month (rare
        # but possible) — treat both as "no meaningful variability" the
        # same way compute_engineered_features.py did (avoid divide-by-zero
        # downstream by leaving it as 0 here; the consumer fills anomaly
        # with 0 for zero-std cases, same as the historical pipeline).
        climatology["rain_month_std"] = climatology["rain_month_std"].fillna(0)

        print(f"  Built {len(climatology)} climatology rows "
              f"(expecting 105 LGAs x 12 months = 1260).")

        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS lga_month_climatology (
                    lga_id INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    rain_month_mean NUMERIC,
                    rain_month_std NUMERIC,
                    discharge_month_mean NUMERIC,
                    PRIMARY KEY (lga_id, month)
                );
            """)
            conn.commit()

            records = list(climatology[[
                "lga_id", "month", "rain_month_mean",
                "rain_month_std", "discharge_month_mean"
            ]].itertuples(index=False, name=None))

            upsert_query = """
                INSERT INTO lga_month_climatology
                    (lga_id, month, rain_month_mean, rain_month_std, discharge_month_mean)
                VALUES %s
                ON CONFLICT (lga_id, month) DO UPDATE SET
                    rain_month_mean = EXCLUDED.rain_month_mean,
                    rain_month_std = EXCLUDED.rain_month_std,
                    discharge_month_mean = EXCLUDED.discharge_month_mean;
            """
            execute_values(cur, upsert_query, records, page_size=500)
            conn.commit()

        print(f"\n✓ Done. lga_month_climatology populated with {len(records)} rows.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
