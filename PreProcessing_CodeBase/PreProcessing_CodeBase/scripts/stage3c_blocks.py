#!/usr/bin/env python3
"""Stage 3c - building-block rollups + per-artifact views for Pre-Processing.

Survey-level: NO cross-point aggregation (that lived inside the gcp_sigma /
cp_sigma indicators at Stage 3b). Two deliverables:

  (A) 4 apex blocks  -> outputs/05_building_blocks.json
      Weighted average of each block's indicators (Stage-3b scores) WITH N/A
      WEIGHT REDISTRIBUTION: any indicator whose trace has na_redistribute=True
      (path-N/A or report-absent) is dropped and the remaining indicator weights
      are renormalised to sum 1.0. Then the block's internal gate (REF: crs/
      projection; GCT: gcp path) zeros the block if its gate indicator tripped.
      The view_only CP indicators (block_id null) never enter a block.

  (B) 3 per-artifact views -> outputs/05b_per_artifact_views.json (parallel
      deliverable; does NOT feed the apex). Each view re-weights the SAME 3b
      indicator scores per its consumes_indicators list (same N/A redistribution),
      inherits the named global gates (zeroes the view if tripped), and the CP
      view is null when cp_designated_count = 0.

Per the spec flag table, PP has no internal_gate-staged flag: the block gates are
driven by the same indicators as the catastrophic GLOBAL gates (PP_WRONG_CRS_DATUM
/ PP_WRONG_PROJECTION at REF, PP_GCP_AUTONOMOUS_PATH at GCT), whose flags fire at
Stage 3d. So flags_raised_stage3c is empty by design. No timestamps in the data
block (determinism rule 3).
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

# gate flag name -> the indicator id whose gate_triggered drives it
GATE_FLAG_BY_INDICATOR = {
    "L3I_PP_001": "PP_WRONG_CRS_DATUM",
    "L3I_PP_004": "PP_WRONG_PROJECTION",
    "L3I_PP_022": "PP_GCP_AUTONOMOUS_PATH",
}


def _rollup(items: list[tuple[float, Any, bool]]) -> tuple[Any, list[str], float]:
    """items: (weight, score, na_redistribute). Drop na/None, renormalise to 1.0."""
    active = [(w, s) for (w, s, na) in items if not na and s is not None]
    dropped = [w for (w, s, na) in items if na or s is None]
    total_w = sum(w for w, _ in active)
    if total_w <= 0:
        return None, [], 0.0
    weighted = round(sum((w / total_w) * s for w, s in active), 1)
    return weighted, dropped, round(total_w, 6)


def _triggered_gate_flags(by_id: dict) -> set[str]:
    out = set()
    for iid, flag_name in GATE_FLAG_BY_INDICATOR.items():
        t = by_id.get(iid)
        if t and t.get("gate_triggered"):
            out.add(flag_name)
    return out


def run(config, project_root, spec, stage3b_data) -> dict:
    traces = stage3b_data.get("indicator_traces", {})
    by_id = {t["indicator_id"]: t for t in traces.values()}
    by_name = {t["indicator_name"]: t for t in traces.values()}
    blocks_spec = spec["building_blocks"]
    apex_weight = {b["block_id"]: float(b["weight"]) for b in spec["pre_processing_score_blocks"]}
    triggered = _triggered_gate_flags(by_id)

    feeders_by_block: dict[str, list[dict]] = {}
    for ind in spec["indicators"]:
        bid = ind["building_block_id"]
        if bid:
            feeders_by_block.setdefault(bid, []).append(ind)

    # ---- (A) apex blocks ----
    blocks: dict[str, dict] = {}
    audit_failures: list[str] = []
    for b in blocks_spec:
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
        weighted, _, active_w = _rollup(items)

        gate_triggered = b.get("has_internal_gate") == "TRUE" and any(
            by_id.get(ind["indicator_id"], {}).get("gate_triggered") for ind in feeders)
        gate_flag = None
        if gate_triggered:
            for ind in feeders:
                if by_id.get(ind["indicator_id"], {}).get("gate_triggered"):
                    gate_flag = by_id[ind["indicator_id"]].get("gate_flag_id")
                    break
        block_score = 0.0 if gate_triggered else weighted

        weight_sum = sum(float(i["weight_in_block"]) for i in feeders)
        audit_ok = abs(weight_sum - 1.0) < WEIGHT_SUM_TOLERANCE
        if not audit_ok:
            audit_failures.append(bid)

        blocks[bid] = {
            "block_id": bid,
            "block_name": b["block_name"],
            "weight_in_apex": apex_weight.get(bid),
            "score": block_score,
            "weighted_before_gate": weighted,
            "active_weight_sum_after_redistribution": active_w,
            "na_dropped_indicators": na_dropped,
            "has_internal_gate": b.get("has_internal_gate") == "TRUE",
            "gate_triggered": gate_triggered,
            "gate_flag_id": gate_flag,
            "gate_condition_spec": b.get("gate_condition") or None,
            "weight_sum_audit": {"computed": round(weight_sum, 6), "expected": 1.0, "ok": audit_ok},
            "contributions": contribs,
        }

    # ---- (B) per-artifact views (parallel deliverable) ----
    cp_count = (by_id.get("L3I_PP_036", {}).get("input_values", {}) or {}).get("cp_count")
    views: dict[str, dict] = {}
    view_audit_failures: list[str] = []
    for v in spec["per_artifact_views"]:
        consumes = v["consumes_indicators"]
        items, contribs, na_dropped = [], [], []
        for name, weight in consumes:
            t = by_name.get(name, {})
            na = bool(t.get("na_redistribute"))
            sc = t.get("score")
            items.append((float(weight), sc, na))
            if na or sc is None:
                na_dropped.append(name)
            contribs.append({"indicator_name": name, "weight_in_view": float(weight),
                             "score": sc, "na_redistribute": na})
        weighted, _, active_w = _rollup(items)

        inherited = [g for g in v.get("global_gates", []) if g in triggered]
        is_cp_view = v["view_id"] == "VIEW_PP_CP_COORD"
        null_reason = None
        if is_cp_view and (cp_count == 0):
            view_score = None
            null_reason = "cp_designated_count == 0 -> UNVERIFIED_NO_CPS (score unaffected)"
        elif inherited:
            view_score = 0.0
        else:
            view_score = weighted

        wsum = sum(float(w) for _, w in consumes)
        ok = abs(wsum - 1.0) < WEIGHT_SUM_TOLERANCE
        if not ok:
            view_audit_failures.append(v["view_id"])

        views[v["view_id"]] = {
            "view_id": v["view_id"],
            "view_name": v["view_name"],
            "display_name": v["display_name"],
            "score": view_score,
            "weighted_before_gate": weighted,
            "active_weight_sum_after_redistribution": active_w,
            "inherited_gates": v.get("global_gates", []),
            "inherited_gates_triggered": inherited,
            "na_dropped_indicators": na_dropped,
            "null_reason": null_reason,
            "weight_sum_audit": {"computed": round(wsum, 6), "expected": 1.0, "ok": ok},
            "indicator_count": len(consumes),
            "contributions": contribs,
        }

    return {
        "survey_level": True,
        "blocks": dict(sorted(blocks.items())),
        "per_artifact_views": dict(sorted(views.items())),
        "flags_raised_stage3c": [],
        "stage3c_notes": [
            "Block score = weighted avg of indicator scores with na_redistribute dropped + "
            "weights renormalised to 1.0; internal gate (REF/GCT) zeros the block.",
            "PP has no internal_gate-staged flag: block gates share the catastrophic GLOBAL "
            "gate indicators (flags fire at 3d) -> flags_raised_stage3c empty by design.",
            "per_artifact_views are a PARALLEL deliverable (05b): they re-weight the same 3b "
            "indicator scores, inherit named global gates, and the CP view is null at cp_count=0; "
            "they do NOT feed the apex.",
        ],
        "stage3c_meta": {
            "block_count": len(blocks),
            "view_count": len(views),
            "weight_sum_audit_failures": audit_failures,
            "view_weight_sum_audit_failures": view_audit_failures,
            "gate_flags_triggered": sorted(triggered),
            "block_score_summary": {bid: b["score"] for bid, b in sorted(blocks.items())},
            "view_score_summary": {vid: v["score"] for vid, v in sorted(views.items())},
            "cp_designated_count": cp_count,
        },
    }


def print_summary(data):
    mm = data["stage3c_meta"]
    print(f"  blocks: {mm['block_count']}  views: {mm['view_count']}  "
          f"weight-audit failures: {len(mm['weight_sum_audit_failures'])+len(mm['view_weight_sum_audit_failures'])}  "
          f"gates triggered: {mm['gate_flags_triggered'] or 'none'}")
    for bid, b in data["blocks"].items():
        g = f"  GATED:{b['gate_flag_id']}" if b["gate_triggered"] else ""
        print(f"    {bid:10s} (w={b['weight_in_apex']}) score={b['score']}  "
              f"[pre-gate={b['weighted_before_gate']} na_dropped={len(b['na_dropped_indicators'])} "
              f"active_w={b['active_weight_sum_after_redistribution']}]{g}")
    for vid, v in data["per_artifact_views"].items():
        nr = f"  NULL:{v['null_reason']}" if v["score"] is None else ""
        print(f"    {v['view_name']:26s} score={v['score']}  "
              f"[na_dropped={len(v['na_dropped_indicators'])}]{nr}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pre-Processing Stage 3c blocks + views")
    parser.add_argument("config")
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
    # 05b parallel deliverable: per-artifact views as their own artifact
    views_env = common.make_envelope(STAGE, {"per_artifact_views": data["per_artifact_views"],
                                             "stage3c_meta": data["stage3c_meta"]}, config, spec_version)
    common.write_envelope(root / config["outputs"]["stage3_per_artifact_views"], views_env)

    print(f"Stage 3c blocks -> {out_path.relative_to(root)}  "
          f"(+ {Path(config['outputs']['stage3_per_artifact_views']).name})")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
