"""
filter_lga_geojson.py

Filters the national Nigeria Admin Level 2 (LGA) GeoJSON down to just
the 5 Niger Delta states your project covers: Bayelsa, Rivers, Delta,
Cross River, Akwa Ibom.

The HDX COD-AB file's exact property names vary slightly between
releases (ADM1_EN, admin1Name, shapeName, etc.), so this script
inspects the first feature's properties, prints them out, and tries a
few common field names automatically. If none match, it tells you
exactly what to change.

USAGE
-----
    python filter_lga_geojson.py path/to/downloaded_nigeria_lgas.geojson

Produces: frontend/lga_boundaries.geojson (adjust OUTPUT_PATH below if
your frontend folder is named differently).
"""

import json
import sys

TARGET_STATES = {"bayelsa", "rivers", "delta", "cross river", "akwa ibom"}

# Common property names used across different releases of this dataset.
STATE_NAME_CANDIDATES = [
    "ADM1_EN", "admin1Name", "ADM1_NAME", "shapeName_1", "STATE",
    "state", "NAME_1", "admin1RefName", "adm1_name",
]

OUTPUT_PATH = "frontend/lga_boundaries.geojson"


def find_state_field(properties: dict):
    for candidate in STATE_NAME_CANDIDATES:
        if candidate in properties:
            return candidate
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python filter_lga_geojson.py path/to/downloaded_file.geojson")
        sys.exit(1)

    input_path = sys.argv[1]

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    features = data.get("features", [])
    if not features:
        print("No features found in the input file — is this the right GeoJSON?")
        sys.exit(1)

    sample_props = features[0]["properties"]
    print("Properties found on the first feature (for reference):")
    for key, value in sample_props.items():
        print(f"  {key}: {value}")

    state_field = find_state_field(sample_props)
    if state_field is None:
        print("\nCouldn't auto-detect the state name field from the list above.")
        print("Open the properties printed above, find whichever field holds")
        print("the state name (e.g. 'Bayelsa'), and add it to")
        print("STATE_NAME_CANDIDATES near the top of this script, then re-run.")
        sys.exit(1)

    print(f"\nUsing '{state_field}' as the state name field.")

    filtered = [
        feature for feature in features
        if str(feature["properties"].get(state_field, "")).strip().lower() in TARGET_STATES
    ]

    print(f"Kept {len(filtered)} of {len(features)} features "
          f"(expecting around 105 for the 5 Niger Delta states).")

    if len(filtered) == 0:
        print("\nNo features matched — check the actual state name spelling in")
        print("the properties above and adjust TARGET_STATES if needed")
        print("(e.g. some datasets write 'Akwa-Ibom' with a hyphen).")
        sys.exit(1)

    output = {"type": "FeatureCollection", "features": filtered}

    import os
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f)

    print(f"\nSaved filtered boundaries to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
