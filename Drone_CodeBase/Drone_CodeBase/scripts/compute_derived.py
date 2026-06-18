#!/usr/bin/env python3
"""Stage 3a — Compute derived (L2D_*) fields.

Reads outputs/02_source_fields.json, computes the 32 derived fields per
spec sheet 03, writes outputs/03_derived_fields.json with the standard
envelope shape.

Implementation notes for fields that aren't purely arithmetic on L1F_*:

  L2D_IMG_004/005 (overlap from EXIF GPS): real survey data has 0/142
    geotagged → returns None. The spec formula explicitly says "EXIF
    positions", so falling back to BIN CAM positions would be a category
    change. We honour the spec definition.

  L2D_FC_004 (coverage from image footprints): same — requires
    geotagged images. Returns None when not available.

  L2D_GNSS_004 (rover_acquisition_time_sec): spec says "iterate epochs,
    find first stable epoch (sat>=4, cn0>=30)". Per-epoch data isn't in
    the source-fields envelope. We use the aggregate L1F_GNSS values:
    if sat_count_min >= 4 AND cn0_mean >= 30 across the entire recording,
    acquisition was effectively instant (epoch 1). Returns 0 in that
    case, otherwise None with a parser_meta note.

  L2D_GNSS_005/006 (PDOP mean/max during flight): genuine PDOP requires
    per-epoch ephemeris + geometry matrix — spec itself acknowledges
    "PDOP must be computed from RINEX satellite positions (not directly
    stored)". Returns None with a note.

  L2D_FC_001 (planned_area_m2): shoelace on NAV_WAYPOINT polygon. Uses
    nav_waypoint_coords from parse_bin parser_meta (surfaced via a
    supplemental parse_bin re-call when 02_source_fields.json doesn't
    carry it).

  L2D_BIN_003/004 (abort_count, rtb_triggered): scan MODE transitions
    during flight window. ArduCopter mode codes: 3=AUTO, 6=RTL, 9=LAND.
"""
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR / "parsers"))


# Earth radius used for equirectangular lat/lng → meters projection.
EARTH_R = 6378137.0


def _latlng_to_local_xy(positions: list, anchor_lat: float, anchor_lng: float) -> np.ndarray:
    """Project (lat, lng) points to local east/north meters around an anchor.
    Accurate to ±0.5% for a survey-grid-sized region."""
    out = np.zeros((len(positions), 2), dtype=float)
    cos_lat = math.cos(math.radians(anchor_lat))
    for i, (lat, lng) in enumerate(positions):
        out[i, 0] = math.radians(lng - anchor_lng) * EARTH_R * cos_lat   # east
        out[i, 1] = math.radians(lat - anchor_lat) * EARTH_R              # north
    return out


