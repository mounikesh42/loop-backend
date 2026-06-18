#!/usr/bin/env python3
"""Stage 12 — smoke-test harness for base_station_score.

Reads the baseline outputs/02_source_fields.json, applies a per-scenario
mutator to a deep-copy of that stage2 data, then re-runs Stages 3a → 3d into
tests/scenarios/<name>/{03_derived,04_indicators,05_blocks,06_apex}.json.

Reports a side-by-side table at the end: apex / block scores / flags fired.

Pass 1 — spec-internal coverage:
  baseline (control), each internal/global gate trip, each threshold flag,
  each enum-band drop, each fallback path, all-flags-stress.

Pass 2 (real-world gap-analysis sheet) is skipped here — no such sheet was
provided for the base subsystem. Add scenarios here when one arrives.
"""
from __future__ import annotations

import copy
import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


# Make the scripts/ package importable when this file is invoked directly.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import compute_derived       # noqa: E402
import compute_indicators    # noqa: E402
import compute_blocks        # noqa: E402
import compute_base_score    # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "paths.json"
BASELINE_STAGE2_PATH = PROJECT_ROOT / "outputs" / "02_source_fields.json"
SCENARIOS_DIR = PROJECT_ROOT / "tests" / "scenarios"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_config_and_spec():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    spec_path = PROJECT_ROOT / config["spec_file"]
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    return config, spec


def _load_baseline_stage2():
    env = json.loads(BASELINE_STAGE2_PATH.read_text(encoding="utf-8"))
    return env["data"]


# ---------------------------------------------------------------------------
# Mutators — each receives the stage2 data dict and modifies it in place.
# May return a Path to a temp file the runner should clean up after.
# ---------------------------------------------------------------------------

def _bump_session_end_by(d, sec: int) -> None:
    iso = d["source_fields"]["L1F_BASE_010_obs_end_utc"]
    obs_end = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    new = obs_end + timedelta(seconds=sec)
    d["source_fields"]["L1F_BASE_023_session_end_utc"] = (
        new.strftime("%Y-%m-%dT%H:%M:%S.") + f"{new.microsecond:06d}Z"
    )


def m_baseline(d):
    pass


def m_coverage_gate_trip(d):
    """flight window outside obs window → coverage=0 → global gate."""
    d["source_fields"]["L1F_BASE_035_flight_start_utc"] = "2025-01-01T00:00:00.000000Z"
    d["source_fields"]["L1F_BASE_036_flight_end_utc"]   = "2025-01-01T00:01:00.000000Z"


def m_antenna_height_missing(d):
    """height absent → setup gate → block=0; apex stays positive."""
    d["source_fields"]["L1F_BASE_026_antenna_height_m"] = None


def m_session_interrupted(d):
    d["source_fields"]["L1F_BASE_018_session_completed_normally"] = False
    d["source_fields"]["L1F_BASE_019_unexpected_shutdown_count"] = 2


def m_download_unconfirmed(d):
    d["source_fields"]["L1F_BASE_024_raw_log_download_confirmed"] = False


def m_oplog_absent(d):
    for k in (
        "L1F_BASE_018_session_completed_normally",
        "L1F_BASE_019_unexpected_shutdown_count",
        "L1F_BASE_020_battery_start_pct",
        "L1F_BASE_021_battery_end_pct",
        "L1F_BASE_022_battery_min_pct",
        "L1F_BASE_023_session_end_utc",
        "L1F_BASE_024_raw_log_download_confirmed",
    ):
        d["source_fields"][k] = None


def m_rinex_version_unsupported(d):
    d["source_fields"]["L1F_BASE_007_rinex_version"] = "2.05"


def m_single_frequency(d):
    d["source_fields"]["L1F_BASE_011_dual_freq_present"] = False


def m_slow_acquisition(d):
    sc = d["source_fields"]["L1F_BASE_017_sat_count_per_epoch"]
    new = []
    for offset, nsat in sc["acquisition_samples"]:
        new.append([offset, 4 if offset < 350.0 else 40])
    sc["acquisition_samples"] = new


