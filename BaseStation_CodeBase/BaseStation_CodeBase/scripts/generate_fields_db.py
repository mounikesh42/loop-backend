"""generate_fields_db.py — Build and query a SQLite DB from CSV field definitions.

Reads the indicator and building-block field specs from the two CSVs and:
  1. Creates (or re-creates) a SQLite DB with two typed tables.
  2. Inserts every row from each CSV.
  3. Dumps a human-readable extract of only the fields defined in those CSVs.

Usage:
    python generate_fields_db.py
    python generate_fields_db.py --db outputs/results.db
    python generate_fields_db.py --db outputs/results.db --query indicators
    python generate_fields_db.py --db outputs/results.db --query building_blocks

Arguments:
    --db      Path to the SQLite DB file (default: outputs/fields.db)
    --query   Which table to show: indicators | building_blocks | all (default: all)
    --csv-dir Directory containing the CSV files (default: same folder as this script)
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Column definitions mirrored from the CSVs ────────────────────────────────

INDICATORS_FIELDS: list[tuple[str, str]] = [
    ("indicator_id",         "TEXT PRIMARY KEY"),
    ("indicator_name",       "TEXT"),
    ("display_name",         "TEXT"),
    ("building_block_id",    "TEXT"),
    ("weight_in_block",      "REAL"),
    ("covers_problems",      "TEXT"),
    ("input_derived_fields", "TEXT"),
    ("has_internal_gate",    "INTEGER"),   # bool → 0/1
    ("gate_condition",       "TEXT"),
    ("gate_action",          "TEXT"),
    ("justification",        "TEXT"),
    ("threshold_summary",    "TEXT"),
]

BUILDING_BLOCKS_FIELDS: list[tuple[str, str]] = [
    ("block_id",                      "TEXT PRIMARY KEY"),
    ("block_name",                    "TEXT"),
    ("display_name",                  "TEXT"),
    ("weight_in_base_station_score",  "REAL"),
    ("question",                      "TEXT"),
    ("failure_owner",                 "TEXT"),
    ("operator_action",               "TEXT"),
    ("formula",                       "TEXT"),
    ("has_internal_gate",             "INTEGER"),
    ("gate_condition",                "TEXT"),
    ("gate_action",                   "TEXT"),
]

TABLE_INDICATORS      = "indicators"
TABLE_BUILDING_BLOCKS = "building_blocks"

# ── ANSI helpers ──────────────────────────────────────────────────────────────

RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
CYAN    = "\033[96m"
YELLOW  = "\033[93m"
GREEN   = "\033[92m"
MAGENTA = "\033[95m"
WHITE   = "\033[97m"
BG_DARK = "\033[48;5;235m"

COL_KEY = 34
COL_VAL = 70

def _rule(char="─", width=COL_KEY + COL_VAL + 5) -> str:
    return f"{DIM}{char * width}{RESET}"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _create_tables(conn: sqlite3.Connection) -> None:
    """Create the two field tables (drops and recreates if they exist)."""
    cur = conn.cursor()

    ind_cols = ",\n    ".join(f"{col} {typ}" for col, typ in INDICATORS_FIELDS)
    cur.execute(f"DROP TABLE IF EXISTS {TABLE_INDICATORS}")
    cur.execute(f"""
        CREATE TABLE {TABLE_INDICATORS} (
            {ind_cols},
            inserted_at TEXT NOT NULL
        )
    """)

    bb_cols = ",\n    ".join(f"{col} {typ}" for col, typ in BUILDING_BLOCKS_FIELDS)
    cur.execute(f"DROP TABLE IF EXISTS {TABLE_BUILDING_BLOCKS}")
    cur.execute(f"""
        CREATE TABLE {TABLE_BUILDING_BLOCKS} (
            {bb_cols},
            inserted_at TEXT NOT NULL
        )
    """)

    conn.commit()


def _bool(val: str) -> int:
    """Convert CSV boolean strings ('True'/'False'/'1'/'0') to 0/1."""
    return 1 if str(val).strip().lower() in ("true", "1", "yes") else 0


def _load_indicators(conn: sqlite3.Connection, csv_path: Path) -> int:
    ts = datetime.now(timezone.utc).isoformat()
    cols = [f for f, _ in INDICATORS_FIELDS]
    placeholders = ", ".join("?" for _ in range(len(cols) + 1))
    sql = f"INSERT INTO {TABLE_INDICATORS} ({', '.join(cols)}, inserted_at) VALUES ({placeholders})"

    count = 0
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            values = [
                row.get("indicator_id", ""),
                row.get("indicator_name", ""),
                row.get("display_name", ""),
                row.get("building_block_id", ""),
                float(row["weight_in_block"]) if row.get("weight_in_block") else None,
                row.get("covers_problems", ""),
                row.get("input_derived_fields", ""),
                _bool(row.get("has_internal_gate", "false")),
                row.get("gate_condition", ""),
                row.get("gate_action", ""),
                row.get("justification", ""),
                row.get("threshold_summary", ""),
                ts,
            ]
            conn.execute(sql, values)
            count += 1
    conn.commit()
    return count


def _load_building_blocks(conn: sqlite3.Connection, csv_path: Path) -> int:
    ts = datetime.now(timezone.utc).isoformat()
    cols = [f for f, _ in BUILDING_BLOCKS_FIELDS]
    placeholders = ", ".join("?" for _ in range(len(cols) + 1))
    sql = f"INSERT INTO {TABLE_BUILDING_BLOCKS} ({', '.join(cols)}, inserted_at) VALUES ({placeholders})"

    count = 0
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            values = [
                row.get("block_id", ""),
                row.get("block_name", ""),
                row.get("display_name", ""),
                float(row["weight_in_base_station_score"]) if row.get("weight_in_base_station_score") else None,
                row.get("question", ""),
                row.get("failure_owner", ""),
                row.get("operator_action", ""),
                row.get("formula", ""),
                _bool(row.get("has_internal_gate", "false")),
                row.get("gate_condition", ""),
                row.get("gate_action", ""),
                ts,
            ]
            conn.execute(sql, values)
            count += 1
    conn.commit()
    return count


# ── Terminal renderer ─────────────────────────────────────────────────────────

def _render_table(conn: sqlite3.Connection, table: str, fields: list[tuple[str, str]]) -> None:
    label = table.replace("_", " ").upper()
    cur = conn.cursor()
    col_names = [f for f, _ in fields]
    cur.execute(f"SELECT {', '.join(col_names)} FROM {table}")
    rows = cur.fetchall()

    print()
    print(f"{BG_DARK}{BOLD}{MAGENTA} ▶  {label} {RESET}  {DIM}({len(rows)} row{'s' if len(rows)!=1 else ''}){RESET}")
    print(_rule("═"))
    print(f"{BOLD}{WHITE}{'FIELD':<{COL_KEY}}  {'VALUE':<{COL_VAL}}{RESET}")
    print(_rule())

    for row in rows:
        first_field = True
        for i, (col, _) in enumerate(fields):
            val = row[i]
            val_str = "" if val is None else str(val)
            if first_field:
                print(f"{YELLOW}{col:<{COL_KEY}}{RESET}  {val_str}")
                first_field = False
            else:
                # indent subsequent fields of the same record
                print(f"{DIM}{col:<{COL_KEY}}{RESET}  {val_str}")
        print(_rule("┄"))


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",      default="outputs/fields.db",
                        help="Path to SQLite DB file")
    parser.add_argument("--query",   default="all",
                        choices=["all", "indicators", "building_blocks"],
                        help="Which table to display")
    parser.add_argument("--csv-dir", default=None,
                        help="Directory containing the CSV files (default: script dir)")
    args = parser.parse_args(argv[1:])

    # ── Locate CSVs ──
    csv_dir = Path(args.csv_dir) if args.csv_dir else Path(__file__).parent
    indicators_csv     = csv_dir / "04_indicators.csv"
    building_blocks_csv = csv_dir / "05_building_blocks.csv"

    for p in (indicators_csv, building_blocks_csv):
        if not p.exists():
            print(f"ERROR: CSV not found: {p}")
            return 2

    # ── Create DB ──
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))

    print(f"\n{BOLD}{CYAN}{'FIELD DB GENERATOR':^{COL_KEY+COL_VAL+5}}{RESET}")
    print(_rule("═"))

    _create_tables(conn)
    n_ind = _load_indicators(conn, indicators_csv)
    n_bb  = _load_building_blocks(conn, building_blocks_csv)

    print(f"\n{GREEN}✓{RESET} DB created at  {CYAN}{db_path.resolve()}{RESET}")
    print(f"  {GREEN}indicators{RESET}      → {n_ind} rows inserted")
    print(f"  {GREEN}building_blocks{RESET} → {n_bb} rows inserted")

    # ── Extract / display ──
    if args.query in ("all", "indicators"):
        _render_table(conn, TABLE_INDICATORS, INDICATORS_FIELDS)

    if args.query in ("all", "building_blocks"):
        _render_table(conn, TABLE_BUILDING_BLOCKS, BUILDING_BLOCKS_FIELDS)

    conn.close()
    print(f"\n{DIM}Tip: filter output with --query indicators  or  --query building_blocks{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
