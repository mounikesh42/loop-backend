#!/usr/bin/env python3
"""Orchestrator for the Processing confidence pipeline.

Reads paths.json and runs Stages 1 -> 2 -> 3a -> 3b -> 3c -> 3d in order,
halting on a Stage-1 hard failure (fail_fast). Writes all 7 envelopes:

  01_inventory.json  02_source_fields.json  03_derived_fields.json
  04_indicators.json  05_building_blocks.json  05b_per_deliverable_views.json
  06_processing_score.json

Per-stage wall time is recorded on each ENVELOPE (wall_time_sec, alongside
generated_at) - never inside the data block, so the data stays deterministic
(rule 3). The smoke harness re-runs Stages 3a->3d directly and omits these.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import csv_export  # noqa: E402
import stage1_inventory  # noqa: E402
import stage2_merge  # noqa: E402
import stage3a_derived  # noqa: E402
import stage3b_indicators  # noqa: E402
import stage3c_blocks  # noqa: E402
import stage3d_score  # noqa: E402


def _write(root, config, key, stage, data, spec_version, elapsed):
    env = common.make_envelope(stage, data, config, spec_version)
    env["wall_time_sec"] = round(elapsed, 4)
    out_path = root / config["outputs"][key]
    common.write_envelope(out_path, env)
    return out_path, env


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Processing confidence pipeline orchestrator")
    ap.add_argument("config", help="path to paths.json")
    ap.add_argument("--export-csv", dest="export_csv", default=None,
                    help="Export final results to CSV file (e.g., '19thmay.csv')")
    ap.add_argument("--export-xlsx", dest="export_xlsx", default=None,
                    help="Export final results to Excel file with multiple sheets (e.g., '19thmay.xlsx')")
    args = ap.parse_args(argv)
    config_path = Path(args.config).resolve()
    config = common.load_config(config_path)
    root = config_path.parent
    spec = common.load_spec(root, config)
    spec_version = spec["_meta"]["version"]

    print(f"Processing pipeline | subsystem={config['subsystem']} | survey={config['survey_id']} "
          f"| spec {spec_version}")
    written = []

    # Stage 1
    t = time.perf_counter()
    env1, hard = stage1_inventory.run(config, root)
    env1["wall_time_sec"] = round(time.perf_counter() - t, 4)
    p1 = root / config["outputs"]["stage1_inventory"]
    common.write_envelope(p1, env1)
    written.append((p1, env1, "stage1_inventory"))
    if hard and config.get("options", {}).get("fail_fast", True):
        print(f"  Stage 1: HARD FAILURE {[h['code'] for h in hard]} -> HALT (fail_fast).")
        return 1

    # Stage 2
    t = time.perf_counter()
    d2 = stage2_merge.run(config, root, spec, env1["data"])
    p2, e2 = _write(root, config, "stage2_source_fields", stage2_merge.STAGE, d2, spec_version,
                    time.perf_counter() - t)
    written.append((p2, e2, "stage2_merge"))

    # Stage 3a
    t = time.perf_counter()
    d3a = stage3a_derived.run(config, root, spec, d2)
    p3a, e3a = _write(root, config, "stage3_derived", stage3a_derived.STAGE, d3a, spec_version,
                      time.perf_counter() - t)
    written.append((p3a, e3a, "stage3a_derived"))

    # Stage 3b
    t = time.perf_counter()
    d3b = stage3b_indicators.run(config, root, spec, d3a, d2)
    p3b, e3b = _write(root, config, "stage3_indicators", stage3b_indicators.STAGE, d3b, spec_version,
                      time.perf_counter() - t)
    written.append((p3b, e3b, "stage3b_indicators"))

    # Stage 3c (+ 05b parallel deliverable)
    t = time.perf_counter()
    d3c = stage3c_blocks.run(config, root, spec, d3b, d2)
    el3c = time.perf_counter() - t
    p3c, e3c = _write(root, config, "stage3_building_blocks", stage3c_blocks.STAGE, d3c,
                      spec_version, el3c)
    written.append((p3c, e3c, "stage3c_blocks"))
    p3cb, e3cb = _write(root, config, "stage3_per_deliverable_views", stage3c_blocks.STAGE, {
        "per_deliverable_views": d3c["per_deliverable_views"],
        "stage3c_meta": {k: d3c["stage3c_meta"][k] for k in
                         ("view_count", "view_weight_sum_audit_failures", "view_score_summary")},
    }, spec_version, el3c)
    written.append((p3cb, e3cb, "stage3c_views_05b"))

    # Stage 3d
    t = time.perf_counter()
    d3d = stage3d_score.run(config, root, spec, d2, d3a, d3b, d3c)
    p3d, e3d = _write(root, config, "stage3_processing_score", stage3d_score.STAGE, d3d, spec_version,
                      time.perf_counter() - t)
    written.append((p3d, e3d, "stage3d_score"))

    # ---- summary + spec_version verification ----
    print("\n  stage                 wall_s   spec_version   artifact")
    total = 0.0
    version_ok = True
    for path, env, label in written:
        total += env["wall_time_sec"]
        ok = env["spec_version"] == spec_version
        version_ok &= ok
        print(f"    {label:20s} {env['wall_time_sec']:7.3f}   {env['spec_version']:<12}{'' if ok else ' !!'}   "
              f"{path.name}")
    print(f"    {'TOTAL':20s} {total:7.3f}")
    print(f"\n  processing_score = {d3d['processing_score']}  | verification = {d3d['verification_status']['value']}"
          f"  | flags(unique) = {d3d['stage3d_meta']['unique_flag_count']}")
    print(f"  all envelopes carry spec_version {spec_version}: {version_ok}")
    
    # ---- CSV/Excel export ----
    if args.export_csv or args.export_xlsx:
        try:
            if args.export_csv:
                csv_path = root / args.export_csv
                csv_export.export_processing_score_csv(e3d, csv_path)
                print(f"\n  ✓ Exported CSV: {csv_path}")
            
            if args.export_xlsx:
                xlsx_path = root / args.export_xlsx
                csv_export.export_processing_score_xlsx(e3d, xlsx_path)
                print(f"  ✓ Exported Excel: {xlsx_path}")
        except Exception as ex:
            print(f"\n  ✗ Export failed: {ex}", file=sys.stderr)
            return 3
    
    return 0 if version_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
