#!/usr/bin/env python3
"""Stage 2 - per-point source-field assembly for Check Point PPK (multi-occupation RTK).

Consumes the Stage 1 inventory (the discovery authority) and, for each
discovered point folder, runs the three RTK parsers and merges their L1F
fields into a single 38-field source_fields dict for that point:

  SRC_CP_RTK_EXPORT  L1F_CP_001..015  (15)  parse_rtk_export  (per-point device export)
  SRC_CP_OPLOG       L1F_CP_016..019  (4)   parse_oplog       (device-type-aware)
  SRC_CP_FORM        L1F_CP_020..038  (19)  parse_form

CheckPoint-specific orchestration:
  - Iterates points[] from the Stage 1 inventory rather than one folder.
  - Threads device_type FORM->OPLOG: the FORM parser runs first; its
    L1F_CP_020_device_type drives the oplog presence expectation
    (CB_X/AEROPOINT/DGPS expected-present; OTHER expected-absent).
  - The RTK export parser is handed the inventory-resolved export PATH (RTK
    exports are vendor files with varying extensions, not a fixed filename).
    There is no RINEX/NAV and no hardware-override file in RTK.

Per spec, every L1F_CP_* field is owned by exactly one source; no cross-parser
L1F computations happen here. Cross-source consistency checks (L2D_CP_006
antenna_type_match, L2D_CP_007 device_id_match, L2D_CP_005 antenna_height_agreement)
are L2D derived fields evaluated at Stage 3a, not here.

Per the spec flag table, no flag has raised_at_stage=pre_score_ingestion, so
data._flags_raised_stage2 is an empty array by design (kept for a uniform
aggregation shape). The spec defines no handoff_crossdoc flags either, so
data._handoff_crossdoc_candidates is empty by design (slot kept for the
cross-bundle stage, per template section 7e). The CHECK_POINT partition and
FLG_CP_002 NO_DESIGNATED_CHECK_POINTS evaluation are deferred to the cross-point
rollup at Stage 3c/3d; merge surfaces only an advisory device_role_partition_preview.
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

# Stage 2 emits per point. device_type must be known before the oplog branch,
# so FORM is parsed first.
SOURCE_ORDER = ("SRC_CP_RTK_EXPORT", "SRC_CP_OPLOG", "SRC_CP_FORM")

IN_SCOPE_ROLE = "CHECK_POINT"
EXCLUDED_ROLE = "GCP"


# ---- parser import ----------------------------------------------------------

def _import_parsers():
    parsers_dir = Path(__file__).resolve().parent / "parsers"
    if str(parsers_dir) not in sys.path:
        sys.path.insert(0, str(parsers_dir))
    import parse_rtk_export  # type: ignore
    import parse_oplog  # type: ignore
    import parse_form  # type: ignore
    return parse_rtk_export, parse_oplog, parse_form


# ---- spec-derived canonical field keys --------------------------------------

def _canonical_keys_by_source(spec: dict[str, Any]) -> dict[str, list[str]]:
    """Map each source file_id to its emitted L1F keys, derived from the spec.

    Every parser emits keys of the form '<field_id>_<field_name>' (verified
    against all three parsers), so the spec is the single source of truth for
    both the per-source audit and for synthesizing nulls when a source's
    instance is absent.
    """
    out: dict[str, list[str]] = {}
    for sf in spec["source_fields"]:
        out.setdefault(sf["file_id"], []).append(f"{sf['field_id']}_{sf['field_name']}")
    return out


def _synthetic_absent_export(keys: list[str], reason: str) -> dict[str, Any]:
    """An absent-export result mirroring the parser contract (null-preserving).

    Used only when the inventory found no RTK export for a point. (Stage 1
    hard-fails when zero exports exist across the whole survey, so this is the
    defensive single-point case.)
    """
    return {
        "fields": {k: None for k in keys},
        "parser_meta": {
            "parser_id": "parse_rtk_export",
            "parser_version": None,
            "source_file_id": "SRC_CP_RTK_EXPORT",
            "source_file_name": None,
            "instance_found": False,
            "fields_provided": sorted(keys),
            "field_sources": {k: "absent_export_null" for k in keys},
            "notes": [reason],
            "flags_raised": [],
        },
    }


# ---- per-point merge --------------------------------------------------------

def _merge_point(
    point: dict[str, Any],
    root: Path,
    parsers: tuple,
    keys_by_source: dict[str, list[str]],
    expected_total: int,
) -> dict[str, Any]:
    parse_rtk_export, parse_oplog, parse_form = parsers
    point_id = point["point_id"]
    point_folder = common.resolve_path(root, point["point_folder"])
    point_warnings: list[dict[str, Any]] = []

    # 1. FORM first - L1F_CP_020_device_type drives the oplog presence branch.
    form_result = parse_form.parse(point_folder, root)
    device_type = form_result["fields"].get("L1F_CP_020_device_type")
    device_role = form_result["fields"].get("L1F_CP_022_device_role")

    # 2. OPLOG - device-type-aware (expected-present for CB_X/AEROPOINT/DGPS).
    oplog_result = parse_oplog.parse(point_folder, root, device_type)

    # 3. RTK EXPORT - handed the inventory-resolved export path.
    export_info = point.get("rtk_export")
    if export_info and not export_info.get("below_min_bytes", False):
        export_path = common.resolve_path(root, export_info["path"])
        export_result = parse_rtk_export.parse(export_path, root)
    else:
        reason = (f"No usable RTK export for {point_id} in the Stage 1 inventory; "
                  "15 SRC_CP_RTK_EXPORT fields kept null.")
        export_result = _synthetic_absent_export(keys_by_source["SRC_CP_RTK_EXPORT"], reason)
        point_warnings.append({
            "code": "RTK_EXPORT_MISSING_FOR_POINT",
            "detail": f"{point_id} has no usable export; export-derived scoring will degrade.",
        })

    parser_results = [
        ("SRC_CP_RTK_EXPORT", export_result),
        ("SRC_CP_OPLOG", oplog_result),
        ("SRC_CP_FORM", form_result),
    ]

    # ---- merge fields (disjoint ID ranges; overlap is a parser bug) ----
    all_fields: dict[str, Any] = {}
    field_owner: dict[str, str] = {}
    overlaps: list[dict[str, str]] = []
    for src_id, result in parser_results:
        for k, v in result["fields"].items():
            if k in all_fields:
                overlaps.append({
                    "l1f_key": k,
                    "first_owner": field_owner[k],
                    "second_owner": src_id,
                })
            else:
                all_fields[k] = v
                field_owner[k] = src_id
    if overlaps:
        point_warnings.append({"code": "L1F_FIELD_OWNERSHIP_OVERLAP", "detail": overlaps})

    # ---- per-source spec-compliance audit (exact key comparison) ----
    per_source_audit: dict[str, Any] = {}
    for src_id, result in parser_results:
        expected = set(keys_by_source.get(src_id, []))
        produced = set(result["fields"].keys())
        missing = sorted(expected - produced)
        extra = sorted(produced - expected)
        per_source_audit[src_id] = {
            "expected_count": len(expected),
            "produced_count": len(produced),
            "non_null_count": sum(1 for k in produced if result["fields"][k] is not None),
            "missing_keys": missing,
            "extra_keys": extra,
            "instance_found": result["parser_meta"].get("instance_found"),
            "source_file_name": result["parser_meta"].get("source_file_name"),
        }
        if missing:
            point_warnings.append({
                "code": "L1F_FIELDS_MISSING_FROM_PARSER",
                "source_file_id": src_id,
                "missing": missing,
            })
        if extra:
            point_warnings.append({
                "code": "L1F_UNEXPECTED_FIELDS_FROM_PARSER",
                "source_file_id": src_id,
                "extra": extra,
            })

    if len(all_fields) != expected_total:
        point_warnings.append({
            "code": "L1F_POINT_FIELD_COUNT_MISMATCH",
            "expected": expected_total,
            "produced": len(all_fields),
        })

    return {
        "point_id": point_id,
        "point_folder": point["point_folder"],
        # Echo of L1F_CP_020 / L1F_CP_022 for downstream convenience; the
        # authoritative values live in source_fields. Stage 3c uses device_role
        # to partition CHECK_POINT vs GCP.
        "device_type": device_type,
        "device_role": device_role,
        "source_fields": dict(sorted(all_fields.items())),
        "per_source_parser_meta": {
            src_id: result["parser_meta"] for src_id, result in parser_results
        },
        "per_source_audit": per_source_audit,
        "point_merge_warnings": point_warnings,
    }


# ---- survey-level run -------------------------------------------------------

def run(
    config: dict[str, Any],
    project_root: Path,
    spec: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    """Merge every inventoried point into per-point source fields.

    `inventory` is the data block of the Stage 1 envelope (env1["data"]).
    """
    parsers = _import_parsers()
    keys_by_source = _canonical_keys_by_source(spec)
    expected_total = sum(len(v) for v in keys_by_source.values())

    points_in = inventory.get("points", [])
    point_records: list[dict[str, Any]] = []
    export_missing_points: list[str] = []
    survey_warnings: list[dict[str, Any]] = []

    for point in points_in:
        rec = _merge_point(point, project_root, parsers, keys_by_source, expected_total)
        point_records.append(rec)
        exp = point.get("rtk_export")
        if not exp or exp.get("below_min_bytes", False):
            export_missing_points.append(rec["point_id"])

    if not points_in:
        survey_warnings.append({
            "code": "NO_POINTS_FROM_INVENTORY",
            "detail": "Stage 1 produced zero point folders; nothing to merge.",
        })

    # Advisory device_role partition preview (authoritative partition is Stage 3c).
    check_pts = [r["point_id"] for r in point_records if r["device_role"] == IN_SCOPE_ROLE]
    gcp_role = [r["point_id"] for r in point_records if r["device_role"] == EXCLUDED_ROLE]
    unresolved = [r["point_id"] for r in point_records
                  if r["device_role"] not in (IN_SCOPE_ROLE, EXCLUDED_ROLE)]
    if point_records and not check_pts:
        survey_warnings.append({
            "code": "NO_CHECK_POINT_ROLE_POINTS_PREVIEW",
            "detail": (
                "No point resolved device_role=CHECK_POINT; unless resolved upstream, Stage "
                "3c/3d will fire FLG_CP_002 NO_DESIGNATED_CHECK_POINTS (check_point_score=null)."
            ),
        })

    # Stage 2 owns no flag; aggregate any parser-level flags (expected empty)
    # so the downstream aggregation pattern stays uniform.
    flags_raised_stage2: list[dict[str, Any]] = []
    for rec in point_records:
        for src_id, pm in rec["per_source_parser_meta"].items():
            for flag in pm.get("flags_raised", []):
                flags_raised_stage2.append({
                    **flag, "_origin_point": rec["point_id"], "_origin_source": src_id,
                })

    merge_notes = [
        "Per spec _meta.counts, 38 source fields per point: SRC_CP_RTK_EXPORT 15 "
        "(L1F_CP_001..015), SRC_CP_OPLOG 4 (016..019), SRC_CP_FORM 19 (020..038); each "
        "field is owned by exactly one source - no cross-parser L1F computation at merge.",
        "device_type is threaded FORM->OPLOG: parse_form runs first and its "
        "L1F_CP_020_device_type sets the oplog presence expectation (CB_X/AEROPOINT/DGPS "
        "expected-present; OTHER expected-absent).",
        "RTK has no observation file: parse_rtk_export reads the inventory-resolved vendor "
        "export path (.csv implemented; .jxl/.pos/.gpx honest-empty). No RINEX/NAV/PDOP "
        "computation and no hardware-override file exist in the RTK chain.",
        "Cross-source consistency (L2D_CP_006 antenna_type_match, L2D_CP_007 device_id_match, "
        "L2D_CP_005 antenna_height_agreement) is derived at Stage 3a, not at merge.",
        "No spec flag has raised_at_stage=pre_score_ingestion; _flags_raised_stage2 is empty "
        "by design (uniform aggregation shape).",
        "The spec defines no handoff_crossdoc flags; _handoff_crossdoc_candidates is empty by "
        "design (slot kept for the cross-bundle stage per template 7e). Base-vs-CP timing / "
        "antenna / baseline cross-checks, if ever added, would land here.",
        "device_role_partition_preview is advisory only; the authoritative CHECK_POINT/GCP "
        "partition and FLG_CP_002 NO_DESIGNATED_CHECK_POINTS evaluation occur at Stage 3c/3d.",
    ]

    return {
        "points": point_records,
        "merge_meta": {
            "expected_field_count_per_point": expected_total,
            "source_field_counts_by_source": {k: len(v) for k, v in sorted(keys_by_source.items())},
            "point_count": len(point_records),
            "points_with_export": sum(
                1 for p in points_in
                if p.get("rtk_export") and not p["rtk_export"].get("below_min_bytes", False)),
            "export_missing_points": export_missing_points,
            "device_role_partition_preview": {
                "check_point_points": check_pts,
                "gcp_role_points": gcp_role,
                "unresolved_role_points": unresolved,
            },
            "survey_merge_warnings": survey_warnings,
            "merge_notes": merge_notes,
        },
        "_flags_raised_stage2": flags_raised_stage2,
        "_handoff_crossdoc_candidates": [],
    }


def print_summary(data: dict[str, Any]) -> None:
    mm = data["merge_meta"]
    expected = mm["expected_field_count_per_point"]
    print(f"  points merged: {mm['point_count']}  (export present: {mm['points_with_export']})")
    for p in data["points"]:
        non_null = sum(1 for v in p["source_fields"].values() if v is not None)
        print(f"    - {p['point_id']}: device_type={p['device_type']} role={p['device_role']}  "
              f"{non_null}/{expected} fields non-null")
    prev = mm["device_role_partition_preview"]
    print(f"  role preview: CHECK_POINT={prev['check_point_points']} "
          f"GCP={prev['gcp_role_points']} unresolved={prev['unresolved_role_points']}")
    n_warn = sum(len(p["point_merge_warnings"]) for p in data["points"]) + len(mm["survey_merge_warnings"])
    print(f"  merge warnings: {n_warn}")
    for w in mm["survey_merge_warnings"]:
        print(f"    WARN  {w['code']}")
    for p in data["points"]:
        for w in p["point_merge_warnings"]:
            print(f"    WARN  [{p['point_id']}] {w['code']}")
    print(f"  _flags_raised_stage2: {len(data['_flags_raised_stage2'])}  "
          f"_handoff_crossdoc_candidates: {len(data['_handoff_crossdoc_candidates'])}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check Point PPK Stage 2 merge")
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

    print(f"Stage 2 source fields -> {common.display_path(out_path, root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
