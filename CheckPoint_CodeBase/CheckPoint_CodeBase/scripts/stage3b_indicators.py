#!/usr/bin/env python3
"""Stage 3b - compute the 14 L3I_CP_* indicators PER POINT (per spec sheet 04).

Option B (per-indicator eval functions): the spec stores every indicator's
threshold bands as prose only (threshold_summary), so each indicator gets one
eval function. Band scores + flag names come from the spec prose, pinned here as
named constants and surfaced in stage3b_meta.tuneables (template rule 8).

CheckPoint-specific mechanics not present in GCP:
  - sigma is the PRIMARY anchor (L3I_CP_001, weight 0.45 in COMPLETE) with
    THREE-WAY graceful degradation:
      present            -> band on sigma_relative_to_target (1x/2x/5x)
      absent + expected  -> 50 + FLG_CP_009 CP_SIGMA_NOT_EXPORTED
      absent + NOT expected (device limit) -> N/A: score=None, na_redistribute
        -> Stage 3c drops it and renormalises the COMPLETE block weights.
  - correction-age (L3I_CP_003) and fix-hold (L3I_CP_012) ALSO N/A-redistribute
    when their input is absent.
  - L3I_CP_002 fix-type gate: FLOAT/AUTONOMOUS -> score 0, gate_triggered=True,
    gate_flag_id = FLG_CP_004 (FLOAT) / FLG_CP_005 (AUTONOMOUS). The flag itself
    fires at Stage 3c (internal_gate), where the FLG_CP_004 severity escalation
    (CATASTROPHIC when effective_check_point_count < 5) is also applied.
  - L3I_CP_005 height gate: absent height + not auto-known -> score 0,
    gate_triggered=True, gate_flag_id = FLG_CP_003 (fires at Stage 3c).

5 threshold flags are STANDALONE (not tied to a scoring indicator), fired here
from the Stage 3a timing/mark-photo signals: FLG_CP_023/024 (delay), FLG_CP_025
(before flight), FLG_CP_026 (during flight, LOW workflow), FLG_CP_029 (no mark
photo, advisory). The remaining 19 threshold flags are indicator-bound.

Each indicator emits a trace block (Section 3). Flags get _origin_stage=
"stage3b" + _origin_point and surface in data.flags_raised_stage3b. No
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

STAGE = "stage3b_indicators"

# ---- engineering tuneables (spec prose pinned as named constants) ----
# L3I_CP_001 sigma band multipliers (x accuracy_target): <=1x / <=2x / <=5x / >5x.
SIGMA_OK_MAX = 1.0
SIGMA_MARGINAL_MAX = 2.0
SIGMA_HIGH_MAX = 5.0
SCORE_SIGMA_NOT_EXPORTED = 50  # absent + expected-for-device

# L3I_CP_003 correction-age bands (sec): <=2 / <=5 / <=15 / <=30 / >30.
CORR_AGE_OK_MAX = 2.0
CORR_AGE_GOOD_MAX = 5.0
CORR_AGE_STALE_MID_MAX = 15.0
CORR_AGE_STALE_MAX = 30.0

# L3I_CP_006 tilt bands (deg): <=2 / <=4 / >4. Advisory paths for non-tilt-comp.
TILT_OK_MAX = 2.0
TILT_WARN_MAX = 4.0
SCORE_TILT_ADVISORY_UNCONFIRMED = 70  # boolean tilt_compensation_used True/None
SCORE_TILT_ADVISORY_FALSE = 50        # boolean explicitly False

# L3I_CP_007 baseline bands (km): <=5 / <=10 / <=20 / <=40 / >40.
BASELINE_OK_MAX = 5.0
BASELINE_GOOD_MAX = 10.0
BASELINE_LONG_MAX = 20.0
BASELINE_EXCESSIVE_MAX = 40.0

# L3I_CP_008 / L3I_CP_010 "undeclared / missing" unconfirmed mid-band.
SCORE_UNCONFIRMED = 70

# L3I_CP_011 PDOP bands: <=2 / <=4 / <=6 / >6.
PDOP_OK_MAX = 2.0
PDOP_GOOD_MAX = 4.0
PDOP_FAIR_MAX = 6.0

# L3I_CP_012 fix-hold bands (sec): >=5 / 1-4 / <1.
FIXHOLD_OK_MIN = 5.0
FIXHOLD_SHORT_MIN = 1.0

# L3I_CP_013 obstruction bands: sats>=10 & CN0>=40 / sats 7-9 or CN0 30-40 / sats<7 or CN0<30.
OBSTRUCT_SAT_OK_MIN = 10
OBSTRUCT_SAT_LOW_MIN = 7
OBSTRUCT_CN0_OK_MIN = 40.0
OBSTRUCT_CN0_LOW_MIN = 30.0

# L3I_CP_014 ionospheric Kp bands: <=4 / 5-6 / >=7.
KP_OK_MAX = 4.0
KP_STORM_MIN = 7.0
SCORE_KP_MODERATE_SINGLE_FREQ = 60
SCORE_KP_UNAVAILABLE_SINGLE_FREQ = 70  # Kp API miss + single-freq cautious midpoint

# Standalone timing-flag thresholds (hours), from the spec flag conditions.
DELAYED_CAPTURE_MIN_H = 24.0
STALE_CAPTURE_MIN_H = 168.0

# ---- canonical derived (Stage 3a) keys -------------------------------------
D_SIGMA_RATIO = "L2D_CP_001_sigma_relative_to_target"
D_SIGMA_AVAIL = "L2D_CP_002_sigma_available"
D_SIGMA_EXPECTED = "L2D_CP_003_sigma_expected_for_device"
D_AUTO_KNOWN = "L2D_CP_004_antenna_height_auto_known"
D_HEIGHT_AGREE = "L2D_CP_005_antenna_height_agreement"
D_ANT_TYPE_MATCH = "L2D_CP_006_antenna_type_match"
D_DEVICE_ID_MATCH = "L2D_CP_007_device_id_match"
D_TILT_VERIFIABLE = "L2D_CP_008_tilt_verifiable"
D_MOUNTPOINT_MATCH = "L2D_CP_009_mountpoint_match"
D_DELAY_H = "L2D_CP_010_capture_to_flight_delay_hours"
D_BEFORE_FLIGHT = "L2D_CP_011_captured_before_flight"
D_DURING_FLIGHT = "L2D_CP_012_captured_during_flight"
D_KP = "L2D_CP_013_kp_index"
D_DUAL_FREQ = "L2D_CP_014_dual_freq_available"
D_INTEGRITY = "L2D_CP_015_session_integrity_ok"

# ---- canonical source (Stage 2) keys ---------------------------------------
S_FIX_TYPE = "L1F_CP_005_fix_type_at_capture"
S_CORR_AGE = "L1F_CP_006_correction_age_at_capture_sec"
S_FIX_HOLD = "L1F_CP_007_fix_hold_duration_sec"
S_PDOP = "L1F_CP_008_pdop_at_capture"
S_SAT_COUNT = "L1F_CP_009_sat_count_at_capture"
S_CN0 = "L1F_CP_010_cn0_mean_at_capture"
S_TILT_LOGGED = "L1F_CP_013_tilt_logged_deg"
S_DOWNLOAD = "L1F_CP_016_raw_log_download_confirmed"
S_SIG_VALID = "L1F_CP_017_raw_log_signature_valid"
S_DEVICE_TYPE = "L1F_CP_020_device_type"
S_ANTENNA_HEIGHT_M = "L1F_CP_024_antenna_height_m"
S_MEAS_TYPE = "L1F_CP_026_antenna_measurement_type"
S_MEAS_TO_REF = "L1F_CP_027_measured_to_reference"
S_HEIGHT_COUNT = "L1F_CP_028_height_measured_count"
S_TILT_COMP_USED = "L1F_CP_029_tilt_compensation_used"
S_BASELINE_KM = "L1F_CP_030_baseline_length_km"
S_MARK_PHOTO = "L1F_CP_036_mark_photo_captured"


# ---- helpers ---------------------------------------------------------------

def _dv(derived: dict, key: str) -> Any:
    fobj = derived.get(key)
    return fobj.get("value") if isinstance(fobj, dict) else None


def _trace(spec_ind: dict, score, band: str, condition: str, inputs: dict,
           gate_triggered: bool = False, gate_action_spec: str | None = None,
           gate_flag_id: str | None = None, na_redistribute: bool = False,
           flags_raised: list[str] | None = None) -> dict:
    return {
        "indicator_id": spec_ind["indicator_id"],
        "indicator_name": spec_ind["indicator_name"],
        "building_block_id": spec_ind["building_block_id"],
        "weight_in_block": spec_ind["weight_in_block"],
        "score": (None if score is None else round(float(score), 1)),
        "na_redistribute": na_redistribute,
        "band_matched": band,
        "condition_evaluated": condition,
        "input_values": inputs,
        "gate_triggered": gate_triggered,
        "gate_action_spec": gate_action_spec,
        "gate_flag_id": gate_flag_id,
        "flags_raised": list(flags_raised or []),
    }


def _flag_record(spec_flag: dict, condition_value: Any, origin_indicator: str | None,
                 point_id: str) -> dict:
    return {
        "flag_id": spec_flag["flag_id"],
        "flag_name": spec_flag["flag_name"],
        "severity": spec_flag["severity"],
        "raised_at_stage_spec": spec_flag["raised_at_stage"],
        "_origin_stage": "stage3b",
        "_origin_point": point_id,
        "_origin_indicator": origin_indicator,
        "condition_value": condition_value,
    }


# ---- per-indicator eval functions (BB_CP_COMPLETE) -------------------------

def _l3i_001_sigma(ind, sf, derived, fi, pid):
    """PRIMARY anchor. Three-way graceful degradation."""
    ratio = _dv(derived, D_SIGMA_RATIO)
    avail = _dv(derived, D_SIGMA_AVAIL)
    expected = _dv(derived, D_SIGMA_EXPECTED)
    inputs = {"sigma_relative_to_target": ratio, "sigma_available": avail,
              "sigma_expected_for_device": expected,
              "bands_x_target": {"ok_max": SIGMA_OK_MAX, "marginal_max": SIGMA_MARGINAL_MAX,
                                 "high_max": SIGMA_HIGH_MAX}}
    if avail and ratio is not None:
        if ratio <= SIGMA_OK_MAX:
            return _trace(ind, 100, "sigma_ok", f"sigma_ratio <= {SIGMA_OK_MAX}x target", inputs), []
        if ratio <= SIGMA_MARGINAL_MAX:
            fl = [_flag_record(fi["FLG_CP_006"], ratio, ind["indicator_id"], pid)]
            return _trace(ind, 70, "sigma_marginal", f"{SIGMA_OK_MAX} < ratio <= {SIGMA_MARGINAL_MAX}x",
                          inputs, flags_raised=[f["flag_id"] for f in fl]), fl
        if ratio <= SIGMA_HIGH_MAX:
            fl = [_flag_record(fi["FLG_CP_007"], ratio, ind["indicator_id"], pid)]
            return _trace(ind, 30, "sigma_high", f"{SIGMA_MARGINAL_MAX} < ratio <= {SIGMA_HIGH_MAX}x",
                          inputs, flags_raised=[f["flag_id"] for f in fl]), fl
        fl = [_flag_record(fi["FLG_CP_008"], ratio, ind["indicator_id"], pid)]
        return _trace(ind, 0, "sigma_reject", f"ratio > {SIGMA_HIGH_MAX}x target", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    # sigma absent
    if expected is True:
        fl = [_flag_record(fi["FLG_CP_009"], {"available": avail, "expected": expected},
                           ind["indicator_id"], pid)]
        return _trace(ind, SCORE_SIGMA_NOT_EXPORTED, "sigma_absent_expected",
                      "sigma absent AND expected-for-device -> re-export", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    # absent AND not expected (device limit) -> N/A, weight redistributes
    return _trace(ind, None, "sigma_na_redistribute",
                  "sigma absent AND NOT expected (device limit) -> N/A; weight redistributes "
                  "within COMPLETE", inputs, na_redistribute=True), []


def _l3i_002_fix_type(ind, sf, derived, fi, pid):
    """Per-point completeness gate. FLOAT/AUTONOMOUS -> 0 + gate (flag fires at 3c)."""
    fix = sf.get(S_FIX_TYPE)
    inputs = {"fix_type_at_capture": fix}
    if fix == "FIXED":
        return _trace(ind, 100, "fixed", "fix_type_at_capture == FIXED", inputs), []
    if fix == "FLOAT":
        return _trace(ind, 0, "float_gate",
                      "fix_type_at_capture == FLOAT -> per-point completeness gate "
                      "(flag fires at Stage 3c)", inputs, gate_triggered=True,
                      gate_action_spec=ind["gate_action"], gate_flag_id="FLG_CP_004"), []
    if fix == "AUTONOMOUS":
        return _trace(ind, 0, "autonomous_gate",
                      "fix_type_at_capture == AUTONOMOUS -> per-point completeness gate "
                      "(flag fires at Stage 3c)", inputs, gate_triggered=True,
                      gate_action_spec=ind["gate_action"], gate_flag_id="FLG_CP_005"), []
    return _trace(ind, SCORE_UNCONFIRMED, "fix_type_unconfirmed",
                  "fix_type_at_capture null/unknown -> unconfirmed", inputs), []


def _l3i_003_correction_age(ind, sf, derived, fi, pid):
    age = sf.get(S_CORR_AGE)
    inputs = {"correction_age_at_capture_sec": age,
              "bands_sec": {"ok": CORR_AGE_OK_MAX, "good": CORR_AGE_GOOD_MAX,
                            "stale_mid": CORR_AGE_STALE_MID_MAX, "stale": CORR_AGE_STALE_MAX}}
    if age is None:
        return _trace(ind, None, "correction_age_na_redistribute",
                      "correction_age absent (not in export) -> N/A; weight redistributes",
                      inputs, na_redistribute=True), []
    if age <= CORR_AGE_OK_MAX:
        return _trace(ind, 100, "corr_age_ok", f"age <= {CORR_AGE_OK_MAX}s", inputs), []
    if age <= CORR_AGE_GOOD_MAX:
        return _trace(ind, 88, "corr_age_good", f"{CORR_AGE_OK_MAX} < age <= {CORR_AGE_GOOD_MAX}s", inputs), []
    if age <= CORR_AGE_STALE_MID_MAX:
        fl = [_flag_record(fi["FLG_CP_010"], age, ind["indicator_id"], pid)]
        return _trace(ind, 60, "corr_age_stale_mid", f"{CORR_AGE_GOOD_MAX} < age <= {CORR_AGE_STALE_MID_MAX}s",
                      inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    if age <= CORR_AGE_STALE_MAX:
        fl = [_flag_record(fi["FLG_CP_010"], age, ind["indicator_id"], pid)]
        return _trace(ind, 30, "corr_age_stale", f"{CORR_AGE_STALE_MID_MAX} < age <= {CORR_AGE_STALE_MAX}s",
                      inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_flag_record(fi["FLG_CP_011"], age, ind["indicator_id"], pid)]
    return _trace(ind, 0, "corr_age_lost", f"age > {CORR_AGE_STALE_MAX}s", inputs,
                  flags_raised=[f["flag_id"] for f in fl]), fl


def _l3i_004_log_integrity(ind, sf, derived, fi, pid):
    download = sf.get(S_DOWNLOAD)
    sig = sf.get(S_SIG_VALID)
    dtype = sf.get(S_DEVICE_TYPE)
    inputs = {"raw_log_download_confirmed": download, "raw_log_signature_valid": sig,
              "device_type": dtype}
    if sig is False:
        fl = [_flag_record(fi["FLG_CP_028"], sig, ind["indicator_id"], pid)]
        return _trace(ind, 30, "log_tampered", "raw_log_signature_valid == False", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    if download is False:
        fl = [_flag_record(fi["FLG_CP_027"], download, ind["indicator_id"], pid)]
        return _trace(ind, 50, "download_unconfirmed", "raw_log_download_confirmed == False", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    if download is True and sig is True:
        return _trace(ind, 100, "downloaded_signed", "downloaded AND signature valid", inputs), []
    if download is True and sig is None:
        return _trace(ind, 80, "downloaded_sig_na",
                      "downloaded AND signature N/A (Emlid / OTHER)", inputs), []
    return _trace(ind, SCORE_UNCONFIRMED, "log_unconfirmed",
                  "download status null -> unconfirmed", inputs), []


# ---- BB_CP_SETUP -----------------------------------------------------------

def _l3i_005_antenna_height(ind, sf, derived, fi, pid):
    """Per-point setup gate. absent + not auto-known -> 0 + gate (flag fires at 3c)."""
    auto = _dv(derived, D_AUTO_KNOWN)
    height = sf.get(S_ANTENNA_HEIGHT_M)
    meas = sf.get(S_MEAS_TYPE)
    ref = sf.get(S_MEAS_TO_REF)
    count = sf.get(S_HEIGHT_COUNT)
    agree_struct = _dv(derived, D_HEIGHT_AGREE)
    agreement = agree_struct.get("agreement") if isinstance(agree_struct, dict) else None
    inputs = {"antenna_height_auto_known": auto, "antenna_height_m": height,
              "antenna_measurement_type": meas, "measured_to_reference": ref,
              "height_measured_count": count, "antenna_height_agreement": agreement}
    if auto is True:
        return _trace(ind, 100, "auto_known_factory",
                      "antenna_height_auto_known=True (CB_X / AEROPOINT) -> 100", inputs), []
    if height is None and auto is False:
        return _trace(ind, 0, "height_missing_gate",
                      "antenna_height_m absent AND auto_known=False -> per-point setup gate "
                      "(flag fires at Stage 3c)", inputs, gate_triggered=True,
                      gate_action_spec=ind["gate_action"], gate_flag_id="FLG_CP_003"), []
    if agreement is False:
        return _trace(ind, 55, "height_conflicts_device",
                      "height present but conflicts with device-reported (review)", inputs), []
    is_vertical = meas == "VERTICAL"
    is_arp = ref == "ARP"
    corroborated = isinstance(count, int) and count >= 3
    if is_vertical and is_arp and corroborated and agreement in (True, None):
        return _trace(ind, 100, "dgps_gold_standard",
                      "VERTICAL AND ARP AND count>=3 AND device-agreement OK-or-NA", inputs), []
    if is_vertical and not corroborated:
        return _trace(ind, 88, "single_vertical", "single VERTICAL measurement (count<3)", inputs), []
    if meas == "SLANT":
        return _trace(ind, 72, "slant", "antenna_measurement_type=SLANT (less precise)", inputs), []
    return _trace(ind, SCORE_UNCONFIRMED, "height_partial",
                  "height entered but missing top-band criteria -> partial", inputs), []


def _l3i_006_pole_stability(ind, sf, derived, fi, pid):
    verifiable = _dv(derived, D_TILT_VERIFIABLE)
    tilt = sf.get(S_TILT_LOGGED)
    tilt_used = sf.get(S_TILT_COMP_USED)
    inputs = {"tilt_verifiable": verifiable, "tilt_logged_deg": tilt,
              "tilt_compensation_used": tilt_used,
              "bands_deg": {"ok_max": TILT_OK_MAX, "warn_max": TILT_WARN_MAX}}
    if verifiable and tilt is not None:
        if tilt <= TILT_OK_MAX:
            return _trace(ind, 100, "tilt_ok", f"verified tilt <= {TILT_OK_MAX} deg", inputs), []
        if tilt <= TILT_WARN_MAX:
            return _trace(ind, 70, "tilt_warn", f"{TILT_OK_MAX} < tilt <= {TILT_WARN_MAX} deg", inputs), []
        fl = [_flag_record(fi["FLG_CP_014"], tilt, ind["indicator_id"], pid)]
        return _trace(ind, 30, "tilt_high", f"verified tilt > {TILT_WARN_MAX} deg", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    # non-tilt-comp / unverifiable: tilt_compensation_used is advisory only
    if tilt_used is False:
        return _trace(ind, SCORE_TILT_ADVISORY_FALSE, "tilt_advisory_false",
                      "non-tilt-comp device; tilt_compensation_used explicitly False", inputs), []
    return _trace(ind, SCORE_TILT_ADVISORY_UNCONFIRMED, "tilt_advisory_unconfirmed",
                  "non-tilt-comp device; tilt_compensation_used True/None (advisory only)", inputs), []


def _l3i_007_baseline(ind, sf, derived, fi, pid):
    bl = sf.get(S_BASELINE_KM)
    inputs = {"baseline_length_km": bl,
              "bands_km": {"ok": BASELINE_OK_MAX, "good": BASELINE_GOOD_MAX,
                           "long": BASELINE_LONG_MAX, "excessive": BASELINE_EXCESSIVE_MAX}}
    if bl is None:
        return _trace(ind, SCORE_UNCONFIRMED, "baseline_unconfirmed",
                      "baseline_length_km null -> unconfirmed", inputs), []
    if bl <= BASELINE_OK_MAX:
        return _trace(ind, 100, "baseline_ok", f"baseline <= {BASELINE_OK_MAX}km", inputs), []
    if bl <= BASELINE_GOOD_MAX:
        return _trace(ind, 88, "baseline_good", f"{BASELINE_OK_MAX} < baseline <= {BASELINE_GOOD_MAX}km", inputs), []
    if bl <= BASELINE_LONG_MAX:
        fl = [_flag_record(fi["FLG_CP_012"], bl, ind["indicator_id"], pid)]
        return _trace(ind, 70, "baseline_long", f"{BASELINE_GOOD_MAX} < baseline <= {BASELINE_LONG_MAX}km",
                      inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    if bl <= BASELINE_EXCESSIVE_MAX:
        fl = [_flag_record(fi["FLG_CP_013"], bl, ind["indicator_id"], pid)]
        return _trace(ind, 40, "baseline_excessive_mid",
                      f"{BASELINE_LONG_MAX} < baseline <= {BASELINE_EXCESSIVE_MAX}km", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_flag_record(fi["FLG_CP_013"], bl, ind["indicator_id"], pid)]
    return _trace(ind, 20, "baseline_excessive", f"baseline > {BASELINE_EXCESSIVE_MAX}km", inputs,
                  flags_raised=[f["flag_id"] for f in fl]), fl


def _l3i_008_ntrip(ind, sf, derived, fi, pid):
    match = _dv(derived, D_MOUNTPOINT_MATCH)
    inputs = {"mountpoint_match": match}
    if match is True:
        return _trace(ind, 100, "mountpoint_match", "ntrip_mountpoint == expected_mountpoint", inputs), []
    if match is False:
        fl = [_flag_record(fi["FLG_CP_022"], match, ind["indicator_id"], pid)]
        return _trace(ind, 40, "mountpoint_mismatch", "ntrip_mountpoint != expected_mountpoint", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    return _trace(ind, SCORE_UNCONFIRMED, "mountpoint_undeclared",
                  "expected_mountpoint not declared -> unconfirmed", inputs), []


def _l3i_009_antenna_type(ind, sf, derived, fi, pid):
    match = _dv(derived, D_ANT_TYPE_MATCH)
    inputs = {"antenna_type_match": match}
    if match is True:
        return _trace(ind, 100, "antenna_type_match", "form antenna_model == device antenna_type", inputs), []
    if match is False:
        fl = [_flag_record(fi["FLG_CP_030"], match, ind["indicator_id"], pid)]
        return _trace(ind, 60, "antenna_type_mismatch", "form antenna_model != device antenna_type", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    return _trace(ind, SCORE_UNCONFIRMED, "antenna_type_unconfirmed",
                  "antenna_model or antenna_type null -> unconfirmed", inputs), []


def _l3i_010_device_id(ind, sf, derived, fi, pid):
    match = _dv(derived, D_DEVICE_ID_MATCH)
    inputs = {"device_id_match": match}
    if match is True:
        return _trace(ind, 100, "device_id_match", "form device_id == device device_id", inputs), []
    if match is False:
        fl = [_flag_record(fi["FLG_CP_016"], match, ind["indicator_id"], pid)]
        return _trace(ind, 60, "device_id_mismatch", "form device_id != device device_id (reviewer-blocking)",
                      inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    return _trace(ind, SCORE_UNCONFIRMED, "device_id_unconfirmed",
                  "either device_id missing -> unconfirmed", inputs), []


# ---- BB_CP_ENV -------------------------------------------------------------

def _l3i_011_pdop(ind, sf, derived, fi, pid):
    pdop = sf.get(S_PDOP)
    inputs = {"pdop_at_capture": pdop,
              "bands": {"ok": PDOP_OK_MAX, "good": PDOP_GOOD_MAX, "fair": PDOP_FAIR_MAX}}
    if pdop is None:
        return _trace(ind, SCORE_UNCONFIRMED, "pdop_unconfirmed", "pdop_at_capture null -> unconfirmed", inputs), []
    if pdop <= PDOP_OK_MAX:
        return _trace(ind, 100, "pdop_ok", f"pdop <= {PDOP_OK_MAX}", inputs), []
    if pdop <= PDOP_GOOD_MAX:
        return _trace(ind, 80, "pdop_good", f"{PDOP_OK_MAX} < pdop <= {PDOP_GOOD_MAX}", inputs), []
    if pdop <= PDOP_FAIR_MAX:
        return _trace(ind, 55, "pdop_fair", f"{PDOP_GOOD_MAX} < pdop <= {PDOP_FAIR_MAX}", inputs), []
    fl = [_flag_record(fi["FLG_CP_019"], pdop, ind["indicator_id"], pid)]
    return _trace(ind, 30, "pdop_poor", f"pdop > {PDOP_FAIR_MAX}", inputs,
                  flags_raised=[f["flag_id"] for f in fl]), fl


def _l3i_012_fix_hold(ind, sf, derived, fi, pid):
    hold = sf.get(S_FIX_HOLD)
    inputs = {"fix_hold_duration_sec": hold,
              "bands_sec": {"ok_min": FIXHOLD_OK_MIN, "short_min": FIXHOLD_SHORT_MIN}}
    if hold is None:
        return _trace(ind, None, "fix_hold_na_redistribute",
                      "fix_hold absent -> N/A; weight redistributes within ENV", inputs,
                      na_redistribute=True), []
    if hold >= FIXHOLD_OK_MIN:
        return _trace(ind, 100, "fix_hold_ok", f"hold >= {FIXHOLD_OK_MIN}s", inputs), []
    if hold >= FIXHOLD_SHORT_MIN:
        fl = [_flag_record(fi["FLG_CP_020"], hold, ind["indicator_id"], pid)]
        return _trace(ind, 60, "fix_hold_short", f"{FIXHOLD_SHORT_MIN} <= hold < {FIXHOLD_OK_MIN}s", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    fl = [_flag_record(fi["FLG_CP_021"], hold, ind["indicator_id"], pid)]
    return _trace(ind, 30, "no_fix_hold", f"hold < {FIXHOLD_SHORT_MIN}s", inputs,
                  flags_raised=[f["flag_id"] for f in fl]), fl


def _l3i_013_obstruction(ind, sf, derived, fi, pid):
    sats = sf.get(S_SAT_COUNT)
    cn0 = sf.get(S_CN0)
    inputs = {"sat_count_at_capture": sats, "cn0_mean_at_capture": cn0,
              "bands": {"sat_ok": OBSTRUCT_SAT_OK_MIN, "sat_low": OBSTRUCT_SAT_LOW_MIN,
                        "cn0_ok": OBSTRUCT_CN0_OK_MIN, "cn0_low": OBSTRUCT_CN0_LOW_MIN}}
    if sats is None or cn0 is None:
        return _trace(ind, SCORE_UNCONFIRMED, "obstruction_unconfirmed",
                      "sat_count or cn0 null -> unconfirmed", inputs), []
    if sats < OBSTRUCT_SAT_LOW_MIN or cn0 < OBSTRUCT_CN0_LOW_MIN:
        fl = [_flag_record(fi["FLG_CP_017"], {"sats": sats, "cn0": cn0}, ind["indicator_id"], pid)]
        return _trace(ind, 35, "obstructed", f"sats < {OBSTRUCT_SAT_LOW_MIN} OR cn0 < {OBSTRUCT_CN0_LOW_MIN}",
                      inputs, flags_raised=[f["flag_id"] for f in fl]), fl
    if sats >= OBSTRUCT_SAT_OK_MIN and cn0 >= OBSTRUCT_CN0_OK_MIN:
        return _trace(ind, 100, "clear_sky", f"sats >= {OBSTRUCT_SAT_OK_MIN} AND cn0 >= {OBSTRUCT_CN0_OK_MIN}",
                      inputs), []
    return _trace(ind, 70, "moderate_obstruction",
                  f"sats {OBSTRUCT_SAT_LOW_MIN}-9 OR cn0 {OBSTRUCT_CN0_LOW_MIN}-40", inputs), []


def _l3i_014_iono(ind, sf, derived, fi, pid):
    kp_struct = _dv(derived, D_KP)
    kp = kp_struct.get("kp") if isinstance(kp_struct, dict) else None
    kp_status = kp_struct.get("status") if isinstance(kp_struct, dict) else "ABSENT"
    dual = _dv(derived, D_DUAL_FREQ)
    inputs = {"kp_index": kp, "kp_status": kp_status, "dual_freq_available": dual,
              "bands": {"ok_max": KP_OK_MAX, "storm_min": KP_STORM_MIN}}
    if dual is True:
        return _trace(ind, 100, "dual_freq_mitigated", "dual_freq=True -> iono mitigated regardless of Kp", inputs), []
    if kp is not None and kp <= KP_OK_MAX:
        return _trace(ind, 100, "kp_low", f"kp <= {KP_OK_MAX} (single-freq, low risk)", inputs), []
    if kp is not None and kp >= KP_STORM_MIN:
        fl = [_flag_record(fi["FLG_CP_018"], {"kp": kp, "dual_freq": dual}, ind["indicator_id"], pid)]
        return _trace(ind, 40, "iono_storm_single_freq", f"kp >= {KP_STORM_MIN} AND single-frequency", inputs,
                      flags_raised=[f["flag_id"] for f in fl]), fl
    if kp is not None:
        return _trace(ind, SCORE_KP_MODERATE_SINGLE_FREQ, "kp_moderate_single_freq",
                      f"{KP_OK_MAX} < kp < {KP_STORM_MIN} AND single-frequency", inputs), []
    return _trace(ind, SCORE_KP_UNAVAILABLE_SINGLE_FREQ, "kp_unavailable_single_freq",
                  "Kp unavailable AND single-freq -> cautious midpoint", inputs), []


_DISPATCH = {
    "L3I_CP_001": _l3i_001_sigma, "L3I_CP_002": _l3i_002_fix_type,
    "L3I_CP_003": _l3i_003_correction_age, "L3I_CP_004": _l3i_004_log_integrity,
    "L3I_CP_005": _l3i_005_antenna_height, "L3I_CP_006": _l3i_006_pole_stability,
    "L3I_CP_007": _l3i_007_baseline, "L3I_CP_008": _l3i_008_ntrip,
    "L3I_CP_009": _l3i_009_antenna_type, "L3I_CP_010": _l3i_010_device_id,
    "L3I_CP_011": _l3i_011_pdop, "L3I_CP_012": _l3i_012_fix_hold,
    "L3I_CP_013": _l3i_013_obstruction, "L3I_CP_014": _l3i_014_iono,
}


# ---- standalone (non-indicator) threshold flags ----------------------------

def _standalone_flags(sf: dict, derived: dict, fi: dict, pid: str) -> list[dict]:
    """5 threshold flags not tied to a scoring indicator: timing (023-026) +
    mark photo (029)."""
    flags: list[dict] = []
    delay = _dv(derived, D_DELAY_H)
    before = _dv(derived, D_BEFORE_FLIGHT)
    during = _dv(derived, D_DURING_FLIGHT)
    photo = sf.get(S_MARK_PHOTO)

    if before is True:
        flags.append(_flag_record(fi["FLG_CP_025"], {"captured_before_flight": True}, None, pid))
    elif during is True:
        flags.append(_flag_record(fi["FLG_CP_026"], {"captured_during_flight": True}, None, pid))
    elif delay is not None:
        if delay > STALE_CAPTURE_MIN_H:
            flags.append(_flag_record(fi["FLG_CP_024"], {"delay_hours": delay}, None, pid))
        elif delay >= DELAYED_CAPTURE_MIN_H:
            flags.append(_flag_record(fi["FLG_CP_023"], {"delay_hours": delay}, None, pid))

    if photo is False:
        flags.append(_flag_record(fi["FLG_CP_029"], {"mark_photo_captured": False}, None, pid))
    return flags


# ---- per-point + survey run ------------------------------------------------

def _evaluate_point(point3a: dict, sf: dict, spec: dict, fi: dict) -> dict:
    pid = point3a["point_id"]
    derived = point3a.get("derived_fields", {})
    traces: dict[str, dict] = {}
    point_flags: list[dict] = []

    for ind in spec.get("indicators", []):
        ind_id = ind["indicator_id"]
        trace, flags = _DISPATCH[ind_id](ind, sf, derived, fi, pid)
        traces[ind_id + "_" + ind["indicator_name"]] = trace
        point_flags.extend(flags)

    point_flags.extend(_standalone_flags(sf, derived, fi, pid))

    return {
        "point_id": pid,
        "device_type": sf.get(S_DEVICE_TYPE),
        "device_role": point3a.get("device_role"),
        "indicator_traces": dict(sorted(traces.items())),
        "flags_raised_stage3b_point": point_flags,
    }


def run(config: dict, project_root: Path, spec: dict, stage3a_data: dict,
        stage2_data: dict) -> dict:
    fi = {f["flag_id"]: f for f in spec.get("flags", [])}
    sf_by_point = {p["point_id"]: p.get("source_fields", {})
                   for p in stage2_data.get("points", [])}
    expected_count = spec["_meta"]["counts"]["indicators"]

    point_records: list[dict] = []
    all_flags: list[dict] = []
    notes: list[str] = [
        "Option B per-indicator eval functions: band scores + flag names come from "
        "each indicator's spec threshold_summary (prose-only), pinned as named "
        "constants surfaced in stage3b_meta.tuneables.",
        "L3I_CP_001 sigma / L3I_CP_003 correction-age / L3I_CP_012 fix-hold can return "
        "score=None with na_redistribute=True; Stage 3c drops those and renormalises the "
        "block weights (sigma redistributes within COMPLETE, fix-hold within ENV).",
        "L3I_CP_002 fix gate + L3I_CP_005 height gate: score 0, gate_triggered=True, "
        "gate_flag_id set (FLG_CP_004/005 / FLG_CP_003); the flag fires at Stage 3c "
        "(internal_gate) where FLG_CP_004 severity escalation (<5 effective CPs) applies.",
        "5 standalone threshold flags (FLG_CP_023/024/025/026 timing, FLG_CP_029 mark "
        "photo) are not tied to a scoring indicator and fire from Stage 3a signals.",
    ]

    for p3a in stage3a_data.get("points", []):
        sf = sf_by_point.get(p3a["point_id"], {})
        rec = _evaluate_point(p3a, sf, spec, fi)
        if len(rec["indicator_traces"]) != expected_count:
            notes.append(f"{rec['point_id']}: produced {len(rec['indicator_traces'])} "
                         f"indicators, expected {expected_count}.")
        point_records.append(rec)
        all_flags.extend(rec["flags_raised_stage3b_point"])

    counts_by_band: dict[str, int] = {}
    gates_triggered: list[str] = []
    na_redistributed: list[str] = []
    for rec in point_records:
        for t in rec["indicator_traces"].values():
            counts_by_band[t["band_matched"]] = counts_by_band.get(t["band_matched"], 0) + 1
            if t.get("gate_triggered"):
                gates_triggered.append(f"{rec['point_id']}:{t['indicator_id']}->{t.get('gate_flag_id')}")
            if t.get("na_redistribute"):
                na_redistributed.append(f"{rec['point_id']}:{t['indicator_id']}")

    return {
        "points": point_records,
        "flags_raised_stage3b": all_flags,
        "stage3b_notes": notes,
        "stage3b_meta": {
            "expected_indicator_count_per_point": expected_count,
            "point_count": len(point_records),
            "counts_by_band": dict(sorted(counts_by_band.items())),
            "gates_triggered": gates_triggered,
            "na_redistributed": na_redistributed,
            "tuneables": {
                "SIGMA_OK_MAX": SIGMA_OK_MAX, "SIGMA_MARGINAL_MAX": SIGMA_MARGINAL_MAX,
                "SIGMA_HIGH_MAX": SIGMA_HIGH_MAX, "SCORE_SIGMA_NOT_EXPORTED": SCORE_SIGMA_NOT_EXPORTED,
                "CORR_AGE_OK_MAX": CORR_AGE_OK_MAX, "CORR_AGE_GOOD_MAX": CORR_AGE_GOOD_MAX,
                "CORR_AGE_STALE_MID_MAX": CORR_AGE_STALE_MID_MAX, "CORR_AGE_STALE_MAX": CORR_AGE_STALE_MAX,
                "TILT_OK_MAX": TILT_OK_MAX, "TILT_WARN_MAX": TILT_WARN_MAX,
                "SCORE_TILT_ADVISORY_UNCONFIRMED": SCORE_TILT_ADVISORY_UNCONFIRMED,
                "SCORE_TILT_ADVISORY_FALSE": SCORE_TILT_ADVISORY_FALSE,
                "BASELINE_OK_MAX": BASELINE_OK_MAX, "BASELINE_GOOD_MAX": BASELINE_GOOD_MAX,
                "BASELINE_LONG_MAX": BASELINE_LONG_MAX, "BASELINE_EXCESSIVE_MAX": BASELINE_EXCESSIVE_MAX,
                "PDOP_OK_MAX": PDOP_OK_MAX, "PDOP_GOOD_MAX": PDOP_GOOD_MAX, "PDOP_FAIR_MAX": PDOP_FAIR_MAX,
                "FIXHOLD_OK_MIN": FIXHOLD_OK_MIN, "FIXHOLD_SHORT_MIN": FIXHOLD_SHORT_MIN,
                "OBSTRUCT_SAT_OK_MIN": OBSTRUCT_SAT_OK_MIN, "OBSTRUCT_SAT_LOW_MIN": OBSTRUCT_SAT_LOW_MIN,
                "OBSTRUCT_CN0_OK_MIN": OBSTRUCT_CN0_OK_MIN, "OBSTRUCT_CN0_LOW_MIN": OBSTRUCT_CN0_LOW_MIN,
                "KP_OK_MAX": KP_OK_MAX, "KP_STORM_MIN": KP_STORM_MIN,
                "SCORE_KP_MODERATE_SINGLE_FREQ": SCORE_KP_MODERATE_SINGLE_FREQ,
                "SCORE_KP_UNAVAILABLE_SINGLE_FREQ": SCORE_KP_UNAVAILABLE_SINGLE_FREQ,
                "SCORE_UNCONFIRMED": SCORE_UNCONFIRMED,
                "DELAYED_CAPTURE_MIN_H": DELAYED_CAPTURE_MIN_H, "STALE_CAPTURE_MIN_H": STALE_CAPTURE_MIN_H,
            },
        },
    }


def print_summary(data: dict) -> None:
    mm = data["stage3b_meta"]
    print(f"  indicators per point: {mm['expected_indicator_count_per_point']}  "
          f"points: {mm['point_count']}")
    for p in data["points"]:
        scores = {t["indicator_id"].replace("L3I_CP_", ""): t["score"]
                  for t in p["indicator_traces"].values()}
        print(f"    - {p['point_id']} ({p['device_type']}): {scores}  "
              f"flags={len(p['flags_raised_stage3b_point'])}")
    print(f"  flags raised at Stage 3b: {len(data['flags_raised_stage3b'])}")
    for fl in data["flags_raised_stage3b"]:
        print(f"    FLAG  [{fl['_origin_point']}] {fl['flag_id']} {fl['flag_name']} ({fl['severity']})")
    if mm["gates_triggered"]:
        print(f"  per-point gates (flags fire at 3c): {mm['gates_triggered']}")
    if mm["na_redistributed"]:
        print(f"  N/A weight-redistributed: {mm['na_redistributed']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check Point PPK Stage 3b indicators")
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
    data = run(config, root, spec, data3a, data2)

    out_path = root / config["outputs"]["stage3_indicators"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    print(f"Stage 3b indicators -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
