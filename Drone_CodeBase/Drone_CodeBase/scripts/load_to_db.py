#!/usr/bin/env python3
"""GCP PPK pipeline JSON → SQLite loader.

Reads paths.json (the same config used by run_pipeline.py), opens each
stage output JSON, and stores the data payload as a flat table in a SQLite
database.  Existing tables are replaced on every run.

Stages loaded:
    stage1_inventory, stage2_source_fields, stage3_derived,
    stage3_indicators, stage3_building_blocks, stage3_cal_conf,
    stage3_drone_score

Usage:
    python load_to_db.py paths.json [--db pipeline.db]
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    with path.open() as fh:
        return json.load(fh)


def shared_db_path(root: Path) -> Path:
    override = os.environ.get("LOOP_PIPELINE_DB")
    if override:
        return Path(override)

    for parent in (root, *root.parents):
        candidate = parent / "apicalls"
        if candidate.exists():
            return candidate / "pipeline.db"

    return root / "outputs" / "pipeline.db"


def normalise_rows(payload) -> list[dict]:
    """Return a list of flat dicts suitable for INSERT.

    The pipeline envelopes can carry:
      • a list of dicts          → used directly
      • a dict of point_id→dict  → flattened to rows with a 'point_id' key
      • a plain dict             → wrapped in a one-element list
    """
    if isinstance(payload, list):
        return [_flatten(r) for r in payload]
    if isinstance(payload, dict):
        # Heuristic: if every value is itself a dict, treat keys as point IDs
        if all(isinstance(v, dict) for v in payload.values()):
            rows = []
            for point_id, record in payload.items():
                row = {"point_id": point_id}
                row.update(_flatten(record))
                rows.append(row)
            return rows
        return [_flatten(payload)]
    # Scalar – shouldn't happen but handle gracefully
    return [{"value": payload}]


def _flatten(obj: dict, prefix: str = "") -> dict:
    """Recursively flatten a nested dict with '__' as the separator.

    Nested lists are JSON-serialised into a single text column so every
    column stays a scalar and SQLite stays happy.
    """
    out = {}
    for k, v in obj.items():
        col = f"{prefix}__{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, col))
        elif isinstance(v, list):
            out[col] = json.dumps(v)
        else:
            out[col] = v
    return out


def infer_col_type(value) -> str:
    if isinstance(value, bool):
        return "INTEGER"   # SQLite has no BOOLEAN; 0/1
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def create_and_insert(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    if not rows:
        print(f"  [{table}] no rows – skipping.")
        return 0

    # Collect all column names (union across rows to handle sparse data)
    all_cols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for col in row:
            if col not in seen:
                all_cols.append(col)
                seen.add(col)

    # Infer types from first non-None value per column
    col_types: dict[str, str] = {}
    for col in all_cols:
        for row in rows:
            v = row.get(col)
            if v is not None:
                col_types[col] = infer_col_type(v)
                break
        else:
            col_types[col] = "TEXT"

    col_defs = ", ".join(f'"{c}" {col_types[c]}' for c in all_cols)
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.execute(f'CREATE TABLE "{table}" ({col_defs})')

    placeholders = ", ".join("?" for _ in all_cols)
    insert_sql = f'INSERT INTO "{table}" VALUES ({placeholders})'

    records = [
        tuple(row.get(col) for col in all_cols)
        for row in rows
    ]
    conn.executemany(insert_sql, records)
    conn.commit()
    return len(records)


# ---------------------------------------------------------------------------
# Stage map  –  (output key in paths.json,  table name,  data key in envelope)
# ---------------------------------------------------------------------------

STAGES = [
    ("stage1_inventory",       "drone_stage1_inventory",       "data"),
    ("stage2_source_fields",   "drone_stage2_source_fields",   "data"),
    ("stage3_derived",         "drone_stage3_derived",         "data"),
    ("stage3_indicators",      "drone_stage3_indicators",      "data"),
    ("stage3_building_blocks", "drone_stage3_building_blocks", "data"),
    ("stage3_cal_conf",        "drone_stage3_cal_conf",        "data"),
    ("stage3_drone_score",     "drone_stage3_drone_score",     "data"),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Load pipeline JSONs into SQLite")
    parser.add_argument("config", help="Path to paths.json")
    parser.add_argument("--db", default=None,
                        help="SQLite database file (default: shared apicalls/pipeline.db)")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 1

    config = json.loads(config_path.read_text())
    root = config_path.parent
    outputs = config.get("outputs", {})

    db_path = Path(args.db) if args.db else shared_db_path(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Database : {db_path.resolve()}")
    print(f"Config   : {config_path}")
    print()

    conn = sqlite3.connect(db_path)

    total_tables = 0
    total_rows = 0

    for output_key, table_name, data_key in STAGES:
        rel_path = outputs.get(output_key)
        if not rel_path:
            print(f"  [{table_name}] key '{output_key}' not in config outputs – skipped.")
            continue

        json_path = root / rel_path
        if not json_path.exists():
            print(f"  [{table_name}] file not found: {json_path} – skipped.")
            continue

        envelope = load_json(json_path)
        payload = envelope.get(data_key, envelope)   # fall back to whole doc
        rows = normalise_rows(payload)

        n = create_and_insert(conn, table_name, rows)
        print(f"  [{table_name}] {n} rows loaded <- {json_path.relative_to(root)}")
        total_tables += 1
        total_rows += n

    conn.close()
    print()
    print(f"Done. {total_tables} table(s), {total_rows} total rows -> {db_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
