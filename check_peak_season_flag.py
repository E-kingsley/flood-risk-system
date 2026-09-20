"""
check_peak_season_flag.py

peak_season_flag is presumably a calendar-based flag (e.g. flood/rainy
season months), meaning it should be constant across all years and
LGAs for a given month. This confirms that assumption and prints the
month -> flag mapping needed to replicate it for live forecasts.

USAGE
-----
    python check_peak_season_flag.py
"""

import os

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def main():
    conn = get_connection()
    try:
        df = pd.read_sql("SELECT month, peak_season_flag FROM lga_monthly_features;", conn)
    finally:
        conn.close()

    print("Distinct peak_season_flag values per month (should show exactly ONE value per month if it's purely calendar-based):")
    summary = df.groupby("month")["peak_season_flag"].agg(["nunique", "unique"])
    print(summary)

    if (summary["nunique"] == 1).all():
        print("\nConfirmed: peak_season_flag depends only on calendar month.")
        print("\nMonth -> flag mapping:")
        mapping = df.groupby("month")["peak_season_flag"].first().to_dict()
        for month in sorted(mapping):
            print(f"  {month}: {int(mapping[month])}")
    else:
        print("\nUnexpected: peak_season_flag varies within at least one month across")
        print("years/LGAs — it's not purely calendar-based. Paste this output back")
        print("so we can figure out what else it depends on.")


if __name__ == "__main__":
    main()