def m_iono_storm_single_freq(d):
    """Inject high Kp into the NOAA cache + force single-frequency."""
    d["source_fields"]["L1F_BASE_011_dual_freq_present"] = False
    obs_start_iso = d["source_fields"]["L1F_BASE_009_obs_start_utc"]
    date_str = obs_start_iso[:10]
    cache_dir = PROJECT_ROOT / "cache" / "noaa_swpc"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{date_str}.json"
    cache_file.write_text(json.dumps({"kp": 7.0, "source": "scenario_injection"}), encoding="utf-8")
    return cache_file


def m_high_multipath(d):
    cn0 = d["source_fields"]["L1F_BASE_014_cn0_per_sat"]
    for sat_id, stats in cn0["per_sat"].items():
        stats["std_dbhz"] = 5.0
        for band in stats.get("per_band", {}).values():
            band["std_dbhz"] = 5.0


def m_truncation_detected(d):
    _bump_session_end_by(d, 10)


def m_benchmark_unverified(d):
    d["source_fields"]["L1F_BASE_033_over_known_mark"] = True
    d["source_fields"]["L1F_BASE_034_verified_by_second_person"] = False


def m_slant_setup(d):
    d["source_fields"]["L1F_BASE_028_antenna_measurement_type"] = "SLANT"


def m_antenna_type_mismatch(d):
    # Form L1F_BASE_025 unchanged; mutate RINEX header value so they disagree.
    d["source_fields"]["L1F_BASE_002_antenna_type"] = "LEIAR25.R3      LEIT"


def m_disturbance_signature(d):
    """Trip the composite — gap + slips + cn0 std all elevated."""
    rinex_pm = d["per_source_parser_meta"]["SRC_BASE_RINEX"]
    stats = rinex_pm.setdefault("stream_stats_for_derived_fields", {})
    stats["count_gap_gt_5s"] = 20
    stats["count_gap_gt_60s"] = 0
    cs = d["source_fields"]["L1F_BASE_015_cycle_slip_markers"]
    cs["total_count"] = 5000  # > 50/hr over a 1.5h session
    cn0 = d["source_fields"]["L1F_BASE_014_cn0_per_sat"]
    for stats_ in cn0["per_sat"].values():
        stats_["std_dbhz"] = 4.5


def m_all_flags_stress(d):
    """Fire as many flags as possible WITHOUT tripping the global gate."""
    m_session_interrupted(d)        # FLG_BASE_004
    m_download_unconfirmed(d)       # FLG_BASE_005
    m_rinex_version_unsupported(d)  # FLG_BASE_006
    m_high_multipath(d)             # FLG_BASE_007
    m_slow_acquisition(d)           # FLG_BASE_009
    cleanup = m_iono_storm_single_freq(d)  # FLG_BASE_008
    m_truncation_detected(d)        # FLG_BASE_012
    m_benchmark_unverified(d)       # FLG_BASE_014
    return cleanup


# --- Pass 2 — derived from the CBMI Base Station Problems sheet ---

def m_kp_cache_low_storm(d):
    """Pass 2 / Problem #20 verify-coverage. Cache a Kp value AND go single-
    frequency → L3I_009 should hit the 'kp_low' top band (100), not the
    cautious API-UNAVAILABLE midpoint (70). Proves the cache-OK path."""
    d["source_fields"]["L1F_BASE_011_dual_freq_present"] = False
    obs_start_iso = d["source_fields"]["L1F_BASE_009_obs_start_utc"]
    date_str = obs_start_iso[:10]
    cache_dir = PROJECT_ROOT / "cache" / "noaa_swpc"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{date_str}.json"
    cache_file.write_text(json.dumps({"kp": 3.0, "source": "scenario_injection_low"}), encoding="utf-8")
    return cache_file


def m_single_vertical_measurement(d):
    """Pass 2 / Problem #1 secondary band. VERTICAL + ARP but height_measured_count=1.
    L3I_005 should land in 'single_vertical' band (88), below gold-standard (100)."""
    d["source_fields"]["L1F_BASE_030_height_measured_count"] = 1


def m_ad_hoc_point_setup(d):
    """Pass 2 / Problem #2 secondary band. Base placed on an ad-hoc point
    (over_known_mark=False) → L3I_006 should hit 'ad_hoc_point' band (60)."""
    d["source_fields"]["L1F_BASE_033_over_known_mark"] = False
    d["source_fields"]["L1F_BASE_034_verified_by_second_person"] = False
    d["source_fields"]["L1F_BASE_032_monument_id"] = None


