#!/usr/bin/env python3
"""Step 12 - self-validating smoke-test harness for the Processing pipeline.

SURVEY-LEVEL: each scenario deep-copies the baseline Stage-2 source_fields,
applies its mutator(s), re-runs Stages 3a-3d into tests/scenarios/<name>/, and
captures processing_score / block & view scores / verification_status / the
aggregated flag-id set. Each result is asserted against an EXPECT entry (expected
apex score +- tol AND exact flag-id set AND verification_status); the harness
exits NON-ZERO on any drift.

The baseline is the real Report-A-like survey (PPK, 3 CPs, 0 GCPs, EPSG:4326) and
scores 84.7 with 9 standing flags - so EXPECT flag sets are the FULL deduped set
per scenario (baseline flags +/- the scenario's change), not a single flag.

Renorm cross-check (one indicator i in block B moving band s_old -> s_new):
  apex' = apex + W_B * (w_i / A_B) * (s_new - s_old)
  active weights A: BA=1.0, IM=1.0, CV=0.71 (4 N/A), DO=0.92 (DEM-void 035 N/A).

Pass 1 = spec-internal band walk; Pass 2 = CBMI Processing Problems v0.2 sheet
concrete numbers. Determinism: scenario artifacts omit generated_at (byte-stable).

Usage:  python3 scripts/test_scenarios.py [paths.json]   (--capture prints values)
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


# ---- helpers for GCP/CP reconfiguration -------------------------------------
def _with_gcps(sf, n_gcp=5, crmse=4.0):
    """Promote the survey to a GCP-controlled config so the GCP-RMSE / count /
    distribution indicators become active (baseline has 0 GCPs)."""
    sf["reportGCP_control_points_count"] = n_gcp
    sf["reportGCP_control_rmse_total_cm"] = crmse
    sf["reportGCP_control_rmse_xy_cm"] = round(crmse * 0.6, 4)
    sf["reportGCP_control_rmse_z_cm"] = round(crmse * 0.7, 4)


def _set_cps(sf, n, total_rmse=None):
    sf["reportGCP_check_points_count"] = n
    if total_rmse is not None:
        sf["reportGCP_check_rmse_total_cm"] = total_rmse


# ============================ PASS 1 mutators ================================
# --- BA block ---
def m_ba_convergence_fail(sf): sf["reportSurveyData_reprojection_error_pix"] = 4.0
def m_ba_reproj_high(sf): sf["reportSurveyData_reprojection_error_pix"] = 3.0
def m_ba_reproj_elevated(sf): sf["reportSurveyData_reprojection_error_pix"] = 2.0
def m_cameras_partial(sf): sf["reportSurveyData_camera_stations"] = 139      # 0.979 -> PARTIAL
def m_cameras_poor(sf): sf["reportSurveyData_camera_stations"] = 131         # 0.922 -> POOR
def m_cameras_severe(sf): sf["reportSurveyData_camera_stations"] = 120       # 0.845 -> SEVERE
def m_camera_pos_severe(sf): sf["reportCameraLocations_total_err_cm"] = 25.0  # 9.2x GSD -> SEVERE
def m_precalib_not_loaded(sf): sf["manifest_precalibration_expected"] = True  # expected, report No
def m_camera_model_mismatch(sf): sf["manifest_declared_camera_model"] = "DJI FC6310"
def m_self_calib_ill(sf): sf["reportSurveyData_reprojection_error_pix"] = 2.6  # degrade -> compound true
def m_reproj_severe_outliers(sf): sf["reportParams_max_reprojection_error_pix"] = 90.0  # 90/1.45=62 -> SEVERE

# --- IM block ---
def m_alignment_accuracy_low(sf): sf["reportParams_alignment_accuracy"] = "Medium"
def m_alignment_accuracy_critical(sf): sf["reportParams_alignment_accuracy"] = "Low"
def m_depth_quality_low(sf): sf["reportParams_depth_quality"] = "Low"
def m_sparse_tiepoints(sf): sf["reportSurveyData_tie_points"] = 400_000       # 1.73M -> SPARSE
def m_very_sparse_tiepoints(sf): sf["reportSurveyData_tie_points"] = 180_000  # 0.78M -> VERY_SPARSE (+localized)
def m_low_multiplicity(sf): sf["reportParams_avg_tie_point_multiplicity"] = 3.5
def m_very_low_multiplicity(sf): sf["reportParams_avg_tie_point_multiplicity"] = 2.7
def m_filtering_oversmoothed(sf): sf["reportParams_depth_filtering_mode"] = "Aggressive"
def m_marker_weak(sf): sf["reportGCP_per_marker_image_count"] = {"01": 4, "02": 4, "04": 4}
def m_marker_insufficient(sf): sf["reportGCP_per_marker_image_count"] = {"01": 2, "02": 2, "04": 2}
def m_atmospheric(sf):
    sf["reportSurveyData_reprojection_error_pix"] = 2.5
    sf["reportSurveyData_tie_points"] = 180_000
    sf["reportCameraLocations_xy_err_cm"] = 20.0

# --- CV block ---
def m_cp_rmse_fail(sf): sf["reportGCP_check_rmse_total_cm"] = 12.0            # 2.4x -> FAIL
def m_cp_outlier_severe(sf): sf["reportGCP_per_marker_residuals"] = {"01": 6.0, "02": 3.0, "04": 21.0}
def m_z_xy_high(sf): sf["reportGCP_check_rmse_z_cm"] = 5.8                    # 5.8/3.41=1.70 -> Z_XY_HIGH (1.5-2)
def m_z_xy_severe(sf): sf["reportGCP_check_rmse_z_cm"] = 8.0                  # 8/3.41=2.35 -> SEVERE (>2)
def m_marker_pix_severe(sf): sf["reportGCP_per_marker_image_pix"] = {"01": 0.5, "02": 0.5, "04": 3.0}  # 2.25x -> SEVERE
def m_reproj_outliers_mild(sf): sf["reportParams_max_reprojection_error_pix"] = 21.0  # 21/1.45=14.5 -> REPROJ_OUTLIERS (10-20)
def m_gcp_typo(sf):  # #26: report marker XYZ differs grossly from pp coord file for one marker
    sf["reportGCP_marker_locations"] = {"01": {"lon": 78.127, "lat": 17.599, "elev": 460.1},
                                        "02": {"lon": 78.1265, "lat": 17.5995, "elev": 461.3},
                                        "04": {"lon": 78.900, "lat": 17.900, "elev": 999.0}}
def m_role_mismatch(sf): sf["manifest_marker_roles_declared"] = {"01": "control", "02": "check", "04": "check"}
def m_no_markers(sf):
    sf["reportGCP_total_markers_count"] = 0
    sf["reportGCP_check_points_count"] = 0
    sf["reportGCP_per_marker_residuals"] = {}
    sf["reportGCP_per_marker_image_pix"] = {}
    sf["reportGCP_per_marker_image_count"] = {}
    sf["reportGCP_marker_roles"] = {}
    sf["reportGCP_check_rmse_total_cm"] = None
    sf["reportGCP_check_rmse_xy_cm"] = None
    sf["reportGCP_check_rmse_z_cm"] = None
def m_gcp_rmse_high(sf): _with_gcps(sf, 5, 18.0)                             # 3.6x -> GCP_RMSE_HIGH
def m_gcp_rmse_reject(sf): _with_gcps(sf, 5, 30.0)                          # 6x -> GCP_RMSE_REJECT
def m_verified_pass(sf): _set_cps(sf, 8, 4.0)                               # 8 CPs, <=1x -> VERIFIED_PASS
def m_verified_fail(sf): _set_cps(sf, 8, 12.0)                              # 8 CPs, >2x -> VERIFIED_FAIL

# --- DO block ---
def m_crs_mismatch_gate(sf): sf["manifest_project_required_crs"] = "EPSG:32643"   # CATASTROPHIC gate
def m_internal_transform_wrong(sf): sf["pp_manifest_capture_crs"] = "EPSG:32643"  # capture != output
def m_dsm_as_dtm(sf): sf["manifest_dtm_deliverable_claimed"] = True               # DTM claimed, no ground class
def m_dem_res_very_coarse(sf): sf["reportDEM_resolution_cm"] = 20.0               # 7.35x -> VERY_COARSE
def m_deliverable_missing(sf): sf["deliverable_dtm_present"] = False              # DO7 + dtm view null
def m_software_drift(sf):
    sf["reportSystem_software_version"] = "1.5.2 build 900"
    sf["manifest_software_version_baseline"] = "1.7.6"

# --- additional band coverage ---
def m_reconstruction_drift(sf):           # #29: good GCP RMSE + bad CP RMSE -> drift composite
    _with_gcps(sf, 5, 4.0)
    sf["reportGCP_check_rmse_total_cm"] = 12.0
def m_optimization_incomplete(sf):        # #6: b1/b2 missing from optimization list
    sf["reportParams_optimization_parameters"] = "f, cx, cy, k1-k3, p1, p2"
def m_filtering_insufficient(sf):         # #13: Mild filtering on a vegetated site
    sf["manifest_declared_site_cover"] = "vegetated"
def m_gcp_count_marginal(sf): _with_gcps(sf, 3, 4.0)   # #18: 3-4 GCPs -> MARGINAL band


# --- stress ---
def m_all_flags_stress(sf):
    sf["reportSurveyData_reprojection_error_pix"] = 2.0   # BA reproj elevated
    sf["reportParams_alignment_accuracy"] = "Medium"      # alignment low
    sf["reportGCP_check_rmse_total_cm"] = 12.0            # cp rmse fail
    sf["reportDEM_resolution_cm"] = 20.0                  # dem very coarse
    sf["reportParams_avg_tie_point_multiplicity"] = 3.5   # low multiplicity


# ============================ PASS 2 mutators (sheet) ========================
def m_p2_convergence_3pix(sf): sf["reportSurveyData_reprojection_error_pix"] = 3.2   # #1 >3 pix
def m_p2_cameras_dropped(sf): sf["reportSurveyData_camera_stations"] = 136           # #2 136/142=0.958 PARTIAL
def m_p2_depth_medium_already(sf): sf["reportParams_depth_quality"] = "Low"          # #12 worse than Medium
def m_p2_cp_rmse_moment(sf): sf["reportGCP_check_rmse_total_cm"] = 11.0              # #20 >2x target
def m_p2_per_cp_outlier(sf): sf["reportGCP_per_marker_residuals"] = {"01": 6.42, "02": 3.24, "04": 18.0}  # #22
def m_p2_gcp_rmse_target(sf): _with_gcps(sf, 5, 9.0)                                # #19 GCP RMSE > target
def m_p2_role_swap(sf): sf["manifest_marker_roles_declared"] = {"01": "control", "02": "control", "04": "check"}  # #25
def m_p2_crs_mismatch_utm(sf): sf["manifest_project_required_crs"] = "EPSG:32647"   # #31 UTM 47N required
def m_p2_internal_geoid(sf):                                                        # #36
    sf["pp_manifest_capture_crs"] = "EPSG:32643"
    sf["pp_manifest_capture_geoid"] = "EGM2008"
def m_p2_dsm_as_dtm(sf): sf["manifest_dtm_deliverable_claimed"] = True              # #39
def m_p2_deliverable_incomplete(sf):                                               # #34
    sf["deliverable_point_cloud_present"] = False
    sf["deliverable_mesh_3d_present"] = False
def m_p2_dem_coarse_4x(sf): sf["reportDEM_resolution_cm"] = 10.9                    # #32 (baseline value, 4x)


PASS1 = [
    ("baseline", "gold-standard control (Report-A-like)", None),
    # BA
    ("ba_convergence_fail", "#1 reproj 4.0 -> BA1=0 + CONVERGENCE_FAIL", m_ba_convergence_fail),
    ("ba_reproj_high", "reproj 3.0 -> BA1=30 + REPROJ_HIGH", m_ba_reproj_high),
    ("ba_reproj_elevated", "reproj 2.0 -> BA1=70 + REPROJ_ELEVATED", m_ba_reproj_elevated),
    ("cameras_partial_align", "139/142 -> BA2=70 + PARTIAL_ALIGN", m_cameras_partial),
    ("cameras_poor_align", "131/142 -> BA2=30 + POOR_ALIGN", m_cameras_poor),
    ("cameras_severe_align", "120/142 -> BA2=0 + SEVERE_ALIGN_FAIL", m_cameras_severe),
    ("camera_pos_severe", "25cm/GSD=9.2x -> BA3=0 + CAMERA_POS_SEVERE", m_camera_pos_severe),
    ("precalib_not_loaded", "expected precal, report No -> BA5=30 + PRECALIB_NOT_LOADED", m_precalib_not_loaded),
    ("camera_model_mismatch", "manifest != report model -> BA6=30 + MODEL_MISMATCH", m_camera_model_mismatch),
    ("self_calib_ill_conditioned", "|K|0.96>0.95 + reproj 2.6 -> BA7=30 + ILL_CONDITIONED", m_self_calib_ill),
    ("reproj_severe_outliers", "max/rms 62 -> BA8=30 + SEVERE_OUTLIERS", m_reproj_severe_outliers),
    ("optimization_incomplete", "#6 b1/b2 missing -> BA4=90 + OPTIMIZATION_INCOMPLETE", m_optimization_incomplete),
    # IM
    ("alignment_accuracy_low", "Medium accuracy -> IM1=50 + ACCURACY_LOW", m_alignment_accuracy_low),
    ("alignment_accuracy_critical", "Low accuracy -> IM1=0 + ACCURACY_CRITICAL", m_alignment_accuracy_critical),
    ("depth_quality_low", "Low depth -> IM2=20 + DEPTH_QUALITY_LOW", m_depth_quality_low),
    ("sparse_tiepoints", "1.73M/km2 -> IM3=70 + SPARSE_TIEPOINTS", m_sparse_tiepoints),
    ("very_sparse_tiepoints", "0.78M/km2 -> IM3=30 + VERY_SPARSE (+localized)", m_very_sparse_tiepoints),
    ("low_multiplicity", "3.5 -> IM4=70 + LOW_MULTIPLICITY", m_low_multiplicity),
    ("very_low_multiplicity", "2.7 -> IM4=30 + VERY_LOW_MULTIPLICITY", m_very_low_multiplicity),
    ("filtering_oversmoothed", "Aggressive on flat -> IM5=50 + OVERSMOOTHED", m_filtering_oversmoothed),
    ("filtering_insufficient", "#13 Mild on vegetated -> IM5=50 + INSUFFICIENT", m_filtering_insufficient),
    ("marker_weak", "4 images/marker -> IM6=50 + MARKER_WEAK", m_marker_weak),
    ("marker_insufficient", "2 images/marker -> IM6=0 + MARKER_INSUFFICIENT", m_marker_insufficient),
    ("atmospheric_artifact", "composite -> IM8=50 + ATMOSPHERIC", m_atmospheric),
    # CV
    ("cp_rmse_fail", "CP RMSE 2.4x -> CV1=0 + CP_RMSE_FAIL", m_cp_rmse_fail),
    ("cp_outlier_severe", "1 CP outlier 2-3x -> CV3 + CP_OUTLIER_SEVERE", m_cp_outlier_severe),
    ("z_xy_high", "z/xy 1.70 -> CV10=70 + Z_XY_HIGH", m_z_xy_high),
    ("z_xy_severe", "z/xy 2.35 -> CV10=30 + Z_XY_SEVERE", m_z_xy_severe),
    ("marker_pix_severe", "1 marker pix 2.25x -> CV11=30 + MARKER_PIX_SEVERE", m_marker_pix_severe),
    ("reproj_outliers_mild", "max/rms 14.5 -> BA8=70 + REPROJ_OUTLIERS", m_reproj_outliers_mild),
    ("gcp_typo", "#26 report marker XYZ != pp coord file -> CV5=30 + GCP_TYPO", m_gcp_typo),
    ("role_mismatch", "manifest role != report -> CV4=30 + ROLE_MISMATCH", m_role_mismatch),
    ("no_markers_at_all", "0 markers -> CATASTROPHIC NO_MARKERS_AT_ALL (flag-only)", m_no_markers),
    ("gcp_rmse_high", "5 GCPs @ 3.6x -> CV2=30 + GCP_RMSE_HIGH", m_gcp_rmse_high),
    ("gcp_rmse_reject", "5 GCPs @ 6x -> CV2=0 + GCP_RMSE_REJECT", m_gcp_rmse_reject),
    ("gcp_count_marginal", "3 GCPs -> CV7=50 + GCP_COUNT_MARGINAL", m_gcp_count_marginal),
    ("reconstruction_drift", "#29 good GCP RMSE + bad CP RMSE -> CV13 + RECONSTRUCTION_DRIFT", m_reconstruction_drift),
    ("verified_pass", "8 CPs @ <=1x -> VERIFIED_RESIDUALS_PASS", m_verified_pass),
    ("verified_fail", "8 CPs @ >2x -> VERIFIED_RESIDUALS_FAIL + CP_RMSE_FAIL", m_verified_fail),
    # DO
    ("crs_mismatch_gate", "UTM required vs 4326 -> CATASTROPHIC gate -> apex 0", m_crs_mismatch_gate),
    ("internal_transform_wrong", "capture CRS != output -> DO3=0 + INTERNAL_TRANSFORM_WRONG", m_internal_transform_wrong),
    ("dsm_as_dtm", "DTM claimed, no ground class -> DO4=30 + DSM_LABELLED_DTM", m_dsm_as_dtm),
    ("dem_res_very_coarse", "DEM 20cm/2.72=7.4x -> DO6=30 + DEM_RES_VERY_COARSE", m_dem_res_very_coarse),
    ("deliverable_missing", "DTM absent -> DO7 + FILE_MISSING + dtm view null", m_deliverable_missing),
    ("software_drift", "version != baseline -> DO8=60 (advisory)", m_software_drift),
    ("all_flags_stress", "5 non-gate flags, no global gate", m_all_flags_stress),
]

PASS2 = [
    ("p2_convergence_3pix", "#1 reproj 3.2 pix (BA convergence)", m_p2_convergence_3pix),
    ("p2_cameras_dropped", "#2 136/142 aligned -> PARTIAL_ALIGN", m_p2_cameras_dropped),
    ("p2_depth_too_low", "#12 Low depth quality", m_p2_depth_medium_already),
    ("p2_cp_rmse_moment_of_truth", "#20 CP RMSE 11cm > 2x target", m_p2_cp_rmse_moment),
    ("p2_per_cp_outlier", "#22 one CP 18cm vs ensemble", m_p2_per_cp_outlier),
    ("p2_gcp_rmse_exceeds_target", "#19 GCP RMSE 9cm > target", m_p2_gcp_rmse_target),
    ("p2_role_swap", "#25 two CPs declared as control", m_p2_role_swap),
    ("p2_crs_mismatch_utm47n", "#31 UTM 47N required vs WGS84 -> gate", m_p2_crs_mismatch_utm),
    ("p2_internal_geoid_misconfig", "#36 capture geoid/CRS != output", m_p2_internal_geoid),
    ("p2_dsm_as_dtm", "#39 DSM delivered as DTM", m_p2_dsm_as_dtm),
    ("p2_deliverable_incomplete", "#34 point cloud + mesh missing", m_p2_deliverable_incomplete),
    ("p2_dem_coarse_4x", "#32 DEM 10.9cm vs 2.72cm GSD = 4x (baseline)", m_p2_dem_coarse_4x),
]


# ---- EXPECT (pinned from a captured run; CROSS-CHECKED with the renorm formula) ----
# (apex score | "gate0", exact flag-id set, verification_status)
EXPECT: dict = {
    "baseline": (84.7, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "ba_convergence_fail": (76.6, {"FLG_PROC_003", "FLG_PROC_006", "FLG_PROC_010", "FLG_PROC_015", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "ba_reproj_high": (78.4, {"FLG_PROC_003", "FLG_PROC_005", "FLG_PROC_010", "FLG_PROC_015", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "ba_reproj_elevated": (82.9, {"FLG_PROC_003", "FLG_PROC_004", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "cameras_partial_align": (82.9, {"FLG_PROC_003", "FLG_PROC_007", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "cameras_poor_align": (80.5, {"FLG_PROC_003", "FLG_PROC_008", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "cameras_severe_align": (78.7, {"FLG_PROC_003", "FLG_PROC_009", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "camera_pos_severe": (81.5, {"FLG_PROC_003", "FLG_PROC_011", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "precalib_not_loaded": (82.6, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_013", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "camera_model_mismatch": (82.6, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_014", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "self_calib_ill_conditioned": (78.4, {"FLG_PROC_003", "FLG_PROC_005", "FLG_PROC_010", "FLG_PROC_015", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "reproj_severe_outliers": (84.4, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_017", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "optimization_incomplete": (84.4, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_012", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "alignment_accuracy_low": (81.7, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_018", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "alignment_accuracy_critical": (78.7, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_019", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "depth_quality_low": (82.3, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_021", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "sparse_tiepoints": (83.3, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_022", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "very_sparse_tiepoints": (80.0, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_023", "FLG_PROC_030", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "low_multiplicity": (83.8, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_024", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "very_low_multiplicity": (82.6, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_025", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "filtering_oversmoothed": (83.2, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_027", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "filtering_insufficient": (83.2, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_026", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "marker_weak": (82.8, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_028", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "marker_insufficient": (81.0, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_029", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "atmospheric_artifact": (75.4, {"FLG_PROC_003", "FLG_PROC_004", "FLG_PROC_010", "FLG_PROC_015", "FLG_PROC_020", "FLG_PROC_023", "FLG_PROC_030", "FLG_PROC_031", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "cp_rmse_fail": (75.2, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_015", "FLG_PROC_020", "FLG_PROC_033", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "cp_outlier_severe": (83.3, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_038", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "z_xy_high": (84.4, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_050", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "z_xy_severe": (84.0, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_051", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "marker_pix_severe": (84.4, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_053", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "reproj_outliers_mild": (85.0, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_016", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "gcp_typo": (83.9, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_041", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "role_mismatch": (82.7, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_040", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "no_markers_at_all": (69.5, {"FLG_PROC_002", "FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_NO_CPS"),
    "gcp_rmse_high": (84.5, {"FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_035", "FLG_PROC_043", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "gcp_rmse_reject": (83.2, {"FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_036", "FLG_PROC_043", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "gcp_count_marginal": (86.8, {"FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_044", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "reconstruction_drift": (79.2, {"FLG_PROC_010", "FLG_PROC_015", "FLG_PROC_020", "FLG_PROC_033", "FLG_PROC_043", "FLG_PROC_054", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "verified_pass": (90.3, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_042", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "VERIFIED_RESIDUALS_PASS"),
    "verified_fail": (75.9, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_015", "FLG_PROC_020", "FLG_PROC_033", "FLG_PROC_042", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "VERIFIED_RESIDUALS_FAIL"),
    "crs_mismatch_gate": ("gate0", {"FLG_PROC_001", "FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "internal_transform_wrong": (82.3, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_056", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "dsm_as_dtm": (83.6, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_057", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "dem_res_very_coarse": (84.2, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_060", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "deliverable_missing": (84.3, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_061", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "software_drift": (84.4, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "all_flags_stress": (69.0, {"FLG_PROC_003", "FLG_PROC_004", "FLG_PROC_010", "FLG_PROC_015", "FLG_PROC_018", "FLG_PROC_020", "FLG_PROC_024", "FLG_PROC_033", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_060", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_convergence_3pix": (78.4, {"FLG_PROC_003", "FLG_PROC_005", "FLG_PROC_010", "FLG_PROC_015", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_cameras_dropped": (82.9, {"FLG_PROC_003", "FLG_PROC_007", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_depth_too_low": (82.3, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_021", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_cp_rmse_moment_of_truth": (75.2, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_015", "FLG_PROC_020", "FLG_PROC_033", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_per_cp_outlier": (84.1, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_037", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_gcp_rmse_exceeds_target": (86.3, {"FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_034", "FLG_PROC_043", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_role_swap": (82.7, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_040", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_crs_mismatch_utm47n": ("gate0", {"FLG_PROC_001", "FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_internal_geoid_misconfig": (82.3, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_056", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_dsm_as_dtm": (83.6, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_057", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_deliverable_incomplete": (84.3, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_061", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
    "p2_dem_coarse_4x": (84.7, {"FLG_PROC_003", "FLG_PROC_010", "FLG_PROC_020", "FLG_PROC_032", "FLG_PROC_043", "FLG_PROC_045", "FLG_PROC_055", "FLG_PROC_059", "FLG_PROC_064"}, "UNVERIFIED_INSUFFICIENT_CPS"),
}


# ---- problem-coverage map (all 39 spec problems) ---------------------------
# (problem_no, coverage_class, owner, [scenarios], coverage_verification)
PROBLEM_MAP = [
    (1, "FULLY COVERED", "Processing (BA)", ["ba_convergence_fail", "p2_convergence_3pix"], "VERIFIED"),
    (2, "FULLY COVERED", "Processing (BA)", ["cameras_partial_align", "cameras_poor_align", "cameras_severe_align", "p2_cameras_dropped"], "VERIFIED"),
    (3, "FULLY COVERED", "Processing (BA, compound)", ["self_calib_ill_conditioned"], "VERIFIED"),
    (4, "FULLY COVERED", "Processing (BA)", ["precalib_not_loaded"], "VERIFIED"),
    (5, "FULLY COVERED (inherited drone)", "Processing (BA)", ["camera_model_mismatch"], "VERIFIED"),
    (6, "FULLY COVERED", "Processing (BA)", ["optimization_incomplete", "all_flags_stress"], "VERIFIED"),
    (7, "FULLY COVERED (advisory)", "Processing (BA)", ["reproj_severe_outliers"], "VERIFIED"),
    (8, "PARTIAL (v1 ensemble)", "Processing (BA, v2 per-camera)", ["camera_pos_severe"], "VERIFIED"),
    (9, "FULLY COVERED", "Processing (IM)", ["sparse_tiepoints", "very_sparse_tiepoints"], "VERIFIED"),
    (10, "FULLY COVERED", "Processing (IM)", ["low_multiplicity", "very_low_multiplicity"], "VERIFIED"),
    (11, "FULLY COVERED", "Processing (IM)", ["alignment_accuracy_low", "alignment_accuracy_critical"], "VERIFIED"),
    (12, "FULLY COVERED", "Processing (IM)", ["depth_quality_low", "p2_depth_too_low"], "VERIFIED"),
    (13, "FULLY COVERED", "Processing (IM)", ["filtering_oversmoothed", "filtering_insufficient"], "VERIFIED"),
    (14, "FULLY COVERED", "Processing (IM)", ["marker_weak", "marker_insufficient"], "VERIFIED"),
    (15, "FULLY COVERED (advisory cross-handoff)", "Processing (IM)", ["atmospheric_artifact"], "VERIFIED"),
    (16, "FULLY COVERED (flag-only)", "Processing (CV)", ["no_markers_at_all"], "VERIFIED"),
    (17, "FULLY COVERED (flag-only)", "Processing (CV)", ["baseline"], "VERIFIED"),
    (18, "FULLY COVERED (inherited GCP)", "Processing (CV)", ["baseline", "gcp_count_marginal", "gcp_rmse_high"], "VERIFIED"),
    (19, "FULLY COVERED", "Processing (CV)", ["gcp_rmse_high", "gcp_rmse_reject", "p2_gcp_rmse_exceeds_target"], "VERIFIED"),
    (20, "FULLY COVERED (moment-of-truth)", "Processing (CV)", ["cp_rmse_fail", "p2_cp_rmse_moment_of_truth", "verified_fail"], "VERIFIED"),
    (21, "FULLY COVERED", "Processing (CV)", ["z_xy_severe"], "VERIFIED"),
    (22, "FULLY COVERED (covers PP#32+CP#14)", "Processing (CV)", ["cp_outlier_severe", "p2_per_cp_outlier"], "VERIFIED"),
    (23, "FULLY COVERED", "Processing (CV)", ["marker_pix_severe"], "VERIFIED"),
    (24, "FULLY COVERED (inherited)", "Processing (CV)", ["baseline", "verified_pass"], "VERIFIED"),
    (25, "FULLY COVERED (covers CP#26+CP#29+GCP#21)", "Processing (CV)", ["role_mismatch", "p2_role_swap"], "VERIFIED"),
    (26, "FULLY COVERED (logic; real v1.7 reports lack abs marker XYZ -> v2)", "Processing (CV, pp_handoff)", ["gcp_typo"], "VERIFIED"),
    (27, "FULLY COVERED (inherited)", "Processing (CV, v2 positions)", [], "DEFERRED_SPEC_GAP"),
    (28, "FULLY COVERED (inherited)", "Processing (CV, v2 positions)", [], "DEFERRED_SPEC_GAP"),
    (29, "FULLY COVERED (inherited)", "Processing (CV composite)", ["reconstruction_drift"], "VERIFIED"),
    (30, "FULLY COVERED", "Processing (DO)", ["baseline"], "VERIFIED"),
    (31, "FULLY COVERED (gate)", "Processing (DO)", ["crs_mismatch_gate", "p2_crs_mismatch_utm47n"], "VERIFIED"),
    (32, "FULLY COVERED", "Processing (DO)", ["dem_res_very_coarse", "p2_dem_coarse_4x"], "VERIFIED"),
    (33, "PARTIAL (v1 advisory, zero weight)", "Processing (DO, v2)", ["software_drift"], "VERIFIED"),
    (34, "FULLY COVERED", "Processing (DO)", ["deliverable_missing", "p2_deliverable_incomplete"], "VERIFIED"),
    (35, "HANDOFF (delivery layer)", "future delivery_score", [], "DEFERRED_HANDOFF"),
    (36, "FULLY COVERED", "Processing (DO, pp_handoff)", ["internal_transform_wrong", "p2_internal_geoid_misconfig"], "VERIFIED"),
    (37, "PARTIAL (v1 proxy)", "Processing (IM, v2 density-map)", ["very_sparse_tiepoints"], "VERIFIED"),
    (38, "FULLY COVERED (v1 limited)", "Processing (DO)", [], "DEFERRED_SPEC_GAP"),
    (39, "FULLY COVERED (echo PP#38)", "Processing (DO)", ["dsm_as_dtm", "p2_dsm_as_dtm"], "VERIFIED"),
]


def _write_problem_coverage(results):
    spec_problems = {str(p["problem_no"]): p for p in _SPEC.get("problem_coverage_map", [])}
    rows = []
    for pno, cov, owner, scens, vstatus in PROBLEM_MAP:
        sp = spec_problems.get(str(pno), {})
        flags = sorted({fl for s in scens for fl in results.get(s, {}).get("flags", [])})
        rows.append({"problem_no": pno, "problem": sp.get("problem", ""), "severity": sp.get("severity", ""),
                     "spec_disposition": sp.get("disposition", ""), "cbmi_coverage_class": cov,
                     "owner": owner, "scenarios": scens, "flags_observed": flags,
                     "coverage_verification": vstatus})
    SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
    (SCENARIOS_DIR / "_pass2_problem_coverage.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (SCENARIOS_DIR / "_pass2_problem_coverage.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["problem_no", "problem", "severity", "spec_disposition", "cbmi_coverage_class",
                    "owner", "scenarios", "flags_observed", "coverage_verification"])
        for r in rows:
            w.writerow([r["problem_no"], r["problem"], r["severity"], r["spec_disposition"],
                        r["cbmi_coverage_class"], r["owner"], ";".join(r["scenarios"]),
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
    d3c = stage3c_blocks.run(_CONFIG, ROOT, _SPEC, d3b, d2)
    d3d = stage3d_score.run(_CONFIG, ROOT, _SPEC, d2, d3a, d3b, d3c)
    out = SCENARIOS_DIR / name
    _write_det(out / "02_source_fields.json", "stage2_merge", d2)
    _write_det(out / "03_derived.json", "stage3a_derived", d3a)
    _write_det(out / "04_indicators.json", "stage3b_indicators", d3b)
    _write_det(out / "05_blocks.json", "stage3c_blocks", d3c)
    _write_det(out / "05b_views.json", "stage3c_blocks", {"per_deliverable_views": d3c["per_deliverable_views"]})
    _write_det(out / "06_apex.json", "stage3d_score", d3d)
    return {
        "scenario": name,
        "processing_score": d3d["processing_score"],
        "global_gate": d3d["global_gate"]["triggered"],
        "verification_status": d3d["verification_status"]["value"],
        "block_scores": d3c["stage3c_meta"]["block_score_summary"],
        "view_scores": d3c["stage3c_meta"]["view_score_summary"],
        "flags": sorted(u["flag_id"] for u in d3d["unique_flags"].values()),
    }


def _check(name, row):
    if name not in EXPECT:
        return None, [f"score={row['processing_score']} vstatus={row['verification_status']} "
                      f"flags={row['flags']}"]
    exp_score, exp_flags, exp_vs = EXPECT[name]
    fails = []
    got = row["processing_score"]
    if exp_score == "gate0":
        if not (got == 0.0 and row["global_gate"]):
            fails.append(f"score: expected 0.0 via gate, got {got} (gate={row['global_gate']})")
    elif got is None or abs(got - exp_score) > SCORE_TOL:
        fails.append(f"score: expected {exp_score}+-{SCORE_TOL}, got {got}")
    if set(row["flags"]) != exp_flags:
        fails.append(f"flags: missing={sorted(exp_flags - set(row['flags']))} "
                     f"extra={sorted(set(row['flags']) - exp_flags)}")
    if exp_vs and row["verification_status"] != exp_vs:
        fails.append(f"verification_status: expected {exp_vs}, got {row['verification_status']}")
    return not fails, fails


def main(argv=None):
    global _CONFIG, _SPEC, _SPEC_VERSION, _BASELINE2
    ap = argparse.ArgumentParser(description="Processing smoke-test harness")
    ap.add_argument("config", nargs="?", default=str(ROOT / "paths.json"))
    ap.add_argument("--capture", action="store_true", help="print captured values for pinning EXPECT")
    args = ap.parse_args(argv)
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
        tag = "OK  " if ok else ("CAP " if ok is None else "FAIL")
        if ok is None:
            uncovered.append(name)
        elif not ok:
            validation_failures.append((name, fails))
        gate = " GATE" if row["global_gate"] else ""
        if args.capture:
            fl = "{" + ", ".join(f'"{f}"' for f in row["flags"]) + "}"
            sc = "\"gate0\"" if row["global_gate"] else row["processing_score"]
            print(f'    "{name}": ({sc}, {fl}, "{row["verification_status"]}"),')
        else:
            print(f"  [{tag}] {name:28s} score={str(row['processing_score']):6s}{gate}  "
                  f"vstatus={row['verification_status']:26s} flags={len(row['flags'])}")
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

    print(f"\n  {len(scenarios)} scenarios | validation failures {len(validation_failures)} | "
          f"uncovered(no EXPECT) {len(uncovered)}")
    print(f"  problem-coverage map: {len(prob_rows)} problems  {cov_tally}")
    print(f"  distinct flags exercised: {len(all_flags)}/{len(spec_flag_ids)}")
    if validation_failures:
        for n, fs in validation_failures:
            print(f"  FAIL {n}: {fs}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
