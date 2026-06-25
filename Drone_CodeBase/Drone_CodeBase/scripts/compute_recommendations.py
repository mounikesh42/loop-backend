#!/usr/bin/env python3
"""Drone PPK — post-pipeline recommendations engine.

Reads:  outputs/04_indicators.json
        outputs/06_drone_score.json
        Drone_Recommendations/drone_indicator_library_v2_1.json
Writes: outputs/07_recommendations.json

Decision rule (per cbmi_chain_library_pattern.md "Three-recommendation vocabulary"):
  capture chain (drone) terminal word = 'resurvey_recommended'

  if any (is_critical_path AND gate_triggered)  OR  worst band level == 'resurvey'
      OR global_gate.triggered:                                  → resurvey_recommended
  elif any band level == 'review':                               → review_recommended
  else:                                                          → good_to_go

`minor` bands are audit-only — they do not drive the decision.

Strictly post-pipeline. Does NOT touch any pipeline script.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


CAPTURE_TERMINAL = "resurvey_recommended"


# ---------------------------------------------------------------------------
# Library / pipeline join
# ---------------------------------------------------------------------------

def _lookup_band(score, bands):
    """Inclusive [lo, hi] match; top band wins ties (bands ordered worst→best per
    library, but score_range overlaps shouldn't exist after STEP A patch). Null-band
    entries (score_range = [None, None]) are skipped here — caller decides whether
    to invoke them for special states like API_UNAVAILABLE (Q-DRONE-4)."""
    matches = []
    for b in bands:
        rng = b.get("score_range")
        if not rng or rng == [None, None] or len(rng) != 2:
            continue
        lo, hi = rng
        if lo is None or hi is None:
            continue
        if lo <= score <= hi:
            matches.append(b)
    if not matches:
        return None
    # If multiple matches: prefer the highest-level band (i.e. "good" over "review"
    # over "minor"/"resurvey") to honour "top band wins ties".
    level_rank = {"good": 0, "minor": 1, "review": 2, "resurvey": 3}
    return min(matches, key=lambda b: level_rank.get(b.get("level"), 99))


def _build_indicator_row(iid, lib_entry, trace):
    """One row of indicators[] in the output JSON. iid is the library id."""
    score = (trace or {}).get("score")
    bands = lib_entry.get("bands", [])
    band = _lookup_band(score, bands) if score is not None else None
    is_cp = bool(lib_entry.get("is_critical_path"))
    pipeline_flags = [f.get("flag_name") for f in (trace or {}).get("flags_raised", [])]
    # Hard-gate detection: critical-path indicator that scored 0 (per spec) AND we
    # have a resurvey band for it (which should be the case after STEP A patch).
    hard_gate_fired = is_cp and score == 0 and (band or {}).get("level") == "resurvey"
    row = {
        "indicator_id": iid,
        "name": lib_entry.get("name"),
        "full_name": lib_entry.get("fullName"),
        "block": lib_entry.get("block"),
        "weight_in_block": lib_entry.get("weight"),
        "score": score,
        "is_critical_path": is_cp,
        "matched_band": (
            {"score_range": band["score_range"], "level": band.get("level"),
             "label": band.get("label"), "flag": band.get("flag")}
            if band else None
        ),
        "verified_statement": None,
        "impact": None,
        "actions": None,
        "pipeline_band_matched": (trace or {}).get("band_matched"),
        "pipeline_condition":   (trace or {}).get("condition"),
        "pipeline_flags_raised": pipeline_flags,
        "hard_gate_fired": hard_gate_fired,
        "gate_action_spec": None,  # drone indicator traces don't carry gate_action_spec
    }
    if band is None:
        return row  # caller adds the caveat
    if band.get("level") == "good":
        row["verified_statement"] = lib_entry.get("verified_statement")
    else:
        row["impact"]  = band.get("impact")
        row["actions"] = band.get("actions")
    return row


# ---------------------------------------------------------------------------
# Decision rule
# ---------------------------------------------------------------------------

LEVEL_TO_DECISION_RANK = {"good": 0, "minor": 0, "review": 1, "resurvey": 2}


def _worst_decision_level(rows, apex_gate_triggered: bool):
    rank = 0
    if apex_gate_triggered:
        rank = max(rank, 2)
    for r in rows:
        if r["hard_gate_fired"]:
            rank = max(rank, 2)
        lvl = (r.get("matched_band") or {}).get("level")
        rank = max(rank, LEVEL_TO_DECISION_RANK.get(lvl, 0))
    return rank


def _decision(rank: int) -> str:
    return {0: "good_to_go", 1: "review_recommended", 2: CAPTURE_TERMINAL}[rank]


def _decision_rationale(rows, apex_gate_triggered: bool, decision: str):
    if decision == CAPTURE_TERMINAL:
        if apex_gate_triggered:
            return "Global gate triggered — apex score forced to 0; critical recapture required."
        hg = [r["indicator_id"] for r in rows if r["hard_gate_fired"]]
        if hg:
            return f"Hard-gate indicator(s) scored 0: {', '.join(hg)}. Resurvey required."
        rs = [r["indicator_id"] for r in rows if (r.get("matched_band") or {}).get("level") == "resurvey"]
        return f"Indicator(s) in resurvey band: {', '.join(rs)}."
    if decision == "review_recommended":
        rv = [r["indicator_id"] for r in rows if (r.get("matched_band") or {}).get("level") == "review"]
        return f"{len(rv)} indicator(s) in review band: {', '.join(rv[:5])}{'…' if len(rv) > 5 else ''}."
    return "All indicators in good or minor (audit-only) bands."


# ---------------------------------------------------------------------------
# Tier interpretation
# ---------------------------------------------------------------------------

DEFAULT_TIERS = [
    ("Gold (90-100)",      90, 100),
    ("Silver (75-89)",     75,  89),
    ("Bronze (60-74)",     60,  74),
    ("Marginal (40-59)",   40,  59),
    ("Poor (<40)",          0,  39),
]


def _tier(score) -> Optional[str]:
    if score is None:
        return None
    for label, lo, hi in DEFAULT_TIERS:
        if lo <= score <= hi:
            return label
    return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def compute(s3b: dict, apex: dict, lib: dict) -> dict:
    apex_data   = apex["data"]
    apex_score  = apex_data.get("drone_score")
    spec_ver    = apex.get("spec_version")
    lib_ver     = lib.get("_meta", {}).get("version")
    auth_status = lib.get("_meta", {}).get("text_authorship_status")

    # Pipeline indicator traces
    pipe_traces = {t["indicator_id"]: t for t in s3b["data"].get("indicators", [])}

    # Build one row per LIBRARY indicator
    rows, caveats = [], []
    for iid in lib["library"]:
        lib_entry = lib["library"][iid]
        trace = pipe_traces.get(iid)
        if trace is None:
            caveats.append({
                "indicator_id": iid,
                "code": "INDICATOR_NOT_PRESENT_IN_PIPELINE_OUTPUT",
                "library_bands": [b.get("score_range") for b in lib_entry.get("bands", [])],
            })
            # Surface a placeholder row so the output is still complete
            rows.append({
                "indicator_id": iid, "name": lib_entry.get("name"),
                "full_name": lib_entry.get("fullName"), "block": lib_entry.get("block"),
                "weight_in_block": lib_entry.get("weight"),
                "score": None, "is_critical_path": bool(lib_entry.get("is_critical_path")),
                "matched_band": None,
                "verified_statement": None, "impact": None, "actions": None,
                "pipeline_band_matched": None, "pipeline_condition": None,
                "pipeline_flags_raised": [], "hard_gate_fired": False,
                "gate_action_spec": None,
            })
            continue
        row = _build_indicator_row(iid, lib_entry, trace)
        if row["score"] is not None and row["matched_band"] is None:
            caveats.append({
                "indicator_id": iid,
                "code": "SCORE_NOT_IN_ANY_LIBRARY_BAND",
                "score": row["score"],
                "library_bands": [b.get("score_range") for b in lib_entry.get("bands", [])],
            })
        rows.append(row)

    # Catch pipeline indicators with no library entry (caveat-only — not a row)
    pipe_only = set(pipe_traces) - set(lib["library"])
    for iid in sorted(pipe_only):
        caveats.append({
            "indicator_id": iid,
            "code": "PIPELINE_INDICATOR_NOT_IN_LIBRARY",
            "score": pipe_traces[iid].get("score"),
        })

    # Chain-level decision
    apex_gate = bool(apex_data.get("global_gate_triggered"))
    rank = _worst_decision_level(rows, apex_gate)
    decision = _decision(rank)
    decision_rationale = _decision_rationale(rows, apex_gate, decision)

    # Summary counts
    summary = {
        "good_count":     sum(1 for r in rows if (r.get("matched_band") or {}).get("level") == "good"),
        "minor_count":    sum(1 for r in rows if (r.get("matched_band") or {}).get("level") == "minor"),
        "review_count":   sum(1 for r in rows if (r.get("matched_band") or {}).get("level") == "review"),
        "resurvey_count": sum(1 for r in rows if (r.get("matched_band") or {}).get("level") == "resurvey"),
        "unknown_count":  sum(1 for r in rows if r.get("matched_band") is None),
        "hard_gates_fired": [r["indicator_id"] for r in rows if r["hard_gate_fired"]],
        "worst_band_level": ["good", "minor", "review", "resurvey"][rank if rank < 3 else 3] if rank else "good",
    }

    # Spec-version note (mismatch is informational)
    spec_note = None
    if spec_ver and lib_ver and str(spec_ver) not in str(lib_ver):
        spec_note = (f"Pipeline ran on spec_version={spec_ver}; library is {lib_ver}. "
                     f"Bands authoritative; numerical scoring uses pipeline.")

    return {
        "subsystem":                 "drone_ppk",
        "generated_at":              datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spec_version":              spec_ver,
        "library_version":           lib_ver,
        "library_authorship_status": auth_status,
        "_spec_version_note":        spec_note,

        "apex_score":                apex_score,
        "tier_interpretation":       _tier(apex_score),
        "decision":                  decision,
        "decision_rationale":        decision_rationale,
        "verification_status_field": None,  # spec sheet 08 metadata doesn't carry it

        "global_gate": {
            "triggered":     apex_gate,
            "condition":     apex_data.get("global_gate_condition"),
            "action":        apex_data.get("global_gate_action"),
            "observed_block_score": apex_data.get("block_contributions", {})
                                              .get("BB_IMG_CAPTURE", {}).get("block_score"),
        },

        "indicators": rows,
        "summary": summary,

        "all_flags_aggregated":         apex_data.get("all_flags_aggregated", []),
        "flags_by_severity":            apex_data.get("all_flags_by_severity", {}),
        "flags_by_origin_stage":        _group_flags_by_origin(apex_data.get("all_flags_aggregated", [])),
        "_handoff_crossdoc_candidates": apex_data.get("_handoff_crossdoc_candidates", []),

        "_caveats": caveats,
        "_engine_meta": {
            "script":                "scripts/compute_recommendations.py",
            "library_path":          "Drone_Recommendations/drone_indicator_library_v2_1.json",
            "decision_rule_source":  "cbmi_chain_library_pattern.md — Three-recommendation vocabulary",
            "vocabulary":            "capture chain (terminal word: resurvey_recommended)",
        },
    }


def _group_flags_by_origin(flags):
    out = {}
    for f in flags:
        out.setdefault(f.get("_origin_stage", "(unknown)"), []).append(f.get("flag_name"))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: compute_recommendations.py <paths.json>", file=sys.stderr)
        return 2

    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())

    lib_path = project_root / "Drone_Recommendations" / "drone_indicator_library_v2_1.json"
    if not lib_path.exists():
        print(f"ERROR: library not found at {lib_path}", file=sys.stderr)
        return 1

    s3b_path  = project_root / config["outputs"]["stage3_indicators"]
    apex_path = project_root / config["outputs"]["stage3_drone_score"]

    if not apex_path.exists():
        # Emit unable_to_assess artifact rather than crashing
        env = {
            "subsystem": "drone_ppk",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision": "unable_to_assess",
            "decision_rationale": f"pipeline apex output missing at {apex_path}",
            "apex_score": None,
            "indicators": [],
            "summary": {"good_count": 0, "minor_count": 0, "review_count": 0,
                        "resurvey_count": 0, "unknown_count": 0,
                        "hard_gates_fired": [], "worst_band_level": "unknown"},
            "_caveats": [{"code": "PIPELINE_NOT_RUN", "details": str(apex_path)}],
        }
        out_path = project_root / "outputs" / "07_recommendations.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(env, indent=2, sort_keys=False, default=str) + "\n")
        print(f"decision: unable_to_assess (no pipeline output)", file=sys.stderr)
        return 1

    s3b  = json.loads(s3b_path.read_text())
    apex = json.loads(apex_path.read_text())
    lib  = json.loads(lib_path.read_text())

    env = compute(s3b, apex, lib)

    out_path = project_root / "outputs" / "07_recommendations.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(env, indent=2, sort_keys=False, default=str) + "\n")

    s = env["summary"]
    try:
        rel = out_path.relative_to(project_root)
    except ValueError:
        rel = out_path
    print(f"apex_score:           {env['apex_score']}")
    print(f"tier:                 {env['tier_interpretation']}")
    print(f"decision:             {env['decision']}")
    print(f"decision_rationale:   {env['decision_rationale']}")
    print(f"summary:              good={s['good_count']}  minor={s['minor_count']}  "
          f"review={s['review_count']}  resurvey={s['resurvey_count']}  unknown={s['unknown_count']}  "
          f"hard_gates={len(s['hard_gates_fired'])}")
    print(f"caveats:              {len(env['_caveats'])}")
    print(f"wrote:                {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
