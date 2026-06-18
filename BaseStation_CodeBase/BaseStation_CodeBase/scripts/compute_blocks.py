#!/usr/bin/env python3
"""Stage 3c — building-block rollups per spec sheet 06.

For each of the 3 blocks:
  - look up its indicators from spec.indicators (filtered by building_block_id)
  - audit that the sum of weights_in_block equals 1.0 (spec-self-consistency)
  - compute weighted sum of indicator scores (pre-gate)
  - apply the block's internal gate if any indicator was gate_triggered
    — and fire the spec-defined internal_gate flag here per template rule 4

Spec semantics applied:
  BB_BASE_COMPLETE — internal_gate fires when coverage_score gate trips
    (L3I_BASE_001 gate_triggered → block=0, FLAG: FLG_BASE_003 BASE_RINEX_FLIGHT_GAP)
  BB_BASE_SETUP    — internal_gate fires when antenna_height_documented gate trips
    (L3I_BASE_005 gate_triggered → block=0, FLAG: FLG_BASE_002 ANTENNA_HEIGHT_MISSING)
  BB_BASE_ENV      — no internal gate

BASE_CRITICAL_FAILURE (FLG_BASE_001) is raised_at_stage=global_gate per sheet
07; it fires at Stage 3d when the completeness block = 0 propagates upward.

This subsystem has no parallel non-scoring deliverable (drone's CAL_CONF
analog), so no 05b_*.json artifact is emitted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


WEIGHT_SUM_TOLERANCE = 1e-6


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


# Map block_id → flag_id raised when its internal gate trips.
_BLOCK_GATE_FLAG_ID = {
    "BB_BASE_COMPLETE": "FLG_BASE_003",   # BASE_RINEX_FLIGHT_GAP
    "BB_BASE_SETUP":    "FLG_BASE_002",   # ANTENNA_HEIGHT_MISSING
    # BB_BASE_ENV — no gate
}


def _flag_record(spec_flag: dict, condition_value: Any, origin_block: str,
                 origin_indicator: str) -> dict:
    return {
        "flag_id": spec_flag["flag_id"],
        "flag_name": spec_flag["flag_name"],
        "severity": spec_flag["severity"],
        "raised_at_stage_spec": spec_flag["raised_at_stage"],
        "_origin_stage": "stage3c",
        "_origin_block": origin_block,
        "_origin_indicator": origin_indicator,
        "condition_value": condition_value,
    }


def run(config: dict, project_root, spec: dict, stage3b_data: dict) -> dict:
    started_at = datetime.now(timezone.utc)
    traces = stage3b_data.get("indicator_traces", {})
    # Build lookup by indicator_id from the trace key suffix.
    trace_by_id = {t["indicator_id"]: t for t in traces.values()}

    flag_index = {f["flag_id"]: f for f in spec.get("flags", [])}

    block_scores: dict[str, dict] = {}
    flags_raised_stage3c: list[dict] = []

    for block in spec.get("building_blocks", []):
        block_id = block["block_id"]
        # Indicators that feed this block.
        feeders = [ind for ind in spec["indicators"]
                   if ind["building_block_id"] == block_id]
        feeders.sort(key=lambda i: i["indicator_id"])

        weight_sum = sum(float(i["weight_in_block"]) for i in feeders)

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
                    "score": None,
                    "contribution": 0.0,
                    "gate_triggered": False,
                    "_note": "indicator trace missing — skipped",
                })
                continue
            score = float(trace["score"])
            contribution = round(float(ind["weight_in_block"]) * score, 3)
            weighted_total += contribution
            contributions.append({
                "indicator_id": ind["indicator_id"],
                "indicator_name": ind["indicator_name"],
                "weight_in_block": ind["weight_in_block"],
                "score": score,
                "contribution": contribution,
                "gate_triggered": bool(trace.get("gate_triggered")),
            })
            if trace.get("gate_triggered") and block["has_internal_gate"] == "TRUE":
                gate_triggered_by = ind["indicator_id"]
                gate_triggered_value = trace.get("input_values")

        weighted_score = round(weighted_total, 1)

        if gate_triggered_by is not None:
            block_score = 0.0
            flag_id = _BLOCK_GATE_FLAG_ID.get(block_id)
            if flag_id and flag_id in flag_index:
                flags_raised_stage3c.append(
                    _flag_record(flag_index[flag_id], gate_triggered_value,
                                 block_id, gate_triggered_by)
                )
        else:
            block_score = weighted_score

        block_scores[block_id] = {
            "block_id": block_id,
            "block_name": block["block_name"],
            "display_name": block["display_name"],
            "weight_in_apex": block["weight_in_base_station_score"],
            "score": block_score,
            "weighted_score_before_gate": weighted_score,
            "has_internal_gate": block["has_internal_gate"],
            "gate_condition_spec": block.get("gate_condition") or None,
            "gate_action_spec": block.get("gate_action") or None,
            "gate_triggered": gate_triggered_by is not None,
            "gate_triggered_by_indicator": gate_triggered_by,
            "formula_spec": block["formula"],
            "weight_sum_audit": {
                "computed": round(weight_sum, 6),
                "expected": 1.0,
                "ok": abs(weight_sum - 1.0) < WEIGHT_SUM_TOLERANCE,
            },
            "contributions": contributions,
        }

    expected_block_count = spec["_meta"]["counts"]["building_blocks"]
    produced_block_count = len(block_scores)

    audit_failures = [
        bid for bid, b in block_scores.items()
        if not b["weight_sum_audit"]["ok"]
    ]

    finished_at = datetime.now(timezone.utc)
    return {
        "block_scores": dict(sorted(block_scores.items())),
        "flags_raised_stage3c": flags_raised_stage3c,
        "stage3c_meta": {
            "expected_block_count": expected_block_count,
            "produced_block_count": produced_block_count,
            "weight_sum_audit_failures": audit_failures,
            "blocks_with_gate_triggered": [
                bid for bid, b in block_scores.items() if b["gate_triggered"]
            ],
            "score_summary": {bid: b["score"] for bid, b in block_scores.items()},
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "wall_time_sec": round((finished_at - started_at).total_seconds(), 3),
        },
    }
