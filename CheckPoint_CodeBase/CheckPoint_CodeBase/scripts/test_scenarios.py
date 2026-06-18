#!/usr/bin/env python3
"""Step 12 - smoke-test harness for the Check Point PPK pipeline.

Two passes:
  Pass 1 (spec-internal coverage): baseline + one scenario per gate / band /
    threshold flag / N/A-redistribution path / global gate / null state, plus an
    all-flags stress case that stays under the global gate.
  Pass 2 (CBMI problem-driven): one scenario per concrete numerical example
    drawn from the CBMI Check Point Problems sheet (v0.3) prose, each scenario's
    entry quoting the sheet verbatim. Plus the 30-row problem-coverage map
    (CSV + JSON).

Each scenario deep-copies the baseline Stage-2 source-fields, applies its
mutator(s), re-runs Stages 3a-3d into tests/scenarios/<name>/, and captures the
apex score / block aggregates / flags. Determinism: artifacts are written
WITHOUT generated_at (the harness omits it) so scenario outputs are byte-stable
across runs. The NOAA Kp cache is isolated to tests/scenarios/_kp_cache so the
real cache/noaa_swpc tree is never touched.

Usage:  python3 scripts/test_scenarios.py [paths.json]
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import stage1_inventory  # noqa: E402
import stage2_merge  # noqa: E402
import stage3a_derived  # noqa: E402
import stage3b_indicators  # noqa: E402
import stage3c_blocks  # noqa: E402
import stage3d_score  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = ROOT / "tests" / "scenarios"
KP_CACHE_REL = "tests/scenarios/_kp_cache"
BASELINE_CAPTURE_DATE = "2026-05-28"
QUIET_KP = 2.3

_CONFIG: dict = {}
_SPEC: dict = {}
_SPEC_VERSION: str = ""
_BASELINE2: dict = {}

# ---- canonical source-field keys (for mutators) ----------------------------
K_SIGMA_H = "L1F_CP_003_position_sigma_horizontal_m"
K_FIX = "L1F_CP_005_fix_type_at_capture"
K_CORR_AGE = "L1F_CP_006_correction_age_at_capture_sec"
K_FIX_HOLD = "L1F_CP_007_fix_hold_duration_sec"
K_PDOP = "L1F_CP_008_pdop_at_capture"
K_SAT = "L1F_CP_009_sat_count_at_capture"
K_CN0 = "L1F_CP_010_cn0_mean_at_capture"
K_TILT = "L1F_CP_013_tilt_logged_deg"
K_CAPTURE = "L1F_CP_014_capture_utc"
K_DOWNLOAD = "L1F_CP_016_raw_log_download_confirmed"
K_SIG = "L1F_CP_017_raw_log_signature_valid"
K_DEVICE_TYPE = "L1F_CP_020_device_type"
K_DEVICE_ID_FORM = "L1F_CP_021_device_id"
K_DEVICE_ROLE = "L1F_CP_022_device_role"
K_ANT_MODEL = "L1F_CP_023_antenna_model"
K_ANT_HEIGHT = "L1F_CP_024_antenna_height_m"
K_MEAS_TYPE = "L1F_CP_026_antenna_measurement_type"
K_MEAS_REF = "L1F_CP_027_measured_to_reference"
K_HEIGHT_COUNT = "L1F_CP_028_height_measured_count"
K_BASELINE = "L1F_CP_030_baseline_length_km"
K_NTRIP = "L1F_CP_031_ntrip_mountpoint"
K_PHOTO = "L1F_CP_036_mark_photo_captured"


# ---- harness plumbing ------------------------------------------------------

def _set(points, idx, key, value):
    points[idx]["source_fields"][key] = value


def _write_deterministic(path: Path, stage: str, data: dict) -> None:
    """Envelope WITHOUT generated_at -> byte-stable artifacts."""
    env = {"spec_version": _SPEC_VERSION, "config_used": _CONFIG, "stage": stage, "data": data}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(env, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def _write_kp(kp, date_str=BASELINE_CAPTURE_DATE):
    p = ROOT / KP_CACHE_REL / f"{date_str}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        json.dump({"kp": kp, "date": date_str, "_source": "harness"}, fh, sort_keys=True)


def _run_chain(data2):
    d3a = stage3a_derived.run(_CONFIG, ROOT, _SPEC, data2)
    d3b = stage3b_indicators.run(_CONFIG, ROOT, _SPEC, d3a, data2)
    d3c = stage3c_blocks.run(_CONFIG, ROOT, _SPEC, d3b)
    d3d = stage3d_score.run(_CONFIG, ROOT, _SPEC, data2, d3a, d3b, d3c)
    return d3a, d3b, d3c, d3d


def _apply_and_run(name, mutators, kp=QUIET_KP):
    _write_kp(kp)
    data2 = copy.deepcopy(_BASELINE2)
    muts = mutators if isinstance(mutators, (list, tuple)) else [mutators]
    for m in muts:
        if m:
            m(data2["points"])
    d3a, d3b, d3c, d3d = _run_chain(data2)
    out = SCENARIOS_DIR / name
    _write_deterministic(out / "02_source_fields.json", "stage2_merge", data2)
    _write_deterministic(out / "03_derived.json", "stage3a_derived", d3a)
    _write_deterministic(out / "04_indicators.json", "stage3b_indicators", d3b)
    _write_deterministic(out / "05_blocks.json", "stage3c_blocks", d3c)
    _write_deterministic(out / "06_apex.json", "stage3d_score", d3d)
    return {
        "scenario": name,
        "check_point_score": d3d["check_point_score"],
        "global_gate_triggered": d3d["global_gate"]["triggered"],
        "effective_check_point_count": d3d["stage3d_meta"]["effective_check_point_count"],
        "block_aggregates": d3c["stage3c_meta"]["aggregate_score_summary"],
        "flags": sorted({f["flag_id"] for f in d3d["all_flags_aggregated"]}),
        "flag_count": len(d3d["all_flags_aggregated"]),
    }


# ---- Pass 1 mutators (spec-internal coverage) ------------------------------

def _m_sigma_marginal(p): _set(p, 0, K_SIGMA_H, 0.03)        # 1.5x -> CP_006
def _m_sigma_high(p): _set(p, 0, K_SIGMA_H, 0.06)            # 3x -> CP_007
def _m_sigma_reject(p): _set(p, 0, K_SIGMA_H, 0.20)          # 10x -> CP_008 (+kill point0)
def _m_sigma_not_exported(p): _set(p, 0, K_SIGMA_H, None)    # CB_X expected -> CP_009


def _m_sigma_na_redistribute(p):
    # NOTE: score lands at 96.3, not 100 - this is CORRECT, not a redistribution bug.
    # The sigma N/A path itself is clean: L3I_CP_001 returns score=None, Stage 3c drops
    # it from COMPLETE and renormalises the remaining weights over 0.55, so COMPLETE
    # stays 100. The -3.7 comes ENTIRELY from switching device_type to OTHER, which has
    # legitimate downstream consequences in SETUP: OTHER is not antenna-height-auto-known
    # (L3I_CP_005 -> 70) and not tilt-verifiable (L3I_CP_006 -> 70 advisory), dropping
    # SETUP to 89.5. The flag set is therefore just {CP_015} (no sigma flag, because
    # absent+not-expected is N/A, not a defect). See expect entry below.
    _set(p, 0, K_SIGMA_H, None)
    _set(p, 0, K_DEVICE_TYPE, "OTHER")


def _m_fix_float(p): _set(p, 0, K_FIX, "FLOAT")             # gate CP_004 (CAT, eff<5)
def _m_fix_autonomous(p): _set(p, 0, K_FIX, "AUTONOMOUS")  # gate CP_005
def _m_corr_stale(p): _set(p, 0, K_CORR_AGE, 10.0)         # 5-15s -> CP_010
def _m_corr_lost(p): _set(p, 0, K_CORR_AGE, 45.0)          # >30s -> CP_011
def _m_corr_na(p): _set(p, 0, K_CORR_AGE, None)            # absent -> redistribute
def _m_log_download(p): _set(p, 0, K_DOWNLOAD, False)      # -> CP_027
def _m_log_tampered(p): _set(p, 0, K_SIG, False)           # -> CP_028


def _m_height_missing_gate(p):
    _set(p, 0, K_DEVICE_TYPE, "DGPS")
    _set(p, 0, K_ANT_HEIGHT, None)


def _m_height_slant(p):
    _set(p, 0, K_DEVICE_TYPE, "DGPS")
    _set(p, 0, K_MEAS_TYPE, "SLANT")


def _m_high_tilt(p): _set(p, 0, K_TILT, 6.0)               # >4 verified -> CP_014
def _m_long_baseline(p): _set(p, 0, K_BASELINE, 15.0)      # 10-20 -> CP_012
def _m_excessive_baseline(p): _set(p, 0, K_BASELINE, 50.0)  # >40 -> CP_013
def _m_ntrip_mismatch(p): _set(p, 0, K_NTRIP, "WRONG_MP")   # -> CP_022
def _m_antenna_type_mismatch(p): _set(p, 0, K_ANT_MODEL, "DIFFERENT_ANT")  # -> CP_030
def _m_device_id_mismatch(p): _set(p, 0, K_DEVICE_ID_FORM, "WRONG-ID")     # -> CP_016
def _m_high_pdop(p): _set(p, 0, K_PDOP, 7.5)              # >6 -> CP_019
def _m_short_fix_hold(p): _set(p, 0, K_FIX_HOLD, 2.0)     # 1-4 -> CP_020
def _m_no_fix_hold(p): _set(p, 0, K_FIX_HOLD, 0.5)        # <1 -> CP_021
def _m_fix_hold_na(p): _set(p, 0, K_FIX_HOLD, None)       # absent -> redistribute
def _m_obstruction(p): _set(p, 0, K_SAT, 5)              # <7 -> CP_017


def _m_iono_storm(p):  # DGPS (single-freq) + Kp storm via cache -> CP_018
    _set(p, 0, K_DEVICE_TYPE, "DGPS")
    _set(p, 0, K_MEAS_TYPE, "VERTICAL")
    _set(p, 0, K_MEAS_REF, "ARP")
    _set(p, 0, K_HEIGHT_COUNT, 3)


def _m_before_flight(p): _set(p, 0, K_CAPTURE, "2026-05-28T08:00:00Z")   # < flight_start -> CP_025
def _m_during_flight(p): _set(p, 0, K_CAPTURE, "2026-05-28T09:45:00Z")   # in window -> CP_026
def _m_delayed_capture(p): _set(p, 0, K_CAPTURE, "2026-05-31T11:05:20Z")  # +3d (24-168h) -> CP_023
def _m_stale_capture(p): _set(p, 0, K_CAPTURE, "2026-06-08T11:05:20Z")    # +11d (>168h) -> CP_024
def _m_no_mark_photo(p): _set(p, 0, K_PHOTO, False)       # -> CP_029


def _m_global_gate_all_float(p):
    for i in range(len(p)):
        _set(p, i, K_FIX, "FLOAT")


def _m_null_no_check_points(p):
    for i in range(len(p)):
        _set(p, i, K_DEVICE_ROLE, "GCP")
        p[i]["device_role"] = "GCP"


def _m_all_flags_stress(p):
    # Distinct non-gate, non-kill defects across the 3 points so the global gate
    # stays closed but many flags aggregate.
    _set(p, 0, K_SIGMA_H, 0.03)      # CP_006 marginal (70, not a kill)
    _set(p, 0, K_BASELINE, 15.0)     # CP_012
    _set(p, 0, K_PDOP, 7.5)          # CP_019
    _set(p, 1, K_CORR_AGE, 10.0)     # CP_010
    _set(p, 1, K_FIX_HOLD, 2.0)      # CP_020
    _set(p, 1, K_NTRIP, "WRONG_MP")  # CP_022
    _set(p, 2, K_ANT_MODEL, "DIFFERENT_ANT")  # CP_030
    _set(p, 2, K_DEVICE_ID_FORM, "WRONG-ID")  # CP_016
    _set(p, 2, K_TILT, 6.0)          # CP_014
    _set(p, 2, K_PHOTO, False)       # CP_029


# ---- Pass 2 mutators (CBMI sheet concrete numbers) -------------------------

def _m_p2_float_012(p):  # #1: FLOAT (decimetre-level), sigma 0.12 m
    # NOTE: raises TWO flags (CP_004 fix-gate AND CP_008 sigma-reject), by design.
    # The CBMI sheet quotes "decimetre-level" for a FLOAT fix; 0.12 m at a 0.02 m
    # accuracy target is 6x (> 5x), which independently trips the sigma-reject band.
    # Both firing together is physically faithful and validates the spec's #30 point:
    # an inflated sigma is the strongest single-occupation tell of a false/poor fix.
    # The score (73.8) is driven by the fix gate alone (COMPLETE -> 0 on this point);
    # the sigma-reject is redundant evidence on the same already-gated point.
    _set(p, 0, K_FIX, "FLOAT")
    _set(p, 0, K_SIGMA_H, 0.12)


def _m_p2_single_epoch_2s(p): _set(p, 0, K_FIX_HOLD, 2.0)   # #2: held 1-4 s
def _m_p2_stale_12s(p): _set(p, 0, K_CORR_AGE, 12.0)        # #3: 5-15s stale
def _m_p2_baseline_35km(p): _set(p, 0, K_BASELINE, 35.0)    # #9: 20-40km excessive


def _m_p2_height_blunder(p):  # #10
    _set(p, 0, K_DEVICE_TYPE, "DGPS")
    _set(p, 0, K_ANT_HEIGHT, None)


def _m_p2_ant_type_mismatch(p): _set(p, 0, K_ANT_MODEL, "TRM_OTHER")   # #11
def _m_p2_device_id_mismatch(p): _set(p, 0, K_DEVICE_ID_FORM, "SN-OTHER")  # #12
def _m_p2_mark_disturbed_96h(p): _set(p, 0, K_CAPTURE, "2026-06-01T10:30:00Z")  # #13: 4 days -> 1-7d
def _m_p2_obstruction_5sats(p): _set(p, 0, K_SAT, 5)        # #21


def _m_p2_iono_kp73(p):  # #22: Kp 7.3, single-freq
    _set(p, 0, K_DEVICE_TYPE, "DGPS")
    _set(p, 0, K_MEAS_TYPE, "VERTICAL")
    _set(p, 0, K_MEAS_REF, "ARP")
    _set(p, 0, K_HEIGHT_COUNT, 3)


def _m_p2_log_not_downloaded(p): _set(p, 0, K_DOWNLOAD, False)  # #23
def _m_p2_log_tampered(p): _set(p, 0, K_SIG, False)            # #24
def _m_p2_pole_tilt_5deg(p): _set(p, 0, K_TILT, 5.0)          # #25: 2m x 5deg ~ 17cm
def _m_p2_no_mark_photo(p): _set(p, 0, K_PHOTO, False)        # #26
def _m_p2_wrong_ntrip(p): _set(p, 0, K_NTRIP, "OTHER_MOUNT")  # #27
def _m_p2_poor_pdop_75(p): _set(p, 0, K_PDOP, 7.5)           # #28: PDOP 7.5
def _m_p2_sigma_ignored_0045(p): _set(p, 0, K_SIGMA_H, 0.045)  # #30: 2.25x -> CP_007


# ---- scenario registry -----------------------------------------------------
PASS1 = [
    ("baseline", "Gold-standard control; expect 100 + only CP_015 advisory x3", None, QUIET_KP),
    ("sigma_marginal", "sigma 1.5x target -> CP_006", _m_sigma_marginal, QUIET_KP),
    ("sigma_high", "sigma 3x target -> CP_007", _m_sigma_high, QUIET_KP),
    ("sigma_reject", "sigma 10x target -> CP_008 + point completeness kill", _m_sigma_reject, QUIET_KP),
    ("sigma_not_exported", "CB_X sigma absent (expected) -> CP_009", _m_sigma_not_exported, QUIET_KP),
    ("sigma_na_redistribute", "OTHER device + sigma absent -> sigma N/A (COMPLETE stays 100 via "
     "weight redistribution); 96.3 from OTHER's SETUP capability loss, not a bug",
     _m_sigma_na_redistribute, QUIET_KP),
    ("fix_float", "FLOAT at capture -> internal gate, CP_004 (escalated CATASTROPHIC, eff<5)",
     _m_fix_float, QUIET_KP),
    ("fix_autonomous", "AUTONOMOUS at capture -> internal gate, CP_005", _m_fix_autonomous, QUIET_KP),
    ("correction_stale", "correction age 10s -> CP_010", _m_corr_stale, QUIET_KP),
    ("correction_lost", "correction age 45s -> CP_011", _m_corr_lost, QUIET_KP),
    ("correction_na", "correction age absent -> N/A weight redistribution (no flag)", _m_corr_na, QUIET_KP),
    ("log_download_unconfirmed", "raw_log_download_confirmed False -> CP_027", _m_log_download, QUIET_KP),
    ("log_tampered", "raw_log_signature_valid False -> CP_028", _m_log_tampered, QUIET_KP),
    ("antenna_height_missing", "DGPS no height -> internal gate, CP_003 (CATASTROPHIC)",
     _m_height_missing_gate, QUIET_KP),
    ("height_slant", "DGPS SLANT measurement -> band 72, no flag (negative band test)",
     _m_height_slant, QUIET_KP),
    ("high_tilt", "verified tilt 6deg -> CP_014", _m_high_tilt, QUIET_KP),
    ("long_baseline", "baseline 15km -> CP_012", _m_long_baseline, QUIET_KP),
    ("excessive_baseline", "baseline 50km -> CP_013", _m_excessive_baseline, QUIET_KP),
    ("ntrip_mismatch", "ntrip != expected -> CP_022", _m_ntrip_mismatch, QUIET_KP),
    ("antenna_type_mismatch", "form antenna_model != device -> CP_030", _m_antenna_type_mismatch, QUIET_KP),
    ("device_id_mismatch", "form device_id != device -> CP_016", _m_device_id_mismatch, QUIET_KP),
    ("high_pdop", "PDOP 7.5 -> CP_019", _m_high_pdop, QUIET_KP),
    ("short_fix_hold", "fix hold 2s -> CP_020", _m_short_fix_hold, QUIET_KP),
    ("no_fix_hold", "fix hold 0.5s -> CP_021", _m_no_fix_hold, QUIET_KP),
    ("fix_hold_na", "fix hold absent -> N/A weight redistribution (no flag)", _m_fix_hold_na, QUIET_KP),
    ("obstruction", "5 sats at capture -> CP_017", _m_obstruction, QUIET_KP),
    ("iono_storm", "DGPS single-freq + Kp 7.3 -> CP_018", _m_iono_storm, 7.3),
    ("captured_before_flight", "capture < flight_start -> CP_025", _m_before_flight, QUIET_KP),
    ("captured_during_flight", "capture in flight window -> CP_026 (LOW workflow)", _m_during_flight, QUIET_KP),
    ("delayed_capture", "capture +3 days after flight (24-168h) -> CP_023", _m_delayed_capture, QUIET_KP),
    ("stale_capture", "capture +11 days after flight (>168h) -> CP_024", _m_stale_capture, QUIET_KP),
    ("no_mark_photo", "mark_photo_captured False -> CP_029 advisory", _m_no_mark_photo, QUIET_KP),
    ("global_gate_all_float", "every CP point FLOAT -> score 0, CP_001 + 3x CP_004",
     _m_global_gate_all_float, QUIET_KP),
    ("null_no_check_points", "zero CHECK_POINT-role points -> score null, CP_002",
     _m_null_no_check_points, QUIET_KP),
    ("all_flags_stress", "10 distinct flags across 3 points, global gate stays closed",
     _m_all_flags_stress, QUIET_KP),
]

PASS2 = [
    ("p2_float_sigma_012", 'CBMI #1: rover FLOAT (decimetre-level) accepted as FIXED; sigma 0.12 m '
     '(6x target -> raises BOTH CP_004 fix-gate AND CP_008 sigma-reject, by design).',
     _m_p2_float_012, QUIET_KP),
    ("p2_single_epoch_2s", 'CBMI #2: single-epoch capture, held FIXED only 2 s (1-4s band).',
     _m_p2_single_epoch_2s, QUIET_KP),
    ("p2_stale_correction_12s", 'CBMI #3: RTK correction 12 s old at capture (5-15s band).',
     _m_p2_stale_12s, QUIET_KP),
    ("p2_excessive_baseline_35km", 'CBMI #9: 35 km baseline (20-40km excessive band); patchy CORS.',
     _m_p2_baseline_35km, QUIET_KP),
    ("p2_height_blunder_dgps", 'CBMI #10: antenna height not entered for DGPS/OTHER device (hard gate).',
     _m_p2_height_blunder, QUIET_KP),
    ("p2_antenna_type_mismatch", 'CBMI #11: form antenna model != device-reported (sub-cm to ~3cm).',
     _m_p2_ant_type_mismatch, QUIET_KP),
    ("p2_device_id_mismatch", 'CBMI #12: form device_id != RTK-reported (provenance).',
     _m_p2_device_id_mismatch, QUIET_KP),
    ("p2_mark_disturbed_96h", 'CBMI #13: check point captured 4 days after flight (1-7d band -> CP_023).',
     _m_p2_mark_disturbed_96h, QUIET_KP),
    ("p2_obstruction_5sats", 'CBMI #21: only 5 satellites at capture (near obstructions).',
     _m_p2_obstruction_5sats, QUIET_KP),
    ("p2_iono_storm_kp73", 'CBMI #22: ionospheric storm Kp 7.3, single-frequency device.',
     _m_p2_iono_kp73, 7.3),
    ("p2_log_not_downloaded", 'CBMI #23: session log not synced/downloaded before close.',
     _m_p2_log_not_downloaded, QUIET_KP),
    ("p2_log_tampered", 'CBMI #24: coordinate manually edited; signature validation fails.',
     _m_p2_log_tampered, QUIET_KP),
    ("p2_pole_tilt_5deg", 'CBMI #25: pole tilt 5deg on ~2 m pole ~= 17 cm horizontal error.',
     _m_p2_pole_tilt_5deg, QUIET_KP),
    ("p2_no_mark_photo", 'CBMI #26: no photo of mark and surroundings.',
     _m_p2_no_mark_photo, QUIET_KP),
    ("p2_wrong_ntrip", 'CBMI #27: NTRIP mountpoint differs from project-declared.',
     _m_p2_wrong_ntrip, QUIET_KP),
    ("p2_poor_pdop_75", 'CBMI #28: PDOP 7.5 at the FIXED capture epoch (>6 band).',
     _m_p2_poor_pdop_75, QUIET_KP),
    ("p2_sigma_ignored_0045", 'CBMI #30: sigma 0.045 m vs 0.02 m target = 2.25x (2-5x band).',
     _m_p2_sigma_ignored_0045, QUIET_KP),
]


# ---- expected outcomes (self-validation; the DURABLE record of intended results) -----------
# Per scenario: (expected apex score, exact aggregated flag-id set). The harness asserts each
# result against this and exits non-zero on any mismatch, so a future change that shifts a
# score or flag is caught immediately. These values are the human-asserted invariants (test
# expectations), not spec-derived - exactly the pattern the GCP sibling harness uses.
# CP_015 CP_NO_REPEATABILITY_CHECK is advisory + always fires once per single-occupation
# point, so it appears in EVERY flag set (including null_no_check_points, whose 3 GCP-role
# points each still emit it at Stage 3a). Score "null" asserts check_point_score is None.
SCORE_TOL = 0.05
A = "FLG_CP_015"
EXPECT = {
    # ---- Pass 1: spec-internal coverage ----
    "baseline":                 (100.0, {A}),
    "sigma_marginal":           (96.4,  {"FLG_CP_006", A}),
    "sigma_high":               (91.7,  {"FLG_CP_007", A}),
    "sigma_reject":             (88.2,  {"FLG_CP_008", A}),
    "sigma_not_exported":       (94.1,  {"FLG_CP_009", A}),
    # 96.3 (not 100): sigma N/A path is clean (COMPLETE stays 100); the drop is OTHER's
    # SETUP capability loss (no auto-height, no verifiable tilt). No sigma flag - N/A != defect.
    "sigma_na_redistribute":    (96.3,  {A}),
    "fix_float":                (73.8,  {"FLG_CP_004", A}),
    "fix_autonomous":           (73.8,  {"FLG_CP_005", A}),
    "correction_stale":         (98.4,  {"FLG_CP_010", A}),
    "correction_lost":          (96.0,  {"FLG_CP_011", A}),
    "correction_na":            (100.0, {A}),
    "log_download_unconfirmed": (98.7,  {"FLG_CP_027", A}),
    "log_tampered":             (98.2,  {"FLG_CP_028", A}),
    "antenna_height_missing":   (79.6,  {"FLG_CP_003", A}),
    "height_slant":             (96.5,  {A}),
    "high_tilt":                (97.1,  {"FLG_CP_014", A}),
    "long_baseline":            (99.1,  {"FLG_CP_012", A}),
    "excessive_baseline":       (97.5,  {"FLG_CP_013", A}),
    "ntrip_mismatch":           (98.8,  {"FLG_CP_022", A}),
    "antenna_type_mismatch":    (99.2,  {"FLG_CP_030", A}),
    "device_id_mismatch":       (99.6,  {"FLG_CP_016", A}),
    "high_pdop":                (96.7,  {"FLG_CP_019", A}),
    "short_fix_hold":           (98.8,  {"FLG_CP_020", A}),
    "no_fix_hold":              (98.0,  {"FLG_CP_021", A}),
    "fix_hold_na":              (100.0, {A}),
    "obstruction":              (98.5,  {"FLG_CP_017", A}),
    "iono_storm":               (97.7,  {"FLG_CP_018", A}),
    "captured_before_flight":   (100.0, {"FLG_CP_025", A}),
    "captured_during_flight":   (100.0, {"FLG_CP_026", A}),
    "delayed_capture":          (100.0, {"FLG_CP_023", A}),
    "stale_capture":            (100.0, {"FLG_CP_024", A}),
    "no_mark_photo":            (100.0, {"FLG_CP_029", A}),
    "global_gate_all_float":    (0.0,   {"FLG_CP_001", "FLG_CP_004", A}),
    "null_no_check_points":     ("null", {"FLG_CP_002", A}),
    "all_flags_stress":         (86.3,  {"FLG_CP_006", "FLG_CP_010", "FLG_CP_012", "FLG_CP_014",
                                         A, "FLG_CP_016", "FLG_CP_019", "FLG_CP_020", "FLG_CP_022",
                                         "FLG_CP_029", "FLG_CP_030"}),
    # ---- Pass 2: CBMI sheet concrete numbers ----
    # Double flag by design: 0.12 m = 6x target trips CP_008 on top of the CP_004 fix gate.
    "p2_float_sigma_012":        (73.8,  {"FLG_CP_004", "FLG_CP_008", A}),
    "p2_single_epoch_2s":        (98.8,  {"FLG_CP_020", A}),
    "p2_stale_correction_12s":   (98.4,  {"FLG_CP_010", A}),
    "p2_excessive_baseline_35km":(98.2,  {"FLG_CP_013", A}),
    "p2_height_blunder_dgps":    (79.6,  {"FLG_CP_003", A}),
    "p2_antenna_type_mismatch":  (99.2,  {"FLG_CP_030", A}),
    "p2_device_id_mismatch":     (99.6,  {"FLG_CP_016", A}),
    "p2_mark_disturbed_96h":     (100.0, {"FLG_CP_023", A}),
    "p2_obstruction_5sats":      (98.5,  {"FLG_CP_017", A}),
    "p2_iono_storm_kp73":        (97.7,  {"FLG_CP_018", A}),
    "p2_log_not_downloaded":     (98.7,  {"FLG_CP_027", A}),
    "p2_log_tampered":           (98.2,  {"FLG_CP_028", A}),
    "p2_pole_tilt_5deg":         (97.1,  {"FLG_CP_014", A}),
    "p2_no_mark_photo":          (100.0, {"FLG_CP_029", A}),
    "p2_wrong_ntrip":            (98.8,  {"FLG_CP_022", A}),
    "p2_poor_pdop_75":           (96.7,  {"FLG_CP_019", A}),
    "p2_sigma_ignored_0045":     (91.7,  {"FLG_CP_007", A}),
}


def _check_expect(name: str, row: dict) -> tuple[bool, list[str]]:
    """Compare a scenario result against its EXPECT entry. Returns (ok, failures)."""
    if name not in EXPECT:
        return True, [f"(no expect entry for {name})"]
    exp_score, exp_flags = EXPECT[name]
    fails = []
    got_score = row["check_point_score"]
    if exp_score == "null":
        if got_score is not None:
            fails.append(f"score: expected null, got {got_score}")
    elif got_score is None:
        fails.append(f"score: expected {exp_score}, got null")
    elif abs(got_score - exp_score) > SCORE_TOL:
        fails.append(f"score: expected {exp_score}+-{SCORE_TOL}, got {got_score}")
    got_flags = set(row["flags"])
    if got_flags != exp_flags:
        missing = sorted(exp_flags - got_flags)
        extra = sorted(got_flags - exp_flags)
        fails.append(f"flags: missing={missing} extra={extra}")
    return not fails, fails


# ---- problem-coverage map (all 30 CBMI rows; dispositions from sheet v0.3 / spec sheet 08) --
# (problem_no, coverage_class, cbmi_stage, [scenarios], verification_status)
PROBLEM_MAP = [
    (1, "FULLY COVERED", "check_point (Stage 1)", ["fix_float", "p2_float_sigma_012", "global_gate_all_float"], "VERIFIED"),
    (2, "FULLY COVERED", "check_point (Stage 1)", ["short_fix_hold", "no_fix_hold", "p2_single_epoch_2s"], "VERIFIED"),
    (3, "FULLY COVERED", "check_point (Stage 1)", ["correction_stale", "correction_lost", "p2_stale_correction_12s"], "VERIFIED"),
    (4, "NOT COVERED (documented)", "base_station_score", [], "OUT_OF_SCOPE"),
    (5, "NOT COVERED (documented)", "pre_processing_score", [], "OUT_OF_SCOPE"),
    (6, "NOT COVERED (documented)", "pre_processing_score", [], "OUT_OF_SCOPE"),
    (7, "NOT COVERED (documented)", "pre_processing_score", [], "OUT_OF_SCOPE"),
    (8, "PARTIAL (advisory only)", "check_point (Stage 1, advisory)", ["baseline"], "VERIFIED"),
    (9, "FULLY COVERED", "check_point (Stage 1)", ["long_baseline", "excessive_baseline", "p2_excessive_baseline_35km"], "VERIFIED"),
    (10, "FULLY COVERED", "check_point (Stage 1)", ["antenna_height_missing", "p2_height_blunder_dgps"], "VERIFIED"),
    (11, "FULLY COVERED", "check_point (Stage 1)", ["antenna_type_mismatch", "p2_antenna_type_mismatch"], "VERIFIED"),
    (12, "FULLY COVERED", "check_point (Stage 1)", ["device_id_mismatch", "p2_device_id_mismatch"], "VERIFIED"),
    (13, "FULLY COVERED", "check_point (Stage 1)", ["delayed_capture", "stale_capture", "p2_mark_disturbed_96h"], "VERIFIED"),
    (14, "PARTIAL (advisory only)", "check_point (Stage 1, advisory)", ["no_mark_photo", "p2_no_mark_photo"], "VERIFIED"),
    (15, "NOT COVERED (documented)", "pre_processing_score", [], "OUT_OF_SCOPE"),
    (16, "SPLIT", "check_point (Stage-1 null) + pre_processing_score", ["null_no_check_points"], "VERIFIED"),
    (17, "NOT COVERED (documented)", "pre_processing_score", [], "OUT_OF_SCOPE"),
    (18, "NOT COVERED (documented)", "pre_processing_score", [], "OUT_OF_SCOPE"),
    (19, "FULLY COVERED", "check_point (Stage 1)", ["captured_before_flight"], "VERIFIED"),
    (20, "FULLY COVERED", "check_point (Stage 1)", ["captured_during_flight"], "VERIFIED"),
    (21, "FULLY COVERED", "check_point (Stage 1)", ["obstruction", "p2_obstruction_5sats"], "VERIFIED"),
    (22, "FULLY COVERED", "check_point (Stage 1)", ["iono_storm", "p2_iono_storm_kp73"], "VERIFIED"),
    (23, "FULLY COVERED", "check_point (Stage 1)", ["log_download_unconfirmed", "p2_log_not_downloaded"], "VERIFIED"),
    (24, "FULLY COVERED", "check_point (Stage 1)", ["log_tampered", "p2_log_tampered"], "VERIFIED"),
    (25, "FULLY COVERED", "check_point (Stage 1)", ["high_tilt", "p2_pole_tilt_5deg"], "VERIFIED"),
    (26, "FULLY COVERED", "check_point (Stage 1)", ["no_mark_photo", "p2_no_mark_photo"], "VERIFIED"),
    (27, "FULLY COVERED", "check_point (Stage 1)", ["ntrip_mismatch", "p2_wrong_ntrip"], "VERIFIED"),
    (28, "FULLY COVERED", "check_point (Stage 1)", ["high_pdop", "p2_poor_pdop_75"], "VERIFIED"),
    (29, "NOT COVERED (documented)", "processing_score (future)", [], "OUT_OF_SCOPE"),
    (30, "FULLY COVERED", "check_point (Stage 1)", ["sigma_marginal", "sigma_high", "p2_sigma_ignored_0045"], "VERIFIED"),
]


def _write_problem_coverage(results_by_name: dict):
    spec_problems = {p["problem_no"]: p for p in _SPEC.get("problem_coverage_map", [])}
    rows = []
    for pno, cov, stage, scenarios, vstatus in PROBLEM_MAP:
        sp = spec_problems.get(pno, {})
        seen_flags = sorted({fl for s in scenarios for fl in results_by_name.get(s, {}).get("flags", [])})
        rows.append({
            "problem_no": pno,
            "problem": sp.get("problem", ""),
            "severity": sp.get("severity", ""),
            "spec_disposition": sp.get("disposition", ""),
            "cbmi_coverage_class": cov,
            "cbmi_stage": stage,
            "scenarios": scenarios,
            "flags_observed": seen_flags,
            "verification_status": vstatus,
        })
    (SCENARIOS_DIR / "_pass2_problem_coverage.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    with (SCENARIOS_DIR / "_pass2_problem_coverage.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["problem_no", "problem", "severity", "spec_disposition",
                    "cbmi_coverage_class", "cbmi_stage", "scenarios", "flags_observed",
                    "verification_status"])
        for r in rows:
            w.writerow([r["problem_no"], r["problem"], r["severity"], r["spec_disposition"],
                        r["cbmi_coverage_class"], r["cbmi_stage"], ";".join(r["scenarios"]),
                        ";".join(r["flags_observed"]), r["verification_status"]])
    return rows


def main(argv=None):
    global _CONFIG, _SPEC, _SPEC_VERSION, _BASELINE2
    parser = argparse.ArgumentParser(description="Check Point PPK smoke-test harness")
    parser.add_argument("config", nargs="?", default=str(ROOT / "paths.json"))
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    _CONFIG = common.load_config(config_path)
    _CONFIG.setdefault("options", {})["noaa_kp_cache_dir"] = KP_CACHE_REL
    _SPEC = common.load_spec(ROOT, _CONFIG)
    _SPEC_VERSION = _SPEC["_meta"]["version"]

    env1, hard = stage1_inventory.run(_CONFIG, ROOT)
    if hard:
        print("HALT: Stage 1 hard failure; cannot build baseline.")
        return 1
    _BASELINE2 = stage2_merge.run(_CONFIG, ROOT, _SPEC, env1["data"])

    all_scenarios = PASS1 + PASS2
    results, results_by_name = [], {}
    validation_failures = []
    print(f"Running {len(all_scenarios)} scenarios (Pass 1: {len(PASS1)}, Pass 2: {len(PASS2)})\n")
    for name, desc, mut, kp in all_scenarios:
        row = _apply_and_run(name, mut, kp)
        row["description"] = desc
        ok, fails = _check_expect(name, row)
        row["expect_ok"] = ok
        row["expect_failures"] = fails
        if not ok:
            validation_failures.append((name, fails))
        results.append(row)
        results_by_name[name] = row
        score = row["check_point_score"]
        sstr = "null" if score is None else str(score)
        mark = "ok " if ok else "FAIL"
        print(f"  [{mark}] {name:30s} score={sstr:>6s}  flags={row['flags']}")
        for f in fails:
            if not ok:
                print(f"         -> {f}")

    print(f"\nSelf-validation: {len(results) - len(validation_failures)}/{len(results)} scenarios "
          f"match EXPECT  ({len(validation_failures)} failures)")

    summary = {
        "scenario_count": len(results),
        "pass1_count": len(PASS1),
        "pass2_count": len(PASS2),
        "spec_version": _SPEC_VERSION,
        "validation_pass": len(validation_failures) == 0,
        "validation_failures": [{"scenario": n, "failures": f} for n, f in validation_failures],
        "results": results,
    }
    (SCENARIOS_DIR / "_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    cov_rows = _write_problem_coverage(results_by_name)
    verified = sum(1 for r in cov_rows if r["verification_status"] == "VERIFIED")
    oos = sum(1 for r in cov_rows if r["verification_status"] == "OUT_OF_SCOPE")
    print(f"\nProblem-coverage map: {len(cov_rows)} problems  VERIFIED={verified}  OUT_OF_SCOPE={oos}")
    print(f"Artifacts -> {SCENARIOS_DIR.relative_to(ROOT)}/  "
          f"(_summary.json, _pass2_problem_coverage.{{csv,json}}, {len(results)} scenario dirs)")
    if validation_failures:
        print(f"\nVALIDATION FAILED: {len(validation_failures)} scenario(s) did not match EXPECT.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
