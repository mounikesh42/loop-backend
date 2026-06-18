#!/usr/bin/env python3
"""Stage 3a - derived fields for Processing (37 L2D_PROC).

Reads outputs/02_source_fields.json (90 L1F) and computes the 37 derived fields
the indicators consume. Two tiers (topologically ordered):
  Tier 1 (33): depend only on source fields.
  Tier 2 (4) : depend on Tier-1 derived values
               - localized_reconstruction_v1_proxy  <- tiepoint_density_per_km2
               - atmospheric_artifact_composite      <- tiepoint_density_per_km2
               - reconstruction_drift_composite      <- cp/gcp_rmse_relative_to_target

The derived layer PREPARES inputs (ratios / classes / booleans / composites);
the SCORE+FLAG banding happens at Stage 3b. A derived value of null means N/A:
the consuming indicator drops out and its block weight is redistributed at 3c
(e.g. the no-GCP path nulls gcp_rmse / gcp_distribution / gcp_vertical / gcp_coord).

Handoff flags (spec raised_at_stage=handoff) fire here (template maps handoff
-> Stage 3a):
  - FLG_PROC_064 PROC_PER_DELIVERABLE_FITNESS  : always-fire informational handoff
  - FLG_PROC_063 PROC_TARGET_DETECTION_FAILURE : fires when a per-CP residual is a
                                                 severe outlier (PP#32 via CV3)

CRS strings are normalised with pyproj ('WGS 84 (EPSG::4326)' vs 'EPSG:4326' ->
4326; geographic-vs-projected via pyproj.CRS.is_projected).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import pyproj

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import stage1_inventory  # noqa: E402
import stage2_merge  # noqa: E402

STAGE = "stage3a_derived"

# ---- engineering tuneables (surfaced; spec-formula constants tagged) --------
TUNEABLES = {
    # from spec formulas (not engineering picks)
    "OPTIMIZATION_FULL_PARAMS": ["f", "b1", "b2", "cx", "cy", "k1", "k2", "k3", "p1", "p2"],
    "SELF_CALIB_K_CORR_THRESHOLD": 0.95,        # spec L2D_007
    "SELF_CALIB_REPROJ_PIX": 2.0,               # spec L2D_007
    "SELF_CALIB_RMSE_TARGET_MULT": 2.0,         # spec L2D_007
    "LOCALIZED_RECON_DENSITY_PER_KM2": 1_000_000,   # spec L2D_015
    "ATMOSPHERIC_REPROJ_PIX": 2.0,              # spec L2D_016
    "ATMOSPHERIC_DENSITY_PER_KM2": 1_000_000,   # spec L2D_016
    "ATMOSPHERIC_CAMERA_XY_CM": 15.0,           # spec L2D_016
    "RECON_DRIFT_GCP_MIN": 3,                   # spec L2D_026
    "RECON_DRIFT_GCP_RMSE_REL_MAX": 1.0,        # spec L2D_026
    "RECON_DRIFT_CP_RMSE_REL_MIN": 2.0,         # spec L2D_026
    # engineering picks (Section 7i rubric)
    "PER_CP_OUTLIER_SEVERE_RATIO": 3.0,         # handoff FLG_PROC_063 trip (CV3 severe)
    "GCP_COORD_NOISE": {"lon": 1e-4, "lat": 1e-4, "elev": 0.5},  # CV5 typo tolerance (~11 m / 0.5 m)
    "ALIGNMENT_CLASS_MAP": {"highest": "High", "high": "High", "medium": "Medium",
                            "low": "Low", "lowest": "Low"},
    "DEPTH_CLASS_MAP": {"ultra high": "High", "high": "High", "medium": "Medium",
                        "low": "Low", "lowest": "Low"},
    # filtering strength rank vs ideal-per-site (appropriateness lookup, L2D_013)
    "FILTERING_STRENGTH_RANK": {"mild": 1, "moderate": 2, "aggressive": 3, "ultra": 3},
    "SITE_IDEAL_FILTERING_RANK": {"flat-structured": 1, "mining-pit": 2, "mixed": 2,
                                  "vegetated": 3},
}


# ---- helpers ----------------------------------------------------------------
def _r4(x):
    return round(x, 4) if isinstance(x, (int, float)) and not isinstance(x, bool) else x


def _div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def _epsg(crs_str):
    """EPSG integer from a CRS string ('WGS 84 (EPSG::4326)', 'EPSG:4326', '4326')."""
    if not crs_str:
        return None
    m = re.search(r"EPSG\s*:+\s*(\d+)", str(crs_str), re.I)
    if m:
        return int(m.group(1))
    m2 = re.fullmatch(r"\s*(\d{4,6})\s*", str(crs_str))
    if m2:
        return int(m2.group(1))
    try:
        return pyproj.CRS.from_user_input(crs_str).to_epsg()
    except Exception:
        return None


def _is_projected(crs_str):
    code = _epsg(crs_str)
    if code is None:
        return None
    try:
        return bool(pyproj.CRS.from_epsg(code).is_projected)
    except Exception:
        return None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


# ---- the 37 derivations -----------------------------------------------------
def compute(sf: dict, notes: dict) -> dict:
    d: dict[str, Any] = {}

    # ---- Tier 1 ----
    d["ba_reprojection_relative"] = _r4(sf.get("reportSurveyData_reprojection_error_pix"))
    d["camera_alignment_fraction"] = _r4(_div(sf.get("reportSurveyData_camera_stations"),
                                              sf.get("reportSurveyData_n_images")))
    d["camera_position_relative_to_gsd"] = _r4(_div(sf.get("reportCameraLocations_total_err_cm"),
                                                    sf.get("reportSurveyData_ground_resolution_cm")))

    # optimization params completeness
    raw = (sf.get("reportParams_optimization_parameters") or "").lower()
    expanded = set()
    if "k1-k3" in raw:
        expanded |= {"k1", "k2", "k3"}
    for p in ["f", "b1", "b2", "cx", "cy", "k1", "k2", "k3", "p1", "p2"]:
        if re.search(rf"\b{re.escape(p)}\b", raw):
            expanded.add(p)
    full = set(TUNEABLES["OPTIMIZATION_FULL_PARAMS"])
    missing = sorted(full - expanded)
    d["optimization_params_completeness"] = {
        "present_count": len(full & expanded), "missing": missing,
        "all_present": not missing, "b1b2_present": {"b1", "b2"} <= expanded}

    # precalibration match: expected(bool) vs report precalibrated(Yes/No)
    exp = sf.get("manifest_precalibration_expected")
    rep_pc = (sf.get("reportCameras_precalibrated") or "").strip().lower()
    rep_loaded = {"yes": True, "no": False}.get(rep_pc)
    # asymmetric: the only problem is "expected loaded but report shows not loaded"
    # (L3I_PROC_005). Not-expecting precalibration is never penalised.
    d["precalibration_loaded_match"] = (None if exp is None or rep_loaded is None
                                        else not (exp is True and rep_loaded is False))

    d["camera_model_match"] = (None if not sf.get("manifest_declared_camera_model")
                               or not sf.get("reportCameras_camera_model")
                               else sf.get("manifest_declared_camera_model").strip()
                               == sf.get("reportCameras_camera_model").strip())

    # self-calibration compound condition
    k12 = sf.get("reportCalibration_k1_k2_correlation")
    reproj = sf.get("reportSurveyData_reprojection_error_pix")
    cp_rmse = sf.get("reportGCP_check_rmse_total_cm")
    tgt = sf.get("manifest_accuracy_target_m")
    cond = (rep_pc == "no" and k12 is not None and abs(k12) > TUNEABLES["SELF_CALIB_K_CORR_THRESHOLD"]
            and ((reproj is not None and reproj > TUNEABLES["SELF_CALIB_REPROJ_PIX"])
                 or (cp_rmse is not None and tgt is not None
                     and cp_rmse > TUNEABLES["SELF_CALIB_RMSE_TARGET_MULT"] * tgt * 100)))
    d["self_calibration_compound_condition"] = bool(cond)

    d["max_reproj_to_rms_ratio"] = _r4(_div(sf.get("reportParams_max_reprojection_error_pix"),
                                            sf.get("reportParams_rms_reprojection_error_pix")))
    d["alignment_accuracy_setting_class"] = TUNEABLES["ALIGNMENT_CLASS_MAP"].get(
        (sf.get("reportParams_alignment_accuracy") or "").strip().lower())
    d["depth_quality_setting_class"] = TUNEABLES["DEPTH_CLASS_MAP"].get(
        (sf.get("reportParams_depth_quality") or "").strip().lower())
    d["tiepoint_density_per_km2"] = _r4(_div(sf.get("reportSurveyData_tie_points"),
                                             sf.get("reportSurveyData_coverage_area_km2")))
    d["tiepoint_multiplicity_value"] = _r4(sf.get("reportParams_avg_tie_point_multiplicity"))

    # filtering mode vs site cover appropriateness
    fmode = (sf.get("reportParams_depth_filtering_mode") or "").strip().lower()
    site = (sf.get("manifest_declared_site_cover") or "").strip().lower()
    fr = TUNEABLES["FILTERING_STRENGTH_RANK"].get(fmode)
    ir = TUNEABLES["SITE_IDEAL_FILTERING_RANK"].get(site)
    if fr is None or ir is None:
        d["filtering_mode_site_match"] = None
    elif fr == ir:
        d["filtering_mode_site_match"] = "appropriate"
    elif fr < ir:
        d["filtering_mode_site_match"] = "insufficient"
    else:
        d["filtering_mode_site_match"] = "oversmoothed"

    imc = sf.get("reportGCP_per_marker_image_count") or {}
    d["marker_image_count_per_marker"] = {"per_marker": imc,
                                          "min": min(imc.values()) if imc else None}

    d["tiepoint_density_per_km2"] = d["tiepoint_density_per_km2"]  # (already set; T2 reads it)

    d["cp_rmse_relative_to_target"] = _r4(_div(sf.get("reportGCP_check_rmse_total_cm"),
                                               (tgt * 100) if tgt else None))
    d["gcp_rmse_relative_to_target"] = _r4(_div(sf.get("reportGCP_control_rmse_total_cm"),
                                                (tgt * 100) if tgt else None))

    # per-CP outlier ratio (CPs only)
    resid = sf.get("reportGCP_per_marker_residuals") or {}
    roles = sf.get("reportGCP_marker_roles") or {}
    cp_resid = {k: v for k, v in resid.items() if roles.get(k) == "check" and v is not None}
    if not cp_resid:  # fall back to all markers if roles absent
        cp_resid = {k: v for k, v in resid.items() if v is not None}
    m = _mean(list(cp_resid.values()))
    ratios = {k: _r4(v / m) for k, v in cp_resid.items()} if m else {}
    d["per_cp_outlier_ratio"] = {"ratios": ratios,
                                 "max_ratio": max(ratios.values()) if ratios else None}

    # marker role match (per-marker report vs manifest)
    decl_roles = sf.get("manifest_marker_roles_declared") or {}
    if roles and decl_roles:
        per = {k: (roles.get(k) == decl_roles.get(k)) for k in set(roles) | set(decl_roles)}
        d["marker_role_match"] = {"per_marker": per, "all_match": all(per.values())}
    else:
        d["marker_role_match"] = None

    # gcp coord match: per-GCP within-noise comparison of report marker XYZ vs the
    # pre-processing coord file. Real v1.7 reports emit NO absolute marker XYZ, so
    # rep_locs is None -> N/A in practice (the comparison runs only when both are
    # present, e.g. a synthetic typo scenario or a future report version).
    rep_locs = sf.get("reportGCP_marker_locations")
    pp_pos = sf.get("pp_gcp_coord_file_gcp_positions")
    if not rep_locs or not pp_pos:
        d["gcp_coord_match"] = None
        notes["gcp_coord_match"] = ("N/A: report emits no absolute marker XYZ (v1.7) and/or no "
                                    "pp positions; CV5 degrades to N/A.")
    else:
        tol = TUNEABLES["GCP_COORD_NOISE"]
        mism = []
        for mid in set(rep_locs) & set(pp_pos):
            r, p = rep_locs[mid] or {}, pp_pos[mid] or {}
            for axis in ("lon", "lat", "elev"):
                rv, pv = r.get(axis), p.get(axis)
                if rv is not None and pv is not None and abs(rv - pv) > tol[axis]:
                    mism.append(mid)
                    break
        d["gcp_coord_match"] = (len(mism) == 0)
        if mism:
            notes["gcp_coord_match"] = f"GCP coord mismatch beyond noise for markers {sorted(mism)} (typo)."

    # gcp distribution / vertical coverage: control points only -> N/A when no GCPs
    n_gcp = sf.get("reportGCP_control_points_count")
    if not n_gcp:
        d["gcp_bundle_distribution_coverage"] = None
        d["gcp_vertical_coverage_ratio"] = None
        notes["gcp_bundle_distribution_coverage"] = "N/A: 0 control points (PPK/no-GCP)."
        notes["gcp_vertical_coverage_ratio"] = "N/A: 0 control points (PPK/no-GCP)."
    else:
        d["gcp_bundle_distribution_coverage"] = None  # needs absolute GCP XYZ (v2)
        d["gcp_vertical_coverage_ratio"] = None

    d["z_xy_residual_ratio"] = _r4(_div(sf.get("reportGCP_check_rmse_z_cm"),
                                        sf.get("reportGCP_check_rmse_xy_cm")))

    pix = sf.get("reportGCP_per_marker_image_pix") or {}
    mp = _mean(list(pix.values()))
    pratios = {k: _r4(v / mp) for k, v in pix.items() if v is not None} if mp else {}
    d["per_marker_pix_outlier_ratio"] = {"ratios": pratios,
                                         "max_ratio": max(pratios.values()) if pratios else None}

    d["markers_total_zero"] = (sf.get("reportGCP_total_markers_count") == 0)
    d["gcps_used_zero"] = (n_gcp == 0)

    # CRS comparisons
    rep_crs = sf.get("reportParams_coordinate_system")
    man_crs = sf.get("manifest_project_required_crs")
    rc, mc = _epsg(rep_crs), _epsg(man_crs)
    d["output_crs_project_match"] = (None if rc is None or mc is None else rc == mc)
    d["output_crs_is_projected"] = _is_projected(rep_crs)

    # internal transform: report has no datum/geoid transform; capture frame == output -> match
    rep_tf = sf.get("reportParams_datum_geoid_transform")
    pp_crs = sf.get("pp_manifest_capture_crs")
    pp_geoid = sf.get("pp_manifest_capture_geoid")
    if pp_crs is None and pp_geoid is None:
        d["internal_transform_match"] = None
        notes["internal_transform_match"] = "N/A: no pp capture CRS/geoid (DO3 degrades)."
    else:
        # no report transform AND capture frame agrees with output AND no geoid -> consistent
        capture_ok = (_epsg(pp_crs) == rc) if (pp_crs and rc) else True
        geoid_none = (pp_geoid or "none").strip().lower() in ("none", "ellipsoidal", "")
        d["internal_transform_match"] = bool((rep_tf is None) and capture_ok and geoid_none)

    # dtm classification consistency (flag when DTM claimed but no ground classification)
    dtm_claimed = sf.get("manifest_dtm_deliverable_claimed")
    gclass = sf.get("reportDEM_ground_classification_ran")
    d["dtm_classification_consistency"] = not (dtm_claimed is True and gclass is False)

    # dem void interpolation fraction (void stats absent in v1.7 -> fraction N/A)
    d["dem_void_interpolation_fraction"] = {
        "void_fraction": None,
        "interpolation_enabled": (sf.get("reportDEM_interpolation_enabled") or "").strip().lower()
        in ("enabled", "yes", "true")}
    d["dem_to_gsd_ratio"] = _r4(_div(sf.get("reportDEM_resolution_cm"),
                                     sf.get("reportSurveyData_ground_resolution_cm")))

    # deliverable completeness
    declared = sf.get("manifest_declared_deliverables") or []
    present = {t: bool(sf.get(f"deliverable_{t}_present")) for t in
               ("ortho", "dsm", "dtm", "point_cloud", "mesh_3d")}
    miss = [t for t in declared if not present.get(t)]
    d["deliverable_completeness"] = {"declared": declared, "present": present,
                                     "missing": miss, "complete": not miss}

    # software version class (v1: matches baseline?)
    def _ver(s):
        m = re.search(r"(\d+\.\d+\.\d+)", str(s or ""))
        return m.group(1) if m else (str(s).strip() if s else None)
    rv = _ver(sf.get("reportSystem_software_version"))
    bv = _ver(sf.get("manifest_software_version_baseline"))
    d["software_version_class"] = {"report_version": rv, "baseline": bv,
                                   "match": (rv == bv) if (rv and bv) else None}

    d["cp_count_value"] = sf.get("reportGCP_check_points_count")

    # ---- Tier 2 ----
    dens = d["tiepoint_density_per_km2"]
    voids = sf.get("reportDEM_void_statistics")
    d["localized_reconstruction_v1_proxy"] = bool(
        (dens is not None and dens < TUNEABLES["LOCALIZED_RECON_DENSITY_PER_KM2"])
        or bool(voids))

    cam_xy = sf.get("reportCameraLocations_xy_err_cm")
    d["atmospheric_artifact_composite"] = bool(
        reproj is not None and reproj > TUNEABLES["ATMOSPHERIC_REPROJ_PIX"]
        and dens is not None and dens < TUNEABLES["ATMOSPHERIC_DENSITY_PER_KM2"]
        and cam_xy is not None and cam_xy > TUNEABLES["ATMOSPHERIC_CAMERA_XY_CM"])

    gcp_rel = d["gcp_rmse_relative_to_target"]
    cp_rel = d["cp_rmse_relative_to_target"]
    d["reconstruction_drift_composite"] = bool(
        (n_gcp or 0) >= TUNEABLES["RECON_DRIFT_GCP_MIN"]
        and gcp_rel is not None and gcp_rel <= TUNEABLES["RECON_DRIFT_GCP_RMSE_REL_MAX"]
        and cp_rel is not None and cp_rel > TUNEABLES["RECON_DRIFT_CP_RMSE_REL_MIN"])

    return d


def _handoff_flags(sf: dict, d: dict) -> list[dict]:
    """The 2 spec handoff flags (raised_at_stage=handoff -> Stage 3a)."""
    flags = []
    # FLG_PROC_064: always-fire informational handoff to the delivery layer
    flags.append({
        "flag_id": "FLG_PROC_064", "flag_name": "PROC_PER_DELIVERABLE_FITNESS",
        "severity": "INFORMATIONAL", "raised_at_stage": "handoff",
        "_origin_stage": "stage3a_handoff",
        "detail": "Per-deliverable fitness for customer use case - future delivery-layer concern (always emitted)."})
    # FLG_PROC_063: target detection failure via a severe per-CP outlier (PP#32 / CV3)
    max_ratio = (d.get("per_cp_outlier_ratio") or {}).get("max_ratio")
    if max_ratio is not None and max_ratio >= TUNEABLES["PER_CP_OUTLIER_SEVERE_RATIO"]:
        flags.append({
            "flag_id": "FLG_PROC_063", "flag_name": "PROC_TARGET_DETECTION_FAILURE",
            "severity": "HIGH", "raised_at_stage": "handoff", "_origin_stage": "stage3a_handoff",
            "detail": f"Severe per-CP residual outlier (max ratio {max_ratio} >= "
                      f"{TUNEABLES['PER_CP_OUTLIER_SEVERE_RATIO']}); possible washed/displaced target (PP#32)."})
    return flags


def run(config, project_root, spec, stage2_data) -> dict:
    sf = stage2_data["source_fields"]
    notes: dict[str, str] = {}
    d = compute(sf, notes)

    spec_names = [x["derived_name"] for x in spec["derived_fields"]]
    id_by_name = {x["derived_name"]: x["derived_id"] for x in spec["derived_fields"]}
    missing = sorted(set(spec_names) - set(d))
    extra = sorted(set(d) - set(spec_names))

    traces = {}
    for x in spec["derived_fields"]:
        nm = x["derived_name"]
        traces[nm] = {
            "derived_id": x["derived_id"], "derived_name": nm,
            "value": d.get(nm), "is_na": d.get(nm) is None,
            "input_field_names": x.get("input_field_names"),
            "note": notes.get(nm),
        }
    handoff = _handoff_flags(sf, d)
    na_fields = sorted(k for k, v in d.items() if v is None)

    return {
        "survey_level": True,
        "derived": dict(sorted(d.items())),
        "derived_traces": dict(sorted(traces.items())),
        "flags_raised_stage3a": handoff,
        "stage3a_meta": {
            "derived_count": len(d),
            "expected_count": len(spec_names),
            "missing_derived": missing,
            "extra_derived": extra,
            "na_derived_fields": na_fields,
            "na_count": len(na_fields),
            "handoff_flag_count": len(handoff),
            "tuneables": TUNEABLES,
            "id_by_name": id_by_name,
        },
    }


def print_summary(data):
    mm = data["stage3a_meta"]
    print(f"  derived: {mm['derived_count']}/{mm['expected_count']}  "
          f"N/A: {mm['na_count']} {mm['na_derived_fields']}  "
          f"missing={mm['missing_derived']} extra={mm['extra_derived']}")
    print(f"  handoff flags: {[f['flag_name'] for f in data['flags_raised_stage3a']]}")
    key = ["ba_reprojection_relative", "camera_alignment_fraction", "camera_position_relative_to_gsd",
           "cp_rmse_relative_to_target", "tiepoint_density_per_km2", "output_crs_project_match",
           "output_crs_is_projected", "gcps_used_zero", "z_xy_residual_ratio", "dem_to_gsd_ratio"]
    for k in key:
        print(f"    {k:34s} = {data['derived'][k]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Processing Stage 3a derived fields")
    ap.add_argument("config")
    args = ap.parse_args(argv)
    config_path = Path(args.config).resolve()
    config = common.load_config(config_path)
    root = config_path.parent
    spec = common.load_spec(root, config)
    spec_version = spec["_meta"]["version"]

    env1, hard = stage1_inventory.run(config, root)
    if hard and config.get("options", {}).get("fail_fast", True):
        print("HALT: Stage 1 hard failure.")
        return 1
    data2 = stage2_merge.run(config, root, spec, env1["data"])
    data = run(config, root, spec, data2)

    out_path = root / config["outputs"]["stage3_derived"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    print(f"Stage 3a derived -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
