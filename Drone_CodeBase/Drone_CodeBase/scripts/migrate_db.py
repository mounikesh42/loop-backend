#!/usr/bin/env python3
"""Migrate old database format to new single database with survey_id prefixes."""
import sqlite3
from pathlib import Path

old_db = Path("dbsqlite")
new_db = Path("pipeline_results.db")
survey_id = "sample_data"

if not old_db.exists():
    print("Old database not found. Creating empty new database...")
    new_conn = sqlite3.connect(str(new_db))
    new_conn.close()
    exit(0)

old_conn = sqlite3.connect(str(old_db))
new_conn = sqlite3.connect(str(new_db))

cursor = old_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
old_tables = [row[0] for row in cursor.fetchall()]

print(f"Migrating {len(old_tables)} tables...")

for old_table in old_tables:
    new_table = f"{survey_id}__{old_table}"
    
    cursor = old_conn.execute(f"PRAGMA table_info({old_table})")
    columns = [row[1] for row in cursor.fetchall()]
    
    col_str = ", ".join(columns)
    new_conn.execute(f"CREATE TABLE {new_table} ({col_str})")
    
    cursor = old_conn.execute(f"SELECT * FROM {old_table}")
    rows = cursor.fetchall()
    
    placeholders = ", ".join(["?" for _ in columns])
    new_conn.executemany(f"INSERT INTO {new_table} VALUES ({placeholders})", rows)
    
    print(f"  {old_table} -> {new_table} ({len(rows)} rows)")

new_conn.commit()
new_conn.close()
old_conn.close()

print("Migration complete!")
