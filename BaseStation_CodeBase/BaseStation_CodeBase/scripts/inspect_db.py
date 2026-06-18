"""inspect_db.py — Pretty terminal + HTML report for demo SQLite tables.

Usage:
    python scripts/inspect_db.py [db_path] [table_filter]

Examples:
    python scripts/inspect_db.py
    python scripts/inspect_db.py ../../apicalls/pipeline.db
    python scripts/inspect_db.py ../../apicalls/pipeline.db base_station_stage3d_base_station_score

Outputs:
  - Rich terminal table (colour-coded, grouped by table)
  - outputs/report_<timestamp>.html  (open in any browser)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── ANSI helpers ──────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[96m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
RED    = "\033[91m"
MAGENTA= "\033[95m"
WHITE  = "\033[97m"
BG_DARK= "\033[48;5;235m"


def _sev_color(text: str) -> str:
    t = text.upper()
    if "HIGH" in t:   return f"{RED}{BOLD}{text}{RESET}"
    if "MEDIUM" in t: return f"{YELLOW}{text}{RESET}"
    if "LOW" in t:    return f"{GREEN}{text}{RESET}"
    return text


def _fmt_value_terminal(raw: str, key: str) -> str:
    """Return a human-readable terminal string for a JSON blob."""
    try:
        v = json.loads(raw)
    except Exception:
        return str(raw)

    # Scalar
    if isinstance(v, (int, float)):
        val = f"{v:.4f}" if isinstance(v, float) and v != int(v) else str(v)
        if key in ("base_station_score", "weighted_score_before_global_gate"):
            colour = GREEN if v == 100.0 else (YELLOW if v >= 60 else RED)
            return f"{colour}{BOLD}{val}{RESET}"
        return f"{CYAN}{val}{RESET}"

    if isinstance(v, str):
        return v

    if isinstance(v, bool):
        return f"{GREEN}true{RESET}" if v else f"{RED}false{RESET}"

    # List of flags / contributions
    if isinstance(v, list):
        if not v:
            return f"{DIM}(empty list){RESET}"
        lines = []
        for item in v:
            if isinstance(item, dict):
                parts = []
                for k2, v2 in item.items():
                    if k2.startswith("_"):
                        continue
                    label = f"{DIM}{k2}{RESET}"
                    val2  = str(v2)
                    if k2 == "severity":
                        val2 = _sev_color(val2)
                    elif k2 == "block_score":
                        colour = GREEN if v2 == 100.0 else (YELLOW if v2 >= 60 else RED)
                        val2 = f"{colour}{v2}{RESET}"
                    parts.append(f"{label}={val2}")
                lines.append("  " + "  ".join(parts))
            else:
                lines.append(f"  {item}")
        return "\n".join(lines)

    # Dict
    if isinstance(v, dict):
        lines = []
        for k2, v2 in v.items():
            label = f"{DIM}{k2}{RESET}"
            val2 = json.dumps(v2) if isinstance(v2, (dict, list)) else str(v2)
            if k2 == "triggered":
                val2 = f"{RED}YES{RESET}" if v2 else f"{GREEN}NO{RESET}"
            elif k2 == "ok":
                val2 = f"{GREEN}✓{RESET}" if v2 else f"{RED}✗{RESET}"
            lines.append(f"  {label}: {val2}")
        return "\n".join(lines)

    return json.dumps(v, indent=2)


# ── Terminal renderer ─────────────────────────────────────────────────────────

COL_KEY   = 40
COL_VALUE = 80
COL_TIME  = 22


def _rule(char="─", width=COL_KEY + COL_VALUE + COL_TIME + 6) -> str:
    return f"{DIM}{char * width}{RESET}"


def render_terminal(tables: dict[str, list[dict]]) -> None:
    print()
    print(f"{BOLD}{CYAN}{'BASE STATION DB INSPECTOR':^{COL_KEY+COL_VALUE+COL_TIME+6}}{RESET}")
    print(_rule("═"))

    for table_name, rows in tables.items():
        label = table_name.replace("demo_", "").replace("_", " ").upper()
        print()
        print(f"{BG_DARK}{BOLD}{MAGENTA} ▶  {label} {RESET}  "
              f"{DIM}({len(rows)} row{'s' if len(rows)!=1 else ''}){RESET}")
        print(_rule())

        # header
        print(f"{BOLD}{WHITE}"
              f"{'KEY':<{COL_KEY}}  {'VALUE':<{COL_VALUE}}  {'INSERTED AT':<{COL_TIME}}"
              f"{RESET}")
        print(_rule())

        for row in rows:
            key       = row["key"]
            inserted  = row["inserted_at"]
            value_str = _fmt_value_terminal(row["value_json"], key)
            value_lines = value_str.split("\n")

            first = True
            for line in value_lines:
                if first:
                    print(f"{YELLOW}{key:<{COL_KEY}}{RESET}  {line:<{COL_VALUE}}  {DIM}{inserted}{RESET}")
                    first = False
                else:
                    print(f"{' ':<{COL_KEY}}  {line}")

            print(_rule("┄"))

    print()
    print(f"{DIM}Tip: pass a table name as second arg to filter, e.g.:{RESET}")
    print(f"  python scripts/inspect_db.py ../../apicalls/pipeline.db base_station_stage3d_base_station_score")
    print()


# ── HTML renderer ─────────────────────────────────────────────────────────────

def _fmt_value_html(raw: str, key: str) -> str:
    try:
        v = json.loads(raw)
    except Exception:
        return f"<span class='str'>{raw}</span>"

    if isinstance(v, (int, float)):
        cls = "score-high" if v == 100.0 else ("score-mid" if v >= 60 else "score-low")
        label = f"{v:.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
        return f"<span class='num {cls}'>{label}</span>"

    if isinstance(v, str):
        return f"<span class='str'>{v}</span>"

    if isinstance(v, bool):
        return f"<span class='bool-{'true' if v else 'false'}'>{str(v).lower()}</span>"

    if isinstance(v, list):
        if not v:
            return "<span class='dim'>— empty —</span>"
        items_html = []
        for item in v:
            if isinstance(item, dict):
                pairs = []
                for k2, v2 in item.items():
                    if k2.startswith("_"):
                        continue
                    val_html = str(v2)
                    if k2 == "severity":
                        sev = str(v2).upper()
                        cls = "sev-high" if "HIGH" in sev else ("sev-med" if "MEDIUM" in sev else "sev-low")
                        val_html = f"<span class='{cls}'>{v2}</span>"
                    elif k2 in ("block_score", "base_station_score"):
                        fv = float(v2)
                        cls = "score-high" if fv == 100.0 else ("score-mid" if fv >= 60 else "score-low")
                        val_html = f"<span class='num {cls}'>{v2}</span>"
                    elif k2 == "triggered":
                        val_html = f"<span class='bool-{'false' if not v2 else 'true'}'>{v2}</span>"
                    pairs.append(f"<span class='kv'><span class='kv-key'>{k2}</span><span class='kv-val'>{val_html}</span></span>")
                items_html.append("<div class='list-item'>" + "".join(pairs) + "</div>")
            else:
                items_html.append(f"<div class='list-item'>{item}</div>")
        return "<div class='list-wrap'>" + "".join(items_html) + "</div>"

    if isinstance(v, dict):
        pairs = []
        for k2, v2 in v.items():
            val_html = json.dumps(v2) if isinstance(v2, (dict, list)) else str(v2)
            if k2 == "triggered":
                val_html = f"<span class='bool-{'false' if not v2 else 'true'}'>{v2}</span>"
            elif k2 == "ok":
                val_html = f"<span class='bool-{'true' if v2 else 'false'}'>{'✓ pass' if v2 else '✗ fail'}</span>"
            pairs.append(f"<span class='kv'><span class='kv-key'>{k2}</span><span class='kv-val'>{val_html}</span></span>")
        return "<div class='dict-wrap'>" + "".join(pairs) + "</div>"

    return f"<pre class='raw'>{json.dumps(v, indent=2)}</pre>"


HTML_CSS = """
:root {
  --bg:        #0d1117;
  --surface:   #161b22;
  --surface2:  #1c2330;
  --border:    #30363d;
  --accent:    #58a6ff;
  --accent2:   #3fb950;
  --text:      #e6edf3;
  --dim:       #8b949e;
  --key-col:   #79c0ff;
  --time-col:  #6e7681;
  --high:      #f85149;
  --med:       #e3b341;
  --low:       #3fb950;
  --score-h:   #3fb950;
  --score-m:   #e3b341;
  --score-l:   #f85149;
  font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', ui-monospace, monospace;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body   { background: var(--bg); color: var(--text); padding: 2rem; font-size: 13px; line-height: 1.5; }
h1     { font-size: 1.4rem; font-weight: 700; color: var(--accent); letter-spacing: .04em;
         border-bottom: 1px solid var(--border); padding-bottom: .75rem; margin-bottom: 1.5rem; }
h1 small { font-size: .75rem; color: var(--dim); font-weight: 400; display: block; margin-top: .2rem; }
.table-block { margin-bottom: 2rem; }
.table-header { display: flex; align-items: center; gap: .6rem;
                background: var(--surface2); border: 1px solid var(--border);
                border-bottom: none; padding: .5rem 1rem; border-radius: 6px 6px 0 0; }
.table-header h2 { font-size: .85rem; font-weight: 600; color: var(--accent2);
                   letter-spacing: .08em; text-transform: uppercase; }
.table-header .row-count { font-size: .75rem; color: var(--dim); margin-left: auto; }
table  { width: 100%; border-collapse: collapse; background: var(--surface);
         border: 1px solid var(--border); border-radius: 0 0 6px 6px; overflow: hidden; }
thead  { background: var(--surface2); }
th     { padding: .5rem 1rem; text-align: left; font-size: .72rem; font-weight: 600;
         color: var(--dim); letter-spacing: .06em; text-transform: uppercase;
         border-bottom: 1px solid var(--border); }
td     { padding: .55rem 1rem; vertical-align: top; border-bottom: 1px solid var(--border); }
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--surface2); }
td.key-cell { color: var(--key-col); font-weight: 600; white-space: nowrap; }
td.time-cell { color: var(--time-col); font-size: .72rem; white-space: nowrap; }
.num   { font-weight: 700; }
.score-high { color: var(--score-h); }
.score-mid  { color: var(--score-m); }
.score-low  { color: var(--score-l); }
.sev-high { color: var(--high); font-weight: 700; }
.sev-med  { color: var(--med); font-weight: 600; }
.sev-low  { color: var(--low); }
.bool-true  { color: var(--low); font-weight: 600; }
.bool-false { color: var(--high); font-weight: 600; }
.dim   { color: var(--dim); }
.str   { color: var(--text); }
.list-wrap { display: flex; flex-direction: column; gap: .4rem; }
.list-item { background: var(--surface2); border: 1px solid var(--border);
             border-radius: 4px; padding: .4rem .6rem;
             display: flex; flex-wrap: wrap; gap: .3rem .8rem; }
