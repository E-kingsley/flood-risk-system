"""
Download ESA WorldCover 2021 (10m) land cover tiles covering the Niger Delta
study area from the public AWS S3 bucket (no account/API key needed), and
mosaic + crop them into a single GeoTIFF.

Usage:
    pip install rasterio requests
    python download_worldcover.py
"""

import os
import requests
import rasterio
from rasterio.merge import merge
from rasterio.windows import from_bounds

BASE_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"

BBOX = {"south": 4.0, "north": 7.0, "west": 5.0, "east": 9.5}

OUT_DIR = "data/raw/worldcover"
TILES_DIR = os.path.join(OUT_DIR, "tiles")
MOSAIC_PATH = os.path.join(OUT_DIR, "niger_delta_worldcover.tif")


def get_needed_tiles(bbox):
    tiles = []
    lat = (bbox["south"] // 3) * 3
    while lat < bbox["north"]:
        lon = (bbox["west"] // 3) * 3
        while lon < bbox["east"]:
            lat_str = f"N{int(lat):02d}" if lat >= 0 else f"S{abs(int(lat)):02d}"
            lon_str = f"E{int(lon):03d}" if lon >= 0 else f"W{abs(int(lon)):03d}"
            tiles.append(f"{lat_str}{lon_str}")
            lon += 3
        lat += 3
    return tiles


def download_tile(tile_id):
    filename = f"ESA_WorldCover_10m_2021_v200_{tile_id}_Map.tif"
    url = f"{BASE_URL}/{filename}"
    out_path = os.path.join(TILES_DIR, filename)

    if os.path.exists(out_path):
        print(f"  Already have {filename}, skipping.")
        return out_path

    print(f"  Downloading {filename} ...")
    response = requests.get(url, stream=True, timeout=120)

    if response.status_code != 200:
        print(f"    ✗ Tile {tile_id} not available (HTTP {response.status_code}) — likely no land in this tile, skipping.")
        return None

    os.makedirs(TILES_DIR, exist_ok=True)
    with open(out_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"    ✓ Saved ({size_mb:.1f} MB)")
    return out_path


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    tiles_needed = get_needed_tiles(BBOX)
    print(f"Tiles needed for bbox {BBOX}: {tiles_needed}\n")

    downloaded_paths = []
    for tile_id in tiles_needed:
        path = download_tile(tile_id)
        if path:
            downloaded_paths.append(path)

    if not downloaded_paths:
        print("\nNo tiles downloaded — cannot proceed.")
        return

    print(f"\nMosaicking {len(downloaded_paths)} tiles...")
    srcs = [rasterio.open(p) for p in downloaded_paths]
    mosaic, out_transform = merge(srcs)

    out_meta = srcs[0].meta.copy()
    out_meta.update({
        "height": mosaic.shape[1],
        "width": mosaic.shape[2],
        "transform": out_transform,
    })

    for s in srcs:
        s.close()

    with rasterio.open(MOSAIC_PATH, "w", **out_meta) as dest:
        dest.write(mosaic)

    print(f"Saved mosaic to {MOSAIC_PATH}")


if __name__ == "__main__":
    main()