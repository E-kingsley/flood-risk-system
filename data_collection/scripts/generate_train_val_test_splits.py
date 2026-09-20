import os
import psycopg2
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.model_selection import train_test_split
from imblearn.over_sampling import SMOTE

load_dotenv()

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )

def prepare_data():
    conn = get_db_connection()
    
    print("Fetching feature matrix with metadata from PostgreSQL...")
    query = """
        SELECT 
            f.lga_id,
            f.year,
            f.month,
            l.state_id,
            f.rainfall_anomaly_index,
            f.antecedent_precip_index,
            f.normalised_discharge_ratio,
            f.terrain_vulnerability_score,
            f.peak_season_flag,
            l.wetland_pct,
            l.built_up_pct,
            f.flood_risk_label
        FROM lga_monthly_features f
        JOIN lgas l ON f.lga_id = l.lga_id;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    print(f"Loaded {len(df)} total records from lga_monthly_features.")

    # -------------------------------------------------------------------------
    # 1. Feature Engineering & Metadata Extraction
    # -------------------------------------------------------------------------
    # Separate traceability metadata BEFORE feature transformations
    metadata = df[['lga_id', 'year', 'month', 'state_id']].copy()

    # One-hot encode state_id for feature matrix X
    df_encoded = pd.get_dummies(df, columns=['state_id'], prefix='state', drop_first=False)
    
    # Cyclical encoding for month (sin/cos transformation preserving seasonality)
    df_encoded['month_sin'] = np.sin(2 * np.pi * df_encoded['month'] / 12.0)
    df_encoded['month_cos'] = np.cos(2 * np.pi * df_encoded['month'] / 12.0)

    # Drop non-feature ID/time columns from X
    X = df_encoded.drop(columns=['lga_id', 'year', 'month', 'flood_risk_label'])
    y = df_encoded['flood_risk_label'].astype(int)

    # -------------------------------------------------------------------------
    # 2. Stratified Train / Validation / Test Split (70% / 15% / 15%)
    # -------------------------------------------------------------------------
    # Step 1: Split off Test set (15%) along with Metadata
    X_temp, X_test, y_temp, y_test, meta_temp, meta_test = train_test_split(
        X, y, metadata, test_size=0.15, random_state=42, stratify=y
    )

    # Step 2: Split remaining 85% into Train (70%) and Val (15%)
    val_ratio_relative = 0.15 / 0.85
    X_train, X_val, y_train, y_val, meta_train, meta_val = train_test_split(
        X_temp, y_temp, meta_temp, test_size=val_ratio_relative, random_state=42, stratify=y_temp
    )

    # -------------------------------------------------------------------------
    # 3. Apply SMOTE to Training Data ONLY
    # -------------------------------------------------------------------------
    smote = SMOTE(random_state=42)
    X_train_resampled, y_train_resampled = smote.fit_resample(X_train, y_train)

    # Reconstruct metadata for SMOTE synthetic samples
    # Original training rows keep their original metadata; synthetic rows get -1 placeholders
    n_original = len(meta_train)
    n_synthetic = len(X_train_resampled) - n_original

    synthetic_meta = pd.DataFrame({
        'lga_id': [-1] * n_synthetic,
        'year': [-1] * n_synthetic,
        'month': [-1] * n_synthetic,
        'state_id': [-1] * n_synthetic
    })
    meta_train_resampled = pd.concat([meta_train.reset_index(drop=True), synthetic_meta], ignore_index=True)

    # Convert back to DataFrames to retain column names cleanly
    X_train_resampled = pd.DataFrame(X_train_resampled, columns=X_train.columns)
    y_train_resampled = pd.Series(y_train_resampled, name='flood_risk_label')

    # -------------------------------------------------------------------------
    # 4. Print Summary Statistics
    # -------------------------------------------------------------------------
    def print_split_info(name, y_data):
        total = len(y_data)
        pos = np.sum(y_data == 1)
        neg = np.sum(y_data == 0)
        pos_pct = (pos / total) * 100
        neg_pct = (neg / total) * 100
        print(f"  └─ {name:<22}: {total:>6} samples | Class 0: {neg:>5} ({neg_pct:.1f}%) | Class 1: {pos:>5} ({pos_pct:.1f}%)")

    print("\n" + "=" * 80)
    print("DATA SPLIT & SMOTE RESAMPLING SUMMARY")
    print("=" * 80)
    print("BEFORE SMOTE:")
    print_split_info("Train (Original)", y_train)
    print_split_info("Validation", y_val)
    print_split_info("Test", y_test)
    
    print("\nAFTER SMOTE (Training Set Only):")
    print_split_info("Train (Resampled)", y_train_resampled)
    print("=" * 80 + "\n")

    # -------------------------------------------------------------------------
    # 5. Save Processed Datasets & Metadata
    # -------------------------------------------------------------------------
    output_dir = os.path.join("data_collection", "processed")
    os.makedirs(output_dir, exist_ok=True)

    # Save feature matrices and target labels
    X_train_resampled.to_csv(os.path.join(output_dir, "X_train.csv"), index=False)
    y_train_resampled.to_csv(os.path.join(output_dir, "y_train.csv"), index=False)
    meta_train_resampled.to_csv(os.path.join(output_dir, "meta_train.csv"), index=False)

    X_val.to_csv(os.path.join(output_dir, "X_val.csv"), index=False)
    y_val.to_csv(os.path.join(output_dir, "y_val.csv"), index=False)
    meta_val[['lga_id', 'year', 'month']].to_csv(os.path.join(output_dir, "meta_val.csv"), index=False)

    X_test.to_csv(os.path.join(output_dir, "X_test.csv"), index=False)
    y_test.to_csv(os.path.join(output_dir, "y_test.csv"), index=False)
    meta_test[['lga_id', 'year', 'month']].to_csv(os.path.join(output_dir, "meta_test.csv"), index=False)

    print(f"✓ Datasets and metadata successfully exported to: {os.path.abspath(output_dir)}")
    print(f"  - X_train.csv {X_train_resampled.shape} | meta_train.csv {meta_train_resampled.shape}")
    print(f"  - X_val.csv   {X_val.shape} | meta_val.csv {meta_val[['lga_id', 'year', 'month']].shape}")
    print(f"  - X_test.csv  {X_test.shape} | meta_test.csv {meta_test[['lga_id', 'year', 'month']].shape}")

if __name__ == "__main__":
    prepare_data()