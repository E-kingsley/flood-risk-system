"""
forecast_service.py

Computes the model's input features for a FUTURE month (one not yet in
lga_monthly_features), using live forecast data from Open-Meteo instead
of the historical database. Uses the exact same formulas as
compute_engineered_features.py so forecast-mode inputs stay on the same
scale the model was trained on:

    rainfall_anomaly_index      = (month's rainfall - rain_month_mean) / rain_month_std
    antecedent_precip_index     = 0.5*(t-1 rainfall) + 0.3*(t-2) + 0.2*(t-3)
    normalised_discharge_ratio  = month's discharge / discharge_month_mean
    peak_season_flag            = 1 if month in [7, 8, 9, 10] else 0

rain_month_mean/std and discharge_month_mean come from the
lga_month_climatology table (built by build_climatology_table.py) —
the same historical baseline used for 2000-2024 data.

DATA SOURCES
------------
Rainfall for months that already ended but are not in the database yet
(e.g. Jan 2025 up to last week): Open-Meteo Archive API (ERA5 observed
weather) - https://archive-api.open-meteo.com/v1/archive

Rainfall forecast: Open-Meteo Seasonal Forecast API (ECMWF EC46 + SEAS5,
seamless), which returns both recent-past and forecast data for any
requested date range — https://seasonal-api.open-meteo.com/v1/seasonal

Discharge forecast: Open-Meteo Flood API (GloFAS v4 seamless), same
source used for historical discharge data, extended with forecast
data up to 7 months ahead — https://flood-api.open-meteo.com/v1/flood

Both are free for non-commercial use, no API key required.
"""

import calendar
import math
import os
import time
from datetime import date, timedelta

import psycopg2
import requests
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

load_dotenv()

SEASONAL_API_URL = "https://seasonal-api.open-meteo.com/v1/seasonal"
ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"
FLOOD_API_URL = "https://flood-api.open-meteo.com/v1/flood"

PEAK_SEASON_MONTHS = {7, 8, 9, 10}

# The archive (observed weather) API lags real time by a few days, so a
# month only counts as "recent" once it ended at least this many days ago.
RECENT_LAG_DAYS = 7


def classify_mode(year, month):
    """
    Decides which weather source applies to a month that is NOT in the
    historical database (i.e. after the last month in lga_monthly_features):

      "recent"   - the month has already ended, so observed weather exists
                   (Open-Meteo Archive API, ERA5 reanalysis)
      "forecast" - the month is in progress or in the future, so the
                   Seasonal Forecast API is used
    """
    _, last_day = month_bounds(year, month)
    if last_day <= date.today() - timedelta(days=RECENT_LAG_DAYS):
        return "recent"
    return "forecast"


# Same five gauge points, and the same nearest-station rule, that
# build_monthly_features.py used to assign river discharge to each LGA.
# The historical discharge baseline is per-station, so live discharge must
# be read at the station too - not at the LGA centroid, which sits on a
# different (much smaller) river cell and gives ratios far below 1.
DISCHARGE_STATIONS = [
    {"name": "Niger at Onitsha", "lat": 6.1500, "lon": 6.7833},
    {"name": "Benue at Makurdi", "lat": 7.7333, "lon": 8.5333},
    {"name": "Niger at Lokoja", "lat": 7.7800, "lon": 6.7600},
    {"name": "Forcados at Warri", "lat": 5.5167, "lon": 5.7500},
    {"name": "Bonny River at Port Harcourt", "lat": 4.7719, "lon": 7.0134},
]


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def nearest_discharge_station(lat, lon):
    return min(
        DISCHARGE_STATIONS,
        key=lambda s: _haversine_km(lat, lon, s["lat"], s["lon"]),
    )


class ForecastUnavailableError(Exception):
    """Raised when live forecast data can't be obtained for the request."""
    pass


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def get_max_historical_period(conn):
    """Returns (year, month) of the most recent row in lga_monthly_features."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT year, month FROM lga_monthly_features
            ORDER BY year DESC, month DESC LIMIT 1;
        """)
        return cur.fetchone()


def is_future_month(conn, year, month):
    """True if (year, month) is beyond the historical dataset's coverage."""
    max_year, max_month = get_max_historical_period(conn)
    return (year, month) > (max_year, max_month)


