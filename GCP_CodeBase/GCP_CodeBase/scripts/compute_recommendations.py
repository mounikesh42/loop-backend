#!/usr/bin/env python3
"""Compute customer-facing recommendations from GCP (PPK) pipeline outputs.

POST-PIPELINE STANDALONE — does NOT modify any pipeline script. Reads the pipeline's
per-point outputs + the Tier-2 indicator library and emits a per-point recommendations
object. GCP is a PER-POINT chain (N GCP occupations per survey), so the engine iterates
POINT x LIBRARY_INDICATOR and joins each (point, indicator) by score-range band lookup.

Inputs:
  - outputs/04_indicators.json   (.data.points[] -> indicator_traces keyed by full id)
  - outputs/06_gcp_score.json    (apex + structured global_gate + null_handling)
  - GCP_Recommendations/gcp_indicator_library_v2_1.json (Tier 2)

Output:
  - outputs/07_recommendations.json

Vocabulary (capture chain, terminal = resurvey_recommended):
  - resurvey_recommended  <- null_handling NOT fired AND
                             (global gate fired OR any point hard-gated OR any point resurvey)
  - review_recommended    <- any point at review level (no resurvey / hard gate)
  - good_to_go            <- all points good
  - unable_to_assess      <- null_handling fired (zero GCP-role points)

Per-point decision: resurvey if any indicator hard_gate_fired or band level "resurvey";
else review if any band level "review"; else good. (minor bands are audit-only.)

Hard gate: a critical-path indicator (is_critical_path) at a point with gate_triggered OR
score == 0 — captured as {point_id: [indicator_id, ...]}.

Usage: /opt/anaconda3/bin/python3 scripts/compute_recommendations.py paths.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LIBRARY_REL_PATH = "GCP_Recommendations/gcp_indicator_library_v2_1.json"
TERMINAL = "resurvey_recommended"

TIER_BANDS = [
    (90, 100, "Gold"),
    (75, 89, "Silver"),
    (60, 74, "Bronze"),
    (40, 59, "Marginal"),
    (0, 39, "Poor"),
]
_LEVEL_RANK = {"good": 0, "minor": 1, "review": 2, "resurvey": 3}
_DECISION_RANK = {"good": 0, "review": 1, "resurvey": 2}


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tier(score) -> str:
    if score is None:
        return "unable_to_assess"
    for lo, hi, name in TIER_BANDS:
        if lo <= score <= hi:
            return f"{name} ({lo}-{hi})"
    return "unknown"


def _lookup_band(score, bands: list[dict]) -> dict | None:
    """Inclusive [lo, hi]; top band wins ties (scan high-to-low by lower bound)."""
    if score is None:
        return None
    for b in sorted(bands, key=lambda x: (x.get("score_range") or [0, 0])[0], reverse=True):
        rng = b.get("score_range") or [None, None]
        lo, hi = rng[0], rng[1]
        if lo is not None and hi is not None and lo <= score <= hi:
            return b
    return None


def _sub(text, point_id):
    """Substitute the {point_id} template token in library customer text."""
    if isinstance(text, str):
        return text.replace("{point_id}", str(point_id))
    if isinstance(text, list):
        return [_sub(x, point_id) for x in text]
    return text


def _level_rank(level) -> int:
    return _LEVEL_RANK.get((level or "").lower(), 0)


def compute(config: dict, project_root: Path, stage3b: dict, apex_env: dict, library: dict) -> dict:
    s3b = stage3b["data"]
    apex = apex_env["data"]
    lib = library["library"]
    lib_meta = library["_meta"]
    apex_score = apex.get("gcp_score")
    global_gate = apex.get("global_gate", {})
    null_handling = apex.get("null_handling", {})

    pipeline_spec_version = apex_env.get("spec_version")
    library_version = lib_meta.get("version")
    spec_version_note = None
    if pipeline_spec_version and library_version and pipeline_spec_version not in library_version:
        spec_version_note = (
            f"Pipeline ran spec v{pipeline_spec_version}; library targets {library_version}. "
            "Band levels (Tier 2) are authoritative for the decision; aggregated-flag severities "
            "reflect the pipeline's spec version."
        )

    base = {
        "subsystem": config.get("subsystem", "gcp_ppk"),
        "generated_at": _iso_now(),
        "spec_version": pipeline_spec_version,
        "library_version": library_version,
        "library_authorship_status": lib_meta.get("text_authorship_status"),
        "_spec_version_note": spec_version_note,
        "apex_score": apex_score,
        "global_gate": global_gate,
        "null_handling": null_handling,
        "all_flags_aggregated": apex.get("all_flags_aggregated", []),
        "flags_by_severity": apex.get("flags_by_severity", {}),
        "flags_by_origin_stage": apex.get("flags_by_origin_stage", {}),
        "_handoff_crossdoc_candidates": apex.get("_handoff_crossdoc_candidates", []),
        "_engine_meta": {
            "script": "scripts/compute_recommendations.py",
            "library_path": LIBRARY_REL_PATH,
            "decision_rule_source": "cbmi_chain_library_pattern.md — Three-recommendation vocabulary (per-point)",
            "vocabulary": f"capture chain (terminal: {TERMINAL})",
        },
    }

    # ---- null_handling: zero GCP-role points -> unable_to_assess, no band computation ----
    if null_handling.get("no_gcp_role_points"):
        base.update({
            "tier_interpretation": "unable_to_assess",
            "decision": "unable_to_assess",
            "decision_rationale": null_handling.get("condition_spec", "Zero GCP-role points."),
            "points": [],
            "subsystem_summary": {
                "n_points": 0, "good_points": 0, "review_points": 0, "resurvey_points": 0,
                "hard_gates_fired_by_point": {}, "worst_point_level": "unable_to_assess",
            },
            "indicator_rollup": {},
            "_caveats": [{"code": "NO_GCP_ROLE_POINTS", "detail": null_handling.get("condition_spec")}],
        })
        return base

    # ---- per-point x per-library-indicator ----
    points_out: list[dict] = []
    caveats: list[dict] = []
    hard_by_point: dict[str, list[str]] = {}
    rollup: dict[str, dict] = {iid: {"worst_score_across_points": None, "worst_level_across_points": "good",
                                     "n_points_in_review": 0, "n_points_in_resurvey": 0,
                                     "hard_gate_points": []} for iid in lib}
    counts = {"good": 0, "review": 0, "resurvey": 0}

    # pipeline indicators with no library entry (caveat once)
    pipe_ids = {t["indicator_id"] for p in s3b.get("points", []) for t in p["indicator_traces"].values()}
    for pid_ind in sorted(pipe_ids - set(lib)):
        caveats.append({"indicator_id": pid_ind, "code": "PIPELINE_INDICATOR_NOT_IN_LIBRARY"})

    for p in s3b.get("points", []):
        point_id = p["point_id"]
        traces = p.get("indicator_traces", {})
        ind_entries: list[dict] = []
        pt_hard: list[str] = []
        pt_worst_level_rank = 0

        for ind_id in sorted(lib):
            e = lib[ind_id]
            full_iid = f"{ind_id}_{e.get('fullName')}"
            trace = traces.get(full_iid)
            if trace is None:  # fallback by short id
                trace = next((t for t in traces.values() if t["indicator_id"] == ind_id), None)
            score = trace.get("score") if trace else None
            gate_triggered = bool(trace.get("gate_triggered")) if trace else False
            is_critical = bool(e.get("is_critical_path"))
            band = _lookup_band(score, e.get("bands", []))
            level = (band or {}).get("level") or "unknown"
            hard = is_critical and (gate_triggered or score == 0)

            ind_entries.append({
                "indicator_id": ind_id,
                "full_indicator_id": full_iid,
                "name": e.get("name"),
                "block": e.get("block"),
                "weight_in_block": e.get("weight"),
                "score": score,
                "is_critical_path": is_critical,
                "matched_band": ({"score_range": band["score_range"], "level": band["level"],
                                  "label": band.get("label")} if band else None),
                "verified_statement": _sub(e.get("verified_statement"), point_id) if level == "good" else None,
                "impact": _sub((band or {}).get("impact"), point_id) if level != "good" else None,
                "actions": _sub((band or {}).get("actions"), point_id) if level != "good" else None,
                "pipeline_band_matched": trace.get("band_matched") if trace else None,
                "pipeline_condition": trace.get("condition_evaluated") if trace else None,
                "pipeline_flags_raised": trace.get("flags_raised") if trace else [],
                "hard_gate_fired": hard,
                "gate_action_spec": trace.get("gate_action_spec") if trace else None,
            })

            # rollup
            r = rollup[ind_id]
            if score is not None and (r["worst_score_across_points"] is None or score < r["worst_score_across_points"]):
                r["worst_score_across_points"] = score
            if _level_rank(level) > _level_rank(r["worst_level_across_points"]):
                r["worst_level_across_points"] = level
            if level == "review":
                r["n_points_in_review"] += 1
            if level == "resurvey":
                r["n_points_in_resurvey"] += 1
            if hard:
                r["hard_gate_points"].append(point_id)
                pt_hard.append(ind_id)
            pt_worst_level_rank = max(pt_worst_level_rank, _level_rank(level))

            # caveats
            if score is not None and band is None:
                caveats.append({"point_id": point_id, "indicator_id": ind_id,
                                "code": "SCORE_NOT_IN_ANY_LIBRARY_BAND", "score": score})
            if trace is None:
                caveats.append({"point_id": point_id, "indicator_id": ind_id, "code": "TRACE_MISSING_FOR_POINT"})

        # per-point decision
        if pt_hard or pt_worst_level_rank >= _LEVEL_RANK["resurvey"]:
            pdecision = "resurvey"
        elif pt_worst_level_rank >= _LEVEL_RANK["review"]:
            pdecision = "review"
        else:
            pdecision = "good"
        counts[pdecision] += 1
        if pt_hard:
            hard_by_point[point_id] = pt_hard
        if pdecision == "resurvey":
            prat = (f"hard gate(s): {', '.join(pt_hard)}" if pt_hard else "resurvey-level band at this point")
        elif pdecision == "review":
            prat = "review-level band(s) at this point"
        else:
            prat = "all indicators in good bands"

        points_out.append({
            "point_id": point_id,
            "device_role": p.get("device_role"),
            "device_type": p.get("device_type"),
            "point_decision": pdecision,
            "point_rationale": prat,
            "indicators": ind_entries,
        })

    # ---- chain-level decision ----
    any_hard = bool(hard_by_point)
    if global_gate.get("triggered") or any_hard or counts["resurvey"] > 0:
        decision = "resurvey_recommended"
        bits = []
        if global_gate.get("triggered"):
            bits.append("global gate fired")
        if any_hard:
            bits.append(f"hard gate(s) at {', '.join(sorted(hard_by_point))}")
        if counts["resurvey"] > 0:
            bits.append(f"{counts['resurvey']} point(s) at resurvey level")
        rationale = "; ".join(bits)
    elif counts["review"] > 0:
        decision = "review_recommended"
        rationale = f"{counts['review']} point(s) at review level, no hard gate"
    else:
        decision = "good_to_go"
        rationale = "all points in good bands; no hard gate fired"

    worst_decision = max(("good", *(pp["point_decision"] for pp in points_out)),
                         key=lambda d: _DECISION_RANK.get(d, 0))

    base.update({
        "tier_interpretation": _tier(apex_score),
        "decision": decision,
        "decision_rationale": rationale,
        "points": points_out,
        "subsystem_summary": {
            "n_points": len(points_out),
            "good_points": counts["good"],
            "review_points": counts["review"],
            "resurvey_points": counts["resurvey"],
            "hard_gates_fired_by_point": hard_by_point,
            "worst_point_level": worst_decision,
        },
        "indicator_rollup": rollup,
        "_caveats": caveats,
    })
    return base


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: compute_recommendations.py <paths.json>", file=sys.stderr)
        return 2
    config_path = Path(argv[1]).resolve()
    root = config_path.parent
    config = _load(config_path)

    s3b_path = root / config["outputs"]["stage3_indicators"]
    apex_path = root / config["outputs"]["stage3_gcp_score"]
    library_path = root / LIBRARY_REL_PATH
    out_path = root / "outputs" / "07_recommendations.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not apex_path.exists() or not s3b_path.exists():
        unable = {
            "subsystem": config.get("subsystem"), "generated_at": _iso_now(),
            "decision": "unable_to_assess",
            "decision_rationale": (f"Pipeline did not produce {apex_path.name} or {s3b_path.name}. "
                                   "Run scripts/run_pipeline.py paths.json first."),
            "_caveats": [{"code": "PIPELINE_OUTPUTS_MISSING"}],
        }
        out_path.write_text(json.dumps(unable, indent=2, sort_keys=True), encoding="utf-8")
        print("decision = unable_to_assess (pipeline outputs missing)")
        return 1

    if not library_path.exists():
        print(f"library not found at {library_path}", file=sys.stderr)
        return 3

    result = compute(config, root, _load(s3b_path), _load(apex_path), _load(library_path))
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

    try:
        rel = out_path.relative_to(root)
    except ValueError:
        rel = out_path
    ss = result.get("subsystem_summary", {})
    print(f"apex_score = {result['apex_score']}  tier = {result['tier_interpretation']}")
    print(f"decision   = {result['decision']}")
    print(f"             {result['decision_rationale']}")
    print(f"points     = {ss.get('n_points')}  good={ss.get('good_points')} "
          f"review={ss.get('review_points')} resurvey={ss.get('resurvey_points')}")
    print(f"hard_gates_fired_by_point = {ss.get('hard_gates_fired_by_point')}")
    print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