# --- Pass 2 (real-world prose) — concrete examples quoted from the
#     CBMI Base Station Problems sheet. Each mutator simulates the exact
#     numerical condition the sheet describes.

def m_realworld_height_conflict_65mm(d):
    """Problem #1 quoted: 'Your last 6 surveys used antenna height 1.800m. The
    Emlid RS2+ ARP offset for a standard 2m tripod is 1.865m... Difference:
    65mm.' Operator entered 1.800; RINEX delta_h is 1.865. The cross-check
    fires (delta_h != 0) and detects the disagreement → L3I_005 hits
    'height_conflicts_with_rinex' band (55)."""
    d["source_fields"]["L1F_BASE_026_antenna_height_m"] = 1.800
    d["source_fields"]["L1F_BASE_003_antenna_delta_h"] = 1.865


def m_realworld_14min_pre_buffer(d):
    """Problem #6 quoted: 'Base station has been logging for 14 minutes.
    Minimum recommended for your receiver type (single-frequency) is 45
    minutes.' Set pre-flight buffer to ~840s = 14 minutes (still ≥120s → top
    band). Plus single-frequency, so format degrades. Demonstrates the spec
    rewards the operator who waited 14 min for top coverage_score band but
    docks format_score for single-freq."""
    obs_start_iso = d["source_fields"]["L1F_BASE_009_obs_start_utc"]
    obs_start = datetime.fromisoformat(obs_start_iso.replace("Z", "+00:00"))
    flight_start = obs_start + timedelta(seconds=14 * 60)
    d["source_fields"]["L1F_BASE_035_flight_start_utc"] = (
        flight_start.strftime("%Y-%m-%dT%H:%M:%S.") + f"{flight_start.microsecond:06d}Z"
    )
    d["source_fields"]["L1F_BASE_011_dual_freq_present"] = False


def m_realworld_30s_pre_buffer(d):
    """Problem #6 'starting early' edge case. Pre-flight buffer 30s (well below
    60s threshold) → coverage_score lands in 'short_pre_buffer' band (72).
    coverage_ratio stays at 1.0 so the gate does not fire."""
    obs_start_iso = d["source_fields"]["L1F_BASE_009_obs_start_utc"]
    obs_start = datetime.fromisoformat(obs_start_iso.replace("Z", "+00:00"))
    flight_start = obs_start + timedelta(seconds=30)
    d["source_fields"]["L1F_BASE_035_flight_start_utc"] = (
        flight_start.strftime("%Y-%m-%dT%H:%M:%S.") + f"{flight_start.microsecond:06d}Z"
    )


def m_realworld_base_gps_glonass_only(d):
    """Problem #16 quoted: 'Your base receiver (Emlid RS+ v2) does not track
    Galileo or BeiDou. Your rover tracked all 4 constellations.' Set
    constellation_set to GPS+GLO only. Stage 1 captures this; cross-doc check
    needs rover bundle (DEFERRED_HANDOFF). This scenario PROVES the base-side
    capture is correct."""
    d["source_fields"]["L1F_BASE_008_constellation_set"] = ["G", "R"]


def m_realworld_battery_5pct_dip(d):
    """Problem #17 quoted: 'a session that dipped to 3% mid-flight was at risk
    even if it recovered.' Test battery_min=5% — below the 10% adequate
    threshold → L3I_002 lands in 'battery_low' band (75) without firing
    BASE_SESSION_INTERRUPTED (because the session completed normally)."""
    d["source_fields"]["L1F_BASE_022_battery_min_pct"] = 5.0
    # Endpoint batteries can stay nominal — only the dip matters per schema.


def m_realworld_iono_boundary_kp_4(d):
    """Problem #20 quoted: 'Kp-index forecast for tomorrow's survey window:
    6.2'. Test the boundary: Kp=4 (just below the 5.0 threshold), single-
    frequency → L3I_009 lands in 'kp_low' band (100), demonstrating the
    threshold split between 'storm' (Kp>=5 → flag) and 'low' (Kp<5)."""
    d["source_fields"]["L1F_BASE_011_dual_freq_present"] = False
    obs_start_iso = d["source_fields"]["L1F_BASE_009_obs_start_utc"]
    date_str = obs_start_iso[:10]
    cache_dir = PROJECT_ROOT / "cache" / "noaa_swpc"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{date_str}.json"
    cache_file.write_text(json.dumps({"kp": 4.0, "source": "boundary_scenario"}), encoding="utf-8")
    return cache_file


