#!/usr/bin/env python3
"""Stage 2 parser — User Input form (SRC_UI_01).

Reads inputs.user_input_file (default sample_data/user_input/form.json) and
emits L1F_UI_001..002. The spec defines exactly two fields here, both
operator-set intent for the planned mission:
  L1F_UI_001 planned_overlap_fwd_pct   (forward/along-track overlap %)
  L1F_UI_002 planned_overlap_lat_pct   (lateral/cross-track overlap %)

Hardware override (SRC_UI_02 → user_input/hardware.json) is a separate file
handled by parse_rinex; this parser is only for SRC_UI_01.
"""
import json
import sys
from pathlib import Path


REQUIRED_FIELDS = ["planned_overlap_fwd_pct", "planned_overlap_lat_pct"]


def parse(config: dict, project_root: Path) -> dict:
    form_path = project_root / config["inputs"]["user_input_file"]
    if not form_path.exists():
        raise FileNotFoundError(f"user input form not found: {form_path}")

    payload = json.loads(form_path.read_text())

    missing = [f for f in REQUIRED_FIELDS if f not in payload]
    if missing:
        raise ValueError(f"form.json missing required fields: {missing}")

    def _as_float(v):
        if v is None:
            return None
        return float(v)

    fields = {
        "L1F_UI_001": _as_float(payload.get("planned_overlap_fwd_pct")),
        "L1F_UI_002": _as_float(payload.get("planned_overlap_lat_pct")),
    }

    parser_meta = {
        "parser": "parse_user_input",
        "form_file": str(form_path),
        "fields_present": sorted(payload.keys()),
        "extra_fields_ignored": [k for k in payload if k not in REQUIRED_FIELDS and not k.startswith("_")],
    }

    return {
        "fields": fields,
        "parser_meta": parser_meta,
        "flags_raised": [],
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: parse_user_input.py <paths.json>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())

    result = parse(config, project_root)
    print(f"parse_user_input: {result['parser_meta']['form_file']}")
    print(f"  L1F_UI_001 planned_overlap_fwd_pct = {result['fields']['L1F_UI_001']}%")
    print(f"  L1F_UI_002 planned_overlap_lat_pct = {result['fields']['L1F_UI_002']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
