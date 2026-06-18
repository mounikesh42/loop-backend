#!/usr/bin/env python3
"""SQLite helper for the Check Point PPK pipeline.

Commands:
  save paths.json dbsqlite    Run the pipeline and save each stage envelope into SQLite.
  tables dbsqlite             List all non-system tables in the SQLite database.
  query dbsqlite table_name    Print all rows from a specific table.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(SCRIPT_DIR))

import common  # noqa: E402
import run_pipeline  # noqa: E402

_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_table_name(table: str) -> None:
    if not _TABLE_NAME_RE.match(table):
        raise ValueError("Invalid table name: must contain only letters, digits, and underscores and not start with a digit")


def _default_db_path(root: Path) -> Path:
    override = os.environ.get("LOOP_PIPELINE_DB")
    if override:
        return Path(override)

    for parent in (root, *root.parents):
        candidate = parent / "apicalls"
        if candidate.exists():
            return candidate / "pipeline.db"

    return root / "outputs" / "pipeline.db"


def _save_stage_outputs(config_path: Path, db_path: Path) -> int:
    config_path = config_path.resolve()
    root = config_path.parent

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    config = common.load_config(config_path)
    result = run_pipeline.main([str(config_path)])
    if result != 0:
        raise SystemExit(result)

    outputs = config.get("outputs", {})
    if not outputs:
        raise KeyError("No outputs defined in config")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for stage_key, rel_path in outputs.items():
            table_name = f"check_point_{stage_key}"
            _validate_table_name(table_name)
            out_path = root / rel_path
            if not out_path.exists():
                raise FileNotFoundError(f"Expected output file not found: {out_path}")
            envelope = json.loads(out_path.read_text(encoding="utf-8"))
            json_text = json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False)
            conn.execute(
                f"DROP TABLE IF EXISTS \"{table_name}\""
            )
            conn.execute(
                f"CREATE TABLE \"{table_name}\" (id INTEGER PRIMARY KEY AUTOINCREMENT, envelope TEXT NOT NULL)"
            )
            conn.execute(
                f"INSERT INTO \"{table_name}\" (envelope) VALUES (?)",
                (json_text,)
            )
        conn.commit()

    print(f"Saved pipeline results to database: {db_path}")
    return 0


def _list_tables(db_path: Path) -> int:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    if not rows:
        print("No tables found.")
        return 0
    for (name,) in rows:
        print(name)
    return 0


def _query_table(db_path: Path, table: str) -> int:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    _validate_table_name(table)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(f"SELECT id, envelope FROM \"{table}\"")
        rows = cursor.fetchall()
    if not rows:
        print(f"No rows found in table: {table}")
        return 0
    for row_id, envelope_text in rows:
        print(f"id={row_id}")
        try:
            payload = json.loads(envelope_text)
            print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        except json.JSONDecodeError:
            print(envelope_text)
        print("-" * 60)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SQLite helper for the Check Point PPK pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    save_parser = subparsers.add_parser("save", help="Run the pipeline and save outputs into SQLite")
    save_parser.add_argument("config", help="Path to paths.json")
    save_parser.add_argument("db", nargs="?", help="Output SQLite database file path (default: shared apicalls/pipeline.db)")

    tables_parser = subparsers.add_parser("tables", help="List all tables in the SQLite database")
    tables_parser.add_argument("db", help="SQLite database file path")

    query_parser = subparsers.add_parser("query", help="Print all rows from a specific SQLite table")
    query_parser.add_argument("db", help="SQLite database file path")
    query_parser.add_argument("table", help="Table name to query")

    args = parser.parse_args(argv)

    try:
        if args.command == "save":
            config_path = Path(args.config).resolve()
            db_path = Path(args.db) if args.db else _default_db_path(config_path.parent)
            return _save_stage_outputs(config_path, db_path)
        if args.command == "tables":
            return _list_tables(Path(args.db))
        if args.command == "query":
            return _query_table(Path(args.db), args.table)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
