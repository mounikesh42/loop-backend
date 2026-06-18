#!/usr/bin/env python3
"""parse_manifest.py - SRC_PROC_MANIFEST parser (survey-level).

Emits the 11 operator-declared source fields (L1F_PROC_068..078) from
proc_manifest.json, keyed by spec field_name (so Stage 3a can look them up
directly, the same convention parse_report.py uses).

The manifest is the OPERATOR-DECLARATIVE cross-check artifact: the report is
software-generated, the manifest is what the operator intended. Stage 3a/3b
compares the two (CRS match, camera-model match, precalibration match, marker
roles, deliverable completeness, accuracy target) - so this parser stores the
declarations verbatim (null-preserving validators) and does NOT itself decide
matches or raise flags.

Absence handling: the manifest is REQUIRED-but-graceful. When it is absent the
report_and_manifest indicators (incl. CV1 cp_rmse moment-of-truth, the CRS-match
gate) degrade to N/A and redistribute at Stage 3b/3c - so all 11 fields return
null here rather than hard-failing.

parse(manifest_path, project_root=None) -> {"fields", "parser_meta"}.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PARSER_ID = "parse_manifest"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_PROC_MANIFEST"
SOURCE_FILE_NAME = "Processing Manifest"

# soft-enum vocab (out-of-vocab is KEPT + noted, never nulled - Stage 3b decides)
ENUM_SITE_COVER = ("flat-structured", "vegetated", "mining-pit", "mixed")
DELIVERABLE_VOCAB = ("ortho", "dsm", "dtm", "point_cloud", "mesh_3d")

# spec field_name -> (json_key == field_name, kind[, vocab])
L1F_SPEC: dict[str, tuple] = {
    "manifest_project_required_crs":      ("manifest_project_required_crs", "str"),
    "manifest_accuracy_target_m":         ("manifest_accuracy_target_m", "number"),
    "manifest_capture_frame_crs":         ("manifest_capture_frame_crs", "str"),
    "manifest_capture_geoid":             ("manifest_capture_geoid", "str"),
    "manifest_declared_camera_model":     ("manifest_declared_camera_model", "str"),
    "manifest_precalibration_expected":   ("manifest_precalibration_expected", "bool"),
    "manifest_marker_roles_declared":     ("manifest_marker_roles_declared", "dict"),
    "manifest_declared_site_cover":       ("manifest_declared_site_cover", "softenum", ENUM_SITE_COVER),
    "manifest_declared_deliverables":     ("manifest_declared_deliverables", "list"),
    "manifest_dtm_deliverable_claimed":   ("manifest_dtm_deliverable_claimed", "bool"),
    "manifest_software_version_baseline": ("manifest_software_version_baseline", "str"),
}

# fields whose absence degrades a scoring indicator to N/A (-> note only)
_REQUIRED = (
    "manifest_project_required_crs", "manifest_accuracy_target_m",
    "manifest_declared_camera_model", "manifest_precalibration_expected",
    "manifest_marker_roles_declared", "manifest_declared_deliverables",
)


# ---- validators (null-preserving) ------------------------------------------
def _v_str(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, str):
        notes.append(f"{name} not a string ({type(value).__name__}={value!r}) - null.")
        return None
    return value.strip() or None


def _v_bool(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, bool):
        notes.append(f"{name} not a bool ({type(value).__name__}={value!r}) - null.")
        return None
    return value


def _v_number(name, value, notes):
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
    return float(value)


def _v_dict(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, dict):
        notes.append(f"{name} not an object ({type(value).__name__}={value!r}) - null.")
        return None
    return {str(k): v for k, v in value.items()}


def _v_list(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, list):
        notes.append(f"{name} not a list ({type(value).__name__}={value!r}) - null.")
        return None
    out = [str(x).strip() for x in value if str(x).strip()]
    unknown = [x for x in out if x not in DELIVERABLE_VOCAB]
    if unknown:
        notes.append(f"{name} has non-standard deliverable types {unknown} - KEPT (Stage 3a decides).")
    return out


def _v_softenum(name, value, vocab, notes):
    if value is None:
        return None
    if not isinstance(value, str):
        notes.append(f"{name} not a string enum ({type(value).__name__}={value!r}) - null.")
        return None
    s = value.strip()
    if not s:
        return None
    if s not in vocab:
        notes.append(f"{name}={s!r} not in expected {list(vocab)} - KEPT (Stage 3b decides).")
    return s


def _empty_fields() -> dict[str, Any]:
    return {k: None for k in L1F_SPEC}


def _result(fields, field_sources, notes, instance_found, status, validation, raw_values):
    return {
        "fields": dict(sorted(fields.items())),
        "parser_meta": {
            "parser_id": PARSER_ID,
            "parser_version": PARSER_VERSION,
            "source_file_id": SOURCE_FILE_ID,
            "source_file_name": SOURCE_FILE_NAME,
            "instance_found": instance_found,
            "manifest_status": status,
            "fields_provided": sorted(fields.keys()),
            "non_null_count": sum(1 for v in fields.values() if v is not None),
            "field_sources": field_sources,
            "validation": validation,
            "raw_values": raw_values,
            "notes": notes,
            "flags_raised": [],
        },
    }


def parse(manifest_path, project_root: Path | None = None) -> dict[str, Any]:
    notes: list[str] = []
    fields = _empty_fields()
    field_sources = {k: "absent_manifest_null" for k in fields}
    empty_validation = {"required_present": [], "required_missing": list(_REQUIRED),
                        "optional_present": [], "extra_keys": []}

    path = Path(manifest_path) if manifest_path else None
    if path is None or not path.is_file():
        notes.append("No processing manifest found - all 11 manifest_* fields null. Manifest is "
                     "REQUIRED-but-graceful: report_and_manifest indicators (precalib, camera "
                     "model, CV1 cp_rmse, gcp_rmse, role consistency, CRS-match gate, dtm "
                     "classification) degrade to N/A and redistribute at Stage 3b/3c.")
        return _result(fields, field_sources, notes, False, None, empty_validation, {})

    try:
        with path.open(encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        notes.append(f"Manifest {path.name} unreadable ({exc}); treating as absent.")
        return _result(fields, field_sources, notes, False, None, empty_validation, {})
    if not isinstance(doc, dict):
        notes.append(f"Manifest root in {path.name} is not an object ({type(doc).__name__}).")
        return _result(fields, field_sources, notes, False, None, empty_validation, {})

    status = doc.get("_status")
    if status == "PLACEHOLDER":
        notes.append(f"{path.name} is a PLACEHOLDER (Section 8 lifecycle) - operator-pending; "
                     "replace before a production survey. Stage 1 raises PLACEHOLDER_INPUTS_DETECTED.")

    all_keys = {jk for jk, *_ in L1F_SPEC.values()}
    required_present = [k for k in _REQUIRED if k in doc]
    required_missing = [k for k in _REQUIRED if k not in doc]
    optional_present = sorted(k for k in all_keys if k not in _REQUIRED and k in doc)
    extra_keys = sorted(k for k in doc if k not in all_keys and not k.startswith("_"))
    if required_missing:
        notes.append(f"Required manifest fields absent: {required_missing}. Each kept null.")
    if extra_keys:
        notes.append(f"Extra keys present (ignored): {extra_keys}.")
    raw_values = {jk: doc.get(jk) for jk in all_keys if jk in doc}

    for fname, spec_t in L1F_SPEC.items():
        jk, kind = spec_t[0], spec_t[1]
        raw = doc.get(jk)
        if kind == "str":
            val = _v_str(jk, raw, notes)
        elif kind == "number":
            val = _v_number(jk, raw, notes)
        elif kind == "bool":
            val = _v_bool(jk, raw, notes)
        elif kind == "dict":
            val = _v_dict(jk, raw, notes)
        elif kind == "list":
            val = _v_list(jk, raw, notes)
        elif kind == "softenum":
            val = _v_softenum(jk, raw, spec_t[2], notes)
        else:
            val = raw
        fields[fname] = val
        if jk in doc:
            field_sources[fname] = "manifest"

    validation = {"required_present": required_present, "required_missing": required_missing,
                  "optional_present": optional_present, "extra_keys": extra_keys}
    return _result(fields, field_sources, notes, True, status, validation, raw_values)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Parse a processing manifest")
    parser.add_argument("manifest_path")
    args = parser.parse_args(argv)
    out = parse(Path(args.manifest_path), Path("."))
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
