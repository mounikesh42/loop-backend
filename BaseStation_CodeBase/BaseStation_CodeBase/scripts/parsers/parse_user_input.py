#!/usr/bin/env python3
"""parse_user_input.py — SRC_BASE_FORM parser.

Emits L1F_BASE_025..036 (12 source fields) for the operator-entered antenna
setup record (user_input_schema.json).

Key spec semantics preserved here:
- antenna_height_m absent (=null) → Stage 3c L3I_BASE_005 internal-gate trips
  ANTENNA_HEIGHT_MISSING, setup block = 0.
- antenna_height_units 'ft' → value normalized to metres, original kept in
  parser_meta.raw_form_values for audit.
- All enums strictly validated; out-of-enum becomes null with a note.
- flight_start_utc / flight_end_utc normalized to UTC ISO microsecond + Z.
- monument_id null is legitimate (not over a catalogued mark); not an error.
- The parser raises no spec flags (per sheet 07 raised_at_stage column —
  setup/coverage gates are Stage 3c, threshold flags are Stage 3b).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PARSER_ID = "parse_user_input"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_BASE_FORM"


FORM_REQUIRED_FIELDS = (
    "antenna_model",
    "antenna_height_m",
    "antenna_height_units",
    "antenna_measurement_type",
    "measured_to_reference",
    "height_measured_count",
    "over_known_mark",
    "verified_by_second_person",
    "flight_start_utc",
    "flight_end_utc",
)
FORM_OPTIONAL_FIELDS = ("pole_tripod_fixed_height", "monument_id")

ENUM_ANTENNA_HEIGHT_UNITS = ("m", "ft")
ENUM_ANTENNA_MEASUREMENT_TYPE = ("VERTICAL", "SLANT")
ENUM_MEASURED_TO_REFERENCE = ("ARP", "SLANT_MARK", "MOUNT_BOTTOM", "OTHER")

L1F_KEY_MAP = {
    "L1F_BASE_025_antenna_model":             "antenna_model",
    "L1F_BASE_026_antenna_height_m":          "antenna_height_m",
    "L1F_BASE_027_antenna_height_units":      "antenna_height_units",
    "L1F_BASE_028_antenna_measurement_type":  "antenna_measurement_type",
    "L1F_BASE_029_measured_to_reference":     "measured_to_reference",
    "L1F_BASE_030_height_measured_count":     "height_measured_count",
    "L1F_BASE_031_pole_tripod_fixed_height":  "pole_tripod_fixed_height",
    "L1F_BASE_032_monument_id":               "monument_id",
    "L1F_BASE_033_over_known_mark":           "over_known_mark",
    "L1F_BASE_034_verified_by_second_person": "verified_by_second_person",
    "L1F_BASE_035_flight_start_utc":          "flight_start_utc",
    "L1F_BASE_036_flight_end_utc":            "flight_end_utc",
}

FT_TO_M = 0.3048


# ---- helpers -------------------------------------------------------------

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
    name: str, value: Any, exclusive_min: float, max_v: float, notes: list[str]
) -> float | None:
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
    if fv <= exclusive_min:
        notes.append(f"{name}={fv} ≤ exclusive minimum {exclusive_min} — kept as-is.")
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


# ---- instance discovery ---------------------------------------------------

def _discover_instance(form_folder: Path) -> Path | None:
    if not form_folder.exists():
        return None
    candidates: list[Path] = []
    for p in form_folder.iterdir():
        if not p.is_file() or p.suffix.lower() != ".json":
            continue
        if p.name.startswith("."):
            continue
        try:
            with p.open("r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        if "$schema" in doc and "properties" in doc:
            continue
        if any(k in doc for k in ("antenna_model", "antenna_height_m", "flight_start_utc")):
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


# ---- main parse() ---------------------------------------------------------

def _empty_fields() -> dict[str, Any]:
    return {k: None for k in L1F_KEY_MAP}


def parse(form_folder: Path, project_root: Path) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    notes: list[str] = []
    flags_raised: list[dict] = []

    instance_path = _discover_instance(form_folder)
    fields = _empty_fields()
    field_sources = {k: "absent_form_null" for k in fields}
    raw_form_values: dict[str, Any] = {}

    if instance_path is None:
        notes.append(
            "No User Input form instance JSON found — only schema (or empty). All L1F "
            "fields kept as null. Consequences (deterministic, per spec):\n"
            "  • Stage 3c L3I_BASE_005 internal gate trips → ANTENNA_HEIGHT_MISSING; "
            "setup block = 0.\n"
            "  • Stage 3c L3I_BASE_001 internal gate trips (flight times unknown → "
            "coverage_ratio defaults to 0) → BASE_RINEX_FLIGHT_GAP; completeness block = 0.\n"
            "  • Stage 3d global gate trips → BASE_CRITICAL_FAILURE; base_station_score = 0."
        )
        validation = {
            "required_present": [],
            "required_missing": list(FORM_REQUIRED_FIELDS),
            "optional_present": [],
            "extra_keys": [],
        }
        return _result(
            started_at, fields, field_sources, notes, flags_raised,
            None, False, validation, raw_form_values,
        )

    try:
        with instance_path.open("r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        notes.append(
            f"User Input form {instance_path.name} could not be parsed ({exc}); "
            "treating as absent — gates will trip as above."
        )
        validation = {
            "required_present": [],
            "required_missing": list(FORM_REQUIRED_FIELDS),
            "optional_present": [],
            "extra_keys": [],
        }
        return _result(
            started_at, fields, field_sources, notes, flags_raised,
            instance_path.name, False, validation, raw_form_values,
        )

    if not isinstance(doc, dict):
        notes.append(
            f"User Input form root is not a JSON object ({type(doc).__name__}); treating as absent."
        )
        validation = {
            "required_present": [],
            "required_missing": list(FORM_REQUIRED_FIELDS),
            "optional_present": [],
            "extra_keys": [],
        }
        return _result(
            started_at, fields, field_sources, notes, flags_raised,
            instance_path.name, False, validation, raw_form_values,
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

    # ---- per-field extraction ----
    raw_form_values = {k: doc.get(k) for k in (FORM_REQUIRED_FIELDS + FORM_OPTIONAL_FIELDS) if k in doc}

    fields["L1F_BASE_025_antenna_model"] = _validate_string(
        "antenna_model", doc.get("antenna_model"), notes, min_length=1
    )

    # Antenna height — read raw value, validate, then unit-normalize.
    raw_height = doc.get("antenna_height_m")
    raw_units = doc.get("antenna_height_units")
    height_validated = _validate_number_in_range(
        "antenna_height_m", raw_height, 0.0, 10.0, notes
    )
    units_validated = _validate_enum(
        "antenna_height_units", raw_units, ENUM_ANTENNA_HEIGHT_UNITS, notes
    )
    if height_validated is not None and units_validated == "ft":
        height_metres = round(height_validated * FT_TO_M, 6)
        notes.append(
            f"antenna_height_m={raw_height}ft → normalized to {height_metres}m "
            "(L1F_BASE_026 stored in metres; raw entry preserved in parser_meta.raw_form_values)."
        )
        fields["L1F_BASE_026_antenna_height_m"] = height_metres
    else:
        fields["L1F_BASE_026_antenna_height_m"] = height_validated
    fields["L1F_BASE_027_antenna_height_units"] = units_validated

    fields["L1F_BASE_028_antenna_measurement_type"] = _validate_enum(
        "antenna_measurement_type", doc.get("antenna_measurement_type"),
        ENUM_ANTENNA_MEASUREMENT_TYPE, notes,
    )
    fields["L1F_BASE_029_measured_to_reference"] = _validate_enum(
        "measured_to_reference", doc.get("measured_to_reference"),
        ENUM_MEASURED_TO_REFERENCE, notes,
    )
    fields["L1F_BASE_030_height_measured_count"] = _validate_int_in_range(
        "height_measured_count", doc.get("height_measured_count"), 1, None, notes
    )
    fields["L1F_BASE_031_pole_tripod_fixed_height"] = _validate_number_in_range(
        "pole_tripod_fixed_height", doc.get("pole_tripod_fixed_height"), 0.0, 10.0, notes
    ) if doc.get("pole_tripod_fixed_height") is not None else None
    fields["L1F_BASE_032_monument_id"] = (
        _validate_string("monument_id", doc.get("monument_id"), notes, min_length=1)
        if doc.get("monument_id") is not None else None
    )
    fields["L1F_BASE_033_over_known_mark"] = _validate_bool(
        "over_known_mark", doc.get("over_known_mark"), notes
    )
    fields["L1F_BASE_034_verified_by_second_person"] = _validate_bool(
        "verified_by_second_person", doc.get("verified_by_second_person"), notes
    )
    fields["L1F_BASE_035_flight_start_utc"] = _normalize_iso_utc(
        "flight_start_utc", doc.get("flight_start_utc"), notes
    )
    fields["L1F_BASE_036_flight_end_utc"] = _normalize_iso_utc(
        "flight_end_utc", doc.get("flight_end_utc"), notes
    )

    for l1f, json_key in L1F_KEY_MAP.items():
        present = json_key in doc
        non_null = present and doc.get(json_key) is not None
        if non_null:
            field_sources[l1f] = "form_json_direct"
        elif present:
            # Key in JSON but value is null: an explicit operator declaration
            # of 'not applicable / not reported'. Schema permits this for
            # optional fields (pole_tripod_fixed_height, monument_id) where
            # nullable type is declared.
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
    if (
        fields["L1F_BASE_028_antenna_measurement_type"] == "VERTICAL"
        and fields["L1F_BASE_029_measured_to_reference"] == "SLANT_MARK"
    ):
        notes.append(
            "antenna_measurement_type=VERTICAL but measured_to_reference=SLANT_MARK — "
            "inconsistent; downstream Stage 3b L3I_BASE_005 will reflect via score path."
        )
    if (
        fields["L1F_BASE_033_over_known_mark"] is True
        and fields["L1F_BASE_032_monument_id"] is None
    ):
        notes.append(
            "over_known_mark=True but monument_id null — known mark without identifier; "
            "Learning Engine handoff (#2/#13) loses cross-session linkage."
        )
    if (
        fields["L1F_BASE_033_over_known_mark"] is True
        and fields["L1F_BASE_034_verified_by_second_person"] is False
    ):
        notes.append(
            "over_known_mark=True AND verified_by_second_person=False — Stage 3a L2D_BASE_024 "
            "benchmark_unverified will fire BASE_BENCHMARK_UNVERIFIED handoff."
        )

    fs = fields["L1F_BASE_035_flight_start_utc"]
    fe = fields["L1F_BASE_036_flight_end_utc"]
    if fs and fe:
        try:
            dts = datetime.fromisoformat(fs.replace("Z", "+00:00"))
            dte = datetime.fromisoformat(fe.replace("Z", "+00:00"))
            if dte <= dts:
                notes.append(
                    f"flight_end_utc ({fe}) ≤ flight_start_utc ({fs}) — coverage_ratio will be 0 "
                    "or negative; Stage 3a L2D_BASE_001 will treat as 0 and gate trips."
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


# ---- CLI ------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import sys
    if len(argv) != 3:
        print("usage: parse_user_input.py <project_root> <form_folder>", file=sys.stderr)
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
