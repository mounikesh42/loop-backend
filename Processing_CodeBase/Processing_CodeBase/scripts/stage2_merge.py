#!/usr/bin/env python3
"""Stage 2 - survey-level source-field assembly for Processing.

Consumes the Stage 1 inventory and runs the 4 parsers ONCE (one survey, not N
occupations), merging their fields into a single source_fields dict keyed by
spec field_name (the convention every Processing parser uses, so Stage 3a can
look fields up by name):

  SRC_PROC_REPORT       67 fields   parse_report       Agisoft PDF (section-keyed)
  SRC_PROC_MANIFEST     11 fields   parse_manifest     operator JSON
  SRC_PROC_DELIVERABLES  9 fields   parse_deliverables presence + CRS (rasterio/laspy)
  SRC_PROC_PP_HANDOFF    3 fields   parse_pp_handoff   pp coord file + pp manifest
                       = 90 source fields total

Field_names are disjoint across sources (overlap is a bug). The cross-SOURCE
consistency checks the spec defines (CRS match + gate, camera-model match,
precalibration match, marker roles, deliverable completeness, internal transform)
are L2D derived fields evaluated at Stage 3a - merge only surfaces advisory
previews.

No spec flag has raised_at_stage=pre_score_ingestion, so _flags_raised_stage2 is
empty by design. The two handoff flags (FLG_PROC_063 target-detection via CV3,
FLG_PROC_064 per-deliverable fitness) fire at the handoff stage (3a) and are
aggregated at 3d - not here. Processing is runtime-independent (reads pp SOURCE
artifacts, never pre_processing_score), so _handoff_crossdoc_candidates is empty.
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

_SRC_ORDER = ["SRC_PROC_REPORT", "SRC_PROC_MANIFEST", "SRC_PROC_DELIVERABLES", "SRC_PROC_PP_HANDOFF"]


def _import_parsers():
    parsers_dir = Path(__file__).resolve().parent / "parsers"
    if str(parsers_dir) not in sys.path:
        sys.path.insert(0, str(parsers_dir))
    import parse_report       # type: ignore
    import parse_manifest     # type: ignore
    import parse_deliverables # type: ignore
    import parse_pp_handoff   # type: ignore
    return parse_report, parse_manifest, parse_deliverables, parse_pp_handoff


def _names_by_source(spec) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for sf in spec["source_fields"]:
        out.setdefault(sf["file_id"], []).append(sf["field_name"])
    return out


def _run_parsers(config, root, spec) -> dict[str, dict]:
    parse_report, parse_manifest, parse_deliverables, parse_pp_handoff = _import_parsers()
    inp = config["inputs"]
    report_names = [s["field_name"] for s in spec["source_fields"]
                    if s["file_id"] == "SRC_PROC_REPORT"]

    def _path(key):
        rel = inp.get(key)
        return (root / rel) if rel else None

    # parse_report returns a (fields, meta) tuple; normalise to the dict shape
    rfields, rmeta = parse_report.parse(_path("report_file"), spec_field_names=report_names)
    return {
        "SRC_PROC_REPORT": {"fields": rfields, "parser_meta": rmeta},
        "SRC_PROC_MANIFEST": parse_manifest.parse(_path("manifest_file"), root),
        "SRC_PROC_DELIVERABLES": parse_deliverables.parse(inp.get("deliverables", {}), root),
        "SRC_PROC_PP_HANDOFF": parse_pp_handoff.parse(inp.get("pp_handoff", {}), root),
    }


def run(config, project_root: Path, spec: dict, inventory: dict) -> dict[str, Any]:
    root = project_root
    names_by_source = _names_by_source(spec)
    expected_total = sum(len(v) for v in names_by_source.values())
    results = _run_parsers(config, root, spec)

    # ---- merge field dicts (disjoint field_names; overlap is a bug) ----
    source_fields: dict[str, Any] = {}
    field_owner: dict[str, str] = {}
    overlaps: list[dict] = []
    survey_warnings: list[dict] = []
    for src_id in _SRC_ORDER:
        for k, v in results[src_id]["fields"].items():
            if k in source_fields:
                overlaps.append({"key": k, "first_owner": field_owner[k], "second_owner": src_id})
            else:
                source_fields[k] = v
                field_owner[k] = src_id
    if overlaps:
        survey_warnings.append({"code": "FIELD_OWNERSHIP_OVERLAP", "detail": overlaps})

    # ---- per-source spec audit (produced field_names vs spec names) ----
    per_source_audit: dict[str, Any] = {}
    produced_total = 0
    for src_id in _SRC_ORDER:
        result = results[src_id]
        expected = set(names_by_source.get(src_id, []))
        produced = set(result["fields"].keys())
        produced_total += len(produced)
        missing = sorted(expected - produced)
        extra = sorted(produced - expected)
        non_null = sum(1 for v in result["fields"].values() if v is not None)
        per_source_audit[src_id] = {
            "expected_count": len(expected),
            "produced_count": len(produced),
            "non_null_count": non_null,
            "missing_field_names": missing,
            "extra_field_names": extra,
            "instance_found": result["parser_meta"].get("instance_found"),
        }
        if missing:
            survey_warnings.append({"code": "FIELDS_MISSING_FROM_PARSER",
                                    "source_file_id": src_id, "missing": missing})
        if extra:
            survey_warnings.append({"code": "UNEXPECTED_FIELDS_FROM_PARSER",
                                    "source_file_id": src_id, "extra": extra})
    if produced_total != expected_total:
        survey_warnings.append({"code": "TOTAL_FIELD_COUNT_MISMATCH",
                                "expected": expected_total, "produced": produced_total})

    # ---- advisory cross-source previews (authoritative checks are Stage 3a) ----
    sf = source_fields
    cp_count = sf.get("reportGCP_check_points_count")
    gcp_count = sf.get("reportGCP_control_points_count")
    if cp_count is None:
        verif_preview = None
    elif cp_count == 0:
        verif_preview = "UNVERIFIED_NO_CPS"
    elif cp_count < 5:
        verif_preview = "UNVERIFIED_INSUFFICIENT_CPS"
    else:
        verif_preview = "VERIFIED_* (CP_RMSE band decides PASS/MARGINAL/FAIL)"
    declared = sf.get("manifest_declared_deliverables") or []
    present_map = {d: sf.get(f"deliverable_{d}_present") for d in
                   ("ortho", "dsm", "dtm", "point_cloud", "mesh_3d")}
    missing_deliv = [d for d in declared if not present_map.get(d)]
    cross_source_previews = {
        "crs_spellings_observed": {
            "report_coordinate_system": sf.get("reportParams_coordinate_system"),
            "manifest_project_required_crs": sf.get("manifest_project_required_crs"),
            "deliverable_ortho_crs": sf.get("deliverable_ortho_crs"),
            "pp_manifest_capture_crs": sf.get("pp_manifest_capture_crs"),
        },
        "camera_model_report_vs_manifest": [sf.get("reportCameras_camera_model"),
                                            sf.get("manifest_declared_camera_model")],
        "precalibration_report_vs_manifest": [sf.get("reportCameras_precalibrated"),
                                              sf.get("manifest_precalibration_expected")],
        "marker_count_report": sf.get("reportGCP_total_markers_count"),
        "gcp_count": gcp_count, "cp_count": cp_count,
        "verification_status_preview": verif_preview,
        "no_gcps_signal": (gcp_count == 0),
        "deliverables_declared": declared,
        "deliverables_present": present_map,
        "deliverables_missing_vs_declared": missing_deliv,
        "pp_handoff_present": results["SRC_PROC_PP_HANDOFF"]["parser_meta"].get("instance_found"),
        "_note": ("Advisory only. CRS match + gate, camera-model match, precalibration match, "
                  "marker-role consistency, deliverable completeness, internal-transform "
                  "consistency and verification_status are derived at Stage 3a/3d. "
                  "crs_spellings_observed surfaces the EPSG-string normalisation the Stage-3a "
                  "comparator must handle ('WGS 84 (EPSG::4326)' vs 'EPSG:4326')."),
    }

    # ---- flag aggregation (empty by design) ----
    flags_raised_stage2: list[dict] = []
    for src_id in _SRC_ORDER:
        for flag in results[src_id]["parser_meta"].get("flags_raised", []):
            flags_raised_stage2.append({**flag, "_origin_source": src_id})

    placeholder_sources = [s for s in _SRC_ORDER
                           if results[s]["parser_meta"].get("manifest_status") == "PLACEHOLDER"
                           or results[s]["parser_meta"].get("is_placeholder")
                           or results[s]["parser_meta"].get("placeholder_stubs")]

    merge_notes = [
        "Survey-level: the 4 parsers run ONCE. 90 source fields: REPORT 67, MANIFEST 11, "
        "DELIVERABLES 9, PP_HANDOFF 3.",
        "source_fields keyed by spec field_name (disjoint across sources). per-record data "
        "(per-marker residuals, GCP positions, deliverable per-type) is nested inside the owning "
        "field's value; the 90 is the LOGICAL field count, audited per-source.",
        "Cross-SOURCE consistency (CRS match+gate, camera-model/precalib/role match, deliverable "
        "completeness, internal transform, verification_status) is derived at Stage 3a/3d, not merge.",
        "MANIFEST is required-but-graceful and PP_HANDOFF optional: absence degrades the relevant "
        "report_and_manifest / report_and_pp_handoff indicators to N/A with weight redistribution.",
        "No spec flag has raised_at_stage=pre_score_ingestion; _flags_raised_stage2 empty by design.",
        "The 2 handoff flags (FLG_PROC_063 via CV3, FLG_PROC_064 delivery-layer) fire at the "
        "handoff stage (3a) and aggregate at 3d, not here.",
        "Processing is runtime-independent (reads pp SOURCE artifacts, NOT pre_processing_score); "
        "_handoff_crossdoc_candidates empty by design.",
    ]

    return {
        "survey_level": True,
        "source_fields": dict(sorted(source_fields.items())),
        "per_source_parser_meta": {sid: results[sid]["parser_meta"] for sid in _SRC_ORDER},
        "per_source_audit": dict(sorted(per_source_audit.items())),
        "merge_meta": {
            "expected_field_count_total": expected_total,
            "produced_field_count_total": produced_total,
            "source_field_counts_by_source": {k: len(v) for k, v in sorted(names_by_source.items())},
            "placeholder_sources": placeholder_sources,
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
        drift = "" if not (a["missing_field_names"] or a["extra_field_names"]) else "  *** KEY DRIFT"
        print(f"    {sid:22s} {a['produced_count']}/{a['expected_count']} keys  "
              f"non_null={a['non_null_count']}  instance={a['instance_found']}{drift}")
    cp = mm["cross_source_previews"]
    print(f"  previews: gcp={cp['gcp_count']} cp={cp['cp_count']} verif={cp['verification_status_preview']} "
          f"no_gcps={cp['no_gcps_signal']} deliv_missing={cp['deliverables_missing_vs_declared']}")
    print(f"  placeholder_sources: {mm['placeholder_sources']}")
    print(f"  warnings: {len(mm['survey_merge_warnings'])}  "
          f"_flags_raised_stage2: {len(data['_flags_raised_stage2'])}  "
          f"_handoff_crossdoc_candidates: {len(data['_handoff_crossdoc_candidates'])}")
    for w in mm["survey_merge_warnings"]:
        print(f"    WARN  {w['code']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Processing Stage 2 merge")
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
