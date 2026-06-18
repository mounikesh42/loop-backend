#!/usr/bin/env python3
"""Helpers to store pipeline JSON envelopes into a SQLite database."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Dict, Any


def _sanitize_table_name(name: str) -> str:
    # allow only letters, digits, and underscore
    return re.sub(r"[^0-9A-Za-z_]", "_", name)


def default_db_path(root: Path) -> Path:
    override = os.environ.get("LOOP_PIPELINE_DB")
    if override:
        return Path(override)

    for parent in (root, *Path(root).parents):
        candidate = parent / "apicalls"
        if candidate.exists():
            return candidate / "pipeline.db"

    return Path(root) / "outputs" / "pipeline.db"


def save_outputs_to_db(db_path: Path, root: Path, config: Dict[str, Any]) -> None:
    db_path = Path(db_path)
    root = Path(root)
    outputs = config.get("outputs", {})
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        for key, rel in outputs.items():
            table = _sanitize_table_name(f"gcp_{key}")
            out_path = root / rel
            if not out_path.exists():
                continue
            with out_path.open("r", encoding="utf-8") as fh:
                envelope = json.load(fh)

            # create per-stage table
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS \"{table}\" (stage TEXT PRIMARY KEY, generated_at TEXT, spec_version TEXT, envelope TEXT)"
            )

            stage = envelope.get("stage", key)
            generated_at = envelope.get("generated_at")
            spec_version = envelope.get("spec_version")
            envelope_text = json.dumps(envelope, ensure_ascii=False)

            # upsert by stage
            cur.execute(
                f"INSERT OR REPLACE INTO \"{table}\" (stage, generated_at, spec_version, envelope) VALUES (?, ?, ?, ?)",
                (stage, generated_at, spec_version, envelope_text),
            )
        conn.commit()
    finally:
        conn.close()


def list_tables(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def read_table(db_path: Path, table: str) -> list[Dict[str, Any]]:
    table_s = _sanitize_table_name(table)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT stage, generated_at, spec_version, envelope FROM \"{table_s}\"")
        rows = cur.fetchall()
        results: list[Dict[str, Any]] = []
        for stage, gen, spec, envelope_text in rows:
            try:
                envelope = json.loads(envelope_text)
            except Exception:
                envelope = envelope_text
            results.append({"stage": stage, "generated_at": gen, "spec_version": spec, "envelope": envelope})
        return results
    finally:
        conn.close()
