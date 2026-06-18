#!/usr/bin/env python3
"""Step 12 - smoke-test harness for gcp_score (GCP PPK).

Reads the baseline outputs/02_source_fields.json, applies a per-scenario
mutator to a DEEP-COPY of that stage-2 data, then re-runs Stages 3a -> 3d into
tests/scenarios/<name>/{02_source_fields,03_derived,04_indicators,05_blocks,
06_gcp_score}.json. A side-by-side table is printed at the end (apex / the
three block aggregates / global gate / flags), plus per-scenario PASS/FAIL
against an expected spec outcome.

GCP is MULTI-POINT: the baseline carries 3 GCP-role points. Mutators operate on
the survey dict (`d["points"][i]`). Most scenarios start from a freshly cleaned
survey (`_clean_survey` -> every point gold-standard, apex=100) and introduce a
single defect on ONE point, so the asserted flag is isolated. Cross-point
aggregation is `mean - k*(100-min)` (k=0.25); the apex is
`0.45*COMPLETE + 0.35*SETUP + 0.20*ENV`.

Determinism: every artifact this harness writes (scenario envelopes, _summary,
_pass2_*) OMITS any wall-clock timestamp, so repeated runs are byte-identical
and diffable. The NOAA-SWPC Kp cache is redirected to a harness-local dir
(tests/scenarios/_kp_cache) via a deep-copied config so the real cache/ tree is
never touched; injected Kp files are cleaned up per scenario.

Pass 1 - spec-internal coverage: baseline control, the all-clean gold survey,
each internal/global gate, every threshold/composite flag (FLG_GCP_001..014),
and the no-flag enum-band drops.
Pass 2 - the 22-row CBMI GCP Problems map (problem -> scenarios -> verification
status), written to _pass2_problem_coverage.{csv,json}.
"""
from __future__ import annotations

import copy
import csv as _csv
import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

# Make the scripts/ package importable when invoked directly.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import stage3a_derived       # noqa: E402
import stage3b_indicators    # noqa: E402
import stage3c_blocks        # noqa: E402
import stage3d_score         # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "paths.json"
BASELINE_STAGE2_PATH = PROJECT_ROOT / "outputs" / "02_source_fields.json"
SCENARIOS_DIR = PROJECT_ROOT / "tests" / "scenarios"

# Harness-local Kp cache (isolated from the real cache/noaa_swpc tree).
_KP_CACHE_REL = "tests/scenarios/_kp_cache"
_KP_CACHE_DIR = PROJECT_ROOT / _KP_CACHE_REL

# Acquisition gate constants (mirror stage3a; only used to BUILD test fixtures).
_ACQ_NSAT = 6

# ---- source-field key constants -------------------------------------------
OBS_START = "L1F_GCP_009_obs_start_utc"
OBS_END = "L1F_GCP_010_obs_end_utc"
FLIGHT_START = "L1F_GCP_039_flight_start_utc"
FLIGHT_END = "L1F_GCP_040_flight_end_utc"
DUAL_FREQ = "L1F_GCP_011_dual_freq_present"
RINEX_VERSION = "L1F_GCP_007_rinex_version"
ANTENNA_TYPE = "L1F_GCP_002_antenna_type"
ANTENNA_MODEL = "L1F_GCP_029_antenna_model"
CYCLE_SLIPS = "L1F_GCP_016_cycle_slip_markers"
CN0_PER_SAT = "L1F_GCP_015_cn0_per_sat"
SAT_COUNT = "L1F_GCP_018_sat_count_per_epoch"
DEVICE_TYPE = "L1F_GCP_026_device_type"
DEVICE_ROLE = "L1F_GCP_028_device_role"
DEVICE_ID_FORM = "L1F_GCP_027_device_id"
DEVICE_ID_RINEX = "L1F_GCP_012_device_id"
SESSION_COMPLETED = "L1F_GCP_019_session_completed_normally"
SHUTDOWN_COUNT = "L1F_GCP_020_unexpected_shutdown_count"
BATTERY_START = "L1F_GCP_021_battery_start_pct"
BATTERY_END = "L1F_GCP_022_battery_end_pct"
BATTERY_MIN = "L1F_GCP_023_battery_min_pct"
SESSION_END = "L1F_GCP_024_session_end_utc"
LOG_DOWNLOAD = "L1F_GCP_025_raw_log_download_confirmed"
ANTENNA_HEIGHT_M = "L1F_GCP_030_antenna_height_m"
ANTENNA_DELTA_H = "L1F_GCP_003_antenna_delta_h"
MEAS_TYPE = "L1F_GCP_032_antenna_measurement_type"
MEAS_TO_REF = "L1F_GCP_033_measured_to_reference"
HEIGHT_COUNT = "L1F_GCP_034_height_measured_count"


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
# Time / structure helpers
# ---------------------------------------------------------------------------

