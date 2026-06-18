#!/usr/bin/env python3
"""Simple SQLite loader for pipeline JSON outputs.

Commands:
  python scripts/db_sqlite.py load dbsqlite
  python scripts/db_sqlite.py tables dbsqlite
  python scripts/db_sqlite.py query dbsqlite <table_name> [<path_filter>]

The loader flattens each JSON output file into a table named after its file base.
"""
import argparse
import json
import os
import re
import sqlite3
from pathlib import Path


def sanitize_table_name(name: str) -> str:
    name = re.sub(r"[^0-9A-Za-z_]+", "_", name)
    if re.match(r"^[0-9]", name):
        name = f"t_{name}"
    return name


def flatten_json(value, prefix=""):
    if isinstance(value, dict):
        for key, sub in value.items():
            new_prefix = f"{prefix}.{key}" if prefix else key
            yield from flatten_json(sub, new_prefix)
    elif isinstance(value, list):
        for index, sub in enumerate(value):
            new_prefix = f"{prefix}[{index}]"
            yield from flatten_json(sub, new_prefix)
    else:
        yield prefix, json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value


def load_outputs(db_path: Path, outputs_dir: Path):
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        for json_path in sorted(outputs_dir.glob("*.json")):
            table_name = sanitize_table_name(json_path.stem)
            conn.execute(f"CREATE TABLE {table_name} (path TEXT PRIMARY KEY, value TEXT)")
            with json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            rows = list(flatten_json(data))
            conn.executemany(
                f"INSERT INTO {table_name} (path, value) VALUES (?, ?)",
                rows,
            )
            print(f"Loaded {json_path.name} into table {table_name} ({len(rows)} rows)")
        conn.commit()
    finally:
        conn.close()


def list_tables(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        for row in cursor:
            print(row[0])
    finally:
        conn.close()


def query_table(db_path: Path, table: str, path_filter: str | None, limit: int = 100):
    conn = sqlite3.connect(str(db_path))
    try:
        if path_filter:
            sql = f"SELECT path, value FROM {table} WHERE path LIKE ? ORDER BY path LIMIT ?"
            cursor = conn.execute(sql, (path_filter, limit))
        else:
            sql = f"SELECT path, value FROM {table} ORDER BY path LIMIT ?"
            cursor = conn.execute(sql, (limit,))
        rows = cursor.fetchall()
        if not rows:
            print("No rows found.")
            return
        for path, value in rows:
            print(path)
            print(value)
            print("---")
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"Database error: {exc}")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Load pipeline outputs into a SQLite database.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    load_parser = subparsers.add_parser("load", help="Load all outputs JSON files into SQLite")
    load_parser.add_argument("db_path", type=Path, help="SQLite database path")
    load_parser.add_argument(
        "--outputs-dir",
        type=Path,
        default=Path("outputs"),
        help="Directory containing JSON output files (default: outputs)",
    )

    tables_parser = subparsers.add_parser("tables", help="List tables in the SQLite database")
    tables_parser.add_argument("db_path", type=Path, help="SQLite database path")

    query_parser = subparsers.add_parser("query", help="Query a flattened JSON table")
    query_parser.add_argument("db_path", type=Path, help="SQLite database path")
    query_parser.add_argument("table", help="Table name to query")
    query_parser.add_argument(
        "path_filter",
        nargs="?",
        help="Optional LIKE filter for JSON paths, e.g. 'data.L1F_%'",
    )
    query_parser.add_argument("--limit", type=int, default=100, help="Max rows to return")

    args = parser.parse_args()
    if args.command == "load":
        load_outputs(args.db_path, args.outputs_dir)
    elif args.command == "tables":
        list_tables(args.db_path)
    elif args.command == "query":
        query_table(args.db_path, args.table, args.path_filter, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
