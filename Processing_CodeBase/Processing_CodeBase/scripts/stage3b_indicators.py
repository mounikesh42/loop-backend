#!/usr/bin/env python3
"""Stage 3b - indicators for Processing (38 L3I_PROC), Option B.

One eval function per indicator. Each reads its input_derived_field(s) (a derived
value, or for L3I_PROC_023 a source field directly), bands it per the spec
threshold_summary (first match wins), and emits a trace block with score +
band + flags. Scores/flag-names come from the spec at runtime.

N/A redistribution (na_redistribute=True -> dropped + weight renormalised at 3c):
  - input value is None (spec-defined path N/A, e.g. no-GCP nulls gcp_rmse /
    gcp_distribution / gcp_vertical / gcp_coord), OR
  - the indicator's evidence tier is unmet (report_and_manifest with no manifest;
    report_and_pp_handoff with no pp handoff; manifest_and_deliverables).

The report is always present here (Stage 1 hard-fails otherwise), so
report_required indicators never N/A on evidence.

One gate: L3I_PROC_031 (output_crs_project_match=False) -> score 0 + the
CATASTROPHIC PROC_OUTPUT_CRS_MISMATCH; the block-zero + apex force-to-0 happen at
3c/3d. Two flag-only signals: L3I_PROC_029 (no markers, CATASTROPHIC) and
L3I_PROC_030 (no GCPs, CRITICAL, weight 0) - flags fire, score still computes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import stage1_inventory  # noqa: E402
import stage2_merge  # noqa: E402
import stage3a_derived  # noqa: E402

STAGE = "stage3b_indicators"

TUNEABLES = {
    "OUTLIER_AGG_PENALTY_FACTOR": 0.25,   # aggregate = mean - 0.25*(100-min) (spec L3I_014/019/027)
    "SOFTWARE_UNKNOWN_SCORE": 60,         # version not matching baseline & not in buggy list (spec L3I_038)
}


# ---- helpers ----------------------------------------------------------------
def _na(reason):
    return {"score": None, "band": "N/A", "flags": [], "na": True, "na_reason": reason, "gate": False}


def _ok(score, band, flags=(), gate=False):
    return {"score": score, "band": band, "flags": list(flags), "na": False,
            "na_reason": None, "gate": gate}


def _agg_outlier(elem_scores, factor):
    if not elem_scores:
        return None
    return round(sum(elem_scores) / len(elem_scores) - factor * (100 - min(elem_scores)), 1)


# ---- the 38 eval functions (keyed by indicator_id) --------------------------
def i001(c):  # ba_reprojection
    v = c.get("ba_reprojection_relative")
    if v is None:
        return _na("reprojection error absent")
    if v <= 1.5:
        return _ok(100, "<=1.5")
    if v <= 2.5:
        return _ok(70, "1.5-2.5", ["PROC_BA_REPROJ_ELEVATED"])
    if v <= 3.5:
        return _ok(30, "2.5-3.5", ["PROC_BA_REPROJ_HIGH"])
    return _ok(0, ">3.5", ["PROC_BA_CONVERGENCE_FAIL"])


def i002(c):  # camera_alignment
    v = c.get("camera_alignment_fraction")
    if v is None:
        return _na("alignment fraction absent")
    if v >= 0.98:
        return _ok(100, ">=0.98")
    if v >= 0.95:
        return _ok(70, "0.95-0.98", ["PROC_CAMERAS_PARTIAL_ALIGN"])
    if v >= 0.90:
        return _ok(30, "0.90-0.95", ["PROC_CAMERAS_POOR_ALIGN"])
    return _ok(0, "<0.90", ["PROC_CAMERAS_SEVERE_ALIGN_FAIL"])


def i003(c):  # camera_position_ensemble
    v = c.get("camera_position_relative_to_gsd")
    if v is None:
        return _na("camera position/GSD absent")
    if v <= 2:
        return _ok(100, "<=2x")
    if v <= 4:
        return _ok(70, "2-4x", ["PROC_CAMERA_POS_ELEVATED"])
    if v <= 8:
        return _ok(30, "4-8x")
    return _ok(0, ">8x", ["PROC_CAMERA_POS_SEVERE"])


def i004(c):  # optimization_completeness
    v = c.get("optimization_params_completeness")
    if not v:
        return _na("optimization params absent")
    if v["all_present"]:
        return _ok(100, "all 10 present")
    if set(v["missing"]) <= {"b1", "b2"}:
        return _ok(90, "b1/b2 missing", ["PROC_OPTIMIZATION_INCOMPLETE"])
    if len(v["missing"]) <= 4:
        return _ok(70, "more missing", ["PROC_OPTIMIZATION_INCOMPLETE"])
    return _ok(30, "severely incomplete", ["PROC_OPTIMIZATION_INCOMPLETE"])


def i005(c):  # precalibration_loaded
    v = c.get("precalibration_loaded_match")
    if v is None:
        return _na("precalibration match N/A")
    return _ok(100, "match") if v else _ok(30, "mismatch", ["PROC_PRECALIB_NOT_LOADED"])


def i006(c):  # camera_model_consistency
    v = c.get("camera_model_match")
    if v is None:
        return _na("camera model match N/A")
    return _ok(100, "match") if v else _ok(30, "mismatch", ["PROC_CAMERA_MODEL_MISMATCH"])


def i007(c):  # self_calibration_quality (needs |K| + precalibrated + compound)
    precal = (c.get("reportCameras_precalibrated") or "").strip().lower()
    if precal == "yes":
        return _ok(100, "precalibrated")
    k = c.get("reportCalibration_k1_k2_correlation")
    if k is None:
        return _na("K1-K2 correlation absent")
    ak = abs(k)
    if ak < 0.90:
        return _ok(100, "|K|<0.90")
    if ak <= 0.95:
        return _ok(70, "|K| 0.90-0.95")
    # |K|>0.95: penalise only if also degraded (compound condition)
    if c.get("self_calibration_compound_condition"):
        return _ok(30, "|K|>0.95 + degraded", ["PROC_SELF_CALIB_ILL_CONDITIONED"])
    return _ok(100, "|K|>0.95 but not degraded")


def i008(c):  # max_reproj_outlier (advisory)
    v = c.get("max_reproj_to_rms_ratio")
    if v is None:
        return _na("max/rms ratio absent")
    if v <= 10:
        return _ok(100, "<=10")
    if v <= 20:
        return _ok(70, "10-20", ["PROC_REPROJ_OUTLIERS"])
    if v <= 50:
        return _ok(50, "20-50")
    return _ok(30, ">50", ["PROC_REPROJ_SEVERE_OUTLIERS"])


def i009(c):  # alignment_accuracy_setting
    v = c.get("alignment_accuracy_setting_class")
    if v is None:
        return _na("alignment accuracy class absent")
    if v == "High":
        return _ok(100, "Highest/High")
    if v == "Medium":
        return _ok(50, "Medium", ["PROC_ALIGNMENT_ACCURACY_LOW"])
    return _ok(0, "Low/Lowest", ["PROC_ALIGNMENT_ACCURACY_CRITICAL"])


def i010(c):  # depth_quality_setting
    v = c.get("depth_quality_setting_class")
    if v is None:
        return _na("depth quality class absent")
    if v == "High":
        return _ok(100, "High/Ultra")
    if v == "Medium":
        return _ok(60, "Medium", ["PROC_DEPTH_QUALITY_MEDIUM"])
    return _ok(20, "Low/Lowest", ["PROC_DEPTH_QUALITY_LOW"])


def i011(c):  # tiepoint_density
    v = c.get("tiepoint_density_per_km2")
    if v is None:
        return _na("tie point density absent")
    if v >= 3_000_000:
        return _ok(100, ">=3M")
    if v >= 1_000_000:
        return _ok(70, "1-3M", ["PROC_SPARSE_TIEPOINTS"])
    if v >= 500_000:
        return _ok(30, "0.5-1M", ["PROC_VERY_SPARSE_TIEPOINTS"])
    return _ok(0, "<0.5M")


def i012(c):  # tiepoint_multiplicity
    v = c.get("tiepoint_multiplicity_value")
    if v is None:
        return _na("multiplicity absent")
    if v >= 4:
        return _ok(100, ">=4")
    if v >= 3:
        return _ok(70, "3-4", ["PROC_LOW_MULTIPLICITY"])
    if v >= 2.5:
        return _ok(30, "2.5-3", ["PROC_VERY_LOW_MULTIPLICITY"])
    return _ok(0, "<2.5")


def i013(c):  # filtering_mode_appropriateness
    v = c.get("filtering_mode_site_match")
    if v is None:
        return _na("filtering/site match N/A")
    if v == "appropriate":
        return _ok(100, "appropriate")
    if v == "insufficient":
        return _ok(50, "insufficient", ["PROC_FILTERING_INSUFFICIENT"])
    return _ok(50, "oversmoothed", ["PROC_FILTERING_OVERSMOOTHED"])


def i014(c):  # marker_image_coverage (aggregated)
    v = c.get("marker_image_count_per_marker")
    pm = (v or {}).get("per_marker") or {}
    if not pm:
        return _na("per-marker image counts absent")
    scores, flags = [], set()
    for cnt in pm.values():
        if cnt >= 5:
            scores.append(100)
        elif cnt >= 3:
            scores.append(50); flags.add("PROC_MARKER_WEAK")
        else:
            scores.append(0); flags.add("PROC_MARKER_INSUFFICIENT_IMAGES")
    return _ok(_agg_outlier(scores, c.tun["OUTLIER_AGG_PENALTY_FACTOR"]), "per-marker agg", sorted(flags))


def i015(c):  # localized_reconstruction (v1 proxy, advisory)
    v = c.get("localized_reconstruction_v1_proxy")
    if v is None:
        return _na("proxy N/A")
    return _ok(50, "proxy-triggered", ["PROC_LOCALIZED_RECON_COLLAPSE"]) if v else _ok(100, "ok")


def i016(c):  # atmospheric_artifact (advisory)
    v = c.get("atmospheric_artifact_composite")
    if v is None:
        return _na("composite N/A")
    return _ok(50, "suspected", ["PROC_ATMOSPHERIC_ARTIFACT_SUSPECTED"]) if v else _ok(100, "ok")


def i017(c):  # cp_rmse (moment of truth)
    v = c.get("cp_rmse_relative_to_target")
    if v is None:
        return _na("no CPs / no accuracy target")
    if v <= 1:
        return _ok(100, "<=1x")
    if v <= 2:
        return _ok(60, "1-2x", ["PROC_CP_RMSE_MARGINAL"])
    return _ok(0, ">2x", ["PROC_CP_RMSE_FAIL"])


def i018(c):  # gcp_rmse
    v = c.get("gcp_rmse_relative_to_target")
    if v is None:
        return _na("no GCPs / no accuracy target")
    if v <= 1:
        return _ok(100, "<=1x")
    if v <= 2:
        return _ok(70, "1-2x", ["PROC_GCP_RMSE_MARGINAL"])
    if v <= 5:
        return _ok(30, "2-5x", ["PROC_GCP_RMSE_HIGH"])
    return _ok(0, ">5x", ["PROC_GCP_RMSE_REJECT"])


def i019(c):  # per_cp_outlier (aggregated)
    v = c.get("per_cp_outlier_ratio")
    ratios = (v or {}).get("ratios") or {}
    if not ratios:
        return _na("no per-CP residuals")
    scores, flags = [], set()
    for r in ratios.values():
        if r <= 1.5:
            scores.append(100)
        elif r <= 2:
            scores.append(70); flags.add("PROC_CP_OUTLIER_MILD")
        elif r <= 3:
            scores.append(30); flags.add("PROC_CP_OUTLIER_SEVERE")
        else:
            scores.append(0); flags.add("PROC_CP_OUTLIER_REJECT")
    return _ok(_agg_outlier(scores, c.tun["OUTLIER_AGG_PENALTY_FACTOR"]), "per-CP agg", sorted(flags))


def i020(c):  # marker_role_consistency
    v = c.get("marker_role_match")
    if v is None:
        return _na("roles N/A (report or manifest roles absent)")
    return _ok(100, "all match") if v["all_match"] else _ok(30, "mismatch", ["PROC_ROLE_MISMATCH"])


def i021(c):  # gcp_coord_consistency
    v = c.get("gcp_coord_match")
    if v is None:
        return _na("no GCP abs coords (report) or no pp positions")
    return _ok(100, "match within noise") if v else _ok(30, "typo", ["PROC_GCP_TYPO"])


def i022(c):  # cp_count_statistical
    v = c.get("cp_count_value")
    if v is None:
        return _na("CP count absent")
    if v >= 20:
        return _ok(100, ">=20")
    if v >= 10:
        return _ok(80, "10-19")
    if v >= 5:
        return _ok(60, "5-9", ["PROC_CP_COUNT_STATISTICAL_WEAK"])
    if v >= 1:
        return _ok(30, "1-4", ["PROC_CP_COUNT_INSUFFICIENT"])
    return _ok(0, "0 CPs")


def i023(c):  # gcp_count_in_bundle (reads a SOURCE field)
    v = c.get("reportGCP_control_points_count")
    if v is None:
        return _na("GCP count absent")
    if v >= 5:
        return _ok(100, ">=5")
    if v >= 3:
        return _ok(50, "3-4", ["PROC_GCP_COUNT_MARGINAL"])
    flags = ["PROC_GCP_COUNT_INSUFFICIENT"]
    if v == 0:
        flags.append("PROC_NO_GCPS_USED")
    return _ok(0, "<3", flags)


def i024(c):  # gcp_bundle_distribution
    v = c.get("gcp_bundle_distribution_coverage")
    if v is None:
        return _na("no GCP positions (0 GCPs or v2 spatial)")
    if v >= 0.8:
        return _ok(100, ">=80%")
    if v >= 0.6:
        return _ok(60, "60-80%", ["PROC_GCPS_CLUSTERED_IN_BUNDLE"])
    return _ok(30, "<60%", ["PROC_GCPS_SEVERELY_CLUSTERED"])


def i025(c):  # gcp_vertical_coverage
    v = c.get("gcp_vertical_coverage_ratio")
    if v is None:
        return _na("no GCP positions / z-range")
    if v >= 0.6:
        return _ok(100, ">=0.6")
    if v >= 0.3:
        return _ok(60, "0.3-0.6", ["PROC_GCPS_NO_VERTICAL_COVERAGE_MARGINAL"])
    return _ok(30, "<0.3", ["PROC_GCPS_NO_VERTICAL_COVERAGE"])


def i026(c):  # z_xy_residual_ratio
    v = c.get("z_xy_residual_ratio")
    if v is None:
        return _na("z/xy ratio absent")
    if v <= 1.5:
        return _ok(100, "<=1.5")
    if v <= 2:
        return _ok(70, "1.5-2", ["PROC_Z_XY_HIGH"])
    return _ok(30, ">2", ["PROC_Z_XY_SEVERE"])


def i027(c):  # per_marker_pix_error (aggregated)
    v = c.get("per_marker_pix_outlier_ratio")
    ratios = (v or {}).get("ratios") or {}
    if not ratios:
        return _na("no per-marker pix residuals")
    scores, flags = [], set()
    for r in ratios.values():
        if r <= 1.5:
            scores.append(100)
        elif r <= 2:
            scores.append(70); flags.add("PROC_MARKER_PIX_HIGH")
        else:
            scores.append(30); flags.add("PROC_MARKER_PIX_SEVERE")
    return _ok(_agg_outlier(scores, c.tun["OUTLIER_AGG_PENALTY_FACTOR"]), "per-marker agg", sorted(flags))


def i028(c):  # reconstruction_drift_composite
    v = c.get("reconstruction_drift_composite")
    if v is None:
        return _na("drift composite N/A")
    return _ok(50, "drift", ["PROC_RECONSTRUCTION_DRIFT"]) if v else _ok(100, "ok")


def i029(c):  # no_markers_at_all (flag-only, CATASTROPHIC)
    v = c.get("markers_total_zero")
    if v is None:
        return _na("markers total absent")
    return _ok(0, "0 markers", ["PROC_NO_MARKERS_AT_ALL"]) if v else _ok(100, "markers>0")


def i030(c):  # no_gcps_used (flag-only, weight 0)
    v = c.get("gcps_used_zero")
    if v is None:
        return _na("gcp-used flag N/A")
    return _ok(0, "0 GCPs", ["PROC_NO_GCPS_USED"]) if v else _ok(100, "GCPs used")


def i031(c):  # output_crs_project_match (GATE)
    v = c.get("output_crs_project_match")
    if v is None:
        return _na("CRS match N/A (report or manifest CRS absent)")
    if v:
        return _ok(100, "match")
    # score 0 + gate; the PROC_OUTPUT_CRS_MISMATCH flag is raised at 3d (global_gate)
    return _ok(0, "mismatch", [], gate=True)


def i032(c):  # output_crs_projection
    v = c.get("output_crs_is_projected")
    if v is None:
        return _na("CRS projection class N/A")
    return _ok(100, "projected") if v else _ok(30, "geographic", ["PROC_OUTPUT_CRS_GEOGRAPHIC"])


def i033(c):  # internal_transform_consistency
    v = c.get("internal_transform_match")
    if v is None:
        return _na("no pp capture CRS/geoid")
    return _ok(100, "match") if v else _ok(0, "mismatch", ["PROC_INTERNAL_TRANSFORM_WRONG"])


def i034(c):  # dtm_classification
    v = c.get("dtm_classification_consistency")
    if v is None:
        return _na("dtm consistency N/A")
    return _ok(100, "consistent / not claimed") if v else _ok(30, "DSM-as-DTM", ["PROC_DSM_LABELLED_DTM"])


def i035(c):  # dem_void_interpolation
    v = c.get("dem_void_interpolation_fraction") or {}
    frac = v.get("void_fraction")
    if frac is None:
        # v1.7 reports no void fraction -> cannot assess -> N/A redistribute (honest;
        # no "free 100"). Agisoft v2+ reports the stat; full detection is a v2 item.
        return _na("void statistics not reported by this Agisoft version (v1.7)")
    if frac < 0.10:
        return _ok(100, "low void fraction")
    return _ok(30, "high void fraction", ["PROC_DEM_INTERPOLATED_VOIDS"])


def i036(c):  # dem_resolution
    v = c.get("dem_to_gsd_ratio")
    if v is None:
        return _na("DEM/GSD ratio absent")
    if v <= 3:
        return _ok(100, "1-3x")
    if v <= 5:
        return _ok(70, "3-5x", ["PROC_DEM_RES_COARSE"])
    if v <= 10:
        return _ok(30, "5-10x", ["PROC_DEM_RES_VERY_COARSE"])
    return _ok(0, ">10x")


def i037(c):  # deliverable_completeness
    v = c.get("deliverable_completeness")
    if v is None:
        return _na("completeness N/A")
    if v["complete"]:
        return _ok(100, "all present")
    return _ok(50, f"missing {v['missing']}", ["PROC_DELIVERABLE_FILE_MISSING"])


def i038(c):  # software_version (advisory, scored)
    v = c.get("software_version_class")
    if v is None:
        return _na("version class N/A")
    if v.get("match"):
        return _ok(100, "matches baseline")
    return _ok(c.tun["SOFTWARE_UNKNOWN_SCORE"], "unknown version")


_EVAL = {f"L3I_PROC_{n:03d}": fn for n, fn in {
    1: i001, 2: i002, 3: i003, 4: i004, 5: i005, 6: i006, 7: i007, 8: i008, 9: i009,
    10: i010, 11: i011, 12: i012, 13: i013, 14: i014, 15: i015, 16: i016, 17: i017,
    18: i018, 19: i019, 20: i020, 21: i021, 22: i022, 23: i023, 24: i024, 25: i025,
    26: i026, 27: i027, 28: i028, 29: i029, 30: i030, 31: i031, 32: i032, 33: i033,
    34: i034, 35: i035, 36: i036, 37: i037, 38: i038,
}.items()}


# ---- context + evidence -----------------------------------------------------
class Ctx:
    def __init__(self, derived, source, tun, manifest_present, pp_present, deliv_present):
        self.d, self.s, self.tun = derived, source, tun
        self.manifest_present = manifest_present
        self.pp_present = pp_present
        self.deliv_present = deliv_present

    def get(self, name):
        return self.d[name] if name in self.d else self.s.get(name)


def _evidence_ok(ev, c):
    if ev == "report_and_manifest_required":
        return c.manifest_present
    if ev == "report_and_pp_handoff_required":
        return c.pp_present
    if ev == "manifest_and_deliverables_required":
        return c.manifest_present and c.deliv_present
    return True  # report_required (report always present)


def run(config, project_root, spec, stage3a_data, stage2_data) -> dict:
    derived = stage3a_data["derived"]
    source = stage2_data["source_fields"]
    flag_by_name = {f["flag_name"]: f for f in spec["flags"]}

    man_names = [s["field_name"] for s in spec["source_fields"] if s["file_id"] == "SRC_PROC_MANIFEST"]
    pp_names = [s["field_name"] for s in spec["source_fields"] if s["file_id"] == "SRC_PROC_PP_HANDOFF"]
    manifest_present = any(source.get(k) is not None for k in man_names)
    pp_present = any(source.get(k) is not None for k in pp_names)
    deliv_present = any(source.get(f"deliverable_{t}_present")
                        for t in ("ortho", "dsm", "dtm", "point_cloud", "mesh_3d"))
    ctx = Ctx(derived, source, TUNEABLES, manifest_present, pp_present, deliv_present)

    traces: dict[str, dict] = {}
    flags_raised: list[dict] = []
    for ind in spec["indicators"]:
        iid = ind["indicator_id"]
        ev = ind["evidence_required"]
        res = _EVAL[iid](ctx)
        # evidence-tier N/A overrides a computed score
        if not res["na"] and not _evidence_ok(ev, ctx):
            res = _na(f"evidence tier {ev} unmet")
        flag_records = []
        for fname in res["flags"]:
            meta = flag_by_name.get(fname, {})
            rec = {"flag_id": meta.get("flag_id"), "flag_name": fname,
                   "severity": meta.get("severity"), "_origin_stage": "stage3b",
                   "_indicator_id": iid}
            flag_records.append(rec)
            flags_raised.append(rec)
        traces[iid] = {
            "indicator_id": iid,
            "indicator_name": ind["indicator_name"],
            "building_block_id": ind["building_block_id"],
            "weight_in_block": float(ind["weight_in_block"]),
            "evidence_required": ev,
            "input_derived_fields": ind.get("input_derived_fields"),
            "input_values": {ind.get("input_derived_fields"): ctx.get(ind.get("input_derived_fields"))},
            "score": res["score"],
            "band_matched": res["band"],
            "na_redistribute": res["na"],
            "na_reason": res["na_reason"],
            "gate_triggered": res["gate"],
            "gate_flag_id": (flag_by_name.get("PROC_OUTPUT_CRS_MISMATCH", {}).get("flag_id")
                             if res["gate"] else None),
            "flags_raised": flag_records,
        }

    na_ids = sorted(iid for iid, t in traces.items() if t["na_redistribute"])
    gate_ids = sorted(iid for iid, t in traces.items() if t["gate_triggered"])
    return {
        "survey_level": True,
        "indicator_traces": dict(sorted(traces.items())),
        "flags_raised_stage3b": flags_raised,
        "stage3b_meta": {
            "indicator_count": len(traces),
            "expected_count": len(spec["indicators"]),
            "na_redistribute_ids": na_ids,
            "na_count": len(na_ids),
            "gate_triggered_ids": gate_ids,
            "flag_count": len(flags_raised),
            "evidence_presence": {"report": True, "manifest": manifest_present,
                                  "pp_handoff": pp_present, "deliverables": deliv_present},
            "tuneables": TUNEABLES,
        },
    }


def print_summary(data):
    mm = data["stage3b_meta"]
    print(f"  indicators: {mm['indicator_count']}/{mm['expected_count']}  "
          f"N/A: {mm['na_count']} {mm['na_redistribute_ids']}  gate: {mm['gate_triggered_ids'] or 'none'}  "
          f"flags: {mm['flag_count']}")
    for iid, t in data["indicator_traces"].items():
        if t["flags_raised"] or t["na_redistribute"]:
            tag = "N/A" if t["na_redistribute"] else f"{t['score']}"
            fl = [f["flag_name"] for f in t["flags_raised"]]
            print(f"    {iid} {t['indicator_name'][:30]:30s} {tag:>5}  {fl}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Processing Stage 3b indicators")
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
    data3a = stage3a_derived.run(config, root, spec, data2)
    data = run(config, root, spec, data3a, data2)

    out_path = root / config["outputs"]["stage3_indicators"]
    common.write_envelope(out_path, common.make_envelope(STAGE, data, config, spec_version))
    print(f"Stage 3b indicators -> {out_path.relative_to(root)}")
    print_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
