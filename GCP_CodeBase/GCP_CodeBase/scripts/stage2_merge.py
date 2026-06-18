#!/usr/bin/env python3
"""Stage 2 - per-point source-field assembly for GCP PPK (multi-occupation).

Consumes the Stage 1 inventory (the discovery authority) and, for each
discovered point folder, runs the three GCP parsers and merges their L1F
fields into a single 40-field source_fields dict for that point:

  SRC_GCP_RINEX   L1F_GCP_001..018  (18)  parse_rinex       (OBS + sibling NAV/PDOP)
  SRC_GCP_OPLOG   L1F_GCP_019..025  (7)   parse_oplog       (device-type-aware)
  SRC_GCP_FORM    L1F_GCP_026..040  (15)  parse_user_input

GCP-specific orchestration vs the base-station single-instance merge:
  - Iterates points[] from the Stage 1 inventory rather than one folder.
  - Threads device_type FORM->OPLOG: the FORM parser runs first; its
    L1F_GCP_026_device_type drives the oplog presence expectation
    (DGPS expected-present; CB_X/AEROPOINT/OTHER expected-absent).
  - The RINEX parser self-discovers the sibling NAV (for PDOP) and loads the
    per-point hardware.json override; Stage 2 passes the inventory-resolved
    hardware path so a configurable hardware_filename is honoured.

Per spec, every L1F_GCP_* field is owned by exactly one source; no
cross-parser L1F computations happen here. Cross-source consistency checks
(L2D_GCP_017 antenna_type_match, L2D_GCP_018 antenna_height_agreement,
L2D_GCP_019 device_id_match, L2D_GCP_023 truncation_check) are L2D derived
fields evaluated at Stage 3a, not here.

Per the spec flag table, no flag has raised_at_stage=pre_score_ingestion, so
data._flags_raised_stage2 is an empty array by design (kept for a uniform
aggregation shape). The GCP/CHECK_POINT partition and FLG_GCP_012
NO_DESIGNATED_GCPS evaluation are deferred to the cross-point rollup at Stage
3c/3d; merge surfaces only an advisory device_role_partition_preview.
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

# Stage 2 emits per point, in the parser invocation order below. device_type
# must be known before the oplog branch, so FORM is parsed first.
SOURCE_ORDER = ("SRC_GCP_RINEX", "SRC_GCP_OPLOG", "SRC_GCP_FORM")

# Wall-clock telemetry each parser stamps into its parser_meta. It is the parser's
# own internal provenance, never consumed downstream, and non-deterministic - so it
# must not reach the written data block (determinism rule 3: only the envelope's
# generated_at may carry a timestamp). Stripped when surfacing per_source_parser_meta.
_VOLATILE_PARSER_META_KEYS = ("started_at", "finished_at", "wall_time_sec")


def _strip_volatile_meta(parser_meta: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in parser_meta.items() if k not in _VOLATILE_PARSER_META_KEYS}


# ---- parser import ----------------------------------------------------------

def _import_parsers():
    parsers_dir = Path(__file__).resolve().parent / "parsers"
    if str(parsers_dir) not in sys.path:
        sys.path.insert(0, str(parsers_dir))
    import parse_rinex  # type: ignore
    import parse_oplog  # type: ignore
    import parse_user_input  # type: ignore
    return parse_rinex, parse_oplog, parse_user_input


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


def _synthetic_absent_rinex(keys: list[str], reason: str) -> dict[str, Any]:
    """An absent-RINEX result mirroring the parser contract (null-preserving).

    Used only when the inventory found no OBS file for a point. (Stage 1
    hard-fails when zero OBS exist across the whole survey, so this is the
    defensive single-point case.)
    """
    return {
        "fields": {k: None for k in keys},
        "parser_meta": {
            "parser_id": "parse_rinex",
            "parser_version": None,
            "source_file_id": "SRC_GCP_RINEX",
            "source_file_name": None,
            "instance_found": False,
            "fields_provided": sorted(keys),
            "field_sources": {k: "absent_rinex_null" for k in keys},
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
    parse_rinex, parse_oplog, parse_user_input = parsers
    point_id = point["point_id"]
    point_folder = root / point["point_folder"]
    point_warnings: list[dict[str, Any]] = []

    # 1. FORM first - L1F_GCP_026_device_type drives the oplog presence branch.
    form_result = parse_user_input.parse(point_folder, root)
    device_type = form_result["fields"].get("L1F_GCP_026_device_type")
    device_role = form_result["fields"].get("L1F_GCP_028_device_role")

    # 2. OPLOG - device-type-aware (expected-present for DGPS only).
    oplog_result = parse_oplog.parse(point_folder, root, device_type)

    # 3. RINEX - self-discovers sibling NAV; honours inventory hardware path.
    obs_info = point.get("rinex_obs")
    if obs_info:
        obs_path = root / obs_info["path"]
        hw_info = point.get("hardware")
        hw_path = (root / hw_info["path"]) if hw_info else None
        rinex_result = parse_rinex.parse(obs_path, root, hw_path)
    else:
        rinex_result = _synthetic_absent_rinex(
            keys_by_source["SRC_GCP_RINEX"],
            f"No RINEX OBS for {point_id} in the Stage 1 inventory; "
            "18 SRC_GCP_RINEX fields kept null.",
        )
        point_warnings.append({
            "code": "RINEX_OBS_MISSING_FOR_POINT",
            "detail": f"{point_id} has no OBS file; RINEX-derived scoring will degrade downstream.",
        })

    parser_results = [
        ("SRC_GCP_RINEX", rinex_result),
        ("SRC_GCP_OPLOG", oplog_result),
        ("SRC_GCP_FORM", form_result),
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
        # Echo of L1F_GCP_026 / L1F_GCP_028 for downstream convenience; the
        # authoritative values live in source_fields. Stage 3c uses device_role
        # to partition GCP vs CHECK_POINT.
        "device_type": device_type,
        "device_role": device_role,
        "source_fields": dict(sorted(all_fields.items())),
        "per_source_parser_meta": {
            src_id: _strip_volatile_meta(result["parser_meta"])
            for src_id, result in parser_results
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
    obs_missing_points: list[str] = []
    survey_warnings: list[dict[str, Any]] = []

    for point in points_in:
        rec = _merge_point(point, project_root, parsers, keys_by_source, expected_total)
        point_records.append(rec)
        if not point.get("rinex_obs"):
            obs_missing_points.append(rec["point_id"])

    if not points_in:
        survey_warnings.append({
            "code": "NO_POINTS_FROM_INVENTORY",
            "detail": "Stage 1 produced zero point folders; nothing to merge.",
        })

    # Advisory device_role partition preview (authoritative partition is Stage 3c).
    gcp_role = [r["point_id"] for r in point_records if r["device_role"] == "GCP"]
    check_pts = [r["point_id"] for r in point_records if r["device_role"] == "CHECK_POINT"]
    unresolved = [r["point_id"] for r in point_records if r["device_role"] not in ("GCP", "CHECK_POINT")]
    if point_records and not gcp_role:
        survey_warnings.append({
            "code": "NO_GCP_ROLE_POINTS_PREVIEW",
            "detail": (
                "No point resolved device_role=GCP; unless resolved upstream, Stage 3c/3d "
                "will fire FLG_GCP_012 NO_DESIGNATED_GCPS (gcp_score=null)."
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
        "Per spec _meta.counts, 40 source fields per point: SRC_GCP_RINEX 18 "
        "(L1F_GCP_001..018), SRC_GCP_OPLOG 7 (019..025), SRC_GCP_FORM 15 (026..040); "
        "each field is owned by exactly one source - no cross-parser L1F computation at merge.",
        "device_type is threaded FORM->OPLOG: parse_user_input runs first and its "
        "L1F_GCP_026_device_type sets the oplog presence expectation (DGPS expected-present; "
        "CB_X/AEROPOINT/OTHER expected-absent, RINEX-only path normal).",
        "Cross-source consistency (L2D_GCP_017 antenna_type_match, L2D_GCP_018 "
        "antenna_height_agreement, L2D_GCP_019 device_id_match, L2D_GCP_023 truncation_check) "
        "is derived at Stage 3a, not at merge.",
        "No spec flag has raised_at_stage=pre_score_ingestion; _flags_raised_stage2 is empty "
        "by design (uniform aggregation shape).",
        "No timestamps anywhere in the data block (determinism rule 3): merge_meta carries "
        "none, and each parser's wall-clock telemetry (started_at/finished_at/wall_time_sec) "
        "is stripped from per_source_parser_meta on the way out.",
        "device_role_partition_preview is advisory only; the authoritative GCP/CHECK_POINT "
        "partition and FLG_GCP_012 NO_DESIGNATED_GCPS evaluation occur at Stage 3c/3d.",
    ]

    return {
        "points": point_records,
        "merge_meta": {
            "expected_field_count_per_point": expected_total,
            "source_field_counts_by_source": {k: len(v) for k, v in sorted(keys_by_source.items())},
            "point_count": len(point_records),
            "points_with_obs": sum(1 for p in points_in if p.get("rinex_obs")),
            "obs_missing_points": obs_missing_points,
            "device_role_partition_preview": {
                "gcp_role_points": gcp_role,
                "check_point_points": check_pts,
                "unresolved_role_points": unresolved,
            },
            "survey_merge_warnings": survey_warnings,
            "merge_notes": merge_notes,
        },
        "_flags_raised_stage2": flags_raised_stage2,
    }


def print_summary(data: dict[str, Any]) -> None:
    mm = data["merge_meta"]
    expected = mm["expected_field_count_per_point"]
    print(f"  points merged: {mm['point_count']}  (OBS present: {mm['points_with_obs']})")
    for p in data["points"]:
        non_null = sum(1 for v in p["source_fields"].values() if v is not None)
        print(f"    - {p['point_id']}: device_type={p['device_type']} role={p['device_role']}  "
              f"{non_null}/{expected} fields non-null")
    prev = mm["device_role_partition_preview"]
    print(f"  role preview: GCP={prev['gcp_role_points']} "
          f"CHECK_POINT={prev['check_point_points']} unresolved={prev['unresolved_role_points']}")
    n_warn = sum(len(p["point_merge_warnings"]) for p in data["points"]) + len(mm["survey_merge_warnings"])
    print(f"  merge warnings: {n_warn}")
    for w in mm["survey_merge_warnings"]:
        print(f"    WARN  {w['code']}")
    for p in data["points"]:
        for w in p["point_merge_warnings"]:
            print(f"    WARN  [{p['point_id']}] {w['code']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GCP PPK Stage 2 merge")
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
