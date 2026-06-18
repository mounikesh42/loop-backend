#!/usr/bin/env python3
"""Stage 3a — compute 24 L2D_BASE_* derived fields per spec sheet 03.

Field kinds (from spec):
  - scoring          (19 fields) — feed L3I_BASE_* indicators at Stage 3b
  - composite_flag   (3 fields:  020 disturbance_signature, 021 log_match_check,
                       022 truncation_check) — fire spec flags at this stage
  - handoff          (2 fields:  023 autonomous_seed_flag, 024 benchmark_unverified)
                     — always-fire-or-conditional informational handoffs

Dependency topology:
  Tier 1 (depends only on L1F source fields):  001-019, 021-024  (23 fields)
  Tier 2 (depends on L2D Tier 1):              020 disturbance_signature

Output shape per field:
  {
    "value":           <primitive | dict | None>,
    "kind":            "scoring" | "composite_flag" | "handoff" | "external",
    "input_field_ids": ["L1F_BASE_NNN_name", ...],
    "_notes":          ["..."]   (optional per-field provenance / fallback notes)
  }

Flags raised here get _origin_stage="stage3a" and surface in
data.flags_raised_stage3a so the Stage 3d aggregation can roll them up.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---- tuneables (NOT thresholds for scoring — those come from spec at L3I) ----

SUPPORTED_RINEX_VERSIONS = {"2.10", "2.11", "3.00", "3.01", "3.02", "3.03", "3.04", "3.05"}

ACQUISITION_NSAT_THRESHOLD = 8
ACQUISITION_STABILITY_SEC = 10.0

# Per schema x-on-low: "Threshold ~<=10% -> risk penalty even if session completed"
BATTERY_MIN_ADEQUATE_PCT = 10.0

# L2D_BASE_022 truncation tolerance — small clock skew between device-end-time and RINEX
# last-observation is normal. 5s gives margin without missing real truncation.
TRUNCATION_TOLERANCE_SEC = 3.0  # tightened from 5.0 — survey-grade clock skew typically <2s; real truncation events show 30+ s deltas

# L2D_BASE_020 disturbance signature — all three must co-occur ("AND")
DISTURBANCE_GAP_GT_5S_COUNT = 5
DISTURBANCE_CYCLE_SLIPS_PER_HOUR = 50
DISTURBANCE_CN0_STD_DBHZ_MEAN = 3.0


# ---- helpers ---------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


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


def _field(value: Any, kind: str, input_field_ids: list[str], notes: list[str] | None = None) -> dict:
    out: dict[str, Any] = {
        "value": value,
        "kind": kind,
        "input_field_ids": list(input_field_ids),
    }
    if notes:
        out["_notes"] = list(notes)
    return out


def _normalize_antenna_string(s: str) -> str:
    return " ".join(s.upper().split())


# ---- per-field computers (alphabetical by L2D id within each tier) --------


def _l2d_001_base_flight_coverage_ratio(sf: dict) -> dict:
    obs_s = _parse_iso(sf.get("L1F_BASE_009_obs_start_utc"))
    obs_e = _parse_iso(sf.get("L1F_BASE_010_obs_end_utc"))
    fl_s = _parse_iso(sf.get("L1F_BASE_035_flight_start_utc"))
    fl_e = _parse_iso(sf.get("L1F_BASE_036_flight_end_utc"))
    ids = [
        "L1F_BASE_009_obs_start_utc", "L1F_BASE_010_obs_end_utc",
        "L1F_BASE_035_flight_start_utc", "L1F_BASE_036_flight_end_utc",
    ]
    if not (obs_s and obs_e and fl_s and fl_e):
        return _field(0.0, "scoring", ids,
                      ["One or more time inputs missing — ratio defaulted to 0 (will trip coverage gate at L3I_BASE_001)."])
    flight_dur = (fl_e - fl_s).total_seconds()
    if flight_dur <= 0:
        return _field(0.0, "scoring", ids,
                      [f"flight_end <= flight_start (dur={flight_dur}s) — ratio = 0."])
    overlap = max(0.0, (min(obs_e, fl_e) - max(obs_s, fl_s)).total_seconds())
    return _field(round(overlap / flight_dur, 4), "scoring", ids)


def _l2d_002_pre_flight_buffer_sec(sf: dict) -> dict:
    obs_s = _parse_iso(sf.get("L1F_BASE_009_obs_start_utc"))
    fl_s = _parse_iso(sf.get("L1F_BASE_035_flight_start_utc"))
    ids = ["L1F_BASE_009_obs_start_utc", "L1F_BASE_035_flight_start_utc"]
    if not (obs_s and fl_s):
        return _field(None, "scoring", ids, ["Time input missing — null."])
    return _field(round((fl_s - obs_s).total_seconds(), 1), "scoring", ids)


def _l2d_003_post_flight_buffer_sec(sf: dict) -> dict:
    obs_e = _parse_iso(sf.get("L1F_BASE_010_obs_end_utc"))
    fl_e = _parse_iso(sf.get("L1F_BASE_036_flight_end_utc"))
    ids = ["L1F_BASE_010_obs_end_utc", "L1F_BASE_036_flight_end_utc"]
    if not (obs_e and fl_e):
        return _field(None, "scoring", ids, ["Time input missing — null."])
    return _field(round((obs_e - fl_e).total_seconds(), 1), "scoring", ids)


def _l2d_004_dual_freq_available(sf: dict) -> dict:
    v = sf.get("L1F_BASE_011_dual_freq_present")
    return _field(bool(v) if v is not None else None, "scoring", ["L1F_BASE_011_dual_freq_present"])


def _l2d_005_cycle_slip_count(sf: dict) -> dict:
    cs = sf.get("L1F_BASE_015_cycle_slip_markers") or {}
    total = cs.get("total_count") if isinstance(cs, dict) else None
    return _field(total, "scoring", ["L1F_BASE_015_cycle_slip_markers"])


def _l2d_006_gap_gt_5s_count(sf: dict, rinex_parser_meta: dict) -> dict:
    """L1F input spec lists epoch_interval_sec+total_epochs, but counting actual
    gaps needs per-epoch deltas. Parser pre-computed and surfaced this value
    in parser_meta.stream_stats_for_derived_fields."""
    stats = rinex_parser_meta.get("stream_stats_for_derived_fields") or {}
    v = stats.get("count_gap_gt_5s")
    notes = [
        "Spec lists inputs as L1F_BASE_012 epoch_interval_sec + L1F_BASE_013 total_epochs, "
        "but actual gap counting requires per-epoch timestamps. Parser pre-computed this "
        "value during streaming and surfaced it in parser_meta.stream_stats_for_derived_fields."
    ]
    return _field(v, "scoring",
                  ["L1F_BASE_012_epoch_interval_sec", "L1F_BASE_013_total_epochs"], notes)


def _l2d_007_any_gap_gt_60s(sf: dict, rinex_parser_meta: dict) -> dict:
    stats = rinex_parser_meta.get("stream_stats_for_derived_fields") or {}
    v = stats.get("count_gap_gt_60s")
    out = (v is not None and v > 0)
    notes = [
        "Computed as count_gap_gt_60s > 0 from parser_meta.stream_stats_for_derived_fields."
    ]
    return _field(out, "scoring",
                  ["L1F_BASE_012_epoch_interval_sec", "L1F_BASE_013_total_epochs"], notes)


def _l2d_008_cn0_mean_dbhz(sf: dict) -> dict:
    cn0 = sf.get("L1F_BASE_014_cn0_per_sat") or {}
    v = cn0.get("overall_mean_dbhz") if isinstance(cn0, dict) else None
    return _field(round(v, 3) if v is not None else None, "scoring", ["L1F_BASE_014_cn0_per_sat"])


def _l2d_009_multipath_risk_level(sf: dict) -> dict:
    """Spec formula: 'std-dev of cn0_per_sat across sats at equal elevation'.
    Per-epoch satellite elevation is not available at Stage 1 (no orbit
    propagation per epoch). Fallback: mean of per-sat C/N0 std-dev across the
    full session. Includes elevation-driven variance, so the value is slightly
    conservative for high-multipath detection."""
    cn0 = sf.get("L1F_BASE_014_cn0_per_sat") or {}
    per_sat = cn0.get("per_sat") if isinstance(cn0, dict) else None
    if not isinstance(per_sat, dict) or not per_sat:
        return _field(None, "scoring", ["L1F_BASE_014_cn0_per_sat"],
                      ["No per-sat C/N0 stats — multipath proxy null."])
    stds = [s.get("std_dbhz") for s in per_sat.values() if isinstance(s, dict) and s.get("std_dbhz") is not None]
    if not stds:
        return _field(None, "scoring", ["L1F_BASE_014_cn0_per_sat"],
                      ["No std_dbhz values in per_sat — multipath proxy null."])
    mean_std = round(statistics.fmean(stds), 3)
    return _field(
        {
            "mean_of_per_sat_cn0_std_dbhz": mean_std,
            "n_sats": len(stds),
        },
        "scoring",
        ["L1F_BASE_014_cn0_per_sat"],
        [
            "Spec asks for std-dev at equal elevation; per-epoch elevation unavailable at S1. "
            "Proxy: mean of per-sat C/N0 std-dev across the session. Conservative for multipath "
            "detection (some std-dev is elevation-driven, not multipath)."
        ],
    )


def _l2d_010_mean_pdop(sf: dict) -> dict:
    pd = sf.get("L1F_BASE_016_pdop_per_epoch")
    fl_s = _parse_iso(sf.get("L1F_BASE_035_flight_start_utc"))
    fl_e = _parse_iso(sf.get("L1F_BASE_036_flight_end_utc"))
    ids = ["L1F_BASE_016_pdop_per_epoch", "L1F_BASE_035_flight_start_utc", "L1F_BASE_036_flight_end_utc"]
    if not isinstance(pd, dict) or not pd.get("samples"):
        return _field(None, "scoring", ids, ["No PDOP samples available — null."])
    if not (fl_s and fl_e):
        return _field(pd["summary"].get("mean"), "scoring", ids,
                      ["No flight window provided — using mean across the entire base session."])
    vals = []
    for s in pd["samples"]:
        ts = _parse_iso(s.get("epoch_utc"))
        if ts is not None and fl_s <= ts <= fl_e:
            vals.append(s["pdop"])
    if not vals:
        return _field(None, "scoring", ids,
                      ["No PDOP samples fell inside flight window — null."])
    return _field(round(statistics.fmean(vals), 3), "scoring", ids,
                  [f"Filtered to {len(vals)} PDOP samples in flight window."])


def _l2d_011_max_pdop(sf: dict) -> dict:
    pd = sf.get("L1F_BASE_016_pdop_per_epoch")
    fl_s = _parse_iso(sf.get("L1F_BASE_035_flight_start_utc"))
    fl_e = _parse_iso(sf.get("L1F_BASE_036_flight_end_utc"))
    ids = ["L1F_BASE_016_pdop_per_epoch", "L1F_BASE_035_flight_start_utc", "L1F_BASE_036_flight_end_utc"]
    if not isinstance(pd, dict) or not pd.get("samples"):
        return _field(None, "scoring", ids, ["No PDOP samples available — null."])
    if not (fl_s and fl_e):
        return _field(pd["summary"].get("max"), "scoring", ids,
                      ["No flight window provided — using max across the entire base session."])
    vals = []
    for s in pd["samples"]:
        ts = _parse_iso(s.get("epoch_utc"))
        if ts is not None and fl_s <= ts <= fl_e:
            vals.append(s["pdop"])
    if not vals:
        return _field(None, "scoring", ids, ["No PDOP samples in flight window — null."])
    return _field(round(max(vals), 3), "scoring", ids,
                  [f"Filtered to {len(vals)} PDOP samples in flight window."])


def _l2d_012_base_acquisition_time_sec(sf: dict) -> dict:
    sc = sf.get("L1F_BASE_017_sat_count_per_epoch") or {}
    samples = sc.get("acquisition_samples") if isinstance(sc, dict) else None
    if not samples:
        return _field(None, "scoring", ["L1F_BASE_017_sat_count_per_epoch"],
                      ["No acquisition samples — null."])
    # Find first epoch where sat_count remains >= threshold for STABILITY_SEC.
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
            return _field(round(offset, 1), "scoring",
                          ["L1F_BASE_017_sat_count_per_epoch"],
                          [
                              f"First epoch where nsat ≥ {ACQUISITION_NSAT_THRESHOLD} held for "
                              f"≥ {ACQUISITION_STABILITY_SEC}s contiguously."
                          ])
    return _field(None, "scoring", ["L1F_BASE_017_sat_count_per_epoch"],
                  [f"Stable nsat ≥ {ACQUISITION_NSAT_THRESHOLD} for ≥ "
                   f"{ACQUISITION_STABILITY_SEC}s never reached in first window."])


def _l2d_013_rinex_version_supported(sf: dict) -> dict:
    ver = sf.get("L1F_BASE_007_rinex_version")
    if ver is None:
        return _field(None, "scoring", ["L1F_BASE_007_rinex_version"])
    return _field(ver in SUPPORTED_RINEX_VERSIONS, "scoring", ["L1F_BASE_007_rinex_version"])


def _l2d_014_header_completeness(sf: dict) -> dict:
    a_type = sf.get("L1F_BASE_002_antenna_type")
    r_type = sf.get("L1F_BASE_004_receiver_type")
    pos = sf.get("L1F_BASE_006_approx_position_xyz")
    ids = ["L1F_BASE_002_antenna_type", "L1F_BASE_004_receiver_type", "L1F_BASE_006_approx_position_xyz"]
    components = {
        "antenna_type_present": bool(a_type),
        "receiver_type_present": bool(r_type),
        "approx_position_present": bool(pos),
    }
    complete = all(components.values())
    return _field({"complete": complete, "components": components}, "scoring", ids)


def _l2d_015_constellation_count(sf: dict) -> dict:
    cs = sf.get("L1F_BASE_008_constellation_set")
    if not isinstance(cs, list):
        return _field(None, "scoring", ["L1F_BASE_008_constellation_set"])
    return _field(len(cs), "scoring", ["L1F_BASE_008_constellation_set"])


def _l2d_016_session_integrity_ok(sf: dict) -> dict:
    completed = sf.get("L1F_BASE_018_session_completed_normally")
    shutdowns = sf.get("L1F_BASE_019_unexpected_shutdown_count")
    bat_min = sf.get("L1F_BASE_022_battery_min_pct")
    ids = [
        "L1F_BASE_018_session_completed_normally",
        "L1F_BASE_019_unexpected_shutdown_count",
        "L1F_BASE_022_battery_min_pct",
    ]
    # Null in any required signal → UNCONFIRMED (None), not False (per schema rule).
    if completed is None and shutdowns is None and bat_min is None:
        return _field(None, "scoring", ids,
                      ["All OPLOG signals null — UNCONFIRMED per schema x-nullability-rule."])
    if completed is None or shutdowns is None:
        return _field(None, "scoring", ids,
                      ["A core OPLOG signal is null — UNCONFIRMED, not False."])
    parts = {
        "completed_normally": bool(completed),
        "no_shutdowns": shutdowns == 0,
        "battery_min_adequate": (
            True if bat_min is None  # mains/solar
            else (bat_min > BATTERY_MIN_ADEQUATE_PCT)
        ),
    }
    ok = all(parts.values())
    return _field({"ok": ok, "components": parts}, "scoring", ids)


def _l2d_017_antenna_type_match(sf: dict) -> dict:
    model = sf.get("L1F_BASE_025_antenna_model")
    a_type = sf.get("L1F_BASE_002_antenna_type")
    ids = ["L1F_BASE_025_antenna_model", "L1F_BASE_002_antenna_type"]
    if not model or not a_type:
        return _field(None, "scoring", ids,
                      ["antenna_model or antenna_type empty/null — cannot compare."])
    return _field(_normalize_antenna_string(model) == _normalize_antenna_string(a_type),
                  "scoring", ids)


def _l2d_018_antenna_height_agreement(sf: dict) -> dict:
    h_m = sf.get("L1F_BASE_026_antenna_height_m")
    delta_h = sf.get("L1F_BASE_003_antenna_delta_h")
    ids = ["L1F_BASE_026_antenna_height_m", "L1F_BASE_003_antenna_delta_h"]
    if h_m is None:
        return _field(None, "scoring", ids, ["antenna_height_m null — cannot compare."])
    if delta_h is None:
        return _field(None, "scoring", ids, ["antenna_delta_h null — cannot compare."])
    if abs(delta_h) < 1e-9:
        return _field(
            {"agreement": None, "reason": "delta_h is 0.000 in RINEX header (device unset)"},
            "scoring", ids,
            ["Per spec rule, cross-check skipped when delta_h == 0; agreement is N/A."],
        )
    diff = round(abs(h_m - delta_h), 4)
    # Tolerance: 0.005 m = 5 mm. A real survey antenna setup repeats to better
    # than ±2 mm; 5 mm tolerance is generous.
    tolerance = 0.005
    return _field(
        {"agreement": diff <= tolerance, "abs_diff_m": diff, "tolerance_m": tolerance},
        "scoring", ids,
    )


def _l2d_019_kp_index(sf: dict, project_root: Path, options: dict) -> dict:
    """External NOAA SWPC dependency. Spec acknowledges this is not yet built.
    Strategy: check the local cache directory; if absent, return null with
    status API_UNAVAILABLE so L3I_BASE_009 ionospheric_risk_score can take
    the fallback path (dual-freq → top band, single-freq → bottom band).
    No network call is attempted from the parser."""
    obs_s = _parse_iso(sf.get("L1F_BASE_009_obs_start_utc"))
    ids = ["L1F_BASE_009_obs_start_utc"]
    if obs_s is None:
        return _field(None, "scoring", ids, ["obs_start_utc null — no lookup."])
    cache_dir = project_root / options.get("noaa_swpc_cache_dir", "cache/noaa_swpc/")
    cache_file = cache_dir / f"{obs_s.strftime('%Y-%m-%d')}.json"
    if cache_file.exists():
        try:
            import json as _json
            with cache_file.open("r", encoding="utf-8") as fh:
                payload = _json.load(fh)
            return _field(
                {"kp": payload.get("kp"), "source": f"cache/{cache_file.name}", "status": "OK"},
                "scoring", ids,
                ["Spec kind=scoring with external NOAA SWPC dependency. Read from local cache; no network call."],
            )
        except (OSError, ValueError):
            pass
    return _field(
        {"kp": None, "source": None, "status": "API_UNAVAILABLE"},
        "scoring", ids,
        [
            "NOAA SWPC cache miss and no live API call attempted at Stage 1. "
            "L3I_BASE_009 ionospheric_risk_score takes the fallback path "
            "(dual-freq → top band, single-freq → bottom band)."
        ],
    )


# -- composite flags (derived but also fire spec flags) --


def _l2d_020_disturbance_signature(derived: dict, sf: dict) -> dict:
    """Tier 2 — depends on L2D_005 cycle_slip_count and L2D_006 gap_gt_5s_count
    plus L1F cn0_per_sat. Spec: gap_gt_5s↑ AND cycle_slip↑ AND cn0 variance↑
    co-occurring. All three must exceed thresholds."""
    ids = [
        "L2D_BASE_005_cycle_slip_count",
        "L2D_BASE_006_gap_gt_5s_count",
        "L1F_BASE_014_cn0_per_sat",
        "L1F_BASE_013_total_epochs",
        "L1F_BASE_012_epoch_interval_sec",
    ]
    slips = derived.get("L2D_BASE_005_cycle_slip_count", {}).get("value")
    gaps = derived.get("L2D_BASE_006_gap_gt_5s_count", {}).get("value")
    cn0_proxy = derived.get("L2D_BASE_009_multipath_risk_level", {}).get("value")
    cn0_std = cn0_proxy.get("mean_of_per_sat_cn0_std_dbhz") if isinstance(cn0_proxy, dict) else None

    total_epochs = sf.get("L1F_BASE_013_total_epochs") or 0
    interval = sf.get("L1F_BASE_012_epoch_interval_sec") or 0
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
    fired = (
        components["gaps_gt_5s_elevated"]
        and components["cycle_slips_elevated"]
        and components["cn0_std_elevated"]
    )
    return _field(
        {"fired": fired, "components": components,
         "thresholds": {
             "gaps_gt_5s_count_threshold": DISTURBANCE_GAP_GT_5S_COUNT,
             "cycle_slips_per_hour_threshold": DISTURBANCE_CYCLE_SLIPS_PER_HOUR,
             "cn0_std_dbhz_threshold": DISTURBANCE_CN0_STD_DBHZ_MEAN,
         }},
        "composite_flag", ids,
        ["Spec rule: all three conditions must co-occur ('AND'). Blind to slow settling per spec note."],
    )


def _l2d_021_log_match_check(sf: dict) -> dict:
    obs_s = _parse_iso(sf.get("L1F_BASE_009_obs_start_utc"))
    obs_e = _parse_iso(sf.get("L1F_BASE_010_obs_end_utc"))
    fl_s = _parse_iso(sf.get("L1F_BASE_035_flight_start_utc"))
    fl_e = _parse_iso(sf.get("L1F_BASE_036_flight_end_utc"))
    ids = [
        "L1F_BASE_009_obs_start_utc", "L1F_BASE_010_obs_end_utc",
        "L1F_BASE_035_flight_start_utc", "L1F_BASE_036_flight_end_utc",
    ]
    if not (obs_s and obs_e and fl_s and fl_e):
        return _field({"matched": None, "reason": "time input null"}, "composite_flag", ids)
    matched = (obs_s <= fl_s) and (fl_e <= obs_e)
    detail = {
        "matched": matched,
        "flight_start_inside": obs_s <= fl_s,
        "flight_end_inside": fl_e <= obs_e,
    }
    return _field(detail, "composite_flag", ids)


def _l2d_022_truncation_check(sf: dict) -> dict:
    session_end = _parse_iso(sf.get("L1F_BASE_023_session_end_utc"))
    obs_end = _parse_iso(sf.get("L1F_BASE_010_obs_end_utc"))
    ids = ["L1F_BASE_023_session_end_utc", "L1F_BASE_010_obs_end_utc"]
    if session_end is None or obs_end is None:
        return _field(
            {"agreement": None, "reason": "session_end_utc or obs_end_utc null"},
            "composite_flag", ids,
            ["session_end_utc null (OPLOG absent) — truncation check unconfirmed, not failed."],
        )
    delta = abs((session_end - obs_end).total_seconds())
    return _field(
        {
            "agreement": delta <= TRUNCATION_TOLERANCE_SEC,
            "abs_delta_sec": round(delta, 3),
            "tolerance_sec": TRUNCATION_TOLERANCE_SEC,
        },
        "composite_flag", ids,
    )


# -- handoff derived fields --


def _l2d_023_autonomous_seed_flag(sf: dict) -> dict:
    """Spec: 'approx_position_xyz is autonomous (always true at S1)'. The base
    RINEX header position is always an autonomous seed at Stage 1 — judgement
    deferred to pre-processing. NON-scoring; always fires the handoff flag."""
    pos = sf.get("L1F_BASE_006_approx_position_xyz")
    ids = ["L1F_BASE_006_approx_position_xyz"]
    return _field({"is_autonomous_seed": True, "position_xyz": pos},
                  "handoff", ids,
                  ["Always-fire at Stage 1 per spec. Judgement deferred to pre_processing_score."])


def _l2d_024_benchmark_unverified(sf: dict) -> dict:
    """Spec formula: 'over_known_mark reused AND NOT verified_by_second_person'.
    The 'reused' qualifier needs cross-session data (Learning Engine territory);
    at Stage 1 single-session, fire if over_known_mark=True AND verified=False."""
    okm = sf.get("L1F_BASE_033_over_known_mark")
    verified = sf.get("L1F_BASE_034_verified_by_second_person")
    monument = sf.get("L1F_BASE_032_monument_id")
    ids = [
        "L1F_BASE_033_over_known_mark",
        "L1F_BASE_034_verified_by_second_person",
        "L1F_BASE_032_monument_id",
    ]
    if okm is None or verified is None:
        return _field({"fired": None, "reason": "input null"}, "handoff", ids)
    fired = bool(okm) and not bool(verified)
    return _field(
        {"fired": fired, "over_known_mark": okm, "verified_by_second_person": verified,
         "monument_id": monument},
        "handoff", ids,
        ["Cross-session 'reused' interpretation requires Learning Engine context; at S1, "
         "single-session interpretation is okm=True AND verified=False."],
    )


# ---- main run() -----------------------------------------------------------

def _add_flag(flags_list: list[dict], flag_id: str, flag_name: str, severity: str,
              raised_at_stage: str, condition_value: Any, derived_field: str) -> None:
    flags_list.append({
        "flag_id": flag_id,
        "flag_name": flag_name,
        "severity": severity,
        "raised_at_stage_spec": raised_at_stage,
        "_origin_stage": "stage3a",
        "_origin_derived_field": derived_field,
        "condition_value": condition_value,
    })


def run(config: dict, project_root: Path, spec: dict, stage2_data: dict) -> dict:
    started_at = datetime.now(timezone.utc)
    sf = stage2_data.get("source_fields", {})
    parser_meta = stage2_data.get("per_source_parser_meta", {})
    rinex_pm = parser_meta.get("SRC_BASE_RINEX", {})
    options = config.get("options", {})

    derived: dict[str, Any] = {}
    flags_raised_stage3a: list[dict] = []
    stage3a_notes: list[str] = []

    # ---- Tier 1: depends only on L1F ----
    derived["L2D_BASE_001_base_flight_coverage_ratio"] = _l2d_001_base_flight_coverage_ratio(sf)
    derived["L2D_BASE_002_pre_flight_buffer_sec"] = _l2d_002_pre_flight_buffer_sec(sf)
    derived["L2D_BASE_003_post_flight_buffer_sec"] = _l2d_003_post_flight_buffer_sec(sf)
    derived["L2D_BASE_004_dual_freq_available"] = _l2d_004_dual_freq_available(sf)
    derived["L2D_BASE_005_cycle_slip_count"] = _l2d_005_cycle_slip_count(sf)
    derived["L2D_BASE_006_gap_gt_5s_count"] = _l2d_006_gap_gt_5s_count(sf, rinex_pm)
    derived["L2D_BASE_007_any_gap_gt_60s"] = _l2d_007_any_gap_gt_60s(sf, rinex_pm)
    derived["L2D_BASE_008_cn0_mean_dbhz"] = _l2d_008_cn0_mean_dbhz(sf)
    derived["L2D_BASE_009_multipath_risk_level"] = _l2d_009_multipath_risk_level(sf)
    derived["L2D_BASE_010_mean_pdop"] = _l2d_010_mean_pdop(sf)
    derived["L2D_BASE_011_max_pdop"] = _l2d_011_max_pdop(sf)
    derived["L2D_BASE_012_base_acquisition_time_sec"] = _l2d_012_base_acquisition_time_sec(sf)
    derived["L2D_BASE_013_rinex_version_supported"] = _l2d_013_rinex_version_supported(sf)
    derived["L2D_BASE_014_header_completeness"] = _l2d_014_header_completeness(sf)
    derived["L2D_BASE_015_constellation_count"] = _l2d_015_constellation_count(sf)
    derived["L2D_BASE_016_session_integrity_ok"] = _l2d_016_session_integrity_ok(sf)
    derived["L2D_BASE_017_antenna_type_match"] = _l2d_017_antenna_type_match(sf)
    derived["L2D_BASE_018_antenna_height_agreement"] = _l2d_018_antenna_height_agreement(sf)
    derived["L2D_BASE_019_kp_index"] = _l2d_019_kp_index(sf, project_root, options)
    derived["L2D_BASE_021_log_match_check"] = _l2d_021_log_match_check(sf)
    derived["L2D_BASE_022_truncation_check"] = _l2d_022_truncation_check(sf)
    derived["L2D_BASE_023_autonomous_seed_flag"] = _l2d_023_autonomous_seed_flag(sf)
    derived["L2D_BASE_024_benchmark_unverified"] = _l2d_024_benchmark_unverified(sf)

    # ---- Tier 2: depends on L2D ----
    derived["L2D_BASE_020_disturbance_signature"] = _l2d_020_disturbance_signature(derived, sf)

    # ---- Flag emission from composite/handoff derivations ----
    # Build a spec flag lookup for severity / spec_stage.
    flag_index = {f["flag_id"]: f for f in spec.get("flags", [])}

    # L2D_BASE_020 → FLG_BASE_010 BASE_POSITION_DISCONTINUITY (composite)
    d020 = derived["L2D_BASE_020_disturbance_signature"]["value"]
    if isinstance(d020, dict) and d020.get("fired"):
        f = flag_index["FLG_BASE_010"]
        _add_flag(flags_raised_stage3a, "FLG_BASE_010", f["flag_name"], f["severity"],
                  f["raised_at_stage"], d020, "L2D_BASE_020_disturbance_signature")

    # L2D_BASE_021 → FLG_BASE_011 BASE_FLIGHT_LOG_MISMATCH (composite)
    d021 = derived["L2D_BASE_021_log_match_check"]["value"]
    if isinstance(d021, dict) and d021.get("matched") is False:
        f = flag_index["FLG_BASE_011"]
        _add_flag(flags_raised_stage3a, "FLG_BASE_011", f["flag_name"], f["severity"],
                  f["raised_at_stage"], d021, "L2D_BASE_021_log_match_check")

    # L2D_BASE_022 → FLG_BASE_012 BASE_RINEX_TRUNCATED (composite)
    d022 = derived["L2D_BASE_022_truncation_check"]["value"]
    if isinstance(d022, dict) and d022.get("agreement") is False:
        f = flag_index["FLG_BASE_012"]
        _add_flag(flags_raised_stage3a, "FLG_BASE_012", f["flag_name"], f["severity"],
                  f["raised_at_stage"], d022, "L2D_BASE_022_truncation_check")

    # L2D_BASE_023 → FLG_BASE_013 BASE_AUTONOMOUS_SEED (always fire — handoff)
    d023 = derived["L2D_BASE_023_autonomous_seed_flag"]["value"]
    if isinstance(d023, dict) and d023.get("is_autonomous_seed"):
        f = flag_index["FLG_BASE_013"]
        _add_flag(flags_raised_stage3a, "FLG_BASE_013", f["flag_name"], f["severity"],
                  f["raised_at_stage"], d023, "L2D_BASE_023_autonomous_seed_flag")

    # L2D_BASE_024 → FLG_BASE_014 BASE_BENCHMARK_UNVERIFIED (conditional handoff)
    d024 = derived["L2D_BASE_024_benchmark_unverified"]["value"]
    if isinstance(d024, dict) and d024.get("fired"):
        f = flag_index["FLG_BASE_014"]
        _add_flag(flags_raised_stage3a, "FLG_BASE_014", f["flag_name"], f["severity"],
                  f["raised_at_stage"], d024, "L2D_BASE_024_benchmark_unverified")

    # ---- expected vs produced count check ----
    expected_count = spec["_meta"]["counts"]["derived_fields"]
    produced_count = len(derived)
    if produced_count != expected_count:
        stage3a_notes.append(
            f"Produced {produced_count} L2D fields, expected {expected_count}."
        )

    # Per-kind counts
    counts_by_kind: dict[str, int] = {}
    for f in derived.values():
        counts_by_kind[f["kind"]] = counts_by_kind.get(f["kind"], 0) + 1

    finished_at = datetime.now(timezone.utc)

    return {
        "derived_fields": dict(sorted(derived.items())),
        "flags_raised_stage3a": flags_raised_stage3a,
        "stage3a_notes": stage3a_notes,
        "stage3a_meta": {
            "expected_field_count": expected_count,
            "produced_field_count": produced_count,
            "counts_by_kind": dict(sorted(counts_by_kind.items())),
            "tuneables": {
                "ACQUISITION_NSAT_THRESHOLD": ACQUISITION_NSAT_THRESHOLD,
                "ACQUISITION_STABILITY_SEC": ACQUISITION_STABILITY_SEC,
                "BATTERY_MIN_ADEQUATE_PCT": BATTERY_MIN_ADEQUATE_PCT,
                "TRUNCATION_TOLERANCE_SEC": TRUNCATION_TOLERANCE_SEC,
                "DISTURBANCE_GAP_GT_5S_COUNT": DISTURBANCE_GAP_GT_5S_COUNT,
                "DISTURBANCE_CYCLE_SLIPS_PER_HOUR": DISTURBANCE_CYCLE_SLIPS_PER_HOUR,
                "DISTURBANCE_CN0_STD_DBHZ_MEAN": DISTURBANCE_CN0_STD_DBHZ_MEAN,
                "SUPPORTED_RINEX_VERSIONS": sorted(SUPPORTED_RINEX_VERSIONS),
            },
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "wall_time_sec": round((finished_at - started_at).total_seconds(), 3),
        },
    }
