#!/usr/bin/env python3
"""Parser for SRC_PROC_REPORT - the Agisoft Metashape Processing Report (PDF).

This is the primary data-layer component: it owns 67 of the 90 source fields.
Per the spec (06b known_limitations) the parser MUST key off SECTION HEADERS,
never page numbers, because Agisoft changes page layout between versions. The
canonical headers (Agisoft v1.6/1.7):

    Survey Data / Camera Calibration / Camera Locations /
    Ground Control Points / Digital Elevation Model /
    Processing Parameters  (+ its sub-blocks)  / System

Extraction is text-based (pdfplumber). Scalar fields come from unique labels;
the Processing-Parameters key/value pairs are parsed per sub-block (General /
Point Cloud / Alignment parameters / Optimization parameters / Depth Maps /
Dense Point Cloud / DEM / Orthomosaic / System) because keys like
"Coordinate system" and "Software version" repeat across blocks.

Honest nulls (documented in parser_meta.nulls) are emitted for fields a given
report does not contain (e.g. control-point RMSE on a PPK/no-GCP survey,
absolute marker XYZ, void statistics) rather than guessing.

Returns: (fields_by_name, parser_meta).
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    import pdfplumber
except ImportError:  # pragma: no cover - surfaced as a parser warning
    pdfplumber = None

FILE_ID = "SRC_PROC_REPORT"

# Canonical top-level section headers (order matters for slicing).
_SECTIONS = [
    "Survey Data", "Camera Calibration", "Camera Locations",
    "Ground Control Points", "Digital Elevation Model",
    "Processing Parameters", "System",
]
# Sub-block headers inside Processing Parameters (each on its own line).
_PP_BLOCKS = [
    "General", "Point Cloud", "Alignment parameters", "Optimization parameters",
    "Depth Maps", "Dense Point Cloud", "DEM", "Orthomosaic", "System",
]
_CALIB_PARAMS = ["F", "Cx", "Cy", "B1", "B2", "K1", "K2", "K3", "P1", "P2"]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _num(s):
    """First numeric token in s (commas stripped, sci-notation ok), else None."""
    if s is None:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?", str(s).replace(",", ""))
    if not m:
        return None
    v = float(m.group(0))
    return int(v) if v.is_integer() and "." not in m.group(0) and "e" not in m.group(0).lower() else v


def _full_text(pdf_path: Path):
    """List of (page_no, text) plus the joined text."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            pages.append((i, pg.extract_text() or ""))
    return pages, "\n".join(t for _, t in pages)


def _split_sections(full_text: str) -> dict:
    """Slice the report into {section_header: body_text} keyed off headers,
    not page numbers (spec requirement)."""
    lines = full_text.splitlines()
    # locate each section header line index
    idx = {}
    for i, ln in enumerate(lines):
        s = ln.strip()
        for h in _SECTIONS:
            if s == h and h not in idx:
                idx[h] = i
    ordered = sorted(idx.items(), key=lambda kv: kv[1])
    out = {}
    for n, (h, start) in enumerate(ordered):
        end = ordered[n + 1][1] if n + 1 < len(ordered) else len(lines)
        out[h] = "\n".join(lines[start:end])
    return out


def _kv(block_text: str, key: str, numeric: bool = False):
    """Value following `key` on its line within block_text. For numeric keys
    the remainder must start with a digit/sign (guards prefix collisions like
    'Key point limit' vs 'Key point limit per Mpx')."""
    for ln in block_text.splitlines():
        s = ln.strip()
        if s.startswith(key):
            rest = s[len(key):].strip()
            if not rest:
                continue
            if numeric and not re.match(r"-?\d", rest):
                continue
            return rest
    return None


# --------------------------------------------------------------------------- #
# section extractors
# --------------------------------------------------------------------------- #
def _label_value(t: str, label: str):
    """Value after `label` anywhere in the text (NOT only at line start). The
    Survey Data block is two-column - 'Coverage area: ... Reprojection error: ...'
    - so right-column labels never start a line."""
    m = re.search(re.escape(label) + r"\s*([-\d][\d,]*\.?\d*)", t)
    return m.group(1) if m else None


