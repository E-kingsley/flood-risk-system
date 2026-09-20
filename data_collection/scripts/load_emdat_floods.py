import os
import argparse
import json
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

TARGET_STATES = {"bayelsa", "rivers", "delta", "cross river", "akwa ibom"}


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )


def parse_emdat_date(row):
    try:
        year = int(row['Start Year']) if pd.notna(row['Start Year']) else None
        if not year:
            return None
        month = int(row['Start Month']) if pd.notna(row['Start Month']) and row['Start Month'] > 0 else 1
        day = int(row['Start Day']) if pd.notna(row['Start Day']) and row['Start Day'] > 0 else 1
        return datetime(year, month, day).date()
    except Exception:
        return None


def extract_admin_units(gadm_json_str):
    """
    Parses EM-DAT's GADM JSON column. Real keys are name_1 (state) and
    name_2 (LGA) — NOT 'level' or 'adm2_name' as originally assumed.
    Returns (state_names set, lga_names set).
    """
    states, lgas = set(), set()
    if pd.isna(gadm_json_str) or not str(gadm_json_str).strip():
        return states, lgas

    try:
        gadm_data = json.loads(gadm_json_str) if isinstance(gadm_json_str, str) else gadm_json_str
        if isinstance(gadm_data, list):
            for unit in gadm_data:
                if isinstance(unit, dict):
                    if unit.get('name_1'):
                        states.add(unit['name_1'].strip())
                    if unit.get('name_2'):
                        lgas.add(unit['name_2'].strip())
    except (json.JSONDecodeError, TypeError):
        pass

    return states, lgas


def determine_severity(row):
    deaths_raw = row.get('Total Deaths')
    affected_raw = row.get('Total Affected')
    deaths_isna = pd.isna(deaths_raw)
    affected_isna = pd.isna(affected_raw)

    if deaths_isna and affected_isna:
        return 'Unknown'

    deaths = deaths_raw if not deaths_isna else 0
    affected = affected_raw if not affected_isna else 0

    if deaths >= 50 or affected >= 100000:
        return 'Extreme'
    elif deaths >= 10 or affected >= 10000:
        return 'Severe'
    elif deaths > 0 or affected > 0:
        return 'Moderate'
    return 'Minor'


def process_emdat():
    parser = argparse.ArgumentParser(description="Ingest EM-DAT Nigeria Flood Events into PostgreSQL")
    parser.add_argument("--file", type=str, default="data_collection/downloads/260430_emdat_archive.xlsx",
                         help="Path to EM-DAT Excel file")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"✗ File not found: {args.file}")
        return

    print(f"Reading EM-DAT archive from {args.file}...")
    df = pd.read_excel(args.file)

    nigeria_floods = df[
        (df['ISO'] == 'NGA') &
        (df['Disaster Type'].astype(str).str.strip().str.lower() == 'flood')
    ].copy()

    print(f"Found {len(nigeria_floods)} flood event records for Nigeria.")

    conn = get_db_connection()
    cursor = conn.cursor()

    # LGA lookup: lowercase name -> lga_id, plus grouped by state
    cursor.execute("""
        SELECT l.lga_id, LOWER(l.name), LOWER(s.name)
        FROM lgas l JOIN states s ON l.state_id = s.state_id
    """)
    rows = cursor.fetchall()
    lga_lookup = {name: lga_id for lga_id, name, _ in rows}
    lgas_by_state = {}
    for lga_id, name, state_name in rows:
        lgas_by_state.setdefault(state_name, []).append(lga_id)

    records_to_insert = []
    lga_level_count = 0
    state_level_count = 0
    skipped_irrelevant = 0
    skipped_no_date = 0

    for _, row in nigeria_floods.iterrows():
        event_date = parse_emdat_date(row)
        if not event_date:
            skipped_no_date += 1
            continue

        severity = determine_severity(row)
        raw_location = str(row['Location']) if pd.notna(row['Location']) else 'Nigeria (Unspecified)'

        gadm_states, gadm_lgas = extract_admin_units(row.get('GADM Admin Units'))
        gadm_states_lower = {s.lower() for s in gadm_states}

        # Does this event touch any of our 5 target states at all?
        relevant_states = gadm_states_lower & TARGET_STATES
        if not relevant_states:
            skipped_irrelevant += 1
            continue

        matched_any_lga = False

        # 1. Try LGA-level matches first (only meaningful if within a target state)
        for lga_name in gadm_lgas:
            lga_id = lga_lookup.get(lga_name.lower().strip())
            if lga_id:
                records_to_insert.append((
                    lga_id, event_date, 'EM-DAT', severity,
                    f"{lga_name} | {raw_location}"[:200]
                ))
                lga_level_count += 1
                matched_any_lga = True

        # 2. For target states with no specific LGA match, assign to all LGAs in that state
        if not matched_any_lga:
            for state_name in relevant_states:
                lga_ids_in_state = lgas_by_state.get(state_name, [])
                for lga_id in lga_ids_in_state:
                    records_to_insert.append((
                        lga_id, event_date, 'EM-DAT', severity,
                        f"[state-level: {state_name}] | {raw_location}"[:200]
                    ))
                    state_level_count += 1

    upsert_query = """
        INSERT INTO flood_events_raw (lga_id, event_date, source, severity, raw_location)
        VALUES %s;
    """

    try:
        if records_to_insert:
            execute_values(cursor, upsert_query, records_to_insert, page_size=1000)
            conn.commit()
        print(f"\n✓ Ingestion Complete! Inserted {len(records_to_insert)} records into flood_events_raw.")
        print(f"  └─ Direct LGA-level matches: {lga_level_count}")
        print(f"  └─ State-level events expanded to all LGAs in state: {state_level_count}")
        print(f"  └─ Skipped (no target state involved): {skipped_irrelevant}")
        print(f"  └─ Skipped (no valid date): {skipped_no_date}")
    except Exception as e:
        conn.rollback()
        print(f"✗ Database insertion failed: {e}")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    process_emdat()