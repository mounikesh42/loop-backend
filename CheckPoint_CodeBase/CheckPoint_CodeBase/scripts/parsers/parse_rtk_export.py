#!/usr/bin/env python3
"""parse_rtk_export.py - SRC_CP_RTK_EXPORT parser (per point / per occupation).

Emits L1F_CP_001..015 (15 source fields) for the per-point RTK device export.
This is the RTK analogue of the PPK chains' RINEX parser, but far simpler: the
device export already carries *computed* values (fix type, receiver sigma,
correction age, PDOP, sat count, CN0) - there is no observation body to stream
and no ephemeris to propagate.

Contract (identical to the GCP parsers so Stage 2 merge ports unchanged):
    parse(path, project_root=None)
      -> {"fields": {"<field_id>_<field_name>": value|None, ...},   # all keys present
          "parser_meta": {parser_id, parser_version, source_file_id,
                          source_file_name, instance_found, fields_provided,
                          field_sources, notes, flags_raised, + csv extras}}

Format dispatch by extension:
  .csv  -> implemented (CB_X / Emlid style flat export; one row per capture)
  .jxl / .pos / .gpx -> NOT yet implemented; all 15 fields kept None with
                        field_sources='format_unsupported' and
                        parser_meta.format_supported=False (never fabricated,
                        never crashes the merge).

Sigma may legitimately be absent (export configured without precision columns,
or a device that does not report it). The parser only records presence/absence
here (value None, field_sources 'absent'/'empty'); the "expected vs not"
judgement and graceful weight redistribution happen at Stage 3a/3b, which need
device_type from the FORM.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

PARSER_ID = "parse_rtk_export"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_CP_RTK_EXPORT"

# (field_id, field_name, export_column, coercion_kind)
# field_name == export_column for every scalar field in the CB_X CSV layout;
# captured_position_ecef is assembled from three columns (handled specially).
_FIELDS = [
    ("L1F_CP_001", "marker_name",                 "marker_name",                 "str"),
    ("L1F_CP_002", "captured_position_ecef",      None,                          "ecef"),
    ("L1F_CP_003", "position_sigma_horizontal_m", "position_sigma_horizontal_m", "float"),
    ("L1F_CP_004", "position_sigma_vertical_m",   "position_sigma_vertical_m",   "float"),
    ("L1F_CP_005", "fix_type_at_capture",         "fix_type_at_capture",         "fix_type"),
    ("L1F_CP_006", "correction_age_at_capture_sec", "correction_age_at_capture_sec", "float"),
    ("L1F_CP_007", "fix_hold_duration_sec",       "fix_hold_duration_sec",       "float"),
    ("L1F_CP_008", "pdop_at_capture",             "pdop_at_capture",             "float"),
    ("L1F_CP_009", "sat_count_at_capture",        "sat_count_at_capture",        "int"),
    ("L1F_CP_010", "cn0_mean_at_capture",         "cn0_mean_at_capture",         "float"),
    ("L1F_CP_011", "antenna_type",                "antenna_type",                "str"),
    ("L1F_CP_012", "device_id",                   "device_id",                   "str"),
    ("L1F_CP_013", "tilt_logged_deg",             "tilt_logged_deg",             "float"),
    ("L1F_CP_014", "capture_utc",                 "capture_utc",                 "str"),
    ("L1F_CP_015", "firmware_version",            "firmware_version",            "str"),
]
_KEY = {fid: f"{fid}_{fname}" for fid, fname, _c, _k in _FIELDS}
_ECEF_COLUMNS = ("ecef_x_m", "ecef_y_m", "ecef_z_m")
_FIX_TYPES = {"FIXED", "FLOAT", "AUTONOMOUS"}
_NULLISH = {"", "N/A", "NA", "NULL", "NONE"}


def _coerce(raw: Any, kind: str) -> tuple[Any, str, str]:
    """Return (value, source_tag, note)."""
    if raw is None:
        return None, "absent", "column absent from export"
    s = str(raw).strip()
    if s.upper() in _NULLISH:
        return None, "empty", "value empty / N/A in export"
    if kind == "str":
        return s, "rtk_export_csv", ""
    if kind == "fix_type":
        u = s.upper()
        if u not in _FIX_TYPES:
            return u, "rtk_export_csv", f"unexpected fix_type '{s}' (not in {sorted(_FIX_TYPES)})"
        return u, "rtk_export_csv", ""
    if kind == "float":
        try:
            return float(s), "rtk_export_csv", ""
        except ValueError:
            return None, "uncoercible", f"non-numeric float: '{s}'"
    if kind == "int":
        try:
            return int(float(s)), "rtk_export_csv", ""
        except ValueError:
            return None, "uncoercible", f"non-numeric int: '{s}'"
    return s, "rtk_export_csv", ""


def _parse_csv(path: Path, fields, field_sources, field_notes, notes):
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        notes.append("no data rows in export; all fields kept null")
        row: dict = {}
    else:
        row = rows[0]
        if len(rows) > 1:
            notes.append(f"{len(rows)} data rows present; using the first")
    columns_found = sorted(row.keys()) if row else []

    for fid, fname, col, kind in _FIELDS:
        key = _KEY[fid]
        if kind == "ecef":
            xyz, bad = {}, []
            for axis, ecol in zip(("x_m", "y_m", "z_m"), _ECEF_COLUMNS):
                v, _src, _n = _coerce(row.get(ecol), "float")
                if v is None:
                    bad.append(ecol)
                else:
                    xyz[axis] = v
            if bad:
                fields[key] = None
                field_sources[key] = "absent" if not row else "empty"
                field_notes[key] = f"missing/invalid ECEF component(s): {bad}"
            else:
                fields[key] = xyz
                field_sources[key] = "rtk_export_csv"
            continue
        value, src, note = _coerce(row.get(col), kind)
        fields[key] = value
        field_sources[key] = src
        if note:
            field_notes[key] = note

    expected_cols = set(_ECEF_COLUMNS) | {c for _f, _n, c, _k in _FIELDS if c}
    missing_cols = sorted(expected_cols - set(columns_found))
    return {
        "format": "csv",
        "format_supported": True,
        "row_count": len(rows),
        "columns_found": columns_found,
        "missing_columns": missing_cols,
    }


def parse(path: Path, project_root: Path | None = None) -> dict:
    path = Path(path)
    ext = path.suffix.lower()
    instance_found = path.exists()

    fields: dict[str, Any] = {_KEY[fid]: None for fid, *_ in _FIELDS}
    field_sources: dict[str, str] = {k: "absent" for k in fields}
    field_notes: dict[str, str] = {}
    notes: list[str] = []

    if not instance_found:
        notes.append(f"export file not found: {path}")
        fmt_meta = {"format": ext.lstrip("."), "format_supported": False}
    elif ext == ".csv":
        fmt_meta = _parse_csv(path, fields, field_sources, field_notes, notes)
    else:
        for k in fields:
            field_sources[k] = "format_unsupported"
        notes.append(f"only .csv is implemented in v1; '{ext}' returns honest-empty fields")
        fmt_meta = {"format": ext.lstrip("."), "format_supported": False}

    parser_meta = {
        "parser_id": PARSER_ID,
        "parser_version": PARSER_VERSION,
        "source_file_id": SOURCE_FILE_ID,
        "source_file_name": path.name if instance_found else None,
        "instance_found": instance_found,
        "fields_provided": sorted(fields.keys()),
        "field_sources": field_sources,
        "field_notes": field_notes,
        "notes": notes,
        "flags_raised": [],
        **fmt_meta,
    }
    return {"fields": dict(sorted(fields.items())), "parser_meta": parser_meta}


def main(argv=None) -> int:
    import argparse
    import json as _j
    ap = argparse.ArgumentParser(description="Parse a Check Point RTK device export")
    ap.add_argument("path")
    args = ap.parse_args(argv)
    print(_j.dumps(parse(Path(args.path)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
