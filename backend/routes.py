from flask import Blueprint, jsonify, request
from backend.db import get_db
from backend.model import get_model
from backend.forecast_service import compute_live_forecast_features, classify_mode, ForecastUnavailableError
import numpy as np
import xgboost as xgb

api_bp = Blueprint("api", __name__, url_prefix="/api")

FEATURE_ORDER = [
    'rainfall_anomaly_index', 'antecedent_precip_index', 'normalised_discharge_ratio',
    'terrain_vulnerability_score', 'peak_season_flag', 'wetland_pct', 'built_up_pct',
    'state_1', 'state_2', 'state_5', 'state_6', 'state_7', 'month_sin', 'month_cos'
]
STATE_DUMMY_IDS = [1, 2, 5, 6, 7]
RISK_LABELS = {0: "Low Risk", 1: "Moderate Risk", 2: "High Risk"}


@api_bp.route("/health", methods=["GET"])
def health_check():
    """Health endpoint verifying database connectivity and model availability."""
    model_loaded = get_model() is not None
    db_status = "ok"

    # Test database connectivity
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1;")
    except Exception as e:
        db_status = f"error: {str(e)}"

    return jsonify({
        "status": "ok",
        "model_loaded": model_loaded,
        "database": db_status
    }), 200


@api_bp.route("/lgas", methods=["GET"])
def get_lgas():
    """
    Returns a JSON array of all 105 LGAs with their lga_id, lga_name,
    state_id, and state_name, sorted alphabetically by state_name, then lga_name.
    """
    try:
        conn = get_db()
        query = """
            SELECT 
                l.lga_id,
                l.name AS lga_name,
                l.state_id,
                s.name AS state_name
            FROM lgas l
            JOIN states s ON l.state_id = s.state_id
            ORDER BY s.name ASC, l.name ASC;
        """
        
        with conn.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()

        lgas = [
            {
                "lga_id": row[0],
                "lga_name": row[1],
                "state_id": row[2],
                "state_name": row[3]
            }
            for row in rows
        ]

        return jsonify(lgas), 200

    except Exception as e:
        # Roll back transaction on error to reset pool connection state
        if 'conn' in locals() and conn:
            conn.rollback()

        return jsonify({
            "error": "Failed to fetch LGAs from database.",
            "details": str(e)
        }), 500


def _get_max_historical_period(conn):
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT year, month FROM lga_monthly_features
            ORDER BY year DESC, month DESC LIMIT 1;
        """)
        return cursor.fetchone()


def _get_historical_features(conn, lga_id, year, month):
    """Returns (feature_values dict, lga_name) from the historical database, or (None, None) if not found."""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT rainfall_anomaly_index, antecedent_precip_index,
                   normalised_discharge_ratio, terrain_vulnerability_score,
                   peak_season_flag
            FROM lga_monthly_features
            WHERE lga_id = %s AND year = %s AND month = %s;
        """, (lga_id, year, month))
        monthly_row = cursor.fetchone()

        if monthly_row is None:
            return None, None

        cursor.execute("""
            SELECT wetland_pct, built_up_pct, state_id, name
            FROM lgas WHERE lga_id = %s;
        """, (lga_id,))
        lga_row = cursor.fetchone()

        if lga_row is None:
            return None, None

    rainfall_anomaly, api_idx, discharge_ratio, terrain_vuln, peak_flag = monthly_row
    wetland_pct, built_up_pct, state_id, lga_name = lga_row

    state_dummies = {f"state_{sid}": (1 if state_id == sid else 0) for sid in STATE_DUMMY_IDS}
    month_sin = np.sin(2 * np.pi * month / 12)
    month_cos = np.cos(2 * np.pi * month / 12)

    feature_values = {
        'rainfall_anomaly_index': float(rainfall_anomaly) if rainfall_anomaly is not None else 0.0,
        'antecedent_precip_index': float(api_idx) if api_idx is not None else 0.0,
        'normalised_discharge_ratio': float(discharge_ratio) if discharge_ratio is not None else 1.0,
        'terrain_vulnerability_score': float(terrain_vuln) if terrain_vuln is not None else 0.0,
        'peak_season_flag': int(bool(peak_flag)),
        'wetland_pct': float(wetland_pct) if wetland_pct is not None else 0.0,
        'built_up_pct': float(built_up_pct) if built_up_pct is not None else 0.0,
        **state_dummies,
        'month_sin': float(month_sin),
        'month_cos': float(month_cos),
    }

    return feature_values, lga_name