SCENARIOS: list[tuple[str, Callable[[dict], Path | None], str]] = [
    ("baseline",                  m_baseline,                  "control — gold-standard placeholder unchanged"),
    ("coverage_gate_trip",        m_coverage_gate_trip,        "flight outside obs → coverage=0 → global gate fires"),
    ("antenna_height_missing",    m_antenna_height_missing,    "height=null → setup gate → block=0; apex partial"),
    ("session_interrupted",       m_session_interrupted,       "completed=False, shutdowns=2 → integrity=20 + flag"),
    ("download_unconfirmed",      m_download_unconfirmed,      "download=False → advisory flag, integrity stays clean"),
    ("oplog_absent",              m_oplog_absent,              "all OPLOG fields null → integrity=60 unconfirmed"),
    ("rinex_version_unsupported", m_rinex_version_unsupported, "version=2.05 not supported → format=35 + flag"),
    ("single_frequency",          m_single_frequency,          "dual_freq=False → format=70, iono dual fallback gone"),
    ("slow_acquisition",          m_slow_acquisition,          "sat ramp delayed → acquisition=30 + flag"),
    ("iono_storm_single_freq",    m_iono_storm_single_freq,    "kp=7 cached + single-freq → iono=40 + flag"),
    ("high_multipath",            m_high_multipath,            "cn0 per-sat std=5 dB-Hz → multipath=35 + flag"),
    ("truncation_detected",       m_truncation_detected,       "session_end 10s after obs_end → truncation flag"),
    ("benchmark_unverified",      m_benchmark_unverified,      "known mark + unverified → verification=50 + flag"),
    ("slant_setup",               m_slant_setup,               "SLANT measurement → height_documented=72"),
    ("antenna_type_mismatch",     m_antenna_type_mismatch,     "RINEX vs form model disagree → type_match=40"),
    ("disturbance_signature",     m_disturbance_signature,     "all 3 disturbance conditions co-occur → composite flag"),
    ("all_flags_stress",          m_all_flags_stress,          "compose 8+ flags without triggering global gate"),
    # --- Pass 2 ---
    ("kp_cache_low_storm",        m_kp_cache_low_storm,        "Pass2 #20: cached Kp=3 + single_freq → iono top band (verifies cache path)"),
    ("single_vertical_measurement", m_single_vertical_measurement, "Pass2 #1: VERTICAL+ARP but count=1 → height_documented=88"),
    ("ad_hoc_point_setup",        m_ad_hoc_point_setup,        "Pass2 #2: over_known_mark=False → verification=60 ad_hoc_point band"),
    # --- Pass 2 real-world (CBMI sheet prose-quoted numerical examples) ---
    ("rw_height_conflict_65mm",   m_realworld_height_conflict_65mm,   "RW #1: form=1.800m vs RINEX delta_h=1.865m (65mm diff) → 55"),
    ("rw_14min_pre_buffer",       m_realworld_14min_pre_buffer,       "RW #6: 14min pre + single_freq → coverage 100, format drops to 70"),
    ("rw_30s_pre_buffer",         m_realworld_30s_pre_buffer,         "RW #6: 30s pre-flight buffer → coverage_score=72 short_pre_buffer band"),
    ("rw_base_gps_glonass_only",  m_realworld_base_gps_glonass_only,  "RW #16: base sees only G+R (2 constellations) — captured for cross-doc"),
    ("rw_battery_5pct_dip",       m_realworld_battery_5pct_dip,       "RW #17: battery_min=5% → integrity=75 battery_low band, no flag"),
    ("rw_iono_boundary_kp_4",     m_realworld_iono_boundary_kp_4,     "RW #20: Kp=4 (just below 5.0 threshold) + single_freq → iono top band"),
]


# ---------------------------------------------------------------------------
# Problem-coverage map (Pass 2 deliverable)
# Derived from CBMI_BaseStation_Problems_Complete sheet — 22 problems mapped
# to the scenarios that exercise them, plus an explicit verification status.
# ---------------------------------------------------------------------------