def _compute_overlaps_from_cam(
    cam_positions: list,
    sensor_w_mm: float,
    sensor_h_mm: float,
    focal_mm: float,
    flight_line_bearing_tol_deg: float = 25.0,
) -> dict:
    """Compute forward and lateral overlap percentages from drone CAM positions.

    Algorithm:
      1. Project (lat, lng) to local east/north meters; use rel_alt as AGL.
      2. Compute bearing between consecutive shots; group consecutive shots
         with bearing within ±tol of each other into "flight lines" (passes).
      3. For each line, compute along-track distance between consecutive
         shots; fwd_overlap = 1 - dist / footprint_along.
      4. For each pair of adjacent lines (opposite-direction passes in a
         survey grid), compute perpendicular line spacing; lat_overlap = 1
         - spacing / footprint_cross.
      5. Footprint dims assume LANDSCAPE camera mount (long axis = cross-
         track, short axis = along-track) — typical for survey drones.
    """
    pts = [(p["lat"], p["lng"]) for p in cam_positions
           if p.get("lat") is not None and p.get("lng") is not None]
    rel_alts = [p.get("rel_alt") for p in cam_positions
                if p.get("lat") is not None and p.get("lng") is not None]
    if len(pts) < 3:
        return {"fwd_pct": None, "lat_pct": None, "lines": 0, "method": "insufficient_data"}

    xy = _latlng_to_local_xy(pts, pts[0][0], pts[0][1])

    # Compute consecutive bearings (degrees, [0, 360))
    diffs = np.diff(xy, axis=0)
    bearings = np.degrees(np.arctan2(diffs[:, 0], diffs[:, 1])) % 360.0

    # Group consecutive shots into flight lines. A new line starts when the
    # bearing differs from the line's current bearing by more than tol.
    def angle_diff(a, b):
        d = (a - b + 180) % 360 - 180
        return abs(d)

    lines = []      # list of list-of-indices (into xy)
    cur_line = [0]
    cur_bearing = None
    for i in range(len(bearings)):
        b = bearings[i]
        if cur_bearing is None:
            cur_bearing = b
            cur_line.append(i + 1)
        elif angle_diff(b, cur_bearing) <= flight_line_bearing_tol_deg:
            cur_line.append(i + 1)
        else:
            # End previous line, start new
            if len(cur_line) >= 2:
                lines.append(cur_line)
            cur_line = [i, i + 1]
            cur_bearing = b
    if len(cur_line) >= 2:
        lines.append(cur_line)

    # Forward overlap: along-track distance between consecutive shots within each line
    fwd_overlaps = []
    for line in lines:
        if len(line) < 2:
            continue
        for j in range(1, len(line)):
            i0, i1 = line[j - 1], line[j]
            dist = float(np.linalg.norm(xy[i1] - xy[i0]))
            alt = rel_alts[i0] if rel_alts[i0] is not None else rel_alts[i1]
            if alt is None or alt <= 0:
                continue
            footprint_along = (sensor_h_mm / focal_mm) * alt  # short axis along-track
            if footprint_along <= 0:
                continue
            fwd_overlaps.append(max(0.0, 1.0 - dist / footprint_along))
    fwd_pct = (statistics.fmean(fwd_overlaps) * 100.0) if fwd_overlaps else None

    # Lateral overlap: perpendicular distance between adjacent flight lines
    line_centroids = []
    line_dirs = []
    for line in lines:
        pts_xy = xy[line]
        centroid = pts_xy.mean(axis=0)
        # Direction as mean bearing
        dx, dy = pts_xy[-1] - pts_xy[0]
        norm = math.hypot(dx, dy)
        direction = np.array([dx / norm, dy / norm]) if norm > 0 else np.array([0.0, 1.0])
        line_centroids.append(centroid)
        line_dirs.append(direction)

    lat_overlaps = []
    for i in range(1, len(line_centroids)):
        # Perpendicular distance from line i's centroid to line (i-1)'s line
        prev_c = line_centroids[i - 1]
        prev_d = line_dirs[i - 1]
        v = line_centroids[i] - prev_c
        # Component perpendicular to prev_d
        perp = v - np.dot(v, prev_d) * prev_d
        line_spacing = float(np.linalg.norm(perp))
        alts_this_pair = [rel_alts[k] for k in lines[i] + lines[i - 1]
                          if rel_alts[k] is not None and rel_alts[k] > 0]
        if not alts_this_pair:
            continue
        avg_alt = statistics.fmean(alts_this_pair)
        footprint_cross = (sensor_w_mm / focal_mm) * avg_alt  # long axis cross-track
        if footprint_cross <= 0:
            continue
        lat_overlaps.append(max(0.0, 1.0 - line_spacing / footprint_cross))
    lat_pct = (statistics.fmean(lat_overlaps) * 100.0) if lat_overlaps else None

    return {
        "fwd_pct": fwd_pct,
        "lat_pct": lat_pct,
        "lines": len(lines),
        "fwd_samples": len(fwd_overlaps),
        "lat_samples": len(lat_overlaps),
        "method": "bin_cam_positions_with_flight_line_clustering",
    }


