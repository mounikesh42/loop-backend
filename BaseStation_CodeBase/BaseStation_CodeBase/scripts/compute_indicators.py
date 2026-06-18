#!/usr/bin/env python3
"""Stage 3b — compute 11 L3I_BASE_* indicators per spec sheet 04.

The spec stores threshold bands as prose `threshold_summary` strings (no
separate sheet 05 with `condition_expression` keys to evaluate), so this
module follows Option B from the BUILD_PROMPT_TEMPLATE: one Python function
per indicator. Spec text drives the bands; only score values come from the
spec literally (extracted into named constants below for auditability).

Per-indicator output (trace block):
  {
    "indicator_id":         "L3I_BASE_NNN",
    "indicator_name":       "...",
    "building_block_id":    "BB_BASE_*",
    "weight_in_block":      0.xx,
    "score":                <0.0..100.0>,
    "band_matched":         "<readable_name>",
    "condition_evaluated":  "<prose condition matched>",
    "input_values":         {<field>: <value>, ...},
    "gate_triggered":       bool,         # only for L3I_001 (coverage) and L3I_005 (height)
    "gate_action_spec":     "<spec text>" | None,
    "flags_raised":         [<flag_id>, ...]   # threshold flags only at this stage
  }

Internal-gate flags (BASE_RINEX_FLIGHT_GAP, ANTENNA_HEIGHT_MISSING) fire at
Stage 3c per template rule 4 — this module only marks `gate_triggered=True`.
Threshold flags fire here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Engineering tuneables — picked because spec's prose threshold_summary is
# qualitative ("low / moderate / high"). All surface in stage3b_meta.tuneables
# so reviewers can see them in one place.
# ---------------------------------------------------------------------------

# Per schema x-on-low: "Threshold ~<=10% -> risk penalty even if session completed"
BATTERY_MIN_ADEQUATE_PCT = 10.0

# Continuity (L3I_004): "low slips" needs a numeric pick
SLIPS_PER_HOUR_LOW = 100.0   # tightened from 200 — industry "elevated" threshold; clean surveys typically <50, healthy 50-100, elevated >100

# Multipath (L3I_008): "low / moderate / high C/N0 variance"
# Picked so a clean open-sky site with realistic elevation-driven variance
# (~1-2 dB-Hz) lands in the top band.
MULTIPATH_STD_LOW_DBHZ = 2.5         # < 2.5 → low
MULTIPATH_STD_HIGH_DBHZ = 4.0        # >= 4.0 → high; in-between → moderate

# Ionospheric (L3I_009): Kp >= 5 considered "high" per NOAA convention
KP_HIGH_THRESHOLD = 5.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


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


def _flag_record(spec_flag: dict, condition_value: Any, origin_indicator: str) -> dict:
    return {
        "flag_id": spec_flag["flag_id"],
        "flag_name": spec_flag["flag_name"],
        "severity": spec_flag["severity"],
        "raised_at_stage_spec": spec_flag["raised_at_stage"],
        "_origin_stage": "stage3b",
        "_origin_indicator": origin_indicator,
        "condition_value": condition_value,
    }


# ---------------------------------------------------------------------------
# Per-indicator eval functions (alphabetical by L3I id)
# ---------------------------------------------------------------------------

# L3I_BASE_001 — coverage_score (BB_BASE_COMPLETE, weight 0.35)
def _l3i_001(spec_ind: dict, derived: dict) -> tuple[dict, list[dict]]:
    cov = derived["L2D_BASE_001_base_flight_coverage_ratio"]["value"]
    pre = derived["L2D_BASE_002_pre_flight_buffer_sec"]["value"]
    post = derived["L2D_BASE_003_post_flight_buffer_sec"]["value"]
    inputs = {"coverage_ratio": cov, "pre_buffer_sec": pre, "post_buffer_sec": post}

    if cov is None or cov < 1.0:
        return _trace(
            spec_ind, 0, "coverage_gate_triggered",
            "coverage_ratio < 1.0 → internal gate trips",
            inputs, gate_triggered=True,
            gate_action_spec=spec_ind["gate_action"],
        ), []

    # coverage == 1.0 → graded by buffers
    if pre is None or post is None:
        return _trace(
            spec_ind, 72, "buffer_data_missing",
            "coverage=1.0 but buffer data missing → bottom band of full-coverage path",
            inputs,
        ), []

    if pre >= 120 and post >= 60:
        return _trace(spec_ind, 100, "perfect_coverage",
                      "coverage=1.0 AND pre>=120s AND post>=60s", inputs), []
    if pre >= 60:
        return _trace(spec_ind, 88, "good_pre_buffer",
                      "coverage=1.0 AND pre>=60s (pre<120 or post<60)", inputs), []
    return _trace(spec_ind, 72, "short_pre_buffer",
                  "coverage=1.0 AND pre<60s", inputs), []


# L3I_BASE_002 — integrity_score (BB_BASE_COMPLETE, weight 0.30)
def _l3i_002(spec_ind: dict, sf: dict, derived: dict, flag_index: dict) -> tuple[dict, list[dict]]:
    completed = sf.get("L1F_BASE_018_session_completed_normally")
    shutdowns = sf.get("L1F_BASE_019_unexpected_shutdown_count")
    bat_min = sf.get("L1F_BASE_022_battery_min_pct")
    download = sf.get("L1F_BASE_024_raw_log_download_confirmed")
    inputs = {
        "session_completed_normally": completed,
        "unexpected_shutdown_count": shutdowns,
        "battery_min_pct": bat_min,
        "raw_log_download_confirmed": download,
    }

    flags: list[dict] = []
    # Advisory: download not confirmed (true regardless of integrity band)
    if download is not True:
        flags.append(_flag_record(flag_index["FLG_BASE_005"], download, spec_ind["indicator_id"]))

    # Hard-bad band: session interrupted
    interrupted = (shutdowns is not None and shutdowns >= 1) or completed is False
    if interrupted:
        flags.append(_flag_record(flag_index["FLG_BASE_004"],
                                  {"completed": completed, "shutdowns": shutdowns},
                                  spec_ind["indicator_id"]))
        return _trace(
            spec_ind, 20, "session_interrupted",
            "completed_normally=False OR unexpected_shutdown_count>=1",
            inputs, flags_raised=[f["flag_id"] for f in flags],
        ), flags

    # Unconfirmed: OPLOG absent (all three OPLOG signals null)
    if completed is None and shutdowns is None and bat_min is None:
        return _trace(
            spec_ind, 60, "oplog_unconfirmed",
            "OPLOG absent → integrity unconfirmed (~60), never silent pass",
            inputs, flags_raised=[f["flag_id"] for f in flags],
        ), flags

    # Battery low (only if measured)
    if bat_min is not None and bat_min <= BATTERY_MIN_ADEQUATE_PCT:
        return _trace(
            spec_ind, 75, "battery_low",
            f"completed_normally=True AND shutdowns=0 BUT battery_min<={BATTERY_MIN_ADEQUATE_PCT}%",
            inputs, flags_raised=[f["flag_id"] for f in flags],
        ), flags

    # Clean
    if completed is True and shutdowns == 0:
        return _trace(
            spec_ind, 100, "clean",
            "completed_normally=True AND shutdowns=0 AND battery_min_ok",
            inputs, flags_raised=[f["flag_id"] for f in flags],
        ), flags

    # Partial-null fallback (some OPLOG fields present, some absent)
    return _trace(
        spec_ind, 80, "partial_unconfirmed",
        "Some OPLOG signals null but session not interrupted — partial confidence",
        inputs, flags_raised=[f["flag_id"] for f in flags],
    ), flags


# L3I_BASE_003 — format_score (BB_BASE_COMPLETE, weight 0.20)
def _l3i_003(spec_ind: dict, sf: dict, derived: dict, flag_index: dict) -> tuple[dict, list[dict]]:
    ver_supported = derived["L2D_BASE_013_rinex_version_supported"]["value"]
    header_comp = derived["L2D_BASE_014_header_completeness"]["value"]
    header_ok = bool(header_comp.get("complete")) if isinstance(header_comp, dict) else None
    dual = derived["L2D_BASE_004_dual_freq_available"]["value"]
    rinex_version = sf.get("L1F_BASE_007_rinex_version")
    inputs = {
        "rinex_version": rinex_version,
        "rinex_version_supported": ver_supported,
        "header_complete": header_ok,
        "dual_freq_available": dual,
    }

    flags: list[dict] = []

    # First-match: version unsupported (highest-priority bad path)
    if ver_supported is False:
        flags.append(_flag_record(flag_index["FLG_BASE_006"], rinex_version, spec_ind["indicator_id"]))
        return _trace(
            spec_ind, 35, "version_unsupported",
            f"rinex_version={rinex_version} not in supported set",
            inputs, flags_raised=[f["flag_id"] for f in flags],
        ), flags

    # Next: header incomplete
    if header_ok is False:
        return _trace(
            spec_ind, 40, "header_incomplete",
            "RINEX header missing required records (antenna_type/receiver_type/approx_position)",
            inputs,
        ), []

    # Single-frequency (carries dual_freq=False)
    if dual is False:
        return _trace(
            spec_ind, 70, "single_freq_only",
            "version supported AND header complete BUT dual_freq=False",
            inputs,
        ), []

    # Top band
    if ver_supported is True and header_ok is True and dual is True:
        return _trace(
            spec_ind, 100, "format_complete_dual_freq",
            "version supported AND header complete AND dual-freq",
            inputs,
        ), []

    # Any other partial (e.g., header_ok None)
    return _trace(spec_ind, 60, "format_partial_unconfirmed",
                  "some format signals null — partial confidence", inputs), []


# L3I_BASE_004 — continuity_score (BB_BASE_COMPLETE, weight 0.15)
def _l3i_004(spec_ind: dict, sf: dict, derived: dict) -> tuple[dict, list[dict]]:
    cs_total = derived["L2D_BASE_005_cycle_slip_count"]["value"]
    gaps5 = derived["L2D_BASE_006_gap_gt_5s_count"]["value"]
    gap60 = derived["L2D_BASE_007_any_gap_gt_60s"]["value"]
    total_epochs = sf.get("L1F_BASE_013_total_epochs") or 0
    interval = sf.get("L1F_BASE_012_epoch_interval_sec") or 0
    session_hours = (total_epochs * interval) / 3600.0 if total_epochs and interval else None
    slips_per_hr = round(cs_total / session_hours, 1) if (cs_total is not None and session_hours) else None

    inputs = {
        "cycle_slip_count": cs_total,
        "cycle_slips_per_hour": slips_per_hr,
        "gap_gt_5s_count": gaps5,
        "any_gap_gt_60s": gap60,
    }

    # Major gap → bottom band
    if gap60 is True:
        return _trace(spec_ind, 40, "major_gap_present",
                      "any_gap_gt_60s=True", inputs), []
    # Minor gap (gap_gt_5s>0 but no 60s gaps)
    if gaps5 is not None and gaps5 > 0:
        return _trace(spec_ind, 75, "minor_gaps_present",
                      "gap_gt_5s>0 AND any_gap_gt_60s=False", inputs), []
    # No gaps, low slips
    if slips_per_hr is not None and slips_per_hr < SLIPS_PER_HOUR_LOW and gaps5 == 0:
        return _trace(
            spec_ind, 100, "clean_continuity",
            f"no gaps AND slips_per_hour<{SLIPS_PER_HOUR_LOW}", inputs,
        ), []
    # No gaps but high slips (engineering — not in spec; engineering bridge)
    if gaps5 == 0:
        return _trace(
            spec_ind, 80, "high_slips_no_gaps",
            f"gap_gt_5s=0 BUT slips_per_hour>={SLIPS_PER_HOUR_LOW}", inputs,
        ), []
    # Fallback null
    return _trace(spec_ind, 60, "continuity_unconfirmed",
                  "input signals null", inputs), []


# L3I_BASE_005 — antenna_height_documented_score (BB_BASE_SETUP, weight 0.55)
def _l3i_005(spec_ind: dict, sf: dict, derived: dict) -> tuple[dict, list[dict]]:
    height_m = sf.get("L1F_BASE_026_antenna_height_m")
    meas_type = sf.get("L1F_BASE_028_antenna_measurement_type")
    ref = sf.get("L1F_BASE_029_measured_to_reference")
    count = sf.get("L1F_BASE_030_height_measured_count")
    agreement_struct = derived["L2D_BASE_018_antenna_height_agreement"]["value"]
    agreement = agreement_struct.get("agreement") if isinstance(agreement_struct, dict) else None

    inputs = {
        "antenna_height_m": height_m,
        "antenna_measurement_type": meas_type,
        "measured_to_reference": ref,
        "height_measured_count": count,
        "antenna_height_agreement": agreement,
    }

    # Gate: height absent → 0 + gate
    if height_m is None:
        return _trace(
            spec_ind, 0, "height_missing_gate",
            "antenna_height_m absent → internal gate trips",
            inputs, gate_triggered=True,
            gate_action_spec=spec_ind["gate_action"],
        ), []

    # Conflict path: height entered AND RINEX agreement explicitly False
    if agreement is False:
        return _trace(spec_ind, 55, "height_conflicts_with_rinex",
                      "antenna_height_m present but disagrees with RINEX antenna_delta_h",
                      inputs), []

    # Top band: VERTICAL + ARP + count>=3 AND (agreement=True OR agreement=None due to delta_h=0)
    is_vertical = meas_type == "VERTICAL"
    is_arp = ref == "ARP"
    has_corroboration = isinstance(count, int) and count >= 3
    rinex_agrees_or_skip = agreement in (True, None)

    if is_vertical and is_arp and has_corroboration and rinex_agrees_or_skip:
        return _trace(
            spec_ind, 100, "gold_standard",
            "VERTICAL AND measured_to=ARP AND height_count>=3 AND RINEX-agreement OK-or-skipped",
            inputs,
        ), []

    # Single vertical (count = 1)
    if is_vertical and not has_corroboration:
        return _trace(spec_ind, 88, "single_vertical",
                      "VERTICAL with single measurement (count<3)", inputs), []

    # Slant path
    if meas_type == "SLANT":
        return _trace(spec_ind, 72, "slant_measurement",
                      "antenna_measurement_type=SLANT (less precise)", inputs), []

    # Partial / unknown — keep a documented mid band
    return _trace(spec_ind, 60, "partial_documentation",
                  "documented but missing top-band criteria", inputs), []


# L3I_BASE_006 — setup_verification_score (BB_BASE_SETUP, weight 0.30)
def _l3i_006(spec_ind: dict, sf: dict) -> tuple[dict, list[dict]]:
    over_known = sf.get("L1F_BASE_033_over_known_mark")
    verified = sf.get("L1F_BASE_034_verified_by_second_person")
    monument = sf.get("L1F_BASE_032_monument_id")
    inputs = {
        "over_known_mark": over_known,
        "verified_by_second_person": verified,
        "monument_id": monument,
    }
    if over_known is True and verified is True:
        return _trace(spec_ind, 100, "verified_known_mark",
                      "over known mark AND verified by 2nd person", inputs), []
    if over_known is True and verified is False:
        return _trace(spec_ind, 50, "unverified_known_mark",
                      "over known mark BUT not verified — BENCHMARK_UNVERIFIED handoff at S3a",
                      inputs), []
    if over_known is True and verified is None:
        return _trace(spec_ind, 80, "known_mark_verification_unconfirmed",
                      "over known mark, verification field null", inputs), []
    if over_known is False:
        # Spec leaves this band implicit. Engineering midpoint:
        return _trace(spec_ind, 50, "ad_hoc_point",
                      "not over a known mark — ad-hoc point", inputs), []
    return _trace(spec_ind, 60, "verification_unconfirmed",
                  "verification inputs null", inputs), []


# L3I_BASE_007 — antenna_type_match_score (BB_BASE_SETUP, weight 0.15)
def _l3i_007(spec_ind: dict, derived: dict) -> tuple[dict, list[dict]]:
    match = derived["L2D_BASE_017_antenna_type_match"]["value"]
    inputs = {"antenna_type_match": match}
    if match is True:
        return _trace(spec_ind, 100, "type_match",
                      "form antenna_model matches RINEX antenna_type", inputs), []
    if match is False:
        return _trace(spec_ind, 40, "type_mismatch",
                      "form antenna_model != RINEX antenna_type", inputs), []
    return _trace(spec_ind, 60, "type_match_unconfirmed",
                  "antenna_type or antenna_model null", inputs), []


# L3I_BASE_008 — multipath_score (BB_BASE_ENV, weight 0.45)
def _l3i_008(spec_ind: dict, derived: dict, flag_index: dict) -> tuple[dict, list[dict]]:
    mp = derived["L2D_BASE_009_multipath_risk_level"]["value"]
    mean_std = mp.get("mean_of_per_sat_cn0_std_dbhz") if isinstance(mp, dict) else None
    inputs = {"mean_of_per_sat_cn0_std_dbhz": mean_std,
              "thresholds_dbhz": {"low": MULTIPATH_STD_LOW_DBHZ, "high": MULTIPATH_STD_HIGH_DBHZ}}
    flags: list[dict] = []
    if mean_std is None:
        return _trace(spec_ind, 60, "multipath_unconfirmed",
                      "cn0 variance proxy unavailable", inputs), []
    if mean_std < MULTIPATH_STD_LOW_DBHZ:
        return _trace(spec_ind, 100, "low_variance",
                      f"mean per-sat C/N0 std < {MULTIPATH_STD_LOW_DBHZ} dB-Hz", inputs), []
    if mean_std < MULTIPATH_STD_HIGH_DBHZ:
        return _trace(spec_ind, 65, "moderate_variance",
                      f"{MULTIPATH_STD_LOW_DBHZ} <= mean_std < {MULTIPATH_STD_HIGH_DBHZ} dB-Hz",
                      inputs), []
    flags.append(_flag_record(flag_index["FLG_BASE_007"], mean_std, spec_ind["indicator_id"]))
    return _trace(
        spec_ind, 35, "high_variance",
        f"mean per-sat C/N0 std >= {MULTIPATH_STD_HIGH_DBHZ} dB-Hz",
        inputs, flags_raised=[f["flag_id"] for f in flags],
    ), flags


# L3I_BASE_009 — ionospheric_risk_score (BB_BASE_ENV, weight 0.20)
def _l3i_009(spec_ind: dict, derived: dict, flag_index: dict) -> tuple[dict, list[dict]]:
    kp_struct = derived["L2D_BASE_019_kp_index"]["value"]
    kp = kp_struct.get("kp") if isinstance(kp_struct, dict) else None
    kp_status = kp_struct.get("status") if isinstance(kp_struct, dict) else "ABSENT"
    dual = derived["L2D_BASE_004_dual_freq_available"]["value"]
    inputs = {"kp_index": kp, "kp_status": kp_status, "dual_freq_available": dual,
              "kp_high_threshold": KP_HIGH_THRESHOLD}

    flags: list[dict] = []
    # Top band: Kp low OR dual_freq
    if dual is True:
        return _trace(spec_ind, 100, "dual_freq_fallback",
                      "dual_freq=True → iono mitigation available regardless of Kp",
                      inputs), []
    if kp is not None and kp < KP_HIGH_THRESHOLD:
        return _trace(spec_ind, 100, "kp_low",
                      f"kp<{KP_HIGH_THRESHOLD} (single-freq but low storm risk)",
                      inputs), []

    # API_UNAVAILABLE + single-freq → cautious midpoint
    if kp_status == "API_UNAVAILABLE":
        return _trace(spec_ind, 70, "kp_api_unavailable_single_freq",
                      "NOAA SWPC unavailable AND single-freq receiver — cautious midpoint",
                      inputs), []

    # Kp high AND single-freq → bottom band + flag
    if kp is not None and kp >= KP_HIGH_THRESHOLD and dual is False:
        flags.append(_flag_record(flag_index["FLG_BASE_008"],
                                  {"kp": kp, "dual_freq": dual},
                                  spec_ind["indicator_id"]))
        return _trace(spec_ind, 40, "iono_storm_single_freq",
                      f"kp>={KP_HIGH_THRESHOLD} AND single-frequency",
                      inputs, flags_raised=[f["flag_id"] for f in flags]), flags

    # Fallback
    return _trace(spec_ind, 70, "iono_unconfirmed",
                  "kp / dual_freq inputs incomplete", inputs), []


# L3I_BASE_010 — pdop_score (BB_BASE_ENV, weight 0.20)
def _l3i_010(spec_ind: dict, derived: dict) -> tuple[dict, list[dict]]:
    mean_pdop = derived["L2D_BASE_010_mean_pdop"]["value"]
    inputs = {"mean_pdop": mean_pdop}
    if mean_pdop is None:
        return _trace(spec_ind, 60, "pdop_unconfirmed",
                      "mean_pdop unavailable (no NAV file or no PDOP samples)", inputs), []
    if mean_pdop < 2.0:
        return _trace(spec_ind, 100, "pdop_excellent",
                      "mean_pdop<2", inputs), []
    if mean_pdop < 4.0:
        return _trace(spec_ind, 80, "pdop_good",
                      "2<=mean_pdop<4", inputs), []
    if mean_pdop < 6.0:
        return _trace(spec_ind, 55, "pdop_marginal",
                      "4<=mean_pdop<6", inputs), []
    return _trace(spec_ind, 30, "pdop_poor",
                  "mean_pdop>=6", inputs), []


# L3I_BASE_011 — acquisition_score (BB_BASE_ENV, weight 0.15)
def _l3i_011(spec_ind: dict, derived: dict, flag_index: dict) -> tuple[dict, list[dict]]:
    acq = derived["L2D_BASE_012_base_acquisition_time_sec"]["value"]
    inputs = {"base_acquisition_time_sec": acq}
    flags: list[dict] = []
    if acq is None:
        return _trace(spec_ind, 60, "acquisition_unconfirmed",
                      "acquisition_time unavailable", inputs), []
    if acq < 60:
        return _trace(spec_ind, 100, "fast_acquisition", "<60s", inputs), []
    if acq < 180:
        return _trace(spec_ind, 80, "normal_acquisition", "60-180s", inputs), []
    if acq < 300:
        return _trace(spec_ind, 55, "slow_acquisition", "180-300s", inputs), []
    flags.append(_flag_record(flag_index["FLG_BASE_009"], acq, spec_ind["indicator_id"]))
    return _trace(
        spec_ind, 30, "very_slow_acquisition", ">=300s",
        inputs, flags_raised=[f["flag_id"] for f in flags],
    ), flags


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _call_dispatch(indicator_id: str, spec_ind: dict, sf: dict, derived: dict,
                   flag_index: dict) -> tuple[dict, list[dict]]:
    if indicator_id == "L3I_BASE_001":
        return _l3i_001(spec_ind, derived)
    if indicator_id == "L3I_BASE_002":
        return _l3i_002(spec_ind, sf, derived, flag_index)
    if indicator_id == "L3I_BASE_003":
        return _l3i_003(spec_ind, sf, derived, flag_index)
    if indicator_id == "L3I_BASE_004":
        return _l3i_004(spec_ind, sf, derived)
    if indicator_id == "L3I_BASE_005":
        return _l3i_005(spec_ind, sf, derived)
    if indicator_id == "L3I_BASE_006":
        return _l3i_006(spec_ind, sf)
    if indicator_id == "L3I_BASE_007":
        return _l3i_007(spec_ind, derived)
    if indicator_id == "L3I_BASE_008":
        return _l3i_008(spec_ind, derived, flag_index)
    if indicator_id == "L3I_BASE_009":
        return _l3i_009(spec_ind, derived, flag_index)
    if indicator_id == "L3I_BASE_010":
        return _l3i_010(spec_ind, derived)
    if indicator_id == "L3I_BASE_011":
        return _l3i_011(spec_ind, derived, flag_index)
    raise KeyError(f"No dispatch entry for {indicator_id}")


def run(config: dict, project_root, spec: dict, stage3a_data: dict,
        stage2_data: dict | None = None) -> dict:
    started_at = datetime.now(timezone.utc)
    sf = (stage2_data or {}).get("source_fields", {})
    derived = stage3a_data.get("derived_fields", {})
    flag_index = {f["flag_id"]: f for f in spec.get("flags", [])}

    traces: dict[str, dict] = {}
    flags_raised_stage3b: list[dict] = []

    for ind in spec.get("indicators", []):
        ind_id = ind["indicator_id"]
        trace, flags = _call_dispatch(ind_id, ind, sf, derived, flag_index)
        traces[ind_id + "_" + ind["indicator_name"]] = trace
        flags_raised_stage3b.extend(flags)

    expected_count = spec["_meta"]["counts"]["indicators"]
    produced_count = len(traces)
    counts_by_band: dict[str, int] = {}
    score_sum = 0.0
    score_n = 0
    gates_triggered: list[str] = []
    for t in traces.values():
        counts_by_band[t["band_matched"]] = counts_by_band.get(t["band_matched"], 0) + 1
        score_sum += t["score"]
        score_n += 1
        if t.get("gate_triggered"):
            gates_triggered.append(t["indicator_id"])

    finished_at = datetime.now(timezone.utc)
    return {
        "indicator_traces": dict(sorted(traces.items())),
        "flags_raised_stage3b": flags_raised_stage3b,
        "stage3b_meta": {
            "expected_indicator_count": expected_count,
            "produced_indicator_count": produced_count,
            "indicator_score_mean": round(score_sum / score_n, 1) if score_n else None,
            "counts_by_band": dict(sorted(counts_by_band.items())),
            "indicators_with_gate_triggered": gates_triggered,
            "tuneables": {
                "BATTERY_MIN_ADEQUATE_PCT": BATTERY_MIN_ADEQUATE_PCT,
                "SLIPS_PER_HOUR_LOW": SLIPS_PER_HOUR_LOW,
                "MULTIPATH_STD_LOW_DBHZ": MULTIPATH_STD_LOW_DBHZ,
                "MULTIPATH_STD_HIGH_DBHZ": MULTIPATH_STD_HIGH_DBHZ,
                "KP_HIGH_THRESHOLD": KP_HIGH_THRESHOLD,
            },
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "wall_time_sec": round((finished_at - started_at).total_seconds(), 3),
        },
    }
