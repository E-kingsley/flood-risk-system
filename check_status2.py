import psycopg2, os
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(
    host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", 5432),
    dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
)
cur = conn.cursor()

print("--- lgas table columns ---")
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='lgas' ORDER BY column_name;")
for row in cur.fetchall():
    print(row[0])

print("\n--- distinct flood_risk_label values + counts ---")
cur.execute("SELECT flood_risk_label, COUNT(*) FROM lga_monthly_features GROUP BY flood_risk_label ORDER BY flood_risk_label;")
for row in cur.fetchall():
    print(row)

cur.close()
conn.close()