def _parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


def _rinex_stats(p: dict) -> dict:
    return p["per_source_parser_meta"]["SRC_GCP_RINEX"]["stream_stats_for_derived_fields"]


def _set_multipath(p: dict, std: float) -> None:
    cn0 = p["source_fields"].get(CN0_PER_SAT) or {}
    for stats in (cn0.get("per_sat") or {}).values():
        if isinstance(stats, dict):
            stats["std_dbhz"] = std
            for band in (stats.get("per_band") or {}).values():
                if isinstance(band, dict):
                    band["std_dbhz"] = std


def _set_acquisition_fast(p: dict) -> None:
    """nsat>=6 from t=0, held -> acquisition_time=0 -> L3I_009=100."""
    p["source_fields"][SAT_COUNT]["acquisition_samples"] = [[float(i), 9] for i in range(15)]


def _set_acquisition_slow(p: dict) -> None:
    """nsat<6 until t=310s, then held -> acquisition_time=310 (>=300) -> FLG_GCP_009."""
    samples = [[float(i), 4] for i in range(310)]
    samples += [[float(310 + i), 9] for i in range(15)]
    p["source_fields"][SAT_COUNT]["acquisition_samples"] = samples


def _trip_coverage(p: dict) -> None:
    """Flight window entirely outside the obs window -> coverage_ratio=0 -> gate."""
    sf = p["source_fields"]
    sf[FLIGHT_START] = "2020-01-01T00:00:00.000000Z"
    sf[FLIGHT_END] = "2020-01-01T00:01:00.000000Z"


def _clean_point(p: dict) -> None:
    """Make one point gold-standard: every L3I indicator -> 100.

    Flight window = middle 50% of the obs window so coverage_ratio=1.0 and the
    pre/post buffers clear the L3I_001 top band (>=120s pre, >=60s post). All
    baseline obs windows are >=1155s, so 0.25*dur >= 288s clears both."""
    sf = p["source_fields"]
    obs_s, obs_e = _parse_iso(sf[OBS_START]), _parse_iso(sf[OBS_END])
    dur = (obs_e - obs_s).total_seconds()
    sf[FLIGHT_START] = _fmt_iso(obs_s + timedelta(seconds=dur * 0.25))
    sf[FLIGHT_END] = _fmt_iso(obs_s + timedelta(seconds=dur * 0.75))
    sf[DUAL_FREQ] = True
    sf[RINEX_VERSION] = "3.03"
    sf[ANTENNA_MODEL] = sf[ANTENNA_TYPE]          # antenna type match
    sf[DEVICE_ID_FORM] = sf[DEVICE_ID_RINEX]      # device-id match
    st = _rinex_stats(p)
    st["count_gap_gt_5s"] = 0
    st["count_gap_gt_60s"] = 0
    if isinstance(sf.get(CYCLE_SLIPS), dict):
        sf[CYCLE_SLIPS]["total_count"] = 0
    _set_multipath(p, 1.0)
    _set_acquisition_fast(p)
    # device_type stays CB_X -> antenna height auto-known -> L3I_005=100.


def _clean_survey(d: dict) -> None:
    for p in d["points"]:
        _clean_point(p)


def _to_dgps(p: dict) -> None:
    """Convert a (cleaned) point to a clean DGPS occupation - no flags raised.

    DGPS expects an oplog (completed/shutdowns/battery/download) and a
    non-auto-known antenna height that must be documented VERTICAL/ARP with
    >=3 measurements; session_end must match obs_end (no truncation)."""
    sf = p["source_fields"]
    sf[DEVICE_TYPE] = "DGPS"
    p["device_type"] = "DGPS"
    sf[SESSION_COMPLETED] = True
    sf[SHUTDOWN_COUNT] = 0
    sf[BATTERY_START] = 100.0
    sf[BATTERY_END] = 90.0
    sf[BATTERY_MIN] = 85.0
    sf[LOG_DOWNLOAD] = True
    sf[SESSION_END] = sf[OBS_END]
    sf[ANTENNA_HEIGHT_M] = 2.0
    sf[MEAS_TYPE] = "VERTICAL"
    sf[MEAS_TO_REF] = "ARP"
    sf[HEIGHT_COUNT] = 3
    sf[ANTENNA_DELTA_H] = 0.0      # delta_h~0 -> RINEX agreement skipped (None)


