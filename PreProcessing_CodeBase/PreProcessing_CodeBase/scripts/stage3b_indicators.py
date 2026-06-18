#!/usr/bin/env python3
"""Stage 3b - compute the 38 L3I_PP_* indicators (survey-level, Option B).

Each indicator's threshold bands are spec prose (threshold_summary), so each gets
one eval function with band scores + flag names pinned as named constants
(surfaced in stage3b_meta.tuneables). Survey-level: indicators run ONCE.

Two redistribution mechanisms (markers set here; the renormalisation math runs at
Stage 3c):
  - PATH-N/A: an indicator whose applies_to_paths does not match the declared
    governing path (declared_path_geotag for GEO-block, declared_path_gcp for the
    rest) -> score=None, na_redistribute=True. Checked CENTRALLY in run().
  - EVIDENCE-N/A: a report-tier indicator whose derived input is null (report
    absent) -> score=None, na_redistribute=True. Checked inside the eval.

Gates (raised_at_stage != threshold) only set gate_triggered + gate_flag_id; the
flag itself fires later: the 3 catastrophic global-gate flags (PP_WRONG_CRS_DATUM,
PP_WRONG_PROJECTION, PP_GCP_AUTONOMOUS_PATH) at Stage 3d, and PP_NO_CHECK_POINTS
(null_handler) at the verification_status step. All threshold flags fire HERE.

gcp_sigma_score (021) and cp_sigma_score (035, view_only) band each per-point
sigma ratio then aggregate mean - k*(100-min), k = options.aggregator_k (0.25).
The 4 view_only CP indicators (035-038) are evaluated and tagged view_only=TRUE;
Stage 3c keeps them out of the apex blocks (they feed the CP artifact view +
verification_status). No timestamps in the data block (determinism rule 3).
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

STAGE = "stage3b_indicators"

# ---- tuneables (spec prose pinned) ------------------------------------------
SIGMA_OK_MAX, SIGMA_MARGINAL_MAX, SIGMA_HIGH_MAX = 1.0, 2.0, 5.0   # x accuracy_target
FIXED_OK_MIN, FIXED_PARTIAL_MIN, FIXED_POOR_MIN = 0.95, 0.80, 0.50
COMPLETE_OK_MIN, COMPLETE_GOOD_MIN, COMPLETE_INCOMPLETE_MIN = 0.99, 0.95, 0.80
BASELINE_OK, BASELINE_GOOD, BASELINE_LONG, BASELINE_EXCESSIVE = 5.0, 10.0, 20.0, 40.0
OVERLAP_OK = 1.0
OVERLAP_GOOD_MIN, OVERLAP_PARTIAL_MIN = 0.95, 0.80
OVERLAP_FWD_OK, OVERLAP_SIDE_OK = 75.0, 65.0          # texture proxy top band
OVERLAP_FWD_MID, OVERLAP_SIDE_MID = 65.0, 55.0
TARGET_PX_OK, TARGET_PX_MARGINAL = 3.0, 2.0
COVERAGE_OK, COVERAGE_PARTIAL = 0.80, 0.60            # GCP/CP distribution
CP_COUNT_OK, CP_COUNT_GOOD, CP_COUNT_WEAK, CP_COUNT_MIN = 20, 10, 5, 1
INDEP_OK_M, INDEP_CLOSE_M = 50.0, 10.0               # CP-GCP independence
TIME_SYNC_OK_MS, TIME_SYNC_DRIFT_MS = 100.0, 1000.0

# ---- derived (3a) keys ------------------------------------------------------
D = {n: f"L2D_PP_{i:03d}_{n}" for i, n in [
    (1, "crs_match_project"), (2, "geoid_match_project"), (3, "height_mode_consistency"),
    (4, "projection_match_location"), (5, "output_crs_metadata_present"),
    (6, "units_match_project"), (7, "customer_coord_crs_consistent"), (8, "localization_disclosed"),
    (9, "provenance_realization_consistent"), (10, "drone_session_within_base_window"),
    (11, "fraction_geotags_fixed"), (12, "geotag_completeness_fraction"),
    (13, "session_overlap_fraction"), (14, "antenna_pco_match"), (15, "sensor_metadata_consistent"),
    (16, "cors_data_continuity"), (17, "time_sync_residual_magnitude"), (18, "overlap_texture_proxy"),
    (19, "flight_conditions_adverse"), (20, "gcp_sigma_relative_to_target"),
    (21, "cp_sigma_relative_to_target"), (22, "gcp_path_acceptable"),
    (23, "gcp_customer_accuracy_adequate"), (24, "gcp_id_reconciliation"), (25, "gcp_coord_age_days"),
    (26, "coord_parse_bbox_sanity"), (27, "gcp_residuals_within_tolerance"),
    (28, "cors_station_health_acceptable"), (29, "gcp_count_adequate"),
    (30, "gcp_distribution_coverage"), (31, "target_pixels_at_gsd"), (32, "vegetation_dtm_risk"),
    (33, "settings_declared_vs_actual_consistent"), (34, "software_version_in_buggy_list"),
    (35, "cp_designated_count"), (36, "cp_distribution_coverage"), (37, "cp_gcp_spatial_independence")]}
PATH_GEOTAG = "L1F_PP_032_declared_path_geotag"
PATH_GCP = "L1F_PP_033_declared_path_gcp"
BASELINE_KM = "L1F_PP_038_baseline_length_km"


# ---- helpers ----------------------------------------------------------------
def _dv(derived, key):
    f = derived.get(key)
    return f.get("value") if isinstance(f, dict) else None


def _trace(ind, score, band, condition, inputs, *, gate_triggered=False,
           gate_action_spec=None, gate_flag_id=None, na_redistribute=False, flags_raised=None):
    return {
        "indicator_id": ind["indicator_id"],
        "indicator_name": ind["indicator_name"],
        "building_block_id": ind["building_block_id"],
        "weight_in_block": ind["weight_in_block"],
        "view_only": ind.get("view_only") == "TRUE",
        "applies_to_paths": ind["applies_to_paths"],
        "score": None if score is None else round(float(score), 1),
        "na_redistribute": na_redistribute,
        "band_matched": band,
        "condition_evaluated": condition,
        "input_values": inputs,
        "gate_triggered": gate_triggered,
        "gate_action_spec": gate_action_spec,
        "gate_flag_id": gate_flag_id,
        "flags_raised": list(flags_raised or []),
    }


def _fire(fi, name, cond, ind_id):
    f = fi.get(name)
    if f is None:
        raise KeyError(f"flag name {name!r} not in spec")
    return {"flag_id": f["flag_id"], "flag_name": f["flag_name"], "severity": f["severity"],
            "raised_at_stage_spec": f["raised_at_stage"], "_origin_stage": "stage3b",
            "_origin_indicator": ind_id, "condition_value": cond}


def _norm(s):
    return " ".join(s.upper().split()) if isinstance(s, str) and s.strip() else None


def _aggregate(scores, k):
    """mean - k*(100 - min), clamped to [0, 100]."""
    n = len(scores)
    mean = sum(scores) / n
    mn = min(scores)
    return max(0.0, min(100.0, mean - k * (100.0 - mn)))


def _governing_path(ind, sf):
    if "CUSTOMER_SUPPLIED" in ind["applies_to_paths"]:
        return sf.get(PATH_GCP)
    if ind["building_block_id"] == "BB_PP_GEO":
        return sf.get(PATH_GEOTAG)
    return sf.get(PATH_GCP)


def _path_applies(ind, sf):
    applies = ind["applies_to_paths"]
    if "ALL_PATHS" in applies:
        return True
    allowed = {p.strip() for p in applies.split("/")}
    return _governing_path(ind, sf) in allowed


# ---- eval functions ---------------------------------------------------------
def _gate(ind, ok, gate_flag_id, inputs, ok_band, bad_band):
    if ok:
        return _trace(ind, 100, ok_band, "gate condition satisfied", inputs), []
    return _trace(ind, 0, bad_band, "gate condition FAILED", inputs, gate_triggered=True,
                  gate_action_spec=ind["gate_action"], gate_flag_id=gate_flag_id), []


def i001(ind, sf, dv, fi, k):  # ref_frame_declared (gate)
    v = dv(D["crs_match_project"])
    return _gate(ind, v is True, "FLG_PP_001", {"crs_match_project": v}, "crs_match", "crs_mismatch")


def i002(ind, sf, dv, fi, k):  # geoid_model_declared
    v = dv(D["geoid_match_project"])
    if v is True:
        return _trace(ind, 100, "geoid_match", "geoid matches + consistent", {"geoid_match_project": v}), []
    fl = [_fire(fi, "PP_GEOID_MISMATCH", v, ind["indicator_id"])]
    return _trace(ind, 0, "geoid_mismatch", "geoid mismatch or inconsistent", {"geoid_match_project": v},
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i003(ind, sf, dv, fi, k):  # height_mode_declared (3 bands - recompute components)
    declared = sf.get("L1F_PP_025_declared_height_mode_per_artifact")
    proj = sf.get("L1F_PP_019_project_required_height_mode")
    inputs = {"declared": declared, "project": proj}
    if not isinstance(declared, dict) or not declared or proj is None:
        return _trace(ind, 70, "height_unconfirmed", "height mode not fully declared", inputs), []
    vals = {_norm(x) for x in declared.values()}
    all_same = len(vals) == 1
    matches = all_same and next(iter(vals)) == _norm(proj)
    if all_same and matches:
        return _trace(ind, 100, "height_ok", "all same AND matches project", inputs), []
    if not all_same:
        fl = [_fire(fi, "PP_HEIGHT_MODE_INCONSISTENT", sorted(vals), ind["indicator_id"])]
        return _trace(ind, 30, "height_inconsistent", "height mode inconsistent across artifacts",
                      inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_HEIGHT_MODE_WRONG", {"declared": sorted(vals), "project": _norm(proj)},
                ind["indicator_id"])]
    return _trace(ind, 0, "height_wrong", "consistent but wrong vs project", inputs,
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i004(ind, sf, dv, fi, k):  # projection_declared (gate)
    v = dv(D["projection_match_location"])
    return _gate(ind, v is True, "FLG_PP_002", {"projection_match_location": v}, "proj_match", "proj_mismatch")


def i005(ind, sf, dv, fi, k):  # output_crs_metadata
    v = dv(D["output_crs_metadata_present"]) or {}
    status = v.get("status")
    if status == "present_and_match":
        return _trace(ind, 100, "present_and_match", "CRS metadata present + matches declared", v), []
    if status == "missing":
        fl = [_fire(fi, "PP_OUTPUT_CRS_MISSING", v, ind["indicator_id"])]
        return _trace(ind, 50, "missing", "CRS metadata missing in an artifact", v,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_OUTPUT_CRS_MISMATCH", v, ind["indicator_id"])]
    return _trace(ind, 0, "mismatch", "artifact CRS metadata disagrees with declared", v,
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i006(ind, sf, dv, fi, k):  # units_declared
    v = dv(D["units_match_project"])
    if v is True:
        return _trace(ind, 100, "units_match", "units match + consistent", {"units_match_project": v}), []
    fl = [_fire(fi, "PP_UNITS_MISMATCH", v, ind["indicator_id"])]
    return _trace(ind, 0, "units_mismatch", "units mismatch or inconsistent", {"units_match_project": v},
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i007(ind, sf, dv, fi, k):  # customer_coord_crs (CUSTOMER_SUPPLIED; path handled centrally)
    v = dv(D["customer_coord_crs_consistent"]) or {}
    if v.get("declared") and v.get("matches"):
        return _trace(ind, 100, "customer_crs_ok", "customer CRS declared + matches", v), []
    if not v.get("declared"):
        fl = [_fire(fi, "PP_CUSTOMER_COORDS_NO_CRS", v, ind["indicator_id"])]
        return _trace(ind, 30, "customer_crs_missing", "customer CRS not declared", v,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_CUSTOMER_COORDS_WRONG_CRS", v, ind["indicator_id"])]
    return _trace(ind, 0, "customer_crs_wrong", "customer CRS wrong", v,
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i008(ind, sf, dv, fi, k):  # localization_disclosed
    v = dv(D["localization_disclosed"])
    if v is True:
        return _trace(ind, 100, "disclosed", "localization disclosed (NOT NULL)", {"localization_disclosed": v}), []
    fl = [_fire(fi, "PP_LOCALIZATION_UNDISCLOSED", v, ind["indicator_id"])]
    return _trace(ind, 60, "undisclosed", "localization undisclosed", {"localization_disclosed": v},
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i009(ind, sf, dv, fi, k):  # provenance_consistency
    v = dv(D["provenance_realization_consistent"])
    if v is True or v is None:
        return _trace(ind, 100, "provenance_consistent", "all control same realization/epoch",
                      {"provenance_realization_consistent": v}), []
    fl = [_fire(fi, "PP_MIXED_PROVENANCE", v, ind["indicator_id"])]
    return _trace(ind, 50, "mixed_provenance", "mixed realizations/epochs",
                  {"provenance_realization_consistent": v}, flags_raised=[f["flag_id"] for f in fl]), fl


def i034(ind, sf, dv, fi, k):  # settings_consistency (report-tier)
    v = dv(D["settings_declared_vs_actual_consistent"])
    if v is None:
        return _trace(ind, None, "report_absent_na", "no report -> advisory; weight redistributes",
                      {"settings": None}, na_redistribute=True), []
    if v.get("consistent") in (True, None):
        return _trace(ind, 100, "settings_match", "declared == report-actual", v), []
    fl = [_fire(fi, "PP_SETTINGS_DECLARED_ACTUAL_MISMATCH", v, ind["indicator_id"])]
    return _trace(ind, 30, "settings_mismatch", "declared != report-actual", v,
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i010(ind, sf, dv, fi, k):  # base_pairing (silent CAT)
    v = dv(D["drone_session_within_base_window"])
    if v is True:
        return _trace(ind, 100, "base_paired", "drone within base window + base_file_id present",
                      {"drone_session_within_base_window": v}), []
    fl = [_fire(fi, "PP_WRONG_BASE_PAIRED", v, ind["indicator_id"])]
    return _trace(ind, 0, "wrong_base", "drone outside base window OR base_file_id mismatch",
                  {"drone_session_within_base_window": v}, flags_raised=[f["flag_id"] for f in fl]), fl


def _band(ind, value, bands, inputs, fi):
    """bands: list of (predicate(value), score, band_name, flag_name_or_None). first match."""
    for pred, score, name, flag in bands:
        if pred(value):
            fl = [_fire(fi, flag, value, ind["indicator_id"])] if flag else []
            return _trace(ind, score, name, name, inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    return _trace(ind, 70, "unconfirmed", "no band matched (null input)", inputs), []


def i011(ind, sf, dv, fi, k):  # geotag_solution_quality
    f = dv(D["fraction_geotags_fixed"])
    inputs = {"fraction_fixed": f}
    if f is None:
        return _trace(ind, 70, "unconfirmed", "fraction_fixed null", inputs), []
    return _band(ind, f, [
        (lambda x: x >= FIXED_OK_MIN, 100, "fix_ok", None),
        (lambda x: x >= FIXED_PARTIAL_MIN, 70, "partial_fix", "PP_GEOTAG_PARTIAL_FIX"),
        (lambda x: x >= FIXED_POOR_MIN, 30, "poor_fix", "PP_GEOTAG_POOR_FIX"),
        (lambda x: True, 0, "not_fixed", "PP_GEOTAG_NOT_FIXED")], inputs, fi)


def i012(ind, sf, dv, fi, k):  # geotag_completeness
    c = dv(D["geotag_completeness_fraction"])
    inputs = {"completeness": c}
    if c is None:
        return _trace(ind, 70, "unconfirmed", "completeness null", inputs), []
    return _band(ind, c, [
        (lambda x: x >= COMPLETE_OK_MIN, 100, "complete_ok", None),
        (lambda x: x >= COMPLETE_GOOD_MIN, 80, "complete_good", None),
        (lambda x: x >= COMPLETE_INCOMPLETE_MIN, 50, "incomplete", "PP_GEOTAGS_INCOMPLETE"),
        (lambda x: True, 0, "severely_incomplete", "PP_GEOTAGS_SEVERELY_INCOMPLETE")], inputs, fi)


def i013(ind, sf, dv, fi, k):  # geotag_baseline (source field)
    bl = sf.get(BASELINE_KM)
    inputs = {"baseline_length_km": bl}
    if bl is None:
        return _trace(ind, 70, "unconfirmed", "baseline null", inputs), []
    if bl <= BASELINE_OK:
        return _trace(ind, 100, "baseline_ok", f"<= {BASELINE_OK}km", inputs), []
    if bl <= BASELINE_GOOD:
        return _trace(ind, 88, "baseline_good", f"<= {BASELINE_GOOD}km", inputs), []
    if bl <= BASELINE_LONG:
        fl = [_fire(fi, "PP_LONG_BASELINE", bl, ind["indicator_id"])]
        return _trace(ind, 70, "baseline_long", f"<= {BASELINE_LONG}km", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    if bl <= BASELINE_EXCESSIVE:
        fl = [_fire(fi, "PP_EXCESSIVE_BASELINE", bl, ind["indicator_id"])]
        return _trace(ind, 40, "baseline_excessive_mid", f"<= {BASELINE_EXCESSIVE}km", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    return _trace(ind, 20, "baseline_excessive", f"> {BASELINE_EXCESSIVE}km", inputs), []


def i014(ind, sf, dv, fi, k):  # session_overlap
    o = dv(D["session_overlap_fraction"])
    inputs = {"session_overlap_fraction": o}
    if o is None:
        return _trace(ind, 70, "unconfirmed", "overlap null", inputs), []
    if o >= OVERLAP_OK:
        return _trace(ind, 100, "overlap_full", "= 1.0", inputs), []
    if o >= OVERLAP_GOOD_MIN:
        return _trace(ind, 80, "overlap_good", ">= 0.95", inputs), []
    if o >= OVERLAP_PARTIAL_MIN:
        fl = [_fire(fi, "PP_PARTIAL_BASE_OVERLAP", o, ind["indicator_id"])]
        return _trace(ind, 50, "overlap_partial", ">= 0.80", inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_INSUFFICIENT_OVERLAP", o, ind["indicator_id"])]
    return _trace(ind, 30, "overlap_insufficient", "< 0.80", inputs, flags_raised=[f["flag_id"] for f in fl]), fl


def i015(ind, sf, dv, fi, k):  # overlap_texture
    v = dv(D["overlap_texture_proxy"]) or {}
    fwd, side = v.get("forward"), v.get("side")
    inputs = v
    if fwd is None or side is None:
        return _trace(ind, 70, "unconfirmed", "overlap not declared", inputs), []
    if fwd >= OVERLAP_FWD_OK and side >= OVERLAP_SIDE_OK:
        return _trace(ind, 100, "texture_ok", ">= 75/65", inputs), []
    if fwd >= OVERLAP_FWD_MID and side >= OVERLAP_SIDE_MID:
        return _trace(ind, 70, "texture_mid", "65-75 / 55-65", inputs), []
    fl = [_fire(fi, "PP_SPARSE_TIEPOINTS_RISK", {"forward": fwd, "side": side}, ind["indicator_id"])]
    return _trace(ind, 40, "texture_sparse", "< 65 / < 55", inputs, flags_raised=[f["flag_id"] for f in fl]), fl


def i018(ind, sf, dv, fi, k):  # cors_data_continuity (report-tier + CORS path)
    v = dv(D["cors_data_continuity"])
    if v is None:
        return _trace(ind, None, "report_absent_na", "no report -> advisory; redistributes",
                      {"cors_coverage": None}, na_redistribute=True), []
    inputs = {"cors_coverage": v}
    if isinstance(v, (int, float)) and v >= 0.999:
        return _trace(ind, 100, "continuous", "continuous", inputs), []
    if isinstance(v, (int, float)) and v >= 0.95:
        fl = [_fire(fi, "PP_CORS_MINOR_GAP", v, ind["indicator_id"])]
        return _trace(ind, 70, "minor_gap", "minor gaps", inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_CORS_MAJOR_GAP", v, ind["indicator_id"])]
    return _trace(ind, 30, "major_gap", "major gaps", inputs, flags_raised=[f["flag_id"] for f in fl]), fl


def i019(ind, sf, dv, fi, k):  # time_sync_residual (report-tier)
    v = dv(D["time_sync_residual_magnitude"])
    if v is None:
        return _trace(ind, None, "report_absent_na", "no report -> advisory; redistributes",
                      {"time_sync": None}, na_redistribute=True), []
    mx = v.get("max_ms") if isinstance(v, dict) else v
    inputs = {"time_sync_residuals": v}
    if mx is not None and mx < TIME_SYNC_OK_MS:
        return _trace(ind, 100, "sync_ok", "< 100ms", inputs), []
    if mx is not None and mx <= TIME_SYNC_DRIFT_MS:
        fl = [_fire(fi, "PP_TIME_SYNC_DRIFT", mx, ind["indicator_id"])]
        return _trace(ind, 70, "sync_drift", "100ms-1s", inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_TIME_SYNC_SEVERE", mx, ind["indicator_id"])]
    return _trace(ind, 30, "sync_severe", "> 1s", inputs, flags_raised=[f["flag_id"] for f in fl]), fl


def i020(ind, sf, dv, fi, k):  # monsoon_flight_artifact (advisory)
    v = dv(D["flight_conditions_adverse"])
    if v is True:
        fl = [_fire(fi, "PP_FLIGHT_CONDITION_RISK", v, ind["indicator_id"])]
        return _trace(ind, 70, "adverse", "adverse conditions declared", {"adverse": v},
                      flags_raised=[f["flag_id"] for f in fl]), fl
    return _trace(ind, 100, "clear", "clear / not adverse", {"adverse": v}), []


def i017(ind, sf, dv, fi, k):  # sensor_metadata (L3I_PP_017)
    v = dv(D["sensor_metadata_consistent"])
    if v is True or v is None:
        return _trace(ind, 100, "sensor_consistent", "EXIF camera serials consistent",
                      {"sensor_metadata_consistent": v}), []
    fl = [_fire(fi, "PP_SENSOR_METADATA_MISMATCH", v, ind["indicator_id"])]
    return _trace(ind, 50, "sensor_mismatch", "camera disagreement", {"sensor_metadata_consistent": v},
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i016(ind, sf, dv, fi, k):  # antenna_pco (L3I_PP_016)
    v = dv(D["antenna_pco_match"]) or {}
    if v.get("match") in (True, None):
        return _trace(ind, 100, "antenna_match", "antenna declared (no device-actual to conflict)", v), []
    fl = [_fire(fi, "PP_ANTENNA_PCO_MISMATCH", v, ind["indicator_id"])]
    return _trace(ind, 50, "antenna_mismatch", "antenna mismatch", v, flags_raised=[f["flag_id"] for f in fl]), fl


def _sigma_indicator(ind, ratios, names, fi, k):
    """band each per-point ratio; aggregate mean - k*(100-min)."""
    if not ratios:
        return _trace(ind, None, "no_points_na", "no points -> N/A; redistributes",
                      {"n": 0}, na_redistribute=True), []
    scores, marg, high, rej = [], [], [], []
    for r in ratios:
        ratio = r["ratio"]; pid = r.get("gcp_id") or r.get("cp_id")
        if ratio <= SIGMA_OK_MAX:
            scores.append(100)
        elif ratio <= SIGMA_MARGINAL_MAX:
            scores.append(70); marg.append(pid)
        elif ratio <= SIGMA_HIGH_MAX:
            scores.append(30); high.append(pid)
        else:
            scores.append(0); rej.append(pid)
    agg = _aggregate(scores, k)
    flags = []
    if marg:
        flags.append(_fire(fi, names[0], {"points": marg}, ind["indicator_id"]))
    if high:
        flags.append(_fire(fi, names[1], {"points": high}, ind["indicator_id"]))
    if rej:
        flags.append(_fire(fi, names[2], {"points": rej}, ind["indicator_id"]))
    inputs = {"n": len(ratios), "min_score": min(scores), "mean_score": round(sum(scores) / len(scores), 2),
              "aggregator_k": k, "marginal": marg, "high": high, "reject": rej}
    return _trace(ind, agg, "sigma_aggregated", "mean - k*(100-min) over per-point sigma bands",
                  inputs, flags_raised=[f["flag_id"] for f in flags]), flags


def i021(ind, sf, dv, fi, k):  # gcp_sigma
    return _sigma_indicator(ind, dv(D["gcp_sigma_relative_to_target"]) or [],
                            ("PP_GCP_SIGMA_MARGINAL", "PP_GCP_SIGMA_HIGH", "PP_GCP_SIGMA_REJECT"), fi, k)


def i022(ind, sf, dv, fi, k):  # gcp_processing_path (gate)
    v = dv(D["gcp_path_acceptable"])
    return _gate(ind, v is True, "FLG_PP_003", {"gcp_path_acceptable": v}, "path_ok", "autonomous_path")


def i023(ind, sf, dv, fi, k):  # gcp_customer_accuracy (CUSTOMER_SUPPLIED)
    v = dv(D["gcp_customer_accuracy_adequate"]) or {}
    if v.get("declared") and v.get("adequate"):
        return _trace(ind, 100, "customer_acc_ok", "declared + meets target", v), []
    if not v.get("declared"):
        fl = [_fire(fi, "PP_GCP_CUSTOMER_NO_ACCURACY_CLAIM", v, ind["indicator_id"])]
        return _trace(ind, 30, "customer_acc_missing", "no accuracy claim (reviewer-blocking)", v,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_GCP_CUSTOMER_INADEQUATE", v, ind["indicator_id"])]
    return _trace(ind, 30, "customer_acc_inadequate", "claim inadequate", v,
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i024(ind, sf, dv, fi, k):  # gcp_id_reconciliation
    v = dv(D["gcp_id_reconciliation"]) or {}
    if v.get("status") == "consistent":
        return _trace(ind, 100, "ids_match", "GCP ids internally consistent", v), []
    if v.get("duplicate_ids") and not v.get("empty_ids"):
        fl = [_fire(fi, "PP_GCP_ID_PARTIAL_MISMATCH", v, ind["indicator_id"])]
        return _trace(ind, 70, "ids_partial", "minor id issues", v, flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_GCP_ID_MISMATCH", v, ind["indicator_id"])]
    return _trace(ind, 30, "ids_mismatch", "major id issues", v, flags_raised=[f["flag_id"] for f in fl]), fl


def i025(ind, sf, dv, fi, k):  # gcp_coord_age (CUSTOMER_SUPPLIED)
    age = dv(D["gcp_coord_age_days"])
    inputs = {"gcp_coord_age_days": age}
    if age is None:
        return _trace(ind, 70, "age_unconfirmed", "age null", inputs), []
    if age < 30:
        return _trace(ind, 100, "age_fresh", "< 30d", inputs), []
    if age < 180:
        return _trace(ind, 80, "age_recent", "30-180d", inputs), []
    if age < 365:
        fl = [_fire(fi, "PP_GCP_COORDS_AGED", age, ind["indicator_id"])]
        return _trace(ind, 50, "age_aged", "180-365d", inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_GCP_COORDS_STALE", age, ind["indicator_id"])]
    return _trace(ind, 30, "age_stale", "> 365d", inputs, flags_raised=[f["flag_id"] for f in fl]), fl


def i026(ind, sf, dv, fi, k):  # coord_parse_sanity
    v = dv(D["coord_parse_bbox_sanity"]) or {}
    if v.get("all_within") is True:
        return _trace(ind, 100, "coords_sane", "all GCPs within polygon (+margin)", v), []
    fl = [_fire(fi, "PP_COORD_MISPARSE", v, ind["indicator_id"])]
    return _trace(ind, 0, "coords_misparse", "GCP(s) outside box (axis-swap/misparse)", v,
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i027(ind, sf, dv, fi, k):  # gcp_residual (report-tier)
    v = dv(D["gcp_residuals_within_tolerance"])
    if v is None:
        return _trace(ind, None, "report_absent_na", "no report -> advisory; redistributes",
                      {"residuals": None}, na_redistribute=True), []
    # tolerance = accuracy_target (spec gives no separate number); count per-GCP residuals over it.
    tol = sf.get("L1F_PP_022_accuracy_target_m") or 0.02
    def _rv(r): return r.get("res_h") if isinstance(r, dict) else r
    vals = [_rv(r) for r in v] if isinstance(v, list) else []
    exceed = [x for x in vals if isinstance(x, (int, float)) and x > tol]
    inputs = {"n_residuals": len(vals), "tolerance_m": tol, "n_exceed": len(exceed)}
    if not exceed:
        return _trace(ind, 100, "residuals_ok", "all within tolerance", inputs), []
    if len(exceed) <= 2:
        fl = [_fire(fi, "PP_GCP_RESIDUAL_OUTLIERS", inputs, ind["indicator_id"])]
        return _trace(ind, 70, "residual_outliers", "1-2 exceed tolerance", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_GCP_RESIDUAL_FAILURES", inputs, ind["indicator_id"])]
    return _trace(ind, 30, "residual_failures", "3+ exceed tolerance", inputs,
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i028(ind, sf, dv, fi, k):  # cors_station_health (report-tier + CORS path)
    v = dv(D["cors_station_health_acceptable"])
    if v is None:
        return _trace(ind, None, "report_absent_na", "no report -> advisory; redistributes",
                      {"cors_quality": None}, na_redistribute=True), []
    status = v.get("status") if isinstance(v, dict) else v
    if status in ("good", True):
        return _trace(ind, 100, "cors_good", "CORS station healthy", {"cors_quality": v}), []
    if status == "degraded":
        fl = [_fire(fi, "PP_CORS_STATION_DEGRADED", v, ind["indicator_id"])]
        return _trace(ind, 50, "cors_degraded", "degraded", {"cors_quality": v},
                      flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_CORS_STATION_UNHEALTHY", v, ind["indicator_id"])]
    return _trace(ind, 0, "cors_unhealthy", "unhealthy", {"cors_quality": v},
                  flags_raised=[f["flag_id"] for f in fl]), fl


def i029(ind, sf, dv, fi, k):  # gcp_count
    v = dv(D["gcp_count_adequate"]) or {}
    ad=v.get("adequacy")
    if ad=="adequate":
        return _trace(ind, 100, "count_adequate", "adequate", v), []
    if ad=="marginal":
        fl = [_fire(fi, "PP_GCP_COUNT_MARGINAL", v, ind["indicator_id"])]
        return _trace(ind, 70, "count_marginal", "marginal", v, flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_GCP_COUNT_INSUFFICIENT", v, ind["indicator_id"])]
    return _trace(ind, 30, "count_insufficient", "insufficient", v, flags_raised=[f["flag_id"] for f in fl]), fl


def _coverage(ind, cov, flag_partial, flag_severe, fi):
    inputs = {"coverage": cov}
    if cov is None:
        return _trace(ind, 70, "coverage_unconfirmed", "coverage null", inputs), []
    if cov >= COVERAGE_OK:
        return _trace(ind, 100, "coverage_ok", ">= 0.80", inputs), []
    if cov >= COVERAGE_PARTIAL:
        fl = [_fire(fi, flag_partial, cov, ind["indicator_id"])]
        return _trace(ind, 60 if "GCP" in flag_partial else 70, "coverage_partial", "0.60-0.80",
                      inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, flag_severe, cov, ind["indicator_id"])]
    return _trace(ind, 30, "coverage_severe", "< 0.60", inputs, flags_raised=[f["flag_id"] for f in fl]), fl


def i030(ind, sf, dv, fi, k):  # gcp_distribution
    return _coverage(ind, dv(D["gcp_distribution_coverage"]), "PP_GCP_CLUSTERED",
                     "PP_GCP_SEVERELY_CLUSTERED", fi)


def i031(ind, sf, dv, fi, k):  # target_visibility
    px = dv(D["target_pixels_at_gsd"])
    inputs = {"target_pixels": px}
    if px is None:
        return _trace(ind, 70, "target_unconfirmed", "px null", inputs), []
    if px >= TARGET_PX_OK:
        return _trace(ind, 100, "target_visible", ">= 3px", inputs), []
    if px >= TARGET_PX_MARGINAL:
        fl = [_fire(fi, "PP_TARGET_MARGINAL", px, ind["indicator_id"])]
        return _trace(ind, 60, "target_marginal", "2-3px", inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_TARGET_INVISIBLE", px, ind["indicator_id"])]
    return _trace(ind, 30, "target_invisible", "< 2px", inputs, flags_raised=[f["flag_id"] for f in fl]), fl


def i032(ind, sf, dv, fi, k):  # vegetation_dtm (advisory)
    v = dv(D["vegetation_dtm_risk"])
    if v is True:
        fl = [_fire(fi, "PP_VEG_DTM_UNRELIABLE", v, ind["indicator_id"])]
        return _trace(ind, 30, "veg_dtm_risk", "vegetated + DTM claimed", {"risk": v},
                      flags_raised=[f["flag_id"] for f in fl]), fl
    return _trace(ind, 100, "no_veg_dtm_risk", "no risk", {"risk": v}), []


def i033(ind, sf, dv, fi, k):  # software_version (v1 advisory, zero-weight flag)
    v = dv(D["software_version_in_buggy_list"]) or {}
    if v.get("in_buggy_list"):
        fl = [_fire(fi, "PP_BUGGY_SOFTWARE_VERSION", v, ind["indicator_id"])]
        return _trace(ind, 100, "buggy_advisory", "in buggy list (v1: advisory, score stays 100)", v,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    return _trace(ind, 100, "software_declared", "v1: no buggy list -> 100", v), []


# ---- view_only CP indicators (035-038) -------------------------------------
def i035(ind, sf, dv, fi, k):  # cp_sigma (view_only)
    return _sigma_indicator(ind, dv(D["cp_sigma_relative_to_target"]) or [],
                            ("PP_CP_SIGMA_MARGINAL", "PP_CP_SIGMA_HIGH", "PP_CP_SIGMA_REJECT"), fi, k)


def i036(ind, sf, dv, fi, k):  # cp_count (view_only; PP_NO_CHECK_POINTS fires at 3d)
    n = dv(D["cp_designated_count"])
    inputs = {"cp_count": n}
    if n is None:
        return _trace(ind, None, "cp_count_na", "cp_count null", inputs, na_redistribute=True), []
    if n >= CP_COUNT_OK:
        return _trace(ind, 100, "cp_count_ok", ">= 20", inputs), []
    if n >= CP_COUNT_GOOD:
        return _trace(ind, 80, "cp_count_good", "10-19", inputs), []
    if n >= CP_COUNT_WEAK:
        fl = [_fire(fi, "PP_CP_COUNT_STATISTICAL_WEAK", n, ind["indicator_id"])]
        return _trace(ind, 60, "cp_count_weak", "5-9", inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    if n >= CP_COUNT_MIN:
        fl = [_fire(fi, "PP_CP_COUNT_INSUFFICIENT", n, ind["indicator_id"])]
        return _trace(ind, 30, "cp_count_insufficient", "1-4", inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    # n == 0: PP_NO_CHECK_POINTS is null_handler -> fired at the verification_status step (3d)
    return _trace(ind, 0, "no_check_points", "cp_count = 0 (PP_NO_CHECK_POINTS fires at 3d)", inputs,
                  gate_triggered=True, gate_flag_id="FLG_PP_067"), []


def i037(ind, sf, dv, fi, k):  # cp_distribution (view_only)
    return _coverage(ind, dv(D["cp_distribution_coverage"]), "PP_CP_CLUSTERED",
                     "PP_CP_SEVERELY_CLUSTERED", fi)


def i038(ind, sf, dv, fi, k):  # cp_gcp_independence (view_only)
    dist = dv(D["cp_gcp_spatial_independence"])
    inputs = {"min_cp_gcp_distance_m": dist}
    if dist is None:
        return _trace(ind, None, "indep_na", "no CP or GCP -> N/A", inputs, na_redistribute=True), []
    if dist >= INDEP_OK_M:
        return _trace(ind, 100, "independent", ">= 50m", inputs), []
    if dist >= INDEP_CLOSE_M:
        fl = [_fire(fi, "PP_CP_GCP_TOO_CLOSE", dist, ind["indicator_id"])]
        return _trace(ind, 70, "too_close", "10-50m", inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_fire(fi, "PP_CP_GCP_OVERLAPPING", dist, ind["indicator_id"])]
    return _trace(ind, 30, "overlapping", "< 10m", inputs, flags_raised=[f["flag_id"] for f in fl]), fl


_DISPATCH = {
    "L3I_PP_001": i001, "L3I_PP_002": i002, "L3I_PP_003": i003, "L3I_PP_004": i004,
    "L3I_PP_005": i005, "L3I_PP_006": i006, "L3I_PP_007": i007, "L3I_PP_008": i008,
    "L3I_PP_009": i009, "L3I_PP_010": i010, "L3I_PP_011": i011, "L3I_PP_012": i012,
    "L3I_PP_013": i013, "L3I_PP_014": i014, "L3I_PP_015": i015, "L3I_PP_016": i016,
    "L3I_PP_017": i017, "L3I_PP_018": i018, "L3I_PP_019": i019, "L3I_PP_020": i020,
    "L3I_PP_021": i021, "L3I_PP_022": i022, "L3I_PP_023": i023, "L3I_PP_024": i024,
    "L3I_PP_025": i025, "L3I_PP_026": i026, "L3I_PP_027": i027, "L3I_PP_028": i028,
    "L3I_PP_029": i029, "L3I_PP_030": i030, "L3I_PP_031": i031, "L3I_PP_032": i032,
    "L3I_PP_033": i033, "L3I_PP_034": i034, "L3I_PP_035": i035, "L3I_PP_036": i036,
    "L3I_PP_037": i037, "L3I_PP_038": i038,
}


def run(config, project_root, spec, stage3a_data, stage2_data):
    k = float(config.get("options", {}).get("aggregator_k", 0.25))
    fi = {f["flag_name"]: f for f in spec.get("flags", [])}
    sf = stage2_data.get("source_fields", {})
    derived = stage3a_data.get("derived_fields", {})
    dv = lambda key: _dv(derived, key)  # noqa: E731
    expected = spec["_meta"]["counts"]["indicators"]

    traces: dict[str, dict] = {}
    all_flags: list[dict] = []
    path_na: list[str] = []

    for ind in spec.get("indicators", []):
        iid = ind["indicator_id"]
        if not _path_applies(ind, sf):
            trace = _trace(ind, None, "path_na_redistribute",
                           f"applies_to_paths={ind['applies_to_paths']} not matched by declared path "
                           f"({_governing_path(ind, sf)})",
                           {"declared_path": _governing_path(ind, sf)}, na_redistribute=True)
            path_na.append(iid)
            flags = []
        else:
            trace, flags = _DISPATCH[iid](ind, sf, dv, fi, k)
        traces[iid + "_" + ind["indicator_name"]] = trace
        for fl in flags:
            all_flags.append({**fl, "_origin_indicator": iid})

    counts_by_band: dict[str, int] = {}
    na_redis, gates, view_only = [], [], []
    for t in traces.values():
        counts_by_band[t["band_matched"]] = counts_by_band.get(t["band_matched"], 0) + 1
        if t["na_redistribute"]:
            na_redis.append(t["indicator_id"])
        if t["gate_triggered"]:
            gates.append(f"{t['indicator_id']}->{t['gate_flag_id']}")
        if t["view_only"]:
            view_only.append(t["indicator_id"])

    return {
        "survey_level": True,
        "indicator_traces": dict(sorted(traces.items())),
        "flags_raised_stage3b": all_flags,
        "stage3b_notes": [
            "Option B per-indicator evals; band scores/flag names from spec threshold_summary.",
            "PATH-N/A checked centrally; EVIDENCE-N/A (report absent) inside report-tier evals; "
            "both set na_redistribute=True (Stage 3c renormalises block weights).",
            "Gate flags (PP_WRONG_CRS_DATUM/PP_WRONG_PROJECTION/PP_GCP_AUTONOMOUS_PATH at 3d; "
            "PP_NO_CHECK_POINTS at verification_status) are markers here, not raised at 3b.",
            "view_only CP indicators (035-038) tagged; Stage 3c excludes them from apex blocks.",
        ],
        "stage3b_meta": {
            "expected_indicator_count": expected,
            "produced_count": len(traces),
            "aggregator_k": k,
            "counts_by_band": dict(sorted(counts_by_band.items())),
            "na_redistribute_indicators": sorted(na_redis),
            "path_na_indicators": sorted(path_na),
            "gates_triggered": gates,
            "view_only_indicators": sorted(view_only),
            "tuneables": {
                "SIGMA_OK_MAX": SIGMA_OK_MAX, "SIGMA_MARGINAL_MAX": SIGMA_MARGINAL_MAX,
                "SIGMA_HIGH_MAX": SIGMA_HIGH_MAX, "FIXED_OK_MIN": FIXED_OK_MIN,
                "FIXED_PARTIAL_MIN": FIXED_PARTIAL_MIN, "FIXED_POOR_MIN": FIXED_POOR_MIN,
                "COMPLETE_OK_MIN": COMPLETE_OK_MIN, "COMPLETE_GOOD_MIN": COMPLETE_GOOD_MIN,
                "COMPLETE_INCOMPLETE_MIN": COMPLETE_INCOMPLETE_MIN, "BASELINE_OK": BASELINE_OK,
                "BASELINE_GOOD": BASELINE_GOOD, "BASELINE_LONG": BASELINE_LONG,
                "BASELINE_EXCESSIVE": BASELINE_EXCESSIVE, "OVERLAP_GOOD_MIN": OVERLAP_GOOD_MIN,
                "OVERLAP_PARTIAL_MIN": OVERLAP_PARTIAL_MIN, "OVERLAP_FWD_OK": OVERLAP_FWD_OK,
                "OVERLAP_SIDE_OK": OVERLAP_SIDE_OK, "OVERLAP_FWD_MID": OVERLAP_FWD_MID,
                "OVERLAP_SIDE_MID": OVERLAP_SIDE_MID, "TARGET_PX_OK": TARGET_PX_OK,
                "TARGET_PX_MARGINAL": TARGET_PX_MARGINAL, "COVERAGE_OK": COVERAGE_OK,
                "COVERAGE_PARTIAL": COVERAGE_PARTIAL, "CP_COUNT_OK": CP_COUNT_OK,
                "CP_COUNT_GOOD": CP_COUNT_GOOD, "CP_COUNT_WEAK": CP_COUNT_WEAK,
                "INDEP_OK_M": INDEP_OK_M, "INDEP_CLOSE_M": INDEP_CLOSE_M,
                "TIME_SYNC_OK_MS": TIME_SYNC_OK_MS, "TIME_SYNC_DRIFT_MS": TIME_SYNC_DRIFT_MS,
            },
        },
    }


def print_summary(data):
    mm = data["stage3b_meta"]
    print(f"  indicators: {mm['produced_count']}/{mm['expected_indicator_count']}  k={mm['aggregator_k']}")
    print(f"    na_redistribute ({len(mm['na_redistribute_indicators'])}): {mm['na_redistribute_indicators']}")
    print(f"    gates: {mm['gates_triggered'] or 'none'}   view_only: {mm['view_only_indicators']}")
    apex = {t["indicator_id"].replace("L3I_PP_", ""): t["score"]
            for t in data["indicator_traces"].values() if not t["view_only"]}
    print(f"    apex-indicator scores: {apex}")
    views = {t["indicator_id"].replace("L3I_PP_", ""): t["score"]
             for t in data["indicator_traces"].values() if t["view_only"]}
    print(f"    view-only scores: {views}")
    print(f"  flags raised at 3b: {len(data['flags_raised_stage3b'])}  "
          f"{[f['flag_id'] for f in data['flags_raised_stage3b']]}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pre-Processing Stage 3b indicators")
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
    data = run(config, root, spec, data3a, data2)
    out_path = root / config["outputs"]["stage3_indicators"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    print(f"Stage 3b indicators -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
