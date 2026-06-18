#!/usr/bin/env python3
"""Stage 3d — Compute drone_score (apex) per spec sheet 08.

Reads outputs/05_building_blocks.json + spec, computes the weighted sum
using the PPK weights from spec.drone_score.weights (never hardcoded).
Applies the global gate: if image_capture_score == 0 → drone_score = 0
and FLG_017 DRONE_CRITICAL_FAILURE is raised at stage=global_gate.

Also aggregates ALL flags raised across Stages 2 / 3b / 3c / 3d into one
consolidated audit list, so a single output artifact carries the full
flag history of the run.

Formula (read at runtime from spec.drone_score.metadata.formula_expression):
  drone_score = 0.40*image_capture_score
              + 0.35*rover_gnss_score
              + 0.25*mission_execution_score
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _read_envelope(path: Path) -> dict:
    return json.loads(path.read_text())


def compute(spec: dict, blocks_envelope: dict, source_envelope: dict,
            indicators_envelope: dict, cal_envelope: dict) -> dict:
    drone_meta = spec["drone_score"]["metadata"]
    drone_weights = spec["drone_score"]["weights"]
    flags_spec = spec["flags"]

    block_scores = blocks_envelope["data"]["blocks"]

    # Compute weighted sum using spec weights
    weighted_sum = 0.0
    block_contributions = {}
    weight_total = 0.0
    img_capture_score = None
    for w in drone_weights:
        bid = w["block_id"]
        weight = float(w["weight_in_ppk"])
        weight_total += weight
        b = block_scores.get(bid)
        if b is None:
            block_contributions[bid] = {
                "weight_in_ppk": weight,
                "block_score": None,
                "contribution": None,
                "notes": w.get("notes"),
                "error": "block not present in stage3c output",
            }
            continue
        score = b["score"]
        contribution = round(weight * float(score), 4)
        weighted_sum += weight * float(score)
        block_contributions[bid] = {
            "weight_in_ppk": weight,
            "block_score": score,
            "contribution": contribution,
            "notes": w.get("notes"),
            "block_display_name": b.get("display_name"),
            "block_gate_triggered": b.get("gate_triggered", False),
        }
        if bid == "BB_IMG_CAPTURE":
            img_capture_score = score

    raw_score = weighted_sum
    final_score = raw_score
    global_gate_triggered = False
    flags_raised_stage3d = []

    # Global gate per spec _meta.critical_gates.drone_score_global_gate:
    #   if image_capture_score == 0 -> drone_score = 0 (FLG_017 DRONE_CRITICAL_FAILURE)
    if img_capture_score is not None and img_capture_score == 0:
        global_gate_triggered = True
        final_score = 0.0
        flag_def = next((f for f in flags_spec if f["flag_id"] == "FLG_017"), None)
        if flag_def:
            flags_raised_stage3d.append({
                "flag_id": flag_def["flag_id"],
                "flag_name": flag_def["flag_name"],
                "severity": flag_def["severity"],
                "stage": flag_def["raised_at_stage"],
                "raised_by": flag_def.get("raised_by_id"),
                "context": (
                    f"image_capture_score=0 (BB_IMG_CAPTURE internal gate was tripped) → "
                    f"global gate forces drone_score=0; all downstream processing stops"
                ),
            })

    # Aggregate flags across all stages
    all_flags = []
    # Stage 2 (ingestion)
    for f in source_envelope["data"].get("_flags_raised_stage2", []):
        all_flags.append({**f, "_origin_stage": "stage2_source_fields"})
    # Stage 3b (indicators)
    for f in indicators_envelope["data"].get("flags_raised_stage3b", []):
        all_flags.append({**f, "_origin_stage": "stage3b_indicators"})
    # Stage 3c (blocks)
    for f in blocks_envelope["data"].get("flags_raised_stage3c", []):
        all_flags.append({**f, "_origin_stage": "stage3c_building_blocks"})
    # Stage 3d (this stage)
    for f in flags_raised_stage3d:
        all_flags.append({**f, "_origin_stage": "stage3d_drone_score"})

    # Group by severity for a quick stakeholder summary
    by_severity = {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}
    for f in all_flags:
        by_severity.setdefault(f["severity"], []).append(f["flag_name"])
    severity_summary = {k: v for k, v in by_severity.items() if v}

    return {
        "drone_score": round(final_score, 1),
        "raw_weighted_sum": round(raw_score, 4),
        "weight_total": round(weight_total, 4),
        "global_gate_triggered": global_gate_triggered,
        "global_gate_condition": drone_meta.get("global_gate_condition"),
        "global_gate_action": drone_meta.get("global_gate_action"),
        "formula_expression": drone_meta.get("formula_expression"),
        "workflow": drone_meta.get("workflow"),
        "block_contributions": block_contributions,
        "cal_conf_parallel": {
            "score": cal_envelope["data"]["cal_conf"]["score"],
            "weight_in_drone_score_ppk": 0.0,
            "note": (
                "CAL_CONF is a PARALLEL deliverable — does NOT feed drone_score. "
                "Travels with the camera calibration file to the Processing Universe (ODM)."
            ),
        },
        "flags_raised_stage3d": flags_raised_stage3d,
        "all_flags_aggregated": all_flags,
        "all_flags_count": len(all_flags),
        "all_flags_by_severity": severity_summary,
        "drone_score_scope_note": drone_meta.get("scope_note"),
        "source_file_set": drone_meta.get("source_file_set"),
    }


def run(config: dict, project_root: Path) -> dict:
    spec_path = project_root / config["spec_file"]
    blocks_path = project_root / config["outputs"]["stage3_building_blocks"]
    src_path = project_root / config["outputs"]["stage2_source_fields"]
    ind_path = project_root / config["outputs"]["stage3_indicators"]
    cal_path = project_root / config["outputs"]["stage3_cal_conf"]

    spec = json.loads(spec_path.read_text())
    blocks = _read_envelope(blocks_path)
    source = _read_envelope(src_path)
    indicators = _read_envelope(ind_path)
    cal = _read_envelope(cal_path)

    result = compute(spec, blocks, source, indicators, cal)

    envelope = {
        "spec_version": config.get("spec_version"),
        "config_used": config,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": "stage3d_drone_score",
        "data": result,
    }
    return envelope


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: compute_drone_score.py <paths.json>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())
    envelope = run(config, project_root)

    out_path = project_root / config["outputs"]["stage3_drone_score"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(envelope, indent=2, sort_keys=True, default=str) + "\n")

    d = envelope["data"]
    print(f"compute_drone_score: wrote {out_path}")
    print()
    print(f"  formula: {d['formula_expression']}")
    print()
    print(f"  Block contributions:")
    for bid, c in d["block_contributions"].items():
        gate = "  [GATE]" if c.get("block_gate_triggered") else ""
        print(f"    {bid:18s} weight={c['weight_in_ppk']:>5}  score={c['block_score']:>6}  contribution={c['contribution']:>7}{gate}")
    print()
    print(f"  raw_weighted_sum:       {d['raw_weighted_sum']}")
    print(f"  global_gate_triggered:  {d['global_gate_triggered']}")
    print(f"  ┌────────────────────────────────────────┐")
    print(f"  │  DRONE_SCORE = {d['drone_score']:>5}                   │")
    print(f"  └────────────────────────────────────────┘")
    print()
    cal = d["cal_conf_parallel"]
    print(f"  CAL_CONF (parallel deliverable): {cal['score']}  (weight in drone_score = {cal['weight_in_drone_score_ppk']})")
    print()
    print(f"  Flags aggregated across all stages: {d['all_flags_count']}")
    if d["all_flags_by_severity"]:
        for sev, names in d["all_flags_by_severity"].items():
            print(f"    {sev}: {', '.join(names)}")
    for f in d["all_flags_aggregated"]:
        print(f"    [{f['flag_id']}] {f['flag_name']} ({f['severity']}, raised at {f['_origin_stage']})")
        print(f"        {f.get('context', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
