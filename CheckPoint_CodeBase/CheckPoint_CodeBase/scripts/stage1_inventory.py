#!/usr/bin/env python3
"""Stage 1 - Discovery & inventory for Check Point PPK (multi-occupation RTK).

Walks each point folder under points_root that matches point_folder_glob,
classifies the files inside it, verifies a usable RTK device export exists,
scans operator JSON inputs for placeholder markers, and writes
outputs/01_inventory.json.

RTK differs from the PPK siblings: the primary per-point input is a vendor
device EXPORT (.csv/.jxl/.pos/.gpx) carrying already-computed fix/sigma/PDOP -
there is NO RINEX observation file, NO NAV file, and NO hardware-override file.
So the classified kinds are just: rtk_export, oplog, form.

Hard-fails (when fail_fast) only on truly critical absences - see
critical_set_policy in the data block.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

STAGE = "stage1_inventory"

# JSON inputs that may carry a top-level _status: PLACEHOLDER marker.
_JSON_INPUT_KINDS = ("oplog", "form")


def _classify(filename: str, inp: dict) -> str:
    if filename == inp["oplog_filename"]:
        return "oplog"
    if filename == inp["form_filename"]:
        return "form"
    low = filename.lower()
    for ext in inp["rtk_export_extensions"]:
        if low.endswith(ext.lower()):
            return "rtk_export"
    return "unclassified"


def _read_status(path: Path):
    """Top-level _status of a JSON input, or a marker string on trouble."""
    try:
        with path.open(encoding="utf-8") as fh:
            obj = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return "UNREADABLE"
    return obj.get("_status") if isinstance(obj, dict) else None


def run(config: dict, root: Path):
    """Return (envelope, hard_failures)."""
    spec = common.load_spec(root, config)
    spec_version = spec["_meta"]["version"]
    inp = config["inputs"]
    points_root = common.resolve_path(root, inp["points_root"])
    min_bytes = int(inp.get("rtk_export_min_bytes", 0))

    warnings = []
    hard_failures = []
    placeholder_files = []

    if config.get("spec_version") != spec_version:
        warnings.append({
            "code": "CONFIG_SPEC_VERSION_DRIFT",
            "detail": (f"paths.json spec_version={config.get('spec_version')} "
                       f"!= spec _meta.version={spec_version}"),
        })

    point_dirs = sorted(
        d for d in points_root.glob(inp["point_folder_glob"]) if d.is_dir()
    )

    points = []
    ext_counts: dict = {}
    for pdir in point_dirs:
        entry = {
            "point_id": pdir.name,
            "point_folder": common.display_path(pdir, root),
            "rtk_export": None,
            "oplog": None,
            "form": None,
            "unclassified_files": [],
            "point_warnings": [],
        }
        for f in sorted(pdir.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            kind = _classify(f.name, inp)
            ext_counts[f.suffix.lower()] = ext_counts.get(f.suffix.lower(), 0) + 1
            if kind == "unclassified":
                entry["unclassified_files"].append(f.name)
                continue
            finfo = {
                "filename": f.name,
                "path": common.display_path(f, root),
                "size_bytes": f.stat().st_size,
            }
            if kind in _JSON_INPUT_KINDS:
                status = _read_status(f)
                finfo["status"] = status
                if status == "PLACEHOLDER":
                    placeholder_files.append(finfo["path"])
                elif status == "UNREADABLE":
                    entry["point_warnings"].append(f"UNREADABLE_JSON: {f.name}")
            if kind == "rtk_export" and finfo["size_bytes"] < min_bytes:
                finfo["below_min_bytes"] = True
                entry["point_warnings"].append(
                    f"RTK_EXPORT_BELOW_MIN_BYTES: {f.name} "
                    f"({finfo['size_bytes']} < {min_bytes})")
            if entry[kind] is not None:
                entry["point_warnings"].append(f"MULTIPLE_{kind.upper()}: extra {f.name}")
            else:
                entry[kind] = finfo

        if entry["rtk_export"] is None:
            entry["point_warnings"].append("MISSING_RTK_EXPORT")
        if entry["form"] is None:
            entry["point_warnings"].append(
                "MISSING_FORM (device_type/role/flight-window pending; placeholder to be created)")
        if entry["oplog"] is None:
            entry["point_warnings"].append(
                "MISSING_OPLOG (device-type-aware; expected-absent for OTHER, else placeholder)")
        points.append(entry)

    def _usable_export(p: dict) -> bool:
        e = p["rtk_export"]
        return e is not None and not e.get("below_min_bytes", False)

    points_with_export = sum(1 for p in points if _usable_export(p))

    if not point_dirs:
        hard_failures.append({
            "code": "NO_POINT_FOLDERS",
            "detail": f"no folders matched '{inp['point_folder_glob']}' under {points_root}",
        })
    elif points_with_export == 0:
        hard_failures.append({
            "code": "NO_RTK_EXPORT",
            "detail": "no usable RTK device export found in any discovered point folder",
        })

    if placeholder_files:
        warnings.append({
            "code": "PLACEHOLDER_INPUTS_DETECTED",
            "detail": "operator-pending placeholder inputs are in use; replace before a real survey",
            "files": sorted(placeholder_files),
        })

    summary = {
        "point_count": len(points),
        "points_with_export": points_with_export,
        "points_with_oplog": sum(1 for p in points if p["oplog"]),
        "points_with_form": sum(1 for p in points if p["form"]),
        "placeholder_input_count": len(placeholder_files),
        "warning_count": len(warnings),
        "hard_failure_count": len(hard_failures),
    }

    data = {
        "critical_set_policy": (
            "RTK device export per point is the only critical input. Hard-fail "
            "(when fail_fast) only when zero point folders are discovered, or "
            "zero usable RTK exports exist across the whole survey. Missing FORM "
            "loses device_type/role/flight-window (placeholder lifecycle); "
            "missing OPLOG is device-type-aware (expected-absent for OTHER, else "
            "a placeholder) and handled by spec degrade paths downstream. There "
            "is no RINEX/NAV in RTK and no hardware-override file."),
        "points_root": common.display_path(points_root, root),
        "point_folder_glob": inp["point_folder_glob"],
        "spec_source_file_types": [sf["file_id"] for sf in spec["source_files"]],
        "extensions_classified": dict(sorted(ext_counts.items())),
        "points": points,
        "placeholder_files": sorted(placeholder_files),
        "warnings": warnings,
        "hard_failures": hard_failures,
        "summary": summary,
    }
    return common.make_envelope(STAGE, data, config, spec_version), hard_failures


def print_summary(envelope: dict, hard_failures: list) -> None:
    s = envelope["data"]["summary"]
    print(f"  points discovered: {s['point_count']}  "
          f"(EXPORT:{s['points_with_export']} OPLOG:{s['points_with_oplog']} "
          f"FORM:{s['points_with_form']})")
    for p in envelope["data"]["points"]:
        present = [k for k in ("rtk_export", "oplog", "form") if p[k]]
        print(f"    - {p['point_id']}: {', '.join(present) if present else '(no classified files)'}")
        for w in p["point_warnings"]:
            print(f"        warn: {w}")
    print(f"  warnings: {s['warning_count']}  hard failures: {s['hard_failure_count']}")
    for w in envelope["data"]["warnings"]:
        print(f"    WARN  {w['code']}")
    for hf in hard_failures:
        print(f"    FAIL  {hf['code']}: {hf['detail']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check Point PPK Stage 1 inventory")
    parser.add_argument("config", help="Path to paths.json")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    config = common.load_config(config_path)
    root = config_path.parent

    envelope, hard_failures = run(config, root)
    out_path = root / config["outputs"]["stage1_inventory"]
    common.write_envelope(out_path, envelope)

    print(f"Stage 1 inventory -> {common.display_path(out_path, root)}")
    print_summary(envelope, hard_failures)

    if hard_failures and config.get("options", {}).get("fail_fast", True):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