def get_lga_forecast_inputs(conn, lga_id):
    """Fetches lat/lon, static physical features, and name for an LGA."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT latitude, longitude, wetland_pct, built_up_pct, state_id, name
            FROM lgas WHERE lga_id = %s;
        """, (lga_id,))
        row = cur.fetchone()

    if row is None:
        raise ForecastUnavailableError(f"lga_id={lga_id} not found.")

    lat, lon, wetland_pct, built_up_pct, state_id, name = row
    if lat is None or lon is None:
        raise ForecastUnavailableError(
            f"No coordinates on file for lga_id={lga_id} ({name}) — "
            "run add_lga_coordinates.py first."
        )

    with conn.cursor() as cur:
        # terrain_vulnerability_score is a static physical characteristic —
        # every historical row for this LGA should carry the same value, so
        # any single row works as the source.
        cur.execute("""
            SELECT terrain_vulnerability_score FROM lga_monthly_features
            WHERE lga_id = %s LIMIT 1;
        """, (lga_id,))
        terrain_row = cur.fetchone()

    terrain_vulnerability_score = float(terrain_row[0]) if terrain_row else 0.0

    return {
        "latitude": float(lat),
        "longitude": float(lon),
        "wetland_pct": float(wetland_pct) if wetland_pct is not None else 0.0,
        "built_up_pct": float(built_up_pct) if built_up_pct is not None else 0.0,
        "state_id": state_id,
        "name": name,
        "terrain_vulnerability_score": terrain_vulnerability_score,
    }


def get_climatology(conn, lga_id, month):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT rain_month_mean, rain_month_std, discharge_month_mean
            FROM lga_month_climatology
            WHERE lga_id = %s AND month = %s;
        """, (lga_id, month))
        row = cur.fetchone()

    if row is None:
        raise ForecastUnavailableError(
            f"No climatology baseline for lga_id={lga_id}, month={month} — "
            "run build_climatology_table.py first."
        )

    rain_mean, rain_std, discharge_mean = row
    return {
        "rain_month_mean": float(rain_mean) if rain_mean is not None else 0.0,
        "rain_month_std": float(rain_std) if rain_std is not None else 0.0,
        "discharge_month_mean": float(discharge_mean) if discharge_mean is not None else 0.0,
    }


def month_bounds(year, month):
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _get_json_with_retry(url, params, source_label):
    """GET a JSON response, retrying transient failures a few times."""
    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            last_error = e
            # A 4xx (e.g. date out of range) will not succeed on retry.
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500:
                break
            time.sleep(1.5 * (attempt + 1))
    raise ForecastUnavailableError(f"{source_label} request failed: {last_error}")


def _fetch_daily_precipitation(url, source_label, lat, lon, start, end):
    """Requests daily precipitation_sum from one Open-Meteo endpoint."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "auto",
    }

    data = _get_json_with_retry(url, params, source_label)

    if "daily" not in data or "precipitation_sum" not in data["daily"]:
        raise ForecastUnavailableError(
            f"Unexpected response from {source_label} API: {data}"
        )

    return zip(data["daily"]["time"], data["daily"]["precipitation_sum"])


def fetch_monthly_rainfall_totals(lat, lon, target_year, target_month):
    """
    Returns monthly rainfall totals keyed by (year, month) for the target
    month and the 3 months before it (the antecedent months).

    Days that have already happened (up to RECENT_LAG_DAYS ago) come from
    the Archive API (observed weather). Only days after that come from the
    Seasonal Forecast API. This keeps the antecedent months on the same
    observed basis instead of mixing in forecast-model values for the past.
    """
    range_start_date = date(target_year, target_month, 1) + relativedelta(months=-3)
    range_start, _ = month_bounds(range_start_date.year, range_start_date.month)
    _, range_end = month_bounds(target_year, target_month)

    observed_cutoff = date.today() - timedelta(days=RECENT_LAG_DAYS)
    daily_pairs = []

    # Part 1: days that have already happened -> observed weather
    observed_end = min(range_end, observed_cutoff)
    if observed_end >= range_start:
        daily_pairs.extend(_fetch_daily_precipitation(
            ARCHIVE_API_URL, "Archive (observed) rainfall",
            lat, lon, range_start, observed_end,
        ))

    # Part 2: days after that -> seasonal forecast
    if range_end > observed_cutoff:
        forecast_start = max(range_start, observed_cutoff + timedelta(days=1))
        daily_pairs.extend(_fetch_daily_precipitation(
            SEASONAL_API_URL, "Seasonal rainfall forecast",
            lat, lon, forecast_start, range_end,
        ))

    monthly_totals = {}
    for date_str, value in daily_pairs:
        y, m, _ = date_str.split("-")
        key = (int(y), int(m))
        monthly_totals[key] = monthly_totals.get(key, 0.0) + (value or 0.0)

    return monthly_totals


