#!/usr/bin/env python3
"""Generate the gold-standard PLACEHOLDER sample_data/ set for pre_processing.

NOT a pipeline stage - a one-off, deterministic generator (no RNG, no clock)
for a spec-faithful "healthy survey" that should score the apex ~100 and yield
verification_status = VERIFIED. Operators replace this with a real survey.

Writes:
  sample_data/pp_manifest.json        40-field processing manifest (_status PLACEHOLDER)
  sample_data/geotags/IMG_####.jpg    12 real JPEGs with EXIF GPS (piexif)
  sample_data/gcp_coords.csv          16 GCPs, UTM-43N, sigma within target
  sample_data/cp_coords.csv           20 CPs, independent + distributed + good sigma
  (processing report intentionally OMITTED -> exercises report-absent path)

Design notes (why these values reach 100 without tripping a gate):
  * CRS is modeled at the DATUM level (WGS84) and matches across artifacts ->
    ref_frame_declared = 100, no PP_WRONG_CRS_DATUM. The UTM zone lives in a
    SEPARATE projection field (projection_declared) -> no PP_WRONG_PROJECTION.
  * LOCAL_BASE_PPK path for geotag AND gcp -> base_pairing / baseline /
    session_overlap / antenna_pco all APPLY and pass; customer-supplied and
    report-tier indicators go N/A and redistribute (no penalty).
  * Geometry is verified in-script against the spec bands before writing.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import piexif
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data"
GEOTAGS = SAMPLE / "geotags"

# ---- site frame (projected, metres; UTM zone 43N / EPSG:32643) --------------
E0, N0, SIDE = 600000.0, 2000000.0, 3000.0          # 3 km x 3 km = 9 km^2
EXTENT_M2 = SIDE * SIDE
POLYGON = [(E0, N0), (E0 + SIDE, N0), (E0 + SIDE, N0 + SIDE), (E0, N0 + SIDE)]

GCP_INSET, GCP_NX, GCP_NY = 80.0, 4, 4               # 16 GCPs, hull ~89.6%
CP_INSET, CP_NX, CP_NY = 150.0, 5, 4                 # 20 CPs, hull ~81%, >=74 m from GCPs
GCP_SIGMA_H, GCP_SIGMA_V = 0.008, 0.014
CP_SIGMA_H, CP_SIGMA_V = 0.010, 0.017
ACCURACY_TARGET_M = 0.02

# ---- geotag image frame (geographic WGS84; EXIF GPS is always WGS84) --------
IMG_LAT0, IMG_LON0 = 18.0700, 75.9400               # near the UTM site, approx
IMG_DLAT, IMG_DLON = 0.0120, 0.0150
IMG_LINES, IMG_PER_LINE = 3, 4                       # 12 images
CAMERA_SERIAL = "P1SN77420019"
CAMERA_MAKE, CAMERA_MODEL, SOFTWARE = "DJI", "ZENMUSE P1", "Emlid Studio 1.9"
GEOTAG_SIGMA_H = 0.012


# ---- tiny 2-D geometry (previews scripts/parsers/geometry.py at Stage 3a) ----
def convex_hull(pts):
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return list(pts)
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def polygon_area(poly):
    a = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def min_pairwise(a, b):
    return min(((ax-bx)**2 + (ay-by)**2) ** 0.5 for ax, ay in a for bx, by in b)


def linspace(lo, hi, n):
    if n == 1:
        return [(lo + hi) / 2.0]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def grid(inset, nx, ny):
    xs = linspace(E0 + inset, E0 + SIDE - inset, nx)
    ys = linspace(N0 + inset, N0 + SIDE - inset, ny)
    return [(round(x, 3), round(y, 3)) for y in ys for x in xs]


# ---- EXIF helpers -----------------------------------------------------------
def dms_rational(dec):
    dec = abs(dec)
    d = int(dec)
    m = int((dec - d) * 60)
    s = (dec - d - m / 60.0) * 3600.0
    return ((d, 1), (m, 1), (int(round(s * 100)), 100))


def write_geotag(path, lat, lon, alt, when_hms):
    h, mi, se = when_hms
    zeroth = {
        piexif.ImageIFD.Make: CAMERA_MAKE,
        piexif.ImageIFD.Model: CAMERA_MODEL,
        piexif.ImageIFD.Software: SOFTWARE,
    }
    exif_ifd = {
        piexif.ExifIFD.DateTimeOriginal: f"2024:06:15 {h:02d}:{mi:02d}:{se:02d}",
        piexif.ExifIFD.BodySerialNumber: CAMERA_SERIAL,
    }
    gps_ifd = {
        piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
        piexif.GPSIFD.GPSLatitudeRef: "N",
        piexif.GPSIFD.GPSLatitude: dms_rational(lat),
        piexif.GPSIFD.GPSLongitudeRef: "E",
        piexif.GPSIFD.GPSLongitude: dms_rational(lon),
        piexif.GPSIFD.GPSAltitudeRef: 0,
        piexif.GPSIFD.GPSAltitude: (int(round(alt * 100)), 100),
        piexif.GPSIFD.GPSMapDatum: "WGS-84",
        piexif.GPSIFD.GPSProcessingMethod: b"ASCII\x00\x00\x00RTK FIXED",
        piexif.GPSIFD.GPSDifferential: 1,
        piexif.GPSIFD.GPSHPositioningError: (int(round(GEOTAG_SIGMA_H * 1000)), 1000),
        piexif.GPSIFD.GPSDateStamp: "2024:06:15",
        piexif.GPSIFD.GPSTimeStamp: ((h, 1), (mi, 1), (se, 1)),
    }
    exif_bytes = piexif.dump({"0th": zeroth, "Exif": exif_ifd, "GPS": gps_ifd})
    img = Image.new("RGB", (64, 64), (120, 125, 130))
    img.save(path, "jpeg", exif=exif_bytes, quality=70)


def build_manifest(geotag_count):
    return {
        "_status": "PLACEHOLDER",
        "_note": ("Gold-standard PLACEHOLDER manifest generated by "
                  "scripts/make_sample_data.py. Healthy LOCAL_BASE_PPK survey; "
                  "operator must overwrite with the real survey upload."),
        "project_required_crs": "WGS84",
        "project_required_geoid": "EGM2008",
        "project_required_height_mode": "orthometric",
        "project_required_units": "m",
        "project_required_projection": "UTM 43N",
        "accuracy_target_m": ACCURACY_TARGET_M,
        "declared_crs_per_artifact": {"geotag": "WGS84", "gcp": "WGS84", "cp": "WGS84"},
        "declared_geoid_per_artifact": {"geotag": "EGM2008", "gcp": "EGM2008", "cp": "EGM2008"},
        "declared_height_mode_per_artifact": {"geotag": "orthometric", "gcp": "orthometric", "cp": "orthometric"},
        "declared_units_per_artifact": {"geotag": "m", "gcp": "m", "cp": "m"},
        "declared_projection": "UTM 43N",
        "realization_epoch_per_artifact": {"geotag": "WGS84(G2139)@2024.0",
                                           "gcp": "WGS84(G2139)@2024.0",
                                           "cp": "WGS84(G2139)@2024.0"},
        "localization_applied_declared": False,
        "customer_supplied_coord_crs": None,
        "customer_accuracy_claim": None,
        "declared_path_geotag": "LOCAL_BASE_PPK",
        "declared_path_gcp": "LOCAL_BASE_PPK",
        "declared_path_cp": "LOCAL_BASE_PPK",
        "declared_software_per_artifact": {"geotag": "Emlid Studio", "gcp": "Emlid Studio", "cp": "Emlid Studio"},
        "declared_software_version_per_artifact": {"geotag": "1.9", "gcp": "1.9", "cp": "1.9"},
        "declared_antenna_per_artifact": {"base": "TRM59800.00", "drone": "DJI P1 internal"},
        "baseline_length_km": 2.5,
        "captured_image_count": geotag_count,
        "planned_forward_overlap": 80,
        "planned_side_overlap": 70,
        "site_cover_declared": "open",
        "dtm_in_deliverables": True,
        "target_size_cm": 50,
        "planned_gsd_cm": 2.5,
        "target_type": "checkerboard",
        "base_file_id": "BASE0615",
        "drone_session_start_utc": "2024-06-15T09:05:00Z",
        "drone_session_end_utc": "2024-06-15T09:55:00Z",
        "base_session_start_utc": "2024-06-15T08:30:00Z",
        "base_session_end_utc": "2024-06-15T10:30:00Z",
        "gcp_coord_determination_date": "2024-06-10",
        "flight_date": "2024-06-15",
        "reconstruction_extent_m2": EXTENT_M2,
        "reconstruction_extent_polygon": [[round(x, 1), round(y, 1)] for x, y in POLYGON],
        "flight_conditions_declared": "clear",
    }


def write_coords(path, prefix, pts, sig_h, sig_v):
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("# CRS: WGS84 / UTM zone 43N (EPSG:32643)\n")
        fh.write("# geoid: EGM2008\n# height_mode: orthometric\n# units: m\n")
        fh.write("# _status: PLACEHOLDER\n")
        w = csv.writer(fh)
        w.writerow(["point_id", "easting", "northing", "elevation", "sigma_h", "sigma_v"])
        for i, (e, n) in enumerate(pts, 1):
            elev = round(540.0 + ((i * 7) % 25) * 0.1, 3)
            w.writerow([f"{prefix}{i:02d}", f"{e:.3f}", f"{n:.3f}", f"{elev:.3f}",
                        f"{sig_h:.3f}", f"{sig_v:.3f}"])


def main():
    SAMPLE.mkdir(exist_ok=True)
    GEOTAGS.mkdir(exist_ok=True)
    gcps = grid(GCP_INSET, GCP_NX, GCP_NY)
    cps = grid(CP_INSET, CP_NX, CP_NY)

    # ---- verify the geometry against the spec bands BEFORE writing ----------
    gcp_cov = polygon_area(convex_hull(gcps)) / EXTENT_M2
    cp_cov = polygon_area(convex_hull(cps)) / EXTENT_M2
    min_dist = min_pairwise(cps, gcps)
    ex = [e for e, _ in POLYGON]
    ny_ = [n for _, n in POLYGON]
    inside = all(min(ex) <= e <= max(ex) and min(ny_) <= n <= max(ny_) for e, n in gcps + cps)

    checks = {
        "gcp_count>=adequate(16)": (len(gcps), len(gcps) == 16),
        "cp_count>=20": (len(cps), len(cps) >= 20),
        "gcp_hull_coverage>=0.80": (round(gcp_cov, 4), gcp_cov >= 0.80),
        "cp_hull_coverage>=0.80": (round(cp_cov, 4), cp_cov >= 0.80),
        "min_cp_gcp_dist>=50m": (round(min_dist, 2), min_dist >= 50.0),
        "all_points_in_polygon": (inside, inside),
        "gcp_sigma<=target": (GCP_SIGMA_H, GCP_SIGMA_H <= ACCURACY_TARGET_M),
        "cp_sigma<=target": (CP_SIGMA_H, CP_SIGMA_H <= ACCURACY_TARGET_M),
    }
    print("== geometry / band self-verification ==")
    ok = True
    for k, (val, passed) in checks.items():
        print(f"  [{'OK ' if passed else 'XX '}] {k:32s} = {val}")
        ok = ok and passed
    if not ok:
        raise SystemExit("FATAL: gold-standard geometry does not clear the spec bands - fix params.")

    # ---- write coord files + manifest --------------------------------------
    write_coords(SAMPLE / "gcp_coords.csv", "GCP", gcps, GCP_SIGMA_H, GCP_SIGMA_V)
    write_coords(SAMPLE / "cp_coords.csv", "CP", cps, CP_SIGMA_H, CP_SIGMA_V)

    # ---- write 12 geotag JPEGs ---------------------------------------------
    n_img = IMG_LINES * IMG_PER_LINE
    lats = linspace(IMG_LAT0, IMG_LAT0 + IMG_DLAT, IMG_LINES)
    lons = linspace(IMG_LON0, IMG_LON0 + IMG_DLON, IMG_PER_LINE)
    k = 0
    for li, lat in enumerate(lats):
        for ci, lon in enumerate(lons):
            k += 1
            minute = 10 + (k - 1) * 3
            write_geotag(GEOTAGS / f"IMG_{k:04d}.jpg", lat, lon, 540.0,
                         (9, minute % 60 if minute < 60 else minute - 60, (k * 5) % 60))
    manifest = build_manifest(n_img)
    with (SAMPLE / "pp_manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print("\n== written ==")
    print(f"  sample_data/pp_manifest.json   ({len(manifest)-2} fields + _status/_note)")
    print(f"  sample_data/geotags/           {n_img} JPEGs (all RTK FIXED, serial {CAMERA_SERIAL})")
    print(f"  sample_data/gcp_coords.csv     {len(gcps)} GCPs (sigma_h={GCP_SIGMA_H})")
    print(f"  sample_data/cp_coords.csv      {len(cps)} CPs  (sigma_h={CP_SIGMA_H})")
    print("  (processing report omitted -> report-absent redistribution path)")


if __name__ == "__main__":
    main()
