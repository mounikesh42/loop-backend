#!/usr/bin/env python3
"""parse_report.py - SRC_PP_REPORT parser (OPTIONAL, survey-level).

Emits L1F_PP_057..062 from the optional processing report. The report deepens
diagnostic depth: it unlocks the 5 report-tier ('detailed_manifest_with_report')
indicators and gives tie-point density to overlap_texture. When the report is
absent (the operational reality, and the gold-standard baseline), those
indicators score ADVISORY and redistribute their weight - the score is not
penalised for a missing report.

Report-tier source fields -> indicators they feed:
    L1F_PP_057 cors_epoch_coverage_during_flight -> cors_data_continuity_score
    L1F_PP_058 time_sync_residuals               -> time_sync_residual_score
    L1F_PP_059 per_gcp_residuals                 -> gcp_residual_score
    L1F_PP_060 cors_quality_metrics              -> cors_station_health_score
    L1F_PP_061 report_actual_settings            -> settings_consistency_score (Approach 2)
    L1F_PP_062 tiepoint_density                  -> overlap_texture_score (replaces declared)

v1 supports a STRUCTURED JSON report (testable, vendor-neutral). Real TBC
adjustment reports are PDF/XML; that extraction is deferred to v2 - a PDF/XML/TXT
report is recorded as present-but-unparsed (fields null) with an honest note,
NOT silently treated as healthy.

The parser raises NO spec flags (all PP flags fire at Stage 3a/3b/3c/3d).

parse(report_file, project_root=None) -> {"fields", "parser_meta"}.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PARSER_ID = "parse_report"
PARSER_VERSION = "1.0"
SOURCE_FILE_ID = "SRC_PP_REPORT"
SOURCE_FILE_NAME = "Processing Report (optional)"

# L1F field key -> json key in a structured report
L1F_SPEC = {
    "L1F_PP_057_cors_epoch_coverage_during_flight": "cors_epoch_coverage_during_flight",
    "L1F_PP_058_time_sync_residuals": "time_sync_residuals",
    "L1F_PP_059_per_gcp_residuals": "per_gcp_residuals",
    "L1F_PP_060_cors_quality_metrics": "cors_quality_metrics",
    "L1F_PP_061_report_actual_settings": "report_actual_settings",
    "L1F_PP_062_tiepoint_density": "tiepoint_density",
}

_ABSENT_NOTE = (
    "Processing report ABSENT -> L1F_PP_057..062 null. Per spec this is not a "
    "penalty: the 5 report-tier indicators (cors_data_continuity, time_sync_residual, "
    "gcp_residual, cors_station_health, settings_consistency) score ADVISORY and "
    "redistribute their weight within their blocks; overlap_texture falls back to "
    "declared planned overlap; tie-point density is unavailable. Reports are not "
    "reliably uploaded in operational reality."
)


def _empty_fields() -> dict[str, Any]:
    return {k: None for k in L1F_SPEC}


def _result(fields, report_present, report_format, fields_present, notes):
    return {
        "fields": dict(sorted(fields.items())),
        "parser_meta": {
            "parser_id": PARSER_ID,
            "parser_version": PARSER_VERSION,
            "source_file_id": SOURCE_FILE_ID,
            "source_file_name": SOURCE_FILE_NAME,
            "instance_found": report_present,
            "report_present": report_present,
            "report_format": report_format,
            "report_tier_unlocked": report_present and bool(fields_present),
            "fields_present": fields_present,
            "fields_provided": list(L1F_SPEC.keys()),
            "notes": notes,
            "flags_raised": [],
        },
    }


def parse(report_file, project_root: Path | None = None) -> dict[str, Any]:
    notes: list[str] = []
    fields = _empty_fields()
    path = Path(report_file) if report_file else None

    if path is None or not path.is_file():
        notes.append(_ABSENT_NOTE)
        return _result(fields, False, None, [], notes)

    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            with path.open(encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            notes.append(f"Report {path.name} unreadable JSON ({exc}); treated as absent (advisory).")
            return _result(fields, False, "json_unreadable", [], notes)
        if not isinstance(doc, dict):
            notes.append(f"Report root in {path.name} is not an object; treated as absent.")
            return _result(fields, False, "json_non_object", [], notes)
        if doc.get("_status") == "PLACEHOLDER":
            notes.append(f"{path.name} is a PLACEHOLDER report (Section 8 lifecycle).")
        present = []
        for l1f, jk in L1F_SPEC.items():
            if jk in doc:
                fields[l1f] = doc[jk]
                present.append(l1f)
        missing = [jk for l1f, jk in L1F_SPEC.items() if l1f not in present]
        if missing:
            notes.append(f"Structured report present but missing keys {missing}; those stay "
                         "null (their indicators remain advisory/redistribute).")
        notes.append(f"Structured JSON report parsed; report-tier unlocked for {len(present)}/6 fields.")
        return _result(fields, True, "json", present, notes)

    # PDF / XML / TXT -> present but not extracted in v1
    notes.append(f"Report present as '{suffix}' (TBC PDF/XML/TXT). v1 extraction deferred to v2 "
                 "(TBC adjustment-report parser); fields kept null and treated as advisory - NOT "
                 "silently assumed healthy. Supply a structured JSON report to unlock report-tier "
                 "scoring in v1.")
    return _result(fields, True, suffix.lstrip("."), [], notes)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Parse an optional pre-processing report")
    parser.add_argument("report_file")
    args = parser.parse_args(argv)
    out = parse(Path(args.report_file), Path("."))
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
