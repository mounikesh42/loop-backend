#!/usr/bin/env python3
"""Stage 3c — Roll up building blocks per spec sheets 06 (building_blocks)
and 07 (block_composition).

Reads indicators from outputs/04_indicators.json and the spec, computes
each block's weighted score per its formula_expression / block_composition
weights, applies the BB_IMG_CAPTURE internal gate, and emits two artifacts:

  outputs/05_building_blocks.json  — the 3 blocks that feed drone_score
                                     (BB_IMG_CAPTURE, BB_ROVER_GNSS,
                                      BB_MISSION_EXEC)
  outputs/05b_cal_conf.json        — BB_CAL_CONF, the parallel deliverable
                                     with weight_in_drone_score_ppk = 0.0
                                     (travels with the calibration file
                                     to the Processing Universe / ODM)

Internal gate (per spec _meta.critical_gates):
  if image_validity_score < 30 -> image_capture_score = 0
  Raises FLG_001 CRITICAL_IMAGE_FAILURE (raised_at_stage = internal_gate).
  The downstream consequence (drone_score = 0) is applied at Stage 3d via
  FLG_017 DRONE_CRITICAL_FAILURE (raised_at_stage = global_gate).

Determinism: block scores rounded to 1 decimal per build spec rule 3.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


# Indicator scores feeding drone_score (these blocks contribute to drone_score)
DRONE_SCORE_BLOCKS = {"BB_IMG_CAPTURE", "BB_ROVER_GNSS", "BB_MISSION_EXEC"}
CAL_CONF_BLOCK = "BB_CAL_CONF"

# Internal gate config — derived from spec but kept here for clarity.
# When BB_IMG_CAPTURE's underlying L3I_IMG_001 (image_validity_score) is
# under 30, the block's final score is forced to 0.
GATE_INDICATOR_BY_BLOCK = {
    "BB_IMG_CAPTURE": ("L3I_IMG_001", 30, "FLG_001"),
}


def _compute_block(block_spec: dict, composition: list, indicator_scores: dict,
                   spec_flags: list) -> dict:
    """Compute one block's weighted score with gate handling.

    composition: list of {block_id, indicator_id, weight} for THIS block.
    Returns dict with score (rounded 1dp), raw_weighted_sum, contributions,
    gate info, and any flag raised by the gate.
    """
    block_id = block_spec["block_id"]
    contributions = {}
    weight_total = 0.0
    weighted_sum = 0.0
    missing_inputs = []

    for c in composition:
        iid = c["indicator_id"]
        w = float(c["weight"])
        s = indicator_scores.get(iid)
        if s is None:
            missing_inputs.append(iid)
            contrib = None
        else:
            contrib = round(w * float(s), 4)
            weighted_sum += w * float(s)
        weight_total += w
        contributions[iid] = {
            "weight": w,
            "indicator_score": s,
            "contribution": contrib,
        }

    raw_score = weighted_sum  # weighted_sum already in 0..100 if weights sum to 1
    final_score = raw_score
    gate_triggered = False
    flags_raised = []

    # Apply internal gate if one is defined for this block
    if block_id in GATE_INDICATOR_BY_BLOCK:
        gate_iid, gate_threshold, gate_flag_id = GATE_INDICATOR_BY_BLOCK[block_id]
        gate_input = indicator_scores.get(gate_iid)
        if gate_input is not None and gate_input < gate_threshold:
            gate_triggered = True
            final_score = 0.0
            # Fire the gate flag (raised_at_stage = internal_gate)
            flag_def = next((f for f in spec_flags if f["flag_id"] == gate_flag_id), None)
            if flag_def:
                flags_raised.append({
                    "flag_id": flag_def["flag_id"],
                    "flag_name": flag_def["flag_name"],
                    "severity": flag_def["severity"],
                    "stage": flag_def["raised_at_stage"],
                    "raised_by": flag_def.get("raised_by_id"),
                    "context": (
                        f"block_id={block_id}: {gate_iid}={gate_input} < gate threshold "
                        f"{gate_threshold} -> {block_spec['block_name']} forced to 0"
                    ),
                })

    return {
        "block_id": block_id,
        "block_name": block_spec["block_name"],
        "display_name": block_spec.get("display_name"),
        "purpose": block_spec.get("purpose"),
        "weight_in_drone_score_ppk": block_spec.get("weight_in_drone_score_ppk"),
        "formula_expression": block_spec.get("formula_expression"),
        "has_internal_gate": bool(block_spec.get("has_internal_gate")),
        "gate_condition": block_spec.get("gate_condition"),
        "gate_triggered": gate_triggered,
        "score": round(final_score, 1),
        "raw_weighted_sum": round(raw_score, 4),
        "weight_total": round(weight_total, 4),
        "weight_total_warning": None if abs(weight_total - 1.0) < 1e-6 else f"weights sum to {weight_total}, expected 1.0",
        "indicator_contributions": contributions,
        "missing_inputs": missing_inputs,
        "flags_raised": flags_raised,
    }


def compute(spec: dict, indicators_envelope: dict) -> dict:
    indicators_spec = spec["indicators"]
    blocks_spec = spec["building_blocks"]
    composition_spec = spec["block_composition"]
    flags_spec = spec["flags"]

    # Indicator scores by indicator_id (from Stage 3b output)
    indicator_scores = indicators_envelope["data"]["indicator_scores"]

    by_block = {}
    for c in composition_spec:
        by_block.setdefault(c["block_id"], []).append(c)

    drone_blocks = {}
    cal_conf_block = None
    flags_raised_all = []

    for b in blocks_spec:
        comp = by_block.get(b["block_id"], [])
        result = _compute_block(b, comp, indicator_scores, flags_spec)
        if b["block_id"] in DRONE_SCORE_BLOCKS:
            drone_blocks[b["block_id"]] = result
        elif b["block_id"] == CAL_CONF_BLOCK:
            cal_conf_block = result
        flags_raised_all.extend(result["flags_raised"])

    return {
        "drone_score_blocks": drone_blocks,
        "cal_conf_block": cal_conf_block,
        "flags_raised_stage3c": flags_raised_all,
    }


def run(config: dict, project_root: Path) -> tuple[dict, dict]:
    spec_path = project_root / config["spec_file"]
    ind_path = project_root / config["outputs"]["stage3_indicators"]
    spec = json.loads(spec_path.read_text())
    ind_envelope = json.loads(ind_path.read_text())
    result = compute(spec, ind_envelope)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    config_used = config

    drone_envelope = {
        "spec_version": config.get("spec_version"),
        "config_used": config_used,
        "generated_at": now_iso,
        "stage": "stage3c_building_blocks",
        "data": {
            "blocks": result["drone_score_blocks"],
            "flags_raised_stage3c": result["flags_raised_stage3c"],
        },
    }

    cal_envelope = {
        "spec_version": config.get("spec_version"),
        "config_used": config_used,
        "generated_at": now_iso,
        "stage": "stage3c_cal_conf",
        "data": {
            "cal_conf": result["cal_conf_block"],
            "cal_conf_note": spec["_meta"].get("cal_conf_note"),
        },
    }
    return drone_envelope, cal_envelope


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: compute_blocks.py <paths.json>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())

    drone_envelope, cal_envelope = run(config, project_root)

    drone_out = project_root / config["outputs"]["stage3_building_blocks"]
    cal_out = project_root / config["outputs"]["stage3_cal_conf"]
    drone_out.parent.mkdir(parents=True, exist_ok=True)
    drone_out.write_text(json.dumps(drone_envelope, indent=2, sort_keys=True, default=str) + "\n")
    cal_out.write_text(json.dumps(cal_envelope, indent=2, sort_keys=True, default=str) + "\n")

    print(f"compute_blocks: wrote {drone_out}")
    print(f"               wrote {cal_out}")
    print()
    print(f"{'block':18s} {'weight':>7s} {'score':>7s}  gate  notes")
    print("-" * 90)
    for bid in ("BB_IMG_CAPTURE", "BB_ROVER_GNSS", "BB_MISSION_EXEC"):
        b = drone_envelope["data"]["blocks"][bid]
        gate = "TRIPPED" if b["gate_triggered"] else "ok"
        warn = b["weight_total_warning"] or ""
        print(f"{bid:18s} {b['weight_in_drone_score_ppk']:>7} {b['score']:>7}  {gate:7s}  {warn}")
    c = cal_envelope["data"]["cal_conf"]
    print(f"{'BB_CAL_CONF':18s} {c['weight_in_drone_score_ppk']:>7} {c['score']:>7}  parallel deliverable (does not feed drone_score)")
    print()
    flags = drone_envelope["data"]["flags_raised_stage3c"]
    print(f"Total flags raised at Stage 3c: {len(flags)}")
    for f in flags:
        print(f"  [{f['flag_id']}] {f['flag_name']} ({f['severity']}, {f['stage']}): {f['context']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
