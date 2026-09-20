"""
add_lga_coordinates.py

Adds latitude/longitude columns to the lgas table and populates them
from frontend/lga_boundaries.geojson's center_lat/center_lon properties
(already present in the HDX COD-AB dataset you filtered earlier).

These coordinates are needed to query live forecast APIs (Open-Meteo
Seasonal Forecast / Flood API) per LGA for future-month predictions.

Matching is done by LGA name, normalised (lowercased, whitespace-
trimmed) to handle minor spelling/casing differences between the
GeoJSON's adm2_name and your lgas.name column. Any LGA that doesn't
find a match is printed clearly at the end so you can fix it manually
(e.g. a spelling difference like "Akwa-Ibom" vs "Akwa Ibom" style
issues, but at the LGA-name level).

USAGE
-----
    python add_lga_coordinates.py

Run from the project root (same place frontend/lga_boundaries.geojson
already exists from the Day 5 map setup).
"""

import json
import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()

GEOJSON_PATH = os.path.join("frontend", "lga_boundaries.geojson")

# Manual overrides for LGAs where the GeoJSON's adm2_name spelling/
# formatting differs from the database's name (confirmed via
# find_close_matches.py — these are typos/formatting quirks in the
# HDX dataset itself, not errors in the database).
NAME_OVERRIDES = {
    "akuku-toru": "akuku toru",
    "bekwarra": "bekwara",
    "esit eket": "esit - eket",
    "obio/akpor": "obia/akpor",
    "calabar municipal": "calabar-municipal",
    "omuma": "omumma",
    "port harcourt": "port-harcourt",
    "yenagoa": "yenegoa",
}


def normalize(name):
    return (name or "").strip().lower()


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_geojson_centroids():
    with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    centroids = {}
    for feature in data["features"]:
        props = feature["properties"]
        name = props.get("adm2_name")
        lat = props.get("center_lat")
        lon = props.get("center_lon")
        if name and lat is not None and lon is not None:
            centroids[normalize(name)] = (float(lat), float(lon))

    return centroids


def main():
    centroids = load_geojson_centroids()
    print(f"Loaded {len(centroids)} LGA centroids from {GEOJSON_PATH}.")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE lgas
                ADD COLUMN IF NOT EXISTS latitude NUMERIC,
                ADD COLUMN IF NOT EXISTS longitude NUMERIC;
            """)
            conn.commit()

            cur.execute("SELECT lga_id, name FROM lgas;")
            db_lgas = cur.fetchall()

            matched = 0
            unmatched = []

            for lga_id, name in db_lgas:
                key = normalize(name)
                key = NAME_OVERRIDES.get(key, key)  # apply override if one exists
                if key in centroids:
                    lat, lon = centroids[key]
                    cur.execute(
                        "UPDATE lgas SET latitude = %s, longitude = %s WHERE lga_id = %s;",
                        (lat, lon, lga_id),
                    )
                    matched += 1
                else:
                    unmatched.append((lga_id, name))

            conn.commit()

        print(f"\nMatched and updated {matched} of {len(db_lgas)} LGAs.")

        if unmatched:
            print(f"\n{len(unmatched)} LGA(s) had no matching centroid — fix these manually:")
            for lga_id, name in unmatched:
                print(f"  lga_id={lga_id}: '{name}'")
            print("\nFor each one, find the closest matching name in the GeoJSON")
            print("properties (adm2_name) and either fix the spelling in your")
            print("database or add a manual override below in this script.")
        else:
            print("All LGAs matched successfully.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
