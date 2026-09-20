import os
import psycopg2
import pandas as pd
import numpy as np
import xgboost as xgb
from dotenv import load_dotenv
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    confusion_matrix
)

load_dotenv()

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )

def evaluate_subset():
    data_dir = os.path.join("data_collection", "processed")
    model_path = os.path.join("data_collection", "models", "flood_risk_xgb_model.json")

    # 1. Load Test Features, Labels, Metadata, and Model
    print("Loading test features, labels, metadata, and trained XGBoost model...")
    X_test = pd.read_csv(os.path.join(data_dir, "X_test.csv"))
    y_test = pd.read_csv(os.path.join(data_dir, "y_test.csv")).values.ravel()
    meta_test = pd.read_csv(os.path.join(data_dir, "meta_test.csv"))

    model = xgb.XGBClassifier()
    model.load_model(model_path)

    # 2. Query PostgreSQL for exact (lga_id, year, month) recorded flood events
    conn = get_db_connection()
    events_query = """
        SELECT DISTINCT 
            e.lga_id,
            EXTRACT(YEAR FROM e.event_date)::INT AS year,
            EXTRACT(MONTH FROM e.event_date)::INT AS month
        FROM flood_events_raw e
        WHERE e.lga_id IS NOT NULL AND e.event_date IS NOT NULL;
    """
    df_events = pd.read_sql_query(events_query, conn)
    conn.close()

    # Create a set of (lga_id, year, month) tuples for exact matching
    recorded_event_tuples = set(zip(df_events["lga_id"], df_events["year"], df_events["month"]))

    # 3. Filter Test Set for Event-Only Subset using Exact Match via meta_test
    event_positive_mask = []
    for idx, row in meta_test.iterrows():
        is_pos = (y_test[idx] == 1)
        lga_year_month = (row["lga_id"], row["year"], row["month"])
        
        if not is_pos:
            # Keep all class 0 (negatives)
            event_positive_mask.append(True)
        else:
            # Keep class 1 ONLY if it directly matches a recorded flood event tuple
            event_positive_mask.append(lga_year_month in recorded_event_tuples)

    event_positive_mask = np.array(event_positive_mask)

    # Subset features and labels
    X_test_subset = X_test[event_positive_mask]
    y_test_subset = y_test[event_positive_mask]

    # 4. Model Inference
    y_pred_full = model.predict(X_test)
    y_proba_full = model.predict_proba(X_test)[:, 1]

    y_pred_sub = model.predict(X_test_subset)
    y_proba_sub = model.predict_proba(X_test_subset)[:, 1]

    # 5. Compute Metrics
    def calc_metrics(y_true, y_pred, y_proba):
        return {
            "Accuracy": accuracy_score(y_true, y_pred),
            "ROC-AUC": roc_auc_score(y_true, y_proba),
            "Precision": precision_score(y_true, y_pred, pos_label=1, zero_division=0),
            "Recall": recall_score(y_true, y_pred, pos_label=1, zero_division=0),
            "F1-Score": f1_score(y_true, y_pred, pos_label=1, zero_division=0),
            "CM": confusion_matrix(y_true, y_pred)
        }

    m_full = calc_metrics(y_test, y_pred_full, y_proba_full)
    m_sub = calc_metrics(y_test_subset, y_pred_sub, y_proba_sub)

    # 6. Print Side-by-Side Comparison
    print("\n" + "=" * 75)
    print("EVALUATION COMPARISON: FULL TEST SET vs. EVENT-ONLY POSITIVES SUBSET")
    print("=" * 75)
    print(f"{'Metric':<20} | {'Full Test Set':<22} | {'Event-Only Subset':<22}")
    print("-" * 75)
    print(f"{'Total Samples':<20} | {len(y_test):<22} | {len(y_test_subset):<22}")
    print(f"{'Positive Samples (1)':<20} | {np.sum(y_test==1):<22} | {np.sum(y_test_subset==1):<22}")
    print(f"{'Negative Samples (0)':<20} | {np.sum(y_test==0):<22} | {np.sum(y_test_subset==0):<22}")
    print("-" * 75)
    print(f"{'Accuracy':<20} | {m_full['Accuracy']:<22.4f} | {m_sub['Accuracy']:<22.4f}")
    print(f"{'ROC-AUC':<20} | {m_full['ROC-AUC']:<22.4f} | {m_sub['ROC-AUC']:<22.4f}")
    print(f"{'Precision (Class 1)':<20} | {m_full['Precision']:<22.4f} | {m_sub['Precision']:<22.4f}")
    print(f"{'Recall (Class 1)':<20} | {m_full['Recall']:<22.4f} | {m_sub['Recall']:<22.4f}")
    print(f"{'F1-Score (Class 1)':<20} | {m_full['F1-Score']:<22.4f} | {m_sub['F1-Score']:<22.4f}")
    print("=" * 75)

    print("\nCONFUSION MATRIX (Full Test Set):")
    print(f"  [ TN: {m_full['CM'][0][0]:>5} | FP: {m_full['CM'][0][1]:>5} ]")
    print(f"  [ FN: {m_full['CM'][1][0]:>5} | TP: {m_full['CM'][1][1]:>5} ]")

    print("\nCONFUSION MATRIX (Event-Only Positives Subset):")
    print(f"  [ TN: {m_sub['CM'][0][0]:>5} | FP: {m_sub['CM'][0][1]:>5} ]")
    print(f"  [ FN: {m_sub['CM'][1][0]:>5} | TP: {m_sub['CM'][1][1]:>5} ]\n")

if __name__ == "__main__":
    evaluate_subset()