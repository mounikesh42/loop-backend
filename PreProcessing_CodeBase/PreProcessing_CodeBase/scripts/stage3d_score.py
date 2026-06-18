#!/usr/bin/env python3
"""Stage 3d - apex pre_processing_score + verification_status + flag aggregation.

  formula:     0.35*REF + 0.30*GEO + 0.25*GCT + 0.10*SD  (block scores from 3c;
               weights read at runtime from spec.pre_processing_score_blocks).
  global gate: PP_WRONG_CRS_DATUM OR PP_WRONG_PROJECTION OR PP_GCP_AUTONOMOUS_PATH
               -> pre_processing_score = 0 (FORCED here; zeroing one block at 3c
               does not zero the apex arithmetically). The named CATASTROPHIC
               flag(s) fire HERE (raised_at_stage=global_gate).
  null:        NONE. The score always computes from the four blocks (spec).

verification_status (the new non-gating field) is computed from the 4 CP numerics
(cp_count / cp_distribution / cp_gcp_independence / cp_sigma) and reports one of
VERIFIED | UNVERIFIED_NO_CPS | UNVERIFIED_INSUFFICIENT_CPS | UNVERIFIED_CP_NOT_
INDEPENDENT | UNVERIFIED_CP_CLUSTERED | UNVERIFIED_CP_TRUST_LOW. It NEVER gates the
score. PP_NO_INDEPENDENT_VERIFICATION (CRITICAL informational) fires when status
!= VERIFIED; PP_NO_CHECK_POINTS (null_handler) fires when cp_count = 0.

Flags from every stage are concatenated into all_flags_aggregated, each retaining
_origin_stage. Stage 2's _handoff_crossdoc_candidates (empty - runtime-independent)
is carried through; the 2 documentation handoff flags (target washed away,
stockpile toe) are REGISTERED (not raised - PP has no input to detect them). No
timestamps in the data block (determinism rule 3).
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

# ---- verification_status tuneables (CP bands; spec-aligned, operator-adjustable) ----
CP_VERIFIED_MIN_COUNT = 5        # < 5 -> INSUFFICIENT (matches PP_CP_COUNT_INSUFFICIENT)
CP_VERIFIED_MIN_COVERAGE = 0.80  # < 0.80 -> CLUSTERED
CP_VERIFIED_MIN_INDEP_M = 50.0   # < 50 m -> NOT_INDEPENDENT
CP_VERIFIED_MIN_SIGMA_SCORE = 50 # cp_sigma_score < 50 -> TRUST_LOW

GATE_FLAG_BY_INDICATOR = {
    "L3I_PP_001": "PP_WRONG_CRS_DATUM",
    "L3I_PP_004": "PP_WRONG_PROJECTION",
    "L3I_PP_022": "PP_GCP_AUTONOMOUS_PATH",
}
HANDOFF_FLAG_NAMES = ("PP_STAGE2_TARGET_DETECTION_FAILURE", "PP_STOCKPILE_BOUNDARY_DISPUTE")


def _flag_record(spec_flag, condition_value, origin) -> dict:
    return {
        "flag_id": spec_flag["flag_id"],
        "flag_name": spec_flag["flag_name"],
        "severity": spec_flag["severity"],
        "raised_at_stage_spec": spec_flag["raised_at_stage"],
        "_origin_stage": origin,
        "condition_value": condition_value,
    }


def _verification_status(cp_count, coverage, indep, cp_sigma_score):
    if cp_count is None or cp_count == 0:
        return "UNVERIFIED_NO_CPS", "no check points designated"
    if cp_count < CP_VERIFIED_MIN_COUNT:
        return "UNVERIFIED_INSUFFICIENT_CPS", f"cp_count={cp_count} < {CP_VERIFIED_MIN_COUNT}"
    if indep is not None and indep < CP_VERIFIED_MIN_INDEP_M:
        return "UNVERIFIED_CP_NOT_INDEPENDENT", f"min CP-GCP dist={indep}m < {CP_VERIFIED_MIN_INDEP_M}m"
    if coverage is not None and coverage < CP_VERIFIED_MIN_COVERAGE:
        return "UNVERIFIED_CP_CLUSTERED", f"CP coverage={coverage} < {CP_VERIFIED_MIN_COVERAGE}"
    if cp_sigma_score is not None and cp_sigma_score < CP_VERIFIED_MIN_SIGMA_SCORE:
        return "UNVERIFIED_CP_TRUST_LOW", f"cp_sigma_score={cp_sigma_score} < {CP_VERIFIED_MIN_SIGMA_SCORE}"
    return "VERIFIED", "CPs sufficient, distributed, independent, trustworthy"


def _aggregate_flags(stage2, s3a, s3b, s3c, apex_flags):
    all_flags: list[dict] = []

    def _tag(flags, default):
        for f in flags:
            f = dict(f)
            f.setdefault("_origin_stage", default)
            all_flags.append(f)

    _tag(stage2.get("_flags_raised_stage2", []), "stage2_merge")
    _tag(s3a.get("flags_raised_stage3a", []), "stage3a")
    _tag(s3b.get("flags_raised_stage3b", []), "stage3b")
    _tag(s3c.get("flags_raised_stage3c", []), "stage3c")
    _tag(apex_flags, "stage3d")

    by_origin: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for f in all_flags:
        by_origin[f["_origin_stage"]] = by_origin.get(f["_origin_stage"], 0) + 1
        sev = f.get("severity", "UNKNOWN").split()[0]
        by_sev[sev] = by_sev.get(sev, 0) + 1
    return all_flags, dict(sorted(by_origin.items())), dict(sorted(by_sev.items()))


def run(config, project_root, spec, stage2_data, stage3a_data, stage3b_data, stage3c_data) -> dict:
    apex_spec = spec["pre_processing_score"]
    fi = {f["flag_name"]: f for f in spec.get("flags", [])}
    block_order = [b["block_id"] for b in spec["pre_processing_score_blocks"]]
    weights = {b["block_id"]: float(b["weight"]) for b in spec["pre_processing_score_blocks"]}
    blocks = stage3c_data.get("blocks", {})
    by_id = {t["indicator_id"]: t for t in stage3b_data.get("indicator_traces", {}).values()}
    derived = stage3a_data.get("derived_fields", {})
    apex_flags: list[dict] = []
    notes: list[str] = []

    # ---- apex weighted sum (spec-formula order) ----
    contributions = []
    weighted = 0.0
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
    triggered_flags = sorted({GATE_FLAG_BY_INDICATOR[iid] for iid in GATE_FLAG_BY_INDICATOR
                              if by_id.get(iid, {}).get("gate_triggered")})
    global_gate = bool(triggered_flags)
    if global_gate:
        for name in triggered_flags:
            apex_flags.append(_flag_record(fi[name], {"gate_indicator_triggered": True}, "stage3d"))
        pre_processing_score = 0.0
        notes.append(f"GLOBAL GATE fired ({triggered_flags}) -> pre_processing_score forced to 0.")
    else:
        pre_processing_score = weighted_before_gate

    # ---- verification_status (non-gating) ----
    def dval(key):
        f = derived.get(key)
        return f.get("value") if isinstance(f, dict) else None
    cp_count = dval("L2D_PP_035_cp_designated_count")
    coverage = dval("L2D_PP_036_cp_distribution_coverage")
    indep = dval("L2D_PP_037_cp_gcp_spatial_independence")
    cp_sigma_score = by_id.get("L3I_PP_035", {}).get("score")
    vstatus, vreason = _verification_status(cp_count, coverage, indep, cp_sigma_score)

    if vstatus != "VERIFIED":
        apex_flags.append(_flag_record(fi["PP_NO_INDEPENDENT_VERIFICATION"],
                                       {"verification_status": vstatus}, "stage3d"))
    if cp_count == 0:
        apex_flags.append(_flag_record(fi["PP_NO_CHECK_POINTS"], {"cp_count": 0}, "stage3d"))

    # ---- flag aggregation across all stages ----
    all_flags, by_origin, by_sev = _aggregate_flags(
        stage2_data, stage3a_data, stage3b_data, stage3c_data, apex_flags)

    # ---- audits ----
    apex_weight_sum = sum(weights.values())
    weight_ok = abs(apex_weight_sum - 1.0) < WEIGHT_SUM_TOLERANCE
    bb_weights = {b["block_id"]: float(b["weight_in_pre_processing_score"])
                  for b in spec.get("building_blocks", [])}
    weight_consistency = {bid: {"apex_sheet": w, "building_blocks_sheet": bb_weights.get(bid),
                                "match": bb_weights.get(bid) == w} for bid, w in weights.items()}
    mismatches = [bid for bid, c in weight_consistency.items() if not c["match"]]

    handoff_registered = [{"flag_id": fi[n]["flag_id"], "flag_name": n,
                           "disposition": fi[n]["condition"], "note": "documentation handoff; "
                           "PP cannot detect this from artifacts - registered, not raised"}
                          for n in HANDOFF_FLAG_NAMES if n in fi]

    return {
        "pre_processing_score": pre_processing_score,
        "apex_formula_spec": apex_spec["formula_expression"],
        "apex_weights_used": weights,
        "weighted_score_before_global_gate": weighted_before_gate,
        "contributions": contributions,
        "global_gate": {
            "triggered": global_gate,
            "condition_spec": apex_spec["global_gate_condition"],
            "action_spec": apex_spec["global_gate_action"],
            "triggered_flags": triggered_flags,
        },
        "null_handling": {"no_null_state": True, "spec": apex_spec["null_handling"]},
        "verification_status": {
            "value": vstatus,
            "reason": vreason,
            "never_score_gating": True,
            "cp_designated_count": cp_count,
            "cp_distribution_coverage": coverage,
            "cp_gcp_spatial_independence_m": indep,
            "cp_sigma_score": cp_sigma_score,
        },
        "per_artifact_views_summary": stage3c_data.get("stage3c_meta", {}).get("view_score_summary"),
        "all_flags_aggregated": all_flags,
        "flags_by_origin_stage": by_origin,
        "flags_by_severity": by_sev,
        "_handoff_crossdoc_candidates": stage2_data.get("_handoff_crossdoc_candidates", []),
        "_handoff_flags_registered": handoff_registered,
        "stage3d_notes": notes or ["No global gate; score = weighted block sum. No null state."],
        "stage3d_meta": {
            "apex_score_id": apex_spec["score_id"],
            "apex_display_name": apex_spec["display_name"],
            "apex_weight_sum_audit": {"computed": round(apex_weight_sum, 6), "expected": 1.0, "ok": weight_ok},
            "apex_weight_consistency_vs_building_blocks": weight_consistency,
            "apex_weight_consistency_mismatches": mismatches,
            "verification_tuneables": {
                "CP_VERIFIED_MIN_COUNT": CP_VERIFIED_MIN_COUNT,
                "CP_VERIFIED_MIN_COVERAGE": CP_VERIFIED_MIN_COVERAGE,
                "CP_VERIFIED_MIN_INDEP_M": CP_VERIFIED_MIN_INDEP_M,
                "CP_VERIFIED_MIN_SIGMA_SCORE": CP_VERIFIED_MIN_SIGMA_SCORE,
            },
            "total_flags_aggregated": len(all_flags),
        },
    }


def print_summary(data):
    gg = data["global_gate"]
    print(f"  pre_processing_score = {data['pre_processing_score']}   "
          f"(weighted_before_gate={data['weighted_score_before_global_gate']})")
    print(f"  formula: {data['apex_formula_spec']}")
    for c in data["contributions"]:
        print(f"    {c['block_id']:10s} w={c['weight_in_apex']} x {c['block_score']} = {c['contribution']}")
    print(f"  global_gate triggered: {gg['triggered']}  {gg['triggered_flags'] or ''}")
    vs = data["verification_status"]
    print(f"  verification_status = {vs['value']}  ({vs['reason']})  [never score-gating]")
    print(f"    cp: count={vs['cp_designated_count']} cov={vs['cp_distribution_coverage']} "
          f"indep={vs['cp_gcp_spatial_independence_m']}m sigma={vs['cp_sigma_score']}")
    print(f"  views: {data['per_artifact_views_summary']}")
    mm = data["stage3d_meta"]
    print(f"  apex weight-sum audit: {mm['apex_weight_sum_audit']['ok']}  "
          f"consistency mismatches: {mm['apex_weight_consistency_mismatches'] or 'none'}")
    print(f"  flags aggregated: {mm['total_flags_aggregated']}  by_severity={data['flags_by_severity']}  "
          f"by_origin={data['flags_by_origin_stage']}")
    print(f"  handoff registered: {[h['flag_name'] for h in data['_handoff_flags_registered']]}  "
          f"crossdoc: {len(data['_handoff_crossdoc_candidates'])}")
    for fl in data["all_flags_aggregated"]:
        print(f"    FLAG {fl['flag_id']} {fl['flag_name']} ({fl['severity']}) @{fl['_origin_stage']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pre-Processing Stage 3d apex score")
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
    d2 = stage2_merge.run(config, root, spec, env1["data"])
    d3a = stage3a_derived.run(config, root, spec, d2)
    d3b = stage3b_indicators.run(config, root, spec, d3a, d2)
    d3c = stage3c_blocks.run(config, root, spec, d3b)
    data = run(config, root, spec, d2, d3a, d3b, d3c)
    out_path = root / config["outputs"]["stage3_pre_processing_score"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    print(f"Stage 3d pre_processing_score -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
