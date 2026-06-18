#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect("pipeline_results.db")
cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print(f"Tables in database: {len(tables)}")
for t in sorted(tables):
    print(f"  {t}")
conn.close()
