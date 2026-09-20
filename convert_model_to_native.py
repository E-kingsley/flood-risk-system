"""
convert_model_to_native.py

model.py loads the model with xgb_model.load_model(model_path), which
expects XGBoost's native JSON/UBJ format (produced by .save_model()) —
not a joblib pickle. This converts the already-trained model without
needing to retrain anything.

USAGE
-----
    python convert_model_to_native.py

Run this from the same folder flood_risk_xgb_model.joblib is sitting in
(your project root, based on your file explorer screenshot).
"""

import joblib

SOURCE_JOBLIB = "flood_risk_xgb_model.joblib"
OUTPUT_NATIVE = "flood_risk_xgb_model.json"

model = joblib.load(SOURCE_JOBLIB)
model.save_model(OUTPUT_NATIVE)

print(f"Converted {SOURCE_JOBLIB} -> {OUTPUT_NATIVE}")
print("Point your MODEL_PATH config/env variable at this new .json file.")
