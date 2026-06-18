#!/usr/bin/env python3
"""Shared helpers for the Pre-Processing confidence pipeline.

Every stage writes the same envelope shape (template rule 2) and obeys the
determinism rules (rule 3): sort_keys on output, no timestamps inside the
data block. These helpers are the single home for that contract.

Lifted as-is from the Check Point / GCP PPK builds (subsystem-agnostic - the
envelope + determinism contract is identical across every subsystem).
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    """ISO-8601 UTC timestamp with 6-digit microseconds, e.g.
    2026-06-01T19:30:00.123456Z. Only ever used in an envelope's
    generated_at - never inside a data block."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def load_config(config_path) -> dict:
    with Path(config_path).open(encoding="utf-8") as fh:
        return json.load(fh)


def load_spec(root: Path, config: dict) -> dict:
    with (root / config["spec_file"]).open(encoding="utf-8") as fh:
        return json.load(fh)


def make_envelope(stage: str, data: dict, config: dict, spec_version: str) -> dict:
    """Template rule 2 envelope. spec_version comes from the spec's
    _meta.version, not the config, so the artifact records what was
    actually scored against."""
    return {
        "spec_version": spec_version,
        "config_used": config,
        "generated_at": now_iso(),
        "stage": stage,
        "data": data,
    }


def write_envelope(out_path: Path, envelope: dict) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(envelope, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def export_results_to_csv(envelope: dict, csv_path: Path) -> None:
    """Export stage3d pre_processing_score envelope to a CSV file.
    
    Creates a single-row CSV with key metrics and all contributing scores.
    Flattens nested structures into readable column names.
    """
    import csv
    
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    
    data = envelope.get("data", {})
    config = envelope.get("config_used", {})
    
    # Extract key metrics
    row = {
        "survey_id": config.get("survey_id", ""),
        "subsystem": config.get("subsystem", ""),
        "generated_at": envelope.get("generated_at", ""),
        "spec_version": envelope.get("spec_version", ""),
        "pre_processing_score": data.get("pre_processing_score", ""),
        "verification_status": data.get("verification_status", {}).get("value", ""),
    }
    
    # Add verification details
    verif = data.get("verification_status", {})
    row.update({
        "cp_count": verif.get("cp_designated_count", ""),
        "cp_distribution_coverage": verif.get("cp_distribution_coverage", ""),
        "cp_independence_m": verif.get("cp_gcp_spatial_independence_m", ""),
        "cp_sigma_score": verif.get("cp_sigma_score", ""),
    })
    
    # Add building block scores
    for contrib in data.get("contributions", []):
        block_name = contrib.get("block_name", "").replace(" ", "_")
        row[f"{block_name}_score"] = contrib.get("block_score", "")
        row[f"{block_name}_weight"] = contrib.get("weight_in_apex", "")
        row[f"{block_name}_contribution"] = contrib.get("contribution", "")
    
    # Add per-artifact view scores
    for view_name, view_score in data.get("per_artifact_views_summary", {}).items():
        row[view_name] = view_score
    
    # Add flag summary
    row["total_flags"] = len(data.get("all_flags_aggregated", []))
    row["global_gate_triggered"] = data.get("global_gate", {}).get("triggered", False)
    
    # Get all field names in consistent order
    fieldnames = sorted(row.keys())
    
    # Write CSV
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)


def export_all_stages_to_csv(root: Path, config: dict, envelopes: dict, csv_prefix: str) -> None:
    """Export all stage outputs to individual CSV files.
    
    Args:
        root: Project root directory
        config: Pipeline config dict
        envelopes: Dict of {stage_name: envelope} for all stages
        csv_prefix: Prefix for CSV filenames (e.g., "19thmay")
    """
    import csv
    
    out_dir = root / "csv_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Export stage3d_score (main result)
    if "stage3d_score" in envelopes:
        csv_path = out_dir / f"{csv_prefix}_pre_processing_score.csv"
        export_results_to_csv(envelopes["stage3d_score"], csv_path)
        print(f"Exported CSV -> {csv_path.relative_to(root)}")