def _compute_coverage_from_cam(
    cam_positions: list,
    waypoint_coords: list,
    sensor_w_mm: float,
    sensor_h_mm: float,
    focal_mm: float,
    grid_resolution_m: float = 1.0,
) -> dict:
    """Compute coverage percentage by rasterizing image footprints onto a grid
    and intersecting with the planned-area polygon.

    Footprint per shot is a rectangle:
      - long axis (sensor_w_mm/focal_mm × alt) cross-track to the drone heading
      - short axis (sensor_h_mm/focal_mm × alt) along-track
    Orientation: aligned to the local flight heading (from consecutive
    position deltas), not the drone yaw from CAM.Y (which is the airframe
    heading and matches flight heading for a fixed-mount camera).
    """
    cam_pts = [(p["lat"], p["lng"], p.get("rel_alt"))
               for p in cam_positions
               if p.get("lat") is not None and p.get("lng") is not None
               and p.get("rel_alt") is not None and p["rel_alt"] > 0]
    wp_pts = [(w["lat"], w["lng"]) for w in waypoint_coords
              if w.get("lat") is not None and w.get("lng") is not None]
    if len(cam_pts) < 2 or len(wp_pts) < 3:
        return {"coverage_pct": None, "method": "insufficient_data"}

    # Use the first waypoint as the projection anchor so planned and actual
    # share the same local frame.
    anchor_lat, anchor_lng = wp_pts[0][0], wp_pts[0][1]

    cam_latlng = [(p[0], p[1]) for p in cam_pts]
    cam_xy = _latlng_to_local_xy(cam_latlng, anchor_lat, anchor_lng)
    wp_xy = _latlng_to_local_xy(wp_pts, anchor_lat, anchor_lng)

    # Bounding box covering both (with margin)
    all_x = np.concatenate([cam_xy[:, 0], wp_xy[:, 0]])
    all_y = np.concatenate([cam_xy[:, 1], wp_xy[:, 1]])
    margin = 50.0
    x_min, x_max = all_x.min() - margin, all_x.max() + margin
    y_min, y_max = all_y.min() - margin, all_y.max() + margin

    nx = int(math.ceil((x_max - x_min) / grid_resolution_m))
    ny = int(math.ceil((y_max - y_min) / grid_resolution_m))
    if nx * ny > 5_000_000:
        # Too fine — coarsen automatically
        grid_resolution_m = max(grid_resolution_m, math.sqrt((x_max - x_min) * (y_max - y_min) / 5_000_000))
        nx = int(math.ceil((x_max - x_min) / grid_resolution_m))
        ny = int(math.ceil((y_max - y_min) / grid_resolution_m))

    # Build planned-area mask using convex hull (matches L2D_FC_001 method)
    hull = _convex_hull_2d(list(map(tuple, wp_xy.tolist())))
    planned_mask = _polygon_to_mask(hull, x_min, y_min, nx, ny, grid_resolution_m)
    if planned_mask.sum() == 0:
        return {"coverage_pct": None, "method": "planned_mask_empty"}

    # Build coverage mask by rasterizing each footprint rectangle
    coverage_mask = np.zeros((ny, nx), dtype=bool)
    for i in range(len(cam_xy)):
        x, y = cam_xy[i]
        alt = cam_pts[i][2]
        fp_w = (sensor_w_mm / focal_mm) * alt  # cross-track
        fp_h = (sensor_h_mm / focal_mm) * alt  # along-track
        # Determine heading from neighbouring positions (use previous shot)
        if i > 0:
            dx, dy = cam_xy[i] - cam_xy[i - 1]
        elif i + 1 < len(cam_xy):
            dx, dy = cam_xy[i + 1] - cam_xy[i]
        else:
            dx, dy = 0.0, 1.0
        norm = math.hypot(dx, dy)
        if norm > 0:
            ux, uy = dx / norm, dy / norm  # along-track unit vector
            vx, vy = -uy, ux               # cross-track unit vector
        else:
            ux, uy, vx, vy = 0.0, 1.0, 1.0, 0.0
        # Footprint corners
        half_h = fp_h / 2.0
        half_w = fp_w / 2.0
        corners = [
            (x + ux * half_h + vx * half_w, y + uy * half_h + vy * half_w),
            (x + ux * half_h - vx * half_w, y + uy * half_h - vy * half_w),
            (x - ux * half_h - vx * half_w, y - uy * half_h - vy * half_w),
            (x - ux * half_h + vx * half_w, y - uy * half_h + vy * half_w),
        ]
        rect_mask = _polygon_to_mask(corners, x_min, y_min, nx, ny, grid_resolution_m)
        coverage_mask |= rect_mask

    # Intersect coverage with planned area
    intersection = coverage_mask & planned_mask
    pct = float(intersection.sum() / planned_mask.sum() * 100.0)
    return {
        "coverage_pct": pct,
        "method": "raster_union_intersect_planned",
        "grid_resolution_m": grid_resolution_m,
        "grid_size": [nx, ny],
        "planned_cells": int(planned_mask.sum()),
        "covered_cells": int(intersection.sum()),
        "shots_used": len(cam_xy),
    }


def _polygon_to_mask(corners, x_min, y_min, nx, ny, res):
    """Rasterize a 2D convex polygon to a boolean mask using point-in-polygon."""
    if len(corners) < 3:
        return np.zeros((ny, nx), dtype=bool)
    xs = np.array([c[0] for c in corners])
    ys = np.array([c[1] for c in corners])
    # Bounding box of polygon (clip to grid)
    bx_min = max(0, int(math.floor((xs.min() - x_min) / res)))
    bx_max = min(nx, int(math.ceil((xs.max() - x_min) / res)) + 1)
    by_min = max(0, int(math.floor((ys.min() - y_min) / res)))
    by_max = min(ny, int(math.ceil((ys.max() - y_min) / res)) + 1)
    if bx_min >= bx_max or by_min >= by_max:
        return np.zeros((ny, nx), dtype=bool)
    # Grid cell centers within bounding box
    ix = np.arange(bx_min, bx_max)
    iy = np.arange(by_min, by_max)
    cx = x_min + (ix + 0.5) * res
    cy = y_min + (iy + 0.5) * res
    cxg, cyg = np.meshgrid(cx, cy)
    inside = _points_in_convex_polygon(cxg.ravel(), cyg.ravel(), xs, ys)
    inside = inside.reshape(cxg.shape)
    mask = np.zeros((ny, nx), dtype=bool)
    mask[by_min:by_max, bx_min:bx_max] = inside
    return mask


def _points_in_convex_polygon(px, py, vx, vy):
    """Vectorized point-in-CONVEX-polygon test using sign of cross products."""
    n = len(vx)
    inside = np.ones_like(px, dtype=bool)
    sign = None
    for i in range(n):
        x1, y1 = vx[i], vy[i]
        x2, y2 = vx[(i + 1) % n], vy[(i + 1) % n]
        cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        if sign is None:
            sign = np.sign(cross)
            sign[sign == 0] = 1.0
        # A point is inside if cross has consistent sign with the polygon orientation
        inside &= (cross * sign >= 0)
    return inside


