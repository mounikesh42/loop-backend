#!/usr/bin/env python3
"""Stage 3c - building-block rollups + cross-point aggregation (spec sheets 05/06).

Two phases, over CHECK_POINT-role points only (GCP-role points are excluded -
the inverse of the gcp_score filter):

  Phase 1 (per point): for each of the 3 blocks, weighted-sum its indicator
    scores from Stage 3b WITH N/A WEIGHT REDISTRIBUTION, then apply the block's
    per-point internal gate:
      BB_CP_COMPLETE - L3I_CP_002 fix gate trips -> per_point completeness = 0,
        FLAG FLG_CP_004 CP_FLOAT_ACCEPTED_AS_FIXED (FLOAT) or FLG_CP_005
        CP_AUTONOMOUS_ACCEPTED (AUTONOMOUS). FLG_CP_004 severity ESCALATES to
        CATASTROPHIC when effective_check_point_count < 5.
      BB_CP_SETUP    - L3I_CP_005 height gate trips -> per_point setup
        confidence = 0, FLAG FLG_CP_003 CP_POINT_ANTENNA_HEIGHT_MISSING (CATASTROPHIC).
      BB_CP_ENV      - no internal gate.

    N/A redistribution: an indicator whose Stage-3b trace has na_redistribute=True
    (sigma absent+device-limit; correction-age absent; fix-hold absent) is DROPPED
    from its block and the remaining indicator weights are renormalised to sum 1.0.

  Phase 2 (cross-point): aggregate each block across CHECK_POINT-role points with
    the spec aggregator  mean - k x (100 - min),  k from options.aggregator_k,
    clamped to [0, 100].

effective_check_point_count is recomputed AUTHORITATIVELY here as the count of
CHECK_POINT-role points with per_point_score > 0 (spec L2D_CP_016 formula),
overriding the Stage-3a provisional, and drives the FLG_CP_004 escalation.

Per-point completeness "kill" state (cp_fix_type_score=0 OR cp_position_sigma_score=0)
is tracked per point for the Stage-3d global gate. FLG_CP_002 NO_DESIGNATED_CHECK_POINTS
(null_handler) fires here when there is no CHECK_POINT-role point.

Weight-sum audit (indicator weights within a block must sum to 1.0) is surfaced
per block. No timestamps in the data block (determinism rule 3).
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

STAGE = "stage3c_blocks"

WEIGHT_SUM_TOLERANCE = 1e-6
SCORE_FLOOR = 0.0
SCORE_CEIL = 100.0
IN_SCOPE_ROLE = "CHECK_POINT"
EXCLUDED_ROLE = "GCP"

# Per-block apex weight key (spec building_blocks).
BLOCK_WEIGHT_KEY = "weight_in_check_point_score"

# Indicators whose score==0 contribute to the per-point completeness "kill"
# (global-gate condition: cp_fix_type_score=0 OR cp_position_sigma_score=0).
KILL_INDICATORS = ("L3I_CP_002", "L3I_CP_001")

# FLG_CP_004 escalates from its spec severity to CATASTROPHIC below this count.
EFFECTIVE_CP_ESCALATION_THRESHOLD = 5


def _flag_record(spec_flag: dict, severity: str, condition_value: Any,
                 origin_block: str | None, origin_indicator: str | None,
                 point_id: str | None) -> dict:
    return {
        "flag_id": spec_flag["flag_id"],
        "flag_name": spec_flag["flag_name"],
        "severity": severity,
        "severity_spec": spec_flag["severity"],
        "raised_at_stage_spec": spec_flag["raised_at_stage"],
        "_origin_stage": "stage3c",
        "_origin_block": origin_block,
        "_origin_indicator": origin_indicator,
        "_origin_point": point_id,
        "condition_value": condition_value,
    }


def _compute_point_block(block: dict, feeders: list[dict], trace_by_id: dict) -> dict:
    """Weighted sum of one block's indicators for one point, with N/A weight
    redistribution and the per-point internal gate."""
    block_id = block["block_id"]
    contributions: list[dict[str, Any]] = []
    active: list[tuple[float, float]] = []
    na_dropped: list[str] = []
    gate_flag_id: str | None = None
    gate_value: Any = None
    gate_indicator: str | None = None

    for ind in feeders:
        trace = trace_by_id.get(ind["indicator_id"])
        w = float(ind["weight_in_block"])
        if trace is None:
            contributions.append({
                "indicator_id": ind["indicator_id"], "indicator_name": ind["indicator_name"],
                "weight_in_block": w, "score": None, "na_redistribute": False,
                "_note": "indicator trace missing - skipped",
            })
            na_dropped.append(ind["indicator_id"])
            continue
        score = trace["score"]
        is_na = bool(trace.get("na_redistribute")) or score is None
        contributions.append({
            "indicator_id": ind["indicator_id"], "indicator_name": ind["indicator_name"],
            "weight_in_block": w, "score": score, "na_redistribute": is_na,
            "gate_triggered": bool(trace.get("gate_triggered")),
        })
        if is_na:
            na_dropped.append(ind["indicator_id"])
            continue
        active.append((w, float(score)))
        if trace.get("gate_triggered") and block.get("has_internal_gate") == "TRUE":
            gate_flag_id = trace.get("gate_flag_id")
            gate_value = trace.get("input_values")
            gate_indicator = ind["indicator_id"]

    total_active_w = sum(w for w, _ in active)
    if total_active_w <= 0:
        weighted = None
    else:
        weighted = round(sum((w / total_active_w) * s for w, s in active), 1)
    block_score = 0.0 if gate_flag_id is not None else weighted

    return {
        "block_id": block_id,
        "score": block_score,
        "weighted_score_before_gate": weighted,
        "gate_triggered": gate_flag_id is not None,
        "gate_flag_id": gate_flag_id,
        "gate_indicator": gate_indicator,
        "gate_value": gate_value,
        "na_dropped_indicators": na_dropped,
        "active_weight_sum": round(total_active_w, 4),
        "contributions": contributions,
    }


def _aggregate(scores: list[float], k: float) -> dict:
    """Spec aggregator: mean - k x (100 - min), clamped to [0, 100]."""
    n = len(scores)
    mean = sum(scores) / n
    mn = min(scores)
    raw = mean - k * (100.0 - mn)
    clamped = max(SCORE_FLOOR, min(SCORE_CEIL, raw))
    return {
        "n_points": n,
        "per_point_scores": [round(s, 1) for s in scores],
        "mean": round(mean, 3),
        "min": round(mn, 3),
        "raw_aggregate": round(raw, 3),
        "aggregate_score": round(clamped, 1),
        "clamped": abs(clamped - raw) > 1e-9,
    }


def run(config: dict, project_root: Path, spec: dict, stage3b_data: dict) -> dict:
    options = config.get("options", {})
    k = float(options.get("aggregator_k", 0.25))
    flag_index = {f["flag_id"]: f for f in spec.get("flags", [])}
    blocks = spec.get("building_blocks", [])
    block_order = [b["block_id"] for b in blocks]
    apex_weight = {b["block_id"]: float(b[BLOCK_WEIGHT_KEY]) for b in blocks}
    feeders_by_block = {
        b["block_id"]: sorted(
            [ind for ind in spec["indicators"] if ind["building_block_id"] == b["block_id"]],
            key=lambda i: i["indicator_id"])
        for b in blocks
    }

    # ---- Phase 1a: per-point block scores (gates + N/A redistribution) ----
    per_point_records: list[dict] = []
    for p in stage3b_data.get("points", []):
        pid = p["point_id"]
        trace_by_id = {t["indicator_id"]: t for t in p["indicator_traces"].values()}
        point_blocks: dict[str, dict] = {}
        for block in blocks:
            point_blocks[block["block_id"]] = _compute_point_block(
                block, feeders_by_block[block["block_id"]], trace_by_id)

        # per_point_score = weighted apex of the 3 per-point block scores.
        pp_total = 0.0
        for bid in block_order:
            bs = point_blocks[bid]["score"]
            pp_total += apex_weight[bid] * (bs if bs is not None else 0.0)
        per_point_score = round(pp_total, 1)

        # completeness kill (global-gate condition): fix=0 OR sigma=0.
        sigma_score = trace_by_id.get("L3I_CP_001", {}).get("score")
        fix_score = trace_by_id.get("L3I_CP_002", {}).get("score")
        completeness_killed = (fix_score == 0) or (sigma_score == 0)

        per_point_records.append({
            "point_id": pid,
            "device_type": p.get("device_type"),
            "device_role": p.get("device_role"),
            "block_scores": dict(sorted(point_blocks.items())),
            "per_point_score": per_point_score,
            "completeness_killed": completeness_killed,
        })

    # ---- Phase 1b: effective_check_point_count (authoritative) + escalation ----
    cp_points = [r for r in per_point_records if r["device_role"] == IN_SCOPE_ROLE]
    excluded_points = [r["point_id"] for r in per_point_records if r["device_role"] == EXCLUDED_ROLE]
    effective_check_point_count = sum(1 for r in cp_points if r["per_point_score"] > 0)
    escalate_float = effective_check_point_count < EFFECTIVE_CP_ESCALATION_THRESHOLD

    # ---- Phase 1c: raise per-point internal-gate flags (with escalation) ----
    flags_raised_stage3c: list[dict] = []
    for r in cp_points:
        for bid, pb in r["block_scores"].items():
            if not pb["gate_triggered"]:
                continue
            fid = pb["gate_flag_id"]
            if fid not in flag_index:
                continue
            severity = flag_index[fid]["severity"]
            if fid == "FLG_CP_004" and escalate_float:
                severity = "CATASTROPHIC"
            flags_raised_stage3c.append(_flag_record(
                flag_index[fid], severity,
                {"gate_value": pb["gate_value"],
                 "effective_check_point_count": effective_check_point_count} if fid == "FLG_CP_004"
                else pb["gate_value"],
                bid, pb["gate_indicator"], r["point_id"]))

    # ---- null_handler: no CHECK_POINT-role points -> FLG_CP_002 ----
    notes: list[str] = []
    if not cp_points:
        if "FLG_CP_002" in flag_index:
            flags_raised_stage3c.append(_flag_record(
                flag_index["FLG_CP_002"], flag_index["FLG_CP_002"]["severity"],
                {"check_point_role_count": 0}, None, None, None))
        notes.append("No CHECK_POINT-role points -> FLG_CP_002 NO_DESIGNATED_CHECK_POINTS; "
                     "aggregate_score=null for all blocks; Stage 3d sets check_point_score=null.")

    # ---- Phase 2: cross-point aggregation across CHECK_POINT-role points ----
    aggregated_blocks: dict[str, dict] = {}
    audit_failures: list[str] = []
    for block in blocks:
        bid = block["block_id"]
        feeders = feeders_by_block[bid]
        weight_sum = sum(float(i["weight_in_block"]) for i in feeders)
        audit_ok = abs(weight_sum - 1.0) < WEIGHT_SUM_TOLERANCE
        if not audit_ok:
            audit_failures.append(bid)
        entry: dict[str, Any] = {
            "block_id": bid,
            "block_name": block["block_name"],
            "display_name": block["display_name"],
            "weight_in_check_point_score": apex_weight[bid],
            "aggregator_spec": block.get("aggregator"),
            "aggregator_k": k,
            "has_internal_gate": block.get("has_internal_gate"),
            "gate_condition_spec": block.get("gate_condition") or None,
            "gate_action_spec": block.get("gate_action") or None,
            "weight_sum_audit": {"computed": round(weight_sum, 6), "expected": 1.0, "ok": audit_ok},
        }
        if cp_points:
            scores = [r["block_scores"][bid]["score"] for r in cp_points]
            scores = [s if s is not None else 0.0 for s in scores]
            entry["aggregation"] = _aggregate(scores, k)
            entry["aggregate_score"] = entry["aggregation"]["aggregate_score"]
        else:
            entry["aggregation"] = None
            entry["aggregate_score"] = None
        aggregated_blocks[bid] = entry

    if any(a["aggregation"] and a["aggregation"]["clamped"] for a in aggregated_blocks.values()):
        notes.append("One or more block aggregates were clamped to [0,100]: the aggregator "
                     "mean-k*(100-min) goes negative when a point is gated to 0 and the mean is low.")
    na_any = sorted({iid for r in cp_points for pb in r["block_scores"].values()
                     for iid in pb["na_dropped_indicators"]})
    if na_any:
        notes.append(f"N/A weight redistribution applied for indicators: {na_any} "
                     "(dropped from their block; remaining weights renormalised to 1.0).")

    return {
        "per_point_blocks": per_point_records,
        "aggregated_blocks": dict(sorted(aggregated_blocks.items())),
        "flags_raised_stage3c": flags_raised_stage3c,
        "stage3c_notes": notes,
        "stage3c_meta": {
            "expected_block_count": spec["_meta"]["counts"]["building_blocks"],
            "produced_block_count": len(aggregated_blocks),
            "aggregator_k": k,
            "check_point_role_count": len(cp_points),
            "excluded_gcp_role_points": excluded_points,
            "effective_check_point_count": effective_check_point_count,
            "effective_count_escalation_threshold": EFFECTIVE_CP_ESCALATION_THRESHOLD,
            "float_severity_escalated": escalate_float,
            "completeness_killed_points": [r["point_id"] for r in cp_points if r["completeness_killed"]],
            "weight_sum_audit_failures": audit_failures,
            "blocks_with_per_point_gate": sorted({
                f["_origin_block"] for f in flags_raised_stage3c if f["_origin_block"]}),
            "aggregate_score_summary": {
                bid: b["aggregate_score"] for bid, b in sorted(aggregated_blocks.items())},
            "per_point_score_summary": {
                r["point_id"]: r["per_point_score"] for r in cp_points},
        },
    }


def print_summary(data: dict) -> None:
    mm = data["stage3c_meta"]
    print(f"  blocks: {mm['produced_block_count']}/{mm['expected_block_count']}  "
          f"CHECK_POINT-role points: {mm['check_point_role_count']}  k={mm['aggregator_k']}  "
          f"weight-sum audit failures: {len(mm['weight_sum_audit_failures'])}")
    print(f"  effective_check_point_count: {mm['effective_check_point_count']}  "
          f"(escalate FLG_CP_004 -> CATASTROPHIC: {mm['float_severity_escalated']})")
    print("  per-point block scores:")
    for r in data["per_point_blocks"]:
        scores = {bid.replace("BB_CP_", ""): bs["score"] for bid, bs in r["block_scores"].items()}
        gated = [bid.replace("BB_CP_", "") for bid, bs in r["block_scores"].items() if bs["gate_triggered"]]
        print(f"    - {r['point_id']} ({r['device_role']}): {scores} per_point={r['per_point_score']}"
              f"{'  GATED:' + ','.join(gated) if gated else ''}"
              f"{'  KILLED' if r['completeness_killed'] else ''}")
    print("  aggregated (cross-point) block scores:")
    for bid, b in data["aggregated_blocks"].items():
        agg = b["aggregation"]
        detail = (f"mean={agg['mean']} min={agg['min']} raw={agg['raw_aggregate']}"
                  f"{' (clamped)' if agg['clamped'] else ''}") if agg else "n/a"
        print(f"    - {bid} (w={b['weight_in_check_point_score']}): {b['aggregate_score']}   [{detail}]")
    print(f"  flags raised at Stage 3c: {len(data['flags_raised_stage3c'])}")
    for fl in data["flags_raised_stage3c"]:
        esc = "" if fl["severity"] == fl["severity_spec"] else f" (escalated from {fl['severity_spec']})"
        print(f"    FLAG  [{fl.get('_origin_point')}] {fl['flag_id']} {fl['flag_name']} ({fl['severity']}){esc}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check Point PPK Stage 3c building blocks")
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
    data = run(config, root, spec, data3b)

    out_path = root / config["outputs"]["stage3_building_blocks"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    print(f"Stage 3c building blocks -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
