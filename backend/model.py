import os
import xgboost as xgb

# Global variable to hold the trained model in memory
xgb_model = None

def init_model(app):
    """Loads the trained XGBoost model once at Flask application startup."""
    global xgb_model
    model_path = app.config["MODEL_PATH"]
    
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"XGBoost model file not found at: {model_path}")
    
    xgb_model = xgb.XGBClassifier()
    xgb_model.load_model(model_path)
    print(f"✓ Trained XGBoost model successfully loaded from {model_path}")

def get_model():
    """Returns the globally loaded XGBoost model instance."""
    return xgb_model