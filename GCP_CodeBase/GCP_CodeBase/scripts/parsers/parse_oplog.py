#!/usr/bin/env python3
"""parse_oplog.py — SRC_GCP_OPLOG parser (per point / per occupation).

Emits L1F_GCP_019..025 (7 source fields) for one GCP device's Operation Log.

Device-type-aware EXPECTED-PRESENCE (spec SRC_GCP_OPLOG.notes / how_to_obtain):
  - DGPS                      -> oplog expected-PRESENT; absence degrades integrity
                                 to unconfirmed.
  - CB_X / AEROPOINT / OTHER  -> oplog expected-ABSENT; integrity scores from
                                 RINEX-only signals (this is the normal case).

The branch itself is *evaluated downstream* — L2D_GCP_016 session_integrity_ok
(composite_scoring) and L3I_GCP_002 occupation_integrity_score (BB_GCP_COMPLETE).
This parser only:
  - extracts the 7 fields faithfully when an oplog instance is present,
  - keeps every field null when absent (never substitutes 0 / 100 / False),
  - records device_type + expected_presence in parser_meta so the merge / Stage 3b
    can apply the branch from the FORM-confirmed device_type,
  - raises NO spec flags. FLG_GCP_004 (GCP_POINT_DEVICE_FAILURE), FLG_GCP_005
    (GCP_POINT_LOG_DOWNLOAD_UNCONFIRMED) and FLG_GCP_014 (GCP_POINT_RINEX_TRUNCATED)
    are all DGPS-only and fire at the threshold / composite stage, not here.

Null semantics (mirrors the base operation_log_schema x-nullability-rule): null
means 'device did not report this' -> scored UNCONFIRMED, deliberately distinct
from a bad value. The parser never substitutes a default.

The oplog instance, when present, lives at <point_folder>/oplog.json (per point),
alongside that point's hardware.json / user_input.json. Discovery is guarded by
oplog-field presence so the sibling hardware/form JSONs are never mistaken for it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PARSER_ID = "parse_oplog"
PARSER_VERSION = "1.0"  # GCP: per-point; adds device_type-aware expected-presence
SOURCE_FILE_ID = "SRC_GCP_OPLOG"

# Conventional per-point instance filename (preferred during discovery).
OPLOG_INSTANCE_FILENAME = "oplog.json"

# device_type enum (L1F_GCP_026) -> expected oplog presence.
DEVICE_TYPES_EXPECT_PRESENT = {"DGPS"}
DEVICE_TYPES_EXPECT_ABSENT = {"CB_X", "AEROPOINT", "OTHER"}
VALID_DEVICE_TYPES = DEVICE_TYPES_EXPECT_PRESENT | DEVICE_TYPES_EXPECT_ABSENT

# Battery adequacy line (L1F_GCP_023 meaning): battery_min_pct >= 20%.
BATTERY_ADEQUACY_MIN_PCT = 20.0

OPLOG_REQUIRED_FIELDS = (
    "session_completed_normally",
    "unexpected_shutdown_count",
    "battery_start_pct",
    "battery_end_pct",
    "battery_min_pct",
    "session_end_utc",
)
OPLOG_OPTIONAL_FIELDS = ("raw_log_download_confirmed",)

# Mapping from L1F field id -> JSON key in the oplog instance.
L1F_KEY_MAP = {
    "L1F_GCP_019_session_completed_normally": "session_completed_normally",
    "L1F_GCP_020_unexpected_shutdown_count":  "unexpected_shutdown_count",
    "L1F_GCP_021_battery_start_pct":          "battery_start_pct",
    "L1F_GCP_022_battery_end_pct":            "battery_end_pct",
    "L1F_GCP_023_battery_min_pct":            "battery_min_pct",
    "L1F_GCP_024_session_end_utc":            "session_end_utc",
    "L1F_GCP_025_raw_log_download_confirmed": "raw_log_download_confirmed",
}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


# ---- device_type classification -------------------------------------------

def _classify_expected_presence(device_type: Any) -> tuple[str | None, str]:
    """Return (normalized_device_type|None, expected_presence).

    expected_presence is one of: 'expected_present', 'expected_absent', 'unknown'.
    'unknown' is used when device_type is None (not known at parse time) or is a
    value outside the enum — in both cases the absence judgement is deferred to
    the merge / Stage 3b, which read the FORM-confirmed device_type.
    """
    if device_type is None:
        return None, "unknown"
    if not isinstance(device_type, str):
        return None, "unknown"
    norm = device_type.strip().upper()
    if not norm:
        return None, "unknown"
    if norm in DEVICE_TYPES_EXPECT_PRESENT:
        return norm, "expected_present"
    if norm in DEVICE_TYPES_EXPECT_ABSENT:
        return norm, "expected_absent"
    return norm, "unknown"


# ---- field validators (null-preserving; never substitute a default) --------

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


# ---- instance discovery ----------------------------------------------------

def _looks_like_oplog(doc: Any) -> bool:
    """True if doc is a dict carrying >=1 oplog field key (and is not a schema).

    Guards against mistaking sibling hardware.json / user_input.json (none of
    whose keys overlap the oplog field set) for an oplog instance.
    """
    if not isinstance(doc, dict):
        return False
    if "$schema" in doc and "properties" in doc:
        return False
    known = set(OPLOG_REQUIRED_FIELDS) | set(OPLOG_OPTIONAL_FIELDS)
    return any(k in doc for k in known)


def _discover_instance(point_folder: Path) -> Path | None:
    """Return the oplog instance JSON in point_folder, or None.

    Prefers the conventional <point_folder>/oplog.json; otherwise falls back to
    the most-recently-modified *.json that passes _looks_like_oplog.
    """
    if not point_folder.exists():
        return None

    preferred = point_folder / OPLOG_INSTANCE_FILENAME
    if preferred.is_file():
        try:
            with preferred.open("r", encoding="utf-8") as fh:
                doc = json.load(fh)
            if _looks_like_oplog(doc):
                return preferred
        except (OSError, json.JSONDecodeError):
            return preferred  # exists but unreadable; let parse() report it

    candidates: list[Path] = []
    for p in point_folder.iterdir():
        if not p.is_file() or p.suffix.lower() != ".json" or p.name.startswith("."):
            continue
        if p.name == OPLOG_INSTANCE_FILENAME:
            continue  # already handled above
        try:
            with p.open("r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if _looks_like_oplog(doc):
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


# ---- main parse() ----------------------------------------------------------

def _empty_fields() -> dict[str, Any]:
    return {k: None for k in L1F_KEY_MAP}


def _absence_note(point_folder: Path, expected_presence: str, device_type: str | None) -> str:
    where = point_folder.name or str(point_folder)
    if expected_presence == "expected_absent":
        return (
            f"No oplog instance in '{where}' — EXPECTED-ABSENT for device_type={device_type}. "
            "Per spec this is the normal case: integrity scores from RINEX-only signals "
            "(L2D_GCP_016 session_integrity_ok evaluates TRUE when RINEX-side is clean / "
            "no gap_gt_60s; L3I_GCP_002 takes the CB_X/AEROPOINT/OTHER path). No oplog flags "
            "apply (FLG_GCP_004/005/014 are DGPS-only). All L1F_GCP_019..025 kept null."
        )
    if expected_presence == "expected_present":
        return (
            f"No oplog instance in '{where}' — but device_type=DGPS expects one PRESENT. "
            "Per spec integrity degrades to UNCONFIRMED: L3I_GCP_002 routes oplog-absent -> ~60; "
            "raw_log_download_confirmed null -> FLG_GCP_005 advisory at the threshold stage. "
            "All L1F_GCP_019..025 kept null (never defaulted)."
        )
    return (
        f"No oplog instance in '{where}'; device_type unknown at parse time. "
        "Absence not yet judged — Stage 2 merge / Stage 3b (L2D_GCP_016, L3I_GCP_002) apply the "
        "device_type branch from the FORM. All L1F_GCP_019..025 kept null."
    )


def parse(
    point_folder: Path,
    project_root: Path,
    device_type: str | None = None,
) -> dict[str, Any]:
    """Parse the (optional) per-point Operation Log.

    point_folder : the per-point folder (e.g. sample_data/gcp_rinex_point_1).
    device_type  : optional FORM-confirmed device_type (CB_X / AEROPOINT / DGPS /
                   OTHER). When omitted the parser stays neutral and defers the
                   expected-presence judgement downstream.
    """
    started_at = datetime.now(timezone.utc)
    notes: list[str] = []
    flags_raised: list[dict] = []  # always empty — oplog flags fire at threshold/composite

    norm_device_type, expected_presence = _classify_expected_presence(device_type)
    if device_type is not None and norm_device_type not in VALID_DEVICE_TYPES:
        notes.append(
            f"device_type={device_type!r} not in enum {sorted(VALID_DEVICE_TYPES)} — "
            "expected_presence set to 'unknown'; absence judgement deferred downstream."
        )

    instance_path = _discover_instance(point_folder)

    fields = _empty_fields()
    field_sources = {k: "absent_oplog_null_per_nullability_rule" for k in fields}

    empty_validation = {
        "required_present": [],
        "required_missing": list(OPLOG_REQUIRED_FIELDS),
        "optional_present": [],
        "extra_keys": [],
    }

    # ---- no instance at all ----
    if instance_path is None:
        notes.append(_absence_note(point_folder, expected_presence, norm_device_type))
        return _result(
            started_at, fields, field_sources, notes, flags_raised,
            None, False, empty_validation, norm_device_type, expected_presence,
        )

    # ---- load instance ----
    try:
        with instance_path.open("r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        notes.append(
            f"Oplog file {instance_path.name} could not be parsed ({exc}); treating as absent "
            "and keeping all L1F_GCP_019..025 null."
        )
        return _result(
            started_at, fields, field_sources, notes, flags_raised,
            instance_path.name, False, empty_validation, norm_device_type, expected_presence,
        )

    if not isinstance(doc, dict):
        notes.append(
            f"Oplog root in {instance_path.name} is not a JSON object "
            f"({type(doc).__name__}); treating as absent."
        )
        return _result(
            started_at, fields, field_sources, notes, flags_raised,
            instance_path.name, False, empty_validation, norm_device_type, expected_presence,
        )

    is_placeholder = doc.get("_status") == "PLACEHOLDER"
    if is_placeholder:
        notes.append(
            f"{instance_path.name} is a PLACEHOLDER (Section 8 lifecycle) — values are "
            "illustrative; replace with real device values before a production survey. "
            "Stage 1 raises PLACEHOLDER_INPUTS_DETECTED."
        )
    if expected_presence == "expected_absent":
        notes.append(
            f"An oplog instance is present although device_type={norm_device_type} is "
            "expected-ABSENT — parsing it anyway; downstream may still take the RINEX-only path."
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
            f"Extra keys present (ignored, not passed into derived fields): {extra_keys}."
        )

    # ---- per-field extraction ----
    fields["L1F_GCP_019_session_completed_normally"] = _validate_bool(
        "session_completed_normally", doc.get("session_completed_normally"), notes
    )
    fields["L1F_GCP_020_unexpected_shutdown_count"] = _validate_int_in_range(
        "unexpected_shutdown_count", doc.get("unexpected_shutdown_count"), 0, None, notes
    )
    fields["L1F_GCP_021_battery_start_pct"] = _validate_number_in_range(
        "battery_start_pct", doc.get("battery_start_pct"), 0.0, 100.0, notes
    )
    fields["L1F_GCP_022_battery_end_pct"] = _validate_number_in_range(
        "battery_end_pct", doc.get("battery_end_pct"), 0.0, 100.0, notes
    )
    fields["L1F_GCP_023_battery_min_pct"] = _validate_number_in_range(
        "battery_min_pct", doc.get("battery_min_pct"), 0.0, 100.0, notes
    )
    fields["L1F_GCP_024_session_end_utc"] = _normalize_iso_utc(
        "session_end_utc", doc.get("session_end_utc"), notes
    )
    fields["L1F_GCP_025_raw_log_download_confirmed"] = _validate_bool(
        "raw_log_download_confirmed", doc.get("raw_log_download_confirmed"), notes
    )

    for l1f, json_key in L1F_KEY_MAP.items():
        if json_key in doc:
            field_sources[l1f] = "oplog_json_direct"
        elif json_key in OPLOG_OPTIONAL_FIELDS:
            field_sources[l1f] = "absent_in_oplog_optional_null"
        else:
            field_sources[l1f] = "absent_in_oplog_required_null"

    # ---- consistency notes (NOT flags — those are raised at Stage 3b/composite) ----
    if (
        fields["L1F_GCP_021_battery_start_pct"] is None
        and fields["L1F_GCP_022_battery_end_pct"] is None
        and fields["L1F_GCP_023_battery_min_pct"] is None
    ):
        notes.append(
            "All three battery_*_pct fields null — battery telemetry not reported; the "
            "battery adequacy line (>=20%) cannot be evaluated for L2D_GCP_016."
        )

    if fields["L1F_GCP_019_session_completed_normally"] is False:
        notes.append(
            "session_completed_normally=False → for DGPS, Stage 3b fires FLG_GCP_004 "
            "GCP_POINT_DEVICE_FAILURE (HIGH)."
        )
    scc = fields["L1F_GCP_020_unexpected_shutdown_count"]
    if isinstance(scc, int) and scc >= 1:
        notes.append(
            f"unexpected_shutdown_count={scc} >= 1 → for DGPS, Stage 3b fires FLG_GCP_004 "
            "GCP_POINT_DEVICE_FAILURE (HIGH)."
        )
    if fields["L1F_GCP_025_raw_log_download_confirmed"] in (None, False):
        notes.append(
            "raw_log_download_confirmed null/False → for DGPS, Stage 3b fires FLG_GCP_005 "
            "GCP_POINT_LOG_DOWNLOAD_UNCONFIRMED (MEDIUM, advisory)."
        )
    bmin = fields["L1F_GCP_023_battery_min_pct"]
    if isinstance(bmin, (int, float)) and bmin < BATTERY_ADEQUACY_MIN_PCT:
        notes.append(
            f"battery_min_pct={bmin} < {BATTERY_ADEQUACY_MIN_PCT}% adequacy line → "
            "L2D_GCP_016 session_integrity_ok=False for DGPS."
        )
    if fields["L1F_GCP_024_session_end_utc"] is not None:
        notes.append(
            "session_end_utc present → feeds L2D_GCP_023 truncation_check vs RINEX obs_end_utc "
            "(FLG_GCP_014 GCP_POINT_RINEX_TRUNCATED, DGPS-only, composite stage)."
        )

    validation = {
        "required_present": required_present,
        "required_missing": required_missing,
        "optional_present": optional_present,
        "extra_keys": extra_keys,
    }
    return _result(
        started_at, fields, field_sources, notes, flags_raised,
        instance_path.name, True, validation, norm_device_type, expected_presence,
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
    device_type: str | None,
    expected_presence: str,
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
            "device_type": device_type,
            "expected_presence": expected_presence,
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


# ---- CLI -------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import sys
    if len(argv) not in (3, 4):
        print(
            "usage: parse_oplog.py <project_root> <point_folder> [device_type]",
            file=sys.stderr,
        )
        return 2
    root = Path(argv[1]).resolve()
    folder = Path(argv[2]).resolve()
    device_type = argv[3] if len(argv) == 4 else None
    out = parse(folder, root, device_type=device_type)
    json.dump(out, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv))
