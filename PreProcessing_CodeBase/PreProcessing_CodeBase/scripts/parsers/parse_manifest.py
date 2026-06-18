#!/usr/bin/env python3
"""parse_manifest.py - SRC_PP_MANIFEST parser (survey-level).

Emits L1F_PP_017..056 (40 source fields) from the operator processing manifest
(pp_manifest.json). The manifest is the spine of pre-processing: it carries the
project requirements (CRS/datum/geoid/height/units/projection), the per-artifact
declarations, the path categories that drive the catastrophic gates, the
accuracy target (sigma denominator), the site geometry (polygon + area for the
distribution / bbox checks), and the drone/base timing window.

Spec semantics preserved:
- declared_*_per_artifact (023/024/025/026/028/035/036/037) are OBJECTS keyed by
  artifact (geotag/gcp/cp[/base/drone]); kept as-is for the Stage-3a consistency
  derivations (crs_match_project, geoid_match_project, ...).
- declared_path_gcp (033) drives PP_GCP_AUTONOMOUS_PATH (catastrophic) and the
  CUSTOMER_SUPPLIED path-N/A redistribution; declared_path_geotag (032) gates the
  GEO-block path-aware indicators. Out-of-enum path values are KEPT (not nulled)
  with a note, so Stage 3b sees the real declaration.
- localization_applied_declared (029) is a nullable bool: null = UNDISCLOSED
  (drives PP_LOCALIZATION_UNDISCLOSED downstream) - kept null, never coerced.
- reconstruction_extent_polygon (055) validated to a list of >=3 [x,y] pairs.
- CRS / geoid / projection / units are stored verbatim (open vocabularies); the
  match-vs-project comparison happens at Stage 3a, not here.
- The parser raises NO spec flags (all PP flags fire at Stage 3a/3b/3c/3d).

parse(manifest_path, project_root=None) -> {"fields", "parser_meta"}.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

PARSER_ID = "parse_manifest"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_PP_MANIFEST"
SOURCE_FILE_NAME = "Processing Manifest"

# soft-enum vocabularies (out-of-vocab is KEPT + noted, not nulled)
ENUM_HEIGHT_MODE = ("orthometric", "ellipsoidal")
ENUM_UNITS = ("m", "US Survey ft", "Intl ft")
ENUM_PATH_GEOTAG = ("LOCAL_BASE_PPK", "CORS", "CLOUD", "OTHER")
ENUM_PATH_GCP = ("AUTONOMOUS", "LOCAL_BASE_PPK", "CORS", "CLOUD",
                 "PUBLISHED_BENCHMARK", "CUSTOMER_SUPPLIED")
ENUM_SITE_COVER = ("open", "vegetated", "mixed")
ENUM_FLIGHT_COND = ("clear", "hazy", "monsoon", "thermal-prone")

# L1F field key -> (json_key, kind[, vocab])
L1F_SPEC: dict[str, tuple] = {
    "L1F_PP_017_project_required_crs":                   ("project_required_crs", "str"),
    "L1F_PP_018_project_required_geoid":                 ("project_required_geoid", "str"),
    "L1F_PP_019_project_required_height_mode":           ("project_required_height_mode", "softenum", ENUM_HEIGHT_MODE),
    "L1F_PP_020_project_required_units":                 ("project_required_units", "softenum", ENUM_UNITS),
    "L1F_PP_021_project_required_projection":            ("project_required_projection", "str"),
    "L1F_PP_022_accuracy_target_m":                      ("accuracy_target_m", "number"),
    "L1F_PP_023_declared_crs_per_artifact":              ("declared_crs_per_artifact", "dict"),
    "L1F_PP_024_declared_geoid_per_artifact":            ("declared_geoid_per_artifact", "dict"),
    "L1F_PP_025_declared_height_mode_per_artifact":      ("declared_height_mode_per_artifact", "dict"),
    "L1F_PP_026_declared_units_per_artifact":            ("declared_units_per_artifact", "dict"),
    "L1F_PP_027_declared_projection":                    ("declared_projection", "str"),
    "L1F_PP_028_realization_epoch_per_artifact":         ("realization_epoch_per_artifact", "dict"),
    "L1F_PP_029_localization_applied_declared":          ("localization_applied_declared", "nbool"),
    "L1F_PP_030_customer_supplied_coord_crs":            ("customer_supplied_coord_crs", "str"),
    "L1F_PP_031_customer_accuracy_claim":                ("customer_accuracy_claim", "number"),
    "L1F_PP_032_declared_path_geotag":                   ("declared_path_geotag", "softenum", ENUM_PATH_GEOTAG),
    "L1F_PP_033_declared_path_gcp":                      ("declared_path_gcp", "softenum", ENUM_PATH_GCP),
    "L1F_PP_034_declared_path_cp":                       ("declared_path_cp", "softenum", ENUM_PATH_GCP),
    "L1F_PP_035_declared_software_per_artifact":         ("declared_software_per_artifact", "dict"),
    "L1F_PP_036_declared_software_version_per_artifact": ("declared_software_version_per_artifact", "dict"),
    "L1F_PP_037_declared_antenna_per_artifact":          ("declared_antenna_per_artifact", "dict"),
    "L1F_PP_038_baseline_length_km":                     ("baseline_length_km", "number"),
    "L1F_PP_039_captured_image_count":                   ("captured_image_count", "int"),
    "L1F_PP_040_planned_forward_overlap":                ("planned_forward_overlap", "number"),
    "L1F_PP_041_planned_side_overlap":                   ("planned_side_overlap", "number"),
    "L1F_PP_042_site_cover_declared":                    ("site_cover_declared", "softenum", ENUM_SITE_COVER),
    "L1F_PP_043_dtm_in_deliverables":                    ("dtm_in_deliverables", "bool"),
    "L1F_PP_044_target_size_cm":                         ("target_size_cm", "number"),
    "L1F_PP_045_planned_gsd_cm":                         ("planned_gsd_cm", "number"),
    "L1F_PP_046_target_type":                            ("target_type", "str"),
    "L1F_PP_047_base_file_id":                           ("base_file_id", "str"),
    "L1F_PP_048_drone_session_start_utc":                ("drone_session_start_utc", "iso"),
    "L1F_PP_049_drone_session_end_utc":                  ("drone_session_end_utc", "iso"),
    "L1F_PP_050_base_session_start_utc":                 ("base_session_start_utc", "iso"),
    "L1F_PP_051_base_session_end_utc":                   ("base_session_end_utc", "iso"),
    "L1F_PP_052_gcp_coord_determination_date":           ("gcp_coord_determination_date", "iso"),
    "L1F_PP_053_flight_date":                            ("flight_date", "iso"),
    "L1F_PP_054_reconstruction_extent_m2":               ("reconstruction_extent_m2", "number"),
    "L1F_PP_055_reconstruction_extent_polygon":          ("reconstruction_extent_polygon", "polygon"),
    "L1F_PP_056_flight_conditions_declared":             ("flight_conditions_declared", "softenum", ENUM_FLIGHT_COND),
}

# core fields whose absence breaks scoring (-> note; manifest is critical so
# Stage 1 hard-fails on whole-file absence before this matters).
_REQUIRED = (
    "project_required_crs", "project_required_geoid", "project_required_height_mode",
    "project_required_units", "project_required_projection", "accuracy_target_m",
    "declared_crs_per_artifact", "declared_path_geotag", "declared_path_gcp",
    "reconstruction_extent_m2", "reconstruction_extent_polygon",
)


# ---- validators (null-preserving) ------------------------------------------
def _v_str(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, str):
        notes.append(f"{name} not a string ({type(value).__name__}={value!r}) - null.")
        return None
    s = value.strip()
    return s or None


def _v_bool(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, bool):
        notes.append(f"{name} not a bool ({type(value).__name__}={value!r}) - null.")
        return None
    return value


def _v_nbool(name, value, notes):
    """Nullable bool: null is meaningful (undisclosed)."""
    if value is None:
        notes.append(f"{name} is null/undisclosed - kept null (drives an undisclosed flag downstream).")
        return None
    return _v_bool(name, value, notes)


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


def _v_int(name, value, notes):
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


def _v_dict(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, dict):
        notes.append(f"{name} not an object ({type(value).__name__}={value!r}) - null.")
        return None
    return value


def _v_polygon(name, value, notes):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) < 3:
        notes.append(f"{name} not a list of >=3 vertices ({value!r}) - null.")
        return None
    out = []
    for i, pt in enumerate(value):
        if (not isinstance(pt, (list, tuple)) or len(pt) != 2
                or not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in pt)):
            notes.append(f"{name}[{i}]={pt!r} not an [x,y] numeric pair - polygon nulled.")
            return None
        out.append([float(pt[0]), float(pt[1])])
    return out


def _empty_fields() -> dict[str, Any]:
    return {k: None for k in L1F_SPEC}


def _result(fields, field_sources, notes, flags_raised, instance_found,
            status, validation, raw_values):
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
            "flags_raised": flags_raised,
        },
    }


def parse(manifest_path, project_root: Path | None = None) -> dict[str, Any]:
    notes: list[str] = []
    flags_raised: list[dict] = []
    fields = _empty_fields()
    field_sources = {k: "absent_manifest_null" for k in fields}
    empty_validation = {"required_present": [], "required_missing": list(_REQUIRED),
                        "optional_present": [], "extra_keys": []}

    path = Path(manifest_path) if manifest_path else None
    if path is None or not path.is_file():
        notes.append("No processing manifest found - all L1F_PP_017..056 null. Manifest is "
                     "CRITICAL (Stage 1 hard-fails); REF block + every declared-tier indicator "
                     "loses its input.")
        return _result(fields, field_sources, notes, flags_raised, False, None,
                       empty_validation, {})

    try:
        with path.open(encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        notes.append(f"Manifest {path.name} unreadable ({exc}); treating as absent.")
        return _result(fields, field_sources, notes, flags_raised, False, None,
                       empty_validation, {})
    if not isinstance(doc, dict):
        notes.append(f"Manifest root in {path.name} is not an object ({type(doc).__name__}).")
        return _result(fields, field_sources, notes, flags_raised, False, None,
                       empty_validation, {})

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

    for l1f, spec_t in L1F_SPEC.items():
        jk, kind = spec_t[0], spec_t[1]
        raw = doc.get(jk)
        if kind == "str":
            val = _v_str(jk, raw, notes)
        elif kind == "number":
            val = _v_number(jk, raw, notes)
        elif kind == "int":
            val = _v_int(jk, raw, notes)
        elif kind == "bool":
            val = _v_bool(jk, raw, notes)
        elif kind == "nbool":
            val = _v_nbool(jk, raw, notes)
        elif kind == "iso":
            val = _v_iso(jk, raw, notes)
        elif kind == "dict":
            val = _v_dict(jk, raw, notes)
        elif kind == "polygon":
            val = _v_polygon(jk, raw, notes)
        elif kind == "softenum":
            val = _v_softenum(jk, raw, spec_t[2], notes)
        else:
            val = raw
        fields[l1f] = val
        if jk in doc:
            field_sources[l1f] = "manifest"

    validation = {"required_present": required_present, "required_missing": required_missing,
                  "optional_present": optional_present, "extra_keys": extra_keys}
    return _result(fields, field_sources, notes, flags_raised, True, status,
                   validation, raw_values)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Parse a pre-processing manifest")
    parser.add_argument("manifest_path")
    args = parser.parse_args(argv)
    out = parse(Path(args.manifest_path), Path("."))
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
