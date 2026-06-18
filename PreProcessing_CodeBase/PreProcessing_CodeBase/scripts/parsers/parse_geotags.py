#!/usr/bin/env python3
"""parse_geotags.py - SRC_PP_GEOTAGS parser (survey-level, per-image).

Reads the geotagged image set (real EXIF JPEGs via piexif) and emits the 7
geotag source fields (L1F_PP_001..007): a scalar geotag_count plus a per-image
list carrying id / position / fix-status / crs / capture-time / camera-serial.

EXIF tag mapping (the convention the gold-standard generator writes; DJI/Emlid
real-world). PPK Q-fix has no portable standard EXIF tag, so it is carried in
the GPS IFD:
    position        GPSLatitude/Ref + GPSLongitude/Ref + GPSAltitude/Ref
    crs_in_exif     GPSMapDatum (e.g. "WGS-84")          -> kept verbatim
    per_geotag_fix  GPSProcessingMethod ("RTK FIXED"/...) + GPSDifferential
                    -> normalised to FIXED / FLOAT / AUTONOMOUS
    sigma_h (bonus) GPSHPositioningError (m)              -> per-image _sigma_h_m
    capture_utc     GPSDateStamp + GPSTimeStamp (UTC)     -> ISO; DateTimeOriginal fallback
    camera_serial   Exif.BodySerialNumber
    make/model      0th.Make / 0th.Model                 -> parser_meta (sensor xcheck)

If a sidecar (.csv/.txt) is present it is noted but EXIF is authoritative in v1.
The parser raises NO spec flags (all PP flags fire at Stage 3a/3b/3c/3d).

parse(geotags_dir, project_root=None, image_extensions=None, sidecar_extensions=None)
  -> {"fields", "parser_meta"}.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import piexif

PARSER_ID = "parse_geotags"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_PP_GEOTAGS"
SOURCE_FILE_NAME = "Geotagged Image Set"

DEFAULT_IMAGE_EXTS = (".jpg", ".jpeg", ".tif", ".tiff")
DEFAULT_SIDECAR_EXTS = (".csv", ".txt")

G = piexif.GPSIFD
I = piexif.ImageIFD
EX = piexif.ExifIFD


def _decode(v):
    if isinstance(v, bytes):
        return v.split(b"\x00", 1)[0].decode("ascii", "replace").strip() or None
    if isinstance(v, str):
        return v.strip() or None
    return v


def _rat(v):
    try:
        num, den = v
        return num / den if den else None
    except (TypeError, ValueError):
        return None


def _dms_to_deg(dms, ref):
    try:
        d = _rat(dms[0]); m = _rat(dms[1]); s = _rat(dms[2])
        if None in (d, m, s):
            return None
        deg = d + m / 60.0 + s / 3600.0
        if isinstance(ref, bytes):
            ref = ref.decode("ascii", "replace")
        if ref and ref.upper() in ("S", "W"):
            deg = -deg
        return round(deg, 8)
    except (TypeError, IndexError):
        return None


def _proc_method(v):
    """GPSProcessingMethod has an 8-byte character-code prefix per EXIF."""
    if isinstance(v, bytes):
        body = v[8:] if len(v) >= 8 else v
        return body.split(b"\x00", 1)[0].decode("ascii", "replace").strip() or None
    return _decode(v)


def _fix_status(proc_method: str | None, differential) -> str:
    pm = (proc_method or "").upper()
    if "FLOAT" in pm or "FLT" in pm:
        return "FLOAT"
    if "FIX" in pm:
        return "FIXED"
    if differential == 1:
        return "FIXED"
    if "DGPS" in pm or "DIFF" in pm:
        return "FLOAT"
    return "AUTONOMOUS"


def _capture_utc(gps: dict, exif_ifd: dict, notes, img_name):
    ds = _decode(gps.get(G.GPSDateStamp))           # "2024:06:15"
    ts = gps.get(G.GPSTimeStamp)                     # ((h,1),(m,1),(s,1))
    if ds and ts:
        try:
            h = int(_rat(ts[0])); m = int(_rat(ts[1])); s = int(_rat(ts[2]))
            return f"{ds.replace(':', '-')}T{h:02d}:{m:02d}:{s:02d}Z"
        except (TypeError, ValueError):
            pass
    dto = _decode(exif_ifd.get(EX.DateTimeOriginal))  # "2024:06:15 09:10:05" (local)
    if dto and " " in dto:
        date, time = dto.split(" ", 1)
        notes.append(f"{img_name}: no GPS UTC time; used DateTimeOriginal (local, no Z).")
        return f"{date.replace(':', '-')}T{time}"
    return None


def _read_image(path: Path, notes: list) -> dict:
    rec = {k: None for k in (
        "L1F_PP_001_image_id", "L1F_PP_002_geotag_position",
        "L1F_PP_003_per_geotag_fix_status", "L1F_PP_005_crs_in_exif",
        "L1F_PP_006_image_capture_utc", "L1F_PP_007_camera_serial")}
    rec["L1F_PP_001_image_id"] = path.name
    rec["_sigma_h_m"] = None
    rec["_camera_make_model"] = None
    try:
        ex = piexif.load(str(path))
    except Exception as exc:  # noqa: BLE001 - piexif raises bare Exception
        notes.append(f"{path.name}: EXIF unreadable ({exc}); image_id only.")
        return rec
    gps = ex.get("GPS", {}) or {}
    zeroth = ex.get("0th", {}) or {}
    exif_ifd = ex.get("Exif", {}) or {}

    if G.GPSLatitude in gps and G.GPSLongitude in gps:
        lat = _dms_to_deg(gps[G.GPSLatitude], gps.get(G.GPSLatitudeRef))
        lon = _dms_to_deg(gps[G.GPSLongitude], gps.get(G.GPSLongitudeRef))
        alt = _rat(gps.get(G.GPSAltitude)) if G.GPSAltitude in gps else None
        if alt is not None and gps.get(G.GPSAltitudeRef) == 1:
            alt = -alt
        rec["L1F_PP_002_geotag_position"] = {"lat": lat, "lon": lon, "alt": alt}
    else:
        notes.append(f"{path.name}: no GPS position in EXIF.")

    rec["L1F_PP_005_crs_in_exif"] = _decode(gps.get(G.GPSMapDatum))
    rec["L1F_PP_003_per_geotag_fix_status"] = _fix_status(
        _proc_method(gps.get(G.GPSProcessingMethod)), gps.get(G.GPSDifferential))
    rec["L1F_PP_006_image_capture_utc"] = _capture_utc(gps, exif_ifd, notes, path.name)
    rec["L1F_PP_007_camera_serial"] = _decode(exif_ifd.get(EX.BodySerialNumber))
    he = gps.get(G.GPSHPositioningError)
    rec["_sigma_h_m"] = round(_rat(he), 6) if he is not None else None
    make = _decode(zeroth.get(I.Make))
    model = _decode(zeroth.get(I.Model))
    rec["_camera_make_model"] = f"{make} {model}".strip() if (make or model) else None
    return rec


def parse(geotags_dir, project_root: Path | None = None,
          image_extensions=None, sidecar_extensions=None) -> dict[str, Any]:
    notes: list[str] = []
    img_exts = tuple(e.lower() for e in (image_extensions or DEFAULT_IMAGE_EXTS))
    side_exts = tuple(e.lower() for e in (sidecar_extensions or DEFAULT_SIDECAR_EXTS))
    gdir = Path(geotags_dir) if geotags_dir else None

    per_image: list[dict] = []
    sidecars: list[str] = []
    if gdir is None or not gdir.is_dir():
        notes.append(f"Geotags dir absent ({geotags_dir}); geotag_count=0. GEO block is CRITICAL "
                     "(Stage 1 hard-fails).")
    else:
        for f in sorted(gdir.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            suf = f.suffix.lower()
            if suf in img_exts:
                per_image.append(_read_image(f, notes))
            elif suf in side_exts:
                sidecars.append(f.name)
        if sidecars:
            notes.append(f"Sidecar(s) present {sidecars}: noted; EXIF is authoritative in v1 "
                         "(no sidecar merge needed for the EXIF-image baseline).")

    count = len(per_image)
    fixed = sum(1 for r in per_image if r["L1F_PP_003_per_geotag_fix_status"] == "FIXED")
    float_n = sum(1 for r in per_image if r["L1F_PP_003_per_geotag_fix_status"] == "FLOAT")
    auto_n = sum(1 for r in per_image if r["L1F_PP_003_per_geotag_fix_status"] == "AUTONOMOUS")
    crs_values = sorted({r["L1F_PP_005_crs_in_exif"] for r in per_image
                         if r["L1F_PP_005_crs_in_exif"]})
    serials = sorted({r["L1F_PP_007_camera_serial"] for r in per_image
                      if r["L1F_PP_007_camera_serial"]})
    cameras = sorted({r["_camera_make_model"] for r in per_image if r["_camera_make_model"]})
    no_pos = [r["L1F_PP_001_image_id"] for r in per_image
              if r["L1F_PP_002_geotag_position"] is None]

    fields = {
        "L1F_PP_004_geotag_count": count,
        "per_image": per_image,
    }
    parser_meta = {
        "parser_id": PARSER_ID,
        "parser_version": PARSER_VERSION,
        "source_file_id": SOURCE_FILE_ID,
        "source_file_name": SOURCE_FILE_NAME,
        "instance_found": count > 0 or bool(sidecars),
        "geotags_dir": str(gdir) if gdir else None,
        "image_count": count,
        "fix_status_distribution": {"FIXED": fixed, "FLOAT": float_n, "AUTONOMOUS": auto_n},
        "fixed_fraction_preview": round(fixed / count, 4) if count else None,
        "distinct_crs_in_exif": crs_values,
        "distinct_camera_serials": serials,
        "distinct_cameras": cameras,
        "images_without_position": no_pos,
        "sidecars_present": sidecars,
        "fields_provided": ["L1F_PP_001_image_id", "L1F_PP_002_geotag_position",
                            "L1F_PP_003_per_geotag_fix_status", "L1F_PP_004_geotag_count",
                            "L1F_PP_005_crs_in_exif", "L1F_PP_006_image_capture_utc",
                            "L1F_PP_007_camera_serial"],
        "notes": notes,
        "flags_raised": [],
    }
    return {"fields": fields, "parser_meta": parser_meta}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Parse a pre-processing geotag image set")
    parser.add_argument("geotags_dir")
    args = parser.parse_args(argv)
    out = parse(Path(args.geotags_dir), Path("."))
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
