#!/usr/bin/env python3
"""parse_form.py - SRC_CP_FORM parser (per point / per occupation).

Emits L1F_CP_020..038 (19 source fields) for the per-point operator field form
(cp_user_input.json). The form is the safeguard against the #10 antenna-height
blunder and carries device identity, antenna setup, baseline, NTRIP, target/mark
metadata, the survey accuracy target, and the drone flight window.

Key spec semantics preserved / CheckPoint-specific behaviour:
- device_type (L1F_CP_020, enum CB_X/AEROPOINT/DGPS/OTHER) drives the oplog
  presence branch (Stage 2), antenna_height_auto_known (Stage 3a), sigma
  expected-for-device, and tilt verifiable-vs-advisory logic.
- device_role (L1F_CP_022, enum GCP/CHECK_POINT) - this score aggregates only
  CHECK_POINT-role points; GCP-role points are excluded (owned by gcp_score).
- device_id (L1F_CP_021) is cross-checked vs RTK-export device_id downstream
  (L2D_CP_007 -> L3I_CP_010; mismatch -> FLG_CP_016, MEDIUM reviewer-blocking).
- antenna_model (L1F_CP_023) is cross-checked vs RTK-export antenna_type
  (L2D_CP_006 -> L3I_CP_009; mismatch -> FLG_CP_030).
- antenna_height_m (L1F_CP_024): for CB_X / AEROPOINT it is factory-known
  (antenna_height_auto_known True at Stage 3a -> L3I_CP_005 = 100). For
  DGPS / OTHER it is operator-measured; 'ft' normalized to metres, raw kept in
  parser_meta.raw_form_values.
- antenna_measurement_type / measured_to_reference / height_measured_count are
  only meaningful for DGPS / OTHER (N/A for CB_X / AEROPOINT).
- tilt_compensation_used (L1F_CP_029, bool) and mark_integrity_confirmed
  (L1F_CP_035, bool) are operator-declarative ADVISORY fields - emitted but
  not scored (mark_integrity_confirmed has no indicator at all; tilt boolean is
  advisory only on non-tilt-comp gear). mark_photo_captured (L1F_CP_036) drives
  the advisory FLG_CP_029 CP_NO_MARK_PHOTO.
- accuracy_target_m (L1F_CP_033) is the denominator for the sigma anchor
  (L2D_CP_001 sigma_relative_to_target).
- flight_start_utc / flight_end_utc (L1F_CP_037/038) feed the capture-vs-flight
  timing derived fields (L2D_CP_010..012 -> timing flags).
- All enums strictly validated; out-of-enum becomes null with a note.
- The parser raises NO spec flags (all CP flags fire at Stage 3a/3b/3c/3d).

parse(point_folder, project_root) -> {"fields", "parser_meta"}.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

PARSER_ID = "parse_form"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_CP_FORM"

FORM_INSTANCE_FILENAMES = ("cp_user_input.json", "user_input.json")

ENUM_DEVICE_TYPE = ("CB_X", "AEROPOINT", "DGPS", "OTHER")
ENUM_DEVICE_ROLE = ("GCP", "CHECK_POINT")
ENUM_ANTENNA_HEIGHT_UNITS = ("m", "ft")
ENUM_ANTENNA_MEASUREMENT_TYPE = ("VERTICAL", "SLANT")
ENUM_MEASURED_TO_REFERENCE = ("ARP", "SLANT_MARK", "MOUNT_BOTTOM", "OTHER")
ENUM_TARGET_TYPE = ("printed_paper", "painted", "cb_x_surface",
                    "aeropoint_surface", "dgps_peg", "other")

FT_TO_M = 0.3048

# L1F field id -> (json_key, kind). kind drives the validator.
L1F_SPEC = {
    "L1F_CP_020_device_type":              ("device_type", "enum_device_type"),
    "L1F_CP_021_device_id":                ("device_id", "str"),
    "L1F_CP_022_device_role":              ("device_role", "enum_device_role"),
    "L1F_CP_023_antenna_model":            ("antenna_model", "str"),
    "L1F_CP_024_antenna_height_m":         ("antenna_height_m", "height"),
    "L1F_CP_025_antenna_height_units":     ("antenna_height_units", "enum_units"),
    "L1F_CP_026_antenna_measurement_type": ("antenna_measurement_type", "enum_meas_type"),
    "L1F_CP_027_measured_to_reference":    ("measured_to_reference", "enum_meas_ref"),
    "L1F_CP_028_height_measured_count":    ("height_measured_count", "int"),
    "L1F_CP_029_tilt_compensation_used":   ("tilt_compensation_used", "bool"),
    "L1F_CP_030_baseline_length_km":       ("baseline_length_km", "number"),
    "L1F_CP_031_ntrip_mountpoint":         ("ntrip_mountpoint", "str"),
    "L1F_CP_032_expected_mountpoint":      ("expected_mountpoint", "str"),
    "L1F_CP_033_accuracy_target_m":        ("accuracy_target_m", "number"),
    "L1F_CP_034_target_type":              ("target_type", "enum_target_type"),
    "L1F_CP_035_mark_integrity_confirmed": ("mark_integrity_confirmed", "bool"),
    "L1F_CP_036_mark_photo_captured":      ("mark_photo_captured", "bool"),
    "L1F_CP_037_flight_start_utc":         ("flight_start_utc", "iso"),
    "L1F_CP_038_flight_end_utc":           ("flight_end_utc", "iso"),
}
_REQUIRED = ("device_type", "device_id", "device_role", "antenna_model",
             "antenna_height_m", "antenna_height_units", "accuracy_target_m")

_ENUM_BY_KIND = {
    "enum_device_type": ENUM_DEVICE_TYPE,
    "enum_device_role": ENUM_DEVICE_ROLE,
    "enum_units": ENUM_ANTENNA_HEIGHT_UNITS,
    "enum_meas_type": ENUM_ANTENNA_MEASUREMENT_TYPE,
    "enum_meas_ref": ENUM_MEASURED_TO_REFERENCE,
    "enum_target_type": ENUM_TARGET_TYPE,
}


def _empty_fields() -> dict[str, Any]:
    return {k: None for k in L1F_SPEC}


# ---- validators (null-preserving) ------------------------------------------

def _v_str(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, str):
        notes.append(f"{name} not a string ({type(value).__name__}={value!r}) - null.")
        return None
    s = value.strip()
    return s if s else None


def _v_bool(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, bool):
        notes.append(f"{name} not a bool ({type(value).__name__}={value!r}) - null.")
        return None
    return value


def _v_enum(name, value, allowed, notes):
    if value is None:
        return None
    if not isinstance(value, str):
        notes.append(f"{name} not a string enum ({type(value).__name__}={value!r}) - null.")
        return None
    norm = value.strip()
    if norm not in allowed:
        notes.append(f"{name}={value!r} not in {list(allowed)} - coerced to null.")
        return None
    return norm


def _v_number(name, value, notes, min_v=0.0, max_v=None):
    if value is None:
        return None
    if isinstance(value, bool):
        notes.append(f"{name} is a bool, expected number - null.")
        return None
    if not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (ValueError, TypeError):
            notes.append(f"{name} not coercible to number ({value!r}) - null.")
            return None
    fv = float(value)
    if fv < min_v:
        notes.append(f"{name}={fv} below minimum {min_v} - kept as-is.")
    if max_v is not None and fv > max_v:
        notes.append(f"{name}={fv} above maximum {max_v} - kept as-is.")
    return fv


def _v_int(name, value, notes, min_v=1):
    if value is None:
        return None
    if isinstance(value, bool):
        notes.append(f"{name} is a bool, expected int - null.")
        return None
    if not isinstance(value, int):
        try:
            value = int(value)
        except (ValueError, TypeError):
            notes.append(f"{name} not coercible to int ({value!r}) - null.")
            return None
    if value < min_v:
        notes.append(f"{name}={value} below minimum {min_v} - kept as-is.")
    return value


def _v_iso(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, str):
        notes.append(f"{name} not a string ({type(value).__name__}={value!r}) - null.")
        return None
    s = value.strip()
    if not s:
        return None
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        notes.append(f"{name}={value!r} not ISO-8601 parseable - kept as-is.")
    return s


def _discover_instance(point_folder: Path) -> Path | None:
    for name in FORM_INSTANCE_FILENAMES:
        candidate = point_folder / name
        if candidate.exists():
            return candidate
    return None


def _result(fields, field_sources, notes, flags_raised, source_file_name,
            instance_found, validation, raw_form_values):
    return {
        "fields": dict(sorted(fields.items())),
        "parser_meta": {
            "parser_id": PARSER_ID,
            "parser_version": PARSER_VERSION,
            "source_file_id": SOURCE_FILE_ID,
            "source_file_name": source_file_name,
            "instance_found": instance_found,
            "fields_provided": sorted(fields.keys()),
            "field_sources": field_sources,
            "validation": validation,
            "raw_form_values": raw_form_values,
            "notes": notes,
            "flags_raised": flags_raised,
        },
    }


_ABSENCE_NOTE = (
    "No User Input form instance found in '{where}'. All L1F_CP_020..038 kept null. "
    "Deterministic consequences (per spec): device_type null -> oplog branch + "
    "antenna_height_auto_known + sigma_expected_for_device cannot be derived; "
    "antenna_height_m null AND auto_known=False -> Stage 3c L3I_CP_005 internal gate "
    "trips FLG_CP_003 CP_POINT_ANTENNA_HEIGHT_MISSING (CATASTROPHIC); accuracy_target_m "
    "null -> sigma_relative_to_target uncomputable; device_role null -> CHECK_POINT "
    "membership unconfirmed."
)


def parse(point_folder: Path, project_root: Path | None = None) -> dict[str, Any]:
    notes: list[str] = []
    flags_raised: list[dict] = []

    point_folder = Path(point_folder)
    instance_path = _discover_instance(point_folder)
    fields = _empty_fields()
    field_sources = {k: "absent_form_null" for k in fields}
    raw_form_values: dict[str, Any] = {}
    empty_validation = {"required_present": [], "required_missing": list(_REQUIRED),
                        "optional_present": [], "extra_keys": []}

    if instance_path is None:
        notes.append(_ABSENCE_NOTE.format(where=point_folder.name or str(point_folder)))
        return _result(fields, field_sources, notes, flags_raised, None, False,
                       empty_validation, raw_form_values)

    try:
        with instance_path.open("r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        notes.append(f"Form {instance_path.name} could not be parsed ({exc}); treating as absent.")
        return _result(fields, field_sources, notes, flags_raised, instance_path.name,
                       False, empty_validation, raw_form_values)

    if not isinstance(doc, dict):
        notes.append(f"Form root in {instance_path.name} is not a JSON object "
                     f"({type(doc).__name__}); treating as absent.")
        return _result(fields, field_sources, notes, flags_raised, instance_path.name,
                       False, empty_validation, raw_form_values)

    if doc.get("_status") == "PLACEHOLDER":
        notes.append(
            f"{instance_path.name} is a PLACEHOLDER (Section 8 lifecycle) - operator-pending; "
            "replace before a production survey. Stage 1 raises PLACEHOLDER_INPUTS_DETECTED.")

    all_keys = {jk for jk, _ in L1F_SPEC.values()}
    required_present = [k for k in _REQUIRED if k in doc]
    required_missing = [k for k in _REQUIRED if k not in doc]
    optional_present = sorted(k for k in all_keys if k not in _REQUIRED and k in doc)
    extra_keys = sorted(k for k in doc if k not in all_keys and not k.startswith("_"))
    if required_missing:
        notes.append(f"Required form fields absent from {instance_path.name}: {required_missing}. "
                     "Each kept null per spec contract.")
    if extra_keys:
        notes.append(f"Extra keys present (ignored): {extra_keys}.")
    raw_form_values = {jk: doc.get(jk) for jk in all_keys if jk in doc}

    # ---- per-field extraction ----
    height_units_key = "L1F_CP_025_antenna_height_units"
    for l1f, (jk, kind) in L1F_SPEC.items():
        raw = doc.get(jk)
        if kind == "height":
            continue  # handled after units known
        if kind.startswith("enum_"):
            val = _v_enum(jk, raw, _ENUM_BY_KIND[kind], notes)
        elif kind == "str":
            val = _v_str(jk, raw, notes)
        elif kind == "bool":
            val = _v_bool(jk, raw, notes)
        elif kind == "number":
            val = _v_number(jk, raw, notes)
        elif kind == "int":
            val = _v_int(jk, raw, notes)
        elif kind == "iso":
            val = _v_iso(jk, raw, notes)
        else:
            val = raw
        fields[l1f] = val
        if jk in doc:
            field_sources[l1f] = "form"

    # antenna_height_m with unit normalization (ft -> m)
    raw_height = doc.get("antenna_height_m")
    height_validated = _v_number("antenna_height_m", raw_height, notes, 0.0, 10.0)
    units = fields[height_units_key]
    if height_validated is not None and units == "ft":
        height_m = round(height_validated * FT_TO_M, 6)
        notes.append(f"antenna_height_m={raw_height}ft -> normalized to {height_m}m "
                     "(stored in metres; raw in parser_meta.raw_form_values).")
        fields["L1F_CP_024_antenna_height_m"] = height_m
    else:
        fields["L1F_CP_024_antenna_height_m"] = height_validated
    if "antenna_height_m" in doc:
        field_sources["L1F_CP_024_antenna_height_m"] = "form"

    # ---- advisory / consistency notes (NOT flags - those fire downstream) ----
    if fields["L1F_CP_022_device_role"] == "GCP":
        notes.append("device_role=GCP - excluded from check_point_score aggregation "
                     "(owned by gcp_score).")
    if fields["L1F_CP_036_mark_photo_captured"] is False:
        notes.append("mark_photo_captured=False -> Stage 3b advisory FLG_CP_029 CP_NO_MARK_PHOTO.")
    dt = fields["L1F_CP_020_device_type"]
    if dt in ("CB_X", "AEROPOINT"):
        if fields["L1F_CP_026_antenna_measurement_type"] is not None or \
           fields["L1F_CP_027_measured_to_reference"] is not None or \
           fields["L1F_CP_028_height_measured_count"] is not None:
            notes.append(f"device_type={dt}: antenna measurement detail fields are N/A "
                         "(factory-known height); values present but not scored.")

    validation = {"required_present": required_present, "required_missing": required_missing,
                  "optional_present": optional_present, "extra_keys": extra_keys}
    return _result(fields, field_sources, notes, flags_raised, instance_path.name,
                   True, validation, raw_form_values)


def main(argv=None) -> int:
    import argparse
    import json as _j
    parser = argparse.ArgumentParser(description="Parse a Check Point operator field form")
    parser.add_argument("point_folder")
    args = parser.parse_args(argv)
    out = parse(Path(args.point_folder), Path("."))
    print(_j.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
