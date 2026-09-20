"""
test_forecast_service.py

Standalone test for forecast_service.py — computes live-forecast
features for a real LGA and a near-future month, and prints them, so
we can sanity-check the values before wiring this into /api/predict.

USAGE
-----
    python test_forecast_service.py <lga_id> <year> <month>

Example (adjust year/month to something within ~7 months of today):
    python test_forecast_service.py 1 2026 11
"""

import sys

sys.path.insert(0, "backend")
from forecast_service import compute_live_forecast_features, ForecastUnavailableError


def main():
    if len(sys.argv) != 4:
        print("Usage: python test_forecast_service.py <lga_id> <year> <month>")
        sys.exit(1)

    lga_id = int(sys.argv[1])
    year = int(sys.argv[2])
    month = int(sys.argv[3])

    print(f"Computing live forecast features for lga_id={lga_id}, {year}-{month:02d}...\n")

    try:
        features = compute_live_forecast_features(lga_id, year, month)
    except ForecastUnavailableError as e:
        print(f"FAILED: {e}")
        sys.exit(1)

    for key, value in features.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
