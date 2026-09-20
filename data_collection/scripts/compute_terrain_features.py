"""
Compute mean elevation and mean slope per LGA from SRTM DEM and Nigeria LGA
boundaries, then update the `lgas` table in PostgreSQL.

Usage:
    pip install geopandas rasterio rasterstats fuzzywuzzy python-Levenshtein
    python compute_terrain_features.py
"""

import os
import sys
import numpy as np
import geopandas as gpd
import rasterio
from rasterstats import zonal_stats
import psycopg2
from dotenv import load_dotenv
from fuzzywuzzy import process as fuzzy_process

load_dotenv()

# --- Config -----------------------------------------------------------

SHAPEFILE_PATH = "data/raw/boundaries/nga_admin2.shp"
DEM_PATH = "data/raw/srtm/niger_delta_srtm.tif"
SLOPE_OUT_PATH = "data/raw/srtm/niger_delta_slope.tif"

TARGET_STATES = ["Bayelsa", "Rivers", "Delta", "Cross River", "Akwa Ibom"]
FUZZY_MATCH_THRESHOLD = 85  # 0-100; below this, treated as unmatched

# --- Database -----------------------------------------------------------

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
    exact_lookup = {}
    by_state = {}
    for lga_id, lga_name, state_name in rows:
        key = (state_name.strip().lower(), lga_name.strip().lower())
        exact_lookup[key] = lga_id
        by_state.setdefault(state_name.strip().lower(), []).append((lga_name, lga_id))
    return exact_lookup, by_state


# --- Slope computation -----------------------------------------------------------

def compute_slope_degrees(dem_path):
    with rasterio.open(dem_path) as src:
        elevation = src.read(1).astype(float)
        if src.nodata is not None:
            elevation[elevation == src.nodata] = np.nan

        transform = src.transform
        pixel_size_x = transform.a
        pixel_size_y = -transform.e

        # SRTM is in geographic (degree) coordinates — convert pixel size to
        # approximate meters so the slope comes out in real-world degrees.
        mean_lat = (src.bounds.top + src.bounds.bottom) / 2
        meters_per_deg_lat = 111320
        meters_per_deg_lon = 111320 * np.cos(np.radians(mean_lat))
        dx = pixel_size_x * meters_per_deg_lon
        dy = pixel_size_y * meters_per_deg_lat

        gy, gx = np.gradient(elevation, dy, dx)
        slope_rad = np.arctan(np.sqrt(gx**2 + gy**2))
        slope_deg = np.degrees(slope_rad)

        profile = src.profile
        return slope_deg, profile


def save_slope_raster(slope_deg, profile, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    profile.update(dtype=rasterio.float32, nodata=np.nan)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(slope_deg.astype(np.float32), 1)


# --- Main -----------------------------------------------------------

def main():
    print("Loading LGA boundaries...")
    gdf = gpd.read_file(SHAPEFILE_PATH)
    print(f"Loaded {len(gdf)} total LGA polygons.")
    print(f"Columns available: {list(gdf.columns)}")

    state_col_candidates = [c for c in gdf.columns if c.upper() in ("ADM1_EN", "ADM1_NAME", "STATE", "STATENAME")]
    name_col_candidates = [c for c in gdf.columns if c.upper() in ("ADM2_EN", "ADM2_NAME", "LGA", "LGANAME")]

    if not state_col_candidates or not name_col_candidates:
        print("\nCould not auto-detect state/LGA name columns from the list above.")
        print("Please tell me the exact column names and I'll adjust the script.")
        sys.exit(1)

    state_col = state_col_candidates[0]
    name_col = name_col_candidates[0]
    print(f"Using state column '{state_col}' and LGA name column '{name_col}'")

    gdf = gdf[gdf[state_col].isin(TARGET_STATES)].copy()
    print(f"Filtered to {len(gdf)} LGAs across target states.")

    if gdf.empty:
        print(f"\nNo rows matched TARGET_STATES via column '{state_col}'.")
        print("Unique values found in that column:")
        print(gpd.read_file(SHAPEFILE_PATH)[state_col].unique())
        sys.exit(1)

    print("\nComputing slope raster from DEM (this may take a minute)...")
    slope_deg, profile = compute_slope_degrees(DEM_PATH)
    save_slope_raster(slope_deg, profile, SLOPE_OUT_PATH)
    print(f"Saved slope raster to {SLOPE_OUT_PATH}")

    with rasterio.open(DEM_PATH) as src:
        raster_crs = src.crs
    if gdf.crs != raster_crs:
        gdf = gdf.to_crs(raster_crs)

    print("\nComputing zonal statistics (mean elevation)...")
    elev_stats = zonal_stats(gdf, DEM_PATH, stats=["mean"], nodata=-32768)

    print("Computing zonal statistics (mean slope)...")
    slope_stats = zonal_stats(gdf, SLOPE_OUT_PATH, stats=["mean"])

    gdf["elevation_mean_m"] = [s["mean"] for s in elev_stats]
    gdf["slope_mean_deg"] = [s["mean"] for s in slope_stats]

    print("\nConnecting to database...")
    conn = get_db_connection()
    exact_lookup, db_by_state = load_db_lgas(conn)

    cur = conn.cursor()
    update_query = "UPDATE lgas SET elevation_mean_m = %s, slope_mean_deg = %s WHERE lga_id = %s"

    matched, fuzzy_matched, unmatched = 0, 0, []

    for _, row in gdf.iterrows():
        state_name = str(row[state_col]).strip()
        lga_name = str(row[name_col]).strip()
        state_key = state_name.lower()
        lga_key = lga_name.lower()

        elev = row["elevation_mean_m"]
        slope = row["slope_mean_deg"]

        if elev is None or (isinstance(elev, float) and np.isnan(elev)):
            continue

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

        cur.execute(update_query, (float(elev), float(slope) if slope is not None else None, lga_id))
        matched += 1

    conn.commit()
    cur.close()
    conn.close()

    print("\n" + "=" * 50)
    print(f"DONE: {matched} LGAs updated ({fuzzy_matched} via fuzzy match)")
    if unmatched:
        print(f"\n{len(unmatched)} unmatched LGAs (no elevation/slope saved):")
        for u in unmatched:
            print(f" - {u}")


if __name__ == "__main__":
    main()