@api_bp.route("/predict", methods=["GET"])
def predict():
    """
    Predicts flood risk (Low / Moderate / High) for a given LGA, month,
    and year.

    Query params: lga_id (int), month (int, 1-12), year (int).

    If the requested year/month is within the historical dataset
    (2000 - most recent month on record), features are read from the
    database. If it's beyond that, live forecast data is fetched from
    Open-Meteo instead (see forecast_service.py) — the response's
    "mode" field indicates which path was used.
    """
    try:
        lga_id = request.args.get("lga_id", type=int)
        month = request.args.get("month", type=int)
        year = request.args.get("year", type=int)

        if lga_id is None or month is None or year is None:
            return jsonify({"error": "lga_id, month, and year query parameters are required."}), 400
        if not (1 <= month <= 12):
            return jsonify({"error": "month must be between 1 and 12."}), 400

        conn = get_db()
        max_year, max_month = _get_max_historical_period(conn)

        if (year, month) <= (max_year, max_month):
            feature_values, lga_name = _get_historical_features(conn, lga_id, year, month)
            if feature_values is None:
                return jsonify({
                    "error": f"No environmental data found for lga_id={lga_id}, year={year}, month={month}, "
                             "or lga_id not found."
                }), 404
            mode = "historical"
        else:
            try:
                forecast_result = compute_live_forecast_features(lga_id, year, month)
            except ForecastUnavailableError as e:
                return jsonify({
                    "error": "Live forecast unavailable for this request.",
                    "details": str(e)
                }), 502

            lga_name = forecast_result.pop("lga_name")
            feature_values = forecast_result
            # "recent" = month already ended (observed weather),
            # "forecast" = current or future month (seasonal forecast)
            mode = classify_mode(year, month)

        feature_vector = np.array([[feature_values[f] for f in FEATURE_ORDER]], dtype=float)

        model = get_model()
        proba = model.predict_proba(feature_vector)[0]
        predicted_class = int(np.argmax(proba))

        booster = model.get_booster()
        dmatrix = xgb.DMatrix(feature_vector, feature_names=FEATURE_ORDER)
        contribs = booster.predict(dmatrix, pred_contribs=True)
        class_contribs = contribs[0][predicted_class]
        feature_contribs = list(zip(FEATURE_ORDER, class_contribs[:-1]))  # last value is bias term
        top_features = sorted(feature_contribs, key=lambda x: abs(x[1]), reverse=True)[:3]

        return jsonify({
            "lga_id": lga_id,
            "lga_name": lga_name,
            "year": year,
            "month": month,
            "mode": mode,
            "predicted_risk_class": predicted_class,
            "predicted_risk_label": RISK_LABELS.get(predicted_class, str(predicted_class)),
            "probabilities": {
                "low_risk": round(float(proba[0]), 4),
                "moderate_risk": round(float(proba[1]), 4),
                "high_risk": round(float(proba[2]), 4)
            },
            "top_contributing_features": [
                {"feature": f, "contribution": round(float(c), 4)} for f, c in top_features
            ],
            # The actual input values the model saw (first 7 features), so
            # unexpected predictions can be checked against real numbers.
            "input_features": {
                f: round(float(feature_values[f]), 3) for f in FEATURE_ORDER[:7]
            }
        }), 200

    except Exception as e:
        if 'conn' in locals() and conn:
            conn.rollback()
        return jsonify({
            "error": "Failed to generate prediction.",
            "details": str(e)
        }), 500


