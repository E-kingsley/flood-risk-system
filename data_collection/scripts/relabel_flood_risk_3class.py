"""
relabel_flood_risk_3class.py

Restores the 3-class flood risk target (Low / Moderate / High) that the
original 6-day plan called for, replacing the binary label that Day 3's
event-only labeling collapsed into.

METHOD
------
1. Pull rainfall_anomaly_index, antecedent_precip_index,
   normalised_discharge_ratio, terrain_vulnerability_score, and
   peak_season_flag for every LGA-month, plus the existing binary
   flood_risk_label (which flags confirmed Dartmouth/EM-DAT flood events).
2. Min-max normalise each environmental feature to [0, 1].
3. Build a weighted composite risk score from the normalised features.
   Weights are a starting point — tune them if your domain knowledge
   suggests a different balance:
     rainfall_anomaly_index      0.30
     antecedent_precip_index     0.25
     normalised_discharge_ratio  0.25
     terrain_vulnerability_score 0.15
     peak_season_flag            0.05
4. Anchor the "High" floor at the lowest composite score among rows with
   a CONFIRMED flood event (flood_risk_label == 1), so ground truth
   drives the top class rather than being overridden by the score.
5. Split the remaining rows into Low / Moderate using a percentile cutoff
   on the composite score (default: 70th percentile).
6. Any row with a confirmed event is force-assigned High (2) regardless
   of its computed score, since it's real ground truth.
7. Writes the result to a new column `flood_risk_class` (0=Low,
   1=Moderate, 2=High) in `lga_monthly_features`, leaving the original
   `flood_risk_label` column untouched so nothing already built breaks.

USAGE
-----
    pip install psycopg2-binary pandas python-dotenv --break-system-packages
    python relabel_flood_risk_3class.py

Requires the same .env variables your Flask backend already uses:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

After running, check the printed class distribution, then:
  - retrain XGBoost with objective="multi:softprob", num_class=3 on
    flood_risk_class instead of flood_risk_label
  - update RISK_LABELS and the /predict response in routes.py to a
    3-way mapping: {0: "Low Risk", 1: "Moderate Risk", 2: "High Risk"}
"""

import os
import sys

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

# ---- Config: tune these if you want a different balance or split ----
WEIGHTS = {
    "rainfall_anomaly_index": 0.30,
    "antecedent_precip_index": 0.25,
    "normalised_discharge_ratio": 0.25,
    "terrain_vulnerability_score": 0.15,
    "peak_season_flag": 0.05,
}
LOW_MODERATE_PERCENTILE = 70  # rows above this percentile (and below the
                              # High floor) become Moderate; below it, Low


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_features(conn) -> pd.DataFrame:
    query = """
        SELECT
            lga_id, year, month,
            rainfall_anomaly_index, antecedent_precip_index,
            normalised_discharge_ratio, terrain_vulnerability_score,
            peak_season_flag, flood_risk_label
        FROM lga_monthly_features;
    """
    df = pd.read_sql(query, conn)

    # flood_risk_label may be stored as text ('0'/'1') rather than int —
    # normalise it to int so comparisons below are reliable.
    df["flood_risk_label"] = df["flood_risk_label"].astype(str).astype(float).astype(int)

    return df


def compute_composite_score(df: pd.DataFrame) -> pd.Series:
    score = pd.Series(0.0, index=df.index)
    for feature, weight in WEIGHTS.items():
        col = df[feature].astype(float)
        col_min, col_max = col.min(), col.max()
        if col_max > col_min:
            normalised = (col - col_min) / (col_max - col_min)
        else:
            # constant column (e.g. peak_season_flag might already be 0/1) —
            # no scaling needed
            normalised = col
        score += weight * normalised
    return score


def assign_classes(df: pd.DataFrame) -> pd.Series:
    confirmed = df["flood_risk_label"] == 1

    if confirmed.sum() == 0:
        print("WARNING: no confirmed flood events found — cannot anchor "
              "the High class. Check flood_risk_label values.", file=sys.stderr)
        high_floor = df["composite_score"].quantile(0.99)
    else:
        high_floor = df.loc[confirmed, "composite_score"].min()

    low_moderate_cutoff = df["composite_score"].quantile(LOW_MODERATE_PERCENTILE / 100)

    # Make sure the High floor never sits below the Low/Moderate cutoff —
    # if it does, nudge it up so classes stay ordered and non-overlapping.
    if high_floor <= low_moderate_cutoff:
        high_floor = df["composite_score"].quantile(0.95)

    def classify(row):
        if row["flood_risk_label"] == 1:
            return 2  # High — ground truth override
        if row["composite_score"] >= high_floor:
            return 2  # High — score alone crosses the anchor threshold
        if row["composite_score"] >= low_moderate_cutoff:
            return 1  # Moderate
        return 0  # Low

    print(f"High-risk score floor (anchored on confirmed events): {high_floor:.4f}")
    print(f"Low/Moderate cutoff (P{LOW_MODERATE_PERCENTILE}): {low_moderate_cutoff:.4f}")

    return df.apply(classify, axis=1)


def write_classes(conn, df: pd.DataFrame):
    with conn.cursor() as cur:
        cur.execute("""
            ALTER TABLE lga_monthly_features
            ADD COLUMN IF NOT EXISTS flood_risk_class INTEGER;
        """)
        conn.commit()

        rows = list(zip(
            df["flood_risk_class"].tolist(),
            df["lga_id"].tolist(),
            df["year"].tolist(),
            df["month"].tolist(),
        ))

        # Bulk update via a temp table join — much faster than one UPDATE
        # per row for ~30k+ rows.
        cur.execute("""
            CREATE TEMP TABLE tmp_flood_class (
                flood_risk_class INTEGER,
                lga_id INTEGER,
                year INTEGER,
                month INTEGER
            ) ON COMMIT DROP;
        """)
        execute_values(
            cur,
            "INSERT INTO tmp_flood_class (flood_risk_class, lga_id, year, month) VALUES %s",
            rows,
        )
        cur.execute("""
            UPDATE lga_monthly_features AS f
            SET flood_risk_class = t.flood_risk_class
            FROM tmp_flood_class AS t
            WHERE f.lga_id = t.lga_id AND f.year = t.year AND f.month = t.month;
        """)
        conn.commit()


def main():
    conn = get_connection()
    try:
        print("Loading features from lga_monthly_features...")
        df = load_features(conn)
        print(f"Loaded {len(df)} rows.")

        df["composite_score"] = compute_composite_score(df)
        df["flood_risk_class"] = assign_classes(df)

        print("\nNew 3-class distribution:")
        print(df["flood_risk_class"].value_counts().sort_index().rename(
            {0: "Low (0)", 1: "Moderate (1)", 2: "High (2)"}
        ))

        print("\nWriting flood_risk_class back to lga_monthly_features...")
        write_classes(conn, df)
        print("Done. Column 'flood_risk_class' added/updated.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
