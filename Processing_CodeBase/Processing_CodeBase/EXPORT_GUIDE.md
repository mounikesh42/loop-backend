# CSV & Excel Export Guide

## Overview

The Processing pipeline now supports exporting results as **CSV** and **Excel** files with custom naming. You can run the pipeline with different input files and export each run with a distinct filename (e.g., `19thmay.xlsx`, `20thmay.xlsx`).

## Features

- **CSV Export**: Single-sheet CSV with main processing score summary
- **Excel Export**: Multi-sheet workbook with:
  - **Summary**: Main score, verification status, flags overview
  - **Score_Contributions**: Block scores and weighted contributions  
  - **Per_Deliverable_Views**: Individual deliverable scores (DSM, DTM, Ortho, etc.)
  - **Flags**: Detailed flag list with severity color-coding
  - **Apex_Formula**: Formula and weights used for score calculation

## Usage

### Basic CSV Export

```bash
python scripts/run_pipeline.py paths.json --export-csv "19thmay.csv"
```

**Output file**: `19thmay.csv` (in project root)

### Basic Excel Export

```bash
python scripts/run_pipeline.py paths.json --export-xlsx "19thmay.xlsx"
```

**Output file**: `19thmay.xlsx` (in project root) with 5 sheets

### Both CSV and Excel Simultaneously

```bash
python scripts/run_pipeline.py paths.json --export-csv "19thmay.csv" --export-xlsx "19thmay.xlsx"
```

## Example Workflow

### Run 1: May 19th data
```bash
python scripts/run_pipeline.py paths.json --export-xlsx "19thmay.xlsx"
# Generates: 19thmay.xlsx
```

### Run 2: May 20th data (after changing input files in paths.json)
```bash
python scripts/run_pipeline.py paths.json --export-xlsx "20thmay.xlsx"
# Generates: 20thmay.xlsx
```

### Run 3: May 21st with both formats
```bash
python scripts/run_pipeline.py paths.json --export-csv "21stmay.csv" --export-xlsx "21stmay.xlsx"
# Generates: 21stmay.csv and 21stmay.xlsx
```

## SQLite Pipeline Export

The SQLite command also supports exports:

```bash
python scripts/sqlite_pipeline.py run paths.json dbsqlite3 --export-xlsx "19thmay.xlsx"
```

This runs the full pipeline, stores results in SQLite, AND exports to Excel.

## File Locations

Export files are created in your project root directory (same location as `paths.json`):

```
Processing_CodeBase/
├── paths.json
├── 19thmay.xlsx          ← exported here
├── 20thmay.xlsx          ← exported here
├── scripts/
│   ├── run_pipeline.py
│   ├── csv_export.py
│   └── ...
└── ...
```

## CSV Output Format

Single header row + one data row with these fields:

| Field | Value | Example |
|-------|-------|---------|
| survey_id | From paths.json | "sample_data" |
| subsystem | From paths.json | "processing" |
| spec_version | From spec file | "1.1.1" |
| generated_at | ISO timestamp | "2026-06-05T06:31:16Z" |
| processing_score | Final apex score | 84.7 |
| verification_status | CP verification state | "UNVERIFIED_INSUFFICIENT_CPS" |
| is_null | Whether score is null | False |
| global_gate_triggered | If global gate fired | False |
| total_flags | Count of all flags | 10 |
| unique_flags | Count of unique flags | 9 |
| critical_flags | Count by severity | 5 |
| high_flags | Count by severity | 2 |
| medium_flags | Count by severity | 2 |
| informational_flags | Count by severity | 1 |

## Excel Sheet Descriptions

### Summary Sheet
Key metadata and scores:
- Survey ID, Subsystem, Spec Version, Timestamp
- Processing Score (main apex)
- Verification Status (CP count/RMSE based)
- Null/gating status
- Flag counts by severity

### Score_Contributions Sheet
How each block contributed to the final score:
- Block ID (e.g., "BB_PROC_BA")
- Block Name (e.g., "ba_quality_score")
- Block Score (out of 100)
- Contribution (weighted score)
- Weight in apex formula

### Per_Deliverable_Views Sheet
Individual deliverable fitness scores:
- Deliverable type (ORTHO, DSM, DTM, MESH_3D, POINT_CLOUD)
- Score for that deliverable

### Flags Sheet
All raised flags with context:
- Flag ID (e.g., "FLG_PROC_010")
- Flag Name (e.g., "PROC_CAMERA_POS_ELEVATED")
- Severity (CRITICAL = red, HIGH = orange, MEDIUM = yellow, INFORMATIONAL = green)
- Origin Stage (which pipeline stage raised it)
- Indicator ID (which indicator raised it)

Color coding in severity column helps quickly identify problematic results.

### Apex_Formula Sheet
Reference documentation:
- Formula used (0.30*ba_quality + 0.30*image_matching + ...)
- Weights for each block
- Easily verify weights match the config

## Tips

1. **Naming Convention**: Use meaningful names like `YYYY-MM-DD.xlsx` or `client_survey_date.xlsx`
2. **No Separate Files**: Both CSV and Excel contain the final score; no need to export both unless you have different consumers
3. **Custom Paths**: Filenames are relative to the project root (same directory as `paths.json`)
4. **Re-export**: You can safely re-export with the same filename—it will overwrite
5. **Automation**: Use the CLI options in scripts or batch jobs to generate exports for multiple runs

## Troubleshooting

### `ModuleNotFoundError: No module named 'openpyxl'`
Fix: `pip install openpyxl` (required for Excel export; CSV export does not require it)

### Export file not created
Check:
1. Output path has write permissions
2. Filename is valid (no illegal characters)
3. Run pipeline completed successfully (check exit code is 0)

### Extra JSON files still generated
The `--export-csv` / `--export-xlsx` flags **do not** disable normal JSON output. Both are created. To use only CSV/Excel, you can delete the JSON files after export or use the `outputs/` directory for JSON and export CSV/Excel elsewhere.

## Next Steps

- Edit `paths.json` to point to new input files
- Re-run pipeline with different export filename
- Compare exported files across multiple runs
- Use Excel file for reporting; CSV for data analysis/scripting