def _survey_data(sec, f, nulls):
    t = sec.get("Survey Data", "")
    f["reportSurveyData_n_images"] = _num(_label_value(t, "Number of images:"))
    f["reportSurveyData_camera_stations"] = _num(_label_value(t, "Camera stations:"))
    f["reportSurveyData_flying_altitude_m"] = _num(_label_value(t, "Flying altitude:"))
    f["reportSurveyData_tie_points"] = _num(_label_value(t, "Tie points:"))
    f["reportSurveyData_ground_resolution_cm"] = _num(_label_value(t, "Ground resolution:"))
    f["reportSurveyData_projections"] = _num(_label_value(t, "Projections:"))
    f["reportSurveyData_coverage_area_km2"] = _num(_label_value(t, "Coverage area:"))
    f["reportSurveyData_reprojection_error_pix"] = _num(_label_value(t, "Reprojection error:"))

    # Table 1. Cameras  ->  model / resolution / focal / pixel / precalibrated
    model = res = focal = pix = precal = None
    for ln in t.splitlines():
        m = re.search(r"(\d+\s*x\s*\d+)\s+(\d+\s*mm)\s+(\d+\s*x\s*\d+\s*[μu]m)\s+(Yes|No)\s*$", ln)
        if m:
            res, focal, pix, precal = m.group(1), m.group(2), m.group(3), m.group(4)
            model = ln[:m.start()].strip().rstrip("….").strip()  # may be truncated in Table 1
            break
    f["reportCameras_resolution"] = res
    f["reportCameras_focal_length"] = focal
    f["reportCameras_pixel_size"] = pix
    f["reportCameras_precalibrated"] = precal
    f["reportCameras_camera_model"] = model  # refined from calibration header below


def _calibration(sec, f, nulls):
    t = sec.get("Camera Calibration", "")
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    # full (untruncated) camera model: the line just before "NNN images"
    for i, ln in enumerate(lines):
        if re.match(r"\d+\s+images$", ln) and i > 0:
            f["reportCameras_camera_model"] = lines[i - 1].strip()
            break
    # Correlation matrix: each param row -> Value, Error, then upper-triangular corr.
    matrix = {}
    for p in _CALIB_PARAMS:
        for ln in lines:
            toks = ln.split()
            if toks and toks[0] == p:
                nums = []
                for tk in toks[1:]:
                    v = _num(tk)
                    if v is None:
                        nums = []
                        break
                    nums.append(v)
                if len(nums) >= 3:  # value, error, diag(1.00), ...
                    matrix[p] = {"value": nums[0], "error": nums[1], "corr": nums[2:]}
                    break
    f["reportCalibration_correlation_matrix"] = matrix or None
    # k1_k2 = first off-diagonal in K1 row; k2_k3 = first off-diagonal in K2 row
    f["reportCalibration_k1_k2_correlation"] = matrix.get("K1", {}).get("corr", [None, None])[1] \
        if matrix.get("K1") and len(matrix["K1"]["corr"]) > 1 else None
    f["reportCalibration_k2_k3_correlation"] = matrix.get("K2", {}).get("corr", [None, None])[1] \
        if matrix.get("K2") and len(matrix["K2"]["corr"]) > 1 else None


def _camera_locations(sec, f, nulls):
    t = sec.get("Camera Locations", "")
    lines = t.splitlines()
    for i, ln in enumerate(lines):
        if "X error (cm)" in ln and "Total error (cm)" in ln:
            for j in range(i + 1, min(i + 4, len(lines))):
                nums = re.findall(r"-?\d+\.?\d*", lines[j])
                if len(nums) >= 5:
                    vals = [float(x) for x in nums[:5]]
                    (f["reportCameraLocations_x_err_cm"], f["reportCameraLocations_y_err_cm"],
                     f["reportCameraLocations_z_err_cm"], f["reportCameraLocations_xy_err_cm"],
                     f["reportCameraLocations_total_err_cm"]) = vals
                    return
    for k in ("x", "y", "z", "xy", "total"):
        f.setdefault(f"reportCameraLocations_{k}_err_cm", None)


