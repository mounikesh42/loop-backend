"""Simple SQLite dumper for stage envelopes.

Creates (or opens) the shared SQLite database at `apicalls/pipeline.db`
and stores each BaseStation stage in its own `base_station_*` table.
Each table stores the top-level data keys as JSON values so the full
payloads are preserved while keeping BaseStation and GCP outputs separate.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _shared_db_path(project_root: Path) -> Path:
    override = os.environ.get("LOOP_PIPELINE_DB")
    if override:
        return Path(override)

    for parent in (project_root, *project_root.parents):
        candidate = parent / "apicalls"
        if candidate.exists():
            return candidate / "pipeline.db"

    return project_root / "outputs" / "pipeline.db"


def _table_name_for_stage(stage: str | None) -> str:
    suffix = re.sub(r"[^0-9A-Za-z_]", "_", stage or "unknown_stage")
    return f"base_station_{suffix}"


def _ensure_schema(conn: sqlite3.Connection, table: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{table}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT NOT NULL,
            spec_version TEXT,
            generated_at TEXT,
            key TEXT NOT NULL,
            value_json TEXT,
            inserted_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def dump_envelope(envelope: dict, project_root: Path) -> None:
    """Insert the given envelope into the shared SQLite DB.

    Args:
        envelope: The envelope as produced by the orchestrator.
        project_root: Path to the project root.
    """
    db_path = _shared_db_path(project_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    stage = envelope.get("stage")
    table = _table_name_for_stage(stage)
    data = envelope.get("data", {})
    if not isinstance(data, dict):
        data = {"value": data}

    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema(conn, table)
        conn.execute(f'DELETE FROM "{table}"')
        rows = [
            (
                stage,
                envelope.get("spec_version"),
                envelope.get("generated_at"),
                key,
                json.dumps(value, sort_keys=True),
                _utc_now_iso(),
            )
            for key, value in data.items()
        ]
        conn.executemany(
            f'INSERT INTO "{table}" (stage, spec_version, generated_at, key, value_json, inserted_at)'
            " VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
