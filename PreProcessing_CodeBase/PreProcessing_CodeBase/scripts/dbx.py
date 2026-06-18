#!/usr/bin/env python3
"""SQLite helper for the pre-processing pipeline.

Commands:
  run <config> <db_path>      Run the pipeline and ingest stage outputs into SQLite.
  list-tables <db_path>       List all tables stored in the database.
  extract <db_path> <table>   Dump a specific table in JSON or CSV.

Examples:
  python scripts/dbx.py run paths.json pre_processing.db
  python scripts/dbx.py list-tables pre_processing.db
  python scripts/dbx.py extract pre_processing.db stage3_indicators --format json
"""

import argparse
import csv
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import common  # noqa: E402


def safe_table_name(name: str) -> str:
    return name.replace("-", "_").replace(" ", "_")


def create_base_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS envelopes (
            id INTEGER PRIMARY KEY,
            stage TEXT NOT NULL,
            spec_version TEXT,
            generated_at TEXT,
            config_used TEXT,
            data TEXT
        )
        """
    )
    conn.commit()


def ingest_outputs(conn: sqlite3.Connection, outputs: dict, root: Path) -> None:
    create_base_tables(conn)
    for stage_name, rel_path in outputs.items():
        table_name = safe_table_name(stage_name)
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id INTEGER PRIMARY KEY,
                stage TEXT NOT NULL,
                spec_version TEXT,
                generated_at TEXT,
                config_used TEXT,
                data TEXT
            )
            """
        )
        full_path = root / rel_path
        with full_path.open("r", encoding="utf-8") as fh:
            envelope = json.load(fh)

        config_text = json.dumps(envelope.get("config_used", {}), sort_keys=True)
        data_text = json.dumps(envelope.get("data", {}), sort_keys=True)
        stage_value = envelope.get("stage", stage_name)
        spec_version = envelope.get("spec_version")
        generated_at = envelope.get("generated_at")

        conn.execute(
            f"INSERT INTO {table_name} (stage, spec_version, generated_at, config_used, data)"
            " VALUES (?, ?, ?, ?, ?)",
            (stage_value, spec_version, generated_at, config_text, data_text),
        )
        conn.execute(
            "INSERT INTO envelopes (stage, spec_version, generated_at, config_used, data)"
            " VALUES (?, ?, ?, ?, ?)",
            (stage_value, spec_version, generated_at, config_text, data_text),
        )
    conn.commit()


def run_pipeline_and_ingest(config_path: Path, db_path: Path) -> int:
    config_path = config_path.resolve()
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 1

    try:
        config = common.load_config(config_path)
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse config {config_path}: {exc}", file=sys.stderr)
        return 1

    print(f"Running pipeline with config: {config_path}")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_pipeline.py"), str(config_path)],
        check=True,
    )

    outputs = config["outputs"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        ingest_outputs(conn, outputs, config_path.parent)

    print(f"Ingested {len(outputs)} stage outputs into {db_path}")
    return 0


def list_tables(db_path: Path) -> int:
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    for row in rows:
        print(row[0])
    return 0


def extract_table(db_path: Path, table_name: str, fmt: str) -> int:
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1
    with sqlite3.connect(str(db_path)) as conn:
        try:
            cursor = conn.execute(f"SELECT * FROM {table_name}")
        except sqlite3.OperationalError as exc:
            print(f"ERROR: could not query table {table_name}: {exc}", file=sys.stderr)
            return 1
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    if fmt == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(columns)
        writer.writerows(rows)
    else:
        output = [dict(zip(columns, row)) for row in rows]
        json.dump(output, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SQLite helper for the pre-processing pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run pipeline and ingest outputs into SQLite")
    run_parser.add_argument("config", help="Path to paths.json")
    run_parser.add_argument("db", help="SQLite database path")

    subparsers.add_parser("list-tables", help="List tables in the SQLite database").add_argument("db", help="SQLite database path")

    extract_parser = subparsers.add_parser("extract", help="Extract a specific table from SQLite")
    extract_parser.add_argument("db", help="SQLite database path")
    extract_parser.add_argument("table", help="Table name to extract")
    extract_parser.add_argument("--format", choices=("json", "csv"), default="json", help="Output format")

    args = parser.parse_args(argv)
    if args.command == "run":
        return run_pipeline_and_ingest(Path(args.config), Path(args.db))
    if args.command == "list-tables":
        return list_tables(Path(args.db))
    if args.command == "extract":
        return extract_table(Path(args.db), args.table, args.format)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
