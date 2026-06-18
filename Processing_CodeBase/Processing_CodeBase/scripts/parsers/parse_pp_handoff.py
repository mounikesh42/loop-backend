#!/usr/bin/env python3
"""parse_pp_handoff.py - SRC_PROC_PP_HANDOFF parser (cross-source reference).

Emits the 3 pre-processing handoff source fields (L1F_PROC_088..090):
  - pp_gcp_coord_file_gcp_positions  : {marker_id: {lon,lat,elev}} from the
                                       pre-processing GCP coord file (CV5 typo check)
  - pp_manifest_capture_crs          : capture CRS declared in pp manifest (DO3)
  - pp_manifest_capture_geoid        : capture geoid declared in pp manifest (DO3)

RUNTIME INDEPENDENCE: this reads pre-processing SOURCE ARTIFACTS (the GCP coord
file + pp manifest), NOT pre_processing_score. So processing_score never depends
on a sibling chain's computed output - only on shared source data.

Absence handling: OPTIONAL source. When absent, all 3 fields are null and the
three cross-source indicators (CV4 role consistency is manifest-based; CV5 gcp
typo, DO3 internal transform) degrade to N/A and redistribute at Stage 3b/3c.
Raises NO flags here.

parse(pp_handoff_map, project_root) -> {"fields", "parser_meta"}.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

PARSER_ID = "parse_pp_handoff"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_PROC_PP_HANDOFF"

_ID_ALIASES = ("marker_id", "point_id", "gcp_id", "id", "name")
_LON_ALIASES = ("lon", "longitude", "x", "easting", "east")
_LAT_ALIASES = ("lat", "latitude", "y", "northing", "north")
_Z_ALIASES = ("elev_m", "elevation", "elev", "height", "z", "alt")


def _to_float(s):
    try:
        return float(str(s).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _pick(cols, aliases):
    for a in aliases:
        if a in cols:
            return a
    return None


def _read_gcp_csv(path: Path, notes: list):
    """Return ({marker_id: {lon,lat,elev}}, header_meta). Skips comment lines,
    matches column aliases, robust to blanks."""
    header_meta: dict[str, Any] = {}
    data_lines: list[str] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                body = s.lstrip("#").strip()
                if ":" in body:
                    k, v = body.split(":", 1)
                    header_meta[k.strip().lower()] = v.strip()
                elif "_status" in body.upper() and "PLACEHOLDER" in body.upper():
                    header_meta["_status"] = "PLACEHOLDER"
                continue
            data_lines.append(line)

    positions: dict[str, dict] = {}
    if not data_lines:
        notes.append(f"{path.name}: no data rows.")
        return positions, header_meta
    reader = csv.reader(data_lines)
    header = [h.strip().lower() for h in next(reader)]
    idx = {c: i for i, c in enumerate(header)}
    id_c = _pick(header, _ID_ALIASES)
    lon_c = _pick(header, _LON_ALIASES)
    lat_c = _pick(header, _LAT_ALIASES)
    z_c = _pick(header, _Z_ALIASES)
    if id_c is None or lon_c is None or lat_c is None:
        notes.append(f"{path.name}: missing required column(s) (id={id_c}, lon={lon_c}, lat={lat_c}).")
        return positions, header_meta
    for ln, raw in enumerate(reader, 2):
        if not raw or all(not c.strip() for c in raw):
            continue
        def cell(c):
            return raw[idx[c]] if (c is not None and idx.get(c) is not None and idx[c] < len(raw)) else None
        mid = (cell(id_c) or "").strip() or f"ROW{ln}"
        positions[mid] = {"lon": _to_float(cell(lon_c)), "lat": _to_float(cell(lat_c)),
                          "elev": _to_float(cell(z_c))}
    return positions, header_meta


def _empty():
    return {"pp_gcp_coord_file_gcp_positions": None, "pp_manifest_capture_crs": None,
            "pp_manifest_capture_geoid": None}


def parse(pp_handoff_map: dict, project_root: Path | None = None) -> dict[str, Any]:
    root = Path(project_root) if project_root else Path(".")
    notes: list[str] = []
    fields = _empty()
    pp_handoff_map = pp_handoff_map or {}
    placeholder = False

    # --- GCP coord file ---
    gcp_rel = pp_handoff_map.get("gcp_coord_file")
    gcp_path = (root / gcp_rel) if gcp_rel else None
    gcp_found = bool(gcp_path and gcp_path.is_file())
    n_markers = 0
    if gcp_found:
        try:
            positions, hmeta = _read_gcp_csv(gcp_path, notes)
            if positions:
                fields["pp_gcp_coord_file_gcp_positions"] = positions
                n_markers = len(positions)
            if hmeta.get("_status") == "PLACEHOLDER" or hmeta.get("status") == "PLACEHOLDER":
                placeholder = True
                notes.append(f"{gcp_path.name} is a PLACEHOLDER coord file (Section 8 lifecycle).")
        except (OSError, csv.Error) as exc:
            notes.append(f"{gcp_path.name} unreadable ({exc}); positions null.")
    else:
        notes.append("pp GCP coord file absent; CV5 gcp_coord_consistency degrades to N/A.")

    # --- pp manifest (capture CRS / geoid) ---
    man_rel = pp_handoff_map.get("pp_manifest_file")
    man_path = (root / man_rel) if man_rel else None
    man_found = bool(man_path and man_path.is_file())
    if man_found:
        try:
            with man_path.open(encoding="utf-8") as fh:
                doc = json.load(fh)
            if isinstance(doc, dict):
                fields["pp_manifest_capture_crs"] = doc.get("pp_manifest_capture_crs")
                fields["pp_manifest_capture_geoid"] = doc.get("pp_manifest_capture_geoid")
                if doc.get("_status") == "PLACEHOLDER":
                    placeholder = True
                    notes.append(f"{man_path.name} is a PLACEHOLDER (Section 8 lifecycle).")
            else:
                notes.append(f"{man_path.name} root is not an object; capture CRS/geoid null.")
        except (OSError, json.JSONDecodeError) as exc:
            notes.append(f"{man_path.name} unreadable ({exc}); capture CRS/geoid null.")
    else:
        notes.append("pp manifest absent; DO3 internal_transform_consistency degrades to N/A.")

    parser_meta = {
        "parser_id": PARSER_ID,
        "parser_version": PARSER_VERSION,
        "source_file_id": SOURCE_FILE_ID,
        "instance_found": gcp_found or man_found,
        "gcp_coord_file_found": gcp_found,
        "pp_manifest_found": man_found,
        "marker_position_count": n_markers,
        "is_placeholder": placeholder,
        "non_null_count": sum(1 for v in fields.values() if v is not None),
        "notes": notes,
        "flags_raised": [],
    }
    return {"fields": dict(sorted(fields.items())), "parser_meta": parser_meta}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Parse pre-processing handoff data")
    ap.add_argument("config", help="path to paths.json")
    args = ap.parse_args(argv)
    config_path = Path(args.config).resolve()
    with config_path.open() as fh:
        config = json.load(fh)
    out = parse(config["inputs"].get("pp_handoff", {}), config_path.parent)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