def _inject_kp(d: dict, kp: float) -> list[Path]:
    """Write a Kp value to the harness-local cache for each distinct obs date."""
    paths: list[Path] = []
    seen: set[str] = set()
    _KP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for p in d["points"]:
        iso = p["source_fields"].get(OBS_START)
        if not iso:
            continue
        date_str = iso[:10]
        if date_str in seen:
            continue
        seen.add(date_str)
        fp = _KP_CACHE_DIR / f"{date_str}.json"
        fp.write_text(json.dumps({"kp": kp, "source": "scenario_injection"}),
                      encoding="utf-8")
        paths.append(fp)
    return paths


# ---------------------------------------------------------------------------
# Mutators - each receives the stage-2 data dict; may return Path(s) to clean up
# ---------------------------------------------------------------------------

def m_baseline(d):
    """Raw fixture: P1 ungated, P2/P3 coverage-gated (flight before obs)."""
    pass


def m_all_clean(d):
    """Gold survey: all 3 points perfect -> every block 100 -> apex 100."""
    _clean_survey(d)


def m_global_gate_all_gated(d):
    """Every GCP point coverage-gated -> global gate -> apex 0 + FLG_GCP_001."""
    _clean_survey(d)
    for p in d["points"]:
        _trip_coverage(p)


def m_coverage_gate_one_point(d):
    """One point's flight outside obs -> FLG_GCP_003; others fine -> no global gate."""
    _clean_survey(d)
    _trip_coverage(d["points"][0])


def m_antenna_height_missing(d):
    """DGPS point with height=null (auto_known=False) -> SETUP gate + FLG_GCP_002."""
    _clean_survey(d)
    p = d["points"][0]
    _to_dgps(p)
    p["source_fields"][ANTENNA_HEIGHT_M] = None


def m_dgps_device_failure(d):
    """DGPS shutdown mid-session -> integrity=30 + FLG_GCP_004."""
    _clean_survey(d)
    p = d["points"][0]
    _to_dgps(p)
    p["source_fields"][SHUTDOWN_COUNT] = 2
    p["source_fields"][SESSION_COMPLETED] = False


def m_dgps_download_unconfirmed(d):
    """DGPS log download not confirmed -> advisory FLG_GCP_005 (integrity stays 100)."""
    _clean_survey(d)
    p = d["points"][0]
    _to_dgps(p)
    p["source_fields"][LOG_DOWNLOAD] = False


def m_rinex_version_unsupported(d):
    """RINEX version 2.05 not in supported set -> format=35 + FLG_GCP_006."""
    _clean_survey(d)
    d["points"][0]["source_fields"][RINEX_VERSION] = "2.05"


def m_high_multipath(d):
    """Per-sat C/N0 std=5 dB-Hz -> multipath=35 + FLG_GCP_007."""
    _clean_survey(d)
    _set_multipath(d["points"][0], 5.0)


def m_iono_storm_single_freq(d):
    """Single-frequency + cached Kp=7 -> iono=40 + FLG_GCP_008."""
    _clean_survey(d)
    d["points"][0]["source_fields"][DUAL_FREQ] = False
    return _inject_kp(d, 7.0)


def m_slow_acquisition(d):
    """Sat ramp delayed past 300s -> acquisition=30 + FLG_GCP_009."""
    _clean_survey(d)
    _set_acquisition_slow(d["points"][0])


def m_device_id_mismatch(d):
    """Form device_id != RINEX device_id -> L3I_006=50 + FLG_GCP_010."""
    _clean_survey(d)
    d["points"][0]["source_fields"][DEVICE_ID_FORM] = "FORM-MISMATCH-99"


def m_disturbance_signature(d):
    """gap_gt_5s>5 AND slips/hr>50 AND cn0_std>3 co-occur -> composite FLG_GCP_011.

    cn0_std=3.5 satisfies the disturbance threshold (>3.0) while staying in the
    'moderate' multipath band (<=4.0) so FLG_GCP_007 does NOT co-fire."""
    _clean_survey(d)
    p = d["points"][0]
    _rinex_stats(p)["count_gap_gt_5s"] = 20
    p["source_fields"][CYCLE_SLIPS]["total_count"] = 5000
    _set_multipath(p, 3.5)


def m_no_gcp_points(d):
    """No point has device_role=GCP -> apex=null + FLG_GCP_012.

    Clean the survey first so no incidental per-point coverage gate (FLG_GCP_003)
    fires - this isolates the null-handler flag."""
    _clean_survey(d)
    for p in d["points"]:
        p["device_role"] = "CHECK_POINT"
        p["source_fields"][DEVICE_ROLE] = "CHECK_POINT"