@api_bp.route("/history", methods=["GET"])
def history():
    """
    Returns the model's predicted risk for every historical month of one LGA.

    Query params: lga_id (int).
    Response: {"lga_id", "lga_name", "records": [
        {"year", "month", "predicted_risk_class", "predicted_risk_label",
         "probabilities": {"low_risk", "moderate_risk", "high_risk"}}, ...
    ]}
    """
    try:
        lga_id = request.args.get("lga_id", type=int)
        if lga_id is None:
            return jsonify({"error": "lga_id query parameter is required."}), 400

        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT wetland_pct, built_up_pct, state_id, name
                FROM lgas WHERE lga_id = %s;
            """, (lga_id,))
            lga_row = cursor.fetchone()
            if lga_row is None:
                return jsonify({"error": f"lga_id={lga_id} not found."}), 404

            cursor.execute("""
                SELECT year, month, rainfall_anomaly_index, antecedent_precip_index,
                       normalised_discharge_ratio, terrain_vulnerability_score,
                       peak_season_flag
                FROM lga_monthly_features
                WHERE lga_id = %s
                ORDER BY year, month;
            """, (lga_id,))
            rows = cursor.fetchall()

        if not rows:
            return jsonify({"error": f"No historical data found for lga_id={lga_id}."}), 404

        wetland_pct, built_up_pct, state_id, lga_name = lga_row
        state_dummies = {f"state_{sid}": (1 if state_id == sid else 0) for sid in STATE_DUMMY_IDS}

        vectors = []
        for year, month, rai, api_idx, ndr, tvs, peak in rows:
            values = {
                'rainfall_anomaly_index': float(rai) if rai is not None else 0.0,
                'antecedent_precip_index': float(api_idx) if api_idx is not None else 0.0,
                'normalised_discharge_ratio': float(ndr) if ndr is not None else 1.0,
                'terrain_vulnerability_score': float(tvs) if tvs is not None else 0.0,
                'peak_season_flag': int(bool(peak)),
                'wetland_pct': float(wetland_pct) if wetland_pct is not None else 0.0,
                'built_up_pct': float(built_up_pct) if built_up_pct is not None else 0.0,
                **state_dummies,
                'month_sin': float(np.sin(2 * np.pi * month / 12)),
                'month_cos': float(np.cos(2 * np.pi * month / 12)),
            }
            vectors.append([values[f] for f in FEATURE_ORDER])

        model = get_model()
        proba = model.predict_proba(np.array(vectors, dtype=float))
        classes = np.argmax(proba, axis=1)

        records = [
            {
                "year": int(year),
                "month": int(month),
                "predicted_risk_class": int(cls),
                "predicted_risk_label": RISK_LABELS.get(int(cls), str(int(cls))),
                "probabilities": {
                    "low_risk": round(float(p[0]), 4),
                    "moderate_risk": round(float(p[1]), 4),
                    "high_risk": round(float(p[2]), 4),
                },
            }
            for (year, month, *_), cls, p in zip(rows, classes, proba)
        ]

        return jsonify({"lga_id": lga_id, "lga_name": lga_name, "records": records}), 200

    except Exception as e:
        if 'conn' in locals() and conn:
            conn.rollback()
        return jsonify({
            "error": "Failed to generate history.",
            "details": str(e)
        }), 500


@api_bp.route("/predict-region", methods=["GET"])
def predict_region():
    """
    Predicts flood risk for every LGA at once, for a single given month/year.

    Only supports dates within the historical dataset (2000 - most recent
    month on record). Running a live forecast for all 105 LGAs in one
    request would mean 105 separate external API calls, which is too slow
    and fragile for a single HTTP request — use the single-LGA /predict
    endpoint for future dates instead.

    Query params: month (int, 1-12), year (int).
    Response: {"year", "month", "mode", "results": [
        {"lga_id", "lga_name", "predicted_risk_class", "predicted_risk_label",
         "probabilities": {"low_risk", "moderate_risk", "high_risk"}}, ...
    ]}
    """
    try:
        month = request.args.get("month", type=int)
        year = request.args.get("year", type=int)

        if month is None or year is None:
            return jsonify({"error": "month and year query parameters are required."}), 400
        if not (1 <= month <= 12):
            return jsonify({"error": "month must be between 1 and 12."}), 400

        conn = get_db()
        max_year, max_month = _get_max_historical_period(conn)

        if (year, month) > (max_year, max_month):
            return jsonify({
                "error": "Regional snapshot is only available for historical dates.",
                "details": f"Latest historical data on record is {max_year}-{max_month:02d}. "
                           "For future dates, use the single-LGA prediction on the Predict tab instead."
            }), 400

        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT l.lga_id, l.name, l.wetland_pct, l.built_up_pct, l.state_id,
                       f.rainfall_anomaly_index, f.antecedent_precip_index,
                       f.normalised_discharge_ratio, f.terrain_vulnerability_score,
                       f.peak_season_flag
                FROM lgas l
                JOIN lga_monthly_features f ON f.lga_id = l.lga_id
                WHERE f.year = %s AND f.month = %s
                ORDER BY l.lga_id;
            """, (year, month))
            rows = cursor.fetchall()

        if not rows:
            return jsonify({"error": f"No historical data found for {year}-{month:02d}."}), 404

        month_sin = float(np.sin(2 * np.pi * month / 12))
        month_cos = float(np.cos(2 * np.pi * month / 12))

        vectors = []
        lga_meta = []
        for lga_id, name, wetland_pct, built_up_pct, state_id, rai, api_idx, ndr, tvs, peak in rows:
            state_dummies = {f"state_{sid}": (1 if state_id == sid else 0) for sid in STATE_DUMMY_IDS}
            values = {
                'rainfall_anomaly_index': float(rai) if rai is not None else 0.0,
                'antecedent_precip_index': float(api_idx) if api_idx is not None else 0.0,
                'normalised_discharge_ratio': float(ndr) if ndr is not None else 1.0,
                'terrain_vulnerability_score': float(tvs) if tvs is not None else 0.0,
                'peak_season_flag': int(bool(peak)),
                'wetland_pct': float(wetland_pct) if wetland_pct is not None else 0.0,
                'built_up_pct': float(built_up_pct) if built_up_pct is not None else 0.0,
                **state_dummies,
                'month_sin': month_sin,
                'month_cos': month_cos,
            }
            vectors.append([values[f] for f in FEATURE_ORDER])
            lga_meta.append((lga_id, name))

        # One vectorized prediction across all 105 LGAs at once, rather
        # than 105 separate model calls.
        model = get_model()
        proba = model.predict_proba(np.array(vectors, dtype=float))
        classes = np.argmax(proba, axis=1)

        results = [
            {
                "lga_id": lga_id,
                "lga_name": name,
                "predicted_risk_class": int(cls),
                "predicted_risk_label": RISK_LABELS.get(int(cls), str(int(cls))),
                "probabilities": {
                    "low_risk": round(float(p[0]), 4),
                    "moderate_risk": round(float(p[1]), 4),
                    "high_risk": round(float(p[2]), 4),
                },
            }
            for (lga_id, name), cls, p in zip(lga_meta, classes, proba)
        ]

        return jsonify({"year": year, "month": month, "mode": "historical", "results": results}), 200

    except Exception as e:
        if 'conn' in locals() and conn:
            conn.rollback()
        return jsonify({
            "error": "Failed to generate regional snapshot.",
            "details": str(e)
        }), 500