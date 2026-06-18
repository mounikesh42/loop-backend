#!/usr/bin/env python3
"""Stage 1 - Discovery & inventory for Pre-Processing (survey-level).

Unlike the per-point/multi-occupation PPK siblings, pre-processing scores ONE
survey from 5 named artifacts (spec sheet 01). This stage discovers and
classifies each, scans operator inputs for placeholder markers, and writes
outputs/01_inventory.json.

The 5 artifacts and their criticality (see critical_set_policy in the data
block for the reasoning):

    SRC_PP_GEOTAGS     geotag image set (dir)   CRITICAL  (GEO block, 0.30)
    SRC_PP_GCP_COORDS  GCP coordinate file      CRITICAL  (GCT 0.25 + SD 0.10)
    SRC_PP_MANIFEST    processing manifest      CRITICAL  (REF block + every
                                                          declared-tier indicator)
    SRC_PP_CP_COORDS   check-point coord file   OPTIONAL  (absence -> verification_
                                                          status=UNVERIFIED_NO_CPS;
                                                          score unaffected by spec)
    SRC_PP_REPORT      processing report        OPTIONAL  (absence -> report-tier
                                                          indicators advisory/redistribute)

Hard-fails (when fail_fast) only on a missing CRITICAL artifact. There is NO
RINEX/NAV anywhere in pre-processing - the geotag set is real EXIF imagery.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

STAGE = "stage1_inventory"

# artifact_key -> (spec file_id, paths.json inputs key, is_critical)
_FILE_ARTIFACTS = {
    "gcp_coords": ("SRC_PP_GCP_COORDS", "gcp_coords_file", True),
    "cp_coords": ("SRC_PP_CP_COORDS", "cp_coords_file", False),
    "manifest": ("SRC_PP_MANIFEST", "manifest_file", True),
    "report": ("SRC_PP_REPORT", "report_file", False),
}


def _read_json_status(path: Path):
    """Top-level _status of a JSON input, or a marker string on trouble."""
    try:
        with path.open(encoding="utf-8") as fh:
            obj = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return "UNREADABLE"
    return obj.get("_status") if isinstance(obj, dict) else None


def _csv_has_placeholder(path: Path) -> bool:
    """Detect a '# _status: PLACEHOLDER' header comment in a coord file."""
    try:
        with path.open(encoding="utf-8") as fh:
            for _ in range(10):
                line = fh.readline()
                if not line:
                    break
                if line.startswith("#") and "_status" in line and "PLACEHOLDER" in line.upper():
                    return True
    except OSError:
        return False
    return False


def run(config: dict, root: Path):
    """Return (envelope, hard_failures)."""
    spec = common.load_spec(root, config)
    spec_version = spec["_meta"]["version"]
    inp = config["inputs"]
    min_bytes = int(inp.get("coord_file_min_bytes", 0))

    warnings: list[dict] = []
    hard_failures: list[dict] = []
    placeholder_files: list[str] = []
    ext_counts: dict[str, int] = {}
    artifacts: dict[str, dict] = {}

    if config.get("spec_version") != spec_version:
        warnings.append({
            "code": "CONFIG_SPEC_VERSION_DRIFT",
            "detail": (f"paths.json spec_version={config.get('spec_version')} "
                       f"!= spec _meta.version={spec_version}"),
        })

    spec_files = {sf["file_id"]: sf for sf in spec["source_files"]}

    # ---- SRC_PP_GEOTAGS : directory of real EXIF images (+ optional sidecar) --
    gdir = root / inp["geotags_dir"]
    img_exts = [e.lower() for e in inp.get("geotag_image_extensions", [])]
    side_exts = [e.lower() for e in inp.get("geotag_sidecar_extensions", [])]
    images: list[str] = []
    sidecars: list[str] = []
    other_geotag_files: list[str] = []
    if gdir.is_dir():
        for f in sorted(gdir.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            suf = f.suffix.lower()
            ext_counts[suf] = ext_counts.get(suf, 0) + 1
            if suf in img_exts:
                images.append(f.name)
            elif suf in side_exts:
                sidecars.append(f.name)
                if _csv_has_placeholder(f):
                    placeholder_files.append(str(f.relative_to(root)))
            else:
                other_geotag_files.append(f.name)
    geotags_present = bool(images) or bool(sidecars)
    artifacts["geotags"] = {
        "file_id": "SRC_PP_GEOTAGS",
        "file_name": spec_files.get("SRC_PP_GEOTAGS", {}).get("file_name"),
        "dir": str(gdir.relative_to(root)) if gdir.exists() else inp["geotags_dir"],
        "present": geotags_present,
        "critical": True,
        "image_count": len(images),
        "sidecar_count": len(sidecars),
        "sample_images": images[:3],
        "unclassified_files": other_geotag_files,
    }
    if not geotags_present:
        hard_failures.append({
            "code": "NO_GEOTAGS",
            "detail": f"no geotag images or sidecar found in {inp['geotags_dir']}",
        })
    if other_geotag_files:
        warnings.append({
            "code": "UNCLASSIFIED_GEOTAG_FILES",
            "detail": f"{len(other_geotag_files)} non-image/non-sidecar files in geotags dir",
            "files": other_geotag_files[:10],
        })

    # ---- the 4 single-file artifacts ----------------------------------------
    for key, (file_id, cfg_key, critical) in _FILE_ARTIFACTS.items():
        rel = inp.get(cfg_key)
        path = (root / rel) if rel else None
        present = bool(path and path.is_file())
        info = {
            "file_id": file_id,
            "file_name": spec_files.get(file_id, {}).get("file_name"),
            "path": rel,
            "present": present,
            "critical": critical,
        }
        if present:
            info["size_bytes"] = path.stat().st_size
            ext_counts[path.suffix.lower()] = ext_counts.get(path.suffix.lower(), 0) + 1
            if key in ("gcp_coords", "cp_coords"):
                if info["size_bytes"] < min_bytes:
                    info["below_min_bytes"] = True
                    warnings.append({"code": f"{key.upper()}_BELOW_MIN_BYTES",
                                     "detail": f"{rel} ({info['size_bytes']} < {min_bytes})"})
                if _csv_has_placeholder(path):
                    placeholder_files.append(rel)
            elif key in ("manifest", "report") and path.suffix.lower() == ".json":
                status = _read_json_status(path)
                info["status"] = status
                if status == "PLACEHOLDER":
                    placeholder_files.append(rel)
                elif status == "UNREADABLE":
                    info["unreadable"] = True
        artifacts[key] = info

        # criticality + spec-defined optional handling
        if critical and (not present or info.get("unreadable")):
            hard_failures.append({
                "code": f"{file_id.replace('SRC_PP_', '')}_MISSING",
                "detail": f"critical artifact {file_id} absent/unreadable at {rel}",
            })
        if not critical and not present:
            if key == "cp_coords":
                warnings.append({
                    "code": "CP_COORDS_ABSENT",
                    "detail": ("no check-point coord file; verification_status will be "
                               "UNVERIFIED_NO_CPS (pre_processing_score unaffected by spec)"),
                })
            elif key == "report":
                warnings.append({
                    "code": "REPORT_ABSENT",
                    "detail": ("no processing report; report-tier indicators "
                               "(cors continuity, time-sync, gcp residual, cors health, "
                               "settings consistency) score advisory and redistribute"),
                })

    if placeholder_files:
        warnings.append({
            "code": "PLACEHOLDER_INPUTS_DETECTED",
            "detail": ("operator-pending placeholder inputs in use; replace before a real "
                       "survey. The geotag image set is placeholder too (covered by the "
                       "manifest's PLACEHOLDER status)."),
            "files": sorted(set(placeholder_files)),
        })

    spec_source_ids = [sf["file_id"] for sf in spec["source_files"]]
    discovered_ids = sorted({a["file_id"] for a in artifacts.values() if a["present"]})
    summary = {
        "expected_source_files": spec["_meta"]["counts"]["source_files"],
        "artifacts_present": sum(1 for a in artifacts.values() if a["present"]),
        "critical_present": sum(1 for a in artifacts.values() if a["critical"] and a["present"]),
        "critical_total": sum(1 for a in artifacts.values() if a["critical"]),
        "geotag_image_count": artifacts["geotags"]["image_count"],
        "placeholder_input_count": len(set(placeholder_files)),
        "warning_count": len(warnings),
        "hard_failure_count": len(hard_failures),
    }

    data = {
        "survey_level": True,
        "critical_set_policy": (
            "CRITICAL = {manifest, geotags, gcp_coords}: a missing manifest leaves no "
            "project requirements / declarations / paths (REF block + every declared-tier "
            "indicator); missing geotags voids the GEO block (0.30); missing GCP coords "
            "voids GCT (0.25) + SD (0.10) - any of these makes the score meaningless, so "
            "hard-fail when fail_fast. OPTIONAL = {cp_coords, report}: cp_coords absence is "
            "a spec-defined state (verification_status=UNVERIFIED_NO_CPS, score unaffected); "
            "report absence degrades report-tier indicators to advisory with weight "
            "redistribution. No RINEX/NAV/hardware-override files exist in pre-processing."),
        "spec_source_file_types": spec_source_ids,
        "discovered_source_file_types": discovered_ids,
        "artifacts": artifacts,
        "extensions_classified": dict(sorted(ext_counts.items())),
        "placeholder_files": sorted(set(placeholder_files)),
        "warnings": warnings,
        "hard_failures": hard_failures,
        "summary": summary,
    }
    return common.make_envelope(STAGE, data, config, spec_version), hard_failures


def print_summary(envelope: dict, hard_failures: list) -> None:
    d = envelope["data"]
    s = d["summary"]
    print(f"  survey-level inventory  |  critical present: {s['critical_present']}/{s['critical_total']}  "
          f"artifacts present: {s['artifacts_present']}/{s['expected_source_files']}")
    for key in ("geotags", "gcp_coords", "cp_coords", "manifest", "report"):
        a = d["artifacts"][key]
        tag = "CRIT" if a["critical"] else "opt "
        mark = "OK " if a["present"] else "-- "
        extra = ""
        if key == "geotags":
            extra = f"images={a['image_count']} sidecars={a['sidecar_count']}"
        elif a.get("status"):
            extra = f"status={a['status']}"
        elif a.get("present"):
            extra = f"{a.get('size_bytes')}B"
        print(f"    [{mark}][{tag}] {a['file_id']:18s} {extra}")
    print(f"  placeholders: {s['placeholder_input_count']}  warnings: {s['warning_count']}  "
          f"hard failures: {s['hard_failure_count']}")
    for w in d["warnings"]:
        print(f"    WARN  {w['code']}")
    for hf in hard_failures:
        print(f"    FAIL  {hf['code']}: {hf['detail']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pre-Processing Stage 1 inventory")
    parser.add_argument("config", help="Path to paths.json")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    config = common.load_config(config_path)
    root = config_path.parent

    envelope, hard_failures = run(config, root)
    out_path = root / config["outputs"]["stage1_inventory"]
    common.write_envelope(out_path, envelope)

    print(f"Stage 1 inventory -> {out_path.relative_to(root)}")
    print_summary(envelope, hard_failures)

    if hard_failures and config.get("options", {}).get("fail_fast", True):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