def m_unrecognized_device_type(d):
    """device_type=OTHER -> standalone FLG_GCP_013."""
    _clean_survey(d)
    p = d["points"][0]
    p["source_fields"][DEVICE_TYPE] = "OTHER"
    p["device_type"] = "OTHER"


def m_rinex_truncated(d):
    """DGPS session_end 30s past obs_end -> composite FLG_GCP_014 (truncation)."""
    _clean_survey(d)
    p = d["points"][0]
    _to_dgps(p)
    sf = p["source_fields"]
    sf[SESSION_END] = _fmt_iso(_parse_iso(sf[OBS_END]) + timedelta(seconds=30))


# --- no-flag enum-band drops (prove the band ladder without raising a flag) ---

def m_single_frequency(d):
    """Single-frequency, no Kp cached -> format=70, iono=70 (API miss). No flag."""
    _clean_survey(d)
    d["points"][0]["source_fields"][DUAL_FREQ] = False


def m_slant_setup(d):
    """DGPS SLANT antenna measurement -> L3I_005=72. No flag."""
    _clean_survey(d)
    p = d["points"][0]
    _to_dgps(p)
    p["source_fields"][MEAS_TYPE] = "SLANT"


def m_single_vertical_count(d):
    """DGPS VERTICAL but a single measurement (count<3) -> L3I_005=88. No flag."""
    _clean_survey(d)
    p = d["points"][0]
    _to_dgps(p)
    p["source_fields"][HEIGHT_COUNT] = 1


def m_antenna_type_mismatch(d):
    """Form antenna_model != RINEX antenna_type -> L3I_007=40 (no spec flag defined)."""
    _clean_survey(d)
    d["points"][0]["source_fields"][ANTENNA_TYPE] = "LEIAR25.R3      LEIT"


def m_all_flags_stress(d):
    """Stack 11 flags on ONE DGPS point without tripping the global gate
    (points 2 & 3 stay clean so not every point is coverage-gated)."""
    _clean_survey(d)
    p = d["points"][0]
    sf = p["source_fields"]
    _to_dgps(p)
    _trip_coverage(p)                               # FLG_GCP_003
    sf[ANTENNA_HEIGHT_M] = None                     # FLG_GCP_002
    sf[SHUTDOWN_COUNT] = 2                           # FLG_GCP_004
    sf[SESSION_COMPLETED] = False
    sf[LOG_DOWNLOAD] = False                         # FLG_GCP_005
    sf[RINEX_VERSION] = "2.05"                       # FLG_GCP_006
    _set_multipath(p, 5.0)                           # FLG_GCP_007
    sf[DUAL_FREQ] = False                            # FLG_GCP_008 (with Kp=7)
    _set_acquisition_slow(p)                         # FLG_GCP_009
    sf[DEVICE_ID_FORM] = "STRESS-MISMATCH"           # FLG_GCP_010
    _rinex_stats(p)["count_gap_gt_5s"] = 20          # FLG_GCP_011 (with slips + cn0)
    sf[CYCLE_SLIPS]["total_count"] = 5000
    sf[SESSION_END] = _fmt_iso(_parse_iso(sf[OBS_END]) + timedelta(seconds=30))  # FLG_GCP_014
    return _inject_kp(d, 7.0)


