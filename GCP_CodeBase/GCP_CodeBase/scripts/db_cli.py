#!/usr/bin/env python3
"""CLI to save pipeline outputs to SQLite, list tables, and query tables."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from db_tools import default_db_path, save_outputs_to_db, list_tables, read_table


def cmd_save(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config_path = root / args.config
    if not config_path.exists():
        print(f"config not found: {config_path}", file=sys.stderr)
        return 1
    with config_path.open(encoding="utf-8") as fh:
        config = json.load(fh)

    db_path = Path(args.db) if args.db else default_db_path(root)
    save_outputs_to_db(db_path, root, config)
    print(f"Saved outputs into database {db_path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    db_path = Path(args.db) if args.db else default_db_path(Path(".").resolve())
    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 1
    tables = list_tables(db_path)
    for t in tables:
        print(t)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    db_path = Path(args.db) if args.db else default_db_path(Path(".").resolve())
    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 1
    rows = read_table(db_path, args.table)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DB CLI for GCP pipeline outputs")
    sub = parser.add_subparsers(dest="cmd")

    p_save = sub.add_parser("save", help="Save pipeline outputs into sqlite DB")
    p_save.add_argument("--db", default=None, help="Path to sqlite DB file (default: shared apicalls/pipeline.db)")
    p_save.add_argument("--root", default=".", help="Project root where outputs live (default: .)")
    p_save.add_argument("--config", default="paths.json", help="Config file relative to root (default: paths.json)")
    p_save.set_defaults(func=cmd_save)

    p_list = sub.add_parser("list", help="List tables in DB")
    p_list.add_argument("--db", default=None, help="Path to sqlite DB file (default: shared apicalls/pipeline.db)")
    p_list.set_defaults(func=cmd_list)

    p_q = sub.add_parser("query", help="Query a specific table and print results as JSON")
    p_q.add_argument("table", help="Table name to query (e.g., gcp_stage1_inventory)")
    p_q.add_argument("--db", default=None, help="Path to sqlite DB file (default: shared apicalls/pipeline.db)")
    p_q.set_defaults(func=cmd_query)

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
