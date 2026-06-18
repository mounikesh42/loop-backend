#!/usr/bin/env python3
"""parse_user_input.py — SRC_GCP_FORM parser (per point / per occupation).

Emits L1F_GCP_026..040 (15 source fields) for the operator-entered field form
(one record per occupation). The form is the safeguard against the #6
antenna-height blunder.

Key spec semantics preserved / GCP-specific behaviour:
- device_type (L1F_GCP_026, enum CB_X/AEROPOINT/DGPS/OTHER) drives the oplog
  branch and the antenna-height auto-fill logic. device_type=OTHER → downstream
  FLG_GCP_013 UNRECOGNIZED_DEVICE_TYPE (advisory, threshold).
- device_role (L1F_GCP_028, enum GCP/CHECK_POINT) is locked to GCP for this
  score; CHECK_POINT points are EXCLUDED from gcp_score aggregation (Stage 3c)
  and routed to the future check_point_score.
- device_id (L1F_GCP_027) is cross-checked vs RINEX device_id downstream
  (L2D_GCP_019 → L3I_GCP_006; mismatch → FLG_GCP_010, HIGH reviewer-blocking).
- antenna_model (L1F_GCP_029) is cross-checked vs RINEX antenna_type
  (L2D_GCP_017 → L3I_GCP_007).
- antenna_height_m (L1F_GCP_030): for CB_X / AEROPOINT it is factory-known and
  auto-filled (antenna_height_auto_known→True derived in Stage 3a; L3I_GCP_005
  = 100). For DGPS it is operator-measured and cross-checked vs RINEX
  antenna_delta_h (L2D_GCP_018). 'ft' is normalized to metres; raw kept in
  parser_meta.raw_form_values.
- antenna_measurement_type / measured_to_reference / height_measured_count are
  only meaningful for DGPS; for CB_X / AEROPOINT they are expected N/A.
- target_size_m (L1F_GCP_037) and target_placed_confirmed (L1F_GCP_038) are
  captured here but CONSUMED BY pre_processing_score (#11 visibility), NOT by
  gcp_score. They are emitted as metadata.
- flight_start_utc / flight_end_utc (L1F_GCP_039/040) feed occupation coverage
  + buffers (L2D_GCP_001/002/003 → L3I_GCP_001). coverage_ratio < 1.0 is a
  CRITICAL internal gate (FLG_GCP_003 GCP_POINT_FLIGHT_GAP).
- All enums strictly validated; out-of-enum becomes null with a note.
- The parser raises NO spec flags (setup/coverage gates are Stage 3c;
  device-id/device-type flags are Stage 3b threshold).

The form instance, when present, lives at <point_folder>/user_input.json
(per point), alongside that point's hardware.json / oplog.json.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PARSER_ID = "parse_user_input"
PARSER_VERSION = "1.0"  # GCP: per-point; adds device_type/role/id + target metadata
SOURCE_FILE_ID = "SRC_GCP_FORM"

# Conventional per-point instance filenames (preferred during discovery).
FORM_INSTANCE_FILENAMES = ("user_input.json", "gcp_user_input.json")

FORM_REQUIRED_FIELDS = (
    "device_type",
    "device_id",
    "device_role",
    "antenna_model",
    "antenna_height_m",
    "antenna_height_units",
    "flight_start_utc",
    "flight_end_utc",
)
FORM_OPTIONAL_FIELDS = (
    "antenna_measurement_type",
    "measured_to_reference",
    "height_measured_count",
    "pole_tripod_fixed_height",
    "target_type",
    "target_size_m",
    "target_placed_confirmed",
)

ENUM_DEVICE_TYPE = ("CB_X", "AEROPOINT", "DGPS", "OTHER")
ENUM_DEVICE_ROLE = ("GCP", "CHECK_POINT")
ENUM_ANTENNA_HEIGHT_UNITS = ("m", "ft")
ENUM_ANTENNA_MEASUREMENT_TYPE = ("VERTICAL", "SLANT")
ENUM_MEASURED_TO_REFERENCE = ("ARP", "SLANT_MARK", "MOUNT_BOTTOM", "OTHER")
ENUM_TARGET_TYPE = (
    "printed_paper", "painted", "aeropoint_surface", "cb_x_surface", "dgps_peg", "other"
)

# DGPS-only antenna detail fields (N/A for CB_X / AEROPOINT / OTHER).
DGPS_ONLY_ANTENNA_FIELDS = (
    "antenna_measurement_type",
    "measured_to_reference",
    "height_measured_count",
)

L1F_KEY_MAP = {
    "L1F_GCP_026_device_type":              "device_type",
    "L1F_GCP_027_device_id":                "device_id",
    "L1F_GCP_028_device_role":              "device_role",
    "L1F_GCP_029_antenna_model":            "antenna_model",
    "L1F_GCP_030_antenna_height_m":         "antenna_height_m",
    "L1F_GCP_031_antenna_height_units":     "antenna_height_units",
    "L1F_GCP_032_antenna_measurement_type": "antenna_measurement_type",
    "L1F_GCP_033_measured_to_reference":    "measured_to_reference",
    "L1F_GCP_034_height_measured_count":    "height_measured_count",
    "L1F_GCP_035_pole_tripod_fixed_height": "pole_tripod_fixed_height",
    "L1F_GCP_036_target_type":              "target_type",
    "L1F_GCP_037_target_size_m":            "target_size_m",
    "L1F_GCP_038_target_placed_confirmed":  "target_placed_confirmed",
    "L1F_GCP_039_flight_start_utc":         "flight_start_utc",
    "L1F_GCP_040_flight_end_utc":           "flight_end_utc",
}

FT_TO_M = 0.3048


# ---- helpers ---------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


def _validate_string(name: str, value: Any, notes: list[str], min_length: int = 1) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        notes.append(f"{name} not a string ({type(value).__name__}={value!r}) — coerced to null.")
        return None
    s = value.strip()
    if len(s) < min_length:
        notes.append(f"{name}={value!r} shorter than minLength {min_length} — coerced to null.")
        return None
    return s


def _validate_bool(name: str, value: Any, notes: list[str]) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        notes.append(f"{name} not a bool ({type(value).__name__}={value!r}) — coerced to null.")
        return None
    return value


def _validate_int_in_range(
    name: str, value: Any, min_v: int, max_v: int | None, notes: list[str]
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        notes.append(f"{name} is a bool, expected int — coerced to null.")
        return None
    if not isinstance(value, int):
        try:
            value = int(value)
        except (ValueError, TypeError):
            notes.append(f"{name} not coercible to int ({value!r}) — kept as null.")
            return None
    if value < min_v:
        notes.append(f"{name}={value} below minimum {min_v}.")
    elif max_v is not None and value > max_v:
        notes.append(f"{name}={value} above maximum {max_v}.")
    return value


def _validate_number_in_range(
    name: str, value: Any, min_v: float, max_v: float, notes: list[str],
    min_inclusive: bool = False,
) -> float | None:
    # min_inclusive=True permits value == min_v (integrated antennas legitimately
    # have a 0.0 vertical offset); the default keeps the exclusive lower bound used
    # for pole/tripod heights and target sizes (a 0-length pole/target is nonsense).
    if value is None:
        return None
    if isinstance(value, bool):
        notes.append(f"{name} is a bool, expected number — coerced to null.")
        return None
    if not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (ValueError, TypeError):
            notes.append(f"{name} not coercible to number ({value!r}) — kept as null.")
            return None
    fv = float(value)
    if min_inclusive:
        if fv < min_v:
            notes.append(f"{name}={fv} < minimum {min_v} — kept as-is.")
    elif fv <= min_v:
        notes.append(f"{name}={fv} <= exclusive minimum {min_v} — kept as-is.")
    if fv > max_v:
        notes.append(f"{name}={fv} > maximum {max_v} — kept as-is.")
    return fv


def _validate_enum(name: str, value: Any, allowed: tuple[str, ...], notes: list[str]) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        notes.append(f"{name}={value!r} not in {list(allowed)} — coerced to null.")
        return None
    return value


def _normalize_iso_utc(name: str, value: Any, notes: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        notes.append(f"{name} not a string ({type(value).__name__}={value!r}) — coerced to null.")
        return None
    s = value.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        notes.append(f"{name}={value!r} not ISO-8601 parseable — kept as-is.")
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return _iso(dt)


# ---- instance discovery ----------------------------------------------------

# Form-unique discriminators. device_id is intentionally EXCLUDED (it also
# appears in hardware.json), so the form is never confused with the sibling
# hardware/oplog JSONs in a shared point folder.
_FORM_DISCRIMINATORS = ("device_type", "device_role", "antenna_model", "target_type", "flight_start_utc")


def _looks_like_form(doc: Any) -> bool:
    if not isinstance(doc, dict):
        return False
    if "$schema" in doc and "properties" in doc:
        return False
    return any(k in doc for k in _FORM_DISCRIMINATORS)


def _discover_instance(point_folder: Path) -> Path | None:
    if not point_folder.exists():
        return None

    for fname in FORM_INSTANCE_FILENAMES:
        preferred = point_folder / fname
        if preferred.is_file():
            try:
                with preferred.open("r", encoding="utf-8") as fh:
                    doc = json.load(fh)
                if _looks_like_form(doc):
                    return preferred
            except (OSError, json.JSONDecodeError):
                return preferred  # exists but unreadable; let parse() report it

    candidates: list[Path] = []
    for p in point_folder.iterdir():
        if not p.is_file() or p.suffix.lower() != ".json" or p.name.startswith("."):
            continue
        if p.name in FORM_INSTANCE_FILENAMES:
            continue
        try:
            with p.open("r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if _looks_like_form(doc):
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


# ---- main parse() ----------------------------------------------------------

def _empty_fields() -> dict[str, Any]:
    return {k: None for k in L1F_KEY_MAP}


_ABSENCE_NOTE = (
    "No User Input form instance found in '{where}'. All L1F_GCP_026..040 kept null. "
    "Deterministic consequences (per spec):\n"
    "  • device_type null → oplog branch + antenna_height_auto_known cannot be derived.\n"
    "  • antenna_height_m null AND antenna_height_auto_known=False → Stage 3c L3I_GCP_005 "
    "internal gate trips FLG_GCP_002 GCP_POINT_ANTENNA_HEIGHT_MISSING (CRITICAL); setup sub-score = 0.\n"
    "  • flight_start/end null → occupation_coverage_ratio < 1.0 → Stage 3c L3I_GCP_001 "
    "internal gate trips FLG_GCP_003 GCP_POINT_FLIGHT_GAP (CRITICAL); completeness sub-score = 0.\n"
    "  • If every GCP-role point gates this way → Stage 3d FLG_GCP_001 GCP_CRITICAL_FAILURE; gcp_score = 0.\n"
    "  • device_role null → GCP membership unconfirmed; zero GCP-role points → FLG_GCP_012 NO_DESIGNATED_GCPS."
)


def parse(point_folder: Path, project_root: Path) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    notes: list[str] = []
    flags_raised: list[dict] = []  # parser raises none; flags fire at Stage 3b/3c/3d

    instance_path = _discover_instance(point_folder)
    fields = _empty_fields()
    field_sources = {k: "absent_form_null" for k in fields}
    raw_form_values: dict[str, Any] = {}

    empty_validation = {
        "required_present": [],
        "required_missing": list(FORM_REQUIRED_FIELDS),
        "optional_present": [],
        "extra_keys": [],
    }

    if instance_path is None:
        notes.append(_ABSENCE_NOTE.format(where=point_folder.name or str(point_folder)))
        return _result(
            started_at, fields, field_sources, notes, flags_raised,
            None, False, empty_validation, raw_form_values,
        )

    try:
        with instance_path.open("r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        notes.append(
            f"User Input form {instance_path.name} could not be parsed ({exc}); "
            "treating as absent — gates will trip as in the no-form case."
        )
        return _result(
            started_at, fields, field_sources, notes, flags_raised,
            instance_path.name, False, empty_validation, raw_form_values,
        )

    if not isinstance(doc, dict):
        notes.append(
            f"User Input form root in {instance_path.name} is not a JSON object "
            f"({type(doc).__name__}); treating as absent."
        )
        return _result(
            started_at, fields, field_sources, notes, flags_raised,
            instance_path.name, False, empty_validation, raw_form_values,
        )

    if doc.get("_status") == "PLACEHOLDER":
        notes.append(
            f"{instance_path.name} is a PLACEHOLDER (Section 8 lifecycle) — operator-pending "
            "values; replace before a production survey. Stage 1 raises PLACEHOLDER_INPUTS_DETECTED."
        )

    required_present = [k for k in FORM_REQUIRED_FIELDS if k in doc]
    required_missing = [k for k in FORM_REQUIRED_FIELDS if k not in doc]
    optional_present = [k for k in FORM_OPTIONAL_FIELDS if k in doc]
    known_keys = set(FORM_REQUIRED_FIELDS) | set(FORM_OPTIONAL_FIELDS)
    extra_keys = sorted(k for k in doc.keys() if k not in known_keys and not k.startswith("_"))

    if required_missing:
        notes.append(
            f"Required form fields absent from {instance_path.name}: {required_missing}. "
            "Each kept as null per spec contract."
        )
    if extra_keys:
        notes.append(f"Extra keys present (ignored): {extra_keys}.")

    raw_form_values = {
        k: doc.get(k) for k in (FORM_REQUIRED_FIELDS + FORM_OPTIONAL_FIELDS) if k in doc
    }

    # ---- per-field extraction ----
    device_type = _validate_enum("device_type", doc.get("device_type"), ENUM_DEVICE_TYPE, notes)
    fields["L1F_GCP_026_device_type"] = device_type
    fields["L1F_GCP_027_device_id"] = _validate_string("device_id", doc.get("device_id"), notes)
    device_role = _validate_enum("device_role", doc.get("device_role"), ENUM_DEVICE_ROLE, notes)
    fields["L1F_GCP_028_device_role"] = device_role
    fields["L1F_GCP_029_antenna_model"] = _validate_string("antenna_model", doc.get("antenna_model"), notes)

    # Antenna height — validate then unit-normalize (ft → m).
    raw_height = doc.get("antenna_height_m")
    raw_units = doc.get("antenna_height_units")
    height_validated = _validate_number_in_range(
        "antenna_height_m", raw_height, 0.0, 10.0, notes, min_inclusive=True
    )
    units_validated = _validate_enum("antenna_height_units", raw_units, ENUM_ANTENNA_HEIGHT_UNITS, notes)
    if height_validated is not None and units_validated == "ft":
        height_metres = round(height_validated * FT_TO_M, 6)
        notes.append(
            f"antenna_height_m={raw_height}ft → normalized to {height_metres}m "
            "(L1F_GCP_030 stored in metres; raw entry preserved in parser_meta.raw_form_values)."
        )
        fields["L1F_GCP_030_antenna_height_m"] = height_metres
    else:
        fields["L1F_GCP_030_antenna_height_m"] = height_validated
    fields["L1F_GCP_031_antenna_height_units"] = units_validated

    fields["L1F_GCP_032_antenna_measurement_type"] = _validate_enum(
        "antenna_measurement_type", doc.get("antenna_measurement_type"),
        ENUM_ANTENNA_MEASUREMENT_TYPE, notes,
    )
    fields["L1F_GCP_033_measured_to_reference"] = _validate_enum(
        "measured_to_reference", doc.get("measured_to_reference"),
        ENUM_MEASURED_TO_REFERENCE, notes,
    )
    fields["L1F_GCP_034_height_measured_count"] = _validate_int_in_range(
        "height_measured_count", doc.get("height_measured_count"), 1, None, notes
    )
    fields["L1F_GCP_035_pole_tripod_fixed_height"] = (
        _validate_number_in_range("pole_tripod_fixed_height", doc.get("pole_tripod_fixed_height"), 0.0, 10.0, notes)
        if doc.get("pole_tripod_fixed_height") is not None else None
    )
    fields["L1F_GCP_036_target_type"] = _validate_enum(
        "target_type", doc.get("target_type"), ENUM_TARGET_TYPE, notes
    )
    fields["L1F_GCP_037_target_size_m"] = (
        _validate_number_in_range("target_size_m", doc.get("target_size_m"), 0.0, 10.0, notes)
        if doc.get("target_size_m") is not None else None
    )
    fields["L1F_GCP_038_target_placed_confirmed"] = _validate_bool(
        "target_placed_confirmed", doc.get("target_placed_confirmed"), notes
    )
    fields["L1F_GCP_039_flight_start_utc"] = _normalize_iso_utc(
        "flight_start_utc", doc.get("flight_start_utc"), notes
    )
    fields["L1F_GCP_040_flight_end_utc"] = _normalize_iso_utc(
        "flight_end_utc", doc.get("flight_end_utc"), notes
    )

    for l1f, json_key in L1F_KEY_MAP.items():
        present = json_key in doc
        non_null = present and doc.get(json_key) is not None
        if non_null:
            field_sources[l1f] = "form_json_direct"
        elif present:
            field_sources[l1f] = (
                "form_json_explicit_null_optional"
                if json_key in FORM_OPTIONAL_FIELDS
                else "form_json_explicit_null_required"
            )
        elif json_key in FORM_OPTIONAL_FIELDS:
            field_sources[l1f] = "absent_in_form_optional_null"
        else:
            field_sources[l1f] = "absent_in_form_required_null"

    # ---- consistency / cross-field notes (NOT flags) ----
    if device_type == "OTHER":
        notes.append(
            "device_type=OTHER → Stage 3b fires FLG_GCP_013 UNRECOGNIZED_DEVICE_TYPE "
            "(MEDIUM, advisory); oplog expectations not enforced."
        )
    elif device_type in ("CB_X", "AEROPOINT"):
        notes.append(
            f"device_type={device_type} → antenna_height_auto_known=True (derived Stage 3a); "
            "L3I_GCP_005 = 100 (factory-known). DGPS-only antenna fields "
            "(measurement_type/measured_to_reference/height_measured_count) are N/A."
        )
        dgps_only_present = [f for f in DGPS_ONLY_ANTENNA_FIELDS if doc.get(f) is not None]
        if dgps_only_present:
            notes.append(
                f"DGPS-only antenna fields present for non-DGPS device_type={device_type}: "
                f"{dgps_only_present} — ignored by L3I_GCP_005."
            )
    elif device_type == "DGPS":
        notes.append(
            "device_type=DGPS → antenna_height operator-measured; L3I_GCP_005 needs "
            "VERTICAL + measured_to=ARP + count>=3 + RINEX agreement for 100. "
            "antenna_height_m cross-checked vs RINEX antenna_delta_h (L2D_GCP_018)."
        )

    if device_role is not None and device_role != "GCP":
        notes.append(
            f"device_role={device_role} (not GCP) → point EXCLUDED from gcp_score aggregation "
            "(Stage 3c, across GCP-role points); routed to the future check_point_score."
        )

    if fields["L1F_GCP_027_device_id"] is not None:
        notes.append(
            "device_id present → feeds L2D_GCP_019 device_id_match vs RINEX device_id → "
            "L3I_GCP_006 (mismatch → FLG_GCP_010 GCP_POINT_DEVICE_ID_MISMATCH, HIGH reviewer-blocking)."
        )
    if fields["L1F_GCP_029_antenna_model"] is not None:
        notes.append(
            "antenna_model present → feeds L2D_GCP_017 antenna_type_match vs RINEX antenna_type → "
            "L3I_GCP_007 (string consistency only, not an ANTEX calibration check)."
        )
    if (
        fields["L1F_GCP_037_target_size_m"] is not None
        or fields["L1F_GCP_038_target_placed_confirmed"] is not None
    ):
        notes.append(
            "target_size_m / target_placed_confirmed are metadata for pre_processing_score "
            "(#11 visibility) — NOT read by gcp_score."
        )

    fs = fields["L1F_GCP_039_flight_start_utc"]
    fe = fields["L1F_GCP_040_flight_end_utc"]
    if fs and fe:
        try:
            dts = datetime.fromisoformat(fs.replace("Z", "+00:00"))
            dte = datetime.fromisoformat(fe.replace("Z", "+00:00"))
            if dte <= dts:
                notes.append(
                    f"flight_end_utc ({fe}) <= flight_start_utc ({fs}) → occupation_coverage_ratio "
                    "<= 0 → Stage 3c L3I_GCP_001 gate trips FLG_GCP_003 GCP_POINT_FLIGHT_GAP (CRITICAL)."
                )
        except ValueError:
            pass

    validation = {
        "required_present": required_present,
        "required_missing": required_missing,
        "optional_present": optional_present,
        "extra_keys": extra_keys,
    }
    return _result(
        started_at, fields, field_sources, notes, flags_raised,
        instance_path.name, True, validation, raw_form_values,
    )


def _result(
    started_at: datetime,
    fields: dict[str, Any],
    field_sources: dict[str, str],
    notes: list[str],
    flags_raised: list[dict],
    source_file_name: str | None,
    instance_found: bool,
    validation: dict[str, Any],
    raw_form_values: dict[str, Any],
) -> dict[str, Any]:
    finished_at = datetime.now(timezone.utc)
    return {
        "fields": fields,
        "parser_meta": {
            "parser_id": PARSER_ID,
            "parser_version": PARSER_VERSION,
            "source_file_id": SOURCE_FILE_ID,
            "source_file_name": source_file_name,
            "instance_found": instance_found,
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "wall_time_sec": round((finished_at - started_at).total_seconds(), 3),
            "fields_provided": sorted(fields.keys()),
            "field_sources": field_sources,
            "validation": validation,
            "raw_form_values": raw_form_values,
            "notes": notes,
            "flags_raised": flags_raised,
        },
    }


# ---- CLI -------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import sys
    if len(argv) != 3:
        print("usage: parse_user_input.py <project_root> <point_folder>", file=sys.stderr)
        return 2
    root = Path(argv[1]).resolve()
    folder = Path(argv[2]).resolve()
    out = parse(folder, root)
    json.dump(out, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv))
