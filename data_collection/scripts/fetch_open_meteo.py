import argparse
import os
import time
from datetime import datetime
import psycopg2
from psycopg2.extras import execute_values
import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()

# Database Connection
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )

def clean_val(val):
    if val is None or val == -999 or val == -999.0:
        return None
    return float(val)

# API Request with Tenacity Retry Logic
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout)),
    reraise=True
)
def fetch_open_meteo_data(lat, lon, start_date="2000-01-01", end_date="2024-12-31"):
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": float(lat),
        "longitude": float(lon),
        "start_date": start_date,
        "end_date": end_date,
        "daily": "precipitation_sum,relative_humidity_2m_mean",
        "timezone": "Africa/Lagos"
    }
    response = requests.get(url, params=params, timeout=25)
    response.raise_for_status()
    return response.json()

def process_open_meteo():
    parser = argparse.ArgumentParser(description="Fetch Open-Meteo Historical data for LGAs")
    parser.add_argument("--state", type=str, help="Filter by State name (e.g., Bayelsa)")
    args = parser.parse_args()

    conn = get_db_connection()
    cursor = conn.cursor()

    # Query LGAs from DB
    query = """
        SELECT l.lga_id, l.name, s.name as state_name, l.centroid_lat, l.centroid_lon 
        FROM lgas l 
        JOIN states s ON l.state_id = s.state_id
    """
    if args.state:
        query += " WHERE LOWER(s.name) = LOWER(%s)"
        cursor.execute(query, (args.state,))
    else:
        cursor.execute(query)

    lgas = cursor.fetchall()
    print(f"Loaded {len(lgas)} LGA(s) to process.\n" + "-" * 50)

    succeeded, failed = [], []

    upsert_query = """
        INSERT INTO open_meteo_raw (lga_id, obs_date, rainfall_mm, humidity_pct)
        VALUES %s
        ON CONFLICT (lga_id, obs_date) DO UPDATE SET
            rainfall_mm = EXCLUDED.rainfall_mm,
            humidity_pct = EXCLUDED.humidity_pct;
    """

    for lga_id, lga_name, state_name, lat, lon in lgas:
        if not lat or not lon:
            print(f"✗ {lga_name} ({state_name}): Missing coordinates")
            failed.append(f"{lga_name} ({state_name}) - No Coords")
            continue

        try:
            data = fetch_open_meteo_data(lat, lon)
            daily = data.get("daily", {})

            dates = daily.get("time", [])
            precip = daily.get("precipitation_sum", [])
            humidity = daily.get("relative_humidity_2m_mean", [])

            records = []
            for d, p, h in zip(dates, precip, humidity):
                obs_date = datetime.strptime(d, "%Y-%m-%d").date()
                records.append((
                    lga_id,
                    obs_date,
                    clean_val(p),
                    clean_val(h)
                ))

            if records:
                execute_values(cursor, upsert_query, records, page_size=1000)
                conn.commit()
                print(f"✓ {lga_name} ({state_name}): Inserted/Updated {len(records)} records")
                succeeded.append(f"{lga_name} ({state_name})")
            else:
                print(f"✗ {lga_name} ({state_name}): Empty response payload")
                failed.append(f"{lga_name} ({state_name}) - Empty Data")

        except Exception as e:
            conn.rollback()
            print(f"✗ {lga_name} ({state_name}): Failed after retries ({e})")
            failed.append(f"{lga_name} ({state_name})")

        # Friendly rate-limiting pause
        time.sleep(2)

    cursor.close()
    conn.close()

    # Final Summary
    print("\n" + "=" * 50)
    print(f"PROCESS COMPLETE: {len(succeeded)} Succeeded | {len(failed)} Failed")
    if failed:
        print("\nFailed LGAs:")
        for item in failed:
            print(f" - {item}")

if __name__ == "__main__":
    process_open_meteo()