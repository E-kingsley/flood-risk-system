import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import xgboost as xgb
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    classification_report,
    confusion_matrix
)

def train_and_evaluate():
    data_dir = os.path.join("data_collection", "processed")
    models_dir = os.path.join("data_collection", "models")
    os.makedirs(models_dir, exist_ok=True)

    # 1. Load Processed Datasets
    print("Loading processed train, validation, and test datasets...")
    X_train = pd.read_csv(os.path.join(data_dir, "X_train.csv"))
    y_train = pd.read_csv(os.path.join(data_dir, "y_train.csv")).values.ravel()

    X_val = pd.read_csv(os.path.join(data_dir, "X_val.csv"))
    y_val = pd.read_csv(os.path.join(data_dir, "y_val.csv")).values.ravel()

    X_test = pd.read_csv(os.path.join(data_dir, "X_test.csv"))
    y_test = pd.read_csv(os.path.join(data_dir, "y_test.csv")).values.ravel()

    print(f"Dataset shapes: Train={X_train.shape}, Val={X_val.shape}, Test={X_test.shape}\n")

    # 2. Instantiate and Train XGBoost Classifier with Early Stopping
    model = xgb.XGBClassifier(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        early_stopping_rounds=30,
        random_state=42,
        use_label_encoder=False
    )

    print("Training XGBoost Classifier...")
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=50
    )
    print(f"✓ Training complete. Best iteration: {model.best_iteration}\n")

    # 3. Evaluate on the Held-Out Test Set
    print("Evaluating model on test dataset...")
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    accuracy = accuracy_score(y_test, y_pred)
    precision = precision_score(y_test, y_pred, pos_label=1, zero_division=0)
    recall = recall_score(y_test, y_pred, pos_label=1)
    f1 = f1_score(y_test, y_pred, pos_label=1)
    roc_auc = roc_auc_score(y_test, y_proba)
    cm = confusion_matrix(y_test, y_pred)

    print("=" * 60)
    print("XGBOOST TEST SET PERFORMANCE SUMMARY")
    print("=" * 60)
    print(f"Overall Accuracy:          {accuracy:.4f}")
    print(f"ROC-AUC Score:             {roc_auc:.4f}")
    print("-" * 60)
    print("TARGET CLASS (Flood Risk = Class 1) METRICS:")
    print(f"  └─ Precision:            {precision:.4f}")
    print(f"  └─ Recall (Sensitivity): {recall:.4f}")
    print(f"  └─ F1-Score:             {f1:.4f}")
    print("=" * 60)
    print("\nClassification Report:\n", classification_report(y_test, y_pred, digits=4))

    print("Confusion Matrix:")
    print(f" [ TN: {cm[0][0]:>5} | FP: {cm[0][1]:>5} ]")
    print(f" [ FN: {cm[1][0]:>5} | TP: {cm[1][1]:>5} ]\n")

    # 4. Plot and Save Feature Importance Chart
    plt.figure(figsize=(10, 6))
    importances = model.feature_importances_
    feature_names = X_train.columns

    importance_df = pd.DataFrame({
        "Feature": feature_names,
        "Importance": importances
    }).sort_values(by="Importance", ascending=False).head(15)

    sns.barplot(
        data=importance_df,
        x="Importance",
        y="Feature",
        palette="crest"
    )
    plt.title("Top 15 Feature Importances (XGBoost Flood Risk Model)", fontsize=14, fontweight="bold")
    plt.xlabel("Gini Importance (Gain)")
    plt.ylabel("Features")
    plt.tight_layout()

    importance_plot_path = os.path.join(models_dir, "feature_importance.png")
    plt.savefig(importance_plot_path, dpi=300)
    plt.close()
    print(f"✓ Feature importance chart saved to: {importance_plot_path}")

    # 5. Save Trained Model Native JSON Format
    model_output_path = os.path.join(models_dir, "flood_risk_xgb_model.json")
    model.save_model(model_output_path)
    print(f"✓ Trained model successfully exported to: {model_output_path}")

if __name__ == "__main__":
    train_and_evaluate()