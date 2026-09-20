import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT", 5432),
    dbname=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
)
cur = conn.cursor()
cur.execute("DELETE FROM flood_events_raw WHERE source = 'EM-DAT'")
conn.commit()
print(f"Cleared {cur.rowcount} old EM-DAT rows.")
cur.close()
conn.close()