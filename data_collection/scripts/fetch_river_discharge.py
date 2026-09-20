"""
Fetch historical river discharge (GloFAS model, via Open-Meteo Flood API)
for key rivers feeding the Niger Delta, and load into grdc_discharge_raw.

Free, no API key needed.

Usage:
    python fetch_river_discharge.py
"""

import os
import time
from datetime import date, timedelta
import psycopg2
from psycopg2.extras import execute_values
import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()

STATIONS = [
    {"name": "Niger at Onitsha", "lat": 6.1500, "lon": 6.7833},
    {"name": "Benue at Makurdi", "lat": 7.7333, "lon": 8.5333},
    {"name": "Niger at Lokoja", "lat": 7.7800, "lon": 6.7600},
    {"name": "Forcados at Warri", "lat": 5.5167, "lon": 5.7500},
    {"name": "Bonny River at Port Harcourt", "lat": 4.7719, "lon": 7.0134},
]

START_DATE = "2000-01-01"
END_DATE = (date.today() - timedelta(days=1)).isoformat()


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout)),
    reraise=True,
)
def fetch_discharge(lat, lon, start_date, end_date):
    url = "https://flood-api.open-meteo.com/v1/flood"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "river_discharge",
        "start_date": start_date,
        "end_date": end_date,
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def main():
    conn = get_db_connection()
    cursor = conn.cursor()

    upsert_query = """
        INSERT INTO grdc_discharge_raw (station_name, obs_date, discharge_cumecs)
        VALUES %s
        ON CONFLICT (station_name, obs_date) DO UPDATE SET
            discharge_cumecs = EXCLUDED.discharge_cumecs;
    """

    succeeded, failed = [], []

    for station in STATIONS:
        name, lat, lon = station["name"], station["lat"], station["lon"]
        try:
            data = fetch_discharge(lat, lon, START_DATE, END_DATE)
            daily = data.get("daily", {})
            dates = daily.get("time", [])
            discharge = daily.get("river_discharge", [])

            records = []
            for d, val in zip(dates, discharge):
                if val is not None:
                    records.append((name, d, float(val)))

            if records:
                execute_values(cursor, upsert_query, records, page_size=1000)
                conn.commit()
                print(f"✓ {name}: Inserted/Updated {len(records)} records")
                succeeded.append(name)
            else:
                print(f"✗ {name}: Empty response payload")
                failed.append(name)

        except Exception as e:
            conn.rollback()
            print(f"✗ {name}: Failed after retries ({e})")
            failed.append(name)

        time.sleep(0.5)

    cursor.close()
    conn.close()

    print("\n" + "=" * 50)
    print(f"DONE: {len(succeeded)} Succeeded | {len(failed)} Failed")
    if failed:
        print("\nFailed stations:")
        for f in failed:
            print(f" - {f}")


if __name__ == "__main__":
    main()