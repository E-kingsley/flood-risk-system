import pandas as pd

df = pd.read_excel("data_collection/downloads/260430_emdat_archive.xlsx")

nigeria_floods = df[
    (df['ISO'] == 'NGA') &
    (df['Disaster Type'].astype(str).str.strip().str.lower() == 'flood')
].copy()

print(f"Total Nigeria flood rows: {len(nigeria_floods)}\n")
print("All columns in file:")
print(list(df.columns))
print()

if 'GADM Admin Units' in df.columns:
    non_null = nigeria_floods['GADM Admin Units'].notna().sum()
    print(f"'GADM Admin Units' present, non-null count: {non_null} / {len(nigeria_floods)}")
    print("Sample values:")
    print(nigeria_floods['GADM Admin Units'].dropna().head(5).tolist())
else:
    print("'GADM Admin Units' column NOT FOUND in file.")

print("\nSample 'Location' values (first 15 rows):")
print(nigeria_floods['Location'].head(15).tolist())