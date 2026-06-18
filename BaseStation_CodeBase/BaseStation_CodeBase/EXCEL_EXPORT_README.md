# Excel Export Guide

This guide explains how to export your BaseStation pipeline results to Excel files with multiple sheets.

## Overview

After running the full pipeline (`run_pipeline.py`), you can export all results to a single Excel file (`.xlsx`) with:
- **Multiple sheets** — one for each data structure (inventory, source fields, derived fields, indicators, building blocks, base score)
- **Custom naming** — name each export by date or run ID
- **Multiple exports** — generate separate Excel files for different runs

## Quick Start

### Option 1: Batch File (Windows) — Easiest

```bash
export.bat 19thmay
```

This creates: `outputs/19thmay.xlsx`

Run it again with a different name to create another file:

```bash
export.bat 25thjune
```

This creates: `outputs/25thjune.xlsx`

### Option 2: Python Direct

```bash
python scripts/export_to_excel.py paths.json 19thmay
```

Output: `outputs/19thmay.xlsx`

### Option 3: Interactive Python

```bash
python scripts/quick_export.py
```

Prompts you for the export name interactively.

## Examples

```bash
# Export with date
export.bat 19thmay
→ outputs/19thmay.xlsx

# Export with run number
export.bat run_001
→ outputs/run_001.xlsx

# Export with descriptive name
export.bat results_final_v2
→ outputs/results_final_v2.xlsx
```

## What's Included

Each Excel file contains multiple sheets organized by stage:

| Sheet Category | Description |
|---|---|
| **Stage1 Inventory** | Counts, warnings, hard failures from input files |
| **Stage2 Source Fields** | Parsed and extracted fields from all sources |
| **Stage3 Derived** | Computed derived fields and intermediate calculations |
| **Stage3 Indicators** | Calculated performance indicators |
| **Stage3 Building Blocks** | Aggregated building block scores |
| **Stage3 Base Score** | Final base station confidence scores, flags, and metadata |

Total: **~33 sheets** with all comprehensive results

## Workflow Example

### Run 1: May 19th Data
```bash
# Run pipeline
python scripts/run_pipeline.py paths.json

# Export results
export.bat 19thmay
```

Output: `outputs/19thmay.xlsx`

### Run 2: June 25th Data  
```bash
# Edit paths.json to point to new input data
# (change inputs to new RINEX files, operator logs, etc.)

# Run pipeline
python scripts/run_pipeline.py paths.json

# Export results with new name
export.bat 25thjune
```

Output: `outputs/25thjune.xlsx`

### Compare Results
Open both Excel files side-by-side in Excel to compare scores, flags, and metrics across dates.

## Installation

Required package (already installed):
- `openpyxl==3.1.5` — Excel file writer

If you need to reinstall:
```bash
pip install openpyxl==3.1.5
```

## Troubleshooting

### File already exists
Re-running with the same name will **overwrite** the existing file. Use a different name to preserve previous exports.

### Permission denied
- Close the Excel file if it's open in Excel
- Try again

### Module not found errors
Ensure the virtual environment is activated:
```bash
.venv\Scripts\activate
```

## Notes

- Sheet names are limited to 31 characters (Excel limitation)
- Sheets are organized by pipeline stage for easy navigation
- Metadata (timestamps, config used, spec version) is included in each stage
- All numerical data is preserved; complex nested structures are flattened for readability

## See Also

- [Main README](../README.md) — Project overview
- [paths.json](../paths.json) — Configuration file
