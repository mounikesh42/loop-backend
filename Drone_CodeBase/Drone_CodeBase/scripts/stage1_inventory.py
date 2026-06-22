#!/usr/bin/env python3
"""Stage 1 — Discovery & inventory.

Walks the four input folders declared in paths.json, classifies what's there,
and emits outputs/01_inventory.json. Hard-fails on missing critical files.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from tkinter import NONE

from path_utils import resolve_path


VALID_IMAGE_EXTS = {".jpg", ".jpeg", ".dng", ".raw"}

# RINEX 2.x naming: ssssdddf.yyt — `yy` is two-digit year, `t` is file type.
# `.obs`/`.nav`/`.21o` etc. are all matched by the regexes below.
RINEX_OBS_RE = re.compile(r"^\.(\d{2}o|obs)$", re.IGNORECASE)
RINEX_NAV_RE = re.compile(r"^\.(\d{2}[nglp]|nav)$", re.IGNORECASE)
RINEX_MET_RE = re.compile(r"^\.(\d{2}m|met)$", re.IGNORECASE)


def classify_rinex_ext(ext: str) -> str:
    if RINEX_OBS_RE.match(ext):
        return "observation"
    if RINEX_NAV_RE.match(ext):
        return "navigation"
    if RINEX_MET_RE.match(ext):
        return "meteorological"
    return "unknown"


def inventory_images(folder: Path) -> dict:
    if not folder.exists():
        return {
            "folder": str(folder),
            "exists": False,
            "file_count": 0,
            "valid_count": 0,
            "extensions_seen": [],
            "unrecognized_files": [],
            "hard_failure": "images folder does not exist",
        }

    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    exts_seen = sorted({p.suffix.upper().lstrip(".") for p in files})
    valid = [p for p in files if p.suffix.lower() in VALID_IMAGE_EXTS]
    unrecognized = [p.name for p in files if p.suffix.lower() not in VALID_IMAGE_EXTS]

    return {
        "folder": str(folder),
        "exists": True,
        "file_count": len(files),
        "valid_count": len(valid),
        "extensions_seen": exts_seen,
        "valid_extensions": sorted(e.lstrip(".").upper() for e in VALID_IMAGE_EXTS),
        "unrecognized_files": sorted(unrecognized),
        "hard_failure": None if valid else "no valid image files found",
    }


def inventory_rinex(folder: Path) -> dict:
    if not folder.exists():
        return {
            "folder": str(folder),
            "exists": False,
            "file_count": 0,
            "observation_files": [],
            "navigation_files": [],
            "other_files": [],
            "unrecognized_files": [],
            "duplicate_candidates": [],
            "hard_failure": "rinex folder does not exist",
        }

    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    obs, nav, met, unknown = [], [], [], []
    for p in files:
        entry = {
            "name": p.name,
            "size_bytes": p.stat().st_size,
            "extension": p.suffix.lstrip("."),
        }
        kind = classify_rinex_ext(p.suffix)
        if kind == "observation":
            obs.append(entry)
        elif kind == "navigation":
            nav.append(entry)
        elif kind == "meteorological":
            met.append(entry)
        else:
            unknown.append(entry)

    # Heuristic: same extension + same size → duplicate candidate.
    by_sig: dict = {}
    for entry in obs + nav + met:
        sig = (entry["extension"], entry["size_bytes"])
        by_sig.setdefault(sig, []).append(entry["name"])
    duplicates = sorted(
        [{"signature": f"{ext}|{size}B", "files": sorted(names)} for (ext, size), names in by_sig.items() if len(names) > 1],
        key=lambda d: d["signature"],
    )

    return {
        "folder": str(folder),
        "exists": True,
        "file_count": len(files),
        "observation_files": sorted(obs, key=lambda d: d["name"]),
        "navigation_files": sorted(nav, key=lambda d: d["name"]),
        "other_files": sorted(met, key=lambda d: d["name"]),
        "unrecognized_files": sorted(unknown, key=lambda d: d["name"]),
        "duplicate_candidates": duplicates,
        "hard_failure": None if obs else "no RINEX observation file found",
    }


def inventory_bin(folder: Path) -> dict:
    if not folder.exists():
        return {
            "folder": str(folder),
            "exists": False,
            "file_count": 0,
            "bin_files": [],
            "hard_failure": "telemetry folder does not exist",
        }

    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    bin_files = [
        {"name": p.name, "size_bytes": p.stat().st_size}
        for p in files if p.suffix.lower() == ".bin"
    ]
    other = [p.name for p in files if p.suffix.lower() != ".bin"]

    if len(bin_files) == 0:
        hard = None
    elif len(bin_files) > 1:
        hard = f"expected exactly 1 .BIN file, found {len(bin_files)}"
    else:
        hard = None

    return {
        "folder": str(folder),
        "exists": True,
        "file_count": len(files),
        "bin_files": sorted(bin_files, key=lambda d: d["name"]),
        "other_files": sorted(other),
        "hard_failure": hard,
    }


def inventory_user_input(form_path: Path) -> dict:
    required = ["planned_overlap_fwd_pct", "planned_overlap_lat_pct"]
    if not form_path.exists():
        return {
            "file": str(form_path),
            "exists": False,
            "required_fields": required,
            "hard_failure": "user input form.json does not exist",
        }
    try:
        payload = json.loads(form_path.read_text())
    except json.JSONDecodeError as exc:
        return {
            "file": str(form_path),
            "exists": True,
            "required_fields": required,
            "hard_failure": f"form.json is not valid JSON: {exc}",
        }

    missing = [k for k in required if k not in payload]
    return {
        "file": str(form_path),
        "exists": True,
        "required_fields": required,
        "fields_present": sorted(payload.keys()),
        "missing_fields": missing,
        "values": {k: payload.get(k) for k in required},
        "hard_failure": f"form.json missing required fields: {missing}" if missing else None,
    }


def inventory_calibration_library(cb_path: Path) -> dict:
    if not cb_path.exists():
        return {
            "file": str(cb_path),
            "exists": False,
            "entry_count": 0,
            "hard_failure": None,  # not critical — pipeline falls through to ODM/self-cal
            "warning": "cb_camera_library.json missing — calibration will fall through to ODM/self-cal lookup tiers",
        }
    try:
        payload = json.loads(cb_path.read_text())
        entries = payload.get("entries", {})
    except json.JSONDecodeError as exc:
        return {
            "file": str(cb_path),
            "exists": True,
            "entry_count": 0,
            "hard_failure": None,
            "warning": f"cb_camera_library.json present but not parseable: {exc}",
        }
    return {
        "file": str(cb_path),
        "exists": True,
        "entry_count": len(entries),
        "entry_keys": sorted(entries.keys()),
        "hard_failure": None,
    }


def inventory_spec(spec_path: Path) -> dict:
    if not spec_path.exists():
        return {
            "file": str(spec_path),
            "exists": False,
            "version": None,
            "hard_failure": "spec file missing",
        }
    try:
        spec = json.loads(spec_path.read_text())
    except json.JSONDecodeError as exc:
        return {
            "file": str(spec_path),
            "exists": True,
            "version": None,
            "hard_failure": f"spec file not valid JSON: {exc}",
        }
    return {
        "file": str(spec_path),
        "exists": True,
        "version": spec.get("_meta", {}).get("version"),
        "hard_failure": None,
    }


def collect_warnings(data: dict) -> list:
    warnings = []
    img = data["images"]
    if img.get("unrecognized_files"):
        warnings.append(f"{len(img['unrecognized_files'])} files in images folder have unrecognized extensions: {img['unrecognized_files'][:5]}")
    if len(img.get("extensions_seen", [])) > 1:
        warnings.append(f"mixed image formats detected: {img['extensions_seen']}")

    rinex = data["rinex"]
    if rinex.get("unrecognized_files"):
        names = [f["name"] for f in rinex["unrecognized_files"]]
        warnings.append(f"{len(names)} files in rinex folder have unrecognized extensions: {names}")
    if rinex.get("duplicate_candidates"):
        for dup in rinex["duplicate_candidates"]:
            warnings.append(f"possible duplicate RINEX files (same ext+size): {dup['files']}")

    bin_inv = data["bin"]
    if bin_inv.get("other_files"):
        warnings.append(f"non-.BIN files in telemetry folder: {bin_inv['other_files']}")

    cb = data["calibration_library"]
    if cb.get("warning"):
        warnings.append(cb["warning"])

    return warnings


def collect_hard_failures(data: dict) -> list:
    failures = []
    for section_key in ("spec", "images", "rinex", "bin", "user_input", "calibration_library"):
        section = data.get(section_key, {})
        if section.get("hard_failure"):
            failures.append({"section": section_key, "reason": section["hard_failure"]})
    return failures


def run(config: dict, project_root: Path) -> dict:
    inputs = config["inputs"]
    spec_path = project_root / config["spec_file"]
    cb_path = project_root / config["cb_library_file"]

    data = {
        "spec": inventory_spec(spec_path),
        "images": inventory_images(resolve_path(project_root, inputs["images_folder"])),
        "rinex": inventory_rinex(resolve_path(project_root, inputs["rinex_folder"])),
        "bin": inventory_bin(resolve_path(project_root, inputs["bin_folder"])),
        "user_input": inventory_user_input(resolve_path(project_root, inputs["user_input_file"])),
        "calibration_library": inventory_calibration_library(cb_path),
    }
    data["warnings"] = collect_warnings(data)
    data["hard_failures"] = collect_hard_failures(data)

    spec_version = data["spec"].get("version") or config.get("spec_version")

    envelope = {
        "spec_version": spec_version,
        "config_used": config,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": "stage1_inventory",
        "data": data,
    }
    return envelope


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: stage1_inventory.py <paths.json>", file=sys.stderr)
        return 2

    config_path = Path(sys.argv[1]).resolve()
    project_root = config_path.parent
    config = json.loads(config_path.read_text())

    envelope = run(config, project_root)

    out_path = project_root / config["outputs"]["stage1_inventory"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n")

    print(f"[stage1] wrote {out_path}")
    data = envelope["data"]
    print(f"[stage1] images: {data['images']['valid_count']}/{data['images']['file_count']} valid")
    print(f"[stage1] rinex:  obs={len(data['rinex']['observation_files'])} nav={len(data['rinex']['navigation_files'])} unknown={len(data['rinex']['unrecognized_files'])}")
    print(f"[stage1] bin:    {len(data['bin']['bin_files'])} file(s)")
    print(f"[stage1] form:   {'OK' if not data['user_input']['hard_failure'] else 'FAIL'}")
    print(f"[stage1] warnings ({len(data['warnings'])}):")
    for w in data["warnings"]:
        print(f"          - {w}")

    if data["hard_failures"]:
        print(f"[stage1] HARD FAILURES ({len(data['hard_failures'])}):", file=sys.stderr)
        for f in data["hard_failures"]:
            print(f"          - [{f['section']}] {f['reason']}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
