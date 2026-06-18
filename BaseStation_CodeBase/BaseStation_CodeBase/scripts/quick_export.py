#!/usr/bin/env python3
"""Simplified wrapper for export_to_excel.py with interactive prompts."""
import sys
from pathlib import Path
import subprocess


def main():
    project_root = Path(__file__).parent.parent
    scripts_dir = project_root / "scripts"
    
    print("\n" + "="*60)
    print("  BaseStation Results → Excel Exporter")
    print("="*60)
    print("\nThis tool exports all pipeline results to an Excel file")
    print("with multiple sheets, one per data stage.\n")
    
    # Get custom name
    output_name = input("Enter export name (e.g., 19thmay, results_final): ").strip()
    
    if not output_name:
        print("ERROR: Output name is required!")
        return 1
    
    # Validate name
    if any(c in output_name for c in '<>:"/\\|?*'):
        print("ERROR: Invalid characters in filename!")
        return 1
    
    print(f"\n→ Exporting to: {output_name}.xlsx")
    print("  Loading all stages...\n")
    
    # Run export
    result = subprocess.run(
        [
            sys.executable,
            str(scripts_dir / "export_to_excel.py"),
            str(project_root / "paths.json"),
            output_name,
        ],
        cwd=str(project_root),
    )
    
    if result.returncode == 0:
        output_file = project_root / "outputs" / f"{output_name}.xlsx"
        print(f"\n✓ Success! File created: outputs/{output_name}.xlsx")
        print(f"  Location: {output_file}")
        print(f"  Size: {output_file.stat().st_size / 1024:.1f} KB")
    else:
        print(f"\n✗ Export failed with code {result.returncode}")
        return 1
    
    print("\n" + "="*60 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