# (name, mutator, description, expect)
#   expect: gcp_score   float -> assert within 0.05 | "null" -> assert None | None -> report-only
#           global_gate bool|None ; exact_flags list|None ; flags_present/absent list
SCENARIOS: list[tuple[str, Callable, str, dict]] = [
    ("baseline", m_baseline,
     "control - raw fixture (P1 ok, P2/P3 coverage-gated)",
     {"gcp_score": 50.6, "global_gate": False,
      "flags_present": ["FLG_GCP_003"], "flags_absent": ["FLG_GCP_001", "FLG_GCP_012"]}),
    ("all_clean", m_all_clean,
     "gold survey - every point perfect -> apex 100",
     {"gcp_score": 100.0, "global_gate": False, "exact_flags": []}),
    ("global_gate_all_gated", m_global_gate_all_gated,
     "every GCP point coverage-gated -> apex 0 + FLG_GCP_001",
     {"gcp_score": 0.0, "global_gate": True,
      "flags_present": ["FLG_GCP_001", "FLG_GCP_003"]}),
    ("coverage_gate_one_point", m_coverage_gate_one_point,
     "one point flight outside obs -> FLG_GCP_003, no global gate",
     {"global_gate": False,
      "flags_present": ["FLG_GCP_003"], "flags_absent": ["FLG_GCP_001"]}),
    ("antenna_height_missing", m_antenna_height_missing,
     "DGPS height=null -> SETUP gate + FLG_GCP_002",
     {"global_gate": False, "flags_present": ["FLG_GCP_002"]}),
    ("dgps_device_failure", m_dgps_device_failure,
     "DGPS shutdown+abnormal end -> integrity=30 + FLG_GCP_004",
     {"global_gate": False,
      "flags_present": ["FLG_GCP_004"], "flags_absent": ["FLG_GCP_005"]}),
    ("dgps_download_unconfirmed", m_dgps_download_unconfirmed,
     "DGPS download unconfirmed -> advisory FLG_GCP_005",
     {"global_gate": False,
      "flags_present": ["FLG_GCP_005"], "flags_absent": ["FLG_GCP_004"]}),
    ("rinex_version_unsupported", m_rinex_version_unsupported,
     "RINEX 2.05 unsupported -> format=35 + FLG_GCP_006",
     {"global_gate": False, "flags_present": ["FLG_GCP_006"]}),
    ("high_multipath", m_high_multipath,
     "per-sat C/N0 std=5 -> multipath=35 + FLG_GCP_007",
     {"global_gate": False, "flags_present": ["FLG_GCP_007"]}),
    ("iono_storm_single_freq", m_iono_storm_single_freq,
     "single-freq + cached Kp=7 -> iono=40 + FLG_GCP_008",
     {"global_gate": False, "flags_present": ["FLG_GCP_008"]}),
    ("slow_acquisition", m_slow_acquisition,
     "sat ramp delayed >300s -> acquisition=30 + FLG_GCP_009",
     {"global_gate": False, "flags_present": ["FLG_GCP_009"]}),
    ("device_id_mismatch", m_device_id_mismatch,
     "form != RINEX device_id -> L3I_006=50 + FLG_GCP_010",
     {"global_gate": False, "flags_present": ["FLG_GCP_010"]}),
    ("disturbance_signature", m_disturbance_signature,
     "gap+slips+cn0 all elevated -> composite FLG_GCP_011 (isolated)",
     {"global_gate": False, "exact_flags": ["FLG_GCP_011"]}),
    ("no_gcp_points", m_no_gcp_points,
     "no device_role=GCP -> apex=null + FLG_GCP_012 (isolated)",
     {"gcp_score": "null", "global_gate": False,
      "exact_flags": ["FLG_GCP_012"]}),
    ("unrecognized_device_type", m_unrecognized_device_type,
     "device_type=OTHER -> standalone FLG_GCP_013",
     {"global_gate": False, "flags_present": ["FLG_GCP_013"]}),
    ("rinex_truncated", m_rinex_truncated,
     "DGPS session_end 30s past obs_end -> composite FLG_GCP_014",
     {"global_gate": False, "flags_present": ["FLG_GCP_014"]}),
    # --- no-flag enum-band drops ---
    ("single_frequency", m_single_frequency,
     "single-freq, no Kp -> format/iono band drop, no flag",
     {"global_gate": False, "exact_flags": []}),
    ("slant_setup", m_slant_setup,
     "DGPS SLANT -> L3I_005=72, no flag",
     {"global_gate": False, "exact_flags": []}),
    ("single_vertical_count", m_single_vertical_count,
     "DGPS VERTICAL count<3 -> L3I_005=88, no flag",
     {"global_gate": False, "exact_flags": []}),
    ("antenna_type_mismatch", m_antenna_type_mismatch,
     "form model != RINEX type -> L3I_007=40, no spec flag",
     {"global_gate": False, "exact_flags": []}),
    # --- stress ---
    ("all_flags_stress", m_all_flags_stress,
     "11 flags on one DGPS point, no global gate",
     {"global_gate": False,
      "flags_present": ["FLG_GCP_002", "FLG_GCP_003", "FLG_GCP_004", "FLG_GCP_005",
                        "FLG_GCP_006", "FLG_GCP_007", "FLG_GCP_008", "FLG_GCP_009",
                        "FLG_GCP_010", "FLG_GCP_011", "FLG_GCP_014"],
      "flags_absent": ["FLG_GCP_001", "FLG_GCP_012", "FLG_GCP_013"]}),
]


