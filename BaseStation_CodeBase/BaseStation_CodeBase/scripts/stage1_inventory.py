#!/usr/bin/env python3
"""Stage 1 — Discovery & inventory.

Walks the three input folders (RINEX, Operation Log, User Input), classifies
each file by extension + lightweight content sniff, and writes
outputs/01_inventory.json.

Critical-set policy (this subsystem):
  CRITICAL  — at least one RINEX OBS file must be present.
              Hard-fail otherwise.
  WARN      — OPLOG instance absent: integrity degrades to 'unconfirmed' per
              the schema's nullability rule (never silent pass).
  WARN      — FORM instance absent: downstream setup-block gate (antenna
              height missing) and coverage gate (no flight times) will trip
              and zero the apex score legitimately.
  WARN      — RINEX header hardware fields blank: Hardware Override file
              (sample_data/hardware.json) will be consulted at Stage 2.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------- helpers ----------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(stage: str, config: dict, spec_version: str, data: dict) -> dict:
    return {
        "spec_version": spec_version,
        "config_used": config,
        "generated_at": utc_now_iso(),
        "stage": stage,
        "data": data,
    }


def _resolve_input_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


# ---------- file classifiers -------------------------------------------------

# RINEX observation file year-coded extensions: NNo / NNO  (e.g. 26o, 25o)
# RINEX navigation  file year-coded extensions: NNn / NNN, NNp / NNP, NNg, NNl
def _is_rinex_obs_ext(name: str) -> bool:
    low = name.lower()
    if low.endswith(".obs") or low.endswith(".o"):
        return True
    # year-suffix: two digits then 'o'
    return len(low) >= 4 and low[-4] == "." and low[-3:-1].isdigit() and low[-1] == "o"


def _is_rinex_nav_ext(name: str) -> bool:
    low = name.lower()
    if low.endswith((".nav", ".n", ".p", ".g", ".l", ".q")):
        return True
    return (
        len(low) >= 4
        and low[-4] == "."
        and low[-3:-1].isdigit()
        and low[-1] in ("n", "p", "g", "l", "q")
    )


def _sniff_rinex_header(path: Path) -> dict[str, Any]:
    """Read the first ~60 header lines to extract version, type, marker, antenna, etc.

    Stops at 'END OF HEADER' or after 100 lines. Read in binary then decode
    permissively so an unexpected codepoint never breaks the sniff.
    """
    info: dict[str, Any] = {
        "rinex_version": None,
        "file_type": None,
        "satellite_system": None,
        "marker_name": "",
        "marker_number": "",
        "antenna_type": "",
        "receiver_type": "",
        "approx_position_xyz": None,
        "antenna_delta_hen": None,
        "time_of_first_obs": None,
        "time_of_last_obs": None,
        "obs_systems_observed": [],
    }
    try:
        with path.open("rb") as fh:
            for i in range(200):
                raw = fh.readline()
                if not raw:
                    break
                line = raw.decode("ascii", errors="replace").rstrip("\r\n")
                label = line[60:].strip() if len(line) > 60 else ""
                content = line[:60]
                if label == "RINEX VERSION / TYPE":
                    ver = content[:9].strip()
                    info["rinex_version"] = ver
                    info["file_type"] = content[20:21].strip() or content[20:40].strip()
                    info["satellite_system"] = content[40:41].strip()
                elif label == "MARKER NAME":
                    info["marker_name"] = content.strip()
                elif label == "MARKER NUMBER":
                    info["marker_number"] = content.strip()
                elif label == "ANT # / TYPE":
                    info["antenna_type"] = content[20:40].strip()
                elif label == "REC # / TYPE / VERS":
                    info["receiver_type"] = content[20:40].strip()
                elif label == "APPROX POSITION XYZ":
                    parts = content.split()
                    if len(parts) >= 3:
                        try:
                            info["approx_position_xyz"] = [float(parts[0]), float(parts[1]), float(parts[2])]
                        except ValueError:
                            pass
                elif label == "ANTENNA: DELTA H/E/N":
                    parts = content.split()
                    if len(parts) >= 3:
                        try:
                            info["antenna_delta_hen"] = [float(parts[0]), float(parts[1]), float(parts[2])]
                        except ValueError:
                            pass
                elif label == "TIME OF FIRST OBS":
                    info["time_of_first_obs"] = content.strip()
                elif label == "TIME OF LAST OBS":
                    info["time_of_last_obs"] = content.strip()
                elif label == "SYS / # / OBS TYPES":
                    sysid = content[:1].strip()
                    if sysid and sysid not in info["obs_systems_observed"]:
                        info["obs_systems_observed"].append(sysid)
                elif label == "END OF HEADER":
                    break
    except OSError as exc:
        info["_sniff_error"] = str(exc)
    return info


def _classify_rinex(path: Path) -> dict[str, Any]:
    entry = {
        "path": str(path.name),
        "size_bytes": path.stat().st_size,
        "kind": "unknown",
        "header": {},
    }
    header = _sniff_rinex_header(path)
    entry["header"] = header
    ft = (header.get("file_type") or "").upper()
    if ft.startswith("O") or "OBSERVATION" in ft:
        entry["kind"] = "rinex_obs"
    elif ft.startswith("N") or "NAV" in ft:
        entry["kind"] = "rinex_nav"
    elif _is_rinex_obs_ext(path.name):
        entry["kind"] = "rinex_obs"
    elif _is_rinex_nav_ext(path.name):
        entry["kind"] = "rinex_nav"
    else:
        entry["kind"] = "rinex_unknown"
    return entry


# ---------- JSON instance vs schema ------------------------------------------

OPLOG_REQUIRED = {
    "session_completed_normally",
    "unexpected_shutdown_count",
    "battery_start_pct",
    "battery_end_pct",
    "battery_min_pct",
    "session_end_utc",
}
FORM_REQUIRED = {
    "antenna_model",
    "antenna_height_m",
    "antenna_height_units",
    "antenna_measurement_type",
    "measured_to_reference",
    "height_measured_count",
    "over_known_mark",
    "verified_by_second_person",
    "flight_start_utc",
    "flight_end_utc",
}


def _load_json_lenient(path: Path) -> dict[str, Any] | list[Any] | Any:
    with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
        return json.load(fh)


def _classify_json(path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": str(path.name),
        "size_bytes": path.stat().st_size,
        "kind": "unknown",
        "notes": [],
    }
    try:
        doc = _load_json_lenient(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        entry["kind"] = "json_unreadable"
        entry["notes"].append(str(exc))
        return entry

    if not isinstance(doc, dict):
        entry["kind"] = "json_unexpected_root"
        return entry

    is_schema = "$schema" in doc and ("title" in doc or "$id" in doc) and "properties" in doc
    if is_schema:
        title = (doc.get("title") or "").lower()
        if "operation log" in title:
            entry["kind"] = "oplog_schema"
        elif "user input" in title or "antenna setup" in title:
            entry["kind"] = "form_schema"
        else:
            entry["kind"] = "json_schema_other"
        entry["title"] = doc.get("title")
        return entry

    keys = set(doc.keys())
    if OPLOG_REQUIRED.issubset(keys) or {"session_completed_normally", "session_end_utc"} <= keys:
        entry["kind"] = "oplog_instance"
        entry["sample_keys"] = sorted(keys)
        return entry
    if FORM_REQUIRED.issubset(keys) or {"antenna_model", "antenna_height_m"} <= keys:
        entry["kind"] = "form_instance"
        entry["sample_keys"] = sorted(keys)
        return entry

    entry["kind"] = "json_other"
    entry["sample_keys"] = sorted(keys)
    return entry


# ---------- main inventory ---------------------------------------------------

def _walk(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]
    )


def _classify_file(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if _is_rinex_obs_ext(path.name) or _is_rinex_nav_ext(path.name) or suffix in {".obs", ".nav", ".rnx"}:
        return _classify_rinex(path)
    if suffix == ".json":
        return _classify_json(path)
    return {
        "path": path.name,
        "size_bytes": path.stat().st_size,
        "kind": f"other:{suffix or 'no_ext'}",
    }


def run(config: dict, project_root: Path, spec: dict) -> dict:
    inputs = config["inputs"]
    rinex_dir = _resolve_input_path(project_root, inputs["rinex_folder"])
    oplog_dir = _resolve_input_path(project_root, inputs["operator_log_folder"])
    form_dir = _resolve_input_path(project_root, inputs["user_input_folder"])

    rinex_files = [_classify_file(p) for p in _walk(rinex_dir)]
    oplog_files = [_classify_file(p) for p in _walk(oplog_dir)]
    form_files = [_classify_file(p) for p in _walk(form_dir)]

    obs_count = sum(1 for f in rinex_files if f["kind"] == "rinex_obs")
    nav_count = sum(1 for f in rinex_files if f["kind"] == "rinex_nav")
    oplog_instance_count = sum(1 for f in oplog_files if f["kind"] == "oplog_instance")
    form_instance_count = sum(1 for f in form_files if f["kind"] == "form_instance")

    warnings: list[dict] = []
    hard_failures: list[dict] = []

    # ---- critical: at least one RINEX OBS file ----
    if obs_count == 0:
        hard_failures.append({
            "code": "RINEX_OBS_ABSENT",
            "source_file_id": "SRC_BASE_RINEX",
            "message": f"No RINEX OBS file found in {inputs['rinex_folder']}",
        })

    # ---- warnings ----
    if nav_count == 0:
        warnings.append({
            "code": "RINEX_NAV_ABSENT",
            "source_file_id": "SRC_BASE_RINEX",
            "message": "No RINEX NAV/EPH file found. Not required for base scoring.",
        })

    if oplog_instance_count == 0:
        warnings.append({
            "code": "OPLOG_INSTANCE_ABSENT",
            "source_file_id": "SRC_BASE_OPLOG",
            "message": (
                "No Operation Log instance found. Integrity sub-score will degrade to "
                "'unconfirmed' (~60) per schema nullability rule — not a silent pass."
            ),
        })

    if form_instance_count == 0:
        warnings.append({
            "code": "FORM_INSTANCE_ABSENT",
            "source_file_id": "SRC_BASE_FORM",
            "message": (
                "No User Input form instance found. Setup block will gate-fail "
                "(ANTENNA_HEIGHT_MISSING) and coverage block will gate-fail "
                "(BASE_RINEX_FLIGHT_GAP) — apex score will be 0 by design."
            ),
        })

    # ---- Placeholder-status detection (operator forgot to overwrite) ----
    placeholder_files: list[dict] = []
    for label, folder in (
        ("sample_data/hardware.json", project_root / "sample_data"),
        ("operator_log_folder", oplog_dir),
        ("user_input_folder", form_dir),
    ):
        if label == "sample_data/hardware.json":
            candidates = [folder / "hardware.json"]
        else:
            candidates = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".json"] if folder.exists() else []
        for p in candidates:
            if not p.exists():
                continue
            try:
                doc = _load_json_lenient(p)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(doc, dict):
                continue
            status = doc.get("_status")
            if isinstance(status, str) and status.strip().upper().startswith("PLACEHOLDER"):
                placeholder_files.append({
                    "path": _display_path(p, project_root),
                    "_status": status,
                })
    if placeholder_files:
        warnings.append({
            "code": "PLACEHOLDER_INPUTS_DETECTED",
            "source_file_id": "multiple",
            "message": (
                f"{len(placeholder_files)} input file(s) still carry _status: PLACEHOLDER — operator must overwrite "
                "with real deployed values before this run is treated as a real survey."
            ),
            "detail": placeholder_files,
        })

    # ---- RINEX header hardware blanks → Hardware Override needed ----
    obs_with_blanks: list[dict] = []
    for f in rinex_files:
        if f["kind"] != "rinex_obs":
            continue
        h = f.get("header", {})
        blanks = [k for k in ("marker_name", "antenna_type", "receiver_type") if not h.get(k)]
        if blanks:
            obs_with_blanks.append({"file": f["path"], "blank_fields": blanks})
    if obs_with_blanks:
        warnings.append({
            "code": "RINEX_HEADER_BLANKS",
            "source_file_id": "SRC_BASE_RINEX",
            "message": (
                "RINEX header fields blank (typical of u-blox conversion). "
                "Hardware Override (sample_data/hardware.json) will be consulted "
                "at Stage 2 per 4-tier resolution priority."
            ),
            "detail": obs_with_blanks,
        })

    expected_sources = {sf["file_id"]: sf for sf in spec["source_files"]}

    summary = {
        "folders_walked": {
            "rinex_folder": str(rinex_dir),
            "operator_log_folder": str(oplog_dir),
            "user_input_folder": str(form_dir),
        },
        "counts": {
            "rinex_obs": obs_count,
            "rinex_nav": nav_count,
            "oplog_instance": oplog_instance_count,
            "form_instance": form_instance_count,
            "rinex_files_total": len(rinex_files),
            "oplog_files_total": len(oplog_files),
            "form_files_total": len(form_files),
        },
        "expected_source_files": {
            sid: {
                "file_name": meta["file_name"],
                "found": (
                    (sid == "SRC_BASE_RINEX" and obs_count > 0)
                    or (sid == "SRC_BASE_OPLOG" and oplog_instance_count > 0)
                    or (sid == "SRC_BASE_FORM" and form_instance_count > 0)
                ),
            }
            for sid, meta in expected_sources.items()
        },
        "files_by_folder": {
            "rinex_folder": rinex_files,
            "operator_log_folder": oplog_files,
            "user_input_folder": form_files,
        },
        "warnings": warnings,
        "hard_failures": hard_failures,
    }
    return summary


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: stage1_inventory.py <paths.json>", file=sys.stderr)
        return 2

    config_path = Path(argv[1]).resolve()
    project_root = config_path.parent
    with config_path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)
    with (project_root / config["spec_file"]).open("r", encoding="utf-8") as fh:
        spec = json.load(fh)

    data = run(config, project_root, spec)
    out_path = project_root / config["outputs"]["stage1_inventory"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env = envelope("stage1_inventory", config, spec["_meta"]["version"], data)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(env, fh, indent=2, sort_keys=True)

    n_warn = len(data["warnings"])
    n_fail = len(data["hard_failures"])
    print(f"[stage1] wrote {out_path}")
    print(f"[stage1] counts: {data['counts']}")
    print(f"[stage1] warnings={n_warn}  hard_failures={n_fail}")
    for w in data["warnings"]:
        print(f"  WARN  {w['code']}  ({w['source_file_id']})  {w['message']}")
    for h in data["hard_failures"]:
        print(f"  FAIL  {h['code']}  ({h['source_file_id']})  {h['message']}")

    if n_fail > 0 and config["options"].get("fail_fast", True):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
