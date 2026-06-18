#!/usr/bin/env python3
"""Stage 3d - apex check_point_score (spec.check_point_score) + survey-level flag aggregation.

  formula:     0.45*BB_CP_COMPLETE + 0.35*BB_CP_SETUP + 0.20*BB_CP_ENV
               (block scores are the cross-point AGGREGATES from Stage 3c).
  weights:     read at runtime from spec.check_point_score_blocks (NEVER hardcoded).
  global gate: every CHECK_POINT-role point has cp_fix_type_score=0 (FLOAT/AUTONOMOUS)
               OR cp_position_sigma_score=0 (catastrophic sigma) -> check_point_score = 0,
               FLAG FLG_CP_001 CP_CRITICAL_FAILURE. This is a PER-POINT test on the
               Stage-3c completeness_killed flag, NOT "COMPLETE aggregate == 0": the
               aggregator can clamp COMPLETE to 0 while some points are still usable.
  null:        zero CHECK_POINT-role points -> check_point_score = null (not 0), FLAG
               FLG_CP_002 NO_DESIGNATED_CHECK_POINTS (already raised at Stage 3c).

All flags raised across Stages 2 / 3a / 3b / 3c plus the apex-stage flag are
concatenated into all_flags_aggregated, each retaining its _origin_stage tag.
Stage 2's _handoff_crossdoc_candidates (empty by spec) is carried through
separately. No timestamps in the data block (determinism rule 3).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import stage1_inventory  # noqa: E402
import stage2_merge  # noqa: E402
import stage3a_derived  # noqa: E402
import stage3b_indicators  # noqa: E402
import stage3c_blocks  # noqa: E402

STAGE = "stage3d_score"
WEIGHT_SUM_TOLERANCE = 1e-6
IN_SCOPE_ROLE = "CHECK_POINT"


def _flag_record(spec_flag: dict, condition_value: Any) -> dict:
    return {
        "flag_id": spec_flag["flag_id"],
        "flag_name": spec_flag["flag_name"],
        "severity": spec_flag["severity"],
        "raised_at_stage_spec": spec_flag["raised_at_stage"],
        "_origin_stage": "stage3d",
        "condition_value": condition_value,
    }


def _aggregate_flags(stage2_data, stage3a_data, stage3b_data, stage3c_data, apex_stage_flags):
    """Concatenate flags from every prior stage plus the apex stage, each
    retaining its _origin_stage tag (backfilled if upstream forgot)."""
    all_flags: list[dict] = []

    def _tag(flags, default_stage):
        for f in flags:
            f = dict(f)
            f.setdefault("_origin_stage", default_stage)
            all_flags.append(f)

    _tag(stage2_data.get("_flags_raised_stage2", []), "stage2_merge_or_parser")
    _tag(stage3a_data.get("flags_raised_stage3a", []), "stage3a")
    _tag(stage3b_data.get("flags_raised_stage3b", []), "stage3b")
    _tag(stage3c_data.get("flags_raised_stage3c", []), "stage3c")
    _tag(apex_stage_flags, "stage3d")

    by_origin: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for f in all_flags:
        by_origin[f["_origin_stage"]] = by_origin.get(f["_origin_stage"], 0) + 1
        sev = f.get("severity", "UNKNOWN")
        by_severity[sev] = by_severity.get(sev, 0) + 1
    return all_flags, dict(sorted(by_origin.items())), dict(sorted(by_severity.items()))


def run(config: dict, project_root: Path, spec: dict, stage2_data: dict,
        stage3a_data: dict, stage3b_data: dict, stage3c_data: dict) -> dict:
    apex_spec = spec["check_point_score"]
    spec_block_order = [b["block_id"] for b in spec["check_point_score_blocks"]]
    block_weights = {b["block_id"]: float(b["weight"]) for b in spec["check_point_score_blocks"]}
    aggregated = stage3c_data.get("aggregated_blocks", {})
    per_point = stage3c_data.get("per_point_blocks", [])
    flag_index = {f["flag_id"]: f for f in spec.get("flags", [])}

    cp_points = [pb for pb in per_point if pb.get("device_role") == IN_SCOPE_ROLE]
    no_cp_points = len(cp_points) == 0

    apex_stage_flags: list[dict] = []
    notes: list[str] = []
    killed_by_point: dict[str, bool] = {}

    if no_cp_points:
        # null_handler: check_point_score is null (NOT 0). FLG_CP_002 normally fires
        # at 3c; only raise here if 3c somehow did not, to avoid a duplicate.
        check_point_score: float | None = None
        weighted_before_gate: float | None = None
        contributions: list[dict] = []
        global_gate_triggered = False
        already_002 = any(f.get("flag_id") == "FLG_CP_002"
                          for f in stage3c_data.get("flags_raised_stage3c", []))
        if not already_002 and "FLG_CP_002" in flag_index:
            apex_stage_flags.append(_flag_record(flag_index["FLG_CP_002"], {"check_point_role_count": 0}))
        notes.append("No CHECK_POINT-role points -> check_point_score = null (null_handler). "
                     "FLG_CP_002 " + ("carried from Stage 3c." if already_002 else "raised here."))
    else:
        contributions = []
        weighted_total = 0.0
        for block_id in spec_block_order:
            weight = block_weights[block_id]
            entry = aggregated.get(block_id, {})
            val = entry.get("aggregate_score")
            score = float(val) if val is not None else 0.0
            contrib = round(weight * score, 3)
            weighted_total += contrib
            contributions.append({
                "block_id": block_id,
                "block_name": entry.get("block_name"),
                "weight_in_apex": weight,
                "block_aggregate_score": score,
                "contribution": contrib,
            })
        weighted_before_gate = round(weighted_total, 1)

        killed_by_point = {pb["point_id"]: bool(pb.get("completeness_killed")) for pb in cp_points}
        global_gate_triggered = all(killed_by_point.values())
        if global_gate_triggered:
            apex_stage_flags.append(_flag_record(
                flag_index["FLG_CP_001"],
                {"all_check_point_role_points_killed": True, "check_point_role_count": len(cp_points)}))
            check_point_score = 0.0
            notes.append("Global gate FIRED: every CHECK_POINT-role point has cp_fix_type_score=0 "
                         "OR cp_position_sigma_score=0 -> check_point_score = 0, FLG_CP_001 "
                         "CP_CRITICAL_FAILURE.")
        else:
            check_point_score = weighted_before_gate

    all_flags, by_origin, by_severity = _aggregate_flags(
        stage2_data, stage3a_data, stage3b_data, stage3c_data, apex_stage_flags)

    apex_weight_sum = sum(block_weights.values())
    weight_audit_ok = abs(apex_weight_sum - 1.0) < WEIGHT_SUM_TOLERANCE

    # Cross-check apex sheet (check_point_score_blocks.weight) vs building-block
    # sheet (building_blocks.weight_in_check_point_score) - they must agree.
    bb_weights = {b["block_id"]: float(b["weight_in_check_point_score"])
                  for b in spec.get("building_blocks", [])}
    weight_consistency = {
        bid: {"check_point_score_blocks": w, "building_blocks": bb_weights.get(bid),
              "match": bb_weights.get(bid) == w}
        for bid, w in block_weights.items()
    }
    weight_mismatches = [bid for bid, c in weight_consistency.items() if not c["match"]]

    return {
        "check_point_score": check_point_score,
        "apex_formula_spec": apex_spec["formula_expression"],
        "apex_weights_used": block_weights,
        "weighted_score_before_global_gate": weighted_before_gate,
        "contributions": contributions,
        "global_gate": {
            "triggered": global_gate_triggered,
            "condition_spec": apex_spec["global_gate_condition"],
            "action_spec": apex_spec["global_gate_action"],
            "completeness_killed_by_point": dict(sorted(killed_by_point.items())),
        },
        "null_handling": {
            "no_check_point_role_points": no_cp_points,
            "condition_spec": apex_spec["null_handling"],
        },
        "all_flags_aggregated": all_flags,
        "flags_by_origin_stage": by_origin,
        "flags_by_severity": by_severity,
        "_handoff_crossdoc_candidates": stage2_data.get("_handoff_crossdoc_candidates", []),
        "stage3d_notes": notes,
        "stage3d_meta": {
            "apex_score_id": apex_spec["score_id"],
            "apex_display_name": apex_spec["display_name"],
            "workflow": apex_spec["workflow"],
            "phase": apex_spec["phase"],
            "scope_note": apex_spec.get("scope_note"),
            "source_file_set": apex_spec.get("source_file_set"),
            "apex_weight_sum_audit": {
                "computed": round(apex_weight_sum, 6), "expected": 1.0, "ok": weight_audit_ok},
            "apex_weight_consistency_vs_building_blocks": weight_consistency,
            "apex_weight_consistency_mismatches": weight_mismatches,
            "check_point_role_count": len(cp_points),
            "effective_check_point_count": stage3c_data.get("stage3c_meta", {}).get(
                "effective_check_point_count"),
            "total_flags_aggregated": len(all_flags),
        },
    }


def print_summary(data: dict) -> None:
    gg = data["global_gate"]
    nh = data["null_handling"]
    score = data["check_point_score"]
    score_str = "null" if score is None else str(score)
    print(f"  check_point_score = {score_str}   "
          f"(weighted_before_gate={data['weighted_score_before_global_gate']})")
    print(f"  formula: {data['apex_formula_spec']}")
    for c in data["contributions"]:
        print(f"    {c['block_id']:18s} w={c['weight_in_apex']}  x agg={c['block_aggregate_score']}"
              f"  = {c['contribution']}")
    if nh["no_check_point_role_points"]:
        print("  null_handler: no CHECK_POINT-role points -> check_point_score = null")
    else:
        print(f"  global_gate triggered: {gg['triggered']}  "
              f"(killed by point: {gg['completeness_killed_by_point']})")
    mm = data["stage3d_meta"]
    aud = mm["apex_weight_sum_audit"]
    print(f"  apex weight-sum audit: computed={aud['computed']} ok={aud['ok']}  "
          f"consistency mismatches: {mm['apex_weight_consistency_mismatches'] or 'none'}")
    print(f"  effective_check_point_count: {mm['effective_check_point_count']}")
    print(f"  flags aggregated (all stages): {mm['total_flags_aggregated']}  "
          f"by_severity={data['flags_by_severity']}")
    print(f"  by_origin_stage={data['flags_by_origin_stage']}")
    print(f"  _handoff_crossdoc_candidates: {len(data['_handoff_crossdoc_candidates'])}")
    for fl in data["all_flags_aggregated"]:
        pt = fl.get("_origin_point")
        loc = f"[{pt}] " if pt else ""
        print(f"    FLAG  {loc}{fl['flag_id']} {fl['flag_name']} ({fl['severity']}) @{fl['_origin_stage']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check Point PPK Stage 3d apex check_point_score")
    parser.add_argument("config", help="Path to paths.json")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    config = common.load_config(config_path)
    root = config_path.parent
    spec = common.load_spec(root, config)
    spec_version = spec["_meta"]["version"]

    env1, hard = stage1_inventory.run(config, root)
    if hard and config.get("options", {}).get("fail_fast", True):
        print("HALT: Stage 1 hard failure (fail_fast).")
        return 1
    data2 = stage2_merge.run(config, root, spec, env1["data"])
    data3a = stage3a_derived.run(config, root, spec, data2)
    data3b = stage3b_indicators.run(config, root, spec, data3a, data2)
    data3c = stage3c_blocks.run(config, root, spec, data3b)
    data = run(config, root, spec, data2, data3a, data3b, data3c)

    out_path = root / config["outputs"]["stage3_check_point_score"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    print(f"Stage 3d check_point_score -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
