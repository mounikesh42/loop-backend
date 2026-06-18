#!/usr/bin/env python3
"""Stage 1 - Discovery & inventory for Processing (survey-level).

Processing scores ONE survey from 4 named sources (spec sheet 01). This stage
discovers and classifies each, sniffs file content by magic bytes, scans the
JSON/CSV inputs for placeholder markers, and writes outputs/01_inventory.json.

The 4 sources and their criticality (see critical_set_policy in the data block):

    SRC_PROC_REPORT       Agisoft PDF report     CRITICAL  (the evidence base; 67/90
                                                            source fields; Option A:
                                                            processing_score=null without it)
    SRC_PROC_MANIFEST     processing manifest     REQUIRED  (absence -> report_and_manifest
                          (.json)                            indicators incl. CV1 cp_rmse
                                                            degrade to N/A + redistribute)
    SRC_PROC_DELIVERABLES 5 typed output files    REQUIRED  (per-type absence -> DO7 flag
                          (tif/las/obj)                      PROC_DELIVERABLE_FILE_MISSING +
                                                            that per-deliverable view = null)
    SRC_PROC_PP_HANDOFF   pp gcp coords +         OPTIONAL  (absence -> CV4/CV5/DO3 cross-
                          pp manifest                        source indicators degrade to N/A)

Hard-fails (when fail_fast) ONLY on a missing/unreadable REPORT - it is the sole
source whose absence makes processing_score null per the spec. Everything else
degrades through spec-defined N/A redistribution, not a hard stop.

CRS extraction from the deliverables is deferred to the Stage 2 parser; Stage 1
only confirms presence + content type (magic bytes), per the discovery contract.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

STAGE = "stage1_inventory"

# deliverable type -> the spec presence field it backs + the view it feeds
_DELIVERABLE_TYPES = {
    "ortho":       ("deliverable_ortho_present", "ortho_score", {"tiff", "bigtiff"}),
    "dsm":         ("deliverable_dsm_present", "dsm_score", {"tiff", "bigtiff"}),
    "dtm":         ("deliverable_dtm_present", "dtm_score", {"tiff", "bigtiff"}),
    "point_cloud": ("deliverable_point_cloud_present", "point_cloud_score", {"las"}),
    "mesh_3d":     ("deliverable_mesh_3d_present", "mesh_3d_score", {"obj", "ply", "text"}),
}


def _magic_kind(path: Path):
    """Light content sniff by leading bytes. Returns a kind string, None for
    unknown/text, or 'UNREADABLE'."""
    try:
        with path.open("rb") as fh:
            head = fh.read(16)
    except OSError:
        return "UNREADABLE"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head[:4] in (b"II+\x00", b"MM\x00+"):
        return "bigtiff"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head.startswith(b"LASF"):
        return "las"
    if head.startswith(b"{") or head.startswith(b"["):
        return "json"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "binary"


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

    warnings: list[dict] = []
    hard_failures: list[dict] = []
    placeholder_files: list[str] = []
    ext_counts: dict[str, int] = {}
    artifacts: dict[str, dict] = {}
    spec_files = {sf["file_id"]: sf for sf in spec["source_files"]}

    if config.get("spec_version") != spec_version:
        warnings.append({
            "code": "CONFIG_SPEC_VERSION_DRIFT",
            "detail": (f"paths.json spec_version={config.get('spec_version')} "
                       f"!= spec _meta.version={spec_version}"),
        })

    def _count_ext(p: Path):
        ext_counts[p.suffix.lower()] = ext_counts.get(p.suffix.lower(), 0) + 1

    # ---- SRC_PROC_REPORT : the critical evidence base (PDF) ------------------
    rel = inp.get("report_file")
    rpath = (root / rel) if rel else None
    rkind = _magic_kind(rpath) if (rpath and rpath.is_file()) else None
    report_present = bool(rpath and rpath.is_file())
    report_info = {
        "file_id": "SRC_PROC_REPORT",
        "file_name": spec_files.get("SRC_PROC_REPORT", {}).get("file_name"),
        "path": rel,
        "present": report_present,
        "critical": True,
        "content_kind": rkind,
    }
    if report_present:
        report_info["size_bytes"] = rpath.stat().st_size
        _count_ext(rpath)
        if rkind != "pdf":
            report_info["content_mismatch"] = True
            warnings.append({"code": "REPORT_NOT_PDF",
                             "detail": f"{rel} content kind={rkind}, expected pdf"})
    else:
        hard_failures.append({
            "code": "REPORT_MISSING",
            "detail": (f"critical source SRC_PROC_REPORT absent at {rel}; processing_score "
                       "is null without the Agisoft report (Option A locked)"),
        })
    artifacts["report"] = report_info

    # ---- SRC_PROC_MANIFEST : required, graceful N/A degrade on absence -------
    rel = inp.get("manifest_file")
    mpath = (root / rel) if rel else None
    manifest_present = bool(mpath and mpath.is_file())
    manifest_info = {
        "file_id": "SRC_PROC_MANIFEST",
        "file_name": spec_files.get("SRC_PROC_MANIFEST", {}).get("file_name"),
        "path": rel,
        "present": manifest_present,
        "critical": False,
    }
    if manifest_present:
        manifest_info["size_bytes"] = mpath.stat().st_size
        _count_ext(mpath)
        status = _read_json_status(mpath)
        manifest_info["status"] = status
        if status == "PLACEHOLDER":
            placeholder_files.append(rel)
        elif status == "UNREADABLE":
            manifest_info["unreadable"] = True
            warnings.append({"code": "MANIFEST_UNREADABLE", "detail": f"{rel} is not valid JSON"})
    else:
        warnings.append({
            "code": "MANIFEST_ABSENT",
            "detail": ("no processing manifest; report_and_manifest indicators "
                       "(precalib, camera-model, CV1 cp_rmse, gcp_rmse, role-consistency, "
                       "CRS-match gate, dtm-classification) degrade to N/A and redistribute"),
        })
    artifacts["manifest"] = manifest_info

    # ---- SRC_PROC_DELIVERABLES : 5 typed files; per-type view null on absence -
    deliverables = inp.get("deliverables", {})
    deliv_entries: dict[str, dict] = {}
    views_null: list[str] = []
    for dtype, (presence_field, view_name, ok_kinds) in _DELIVERABLE_TYPES.items():
        drel = deliverables.get(dtype)
        dpath = (root / drel) if drel else None
        present = bool(dpath and dpath.is_file())
        entry = {
            "present": present,
            "path": drel,
            "presence_field": presence_field,
            "feeds_view": view_name,
        }
        if present:
            entry["size_bytes"] = dpath.stat().st_size
            _count_ext(dpath)
            kind = _magic_kind(dpath)
            entry["content_kind"] = kind
            if kind not in ok_kinds and kind not in ("text",):
                entry["content_mismatch"] = True
                warnings.append({"code": "DELIVERABLE_CONTENT_MISMATCH",
                                 "detail": f"{dtype}: {drel} kind={kind}, expected {sorted(ok_kinds)}"})
        else:
            views_null.append(view_name)
            warnings.append({
                "code": "DELIVERABLE_FILE_MISSING",
                "detail": (f"{dtype} deliverable absent -> DO7 PROC_DELIVERABLE_FILE_MISSING "
                           f"will fire + {view_name} view returns null"),
            })
        deliv_entries[dtype] = entry
    present_count = sum(1 for e in deliv_entries.values() if e["present"])
    if present_count:
        # at least one deliverable present -> the SRC counts as discovered
        pass
    artifacts["deliverables"] = {
        "file_id": "SRC_PROC_DELIVERABLES",
        "file_name": spec_files.get("SRC_PROC_DELIVERABLES", {}).get("file_name"),
        "present": present_count > 0,
        "critical": False,
        "types_present": present_count,
        "types_total": len(_DELIVERABLE_TYPES),
        "by_type": deliv_entries,
        "views_null_due_to_missing_files": sorted(views_null),
    }

    # ---- SRC_PROC_PP_HANDOFF : optional cross-source reference ----------------
    pp = inp.get("pp_handoff", {})
    pp_entries: dict[str, dict] = {}
    pp_any = False
    for key in ("gcp_coord_file", "pp_manifest_file"):
        prel = pp.get(key)
        ppath = (root / prel) if prel else None
        present = bool(ppath and ppath.is_file())
        pp_any = pp_any or present
        e = {"present": present, "path": prel}
        if present:
            e["size_bytes"] = ppath.stat().st_size
            _count_ext(ppath)
            if ppath.suffix.lower() == ".json":
                st = _read_json_status(ppath)
                e["status"] = st
                if st == "PLACEHOLDER":
                    placeholder_files.append(prel)
            elif ppath.suffix.lower() == ".csv" and _csv_has_placeholder(ppath):
                placeholder_files.append(prel)
        pp_entries[key] = e
    if not pp_any:
        warnings.append({
            "code": "PP_HANDOFF_ABSENT",
            "detail": ("no pre-processing handoff data; cross-source indicators "
                       "CV4 (role consistency), CV5 (gcp coord typo), DO3 (internal "
                       "transform) degrade to N/A and redistribute"),
        })
    artifacts["pp_handoff"] = {
        "file_id": "SRC_PROC_PP_HANDOFF",
        "file_name": spec_files.get("SRC_PROC_PP_HANDOFF", {}).get("file_name"),
        "present": pp_any,
        "critical": False,
        "by_file": pp_entries,
    }

    if placeholder_files:
        warnings.append({
            "code": "PLACEHOLDER_INPUTS_DETECTED",
            "detail": ("operator-pending placeholder inputs in use; replace with real "
                       "exports before a production survey"),
            "files": sorted(set(placeholder_files)),
        })

    spec_source_ids = [sf["file_id"] for sf in spec["source_files"]]
    discovered_ids = sorted({a["file_id"] for a in artifacts.values() if a["present"]})
    summary = {
        "expected_source_files": spec["_meta"]["counts"]["source_files"],
        "sources_present": sum(1 for a in artifacts.values() if a["present"]),
        "critical_present": sum(1 for a in artifacts.values() if a.get("critical") and a["present"]),
        "critical_total": sum(1 for a in artifacts.values() if a.get("critical")),
        "deliverable_types_present": artifacts["deliverables"]["types_present"],
        "views_null_count": len(views_null),
        "placeholder_input_count": len(set(placeholder_files)),
        "warning_count": len(warnings),
        "hard_failure_count": len(hard_failures),
    }

    data = {
        "survey_level": True,
        "critical_set_policy": (
            "CRITICAL = {SRC_PROC_REPORT} only. The Agisoft report is the evidence base "
            "(67/90 source fields); Option A is locked, so processing_score is null without "
            "it -> hard-fail when fail_fast. REQUIRED-BUT-GRACEFUL = {SRC_PROC_MANIFEST, "
            "SRC_PROC_DELIVERABLES}: manifest absence degrades report_and_manifest indicators "
            "(including the CV1 cp_rmse moment-of-truth) to N/A with weight redistribution; "
            "each missing deliverable fires DO7 PROC_DELIVERABLE_FILE_MISSING and nulls that "
            "one per-deliverable view, leaving processing_score itself intact. OPTIONAL = "
            "{SRC_PROC_PP_HANDOFF}: absence degrades the three cross-source indicators "
            "(CV4/CV5/DO3) to N/A. No RINEX/NAV/hardware-override files exist in processing."),
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
          f"sources present: {s['sources_present']}/{s['expected_source_files']}")
    a = d["artifacts"]
    for key in ("report", "manifest"):
        info = a[key]
        tag = "CRIT" if info.get("critical") else "req "
        mark = "OK " if info["present"] else "-- "
        extra = f"status={info['status']}" if info.get("status") else (
            f"{info.get('content_kind')} {info.get('size_bytes')}B" if info["present"] else "ABSENT")
        print(f"    [{mark}][{tag}] {info['file_id']:22s} {extra}")
    dv = a["deliverables"]
    print(f"    [{'OK ' if dv['present'] else '-- '}][opt ] {dv['file_id']:22s} "
          f"types {dv['types_present']}/{dv['types_total']} present")
    for dtype, e in dv["by_type"].items():
        mk = "OK " if e["present"] else "-- "
        ex = f"{e.get('content_kind')} {e.get('size_bytes')}B" if e["present"] else f"missing -> {e['feeds_view']}=null"
        print(f"        [{mk}] {dtype:12s} {ex}")
    pph = a["pp_handoff"]
    print(f"    [{'OK ' if pph['present'] else '-- '}][opt ] {pph['file_id']:22s} "
          f"{'present' if pph['present'] else 'ABSENT -> CV4/CV5/DO3 N/A'}")
    print(f"  placeholders: {s['placeholder_input_count']}  warnings: {s['warning_count']}  "
          f"hard failures: {s['hard_failure_count']}")
    for w in d["warnings"]:
        print(f"    WARN  {w['code']}")
    for hf in hard_failures:
        print(f"    FAIL  {hf['code']}: {hf['detail']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Processing Stage 1 inventory")
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