# ---------------------------------------------------------------------------
# Pass 2 - CBMI GCP Problems map (22 rows). Disposition tags from the spec
# problem_coverage_map: OWNED/SPLIT rows are exercised by scenarios; PRE-PROC,
# LE, STAGE2, OUT, OUT-FUTURE rows are handled by other subsystems.
# (no, name, disposition, covered_by, scenarios, verification_status)
# ---------------------------------------------------------------------------
PASS2_PROBLEM_MAP = [
    (1, "No Check Points", "OUT-FUTURE",
     "future check_point_score; survey-design", [],
     "OUT_OF_SCOPE - future check_point_score"),
    (2, "CP Not Spatially Independent", "PRE-PROC",
     "pre_processing_score", [],
     "OUT_OF_SCOPE - pre_processing_score"),
    (3, "Insufficient GCP Count", "PRE-PROC",
     "pre_processing_score (count gate, needs site area)", [],
     "OUT_OF_SCOPE - pre_processing_score"),
    (4, "Clustered Near Access Roads", "PRE-PROC",
     "pre_processing_score (needs reconstruction extent)", [],
     "OUT_OF_SCOPE - pre_processing_score"),
    (5, "Vertical Extremes Uncovered", "LE",
     "Learning Engine (needs DTM)", [],
     "OUT_OF_SCOPE - Learning Engine"),
    (6, "Antenna Height Blunder (Pole-Mounted)", "OWNED",
     "gcp_antenna_height_documented_score + hard gate",
     ["antenna_height_missing", "slant_setup", "single_vertical_count"],
     "VERIFIED - gate (FLG_GCP_002) + bands 72/88"),
    (7, "Coordinate Accuracy Unknown", "PRE-PROC",
     "pre_processing_score (Stage 2)", [],
     "OUT_OF_SCOPE - pre_processing_score"),
    (8, "Battery/Solar Failure Mid-Session", "OWNED",
     "occupation_completeness + integrity per-point",
     ["dgps_device_failure", "dgps_download_unconfirmed"],
     "VERIFIED - FLG_GCP_004/005 (DGPS)"),
    (9, "Multipath from Structures", "OWNED",
     "gcp_multipath_score per point",
     ["high_multipath", "disturbance_signature"],
     "VERIFIED - FLG_GCP_007"),
    (10, "Mark Disturbed Pre-Flight", "SPLIT",
     "CB_X/AeroPoint via RINEX disturbance composite; traditional marks -> Stage 2 ODM",
     ["disturbance_signature"],
     "VERIFIED - CB_X/AeroPoint RINEX path (FLG_GCP_011); traditional -> Stage 2"),
    (11, "Target Not Visible at Flight Altitude", "PRE-PROC",
     "pre_processing_score (cross-doc with planned_gsd_cm)", [],
     "OUT_OF_SCOPE - pre_processing_score"),
    (12, "Wrong Coord Typo in ODM", "OUT",
     "Outside CBMI (manual ODM entry)", [],
     "OUT_OF_SCOPE - outside CBMI"),
    (13, "Pre-Surveyed Coords Wrong CRS", "PRE-PROC",
     "pre_processing_score (datum)", [],
     "OUT_OF_SCOPE - pre_processing_score"),
    (14, "Device_ID Mismatch", "OWNED",
     "gcp_device_id_match_score (reviewer-blocking)",
     ["device_id_mismatch"],
     "VERIFIED - FLG_GCP_010"),
    (15, "AeroPoint/CB_X Moved During Flight", "OWNED",
     "GCP_POINT_DISTURBANCE composite (flag-only)",
     ["disturbance_signature"],
     "VERIFIED - FLG_GCP_011"),
    (16, "Uneven Surface Tilt", "OUT",
     "Hardware limitation (no inclinometer)", [],
     "OUT_OF_SCOPE - hardware limitation"),
    (17, "Target Washed/Blown Away", "STAGE2",
     "Stage 2 ODM target detection", [],
     "OUT_OF_SCOPE - Stage 2 ODM"),
    (18, "Recording Starts After Takeoff", "OWNED",
     "gcp_acquisition_score",
     ["slow_acquisition"],
     "VERIFIED - FLG_GCP_009"),
    (19, "Processing Method Too Weak", "PRE-PROC",
     "pre_processing_score (Stage 2)", [],
     "OUT_OF_SCOPE - pre_processing_score"),
    (20, "Too Many Check Points, Few GCPs", "OUT-FUTURE",
     "future check_point_score (and pre_processing role-allocation)", [],
     "OUT_OF_SCOPE - future check_point_score"),
    (21, "Roles Reversed in ODM", "OUT",
     "Outside CBMI (third-party software input)", [],
     "OUT_OF_SCOPE - outside CBMI"),
    (22, "Drift Despite Good GCPs", "LE",
     "Learning Engine (consequence of #5)", [],
     "OUT_OF_SCOPE - Learning Engine"),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _fmtnum(x) -> str:
    return "null" if x is None else f"{x:.1f}"


def _envelope(spec_version, config, stage, data, scenario_name):
    # NOTE: no `generated_at` -> byte-stable, diffable artifacts.
    return {
        "spec_version": spec_version,
        "config_used": config,
        "stage": stage,
        "scenario": scenario_name,
        "data": data,
    }


def _check_expect(expect: dict, result: dict) -> tuple[bool, list[str]]:
    fails: list[str] = []
    flag_ids = result["flag_ids"]

    want_score = expect.get("gcp_score")
    if isinstance(want_score, (int, float)):
        if result["apex"] is None:
            fails.append(f"gcp_score: expected {want_score}, got null")
        elif abs(result["apex"] - want_score) > 0.05:
            fails.append(f"gcp_score: expected {want_score}, got {result['apex']}")
    elif want_score == "null":
        if result["apex"] is not None:
            fails.append(f"gcp_score: expected null, got {result['apex']}")

    want_gate = expect.get("global_gate")
    if want_gate is not None and bool(result["global_gate"]) != bool(want_gate):
        fails.append(f"global_gate: expected {want_gate}, got {result['global_gate']}")

    if "exact_flags" in expect:
        got = sorted(set(flag_ids))
        want = sorted(set(expect["exact_flags"]))
        if got != want:
            fails.append(f"exact_flags: expected {want}, got {got}")

    for f in expect.get("flags_present", []):
        if f not in flag_ids:
            fails.append(f"flag {f} expected present, missing (got {sorted(set(flag_ids))})")
    for f in expect.get("flags_absent", []):
        if f in flag_ids:
            fails.append(f"flag {f} expected absent, present")

    return (not fails), fails


def _run_one(name, mutator, description, expect, config, spec, baseline_stage2):
    stage2 = copy.deepcopy(baseline_stage2)
    cleanup_paths: list[Path] = []
    try:
        result = mutator(stage2)
        if isinstance(result, Path):
            cleanup_paths.append(result)
        elif isinstance(result, list):
            cleanup_paths.extend(p for p in result if isinstance(p, Path))

        s3a = stage3a_derived.run(config, PROJECT_ROOT, spec, stage2)
        s3b = stage3b_indicators.run(config, PROJECT_ROOT, spec, s3a, stage2)
        s3c = stage3c_blocks.run(config, PROJECT_ROOT, spec, s3b)
        s3d = stage3d_score.run(config, PROJECT_ROOT, spec, stage2, s3a, s3b, s3c)

        sv = spec["_meta"]["version"]
        out_dir = SCENARIOS_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)
        for fname, stage, payload in (
            ("02_source_fields.json", "stage2_source_fields", stage2),
            ("03_derived.json", "stage3a_derived", s3a),
            ("04_indicators.json", "stage3b_indicators", s3b),
            ("05_blocks.json", "stage3c_blocks", s3c),
            ("06_gcp_score.json", "stage3d_score", s3d),
        ):
            (out_dir / fname).write_text(json.dumps(
                _envelope(sv, config, stage, payload, name),
                indent=2, sort_keys=True), encoding="utf-8")

        blocks = s3c["aggregated_blocks"]
        result_row = {
            "name": name,
            "description": description,
            "apex": s3d["gcp_score"],
            "block_complete": blocks["BB_GCP_COMPLETE"]["aggregate_score"],
            "block_setup": blocks["BB_GCP_SETUP"]["aggregate_score"],
            "block_env": blocks["BB_GCP_ENV"]["aggregate_score"],
            "global_gate": s3d["global_gate"]["triggered"],
            "blocks_with_gate": s3c["stage3c_meta"]["blocks_with_per_point_gate"],
            "gcp_point_count": s3c["stage3c_meta"]["gcp_role_point_count"],
            "flag_ids": [f["flag_id"] for f in s3d["all_flags_aggregated"]],
            "flag_count_by_origin": s3d["flags_by_origin_stage"],
            "crashed": False,
        }
        ok, fails = _check_expect(expect, result_row)
        result_row["expect_ok"] = ok
        result_row["expect_failures"] = fails
        return result_row
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "description": description,
            "crashed": True,
            "expect_ok": False,
            "expect_failures": [f"CRASH: {type(exc).__name__}: {exc}"],
            "exception": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    finally:
        for p in cleanup_paths:
            if p.exists():
                p.unlink()


