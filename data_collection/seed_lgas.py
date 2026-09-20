"""
seed_lgas.py
------------
Seeds the `states` and `lgas` tables for the 5-state Niger Delta study area.

Instead of hand-typed centroid coordinates (error-prone), this script
geocodes each LGA's centroid automatically via OpenStreetMap's free
Nominatim API, then inserts states + LGAs into PostgreSQL.

Run once, at the start of Day 1: `python seed_lgas.py`

Requires: requests, psycopg2-binary, python-dotenv (already in requirements.txt)
"""

import os
import time
import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "flood_risk_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD"),
}

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "flood-risk-capstone-project/1.0 (student research use)"}

# Verified against Rivers/Bayelsa official sources + standard LGA lists.
# Spot-check a few against an official source before your final report —
# LGA boundaries occasionally get contested/renamed.
STATE_LGAS = {
    "Bayelsa": [
        "Brass", "Ekeremor", "Kolokuma/Opokuma", "Nembe", "Ogbia",
        "Sagbama", "Southern Ijaw", "Yenagoa",
    ],
    "Rivers": [
        "Abua/Odual", "Ahoada East", "Ahoada West", "Akuku-Toru", "Andoni",
        "Asari-Toru", "Bonny", "Degema", "Eleme", "Emohua", "Etche",
        "Gokana", "Ikwerre", "Khana", "Obio/Akpor", "Ogba/Egbema/Ndoni",
        "Ogu/Bolo", "Okrika", "Omuma", "Opobo/Nkoro", "Oyigbo",
        "Port Harcourt", "Tai",
    ],
    "Delta": [
        "Aniocha North", "Aniocha South", "Bomadi", "Burutu", "Ethiope East",
        "Ethiope West", "Ika North East", "Ika South", "Isoko North",
        "Isoko South", "Ndokwa East", "Ndokwa West", "Okpe", "Oshimili North",
        "Oshimili South", "Patani", "Sapele", "Udu", "Ughelli North",
        "Ughelli South", "Ukwuani", "Uvwie", "Warri North", "Warri South",
        "Warri South West",
    ],
    "Cross River": [
        "Abi", "Akamkpa", "Akpabuyo", "Bakassi", "Bekwarra", "Biase", "Boki",
        "Calabar Municipal", "Calabar South", "Etung", "Ikom", "Obanliku",
        "Obubra", "Obudu", "Odukpani", "Ogoja", "Yakurr", "Yala",
    ],
    "Akwa Ibom": [
        "Abak", "Eastern Obolo", "Eket", "Esit Eket", "Essien Udim",
        "Etim Ekpo", "Etinan", "Ibeno", "Ibesikpo Asutan", "Ibiono Ibom",
        "Ika", "Ikono", "Ikot Abasi", "Ikot Ekpene", "Ini", "Itu", "Mbo",
        "Mkpat Enin", "Nsit Atai", "Nsit Ibom", "Nsit Ubium", "Obot Akara",
        "Okobo", "Onna", "Oron", "Oruk Anam", "Udung Uko", "Ukanafun",
        "Uruan", "Urue-Offong/Oruko", "Uyo",
    ],
}


def geocode(lga_name: str, state_name: str):
    """Query Nominatim for an LGA centroid. Returns (lat, lon) or (None, None)."""
    query = f"{lga_name}, {state_name} State, Nigeria"
    params = {"q": query, "format": "json", "limit": 1}
    try:
        resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        print(f"  [WARN] Geocoding failed for {query}: {e}")
    return None, None


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    failed = []

    for state_name, lgas in STATE_LGAS.items():
        cur.execute(
            "INSERT INTO states (name) VALUES (%s) "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING state_id",
            (state_name,),
        )
        state_id = cur.fetchone()[0]
        conn.commit()
        print(f"\n{state_name} (state_id={state_id})")

        for lga_name in lgas:
            lat, lon = geocode(lga_name, state_name)
            if lat is None:
                failed.append((state_name, lga_name))
                print(f"  ✗ {lga_name}: geocoding failed, inserted with NULL centroid")
            else:
                print(f"  ✓ {lga_name}: ({lat:.4f}, {lon:.4f})")

            cur.execute(
                """
                INSERT INTO lgas (state_id, name, centroid_lat, centroid_lon)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (state_id, name) DO UPDATE
                SET centroid_lat = EXCLUDED.centroid_lat,
                    centroid_lon = EXCLUDED.centroid_lon
                """,
                (state_id, lga_name, lat, lon),
            )
            conn.commit()

            # Nominatim's usage policy caps free requests at 1/sec — respect it
            time.sleep(1.1)

    cur.close()
    conn.close()

    print("\n" + "=" * 50)
    total = sum(len(v) for v in STATE_LGAS.values())
    print(f"Done. {total - len(failed)}/{total} LGAs geocoded successfully.")
    if failed:
        print("Failed (NULL centroid — fix manually before Day 1 ends):")
        for state_name, lga_name in failed:
            print(f"  - {lga_name}, {state_name}")


if __name__ == "__main__":
    main()
