#!/usr/bin/env python3
"""Stage 2 resolver — Camera Calibration lookup (SRC_IMG_02).

Reads EXIF Make+Model from parse_images result, looks up the calibration
library at the three tiers defined by the spec (and paths.json options):
  1. CB_LIBRARY       — cb_camera_library.json keyed by "Make|Model"
  2. ODM_DATABASE     — stub (logs "not yet implemented"), falls through
  3. SELF_CALIBRATED  — sentinel; geometry/distortion left null, ODM
                        will estimate at processing time

Emits all 15 L1F_CAL_001..015 fields. The calibration_source field
(L1F_CAL_006) reflects which tier succeeded.

Matching policy: literal equality on "<Make>|<Model>" first. If that
misses, case-insensitive fallback is attempted and logged in parser_meta.
"""
import json
import sys
from pathlib import Path


CAL_FIELDS = [
    ("L1F_CAL_001", "camera_make"),
    ("L1F_CAL_002", "camera_model"),
    ("L1F_CAL_003", "focal_length_mm"),
    ("L1F_CAL_004", "sensor_width_mm"),
    ("L1F_CAL_005", "sensor_height_mm"),
    ("L1F_CAL_006", "calibration_source"),
    ("L1F_CAL_007", "calibration_date"),
    ("L1F_CAL_008", "principal_point_x"),
    ("L1F_CAL_009", "principal_point_y"),
    ("L1F_CAL_010", "k1"),
    ("L1F_CAL_011", "k2"),
    ("L1F_CAL_012", "k3"),
    ("L1F_CAL_013", "p1"),
    ("L1F_CAL_014", "p2"),
    ("L1F_CAL_015", "odm_format_valid"),
]


def _lookup_cb(library: dict, make: str, model: str) -> tuple[dict | None, str | None]:
    """Try literal then case-insensitive. Return (entry, match_kind) or (None, None)."""
    if not make or not model:
        return None, None
    entries = library.get("entries", {})
    key = f"{make}|{model}"
    if key in entries:
        return entries[key], "literal"
    # Case-insensitive scan
    lower = key.lower()
    for k, v in entries.items():
        if k.lower() == lower:
            return v, "case_insensitive"
    return None, None


def resolve(config: dict, project_root: Path, images_fields: dict) -> dict:
    """Resolve calibration. images_fields is the L1F_IMG_* fields dict from parse_images."""
    library_path = project_root / config["cb_library_file"]
    lookup_order = config.get("options", {}).get(
        "calibration_lookup_order", ["CB_LIBRARY", "ODM_DATABASE", "SELF_CALIBRATED"]
    )

    # EXIF Make/Model (the parse_images output uses {value, values_seen, mixed})
    make_field = images_fields.get("L1F_IMG_009") or {}
    model_field = images_fields.get("L1F_IMG_010") or {}
    exif_make = make_field.get("value") if isinstance(make_field, dict) else make_field
    exif_model = model_field.get("value") if isinstance(model_field, dict) else model_field

    library = {}
    library_present = library_path.exists()
    if library_present:
        try:
            library = json.loads(library_path.read_text())
        except (json.JSONDecodeError, OSError):
            library = {}
            library_present = False

    tier_used = None
    match_kind = None
    matched_key = None
    entry = None

    for tier in lookup_order:
        if tier == "CB_LIBRARY":
            if not library_present:
                continue
            entry, match_kind = _lookup_cb(library, exif_make, exif_model)
            if entry is not None:
                tier_used = "CB_LIBRARY"
                matched_key = f"{entry.get('camera_make')}|{entry.get('camera_model')}"
                break
        elif tier == "ODM_DATABASE":
            # Stub per build instructions: log and fall through.
            # (No real ODM database integration yet.)
            continue
        elif tier == "SELF_CALIBRATED":
            # Sentinel — populate identity from EXIF, leave geometry/distortion null.
            tier_used = "SELF_CALIBRATED"
            break

    # Assemble L1F_CAL_* fields
    fields: dict = {fid: None for fid, _ in CAL_FIELDS}
    if tier_used == "CB_LIBRARY" and entry is not None:
        for fid, key in CAL_FIELDS:
            fields[fid] = entry.get(key)
        # calibration_source field reflects the library entry's declared source
        # (typically "CB_LIBRARY"). Honor the library's value as authoritative.
        fields["L1F_CAL_006"] = entry.get("calibration_source", "CB_LIBRARY")
    elif tier_used == "SELF_CALIBRATED":
        # EXIF identity carries through; everything else null.
        fields["L1F_CAL_001"] = exif_make
        fields["L1F_CAL_002"] = exif_model
        fields["L1F_CAL_006"] = "SELF_CALIBRATED"
    # else: all None (shouldn't happen with SELF_CALIBRATED in lookup_order)

    parser_meta = {
        "parser": "resolve_calibration",
        "exif_make": exif_make,
        "exif_model": exif_model,
        "exif_lookup_key": f"{exif_make}|{exif_model}" if exif_make and exif_model else None,
        "library_path": str(library_path),
        "library_present": library_present,
        "library_entries": sorted(library.get("entries", {}).keys()) if library_present else [],
        "lookup_order": lookup_order,
        "tier_used": tier_used,
        "match_kind": match_kind,
        "matched_library_key": matched_key,
        "odm_database_note": "ODM lookup not yet implemented — falling through to next tier",
    }

    return {
        "fields": fields,
        "parser_meta": parser_meta,
        "flags_raised": [],
    }


def main() -> int:
    """Standalone run: parses images on the fly to get EXIF Make/Model, then resolves."""
    if len(sys.argv) != 2:
        print("usage: resolve_calibration.py <paths.json>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())

    # Import parse_images dynamically to get EXIF without requiring an
    # earlier-stage on-disk artifact (Step 2 design: parsers chained in memory).
    sys.path.insert(0, str(Path(__file__).parent))
    import parse_images  # noqa: E402
    images_result = parse_images.parse(config, project_root)

    result = resolve(config, project_root, images_result["fields"])
    fields = result["fields"]
    meta = result["parser_meta"]

    print(f"resolve_calibration:")
    print(f"  EXIF Make|Model: {meta['exif_lookup_key']}")
    print(f"  library: {meta['library_path']} (present={meta['library_present']})")
    print(f"  library entries: {meta['library_entries']}")
    print(f"  lookup_order: {meta['lookup_order']}")
    print(f"  tier_used: {meta['tier_used']}  match_kind: {meta['match_kind']}  matched_key: {meta['matched_library_key']}")
    print()
    for fid, name in CAL_FIELDS:
        print(f"  {fid} {name:24s} = {fields[fid]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
