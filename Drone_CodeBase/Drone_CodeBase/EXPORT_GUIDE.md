# Export Results Guide

## Quick Start

### Option 1: Export existing results
If you've already run the pipeline and want to export the results:

```bash
python scripts/export_results.py paths.json 19thmay
# Generates: 19thmay.xlsx
```

### Option 2: Run pipeline + export in one command
To run the full pipeline and export results at the same time:

```bash
cd scripts
python pipeline_and_export.py 19thmay
# Runs pipeline → Generates: 19thmay.xlsx
```

## Usage Examples

### Export as Excel (default)
```bash
python scripts/export_results.py paths.json 19thmay
# Creates: 19thmay.xlsx
```

### Export as CSV
```bash
python scripts/export_results.py paths.json 19thmay.csv
# Creates: 19thmay.csv
```

### Different run dates
```bash
# First run
python scripts/pipeline_and_export.py may19th

# Change input files (e.g., sample_data_19), then run again
python scripts/pipeline_and_export.py may20th_morning

# Or if using different paths.json
python scripts/export_results.py paths_alternate.json may20th_evening
```

## Output Format

Each export creates a single-sheet file with these columns:

| Column | Description |
|--------|-------------|
| generated_at | Timestamp of generation |
| survey_id | Project ID from config |
| spec_version | Schema version |
| **drone_score** | **Final score (0-100)** |
| raw_weighted_sum | Unrounded weighted sum |
| global_gate_triggered | Boolean - if TRUE, drone_score forced to 0 |
| img_capture_score | Block score for image quality |
| rover_gnss_score | Block score for GNSS quality |
| mission_exec_score | Block score for mission execution |
| cal_conf_score | Calibration confidence (parallel track) |
| total_flags | Total issues detected |
| flags_critical | Count of CRITICAL severity flags |
| flags_high | Count of HIGH severity flags |
| flags_medium | Count of MEDIUM severity flags |
| flags_low | Count of LOW severity flags |
| flags_list | Semicolon-separated flag names |

## Requirements

For Excel export, install openpyxl (if not already installed):
```bash
pip install openpyxl
```

CSV export works without additional dependencies.

## Workflow

1. **First run (May 19th data)**
   ```bash
   # Data: sample_data/
   python scripts/pipeline_and_export.py 19thmay
   ```

2. **Update input files**
   ```bash
   # Change contents of:
   # - sample_data/images/
   # - sample_data/telemetry/
   # - sample_data/user_input/form.json
   # - sample_data/user_input/hardware.json
   ```

3. **Second run (May 20th data)**
   ```bash
   python scripts/pipeline_and_export.py 20thmay
   ```

4. **Compare results**
   - Open `19thmay.xlsx` and `20thmay.xlsx` side-by-side
   - Compare drone_score, block scores, and flags

## Troubleshooting

**"Drone score file not found"**
- Make sure you've run the pipeline first (all stages must complete)
- Check that paths.json exists in current directory

**"pandas is required"**
- Run: `pip install pandas openpyxl`

**Permission denied on export**
- Ensure the script directory is writable
- Check that no other program has the xlsx file open
