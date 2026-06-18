#!/usr/bin/env python3
"""parse_oplog.py — SRC_BASE_OPLOG parser.

Emits L1F_BASE_018..024 (7 source fields) for the Operation Log.

Key spec semantic preserved here (operation_log_schema.json
x-nullability-rule): every field is nullable, and null means 'device did not
report this' → scored as UNCONFIRMED, deliberately distinct from a bad value.
The parser never substitutes a default (no 0 / 100 / False for missing data).

If the entire OPLOG file is absent, ALL fields are set to null with a clear
parser_meta note. Stage 3b's integrity indicator handles the degrade-to-
unconfirmed path; the parser does not raise any spec flags itself (per
raised_at_stage column in sheet 07).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PARSER_ID = "parse_oplog"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_BASE_OPLOG"


OPLOG_REQUIRED_FIELDS = (
    "session_completed_normally",
    "unexpected_shutdown_count",
    "battery_start_pct",
    "battery_end_pct",
    "battery_min_pct",
    "session_end_utc",
)
OPLOG_OPTIONAL_FIELDS = ("raw_log_download_confirmed",)

# Mapping from JSON key → L1F field name
L1F_KEY_MAP = {
    "L1F_BASE_018_session_completed_normally": "session_completed_normally",
    "L1F_BASE_019_unexpected_shutdown_count":  "unexpected_shutdown_count",
    "L1F_BASE_020_battery_start_pct":          "battery_start_pct",
    "L1F_BASE_021_battery_end_pct":            "battery_end_pct",
    "L1F_BASE_022_battery_min_pct":            "battery_min_pct",
    "L1F_BASE_023_session_end_utc":            "session_end_utc",
    "L1F_BASE_024_raw_log_download_confirmed": "raw_log_download_confirmed",
}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


# ---- field validators -----------------------------------------------------

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
    name: str, value: Any, min_v: float, max_v: float, notes: list[str]
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
    if fv < min_v or fv > max_v:
        notes.append(f"{name}={fv} out of [{min_v}, {max_v}] — kept as-is.")
    return fv


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

def _discover_instance(operator_log_folder: Path) -> Path | None:
    """Return the most recently modified non-schema OPLOG instance JSON, or None."""
    if not operator_log_folder.exists():
        return None
    candidates: list[Path] = []
    for p in operator_log_folder.iterdir():
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
        if any(k in doc for k in OPLOG_REQUIRED_FIELDS):
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


# ---- main parse() ---------------------------------------------------------

def _empty_fields() -> dict[str, Any]:
    return {k: None for k in L1F_KEY_MAP}


def parse(operator_log_folder: Path, project_root: Path) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    notes: list[str] = []
    flags_raised: list[dict] = []

    instance_path = _discover_instance(operator_log_folder)

    fields = _empty_fields()
    field_sources = {k: "absent_oplog_null_per_schema_rule" for k in fields}

    if instance_path is None:
        notes.append(
            "No Operation Log instance JSON found in folder — only schema (or empty). "
            "All L1F fields kept as null per schema x-nullability-rule (degrade to "
            "UNCONFIRMED, never silent pass). Stage 3b L3I_BASE_002 integrity_score "
            "will route through its unconfirmed-path (~60); BASE_LOG_DOWNLOAD_UNCONFIRMED "
            "advisory will fire because raw_log_download_confirmed is null."
        )
        validation = {
            "required_present": [],
            "required_missing": list(OPLOG_REQUIRED_FIELDS),
            "optional_present": [],
            "extra_keys": [],
        }
        return _result(
            started_at, fields, field_sources, notes, flags_raised, None, False, validation
        )

    # ---- load instance ----
    try:
        with instance_path.open("r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        notes.append(
            f"Operation Log file {instance_path.name} could not be parsed ({exc}); "
            "treating as absent and degrading to unconfirmed."
        )
        validation = {
            "required_present": [],
            "required_missing": list(OPLOG_REQUIRED_FIELDS),
            "optional_present": [],
            "extra_keys": [],
        }
        return _result(
            started_at, fields, field_sources, notes, flags_raised,
            instance_path.name, False, validation,
        )

    if not isinstance(doc, dict):
        notes.append(
            f"Operation Log root is not a JSON object ({type(doc).__name__}); "
            "treating as absent."
        )
        validation = {
            "required_present": [],
            "required_missing": list(OPLOG_REQUIRED_FIELDS),
            "optional_present": [],
            "extra_keys": [],
        }
        return _result(
            started_at, fields, field_sources, notes, flags_raised,
            instance_path.name, False, validation,
        )

    # ---- validation tracking ----
    required_present = [k for k in OPLOG_REQUIRED_FIELDS if k in doc]
    required_missing = [k for k in OPLOG_REQUIRED_FIELDS if k not in doc]
    optional_present = [k for k in OPLOG_OPTIONAL_FIELDS if k in doc]
    known_keys = set(OPLOG_REQUIRED_FIELDS) | set(OPLOG_OPTIONAL_FIELDS)
    extra_keys = sorted(k for k in doc.keys() if k not in known_keys and not k.startswith("_"))

    if required_missing:
        notes.append(
            f"Required fields absent from {instance_path.name}: {required_missing}. "
            "Each kept as null per nullability rule."
        )
    if extra_keys:
        notes.append(
            f"Extra keys present (ignored, will not silently pass into derived fields): {extra_keys}."
        )

    # ---- per-field extraction ----
    fields["L1F_BASE_018_session_completed_normally"] = _validate_bool(
        "session_completed_normally", doc.get("session_completed_normally"), notes
    )
    fields["L1F_BASE_019_unexpected_shutdown_count"] = _validate_int_in_range(
        "unexpected_shutdown_count", doc.get("unexpected_shutdown_count"), 0, None, notes
    )
    fields["L1F_BASE_020_battery_start_pct"] = _validate_number_in_range(
        "battery_start_pct", doc.get("battery_start_pct"), 0.0, 100.0, notes
    )
    fields["L1F_BASE_021_battery_end_pct"] = _validate_number_in_range(
        "battery_end_pct", doc.get("battery_end_pct"), 0.0, 100.0, notes
    )
    fields["L1F_BASE_022_battery_min_pct"] = _validate_number_in_range(
        "battery_min_pct", doc.get("battery_min_pct"), 0.0, 100.0, notes
    )
    fields["L1F_BASE_023_session_end_utc"] = _normalize_iso_utc(
        "session_end_utc", doc.get("session_end_utc"), notes
    )
    fields["L1F_BASE_024_raw_log_download_confirmed"] = _validate_bool(
        "raw_log_download_confirmed", doc.get("raw_log_download_confirmed"), notes
    )

    for l1f, json_key in L1F_KEY_MAP.items():
        if json_key in doc:
            field_sources[l1f] = "oplog_json_direct"
        else:
            if json_key in OPLOG_OPTIONAL_FIELDS:
                field_sources[l1f] = "absent_in_oplog_optional_null"
            else:
                field_sources[l1f] = "absent_in_oplog_required_null"

    # ---- consistency notes (NOT flags — those are raised at Stage 3b/3c) ----
    if (
        fields["L1F_BASE_020_battery_start_pct"] is None
        and fields["L1F_BASE_021_battery_end_pct"] is None
        and fields["L1F_BASE_022_battery_min_pct"] is None
    ):
        notes.append(
            "All three battery_*_pct fields null — interpreted as mains/solar unit (battery N/A) "
            "per schema x-edge-cases.mains_or_solar_unit rule."
        )

    if fields["L1F_BASE_018_session_completed_normally"] is False:
        notes.append(
            "session_completed_normally=False → Stage 3b L3I_BASE_002 will fire "
            "BASE_SESSION_INTERRUPTED."
        )
    scc = fields["L1F_BASE_019_unexpected_shutdown_count"]
    if isinstance(scc, int) and scc >= 1:
        notes.append(
            f"unexpected_shutdown_count={scc} ≥ 1 → Stage 3b L3I_BASE_002 will fire "
            "BASE_SESSION_INTERRUPTED."
        )
    if fields["L1F_BASE_024_raw_log_download_confirmed"] in (None, False):
        notes.append(
            "raw_log_download_confirmed null or False → Stage 3b L3I_BASE_002 will fire "
            "BASE_LOG_DOWNLOAD_UNCONFIRMED (advisory)."
        )
    if (
        isinstance(fields["L1F_BASE_022_battery_min_pct"], (int, float))
        and fields["L1F_BASE_022_battery_min_pct"] <= 10.0
    ):
        notes.append(
            f"battery_min_pct={fields['L1F_BASE_022_battery_min_pct']} ≤ 10% — at-risk threshold "
            "per schema x-on-low rule. Stage 3a L2D_BASE_016 integrity will reflect this."
        )

    validation = {
        "required_present": required_present,
        "required_missing": required_missing,
        "optional_present": optional_present,
        "extra_keys": extra_keys,
    }
    return _result(
        started_at, fields, field_sources, notes, flags_raised,
        instance_path.name, True, validation,
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
            "notes": notes,
            "flags_raised": flags_raised,
        },
    }


# ---- CLI ------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import sys
    if len(argv) != 3:
        print("usage: parse_oplog.py <project_root> <operator_log_folder>", file=sys.stderr)
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
