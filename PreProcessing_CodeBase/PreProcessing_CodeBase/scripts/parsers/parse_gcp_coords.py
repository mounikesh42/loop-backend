#!/usr/bin/env python3
"""parse_gcp_coords.py - SRC_PP_GCP_COORDS parser (survey-level, per-GCP).

Reads the GCP coordinate file (CSV/TXT) and emits the 5 GCP source fields
(L1F_PP_008..012): the file-header CRS plus a per-GCP list carrying
id / position / sigma_h / sigma_v.

File shape (vendor-agnostic; the gold-standard generator writes this):
    # CRS: WGS84 / UTM zone 43N (EPSG:32643)   <- crs_in_coord_file_header (L1F_PP_012)
    # geoid: EGM2008                            \\
    # height_mode: orthometric                   } header metadata (parser_meta)
    # units: m                                   /
    # _status: PLACEHOLDER                      <- placeholder marker
    point_id,easting,northing,elevation,sigma_h,sigma_v
    GCP01,600080.000,2000080.000,540.700,0.008,0.014
    ...

Column names are matched flexibly (point_id|gcp_id|id; easting|e|x; northing|n|y;
elevation|elev|height|z; sigma_h|sig_h|sh; sigma_v|sig_v|sv). Positions are kept
in the file's own (projected) frame - the bbox-sanity / distribution checks at
Stage 3a compare them against reconstruction_extent_polygon (same frame).

The _read_coord_csv() core is reused verbatim by parse_cp_coords.py.
The parser raises NO spec flags (all PP flags fire at Stage 3a/3b/3c/3d).

parse(gcp_coords_file, project_root=None) -> {"fields", "parser_meta"}.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

PARSER_ID = "parse_gcp_coords"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_PP_GCP_COORDS"
SOURCE_FILE_NAME = "GCP Coordinate File"

_ID_ALIASES = ("point_id", "gcp_id", "cp_id", "id", "name")
_E_ALIASES = ("easting", "e", "x", "east")
_N_ALIASES = ("northing", "n", "y", "north")
_Z_ALIASES = ("elevation", "elev", "height", "z", "ortho_height")
_SH_ALIASES = ("sigma_h", "sig_h", "sh", "std_h", "sdh", "s_h")
_SV_ALIASES = ("sigma_v", "sig_v", "sv", "std_v", "sdv", "s_v")


def _to_float(s):
    try:
        return float(str(s).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _pick(header_map: dict, aliases) -> str | None:
    for a in aliases:
        if a in header_map:
            return header_map[a]
    return None


def _read_coord_csv(path: Path, prefix: str, notes: list) -> tuple[str | None, dict, list[dict]]:
    """Return (crs_header, header_metadata, rows). prefix is 'gcp'/'cp'.

    rows: list of {'id','position':{easting,northing,elevation},'sigma_h','sigma_v'}.
    Robust to comment lines, header aliases, blank/short rows.
    """
    header_meta: dict[str, Any] = {}
    crs_header = None
    data_lines: list[str] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                body = stripped.lstrip("#").strip()
                if ":" in body:
                    key, val = body.split(":", 1)
                    key = key.strip().lower(); val = val.strip()
                    header_meta[key] = val
                    if key == "crs":
                        crs_header = val
                continue
            data_lines.append(line)

    rows: list[dict] = []
    if not data_lines:
        notes.append(f"{path.name}: no data rows found.")
        return crs_header, header_meta, rows

    reader = csv.reader(data_lines)
    header = next(reader)
    header_map = {h.strip().lower(): h.strip().lower() for h in header}
    id_col = _pick(header_map, _ID_ALIASES)
    e_col = _pick(header_map, _E_ALIASES)
    n_col = _pick(header_map, _N_ALIASES)
    z_col = _pick(header_map, _Z_ALIASES)
    sh_col = _pick(header_map, _SH_ALIASES)
    sv_col = _pick(header_map, _SV_ALIASES)
    cols = [h.strip().lower() for h in header]
    idx = {c: i for i, c in enumerate(cols)}
    if id_col is None or e_col is None or n_col is None:
        notes.append(f"{path.name}: missing required column(s) "
                     f"(id={id_col}, easting={e_col}, northing={n_col}); header={cols}.")
    for ln, raw in enumerate(reader, 2):
        if not raw or all(not c.strip() for c in raw):
            continue
        def cell(col):
            return raw[idx[col]] if (col is not None and idx.get(col) is not None
                                     and idx[col] < len(raw)) else None
        rid = (cell(id_col) or "").strip() or f"{prefix.upper()}_ROW{ln}"
        rows.append({
            "id": rid,
            "position": {"easting": _to_float(cell(e_col)),
                         "northing": _to_float(cell(n_col)),
                         "elevation": _to_float(cell(z_col))},
            "sigma_h": _to_float(cell(sh_col)),
            "sigma_v": _to_float(cell(sv_col)),
        })
    return crs_header, header_meta, rows


def _aggregate(rows: list[dict]) -> dict:
    eas = [r["position"]["easting"] for r in rows if r["position"]["easting"] is not None]
    nor = [r["position"]["northing"] for r in rows if r["position"]["northing"] is not None]
    sh = [r["sigma_h"] for r in rows if r["sigma_h"] is not None]
    sv = [r["sigma_v"] for r in rows if r["sigma_v"] is not None]
    ids = [r["id"] for r in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    return {
        "count": len(rows),
        "duplicate_ids": dup,
        "sigma_h_min": round(min(sh), 6) if sh else None,
        "sigma_h_max": round(max(sh), 6) if sh else None,
        "sigma_v_max": round(max(sv), 6) if sv else None,
        "rows_missing_sigma_h": [r["id"] for r in rows if r["sigma_h"] is None],
        "bbox": ({"easting_min": min(eas), "easting_max": max(eas),
                  "northing_min": min(nor), "northing_max": max(nor)}
                 if eas and nor else None),
    }


def parse(gcp_coords_file, project_root: Path | None = None) -> dict[str, Any]:
    notes: list[str] = []
    path = Path(gcp_coords_file) if gcp_coords_file else None
    crs_header = None
    header_meta: dict = {}
    rows: list[dict] = []

    if path is None or not path.is_file():
        notes.append(f"GCP coord file absent ({gcp_coords_file}); 0 GCPs. GCT (0.25) + SD (0.10) "
                     "are CRITICAL (Stage 1 hard-fails).")
    else:
        try:
            crs_header, header_meta, rows = _read_coord_csv(path, "gcp", notes)
        except (OSError, csv.Error) as exc:
            notes.append(f"GCP coord file {path.name} unreadable ({exc}); treating as empty.")
        if header_meta.get("_status") == "PLACEHOLDER" or header_meta.get("status") == "PLACEHOLDER":
            notes.append(f"{path.name} is a PLACEHOLDER coord file (Section 8 lifecycle).")

    per_gcp = [{
        "L1F_PP_008_gcp_id": r["id"],
        "L1F_PP_009_gcp_position": r["position"],
        "L1F_PP_010_per_gcp_sigma_h": r["sigma_h"],
        "L1F_PP_011_per_gcp_sigma_v": r["sigma_v"],
    } for r in rows]

    agg = _aggregate(rows)
    fields = {
        "L1F_PP_012_crs_in_coord_file_header": crs_header,
        "per_gcp": per_gcp,
    }
    parser_meta = {
        "parser_id": PARSER_ID,
        "parser_version": PARSER_VERSION,
        "source_file_id": SOURCE_FILE_ID,
        "source_file_name": SOURCE_FILE_NAME,
        "instance_found": path is not None and path.is_file(),
        "gcp_count": agg["count"],
        "crs_in_coord_file_header": crs_header,
        "header_metadata": header_meta,
        "sigma_h_range": [agg["sigma_h_min"], agg["sigma_h_max"]],
        "sigma_v_max": agg["sigma_v_max"],
        "duplicate_ids": agg["duplicate_ids"],
        "rows_missing_sigma_h": agg["rows_missing_sigma_h"],
        "bbox": agg["bbox"],
        "fields_provided": ["L1F_PP_008_gcp_id", "L1F_PP_009_gcp_position",
                            "L1F_PP_010_per_gcp_sigma_h", "L1F_PP_011_per_gcp_sigma_v",
                            "L1F_PP_012_crs_in_coord_file_header"],
        "notes": notes,
        "flags_raised": [],
    }
    return {"fields": fields, "parser_meta": parser_meta}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Parse a pre-processing GCP coordinate file")
    parser.add_argument("gcp_coords_file")
    args = parser.parse_args(argv)
    out = parse(Path(args.gcp_coords_file), Path("."))
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
