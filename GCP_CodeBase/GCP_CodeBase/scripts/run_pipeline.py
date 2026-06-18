#!/usr/bin/env python3
"""GCP PPK scoring pipeline orchestrator.

Reads paths.json and runs the stages in strict order:

    Stage 1   Discovery & inventory          (per-point, multi-occupation)
    Stage 2   Parse to canonical source       -> per-point source-field list
    Stage 3a  Derived fields                   (per point)
    Stage 3b  Indicators + thresholds + flags  (per point)
    Stage 3c  Building-block rollups           (per point) + cross-point aggregation
    Stage 3d  Apex gcp_score                   (aggregated blocks + global gate)

Each stage writes a deterministic envelope to the path named in
paths.json["outputs"]. Halts on hard failures when options.fail_fast is true.
"""

import argparse
import json
import sys
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
    parser = argparse.ArgumentParser(description="GCP PPK scoring pipeline")
    parser.add_argument("config", help="Path to paths.json")
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

    print(f"GCP PPK pipeline  |  subsystem={config.get('subsystem')}  "
          f"spec_version={spec_version}")
    print(f"project_root = {root}")

    # ---- Stage 1 ---------------------------------------------------------
    env1, hard = stage1_inventory.run(config, root)
    out1 = root / config["outputs"]["stage1_inventory"]
    common.write_envelope(out1, env1)
    print(f"Stage 1 inventory -> {out1.relative_to(root)}")
    stage1_inventory.print_summary(env1, hard)
    if hard and fail_fast:
        print("HALT: Stage 1 reported a hard failure (fail_fast).")
        return 1

    # ---- Stage 2 ---------------------------------------------------------
    data2 = stage2_merge.run(config, root, spec, env1["data"])
    out2 = root / config["outputs"]["stage2_source_fields"]
    common.write_envelope(out2, common.make_envelope("stage2_merge", data2, config, spec_version))
    print(f"Stage 2 source fields -> {out2.relative_to(root)}")
    stage2_merge.print_summary(data2)

    # ---- Stage 3a --------------------------------------------------------
    data3a = stage3a_derived.run(config, root, spec, data2)
    out3a = root / config["outputs"]["stage3_derived"]
    common.write_envelope(out3a, common.make_envelope("stage3a_derived", data3a, config, spec_version))
    print(f"Stage 3a derived fields -> {out3a.relative_to(root)}")
    stage3a_derived.print_summary(data3a)

    # ---- Stage 3b --------------------------------------------------------
    data3b = stage3b_indicators.run(config, root, spec, data3a, data2)
    out3b = root / config["outputs"]["stage3_indicators"]
    common.write_envelope(out3b, common.make_envelope("stage3b_indicators", data3b, config, spec_version))
    print(f"Stage 3b indicators -> {out3b.relative_to(root)}")
    stage3b_indicators.print_summary(data3b)

    # ---- Stage 3c --------------------------------------------------------
    data3c = stage3c_blocks.run(config, root, spec, data3b)
    out3c = root / config["outputs"]["stage3_building_blocks"]
    common.write_envelope(out3c, common.make_envelope("stage3c_blocks", data3c, config, spec_version))
    print(f"Stage 3c building blocks -> {out3c.relative_to(root)}")
    stage3c_blocks.print_summary(data3c)

    # ---- Stage 3d --------------------------------------------------------
    data3d = stage3d_score.run(config, root, spec, data2, data3a, data3b, data3c)
    out3d = root / config["outputs"]["stage3_gcp_score"]
    common.write_envelope(out3d, common.make_envelope("stage3d_score", data3d, config, spec_version))
    print(f"Stage 3d gcp_score -> {out3d.relative_to(root)}")
    stage3d_score.print_summary(data3d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
