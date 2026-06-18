#!/usr/bin/env python3
"""Stage 2 parser — Images (SRC_IMG_01).

Reads EXIF from every image in inputs.images_folder and emits the
L1F_IMG_001..017 canonical source-field values for Stage 2.

Output shape per field:
  - Scalar fields (counts, dominant Make/Model/format, sensor dims):
        either bare int/string, or {"value": ..., "values_seen": [...], "mixed": bool}
  - Per-image array fields (GPS, focal length, exposure, etc.):
        {"values": [...|None], "count_present": N, "count_total": M, plus min/max/mean
         when numeric and at least one value present}

The per-image arrays are sorted-by-filename and the filenames list lives in
_parser_meta.image_filenames so downstream stages can cross-reference.
"""
import json
import statistics
import sys
from fractions import Fraction
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from PIL.ExifTags import GPSTAGS, TAGS


VALID_EXTS = {".jpg", ".jpeg", ".dng", ".raw"}

# Reverse lookup so we can pull EXIF entries by name rather than numeric tag id.
NAME_TO_TAG = {name: tag_id for tag_id, name in TAGS.items()}
GPSINFO_TAG = NAME_TO_TAG["GPSInfo"]


def rational_to_float(value):
    """EXIF often returns rationals; coerce to plain float."""
    if value is None:
        return None
    if isinstance(value, Fraction):
        return float(value)
    if isinstance(value, tuple) and len(value) == 2:
        num, den = value
        return float(num) / float(den) if den else None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def dms_to_decimal(dms, ref):
    """Convert EXIF GPS [(deg), (min), (sec)] + N/S/E/W ref to decimal degrees."""
    if not dms or len(dms) < 3:
        return None
    try:
        deg = rational_to_float(dms[0])
        minutes = rational_to_float(dms[1])
        sec = rational_to_float(dms[2])
        if None in (deg, minutes, sec):
            return None
        dec = deg + minutes / 60.0 + sec / 3600.0
        if ref in ("S", "W"):
            dec = -dec
        return dec
    except (TypeError, ValueError, IndexError):
        return None


def decode_gps(exif: dict) -> dict:
    raw = exif.get(GPSINFO_TAG)
    if not raw:
        return {"latitude": None, "longitude": None, "altitude": None, "timestamp": None}
    gps = {GPSTAGS.get(k, k): v for k, v in raw.items()}
    lat = dms_to_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
    lon = dms_to_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
    alt = rational_to_float(gps.get("GPSAltitude"))
    if alt is not None and gps.get("GPSAltitudeRef") in (1, b"\x01"):
        alt = -alt
    timestamp = None
    if gps.get("GPSDateStamp") and gps.get("GPSTimeStamp"):
        # GPSDateStamp 'YYYY:MM:DD', GPSTimeStamp ((h,1),(m,1),(s,1)) typically.
        date_s = gps["GPSDateStamp"]
        ts = gps["GPSTimeStamp"]
        try:
            h = int(rational_to_float(ts[0]) or 0)
            m = int(rational_to_float(ts[1]) or 0)
            s = rational_to_float(ts[2]) or 0
            timestamp = f"{date_s.replace(':', '-')}T{h:02d}:{m:02d}:{s:06.3f}Z"
        except (TypeError, ValueError, IndexError):
            timestamp = None
    return {"latitude": lat, "longitude": lon, "altitude": alt, "timestamp": timestamp}