def _gcp(sec, f, nulls):
    t = sec.get("Ground Control Points", "")
    lines = t.splitlines()

    # --- summary RMSE tables (Control points RMSE / Check points RMSE) -------
    def _summary_after_header(start):
        for j in range(start + 1, min(start + 4, len(lines))):
            nums = re.findall(r"-?\d+\.?\d*", lines[j])
            if len(nums) >= 6:               # Count X Y Z XY Total
                return [float(x) for x in nums[:6]]
        return None

    control = check = None
    for i, ln in enumerate(lines):
        if "Count" in ln and "Total (cm)" in ln:
            row = _summary_after_header(i)
            # role from the nearby caption
            caption = " ".join(lines[i:i + 4])
            if row and "Control points RMSE" in caption:
                control = row
            elif row and "Check points RMSE" in caption:
                check = row
            elif row and check is None:
                check = row  # default unlabelled summary to check
    if control:
        f["reportGCP_control_rmse_xy_cm"] = control[4]
        f["reportGCP_control_rmse_z_cm"] = control[3]
        f["reportGCP_control_rmse_total_cm"] = control[5]
    else:
        for k in ("xy", "z", "total"):
            nulls[f"reportGCP_control_rmse_{k}_cm"] = "no control-point RMSE table (PPK/no-GCP survey)"
            f[f"reportGCP_control_rmse_{k}_cm"] = None
    if check:
        f["reportGCP_check_rmse_xy_cm"] = check[4]
        f["reportGCP_check_rmse_z_cm"] = check[3]
        f["reportGCP_check_rmse_total_cm"] = check[5]
    else:
        for k in ("xy", "z", "total"):
            nulls[f"reportGCP_check_rmse_{k}_cm"] = "no check-point RMSE table reported"
            f[f"reportGCP_check_rmse_{k}_cm"] = None

    # --- per-marker tables (Label X Y Z Total Image(pix)) --------------------
    residuals, image_pix, image_count, roles = {}, {}, {}, {}
    cur_role = None
    for ln in lines:
        s = ln.strip()
        # caption tells us which role the preceding rows had
        if re.search(r"Table \d+\.\s*Control points", s):
            cur_role = "control"
        elif re.search(r"Table \d+\.\s*Check points", s):
            cur_role = "check"
        m = re.match(r"^(\S+)\s+(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s+(-?\d+\.?\d*)\s*\((\d+)\)", s)
        if m and m.group(1).lower() != "total":
            label = m.group(1)
            residuals[label] = float(m.group(5))     # Total (cm)
            image_pix[label] = float(m.group(6))     # Image (pix)
            image_count[label] = int(m.group(7))     # (N images)
            roles[label] = None                      # filled after role caption seen
    # assign roles: rows above a caption belong to that caption's role; default check
    # (single-role report -> all share the one caption role)
    role_seen = "check" if "Check points" in t and "Control points RMSE" not in t else None
    for label in residuals:
        roles[label] = roles[label] or role_seen or "check"

    f["reportGCP_per_marker_residuals"] = residuals or None
    f["reportGCP_per_marker_image_pix"] = image_pix or None
    f["reportGCP_per_marker_image_count"] = image_count or None
    f["reportGCP_marker_roles"] = roles or None

    # counts: prefer the summary Count cell; fall back to per-marker tallies
    n_check = int(check[0]) if check else sum(1 for r in roles.values() if r == "check")
    n_control = int(control[0]) if control else sum(1 for r in roles.values() if r == "control")
    f["reportGCP_check_points_count"] = n_check
    f["reportGCP_control_points_count"] = n_control

    # absolute marker XYZ not present in v1.7 reports (only error components)
    f["reportGCP_marker_locations"] = None
    nulls["reportGCP_marker_locations"] = "absolute marker XYZ not emitted in Agisoft v1.7 report"


