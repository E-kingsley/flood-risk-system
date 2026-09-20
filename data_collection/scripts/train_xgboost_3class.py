"""
train_xgboost_3class.py

Trains the multiclass (Low / Moderate / High) flood risk XGBoost model
on flood_risk_class, replacing the earlier binary flood_risk_label model.

PIPELINE
--------
1. Load lga_monthly_features joined with lgas.
2. Build the 14-feature matrix (same FEATURE_ORDER used by the Flask API):
   rainfall_anomaly_index, antecedent_precip_index,
   normalised_discharge_ratio, terrain_vulnerability_score,
   peak_season_flag, wetland_pct, built_up_pct,
   state_1, state_2, state_5, state_6, state_7,
   month_sin, month_cos
3. Stratified 70/15/15 train/val/test split (stratified on flood_risk_class
   so all three splits keep roughly the same class balance).
4. SMOTE oversampling on the TRAINING split only (never touch val/test —
   that would leak synthetic samples into evaluation).
5. GridSearchCV over max_depth, learning_rate, n_estimators, subsample,
   using macro F1 as the scoring metric (appropriate for imbalanced
   multiclass — treats all three classes as equally important rather
   than optimizing for the Low-risk majority).
6. Evaluate the best model on the held-out test set: accuracy, precision,
   recall, macro F1, one-vs-rest multiclass ROC-AUC, and a confusion
   matrix.
7. Serialize the trained model with joblib.

USAGE
-----
    pip install scikit-learn xgboost imbalanced-learn joblib psycopg2-binary pandas python-dotenv --break-system-packages
    python train_xgboost_3class.py

Requires the same .env as your other scripts:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
"""

import os

import joblib
import numpy as np
import pandas as pd
import psycopg2
import xgboost as xgb
from dotenv import load_dotenv
from imblearn.over_sampling import SMOTE
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    roc_auc_score,
)
from sklearn.model_selection import GridSearchCV, train_test_split
from sklearn.preprocessing import label_binarize

load_dotenv()

FEATURE_ORDER = [
    "rainfall_anomaly_index", "antecedent_precip_index", "normalised_discharge_ratio",
    "terrain_vulnerability_score", "peak_season_flag", "wetland_pct", "built_up_pct",
    "state_1", "state_2", "state_5", "state_6", "state_7", "month_sin", "month_cos",
]
STATE_DUMMY_IDS = [1, 2, 5, 6, 7]
CLASS_LABELS = {0: "Low Risk", 1: "Moderate Risk", 2: "High Risk"}
MODEL_OUTPUT_PATH = "flood_risk_xgb_model.joblib"

PARAM_GRID = {
    "max_depth": [3, 4, 5, 6],
    "learning_rate": [0.01, 0.05, 0.1],
    "n_estimators": [100, 200, 300],
    "subsample": [0.7, 0.8, 1.0],
}


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_dataset(conn) -> pd.DataFrame:
    query = """
        SELECT
            f.lga_id, f.year, f.month,
            f.rainfall_anomaly_index, f.antecedent_precip_index,
            f.normalised_discharge_ratio, f.terrain_vulnerability_score,
            f.peak_season_flag, f.flood_risk_class,
            l.wetland_pct, l.built_up_pct, l.state_id
        FROM lga_monthly_features f
        JOIN lgas l ON l.lga_id = f.lga_id
        WHERE f.flood_risk_class IS NOT NULL;
    """
    df = pd.read_sql(query, conn)
    df["flood_risk_class"] = df["flood_risk_class"].astype(int)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    for sid in STATE_DUMMY_IDS:
        df[f"state_{sid}"] = (df["state_id"] == sid).astype(int)

    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)

    return df[FEATURE_ORDER]


def main():
    conn = get_connection()
    try:
        print("Loading and joining data...")
        raw = load_dataset(conn)
        print(f"Loaded {len(raw)} rows.")
    finally:
        conn.close()

    X = build_features(raw)
    y = raw["flood_risk_class"]

    print("\nClass distribution (full dataset):")
    print(y.value_counts().sort_index().rename(CLASS_LABELS))

    # 70/15/15 stratified split: first carve off 30% for val+test,
    # then split that 30% evenly into val and test (15%/15% of total).
    X_train, X_temp, y_train, y_temp = train_test_split(
        X, y, test_size=0.30, stratify=y, random_state=42
    )
    X_val, X_test, y_val, y_test = train_test_split(
        X_temp, y_temp, test_size=0.50, stratify=y_temp, random_state=42
    )
    print(f"\nSplit sizes -> train: {len(X_train)}, val: {len(X_val)}, test: {len(X_test)}")

    print("\nApplying SMOTE to the training split only...")
    smote = SMOTE(random_state=42)
    X_train_res, y_train_res = smote.fit_resample(X_train, y_train)
    print("Post-SMOTE training class distribution:")
    print(y_train_res.value_counts().sort_index().rename(CLASS_LABELS))

    print("\nRunning GridSearchCV (this may take a few minutes)...")
    base_model = xgb.XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        eval_metric="mlogloss",
        random_state=42,
    )
    grid_search = GridSearchCV(
        estimator=base_model,
        param_grid=PARAM_GRID,
        scoring="f1_macro",
        cv=3,
        n_jobs=-1,
        verbose=1,
    )
    grid_search.fit(X_train_res, y_train_res)

    print(f"\nBest params: {grid_search.best_params_}")
    print(f"Best CV macro F1: {grid_search.best_score_:.4f}")

    best_model = grid_search.best_estimator_

    # Sanity-check on the validation set before touching test data.
    val_preds = best_model.predict(X_val)
    print(f"\nValidation macro F1: {f1_score(y_val, val_preds, average='macro'):.4f}")

    # Final, one-time evaluation on the held-out test set.
    print("\n" + "=" * 60)
    print("FINAL TEST SET EVALUATION")
    print("=" * 60)
    test_preds = best_model.predict(X_test)
    test_proba = best_model.predict_proba(X_test)

    print(f"Accuracy:  {accuracy_score(y_test, test_preds):.4f}")
    print(f"Macro F1:  {f1_score(y_test, test_preds, average='macro'):.4f}")

    y_test_bin = label_binarize(y_test, classes=[0, 1, 2])
    roc_auc = roc_auc_score(y_test_bin, test_proba, average="macro", multi_class="ovr")
    print(f"ROC-AUC (macro, one-vs-rest): {roc_auc:.4f}")

    print("\nPer-class precision / recall / F1:")
    print(classification_report(
        y_test, test_preds,
        target_names=[CLASS_LABELS[i] for i in sorted(CLASS_LABELS)],
    ))

    print("Confusion matrix (rows = actual, columns = predicted):")
    cm = confusion_matrix(y_test, test_preds)
    print(pd.DataFrame(
        cm,
        index=[f"Actual {CLASS_LABELS[i]}" for i in sorted(CLASS_LABELS)],
        columns=[f"Pred {CLASS_LABELS[i]}" for i in sorted(CLASS_LABELS)],
    ))

    print(f"\nSaving model to {MODEL_OUTPUT_PATH}...")
    joblib.dump(best_model, MODEL_OUTPUT_PATH)
    print("Done.")


if __name__ == "__main__":
    main()
