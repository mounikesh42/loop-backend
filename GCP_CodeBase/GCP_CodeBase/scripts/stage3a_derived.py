#!/usr/bin/env python3
"""Stage 3a - compute the 23 L2D_GCP_* derived fields PER POINT (per spec).

GCP runs the derivation once per occupation (each point's source_fields +
its SRC_GCP_RINEX parser_meta). Field kinds come straight from the spec:
  - scoring           feed L3I_GCP_* indicators at Stage 3b
  - composite_scoring L2D_GCP_016 session_integrity_ok (device-type-aware)
  - composite_flag    L2D_GCP_022 position_disturbance_signature -> FLG_GCP_011
                      L2D_GCP_023 truncation_check (DGPS only)   -> FLG_GCP_014

Dependency topology (compute Tier 1 then Tier 2):
  Tier 1 (L1F only):  001-015, 017-021, 023
  Tier 2 (needs L2D): 016 (needs 007 any_gap_gt_60s),
                      022 (needs 005 cycle_slip, 006 gap_gt_5s, 009 multipath)

GCP-specific deltas vs the base-station build:
  - mean_pdop / max_pdop are computed OVER THE OCCUPATION (spec: "during
    occupation"), i.e. the whole observation session, not a flight-window slice.
  - session_integrity_ok is device-type-aware: DGPS uses the oplog signals
    (completed_normally AND shutdowns==0 AND battery_min >= 20%); CB_X /
    AEROPOINT / OTHER / unknown have no oplog and pass iff there is no >60s gap.
  - antenna_height_auto_known (021) is new: True for CB_X / AEROPOINT.
  - acquisition uses the spec's >=6-sat stability threshold (base used 8).
  - truncation_check (023) applies to DGPS only (oplog expected-present).

kp_index (020) is an external NOAA SWPC dependency: cache-only here (no live
network call, matching the base build's deterministic offline behaviour). A
cache miss yields status=API_UNAVAILABLE so the Stage 3b ionospheric indicator
takes its dual-freq fallback path.

Output shape per derived field:
  {"value": <primitive|dict|None>, "kind": <from spec>,
   "input_field_ids": [...], "_notes": [...]?}

Flags raised here get _origin_stage="stage3a" + _origin_point and surface in
data.flags_raised_stage3a for the Stage 3d rollup. No timestamps live in the
data block (determinism rule 3).
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import stage1_inventory  # noqa: E402
import stage2_merge  # noqa: E402

STAGE = "stage3a_derived"

# ---- tuneables (engineering choices for fields whose exact algorithm is not
#      fully pinned by the spec; surfaced in stage3a_meta.tuneables). Scoring
#      thresholds proper are read from the spec at L3I (Stage 3b), not here. ----

# L2D_GCP_013 authoritative version gate (mirrors the spec formula set).
SUPPORTED_RINEX_VERSIONS = {"2.10", "2.11", "3.02", "3.03", "3.04", "3.05"}

# L2D_GCP_012 acquisition: spec meaning = "stable sat_count (>=6 sats ...)".
ACQUISITION_NSAT_THRESHOLD = 6
ACQUISITION_STABILITY_SEC = 10.0

# L2D_GCP_016 DGPS battery adequacy: spec meaning = ">= 20%".
BATTERY_MIN_ADEQUATE_PCT = 20.0

# L2D_GCP_023 truncation tolerance: survey-grade clock skew is sub-2s; real
# truncation shows 30+ s. 3s gives margin without false positives.
TRUNCATION_TOLERANCE_SEC = 3.0

# L2D_GCP_022 disturbance signature - all three must co-occur ("AND").
DISTURBANCE_GAP_GT_5S_COUNT = 5
DISTURBANCE_CYCLE_SLIPS_PER_HOUR = 50
DISTURBANCE_CN0_STD_DBHZ_MEAN = 3.0

# L2D_GCP_009 multipath proxy level thresholds (spec meaning): low <= 2.5,
# moderate 2.5-4.0, high > 4.0 dB-Hz. The numeric proxy is the scored input;
# the level label here is advisory provenance (L3I applies the score).
MULTIPATH_LOW_MAX_DBHZ = 2.5
MULTIPATH_MODERATE_MAX_DBHZ = 4.0


# ---- field-key constants (canonical source_field keys) ---------------------

OBS_START = "L1F_GCP_009_obs_start_utc"
OBS_END = "L1F_GCP_010_obs_end_utc"
FLIGHT_START = "L1F_GCP_039_flight_start_utc"
FLIGHT_END = "L1F_GCP_040_flight_end_utc"
DUAL_FREQ = "L1F_GCP_011_dual_freq_present"
CYCLE_SLIP_MARKERS = "L1F_GCP_016_cycle_slip_markers"
CN0_PER_SAT = "L1F_GCP_015_cn0_per_sat"
PDOP_PER_EPOCH = "L1F_GCP_017_pdop_per_epoch"
SAT_COUNT_PER_EPOCH = "L1F_GCP_018_sat_count_per_epoch"
RINEX_VERSION = "L1F_GCP_007_rinex_version"
ANTENNA_TYPE = "L1F_GCP_002_antenna_type"
RECEIVER_TYPE = "L1F_GCP_004_receiver_type"
APPROX_POS = "L1F_GCP_006_approx_position_xyz"
CONSTELLATION_SET = "L1F_GCP_008_constellation_set"
SESSION_COMPLETED = "L1F_GCP_019_session_completed_normally"
SHUTDOWN_COUNT = "L1F_GCP_020_unexpected_shutdown_count"
BATTERY_MIN = "L1F_GCP_023_battery_min_pct"
ANTENNA_MODEL = "L1F_GCP_029_antenna_model"
ANTENNA_HEIGHT_M = "L1F_GCP_030_antenna_height_m"
ANTENNA_DELTA_H = "L1F_GCP_003_antenna_delta_h"
DEVICE_ID_FORM = "L1F_GCP_027_device_id"
DEVICE_ID_RINEX = "L1F_GCP_012_device_id"
DEVICE_TYPE = "L1F_GCP_026_device_type"
SESSION_END = "L1F_GCP_024_session_end_utc"
EPOCH_INTERVAL = "L1F_GCP_013_epoch_interval_sec"
TOTAL_EPOCHS = "L1F_GCP_014_total_epochs"

# Derived-field keys (Tier 2 inputs reference these).
D_GAP_GT_60S = "L2D_GCP_007_any_gap_gt_60s"
D_CYCLE_SLIP = "L2D_GCP_005_cycle_slip_count"
D_GAP_GT_5S = "L2D_GCP_006_gap_gt_5s_count"
D_MULTIPATH = "L2D_GCP_009_multipath_risk_level"


# ---- helpers ---------------------------------------------------------------

def _parse_iso(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _field(value: Any, input_field_ids: list[str], notes: list[str] | None = None) -> dict:
    out: dict[str, Any] = {"value": value, "input_field_ids": list(input_field_ids)}
    if notes:
        out["_notes"] = list(notes)
    return out


def _normalize_str(s: str) -> str:
    return " ".join(s.upper().split())


# ---- per-field computers ---------------------------------------------------

def _l2d_001_occupation_coverage_ratio(sf: dict) -> dict:
    obs_s, obs_e = _parse_iso(sf.get(OBS_START)), _parse_iso(sf.get(OBS_END))
    fl_s, fl_e = _parse_iso(sf.get(FLIGHT_START)), _parse_iso(sf.get(FLIGHT_END))
    ids = [OBS_START, OBS_END, FLIGHT_START, FLIGHT_END]
    if not (obs_s and obs_e and fl_s and fl_e):
        return _field(0.0, ids,
                      ["One or more time inputs missing - ratio defaulted to 0 "
                       "(trips coverage gate FLG_GCP_003 at L3I_GCP_001)."])
    flight_dur = (fl_e - fl_s).total_seconds()
    if flight_dur <= 0:
        return _field(0.0, ids, [f"flight_end <= flight_start (dur={flight_dur}s) - ratio = 0."])
    overlap = max(0.0, (min(obs_e, fl_e) - max(obs_s, fl_s)).total_seconds())
    return _field(round(overlap / flight_dur, 4), ids)


def _l2d_002_pre_flight_buffer_sec(sf: dict) -> dict:
    obs_s, fl_s = _parse_iso(sf.get(OBS_START)), _parse_iso(sf.get(FLIGHT_START))
    ids = [OBS_START, FLIGHT_START]
    if not (obs_s and fl_s):
        return _field(None, ids, ["Time input missing - null."])
    return _field(round((fl_s - obs_s).total_seconds(), 1), ids)


def _l2d_003_post_flight_buffer_sec(sf: dict) -> dict:
    obs_e, fl_e = _parse_iso(sf.get(OBS_END)), _parse_iso(sf.get(FLIGHT_END))
    ids = [OBS_END, FLIGHT_END]
    if not (obs_e and fl_e):
        return _field(None, ids, ["Time input missing - null."])
    return _field(round((obs_e - fl_e).total_seconds(), 1), ids)


def _l2d_004_dual_freq_available(sf: dict) -> dict:
    v = sf.get(DUAL_FREQ)
    return _field(bool(v) if v is not None else None, [DUAL_FREQ])


def _l2d_005_cycle_slip_count(sf: dict) -> dict:
    cs = sf.get(CYCLE_SLIP_MARKERS) or {}
    total = cs.get("total_count") if isinstance(cs, dict) else None
    return _field(total, [CYCLE_SLIP_MARKERS])


def _l2d_006_gap_gt_5s_count(rinex_pm: dict) -> dict:
    stats = rinex_pm.get("stream_stats_for_derived_fields") or {}
    return _field(stats.get("count_gap_gt_5s"), [EPOCH_INTERVAL, TOTAL_EPOCHS],
                  ["Spec lists epoch_interval_sec + total_epochs, but gap counting needs "
                   "per-epoch deltas; parser pre-computed count_gap_gt_5s during streaming "
                   "(parser_meta.stream_stats_for_derived_fields)."])


def _l2d_007_any_gap_gt_60s(rinex_pm: dict) -> dict:
    stats = rinex_pm.get("stream_stats_for_derived_fields") or {}
    v = stats.get("count_gap_gt_60s")
    return _field((v is not None and v > 0), [EPOCH_INTERVAL, TOTAL_EPOCHS],
                  ["Computed as count_gap_gt_60s > 0 from parser_meta.stream_stats_for_derived_fields."])


def _l2d_008_cn0_mean_dbhz(sf: dict) -> dict:
    cn0 = sf.get(CN0_PER_SAT) or {}
    v = cn0.get("overall_mean_dbhz") if isinstance(cn0, dict) else None
    return _field(round(v, 3) if v is not None else None, [CN0_PER_SAT])


def _l2d_009_multipath_risk_level(sf: dict) -> dict:
    cn0 = sf.get(CN0_PER_SAT) or {}
    per_sat = cn0.get("per_sat") if isinstance(cn0, dict) else None
    if not isinstance(per_sat, dict) or not per_sat:
        return _field(None, [CN0_PER_SAT], ["No per-sat C/N0 stats - multipath proxy null."])
    stds = [s.get("std_dbhz") for s in per_sat.values()
            if isinstance(s, dict) and s.get("std_dbhz") is not None]
    if not stds:
        return _field(None, [CN0_PER_SAT], ["No std_dbhz values in per_sat - multipath proxy null."])
    mean_std = round(statistics.fmean(stds), 3)
    if mean_std <= MULTIPATH_LOW_MAX_DBHZ:
        level = "low"
    elif mean_std <= MULTIPATH_MODERATE_MAX_DBHZ:
        level = "moderate"
    else:
        level = "high"
    return _field(
        {"mean_of_per_sat_cn0_std_dbhz": mean_std, "n_sats": len(stds), "level": level},
        [CN0_PER_SAT],
        ["Spec asks for std-dev at equal elevation; per-epoch elevation unavailable, so "
         "proxy = mean of per-sat C/N0 std-dev across the occupation (conservative). "
         "level label is advisory (low<=2.5, moderate<=4.0, high>4.0 dB-Hz); the score is "
         "applied at L3I in Stage 3b."],
    )


def _l2d_010_mean_pdop(sf: dict) -> dict:
    pd = sf.get(PDOP_PER_EPOCH)
    if not isinstance(pd, dict) or not isinstance(pd.get("summary"), dict):
        return _field(None, [PDOP_PER_EPOCH], ["No PDOP summary available - null."])
    return _field(pd["summary"].get("mean"), [PDOP_PER_EPOCH],
                  ["Mean PDOP over the whole occupation (spec: 'during occupation')."])


def _l2d_011_max_pdop(sf: dict) -> dict:
    pd = sf.get(PDOP_PER_EPOCH)
    if not isinstance(pd, dict) or not isinstance(pd.get("summary"), dict):
        return _field(None, [PDOP_PER_EPOCH], ["No PDOP summary available - null."])
    return _field(pd["summary"].get("max"), [PDOP_PER_EPOCH],
                  ["Max PDOP over the whole occupation (spec: 'during occupation')."])


def _l2d_012_device_acquisition_time_sec(sf: dict) -> dict:
    sc = sf.get(SAT_COUNT_PER_EPOCH) or {}
    samples = sc.get("acquisition_samples") if isinstance(sc, dict) else None
    if not samples:
        return _field(None, [SAT_COUNT_PER_EPOCH], ["No acquisition samples - null."])
    n = len(samples)
    for i, (offset, nsat) in enumerate(samples):
        if nsat < ACQUISITION_NSAT_THRESHOLD:
            continue
        deadline = offset + ACQUISITION_STABILITY_SEC
        stable = True
        for j in range(i, n):
            o, ns = samples[j]
            if o > deadline:
                break
            if ns < ACQUISITION_NSAT_THRESHOLD:
                stable = False
                break
        if stable:
            return _field(round(offset, 1), [SAT_COUNT_PER_EPOCH],
                          [f"First epoch where nsat >= {ACQUISITION_NSAT_THRESHOLD} held for "
                           f">= {ACQUISITION_STABILITY_SEC}s contiguously. (PDOP qualifier "
                           "approximated by the sat-count threshold.)"])
    return _field(None, [SAT_COUNT_PER_EPOCH],
                  [f"Stable nsat >= {ACQUISITION_NSAT_THRESHOLD} for >= "
                   f"{ACQUISITION_STABILITY_SEC}s never reached in the first window."])


def _l2d_013_rinex_version_supported(sf: dict) -> dict:
    ver = sf.get(RINEX_VERSION)
    if ver is None:
        return _field(None, [RINEX_VERSION])
    return _field(ver in SUPPORTED_RINEX_VERSIONS, [RINEX_VERSION])


def _l2d_014_header_completeness(sf: dict) -> dict:
    components = {
        "antenna_type_present": bool(sf.get(ANTENNA_TYPE)),
        "receiver_type_present": bool(sf.get(RECEIVER_TYPE)),
        "approx_position_present": bool(sf.get(APPROX_POS)),
    }
    return _field({"complete": all(components.values()), "components": components},
                  [ANTENNA_TYPE, RECEIVER_TYPE, APPROX_POS])


def _l2d_015_constellation_count(sf: dict) -> dict:
    cs = sf.get(CONSTELLATION_SET)
    if not isinstance(cs, list):
        return _field(None, [CONSTELLATION_SET])
    return _field(len(cs), [CONSTELLATION_SET])


def _l2d_016_session_integrity_ok(sf: dict, derived: dict) -> dict:
    """Device-type-aware (composite_scoring). DGPS uses oplog signals; all other
    device types (and unknown) have no oplog and pass iff there is no >60s gap."""
    dtype = sf.get(DEVICE_TYPE)
    ids = [SESSION_COMPLETED, SHUTDOWN_COUNT, BATTERY_MIN, D_GAP_GT_60S, DEVICE_TYPE]
    any_gap60 = derived.get(D_GAP_GT_60S, {}).get("value")

    if dtype == "DGPS":
        completed = sf.get(SESSION_COMPLETED)
        shutdowns = sf.get(SHUTDOWN_COUNT)
        bat_min = sf.get(BATTERY_MIN)
        if completed is None or shutdowns is None:
            return _field(None, ids,
                          ["device_type=DGPS but a core OPLOG signal is null - UNCONFIRMED "
                           "(not False); oplog was expected-present."])
        parts = {
            "completed_normally": bool(completed),
            "no_shutdowns": shutdowns == 0,
            "battery_min_adequate": (True if bat_min is None else bat_min >= BATTERY_MIN_ADEQUATE_PCT),
        }
        return _field({"ok": all(parts.values()), "components": parts, "branch": "dgps_oplog"}, ids)

    # CB_X / AEROPOINT / OTHER / unknown: oplog expected-absent -> RINEX continuity.
    if any_gap60 is None:
        return _field(None, ids,
                      [f"device_type={dtype}: no RINEX gap signal available - UNCONFIRMED."])
    branch = "non_dgps_rinex_only" if dtype in ("CB_X", "AEROPOINT", "OTHER") else "unknown_device_rinex_only"
    return _field(
        {"ok": (any_gap60 is False), "components": {"no_gap_gt_60s": (any_gap60 is False)}, "branch": branch},
        ids,
        [f"device_type={dtype}: oplog expected-absent; integrity = no >60s recording gap."],
    )


def _l2d_017_antenna_type_match(sf: dict) -> dict:
    model, a_type = sf.get(ANTENNA_MODEL), sf.get(ANTENNA_TYPE)
    ids = [ANTENNA_MODEL, ANTENNA_TYPE]
    if not model or not a_type:
        return _field(None, ids, ["antenna_model or antenna_type empty/null - cannot compare."])
    return _field(_normalize_str(model) == _normalize_str(a_type), ids)


def _l2d_018_antenna_height_agreement(sf: dict) -> dict:
    h_m, delta_h = sf.get(ANTENNA_HEIGHT_M), sf.get(ANTENNA_DELTA_H)
    ids = [ANTENNA_HEIGHT_M, ANTENNA_DELTA_H]
    if h_m is None:
        return _field(None, ids, ["antenna_height_m null - cannot compare."])
    if delta_h is None:
        return _field(None, ids, ["antenna_delta_h null - cannot compare."])
    if abs(delta_h) < 1e-9:
        return _field({"agreement": None, "reason": "delta_h is 0.000 in RINEX header (device unset)"},
                      ids, ["Per spec rule, cross-check skipped when delta_h == 0; agreement N/A."])
    diff = round(abs(h_m - delta_h), 4)
    tolerance = 0.005
    return _field({"agreement": diff <= tolerance, "abs_diff_m": diff, "tolerance_m": tolerance}, ids)


def _l2d_019_device_id_match(sf: dict) -> dict:
    form_id, rinex_id = sf.get(DEVICE_ID_FORM), sf.get(DEVICE_ID_RINEX)
    ids = [DEVICE_ID_FORM, DEVICE_ID_RINEX]
    if not form_id or not rinex_id:
        return _field(None, ids,
                      ["form or RINEX device_id empty/null - L3I_GCP_006 takes the missing path (60)."])
    return _field(_normalize_str(form_id) == _normalize_str(rinex_id), ids,
                  ["Match -> L3I_GCP_006 = 100; mismatch -> 50 + FLG_GCP_010 (HIGH, Stage 3b threshold)."])


def _l2d_020_kp_index(sf: dict, project_root: Path, options: dict) -> dict:
    """External NOAA SWPC dependency. Cache-only (no live network call), matching
    the base build's deterministic offline behaviour. Cache miss -> API_UNAVAILABLE
    so L3I ionospheric_risk takes the dual-freq fallback path."""
    obs_s = _parse_iso(sf.get(OBS_START))
    ids = [OBS_START]
    if obs_s is None:
        return _field(None, ids, ["obs_start_utc null - no lookup."])
    cache_dir = project_root / options.get("noaa_swpc_cache_dir", "cache/noaa_swpc")
    cache_file = cache_dir / f"{obs_s.strftime('%Y-%m-%d')}.json"
    if cache_file.exists():
        try:
            import json as _json
            with cache_file.open("r", encoding="utf-8") as fh:
                payload = _json.load(fh)
            return _field({"kp": payload.get("kp"), "source": f"cache/{cache_file.name}", "status": "OK"},
                          ids, ["Read from local NOAA SWPC cache; no network call."])
        except (OSError, ValueError):
            pass
    return _field({"kp": None, "source": None, "status": "API_UNAVAILABLE"}, ids,
                  ["NOAA SWPC cache miss; no live API call attempted (deterministic offline run). "
                   "L3I ionospheric_risk takes the dual-freq fallback path."])


def _l2d_021_antenna_height_auto_known(sf: dict) -> dict:
    dtype = sf.get(DEVICE_TYPE)
    ids = [DEVICE_TYPE]
    if dtype is None:
        return _field(None, ids, ["device_type null - auto-known status unknown."])
    return _field(dtype in ("CB_X", "AEROPOINT"), ids,
                  ["True -> L3I_GCP_005 antenna_height_documented_score = 100 by definition."])


def _l2d_022_position_disturbance_signature(sf: dict, derived: dict) -> dict:
    """Tier 2 (composite_flag). gap_gt_5s up AND cycle_slip up AND cn0 variance up,
    co-occurring. All three must exceed thresholds. Blind to slow settling per spec."""
    ids = [D_CYCLE_SLIP, D_GAP_GT_5S, CN0_PER_SAT, TOTAL_EPOCHS, EPOCH_INTERVAL]
    slips = derived.get(D_CYCLE_SLIP, {}).get("value")
    gaps = derived.get(D_GAP_GT_5S, {}).get("value")
    cn0_proxy = derived.get(D_MULTIPATH, {}).get("value")
    cn0_std = cn0_proxy.get("mean_of_per_sat_cn0_std_dbhz") if isinstance(cn0_proxy, dict) else None

    total_epochs = sf.get(TOTAL_EPOCHS) or 0
    interval = sf.get(EPOCH_INTERVAL) or 0
    session_hours = (total_epochs * interval) / 3600.0 if total_epochs and interval else 0.0
    slips_per_hour = (slips / session_hours) if (slips is not None and session_hours > 0) else None

    components = {
        "gaps_gt_5s_count": gaps,
        "gaps_gt_5s_elevated": gaps is not None and gaps > DISTURBANCE_GAP_GT_5S_COUNT,
        "cycle_slips_per_hour": round(slips_per_hour, 1) if slips_per_hour is not None else None,
        "cycle_slips_elevated": slips_per_hour is not None and slips_per_hour > DISTURBANCE_CYCLE_SLIPS_PER_HOUR,
        "cn0_std_mean_dbhz": cn0_std,
        "cn0_std_elevated": cn0_std is not None and cn0_std > DISTURBANCE_CN0_STD_DBHZ_MEAN,
    }
    fired = (components["gaps_gt_5s_elevated"]
             and components["cycle_slips_elevated"]
             and components["cn0_std_elevated"])
    return _field(
        {"fired": fired, "components": components,
         "thresholds": {
             "gaps_gt_5s_count_threshold": DISTURBANCE_GAP_GT_5S_COUNT,
             "cycle_slips_per_hour_threshold": DISTURBANCE_CYCLE_SLIPS_PER_HOUR,
             "cn0_std_dbhz_threshold": DISTURBANCE_CN0_STD_DBHZ_MEAN,
         }},
        ids,
        ["Spec rule: all three conditions must co-occur ('AND'). Blind to slow settling per spec note."],
    )


def _l2d_023_truncation_check(sf: dict) -> dict:
    """composite_flag, DGPS only (oplog expected-present). |session_end - obs_end|
    within tolerance; disagreement = silent truncation."""
    dtype = sf.get(DEVICE_TYPE)
    ids = [SESSION_END, OBS_END, DEVICE_TYPE]
    if dtype != "DGPS":
        return _field({"applicable": False, "agreement": None,
                       "reason": f"device_type={dtype}: oplog expected-absent; truncation check N/A"},
                      ids, ["Truncation check applies to DGPS only (oplog expected-present)."])
    session_end, obs_end = _parse_iso(sf.get(SESSION_END)), _parse_iso(sf.get(OBS_END))
    if session_end is None or obs_end is None:
        return _field({"applicable": True, "agreement": None, "reason": "session_end_utc or obs_end_utc null"},
                      ids, ["device_type=DGPS but session_end_utc null - truncation UNCONFIRMED, not failed."])
    delta = abs((session_end - obs_end).total_seconds())
    return _field({"applicable": True, "agreement": delta <= TRUNCATION_TOLERANCE_SEC,
                   "abs_delta_sec": round(delta, 3), "tolerance_sec": TRUNCATION_TOLERANCE_SEC}, ids)


# ---- flag emission ---------------------------------------------------------

def _add_flag(flags: list[dict], flag_index: dict, flag_id: str,
              condition_value: Any, derived_field: str, point_id: str) -> None:
    f = flag_index[flag_id]
    flags.append({
        "flag_id": flag_id,
        "flag_name": f["flag_name"],
        "severity": f["severity"],
        "raised_at_stage_spec": f["raised_at_stage"],
        "_origin_stage": "stage3a",
        "_origin_point": point_id,
        "_origin_derived_field": derived_field,
        "condition_value": condition_value,
    })


# ---- per-point + survey run ------------------------------------------------

def _derive_point(point: dict, rinex_pm: dict, project_root: Path, options: dict,
                  kind_by_key: dict, flag_index: dict) -> dict:
    sf = point.get("source_fields", {})
    point_id = point["point_id"]
    derived: dict[str, Any] = {}

    # Tier 1
    derived["L2D_GCP_001_occupation_coverage_ratio"] = _l2d_001_occupation_coverage_ratio(sf)
    derived["L2D_GCP_002_pre_flight_buffer_sec"] = _l2d_002_pre_flight_buffer_sec(sf)
    derived["L2D_GCP_003_post_flight_buffer_sec"] = _l2d_003_post_flight_buffer_sec(sf)
    derived["L2D_GCP_004_dual_freq_available"] = _l2d_004_dual_freq_available(sf)
    derived["L2D_GCP_005_cycle_slip_count"] = _l2d_005_cycle_slip_count(sf)
    derived["L2D_GCP_006_gap_gt_5s_count"] = _l2d_006_gap_gt_5s_count(rinex_pm)
    derived["L2D_GCP_007_any_gap_gt_60s"] = _l2d_007_any_gap_gt_60s(rinex_pm)
    derived["L2D_GCP_008_cn0_mean_dbhz"] = _l2d_008_cn0_mean_dbhz(sf)
    derived["L2D_GCP_009_multipath_risk_level"] = _l2d_009_multipath_risk_level(sf)
    derived["L2D_GCP_010_mean_pdop"] = _l2d_010_mean_pdop(sf)
    derived["L2D_GCP_011_max_pdop"] = _l2d_011_max_pdop(sf)
    derived["L2D_GCP_012_device_acquisition_time_sec"] = _l2d_012_device_acquisition_time_sec(sf)
    derived["L2D_GCP_013_rinex_version_supported"] = _l2d_013_rinex_version_supported(sf)
    derived["L2D_GCP_014_header_completeness"] = _l2d_014_header_completeness(sf)
    derived["L2D_GCP_015_constellation_count"] = _l2d_015_constellation_count(sf)
    derived["L2D_GCP_017_antenna_type_match"] = _l2d_017_antenna_type_match(sf)
    derived["L2D_GCP_018_antenna_height_agreement"] = _l2d_018_antenna_height_agreement(sf)
    derived["L2D_GCP_019_device_id_match"] = _l2d_019_device_id_match(sf)
    derived["L2D_GCP_020_kp_index"] = _l2d_020_kp_index(sf, project_root, options)
    derived["L2D_GCP_021_antenna_height_auto_known"] = _l2d_021_antenna_height_auto_known(sf)
    derived["L2D_GCP_023_truncation_check"] = _l2d_023_truncation_check(sf)

    # Tier 2 (depend on Tier 1 derived values)
    derived["L2D_GCP_016_session_integrity_ok"] = _l2d_016_session_integrity_ok(sf, derived)
    derived["L2D_GCP_022_position_disturbance_signature"] = _l2d_022_position_disturbance_signature(sf, derived)

    # Inject spec kind onto each derived field.
    for key, fobj in derived.items():
        fobj["kind"] = kind_by_key.get(key, "scoring")

    # Composite flags fired at Stage 3a.
    point_flags: list[dict] = []
    d022 = derived["L2D_GCP_022_position_disturbance_signature"]["value"]
    if isinstance(d022, dict) and d022.get("fired"):
        _add_flag(point_flags, flag_index, "FLG_GCP_011", d022,
                  "L2D_GCP_022_position_disturbance_signature", point_id)
    d023 = derived["L2D_GCP_023_truncation_check"]["value"]
    if isinstance(d023, dict) and d023.get("agreement") is False:
        _add_flag(point_flags, flag_index, "FLG_GCP_014", d023,
                  "L2D_GCP_023_truncation_check", point_id)

    return {
        "point_id": point_id,
        "device_type": point.get("device_type"),
        "device_role": point.get("device_role"),
        "derived_fields": dict(sorted(derived.items())),
        "flags_raised_stage3a_point": point_flags,
    }


def run(config: dict, project_root: Path, spec: dict, stage2_data: dict) -> dict:
    options = config.get("options", {})
    kind_by_key = {f"{d['derived_id']}_{d['derived_name']}": d["kind"] for d in spec["derived_fields"]}
    flag_index = {f["flag_id"]: f for f in spec.get("flags", [])}
    expected_count = spec["_meta"]["counts"]["derived_fields"]

    point_records: list[dict] = []
    all_flags: list[dict] = []
    notes: list[str] = []

    for point in stage2_data.get("points", []):
        rinex_pm = point.get("per_source_parser_meta", {}).get("SRC_GCP_RINEX", {})
        rec = _derive_point(point, rinex_pm, project_root, options, kind_by_key, flag_index)
        point_records.append(rec)
        all_flags.extend(rec["flags_raised_stage3a_point"])
        if len(rec["derived_fields"]) != expected_count:
            notes.append(f"{rec['point_id']}: produced {len(rec['derived_fields'])} "
                         f"L2D fields, expected {expected_count}.")

    counts_by_kind: dict[str, int] = {}
    if point_records:
        for fobj in point_records[0]["derived_fields"].values():
            counts_by_kind[fobj["kind"]] = counts_by_kind.get(fobj["kind"], 0) + 1

    return {
        "points": point_records,
        "flags_raised_stage3a": all_flags,
        "stage3a_notes": notes,
        "stage3a_meta": {
            "expected_field_count_per_point": expected_count,
            "point_count": len(point_records),
            "counts_by_kind_per_point": dict(sorted(counts_by_kind.items())),
            "tuneables": {
                "SUPPORTED_RINEX_VERSIONS": sorted(SUPPORTED_RINEX_VERSIONS),
                "ACQUISITION_NSAT_THRESHOLD": ACQUISITION_NSAT_THRESHOLD,
                "ACQUISITION_STABILITY_SEC": ACQUISITION_STABILITY_SEC,
                "BATTERY_MIN_ADEQUATE_PCT": BATTERY_MIN_ADEQUATE_PCT,
                "TRUNCATION_TOLERANCE_SEC": TRUNCATION_TOLERANCE_SEC,
                "DISTURBANCE_GAP_GT_5S_COUNT": DISTURBANCE_GAP_GT_5S_COUNT,
                "DISTURBANCE_CYCLE_SLIPS_PER_HOUR": DISTURBANCE_CYCLE_SLIPS_PER_HOUR,
                "DISTURBANCE_CN0_STD_DBHZ_MEAN": DISTURBANCE_CN0_STD_DBHZ_MEAN,
                "MULTIPATH_LOW_MAX_DBHZ": MULTIPATH_LOW_MAX_DBHZ,
                "MULTIPATH_MODERATE_MAX_DBHZ": MULTIPATH_MODERATE_MAX_DBHZ,
            },
        },
    }


def print_summary(data: dict) -> None:
    mm = data["stage3a_meta"]
    print(f"  derived per point: {mm['expected_field_count_per_point']}  "
          f"(kinds: {mm['counts_by_kind_per_point']})  points: {mm['point_count']}")
    for p in data["points"]:
        d = p["derived_fields"]
        cov = d["L2D_GCP_001_occupation_coverage_ratio"]["value"]
        integ = d["L2D_GCP_016_session_integrity_ok"]["value"]
        integ_ok = integ.get("ok") if isinstance(integ, dict) else integ
        auto = d["L2D_GCP_021_antenna_height_auto_known"]["value"]
        print(f"    - {p['point_id']}: coverage_ratio={cov} integrity_ok={integ_ok} "
              f"height_auto_known={auto} flags={len(p['flags_raised_stage3a_point'])}")
    print(f"  flags raised at Stage 3a: {len(data['flags_raised_stage3a'])}")
    for fl in data["flags_raised_stage3a"]:
        print(f"    FLAG  [{fl['_origin_point']}] {fl['flag_id']} {fl['flag_name']} ({fl['severity']})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GCP PPK Stage 3a derived fields")
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
    data = run(config, root, spec, data2)

    out_path = root / config["outputs"]["stage3_derived"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    print(f"Stage 3a derived fields -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