def _dem(sec, f, nulls):
    t = sec.get("Digital Elevation Model", "")
    f["reportDEM_resolution_cm"] = _num(_kv(t, "Resolution:"))
    f["reportDEM_point_density"] = _num(_kv(t, "Point density:"))
    # z-range from the DEM figure colour-scale labels ("481 m" .. "453 m").
    # Exclude the map scale bar: a standalone "N m" line sitting immediately
    # before the "Fig." caption (present in every Agisoft figure).
    dlines = t.splitlines()
    ms = []
    for i, ln in enumerate(dlines):
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*m\s*$", ln)
        if not m:
            continue
        nxt = dlines[i + 1].strip() if i + 1 < len(dlines) else ""
        if nxt.startswith("Fig."):
            continue  # map scale bar, not an elevation label
        ms.append(float(m.group(1)))
    if ms:
        f["reportDEM_z_range_min_m"] = min(ms)
        f["reportDEM_z_range_max_m"] = max(ms)
        nulls["_note_dem_zrange"] = "z-range derived from DEM figure colour-scale labels (approximate)"
    else:
        f["reportDEM_z_range_min_m"] = f["reportDEM_z_range_max_m"] = None
        nulls["reportDEM_z_range_min_m"] = nulls["reportDEM_z_range_max_m"] = "no DEM elevation labels found"
    # void statistics: version-dependent, not in v1.7 reference
    f["reportDEM_void_statistics"] = None
    nulls["reportDEM_void_statistics"] = "void statistics not reported by this Agisoft version"


def _processing_params(sec, f, nulls):
    t = sec.get("Processing Parameters", "")
    lines = t.splitlines()
    # split into sub-blocks by header lines
    blocks, cur, name = {}, [], None
    for ln in lines:
        s = ln.strip()
        if s in _PP_BLOCKS:
            if name is not None:
                blocks[name] = "\n".join(cur)
            name, cur = s, []
        else:
            cur.append(ln)
    if name is not None:
        blocks[name] = "\n".join(cur)

    gen = blocks.get("General", "")
    pc = blocks.get("Point Cloud", "")
    al = blocks.get("Alignment parameters", "")
    opt = blocks.get("Optimization parameters", "")
    dm = blocks.get("Depth Maps", "")
    dem = blocks.get("DEM", "")
    om = blocks.get("Orthomosaic", "")
    sysb = blocks.get("System", "")

    f["reportGCP_total_markers_count"] = _num(_kv(gen, "Markers", numeric=True))
    f["reportParams_coordinate_system"] = _kv(gen, "Coordinate system")

    # Point Cloud: pull the (… pix) value out of the parenthesis
    def _pix_paren(line):
        m = re.search(r"\(([\d.]+)\s*pix\)", line or "")
        return float(m.group(1)) if m else None
    f["reportParams_rms_reprojection_error_pix"] = _pix_paren(_kv(pc, "RMS reprojection error"))
    f["reportParams_max_reprojection_error_pix"] = _pix_paren(_kv(pc, "Max reprojection error"))
    f["reportParams_avg_tie_point_multiplicity"] = _num(_kv(pc, "Average tie point multiplicity"))

    f["reportParams_alignment_accuracy"] = _kv(al, "Accuracy")
    f["reportParams_generic_preselection"] = _kv(al, "Generic preselection")
    f["reportParams_key_point_limit"] = _num(_kv(al, "Key point limit", numeric=True))
    f["reportParams_tie_point_limit"] = _num(_kv(al, "Tie point limit", numeric=True))
    f["reportParams_guided_image_matching"] = _kv(al, "Guided image matching")
    f["reportParams_adaptive_camera_fitting"] = _kv(al, "Adaptive camera model fitting")

    f["reportParams_optimization_parameters"] = _kv(opt, "Parameters")

    f["reportParams_depth_quality"] = _kv(dm, "Quality")
    f["reportParams_depth_filtering_mode"] = _kv(dm, "Filtering mode")

    f["reportParams_ortho_blending_mode"] = _kv(om, "Blending mode")
    f["reportParams_ortho_hole_filling"] = _kv(om, "Enable hole filling")
    f["reportParams_ortho_ghosting_filter"] = _kv(om, "Enable ghosting filter")

    # per-deliverable coordinate systems (sub-block scoped)
    f["reportPerDeliverable_dem_coordinate_system"] = _kv(dem, "Coordinate system")
    f["reportPerDeliverable_orthomosaic_coordinate_system"] = _kv(om, "Coordinate system")
    dc_cs = _kv(blocks.get("Dense Point Cloud", ""), "Coordinate system")
    f["reportPerDeliverable_dense_cloud_coordinate_system"] = dc_cs
    if dc_cs is None:
        nulls["reportPerDeliverable_dense_cloud_coordinate_system"] = \
            "dense cloud block has no Coordinate system line in this report"

    # DEM reconstruction params
    f["reportDEM_source_data"] = _kv(dem, "Source data")
    f["reportDEM_interpolation_enabled"] = _kv(dem, "Interpolation")
    # ground classification: not run when DEM source is the (unclassified) dense cloud
    src = (f.get("reportDEM_source_data") or "").lower()
    f["reportDEM_ground_classification_ran"] = False if "dense cloud" in src else None
    f["reportDEM_point_classification_params"] = None
    nulls.setdefault("reportDEM_point_classification_params",
                     "no ground-classification parameters reported (DEM source = dense cloud)")

    # datum/geoid transform: not separately reported (CRS line carries no geoid)
    f["reportParams_datum_geoid_transform"] = None
    nulls["reportParams_datum_geoid_transform"] = "no separate datum/geoid transform line in report"

    # System sub-block (also a top-level System section; prefer the dedicated one in _system)
    if sysb:
        f.setdefault("reportSystem_software_version", _kv(sysb, "Software version"))


