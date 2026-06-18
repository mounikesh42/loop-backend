#!/usr/bin/env python3
"""Export drone pipeline results to CSV/Excel with custom naming.

Usage:
  python export_results.py <paths.json> <output_name>

Example:
  python export_results.py paths.json 19thmay
  → Generates: 19thmay.xlsx in the current directory

The export flattens the hierarchical JSON results into a single sheet
with all key metrics, scores, and flags in columns.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False


def flatten_dict(d: Dict, parent_key: str = '', sep: str = '_') -> Dict:
    """Recursively flatten nested dictionaries."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            # For lists, convert to comma-separated string (useful for flags)
            if v and isinstance(v[0], dict):
                # List of dicts - extract key fields
                items.append((new_key, json.dumps(v)))
            else:
                items.append((new_key, "; ".join(str(x) for x in v)))
        else:
            items.append((new_key, v))
    return dict(items)


def extract_summary_row(drone_score_envelope: dict) -> Dict[str, Any]:
    """Extract key results into a single summary row."""
    data = drone_score_envelope.get("data", {})
    config = drone_score_envelope.get("config_used", {})
    
    row = {
        "generated_at": drone_score_envelope.get("generated_at"),
        "survey_id": config.get("survey_id"),
        "spec_version": drone_score_envelope.get("spec_version"),
        "stage": drone_score_envelope.get("stage"),
        
        # Main scores
        "drone_score": data.get("drone_score"),
        "raw_weighted_sum": data.get("raw_weighted_sum"),
        "global_gate_triggered": data.get("global_gate_triggered"),
        
        # Block contributions (flattened)
        "img_capture_score": data.get("block_contributions", {}).get("BB_IMG_CAPTURE", {}).get("block_score"),
        "img_capture_contribution": data.get("block_contributions", {}).get("BB_IMG_CAPTURE", {}).get("contribution"),
        "rover_gnss_score": data.get("block_contributions", {}).get("BB_ROVER_GNSS", {}).get("block_score"),
        "rover_gnss_contribution": data.get("block_contributions", {}).get("BB_ROVER_GNSS", {}).get("contribution"),
        "mission_exec_score": data.get("block_contributions", {}).get("BB_MISSION_EXEC", {}).get("block_score"),
        "mission_exec_contribution": data.get("block_contributions", {}).get("BB_MISSION_EXEC", {}).get("contribution"),
        
        # Calibration info
        "cal_conf_score": data.get("cal_conf_parallel", {}).get("score"),
        
        # Flag summary
        "total_flags": data.get("all_flags_count"),
        "flags_critical": len(data.get("all_flags_by_severity", {}).get("CRITICAL", [])),
        "flags_high": len(data.get("all_flags_by_severity", {}).get("HIGH", [])),
        "flags_medium": len(data.get("all_flags_by_severity", {}).get("MEDIUM", [])),
        "flags_low": len(data.get("all_flags_by_severity", {}).get("LOW", [])),
        "flags_list": "; ".join([
            f["flag_name"] for f in data.get("all_flags_aggregated", [])
        ]) or "None",
    }
    
    return row


def export_to_excel(drone_score_envelope: dict, output_path: Path) -> None:
    """Export results to Excel file."""
    if not PANDAS_AVAILABLE:
        raise ImportError("pandas is required for Excel export. Install with: pip install pandas openpyxl")
    
    # Extract summary row
    row = extract_summary_row(drone_score_envelope)
    
    # Create DataFrame
    df = pd.DataFrame([row])
    
    # Write to Excel
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(output_path, sheet_name="Results", index=False)
    print(f"✓ Exported to: {output_path}")
    print(f"  Columns: {len(df.columns)}")
    print(f"  Rows: {len(df)}")


def export_to_csv(drone_score_envelope: dict, output_path: Path) -> None:
    """Export results to CSV file."""
    row = extract_summary_row(drone_score_envelope)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write CSV
    with output_path.open('w', newline='', encoding='utf-8') as f:
        # Write headers
        f.write(",".join(row.keys()) + "\n")
        # Write values (with proper escaping for CSV)
        values = []
        for v in row.values():
            if v is None:
                values.append("")
            elif isinstance(v, bool):
                values.append(str(v))
            elif isinstance(v, (int, float)):
                values.append(str(v))
            else:
                # Quote strings that contain commas or quotes
                s = str(v)
                if "," in s or '"' in s:
                    s = '"' + s.replace('"', '""') + '"'
                values.append(s)
        f.write(",".join(values) + "\n")
    
    print(f"✓ Exported to: {output_path}")
    print(f"  Columns: {len(row)}")


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: export_results.py <paths.json> <output_name>")
        print()
        print("Examples:")
        print("  python export_results.py paths.json 19thmay")
        print("    → Generates: 19thmay.xlsx")
        print()
        print("  python export_results.py paths.json 20may.csv")
        print("    → Generates: 20may.csv")
        return 2
    
    config_path = Path(sys.argv[1]).resolve()
    output_name = sys.argv[2]
    
    # Determine output format from name or default to xlsx
    if output_name.endswith('.csv'):
        output_file = Path(output_name)
    elif output_name.endswith('.xlsx'):
        output_file = Path(output_name)
    else:
        # Default to xlsx
        output_file = Path(f"{output_name}.xlsx")
    
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        return 1
    
    # Load config and find drone_score.json
    project_root = config_path.parent
    config = json.loads(config_path.read_text())
    
    drone_score_path = project_root / config["outputs"]["stage3_drone_score"]
    
    if not drone_score_path.exists():
        print(f"Error: Drone score file not found: {drone_score_path}", file=sys.stderr)
        print(f"  Make sure you've run the full pipeline first", file=sys.stderr)
        return 1
    
    # Load drone score
    drone_score_envelope = json.loads(drone_score_path.read_text())
    
    # Export in requested format
    if output_file.suffix.lower() == '.csv':
        export_to_csv(drone_score_envelope, output_file)
    else:
        export_to_excel(drone_score_envelope, output_file)
    
    # Print summary
    data = drone_score_envelope.get("data", {})
    print()
    print("=" * 50)
    print(f"  DRONE SCORE: {data.get('drone_score')}")
    print(f"  Generated: {drone_score_envelope.get('generated_at')}")
    print(f"  Total Flags: {data.get('all_flags_count')}")
    print("=" * 50)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