.dict-wrap { display: flex; flex-direction: column; gap: .2rem; }
.kv        { display: inline-flex; gap: .3rem; align-items: baseline; }
.kv-key    { color: var(--dim); font-size: .78rem; }
.kv-val    { color: var(--text); }
pre.raw    { color: var(--dim); font-size: .78rem; white-space: pre-wrap; }
footer     { margin-top: 2rem; color: var(--dim); font-size: .75rem;
             border-top: 1px solid var(--border); padding-top: .75rem; }
"""


def render_html(tables: dict[str, list[dict]], db_path: Path) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    blocks = []
    for table_name, rows in tables.items():
        label = table_name.replace("demo_", "").replace("_", " ").upper()
        rows_html = []
        for row in rows:
            key      = row["key"]
            inserted = row["inserted_at"]
            val_html = _fmt_value_html(row["value_json"], key)
            rows_html.append(
                f"<tr>"
                f"<td class='key-cell'>{key}</td>"
                f"<td>{val_html}</td>"
                f"<td class='time-cell'>{inserted}</td>"
                f"</tr>"
            )
        blocks.append(f"""
<div class="table-block">
  <div class="table-header">
    <h2>{label}</h2>
    <span class="row-count">{len(rows)} row{'s' if len(rows)!=1 else ''}</span>
  </div>
  <table>
    <thead><tr><th>Key</th><th>Value</th><th>Inserted At</th></tr></thead>
    <tbody>{"".join(rows_html)}</tbody>
  </table>