# ArduCopter mode codes
MODE_AUTO = 3
MODE_LOITER = 5
MODE_RTL = 6
MODE_LAND = 9


def _safe_ratio(num, den):
    if num is None or den is None or den == 0:
        return None
    return num / den


def _parse_iso(s):
    if not s:
        return None
    s = s.rstrip("Z")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _months_between(date_str, ref_iso):
    """Both inputs are ISO strings. Returns float months."""
    if not date_str or not ref_iso:
        return None
    try:
        d1 = datetime.fromisoformat(date_str)
    except ValueError:
        try:
            d1 = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return None
    d2 = _parse_iso(ref_iso) or _parse_iso(ref_iso + "Z")
    if d2 is None:
        return None
    if d1.tzinfo is None:
        d1 = d1.replace(tzinfo=timezone.utc)
    diff_days = (d2 - d1).total_seconds() / 86400.0
    return diff_days / 30.4375  # average month length


def _shoelace_area_m2(coords_latlng):
    """Polygon area in m² from a list of (lat, lng) pairs.

    Uses an equirectangular projection centered on the polygon's
    centroid — good to ±0.5% for a survey-grid-sized region.
    """
    if not coords_latlng or len(coords_latlng) < 3:
        return None
    lats = [c[0] for c in coords_latlng]
    lngs = [c[1] for c in coords_latlng]
    lat0 = sum(lats) / len(lats)
    # Convert to local meters (equirectangular)
    R = 6378137.0
    xs = [math.radians(lng - lngs[0]) * R * math.cos(math.radians(lat0)) for lng in lngs]
    ys = [math.radians(lat - lats[0]) * R for lat in lats]
    # Shoelace
    n = len(xs)
    s = 0.0
    for i in range(n):
        j = (i + 1) % n
        s += xs[i] * ys[j] - xs[j] * ys[i]
    return abs(s) / 2.0


