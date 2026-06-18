#!/usr/bin/env python3
"""Export pipeline results to Excel (.xlsx) with multiple sheets and custom naming.

Usage:
    python export_to_excel.py <paths.json> <output_name>
    
Example:
    python export_to_excel.py paths.json 19thmay
    → Creates outputs/19thmay.xlsx with all stages as separate sheets
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd


def flatten_dict(obj: Any, prefix: str = "") -> dict:
    """Recursively flatten nested dicts/lists into a format suitable for Excel."""
    result = {}
    
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (dict, list)):
                result.update(flatten_dict(value, new_key))
            else:
                result[new_key] = value
    elif isinstance(obj, list):
        if not obj:
            if prefix:
                result[prefix] = "[]"
        elif isinstance(obj[0], dict):
            # List of dicts → DataFrame rows
            for i, item in enumerate(obj):
                result.update(flatten_dict(item, f"{prefix}[{i}]"))
        else:
            # Simple list → serialize as JSON string
            if prefix:
                result[prefix] = json.dumps(obj)
    else:
        if prefix:
            result[prefix] = obj
    
    return result


def json_to_dataframe(json_data: dict | list) -> pd.DataFrame:
    """Convert JSON structure to pandas DataFrame."""
    if isinstance(json_data, list):
        if not json_data:
            return pd.DataFrame()
        if isinstance(json_data[0], dict):
            # List of records
            return pd.DataFrame(json_data)
        else:
            # List of values
            return pd.DataFrame({"values": json_data})
    elif isinstance(json_data, dict):
        if not json_data:
            return pd.DataFrame()
        # Try to detect if it's records-like
        if all(isinstance(v, dict) for v in json_data.values()):
            # Dict of objects → convert to DataFrame
            records = [{"key": k, **v} for k, v in json_data.items()]
            return pd.DataFrame(records)
        else:
            # Single record → convert to Series, reshape to DataFrame
            return pd.DataFrame([json_data])
    else:
        return pd.DataFrame([{"value": json_data}])


def extract_data_sheets(json_envelope: dict) -> dict[str, pd.DataFrame]:
    """Extract all data from JSON envelope into multiple sheets."""
    sheets = {}
    
    if "data" not in json_envelope:
        return sheets
    
    data = json_envelope["data"]
    
    # Handle different data structures
    if isinstance(data, dict):
        for key, value in data.items():
            sheet_name = key.replace("_", " ").title()[:31]  # Excel sheet name limit
            try:
                df = json_to_dataframe(value)
                if not df.empty:
                    sheets[sheet_name] = df
            except Exception as e:
                print(f"  Warning: Could not convert '{key}' to DataFrame: {e}")
    
    return sheets


def load_json_file(filepath: Path) -> dict:
    """Load JSON file."""
    with filepath.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def export_to_excel(config_path: Path, output_name: str) -> int:
    """Export all JSON results to a single XLSX file with multiple sheets."""
    project_root = config_path.parent
    
    # Load config
    config = load_json_file(config_path)
    outputs = config.get("outputs", {})
    
    if not outputs:
        print("ERROR: No outputs defined in config", file=sys.stderr)
        return 1
    
    print(f"[export_to_excel] Loading results from {project_root}")
    print(f"[export_to_excel] Output name: {output_name}")
    
    # Collect all sheets from all output JSON files
    all_sheets = {}
    stage_order = [
        "stage1_inventory",
        "stage2_source_fields",
        "stage3_derived",
        "stage3_indicators",
        "stage3_building_blocks",
        "stage3_base_score",
    ]
    
    for stage in stage_order:
        if stage not in outputs:
            continue
        
        output_rel = outputs[stage]
        output_path = project_root / output_rel
        
        if not output_path.exists():
            print(f"  ⚠ {stage} not found at {output_rel}")
            continue
        
        print(f"  Loading {stage}...", end="")
        try:
            json_data = load_json_file(output_path)
            sheets = extract_data_sheets(json_data)
            
            # Add stage-level metadata sheet
            metadata = {
                "config_used": json_data.get("config_used", {}),
                "generated_at": json_data.get("generated_at", ""),
                "spec_version": json_data.get("spec_version", ""),
                "stage": json_data.get("stage", ""),
            }
            metadata_df = pd.DataFrame([metadata])
            stage_name = stage.replace("_", " ").title()
            all_sheets[f"{stage_name}_Meta"] = metadata_df
            
            # Merge data sheets with stage prefix
            for sheet_name, df in sheets.items():
                prefixed_name = f"{stage_name} - {sheet_name}"[:31]  # Excel limit
                all_sheets[prefixed_name] = df
            
            print(f" ✓ ({len(sheets)} sheets)")
        except Exception as e:
            print(f" ✗ Error: {e}")
            continue
    
    if not all_sheets:
        print("ERROR: No data sheets extracted", file=sys.stderr)
        return 1
    
    # Export to Excel
    output_file = project_root / "outputs" / f"{output_name}.xlsx"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"\n[export_to_excel] Writing {len(all_sheets)} sheets to {output_file.name}...", end="")
    try:
        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            for sheet_name, df in all_sheets.items():
                # Limit columns displayed and truncate sheet names
                display_cols = min(len(df.columns), 50)  # Practical Excel limit
                df_export = df.iloc[:, :display_cols]
                
                # Truncate sheet name to 31 chars (Excel limit)
                safe_name = sheet_name[:31]
                df_export.to_excel(writer, sheet_name=safe_name, index=False)
        
        print(f" ✓")
        print(f"[export_to_excel] Success! Output: {output_file}")
        print(f"[export_to_excel] Total sheets: {len(all_sheets)}")
        return 0
    except Exception as e:
        print(f" ✗")
        print(f"ERROR: Could not write Excel file: {e}", file=sys.stderr)
        return 1


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(
            "usage: export_to_excel.py <paths.json> <output_name>",
            file=sys.stderr,
        )
        print(
            "\nexample: export_to_excel.py paths.json 19thmay",
            file=sys.stderr,
        )
        print(
            "         → Creates outputs/19thmay.xlsx",
            file=sys.stderr,
        )
        return 2
    
    config_path = Path(argv[1]).resolve()
    output_name = argv[2]
    
    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        return 1
    
    return export_to_excel(config_path, output_name)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
