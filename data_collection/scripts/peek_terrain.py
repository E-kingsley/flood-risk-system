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
cur.execute("""
    SELECT s.name AS state, l.name AS lga, l.elevation_mean_m, l.slope_mean_deg
    FROM lgas l JOIN states s ON l.state_id = s.state_id
    ORDER BY l.elevation_mean_m ASC
    LIMIT 10;
""")
for row in cur.fetchall():
    print(row)
cur.close()
conn.close()