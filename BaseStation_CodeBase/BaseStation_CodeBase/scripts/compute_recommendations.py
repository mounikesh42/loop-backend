#!/usr/bin/env python3
"""Compute customer-facing recommendations from base station pipeline outputs.

POST-PIPELINE STANDALONE — does NOT modify any other script.

Inputs:
  - outputs/04_indicators.json          (per-indicator scores + bands)
  - outputs/06_base_station_score.json  (apex + global gate + aggregated flags)
  - BaseStation_Recommendations/base_station_indicator_library_v2_1.json (Tier 2)

Output:
  - outputs/07_recommendations.json     (chain decision + per-indicator
                                         verified_statement / impact / actions)

Decision rules (per cbmi_chain_library_pattern.md):
  - resurvey_recommended  ← any hard gate fired (critical-path indicator at 0)
                          OR any indicator landed in a band with level "resurvey"
  - review_recommended    ← any indicator in a band with level "review"
                          (and no hard gate / no resurvey-level band)
  - good_to_go            ← all indicators in good or minor bands

Tier interpretation (per v2.1 spec base_station_score.tier_interpretation):
  - Gold     90-100
  - Silver   75-89
  - Bronze   60-74
  - Marginal 40-59
  - Poor     <40

Usage:
    python3 scripts/compute_recommendations.py paths.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LIBRARY_REL_PATH = "BaseStation_Recommendations/base_station_indicator_library_v2_1.json"

TIER_BANDS = [
    (90, 100, "Gold"),
    (75, 89,  "Silver"),
    (60, 74,  "Bronze"),
    (40, 59,  "Marginal"),
    (0,  39,  "Poor"),
]


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tier(score: float | int | None) -> str | None:
    if score is None:
        return None
    for lo, hi, name in TIER_BANDS:
        if lo <= score <= hi:
            return f"{name} ({lo}-{hi})"
    return None


def _lookup_band(score: float | int | None, bands: list[dict]) -> dict | None:
    """Inclusive [lo, hi] range lookup. Returns the first band that contains
    the score. (Library is internally non-overlapping after the boundary
    patch, so first-match is also unique-match.)"""
    if score is None:
        return None
    for b in bands:
        rng = b.get("score_range") or [None, None]
        lo, hi = rng[0], rng[1]
        if lo is None or hi is None:
            continue
        if lo <= score <= hi:
            return b
    return None


def _level_severity_rank(level: str | None) -> int:
    """Higher rank = worse. Used to find the worst level across indicators."""
    return {
        "good":     0,
        "minor":    1,
        "review":   2,
        "resurvey": 3,
    }.get((level or "").lower(), 0)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def compute(config: dict, project_root: Path,
            stage3b: dict, apex_env: dict, library: dict) -> dict:
    """Build the recommendations object from in-memory inputs."""
    s3b_data = stage3b["data"]
    apex_data = apex_env["data"]
    library_indicators = library["library"]
    apex_score = apex_data.get("base_station_score")
    global_gate = apex_data.get("global_gate", {})

    # ---- per-indicator analysis ----
    per_indicator: list[dict] = []
    caveats: list[dict] = []
    counts = {"good": 0, "minor": 0, "review": 0, "resurvey": 0, "unknown": 0}
    hard_gates_fired: list[str] = []
    worst_level = "good"
    worst_rank = 0

    # All flag IDs raised at any prior stage, indexed by their _origin_derived_field
    # (Stage 3a composite/handoff origins) and by raised-stage flag set.
    flags_by_indicator: dict[str, list[str]] = {}
    for f in apex_data.get("all_flags_aggregated", []):
        # Indicators map to flags via library's `flag` field; but the pipeline
        # also surfaces _origin_derived_field for each aggregated flag. For
        # indicator-level rollup, we use the indicator-trace's flags_raised
        # list (Stage 3b output) — see below.
        pass

    indicator_trace_by_id: dict[str, dict] = {}
    for k, t in s3b_data.get("indicator_traces", {}).items():
        indicator_trace_by_id[t["indicator_id"]] = t

    for ind_id in sorted(library_indicators.keys()):
        lib_ind = library_indicators[ind_id]
        trace = indicator_trace_by_id.get(ind_id)
        score = trace.get("score") if trace else None
        gate_triggered = bool(trace.get("gate_triggered")) if trace else False
        is_critical = bool(lib_ind.get("is_critical_path"))
        bands = lib_ind.get("bands", [])
        band = _lookup_band(score, bands)
        level = (band or {}).get("level") or "unknown"

        entry: dict[str, Any] = {
            "indicator_id": ind_id,
            "name":          lib_ind.get("name"),
            "full_name":     lib_ind.get("fullName"),
            "block":         lib_ind.get("block"),
            "weight_in_block": lib_ind.get("weight"),
            "score":         score,
            "is_critical_path": is_critical,
            "matched_band": (
                {
                    "score_range": band["score_range"],
                    "level":       band["level"],
                    "label":       band.get("label"),
                }
                if band else None
            ),
            # Tier 2 customer-voice text — always populated for the matching path
            "verified_statement": (lib_ind.get("verified_statement")
                                   if level == "good" else None),
            "impact":            (band or {}).get("impact"),
            "actions":           (band or {}).get("actions"),
            # Pipeline-side provenance
            "pipeline_band_matched":  trace.get("band_matched") if trace else None,
            "pipeline_condition":     trace.get("condition_evaluated") if trace else None,
            "pipeline_flags_raised":  trace.get("flags_raised") if trace else [],
            # Hard-gate handling
            "hard_gate_fired": gate_triggered and is_critical,
            "gate_action_spec": trace.get("gate_action_spec") if trace else None,
        }
        per_indicator.append(entry)

        # Aggregate
        counts[level if level in counts else "unknown"] += 1
        if entry["hard_gate_fired"]:
            hard_gates_fired.append(ind_id)
        rank = _level_severity_rank(level)
        if rank > worst_rank:
            worst_rank = rank
            worst_level = level

        # Caveat tracking (e.g. score outside any defined band)
        if score is not None and band is None:
            caveats.append({
                "indicator_id": ind_id,
                "code": "SCORE_NOT_IN_ANY_LIBRARY_BAND",
                "score": score,
                "library_ranges": [b.get("score_range") for b in bands],
            })
        if trace is None:
            caveats.append({
                "indicator_id": ind_id,
                "code": "INDICATOR_NOT_PRESENT_IN_PIPELINE_OUTPUT",
            })

    # ---- chain-level decision ----
    if hard_gates_fired or counts["resurvey"] > 0 or global_gate.get("triggered"):
        decision = "resurvey_recommended"
    elif counts["review"] > 0:
        decision = "review_recommended"
    else:
        decision = "good_to_go"

    # Rationale string
    if decision == "resurvey_recommended":
        bits: list[str] = []
        if global_gate.get("triggered"):
            bits.append("global gate fired")
        if hard_gates_fired:
            bits.append(f"hard gate(s): {', '.join(hard_gates_fired)}")
        if counts["resurvey"] > 0 and counts["resurvey"] != len(hard_gates_fired):
            bits.append(f"{counts['resurvey']} resurvey-level finding(s)")
        rationale = "; ".join(bits) or "resurvey conditions met"
    elif decision == "review_recommended":
        rationale = f"{counts['review']} review-level finding(s), no hard gate"
    else:
        notes = []
        if counts["minor"] > 0:
            notes.append(f"{counts['minor']} minor hygiene finding(s) (audit-only)")
        rationale = "; ".join(notes) if notes else "all indicators in good bands; no hard gate fired"

    # ---- assemble output ----
    spec_version_note = None
    pipeline_spec_version = apex_env.get("spec_version")
    library_version = library["_meta"].get("version")
    if pipeline_spec_version and library_version and pipeline_spec_version not in library_version:
        spec_version_note = (
            f"Pipeline ran spec v{pipeline_spec_version}; library targets {library_version}. "
            "Severity labels on aggregated flags reflect the pipeline's spec version, not the library. "
            "Decision logic uses the library (Tier 2) — band levels are authoritative for the decision."
        )

    return {
        "subsystem": config.get("subsystem", "base_station_ppk"),
        "generated_at": _iso_now(),
        "spec_version":    pipeline_spec_version,
        "library_version": library_version,
        "library_authorship_status": library["_meta"].get("text_authorship_status"),
        "_spec_version_note": spec_version_note,

        # ---- headline ----
        "apex_score":            apex_score,
        "tier_interpretation":   _tier(apex_score),
        "decision":              decision,
        "decision_rationale":    rationale,
        "verification_status_field": apex_data.get("stage3d_meta", {}).get("apex_score_id"),

        # ---- global gate carried through ----
        "global_gate":           global_gate,

        # ---- per-indicator detail ----
        "indicators": per_indicator,

        # ---- rollup ----
        "summary": {
            "good_count":       counts["good"],
            "minor_count":      counts["minor"],
            "review_count":     counts["review"],
            "resurvey_count":   counts["resurvey"],
            "unknown_count":    counts["unknown"],
            "hard_gates_fired": hard_gates_fired,
            "worst_band_level": worst_level,
        },

        # ---- preserved from apex ----
        "all_flags_aggregated": apex_data.get("all_flags_aggregated", []),
        "flags_by_severity":    apex_data.get("flags_by_severity", {}),
        "flags_by_origin_stage": apex_data.get("flags_by_origin_stage", {}),
        "_handoff_crossdoc_candidates": apex_data.get("_handoff_crossdoc_candidates", []),

        # ---- diagnostics ----
        "_caveats": caveats,
        "_engine_meta": {
            "script": "scripts/compute_recommendations.py",
            "library_path": LIBRARY_REL_PATH,
            "decision_rule_source": "cbmi_chain_library_pattern.md — Three-recommendation vocabulary",
        },
    }


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: compute_recommendations.py <paths.json>", file=sys.stderr)
        return 2

    config_path = Path(argv[1]).resolve()
    root = config_path.parent
    config = _load(config_path)

    # Input artifacts
    s3b_path = root / config["outputs"]["stage3_indicators"]
    apex_path = root / config["outputs"]["stage3_base_score"]
    library_path = root / LIBRARY_REL_PATH

    # Pipeline-incomplete edge case
    if not apex_path.exists() or not s3b_path.exists():
        out_path = root / "outputs" / "07_recommendations.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        unable = {
            "subsystem": config.get("subsystem"),
            "generated_at": _iso_now(),
            "decision": "unable_to_assess",
            "decision_rationale": (
                f"Pipeline did not produce {apex_path.name} or {s3b_path.name}. "
                "Run the pipeline (`python3 scripts/run_pipeline.py paths.json`) "
                "before computing recommendations."
            ),
            "_caveats": [{"code": "PIPELINE_OUTPUTS_MISSING"}],
        }
        out_path.write_text(json.dumps(unable, indent=2, sort_keys=True), encoding="utf-8")
        print(f"decision = unable_to_assess (pipeline outputs missing)")
        print(f"wrote {out_path.relative_to(root)}")
        return 1

    if not library_path.exists():
        print(f"library not found at {library_path}", file=sys.stderr)
        return 3

    stage3b = _load(s3b_path)
    apex_env = _load(apex_path)
    library = _load(library_path)

    result = compute(config, root, stage3b, apex_env, library)

    out_path = root / "outputs" / "07_recommendations.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

    # Headline + path; guard relative_to
    try:
        rel = out_path.relative_to(root)
    except ValueError:
        rel = out_path
    print(f"apex_score = {result['apex_score']}  tier = {result['tier_interpretation']}")
    print(f"decision   = {result['decision']}")
    print(f"            {result['decision_rationale']}")
    print(f"summary    = good={result['summary']['good_count']}  "
          f"minor={result['summary']['minor_count']}  "
          f"review={result['summary']['review_count']}  "
          f"resurvey={result['summary']['resurvey_count']}  "
          f"hard_gates={result['summary']['hard_gates_fired']}")
    print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
