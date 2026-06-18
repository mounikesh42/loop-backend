#!/usr/bin/env python3
"""Stage 3b — Compute indicators with threshold bands and flag wiring.

Reads outputs/02_source_fields.json + outputs/03_derived_fields.json and
the spec (sheets 04 indicators, 05 thresholds, 09 flags). For each
indicator, evaluates its threshold bands in band_order top-down and
returns the score from the FIRST matching band. Emits a per-indicator
trace block per the build spec:

    {"indicator_id": "L3I_FC_001",
     "score": 72,
     "band_matched": "TH_056",
     "condition": "area_coverage_ratio >= 0.93",
     "input_value": 0.951,
     "flags_raised": []}

Score values and flag names are always pulled from the spec at runtime,
never hardcoded. Only the band-selection LOGIC is in Python — because the
spec's condition_expression mixes Python and human-readable prose
(uppercase AND/OR, "is absent", "is unknown/null", "is mixed DNG and JPG",
"min" shorthand, API_UNAVAILABLE fallback) that isn't safely eval-able.

Internal gates:
  L3I_IMG_001 image_validity_score < 30: gate fires at the BLOCK level
    (Stage 3c — compute_blocks.py), since the gate's action (zero out
    image_capture_score) is at the block. The flag (FLG_001
    CRITICAL_IMAGE_FAILURE, raised_at_stage = internal_gate) is also
    raised there. Stage 3b does NOT fire it — the indicator just scores
    in its threshold band normally.
  L3I_GNSS_004 critical_gap_present == True: encoded directly in the
    threshold ladder (TH_044 score=0); flag FLG_005 RINEX_CRITICAL_GAP
    fires at threshold_band when that row is selected.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _safe_min(*vals):
    """min ignoring None; returns None if all values are None."""
    vs = [v for v in vals if v is not None]
    return min(vs) if vs else None


def _safe_max(*vals):
    vs = [v for v in vals if v is not None]
    return max(vs) if vs else None


# ---------------------------------------------------------------------------
# Per-indicator evaluators.
#
# Each eval function returns (band_order, input_value_repr) — band_order
# is 1..N matching the spec's band_order column; input_value_repr is what
# the trace should show in `input_value`. The caller looks up the
# spec's TH_* row for that band_order and reads score + flag.
# ---------------------------------------------------------------------------

def _eval_L3I_IMG_001(f, d):
    """image_validity_score — bands on image_validity_ratio."""
    r = d.get("L2D_IMG_001")
    if r is None: return (5, None)
    if r >= 0.99: return (1, r)
    if r >= 0.97: return (2, r)
    if r >= 0.94: return (3, r)
    if r >= 0.90: return (4, r)
    return (5, r)


def _eval_L3I_IMG_002(f, d):
    """image_geotag_score — bands on image_geotag_ratio. Note band 5 spec
    says <0.85 (not <0.88) — first-match-wins means values in [0.85, 0.88)
    fall through to band 5 since no band 4 alternative catches them."""
    r = d.get("L2D_IMG_002")
    if r is None: return (5, None)
    if r >= 0.99: return (1, r)
    if r >= 0.97: return (2, r)
    if r >= 0.93: return (3, r)
    if r >= 0.88: return (4, r)
    return (5, r)


def _eval_L3I_IMG_003(f, d):
    """image_overlap_score — min(fwd, lat) overlap pct."""
    fwd = d.get("L2D_IMG_004")
    lat = d.get("L2D_IMG_005")
    m = _safe_min(fwd, lat)
    if m is None: return (5, None)
    if m >= 70: return (1, m)
    if m >= 60: return (2, m)
    if m >= 50: return (3, m)
    if m >= 40: return (4, m)
    return (5, m)


def _eval_L3I_IMG_004(f, d):
    """image_format_score — bands on L1F_IMG_004 image_format value/mixed."""
    fmt = f.get("L1F_IMG_004")
    if not isinstance(fmt, dict): return (3, fmt)
    if fmt.get("mixed"): return (3, fmt)  # band 3: mixed DNG and JPG
    val = fmt.get("value")
    if val in ("DNG", "RAW"): return (1, val)
    if val == "JPG": return (2, val)
    return (3, val)  # unknown formats fall to the worst band


def _eval_L3I_IMG_005(f, d):
    """image_exposure_consistency_score — bands on coefficient of variation."""
    cv = d.get("L2D_IMG_009")
    if cv is None: return (5, None)
    if cv < 0.05: return (1, cv)
    if cv < 0.10: return (2, cv)
    if cv < 0.20: return (3, cv)
    if cv < 0.35: return (4, cv)
    return (5, cv)


def _eval_L3I_IMG_006(f, d):
    """calibration_match_score — Make+Model match with the calibration entry.
    Band 4: "no calibration file present" — null calibration source."""
    cal_source = f.get("L1F_CAL_006")
    if cal_source is None:
        return (4, "no_calibration_file_present")
    make_match = d.get("L2D_IMG_006")
    model_match = d.get("L2D_IMG_007")
    val = f"make_match={make_match}, model_match={model_match}"
    if make_match and model_match: return (1, val)
    if make_match and not model_match: return (2, val)
    return (3, val)  # make_match=False AND model_match=False


def _eval_L3I_IMG_007(f, d):
    """calibration_source_score — discrete enum bands."""
    src = f.get("L1F_CAL_006")
    if src == "CB_LIBRARY":     return (1, src)
    if src == "ODM_DATABASE":   return (2, src)
    if src == "SELF_CALIBRATED":return (3, src)
    return (4, src)  # null / absent


def _eval_L3I_GNSS_001(f, d):
    """rinex_coverage_score — bands combine coverage ratio + buffers."""
    cov = d.get("L2D_GNSS_001")
    pre = f.get("L1F_GNSS_004")
    post = f.get("L1F_GNSS_005")
    val = {"coverage": cov, "pre_buffer": pre, "post_buffer": post}
    if cov is None: return (5, val)
    if cov >= 1.10 and (pre or 0) >= 120 and (post or 0) >= 60: return (1, val)
    if cov >= 1.00 and (pre or 0) >= 60: return (2, val)
    if cov >= 0.95: return (3, val)
    if cov >= 0.85: return (4, val)
    return (5, val)


def _eval_L3I_GNSS_002(f, d):
    """rinex_signal_quality_score — combined C/N0 and cycle slip thresholds.
    NB band 3 uses OR, band 4 uses OR with negated thresholds — read the
    spec carefully."""
    cn0 = f.get("L1F_GNSS_008")
    cs = f.get("L1F_GNSS_012")
    val = {"cn0_mean": cn0, "cycle_slips": cs}
    if cn0 is None or cs is None: return (4, val)
    if cn0 >= 35 and cs < 5:  return (1, val)
    if cn0 >= 32 and cs < 10: return (2, val)
    if cn0 >= 28 or cs < 20:  return (3, val)
    return (4, val)


def _eval_L3I_GNSS_003(f, d):
    """rinex_frequency_availability_score — dual-frequency boolean."""
    df = d.get("L2D_GNSS_002")
    return (1, df) if df else (2, df)


def _eval_L3I_GNSS_004(f, d):
    """rover_continuity_score — critical gap is the killer.
    L3I_GNSS_004 also acts as a HARD RULE per spec: if critical_gap_present
    is True, rover_continuity_score = 0 (handled by score_value in spec)."""
    cgp = d.get("L2D_GNSS_003")
    return (2, cgp) if cgp else (1, cgp)


def _eval_L3I_GNSS_005(f, d):
    """rover_acquisition_score — time-to-stable-lock bands."""
    t = d.get("L2D_GNSS_004")
    if t is None: return (4, None)
    if t < 60:  return (1, t)
    if t < 120: return (2, t)
    if t < 300: return (3, t)
    return (4, t)


def _eval_L3I_GNSS_006(f, d):
    """rover_pdop_score — combined mean+max PDOP bands."""
    mean = d.get("L2D_GNSS_005")
    mx = d.get("L2D_GNSS_006")
    val = {"mean_pdop": mean, "max_pdop": mx}
    if mean is None or mx is None: return (5, val)
    if mean < 1.5 and mx < 2.5: return (1, val)
    if mean < 2.0 and mx < 3.5: return (2, val)
    if mean < 3.0 and mx < 5.0: return (3, val)
    if mean < 6.0 or mx < 8.0:  return (4, val)
    return (5, val)


def _eval_L3I_FC_001(f, d):
    """mission_coverage_score — area_coverage_ratio bands."""
    r = d.get("L2D_FC_010")
    if r is None: return (5, None)
    if r >= 0.99: return (1, r)
    if r >= 0.97: return (2, r)
    if r >= 0.93: return (3, r)
    if r >= 0.88: return (4, r)
    return (5, r)


def _eval_L3I_FC_002(f, d):
    """mission_gsd_score — gsd_execution_ratio centered on 1.0."""
    r = d.get("L2D_FC_005")
    if r is None: return (4, None)
    if 0.92 <= r <= 1.05: return (1, r)
    if 0.85 <= r <= 1.10: return (2, r)
    if 0.78 <= r <= 1.18: return (3, r)
    return (4, r)


def _eval_L3I_FC_003(f, d):
    """mission_overlap_score — min of fwd/lat execution ratios."""
    fwd = d.get("L2D_FC_006")
    lat = d.get("L2D_FC_007")
    m = _safe_min(fwd, lat)
    if m is None: return (4, None)
    if 0.95 <= m <= 1.10: return (1, m)
    if 0.88 <= m <= 1.15: return (2, m)
    if 0.80 <= m <= 1.20: return (3, m)
    return (4, m)


def _eval_L3I_FC_004(f, d):
    """mission_altitude_score — altitude_execution_ratio centered on 1.0."""
    r = d.get("L2D_FC_008")
    if r is None: return (4, None)
    if 0.95 <= r <= 1.05: return (1, r)
    if 0.88 <= r <= 1.10: return (2, r)
    if 0.80 <= r <= 1.18: return (3, r)
    return (4, r)


def _eval_L3I_FC_005(f, d, parser_meta=None):
    """wind_condition_score — primary API path + ATT-proxy fallback bands.
    Bands 6/7/8 only apply when the Open-Meteo API was unavailable; we
    detect that via parser_meta.fetch_openmeteo.fallback_used."""
    parser_meta = parser_meta or {}
    fbu = (parser_meta.get("fetch_openmeteo") or {}).get("fallback_used")
    if fbu:
        band_lbl = (parser_meta.get("fetch_openmeteo") or {}).get("fallback_band")
        if band_lbl == "calm":     return (6, band_lbl)
        if band_lbl == "moderate": return (7, band_lbl)
        return (8, band_lbl or "unknown")
    w = f.get("L1F_API_001")
    if w is None: return (5, None)
    if w < 5:  return (1, w)
    if w < 8:  return (2, w)
    if w < 10: return (3, w)
    if w < 12: return (4, w)
    return (5, w)


def _eval_L3I_FC_006(f, d):
    """mission_altitude_consistency_score — altitude_variance_m bands."""
    v = d.get("L2D_FC_012")
    if v is None: return (5, None)
    if v < 2:  return (1, v)
    if v < 5:  return (2, v)
    if v < 10: return (3, v)
    if v < 20: return (4, v)
    return (5, v)


def _eval_L3I_FC_007(f, d):
    """mission_completion_score — bands on mission_completion_ratio."""
    r = d.get("L2D_FC_009")
    if r is None: return (4, None)
    if r == 1.00: return (1, r)
    if r >= 0.97: return (2, r)
    if r >= 0.93: return (3, r)
    return (4, r)


def _eval_L3I_CAL_001(f, d):
    """CAL_CONF calibration_source_score — same enum as L3I_IMG_007 but
    additionally raises flags in the CAL_CONF context (per script_hints).
    Returns the same band_order; flag_raised comes from the spec TH_*."""
    return _eval_L3I_IMG_007(f, d)


def _eval_L3I_CAL_002(f, d):
    """CAL_CONF calibration_match_score — 3 bands (no 'no file' band; that's
    handled in L3I_CAL_001 via SELF_CALIBRATED)."""
    make_match = d.get("L2D_IMG_006")
    model_match = d.get("L2D_IMG_007")
    val = f"make_match={make_match}, model_match={model_match}"
    if make_match and model_match: return (1, val)
    if make_match and not model_match: return (2, val)
    return (3, val)


def _eval_L3I_CAL_003(f, d):
    """calibration_age_score — calibration_age_months bands, with band 6
    when calibration_date is unknown/null (raises CALIBRATION_DATE_UNKNOWN)."""
    cal_date = f.get("L1F_CAL_007")
    age = d.get("L2D_IMG_008")
    if cal_date is None or age is None:
        return (6, None)
    if age < 6:  return (1, age)
    if age < 12: return (2, age)
    if age < 24: return (3, age)
    if age < 36: return (4, age)
    return (5, age)


EVALUATORS = {
    "L3I_IMG_001": _eval_L3I_IMG_001,
    "L3I_IMG_002": _eval_L3I_IMG_002,
    "L3I_IMG_003": _eval_L3I_IMG_003,
    "L3I_IMG_004": _eval_L3I_IMG_004,
    "L3I_IMG_005": _eval_L3I_IMG_005,
    "L3I_IMG_006": _eval_L3I_IMG_006,
    "L3I_IMG_007": _eval_L3I_IMG_007,
    "L3I_GNSS_001": _eval_L3I_GNSS_001,
    "L3I_GNSS_002": _eval_L3I_GNSS_002,
    "L3I_GNSS_003": _eval_L3I_GNSS_003,
    "L3I_GNSS_004": _eval_L3I_GNSS_004,
    "L3I_GNSS_005": _eval_L3I_GNSS_005,
    "L3I_GNSS_006": _eval_L3I_GNSS_006,
    "L3I_FC_001": _eval_L3I_FC_001,
    "L3I_FC_002": _eval_L3I_FC_002,
    "L3I_FC_003": _eval_L3I_FC_003,
    "L3I_FC_004": _eval_L3I_FC_004,
    "L3I_FC_005": _eval_L3I_FC_005,
    "L3I_FC_006": _eval_L3I_FC_006,
    "L3I_FC_007": _eval_L3I_FC_007,
    "L3I_CAL_001": _eval_L3I_CAL_001,
    "L3I_CAL_002": _eval_L3I_CAL_002,
    "L3I_CAL_003": _eval_L3I_CAL_003,
}


def compute(spec: dict, source_envelope: dict, derived_envelope: dict) -> dict:
    indicators_spec = spec["indicators"]
    thresholds_spec = spec["thresholds"]
    flags_spec = spec["flags"]

    by_indicator = {}
    for t in thresholds_spec:
        by_indicator.setdefault(t["indicator_id"], []).append(t)
    for tlist in by_indicator.values():
        tlist.sort(key=lambda x: x["band_order"])

    fields = {k: v for k, v in source_envelope["data"].items() if k.startswith("L1F_")}
    derived = {k: v for k, v in derived_envelope["data"].items() if k.startswith("L2D_")}
    parser_meta = (source_envelope["data"].get("_parser_meta") or {})

    traces = []
    flags_raised_all = []
    indicator_scores = {}

    for ind in indicators_spec:
        iid = ind["indicator_id"]
        evaluator = EVALUATORS.get(iid)
        if evaluator is None:
            traces.append({
                "indicator_id": iid,
                "indicator_name": ind["indicator_name"],
                "score": None,
                "band_matched": None,
                "condition": None,
                "input_value": None,
                "flags_raised": [],
                "error": f"no evaluator implemented for {iid}",
            })
            continue

        # Call evaluator (L3I_FC_005 needs parser_meta for fallback detection)
        if iid == "L3I_FC_005":
            band_order, input_value = evaluator(fields, derived, parser_meta)
        else:
            band_order, input_value = evaluator(fields, derived)

        bands = by_indicator.get(iid, [])
        # Spec's band_order is 1-based; find matching threshold row.
        match = next((t for t in bands if t["band_order"] == band_order), None)
        if match is None:
            # Defensive fallback: use the worst band (highest band_order)
            match = bands[-1] if bands else None

        score = match["score_value"] if match else None
        threshold_id = match["threshold_id"] if match else None
        condition = match.get("condition_expression") if match else None
        band_flag_name = match.get("flag_raised") if match else None

        flags_for_trace = []
        if band_flag_name:
            flag_def = next((f for f in flags_spec if f["flag_name"] == band_flag_name), None)
            if flag_def:
                flags_for_trace.append({
                    "flag_id": flag_def["flag_id"],
                    "flag_name": flag_def["flag_name"],
                    "severity": flag_def["severity"],
                    "stage": flag_def["raised_at_stage"],
                    "raised_by": flag_def.get("raised_by_id"),
                    "context": f"threshold {threshold_id} triggered: {condition}",
                })

        traces.append({
            "indicator_id": iid,
            "indicator_name": ind["indicator_name"],
            "score": score,
            "band_matched": threshold_id,
            "condition": condition,
            "input_value": input_value,
            "flags_raised": flags_for_trace,
        })
        if flags_for_trace:
            flags_raised_all.extend(flags_for_trace)
        indicator_scores[iid] = score

    return {
        "traces": traces,
        "scores": indicator_scores,
        "flags_raised": flags_raised_all,
    }


def run(config: dict, project_root: Path) -> dict:
    spec_path = project_root / config["spec_file"]
    src_path = project_root / config["outputs"]["stage2_source_fields"]
    der_path = project_root / config["outputs"]["stage3_derived"]

    spec = json.loads(spec_path.read_text())
    src_envelope = json.loads(src_path.read_text())
    der_envelope = json.loads(der_path.read_text())

    result = compute(spec, src_envelope, der_envelope)

    envelope = {
        "spec_version": config.get("spec_version"),
        "config_used": config,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": "stage3b_indicators",
        "data": {
            "indicators": result["traces"],
            "indicator_scores": result["scores"],
            "flags_raised_stage3b": result["flags_raised"],
        },
    }
    return envelope


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: compute_indicators.py <paths.json>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())
    envelope = run(config, project_root)

    out_path = project_root / config["outputs"]["stage3_indicators"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(envelope, indent=2, sort_keys=True, default=str) + "\n")

    traces = envelope["data"]["indicators"]
    print(f"compute_indicators: wrote {out_path}")
    print(f"  {len(traces)} indicators evaluated")
    print()
    print(f"{'indicator_id':14s} {'name':40s} {'score':>6s}  {'band':6s}  flags")
    print("-" * 100)
    for t in traces:
        flags = ",".join(f["flag_name"] for f in t["flags_raised"]) or "-"
        print(f"{t['indicator_id']:14s} {t['indicator_name']:40s} {str(t['score']):>6s}  {t['band_matched']:6s}  {flags}")
    print()
    n_flags = len(envelope["data"]["flags_raised_stage3b"])
    print(f"Total flags raised at Stage 3b: {n_flags}")
    for f in envelope["data"]["flags_raised_stage3b"]:
        print(f"  [{f['flag_id']}] {f['flag_name']} ({f['severity']}, {f['stage']}): {f['context']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
