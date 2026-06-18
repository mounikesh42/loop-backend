#!/usr/bin/env python3
"""Stage 3c - building-block rollups + cross-point aggregation (spec sheet 06/07).

Two phases:

  Phase 1 (per point): for each of the 3 blocks, weighted-sum its indicator
    scores from Stage 3b, then apply the block's per-point internal gate:
      BB_GCP_COMPLETE - L3I_GCP_001 coverage gate trips -> per_point
        completeness = 0, FLAG FLG_GCP_003 GCP_POINT_FLIGHT_GAP (CRITICAL).
      BB_GCP_SETUP    - L3I_GCP_005 height gate trips -> per_point setup
        confidence = 0, FLAG FLG_GCP_002 GCP_POINT_ANTENNA_HEIGHT_MISSING (CRITICAL).
      BB_GCP_ENV      - no internal gate.

  Phase 2 (cross-point): aggregate each block across the GCP-role points with
    the spec aggregator  mean - k x (100 - min),  k from options.aggregator_k.
    The result is clamped to [0, 100] (the formula can go negative when a point
    is gated to 0 and the mean is low). CHECK_POINT-role points are scored
    per-point but excluded from the GCP-score aggregation.

Flag stages (template rule 4): the two per-point internal_gate flags fire HERE.
The survey-level global gate FLG_GCP_001 GCP_CRITICAL_FAILURE
("every GCP-role point has occupation_coverage_score = 0") defers to Stage 3d.
FLG_GCP_012 NO_DESIGNATED_GCPS (null_handler) fires here when the survey has no
GCP-role point, so Stage 3d can set gcp_score = null.

Weight-sum audit (indicator weights within a block must sum to 1.0) is the
canonical home of this check; surfaced per block in aggregated_blocks and in
stage3c_meta. No timestamps live in the data block (determinism rule 3).
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

# block_id -> internal_gate flag fired when its per-point gate trips.
_BLOCK_GATE_FLAG_ID = {
    "BB_GCP_COMPLETE": "FLG_GCP_003",   # GCP_POINT_FLIGHT_GAP
    "BB_GCP_SETUP": "FLG_GCP_002",      # GCP_POINT_ANTENNA_HEIGHT_MISSING
    # BB_GCP_ENV - no gate
}


def _flag_record(spec_flag: dict, condition_value: Any, origin_block: str | None,
                 origin_indicator: str | None, point_id: str | None) -> dict:
    return {
        "flag_id": spec_flag["flag_id"],
        "flag_name": spec_flag["flag_name"],
        "severity": spec_flag["severity"],
        "raised_at_stage_spec": spec_flag["raised_at_stage"],
        "_origin_stage": "stage3c",
        "_origin_block": origin_block,
        "_origin_indicator": origin_indicator,
        "_origin_point": point_id,
        "condition_value": condition_value,
    }


def _compute_point_block(block: dict, feeders: list[dict], trace_by_id: dict) -> dict:
    """Weighted sum of one block's indicators for one point, with per-point gate."""
    block_id = block["block_id"]
    contributions: list[dict[str, Any]] = []
    weighted_total = 0.0
    gate_triggered_by: str | None = None
    gate_triggered_value: Any = None

    for ind in feeders:
        trace = trace_by_id.get(ind["indicator_id"])
        if trace is None:
            contributions.append({
                "indicator_id": ind["indicator_id"],
                "indicator_name": ind["indicator_name"],
                "weight_in_block": ind["weight_in_block"],
                "score": None, "contribution": 0.0, "gate_triggered": False,
                "_note": "indicator trace missing - skipped",
            })
            continue
        score = float(trace["score"])
        contribution = round(float(ind["weight_in_block"]) * score, 3)
        weighted_total += contribution
        contributions.append({
            "indicator_id": ind["indicator_id"],
            "indicator_name": ind["indicator_name"],
            "weight_in_block": ind["weight_in_block"],
            "score": score, "contribution": contribution,
            "gate_triggered": bool(trace.get("gate_triggered")),
        })
        if trace.get("gate_triggered") and block.get("has_internal_gate") == "TRUE":
            gate_triggered_by = ind["indicator_id"]
            gate_triggered_value = trace.get("input_values")

    weighted_score = round(weighted_total, 1)
    block_score = 0.0 if gate_triggered_by is not None else weighted_score
    return {
        "block_id": block_id,
        "score": block_score,
        "weighted_score_before_gate": weighted_score,
        "gate_triggered": gate_triggered_by is not None,
        "gate_triggered_by_indicator": gate_triggered_by,
        "gate_triggered_value": gate_triggered_value,
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
    feeders_by_block = {
        b["block_id"]: sorted(
            [ind for ind in spec["indicators"] if ind["building_block_id"] == b["block_id"]],
            key=lambda i: i["indicator_id"],
        )
        for b in blocks
    }

    flags_raised_stage3c: list[dict] = []
    per_point_records: list[dict] = []

    # ---- Phase 1: per-point block scores + per-point internal gates ----
    for p in stage3b_data.get("points", []):
        pid = p["point_id"]
        trace_by_id = {t["indicator_id"]: t for t in p["indicator_traces"].values()}
        point_blocks: dict[str, dict] = {}
        for block in blocks:
            pb = _compute_point_block(block, feeders_by_block[block["block_id"]], trace_by_id)
            if pb["gate_triggered"]:
                flag_id = _BLOCK_GATE_FLAG_ID.get(block["block_id"])
                if flag_id and flag_id in flag_index:
                    flags_raised_stage3c.append(_flag_record(
                        flag_index[flag_id], pb["gate_triggered_value"],
                        block["block_id"], pb["gate_triggered_by_indicator"], pid))
            point_blocks[block["block_id"]] = pb
        per_point_records.append({
            "point_id": pid,
            "device_type": p.get("device_type"),
            "device_role": p.get("device_role"),
            "block_scores": dict(sorted(point_blocks.items())),
        })

    # ---- Phase 2: cross-point aggregation across GCP-role points ----
    gcp_points = [r for r in per_point_records if r["device_role"] == "GCP"]
    check_points = [r["point_id"] for r in per_point_records if r["device_role"] == "CHECK_POINT"]

    aggregated_blocks: dict[str, dict] = {}
    audit_failures: list[str] = []
    for block in blocks:
        block_id = block["block_id"]
        feeders = feeders_by_block[block_id]
        weight_sum = sum(float(i["weight_in_block"]) for i in feeders)
        audit_ok = abs(weight_sum - 1.0) < WEIGHT_SUM_TOLERANCE
        if not audit_ok:
            audit_failures.append(block_id)

        entry: dict[str, Any] = {
            "block_id": block_id,
            "block_name": block["block_name"],
            "display_name": block["display_name"],
            "weight_in_gcp_score": block["weight_in_gcp_score"],
            "aggregator_spec": block.get("aggregator"),
            "aggregator_k": k,
            "has_internal_gate": block.get("has_internal_gate"),
            "gate_condition_spec": block.get("gate_condition") or None,
            "gate_action_spec": block.get("gate_action") or None,
            "weight_sum_audit": {
                "computed": round(weight_sum, 6), "expected": 1.0, "ok": audit_ok},
        }
        if gcp_points:
            scores = [r["block_scores"][block_id]["score"] for r in gcp_points]
            entry["aggregation"] = _aggregate(scores, k)
            entry["aggregate_score"] = entry["aggregation"]["aggregate_score"]
        else:
            entry["aggregation"] = None
            entry["aggregate_score"] = None
        aggregated_blocks[block_id] = entry

    notes: list[str] = []
    if not gcp_points:
        flags_raised_stage3c.append(_flag_record(
            flag_index["FLG_GCP_012"], {"gcp_role_point_count": 0}, None, None, None))
        notes.append("No GCP-role points in survey -> FLG_GCP_012 NO_DESIGNATED_GCPS; "
                     "aggregate_score=null for all blocks; Stage 3d sets gcp_score=null.")
    if any(a["aggregation"] and a["aggregation"]["clamped"] for a in aggregated_blocks.values()):
        notes.append("One or more block aggregates were clamped to the [0,100] floor: the "
                     "aggregator mean-k*(100-min) goes negative when a point is gated to 0 "
                     "and the mean is low.")

    return {
        "per_point_blocks": per_point_records,
        "aggregated_blocks": dict(sorted(aggregated_blocks.items())),
        "flags_raised_stage3c": flags_raised_stage3c,
        "stage3c_notes": notes,
        "stage3c_meta": {
            "expected_block_count": spec["_meta"]["counts"]["building_blocks"],
            "produced_block_count": len(aggregated_blocks),
            "aggregator_k": k,
            "gcp_role_point_count": len(gcp_points),
            "check_point_role_points": check_points,
            "weight_sum_audit_failures": audit_failures,
            "blocks_with_per_point_gate": sorted({
                f["_origin_block"] for f in flags_raised_stage3c if f["_origin_block"]}),
            "aggregate_score_summary": {
                bid: b["aggregate_score"] for bid, b in sorted(aggregated_blocks.items())},
        },
    }


def print_summary(data: dict) -> None:
    mm = data["stage3c_meta"]
    print(f"  blocks: {mm['produced_block_count']}/{mm['expected_block_count']}  "
          f"GCP-role points: {mm['gcp_role_point_count']}  k={mm['aggregator_k']}  "
          f"weight-sum audit failures: {len(mm['weight_sum_audit_failures'])}")
    print("  per-point block scores:")
    for r in data["per_point_blocks"]:
        scores = {bid.replace("BB_GCP_", ""): bs["score"]
                  for bid, bs in r["block_scores"].items()}
        gated = [bid.replace("BB_GCP_", "") for bid, bs in r["block_scores"].items()
                 if bs["gate_triggered"]]
        print(f"    - {r['point_id']} ({r['device_role']}): {scores}"
              f"{'  GATED:' + ','.join(gated) if gated else ''}")
    print("  aggregated (cross-point) block scores:")
    for bid, b in data["aggregated_blocks"].items():
        agg = b["aggregation"]
        detail = (f"mean={agg['mean']} min={agg['min']} raw={agg['raw_aggregate']}"
                  f"{' (clamped)' if agg['clamped'] else ''}") if agg else "n/a"
        print(f"    - {bid} (w={b['weight_in_gcp_score']}): {b['aggregate_score']}   [{detail}]")
    print(f"  flags raised at Stage 3c: {len(data['flags_raised_stage3c'])}")
    for fl in data["flags_raised_stage3c"]:
        print(f"    FLAG  [{fl.get('_origin_point')}] {fl['flag_id']} {fl['flag_name']} ({fl['severity']})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GCP PPK Stage 3c building blocks")
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
