#!/usr/bin/env python3
"""SQLite CLI for processing pipeline outputs.

Commands:
  run paths.json dbsqlite3        Run the pipeline and store JSON envelopes into SQLite
  list-tables dbsqlite3           List tables in the SQLite database
  show-table dbsqlite3 table_name [--limit N]  Show rows from a specific table
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import common  # noqa: E402
import csv_export  # noqa: E402
import run_pipeline  # noqa: E402


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            config_path TEXT NOT NULL,
            survey_id TEXT,
            subsystem TEXT,
            spec_version TEXT,
            run_started_at TEXT NOT NULL,
            run_completed_at TEXT,
            status TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stage_envelopes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            stage TEXT NOT NULL,
            artifact_key TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            spec_version TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            config_used JSON NOT NULL,
            data JSON NOT NULL,
            FOREIGN KEY(run_id) REFERENCES pipeline_runs(run_id)
        )
        """
    )
    conn.commit()


def _insert_pipeline_run(
    conn: sqlite3.Connection,
    config_path: Path,
    config: dict,
    started_at: str,
    completed_at: str | None,
    status: str,
) -> int:
    cursor = conn.execute(
        "INSERT INTO pipeline_runs (config_path, survey_id, subsystem, spec_version, run_started_at, run_completed_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            str(config_path),
            config.get("survey_id"),
            config.get("subsystem"),
            config.get("spec_version"),
            started_at,
            completed_at,
            status,
        ),
    )
    return cursor.lastrowid


def _insert_envelope_row(conn: sqlite3.Connection, run_id: int, artifact_key: str, envelope_path: Path, envelope: dict) -> None:
    conn.execute(
        "INSERT INTO stage_envelopes (run_id, stage, artifact_key, artifact_path, spec_version, generated_at, config_used, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            envelope.get("stage"),
            artifact_key,
            str(envelope_path),
            envelope.get("spec_version"),
            envelope.get("generated_at"),
            json.dumps(envelope.get("config_used", {}), sort_keys=True, ensure_ascii=False),
            json.dumps(envelope.get("data", {}), sort_keys=True, ensure_ascii=False),
        ),
    )


def run_and_store(config_path: Path, db_path: Path, export_csv: str = None, export_xlsx: str = None) -> int:
    config = common.load_config(config_path)
    root = config_path.parent
    started_at = datetime.now().isoformat(timespec="seconds")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _open_db(db_path)
    _ensure_schema(conn)
    try:
        # Build arguments for run_pipeline
        pipeline_args = [str(config_path)]
        if export_csv:
            pipeline_args.extend(["--export-csv", export_csv])
        if export_xlsx:
            pipeline_args.extend(["--export-xlsx", export_xlsx])
        
        exit_code = run_pipeline.main(pipeline_args)
        status = "SUCCESS" if exit_code == 0 else f"FAILURE_{exit_code}"
        completed_at = datetime.now().isoformat(timespec="seconds")
        run_id = _insert_pipeline_run(conn, config_path, config, started_at, completed_at, status)

        for artifact_key, artifact_rel in config.get("outputs", {}).items():
            envelope_path = root / artifact_rel
            if not envelope_path.exists():
                print(f"Warning: output artifact missing, skipping {artifact_key}: {envelope_path}")
                continue
            with envelope_path.open("r", encoding="utf-8") as fh:
                envelope = json.load(fh)
            _insert_envelope_row(conn, run_id, artifact_key, envelope_path, envelope)

        conn.commit()
        print(f"Stored {len(config.get('outputs', {}))} artifacts into {db_path}")
        return exit_code
    finally:
        conn.close()


def list_tables(db_path: Path) -> None:
    conn = _open_db(db_path)
    try:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        rows = cursor.fetchall()
        if not rows:
            print("No tables found.")
            return
        for row in rows:
            print(row[0])
    finally:
        conn.close()


def show_table(db_path: Path, table: str, limit: int) -> None:
    conn = _open_db(db_path)
    try:
        cursor = conn.execute(f"SELECT * FROM {table} LIMIT ?", (limit,))
        rows = cursor.fetchall()
        if not rows:
            print(f"No rows found in table '{table}'.")
            return
        columns = [description[0] for description in cursor.description]
        print("\t".join(columns))
        for row in rows:
            values = []
            for col in columns:
                value = row[col]
                if isinstance(value, str) and (col in ("config_used", "data")):
                    values.append(value)
                else:
                    values.append(str(value))
            print("\t".join(values))
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Store and inspect pipeline outputs in SQLite")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_run = subparsers.add_parser("run", help="Run the pipeline and store outputs in a SQLite database")
    parser_run.add_argument("config", help="Path to paths.json")
    parser_run.add_argument("database", help="SQLite database file path")
    parser_run.add_argument("--export-csv", dest="export_csv", default=None,
                            help="Export final results to CSV file (e.g., '19thmay.csv')")
    parser_run.add_argument("--export-xlsx", dest="export_xlsx", default=None,
                            help="Export final results to Excel file with multiple sheets (e.g., '19thmay.xlsx')")

    parser_list = subparsers.add_parser("list-tables", help="List tables in the SQLite database")
    parser_list.add_argument("database", help="SQLite database file path")

    parser_show = subparsers.add_parser("show-table", help="Show rows from a specific table")
    parser_show.add_argument("database", help="SQLite database file path")
    parser_show.add_argument("table", help="Table name to query")
    parser_show.add_argument("--limit", type=int, default=100, help="Maximum number of rows to show")

    args = parser.parse_args(argv)
    db_path = Path(args.database)

    if args.command == "run":
        return run_and_store(Path(args.config), db_path, args.export_csv, args.export_xlsx)
    if args.command == "list-tables":
        list_tables(db_path)
        return 0
    if args.command == "show-table":
        show_table(db_path, args.table, args.limit)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
