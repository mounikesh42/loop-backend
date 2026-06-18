#!/usr/bin/env python3
"""Convert existing JSON outputs to individual CSV files."""

import csv
import json
import sys
from pathlib import Path


def flatten_dict(d, parent_key="", sep="_"):
    """Flatten nested dictionaries."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        elif isinstance(v, (list, tuple)):
            # Convert lists to JSON string
            items.append((new_key, json.dumps(v)))
        else:
            items.append((new_key, v))
    return dict(items)


def json_to_csv(json_file, csv_file):
    """Convert a JSON file to CSV."""
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Extract the envelope data
    envelope = data.get("envelope", data)
    
    if not isinstance(envelope, list):
        # Single object or nested structure - wrap in list
        envelope = [envelope]
    
    if not envelope:
        print(f"  ⊘ {json_file.name} (empty data, skipping)")
        return
    
    # Flatten all records
    flat_records = [flatten_dict(record) for record in envelope]
    
    # Get all unique keys and sort
    all_keys = set()
    for record in flat_records:
        all_keys.update(record.keys())
    fieldnames = sorted(all_keys)
    
    # Write CSV
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat_records)
    
    row_count = len(flat_records)
    col_count = len(fieldnames)
    print(f"  ✓ {json_file.name} → {csv_file.name} ({row_count} rows, {col_count} cols)")


def main():
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1]).resolve()
    else:
        config_path = Path("paths.json").resolve()
    
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 1
    
    # Load config
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    root = config_path.parent
    outputs = config.get("outputs", {})
    
    print(f"📁 Converting JSON outputs to CSV:\n")
    
    for key, rel_path in outputs.items():
        json_file = root / rel_path
        if not json_file.exists():
            print(f"  ⊘ {json_file.name} not found")
            continue
        
        # Create CSV in same directory as JSON, with same name but .csv extension
        csv_file = json_file.with_suffix(".csv")
        
        try:
            json_to_csv(json_file, csv_file)
        except Exception as e:
            print(f"  ✗ ERROR: {e}", file=sys.stderr)
            return 1
    
    print(f"\n✅ All conversions complete!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
