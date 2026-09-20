"""
Day 3 - Step 1: Aggregate raw NASA POWER / Open-Meteo / river discharge data
into monthly per-LGA rows in lga_monthly_features.

- Primary weather source: nasa_power_raw (fully loaded).
- Open-Meteo used only to fill months where NASA POWER has no data.
- River discharge: each LGA assigned to its nearest of the 5 gauge points
  (straight-line distance from LGA centroid), then monthly-averaged.

Usage:
    python build_monthly_features.py
"""

import os
import math
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

STATIONS = [
    {"name": "Niger at Onitsha", "lat": 6.1500, "lon": 6.7833},
    {"name": "Benue at Makurdi", "lat": 7.7333, "lon": 8.5333},
    {"name": "Niger at Lokoja", "lat": 7.7800, "lon": 6.7600},
    {"name": "Forcados at Warri", "lat": 5.5167, "lon": 5.7500},
    {"name": "Bonny River at Port Harcourt", "lat": 4.7719, "lon": 7.0134},
]


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def nearest_station(lat, lon):
    best_name, best_dist = None, float("inf")
    for s in STATIONS:
        d = haversine_km(lat, lon, s["lat"], s["lon"])
        if d < best_dist:
            best_dist = d
            best_name = s["name"]
    return best_name


def main():
    conn = get_db_connection()
    cur = conn.cursor()

    print("Loading LGAs...")
    cur.execute("SELECT lga_id, name, centroid_lat, centroid_lon FROM lgas")
    lgas = cur.fetchall()
    print(f"  {len(lgas)} LGAs loaded.")

    lga_station_map = {
        lga_id: nearest_station(float(lat), float(lon))
        for lga_id, name, lat, lon in lgas
    }

    print("\nAggregating NASA POWER data monthly per LGA...")
    cur.execute("""
        SELECT lga_id,
               EXTRACT(YEAR FROM obs_date)::int AS year,
               EXTRACT(MONTH FROM obs_date)::int AS month,
               AVG(rainfall_mm) AS rainfall_mm,
               SUM(rainfall_mm) AS rainfall_total_mm,
               AVG(temp_mean_c) AS temp_mean_c,
               AVG(humidity_pct) AS humidity_pct
        FROM nasa_power_raw
        GROUP BY lga_id, year, month
    """)
    nasa_rows = cur.fetchall()
    print(f"  {len(nasa_rows)} LGA-month rows from NASA POWER.")

    # Build lookup: (lga_id, year, month) -> dict of values
    monthly = {}
    for lga_id, year, month, rainfall_avg, rainfall_total, temp, hum in nasa_rows:
        monthly[(lga_id, year, month)] = {
            "rainfall_mm": float(rainfall_total) if rainfall_total is not None else None,
            "temp_mean_c": float(temp) if temp is not None else None,
            "humidity_pct": float(hum) if hum is not None else None,
        }

    print("\nFilling gaps from Open-Meteo where NASA POWER is missing...")
    cur.execute("""
        SELECT lga_id,
               EXTRACT(YEAR FROM obs_date)::int AS year,
               EXTRACT(MONTH FROM obs_date)::int AS month,
               SUM(rainfall_mm) AS rainfall_total_mm,
               AVG(humidity_pct) AS humidity_pct
        FROM open_meteo_raw
        GROUP BY lga_id, year, month
    """)
    meteo_rows = cur.fetchall()
    filled_count = 0
    for lga_id, year, month, rainfall_total, hum in meteo_rows:
        key = (lga_id, year, month)
        if key not in monthly:
            monthly[key] = {
                "rainfall_mm": float(rainfall_total) if rainfall_total is not None else None,
                "temp_mean_c": None,
                "humidity_pct": float(hum) if hum is not None else None,
            }
            filled_count += 1
        else:
            # fill only missing humidity/rainfall if NASA POWER row had nulls
            if monthly[key]["rainfall_mm"] is None and rainfall_total is not None:
                monthly[key]["rainfall_mm"] = float(rainfall_total)
                filled_count += 1
            if monthly[key]["humidity_pct"] is None and hum is not None:
                monthly[key]["humidity_pct"] = float(hum)

    print(f"  Filled/supplemented {filled_count} values from Open-Meteo.")

    print("\nAggregating river discharge monthly per station...")
    cur.execute("""
        SELECT station_name,
               EXTRACT(YEAR FROM obs_date)::int AS year,
               EXTRACT(MONTH FROM obs_date)::int AS month,
               AVG(discharge_cumecs) AS discharge_cumecs
        FROM grdc_discharge_raw
        GROUP BY station_name, year, month
    """)
    discharge_rows = cur.fetchall()
    discharge_lookup = {}
    for station, year, month, disch in discharge_rows:
        discharge_lookup[(station, year, month)] = float(disch) if disch is not None else None
    print(f"  {len(discharge_rows)} station-month discharge rows.")

    print("\nBuilding final monthly feature rows...")
    records = []
    for (lga_id, year, month), vals in monthly.items():
        station = lga_station_map.get(lga_id)
        discharge = discharge_lookup.get((station, year, month)) if station else None
        records.append((
            lga_id, year, month,
            vals["rainfall_mm"], vals["temp_mean_c"], vals["humidity_pct"], discharge
        ))

    print(f"  {len(records)} total LGA-month rows to upsert.")

    upsert_query = """
        INSERT INTO lga_monthly_features
            (lga_id, year, month, rainfall_mm, temp_mean_c, humidity_pct, river_discharge_cumecs)
        VALUES %s
        ON CONFLICT (lga_id, year, month) DO UPDATE SET
            rainfall_mm = EXCLUDED.rainfall_mm,
            temp_mean_c = EXCLUDED.temp_mean_c,
            humidity_pct = EXCLUDED.humidity_pct,
            river_discharge_cumecs = EXCLUDED.river_discharge_cumecs;
    """
    execute_values(cur, upsert_query, records, page_size=1000)
    conn.commit()

    cur.close()
    conn.close()
    print("\n✓ Done. lga_monthly_features populated with base monthly data.")


if __name__ == "__main__":
    main()