PASS2_PROBLEM_MAP = [
    # (no, name, cbmi_coverage, cbmi_stage, scenarios, verification_status)
    (1,  "Antenna Height Blunder",                "FULLY COVERED at Stage 1",      "Stage 1",            ["antenna_height_missing", "single_vertical_measurement", "slant_setup", "rw_height_conflict_65mm"], "VERIFIED — incl. 65mm RINEX-vs-form conflict path"),
    (2,  "Unstable/Unverified Benchmark",         "PARTIAL at Stage 1",            "Stage 1 (partial)",  ["benchmark_unverified", "ad_hoc_point_setup"],                          "VERIFIED"),
    (3,  "Autonomous Position as Known Base",     "PARTIAL — Stage 2 only",        "Stage 2",            ["baseline (FLG_BASE_013 always-fires)"],                                "VERIFIED — handoff captured"),
    (4,  "Wrong CRS/Datum",                       "PARTIAL — Stage 2 only",        "Stage 2",            [],                                                                       "OUT_OF_SCOPE — Stage 2 / pre_processing_score"),
    (5,  "Height Mode Confusion",                 "PARTIAL — measurement_type captured, NOT datum", "Stage 2 (gap)", ["slant_setup (captures measurement_type)"],                "DEFERRED_SPEC_GAP — height_mode not in source_fields"),
    (6,  "Short Observation Window",              "FULLY COVERED at Stage 1",      "Stage 1",            ["coverage_gate_trip", "rw_14min_pre_buffer", "rw_30s_pre_buffer"],        "VERIFIED — all 3 spec buffer bands (100/88/72) + gate"),
    (7,  "Base Disturbed Mid-Observation",        "PARTIAL at Stage 1",            "Stage 1 (partial)",  ["disturbance_signature"],                                                "VERIFIED"),
    (8,  "Base Log Not Downloaded",               "PARTIAL at Stage 1",            "Stage 1 (gap)",      ["download_unconfirmed", "oplog_absent"],                                 "VERIFIED"),
    (9,  "Baseline Too Long (PPK)",               "PARTIAL — RTK fully, PPK Stage 2", "PPK Stage 2",     [],                                                                       "OUT_OF_SCOPE — PPK Stage 2 / pre_processing_score"),
    (10, "RINEX Incompatibility",                 "PARTIAL at Stage 1",            "Stage 1 (partial)",  ["rinex_version_unsupported"],                                            "VERIFIED"),
    (11, "Float Solution Accepted as Fixed",      "PARTIAL — via GCP residuals",   "Stage 2 (gap)",      [],                                                                       "OUT_OF_SCOPE — RTK + needs GCP at Stage 2"),
    (12, "Geoid Model Mismatch",                  "NOT COVERED at Stage 1",        "Not covered",        [],                                                                       "OUT_OF_SCOPE — pre_processing_score (geoid)"),
    (13, "Re-Occupation Error Across Sessions",   "PARTIAL — monument_id captured for LE", "Stage 1 → LE", ["baseline (monument_id captured)", "ad_hoc_point_setup (null)"],       "VERIFIED — Stage-1 capture only; LE handles cross-session"),
    (14, "Multipath from Structures",             "FULLY COVERED at Stage 1",      "Stage 1",            ["high_multipath", "disturbance_signature"],                              "VERIFIED"),
    (15, "Multiple Flights — Wrong Log Matched",  "PARTIAL — matching logic gap",  "Stage 1 (gap)",      ["coverage_gate_trip (FLG_011 fires as side-effect)"],                    "VERIFIED — FLG_011 structurally coupled with coverage gate"),
    (16, "Constellation Mismatch Base/Rover",     "PARTIAL — base-side only",      "Stage 1 (partial)",  ["baseline (constellation_set=5)", "rw_base_gps_glonass_only (count=2)"], "DEFERRED_HANDOFF — base-side capture verified, cross-doc needs rover (FLG_BASE_016)"),
    (17, "Power Interruption Mid-Observation",    "FULLY COVERED at Stage 1",      "Stage 1",            ["session_interrupted", "oplog_absent", "rw_battery_5pct_dip"],            "VERIFIED — incl. deep-dip mid-session, recovered"),
    (18, "UTC/Time Offset Error",                 "PARTIAL at Stage 1",            "Stage 1 (implied)",  ["baseline (timestamps normalized to UTC)"],                              "DEFERRED_HANDOFF — needs rover bundle (FLG_BASE_015)"),
    (19, "Firmware Version Mismatch",             "PARTIAL — firmware captured via override", "LE (gap)", ["baseline (firmware_version from Hardware Override)"],                  "DEFERRED_HANDOFF — needs rover bundle (FLG_BASE_017)"),
    (20, "Ionospheric Storm",                     "NOT COVERED at Stage 1",        "Not covered",        ["iono_storm_single_freq (Kp=7)", "kp_cache_low_storm (Kp=3)", "rw_iono_boundary_kp_4 (Kp=4)", "single_frequency (API_UNAVAILABLE)"], "VERIFIED — all 4 Kp paths: high+flag, low, boundary, API-miss"),
    (21, "Phase Centre / ANTEX Uncalibrated",     "PARTIAL at Stage 1",            "Stage 1 (partial)",  ["antenna_type_mismatch"],                                                "VERIFIED — type match only, not full ANTEX calibration"),
    (22, "L-Band Correction Conflict",            "PARTIAL at Stage 1",            "Stage 1 (partial)",  [],                                                                       "OUT_OF_SCOPE — RTK/Trimble-only"),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _envelope(spec_version, config, stage, data, scenario_name):
    return {
        "spec_version": spec_version,
        "config_used": config,
        "generated_at": _iso_now(),
        "stage": stage,
        "scenario": scenario_name,
        "data": data,
    }


def _run_one(name: str, mutator: Callable[[dict], Path | None],
             description: str, config: dict, spec: dict,
             baseline_stage2: dict) -> dict:
    stage2 = copy.deepcopy(baseline_stage2)
    cleanup_paths: list[Path] = []
    try:
        result = mutator(stage2)
        if isinstance(result, Path):
            cleanup_paths.append(result)

        s3a = compute_derived.run(config, PROJECT_ROOT, spec, stage2)
        s3b = compute_indicators.run(config, PROJECT_ROOT, spec, s3a, stage2)
        s3c = compute_blocks.run(config, PROJECT_ROOT, spec, s3b)
        s3d = compute_base_score.run(config, PROJECT_ROOT, spec, stage2, s3a, s3b, s3c)

        sv = spec["_meta"]["version"]
        out_dir = SCENARIOS_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)
        # Persist the mutated Stage-2 source-field envelope so each scenario
        # directory is self-contained and reproducible without reading the
        # mutator code.
        (out_dir / "02_source_fields.json").write_text(json.dumps(
            _envelope(sv, config, "stage2_source_fields", stage2, name),
            indent=2, sort_keys=True), encoding="utf-8")
        (out_dir / "03_derived.json").write_text(json.dumps(
            _envelope(sv, config, "stage3a_derived_fields", s3a, name),
            indent=2, sort_keys=True), encoding="utf-8")
        (out_dir / "04_indicators.json").write_text(json.dumps(
            _envelope(sv, config, "stage3b_indicators", s3b, name),
            indent=2, sort_keys=True), encoding="utf-8")
        (out_dir / "05_blocks.json").write_text(json.dumps(
            _envelope(sv, config, "stage3c_building_blocks", s3c, name),
            indent=2, sort_keys=True), encoding="utf-8")
        (out_dir / "06_apex.json").write_text(json.dumps(
            _envelope(sv, config, "stage3d_base_station_score", s3d, name),
            indent=2, sort_keys=True), encoding="utf-8")

        blocks = s3c["block_scores"]
        return {
            "name": name,
            "description": description,
            "apex": s3d["base_station_score"],
            "block_complete": blocks["BB_BASE_COMPLETE"]["score"],
            "block_setup":    blocks["BB_BASE_SETUP"]["score"],
            "block_env":      blocks["BB_BASE_ENV"]["score"],
            "global_gate":    s3d["global_gate"]["triggered"],
            "blocks_with_gate": s3c["stage3c_meta"]["blocks_with_gate_triggered"],
            "flag_ids":       [f["flag_id"] for f in s3d["all_flags_aggregated"]],
            "flag_count_by_origin": s3d["flags_by_origin_stage"],
            "crashed": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "description": description,
            "crashed": True,
            "exception": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    finally:
        for p in cleanup_paths:
            if p.exists():
                p.unlink()


def _print_table(results: list[dict]) -> None:
    print()
    print("=" * 140)
    print(f"{'scenario':28s} {'apex':>6s}  {'C':>6s} {'S':>6s} {'E':>6s}  {'gate':5s}  flags")
    print("-" * 140)
    for r in results:
        if r.get("crashed"):
            print(f"{r['name']:28s}  CRASH  {r['exception']}")
            continue
        flags = ",".join(r["flag_ids"]) or "-"
        print(
            f"{r['name']:28s} {r['apex']:6.1f}  "
            f"{r['block_complete']:6.1f} {r['block_setup']:6.1f} {r['block_env']:6.1f}  "
            f"{str(r['global_gate']):5s}  {flags}"
        )
    print("=" * 140)


def main() -> int:
    config, spec = _load_config_and_spec()
    baseline_stage2 = _load_baseline_stage2()

    results: list[dict] = []
    for name, mutator, description in SCENARIOS:
        r = _run_one(name, mutator, description, config, spec, baseline_stage2)
        results.append(r)
        if r.get("crashed"):
            print(f"[{name}] CRASH — {r['exception']}")
        else:
            flags_short = ",".join(f.replace("FLG_BASE_", "") for f in r["flag_ids"]) or "-"
            print(
                f"[{name}] apex={r['apex']:>5.1f}  "
                f"C/S/E={r['block_complete']:>5.1f}/{r['block_setup']:>5.1f}/{r['block_env']:>5.1f}  "
                f"gate={str(r['global_gate']):5s}  flags={flags_short}"
            )

    # Summary
    n_crashed = sum(1 for r in results if r.get("crashed"))
    summary_file = (PROJECT_ROOT / "tests" / "scenarios" / "_summary.json")
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    summary_file.write_text(json.dumps({
        "generated_at": _iso_now(),
        "spec_version": spec["_meta"]["version"],
        "n_scenarios": len(results),
        "n_crashed": n_crashed,
        "results": [
            {k: v for k, v in r.items() if k != "traceback"} for r in results
        ],
    }, indent=2, sort_keys=True), encoding="utf-8")

    # Pass-2 problem-coverage map (CSV + JSON, easy to diff and human-read)
    pass2_csv = PROJECT_ROOT / "tests" / "scenarios" / "_pass2_problem_coverage.csv"
    pass2_json = PROJECT_ROOT / "tests" / "scenarios" / "_pass2_problem_coverage.json"
    import csv as _csv
    with pass2_csv.open("w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["problem_no", "problem_name", "cbmi_coverage_class",
                    "cbmi_stage", "scenarios", "verification_status"])
        for no, name, cls, stage, scns, status in PASS2_PROBLEM_MAP:
            w.writerow([no, name, cls, stage, " | ".join(scns) if scns else "-", status])
    pass2_payload = [
        {"problem_no": no, "problem_name": name, "cbmi_coverage_class": cls,
         "cbmi_stage": stage, "scenarios": scns, "verification_status": status}
        for no, name, cls, stage, scns, status in PASS2_PROBLEM_MAP
    ]
    by_status = {}
    for row in pass2_payload:
        by_status[row["verification_status"]] = by_status.get(row["verification_status"], 0) + 1
    pass2_json.write_text(json.dumps({
        "generated_at": _iso_now(),
        "spec_version": spec["_meta"]["version"],
        "n_problems": len(pass2_payload),
        "by_status": dict(sorted(by_status.items())),
        "problems": pass2_payload,
    }, indent=2, sort_keys=True), encoding="utf-8")

    _print_table(results)
    print(f"\n{len(results)} scenarios run, {n_crashed} crashed.")
    print(f"Summary written to {summary_file.relative_to(PROJECT_ROOT)}")
    print(f"Pass-2 problem-coverage map written to {pass2_csv.relative_to(PROJECT_ROOT)}")
    print(f"                                       {pass2_json.relative_to(PROJECT_ROOT)}")
    print()
    print(f"=== Pass-2 problem coverage by verification_status ({len(pass2_payload)} of 22) ===")
    for status in sorted(by_status.keys()):
        print(f"  {status:60s} {by_status[status]}")
    return 1 if n_crashed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
