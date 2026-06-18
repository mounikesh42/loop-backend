#!/usr/bin/env python3
"""Stage 3a - compute the 37 L2D_PP_* derived fields for Pre-Processing (survey-level).

All 37 are 'scoring' kind and computed ONCE for the survey (no per-occupation
loop). They fall into families:
  - reference-frame consistency (001-009): CRS/geoid/height/projection/units/
    provenance booleans - the inputs to the catastrophic gates and the REF block.
  - geotag integrity (010-019): base pairing, fraction-fixed, completeness,
    session overlap, overlap/texture, sensor + antenna consistency, conditions.
  - GCP coord trust (020-029): per-GCP sigma ratios (list -> aggregated at 3b),
    path acceptability, id reconciliation, bbox sanity, count adequacy.
  - survey design geometry (030-032): GCP distribution coverage, target px, veg/DTM.
  - report-tier + software (016/017/027/028/033/034): report-dependent (null when
    report absent) and the v1-advisory software field.
  - CP-only (021/035/036/037): feed cp_* indicators (views) + verification_status.

Engineering choices (surfaced in stage3a_meta) and structural limitations that
are spec-amendment candidates are documented per-field in _notes. No spec flag
has raised_at_stage that maps to 3a in pre-processing, so flags_raised_stage3a
is empty by design. No timestamps in the data block (determinism rule 3).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "parsers"))
import common            # noqa: E402
import stage1_inventory  # noqa: E402
import stage2_merge      # noqa: E402
import geometry          # noqa: E402

STAGE = "stage3a_derived"

# ---- engineering tuneables (spec is prose-vague here; surfaced for review) ----
# L2D_PP_029 gcp_count_adequate: spec says only "gcp_count vs extent_m2 ratio
# (industry guidance)" - no number. DECISION pending operator confirmation.
# required_adequate = BASE + PER_KM2 * area_km2 ; marginal = MARGINAL_FACTOR x adequate.
GCP_COUNT_BASE = 4.0
GCP_COUNT_PER_KM2 = 1.0
GCP_COUNT_MARGINAL_FACTOR = 0.6
# L2D_PP_026 coord bbox-sanity overshoot tolerance (axis-swap lands km away).
BBOX_SANITY_MARGIN_M = 50.0
ADVERSE_FLIGHT_CONDITIONS = ("hazy", "monsoon", "thermal-prone")
VEGETATED = "vegetated"
AUTONOMOUS = "AUTONOMOUS"
CUSTOMER_SUPPLIED = "CUSTOMER_SUPPLIED"
FIXED = "FIXED"

# ---- source-field keys ------------------------------------------------------
PROJECT_CRS = "L1F_PP_017_project_required_crs"
PROJECT_GEOID = "L1F_PP_018_project_required_geoid"
PROJECT_HEIGHT = "L1F_PP_019_project_required_height_mode"
PROJECT_UNITS = "L1F_PP_020_project_required_units"
PROJECT_PROJECTION = "L1F_PP_021_project_required_projection"
ACC_TARGET = "L1F_PP_022_accuracy_target_m"
DECLARED_CRS = "L1F_PP_023_declared_crs_per_artifact"
DECLARED_GEOID = "L1F_PP_024_declared_geoid_per_artifact"
DECLARED_HEIGHT = "L1F_PP_025_declared_height_mode_per_artifact"
DECLARED_UNITS = "L1F_PP_026_declared_units_per_artifact"
DECLARED_PROJECTION = "L1F_PP_027_declared_projection"
REALIZATION = "L1F_PP_028_realization_epoch_per_artifact"
LOCALIZATION = "L1F_PP_029_localization_applied_declared"
CUST_CRS = "L1F_PP_030_customer_supplied_coord_crs"
CUST_ACC = "L1F_PP_031_customer_accuracy_claim"
PATH_GCP = "L1F_PP_033_declared_path_gcp"
ANTENNA = "L1F_PP_037_declared_antenna_per_artifact"
CAPTURED_COUNT = "L1F_PP_039_captured_image_count"
FWD_OVERLAP = "L1F_PP_040_planned_forward_overlap"
SIDE_OVERLAP = "L1F_PP_041_planned_side_overlap"
SITE_COVER = "L1F_PP_042_site_cover_declared"
DTM = "L1F_PP_043_dtm_in_deliverables"
TARGET_SIZE = "L1F_PP_044_target_size_cm"
GSD = "L1F_PP_045_planned_gsd_cm"
BASE_FILE_ID = "L1F_PP_047_base_file_id"
DRONE_START = "L1F_PP_048_drone_session_start_utc"
DRONE_END = "L1F_PP_049_drone_session_end_utc"
BASE_START = "L1F_PP_050_base_session_start_utc"
BASE_END = "L1F_PP_051_base_session_end_utc"
GCP_DET_DATE = "L1F_PP_052_gcp_coord_determination_date"
FLIGHT_DATE = "L1F_PP_053_flight_date"
EXTENT_M2 = "L1F_PP_054_reconstruction_extent_m2"
POLYGON = "L1F_PP_055_reconstruction_extent_polygon"
FLIGHT_COND = "L1F_PP_056_flight_conditions_declared"
GEOTAG_COUNT = "L1F_PP_004_geotag_count"
CRS_COORD_HEADER = "L1F_PP_012_crs_in_coord_file_header"
SOFTWARE_VER = "L1F_PP_036_declared_software_version_per_artifact"
SOFTWARE = "L1F_PP_035_declared_software_per_artifact"
CORS_COVERAGE = "L1F_PP_057_cors_epoch_coverage_during_flight"
TIME_SYNC = "L1F_PP_058_time_sync_residuals"
GCP_RESIDUALS = "L1F_PP_059_per_gcp_residuals"
CORS_QUALITY = "L1F_PP_060_cors_quality_metrics"
REPORT_SETTINGS = "L1F_PP_061_report_actual_settings"
TIEPOINT = "L1F_PP_062_tiepoint_density"


# ---- helpers ----------------------------------------------------------------
def _field(value, input_field_ids, notes=None) -> dict:
    out = {"value": value, "input_field_ids": list(input_field_ids)}
    if notes:
        out["_notes"] = list(notes)
    return out


def _parse_iso(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_date(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").split("T")[0]).date()
    except ValueError:
        return None


def normalize_datum(s):
    """WGS-84 / 'WGS84 / UTM zone 43N (EPSG:32643)' / WGS 84 -> 'WGS84' (datum only)."""
    if not isinstance(s, str) or not s.strip():
        return None
    t = s.upper().strip()
    if "/" in t:
        t = t.split("/", 1)[0]
    if "(" in t:
        t = t.split("(", 1)[0]
    t = " ".join(t.replace("-", " ").replace("_", " ").split())
    return t.replace("WGS 84", "WGS84").strip() or None


def _norm(s):
    return " ".join(s.upper().split()) if isinstance(s, str) and s.strip() else None


def parse_utm_zone(s):
    if not isinstance(s, str):
        return (None, None)
    m = re.search(r"(\d{1,2})\s*([NS])", s.upper())
    if m:
        return (int(m.group(1)), m.group(2))
    m2 = re.search(r"ZONE\s*(\d{1,2})", s.upper())
    return (int(m2.group(1)), None) if m2 else (None, None)


def _utm_zone_from_lon(lon):
    return int((lon + 180) // 6) + 1


def _all_match_project(declared, project, norm=_norm):
    """(all artifacts == project) AND (consistent across artifacts)."""
    if not isinstance(declared, dict) or not declared or project is None:
        return None, {}
    pn = norm(project)
    per = {a: norm(v) for a, v in declared.items()}
    consistent = len(set(per.values())) == 1
    matches = all(v == pn for v in per.values())
    return (consistent and matches), {"per_artifact": per, "project": pn, "consistent": consistent}


def _geotag_lonlat(sf):
    out = []
    for r in sf.get("per_image", []):
        p = r.get("L1F_PP_002_geotag_position") or {}
        if p.get("lon") is not None and p.get("lat") is not None:
            out.append((p["lon"], p["lat"]))
    return out


def _xy_list(sf, list_key, pos_key):
    out = []
    for r in sf.get(list_key, []):
        xy = geometry.as_xy(r.get(pos_key))
        if xy is not None:
            out.append(xy)
    return out


# ---- the 37 derived computers ----------------------------------------------
def compute(sf: dict) -> dict[str, dict]:
    d: dict[str, dict] = {}
    target = sf.get(ACC_TARGET)
    is_customer = sf.get(PATH_GCP) == CUSTOMER_SUPPLIED
    poly = sf.get(POLYGON)

    # 001 crs_match_project (HARD GATE input)
    v, det = _all_match_project(sf.get(DECLARED_CRS), sf.get(PROJECT_CRS), normalize_datum)
    d["L2D_PP_001_crs_match_project"] = _field(
        v, [DECLARED_CRS, PROJECT_CRS],
        [f"Datum-normalised: {det}. Hard-gate input (False -> PP_WRONG_CRS_DATUM, apex=0)."])

    # 002 geoid_match_project
    v, det = _all_match_project(sf.get(DECLARED_GEOID), sf.get(PROJECT_GEOID))
    d["L2D_PP_002_geoid_match_project"] = _field(v, [DECLARED_GEOID, PROJECT_GEOID], [f"{det}"])

    # 003 height_mode_consistency
    v, det = _all_match_project(sf.get(DECLARED_HEIGHT), sf.get(PROJECT_HEIGHT))
    d["L2D_PP_003_height_mode_consistency"] = _field(v, [DECLARED_HEIGHT, PROJECT_HEIGHT], [f"{det}"])

    # 004 projection_match_location (HARD GATE input)
    decl_proj = sf.get(DECLARED_PROJECTION)
    zone, hemi = parse_utm_zone(decl_proj)
    lonlat = _geotag_lonlat(sf)
    exp_zone = exp_hemi = None
    if lonlat:
        mean_lon = sum(p[0] for p in lonlat) / len(lonlat)
        mean_lat = sum(p[1] for p in lonlat) / len(lonlat)
        exp_zone = _utm_zone_from_lon(mean_lon)
        exp_hemi = "N" if mean_lat >= 0 else "S"
    easting_ok = True
    if poly:
        minx, _, maxx, _ = geometry.bbox_of([tuple(p) for p in poly])
        easting_ok = 100000 <= minx and maxx <= 900000
    zone_ok = zone is not None and (exp_zone is None or zone == exp_zone)
    hemi_ok = hemi is None or exp_hemi is None or hemi == exp_hemi
    proj_match = bool(zone_ok and hemi_ok and easting_ok)
    d["L2D_PP_004_projection_match_location"] = _field(
        proj_match, [DECLARED_PROJECTION, POLYGON],
        [f"declared zone={zone}{hemi or ''}; expected-from-geotag-lon={exp_zone}{exp_hemi or ''}; "
         f"easting_plausible={easting_ok}. ENGINEERING METHOD (zone from geotag mean lon + "
         "UTM easting plausibility). Hard-gate input (False -> PP_WRONG_PROJECTION, apex=0)."])

    # 005 output_crs_metadata_present (artifact-actual vs declared)
    declared_crs = sf.get(DECLARED_CRS) or {}
    exif_crs = sorted({r.get("L1F_PP_005_crs_in_exif") for r in sf.get("per_image", [])
                       if r.get("L1F_PP_005_crs_in_exif")})
    coord_crs = sf.get(CRS_COORD_HEADER)
    geo_present = bool(exif_crs)
    geo_match = geo_present and all(normalize_datum(c) == normalize_datum(declared_crs.get("geotag"))
                                    for c in exif_crs)
    gcp_present = coord_crs is not None
    gcp_match = gcp_present and normalize_datum(coord_crs) == normalize_datum(declared_crs.get("gcp"))
    if geo_present and gcp_present and geo_match and gcp_match:
        status = "present_and_match"
    elif not (geo_present and gcp_present):
        status = "missing"
    else:
        status = "mismatch"
    d["L2D_PP_005_output_crs_metadata_present"] = _field(
        {"status": status, "geotag_present": geo_present, "geotag_match": geo_match,
         "gcp_present": gcp_present, "gcp_match": gcp_match},
        ["crs_in_exif", CRS_COORD_HEADER, DECLARED_CRS],
        ["present+match->100; missing->50; mismatch->0. Datum-normalised comparison."])

    # 006 units_match_project
    v, det = _all_match_project(sf.get(DECLARED_UNITS), sf.get(PROJECT_UNITS))
    d["L2D_PP_006_units_match_project"] = _field(v, [DECLARED_UNITS, PROJECT_UNITS], [f"{det}"])

    # 007 customer_coord_crs_consistent (CUSTOMER_SUPPLIED only)
    if not is_customer:
        d["L2D_PP_007_customer_coord_crs_consistent"] = _field(
            None, [CUST_CRS, PROJECT_CRS, PATH_GCP],
            ["N/A: declared_path_gcp is not CUSTOMER_SUPPLIED; indicator weight redistributes."])
    else:
        cust = sf.get(CUST_CRS)
        val = bool(cust) and normalize_datum(cust) == normalize_datum(sf.get(PROJECT_CRS))
        d["L2D_PP_007_customer_coord_crs_consistent"] = _field(
            {"declared": cust is not None, "matches": val}, [CUST_CRS, PROJECT_CRS, PATH_GCP],
            ["customer-supplied path: CRS declared AND matches project."])

    # 008 localization_disclosed
    loc = sf.get(LOCALIZATION)
    d["L2D_PP_008_localization_disclosed"] = _field(
        loc is not None, [LOCALIZATION],
        [f"localization_applied_declared={loc}; disclosed iff NOT NULL (False counts as disclosed)."])

    # 009 provenance_realization_consistent
    real = sf.get(REALIZATION)
    if not isinstance(real, dict) or not real:
        v9 = None
    else:
        v9 = len({_norm(x) for x in real.values()}) == 1
    d["L2D_PP_009_provenance_realization_consistent"] = _field(
        v9, [REALIZATION], ["All control declares same ITRF realization/epoch."])

    # 010 drone_session_within_base_window
    ds, de = _parse_iso(sf.get(DRONE_START)), _parse_iso(sf.get(DRONE_END))
    bs, be = _parse_iso(sf.get(BASE_START)), _parse_iso(sf.get(BASE_END))
    base_id = sf.get(BASE_FILE_ID)
    if None in (ds, de, bs, be):
        v10 = None
        n10 = "timing null - uncomputable."
    else:
        within = ds >= bs and de <= be
        v10 = bool(within and base_id)
        n10 = (f"drone[{sf.get(DRONE_START)},{sf.get(DRONE_END)}] within "
               f"base[{sf.get(BASE_START)},{sf.get(BASE_END)}]={within}; base_file_id present="
               f"{bool(base_id)}. NOTE: no external 'expected base_file_id' source -> presence "
               "check only (SPEC limitation).")
    d["L2D_PP_010_drone_session_within_base_window"] = _field(
        v10, [DRONE_START, DRONE_END, BASE_START, BASE_END, BASE_FILE_ID], [n10])

    # 011 fraction_geotags_fixed
    per_img = sf.get("per_image", [])
    gc = sf.get(GEOTAG_COUNT) or len(per_img)
    fixed = sum(1 for r in per_img if r.get("L1F_PP_003_per_geotag_fix_status") == FIXED)
    d["L2D_PP_011_fraction_geotags_fixed"] = _field(
        round(fixed / gc, 4) if gc else None, ["per_geotag_fix_status", GEOTAG_COUNT],
        [f"{fixed}/{gc} FIXED."])

    # 012 geotag_completeness_fraction
    cap = sf.get(CAPTURED_COUNT)
    d["L2D_PP_012_geotag_completeness_fraction"] = _field(
        round(gc / cap, 4) if (gc and cap) else None, [GEOTAG_COUNT, CAPTURED_COUNT],
        [f"geotag_count={gc} / captured={cap}."])

    # 013 session_overlap_fraction
    if None in (ds, de, bs, be) or (de - ds).total_seconds() <= 0:
        v13 = None
    else:
        overlap = (min(de, be) - max(ds, bs)).total_seconds()
        v13 = round(max(0.0, overlap) / (de - ds).total_seconds(), 4)
    d["L2D_PP_013_session_overlap_fraction"] = _field(
        v13, [DRONE_START, DRONE_END, BASE_START, BASE_END], ["fraction of flight inside base window."])

    # 014 antenna_pco_match (declared-only; no device-actual in PP)
    ant = sf.get(ANTENNA)
    d["L2D_PP_014_antenna_pco_match"] = _field(
        {"match": True if ant else None, "declared": ant, "device_actual": None},
        [ANTENNA], ["No device-reported antenna in the PP artifact set -> declared-only; no "
                    "mismatch detectable (defaults to match). SPEC limitation."])

    # 015 sensor_metadata_consistent (EXIF-internal; no manifest camera field)
    serials = sorted({r.get("L1F_PP_007_camera_serial") for r in per_img
                      if r.get("L1F_PP_007_camera_serial")})
    d["L2D_PP_015_sensor_metadata_consistent"] = _field(
        (len(serials) <= 1) if per_img else None, ["camera_serial"],
        [f"distinct EXIF camera serials={serials}. NOTE: manifest carries no camera field and "
         "there is no flight-log source -> consistency = EXIF-internal (all images one serial). "
         "SPEC limitation."])

    # 016 cors_data_continuity (report-dependent)
    d["L2D_PP_016_cors_data_continuity"] = _field(
        sf.get(CORS_COVERAGE), [CORS_COVERAGE],
        ["report-dependent; null when report absent (indicator advisory + redistributes)."])

    # 017 time_sync_residual_magnitude (report-dependent)
    d["L2D_PP_017_time_sync_residual_magnitude"] = _field(
        sf.get(TIME_SYNC), [TIME_SYNC], ["report-dependent; null when report absent."])

    # 018 overlap_texture_proxy
    fwd, side = sf.get(FWD_OVERLAP), sf.get(SIDE_OVERLAP)
    tp = sf.get(TIEPOINT)
    min_ov = min(fwd, side) if (fwd is not None and side is not None) else None
    d["L2D_PP_018_overlap_texture_proxy"] = _field(
        {"min_overlap": min_ov, "forward": fwd, "side": side, "tiepoint_density": tp,
         "source": "tiepoint_density" if tp is not None else "declared_overlap"},
        [FWD_OVERLAP, SIDE_OVERLAP, TIEPOINT],
        ["min(forward,side) overlap; tie-point density (report) replaces it when present."])

    # 019 flight_conditions_adverse
    cond = sf.get(FLIGHT_COND)
    d["L2D_PP_019_flight_conditions_adverse"] = _field(
        (cond in ADVERSE_FLIGHT_CONDITIONS) if cond else None, [FLIGHT_COND],
        [f"conditions={cond!r}; adverse set={list(ADVERSE_FLIGHT_CONDITIONS)}."])

    # 020 gcp_sigma_relative_to_target (per-GCP list -> aggregated at 3b)
    gcp_ratios = []
    for r in sf.get("per_gcp", []):
        sh = r.get("L1F_PP_010_per_gcp_sigma_h")
        if sh is not None and target:
            gcp_ratios.append({"gcp_id": r.get("L1F_PP_008_gcp_id"), "ratio": round(sh / target, 4)})
    d["L2D_PP_020_gcp_sigma_relative_to_target"] = _field(
        gcp_ratios, ["per_gcp_sigma_h", ACC_TARGET],
        [f"{len(gcp_ratios)} per-GCP sigma/target ratios; aggregated mean-0.25*(100-min) at 3b."])

    # 021 cp_sigma_relative_to_target (per-CP list; view + verification_status)
    cp_ratios = []
    for r in sf.get("per_cp", []):
        sh = r.get("L1F_PP_015_per_cp_sigma_h")
        if sh is not None and target:
            cp_ratios.append({"cp_id": r.get("L1F_PP_013_cp_id"), "ratio": round(sh / target, 4)})
    d["L2D_PP_021_cp_sigma_relative_to_target"] = _field(
        cp_ratios, ["per_cp_sigma_h", ACC_TARGET],
        [f"{len(cp_ratios)} per-CP sigma/target ratios; feeds cp_sigma_score (view) + verification_status."])

    # 022 gcp_path_acceptable (HARD GATE input)
    pg = sf.get(PATH_GCP)
    d["L2D_PP_022_gcp_path_acceptable"] = _field(
        (pg != AUTONOMOUS) if pg else None, [PATH_GCP],
        [f"declared_path_gcp={pg}; AUTONOMOUS -> PP_GCP_AUTONOMOUS_PATH (apex=0)."])

    # 023 gcp_customer_accuracy_adequate (CUSTOMER_SUPPLIED only)
    if not is_customer:
        d["L2D_PP_023_gcp_customer_accuracy_adequate"] = _field(
            None, [CUST_ACC, ACC_TARGET, PATH_GCP], ["N/A: not CUSTOMER_SUPPLIED; redistributes."])
    else:
        claim = sf.get(CUST_ACC)
        adequate = (claim is not None and target is not None and claim <= target)
        d["L2D_PP_023_gcp_customer_accuracy_adequate"] = _field(
            {"declared": claim is not None, "adequate": adequate, "claim": claim},
            [CUST_ACC, ACC_TARGET, PATH_GCP], ["customer accuracy declared AND meets target."])

    # 024 gcp_id_reconciliation (coord-file internal; no manifest id list)
    ids = [r.get("L1F_PP_008_gcp_id") for r in sf.get("per_gcp", [])]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    empties = sum(1 for i in ids if not i)
    d["L2D_PP_024_gcp_id_reconciliation"] = _field(
        {"status": "consistent" if (not dup and not empties) else "issues",
         "duplicate_ids": dup, "empty_ids": empties, "no_external_list": True},
        ["gcp_id"], ["NOTE: manifest carries no GCP id list -> reconciliation is coord-file "
                     "internal consistency (unique, non-empty); no cross-artifact list to "
                     "mismatch against. SPEC limitation."])

    # 025 gcp_coord_age_days (CUSTOMER_SUPPLIED only)
    if not is_customer:
        d["L2D_PP_025_gcp_coord_age_days"] = _field(
            None, [GCP_DET_DATE, FLIGHT_DATE, PATH_GCP], ["N/A: not CUSTOMER_SUPPLIED; redistributes."])
    else:
        det, fl = _parse_date(sf.get(GCP_DET_DATE)), _parse_date(sf.get(FLIGHT_DATE))
        age = (fl - det).days if (det and fl) else None
        d["L2D_PP_025_gcp_coord_age_days"] = _field(
            age, [GCP_DET_DATE, FLIGHT_DATE, PATH_GCP], ["flight_date - coord_determination_date."])

    # 026 coord_parse_bbox_sanity
    gcp_xy = _xy_list(sf, "per_gcp", "L1F_PP_009_gcp_position")
    if not gcp_xy or not poly:
        v26 = None
        outside = []
    else:
        polyt = [tuple(p) for p in poly]
        flags = [geometry.point_within(xy, polyt, BBOX_SANITY_MARGIN_M) for xy in gcp_xy]
        outside = [sf["per_gcp"][i].get("L1F_PP_008_gcp_id")
                   for i, ok in enumerate(flags) if not ok]
        v26 = {"all_within": all(flags), "outside_count": len(outside),
               "fraction_within": round(sum(flags) / len(flags), 4), "outside_ids": outside}
    d["L2D_PP_026_coord_parse_bbox_sanity"] = _field(
        v26, ["gcp_position", POLYGON],
        [f"GCPs within polygon (+{BBOX_SANITY_MARGIN_M}m margin); axis-swap/misparse lands far out."])

    # 027 gcp_residuals_within_tolerance (report-dependent)
    d["L2D_PP_027_gcp_residuals_within_tolerance"] = _field(
        sf.get(GCP_RESIDUALS), [GCP_RESIDUALS], ["report-dependent; null when report absent."])

    # 028 cors_station_health_acceptable (report-dependent)
    d["L2D_PP_028_cors_station_health_acceptable"] = _field(
        sf.get(CORS_QUALITY), [CORS_QUALITY], ["report-dependent; null when report absent."])

    # 029 gcp_count_adequate (TUNEABLE - operator decision)
    n_gcp = len(sf.get("per_gcp", []))
    area = sf.get(EXTENT_M2)
    if not area or area <= 0:
        v29 = None
    else:
        km2 = area / 1_000_000.0
        req_adeq = GCP_COUNT_BASE + GCP_COUNT_PER_KM2 * km2
        req_marg = GCP_COUNT_MARGINAL_FACTOR * req_adeq
        adequacy = ("adequate" if n_gcp >= req_adeq else
                    "marginal" if n_gcp >= req_marg else "insufficient")
        v29 = {"gcp_count": n_gcp, "area_km2": round(km2, 4),
               "required_adequate": round(req_adeq, 2), "required_marginal": round(req_marg, 2),
               "adequacy": adequacy}
    d["L2D_PP_029_gcp_count_adequate"] = _field(
        v29, ["gcp_id", EXTENT_M2],
        ["adequacy = BASE + PER_KM2*area (tuneable; spec gives no number - operator DECISION)."])

    # 030 gcp_distribution_coverage
    cov = geometry.hull_coverage_fraction(gcp_xy, [tuple(p) for p in poly]) if (gcp_xy and poly) else None
    d["L2D_PP_030_gcp_distribution_coverage"] = _field(
        cov, ["gcp_position", POLYGON], ["GCP convex-hull area / reconstruction-extent area."])

    # 031 target_pixels_at_gsd
    ts, gsd = sf.get(TARGET_SIZE), sf.get(GSD)
    d["L2D_PP_031_target_pixels_at_gsd"] = _field(
        round(ts / gsd, 4) if (ts and gsd) else None, [TARGET_SIZE, GSD],
        [f"target_size_cm={ts} / planned_gsd_cm={gsd} = pixels across target."])

    # 032 vegetation_dtm_risk
    sc, dtm = sf.get(SITE_COVER), sf.get(DTM)
    d["L2D_PP_032_vegetation_dtm_risk"] = _field(
        (sc == VEGETATED and dtm is True) if sc is not None else None, [SITE_COVER, DTM],
        [f"site_cover={sc!r} AND dtm_in_deliverables={dtm}."])

    # 033 settings_declared_vs_actual_consistent (report-dependent, Approach 2)
    rep = sf.get(REPORT_SETTINGS)
    if not isinstance(rep, dict):
        v33 = None
    else:
        checks = {}
        if "datum" in rep:
            checks["datum"] = normalize_datum(rep["datum"]) == normalize_datum(
                (sf.get(DECLARED_CRS) or {}).get("gcp"))
        if "geoid" in rep:
            checks["geoid"] = _norm(rep["geoid"]) == _norm((sf.get(DECLARED_GEOID) or {}).get("gcp"))
        v33 = {"consistent": all(checks.values()) if checks else None, "checks": checks}
    d["L2D_PP_033_settings_declared_vs_actual_consistent"] = _field(
        v33, [REPORT_SETTINGS, DECLARED_GEOID, DECLARED_CRS],
        ["Approach 2; report-dependent; null when report absent (advisory + redistributes)."])

    # 034 software_version_in_buggy_list (v1: advisory, no list)
    d["L2D_PP_034_software_version_in_buggy_list"] = _field(
        {"in_buggy_list": False, "version": sf.get(SOFTWARE_VER), "software": sf.get(SOFTWARE)},
        [SOFTWARE, SOFTWARE_VER],
        ["v1: CBMI maintains no known-buggy list -> always False (software_version_score=100); "
         "real scoring deferred to v2."])

    # 035 cp_designated_count
    d["L2D_PP_035_cp_designated_count"] = _field(
        len(sf.get("per_cp", [])), ["cp_id"], ["CP count for verification_status + cp_count_score."])

    # 036 cp_distribution_coverage
    cp_xy = _xy_list(sf, "per_cp", "L1F_PP_014_cp_position")
    cp_cov = geometry.hull_coverage_fraction(cp_xy, [tuple(p) for p in poly]) if (cp_xy and poly) else None
    d["L2D_PP_036_cp_distribution_coverage"] = _field(
        cp_cov, ["cp_position", POLYGON], ["CP convex-hull area / extent area (verification_status)."])

    # 037 cp_gcp_spatial_independence
    indep = geometry.min_pairwise_distance(cp_xy, gcp_xy) if (cp_xy and gcp_xy) else None
    d["L2D_PP_037_cp_gcp_spatial_independence"] = _field(
        indep, ["cp_position", "gcp_position"], ["min pairwise CP-GCP distance (m)."])

    return d


def run(config: dict, project_root: Path, spec: dict, stage2_data: dict) -> dict:
    kind_by_key = {f"{d['derived_id']}_{d['derived_name']}": d["kind"] for d in spec["derived_fields"]}
    total_expected = spec["_meta"]["counts"]["derived_fields"]
    sf = stage2_data.get("source_fields", {})

    derived = compute(sf)
    for key, fobj in derived.items():
        fobj["kind"] = kind_by_key.get(key, "scoring")

    produced = set(derived.keys())
    expected = set(kind_by_key.keys())
    notes = []
    if produced != expected:
        notes.append({"missing": sorted(expected - produced), "extra": sorted(produced - expected)})

    counts_by_kind: dict[str, int] = {}
    for fobj in derived.values():
        counts_by_kind[fobj["kind"]] = counts_by_kind.get(fobj["kind"], 0) + 1
    null_fields = sorted(k for k, f in derived.items() if f["value"] is None)

    return {
        "survey_level": True,
        "derived_fields": dict(sorted(derived.items())),
        "flags_raised_stage3a": [],
        "stage3a_notes": notes,
        "stage3a_meta": {
            "total_derived_field_count": total_expected,
            "produced_count": len(derived),
            "counts_by_kind": dict(sorted(counts_by_kind.items())),
            "null_value_fields": null_fields,
            "null_reason_index": {
                "customer_supplied_NA": ["L2D_PP_007_customer_coord_crs_consistent",
                                         "L2D_PP_023_gcp_customer_accuracy_adequate",
                                         "L2D_PP_025_gcp_coord_age_days"],
                "report_absent_advisory": ["L2D_PP_016_cors_data_continuity",
                                           "L2D_PP_017_time_sync_residual_magnitude",
                                           "L2D_PP_027_gcp_residuals_within_tolerance",
                                           "L2D_PP_028_cors_station_health_acceptable",
                                           "L2D_PP_033_settings_declared_vs_actual_consistent"],
            },
            "tuneables": {
                "GCP_COUNT_BASE": GCP_COUNT_BASE,
                "GCP_COUNT_PER_KM2": GCP_COUNT_PER_KM2,
                "GCP_COUNT_MARGINAL_FACTOR": GCP_COUNT_MARGINAL_FACTOR,
                "BBOX_SANITY_MARGIN_M": BBOX_SANITY_MARGIN_M,
                "ADVERSE_FLIGHT_CONDITIONS": list(ADVERSE_FLIGHT_CONDITIONS),
                "datum_normalization": "uppercase, strip '-/_', take token before '/' and '(', "
                                       "collapse 'WGS 84'->'WGS84'",
                "projection_method": "UTM zone parsed from declared_projection, cross-checked vs "
                                     "geotag mean-longitude zone + UTM easting plausibility",
            },
            "spec_amendment_candidates": [
                "L2D_PP_010: no external 'expected base_file_id' source -> presence check only.",
                "L2D_PP_014 antenna_pco_match: no device-reported antenna in PP artifacts -> "
                "declared-only, mismatch undetectable.",
                "L2D_PP_015 sensor_metadata_consistent: manifest has no camera field + no flight-log "
                "source -> EXIF-internal consistency only.",
                "L2D_PP_024 gcp_id_reconciliation: manifest carries no GCP id list -> coord-file "
                "internal consistency only.",
                "L2D_PP_029 gcp_count_adequate: spec gives no numeric ratio -> tuneable, operator "
                "DECISION pending (defaulted to BASE=4 + 1/km2).",
            ],
        },
    }


def print_summary(data: dict) -> None:
    mm = data["stage3a_meta"]
    d = data["derived_fields"]
    print(f"  derived fields: {mm['produced_count']}/{mm['total_derived_field_count']}  "
          f"kinds={mm['counts_by_kind']}  null={len(mm['null_value_fields'])}")
    def val(k):
        return d[k]["value"]
    print(f"    gates: crs_match={val('L2D_PP_001_crs_match_project')} "
          f"proj_match={val('L2D_PP_004_projection_match_location')} "
          f"gcp_path_ok={val('L2D_PP_022_gcp_path_acceptable')}")
    print(f"    geotag: fixed={val('L2D_PP_011_fraction_geotags_fixed')} "
          f"complete={val('L2D_PP_012_geotag_completeness_fraction')} "
          f"overlap={val('L2D_PP_013_session_overlap_fraction')}")
    print(f"    geom: gcp_cov={val('L2D_PP_030_gcp_distribution_coverage')} "
          f"cp_cov={val('L2D_PP_036_cp_distribution_coverage')} "
          f"cp_gcp_dist={val('L2D_PP_037_cp_gcp_spatial_independence')}")
    print(f"    counts: gcp_adequacy={ (val('L2D_PP_029_gcp_count_adequate') or {}).get('adequacy') } "
          f"cp_count={val('L2D_PP_035_cp_designated_count')} "
          f"target_px={val('L2D_PP_031_target_pixels_at_gsd')}")
    print(f"    null fields: {mm['null_value_fields']}")
    print(f"  flags at 3a: {len(data['flags_raised_stage3a'])} (none by design)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pre-Processing Stage 3a derived fields")
    parser.add_argument("config", help="Path to paths.json")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    config = common.load_config(config_path)
    root = config_path.parent
    spec = common.load_spec(root, config)
    spec_version = spec["_meta"]["version"]

    env1, hard = stage1_inventory.run(config, root)
    if hard and config.get("options", {}).get("fail_fast", True):
        print("HALT: Stage 1 hard failure (fail_fast).")
        return 1
    data2 = stage2_merge.run(config, root, spec, env1["data"])
    data = run(config, root, spec, data2)

    out_path = root / config["outputs"]["stage3_derived"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    print(f"Stage 3a derived fields -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