def _print_table(results: list[dict]) -> None:
    print()
    print("=" * 132)
    print(f"{'scenario':28s} {'apex':>6s}  {'CMPL':>6s} {'SETUP':>6s} {'ENV':>6s}  "
          f"{'gate':5s} {'OK':3s}  flags")
    print("-" * 132)
    for r in results:
        if r.get("crashed"):
            print(f"{r['name']:28s}  CRASH  {r['exception']}")
            continue
        flags = ",".join(x.replace("FLG_GCP_", "") for x in r["flag_ids"]) or "-"
        ok = "ok" if r["expect_ok"] else "XX"
        print(f"{r['name']:28s} {_fmtnum(r['apex']):>6s}  "
              f"{_fmtnum(r['block_complete']):>6s} {_fmtnum(r['block_setup']):>6s} "
              f"{_fmtnum(r['block_env']):>6s}  "
              f"{str(r['global_gate']):5s} {ok:3s}  {flags}")
    print("=" * 132)


def main() -> int:
    config, spec = _load_config_and_spec()
    # Isolate the Kp cache so the real cache/noaa_swpc tree is never touched.
    config = copy.deepcopy(config)
    config.setdefault("options", {})["noaa_swpc_cache_dir"] = _KP_CACHE_REL
    _KP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for stale in _KP_CACHE_DIR.glob("*.json"):
        stale.unlink()

    baseline_stage2 = _load_baseline_stage2()

    results: list[dict] = []
    for name, mutator, description, expect in SCENARIOS:
        r = _run_one(name, mutator, description, expect, config, spec, baseline_stage2)
        results.append(r)
        if r.get("crashed"):
            print(f"[{name}] CRASH - {r['exception']}")
        else:
            flags_short = ",".join(f.replace("FLG_GCP_", "") for f in r["flag_ids"]) or "-"
            print(f"[{name}] {'ok ' if r['expect_ok'] else 'XX '} "
                  f"apex={_fmtnum(r['apex']):>5s}  "
                  f"C/S/E={_fmtnum(r['block_complete'])}/{_fmtnum(r['block_setup'])}/"
                  f"{_fmtnum(r['block_env'])}  "
                  f"gate={str(r['global_gate']):5s}  flags={flags_short}")
            for f in r["expect_failures"]:
                print(f"        FAIL: {f}")

    n_crashed = sum(1 for r in results if r.get("crashed"))
    n_failed = sum(1 for r in results if not r.get("expect_ok"))

    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    (SCENARIOS_DIR / "_summary.json").write_text(json.dumps({
        "spec_version": spec["_meta"]["version"],
        "n_scenarios": len(results),
        "n_crashed": n_crashed,
        "n_failed_expectations": n_failed,
        "results": [{k: v for k, v in r.items() if k != "traceback"} for r in results],
    }, indent=2, sort_keys=True), encoding="utf-8")

    # Pass-2 problem-coverage map (CSV + JSON).
    pass2_csv = SCENARIOS_DIR / "_pass2_problem_coverage.csv"
    pass2_json = SCENARIOS_DIR / "_pass2_problem_coverage.json"
    with pass2_csv.open("w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["problem_no", "problem_name", "disposition", "covered_by",
                    "scenarios", "verification_status"])
        for no, nm, disp, cov, scns, status in PASS2_PROBLEM_MAP:
            w.writerow([no, nm, disp, cov, " | ".join(scns) if scns else "-", status])
    pass2_payload = [
        {"problem_no": no, "problem_name": nm, "disposition": disp,
         "covered_by": cov, "scenarios": scns, "verification_status": status}
        for no, nm, disp, cov, scns, status in PASS2_PROBLEM_MAP
    ]
    by_status: dict[str, int] = {}
    for row in pass2_payload:
        s = row["verification_status"].split(" - ")[0]
        by_status[s] = by_status.get(s, 0) + 1
    pass2_json.write_text(json.dumps({
        "spec_version": spec["_meta"]["version"],
        "n_problems": len(pass2_payload),
        "by_status": dict(sorted(by_status.items())),
        "problems": pass2_payload,
    }, indent=2, sort_keys=True), encoding="utf-8")

    _print_table(results)
    print(f"\n{len(results)} scenarios run, {n_crashed} crashed, "
          f"{n_failed} with expectation failures.")
    print(f"Summary           -> {(SCENARIOS_DIR / '_summary.json').relative_to(PROJECT_ROOT)}")
    print(f"Pass-2 coverage   -> {pass2_csv.relative_to(PROJECT_ROOT)}")
    print(f"                     {pass2_json.relative_to(PROJECT_ROOT)}")
    print(f"\n=== Pass-2 coverage by status (22 problems) ===")
    for status in sorted(by_status.keys()):
        print(f"  {status:24s} {by_status[status]}")
    return 1 if (n_crashed or n_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
