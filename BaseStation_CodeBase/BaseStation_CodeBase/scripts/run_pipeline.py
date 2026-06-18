#!/usr/bin/env python3
"""Base Station PPK provenance pipeline — orchestrator.

Reads paths.json and runs Stages 1 → 2 → 3a → 3b → 3c → 3d in order.
Halts on hard failures. All weights/thresholds/flag names come from the
spec at runtime; nothing about scoring is hardcoded here.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import db_dump


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config(config_path: Path) -> dict:
    with config_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_spec(project_root: Path, spec_rel: str) -> dict:
    spec_path = project_root / spec_rel
    if not spec_path.exists():
        raise SystemExit(f"FATAL: spec file not found at {spec_path}")
    with spec_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def envelope(stage: str, config: dict, spec_version: str, data: dict) -> dict:
    return {
        "spec_version": spec_version,
        "config_used": config,
        "generated_at": utc_now_iso(),
        "stage": stage,
        "data": data,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: run_pipeline.py <paths.json>", file=sys.stderr)
        return 2

    config_path = Path(argv[1]).resolve()
    project_root = config_path.parent
    config = load_config(config_path)
    spec = load_spec(project_root, config["spec_file"])
    spec_version = spec["_meta"]["version"]

    print(f"[orchestrator] survey_id        = {config['survey_id']}")
    print(f"[orchestrator] subsystem        = {config.get('subsystem', '(unset)')}")
    print(f"[orchestrator] config_path      = {config_path}")
    print(f"[orchestrator] project_root     = {project_root}")
    print(f"[orchestrator] spec_version     = {spec_version}")
    print(f"[orchestrator] spec_counts      = {spec['_meta']['counts']}")
    print("[orchestrator] inputs:")
    for k, v in config["inputs"].items():
        resolved = project_root / v
        marker = "OK" if resolved.exists() else "MISSING"
        print(f"  - {k:24s} {v}  [{marker}]")
    print("[orchestrator] outputs (planned):")
    for k, v in config["outputs"].items():
        print(f"  - {k:24s} {v}")

    start = time.perf_counter()

    import stage1_inventory
    t0 = time.perf_counter()
    inv = stage1_inventory.run(config, project_root, spec)
    inv_envelope = envelope("stage1_inventory", config, spec_version, inv)
    out_path = project_root / config["outputs"]["stage1_inventory"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(inv_envelope, fh, indent=2, sort_keys=True)
    try:
        db_dump.dump_envelope(inv_envelope, project_root)
    except Exception as e:
        print(f"[orchestrator] warning: could not dump stage1 to DB: {e}")
    print(
        f"\n[stage1] {time.perf_counter()-t0:.3f}s  counts={inv['counts']}  "
        f"warnings={len(inv['warnings'])}  hard_failures={len(inv['hard_failures'])}"
    )
    for w in inv["warnings"]:
        print(f"  WARN  {w['code']}  {w['message']}")
    for h in inv["hard_failures"]:
        print(f"  FAIL  {h['code']}  {h['message']}")
    if inv["hard_failures"] and config["options"].get("fail_fast", True):
        print("[orchestrator] halting: hard failures and fail_fast=true")
        return 1

    import stage2_merge
    t0 = time.perf_counter()
    merge = stage2_merge.run(config, project_root, spec)
    merge_envelope = envelope("stage2_source_fields", config, spec_version, merge)
    out_path = project_root / config["outputs"]["stage2_source_fields"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(merge_envelope, fh, indent=2, sort_keys=True)
    try:
        db_dump.dump_envelope(merge_envelope, project_root)
    except Exception as e:
        print(f"[orchestrator] warning: could not dump stage2 to DB: {e}")
    mm = merge["merge_meta"]
    print(
        f"\n[stage2] {time.perf_counter()-t0:.3f}s  "
        f"fields={mm['produced_field_count']}/{mm['expected_field_count']}  "
        f"warnings={len(mm['merge_warnings'])}  "
        f"per_parser_sec={mm['per_parser_wall_time_sec']}"
    )
    for w in mm["merge_warnings"]:
        print(f"  WARN  {w.get('code')}  {w.get('detail') or w}")
    if mm["merge_warnings"] and config["options"].get("fail_fast", True):
        critical_codes = {"L1F_FIELD_COUNT_MISMATCH", "L1F_FIELD_OWNERSHIP_OVERLAP", "RINEX_OBS_FOR_PARSER_NOT_FOUND"}
        if any(w.get("code") in critical_codes for w in mm["merge_warnings"]):
            print("[orchestrator] halting: critical merge warning and fail_fast=true")
            return 1

    import compute_derived
    t0 = time.perf_counter()
    der = compute_derived.run(config, project_root, spec, merge)
    der_envelope = envelope("stage3a_derived_fields", config, spec_version, der)
    out_path = project_root / config["outputs"]["stage3_derived"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(der_envelope, fh, indent=2, sort_keys=True)
    try:
        db_dump.dump_envelope(der_envelope, project_root)
    except Exception as e:
        print(f"[orchestrator] warning: could not dump stage3a to DB: {e}")
    dm = der["stage3a_meta"]
    print(
        f"[stage3a] {time.perf_counter()-t0:.3f}s  "
        f"fields={dm['produced_field_count']}/{dm['expected_field_count']}  "
        f"by_kind={dm['counts_by_kind']}  "
        f"flags={len(der['flags_raised_stage3a'])}"
    )
    for fl in der["flags_raised_stage3a"]:
        print(f"  FLAG  {fl['flag_id']}  {fl['flag_name']}  ({fl['severity']}, "
              f"raised_at_stage_spec={fl['raised_at_stage_spec']})")

    import compute_indicators
    t0 = time.perf_counter()
    ind = compute_indicators.run(config, project_root, spec, der, merge)
    ind_envelope = envelope("stage3b_indicators", config, spec_version, ind)
    out_path = project_root / config["outputs"]["stage3_indicators"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(ind_envelope, fh, indent=2, sort_keys=True)
    try:
        db_dump.dump_envelope(ind_envelope, project_root)
    except Exception as e:
        print(f"[orchestrator] warning: could not dump stage3b to DB: {e}")
    im = ind["stage3b_meta"]
    print(
        f"[stage3b] {time.perf_counter()-t0:.3f}s  "
        f"indicators={im['produced_indicator_count']}/{im['expected_indicator_count']}  "
        f"mean_score={im['indicator_score_mean']}  "
        f"gates_triggered={im['indicators_with_gate_triggered'] or 'none'}  "
        f"flags={len(ind['flags_raised_stage3b'])}"
    )
    for fl in ind["flags_raised_stage3b"]:
        print(f"  FLAG  {fl['flag_id']}  {fl['flag_name']}  ({fl['severity']})")

    import compute_blocks
    t0 = time.perf_counter()
    blks = compute_blocks.run(config, project_root, spec, ind)
    blks_envelope = envelope("stage3c_building_blocks", config, spec_version, blks)
    out_path = project_root / config["outputs"]["stage3_building_blocks"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(blks_envelope, fh, indent=2, sort_keys=True)
    try:
        db_dump.dump_envelope(blks_envelope, project_root)
    except Exception as e:
        print(f"[orchestrator] warning: could not dump stage3c to DB: {e}")
    bm = blks["stage3c_meta"]
    print(
        f"[stage3c] {time.perf_counter()-t0:.3f}s  "
        f"blocks={bm['produced_block_count']}/{bm['expected_block_count']}  "
        f"scores={bm['score_summary']}  "
        f"weight_audits_failed={bm['weight_sum_audit_failures'] or 'none'}  "
        f"gates_triggered={bm['blocks_with_gate_triggered'] or 'none'}  "
        f"flags={len(blks['flags_raised_stage3c'])}"
    )
    for fl in blks["flags_raised_stage3c"]:
        print(f"  FLAG  {fl['flag_id']}  {fl['flag_name']}  ({fl['severity']})  "
              f"by={fl['_origin_block']}/{fl['_origin_indicator']}")

    import compute_base_score
    t0 = time.perf_counter()
    apex = compute_base_score.run(config, project_root, spec, merge, der, ind, blks)
    apex_envelope = envelope("stage3d_base_station_score", config, spec_version, apex)
    out_path = project_root / config["outputs"]["stage3_base_score"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(apex_envelope, fh, indent=2, sort_keys=True)
    try:
        db_dump.dump_envelope(apex_envelope, project_root)
    except Exception as e:
        print(f"[orchestrator] warning: could not dump stage3d to DB: {e}")
    am = apex["stage3d_meta"]
    print(
        f"[stage3d] {time.perf_counter()-t0:.3f}s  "
        f"base_station_score={apex['base_station_score']}  "
        f"global_gate_triggered={apex['global_gate']['triggered']}  "
        f"total_flags={am['total_flags_aggregated']}  "
        f"by_severity={apex['flags_by_severity']}"
    )
    print(f"  apex_formula: {apex['apex_formula_spec']}")
    print(f"  flags_by_origin_stage: {apex['flags_by_origin_stage']}")
    for fl in apex["all_flags_aggregated"]:
        print(f"  FLAG  {fl['flag_id']:12s}  {fl['flag_name']:30s}  "
              f"sev={fl['severity']:8s}  origin={fl['_origin_stage']}")

    elapsed = time.perf_counter() - start
    print(f"\n[orchestrator] wall_time_sec={elapsed:.3f}  "
          f"base_station_score={apex['base_station_score']}  "
          f"flags={am['total_flags_aggregated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
