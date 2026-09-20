"""
Download SRTM GL1 (30m) elevation data for the Niger Delta study area
(Bayelsa, Rivers, Delta, Cross River, Akwa Ibom States) from OpenTopography.

Usage:
    1. pip install requests
    2. Set your API key as an environment variable (never write it in this file):
           PowerShell:  $env:OPENTOPOGRAPHY_API_KEY = "your-key-here"
    3. python download_srtm_niger_delta.py
"""

import os
import requests

# --- Config ---------------------------------------------------------------

API_KEY = os.environ.get("OPENTOPOGRAPHY_API_KEY", "")

# Bounding box covering Bayelsa, Rivers, Delta, Cross River, and Akwa Ibom
# States. This is intentionally generous (a bit wider than the states
# themselves) so no LGA gets clipped at the edge.
BBOX = {
    "south": 4.0,
    "north": 7.0,
    "west": 5.0,
    "east": 9.5,
}

DEM_TYPE = "SRTMGL1"  # 30m resolution. Use "SRTMGL3" for 90m if the file is too large.
OUTPUT_FORMAT = "GTiff"
OUTPUT_PATH = "data/raw/srtm/niger_delta_srtm.tif"

# --- Download ---------------------------------------------------------------

def download_dem():
    if not API_KEY:
        raise ValueError("Set the OPENTOPOGRAPHY_API_KEY environment variable before running this script.")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    url = "https://portal.opentopography.org/API/globaldem"
    params = {
        "demtype": DEM_TYPE,
        "south": BBOX["south"],
        "north": BBOX["north"],
        "west": BBOX["west"],
        "east": BBOX["east"],
        "outputFormat": OUTPUT_FORMAT,
        "API_Key": API_KEY,
    }

    print(f"Requesting {DEM_TYPE} for bbox {BBOX} ...")
    response = requests.get(url, params=params, timeout=300)

    # Real GeoTIFFs start with one of these byte signatures ("magic numbers"):
    # b'II*\x00' (little-endian) or b'MM\x00*' (big-endian)
    is_tiff = response.content[:4] in (b"II*\x00", b"MM\x00*")

    if response.status_code == 200 and is_tiff:
        with open(OUTPUT_PATH, "wb") as f:
            f.write(response.content)
        size_mb = len(response.content) / (1024 * 1024)
        print(f"Saved {OUTPUT_PATH} ({size_mb:.1f} MB)")
    else:
        # OpenTopography returns JSON/text on errors (bad key, area too large, etc.)
        print(f"Request failed: HTTP {response.status_code}")
        print(response.text[:1000])
        raise RuntimeError("Download failed — see message above.")


if __name__ == "__main__":
    download_dem()