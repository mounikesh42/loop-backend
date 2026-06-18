"""Run pipeline and populate simple demo tables in SQLite for quick inspection.

Usage: python scripts/demo_db_run.py [paths.json]

This will:
 - run the existing `scripts/run_pipeline.py` using the provided paths.json (default: paths.json)
 - open the shared `apicalls/pipeline.db` and create simple tables named
   `base_station_demo_<output_basename>`
   where each table has rows (key, value_json, inserted_at) holding the top-level
   keys from the envelope `data` object for that stage.

This is intended for demos and quick inspection — tables store JSON blobs for values.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import db_dump


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _table_name_for(path: Path) -> str:
    # e.g., outputs/02_source_fields.json -> base_station_demo_02_source_fields
    return "base_station_demo_" + path.stem


def _ensure_table(conn: sqlite3.Connection, table: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT,
            value_json TEXT,
            inserted_at TEXT
        )
        """
    )


def populate_from_file(conn: sqlite3.Connection, file_path: Path) -> None:
    with file_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    data = payload.get("data") or {}
    table = _table_name_for(file_path)
    _ensure_table(conn, table)
    for k, v in data.items():
        conn.execute(
            f"INSERT INTO {table} (key, value_json, inserted_at) VALUES (?, ?, ?)",
            (k, json.dumps(v, sort_keys=True), _utc_now_iso()),
        )


def main(argv: list[str]) -> int:
    cfg = Path(argv[1]) if len(argv) > 1 else Path("paths.json")
    if not cfg.exists():
        print(f"paths.json not found at {cfg}")
        return 2

    project_root = cfg.resolve().parent

    # run the pipeline (import and call main)
    import run_pipeline as rp

    rc = rp.main([sys.argv[0], str(cfg)])
    if rc != 0:
        print(f"pipeline exited with code {rc}")

    db_path = db_dump._shared_db_path(project_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        # populate demo tables from each configured output JSON
        with cfg.open("r", encoding="utf-8") as fh:
            paths = json.load(fh)
        outputs = paths.get("outputs", {})
        for name, rel in outputs.items():
            p = project_root / rel
            if p.exists():
                populate_from_file(conn, p)
        conn.commit()
    finally:
        conn.close()

    print(f"demo tables added to {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
