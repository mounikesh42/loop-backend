#!/usr/bin/env python3
"""Stage 2 - survey-level source-field assembly for Pre-Processing.

Consumes the Stage 1 inventory and runs the 5 parsers ONCE (this subsystem
scores one survey, not N occupations), merging their L1F fields into a single
source_fields structure:

  SRC_PP_GEOTAGS     L1F_PP_001..007 (7)   parse_geotags     scalar count + per_image[]
  SRC_PP_GCP_COORDS  L1F_PP_008..012 (5)   parse_gcp_coords  header CRS + per_gcp[]
  SRC_PP_CP_COORDS   L1F_PP_013..016 (4)   parse_cp_coords   per_cp[] (optional)
  SRC_PP_MANIFEST    L1F_PP_017..056 (40)  parse_manifest    flat
  SRC_PP_REPORT      L1F_PP_057..062 (6)   parse_report      flat (optional)
                                     = 62 source fields total

Every L1F field is owned by exactly one source (disjoint id ranges); no
cross-parser L1F computation happens here. The cross-SOURCE consistency checks
the spec defines (crs_match_project, geotag completeness vs captured count,
sensor_metadata_consistent, coord-vs-polygon bbox) are L2D derived fields
evaluated at Stage 3a - merge only surfaces advisory previews.

Per the spec flag table, no flag has raised_at_stage=pre_score_ingestion, so
_flags_raised_stage2 is empty by design. Pre-processing is runtime-independent
(it does NOT read sibling score outputs), so _handoff_crossdoc_candidates is
empty by design - the slot is kept for a future capture_score per template 7e.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import stage1_inventory  # noqa: E402

STAGE = "stage2_merge"


def _import_parsers():
    parsers_dir = Path(__file__).resolve().parent / "parsers"
    if str(parsers_dir) not in sys.path:
        sys.path.insert(0, str(parsers_dir))
    import parse_manifest      # type: ignore
    import parse_geotags       # type: ignore
    import parse_gcp_coords    # type: ignore
    import parse_cp_coords     # type: ignore
    import parse_report        # type: ignore
    return {
        "SRC_PP_MANIFEST": parse_manifest,
        "SRC_PP_GEOTAGS": parse_geotags,
        "SRC_PP_GCP_COORDS": parse_gcp_coords,
        "SRC_PP_CP_COORDS": parse_cp_coords,
        "SRC_PP_REPORT": parse_report,
    }


def _canonical_keys_by_source(spec: dict[str, Any]) -> dict[str, list[str]]:
    """Map each source file_id to its L1F keys ('<field_id>_<field_name>')."""
    out: dict[str, list[str]] = {}
    for sf in spec["source_fields"]:
        out.setdefault(sf["file_id"], []).append(f"{sf['field_id']}_{sf['field_name']}")
    return out


def _run_parsers(config, root) -> dict[str, dict]:
    p = _import_parsers()
    inp = config["inputs"]

    def _path(key):
        rel = inp.get(key)
        return (root / rel) if rel else None

    return {
        "SRC_PP_MANIFEST": p["SRC_PP_MANIFEST"].parse(_path("manifest_file"), root),
        "SRC_PP_GEOTAGS": p["SRC_PP_GEOTAGS"].parse(
            _path("geotags_dir"), root,
            image_extensions=inp.get("geotag_image_extensions"),
            sidecar_extensions=inp.get("geotag_sidecar_extensions")),
        "SRC_PP_GCP_COORDS": p["SRC_PP_GCP_COORDS"].parse(_path("gcp_coords_file"), root),
        "SRC_PP_CP_COORDS": p["SRC_PP_CP_COORDS"].parse(_path("cp_coords_file"), root),
        "SRC_PP_REPORT": p["SRC_PP_REPORT"].parse(_path("report_file"), root),
    }


def _scalar_l1f_nonnull(result: dict) -> int:
    return sum(1 for k, v in result["fields"].items()
              if k.startswith("L1F_PP_") and v is not None)


def _record_count(result: dict) -> int | None:
    for rk in ("per_image", "per_gcp", "per_cp"):
        if rk in result["fields"]:
            return len(result["fields"][rk])
    return None


def run(config, project_root: Path, spec: dict, inventory: dict) -> dict[str, Any]:
    root = project_root
    keys_by_source = _canonical_keys_by_source(spec)
    expected_total = sum(len(v) for v in keys_by_source.values())
    results = _run_parsers(config, root)

    # ---- merge field dicts (disjoint id ranges; overlap is a bug) ----
    source_fields: dict[str, Any] = {}
    field_owner: dict[str, str] = {}
    overlaps: list[dict] = []
    survey_warnings: list[dict] = []
    for src_id in ("SRC_PP_MANIFEST", "SRC_PP_GEOTAGS", "SRC_PP_GCP_COORDS",
                   "SRC_PP_CP_COORDS", "SRC_PP_REPORT"):
        for k, v in results[src_id]["fields"].items():
            if k in source_fields:
                overlaps.append({"key": k, "first_owner": field_owner[k], "second_owner": src_id})
            else:
                source_fields[k] = v
                field_owner[k] = src_id
    if overlaps:
        survey_warnings.append({"code": "L1F_FIELD_OWNERSHIP_OVERLAP", "detail": overlaps})

    # ---- per-source spec audit (fields_provided vs spec keys) ----
    per_source_audit: dict[str, Any] = {}
    produced_l1f_total = 0
    for src_id, result in results.items():
        expected = set(keys_by_source.get(src_id, []))
        produced = set(result["parser_meta"].get("fields_provided", []))
        produced_l1f_total += len(produced)
        missing = sorted(expected - produced)
        extra = sorted(produced - expected)
        per_source_audit[src_id] = {
            "expected_count": len(expected),
            "produced_count": len(produced),
            "missing_keys": missing,
            "extra_keys": extra,
            "instance_found": result["parser_meta"].get("instance_found"),
            "record_count": _record_count(result),
            "scalar_l1f_non_null": _scalar_l1f_nonnull(result),
        }
        if missing:
            survey_warnings.append({"code": "L1F_FIELDS_MISSING_FROM_PARSER",
                                    "source_file_id": src_id, "missing": missing})
        if extra:
            survey_warnings.append({"code": "L1F_UNEXPECTED_FIELDS_FROM_PARSER",
                                    "source_file_id": src_id, "extra": extra})
    if produced_l1f_total != expected_total:
        survey_warnings.append({"code": "L1F_TOTAL_FIELD_COUNT_MISMATCH",
                                "expected": expected_total, "produced": produced_l1f_total})

    # ---- advisory cross-source previews (authoritative checks are Stage 3a) ----
    man = results["SRC_PP_MANIFEST"]["fields"]
    geo_pm = results["SRC_PP_GEOTAGS"]["parser_meta"]
    gcp_pm = results["SRC_PP_GCP_COORDS"]["parser_meta"]
    cp_pm = results["SRC_PP_CP_COORDS"]["parser_meta"]
    geotag_count = results["SRC_PP_GEOTAGS"]["fields"].get("L1F_PP_004_geotag_count")
    captured = man.get("L1F_PP_039_captured_image_count")
    completeness_preview = (round(geotag_count / captured, 4)
                            if geotag_count and captured else None)
    crs_spellings = {
        "manifest_declared_crs": man.get("L1F_PP_023_declared_crs_per_artifact"),
        "geotag_exif_crs": geo_pm.get("distinct_crs_in_exif"),
        "gcp_header_crs": gcp_pm.get("crs_in_coord_file_header"),
    }
    cross_source_previews = {
        "geotag_completeness_preview": {
            "geotag_count": geotag_count, "captured_image_count": captured,
            "fraction": completeness_preview},
        "sensor_serials_in_exif": geo_pm.get("distinct_camera_serials"),
        "gcp_count": gcp_pm.get("gcp_count"),
        "cp_count": cp_pm.get("cp_count"),
        "no_check_points": cp_pm.get("no_check_points"),
        "report_present": results["SRC_PP_REPORT"]["parser_meta"].get("report_present"),
        "crs_spellings_observed": crs_spellings,
        "_note": ("Advisory only. crs_match_project, geotag completeness, "
                  "sensor_metadata_consistent and bbox-sanity are derived at Stage 3a; "
                  "crs_spellings_observed flags the WGS84/WGS-84/combined-string "
                  "normalisation the Stage-3a CRS comparator must handle."),
    }

    # ---- flag aggregation (empty by design) ----
    flags_raised_stage2: list[dict] = []
    for src_id, result in results.items():
        for flag in result["parser_meta"].get("flags_raised", []):
            flags_raised_stage2.append({**flag, "_origin_source": src_id})

    merge_notes = [
        "Survey-level: the 5 parsers run ONCE (not per-occupation). 62 source fields: "
        "GEOTAGS 7, GCP_COORDS 5, CP_COORDS 4, MANIFEST 40, REPORT 6.",
        "source_fields collapses the per-record sources: geotags -> geotag_count + per_image[]; "
        "gcp -> crs_in_coord_file_header + per_gcp[]; cp -> per_cp[]. The 62 is the LOGICAL "
        "L1F count (audited via each parser's fields_provided), not the top-level key count.",
        "Every L1F field is owned by exactly one source (disjoint id ranges); no cross-parser "
        "L1F computation at merge.",
        "Cross-SOURCE consistency (crs_match_project, geotag completeness vs captured count, "
        "sensor_metadata_consistent, coord bbox-vs-polygon) is derived at Stage 3a, not merge.",
        "CP and REPORT are OPTIONAL: cp absent -> verification_status path (score unaffected); "
        "report absent -> report-tier indicators advisory/redistribute.",
        "No spec flag has raised_at_stage=pre_score_ingestion; _flags_raised_stage2 empty by design.",
        "Pre-processing is runtime-independent (does NOT read drone/base/gcp/check_point scores); "
        "_handoff_crossdoc_candidates empty by design (slot kept for a future capture_score).",
    ]

    return {
        "survey_level": True,
        "source_fields": dict(sorted(source_fields.items())),
        "per_source_parser_meta": {sid: results[sid]["parser_meta"] for sid in results},
        "per_source_audit": dict(sorted(per_source_audit.items())),
        "merge_meta": {
            "expected_field_count_total": expected_total,
            "produced_field_count_total": produced_l1f_total,
            "source_field_counts_by_source": {k: len(v) for k, v in sorted(keys_by_source.items())},
            "cross_source_previews": cross_source_previews,
            "survey_merge_warnings": survey_warnings,
            "merge_notes": merge_notes,
        },
        "_flags_raised_stage2": flags_raised_stage2,
        "_handoff_crossdoc_candidates": [],
    }


def print_summary(data: dict) -> None:
    mm = data["merge_meta"]
    print(f"  survey-level merge  |  produced {mm['produced_field_count_total']}/"
          f"{mm['expected_field_count_total']} source fields")
    for sid, a in data["per_source_audit"].items():
        rec = f" records={a['record_count']}" if a["record_count"] is not None else ""
        flag = "" if not (a["missing_keys"] or a["extra_keys"]) else "  *** KEY DRIFT"
        print(f"    {sid:18s} {a['produced_count']}/{a['expected_count']} keys  "
              f"instance={a['instance_found']}{rec}{flag}")
    cp = mm["cross_source_previews"]
    print(f"  previews: completeness={cp['geotag_completeness_preview']['fraction']} "
          f"gcp={cp['gcp_count']} cp={cp['cp_count']} no_cps={cp['no_check_points']} "
          f"report={cp['report_present']}")
    print(f"  warnings: {len(mm['survey_merge_warnings'])}  "
          f"_flags_raised_stage2: {len(data['_flags_raised_stage2'])}  "
          f"_handoff_crossdoc_candidates: {len(data['_handoff_crossdoc_candidates'])}")
    for w in mm["survey_merge_warnings"]:
        print(f"    WARN  {w['code']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pre-Processing Stage 2 merge")
    parser.add_argument("config", help="Path to paths.json")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    config = common.load_config(config_path)
    root = config_path.parent
    spec = common.load_spec(root, config)
    spec_version = spec["_meta"]["version"]

    env1, hard_failures = stage1_inventory.run(config, root)
    if hard_failures and config.get("options", {}).get("fail_fast", True):
        print("HALT: Stage 1 reported a hard failure (fail_fast); not running merge.")
        stage1_inventory.print_summary(env1, hard_failures)
        return 1

    data = run(config, root, spec, env1["data"])
    out_path = root / config["outputs"]["stage2_source_fields"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))

    print(f"Stage 2 source fields -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
