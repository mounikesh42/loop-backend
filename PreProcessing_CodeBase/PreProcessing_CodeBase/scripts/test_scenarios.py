#!/usr/bin/env python3
"""Step 12 - self-validating smoke-test harness for the Pre-Processing pipeline.

SURVEY-LEVEL: each scenario deep-copies the baseline Stage-2 source_fields,
applies its mutator(s), re-runs Stages 3a-3d into tests/scenarios/<name>/, and
captures pre_processing_score / block & view scores / verification_status / the
aggregated flag-id set. Each result is asserted against an EXPECT entry (expected
apex score +- tol AND exact flag set AND verification_status); the harness exits
NON-ZERO on any drift, so a future change that shifts a score or flag is caught.

Determinism: scenario artifacts are written WITHOUT generated_at (byte-stable).

Pass 1 (spec-internal coverage) is built here (Step 12a). Pass 2 (CBMI Problems
sheet v1.0) + the 42-row problem-coverage map land in Step 12b.

Usage:  python3 scripts/test_scenarios.py [paths.json]
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import stage1_inventory  # noqa: E402
import stage2_merge  # noqa: E402
import stage3a_derived  # noqa: E402
import stage3b_indicators  # noqa: E402
import stage3c_blocks  # noqa: E402
import stage3d_score  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = ROOT / "tests" / "scenarios"
SCORE_TOL = 0.05

_CONFIG: dict = {}
_SPEC: dict = {}
_SPEC_VERSION = ""
_BASELINE2: dict = {}

# ---- source-field keys for mutators ----
K_PROJ_CRS = "L1F_PP_017_project_required_crs"
K_PROJ_GEOID = "L1F_PP_018_project_required_geoid"
K_PROJ_UNITS = "L1F_PP_020_project_required_units"
K_DECL_HEIGHT = "L1F_PP_025_declared_height_mode_per_artifact"
K_DECL_PROJ = "L1F_PP_027_declared_projection"
K_REALIZATION = "L1F_PP_028_realization_epoch_per_artifact"
K_LOCALIZATION = "L1F_PP_029_localization_applied_declared"
K_PATH_GCP = "L1F_PP_033_declared_path_gcp"
K_BASELINE = "L1F_PP_038_baseline_length_km"
K_CAPTURED = "L1F_PP_039_captured_image_count"
K_FWD = "L1F_PP_040_planned_forward_overlap"
K_SIDE = "L1F_PP_041_planned_side_overlap"
K_SITE_COVER = "L1F_PP_042_site_cover_declared"
K_TARGET_SIZE = "L1F_PP_044_target_size_cm"
K_GSD = "L1F_PP_045_planned_gsd_cm"
K_BASE_FILE = "L1F_PP_047_base_file_id"
K_BASE_START = "L1F_PP_050_base_session_start_utc"
K_EXTENT = "L1F_PP_054_reconstruction_extent_m2"
K_FLIGHT_COND = "L1F_PP_056_flight_conditions_declared"


# ---- Pass 1 mutators (operate on the source_fields dict) --------------------
def _img_fix(sf, n, status):
    for r in sf["per_image"][:n]:
        r["L1F_PP_003_per_geotag_fix_status"] = status


# gates
def m_wrong_crs(sf): sf[K_PROJ_CRS] = "NAD83(2011)"                 # crs_match False -> gate 001
def m_wrong_projection(sf): sf[K_DECL_PROJ] = "UTM 44N"            # zone != geotag-lon zone -> gate 004
def m_gcp_autonomous(sf): sf[K_PATH_GCP] = "AUTONOMOUS"            # path -> gate 022

# REF bands
def m_geoid_mismatch(sf): sf[K_PROJ_GEOID] = "GEOID18"             # 002 -> 0 + PP_GEOID_MISMATCH
def m_height_inconsistent(sf): sf[K_DECL_HEIGHT] = {"geotag": "orthometric", "gcp": "ellipsoidal", "cp": "orthometric"}
def m_units_mismatch(sf): sf[K_PROJ_UNITS] = "US Survey ft"        # 006 -> 0 + PP_UNITS_MISMATCH
def m_localization_undisclosed(sf): sf[K_LOCALIZATION] = None      # 008 -> 60 + PP_LOCALIZATION_UNDISCLOSED
def m_output_crs_mismatch(sf):                                    # exif != declared -> 005 mismatch
    for r in sf["per_image"]:
        r["L1F_PP_005_crs_in_exif"] = "NAD83"
def m_provenance_mixed(sf):
    sf[K_REALIZATION] = {"geotag": "WGS84(G2139)@2024.0", "gcp": "ITRF2014@2020.0", "cp": "WGS84(G2139)@2024.0"}

# GEO bands
def m_geotag_partial_fix(sf): _img_fix(sf, 1, "FLOAT")             # 11/12 -> 70 PARTIAL
def m_geotag_poor_fix(sf): _img_fix(sf, 5, "FLOAT")               # 7/12 -> 30 POOR
def m_geotag_not_fixed(sf): _img_fix(sf, 8, "FLOAT")             # 4/12 -> 0 NOT_FIXED
def m_wrong_base(sf): sf[K_BASE_FILE] = None                      # base_pairing -> 0
def m_geotags_incomplete(sf): sf[K_CAPTURED] = 15                # 12/15=0.80 -> 50 INCOMPLETE
def m_long_baseline(sf): sf[K_BASELINE] = 15.0                   # 10-20 -> 70 LONG
def m_sparse_tiepoints(sf): sf[K_FWD] = 60; sf[K_SIDE] = 50       # <65/<55 -> 40 SPARSE
def m_sensor_mismatch(sf): sf["per_image"][0]["L1F_PP_007_camera_serial"] = "OTHER-SN"
def m_monsoon(sf): sf[K_FLIGHT_COND] = "monsoon"                  # adverse -> 70
def m_insufficient_overlap(sf): sf[K_BASE_START] = "2024-06-15T09:30:00Z"  # overlap 0.5 + base not covering

# GCT bands
def m_gcp_sigma_marginal(sf): sf["per_gcp"][0]["L1F_PP_010_per_gcp_sigma_h"] = 0.03   # 1.5x
def m_gcp_sigma_high(sf): sf["per_gcp"][0]["L1F_PP_010_per_gcp_sigma_h"] = 0.05       # 2.5x
def m_gcp_sigma_reject(sf): sf["per_gcp"][0]["L1F_PP_010_per_gcp_sigma_h"] = 0.15     # 7.5x
def m_coord_misparse(sf):
    sf["per_gcp"][0]["L1F_PP_009_gcp_position"] = {"easting": 2000080.0, "northing": 600080.0, "elevation": 540.0}
def m_gcp_id_partial(sf): sf["per_gcp"][1]["L1F_PP_008_gcp_id"] = "GCP01"             # dup id

# SD bands
def m_undersized_network(sf): sf[K_EXTENT] = 30_000_000.0         # count marginal + clustering
def m_target_marginal(sf): sf[K_TARGET_SIZE] = 12.0; sf[K_GSD] = 5.0   # 2.4 px -> 60
def m_target_invisible(sf): sf[K_TARGET_SIZE] = 5.0; sf[K_GSD] = 5.0   # 1.0 px -> 30
def m_veg_dtm(sf): sf[K_SITE_COVER] = "vegetated"                # vegetated + DTM -> advisory 30

# verification_status (apex must stay unaffected)
def m_no_cps(sf): sf["per_cp"] = []
def m_insufficient_cps(sf): sf["per_cp"] = sf["per_cp"][:3]
def m_cp_clustered(sf):                                          # collapse CPs into one corner
    for i, r in enumerate(sf["per_cp"]):
        r["L1F_PP_014_cp_position"] = {"easting": 600200.0 + i * 5, "northing": 2000200.0 + i * 5, "elevation": 540.0}
def m_cp_not_independent(sf):                                   # put every CP on top of a GCP
    g = sf["per_gcp"]
    for i, r in enumerate(sf["per_cp"]):
        gp = g[i % len(g)]["L1F_PP_009_gcp_position"]
        r["L1F_PP_014_cp_position"] = {"easting": gp["easting"] + 2.0, "northing": gp["northing"] + 2.0, "elevation": 540.0}

def m_all_flags_stress(sf):
    sf[K_PROJ_GEOID] = "GEOID18"          # PP_GEOID_MISMATCH
    sf[K_BASELINE] = 15.0                 # PP_LONG_BASELINE
    sf["per_gcp"][0]["L1F_PP_010_per_gcp_sigma_h"] = 0.03  # PP_GCP_SIGMA_MARGINAL
    sf[K_TARGET_SIZE] = 12.0; sf[K_GSD] = 5.0              # PP_TARGET_MARGINAL
    sf[K_FLIGHT_COND] = "monsoon"         # PP_FLIGHT_CONDITION_RISK


# ---- 12b: keys + mutators for the path/report/CP flag families -------------
K_DECL_HEIGHT_PA = "L1F_PP_025_declared_height_mode_per_artifact"
K_PROJ_HEIGHT = "L1F_PP_019_project_required_height_mode"
K_PATH_GEOTAG = "L1F_PP_032_declared_path_geotag"
K_CUST_CRS = "L1F_PP_030_customer_supplied_coord_crs"
K_CUST_ACC = "L1F_PP_031_customer_accuracy_claim"
K_GCP_DATE = "L1F_PP_052_gcp_coord_determination_date"
K_BASE_END = "L1F_PP_051_base_session_end_utc"
K_REP_CORS = "L1F_PP_057_cors_epoch_coverage_during_flight"
K_REP_TSYNC = "L1F_PP_058_time_sync_residuals"
K_REP_GRES = "L1F_PP_059_per_gcp_residuals"
K_REP_CORSQ = "L1F_PP_060_cors_quality_metrics"
K_REP_SETTINGS = "L1F_PP_061_report_actual_settings"
K_POLY = "L1F_PP_055_reconstruction_extent_polygon"


def _poly(sf, side):
    e0, n0 = 600000.0, 2000000.0
    sf[K_POLY] = [[e0, n0], [e0 + side, n0], [e0 + side, n0 + side], [e0, n0 + side]]


# Pass 2 (CBMI sheet concrete numbers) + REF/GEO/GCT completion
def m_excessive_baseline(sf): sf[K_BASELINE] = 35.0                      # #11: 20-40km -> PP_EXCESSIVE_BASELINE
def m_severely_incomplete(sf): sf[K_CAPTURED] = 20                       # #10: 12/20=0.6 -> PP_GEOTAGS_SEVERELY_INCOMPLETE
def m_height_wrong(sf): sf[K_DECL_HEIGHT_PA] = {"geotag": "ellipsoidal", "gcp": "ellipsoidal", "cp": "ellipsoidal"}  # #3 consistent-but-wrong
def m_output_crs_missing(sf):
    for r in sf["per_image"]:
        r["L1F_PP_005_crs_in_exif"] = None                              # #6 -> PP_OUTPUT_CRS_MISSING
def m_gcp_id_major(sf): sf["per_gcp"][0]["L1F_PP_008_gcp_id"] = ""        # #19 empty id -> PP_GCP_ID_MISMATCH
def m_gcp_count_marginal(sf): sf[K_EXTENT] = 18_000_000.0                 # #27 16 marginal -> PP_GCP_COUNT_MARGINAL
def m_partial_overlap(sf): sf[K_BASE_END] = "2024-06-15T09:50:00Z"        # #13 overlap 0.90 (coupled WRONG_BASE)
def m_gcp_clustered(sf): _poly(sf, 3394.0)                               # #28 hull ~70% (couples CP clustering via same polygon)
def m_gcp_severely_clustered(sf): _poly(sf, 4500.0)                       # #28 hull ~40%

# Customer-supplied path family (#4, #18, #21)
def _customer(sf, crs="WGS84", acc=0.015, date="2024-06-10"):
    sf[K_PATH_GCP] = "CUSTOMER_SUPPLIED"; sf[K_CUST_CRS] = crs
    sf[K_CUST_ACC] = acc; sf[K_GCP_DATE] = date
def m_customer_no_crs(sf): _customer(sf, crs=None)                       # PP_CUSTOMER_COORDS_NO_CRS
def m_customer_wrong_crs(sf): _customer(sf, crs="NAD83(2011)")          # PP_CUSTOMER_COORDS_WRONG_CRS
def m_customer_inadequate(sf): _customer(sf, acc=0.05)                   # PP_GCP_CUSTOMER_INADEQUATE
def m_customer_no_claim(sf): _customer(sf, acc=None)                     # PP_GCP_CUSTOMER_NO_ACCURACY_CLAIM
def m_customer_aged(sf): _customer(sf, date="2023-09-01")               # 287d -> PP_GCP_COORDS_AGED
def m_customer_stale(sf): _customer(sf, date="2022-01-01")             # >365d -> PP_GCP_COORDS_STALE

# Report-present family (#15, #20, #36)
def m_report_settings_mismatch(sf): sf[K_REP_SETTINGS] = {"datum": "NAD83(2011)", "geoid": "EGM2008"}  # PP_SETTINGS_*
def m_report_tsync_drift(sf): sf[K_REP_TSYNC] = {"max_ms": 500}          # PP_TIME_SYNC_DRIFT
def m_report_tsync_severe(sf): sf[K_REP_TSYNC] = {"max_ms": 2000}        # PP_TIME_SYNC_SEVERE
def m_report_residual_outliers(sf): sf[K_REP_GRES] = [{"res_h": 0.05}, {"res_h": 0.004}]            # 1 exceed
def m_report_residual_failures(sf): sf[K_REP_GRES] = [{"res_h": 0.05}, {"res_h": 0.06}, {"res_h": 0.07}]  # 3 exceed

# CORS path + report family (#12, #22)
def m_cors_minor_gap(sf): sf[K_PATH_GEOTAG] = "CORS"; sf[K_REP_CORS] = 0.97   # PP_CORS_MINOR_GAP
def m_cors_major_gap(sf): sf[K_PATH_GEOTAG] = "CORS"; sf[K_REP_CORS] = 0.90   # PP_CORS_MAJOR_GAP
def m_cors_degraded(sf): sf[K_PATH_GCP] = "CORS"; sf[K_REP_CORSQ] = {"status": "degraded"}  # PP_CORS_STATION_DEGRADED
def m_cors_unhealthy(sf): sf[K_PATH_GCP] = "CORS"; sf[K_REP_CORSQ] = {"status": "poor"}     # PP_CORS_STATION_UNHEALTHY

# CP-band family (#23, #25, #29, #30)
def m_cp_sigma_high(sf): sf["per_cp"][0]["L1F_PP_015_per_cp_sigma_h"] = 0.05   # PP_CP_SIGMA_HIGH
def m_cp_sigma_reject(sf): sf["per_cp"][0]["L1F_PP_015_per_cp_sigma_h"] = 0.15  # PP_CP_SIGMA_REJECT
def m_cp_weak(sf): sf["per_cp"] = [sf["per_cp"][i] for i in (0, 4, 8, 12, 16, 19)]  # 6 spread -> WEAK band
def m_cp_too_close(sf):
    gp = sf["per_gcp"][0]["L1F_PP_009_gcp_position"]
    sf["per_cp"][0]["L1F_PP_014_cp_position"] = {"easting": gp["easting"] + 30.0, "northing": gp["northing"], "elevation": 540.0}
def m_cp_sigma_marginal(sf): sf["per_cp"][0]["L1F_PP_015_per_cp_sigma_h"] = 0.03  # PP_CP_SIGMA_MARGINAL


PASS1 = [
    ("baseline", "gold-standard control", None),
    ("wrong_crs_datum", "project CRS != declared -> global gate", m_wrong_crs),
    ("wrong_projection", "declared UTM zone != survey location -> global gate", m_wrong_projection),
    ("gcp_autonomous", "GCP path AUTONOMOUS -> global gate", m_gcp_autonomous),
    ("geoid_mismatch", "geoid != project -> PP_GEOID_MISMATCH", m_geoid_mismatch),
    ("height_inconsistent", "height mode inconsistent -> PP_HEIGHT_MODE_INCONSISTENT", m_height_inconsistent),
    ("units_mismatch", "units != project -> PP_UNITS_MISMATCH", m_units_mismatch),
    ("localization_undisclosed", "localization null -> PP_LOCALIZATION_UNDISCLOSED", m_localization_undisclosed),
    ("output_crs_mismatch", "EXIF CRS != declared -> PP_OUTPUT_CRS_MISMATCH", m_output_crs_mismatch),
    ("provenance_mixed", "mixed realization/epoch -> PP_MIXED_PROVENANCE", m_provenance_mixed),
    ("geotag_partial_fix", "11/12 fixed -> PP_GEOTAG_PARTIAL_FIX", m_geotag_partial_fix),
    ("geotag_poor_fix", "7/12 fixed -> PP_GEOTAG_POOR_FIX", m_geotag_poor_fix),
    ("geotag_not_fixed", "4/12 fixed -> PP_GEOTAG_NOT_FIXED", m_geotag_not_fixed),
    ("wrong_base_paired", "base_file_id absent -> PP_WRONG_BASE_PAIRED", m_wrong_base),
    ("geotags_incomplete", "12/15 captured -> PP_GEOTAGS_INCOMPLETE", m_geotags_incomplete),
    ("long_baseline", "15 km -> PP_LONG_BASELINE", m_long_baseline),
    ("sparse_tiepoints", "overlap 60/50 -> PP_SPARSE_TIEPOINTS_RISK", m_sparse_tiepoints),
    ("sensor_mismatch", "camera serial differs -> PP_SENSOR_METADATA_MISMATCH", m_sensor_mismatch),
    ("monsoon_conditions", "adverse conditions -> PP_FLIGHT_CONDITION_RISK", m_monsoon),
    ("insufficient_overlap", "base window short -> overlap+pairing flags (coupled)", m_insufficient_overlap),
    ("gcp_sigma_marginal", "1 GCP 1.5x target -> PP_GCP_SIGMA_MARGINAL", m_gcp_sigma_marginal),
    ("gcp_sigma_high", "1 GCP 2.5x target -> PP_GCP_SIGMA_HIGH", m_gcp_sigma_high),
    ("gcp_sigma_reject", "1 GCP 7.5x target -> PP_GCP_SIGMA_REJECT", m_gcp_sigma_reject),
    ("coord_misparse", "GCP axis-swap -> PP_COORD_MISPARSE", m_coord_misparse),
    ("gcp_id_partial", "duplicate GCP id -> PP_GCP_ID_PARTIAL_MISMATCH", m_gcp_id_partial),
    ("undersized_network", "30 km2 site -> count+clustering (coupled)", m_undersized_network),
    ("target_marginal", "2.4 px target -> PP_TARGET_MARGINAL", m_target_marginal),
    ("target_invisible", "1.0 px target -> PP_TARGET_INVISIBLE", m_target_invisible),
    ("vegetation_dtm", "vegetated + DTM -> PP_VEG_DTM_UNRELIABLE", m_veg_dtm),
    ("no_check_points", "0 CPs -> UNVERIFIED_NO_CPS (apex unaffected)", m_no_cps),
    ("insufficient_cps", "3 CPs -> UNVERIFIED_INSUFFICIENT_CPS (apex unaffected)", m_insufficient_cps),
    ("cp_clustered", "CPs clustered -> UNVERIFIED_CP_CLUSTERED (apex unaffected)", m_cp_clustered),
    ("cp_not_independent", "CPs on GCPs -> UNVERIFIED_CP_NOT_INDEPENDENT (apex unaffected)", m_cp_not_independent),
    ("all_flags_stress", "5 non-gate flags, no global gate", m_all_flags_stress),
]
PASS2 = [
    # CBMI Problems sheet v1.0 concrete numbers + flag-family completion
    ("p2_excessive_baseline_35km", "#11: 35 km baseline (20-40 km band)", m_excessive_baseline),
    ("p2_geotags_severely_incomplete", "#10: 12/20 geotagged (<0.80)", m_severely_incomplete),
    ("p2_height_mode_wrong", "#3: all-ellipsoidal vs orthometric project -> PP_HEIGHT_MODE_WRONG", m_height_wrong),
    ("p2_output_crs_missing", "#6: EXIF CRS absent -> PP_OUTPUT_CRS_MISSING", m_output_crs_missing),
    ("p2_gcp_id_major", "#19: empty GCP id -> PP_GCP_ID_MISMATCH", m_gcp_id_major),
    ("p2_gcp_count_marginal", "#27: 16 GCPs over 18 km2 -> PP_GCP_COUNT_MARGINAL", m_gcp_count_marginal),
    ("p2_partial_overlap", "#13: base ends early, overlap 0.90 -> PARTIAL+WRONG_BASE", m_partial_overlap),
    ("p2_gcp_clustered", "#28: hull ~70% of extent -> PP_GCP_CLUSTERED (+CP via shared polygon)", m_gcp_clustered),
    ("p2_gcp_severely_clustered", "#28: hull ~40% -> PP_GCP_SEVERELY_CLUSTERED", m_gcp_severely_clustered),
    ("p2_customer_no_crs", "#4: customer GCPs, no CRS declared", m_customer_no_crs),
    ("p2_customer_wrong_crs", "#4: customer GCPs in wrong CRS", m_customer_wrong_crs),
    ("p2_customer_inadequate", "#18: customer accuracy 0.05 > 0.02 target", m_customer_inadequate),
    ("p2_customer_no_claim", "#18: customer GCPs, no accuracy claim", m_customer_no_claim),
    ("p2_customer_coords_aged", "#21: customer coords 287 days old", m_customer_aged),
    ("p2_customer_coords_stale", "#21: customer coords >365 days old", m_customer_stale),
    ("p2_report_settings_mismatch", "#36: report datum != declared", m_report_settings_mismatch),
    ("p2_report_tsync_drift", "#15: time-sync 500 ms", m_report_tsync_drift),
    ("p2_report_tsync_severe", "#15: time-sync 2 s", m_report_tsync_severe),
    ("p2_report_residual_outliers", "#20: 1 GCP residual over tolerance", m_report_residual_outliers),
    ("p2_report_residual_failures", "#20: 3 GCP residuals over tolerance", m_report_residual_failures),
    ("p2_cors_minor_gap", "#12: CORS path, 97% coverage", m_cors_minor_gap),
    ("p2_cors_major_gap", "#12: CORS path, 90% coverage", m_cors_major_gap),
    ("p2_cors_station_degraded", "#22: CORS path, station degraded", m_cors_degraded),
    ("p2_cors_station_unhealthy", "#22: CORS path, station poor", m_cors_unhealthy),
    ("p2_cp_sigma_high", "#23: 1 CP at 2.5x target", m_cp_sigma_high),
    ("p2_cp_sigma_reject", "#23: 1 CP at 7.5x target", m_cp_sigma_reject),
    ("p2_cp_count_weak", "#29: 6 CPs (5-9 band)", m_cp_weak),
    ("p2_cp_too_close", "#25: 1 CP 30 m from a GCP", m_cp_too_close),
    ("p2_cp_sigma_marginal", "#23: 1 CP at 1.5x target", m_cp_sigma_marginal),
]


# ---- EXPECT (self-validation; human-asserted invariants) -------------------
# (apex score | "gate0", exact flag-id set, verification_status). Pinned in 12a
# after a captured run and CROSS-CHECKED against the block renorm formula
# apex = 100 - weight_B * w_indicator * (100 - band_score) / A_block
# (A = active-weight after N/A redistribution: REF 0.93, GEO 0.97, GCT 0.70, SD 1.0).
EXPECT: dict = {
    "baseline":                 (100.0, set(), "VERIFIED"),
    "wrong_crs_datum":          ("gate0", {"FLG_PP_001"}, "VERIFIED"),
    "wrong_projection":         ("gate0", {"FLG_PP_002"}, "VERIFIED"),
    "gcp_autonomous":           ("gate0", {"FLG_PP_003"}, "VERIFIED"),
    "geoid_mismatch":           (92.5, {"FLG_PP_004"}, "VERIFIED"),
    "height_inconsistent":      (96.0, {"FLG_PP_005"}, "VERIFIED"),
    "units_mismatch":           (98.1, {"FLG_PP_009"}, "VERIFIED"),
    "localization_undisclosed": (99.7, {"FLG_PP_012"}, "VERIFIED"),
    "output_crs_mismatch":      (96.2, {"FLG_PP_008"}, "VERIFIED"),
    "provenance_mixed":         (99.8, {"FLG_PP_013"}, "VERIFIED"),
    "geotag_partial_fix":       (98.1, {"FLG_PP_016"}, "VERIFIED"),
    "geotag_poor_fix":          (95.7, {"FLG_PP_017"}, "VERIFIED"),
    "geotag_not_fixed":         (93.8, {"FLG_PP_018"}, "VERIFIED"),
    "wrong_base_paired":        (92.3, {"FLG_PP_015"}, "VERIFIED"),
    "geotags_incomplete":       (97.7, {"FLG_PP_019"}, "VERIFIED"),
    "long_baseline":            (99.1, {"FLG_PP_021"}, "VERIFIED"),
    "sparse_tiepoints":         (98.5, {"FLG_PP_025"}, "VERIFIED"),
    "sensor_mismatch":          (99.5, {"FLG_PP_027"}, "VERIFIED"),
    "monsoon_conditions":       (99.9, {"FLG_PP_032"}, "VERIFIED"),
    "insufficient_overlap":     (90.1, {"FLG_PP_015", "FLG_PP_024"}, "VERIFIED"),
    "gcp_sigma_marginal":       (99.0, {"FLG_PP_033"}, "VERIFIED"),
    "gcp_sigma_high":           (97.7, {"FLG_PP_034"}, "VERIFIED"),
    "gcp_sigma_reject":         (96.7, {"FLG_PP_035"}, "VERIFIED"),
    "coord_misparse":           (98.2, {"FLG_PP_042"}, "VERIFIED"),
    "gcp_id_partial":           (98.9, {"FLG_PP_038"}, "VERIFIED"),
    "undersized_network":       (97.2, {"FLG_PP_048"}, "VERIFIED"),
    "target_marginal":          (99.4, {"FLG_PP_051"}, "VERIFIED"),
    "target_invisible":         (99.0, {"FLG_PP_052"}, "VERIFIED"),
    "vegetation_dtm":           (99.7, {"FLG_PP_053"}, "VERIFIED"),
    "no_check_points":          (100.0, {"FLG_PP_055", "FLG_PP_067"}, "UNVERIFIED_NO_CPS"),
    "insufficient_cps":         (100.0, {"FLG_PP_055", "FLG_PP_062", "FLG_PP_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "cp_clustered":             (100.0, {"FLG_PP_055", "FLG_PP_064"}, "UNVERIFIED_CP_CLUSTERED"),
    "cp_not_independent":       (100.0, {"FLG_PP_055", "FLG_PP_066"}, "UNVERIFIED_CP_NOT_INDEPENDENT"),
    "all_flags_stress":         (89.9, {"FLG_PP_004", "FLG_PP_021", "FLG_PP_032", "FLG_PP_033", "FLG_PP_051"}, "VERIFIED"),
    # ---- Pass 2: CBMI Problems sheet v1.0 + flag-family completion ----
    "p2_excessive_baseline_35km":     (98.1, {"FLG_PP_022"}, "VERIFIED"),
    "p2_geotags_severely_incomplete": (95.3, {"FLG_PP_020"}, "VERIFIED"),
    "p2_height_mode_wrong":           (94.4, {"FLG_PP_006"}, "VERIFIED"),
    "p2_output_crs_missing":          (98.1, {"FLG_PP_007"}, "VERIFIED"),
    "p2_gcp_id_major":                (97.5, {"FLG_PP_039"}, "VERIFIED"),
    "p2_gcp_count_marginal":          (98.8, {"FLG_PP_047"}, "VERIFIED"),
    "p2_partial_overlap":             (90.7, {"FLG_PP_015", "FLG_PP_023"}, "VERIFIED"),
    "p2_gcp_clustered":               (98.6, {"FLG_PP_049", "FLG_PP_055", "FLG_PP_063"}, "UNVERIFIED_CP_CLUSTERED"),
    "p2_gcp_severely_clustered":      (97.5, {"FLG_PP_050", "FLG_PP_055", "FLG_PP_064"}, "UNVERIFIED_CP_CLUSTERED"),
    "p2_customer_no_crs":             (98.7, {"FLG_PP_010"}, "VERIFIED"),
    "p2_customer_wrong_crs":          (98.2, {"FLG_PP_011"}, "VERIFIED"),
    "p2_customer_inadequate":         (97.2, {"FLG_PP_036"}, "VERIFIED"),
    "p2_customer_no_claim":           (97.2, {"FLG_PP_037"}, "VERIFIED"),
    "p2_customer_coords_aged":        (98.7, {"FLG_PP_040"}, "VERIFIED"),
    "p2_customer_coords_stale":       (98.2, {"FLG_PP_041"}, "VERIFIED"),
    "p2_report_settings_mismatch":    (99.5, {"FLG_PP_014"}, "VERIFIED"),
    "p2_report_tsync_drift":          (99.9, {"FLG_PP_030"}, "VERIFIED"),
    "p2_report_tsync_severe":         (99.8, {"FLG_PP_031"}, "VERIFIED"),
    "p2_report_residual_outliers":    (99.7, {"FLG_PP_043"}, "VERIFIED"),
    "p2_report_residual_failures":    (99.3, {"FLG_PP_044"}, "VERIFIED"),
    "p2_cors_minor_gap":              (99.8, {"FLG_PP_028"}, "VERIFIED"),
    "p2_cors_major_gap":              (99.4, {"FLG_PP_029"}, "VERIFIED"),
    "p2_cors_station_degraded":       (99.7, {"FLG_PP_045"}, "VERIFIED"),
    "p2_cors_station_unhealthy":      (99.3, {"FLG_PP_046"}, "VERIFIED"),
    "p2_cp_sigma_high":               (100.0, {"FLG_PP_059"}, "VERIFIED"),
    "p2_cp_sigma_reject":             (100.0, {"FLG_PP_060"}, "VERIFIED"),
    "p2_cp_count_weak":               (100.0, {"FLG_PP_055", "FLG_PP_061", "FLG_PP_063"}, "UNVERIFIED_CP_CLUSTERED"),
    "p2_cp_too_close":                (100.0, {"FLG_PP_055", "FLG_PP_065"}, "UNVERIFIED_CP_NOT_INDEPENDENT"),
    "p2_cp_sigma_marginal":           (100.0, {"FLG_PP_058"}, "VERIFIED"),
}


# ---- problem-coverage map (all 42 spec problems) ---------------------------
# (problem_no, coverage_class, cbmi_stage, [scenarios], coverage_verification)
PROBLEM_MAP = [
    (1, "FULLY COVERED", "Pre-processing (REF gate)", ["wrong_crs_datum"], "VERIFIED"),
    (2, "FULLY COVERED", "Pre-processing (REF)", ["geoid_mismatch", "all_flags_stress"], "VERIFIED"),
    (3, "FULLY COVERED", "Pre-processing (REF)", ["height_inconsistent", "p2_height_mode_wrong"], "VERIFIED"),
    (4, "FULLY COVERED", "Pre-processing (REF, CUSTOMER_SUPPLIED)", ["p2_customer_no_crs", "p2_customer_wrong_crs"], "VERIFIED"),
    (5, "FULLY COVERED", "Pre-processing (REF)", ["localization_undisclosed"], "VERIFIED"),
    (6, "FULLY COVERED", "Pre-processing (REF)", ["output_crs_mismatch", "p2_output_crs_missing"], "VERIFIED"),
    (7, "FULLY COVERED", "Pre-processing (REF gate)", ["wrong_projection"], "VERIFIED"),
    (8, "FULLY COVERED", "Pre-processing (REF)", ["units_mismatch"], "VERIFIED"),
    (9, "FULLY COVERED", "Pre-processing (GEO)", ["geotag_partial_fix", "geotag_poor_fix", "geotag_not_fixed"], "VERIFIED"),
    (10, "FULLY COVERED", "Pre-processing (GEO)", ["geotags_incomplete", "p2_geotags_severely_incomplete"], "VERIFIED"),
    (11, "FULLY COVERED", "Pre-processing (GEO)", ["long_baseline", "p2_excessive_baseline_35km"], "VERIFIED"),
    (12, "FULLY COVERED (report-tier)", "Pre-processing (GEO, report+CORS)", ["p2_cors_minor_gap", "p2_cors_major_gap"], "VERIFIED"),
    (13, "FULLY COVERED", "Pre-processing (GEO)", ["insufficient_overlap", "p2_partial_overlap"], "VERIFIED"),
    (14, "PARTIAL (v1 limitation)", "Pre-processing (GEO, declared-only)", [], "DEFERRED_SPEC_GAP"),
    (15, "FULLY COVERED (report-tier)", "Pre-processing (GEO, report)", ["p2_report_tsync_drift", "p2_report_tsync_severe"], "VERIFIED"),
    (16, "FULLY COVERED", "Pre-processing (GCT)", ["gcp_sigma_marginal", "gcp_sigma_high", "gcp_sigma_reject"], "VERIFIED"),
    (17, "FULLY COVERED", "Pre-processing (GCT gate)", ["gcp_autonomous"], "VERIFIED"),
    (18, "FULLY COVERED", "Pre-processing (GCT, CUSTOMER_SUPPLIED)", ["p2_customer_inadequate", "p2_customer_no_claim"], "VERIFIED"),
    (19, "FULLY COVERED", "Pre-processing (GCT)", ["gcp_id_partial", "p2_gcp_id_major"], "VERIFIED"),
    (20, "FULLY COVERED (report-tier)", "Pre-processing (GCT, report)", ["p2_report_residual_outliers", "p2_report_residual_failures"], "VERIFIED"),
    (21, "FULLY COVERED", "Pre-processing (GCT, CUSTOMER_SUPPLIED)", ["p2_customer_coords_aged", "p2_customer_coords_stale"], "VERIFIED"),
    (22, "FULLY COVERED (report-tier)", "Pre-processing (GCT, report+CORS)", ["p2_cors_station_degraded", "p2_cors_station_unhealthy"], "VERIFIED"),
    (23, "OWNED_BY_VERIFICATION_STATUS", "verification_status", ["p2_cp_sigma_marginal", "p2_cp_sigma_high", "p2_cp_sigma_reject"], "VERIFIED"),
    (24, "OWNED_BY_VERIFICATION_STATUS", "verification_status", [], "DEFERRED_SPEC_GAP"),
    (25, "OWNED_BY_VERIFICATION_STATUS", "verification_status", ["cp_not_independent", "p2_cp_too_close"], "VERIFIED"),
    (26, "OWNED_BY_VERIFICATION_STATUS", "verification_status (null_handler)", ["no_check_points"], "VERIFIED"),
    (27, "FULLY COVERED", "Pre-processing (SD)", ["undersized_network", "p2_gcp_count_marginal"], "VERIFIED"),
    (28, "FULLY COVERED", "Pre-processing (SD)", ["p2_gcp_clustered", "p2_gcp_severely_clustered"], "VERIFIED"),
    (29, "OWNED_BY_VERIFICATION_STATUS", "verification_status", ["insufficient_cps", "p2_cp_count_weak"], "VERIFIED"),
    (30, "OWNED_BY_VERIFICATION_STATUS", "verification_status", ["cp_clustered"], "VERIFIED"),
    (31, "FULLY COVERED", "Pre-processing (SD)", ["target_marginal", "target_invisible"], "VERIFIED"),
    (32, "HANDOFF (processing)", "future processing_score", [], "DEFERRED_HANDOFF"),
    (33, "FULLY COVERED", "Pre-processing (GEO)", ["wrong_base_paired"], "VERIFIED"),
    (34, "FULLY COVERED", "Pre-processing (GEO)", ["sensor_mismatch"], "VERIFIED"),
    (35, "PARTIAL (v1 advisory)", "Pre-processing (SD, v2)", [], "DEFERRED_SPEC_GAP"),
    (36, "FULLY COVERED (report-tier)", "Pre-processing (REF, report)", ["p2_report_settings_mismatch"], "VERIFIED"),
    (37, "FULLY COVERED", "Pre-processing (GEO)", ["sparse_tiepoints"], "VERIFIED"),
    (38, "FULLY COVERED", "Pre-processing (SD)", ["vegetation_dtm"], "VERIFIED"),
    (39, "HANDOFF (analytics)", "future volume analytics", [], "DEFERRED_HANDOFF"),
    (40, "FULLY COVERED", "Pre-processing (GCT)", ["coord_misparse"], "VERIFIED"),
    (41, "FULLY COVERED (advisory)", "Pre-processing (GEO, cross-handoff drone)", ["monsoon_conditions"], "VERIFIED"),
    (42, "FULLY COVERED", "Pre-processing (REF)", ["provenance_mixed"], "VERIFIED"),
]


def _write_problem_coverage(results):
    spec_problems = {p["problem_no"]: p for p in _SPEC.get("problem_coverage_map", [])}
    rows = []
    for pno, cov, stage, scens, vstatus in PROBLEM_MAP:
        sp = spec_problems.get(pno, {})
        flags = sorted({fl for s in scens for fl in results.get(s, {}).get("flags", [])})
        rows.append({
            "problem_no": pno, "problem": sp.get("problem", ""), "severity": sp.get("severity", ""),
            "spec_disposition": sp.get("disposition", ""), "cbmi_coverage_class": cov,
            "cbmi_stage": stage, "scenarios": scens, "flags_observed": flags,
            "coverage_verification": vstatus,
        })
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    (SCENARIOS_DIR / "_pass2_problem_coverage.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (SCENARIOS_DIR / "_pass2_problem_coverage.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["problem_no", "problem", "severity", "spec_disposition", "cbmi_coverage_class",
                    "cbmi_stage", "scenarios", "flags_observed", "coverage_verification"])
        for r in rows:
            w.writerow([r["problem_no"], r["problem"], r["severity"], r["spec_disposition"],
                        r["cbmi_coverage_class"], r["cbmi_stage"], ";".join(r["scenarios"]),
                        ";".join(r["flags_observed"]), r["coverage_verification"]])
    return rows


def _write_det(path, stage, data):
    env = {"spec_version": _SPEC_VERSION, "config_used": _CONFIG, "stage": stage, "data": data}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(env, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def _run_one(name, mutator):
    d2 = copy.deepcopy(_BASELINE2)
    if mutator:
        mutator(d2["source_fields"])
    d3a = stage3a_derived.run(_CONFIG, ROOT, _SPEC, d2)
    d3b = stage3b_indicators.run(_CONFIG, ROOT, _SPEC, d3a, d2)
    d3c = stage3c_blocks.run(_CONFIG, ROOT, _SPEC, d3b)
    d3d = stage3d_score.run(_CONFIG, ROOT, _SPEC, d2, d3a, d3b, d3c)
    out = SCENARIOS_DIR / name
    _write_det(out / "02_source_fields.json", "stage2_merge", d2)
    _write_det(out / "03_derived.json", "stage3a_derived", d3a)
    _write_det(out / "04_indicators.json", "stage3b_indicators", d3b)
    _write_det(out / "05_blocks.json", "stage3c_blocks", d3c)
    _write_det(out / "05b_views.json", "stage3c_blocks", {"per_artifact_views": d3c["per_artifact_views"]})
    _write_det(out / "06_apex.json", "stage3d_score", d3d)
    return {
        "scenario": name,
        "pre_processing_score": d3d["pre_processing_score"],
        "global_gate": d3d["global_gate"]["triggered"],
        "verification_status": d3d["verification_status"]["value"],
        "block_scores": d3c["stage3c_meta"]["block_score_summary"],
        "view_scores": d3c["stage3c_meta"]["view_score_summary"],
        "flags": sorted({f["flag_id"] for f in d3d["all_flags_aggregated"]}),
    }


def _check(name, row):
    if name not in EXPECT:
        return None, [f"(no EXPECT for {name}: score={row['pre_processing_score']} "
                      f"flags={row['flags']} vstatus={row['verification_status']})"]
    exp_score, exp_flags, exp_vs = EXPECT[name]
    fails = []
    got = row["pre_processing_score"]
    if exp_score == "gate0":
        if not (got == 0.0 and row["global_gate"]):
            fails.append(f"score: expected 0.0 via gate, got {got} (gate={row['global_gate']})")
    elif abs(got - exp_score) > SCORE_TOL:
        fails.append(f"score: expected {exp_score}+-{SCORE_TOL}, got {got}")
    if set(row["flags"]) != exp_flags:
        fails.append(f"flags: missing={sorted(exp_flags-set(row['flags']))} "
                     f"extra={sorted(set(row['flags'])-exp_flags)}")
    if exp_vs and row["verification_status"] != exp_vs:
        fails.append(f"verification_status: expected {exp_vs}, got {row['verification_status']}")
    return not fails, fails


def main(argv=None):
    global _CONFIG, _SPEC, _SPEC_VERSION, _BASELINE2
    parser = argparse.ArgumentParser(description="Pre-Processing smoke-test harness")
    parser.add_argument("config", nargs="?", default=str(ROOT / "paths.json"))
    args = parser.parse_args(argv)
    _CONFIG = common.load_config(Path(args.config).resolve())
    _SPEC = common.load_spec(ROOT, _CONFIG)
    _SPEC_VERSION = _SPEC["_meta"]["version"]

    env1, hard = stage1_inventory.run(_CONFIG, ROOT)
    if hard:
        print("HALT: Stage 1 hard failure; cannot build baseline.")
        return 1
    _BASELINE2 = stage2_merge.run(_CONFIG, ROOT, _SPEC, env1["data"])

    scenarios = PASS1 + PASS2
    results, validation_failures, uncovered = {}, [], []
    print(f"Running {len(scenarios)} scenarios (Pass 1: {len(PASS1)}, Pass 2: {len(PASS2)})\n")
    for name, desc, mut in scenarios:
        row = _run_one(name, mut)
        results[name] = row
        ok, fails = _check(name, row)
        tag = "OK  " if ok else ("MISS" if ok is None else "FAIL")
        if ok is None:
            uncovered.append(name)
        elif not ok:
            validation_failures.append((name, fails))
        gate = " GATE" if row["global_gate"] else ""
        print(f"  [{tag}] {name:24s} score={str(row['pre_processing_score']):6s}{gate}  "
              f"vstatus={row['verification_status']:28s} flags={row['flags']}")
        for f in fails:
            if not ok:
                print(f"         -> {f}")

    (SCENARIOS_DIR / "_summary.json").parent.mkdir(parents=True, exist_ok=True)
    (SCENARIOS_DIR / "_summary.json").write_text(
        json.dumps({"results": results, "pass1": len(PASS1), "pass2": len(PASS2)},
                   indent=2, sort_keys=True) + "\n", encoding="utf-8")

    prob_rows = _write_problem_coverage(results)
    cov_tally: dict = {}
    for r in prob_rows:
        cov_tally[r["coverage_verification"]] = cov_tally.get(r["coverage_verification"], 0) + 1
    all_flags = sorted({fl for r in results.values() for fl in r["flags"]})
    spec_flag_ids = {f["flag_id"] for f in _SPEC["flags"]}

    print(f"\n  {len(scenarios)} scenarios | covered {len(scenarios)-len(uncovered)} | "
          f"validation failures {len(validation_failures)} | uncovered(no EXPECT) {len(uncovered)}")
    print(f"  problem-coverage map: {len(prob_rows)} problems  {cov_tally}")
    print(f"  distinct flags exercised: {len(all_flags)}/{len(spec_flag_ids)}  "
          f"unreached(by design): {sorted(spec_flag_ids - set(all_flags))}")
    if uncovered:
        print(f"  uncovered: {uncovered}")
    if validation_failures:
        for n, fs in validation_failures:
            print(f"  FAIL {n}: {fs}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
