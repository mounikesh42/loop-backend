#!/usr/bin/env python3
"""Stage 2 — merge per-parser outputs into a single source-fields envelope.

Calls each parser, flattens the 36 L1F fields, and produces:
  - data.source_fields                 — flat dict, all 36 L1F_BASE_* fields
  - data.per_source_parser_meta        — keyed by SRC_BASE_* file_id
  - data.merge_meta                    — counts, warnings, notes, timing
  - data._flags_raised_stage2          — flags this stage owns (empty for base)
  - data._handoff_crossdoc_candidates  — flags deferred to a cross-bundle stage

Per spec sheet 02, every L1F_BASE_* field is owned by exactly one source, so
no cross-parser L1F computations happen here. The four cross-source consistency
checks (height agreement / type match / log match / truncation) live at Stage
3a (L2D level) per spec sheet 03; this merge surfaces nothing about them.

Per sheet 07, no flag has `raised_at_stage = pre_score_ingestion`. So
data._flags_raised_stage2 is empty by design — kept as an array so that the
aggregation pattern in run_pipeline.py / compute_<apex>.py stays uniform.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


# ---- RINEX OBS discovery ----------------------------------------------------

def _find_rinex_obs(folder: Path) -> Path | None:
    """Return the most plausible RINEX observation file in this folder.

    Filter by extension first; sniff first line of any matching candidate to
    confirm it's an OBSERVATION DATA file. Largest matching file wins.
    """
    if not folder.exists():
        return None
    candidates: list[Path] = []
    for p in folder.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        sfx = p.suffix.lower()
        plausible = (
            sfx in (".obs", ".o", ".rnx")
            or (len(sfx) == 4 and sfx[1:3].isdigit() and sfx[3] == "o")
        )
        if not plausible:
            try:
                with p.open("r", encoding="ascii", errors="replace") as fh:
                    first = fh.readline()
                if "OBSERVATION DATA" not in first:
                    continue
            except OSError:
                continue
        else:
            try:
                with p.open("r", encoding="ascii", errors="replace") as fh:
                    first = fh.readline()
                if "OBSERVATION DATA" not in first and "OBS" not in first[20:40].upper():
                    # Extension matched but content disagrees — skip.
                    continue
            except OSError:
                continue
        candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


# ---- parser orchestration ---------------------------------------------------

def _import_parsers():
    parsers_dir = Path(__file__).resolve().parent / "parsers"
    if str(parsers_dir) not in sys.path:
        sys.path.insert(0, str(parsers_dir))
    import parse_rinex  # type: ignore
    import parse_oplog  # type: ignore
    import parse_user_input  # type: ignore
    return parse_rinex, parse_oplog, parse_user_input


def _resolve_input_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def run(config: dict[str, Any], project_root: Path, spec: dict[str, Any]) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)

    parse_rinex, parse_oplog, parse_user_input = _import_parsers()

    inputs = config["inputs"]
    rinex_folder = _resolve_input_path(project_root, inputs["rinex_folder"])
    oplog_folder = _resolve_input_path(project_root, inputs["operator_log_folder"])
    form_folder = _resolve_input_path(project_root, inputs["user_input_folder"])

    rinex_obs_path = _find_rinex_obs(rinex_folder)

    # ---- run parsers ----
    per_parser_timing: dict[str, float] = {}
    merge_warnings: list[dict[str, Any]] = []

    if rinex_obs_path is None:
        merge_warnings.append({
            "code": "RINEX_OBS_FOR_PARSER_NOT_FOUND",
            "source_file_id": "SRC_BASE_RINEX",
            "detail": f"No RINEX observation file found in {rinex_folder}.",
        })
        rinex_result = {
            "fields": {
                f"L1F_BASE_{i:03d}_placeholder": None
                for i in range(1, 18)
            },
            "parser_meta": {
                "parser_id": "parse_rinex",
                "parser_version": None,
                "source_file_id": "SRC_BASE_RINEX",
                "source_file_name": None,
                "instance_found": False,
                "fields_provided": [],
                "field_sources": {},
                "notes": ["No OBS file available — parser not run."],
                "flags_raised": [],
            },
        }
    else:
        t = datetime.now(timezone.utc)
        rinex_result = parse_rinex.parse(rinex_obs_path, project_root)
        per_parser_timing["SRC_BASE_RINEX"] = round((datetime.now(timezone.utc) - t).total_seconds(), 3)

    t = datetime.now(timezone.utc)
    oplog_result = parse_oplog.parse(oplog_folder, project_root)
    per_parser_timing["SRC_BASE_OPLOG"] = round((datetime.now(timezone.utc) - t).total_seconds(), 3)

    t = datetime.now(timezone.utc)
    form_result = parse_user_input.parse(form_folder, project_root)
    per_parser_timing["SRC_BASE_FORM"] = round((datetime.now(timezone.utc) - t).total_seconds(), 3)

    # ---- merge fields ----
    all_fields: dict[str, Any] = {}
    field_owner: dict[str, str] = {}
    overlaps: list[dict[str, str]] = []
    parser_results = [
        ("SRC_BASE_RINEX", rinex_result),
        ("SRC_BASE_OPLOG", oplog_result),
        ("SRC_BASE_FORM", form_result),
    ]
    for src_id, result in parser_results:
        for k, v in result["fields"].items():
            if k in all_fields:
                overlaps.append({
                    "l1f_key": k,
                    "first_owner": field_owner[k],
                    "second_owner": src_id,
                })
            else:
                all_fields[k] = v
                field_owner[k] = src_id

    if overlaps:
        merge_warnings.append({
            "code": "L1F_FIELD_OWNERSHIP_OVERLAP",
            "detail": overlaps,
        })

    # ---- count audit ----
    expected_count = spec["_meta"]["counts"]["source_fields"]
    produced_count = sum(
        1 for k in all_fields if not k.endswith("_placeholder")
    )
    if produced_count != expected_count:
        merge_warnings.append({
            "code": "L1F_FIELD_COUNT_MISMATCH",
            "expected": expected_count,
            "produced": produced_count,
        })

    # ---- aggregate spec-compliance audit per source ----
    expected_field_ids_by_source: dict[str, list[str]] = {}
    for sf in spec["source_fields"]:
        expected_field_ids_by_source.setdefault(sf["file_id"], []).append(sf["field_id"])

    per_source_audit: dict[str, Any] = {}
    for src_id, result in parser_results:
        expected_ids = set(expected_field_ids_by_source.get(src_id, []))
        # The parser emits keys like "L1F_BASE_001_marker_name"; strip the suffix.
        produced_ids = set()
        for k in result["fields"].keys():
            if k.endswith("_placeholder"):
                continue
            parts = k.split("_", 3)
            if len(parts) >= 3:
                produced_ids.add("_".join(parts[:3]))
        per_source_audit[src_id] = {
            "expected_count": len(expected_ids),
            "produced_count": len(produced_ids),
            "missing_ids": sorted(expected_ids - produced_ids),
            "extra_ids": sorted(produced_ids - expected_ids),
            "instance_found": result["parser_meta"].get("instance_found"),
            "source_file_name": result["parser_meta"].get("source_file_name"),
            "wall_time_sec": per_parser_timing.get(src_id),
        }
        if expected_ids - produced_ids:
            merge_warnings.append({
                "code": "L1F_FIELDS_MISSING_FROM_PARSER",
                "source_file_id": src_id,
                "missing": sorted(expected_ids - produced_ids),
            })

    # ---- per-source parser_meta surfaced verbatim ----
    per_source_parser_meta = {
        src_id: result["parser_meta"] for src_id, result in parser_results
    }

    # ---- flags this stage owns ----
    # Per sheet 07, no flag has raised_at_stage = pre_score_ingestion.
    # Aggregate any parser-level flags (should be empty for all 3 parsers).
    flags_raised_stage2: list[dict[str, Any]] = []
    for src_id, result in parser_results:
        for flag in result["parser_meta"].get("flags_raised", []):
            flags_raised_stage2.append({
                **flag,
                "_origin_stage": "stage2_merge_or_parser",
                "_origin_source": src_id,
            })

    # ---- handoff_crossdoc flags: not closeable at Stage 1 base alone ----
    handoff_crossdoc_candidates = []
    for f in spec.get("flags", []):
        if f.get("raised_at_stage") == "handoff_crossdoc":
            handoff_crossdoc_candidates.append({
                "flag_id": f["flag_id"],
                "flag_name": f["flag_name"],
                "severity": f["severity"],
                "covers_problems": f.get("covers_problems"),
                "status": "deferred_no_rover_bundle_at_stage1",
                "note": (
                    "Cross-document flag — needs rover bundle to evaluate. "
                    "Captured for downstream pre_processing cross-bundle audit."
                ),
            })

    merge_notes: list[str] = []
    merge_notes.append(
        "Per spec sheet 02, every L1F_BASE_* field is owned by exactly one source; "
        "no cross-parser L1F field computations performed at merge."
    )
    merge_notes.append(
        "Cross-source consistency checks (L2D_BASE_017 antenna_type_match, "
        "L2D_BASE_018 antenna_height_agreement, L2D_BASE_021 log_match_check, "
        "L2D_BASE_022 truncation_check) live at Stage 3a per spec sheet 03 — not here."
    )
    merge_notes.append(
        "Per sheet 07 raised_at_stage column, no flag is owned by pre_score_ingestion; "
        "_flags_raised_stage2 is empty by design (uniform shape preserved for aggregation)."
    )
    merge_notes.append(
        f"{len(handoff_crossdoc_candidates)} handoff_crossdoc flags listed as "
        "candidates pending the cross-bundle audit (rover/drone document)."
    )

    finished_at = datetime.now(timezone.utc)

    return {
        "source_fields": dict(sorted(all_fields.items())),
        "per_source_parser_meta": per_source_parser_meta,
        "per_source_audit": per_source_audit,
        "merge_meta": {
            "expected_field_count": expected_count,
            "produced_field_count": produced_count,
            "rinex_obs_used": rinex_obs_path.name if rinex_obs_path else None,
            "merge_warnings": merge_warnings,
            "merge_notes": merge_notes,
            "per_parser_wall_time_sec": per_parser_timing,
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "wall_time_sec": round((finished_at - started_at).total_seconds(), 3),
        },
        "_flags_raised_stage2": flags_raised_stage2,
        "_handoff_crossdoc_candidates": handoff_crossdoc_candidates,
    }
