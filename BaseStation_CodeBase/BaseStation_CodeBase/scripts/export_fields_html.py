"""export_fields_html.py — Extract CSV-defined fields from results.db → HTML report.

Reads the existing results.db (key/value_json tables), filters to only the keys
that are defined in 04_indicators.csv and 05_building_blocks.csv, and writes a
styled HTML report.

Usage:
    python scripts/export_fields_html.py
    python scripts/export_fields_html.py --db outputs/results.db
    python scripts/export_fields_html.py --db outputs/results.db --csv-dir data/
    python scripts/export_fields_html.py --db outputs/results.db --out outputs/my_report.html
    python scripts/export_fields_html.py --db outputs/results.db --section indicators
    python scripts/export_fields_html.py --db outputs/results.db --section building_blocks

Arguments:
    --db       Path to the SQLite DB  (default: outputs/results.db)
    --csv-dir  Folder with the two CSV files  (default: same folder as this script)
    --out      HTML output path  (default: outputs/fields_report_<timestamp>.html)
    --section  Which section to include: indicators | building_blocks | all  (default: all)
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── Field names from the two CSVs ─────────────────────────────────────────────

INDICATOR_FIELDS = [
    "indicator_id",
    "indicator_name",
    "display_name",
    "building_block_id",
    "weight_in_block",
    "covers_problems",
    "input_derived_fields",
    "has_internal_gate",
    "gate_condition",
    "gate_action",
    "justification",
    "threshold_summary",
]

BUILDING_BLOCK_FIELDS = [
    "block_id",
    "block_name",
    "display_name",
    "weight_in_base_station_score",
    "question",
    "failure_owner",
    "operator_action",
    "formula",
    "has_internal_gate",
    "gate_condition",
    "gate_action",
]


# ── Load CSV field definitions ────────────────────────────────────────────────

def _load_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r[0] for r in cur.fetchall()]


def _fetch_latest(conn: sqlite3.Connection, table: str) -> list[dict]:
    """Return the latest batch of key/value_json rows from a table."""
    cur = conn.cursor()
    try:
        cur.execute(f"""
            SELECT key, value_json, inserted_at
            FROM {table}
            WHERE inserted_at = (SELECT MAX(inserted_at) FROM {table})
            ORDER BY rowid
        """)
        return [{"key": r[0], "value_json": r[1], "inserted_at": r[2]}
                for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


def _extract_matching_keys(
    rows: list[dict],
    allowed_keys: set[str],
) -> list[dict]:
    """Keep only rows whose key is in allowed_keys."""
    return [r for r in rows if r["key"] in allowed_keys]


# ── Value formatting ──────────────────────────────────────────────────────────

def _fmt_value(raw: str, key: str) -> str:
    """Return an HTML snippet for a value_json cell."""
    try:
        v = json.loads(raw)
    except Exception:
        return f"<span class='str'>{_esc(str(raw))}</span>"

    if isinstance(v, bool):
        cls = "bool-true" if v else "bool-false"
        return f"<span class='{cls}'>{'true' if v else 'false'}</span>"

    if isinstance(v, (int, float)):
        cls = "score-high" if v == 100.0 else ("score-mid" if v >= 60 else "score-low")
        label = f"{v:.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
        return f"<span class='num {cls}'>{label}</span>"

    if isinstance(v, str):
        return f"<span class='str'>{_esc(v)}</span>"

    if isinstance(v, list):
        if not v:
            return "<span class='dim'>— empty —</span>"
        items = []
        for item in v:
            if isinstance(item, dict):
                pairs = "".join(
                    f"<span class='kv'><span class='kv-key'>{_esc(k2)}</span>"
                    f"<span class='kv-val'>{_esc(str(v2))}</span></span>"
                    for k2, v2 in item.items() if not k2.startswith("_")
                )
                items.append(f"<div class='list-item'>{pairs}</div>")
            else:
                items.append(f"<div class='list-item'>{_esc(str(item))}</div>")
        return "<div class='list-wrap'>" + "".join(items) + "</div>"

    if isinstance(v, dict):
        pairs = "".join(
            f"<span class='kv'><span class='kv-key'>{_esc(k2)}</span>"
            f"<span class='kv-val'>{_esc(str(v2))}</span></span>"
            for k2, v2 in v.items()
        )
        return f"<div class='dict-wrap'>{pairs}</div>"

    return f"<pre class='raw'>{_esc(json.dumps(v, indent=2))}</pre>"


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


# ── HTML builder ──────────────────────────────────────────────────────────────

CSS = """
:root {
  --bg:       #0d1117;
  --surface:  #161b22;
  --surface2: #1c2330;
  --border:   #30363d;
  --accent:   #58a6ff;
  --accent2:  #3fb950;
  --text:     #e6edf3;
  --dim:      #8b949e;
  --key-col:  #79c0ff;
  --time-col: #6e7681;
  --high:     #f85149;
  --med:      #e3b341;
  --low:      #3fb950;
  font-family: 'JetBrains Mono','Fira Code','Cascadia Code',ui-monospace,monospace;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body   { background: var(--bg); color: var(--text); padding: 2rem; font-size: 13px; line-height: 1.6; }

/* ── header ── */
.page-header { border-bottom: 1px solid var(--border); padding-bottom: 1rem; margin-bottom: 2rem; }
.page-header h1 { font-size: 1.4rem; font-weight: 700; color: var(--accent); letter-spacing: .04em; }
.page-header .meta { font-size: .75rem; color: var(--dim); margin-top: .3rem; }

/* ── section tabs ── */
.section-nav { display: flex; gap: .5rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
.tab-btn {
  padding: .35rem 1rem; border-radius: 20px; border: 1px solid var(--border);
  background: var(--surface); color: var(--dim); font-size: .78rem;
  cursor: pointer; font-family: inherit; letter-spacing: .05em; transition: all .15s;
}
.tab-btn:hover { border-color: var(--accent); color: var(--accent); }
.tab-btn.active { background: var(--accent); border-color: var(--accent); color: #0d1117; font-weight: 700; }

/* ── search bar ── */
.search-bar {
  width: 100%; padding: .5rem 1rem; background: var(--surface); border: 1px solid var(--border);
  border-radius: 6px; color: var(--text); font-family: inherit; font-size: .85rem;
  margin-bottom: 1.5rem; outline: none;
}
.search-bar:focus { border-color: var(--accent); }

/* ── section block ── */
.section-block { margin-bottom: 2.5rem; }
.section-head {
  display: flex; align-items: center; gap: .6rem;
  background: var(--surface2); border: 1px solid var(--border);
  border-bottom: none; padding: .55rem 1rem; border-radius: 6px 6px 0 0;
}
.section-head h2 { font-size: .85rem; font-weight: 600; color: var(--accent2);
                   letter-spacing: .08em; text-transform: uppercase; }
.section-head .badge { margin-left: auto; font-size: .72rem; color: var(--dim); }

/* ── table ── */
table { width: 100%; border-collapse: collapse; background: var(--surface);
        border: 1px solid var(--border); border-radius: 0 0 6px 6px; overflow: hidden; }
thead { background: var(--surface2); }
th { padding: .45rem 1rem; text-align: left; font-size: .72rem; font-weight: 600;
     color: var(--dim); letter-spacing: .06em; text-transform: uppercase;
     border-bottom: 1px solid var(--border); white-space: nowrap; }
td { padding: .55rem 1rem; vertical-align: top; border-bottom: 1px solid var(--border); }
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--surface2); }

/* ── field columns ── */
td.field-name  { color: var(--key-col); font-weight: 600; white-space: nowrap; width: 220px; }
td.field-value { word-break: break-word; }
td.field-src   { color: var(--time-col); font-size: .72rem; white-space: nowrap; }
td.field-time  { color: var(--time-col); font-size: .72rem; white-space: nowrap; }

/* ── value types ── */
.num        { font-weight: 700; }
.score-high { color: var(--low);  }
.score-mid  { color: var(--med);  }
.score-low  { color: var(--high); }
.bool-true  { color: var(--low);  font-weight: 600; }
.bool-false { color: var(--high); font-weight: 600; }
.dim        { color: var(--dim);  }
.str        { color: var(--text); }
.list-wrap  { display: flex; flex-direction: column; gap: .3rem; }
.list-item  { background: var(--surface2); border: 1px solid var(--border);
              border-radius: 4px; padding: .3rem .6rem;
              display: flex; flex-wrap: wrap; gap: .3rem .8rem; }
.dict-wrap  { display: flex; flex-direction: column; gap: .2rem; }
.kv         { display: inline-flex; gap: .3rem; align-items: baseline; }
.kv-key     { color: var(--dim); font-size: .78rem; }
.kv-val     { color: var(--text); }
pre.raw     { color: var(--dim); font-size: .78rem; white-space: pre-wrap; }

/* ── "not found" row ── */
.missing-row td { color: var(--dim); font-style: italic; }

/* ── footer ── */
footer { margin-top: 2rem; color: var(--dim); font-size: .75rem;
         border-top: 1px solid var(--border); padding-top: .75rem; }

/* ── hidden by filter ── */
.hidden { display: none !important; }
"""

JS = """
const tabs   = document.querySelectorAll('.tab-btn');
const blocks = document.querySelectorAll('.section-block');
const search = document.getElementById('search');

function applyTab(target) {
  tabs.forEach(t => t.classList.toggle('active', t.dataset.target === target));
  blocks.forEach(b => {
    b.classList.toggle('hidden', target !== 'all' && b.dataset.section !== target);
  });
}

tabs.forEach(t => t.addEventListener('click', () => applyTab(t.dataset.target)));

search.addEventListener('input', () => {
  const q = search.value.toLowerCase();
  document.querySelectorAll('tbody tr').forEach(row => {
    row.classList.toggle('hidden', q !== '' && !row.textContent.toLowerCase().includes(q));
  });
});
"""


def _build_section_html(
    title: str,
    section_id: str,
    field_names: list[str],
    csv_rows: list[dict],
    db_lookup: dict[str, dict],   # key → {value_json, inserted_at, table}
) -> str:
    rows_html = []
    for field in field_names:
        db_row = db_lookup.get(field)
        if db_row:
            val_html  = _fmt_value(db_row["value_json"], field)
            src       = db_row.get("table", "—")
            timestamp = db_row.get("inserted_at", "—")
            tr_cls    = ""
        else:
            # Field defined in CSV but not yet present in the DB
            val_html  = "<span class='dim'>— not in DB —</span>"
            src       = "—"
            timestamp = "—"
            tr_cls    = " class='missing-row'"

        rows_html.append(
            f"<tr{tr_cls}>"
            f"<td class='field-name'>{_esc(field)}</td>"
            f"<td class='field-value'>{val_html}</td>"
            f"<td class='field-src'>{_esc(src)}</td>"
            f"<td class='field-time'>{_esc(timestamp)}</td>"
            f"</tr>"
        )

    found    = sum(1 for f in field_names if f in db_lookup)
    total    = len(field_names)
    badge    = f"{found} / {total} fields found in DB"

    return f"""
<div class="section-block" data-section="{section_id}">
  <div class="section-head">
    <h2>{_esc(title)}</h2>
    <span class="badge">{badge}</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>Field</th>
        <th>Value from DB</th>
        <th>Source Table</th>
        <th>Inserted At</th>
      </tr>
    </thead>
    <tbody>{"".join(rows_html)}</tbody>
  </table>
</div>"""


def build_html(
    db_path: Path,
    ind_lookup:  dict[str, dict],
    bb_lookup:   dict[str, dict],
    section: str,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    sections_html = ""
    if section in ("all", "indicators"):
        sections_html += _build_section_html(
            "Indicators", "indicators",
            INDICATOR_FIELDS, [], ind_lookup,
        )
    if section in ("all", "building_blocks"):
        sections_html += _build_section_html(
            "Building Blocks", "building_blocks",
            BUILDING_BLOCK_FIELDS, [], bb_lookup,
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fields Report — {_esc(db_path.name)}</title>
<style>{CSS}</style>
</head>
<body>

<div class="page-header">
  <h1>Fields Report</h1>
  <div class="meta">{_esc(str(db_path.resolve()))} &nbsp;·&nbsp; generated {now}</div>
</div>

<div class="section-nav">
  <button class="tab-btn {'active' if section == 'all' else ''}" data-target="all">All</button>
  <button class="tab-btn {'active' if section == 'indicators' else ''}" data-target="indicators">Indicators</button>
  <button class="tab-btn {'active' if section == 'building_blocks' else ''}" data-target="building_blocks">Building Blocks</button>
</div>

<input class="search-bar" id="search" type="search" placeholder="Search field name or value…">

{sections_html}

<footer>export_fields_html.py &nbsp;·&nbsp; source: {_esc(db_path.name)}</footer>
<script>{JS}</script>
</body>
</html>"""


# ── DB scan ───────────────────────────────────────────────────────────────────

def _build_lookup(
    conn: sqlite3.Connection,
    wanted_keys: set[str],
) -> dict[str, dict]:
    """Scan all tables for rows whose key is in wanted_keys. Returns key → row."""
    lookup: dict[str, dict] = {}
    for table in _get_tables(conn):
        rows = _fetch_latest(conn, table)
        for row in rows:
            if row["key"] in wanted_keys and row["key"] not in lookup:
                lookup[row["key"]] = {**row, "table": table}
    return lookup


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",      default="outputs/results.db")
    parser.add_argument("--csv-dir", default=None)
    parser.add_argument("--out",     default=None)
    parser.add_argument("--section", default="all",
                        choices=["all", "indicators", "building_blocks"])
    args = parser.parse_args(argv[1:])

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}")
        return 2

    csv_dir = Path(args.csv_dir) if args.csv_dir else Path(__file__).parent
    ind_csv = csv_dir / "04_indicators.csv"
    bb_csv  = csv_dir / "05_building_blocks.csv"
    for p in (ind_csv, bb_csv):
        if not p.exists():
            print(f"ERROR: CSV not found: {p}")
            return 2

    # Build the set of wanted keys from the CSV field lists
    # (field names themselves are the keys we look for in the DB)
    wanted_ind = set(INDICATOR_FIELDS)
    wanted_bb  = set(BUILDING_BLOCK_FIELDS)
    wanted_all = wanted_ind | wanted_bb

    conn = sqlite3.connect(str(db_path))
    all_lookup = _build_lookup(conn, wanted_all)
    conn.close()

    ind_lookup = {k: v for k, v in all_lookup.items() if k in wanted_ind}
    bb_lookup  = {k: v for k, v in all_lookup.items() if k in wanted_bb}

    html = build_html(db_path, ind_lookup, bb_lookup, args.section)

    ts  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else db_path.parent / f"fields_report_{ts}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    print(f"\n✓ HTML report written → {out.resolve()}")
    found = len(all_lookup)
    total = len(wanted_all)
    print(f"  {found} / {total} fields matched from DB\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
