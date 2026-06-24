#!/usr/bin/env python3
"""Orchestrator for the drone survey scoring pipeline.

Reads paths.json and runs Stage 1 -> 2 -> 3a -> 3b -> 3c -> 3d in order,
halting on hard failures. Each stage script is imported and called directly.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR))
sys.path.insert(0, str(THIS_DIR / "parsers"))

import stage1_inventory  # noqa: E402
import parse_images       # noqa: E402
import parse_rinex        # noqa: E402
import parse_bin          # noqa: E402
import parse_user_input   # noqa: E402
import resolve_calibration  # noqa: E402
import fetch_openmeteo    # noqa: E402
import compute_derived    # noqa: E402
import compute_indicators  # noqa: E402
import compute_blocks      # noqa: E402
import compute_drone_score # noqa: E402


def load_config(config_path: Path) -> dict:
    with config_path.open() as f:
        return json.load(f)


def write_envelope(envelope: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(envelope, indent=2, sort_keys=True, default=str) + "\n")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s):
    if not s:
        return None
    s = s.rstrip("Z")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def stage2_merge(config: dict, project_root: Path) -> dict:
    """Run all six Stage 2 parsers, merge field outputs, evaluate ingestion flags,
    and compute fields that require cross-parser inputs (pre/post buffer).
    """
    print("[stage2] parse_images ...")
    images = parse_images.parse(config, project_root)
    print(f"[stage2]   {images['parser_meta']['count_total']} images "
          f"({images['parser_meta']['count_valid']} valid, "
          f"{images['parser_meta']['count_geotagged']} geotagged)")

    print("[stage2] parse_rinex ... (georinex load takes ~7 min on this file)")
    rinex = parse_rinex.parse(config, project_root)
    print(f"[stage2]   obs {rinex['fields']['L1F_GNSS_001']} -> {rinex['fields']['L1F_GNSS_002']}; "
          f"epochs={rinex['fields']['L1F_GNSS_006']}")

    print("[stage2] parse_bin ...")
    bin_result = parse_bin.parse(config, project_root)
    print(f"[stage2]   CAM={bin_result['fields']['L1F_BIN_CAM_005']}, "
          f"flight={bin_result['fields']['L1F_BIN_TLM_004']}s, "
          f"waypoints={bin_result['fields']['L1F_BIN_TLM_005']}/{bin_result['fields']['L1F_BIN_MP_003']}")

    print("[stage2] parse_user_input ...")
    ui = parse_user_input.parse(config, project_root)
    print(f"[stage2]   overlap fwd={ui['fields']['L1F_UI_001']}%, lat={ui['fields']['L1F_UI_002']}%")

    print("[stage2] resolve_calibration ...")
    cal = resolve_calibration.resolve(config, project_root, images["fields"])
    print(f"[stage2]   tier={cal['parser_meta']['tier_used']}  "
          f"matched={cal['parser_meta']['matched_library_key']}")

    print("[stage2] fetch_openmeteo ...")
    api = fetch_openmeteo.fetch(config, project_root, bin_result)
    print(f"[stage2]   wind={api['fields']['L1F_API_001']} m/s  "
          f"cache_hit={api['parser_meta']['cache_hit']}  fallback={api['parser_meta']['fallback_used']}")

    # ---- Cross-parser computed fields ----
    # L1F_GNSS_004 pre_buffer_sec  = flight_start_utc - obs_start_utc  (positive = receiver was on before takeoff)
    # L1F_GNSS_005 post_buffer_sec = obs_end_utc      - flight_end_utc (positive = receiver kept logging after landing)
    obs_start = _parse_iso(rinex["fields"]["L1F_GNSS_001"])
    obs_end = _parse_iso(rinex["fields"]["L1F_GNSS_002"])
    flight_start = _parse_iso(bin_result["parser_meta"].get("flight_start_utc"))
    flight_end = _parse_iso(bin_result["parser_meta"].get("flight_end_utc"))

    pre_buffer = None
    post_buffer = None
    if obs_start and flight_start:
        pre_buffer = round((flight_start - obs_start).total_seconds(), 4)
    if obs_end and flight_end:
        post_buffer = round((obs_end - flight_end).total_seconds(), 4)

    # Apply the joined values back into the RINEX fields
    rinex["fields"]["L1F_GNSS_004"] = pre_buffer
    rinex["fields"]["L1F_GNSS_005"] = post_buffer

    # ---- pre_score_ingestion flags ----
    flags = list(images["flags_raised"]) + list(rinex["flags_raised"]) + list(bin_result["flags_raised"])
    flags += list(ui["flags_raised"]) + list(cal["flags_raised"]) + list(api["flags_raised"])

    # FLG_019 CAM_COUNT_MISMATCH: L2D_BIN_005 := (cam_record_count == total_images)
    cam_count = bin_result["fields"]["L1F_BIN_CAM_005"]
    image_count = images["fields"]["L1F_IMG_001"]
    cam_image_count_match = (cam_count == image_count)
    if not cam_image_count_match:
        flags.append({
            "flag_id": "FLG_019",
            "flag_name": "CAM_COUNT_MISMATCH",
            "severity": "MEDIUM",
            "stage": "pre_score_ingestion",
            "raised_by": "L2D_BIN_005",
            "context": f"cam_record_count={cam_count} != total_images={image_count}",
        })

    # ---- Assemble merged data block ----
    all_fields = {}
    for src in (images, rinex, bin_result, ui, cal, api):
        all_fields.update(src["fields"])

    data = {
        **all_fields,
        "_derived_at_ingestion": {
            "L2D_BIN_005_cam_image_count_match": cam_image_count_match,
        },
        "_parser_meta": {
            "parse_images": images["parser_meta"],
            "parse_rinex": rinex["parser_meta"],
            "parse_bin": bin_result["parser_meta"],
            "parse_user_input": ui["parser_meta"],
            "resolve_calibration": cal["parser_meta"],
            "fetch_openmeteo": api["parser_meta"],
        },
        "_flags_raised_stage2": flags,
    }

    return {
        "spec_version": config.get("spec_version"),
        "config_used": config,
        "generated_at": utc_now(),
        "stage": "stage2_source_fields",
        "data": data,
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_pipeline.py <paths.json>", file=sys.stderr)
        return 2

    config_path = Path(sys.argv[1]).resolve()
    if not config_path.exists():
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2

    config = load_config(config_path)
    project_root = config_path.parent
    fail_fast = config.get("options", {}).get("fail_fast", True)

    print(f"[run_pipeline] survey_id  = {config['survey_id']}")
    print(f"[run_pipeline] spec       = {config['spec_file']}")
    print(f"[run_pipeline] cwd        = {project_root}")
    print()

    # --- Stage 1 ---
    print("[run_pipeline] === Stage 1: inventory ===")
    envelope = stage1_inventory.run(config, project_root)
    out_path = project_root / config["outputs"]["stage1_inventory"]
    write_envelope(envelope, out_path)
    data = envelope["data"]
    print(f"[run_pipeline] wrote {out_path}")
    print(f"[run_pipeline] images: {data['images']['valid_count']}/{data['images']['file_count']} valid; "
          f"rinex obs={len(data['rinex']['observation_files'])} nav={len(data['rinex']['navigation_files'])}; "
          f"bin={len(data['bin']['bin_files'])}; warnings={len(data['warnings'])}")
    if data["hard_failures"]:
        for f in data["hard_failures"]:
            print(f"[run_pipeline] HARD FAILURE [{f['section']}] {f['reason']}", file=sys.stderr)
        if fail_fast:
            return 1
    print()

    # --- Stage 2 ---
    print("[run_pipeline] === Stage 2: source-field merge ===")
    envelope = stage2_merge(config, project_root)
    out_path = project_root / config["outputs"]["stage2_source_fields"]
    write_envelope(envelope, out_path)
    flags_n = len(envelope["data"]["_flags_raised_stage2"])
    print(f"[run_pipeline] wrote {out_path}  ({flags_n} ingestion flag(s))")
    for f in envelope["data"]["_flags_raised_stage2"]:
        print(f"[run_pipeline]   - {f['flag_id']} {f['flag_name']} ({f['severity']}): {f['context']}")
    print()

    # --- Stage 3a: derived fields ---
    print("[run_pipeline] === Stage 3a: derived fields ===")
    envelope = compute_derived.run(config, project_root)
    out_path = project_root / config["outputs"]["stage3_derived"]
    write_envelope(envelope, out_path)
    derived = {k: v for k, v in envelope["data"].items() if k.startswith("L2D_")}
    populated = sum(1 for v in derived.values() if v is not None and v is not False)
    print(f"[run_pipeline] wrote {out_path}  ({len(derived)} derived; {populated} non-null)")
    print()

    # --- Stage 3b: indicators with thresholds and flags ---
    print("[run_pipeline] === Stage 3b: indicators ===")
    envelope = compute_indicators.run(config, project_root)
    out_path = project_root / config["outputs"]["stage3_indicators"]
    write_envelope(envelope, out_path)
    n_inds = len(envelope["data"]["indicators"])
    flags_3b = envelope["data"]["flags_raised_stage3b"]
    print(f"[run_pipeline] wrote {out_path}  ({n_inds} indicators; {len(flags_3b)} flag(s))")
    for f in flags_3b:
        print(f"[run_pipeline]   - {f['flag_id']} {f['flag_name']} ({f['severity']}, {f['stage']}): {f['context']}")
    print()

    # --- Stage 3c: building-block rollups ---
    print("[run_pipeline] === Stage 3c: building-block rollups ===")
    drone_envelope, cal_envelope = compute_blocks.run(config, project_root)
    drone_out = project_root / config["outputs"]["stage3_building_blocks"]
    cal_out = project_root / config["outputs"]["stage3_cal_conf"]
    write_envelope(drone_envelope, drone_out)
    write_envelope(cal_envelope, cal_out)
    print(f"[run_pipeline] wrote {drone_out}")
    print(f"[run_pipeline] wrote {cal_out}")
    flags_3c = drone_envelope["data"]["flags_raised_stage3c"]
    for bid in ("BB_IMG_CAPTURE", "BB_ROVER_GNSS", "BB_MISSION_EXEC"):
        b = drone_envelope["data"]["blocks"][bid]
        gate = "TRIPPED" if b["gate_triggered"] else "ok"
        print(f"[run_pipeline]   {bid:18s} weight={b['weight_in_drone_score_ppk']:>5}  score={b['score']:>6}  gate={gate}")
    c = cal_envelope["data"]["cal_conf"]
    print(f"[run_pipeline]   {'BB_CAL_CONF':18s} weight={c['weight_in_drone_score_ppk']:>5}  score={c['score']:>6}  (parallel deliverable)")
    if flags_3c:
        for f in flags_3c:
            print(f"[run_pipeline]   - {f['flag_id']} {f['flag_name']} ({f['severity']}): {f['context']}")
    print()

    # --- Stage 3d: drone_score (apex) ---
    print("[run_pipeline] === Stage 3d: drone_score ===")
    envelope = compute_drone_score.run(config, project_root)
    out_path = project_root / config["outputs"]["stage3_drone_score"]
    write_envelope(envelope, out_path)
    d = envelope["data"]
    print(f"[run_pipeline] wrote {out_path}")
    for bid, c in d["block_contributions"].items():
        print(f"[run_pipeline]   {bid:18s} weight={c['weight_in_ppk']:>5}  score={c['block_score']:>6}  contribution={c['contribution']}")
    gate = "TRIPPED" if d["global_gate_triggered"] else "not tripped"
    print(f"[run_pipeline]   global_gate: {gate}")
    print(f"[run_pipeline]   DRONE_SCORE = {d['drone_score']}")
    print(f"[run_pipeline]   CAL_CONF    = {d['cal_conf_parallel']['score']} (parallel; not part of drone_score)")
    print(f"[run_pipeline]   flags total = {d['all_flags_count']}")
    for f in d["all_flags_aggregated"]:
        print(f"[run_pipeline]     - {f['flag_id']} {f['flag_name']} ({f['severity']}, raised at {f['_origin_stage']})")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
