#!/usr/bin/env python3
"""Convenience wrapper: Run full pipeline + export results with custom name.

Usage:
  python pipeline_and_export.py <output_name>

Examples:
  python pipeline_and_export.py 19thmay
    → Runs full pipeline, exports: 19thmay.xlsx
  
  python pipeline_and_export.py may20_morning
    → Runs full pipeline, exports: may20_morning.xlsx

This reads paths.json from the current directory, runs the full pipeline,
and exports results to Excel with your chosen name.
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

# Import pipeline modules
THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR))
sys.path.insert(0, str(THIS_DIR / "parsers"))

import run_pipeline
import export_results


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: pipeline_and_export.py <output_name>")
        print()
        print("Examples:")
        print("  python pipeline_and_export.py 19thmay")
        print("    → Generates: 19thmay.xlsx")
        print()
        print("  python pipeline_and_export.py may20_morning")
        print("    → Generates: may20_morning.xlsx")
        return 2
    
    output_name = sys.argv[1]
    config_path = Path("paths.json").resolve()
    
    if not config_path.exists():
        print(f"Error: paths.json not found in {config_path.parent}", file=sys.stderr)
        return 1
    
    project_root = config_path.parent
    config = json.loads(config_path.read_text())
    
    print()
    print("=" * 60)
    print("  DRONE PIPELINE + EXPORT")
    print("=" * 60)
    print(f"  Config: {config_path}")
    print(f"  Project: {config.get('survey_id')}")
    print(f"  Output name: {output_name}")
    print()
    
    # Run the full pipeline
    try:
        print("► Running full pipeline...")
        print("-" * 60)
        run_pipeline.main()
        print("-" * 60)
        print("✓ Pipeline completed successfully")
        print()
    except Exception as e:
        print(f"✗ Pipeline failed: {e}", file=sys.stderr)
        return 1
    
    # Export results
    try:
        print("► Exporting results...")
        
        # Determine output format
        if output_name.endswith('.csv'):
            output_file = Path(output_name)
        elif output_name.endswith('.xlsx'):
            output_file = Path(output_name)
        else:
            output_file = Path(f"{output_name}.xlsx")
        
        # Load drone score
        drone_score_path = project_root / config["outputs"]["stage3_drone_score"]
        drone_score_envelope = json.loads(drone_score_path.read_text())
        
        # Export in requested format
        if output_file.suffix.lower() == '.csv':
            export_results.export_to_csv(drone_score_envelope, output_file)
        else:
            export_results.export_to_excel(drone_score_envelope, output_file)
        
        # Print summary
        data = drone_score_envelope.get("data", {})
        print()
        print("=" * 60)
        print(f"  ✓ EXPORT COMPLETE")
        print("=" * 60)
        print(f"  Output file: {output_file.resolve()}")
        print(f"  Drone score: {data.get('drone_score')}")
        print(f"  Generated: {drone_score_envelope.get('generated_at')}")
        print(f"  Flags: {data.get('all_flags_count')}")
        print("=" * 60)
        print()
        
        return 0
        
    except Exception as e:
        print(f"✗ Export failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
