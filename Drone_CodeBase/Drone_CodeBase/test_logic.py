#!/usr/bin/env python3
"""Test the list_surveys logic."""
import sqlite3
from pathlib import Path

DB_FILE = Path("pipeline_results.db")

if not DB_FILE.exists():
    print("Database not found!")
    exit(1)

conn = sqlite3.connect(str(DB_FILE))
cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [row[0] for row in cursor]
conn.close()

print(f"All tables: {tables}")

# Extract unique survey_ids from table names (format: survey_id__table_name)
survey_ids = set()
for table in tables:
    if "__" in table:
        survey_id = table.split("__")[0]
        survey_ids.add(survey_id)
        print(f"  Found survey: {survey_id}")

print(f"\nExtracted survey_ids: {survey_ids}")

# Now test filtering for sample_data
survey_id = "sample_data"
filtered = [t for t in tables if t.startswith(f"{survey_id}__")]
print(f"\nTables for {survey_id}: {filtered}")
