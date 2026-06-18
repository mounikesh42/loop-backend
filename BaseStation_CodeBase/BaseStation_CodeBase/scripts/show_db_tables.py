"""Simple viewer for shared pipeline DB demo tables.

Usage:
  python scripts/show_db_tables.py            # list tables and row counts
  python scripts/show_db_tables.py base_station_demo_02_source_fields  # show 20 rows
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path


def _default_db_path() -> Path:
    override = os.environ.get("LOOP_PIPELINE_DB")
    if override:
        return Path(override)

    cwd = Path.cwd().resolve()
    for parent in (cwd, *cwd.parents):
        candidate = parent / "apicalls"
        if candidate.exists():
            return candidate / "pipeline.db"

    return Path("outputs") / "pipeline.db"


DB = _default_db_path()


def list_tables(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r[0] for r in cur.fetchall()]


def count_rows(conn, table):
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


def show_rows(conn, table, limit=20):
    cur = conn.cursor()
    cur.execute(f"SELECT id, key, value_json, inserted_at FROM {table} ORDER BY id LIMIT ?", (limit,))
    rows = cur.fetchall()
    for r in rows:
        id_, key, val, inserted = r
        try:
            parsed = json.loads(val)
            pretty = json.dumps(parsed, indent=2, sort_keys=True)
        except Exception:
            pretty = str(val)
        print(f"-- id={id_} key={key} inserted_at={inserted}\n{pretty}\n")


def main(argv):
    if not DB.exists():
        print(f"DB not found at {DB}. Run the demo runner first: python scripts/demo_db_run.py paths.json")
        return 2
    conn = sqlite3.connect(str(DB))
    try:
        if len(argv) == 1:
            tables = list_tables(conn)
            for t in tables:
                try:
                    cnt = count_rows(conn, t)
                except Exception:
                    cnt = "?"
                print(f"{t}: {cnt} rows")
        else:
            table = argv[1]
            show_rows(conn, table)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
