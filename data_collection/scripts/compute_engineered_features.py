"""
Day 3 - Step 2: Compute engineered features (rainfall anomaly, antecedent
precipitation index, discharge ratio, terrain vulnerability, peak season)
and update lga_monthly_features.

Usage:
    pip install pandas numpy
    python compute_engineered_features.py
"""

import os
import pandas as pd
import numpy as np
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

PEAK_MONTHS = {7, 8, 9, 10}
API_WEIGHTS = [0.5, 0.3, 0.2]  # weights for t-1, t-2, t-3 months


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def minmax(series):
    lo, hi = series.min(), series.max()
    if hi == lo:
        return series * 0
    return (series - lo) / (hi - lo)


def main():
    conn = get_db_connection()

    print("Loading monthly features...")
    df = pd.read_sql("""
        SELECT lga_id, year, month, rainfall_mm, river_discharge_cumecs
        FROM lga_monthly_features
        ORDER BY lga_id, year, month
    """, conn)
    print(f"  {len(df)} rows loaded.")

    print("Loading LGA terrain attributes...")
    terrain = pd.read_sql("""
        SELECT lga_id, elevation_mean_m, slope_mean_deg, wetland_pct, water_bodies_pct
        FROM lgas
    """, conn)

    # --- Terrain vulnerability score (static per LGA) ---
    terrain["inv_elev"] = 1 - minmax(terrain["elevation_mean_m"])
    terrain["inv_slope"] = 1 - minmax(terrain["slope_mean_deg"])
    terrain["wetland_norm"] = minmax(terrain["wetland_pct"].fillna(0))
    terrain["water_norm"] = minmax(terrain["water_bodies_pct"].fillna(0))
    terrain["terrain_vulnerability_score"] = terrain[
        ["inv_elev", "inv_slope", "wetland_norm", "water_norm"]
    ].mean(axis=1)

    df = df.merge(terrain[["lga_id", "terrain_vulnerability_score"]], on="lga_id", how="left")

    # --- Peak season flag ---
    df["peak_season_flag"] = df["month"].isin(PEAK_MONTHS)

    # --- Rainfall anomaly index (z-score vs that LGA's own history for that calendar month) ---
    print("Computing rainfall anomaly index...")
    stats = df.groupby(["lga_id", "month"])["rainfall_mm"].agg(["mean", "std"]).reset_index()
    stats.columns = ["lga_id", "month", "rain_month_mean", "rain_month_std"]
    df = df.merge(stats, on=["lga_id", "month"], how="left")
    df["rainfall_anomaly_index"] = (df["rainfall_mm"] - df["rain_month_mean"]) / df["rain_month_std"].replace(0, np.nan)
    df["rainfall_anomaly_index"] = df["rainfall_anomaly_index"].fillna(0)

    # --- Antecedent precipitation index (weighted sum of previous 3 months, per LGA) ---
    print("Computing antecedent precipitation index...")
    df = df.sort_values(["lga_id", "year", "month"]).reset_index(drop=True)
    df["rain_lag1"] = df.groupby("lga_id")["rainfall_mm"].shift(1)
    df["rain_lag2"] = df.groupby("lga_id")["rainfall_mm"].shift(2)
    df["rain_lag3"] = df.groupby("lga_id")["rainfall_mm"].shift(3)
    df["antecedent_precip_index"] = (
        df["rain_lag1"].fillna(0) * API_WEIGHTS[0]
        + df["rain_lag2"].fillna(0) * API_WEIGHTS[1]
        + df["rain_lag3"].fillna(0) * API_WEIGHTS[2]
    )

    # --- Normalised discharge ratio (vs that station's own history for that calendar month) ---
    print("Computing normalised discharge ratio...")
    disc_stats = df.groupby(["lga_id", "month"])["river_discharge_cumecs"].transform("mean")
    df["normalised_discharge_ratio"] = df["river_discharge_cumecs"] / disc_stats.replace(0, np.nan)
    df["normalised_discharge_ratio"] = df["normalised_discharge_ratio"].fillna(1.0)

    print("\nUpdating database...")
    records = list(df[[
        "rainfall_anomaly_index", "antecedent_precip_index",
        "normalised_discharge_ratio", "terrain_vulnerability_score",
        "peak_season_flag", "lga_id", "year", "month"
    ]].itertuples(index=False, name=None))

    cur = conn.cursor()
    update_query = """
        UPDATE lga_monthly_features AS f SET
            rainfall_anomaly_index = v.rai::numeric,
            antecedent_precip_index = v.api::numeric,
            normalised_discharge_ratio = v.ndr::numeric,
            terrain_vulnerability_score = v.tvs::numeric,
            peak_season_flag = v.psf::boolean
        FROM (VALUES %s) AS v(rai, api, ndr, tvs, psf, lga_id, year, month)
        WHERE f.lga_id = v.lga_id::int AND f.year = v.year::int AND f.month = v.month::int;
    """
    execute_values(cur, update_query, records, page_size=2000)
    conn.commit()

    cur.close()
    conn.close()
    print(f"\n✓ Done. Updated {len(records)} rows with engineered features.")


if __name__ == "__main__":
    main()