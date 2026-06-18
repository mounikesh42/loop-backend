#!/usr/bin/env python3
"""parse_cp_coords.py - SRC_PP_CP_COORDS parser (survey-level, per-CP).

Reads the check-point coordinate file (CSV/TXT) and emits the 4 CP source fields
(L1F_PP_013..016): a per-CP list carrying id / position / sigma_h / sigma_v.
Unlike the GCP file, the CP source has NO header-CRS field (the CP artifact's CRS
is taken from the manifest's declared_crs_per_artifact['cp']); the file-header CRS
is kept as bonus context in parser_meta.

CP is the one OPTIONAL artifact: when absent (no CPs designated), the parser
returns 0 CPs and that drives, downstream:
    verification_status = UNVERIFIED_NO_CPS
    cp_coord_artifact_score = null
    pre_processing_score    = UNAFFECTED (CP never gates / contributes to the apex)

Shares the CSV core with the GCP parser (_read_coord_csv / _aggregate).
The parser raises NO spec flags (all PP flags fire at Stage 3a/3b/3c/3d).

parse(cp_coords_file, project_root=None) -> {"fields", "parser_meta"}.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_gcp_coords import _read_coord_csv, _aggregate  # noqa: E402

PARSER_ID = "parse_cp_coords"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_PP_CP_COORDS"
SOURCE_FILE_NAME = "Check-Point Coordinate File"


def parse(cp_coords_file, project_root: Path | None = None) -> dict[str, Any]:
    notes: list[str] = []
    path = Path(cp_coords_file) if cp_coords_file else None
    crs_header = None
    header_meta: dict = {}
    rows: list[dict] = []
    absent = path is None or not path.is_file()

    if absent:
        notes.append(
            f"No CP coord file ({cp_coords_file}) -> 0 check points. This is a spec-defined "
            "OPTIONAL state, NOT an error: verification_status=UNVERIFIED_NO_CPS, "
            "cp_coord_artifact_score=null, pre_processing_score UNAFFECTED (CP never gates "
            "or contributes to the apex).")
    else:
        try:
            crs_header, header_meta, rows = _read_coord_csv(path, "cp", notes)
        except (OSError, csv.Error) as exc:
            notes.append(f"CP coord file {path.name} unreadable ({exc}); treating as 0 CPs.")
        if header_meta.get("_status") == "PLACEHOLDER" or header_meta.get("status") == "PLACEHOLDER":
            notes.append(f"{path.name} is a PLACEHOLDER coord file (Section 8 lifecycle).")

    per_cp = [{
        "L1F_PP_013_cp_id": r["id"],
        "L1F_PP_014_cp_position": r["position"],
        "L1F_PP_015_per_cp_sigma_h": r["sigma_h"],
        "L1F_PP_016_per_cp_sigma_v": r["sigma_v"],
    } for r in rows]

    agg = _aggregate(rows)
    fields = {"per_cp": per_cp}
    parser_meta = {
        "parser_id": PARSER_ID,
        "parser_version": PARSER_VERSION,
        "source_file_id": SOURCE_FILE_ID,
        "source_file_name": SOURCE_FILE_NAME,
        "instance_found": not absent,
        "cp_count": agg["count"],
        "no_check_points": agg["count"] == 0,
        "crs_in_coord_file_header_bonus": crs_header,
        "header_metadata": header_meta,
        "sigma_h_range": [agg["sigma_h_min"], agg["sigma_h_max"]],
        "sigma_v_max": agg["sigma_v_max"],
        "duplicate_ids": agg["duplicate_ids"],
        "rows_missing_sigma_h": agg["rows_missing_sigma_h"],
        "bbox": agg["bbox"],
        "fields_provided": ["L1F_PP_013_cp_id", "L1F_PP_014_cp_position",
                            "L1F_PP_015_per_cp_sigma_h", "L1F_PP_016_per_cp_sigma_v"],
        "notes": notes,
        "flags_raised": [],
    }
    return {"fields": fields, "parser_meta": parser_meta}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Parse a pre-processing CP coordinate file")
    parser.add_argument("cp_coords_file")
    args = parser.parse_args(argv)
    out = parse(Path(args.cp_coords_file), Path("."))
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
