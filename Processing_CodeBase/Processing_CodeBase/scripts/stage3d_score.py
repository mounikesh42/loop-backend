#!/usr/bin/env python3
"""Stage 3d - apex processing_score + verification_status + flag aggregation.

  formula:     0.30*ba_quality + 0.30*image_matching + 0.25*control_verification
               + 0.15*deliverable_output  (block scores from 3c; weights read at
               runtime from spec.processing_score_blocks).
  global gate: PROC_OUTPUT_CRS_MISMATCH (L3I_PROC_031 gate) -> processing_score = 0
               (FORCED here; zeroing the 0.15 DO block at 3c does not zero the apex
               arithmetically). The CATASTROPHIC flag fires HERE (raised_at_stage=
               global_gate).
  null:        processing_score = null when the Agisoft report is absent (Option A).
               Stage 1 hard-fails on a missing report, so the pipeline halts before
               3d; the guard here is the belt-and-braces contract.

verification_status (non-gating categorical) is computed from CP count + CP RMSE:
  CPs>=5 & cp_rmse<=target -> VERIFIED_RESIDUALS_PASS
  CPs>=5 & 1-2x            -> VERIFIED_RESIDUALS_MARGINAL
  CPs>=5 & >2x             -> VERIFIED_RESIDUALS_FAIL
  1-4 CPs                  -> UNVERIFIED_INSUFFICIENT_CPS
  0 CPs                    -> UNVERIFIED_NO_CPS
It NEVER gates the score (a high-quality survey can be unverified).

Flags from every stage are concatenated into all_flags_aggregated (each retains
_origin_stage), then deduped by flag_id into unique_flags (PROC_NO_GCPS_USED is
double-raised by L3I_PROC_023 + L3I_PROC_030 by design). Stage 2's
_handoff_crossdoc_candidates (empty - runtime-independent) is carried through.
No timestamps in the data block (rule 3).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import stage1_inventory  # noqa: E402
import stage2_merge  # noqa: E402
import stage3a_derived  # noqa: E402
import stage3b_indicators  # noqa: E402
import stage3c_blocks  # noqa: E402

STAGE = "stage3d_score"
WEIGHT_SUM_TOL = 1e-6

# verification_status tuneables (spec-aligned, operator-adjustable)
CP_VERIFIED_MIN_COUNT = 5      # >=5 CPs required for a VERIFIED_* status
CP_RMSE_PASS_REL = 1.0         # cp_rmse/target <= 1 -> PASS
CP_RMSE_MARGINAL_REL = 2.0     # 1-2x -> MARGINAL ; >2x -> FAIL

GATE_FLAG_BY_INDICATOR = {"L3I_PROC_031": "PROC_OUTPUT_CRS_MISMATCH"}


def _flag_record(spec_flag, origin, extra=None):
    rec = {"flag_id": spec_flag["flag_id"], "flag_name": spec_flag["flag_name"],
           "severity": spec_flag["severity"], "raised_at_stage_spec": spec_flag["raised_at_stage"],
           "_origin_stage": origin}
    if extra:
        rec.update(extra)
    return rec


def _verification_status(cp_count, cp_rmse_rel):
    if cp_count is None or cp_count == 0:
        return "UNVERIFIED_NO_CPS", "0 check points designated"
    if cp_count < CP_VERIFIED_MIN_COUNT:
        return "UNVERIFIED_INSUFFICIENT_CPS", f"cp_count={cp_count} < {CP_VERIFIED_MIN_COUNT}"
    if cp_rmse_rel is None:
        return "UNVERIFIED_INSUFFICIENT_CPS", "CP RMSE unavailable"
    if cp_rmse_rel <= CP_RMSE_PASS_REL:
        return "VERIFIED_RESIDUALS_PASS", f"{cp_count} CPs, cp_rmse {cp_rmse_rel}x target <= 1"
    if cp_rmse_rel <= CP_RMSE_MARGINAL_REL:
        return "VERIFIED_RESIDUALS_MARGINAL", f"{cp_count} CPs, cp_rmse {cp_rmse_rel}x target in 1-2"
    return "VERIFIED_RESIDUALS_FAIL", f"{cp_count} CPs, cp_rmse {cp_rmse_rel}x target > 2"


def run(config, project_root, spec, stage2_data, stage3a_data, stage3b_data, stage3c_data) -> dict:
    apex_spec = spec["processing_score"]
    fi = {f["flag_name"]: f for f in spec["flags"]}
    block_order = [b["block_id"] for b in spec["processing_score_blocks"]]
    weights = {b["block_id"]: float(b["weight"]) for b in spec["processing_score_blocks"]}
    blocks = stage3c_data["blocks"]
    by_id = stage3b_data["indicator_traces"]
    derived = stage3a_data["derived"]
    source = stage2_data["source_fields"]
    apex_flags, notes = [], []

    # ---- null state: no report -> processing_score = null (Option A) ----
    report_present = any(source.get(s["field_name"]) is not None
                         for s in spec["source_fields"] if s["file_id"] == "SRC_PROC_REPORT")

    # ---- apex weighted sum (spec-formula order) ----
    contributions, weighted = [], 0.0
    for bid in block_order:
        b = blocks.get(bid, {})
        score = float(b.get("score") or 0.0)
        contrib = round(weights[bid] * score, 3)
        weighted += contrib
        contributions.append({"block_id": bid, "block_name": b.get("block_name"),
                              "weight_in_apex": weights[bid], "block_score": score,
                              "contribution": contrib})
    weighted_before_gate = round(weighted, 1)

    # ---- global gate (force apex=0) ----
    triggered = sorted({GATE_FLAG_BY_INDICATOR[iid] for iid in GATE_FLAG_BY_INDICATOR
                        if by_id.get(iid, {}).get("gate_triggered")})
    global_gate = bool(triggered)

    if not report_present:
        processing_score = None
        notes.append("Agisoft report absent -> processing_score = null (Option A).")
    elif global_gate:
        for name in triggered:
            apex_flags.append(_flag_record(fi[name], "stage3d", {"gate_indicator_triggered": True}))
        processing_score = 0.0
        notes.append(f"GLOBAL GATE fired ({triggered}) -> processing_score forced to 0.")
    else:
        processing_score = weighted_before_gate

    # ---- verification_status (non-gating) ----
    cp_count = derived.get("cp_count_value")
    cp_rmse_rel = derived.get("cp_rmse_relative_to_target")
    vstatus, vreason = _verification_status(cp_count, cp_rmse_rel)

    # ---- flag aggregation across all stages + dedupe ----
    all_flags = []
    for flags, default in [
        (stage2_data.get("_flags_raised_stage2", []), "stage2_merge"),
        (stage3a_data.get("flags_raised_stage3a", []), "stage3a"),
        (stage3b_data.get("flags_raised_stage3b", []), "stage3b"),
        (stage3c_data.get("flags_raised_stage3c", []), "stage3c"),
        (apex_flags, "stage3d"),
    ]:
        for f in flags:
            f = dict(f)
            f.setdefault("_origin_stage", default)
            all_flags.append(f)

    # dedupe by flag_id -> unique_flags (record all origins)
    unique: dict[str, dict] = {}
    for f in all_flags:
        fid = f.get("flag_id")
        if fid not in unique:
            unique[fid] = {"flag_id": fid, "flag_name": f.get("flag_name"),
                           "severity": f.get("severity"), "origins": []}
        origin = f.get("_indicator_id") or f.get("_origin_stage")
        if origin not in unique[fid]["origins"]:
            unique[fid]["origins"].append(origin)
    unique_flag_names = sorted(u["flag_name"] for u in unique.values())

    by_origin, by_sev = {}, {}
    for f in all_flags:
        by_origin[f["_origin_stage"]] = by_origin.get(f["_origin_stage"], 0) + 1
        sev = (f.get("severity") or "UNKNOWN").split()[0]
        by_sev[sev] = by_sev.get(sev, 0) + 1

    # ---- audits ----
    apex_weight_sum = sum(weights.values())
    weight_ok = abs(apex_weight_sum - 1.0) < WEIGHT_SUM_TOL
    bb_weights = {b["block_id"]: float(b["weight_in_processing_score"]) for b in spec["building_blocks"]}
    consistency = {bid: {"apex_sheet": w, "building_blocks_sheet": bb_weights.get(bid),
                         "match": bb_weights.get(bid) == w} for bid, w in weights.items()}
    mismatches = [bid for bid, c in consistency.items() if not c["match"]]

    return {
        "processing_score": processing_score,
        "apex_formula_spec": apex_spec["formula_expression"],
        "apex_weights_used": weights,
        "weighted_score_before_global_gate": weighted_before_gate,
        "contributions": contributions,
        "global_gate": {
            "triggered": global_gate,
            "condition_spec": apex_spec["global_gate_condition"],
            "action_spec": apex_spec["global_gate_action"],
            "triggered_flags": triggered,
            "mechanism": "force processing_score=0 (also zeros DO block at 3c); spec arithmetic "
                         "'via block-of-blocks' is loose - the populated global_gate_condition + "
                         "CATASTROPHIC severity make it a true force-to-0 (drone/PP pattern).",
        },
        "null_handling": {"is_null": processing_score is None, "report_present": report_present,
                          "spec": apex_spec["null_handling"]},
        "verification_status": {
            "value": vstatus, "reason": vreason, "never_score_gating": True,
            "cp_count": cp_count, "cp_rmse_relative_to_target": cp_rmse_rel,
        },
        "per_deliverable_views_summary": stage3c_data["stage3c_meta"]["view_score_summary"],
        "all_flags_aggregated": all_flags,
        "unique_flags": dict(sorted(unique.items())),
        "unique_flag_names": unique_flag_names,
        "flags_by_origin_stage": dict(sorted(by_origin.items())),
        "flags_by_severity": dict(sorted(by_sev.items())),
        "_handoff_crossdoc_candidates": stage2_data.get("_handoff_crossdoc_candidates", []),
        "stage3d_notes": notes or ["No global gate; score = weighted block sum."],
        "stage3d_meta": {
            "apex_score_id": apex_spec["score_id"],
            "apex_display_name": apex_spec["display_name"],
            "apex_weight_sum_audit": {"computed": round(apex_weight_sum, 6), "expected": 1.0, "ok": weight_ok},
            "apex_weight_consistency_vs_building_blocks": consistency,
            "apex_weight_consistency_mismatches": mismatches,
            "verification_tuneables": {"CP_VERIFIED_MIN_COUNT": CP_VERIFIED_MIN_COUNT,
                                       "CP_RMSE_PASS_REL": CP_RMSE_PASS_REL,
                                       "CP_RMSE_MARGINAL_REL": CP_RMSE_MARGINAL_REL},
            "total_flags_aggregated": len(all_flags),
            "unique_flag_count": len(unique),
        },
    }


def print_summary(data):
    gg = data["global_gate"]
    print(f"  processing_score = {data['processing_score']}   "
          f"(before_gate={data['weighted_score_before_global_gate']})")
    print(f"  formula: {data['apex_formula_spec']}")
    for c in data["contributions"]:
        print(f"    {c['block_id']:12s} w={c['weight_in_apex']} x {c['block_score']} = {c['contribution']}")
    print(f"  global_gate: {gg['triggered']}  {gg['triggered_flags'] or ''}")
    vs = data["verification_status"]
    print(f"  verification_status = {vs['value']} ({vs['reason']}) [non-gating]")
    mm = data["stage3d_meta"]
    print(f"  apex weight-sum audit: {mm['apex_weight_sum_audit']['ok']}  "
          f"consistency mismatches: {mm['apex_weight_consistency_mismatches'] or 'none'}")
    print(f"  views: {data['per_deliverable_views_summary']}")
    print(f"  flags: {mm['total_flags_aggregated']} raw / {mm['unique_flag_count']} unique  "
          f"sev={data['flags_by_severity']}  origin={data['flags_by_origin_stage']}")
    for n in data["unique_flag_names"]:
        u = next(u for u in data["unique_flags"].values() if u["flag_name"] == n)
        print(f"    FLAG {u['flag_id']} {n} ({u['severity']}) origins={u['origins']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Processing Stage 3d apex score")
    ap.add_argument("config")
    args = ap.parse_args(argv)
    config_path = Path(args.config).resolve()
    config = common.load_config(config_path)
    root = config_path.parent
    spec = common.load_spec(root, config)
    spec_version = spec["_meta"]["version"]
    env1, hard = stage1_inventory.run(config, root)
    if hard and config.get("options", {}).get("fail_fast", True):
        print("HALT: Stage 1 hard failure (fail_fast).")
        return 1
    d2 = stage2_merge.run(config, root, spec, env1["data"])
    d3a = stage3a_derived.run(config, root, spec, d2)
    d3b = stage3b_indicators.run(config, root, spec, d3a, d2)
    d3c = stage3c_blocks.run(config, root, spec, d3b, d2)
    data = run(config, root, spec, d2, d3a, d3b, d3c)
    out_path = root / config["outputs"]["stage3_processing_score"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    print(f"Stage 3d processing_score -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
