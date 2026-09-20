"""
Compute land cover percentages per LGA (wetland, built-up, cropland, water
bodies) from ESA WorldCover 2021, then update the `lgas` table in PostgreSQL.

Usage:
    python compute_landcover_features.py
"""

import os
import geopandas as gpd
import rasterio
from rasterstats import zonal_stats
import psycopg2
from dotenv import load_dotenv
from fuzzywuzzy import process as fuzzy_process

load_dotenv()

SHAPEFILE_PATH = "data/raw/boundaries/nga_admin2.shp"
LANDCOVER_PATH = "data/raw/worldcover/niger_delta_worldcover.tif"

TARGET_STATES = ["Bayelsa", "Rivers", "Delta", "Cross River", "Akwa Ibom"]
FUZZY_MATCH_THRESHOLD = 85

# ESA WorldCover class codes
CROPLAND = 40
BUILT_UP = 50
WATER = 80
WETLAND_CODES = (90, 95)  # herbaceous wetland + mangroves


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_db_lgas(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT l.lga_id, l.name, s.name
        FROM lgas l JOIN states s ON l.state_id = s.state_id
    """)
    rows = cur.fetchall()
    cur.close()
    exact_lookup, by_state = {}, {}
    for lga_id, lga_name, state_name in rows:
        key = (state_name.strip().lower(), lga_name.strip().lower())
        exact_lookup[key] = lga_id
        by_state.setdefault(state_name.strip().lower(), []).append((lga_name, lga_id))
    return exact_lookup, by_state


def pct_from_counts(counts, codes):
    total = sum(counts.values())
    if total == 0:
        return None
    if isinstance(codes, int):
        codes = (codes,)
    matched = sum(v for k, v in counts.items() if k in codes)
    return round((matched / total) * 100, 2)


def main():
    print("Loading LGA boundaries...")
    gdf = gpd.read_file(SHAPEFILE_PATH)
    gdf = gdf[gdf["adm1_name"].isin(TARGET_STATES)].copy()
    print(f"Filtered to {len(gdf)} LGAs across target states.")

    with rasterio.open(LANDCOVER_PATH) as src:
        raster_crs = src.crs
    if gdf.crs != raster_crs:
        gdf = gdf.to_crs(raster_crs)

    print("Computing categorical zonal statistics (this may take a few minutes)...")
    stats = zonal_stats(gdf, LANDCOVER_PATH, categorical=True, nodata=0)

    gdf["cropland_pct"] = [pct_from_counts(s, CROPLAND) for s in stats]
    gdf["built_up_pct"] = [pct_from_counts(s, BUILT_UP) for s in stats]
    gdf["water_bodies_pct"] = [pct_from_counts(s, WATER) for s in stats]
    gdf["wetland_pct"] = [pct_from_counts(s, WETLAND_CODES) for s in stats]

    print("\nConnecting to database...")
    conn = get_db_connection()
    exact_lookup, db_by_state = load_db_lgas(conn)

    cur = conn.cursor()
    update_query = """
        UPDATE lgas SET wetland_pct = %s, built_up_pct = %s,
                         cropland_pct = %s, water_bodies_pct = %s
        WHERE lga_id = %s
    """

    matched, fuzzy_matched, unmatched = 0, 0, []

    for _, row in gdf.iterrows():
        state_name = str(row["adm1_name"]).strip()
        lga_name = str(row["adm2_name"]).strip()
        state_key, lga_key = state_name.lower(), lga_name.lower()

        lga_id = exact_lookup.get((state_key, lga_key))
        if lga_id is None:
            candidates = db_by_state.get(state_key, [])
            if candidates:
                names = [c[0] for c in candidates]
                best_match, score = fuzzy_process.extractOne(lga_name, names)
                if score >= FUZZY_MATCH_THRESHOLD:
                    lga_id = dict(candidates)[best_match]
                    fuzzy_matched += 1

        if lga_id is None:
            unmatched.append(f"{lga_name} ({state_name})")
            continue

        cur.execute(update_query, (
            row["wetland_pct"], row["built_up_pct"],
            row["cropland_pct"], row["water_bodies_pct"], lga_id
        ))
        matched += 1

    conn.commit()
    cur.close()
    conn.close()

    print("\n" + "=" * 50)
    print(f"DONE: {matched} LGAs updated ({fuzzy_matched} via fuzzy match)")
    if unmatched:
        print(f"\n{len(unmatched)} unmatched LGAs:")
        for u in unmatched:
            print(f" - {u}")


if __name__ == "__main__":
    main()