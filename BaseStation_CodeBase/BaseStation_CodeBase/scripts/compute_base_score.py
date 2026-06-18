#!/usr/bin/env python3
"""Stage 3d — apex score per spec.base_station_score.

  formula:    0.45*BB_BASE_COMPLETE + 0.35*BB_BASE_SETUP + 0.20*BB_BASE_ENV
  global gate: BB_BASE_COMPLETE == 0  →  base_station_score = 0,
               raises FLG_BASE_001 BASE_CRITICAL_FAILURE
  weights:    read at runtime from spec.base_station_score_blocks (NEVER hardcoded)

All flags from Stages 2, 3a, 3b, 3c, and any apex-stage flag (only
BASE_CRITICAL_FAILURE) are aggregated into all_flags_aggregated, each
retaining its `_origin_stage` tag. Stage 2's handoff_crossdoc candidates are
preserved separately as deferred items.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


def _flag_record(spec_flag: dict, condition_value: Any) -> dict:
    return {
        "flag_id": spec_flag["flag_id"],
        "flag_name": spec_flag["flag_name"],
        "severity": spec_flag["severity"],
        "raised_at_stage_spec": spec_flag["raised_at_stage"],
        "_origin_stage": "stage3d",
        "condition_value": condition_value,
    }


def _aggregate_flags(stage2_data, stage3a_data, stage3b_data, stage3c_data,
                     apex_stage_flags) -> tuple[list[dict], dict[str, int], dict[str, int]]:
    """Concatenate flags from all prior stages plus any apex-stage flag.
    Returns (flags, by_origin_stage_counts, by_severity_counts)."""
    all_flags: list[dict] = []

    def _tag_if_missing(flags, default_stage):
        for f in flags:
            f = dict(f)  # shallow copy so we don't mutate upstream
            f.setdefault("_origin_stage", default_stage)
            all_flags.append(f)

    _tag_if_missing(stage2_data.get("_flags_raised_stage2", []), "stage2_merge_or_parser")
    _tag_if_missing(stage3a_data.get("flags_raised_stage3a", []), "stage3a")
    _tag_if_missing(stage3b_data.get("flags_raised_stage3b", []), "stage3b")
    _tag_if_missing(stage3c_data.get("flags_raised_stage3c", []), "stage3c")
    _tag_if_missing(apex_stage_flags, "stage3d")

    by_origin: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for f in all_flags:
        by_origin[f["_origin_stage"]] = by_origin.get(f["_origin_stage"], 0) + 1
        sev = f.get("severity", "UNKNOWN")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    return all_flags, dict(sorted(by_origin.items())), dict(sorted(by_severity.items()))


def run(config: dict, project_root, spec: dict,
        stage2_data: dict, stage3a_data: dict, stage3b_data: dict, stage3c_data: dict) -> dict:
    started_at = datetime.now(timezone.utc)

    apex_spec = spec["base_station_score"]
    # Preserve spec-formula order (COMPLETE, SETUP, ENV) for contributions so the
    # decomposition reads left-to-right against the prose formula.
    spec_block_order = [b["block_id"] for b in spec["base_station_score_blocks"]]
    block_weights = {b["block_id"]: float(b["weight"]) for b in spec["base_station_score_blocks"]}
    block_scores = stage3c_data.get("block_scores", {})

    # ---- Weighted sum ----
    contributions: list[dict] = []
    weighted_total = 0.0
    for block_id in spec_block_order:
        weight = block_weights[block_id]
        block = block_scores.get(block_id, {})
        score = float(block.get("score", 0.0))
        contrib = round(weight * score, 3)
        weighted_total += contrib
        contributions.append({
            "block_id": block_id,
            "block_name": block.get("block_name"),
            "weight_in_apex": weight,
            "block_score": score,
            "contribution": contrib,
        })

    weighted_score_before_global_gate = round(weighted_total, 1)

    # ---- Global gate ----
    flag_index = {f["flag_id"]: f for f in spec.get("flags", [])}
    apex_stage_flags: list[dict] = []

    complete_score = float(block_scores.get("BB_BASE_COMPLETE", {}).get("score", 0.0))
    global_gate_triggered = complete_score == 0
    if global_gate_triggered:
        apex_stage_flags.append(
            _flag_record(flag_index["FLG_BASE_001"], {"BB_BASE_COMPLETE_score": complete_score})
        )
        base_station_score = 0.0
    else:
        base_station_score = weighted_score_before_global_gate

    # ---- Aggregate all flags ----
    all_flags_aggregated, by_origin, by_severity = _aggregate_flags(
        stage2_data, stage3a_data, stage3b_data, stage3c_data, apex_stage_flags
    )

    # ---- Apex-weight sum audit (must equal 1.0 per spec self-consistency) ----
    apex_weight_sum = sum(block_weights.values())
    weight_audit_ok = abs(apex_weight_sum - 1.0) < 1e-6

    finished_at = datetime.now(timezone.utc)

    return {
        "base_station_score": base_station_score,
        "apex_formula_spec": apex_spec["formula_expression"],
        "apex_weights_used": block_weights,
        "weighted_score_before_global_gate": weighted_score_before_global_gate,
        "contributions": contributions,
        "global_gate": {
            "triggered": global_gate_triggered,
            "condition_spec": apex_spec["global_gate_condition"],
            "action_spec": apex_spec["global_gate_action"],
            "block_score_observed": complete_score,
        },
        "all_flags_aggregated": all_flags_aggregated,
        "flags_by_origin_stage": by_origin,
        "flags_by_severity": by_severity,
        "_handoff_crossdoc_candidates": stage2_data.get("_handoff_crossdoc_candidates", []),
        "stage3d_meta": {
            "apex_score_id": apex_spec["score_id"],
            "apex_display_name": apex_spec["display_name"],
            "workflow": apex_spec["workflow"],
            "phase": apex_spec["phase"],
            "scope_note": apex_spec.get("scope_note"),
            "source_file_set": apex_spec.get("source_file_set"),
            "apex_weight_sum_audit": {
                "computed": round(apex_weight_sum, 6),
                "expected": 1.0,
                "ok": weight_audit_ok,
            },
            "total_flags_aggregated": len(all_flags_aggregated),
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "wall_time_sec": round((finished_at - started_at).total_seconds(), 3),
        },
    }