def extract_one(path: Path) -> dict:
    """Parse one image. Returns a per-image record. 'valid' flag indicates open/parse success."""
    rec = {
        "filename": path.name,
        "valid": False,
        "format": None,
        "exif_present": False,
        "make": None,
        "model": None,
        "image_width_px": None,
        "image_height_px": None,
        "focal_length_mm": None,
        "subject_distance_m": None,
        "iso": None,
        "exposure_time_s": None,
        "f_number": None,
        "datetime_original": None,
        "gps_latitude": None,
        "gps_longitude": None,
        "gps_altitude": None,
        "gps_timestamp": None,
    }
    try:
        img = Image.open(path)
        img.verify()  # quick corruption check (consumes the file handle)
        img = Image.open(path)  # reopen for further reads
        rec["valid"] = True
        raw_fmt = (img.format or path.suffix.lstrip(".")).upper()
        # Pillow's RAW/DNG support varies; normalize JPEG container variants
        # (JPEG/MPO — Sony stereoscopic multi-picture) to JPG per spec enum
        # L1F_IMG_004 = enum(JPG|DNG|RAW).
        if raw_fmt in ("JPEG", "MPO", "JPG"):
            rec["format"] = "JPG"
        else:
            rec["format"] = raw_fmt

        rec["image_width_px"] = int(img.size[0])
        rec["image_height_px"] = int(img.size[1])

        exif = img._getexif() or {}
        rec["exif_present"] = bool(exif)

        rec["make"] = (exif.get(NAME_TO_TAG["Make"]) or "").strip() or None
        rec["model"] = (exif.get(NAME_TO_TAG["Model"]) or "").strip() or None

        # Prefer the high-precision Exif*Width/Height fields when present (they
        # describe the actual sensor output, not container metadata).
        ew = exif.get(NAME_TO_TAG.get("ExifImageWidth", 40962))
        eh = exif.get(NAME_TO_TAG.get("ExifImageHeight", 40963))
        if ew:
            rec["image_width_px"] = int(ew)
        if eh:
            rec["image_height_px"] = int(eh)

        rec["focal_length_mm"] = rational_to_float(exif.get(NAME_TO_TAG["FocalLength"]))
        rec["subject_distance_m"] = rational_to_float(exif.get(NAME_TO_TAG["SubjectDistance"]))

        iso = exif.get(NAME_TO_TAG["ISOSpeedRatings"])
        if isinstance(iso, (tuple, list)):
            iso = iso[0] if iso else None
        rec["iso"] = int(iso) if iso is not None else None

        rec["exposure_time_s"] = rational_to_float(exif.get(NAME_TO_TAG["ExposureTime"]))
        rec["f_number"] = rational_to_float(exif.get(NAME_TO_TAG["FNumber"]))

        dt = exif.get(NAME_TO_TAG["DateTimeOriginal"])
        if dt:
            # EXIF is 'YYYY:MM:DD HH:MM:SS'; keep verbatim for now (it's commonly a
            # placeholder when the camera clock isn't set).
            rec["datetime_original"] = dt

        gps = decode_gps(exif)
        rec["gps_latitude"] = gps["latitude"]
        rec["gps_longitude"] = gps["longitude"]
        rec["gps_altitude"] = gps["altitude"]
        rec["gps_timestamp"] = gps["timestamp"]
    except (UnidentifiedImageError, OSError, ValueError):
        rec["valid"] = False
    return rec


def aggregate_scalar(per_image: list, key: str, expected_type=None) -> dict:
    """Collapse a per-image categorical/scalar field into {value, values_seen, mixed}."""
    seen = []
    for r in per_image:
        v = r.get(key)
        if v is None:
            continue
        if expected_type is not None:
            try:
                v = expected_type(v)
            except (TypeError, ValueError):
                continue
        if v not in seen:
            seen.append(v)
    # Sort for determinism but preserve type
    try:
        seen_sorted = sorted(seen)
    except TypeError:
        seen_sorted = seen
    if not seen_sorted:
        return {"value": None, "values_seen": [], "mixed": False}
    value = seen_sorted[0] if len(seen_sorted) == 1 else "MIXED"
    return {"value": value, "values_seen": seen_sorted, "mixed": len(seen_sorted) > 1}


def aggregate_array(per_image: list, key: str) -> dict:
    """Collapse a per-image numeric field into {values[], count_present, count_total, min, max, mean}."""
    values = [r.get(key) for r in per_image]
    present = [v for v in values if v is not None]
    out = {
        "values": values,
        "count_present": len(present),
        "count_total": len(values),
    }
    numeric = [v for v in present if isinstance(v, (int, float))]
    if numeric:
        out["min"] = round(min(numeric), 6)
        out["max"] = round(max(numeric), 6)
        out["mean"] = round(statistics.fmean(numeric), 6)
        if len(numeric) > 1:
            out["stdev"] = round(statistics.stdev(numeric), 6)
    return out


