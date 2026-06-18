#!/usr/bin/env python3
"""Stage 3d - apex gcp_score (spec.gcp_score) + survey-level flag aggregation.

  formula:     0.45*BB_GCP_COMPLETE + 0.35*BB_GCP_SETUP + 0.20*BB_GCP_ENV
               (block scores are the cross-point AGGREGATES from Stage 3c).
  weights:     read at runtime from spec.gcp_score_blocks (NEVER hardcoded).
  global gate: every GCP-role point has its coverage gate fired
               -> gcp_score = 0, FLAG FLG_GCP_001 GCP_CRITICAL_FAILURE.
               This is a PER-POINT test, NOT "COMPLETE aggregate == 0": the
               aggregator can clamp the COMPLETE block to 0 while some points
               are still ungated, so the aggregate-equals-0 shortcut (used by
               the single-instance base_station_score) would over-trigger here.
  null:        zero GCP-role points -> gcp_score = null (not 0), FLAG
               FLG_GCP_012 NO_DESIGNATED_GCPS (already raised at Stage 3c).

All flags raised across Stages 2 / 3a / 3b / 3c plus the apex-stage flag are
concatenated into all_flags_aggregated, each retaining its _origin_stage tag.
No timestamps live in the data block (determinism rule 3).
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


def _flag_record(spec_flag: dict, condition_value: Any) -> dict:
    return {
        "flag_id": spec_flag["flag_id"],
        "flag_name": spec_flag["flag_name"],
        "severity": spec_flag["severity"],
        "raised_at_stage_spec": spec_flag["raised_at_stage"],
        "_origin_stage": "stage3d",
        "condition_value": condition_value,
    }


def _aggregate_flags(stage2_data: dict, stage3a_data: dict, stage3b_data: dict,
                     stage3c_data: dict, apex_stage_flags: list[dict]
                     ) -> tuple[list[dict], dict[str, int], dict[str, int]]:
    """Concatenate flags from every prior stage plus the apex stage, each
    retaining its _origin_stage tag. Returns (flags, by_origin, by_severity)."""
    all_flags: list[dict] = []

    def _tag(flags: list[dict], default_stage: str) -> None:
        for f in flags:
            f = dict(f)  # copy so we never mutate an upstream stage's record
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
    apex_spec = spec["gcp_score"]
    spec_block_order = [b["block_id"] for b in spec["gcp_score_blocks"]]
    block_weights = {b["block_id"]: float(b["weight"]) for b in spec["gcp_score_blocks"]}
    aggregated = stage3c_data.get("aggregated_blocks", {})
    per_point = stage3c_data.get("per_point_blocks", [])
    flag_index = {f["flag_id"]: f for f in spec.get("flags", [])}

    gcp_points = [pb for pb in per_point if pb.get("device_role") == "GCP"]
    no_gcp_points = len(gcp_points) == 0

    apex_stage_flags: list[dict] = []
    notes: list[str] = []
    coverage_gate_fired_by_point: dict[str, bool] = {}

    if no_gcp_points:
        # null_handler: gcp_score is null (NOT 0). FLG_GCP_012 normally fires at
        # 3c; only raise here if 3c somehow did not, to avoid a duplicate.
        gcp_score: float | None = None
        weighted_score_before_global_gate: float | None = None
        contributions: list[dict] = []
        global_gate_triggered = False
        already_012 = any(f.get("flag_id") == "FLG_GCP_012"
                          for f in stage3c_data.get("flags_raised_stage3c", []))
        if not already_012 and "FLG_GCP_012" in flag_index:
            apex_stage_flags.append(_flag_record(
                flag_index["FLG_GCP_012"], {"gcp_role_point_count": 0}))
        notes.append(
            "No GCP-role points -> gcp_score = null (null_handler). FLG_GCP_012 "
            "NO_DESIGNATED_GCPS " + ("carried from Stage 3c." if already_012 else "raised here."))
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
        weighted_score_before_global_gate = round(weighted_total, 1)

        coverage_gate_fired_by_point = {
            pb["point_id"]: bool(pb["block_scores"]["BB_GCP_COMPLETE"]["gate_triggered"])
            for pb in gcp_points
        }
        global_gate_triggered = all(coverage_gate_fired_by_point.values())
        if global_gate_triggered:
            apex_stage_flags.append(_flag_record(
                flag_index["FLG_GCP_001"],
                {"all_gcp_role_points_coverage_gated": True,
                 "gcp_role_point_count": len(gcp_points)}))
            gcp_score = 0.0
            notes.append(
                "Global gate FIRED: every GCP-role point is coverage-gated -> "
                "gcp_score = 0, FLG_GCP_001 GCP_CRITICAL_FAILURE.")
        else:
            gcp_score = weighted_score_before_global_gate

    all_flags, by_origin, by_severity = _aggregate_flags(
        stage2_data, stage3a_data, stage3b_data, stage3c_data, apex_stage_flags)

    apex_weight_sum = sum(block_weights.values())
    weight_audit_ok = abs(apex_weight_sum - 1.0) < WEIGHT_SUM_TOLERANCE

    # Cross-check the apex sheet (gcp_score_blocks.weight) against the building-
    # block sheet (building_blocks.weight_in_gcp_score) - they must agree.
    bb_weights = {b["block_id"]: float(b["weight_in_gcp_score"])
                  for b in spec.get("building_blocks", [])}
    weight_consistency = {
        bid: {"gcp_score_blocks": w,
              "building_blocks": bb_weights.get(bid),
              "match": bb_weights.get(bid) == w}
        for bid, w in block_weights.items()
    }
    weight_mismatches = [bid for bid, c in weight_consistency.items() if not c["match"]]

    return {
        "gcp_score": gcp_score,
        "apex_formula_spec": apex_spec["formula_expression"],
        "apex_weights_used": block_weights,
        "weighted_score_before_global_gate": weighted_score_before_global_gate,
        "contributions": contributions,
        "global_gate": {
            "triggered": global_gate_triggered,
            "condition_spec": apex_spec["global_gate_condition"],
            "action_spec": apex_spec["global_gate_action"],
            "coverage_gate_fired_by_point": dict(sorted(coverage_gate_fired_by_point.items())),
        },
        "null_handling": {
            "no_gcp_role_points": no_gcp_points,
            "condition_spec": apex_spec["null_handling"],
        },
        "all_flags_aggregated": all_flags,
        "flags_by_origin_stage": by_origin,
        "flags_by_severity": by_severity,
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
            "gcp_role_point_count": len(gcp_points),
            "total_flags_aggregated": len(all_flags),
        },
    }


def print_summary(data: dict) -> None:
    gg = data["global_gate"]
    nh = data["null_handling"]
    score = data["gcp_score"]
    score_str = "null" if score is None else str(score)
    print(f"  gcp_score = {score_str}   "
          f"(weighted_before_gate={data['weighted_score_before_global_gate']})")
    print(f"  formula: {data['apex_formula_spec']}")
    for c in data["contributions"]:
        print(f"    {c['block_id']:18s} w={c['weight_in_apex']}  x agg={c['block_aggregate_score']}"
              f"  = {c['contribution']}")
    if nh["no_gcp_role_points"]:
        print("  null_handler: no GCP-role points -> gcp_score = null")
    else:
        print(f"  global_gate triggered: {gg['triggered']}  "
              f"(coverage-gated by point: {gg['coverage_gate_fired_by_point']})")
    mm = data["stage3d_meta"]
    aud = mm["apex_weight_sum_audit"]
    print(f"  apex weight-sum audit: computed={aud['computed']} ok={aud['ok']}  "
          f"consistency mismatches: {mm['apex_weight_consistency_mismatches'] or 'none'}")
    print(f"  flags aggregated (all stages): {mm['total_flags_aggregated']}  "
          f"by_severity={data['flags_by_severity']}")
    print(f"  by_origin_stage={data['flags_by_origin_stage']}")
    for fl in data["all_flags_aggregated"]:
        pt = fl.get("_origin_point")
        loc = f"[{pt}] " if pt else ""
        print(f"    FLAG  {loc}{fl['flag_id']} {fl['flag_name']} ({fl['severity']}) "
              f"@{fl['_origin_stage']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GCP PPK Stage 3d apex gcp_score")
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

    out_path = root / config["outputs"]["stage3_gcp_score"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    print(f"Stage 3d gcp_score -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
