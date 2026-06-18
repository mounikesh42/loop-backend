#!/usr/bin/env python3
"""Stage 3a - compute the 16 L2D_CP_* derived fields for Check Point PPK.

15 fields are PER POINT (L2D_CP_001..015); 1 is SURVEY-LEVEL
(L2D_CP_016 effective_check_point_count), so the per-point loop produces 15 and
the survey rollup adds 1 -> 16 total (spec _meta.counts.derived_fields).

Field kinds come straight from the spec:
  - scoring            feed L3I_CP_* indicators at Stage 3b (14 per-point + the
                       1 survey-level field)
  - composite_scoring  L2D_CP_015 session_integrity_ok (device-type-aware)

Dependency topology is shallow here (RTK exports carry already-computed values):
nearly all L2D read L1F directly; none depend on another L2D, so there is a
single tier. effective_check_point_count is the lone cross-point field.

CheckPoint-specific deltas vs the GCP build:
  - The primary quality signal is receiver sigma (L2D_CP_001 ratio to the survey
    accuracy target), not occupation coverage. sigma graceful-degradation state
    is captured by L2D_CP_002 sigma_available + L2D_CP_003 sigma_expected_for_device.
  - PDOP / sat-count / CN0 / fix-hold are read directly from the export (no NAV
    propagation), so they are L1F, not L2D - this stage has fewer derived fields
    than GCP's 23.
  - capture-vs-flight timing (L2D_CP_010..012) replaces GCP's occupation buffers.
  - L2D_CP_005 antenna_height_agreement is structurally UNCOMPUTABLE from RTK
    data: the device export carries no device-reported antenna height to compare
    the form height against (the spec formula references antenna_delta_h, which
    has no L1F_CP_* source field). Returns agreement N/A - SPEC AMENDMENT
    CANDIDATE.
  - effective_check_point_count is provisional here (= CHECK_POINT-role count)
    and recomputed authoritatively at Stage 3c against per_point_score, which is
    where the FLG_CP_004 FLOAT severity escalation (< 5) is applied.

kp_index (L2D_CP_013) is an external NOAA SWPC dependency: cache-only (no live
network call). A cache miss yields status=API_UNAVAILABLE so the Stage 3b
ionospheric indicator takes its dual-freq fallback path.

The only flag raised at this stage is FLG_CP_015 CP_NO_REPEATABILITY_CHECK
(advisory, zero scoring weight) which ALWAYS fires once per single-occupation
point in v1 (raised_at_stage=composite). Flags get _origin_stage="stage3a" +
_origin_point and surface in data.flags_raised_stage3a. No timestamps live in
the data block (determinism rule 3).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import stage1_inventory  # noqa: E402
import stage2_merge  # noqa: E402

STAGE = "stage3a_derived"

# ---- device-type sets that MIRROR the spec derived-field formula_expressions
#      (not invented thresholds). Declared as named constants + surfaced in
#      stage3a_meta.tuneables so a reviewer can see/challenge them in one place. ----

# L2D_CP_003 formula: "device_type IN {CB_X, AEROPOINT, DGPS}".
SIGMA_EXPECTED_DEVICE_TYPES = {"CB_X", "AEROPOINT", "DGPS"}
# L2D_CP_004 formula: "device_type IN {CB_X, AEROPOINT}".
ANTENNA_HEIGHT_AUTO_KNOWN_DEVICE_TYPES = {"CB_X", "AEROPOINT"}

# ---- genuine engineering tuneables (spec prose is qualitative) ----
# L2D_CP_008: spec "device_type supports tilt logging". CB_X is the tilt-comp
# type in our enum (spec also names Trimble R12+ / Emlid RS3+, which would map
# here if the device_type enum is later expanded).
TILT_CAPABLE_DEVICE_TYPES = {"CB_X"}
# L2D_CP_014: spec "device firmware/model exposes L1+L2 carrier phase". device_type
# is the deterministic proxy for the spec-listed (free-string) firmware/antenna
# inputs: CB_X / AEROPOINT are dual-frequency; DGPS is legacy single-frequency;
# OTHER is unknown (-> None, conservative).
DUAL_FREQ_DEVICE_TYPES = {"CB_X", "AEROPOINT"}
SINGLE_FREQ_DEVICE_TYPES = {"DGPS"}

# Normalised-string equality tolerance for antenna_height_agreement, kept for
# the day an RTK export does carry a device height (currently unreachable).
ANTENNA_HEIGHT_AGREEMENT_TOLERANCE_M = 0.005

IN_SCOPE_ROLE = "CHECK_POINT"

# ---- canonical source-field keys -------------------------------------------
SIGMA_H = "L1F_CP_003_position_sigma_horizontal_m"
ACCURACY_TARGET = "L1F_CP_033_accuracy_target_m"
DEVICE_TYPE = "L1F_CP_020_device_type"
ANTENNA_HEIGHT_M = "L1F_CP_024_antenna_height_m"
ANTENNA_MODEL_FORM = "L1F_CP_023_antenna_model"
ANTENNA_TYPE_DEV = "L1F_CP_011_antenna_type"
DEVICE_ID_FORM = "L1F_CP_021_device_id"
DEVICE_ID_DEV = "L1F_CP_012_device_id"
TILT_LOGGED = "L1F_CP_013_tilt_logged_deg"
NTRIP_MP = "L1F_CP_031_ntrip_mountpoint"
EXPECTED_MP = "L1F_CP_032_expected_mountpoint"
CAPTURE_UTC = "L1F_CP_014_capture_utc"
FLIGHT_START = "L1F_CP_037_flight_start_utc"
FLIGHT_END = "L1F_CP_038_flight_end_utc"
FIRMWARE = "L1F_CP_015_firmware_version"
SESSION_COMPLETED = "L1F_CP_018_session_completed_normally"
LOG_DOWNLOAD = "L1F_CP_016_raw_log_download_confirmed"
LOG_SIG_VALID = "L1F_CP_017_raw_log_signature_valid"
DEVICE_ROLE = "L1F_CP_022_device_role"


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

def _l2d_001_sigma_relative_to_target(sf: dict) -> dict:
    sig, target = sf.get(SIGMA_H), sf.get(ACCURACY_TARGET)
    ids = [SIGMA_H, ACCURACY_TARGET]
    if sig is None:
        return _field(None, ids, ["position_sigma_horizontal_m null - ratio uncomputable "
                                  "(L3I_CP_001 takes the sigma-absent path)."])
    if target is None or target <= 0:
        return _field(None, ids, [f"accuracy_target_m invalid ({target}) - ratio uncomputable."])
    return _field(round(sig / target, 4), ids,
                  ["Primary quality anchor: receiver sigma_h as a multiple of the survey "
                   "accuracy target. Banded at 1x/2x/5x by L3I_CP_001."])


def _l2d_002_sigma_available(sf: dict) -> dict:
    return _field(sf.get(SIGMA_H) is not None, [SIGMA_H],
                  ["True iff per-point sigma is present in the device export."])


def _l2d_003_sigma_expected_for_device(sf: dict) -> dict:
    dtype = sf.get(DEVICE_TYPE)
    ids = [DEVICE_TYPE]
    if dtype is None:
        return _field(None, ids, ["device_type null - expectation unknown."])
    return _field(dtype in SIGMA_EXPECTED_DEVICE_TYPES, ids,
                  ["Spec formula: device_type IN {CB_X, AEROPOINT, DGPS}. True+absent -> "
                   "Scenario 1 (re-export, FLG_CP_009); False+absent -> Scenario 2 (N/A, "
                   "weight redistributes)."])


def _l2d_004_antenna_height_auto_known(sf: dict) -> dict:
    dtype = sf.get(DEVICE_TYPE)
    ids = [DEVICE_TYPE]
    if dtype is None:
        return _field(None, ids, ["device_type null - auto-known status unknown."])
    return _field(dtype in ANTENNA_HEIGHT_AUTO_KNOWN_DEVICE_TYPES, ids,
                  ["Spec formula: device_type IN {CB_X, AEROPOINT}. True -> L3I_CP_005 "
                   "antenna_height_documented_score = 100 by definition (factory-known)."])


def _l2d_005_antenna_height_agreement(sf: dict) -> dict:
    """Form-height vs device-reported-height cross-check. RTK device exports carry
    NO device-reported antenna height (no antenna_delta_h L1F field), so this is
    structurally N/A. SPEC AMENDMENT CANDIDATE."""
    h_m = sf.get(ANTENNA_HEIGHT_M)
    ids = [ANTENNA_HEIGHT_M, ANTENNA_TYPE_DEV]
    return _field(
        {"agreement": None,
         "reason": "RTK device export carries no device-reported antenna height "
                   "(no antenna_delta_h source field) to cross-check the form height against",
         "form_height_m": h_m},
        ids,
        ["Spec formula references antenna_delta_h (device), which has no L1F_CP_* source "
         "field in the RTK chain - cross-check structurally uncomputable. agreement=N/A; "
         "L3I_CP_005 cannot reach its 'conflict with device-reported -> 55' band. SPEC "
         "AMENDMENT CANDIDATE."])


def _l2d_006_antenna_type_match(sf: dict) -> dict:
    model, a_type = sf.get(ANTENNA_MODEL_FORM), sf.get(ANTENNA_TYPE_DEV)
    ids = [ANTENNA_MODEL_FORM, ANTENNA_TYPE_DEV]
    if not model or not a_type:
        return _field(None, ids, ["antenna_model or antenna_type empty/null - cannot compare."])
    return _field(_normalize_str(model) == _normalize_str(a_type), ids,
                  ["Match -> L3I_CP_009 = 100; mismatch -> 60 + FLG_CP_030 (MEDIUM)."])


def _l2d_007_device_id_match(sf: dict) -> dict:
    form_id, dev_id = sf.get(DEVICE_ID_FORM), sf.get(DEVICE_ID_DEV)
    ids = [DEVICE_ID_FORM, DEVICE_ID_DEV]
    if not form_id or not dev_id:
        return _field(None, ids,
                      ["form or device device_id empty/null - L3I_CP_010 takes the missing "
                       "path (70 unconfirmed)."])
    return _field(_normalize_str(form_id) == _normalize_str(dev_id), ids,
                  ["Match -> L3I_CP_010 = 100; mismatch -> 60 + FLG_CP_016 (MEDIUM, "
                   "reviewer-blocking)."])


def _l2d_008_tilt_verifiable(sf: dict) -> dict:
    dtype = sf.get(DEVICE_TYPE)
    tilt = sf.get(TILT_LOGGED)
    ids = [DEVICE_TYPE, TILT_LOGGED]
    capable = dtype in TILT_CAPABLE_DEVICE_TYPES if dtype else False
    verifiable = bool(capable and tilt is not None)
    return _field(verifiable, ids,
                  ["device_type supports tilt logging AND tilt_logged_deg present. Verifiable "
                   "-> L3I_CP_006 scores logged tilt; not verifiable -> advisory "
                   "tilt_compensation_used path."])


def _l2d_009_mountpoint_match(sf: dict) -> dict:
    ntrip, expected = sf.get(NTRIP_MP), sf.get(EXPECTED_MP)
    ids = [NTRIP_MP, EXPECTED_MP]
    if not expected:
        return _field(None, ids, ["expected_mountpoint not declared - L3I_CP_008 takes the "
                                  "70 unconfirmed path."])
    if not ntrip:
        return _field(None, ids, ["ntrip_mountpoint null - cannot compare."])
    return _field(_normalize_str(ntrip) == _normalize_str(expected), ids,
                  ["Match -> L3I_CP_008 = 100; mismatch -> 40 + FLG_CP_022."])


def _l2d_010_capture_to_flight_delay_hours(sf: dict) -> dict:
    cap, fl_e = _parse_iso(sf.get(CAPTURE_UTC)), _parse_iso(sf.get(FLIGHT_END))
    ids = [CAPTURE_UTC, FLIGHT_START, FLIGHT_END]
    if cap is None or fl_e is None:
        return _field(None, ids, ["capture_utc or flight_end_utc null - delay uncomputable."])
    return _field(round((cap - fl_e).total_seconds() / 3600.0, 3), ids,
                  ["Positive = captured after flight end (preferred). 24-168h -> FLG_CP_023; "
                   ">168h -> FLG_CP_024."])


def _l2d_011_captured_before_flight(sf: dict) -> dict:
    cap, fl_s = _parse_iso(sf.get(CAPTURE_UTC)), _parse_iso(sf.get(FLIGHT_START))
    ids = [CAPTURE_UTC, FLIGHT_START]
    if cap is None or fl_s is None:
        return _field(None, ids, ["capture_utc or flight_start_utc null - cannot evaluate."])
    return _field(cap < fl_s, ids, ["True -> FLG_CP_025 CP_CAPTURED_BEFORE_FLIGHT."])


def _l2d_012_captured_during_flight(sf: dict) -> dict:
    cap = _parse_iso(sf.get(CAPTURE_UTC))
    fl_s, fl_e = _parse_iso(sf.get(FLIGHT_START)), _parse_iso(sf.get(FLIGHT_END))
    ids = [CAPTURE_UTC, FLIGHT_START, FLIGHT_END]
    if cap is None or fl_s is None or fl_e is None:
        return _field(None, ids, ["capture_utc / flight window null - cannot evaluate."])
    return _field(fl_s <= cap <= fl_e, ids,
                  ["True -> FLG_CP_026 CP_CAPTURED_DURING_FLIGHT (LOW workflow advisory)."])


def _l2d_013_kp_index(sf: dict, project_root: Path, options: dict) -> dict:
    """External NOAA SWPC dependency keyed on capture_utc date. Cache-only (no
    live network call). Cache miss -> API_UNAVAILABLE so L3I_CP_014 takes the
    dual-freq fallback path."""
    cap = _parse_iso(sf.get(CAPTURE_UTC))
    ids = [CAPTURE_UTC]
    if cap is None:
        return _field({"kp": None, "source": None, "status": "NO_CAPTURE_TIME"}, ids,
                      ["capture_utc null - no Kp lookup."])
    cache_dir = project_root / options.get("noaa_kp_cache_dir", "cache/noaa_swpc")
    cache_file = cache_dir / f"{cap.strftime('%Y-%m-%d')}.json"
    if cache_file.exists():
        try:
            import json as _json
            with cache_file.open("r", encoding="utf-8") as fh:
                payload = _json.load(fh)
            return _field({"kp": payload.get("kp"), "source": f"cache/{cache_file.name}",
                           "status": "OK"}, ids,
                          ["Read from local NOAA SWPC cache; no network call."])
        except (OSError, ValueError):
            pass
    return _field({"kp": None, "source": None, "status": "API_UNAVAILABLE"}, ids,
                  ["NOAA SWPC cache miss; no live API call (deterministic offline run). "
                   "L3I_CP_014 takes the dual-freq fallback path."])


def _l2d_014_dual_freq_available(sf: dict) -> dict:
    dtype = sf.get(DEVICE_TYPE)
    ids = [FIRMWARE, ANTENNA_TYPE_DEV]
    if dtype is None:
        return _field(None, ids, ["device_type null - dual-freq capability unknown."])
    if dtype in DUAL_FREQ_DEVICE_TYPES:
        val = True
    elif dtype in SINGLE_FREQ_DEVICE_TYPES:
        val = False
    else:
        return _field(None, ids, [f"device_type={dtype}: dual-freq capability unknown "
                                  "(OTHER) - L3I_CP_014 treats as single-freq-risk if Kp high."])
    return _field(val, ids,
                  ["device_type is the deterministic proxy for the spec's firmware/antenna "
                   "inputs (CB_X/AEROPOINT dual; DGPS single). Mitigates ionospheric impact "
                   "when True."])


def _l2d_015_session_integrity_ok(sf: dict) -> dict:
    """composite_scoring, device-type-aware (spec L2D_CP_015):
      CB_X / DGPS / AEROPOINT: completed_normally AND signature valid (if available)
      OTHER:                   signature N/A; integrity from download confirmation."""
    dtype = sf.get(DEVICE_TYPE)
    completed = sf.get(SESSION_COMPLETED)
    download = sf.get(LOG_DOWNLOAD)
    sig = sf.get(LOG_SIG_VALID)
    ids = [SESSION_COMPLETED, LOG_DOWNLOAD, LOG_SIG_VALID, DEVICE_TYPE]

    if dtype in ("CB_X", "DGPS", "AEROPOINT"):
        if completed is None:
            return _field(None, ids,
                          [f"device_type={dtype}: session_completed_normally null - "
                           "UNCONFIRMED (not False)."])
        parts = {"completed_normally": bool(completed)}
        if sig is not None:  # "signature valid (if available)"
            parts["signature_valid"] = bool(sig)
        return _field({"ok": all(parts.values()), "components": parts,
                       "branch": "completed_and_signature_if_available"}, ids)
    if dtype == "OTHER":
        if download is None:
            return _field(None, ids,
                          ["device_type=OTHER: signature N/A and download confirmation null "
                           "- UNCONFIRMED."])
        return _field({"ok": bool(download), "components": {"download_confirmed": bool(download)},
                       "branch": "other_download_only"}, ids)
    return _field(None, ids, [f"device_type={dtype!r} unrecognised - integrity UNCONFIRMED."])


# ---- flag emission ---------------------------------------------------------

def _add_flag(flags: list[dict], flag_index: dict, flag_id: str,
              condition_value: Any, derived_field: str | None, point_id: str | None) -> None:
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

def _derive_point(point: dict, project_root: Path, options: dict,
                  kind_by_key: dict, flag_index: dict) -> dict:
    sf = point.get("source_fields", {})
    point_id = point["point_id"]
    derived: dict[str, Any] = {}

    derived["L2D_CP_001_sigma_relative_to_target"] = _l2d_001_sigma_relative_to_target(sf)
    derived["L2D_CP_002_sigma_available"] = _l2d_002_sigma_available(sf)
    derived["L2D_CP_003_sigma_expected_for_device"] = _l2d_003_sigma_expected_for_device(sf)
    derived["L2D_CP_004_antenna_height_auto_known"] = _l2d_004_antenna_height_auto_known(sf)
    derived["L2D_CP_005_antenna_height_agreement"] = _l2d_005_antenna_height_agreement(sf)
    derived["L2D_CP_006_antenna_type_match"] = _l2d_006_antenna_type_match(sf)
    derived["L2D_CP_007_device_id_match"] = _l2d_007_device_id_match(sf)
    derived["L2D_CP_008_tilt_verifiable"] = _l2d_008_tilt_verifiable(sf)
    derived["L2D_CP_009_mountpoint_match"] = _l2d_009_mountpoint_match(sf)
    derived["L2D_CP_010_capture_to_flight_delay_hours"] = _l2d_010_capture_to_flight_delay_hours(sf)
    derived["L2D_CP_011_captured_before_flight"] = _l2d_011_captured_before_flight(sf)
    derived["L2D_CP_012_captured_during_flight"] = _l2d_012_captured_during_flight(sf)
    derived["L2D_CP_013_kp_index"] = _l2d_013_kp_index(sf, project_root, options)
    derived["L2D_CP_014_dual_freq_available"] = _l2d_014_dual_freq_available(sf)
    derived["L2D_CP_015_session_integrity_ok"] = _l2d_015_session_integrity_ok(sf)

    for key, fobj in derived.items():
        fobj["kind"] = kind_by_key.get(key, "scoring")

    # FLG_CP_015 CP_NO_REPEATABILITY_CHECK: always fires per single-occupation
    # point in v1 (advisory, zero scoring weight). raised_at_stage=composite.
    point_flags: list[dict] = []
    if "FLG_CP_015" in flag_index:
        _add_flag(point_flags, flag_index, "FLG_CP_015",
                  {"single_occupation": True}, None, point_id)

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
    total_expected = spec["_meta"]["counts"]["derived_fields"]
    per_point_expected = total_expected - 1  # L2D_CP_016 is survey-level

    point_records: list[dict] = []
    all_flags: list[dict] = []
    notes: list[str] = []

    for point in stage2_data.get("points", []):
        rec = _derive_point(point, project_root, options, kind_by_key, flag_index)
        point_records.append(rec)
        all_flags.extend(rec["flags_raised_stage3a_point"])
        if len(rec["derived_fields"]) != per_point_expected:
            notes.append(f"{rec['point_id']}: produced {len(rec['derived_fields'])} "
                         f"per-point L2D fields, expected {per_point_expected}.")

    # ---- survey-level L2D_CP_016 effective_check_point_count (PROVISIONAL) ----
    cp_points = [r for r in point_records if r["device_role"] == IN_SCOPE_ROLE]
    survey_derived = {
        "L2D_CP_016_effective_check_point_count": _field(
            len(cp_points), [DEVICE_ROLE],
            ["PROVISIONAL = count of CHECK_POINT-role points. Spec definition is 'points "
             "where per_point_score > 0'; per_point_score exists only at Stage 3c, where the "
             "authoritative value is recomputed and the FLG_CP_004 FLOAT severity escalation "
             "(< 5) is applied. A CHECK_POINT point has per_point_score = 0 only if all three "
             "blocks gate to 0; the ENV block has no gate, so provisional == final except in "
             "pathological cases."]),
    }
    survey_derived["L2D_CP_016_effective_check_point_count"]["kind"] = \
        kind_by_key.get("L2D_CP_016_effective_check_point_count", "scoring")

    counts_by_kind: dict[str, int] = {}
    if point_records:
        for fobj in point_records[0]["derived_fields"].values():
            counts_by_kind[fobj["kind"]] = counts_by_kind.get(fobj["kind"], 0) + 1
    for fobj in survey_derived.values():
        counts_by_kind[fobj["kind"]] = counts_by_kind.get(fobj["kind"], 0) + 1

    return {
        "points": point_records,
        "survey_derived": survey_derived,
        "flags_raised_stage3a": all_flags,
        "stage3a_notes": notes,
        "stage3a_meta": {
            "total_derived_field_count": total_expected,
            "per_point_field_count": per_point_expected,
            "survey_level_field_count": len(survey_derived),
            "point_count": len(point_records),
            "check_point_role_count": len(cp_points),
            "counts_by_kind_total": dict(sorted(counts_by_kind.items())),
            "tuneables": {
                "SIGMA_EXPECTED_DEVICE_TYPES": sorted(SIGMA_EXPECTED_DEVICE_TYPES),
                "ANTENNA_HEIGHT_AUTO_KNOWN_DEVICE_TYPES": sorted(ANTENNA_HEIGHT_AUTO_KNOWN_DEVICE_TYPES),
                "TILT_CAPABLE_DEVICE_TYPES": sorted(TILT_CAPABLE_DEVICE_TYPES),
                "DUAL_FREQ_DEVICE_TYPES": sorted(DUAL_FREQ_DEVICE_TYPES),
                "SINGLE_FREQ_DEVICE_TYPES": sorted(SINGLE_FREQ_DEVICE_TYPES),
                "ANTENNA_HEIGHT_AGREEMENT_TOLERANCE_M": ANTENNA_HEIGHT_AGREEMENT_TOLERANCE_M,
            },
            "spec_amendment_candidates": [
                "L2D_CP_005 antenna_height_agreement: spec formula references antenna_delta_h "
                "(device-reported height), which has no L1F_CP_* source field in the RTK chain; "
                "cross-check is structurally N/A.",
            ],
        },
    }


def print_summary(data: dict) -> None:
    mm = data["stage3a_meta"]
    print(f"  derived fields: {mm['per_point_field_count']}/point + "
          f"{mm['survey_level_field_count']} survey-level = {mm['total_derived_field_count']}  "
          f"(kinds: {mm['counts_by_kind_total']})  points: {mm['point_count']}")
    for p in data["points"]:
        d = p["derived_fields"]
        ratio = d["L2D_CP_001_sigma_relative_to_target"]["value"]
        integ = d["L2D_CP_015_session_integrity_ok"]["value"]
        integ_ok = integ.get("ok") if isinstance(integ, dict) else integ
        kp = d["L2D_CP_013_kp_index"]["value"]
        kpv = kp.get("kp") if isinstance(kp, dict) else None
        kps = kp.get("status") if isinstance(kp, dict) else None
        print(f"    - {p['point_id']}: sigma_ratio={ratio} integrity_ok={integ_ok} "
              f"kp={kpv}({kps}) flags={len(p['flags_raised_stage3a_point'])}")
    eff = data["survey_derived"]["L2D_CP_016_effective_check_point_count"]["value"]
    print(f"  survey: effective_check_point_count (provisional) = {eff}")
    print(f"  flags raised at Stage 3a: {len(data['flags_raised_stage3a'])}")
    for fl in data["flags_raised_stage3a"]:
        print(f"    FLAG  [{fl['_origin_point']}] {fl['flag_id']} {fl['flag_name']} ({fl['severity']})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check Point PPK Stage 3a derived fields")
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
