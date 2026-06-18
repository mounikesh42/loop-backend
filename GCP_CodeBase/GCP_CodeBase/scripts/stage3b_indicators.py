#!/usr/bin/env python3
"""Stage 3b - compute the 10 L3I_GCP_* indicators PER POINT (per spec sheet 04).

The spec stores threshold bands as prose `threshold_summary` strings (no
machine-readable condition expressions), so this module follows Option B from
the BUILD_PROMPT_TEMPLATE: one Python function per indicator. Spec prose drives
the bands; weights come from the spec at runtime (spec_ind["weight_in_block"]).
Engineering thresholds are named constants surfaced in stage3b_meta.tuneables.

GCP runs the evaluation once per occupation. Each indicator reads that point's
derived_fields (Stage 3a) and, where the spec input is a source field, that
point's source_fields (Stage 2). Per-point trace block:
  {
    "indicator_id", "indicator_name", "building_block_id", "weight_in_block",
    "score", "band_matched", "condition_evaluated", "input_values",
    "gate_triggered", "gate_action_spec", "flags_raised"
  }

GCP-specific deltas vs the base-station build:
  - L3I_GCP_002 occupation_integrity_score is DEVICE-TYPE-AWARE: DGPS uses the
    oplog bands (shutdown -> 30 + FLG_GCP_004; battery_min >= 20%); CB_X /
    AEROPOINT / OTHER have no oplog and score off RINEX continuity
    (session_integrity_ok True -> 100, else 40).
  - L3I_GCP_004 occupation_continuity_score bands on RAW cycle-slip count
    (<5 / 5-20 / >20), not slips-per-hour.
  - L3I_GCP_005 gives auto-known devices (CB_X / AEROPOINT) 100 by definition.
  - L3I_GCP_009 acquisition bands are 100/88/65/30 at <60/60-119/120-299/>=300.
  - L3I_GCP_010 ionospheric risk has three Kp bands (<=4 / 5-6 / >=7).

Flag stages (template rule 4):
  - threshold flags fire HERE: FLG_GCP_004, 005, 006, 007, 008, 009, 010, 013.
  - internal_gate flags (FLG_GCP_002 from L3I_005, FLG_GCP_003 from L3I_001)
    fire at Stage 3c; this module only marks gate_triggered=True.
  - composite flags (FLG_GCP_011, 014) already fired at Stage 3a.

Spec inconsistency handled: L3I_GCP_007 prose says "mismatch -> 40 + FLAG" but
no antenna-type-mismatch flag exists among the 14 spec flags; we score 40 and
raise no flag, recording the gap in stage3b_notes. No timestamps live in the
data block (determinism rule 3).
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

# ---- engineering tuneables (spec prose is qualitative; pinned here for audit) ----

# L3I_GCP_008 multipath C/N0-variance band boundaries (dB-Hz): low <=2.5,
# moderate <=4.0, high >4.0 (spec prose).
MULTIPATH_STD_LOW_DBHZ = 2.5
MULTIPATH_STD_HIGH_DBHZ = 4.0

# L3I_GCP_010 ionospheric Kp bands (spec prose: <=4 / 5-6 / >=7).
KP_TOP_MAX = 4.0
KP_STORM_MIN = 7.0

# L3I_GCP_002 DGPS battery adequacy (spec ">= 20%").
BATTERY_MIN_ADEQUATE_PCT = 20.0

# L3I_GCP_004 raw cycle-slip count bands (spec prose: <5 / 5-20 / >20).
SLIPS_CLEAN_MAX = 5
SLIPS_ELEVATED_MAX = 20

# L3I_GCP_009 slow-acquisition flag threshold (spec ">= 300s").
ACQUISITION_SLOW_SEC = 300

# Null-input / unconfirmed mid-band scores (engineering picks; never a silent
# top-band pass when an input is missing).
SCORE_UNCONFIRMED = 60
SCORE_PARTIAL = 80
SCORE_DGPS_BATTERY_LOW = 75


# ---- canonical field keys --------------------------------------------------

# Derived (Stage 3a) keys.
D_COVERAGE = "L2D_GCP_001_occupation_coverage_ratio"
D_PRE_BUF = "L2D_GCP_002_pre_flight_buffer_sec"
D_POST_BUF = "L2D_GCP_003_post_flight_buffer_sec"
D_DUAL_FREQ = "L2D_GCP_004_dual_freq_available"
D_CYCLE_SLIP = "L2D_GCP_005_cycle_slip_count"
D_GAP_GT_5S = "L2D_GCP_006_gap_gt_5s_count"
D_GAP_GT_60S = "L2D_GCP_007_any_gap_gt_60s"
D_CN0_MEAN = "L2D_GCP_008_cn0_mean_dbhz"
D_MULTIPATH = "L2D_GCP_009_multipath_risk_level"
D_ACQUISITION = "L2D_GCP_012_device_acquisition_time_sec"
D_VER_SUPPORTED = "L2D_GCP_013_rinex_version_supported"
D_HEADER = "L2D_GCP_014_header_completeness"
D_CONSTELLATION = "L2D_GCP_015_constellation_count"
D_INTEGRITY = "L2D_GCP_016_session_integrity_ok"
D_ANT_TYPE_MATCH = "L2D_GCP_017_antenna_type_match"
D_ANT_HEIGHT_AGREE = "L2D_GCP_018_antenna_height_agreement"
D_DEVICE_ID_MATCH = "L2D_GCP_019_device_id_match"
D_KP = "L2D_GCP_020_kp_index"
D_AUTO_KNOWN = "L2D_GCP_021_antenna_height_auto_known"

# Source (Stage 2) keys.
S_SESSION_COMPLETED = "L1F_GCP_019_session_completed_normally"
S_SHUTDOWN_COUNT = "L1F_GCP_020_unexpected_shutdown_count"
S_BATTERY_MIN = "L1F_GCP_023_battery_min_pct"
S_LOG_DOWNLOAD = "L1F_GCP_025_raw_log_download_confirmed"
S_DEVICE_TYPE = "L1F_GCP_026_device_type"
S_ANTENNA_HEIGHT_M = "L1F_GCP_030_antenna_height_m"
S_MEAS_TYPE = "L1F_GCP_032_antenna_measurement_type"
S_MEAS_TO_REF = "L1F_GCP_033_measured_to_reference"
S_HEIGHT_COUNT = "L1F_GCP_034_height_measured_count"
S_RINEX_VERSION = "L1F_GCP_007_rinex_version"


# ---- helpers ---------------------------------------------------------------

def _dv(derived: dict, key: str) -> Any:
    """Value of a Stage 3a derived field (or None when absent)."""
    fobj = derived.get(key)
    return fobj.get("value") if isinstance(fobj, dict) else None


def _trace(spec_ind: dict, score: float, band: str, condition: str,
           inputs: dict, gate_triggered: bool = False,
           gate_action_spec: str | None = None,
           flags_raised: list[str] | None = None) -> dict:
    return {
        "indicator_id": spec_ind["indicator_id"],
        "indicator_name": spec_ind["indicator_name"],
        "building_block_id": spec_ind["building_block_id"],
        "weight_in_block": spec_ind["weight_in_block"],
        "score": round(float(score), 1),
        "band_matched": band,
        "condition_evaluated": condition,
        "input_values": inputs,
        "gate_triggered": gate_triggered,
        "gate_action_spec": gate_action_spec,
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


# ---- per-indicator eval functions ------------------------------------------

def _l3i_001(spec_ind: dict, sf: dict, derived: dict, flag_index: dict, pid: str):
    cov = _dv(derived, D_COVERAGE)
    pre = _dv(derived, D_PRE_BUF)
    post = _dv(derived, D_POST_BUF)
    inputs = {"occupation_coverage_ratio": cov, "pre_flight_buffer_sec": pre,
              "post_flight_buffer_sec": post}

    if cov is None or cov < 1.0:
        return _trace(spec_ind, 0, "coverage_gate_triggered",
                      "occupation_coverage_ratio < 1.0 -> internal gate trips (flag fires at Stage 3c)",
                      inputs, gate_triggered=True,
                      gate_action_spec=spec_ind["gate_action"]), []
    if pre is None or post is None:
        return _trace(spec_ind, 72, "buffer_data_missing",
                      "coverage=1.0 but buffer data missing -> bottom full-coverage band", inputs), []
    if pre >= 120 and post >= 60:
        return _trace(spec_ind, 100, "perfect_coverage",
                      "coverage=1.0 AND pre>=120s AND post>=60s", inputs), []
    if pre >= 60:
        return _trace(spec_ind, 88, "good_pre_buffer",
                      "coverage=1.0 AND pre>=60s (pre<120 or post<60)", inputs), []
    return _trace(spec_ind, 72, "short_pre_buffer",
                  "coverage=1.0 AND pre<60s", inputs), []


def _l3i_002(spec_ind: dict, sf: dict, derived: dict, flag_index: dict, pid: str):
    device_type = sf.get(S_DEVICE_TYPE)
    integrity = _dv(derived, D_INTEGRITY)
    integrity_ok = integrity.get("ok") if isinstance(integrity, dict) else integrity
    any_gap60 = _dv(derived, D_GAP_GT_60S)
    completed = sf.get(S_SESSION_COMPLETED)
    shutdowns = sf.get(S_SHUTDOWN_COUNT)
    bat_min = sf.get(S_BATTERY_MIN)
    download = sf.get(S_LOG_DOWNLOAD)
    inputs = {
        "device_type": device_type,
        "session_integrity_ok": integrity_ok,
        "any_gap_gt_60s": any_gap60,
        "session_completed_normally": completed,
        "unexpected_shutdown_count": shutdowns,
        "battery_min_pct": bat_min,
        "raw_log_download_confirmed": download,
    }

    if device_type == "DGPS":
        flags: list[dict] = []
        if download is not True:  # advisory, DGPS only
            flags.append(_flag_record(flag_index["FLG_GCP_005"], download,
                                      spec_ind["indicator_id"], pid))
        if (shutdowns is not None and shutdowns >= 1) or completed is False:
            flags.append(_flag_record(flag_index["FLG_GCP_004"],
                                      {"completed": completed, "shutdowns": shutdowns},
                                      spec_ind["indicator_id"], pid))
            return _trace(spec_ind, 30, "dgps_session_interrupted",
                          "device_type=DGPS AND (unexpected_shutdown_count>=1 OR completed_normally=False)",
                          inputs, flags_raised=[f["flag_id"] for f in flags]), flags
        if completed is None and shutdowns is None and bat_min is None:
            return _trace(spec_ind, SCORE_UNCONFIRMED, "dgps_oplog_unconfirmed",
                          "device_type=DGPS AND oplog absent/null -> unconfirmed", inputs,
                          flags_raised=[f["flag_id"] for f in flags]), flags
        if bat_min is not None and bat_min < BATTERY_MIN_ADEQUATE_PCT:
            return _trace(spec_ind, SCORE_DGPS_BATTERY_LOW, "dgps_battery_low",
                          f"device_type=DGPS, completed AND no shutdown BUT battery_min<{BATTERY_MIN_ADEQUATE_PCT}%",
                          inputs, flags_raised=[f["flag_id"] for f in flags]), flags
        if completed is True and shutdowns == 0:
            return _trace(spec_ind, 100, "dgps_clean",
                          "device_type=DGPS AND completed_normally AND 0 shutdowns AND battery_min adequate",
                          inputs, flags_raised=[f["flag_id"] for f in flags]), flags
        return _trace(spec_ind, SCORE_PARTIAL, "dgps_partial_unconfirmed",
                      "device_type=DGPS, some oplog signals null but session not interrupted",
                      inputs, flags_raised=[f["flag_id"] for f in flags]), flags

    # CB_X / AEROPOINT / OTHER / unknown: oplog expected-absent -> RINEX continuity.
    if integrity_ok is True:
        return _trace(spec_ind, 100, "non_dgps_continuous",
                      f"device_type={device_type}: oplog expected-absent; any_gap_gt_60s=False", inputs), []
    if integrity_ok is False:
        return _trace(spec_ind, 40, "non_dgps_gap",
                      f"device_type={device_type}: oplog expected-absent; any_gap_gt_60s=True", inputs), []
    return _trace(spec_ind, SCORE_UNCONFIRMED, "non_dgps_unconfirmed",
                  f"device_type={device_type}: RINEX continuity signal null -> unconfirmed", inputs), []


def _l3i_003(spec_ind: dict, sf: dict, derived: dict, flag_index: dict, pid: str):
    ver_supported = _dv(derived, D_VER_SUPPORTED)
    header = _dv(derived, D_HEADER)
    header_ok = bool(header.get("complete")) if isinstance(header, dict) else None
    dual = _dv(derived, D_DUAL_FREQ)
    constellation = _dv(derived, D_CONSTELLATION)
    rinex_version = sf.get(S_RINEX_VERSION)
    inputs = {"rinex_version": rinex_version, "rinex_version_supported": ver_supported,
              "header_complete": header_ok, "dual_freq_available": dual,
              "constellation_count": constellation}

    if ver_supported is False:
        flags = [_flag_record(flag_index["FLG_GCP_006"], rinex_version, spec_ind["indicator_id"], pid)]
        return _trace(spec_ind, 35, "version_unsupported",
                      f"rinex_version={rinex_version} not in supported set", inputs,
                      flags_raised=[f["flag_id"] for f in flags]), flags
    if header_ok is False:
        return _trace(spec_ind, 40, "header_incomplete",
                      "RINEX header missing antenna_type/receiver_type/approx_position", inputs), []
    if dual is False:
        return _trace(spec_ind, 70, "single_freq_only",
                      "version supported AND header complete BUT dual_freq=False", inputs), []
    if ver_supported is True and header_ok is True and dual is True:
        return _trace(spec_ind, 100, "format_complete_dual_freq",
                      "version supported AND header complete AND dual-freq", inputs), []
    return _trace(spec_ind, SCORE_UNCONFIRMED, "format_partial_unconfirmed",
                  "some format signals null -> partial confidence", inputs), []


def _l3i_004(spec_ind: dict, sf: dict, derived: dict, flag_index: dict, pid: str):
    slips = _dv(derived, D_CYCLE_SLIP)
    gaps5 = _dv(derived, D_GAP_GT_5S)
    gap60 = _dv(derived, D_GAP_GT_60S)
    inputs = {"cycle_slip_count": slips, "gap_gt_5s_count": gaps5, "any_gap_gt_60s": gap60}

    if gap60 is True or (slips is not None and slips > SLIPS_ELEVATED_MAX):
        return _trace(spec_ind, 40, "major_gap_or_high_slips",
                      f"any_gap_gt_60s=True OR cycle_slip_count>{SLIPS_ELEVATED_MAX}", inputs), []
    if (gaps5 is not None and gaps5 > 0) or (slips is not None and SLIPS_CLEAN_MAX <= slips <= SLIPS_ELEVATED_MAX):
        return _trace(spec_ind, 75, "minor_gaps_or_moderate_slips",
                      f"minor gaps (gap_gt_5s>0) OR {SLIPS_CLEAN_MAX}-{SLIPS_ELEVATED_MAX} slips", inputs), []
    if gaps5 == 0 and slips is not None and slips < SLIPS_CLEAN_MAX:
        return _trace(spec_ind, 100, "clean_continuity",
                      f"no gaps AND cycle_slip_count<{SLIPS_CLEAN_MAX}", inputs), []
    return _trace(spec_ind, SCORE_UNCONFIRMED, "continuity_unconfirmed",
                  "continuity input signals null -> unconfirmed", inputs), []


def _l3i_005(spec_ind: dict, sf: dict, derived: dict, flag_index: dict, pid: str):
    auto_known = _dv(derived, D_AUTO_KNOWN)
    height_m = sf.get(S_ANTENNA_HEIGHT_M)
    meas_type = sf.get(S_MEAS_TYPE)
    ref = sf.get(S_MEAS_TO_REF)
    count = sf.get(S_HEIGHT_COUNT)
    agree_struct = _dv(derived, D_ANT_HEIGHT_AGREE)
    agreement = agree_struct.get("agreement") if isinstance(agree_struct, dict) else None
    inputs = {"antenna_height_auto_known": auto_known, "antenna_height_m": height_m,
              "antenna_measurement_type": meas_type, "measured_to_reference": ref,
              "height_measured_count": count, "antenna_height_agreement": agreement}

    if auto_known is True:
        return _trace(spec_ind, 100, "auto_known_factory",
                      "antenna_height_auto_known=True (CB_X / AEROPOINT) -> 100 by definition", inputs), []
    if height_m is None and auto_known is False:
        return _trace(spec_ind, 0, "height_missing_gate",
                      "antenna_height_m absent AND auto_known=False -> internal gate trips (flag fires at Stage 3c)",
                      inputs, gate_triggered=True, gate_action_spec=spec_ind["gate_action"]), []
    if agreement is False:
        return _trace(spec_ind, 55, "height_conflicts_with_rinex",
                      "antenna_height_m present but disagrees with RINEX antenna_delta_h (review)", inputs), []
    is_vertical = meas_type == "VERTICAL"
    is_arp = ref == "ARP"
    has_corroboration = isinstance(count, int) and count >= 3
    if is_vertical and is_arp and has_corroboration and agreement in (True, None):
        return _trace(spec_ind, 100, "dgps_gold_standard",
                      "VERTICAL AND measured_to=ARP AND height_count>=3 AND RINEX-agreement OK-or-skipped",
                      inputs), []
    if is_vertical and not has_corroboration:
        return _trace(spec_ind, 88, "dgps_single_vertical",
                      "VERTICAL with single measurement (count<3)", inputs), []
    if meas_type == "SLANT":
        return _trace(spec_ind, 72, "dgps_slant",
                      "antenna_measurement_type=SLANT (less precise)", inputs), []
    return _trace(spec_ind, SCORE_UNCONFIRMED, "height_partial_documentation",
                  "height entered but missing top-band criteria -> partial", inputs), []


def _l3i_006(spec_ind: dict, sf: dict, derived: dict, flag_index: dict, pid: str):
    match = _dv(derived, D_DEVICE_ID_MATCH)
    inputs = {"device_id_match": match}
    if match is True:
        return _trace(spec_ind, 100, "device_id_match",
                      "form device_id == RINEX device_id", inputs), []
    if match is False:
        flags = [_flag_record(flag_index["FLG_GCP_010"], match, spec_ind["indicator_id"], pid)]
        return _trace(spec_ind, 50, "device_id_mismatch",
                      "form device_id != RINEX device_id (reviewer-blocking)", inputs,
                      flags_raised=[f["flag_id"] for f in flags]), flags
    return _trace(spec_ind, SCORE_UNCONFIRMED, "device_id_unconfirmed",
                  "form or RINEX device_id missing -> unconfirmed", inputs), []


def _l3i_007(spec_ind: dict, sf: dict, derived: dict, flag_index: dict, pid: str):
    match = _dv(derived, D_ANT_TYPE_MATCH)
    inputs = {"antenna_type_match": match}
    if match is True:
        return _trace(spec_ind, 100, "antenna_type_match",
                      "form antenna_model == RINEX antenna_type", inputs), []
    if match is False:
        # Spec prose says "40 + FLAG" but no antenna-type-mismatch flag is defined
        # among the 14 spec flags -> score 40, raise no flag (see stage3b_notes).
        return _trace(spec_ind, 40, "antenna_type_mismatch",
                      "form antenna_model != RINEX antenna_type (no spec flag defined for this band)",
                      inputs), []
    return _trace(spec_ind, SCORE_UNCONFIRMED, "antenna_type_unconfirmed",
                  "antenna_type or antenna_model null -> unconfirmed", inputs), []


def _l3i_008(spec_ind: dict, sf: dict, derived: dict, flag_index: dict, pid: str):
    mp = _dv(derived, D_MULTIPATH)
    mean_std = mp.get("mean_of_per_sat_cn0_std_dbhz") if isinstance(mp, dict) else None
    cn0_mean = _dv(derived, D_CN0_MEAN)
    inputs = {"mean_of_per_sat_cn0_std_dbhz": mean_std, "cn0_mean_dbhz": cn0_mean,
              "thresholds_dbhz": {"low_max": MULTIPATH_STD_LOW_DBHZ, "high_min": MULTIPATH_STD_HIGH_DBHZ}}
    if mean_std is None:
        return _trace(spec_ind, SCORE_UNCONFIRMED, "multipath_unconfirmed",
                      "cn0 variance proxy unavailable -> unconfirmed", inputs), []
    if mean_std <= MULTIPATH_STD_LOW_DBHZ:
        return _trace(spec_ind, 100, "low_multipath",
                      f"mean per-sat C/N0 std <= {MULTIPATH_STD_LOW_DBHZ} dB-Hz", inputs), []
    if mean_std <= MULTIPATH_STD_HIGH_DBHZ:
        return _trace(spec_ind, 65, "moderate_multipath",
                      f"{MULTIPATH_STD_LOW_DBHZ} < mean_std <= {MULTIPATH_STD_HIGH_DBHZ} dB-Hz", inputs), []
    flags = [_flag_record(flag_index["FLG_GCP_007"], mean_std, spec_ind["indicator_id"], pid)]
    return _trace(spec_ind, 35, "high_multipath",
                  f"mean per-sat C/N0 std > {MULTIPATH_STD_HIGH_DBHZ} dB-Hz", inputs,
                  flags_raised=[f["flag_id"] for f in flags]), flags


def _l3i_009(spec_ind: dict, sf: dict, derived: dict, flag_index: dict, pid: str):
    acq = _dv(derived, D_ACQUISITION)
    inputs = {"device_acquisition_time_sec": acq}
    if acq is None:
        return _trace(spec_ind, SCORE_UNCONFIRMED, "acquisition_unconfirmed",
                      "acquisition_time unavailable -> unconfirmed", inputs), []
    if acq < 60:
        return _trace(spec_ind, 100, "fast_acquisition", "acquisition <60s", inputs), []
    if acq < 120:
        return _trace(spec_ind, 88, "normal_acquisition", "60-119s", inputs), []
    if acq < ACQUISITION_SLOW_SEC:
        return _trace(spec_ind, 65, "slow_acquisition", "120-299s", inputs), []
    flags = [_flag_record(flag_index["FLG_GCP_009"], acq, spec_ind["indicator_id"], pid)]
    return _trace(spec_ind, 30, "very_slow_acquisition", f"acquisition >= {ACQUISITION_SLOW_SEC}s", inputs,
                  flags_raised=[f["flag_id"] for f in flags]), flags


def _l3i_010(spec_ind: dict, sf: dict, derived: dict, flag_index: dict, pid: str):
    kp_struct = _dv(derived, D_KP)
    kp = kp_struct.get("kp") if isinstance(kp_struct, dict) else None
    kp_status = kp_struct.get("status") if isinstance(kp_struct, dict) else "ABSENT"
    dual = _dv(derived, D_DUAL_FREQ)
    inputs = {"kp_index": kp, "kp_status": kp_status, "dual_freq_available": dual,
              "kp_top_max": KP_TOP_MAX, "kp_storm_min": KP_STORM_MIN}

    if dual is True:
        return _trace(spec_ind, 100, "dual_freq_fallback",
                      "dual_freq=True -> iono mitigation available regardless of Kp", inputs), []
    if kp is not None and kp <= KP_TOP_MAX:
        return _trace(spec_ind, 100, "kp_low",
                      f"kp<={KP_TOP_MAX} (single-freq but low storm risk)", inputs), []
    if kp is not None and kp >= KP_STORM_MIN:
        flags = [_flag_record(flag_index["FLG_GCP_008"], {"kp": kp, "dual_freq": dual},
                              spec_ind["indicator_id"], pid)]
        return _trace(spec_ind, 40, "iono_storm_single_freq",
                      f"kp>={KP_STORM_MIN} AND single-frequency", inputs,
                      flags_raised=[f["flag_id"] for f in flags]), flags
    if kp is not None and KP_TOP_MAX < kp < KP_STORM_MIN:
        return _trace(spec_ind, 60, "kp_moderate_single_freq",
                      f"{KP_TOP_MAX}<kp<{KP_STORM_MIN} AND single-frequency", inputs), []
    return _trace(spec_ind, 70, "kp_unavailable_single_freq",
                  "Kp unavailable/null AND single-freq -> cautious midpoint", inputs), []


_DISPATCH = {
    "L3I_GCP_001": _l3i_001, "L3I_GCP_002": _l3i_002, "L3I_GCP_003": _l3i_003,
    "L3I_GCP_004": _l3i_004, "L3I_GCP_005": _l3i_005, "L3I_GCP_006": _l3i_006,
    "L3I_GCP_007": _l3i_007, "L3I_GCP_008": _l3i_008, "L3I_GCP_009": _l3i_009,
    "L3I_GCP_010": _l3i_010,
}


# ---- per-point + survey run ------------------------------------------------

def _evaluate_point(point3a: dict, sf: dict, spec: dict, flag_index: dict) -> dict:
    pid = point3a["point_id"]
    derived = point3a.get("derived_fields", {})
    device_type = sf.get(S_DEVICE_TYPE)

    traces: dict[str, dict] = {}
    point_flags: list[dict] = []

    # Standalone device-type check (FLG_GCP_013) - not tied to a scoring indicator.
    if device_type == "OTHER":
        point_flags.append(_flag_record(flag_index["FLG_GCP_013"], device_type, None, pid))

    for ind in spec.get("indicators", []):
        ind_id = ind["indicator_id"]
        trace, flags = _DISPATCH[ind_id](ind, sf, derived, flag_index, pid)
        traces[ind_id + "_" + ind["indicator_name"]] = trace
        point_flags.extend(flags)

    return {
        "point_id": pid,
        "device_type": device_type,
        "device_role": point3a.get("device_role"),
        "indicator_traces": dict(sorted(traces.items())),
        "flags_raised_stage3b_point": point_flags,
    }


def run(config: dict, project_root: Path, spec: dict, stage3a_data: dict,
        stage2_data: dict) -> dict:
    flag_index = {f["flag_id"]: f for f in spec.get("flags", [])}
    sf_by_point = {p["point_id"]: p.get("source_fields", {})
                   for p in stage2_data.get("points", [])}
    expected_count = spec["_meta"]["counts"]["indicators"]

    point_records: list[dict] = []
    all_flags: list[dict] = []
    notes: list[str] = [
        "L3I_GCP_007 antenna_type mismatch -> 40, no flag raised: the spec "
        "defines no antenna-type-mismatch flag among the 14 flags (soft "
        "type-string check; the true ANTEX check is Stage 2). Spec prose reconciled.",
        "Derived mean_pdop/max_pdop (L2D_GCP_010/011) are not consumed by any "
        "L3I indicator (GCP has no standalone PDOP indicator); they remain "
        "informational provenance only.",
    ]

    for p3a in stage3a_data.get("points", []):
        sf = sf_by_point.get(p3a["point_id"], {})
        rec = _evaluate_point(p3a, sf, spec, flag_index)
        if len(rec["indicator_traces"]) != expected_count:
            notes.append(f"{rec['point_id']}: produced {len(rec['indicator_traces'])} "
                         f"indicators, expected {expected_count}.")
        point_records.append(rec)
        all_flags.extend(rec["flags_raised_stage3b_point"])

    # Survey-level rollup of bands/scores per indicator (provenance, not scoring).
    counts_by_band: dict[str, int] = {}
    gates_triggered: list[str] = []
    for rec in point_records:
        for t in rec["indicator_traces"].values():
            counts_by_band[t["band_matched"]] = counts_by_band.get(t["band_matched"], 0) + 1
            if t.get("gate_triggered"):
                gates_triggered.append(f"{rec['point_id']}:{t['indicator_id']}")

    return {
        "points": point_records,
        "flags_raised_stage3b": all_flags,
        "stage3b_notes": notes,
        "stage3b_meta": {
            "expected_indicator_count_per_point": expected_count,
            "point_count": len(point_records),
            "counts_by_band": dict(sorted(counts_by_band.items())),
            "gates_triggered": gates_triggered,
            "tuneables": {
                "MULTIPATH_STD_LOW_DBHZ": MULTIPATH_STD_LOW_DBHZ,
                "MULTIPATH_STD_HIGH_DBHZ": MULTIPATH_STD_HIGH_DBHZ,
                "KP_TOP_MAX": KP_TOP_MAX,
                "KP_STORM_MIN": KP_STORM_MIN,
                "BATTERY_MIN_ADEQUATE_PCT": BATTERY_MIN_ADEQUATE_PCT,
                "SLIPS_CLEAN_MAX": SLIPS_CLEAN_MAX,
                "SLIPS_ELEVATED_MAX": SLIPS_ELEVATED_MAX,
                "ACQUISITION_SLOW_SEC": ACQUISITION_SLOW_SEC,
                "SCORE_UNCONFIRMED": SCORE_UNCONFIRMED,
                "SCORE_PARTIAL": SCORE_PARTIAL,
                "SCORE_DGPS_BATTERY_LOW": SCORE_DGPS_BATTERY_LOW,
            },
        },
    }


def print_summary(data: dict) -> None:
    mm = data["stage3b_meta"]
    print(f"  indicators per point: {mm['expected_indicator_count_per_point']}  "
          f"points: {mm['point_count']}")
    for p in data["points"]:
        scores = {t["indicator_id"].replace("L3I_GCP_", ""): t["score"]
                  for t in p["indicator_traces"].values()}
        print(f"    - {p['point_id']} ({p['device_type']}): {scores}  "
              f"flags={len(p['flags_raised_stage3b_point'])}")
    print(f"  flags raised at Stage 3b: {len(data['flags_raised_stage3b'])}")
    for fl in data["flags_raised_stage3b"]:
        print(f"    FLAG  [{fl['_origin_point']}] {fl['flag_id']} {fl['flag_name']} ({fl['severity']})")
    if mm["gates_triggered"]:
        print(f"  internal gates triggered (flags fire at Stage 3c): {mm['gates_triggered']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GCP PPK Stage 3b indicators")
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