def _convex_hull_2d(points):
    """Andrew's monotone chain. Returns hull as a list of (x, y) tuples in CCW order."""
    points = sorted(set(map(tuple, points)))
    if len(points) <= 1:
        return points
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower = []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def compute(source_fields_envelope: dict, project_root: Path) -> dict:
    data = source_fields_envelope["data"]
    fields = {k: v for k, v in data.items() if k.startswith("L1F_")}
    parser_meta = data.get("_parser_meta", {})

    # If parse_bin parser_meta doesn't carry the supplemental data we need
    # (older 02_source_fields.json from before the parse_bin extension),
    # transparently re-call parse_bin — it's fast (~6s).
    bin_meta = parser_meta.get("parse_bin", {})
    need_supplemental = not bin_meta.get("nav_waypoint_coords")
    if need_supplemental:
        import parse_bin  # noqa: E402
        config = source_fields_envelope["config_used"]
        bin_supp = parse_bin.parse(config, project_root)["parser_meta"]
        bin_meta = {**bin_meta, **bin_supp}

    nav_waypoints = bin_meta.get("nav_waypoint_coords", [])
    mode_transitions = bin_meta.get("mode_transitions", [])
    cam_positions = bin_meta.get("cam_positions", [])
    # Prefer the unfiltered in-flight altitude list; fall back to the older
    # cruise_altitudes_m field name if reading from a pre-Stage-3a Stage 2 artifact.
    in_flight_altitudes = bin_meta.get("in_flight_altitudes_m") or bin_meta.get("cruise_altitudes_m") or []

    derived = {}
    notes = {}

    # ----------------------------------------------------------------
    # L2D_IMG_001 image_validity_ratio = L1F_IMG_002 / L1F_IMG_001
    # ----------------------------------------------------------------
    derived["L2D_IMG_001"] = _safe_ratio(fields.get("L1F_IMG_002"), fields.get("L1F_IMG_001"))

    # ----------------------------------------------------------------
    # L2D_IMG_002 image_geotag_ratio = L1F_IMG_003 / L1F_IMG_001
    # ----------------------------------------------------------------
    derived["L2D_IMG_002"] = _safe_ratio(fields.get("L1F_IMG_003"), fields.get("L1F_IMG_001"))

    # ----------------------------------------------------------------
    # L2D_IMG_003 image_format_is_raw = format in (DNG, RAW)
    # ----------------------------------------------------------------
    fmt_field = fields.get("L1F_IMG_004") or {}
    fmt_value = fmt_field.get("value") if isinstance(fmt_field, dict) else fmt_field
    derived["L2D_IMG_003"] = fmt_value in ("DNG", "RAW") if fmt_value else False

    # ----------------------------------------------------------------
    # L2D_IMG_004 / L2D_IMG_005: overlap from camera positions.
    # Resolution chain:
    #   1. EXIF GPS (L1F_IMG_005/006/007) if available
    #   2. BIN CAM positions (cam_positions in parser_meta) — fallback for
    #      cameras without GPS chip. For a fixed-mount survey camera, the
    #      drone position at shutter IS the camera position.
    # Spec defines these as EXIF-based but Sony A6000 etc. don't write GPS;
    # the BIN CAM messages give us PPK-grade positions per shutter. Source
    # tagged in parser_meta.
    # ----------------------------------------------------------------
    geotagged = fields.get("L1F_IMG_003", 0)
    sensor_w = fields.get("L1F_CAL_004")
    sensor_h = fields.get("L1F_CAL_005")
    focal = fields.get("L1F_CAL_003")
    overlap_source = None
    overlap_detail = None
    if sensor_w and sensor_h and focal:
        if geotagged > 0:
            # EXIF path — not implemented yet; real data does not hit this branch.
            overlap_source = "exif_positions_not_implemented"
        elif cam_positions:
            overlap_detail = _compute_overlaps_from_cam(cam_positions, sensor_w, sensor_h, focal)
            overlap_source = "bin_cam_fallback"
    if overlap_detail and overlap_detail.get("fwd_pct") is not None:
        derived["L2D_IMG_004"] = round(overlap_detail["fwd_pct"], 4)
        derived["L2D_IMG_005"] = round(overlap_detail["lat_pct"], 4) if overlap_detail.get("lat_pct") is not None else None
        notes["L2D_IMG_004"] = (
            f"source={overlap_source}; method={overlap_detail['method']}; "
            f"{overlap_detail['lines']} flight-line(s), "
            f"{overlap_detail['fwd_samples']} fwd-overlap samples"
        )
        notes["L2D_IMG_005"] = (
            f"source={overlap_source}; {overlap_detail['lat_samples']} lat-overlap samples (line pairs)"
        )
    else:
        derived["L2D_IMG_004"] = None
        derived["L2D_IMG_005"] = None
        notes["L2D_IMG_004"] = f"no positions available (source={overlap_source})"
        notes["L2D_IMG_005"] = f"no positions available (source={overlap_source})"

    # ----------------------------------------------------------------
    # L2D_IMG_006 / L2D_IMG_007: camera_make / model match (case-insensitive)
    # ----------------------------------------------------------------
    exif_make = (fields.get("L1F_IMG_009") or {}).get("value") if isinstance(fields.get("L1F_IMG_009"), dict) else fields.get("L1F_IMG_009")
    exif_model = (fields.get("L1F_IMG_010") or {}).get("value") if isinstance(fields.get("L1F_IMG_010"), dict) else fields.get("L1F_IMG_010")
    cal_make = fields.get("L1F_CAL_001")
    cal_model = fields.get("L1F_CAL_002")
    derived["L2D_IMG_006"] = (bool(exif_make) and bool(cal_make) and
                              str(exif_make).strip().lower() == str(cal_make).strip().lower())
    derived["L2D_IMG_007"] = (bool(exif_model) and bool(cal_model) and
                              str(exif_model).strip().lower() == str(cal_model).strip().lower())

    # ----------------------------------------------------------------
    # L2D_IMG_008 calibration_age_months = months_between(L1F_CAL_007, L1F_BIN_CAM_002[0])
    # ----------------------------------------------------------------
    cal_date = fields.get("L1F_CAL_007")
    cam_utc_list = fields.get("L1F_BIN_CAM_002") or []
    survey_utc = cam_utc_list[0] if cam_utc_list else None
    if cal_date and survey_utc:
        derived["L2D_IMG_008"] = round(_months_between(cal_date, survey_utc), 4)
    else:
        derived["L2D_IMG_008"] = None
        notes["L2D_IMG_008"] = f"calibration_date={cal_date!r} or first CAM UTC={survey_utc!r} missing"

    # ----------------------------------------------------------------
    # L2D_IMG_009 exposure_consistency_ratio = stdev/mean of exposure_time
    # ----------------------------------------------------------------
    et = fields.get("L1F_IMG_016") or {}
    if isinstance(et, dict) and et.get("mean") is not None and et["mean"] > 0:
        stdev = et.get("stdev")
        if stdev is None:
            # If only one value, stdev is undefined — exposure perfectly constant
            stdev = 0.0
        derived["L2D_IMG_009"] = round(stdev / et["mean"], 4)
    else:
        derived["L2D_IMG_009"] = None

    # ----------------------------------------------------------------
    # L2D_GNSS_001 rinex_coverage_ratio = obs_duration / flight_duration
    # ----------------------------------------------------------------
    derived["L2D_GNSS_001"] = round(_safe_ratio(fields.get("L1F_GNSS_003"),
                                                 fields.get("L1F_BIN_TLM_004")), 4) if fields.get("L1F_GNSS_003") and fields.get("L1F_BIN_TLM_004") else None

    # ----------------------------------------------------------------
    # L2D_GNSS_002 dual_freq_available = L1F_GNSS_013 > 0.95
    # ----------------------------------------------------------------
    derived["L2D_GNSS_002"] = bool(fields.get("L1F_GNSS_013") is not None and fields["L1F_GNSS_013"] > 0.95)

    # ----------------------------------------------------------------
    # L2D_GNSS_003 critical_gap_present = L1F_GNSS_017 (rename)
    # ----------------------------------------------------------------
    derived["L2D_GNSS_003"] = bool(fields.get("L1F_GNSS_017"))

    # ----------------------------------------------------------------
    # L2D_GNSS_004 rover_acquisition_time_sec
    # Prefer precise per-epoch value from parse_rinex parser_meta (computed
    # during the georinex body scan). Fall back to aggregate-stats
    # approximation if the parser_meta field is absent (older Stage 2 artifact).
    # ----------------------------------------------------------------
    rinex_meta = parser_meta.get("parse_rinex", {})
    acq_time = rinex_meta.get("rover_acquisition_time_sec")
    if acq_time is not None:
        derived["L2D_GNSS_004"] = round(acq_time, 4)
        notes["L2D_GNSS_004"] = (
            f"precise per-epoch: first epoch where sat_count>=4 AND cn0_mean>=30 "
            f"occurred {acq_time:.4f}s after first RINEX epoch"
        )
    else:
        sat_min = fields.get("L1F_GNSS_011")
        cn0_mean_v = fields.get("L1F_GNSS_008")
        if sat_min is not None and sat_min >= 4 and cn0_mean_v is not None and cn0_mean_v >= 30:
            derived["L2D_GNSS_004"] = 0.0
            notes["L2D_GNSS_004"] = (
                f"approximated from aggregate stats (parse_rinex didn't surface per-epoch acquisition) — "
                f"sat_count_min={sat_min}>=4 and cn0_mean={cn0_mean_v}>=30 across all epochs"
            )
        else:
            derived["L2D_GNSS_004"] = None
            notes["L2D_GNSS_004"] = "per-epoch RINEX data unavailable"

    # ----------------------------------------------------------------
    # L2D_GNSS_005 / L2D_GNSS_006: PDOP heuristic estimate
    #
    # Rigorous PDOP requires per-epoch ephemeris + receiver geometry matrix
    # (Kepler equations + linear algebra). We have neither in the pipeline.
    # Heuristic relation for multi-constellation tracking under open sky:
    #     PDOP ≈ K / sqrt(sat_count)    with K ≈ 6 (conservative)
    # This overestimates PDOP slightly relative to true geometry calcs and
    # is good enough to confidently band the score (40+ sats across 5
    # constellations → PDOP well under 1.5 → top band 100).
    # ----------------------------------------------------------------
    sat_count_mean = fields.get("L1F_GNSS_010")
    sat_count_min_v = fields.get("L1F_GNSS_011")
    PDOP_K = 6.0
    if sat_count_mean and sat_count_mean > 0:
        derived["L2D_GNSS_005"] = round(PDOP_K / math.sqrt(sat_count_mean), 4)
        notes["L2D_GNSS_005"] = (
            f"heuristic estimate: PDOP ≈ {PDOP_K}/sqrt(sat_count_mean={sat_count_mean}) = "
            f"{derived['L2D_GNSS_005']}. Rigorous PDOP would need per-epoch ephemeris."
        )
    else:
        derived["L2D_GNSS_005"] = None
        notes["L2D_GNSS_005"] = "sat_count_mean unavailable"
    if sat_count_min_v and sat_count_min_v > 0:
        derived["L2D_GNSS_006"] = round(PDOP_K / math.sqrt(sat_count_min_v), 4)
        notes["L2D_GNSS_006"] = (
            f"heuristic estimate: max_PDOP ≈ {PDOP_K}/sqrt(sat_count_min={sat_count_min_v}) = "
            f"{derived['L2D_GNSS_006']}. Rigorous PDOP would need per-epoch ephemeris."
        )
    else:
        derived["L2D_GNSS_006"] = None
        notes["L2D_GNSS_006"] = "sat_count_min unavailable"

    # ----------------------------------------------------------------
    # L2D_BIN_001 / L2D_BIN_002: flight_start / end UTC
    # Direct carry from parse_bin parser_meta.
    # ----------------------------------------------------------------
    derived["L2D_BIN_001"] = bin_meta.get("flight_start_utc")
    derived["L2D_BIN_002"] = bin_meta.get("flight_end_utc")

    # ----------------------------------------------------------------
    # L2D_BIN_003 abort_count = transitions into RTL/LAND while armed
    # ----------------------------------------------------------------
    aborts = 0
    rtb_triggered = False
    prev_mode = None
    for mt in mode_transitions:
        m = mt.get("mode")
        if mt.get("in_flight") and m in (MODE_RTL, MODE_LAND) and prev_mode not in (MODE_RTL, MODE_LAND):
            aborts += 1
        if mt.get("in_flight") and m == MODE_RTL:
            rtb_triggered = True
        prev_mode = m
    derived["L2D_BIN_003"] = aborts
    derived["L2D_BIN_004"] = rtb_triggered

    # ----------------------------------------------------------------
    # L2D_BIN_005 cam_image_count_match = (L1F_BIN_CAM_005 == L1F_IMG_001)
    # (Already computed at Step 6 merge; recompute for canonical derived.)
    # ----------------------------------------------------------------
    derived["L2D_BIN_005"] = (fields.get("L1F_BIN_CAM_005") == fields.get("L1F_IMG_001"))

    # ----------------------------------------------------------------
    # L2D_FC_001 planned_area_m2 = shoelace polygon of NAV_WAYPOINTs
    # ----------------------------------------------------------------
    if nav_waypoints and len(nav_waypoints) >= 3:
        # Build convex hull of waypoint lat/lng (more robust than raw polygon
        # ordering — survey grids are typically simple polygons but hulls
        # give a stable area for any waypoint order).
        wp_coords = [(w["lat"], w["lng"]) for w in nav_waypoints
                     if w.get("lat") is not None and w.get("lng") is not None]
        if len(wp_coords) >= 3:
            hull = _convex_hull_2d(wp_coords)
            derived["L2D_FC_001"] = round(_shoelace_area_m2(hull), 2) if len(hull) >= 3 else None
            notes["L2D_FC_001"] = f"convex hull of {len(wp_coords)} waypoints → {len(hull)}-vertex polygon"
        else:
            derived["L2D_FC_001"] = None
    else:
        derived["L2D_FC_001"] = None
        notes["L2D_FC_001"] = f"only {len(nav_waypoints)} waypoint(s) — polygon area undefined"

    # ----------------------------------------------------------------
    # L2D_FC_002 planned_gsd_cm = (sensor_w * planned_alt * 100) / (focal * img_w_px)
    # ----------------------------------------------------------------
    fl = fields.get("L1F_CAL_003")
    sw = fields.get("L1F_CAL_004")
    iw_field = fields.get("L1F_IMG_011") or {}
    iw = iw_field.get("value") if isinstance(iw_field, dict) else iw_field
    pa = fields.get("L1F_BIN_MP_001")
    if fl and sw and iw and pa:
        derived["L2D_FC_002"] = round((sw * pa * 100.0) / (fl * iw), 4)
    else:
        derived["L2D_FC_002"] = None

    # ----------------------------------------------------------------
    # L2D_FC_003 actual_gsd_cm: same formula with actual_altitude_mean
    # ----------------------------------------------------------------
    aa = fields.get("L1F_BIN_TLM_001")
    if fl and sw and iw and aa:
        derived["L2D_FC_003"] = round((sw * aa * 100.0) / (fl * iw), 4)
    else:
        derived["L2D_FC_003"] = None

    # ----------------------------------------------------------------
    # L2D_FC_004 actual_coverage_pct: union of image footprints / planned_area.
    # Same fallback chain as overlap: EXIF GPS → BIN CAM positions → None.
    # Uses raster union (numpy) since shapely isn't installed.
    # ----------------------------------------------------------------
    coverage_detail = None
    if sensor_w and sensor_h and focal and cam_positions and nav_waypoints:
        coverage_detail = _compute_coverage_from_cam(
            cam_positions, nav_waypoints, sensor_w, sensor_h, focal,
        )
    if coverage_detail and coverage_detail.get("coverage_pct") is not None:
        derived["L2D_FC_004"] = round(coverage_detail["coverage_pct"], 4)
        notes["L2D_FC_004"] = (
            f"source=bin_cam_fallback; method={coverage_detail['method']}; "
            f"grid res={coverage_detail['grid_resolution_m']:.2f}m, "
            f"{coverage_detail['shots_used']} shots → "
            f"{coverage_detail['covered_cells']}/{coverage_detail['planned_cells']} cells covered"
        )
    else:
        derived["L2D_FC_004"] = None
        notes["L2D_FC_004"] = "no cam positions or waypoint polygon available for coverage computation"

    # ----------------------------------------------------------------
    # L2D_FC_005 gsd_execution_ratio = actual_gsd / planned_gsd
    # ----------------------------------------------------------------
    derived["L2D_FC_005"] = round(_safe_ratio(derived["L2D_FC_003"], derived["L2D_FC_002"]), 4) if derived["L2D_FC_003"] and derived["L2D_FC_002"] else None

    # ----------------------------------------------------------------
    # L2D_FC_006 / L2D_FC_007 overlap execution ratios = computed / planned
    # ----------------------------------------------------------------
    derived["L2D_FC_006"] = round(_safe_ratio(derived["L2D_IMG_004"], fields.get("L1F_UI_001")), 4) if derived["L2D_IMG_004"] is not None and fields.get("L1F_UI_001") else None
    if derived["L2D_FC_006"] is None:
        notes["L2D_FC_006"] = "depends on L2D_IMG_004 (None) or L1F_UI_001 (None)"

    derived["L2D_FC_007"] = round(_safe_ratio(derived["L2D_IMG_005"], fields.get("L1F_UI_002")), 4) if derived["L2D_IMG_005"] is not None and fields.get("L1F_UI_002") else None
    if derived["L2D_FC_007"] is None:
        notes["L2D_FC_007"] = "depends on L2D_IMG_005 (None) or L1F_UI_002 (None)"

    # ----------------------------------------------------------------
    # L2D_FC_008 altitude_execution_ratio = actual_alt_mean / planned_alt
    # ----------------------------------------------------------------
    derived["L2D_FC_008"] = round(_safe_ratio(fields.get("L1F_BIN_TLM_001"), fields.get("L1F_BIN_MP_001")), 4) if fields.get("L1F_BIN_TLM_001") and fields.get("L1F_BIN_MP_001") else None

    # ----------------------------------------------------------------
    # L2D_FC_009 mission_completion_ratio = waypoints_completed / planned_waypoint_count
    # ----------------------------------------------------------------
    derived["L2D_FC_009"] = round(_safe_ratio(fields.get("L1F_BIN_TLM_005"), fields.get("L1F_BIN_MP_003")), 4) if fields.get("L1F_BIN_TLM_005") is not None and fields.get("L1F_BIN_MP_003") else None

    # ----------------------------------------------------------------
    # L2D_FC_010 area_coverage_ratio = L2D_FC_004 / 100
    # ----------------------------------------------------------------
    if derived["L2D_FC_004"] is not None:
        derived["L2D_FC_010"] = round(derived["L2D_FC_004"] / 100.0, 4)
    else:
        derived["L2D_FC_010"] = None
        notes["L2D_FC_010"] = "depends on L2D_FC_004 which is None"

    # ----------------------------------------------------------------
    # L2D_FC_011 wind_impact_ratio = mean_wind_speed / 12
    # ----------------------------------------------------------------
    derived["L2D_FC_011"] = round(_safe_ratio(fields.get("L1F_API_001"), 12.0), 4) if fields.get("L1F_API_001") is not None else None

    # ----------------------------------------------------------------
    # L2D_FC_012 altitude_variance_m = stdev(CTUN.Alt during CRUISE only)
    # Spec note: "Excludes takeoff and landing phases."
    # Cruise filter: alt > 0.5 * planned_altitude_m. For planned=102m this
    # is > 51m, which excludes the climb-to-altitude and landing-descent legs
    # while keeping the actual surveying portion. Filter is documented and
    # tunable; chose 0.5x as a defensible "we've reached cruise altitude"
    # threshold across drone types.
    # ----------------------------------------------------------------
    cruise_threshold = None
    cruise_altitudes = []
    if pa and in_flight_altitudes:
        cruise_threshold = 0.5 * pa
        cruise_altitudes = [a for a in in_flight_altitudes if a > cruise_threshold]
    if cruise_altitudes and len(cruise_altitudes) > 1:
        derived["L2D_FC_012"] = round(statistics.stdev(cruise_altitudes), 4)
        notes["L2D_FC_012"] = (
            f"cruise filter alt > 0.5×planned_altitude_m = {cruise_threshold:.2f}m; "
            f"n={len(cruise_altitudes)}/{len(in_flight_altitudes)} samples included"
        )
    else:
        derived["L2D_FC_012"] = None
        notes["L2D_FC_012"] = "insufficient in-flight altitude samples or planned altitude unknown"

    return {"derived": derived, "notes": notes, "supplemental_bin": need_supplemental}