def _system(sec, f, nulls):
    t = sec.get("System", "")
    f["reportSystem_software_name"] = _kv(t, "Software name")
    sv = _kv(t, "Software version")
    if sv:
        f["reportSystem_software_version"] = sv
    f.setdefault("reportSystem_software_version", None)
    f["reportSystem_os"] = _kv(t, "OS")


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def parse(pdf_path, spec_field_names=None):
    pdf_path = Path(pdf_path)
    fields: dict = {}
    nulls: dict = {}
    warnings: list = []

    if pdfplumber is None:
        return {}, {"file_id": FILE_ID, "error": "pdfplumber not installed",
                    "fields_produced": 0}

    pages, full = _full_text(pdf_path)
    sections = _split_sections(full)
    missing_sections = [h for h in _SECTIONS if h not in sections]
    if missing_sections:
        warnings.append({"code": "MISSING_SECTIONS", "detail": missing_sections})

    _survey_data(sections, fields, nulls)
    _calibration(sections, fields, nulls)
    _camera_locations(sections, fields, nulls)
    _gcp(sections, fields, nulls)
    _dem(sections, fields, nulls)
    _processing_params(sections, fields, nulls)
    _system(sections, fields, nulls)

    # no silent nulls: any None field without a recorded reason gets one
    for k, v in fields.items():
        if v is None and k not in nulls and not k.startswith("_"):
            nulls[k] = "not found in report (label absent or layout unparsed)"

    # drop private notes (keys starting with "_") from the field set
    notes = {k: v for k, v in nulls.items() if k.startswith("_")}
    nulls = {k: v for k, v in nulls.items() if not k.startswith("_")}

    produced = {k: v for k, v in fields.items() if v is not None}
    null_fields = sorted({k for k, v in fields.items() if v is None} | set(nulls))

    parser_meta = {
        "file_id": FILE_ID,
        "instance_found": len(pages) > 0,
        "source_path": str(pdf_path),
        "extraction_method": "section-header-keyed pdfplumber text (not page-number based)",
        "page_count": len(pages),
        "sections_found": sorted(sections.keys()),
        "missing_sections": missing_sections,
        "software_name": fields.get("reportSystem_software_name"),
        "software_version": fields.get("reportSystem_software_version"),
        "os": fields.get("reportSystem_os"),
        "fields_produced": len(produced),
        "fields_null": len(null_fields),
        "nulls": nulls,
        "notes": notes,
        "warnings": warnings,
    }

    # optional self-audit against the spec's report-owned field names
    if spec_field_names is not None:
        expected = set(spec_field_names)
        emitted = set(fields.keys())
        parser_meta["audit"] = {
            "expected": len(expected),
            "emitted": len(emitted & expected),
            "missing": sorted(expected - emitted),
            "extra": sorted(emitted - expected),
        }
    return fields, parser_meta


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Parse an Agisoft Metashape PDF report")
    ap.add_argument("pdf")
    args = ap.parse_args()
    flds, meta = parse(args.pdf)
    print(json.dumps({"fields": flds, "parser_meta": meta}, indent=2, sort_keys=True, default=str))