</div>""")

    body = "\n".join(blocks)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DB Inspector — {db_path.name}</title>
<style>{HTML_CSS}</style>
</head>
<body>
<h1>DB Inspector
  <small>{db_path.resolve()} &nbsp;·&nbsp; generated {now}</small>
</h1>
{body}
<footer>inspect_db.py &nbsp;·&nbsp; {len(tables)} table(s) shown</footer>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def _load_tables(db_path: Path, table_filter: str | None) -> dict[str, list[dict]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    all_tables = [r["name"] for r in cur.fetchall()]

    if table_filter:
        all_tables = [t for t in all_tables if table_filter in t]

    result: dict[str, list[dict]] = {}
    for t in all_tables:
        # Only show the latest batch (by max inserted_at) to avoid duplicates
        # from repeated pipeline runs stored in the same table.
        cur.execute(f"""
            SELECT key, value_json, inserted_at
            FROM {t}
            WHERE inserted_at = (SELECT MAX(inserted_at) FROM {t})
            ORDER BY id
        """)
        rows = [dict(r) for r in cur.fetchall()]
        if rows:
            result[t] = rows

    conn.close()
    return result


def main(argv: list[str]) -> int:
    # Locate DB
    if len(argv) > 1 and Path(argv[1]).suffix == ".db":
        db_path = Path(argv[1])
    else:
        db_path = Path("../../apicalls/pipeline.db")
        if not db_path.exists():
            candidates = list(Path(".").rglob("pipeline.db"))
            if candidates:
                db_path = candidates[0]

    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        return 2

    table_filter = argv[2] if len(argv) > 2 else (argv[1] if len(argv) > 1 and ".db" not in argv[1] else None)

    tables = _load_tables(db_path, table_filter)
    if not tables:
        print("No tables found (or none matched the filter).")
        return 0

    # ── Terminal ──
    render_terminal(tables)

    # ── HTML ──
    html = render_html(tables, db_path)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = db_path.parent
    html_path = out_dir / f"report_{ts}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"{GREEN}HTML report:{RESET} {html_path.resolve()}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