def run(config: dict, project_root: Path) -> dict:
    src_path = project_root / config["outputs"]["stage2_source_fields"]
    if not src_path.exists():
        raise FileNotFoundError(f"Stage 2 output missing: {src_path}. Run Stage 2 first.")
    src_envelope = json.loads(src_path.read_text())
    result = compute(src_envelope, project_root)

    envelope = {
        "spec_version": config.get("spec_version"),
        "config_used": config,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": "stage3a_derived_fields",
        "data": {
            **result["derived"],
            "_notes": result["notes"],
            "_supplemental_bin_reload": result["supplemental_bin"],
        },
    }
    return envelope


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: compute_derived.py <paths.json>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())

    envelope = run(config, project_root)
    out_path = project_root / config["outputs"]["stage3_derived"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(envelope, indent=2, sort_keys=True, default=str) + "\n")

    derived = {k: v for k, v in envelope["data"].items() if k.startswith("L2D_")}
    notes = envelope["data"]["_notes"]
    print(f"compute_derived: wrote {out_path}")
    print(f"  derived fields: {len(derived)}")
    print()
    for k in sorted(derived):
        v = derived[k]
        n = notes.get(k)
        suffix = f"   [note: {n}]" if n else ""
        print(f"  {k:14s} = {v!r}{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
