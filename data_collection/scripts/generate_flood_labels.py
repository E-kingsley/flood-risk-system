import os
import psycopg2
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )

def generate_flood_labels():
    conn = get_db_connection()
    
    print("Fetching monthly feature matrix...")
    # 1. Fetch feature dataset with State information
    features_query = """
        SELECT 
            f.id,
            f.lga_id,
            f.year,
            f.month,
            s.state_id,
            f.rainfall_anomaly_index,
            f.antecedent_precip_index,
            f.normalised_discharge_ratio,
            f.terrain_vulnerability_score,
            f.peak_season_flag
        FROM lga_monthly_features f
        JOIN lgas l ON f.lga_id = l.lga_id
        JOIN states s ON l.state_id = s.state_id;
    """
    df_features = pd.read_sql_query(features_query, conn)
    print(f"Loaded {len(df_features)} LGA-month records.")

    # 2. Fetch State-Month Flood Event occurrences
    events_query = """
        SELECT DISTINCT 
            s.state_id,
            EXTRACT(YEAR FROM e.event_date)::INT AS year,
            EXTRACT(MONTH FROM e.event_date)::INT AS month
        FROM flood_events_raw e
        JOIN lgas l ON e.lga_id = l.lga_id
        JOIN states s ON l.state_id = s.state_id
        WHERE e.event_date IS NOT NULL;
    """
    df_events = pd.read_sql_query(events_query, conn)
    df_events['event_flag'] = 1

    # -------------------------------------------------------------------------
    # Rule 1: State/Date Event-Based Matching
    # -------------------------------------------------------------------------
    df_merged = pd.merge(
        df_features,
        df_events,
        on=['state_id', 'year', 'month'],
        how='left'
    )
    df_merged['event_flag'] = df_merged['event_flag'].fillna(0).astype(int)

    # -------------------------------------------------------------------------
    # Rule 2: Physical/Environmental Threshold-Based Matching
    # -------------------------------------------------------------------------
    # Calculate dataset-level 75th percentile for antecedent precipitation
    api_75th = df_merged['antecedent_precip_index'].quantile(0.75)

    condition_severe_rain = df_merged['rainfall_anomaly_index'] > 1.5
    condition_high_vulnerability = df_merged['terrain_vulnerability_score'] > 0.6
    condition_peak_season = df_merged['peak_season_flag'] == 1
    condition_high_antecedent = df_merged['antecedent_precip_index'] > api_75th

    df_merged['threshold_flag'] = (
        condition_severe_rain & 
        condition_high_vulnerability & 
        (condition_peak_season | condition_high_antecedent)
    ).astype(int)

    # -------------------------------------------------------------------------
    # Rule 3: Hybrid Combination (OR Logic)
    # -------------------------------------------------------------------------
    df_merged['flood_risk_label'] = (
        (df_merged['event_flag'] == 1) | (df_merged['threshold_flag'] == 1)
    ).astype(int)

    # -------------------------------------------------------------------------
    # Class Balance Breakdown
    # -------------------------------------------------------------------------
    total_records = len(df_merged)
    class_counts = df_merged['flood_risk_label'].value_counts()
    no_risk = class_counts.get(0, 0)
    flood_risk = class_counts.get(1, 0)
    
    pct_no_risk = (no_risk / total_records) * 100
    pct_flood_risk = (flood_risk / total_records) * 100

    print("\n" + "="*50)
    print("HYBRID FLOOD RISK LABELING SUMMARY")
    print("="*50)
    print(f"Total Rows Analyzed:             {total_records}")
    print(f"State Event-Based Triggers:      {df_merged['event_flag'].sum()}")
    print(f"Environmental Threshold Triggers:{df_merged['threshold_flag'].sum()}")
    print(f"Combined Positive Labels:        {flood_risk}")
    print("-" * 50)
    print("CLASS BALANCE BEFORE SMOTE:")
    print(f" - Class 0 (No Flood Risk): {no_risk} ({pct_no_risk:.2f}%)")
    print(f" - Class 1 (Flood Risk):    {flood_risk} ({pct_flood_risk:.2f}%)")
    print("="*50 + "\n")

    # -------------------------------------------------------------------------
    # Write flood_risk_label back to PostgreSQL
    # -------------------------------------------------------------------------
    cursor = conn.cursor()
    
    print("Updating lga_monthly_features in database...")
    
    # Ensure target column exists
    cursor.execute("""
        ALTER TABLE lga_monthly_features 
        ADD COLUMN IF NOT EXISTS flood_risk_label INTEGER DEFAULT 0;
    """)

    # Batch update using temporary table for speed
    cursor.execute("""
        CREATE TEMP TABLE temp_labels (
            id INT PRIMARY KEY,
            flood_risk_label INT
        ) ON COMMIT DROP;
    """)

    records_to_update = list(zip(df_merged['id'], df_merged['flood_risk_label']))
    
    from psycopg2.extras import execute_values
    execute_values(
        cursor,
        "INSERT INTO temp_labels (id, flood_risk_label) VALUES %s",
        records_to_update
    )

    cursor.execute("""
        UPDATE lga_monthly_features f
        SET flood_risk_label = t.flood_risk_label
        FROM temp_labels t
        WHERE f.id = t.id;
    """)

    conn.commit()
    cursor.close()
    conn.close()
    print("✓ Successfully updated flood_risk_label in database.")

if __name__ == "__main__":
    generate_flood_labels()