"""
find_close_matches.py

For each unmatched LGA name, prints the closest-spelled candidates
found in frontend/lga_boundaries.geojson, so you can confirm the
correct match instead of guessing at spelling differences.

USAGE
-----
    python find_close_matches.py
"""

import difflib
import json
import os

GEOJSON_PATH = os.path.join("frontend", "lga_boundaries.geojson")

UNMATCHED = [
    "Akuku-Toru", "Bekwarra", "Esit Eket", "Obio/Akpor",
    "Calabar Municipal", "Omuma", "Port Harcourt", "Yenagoa",
]


def main():
    with open(GEOJSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_names = sorted({
        feature["properties"].get("adm2_name", "")
        for feature in data["features"]
        if feature["properties"].get("adm2_name")
    })

    for name in UNMATCHED:
        matches = difflib.get_close_matches(name, all_names, n=3, cutoff=0.4)
        print(f"'{name}' -> closest GeoJSON name(s): {matches if matches else 'NONE FOUND'}")


if __name__ == "__main__":
    main()
