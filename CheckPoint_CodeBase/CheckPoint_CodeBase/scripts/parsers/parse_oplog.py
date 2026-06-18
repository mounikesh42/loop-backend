#!/usr/bin/env python3
"""parse_oplog.py - SRC_CP_OPLOG parser (per point / per occupation).

Emits L1F_CP_016..019 (4 source fields) for the check-point operation/session
log. Device-type-aware presence:
  - CB_X / AEROPOINT / DGPS -> a session log is expected-present.
  - OTHER                    -> expected-absent (all 4 fields null, no penalty).
Signature availability is also device-type-aware (informational here; the
scoring lives in Stage 3a session_integrity_ok + Stage 3b cp_log_integrity_score):
  - CB_X -> signed log expected (raw_log_signature_valid expected present).
  - DGPS / AEROPOINT -> signature optional ("if available").
  - OTHER -> signature N/A.

Spec fields:
  - raw_log_download_confirmed (L1F_CP_016, bool)
  - raw_log_signature_valid    (L1F_CP_017, bool; nullable - N/A on unsigned gear)
  - session_completed_normally (L1F_CP_018, bool)
  - session_end_utc            (L1F_CP_019, ISO-8601 UTC string)

device_type is passed in by the Stage 2 merge (from the FORM parser's
device_type) so absence can be judged expected vs unexpected. The parser raises
NO spec flags; integrity flags fire at Stage 3b threshold.

parse(point_folder, project_root, device_type) -> {"fields", "parser_meta"}.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

PARSER_ID = "parse_oplog"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_CP_OPLOG"

OPLOG_INSTANCE_FILENAMES = ("cp_oplog.json", "oplog.json", "operation_log.json")

# device_types for which a session-log instance is expected to be present.
_OPLOG_EXPECTED_DEVICE_TYPES = ("CB_X", "AEROPOINT", "DGPS")
# device_types for which a valid signature is expected (signed logs).
_SIGNATURE_EXPECTED_DEVICE_TYPES = ("CB_X",)

L1F_KEY_MAP = {
    "L1F_CP_016_raw_log_download_confirmed": "raw_log_download_confirmed",
    "L1F_CP_017_raw_log_signature_valid": "raw_log_signature_valid",
    "L1F_CP_018_session_completed_normally": "session_completed_normally",
    "L1F_CP_019_session_end_utc": "session_end_utc",
}


def _empty_fields() -> dict[str, Any]:
    return {k: None for k in L1F_KEY_MAP}


def _validate_bool(name, value, notes):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    notes.append(f"{name}={value!r} not a bool - coerced to null.")
    return None


def _coerce_iso(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, str):
        notes.append(f"{name}={value!r} not a string - coerced to null.")
        return None
    s = value.strip()
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        notes.append(f"{name}={value!r} not ISO-8601 - kept raw string.")
        return s
    return s


def _discover_instance(point_folder: Path) -> Path | None:
    for name in OPLOG_INSTANCE_FILENAMES:
        candidate = point_folder / name
        if candidate.exists():
            return candidate
    return None


def _result(fields, field_sources, notes, flags_raised, source_file_name,
            instance_found, device_type, expected, signature_expected):
    return {
        "fields": dict(sorted(fields.items())),
        "parser_meta": {
            "parser_id": PARSER_ID,
            "parser_version": PARSER_VERSION,
            "source_file_id": SOURCE_FILE_ID,
            "source_file_name": source_file_name,
            "instance_found": instance_found,
            "device_type_in": device_type,
            "oplog_expected": expected,
            "signature_expected": signature_expected,
            "fields_provided": sorted(fields.keys()),
            "field_sources": field_sources,
            "notes": notes,
            "flags_raised": flags_raised,
        },
    }


def parse(point_folder: Path, project_root: Path | None = None,
          device_type: str | None = None) -> dict[str, Any]:
    notes: list[str] = []
    flags_raised: list[dict] = []

    point_folder = Path(point_folder)
    instance_path = _discover_instance(point_folder)
    fields = _empty_fields()
    field_sources = {k: "absent_oplog_null" for k in fields}
    expected = device_type in _OPLOG_EXPECTED_DEVICE_TYPES if device_type else False
    signature_expected = device_type in _SIGNATURE_EXPECTED_DEVICE_TYPES if device_type else False

    if instance_path is None:
        if expected:
            notes.append(
                f"OPLOG expected for device_type={device_type} but no instance found in "
                f"'{point_folder.name}'. All L1F_CP_016..019 kept null; cp_log_integrity_score "
                "degrades to the download-unconfirmed path downstream.")
        else:
            notes.append(
                f"No OPLOG instance in '{point_folder.name}' (device_type={device_type}); "
                "expected-absent, all 4 fields null without penalty.")
        return _result(fields, field_sources, notes, flags_raised, None, False,
                       device_type, expected, signature_expected)

    try:
        with instance_path.open("r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        notes.append(f"OPLOG {instance_path.name} could not be parsed ({exc}); treating as absent.")
        return _result(fields, field_sources, notes, flags_raised, instance_path.name,
                       False, device_type, expected, signature_expected)

    if not isinstance(doc, dict):
        notes.append(f"OPLOG root in {instance_path.name} is not a JSON object "
                     f"({type(doc).__name__}); treating as absent.")
        return _result(fields, field_sources, notes, flags_raised, instance_path.name,
                       False, device_type, expected, signature_expected)

    if doc.get("_status") == "PLACEHOLDER":
        notes.append(
            f"{instance_path.name} is a PLACEHOLDER (Section 8 lifecycle) - operator-pending; "
            "replace before a production survey. Stage 1 raises PLACEHOLDER_INPUTS_DETECTED.")

    fields["L1F_CP_016_raw_log_download_confirmed"] = _validate_bool(
        "raw_log_download_confirmed", doc.get("raw_log_download_confirmed"), notes)
    fields["L1F_CP_017_raw_log_signature_valid"] = _validate_bool(
        "raw_log_signature_valid", doc.get("raw_log_signature_valid"), notes)
    fields["L1F_CP_018_session_completed_normally"] = _validate_bool(
        "session_completed_normally", doc.get("session_completed_normally"), notes)
    fields["L1F_CP_019_session_end_utc"] = _coerce_iso(
        "session_end_utc", doc.get("session_end_utc"), notes)
    for k in L1F_KEY_MAP:
        if fields[k] is not None:
            field_sources[k] = "oplog"

    # Informational: a signed-log device with no signature field present.
    if (signature_expected
            and fields["L1F_CP_017_raw_log_signature_valid"] is None
            and "raw_log_signature_valid" not in doc):
        notes.append(
            f"device_type={device_type} expects a signed log but raw_log_signature_valid "
            "is absent; cp_log_integrity_score will treat signature as unavailable.")

    return _result(fields, field_sources, notes, flags_raised, instance_path.name,
                   True, device_type, expected, signature_expected)


def main(argv=None) -> int:
    import argparse
    import json as _j
    parser = argparse.ArgumentParser(description="Parse a Check Point operation/session log")
    parser.add_argument("point_folder")
    parser.add_argument("--device-type", default=None)
    args = parser.parse_args(argv)
    out = parse(Path(args.point_folder), Path("."), args.device_type)
    print(_j.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