def fetch_monthly_discharge_mean(lat, lon, target_year, target_month):
    """Fetches daily river discharge for the target month and returns the mean."""
    range_start, range_end = month_bounds(target_year, target_month)

    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "river_discharge",
        "start_date": range_start.isoformat(),
        "end_date": range_end.isoformat(),
    }

    data = _get_json_with_retry(FLOOD_API_URL, params, "Flood/discharge")

    if "daily" not in data or "river_discharge" not in data["daily"]:
        raise ForecastUnavailableError(
            f"Unexpected response from Flood API: {data}"
        )

    values = [v for v in data["daily"]["river_discharge"] if v is not None]
    if not values:
        raise ForecastUnavailableError(
            "Flood API returned no usable discharge values for this location/period "
            "(no modelled river cell near the gauge point)."
        )

    return sum(values) / len(values)


def get_lag_month(year, month, lag):
    lag_date = date(year, month, 1) + relativedelta(months=-lag)
    return lag_date.year, lag_date.month


def compute_live_forecast_features(lga_id, target_year, target_month):
    """
    Returns a dict of all 14 model features for a future LGA-month,
    computed from live forecast data. Raises ForecastUnavailableError
    with a clear message if anything can't be obtained.
    """
    conn = get_connection()
    try:
        lga_info = get_lga_forecast_inputs(conn, lga_id)
        climatology = get_climatology(conn, lga_id, target_month)

        monthly_rain = fetch_monthly_rainfall_totals(
            lga_info["latitude"], lga_info["longitude"], target_year, target_month
        )

        rain_target = monthly_rain.get((target_year, target_month))
        if rain_target is None:
            raise ForecastUnavailableError(
                f"No rainfall forecast data returned for {target_year}-{target_month:02d} "
                "— this month may be too far ahead (Open-Meteo's seasonal forecast "
                "covers roughly 7 months out)."
            )

        lag1_year, lag1_month = get_lag_month(target_year, target_month, 1)
        lag2_year, lag2_month = get_lag_month(target_year, target_month, 2)
        lag3_year, lag3_month = get_lag_month(target_year, target_month, 3)

        rain_lag1 = monthly_rain.get((lag1_year, lag1_month), 0.0)
        rain_lag2 = monthly_rain.get((lag2_year, lag2_month), 0.0)
        rain_lag3 = monthly_rain.get((lag3_year, lag3_month), 0.0)

        if climatology["rain_month_std"] > 0:
            rainfall_anomaly_index = (
                (rain_target - climatology["rain_month_mean"]) / climatology["rain_month_std"]
            )
        else:
            rainfall_anomaly_index = 0.0

        antecedent_precip_index = 0.5 * rain_lag1 + 0.3 * rain_lag2 + 0.2 * rain_lag3

        station = nearest_discharge_station(lga_info["latitude"], lga_info["longitude"])
        discharge_target = fetch_monthly_discharge_mean(
            station["lat"], station["lon"], target_year, target_month
        )

        if climatology["discharge_month_mean"] > 0:
            normalised_discharge_ratio = discharge_target / climatology["discharge_month_mean"]
        else:
            normalised_discharge_ratio = 1.0

        peak_season_flag = 1 if target_month in PEAK_SEASON_MONTHS else 0

        state_dummies = {
            f"state_{sid}": (1 if lga_info["state_id"] == sid else 0)
            for sid in [1, 2, 5, 6, 7]
        }

        import numpy as np
        month_sin = float(np.sin(2 * np.pi * target_month / 12))
        month_cos = float(np.cos(2 * np.pi * target_month / 12))

        return {
            "lga_name": lga_info["name"],
            "rainfall_anomaly_index": rainfall_anomaly_index,
            "antecedent_precip_index": antecedent_precip_index,
            "normalised_discharge_ratio": normalised_discharge_ratio,
            "terrain_vulnerability_score": lga_info["terrain_vulnerability_score"],
            "peak_season_flag": peak_season_flag,
            "wetland_pct": lga_info["wetland_pct"],
            "built_up_pct": lga_info["built_up_pct"],
            **state_dummies,
            "month_sin": month_sin,
            "month_cos": month_cos,
        }

    finally:
        conn.close()