def parse(config: dict, project_root: Path) -> dict:
    img_folder = project_root / config["inputs"]["images_folder"]
    files = sorted(
        p for p in img_folder.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in VALID_EXTS
    )

    per_image = [extract_one(p) for p in files]
    per_image.sort(key=lambda r: r["filename"])

    valid = [r for r in per_image if r["valid"]]
    geotagged = [r for r in valid if r["gps_latitude"] is not None and r["gps_longitude"] is not None]

    # Sheet 02 source-field assembly:
    fields = {
        "L1F_IMG_001": len(per_image),                          # total_images
        "L1F_IMG_002": len(valid),                              # valid_images
        "L1F_IMG_003": len(geotagged),                          # geotagged_images
        "L1F_IMG_004": aggregate_scalar(per_image, "format"),   # image_format enum
        "L1F_IMG_005": aggregate_array(per_image, "gps_latitude"),
        "L1F_IMG_006": aggregate_array(per_image, "gps_longitude"),
        "L1F_IMG_007": aggregate_array(per_image, "gps_altitude"),
        "L1F_IMG_008": {
            "values": [r["gps_timestamp"] for r in per_image],
            "count_present": sum(1 for r in per_image if r["gps_timestamp"]),
            "count_total": len(per_image),
        },
        "L1F_IMG_009": aggregate_scalar(per_image, "make"),
        "L1F_IMG_010": aggregate_scalar(per_image, "model"),
        "L1F_IMG_011": aggregate_scalar(per_image, "image_width_px"),
        "L1F_IMG_012": aggregate_scalar(per_image, "image_height_px"),
        "L1F_IMG_013": aggregate_array(per_image, "focal_length_mm"),
        "L1F_IMG_014": aggregate_array(per_image, "subject_distance_m"),
        "L1F_IMG_015": aggregate_array(per_image, "iso"),
        "L1F_IMG_016": aggregate_array(per_image, "exposure_time_s"),
        "L1F_IMG_017": aggregate_array(per_image, "f_number"),
    }

    parser_meta = {
        "parser": "parse_images",
        "image_filenames": [r["filename"] for r in per_image],
        "count_total": len(per_image),
        "count_valid": len(valid),
        "count_geotagged": len(geotagged),
        "count_exif_present": sum(1 for r in per_image if r["exif_present"]),
        "datetime_original_first": next((r["datetime_original"] for r in per_image if r["datetime_original"]), None),
        "datetime_original_last": next((r["datetime_original"] for r in reversed(per_image) if r["datetime_original"]), None),
    }

    return {
        "fields": fields,
        "parser_meta": parser_meta,
        "flags_raised": [],  # image parser does not raise pre-ingestion flags per spec sheet 09
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: parse_images.py <paths.json>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())
    result = parse(config, project_root)

    if "--full" in sys.argv:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0

    # Compact summary for inspection — full assembly into 02_source_fields.json
    # happens in run_pipeline.py at the Step 6 merge.
    fields = result["fields"]
    meta = result["parser_meta"]
    print(f"parse_images: {meta['count_total']} images")
    print(f"  L1F_IMG_001 total_images        = {fields['L1F_IMG_001']}")
    print(f"  L1F_IMG_002 valid_images        = {fields['L1F_IMG_002']}")
    print(f"  L1F_IMG_003 geotagged_images    = {fields['L1F_IMG_003']}")
    print(f"  L1F_IMG_004 image_format        = {fields['L1F_IMG_004']}")
    for fid, name in [
        ("L1F_IMG_005", "GPSLatitude"),
        ("L1F_IMG_006", "GPSLongitude"),
        ("L1F_IMG_007", "GPSAltitude"),
        ("L1F_IMG_008", "GPSTimeStamp"),
    ]:
        f = fields[fid]
        print(f"  {fid} {name:18s} = {f['count_present']}/{f['count_total']} present")
    print(f"  L1F_IMG_009 Make                = {fields['L1F_IMG_009']}")
    print(f"  L1F_IMG_010 Model               = {fields['L1F_IMG_010']}")
    print(f"  L1F_IMG_011 ImageWidth          = {fields['L1F_IMG_011']}")
    print(f"  L1F_IMG_012 ImageLength         = {fields['L1F_IMG_012']}")
    for fid, name in [
        ("L1F_IMG_013", "FocalLength_mm"),
        ("L1F_IMG_014", "SubjectDistance_m"),
        ("L1F_IMG_015", "ISO"),
        ("L1F_IMG_016", "ExposureTime_s"),
        ("L1F_IMG_017", "FNumber"),
    ]:
        f = fields[fid]
        stats = f"min={f.get('min')} max={f.get('max')} mean={f.get('mean')} stdev={f.get('stdev')}" if f.get("min") is not None else "(no values)"
        print(f"  {fid} {name:18s} = {f['count_present']}/{f['count_total']} present | {stats}")
    print(f"  _meta DateTimeOriginal range    = {meta['datetime_original_first']} → {meta['datetime_original_last']}")
    print(f"  flags_raised                    = {result['flags_raised']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
