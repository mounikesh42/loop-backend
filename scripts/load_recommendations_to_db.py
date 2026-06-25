#!/usr/bin/env python3
"""Load a module recommendation artifact into the shared pipeline SQLite DB."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


TABLES = {
    "base_station": "base_station_stage4_recommendations",
    "drone": "drone_stage4_recommendations",
    "gcp": "gcp_stage4_recommendations",
    "check_point": "check_point_stage4_recommendations",
}


def shared_db_path(root: Path) -> Path:
    override = os.environ.get("LOOP_PIPELINE_DB")
    if override:
        return Path(override)

    for parent in (root, *root.parents):
        candidate = parent / "apicalls"
        if candidate.exists():
            return candidate / "pipeline.db"

    return root / "outputs" / "pipeline.db"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load recommendations JSON into SQLite")
    parser.add_argument("namespace", choices=sorted(TABLES))
    parser.add_argument("config", help="Path to the module paths.json/job config")
    parser.add_argument(
        "--recommendations",
        default="outputs/07_recommendations.json",
        help="Recommendation JSON path relative to the module root",
    )
    parser.add_argument("--db", default=None, help="SQLite DB path")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 1

    root = config_path.parent
    rec_path = root / args.recommendations
    if not rec_path.exists():
        print(f"ERROR: recommendations not found: {rec_path}", file=sys.stderr)
        return 1

    payload = load_json(rec_path)
    db_path = Path(args.db) if args.db else shared_db_path(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    table = TABLES[args.namespace]

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        conn.execute(
            f"""
            CREATE TABLE "{table}" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL,
                source_path TEXT NOT NULL,
                envelope TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f'INSERT INTO "{table}" (namespace, source_path, envelope) VALUES (?, ?, ?)',
            (args.namespace, str(rec_path), json.dumps(payload, sort_keys=True)),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"[{table}] loaded <- {rec_path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
