#!/usr/bin/env python3
"""Pre-Processing confidence-score pipeline orchestrator.

Reads paths.json and runs the stages in strict order (SURVEY-LEVEL - one survey,
computed once):

    Stage 1   Discovery & inventory           -> 01_inventory.json
    Stage 2   Parse + survey-level merge       -> 02_source_fields.json
    Stage 3a  Derived fields (incl. geometry)  -> 03_derived_fields.json
    Stage 3b  Indicators (Option B + flags)    -> 04_indicators.json
    Stage 3c  Building blocks                   -> 05_building_blocks.json
              Per-artifact views (parallel)     -> 05b_per_artifact_views.json
    Stage 3d  Apex pre_processing_score          -> 06_pre_processing_score.json
              (+ verification_status, flag aggregation)

Each stage writes a deterministic envelope to the path named in
paths.json["outputs"]. Halts on Stage 1 hard failures when options.fail_fast is
true. Per-stage wall time is printed, never written into a data block.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import stage1_inventory  # noqa: E402
import stage2_merge  # noqa: E402
import stage3a_derived  # noqa: E402
import stage3b_indicators  # noqa: E402
import stage3c_blocks  # noqa: E402
import stage3d_score  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pre-Processing confidence-score pipeline")
    parser.add_argument("config", help="Path to paths.json")
    parser.add_argument("--date", "--output-prefix", dest="csv_prefix", default=None,
                        help="Date or name prefix for CSV output files (e.g., '19thmay' -> '19thmay_pre_processing_score.csv')")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 1
    try:
        config = common.load_config(config_path)
    except json.JSONDecodeError as exc:
        print(f"ERROR: could not parse config {config_path}: {exc}", file=sys.stderr)
        return 1

    root = config_path.parent
    spec = common.load_spec(root, config)
    spec_version = spec["_meta"]["version"]
    fail_fast = config.get("options", {}).get("fail_fast", True)
    out = config["outputs"]

    print(f"Pre-Processing pipeline  |  subsystem={config.get('subsystem')}  spec_version={spec_version}")
    print(f"project_root = {root}")
    timings: dict[str, float] = {}
    
    # Collect envelopes for CSV export
    envelopes: dict = {}

    # ---- Stage 1 ----
    t = time.perf_counter()
    env1, hard = stage1_inventory.run(config, root)
    timings["stage1"] = round(time.perf_counter() - t, 3)
    common.write_envelope(root / out["stage1_inventory"], env1)
    envelopes["stage1_inventory"] = env1
    print(f"Stage 1 inventory -> {out['stage1_inventory']}  ({timings['stage1']}s)")
    stage1_inventory.print_summary(env1, hard)
    if hard and fail_fast:
        print("HALT: Stage 1 reported a hard failure (fail_fast).")
        return 1

    # ---- Stage 2 ----
    t = time.perf_counter()
    d2 = stage2_merge.run(config, root, spec, env1["data"])
    timings["stage2"] = round(time.perf_counter() - t, 3)
    envelope2 = common.make_envelope("stage2_merge", d2, config, spec_version)
    common.write_envelope(root / out["stage2_source_fields"], envelope2)
    envelopes["stage2_merge"] = envelope2
    print(f"Stage 2 source fields -> {out['stage2_source_fields']}  ({timings['stage2']}s)")
    stage2_merge.print_summary(d2)

    # ---- Stage 3a ----
    t = time.perf_counter()
    d3a = stage3a_derived.run(config, root, spec, d2)
    timings["stage3a"] = round(time.perf_counter() - t, 3)
    envelope3a = common.make_envelope("stage3a_derived", d3a, config, spec_version)
    common.write_envelope(root / out["stage3_derived"], envelope3a)
    envelopes["stage3a_derived"] = envelope3a
    print(f"Stage 3a derived -> {out['stage3_derived']}  ({timings['stage3a']}s)")
    stage3a_derived.print_summary(d3a)

    # ---- Stage 3b ----
    t = time.perf_counter()
    d3b = stage3b_indicators.run(config, root, spec, d3a, d2)
    timings["stage3b"] = round(time.perf_counter() - t, 3)
    envelope3b = common.make_envelope("stage3b_indicators", d3b, config, spec_version)
    common.write_envelope(root / out["stage3_indicators"], envelope3b)
    envelopes["stage3b_indicators"] = envelope3b
    print(f"Stage 3b indicators -> {out['stage3_indicators']}  ({timings['stage3b']}s)")
    stage3b_indicators.print_summary(d3b)

    # ---- Stage 3c (blocks + 05b views) ----
    t = time.perf_counter()
    d3c = stage3c_blocks.run(config, root, spec, d3b)
    timings["stage3c"] = round(time.perf_counter() - t, 3)
    envelope3c = common.make_envelope("stage3c_blocks", d3c, config, spec_version)
    common.write_envelope(root / out["stage3_building_blocks"], envelope3c)
    envelopes["stage3c_blocks"] = envelope3c
    common.write_envelope(root / out["stage3_per_artifact_views"], common.make_envelope(
        "stage3c_blocks",
        {"per_artifact_views": d3c["per_artifact_views"], "stage3c_meta": d3c["stage3c_meta"]},
        config, spec_version))
    print(f"Stage 3c blocks -> {out['stage3_building_blocks']} (+ {Path(out['stage3_per_artifact_views']).name})  "
          f"({timings['stage3c']}s)")
    stage3c_blocks.print_summary(d3c)

    # ---- Stage 3d ----
    t = time.perf_counter()
    d3d = stage3d_score.run(config, root, spec, d2, d3a, d3b, d3c)
    timings["stage3d"] = round(time.perf_counter() - t, 3)
    envelope3d = common.make_envelope("stage3d_score", d3d, config, spec_version)
    common.write_envelope(root / out["stage3_pre_processing_score"], envelope3d)
    envelopes["stage3d_score"] = envelope3d
    print(f"Stage 3d pre_processing_score -> {out['stage3_pre_processing_score']}  ({timings['stage3d']}s)")
    stage3d_score.print_summary(d3d)

    total = round(sum(timings.values()), 3)
    print(f"\nPipeline complete  |  pre_processing_score={d3d['pre_processing_score']}  "
          f"verification_status={d3d['verification_status']['value']}  "
          f"total wall time {total}s  per-stage {timings}")
    
    # ---- CSV Export (if --date/--output-prefix specified) ----
    if args.csv_prefix:
        common.export_all_stages_to_csv(root, config, envelopes, args.csv_prefix)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
