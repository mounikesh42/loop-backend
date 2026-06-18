#!/usr/bin/env python3
"""Stage 3c - building-block rollups + per-deliverable views for Processing.

Two deliverables:

  (A) 4 apex blocks  -> outputs/05_building_blocks.json
      Weighted average of each block's Stage-3b indicator scores WITH N/A WEIGHT
      REDISTRIBUTION: any indicator whose trace has na_redistribute=True (path-N/A
      e.g. no-GCP, or evidence-tier unmet) is dropped and the remaining weights
      renormalise to 1.0. The DO block has the one internal gate: when L3I_PROC_031
      (output_crs_project_match) trips, deliverable_output_score = 0 (the global
      gate at 3d then forces processing_score = 0).

  (B) 5 per-deliverable views -> outputs/05b_per_deliverable_views.json (PARALLEL
      deliverable; does NOT feed the apex). Each view re-weights the SAME 3b
      indicator scores per its weight_map (same N/A redistribution) and returns
      null when its deliverable file is absent (required_input_field
      deliverable_<type>_present == False). The views carry NO explicit gate
      inheritance - the CRS-match indicator sits inside each weight_map, so a CRS
      mismatch lowers a view through that indicator scoring 0 (spec-literal).

flags_raised_stage3c is empty by design: the DO gate's flag is raised at 3d
(spec raised_at_stage=global_gate). No timestamps in the data block (rule 3).
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
WEIGHT_SUM_TOL = 1e-6
# indicator whose gate_triggered drives a block-internal gate -> its flag (raised at 3d)
GATE_FLAG_BY_INDICATOR = {"L3I_PROC_031": "PROC_OUTPUT_CRS_MISMATCH"}


def _rollup(items: list[tuple[float, Any, bool]]):
    """items: (weight, score, na). Drop na/None, renormalise to 1.0."""
    active = [(w, s) for (w, s, na) in items if not na and s is not None]
    dropped_w = sum(w for (w, s, na) in items if na or s is None)
    total_w = sum(w for w, _ in active)
    if total_w <= 0:
        return None, 0.0, round(dropped_w, 6)
    return round(sum((w / total_w) * s for w, s in active), 1), round(total_w, 6), round(dropped_w, 6)


def run(config, project_root, spec, stage3b_data, stage2_data) -> dict:
    traces = stage3b_data["indicator_traces"]
    by_id = traces
    by_name = {t["indicator_name"]: t for t in traces.values()}
    source = stage2_data["source_fields"]
    apex_weight = {b["block_id"]: float(b["weight"]) for b in spec["processing_score_blocks"]}

    feeders_by_block: dict[str, list[dict]] = {}
    for ind in spec["indicators"]:
        feeders_by_block.setdefault(ind["building_block_id"], []).append(ind)

    # ---- (A) apex blocks ----
    blocks: dict[str, dict] = {}
    audit_failures: list[str] = []
    block_meta = {b["block_id"]: b for b in spec["building_blocks"]}
    for b in spec["building_blocks"]:
        bid = b["block_id"]
        feeders = feeders_by_block.get(bid, [])
        items, contribs, na_dropped = [], [], []
        for ind in feeders:
            t = by_id.get(ind["indicator_id"], {})
            w = float(ind["weight_in_block"])
            na = bool(t.get("na_redistribute"))
            sc = t.get("score")
            items.append((w, sc, na))
            if na or sc is None:
                na_dropped.append(ind["indicator_id"])
            contribs.append({"indicator_id": ind["indicator_id"], "weight_in_block": w,
                             "score": sc, "na_redistribute": na})
        weighted, active_w, dropped_w = _rollup(items)

        gate_on = str(b.get("has_internal_gate")).upper() == "TRUE" and any(
            by_id.get(ind["indicator_id"], {}).get("gate_triggered") for ind in feeders)
        gate_flag = None
        if gate_on:
            for ind in feeders:
                if by_id.get(ind["indicator_id"], {}).get("gate_triggered"):
                    gate_flag = GATE_FLAG_BY_INDICATOR.get(ind["indicator_id"])
                    break
        block_score = 0.0 if gate_on else weighted

        wsum = sum(float(i["weight_in_block"]) for i in feeders)
        ok = abs(wsum - 1.0) < WEIGHT_SUM_TOL
        if not ok:
            audit_failures.append(bid)

        blocks[bid] = {
            "block_id": bid, "block_name": b["block_name"],
            "weight_in_apex": apex_weight.get(bid), "score": block_score,
            "weighted_before_gate": weighted,
            "active_weight_sum_after_redistribution": active_w,
            "na_redistributed_weight": dropped_w,
            "na_dropped_indicators": na_dropped,
            "has_internal_gate": str(b.get("has_internal_gate")).upper() == "TRUE",
            "gate_triggered": gate_on, "gate_flag_id": gate_flag,
            "gate_condition_spec": b.get("gate_condition") or None,
            "weight_sum_audit": {"computed": round(wsum, 6), "expected": 1.0, "ok": ok},
            "contributions": contribs,
        }

    # ---- (B) per-deliverable views (parallel 05b) ----
    # When the catastrophic output-CRS gate trips, every deliverable is in the
    # wrong frame -> null all views (reason output_crs_mismatch), mirroring the
    # file-missing null and the apex force-to-0. (The spec leaves view gate-
    # inheritance unstated; documented as a spec-clarification candidate.)
    crs_gate_tripped = any(by_id.get(iid, {}).get("gate_triggered") for iid in GATE_FLAG_BY_INDICATOR)
    views: dict[str, dict] = {}
    view_audit_failures: list[str] = []
    for v in spec["per_deliverable_views"]:
        wm = v["weight_map"]
        items, contribs, na_dropped = [], [], []
        for name, weight in wm:
            t = by_name.get(name, {})
            na = bool(t.get("na_redistribute"))
            sc = t.get("score")
            items.append((float(weight), sc, na))
            if na or sc is None:
                na_dropped.append(name)
            contribs.append({"indicator_name": name, "weight_in_view": float(weight),
                             "score": sc, "na_redistribute": na})
        weighted, active_w, dropped_w = _rollup(items)

        present = bool(source.get(v["required_input_field"]))
        if not present:
            view_score = None
            null_reason = v["null_when_missing_reason"]
        elif crs_gate_tripped:
            view_score = None
            null_reason = "output_crs_mismatch"
        else:
            view_score = weighted
            null_reason = None

        wsum = sum(float(w) for _, w in wm)
        ok = abs(wsum - 1.0) < WEIGHT_SUM_TOL
        if not ok:
            view_audit_failures.append(v["view_id"])

        views[v["view_id"]] = {
            "view_id": v["view_id"], "view_name": v["view_name"],
            "display_name": v["display_name"], "score": view_score,
            "weighted_before_null": weighted,
            "active_weight_sum_after_redistribution": active_w,
            "na_redistributed_weight": dropped_w, "na_dropped_indicators": na_dropped,
            "deliverable_present": present, "required_input_field": v["required_input_field"],
            "null_reason": null_reason, "indicator_count": len(wm),
            "weight_sum_audit": {"computed": round(wsum, 6), "expected": 1.0, "ok": ok},
            "contributions": contribs,
        }

    # apex preview (authoritative apex computed at 3d)
    apex_preview = None
    if all(blocks[b["block_id"]]["score"] is not None for b in spec["processing_score_blocks"]):
        apex_preview = round(sum(apex_weight[b["block_id"]] * blocks[b["block_id"]]["score"]
                                 for b in spec["processing_score_blocks"]), 1)

    return {
        "survey_level": True,
        "blocks": dict(sorted(blocks.items())),
        "per_deliverable_views": dict(sorted(views.items())),
        "flags_raised_stage3c": [],
        "stage3c_notes": [
            "Block score = weighted avg of 3b indicator scores with na_redistribute dropped + "
            "weights renormalised to 1.0; the DO internal gate (L3I_PROC_031) zeros the block.",
            "The DO gate's flag PROC_OUTPUT_CRS_MISMATCH is raised at 3d (raised_at_stage="
            "global_gate) -> flags_raised_stage3c empty by design.",
            "per_deliverable_views are a PARALLEL deliverable (05b): they re-weight the same 3b "
            "indicator scores per weight_map and return null when the deliverable file is absent; "
            "they do NOT feed the apex. No explicit gate inheritance (CRS indicator is in each map).",
        ],
        "stage3c_meta": {
            "block_count": len(blocks), "view_count": len(views),
            "weight_sum_audit_failures": audit_failures,
            "view_weight_sum_audit_failures": view_audit_failures,
            "block_score_summary": {bid: blocks[bid]["score"] for bid in sorted(blocks)},
            "view_score_summary": {vid: views[vid]["score"] for vid in sorted(views)},
            "apex_preview": apex_preview,
            "gate_triggered_blocks": [bid for bid in blocks if blocks[bid]["gate_triggered"]],
        },
    }


def print_summary(data):
    mm = data["stage3c_meta"]
    print(f"  blocks: {mm['block_count']}  views: {mm['view_count']}  "
          f"weight-audit failures: {len(mm['weight_sum_audit_failures'])+len(mm['view_weight_sum_audit_failures'])}  "
          f"apex_preview: {mm['apex_preview']}")
    for bid, b in data["blocks"].items():
        g = f" GATED:{b['gate_flag_id']}" if b["gate_triggered"] else ""
        print(f"    {bid:12s}(w={b['weight_in_apex']}) score={b['score']:<6} "
              f"[pre-gate={b['weighted_before_gate']} na_w={b['na_redistributed_weight']}]{g}")
    for vid, v in data["per_deliverable_views"].items():
        nr = f" NULL:{v['null_reason']}" if v["score"] is None else ""
        print(f"    {v['view_name']:20s} score={v['score']}  [na_dropped={len(v['na_dropped_indicators'])}]{nr}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Processing Stage 3c blocks + views")
    ap.add_argument("config")
    args = ap.parse_args(argv)
    config_path = Path(args.config).resolve()
    config = common.load_config(config_path)
    root = config_path.parent
    spec = common.load_spec(root, config)
    spec_version = spec["_meta"]["version"]

    env1, hard = stage1_inventory.run(config, root)
    if hard and config.get("options", {}).get("fail_fast", True):
        print("HALT: Stage 1 hard failure.")
        return 1
    data2 = stage2_merge.run(config, root, spec, env1["data"])
    data3a = stage3a_derived.run(config, root, spec, data2)
    data3b = stage3b_indicators.run(config, root, spec, data3a, data2)
    data = run(config, root, spec, data3b, data2)

    out_path = root / config["outputs"]["stage3_building_blocks"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    views_env = common.make_envelope(STAGE, {
        "per_deliverable_views": data["per_deliverable_views"],
        "stage3c_meta": {k: data["stage3c_meta"][k] for k in
                         ("view_count", "view_weight_sum_audit_failures", "view_score_summary")},
    }, config, spec_version)
    common.write_envelope(root / config["outputs"]["stage3_per_deliverable_views"], views_env)

    print(f"Stage 3c blocks -> {out_path.relative_to(root)}  "
          f"(+ {Path(config['outputs']['stage3_per_deliverable_views']).name})")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
