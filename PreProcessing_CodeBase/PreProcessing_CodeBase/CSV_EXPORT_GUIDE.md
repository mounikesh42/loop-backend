# CSV Export Feature

The pre-processing pipeline now supports exporting results to CSV format. This allows you to:

- Export pipeline results as CSV files for each run
- Use meaningful names/dates for output files
- Run multiple times with different input files and get uniquely named outputs
- All results stored in a dedicated `csv_outputs/` directory

## Usage

### Basic CSV Export

To run the pipeline and export results as CSV:

```bash
python scripts/run_pipeline.py paths.json --date "19thmay"
```

This will create:
- All standard JSON outputs in `outputs/` directory (as before)
- A CSV file: `csv_outputs/19thmay_pre_processing_score.csv`

### Running Multiple Times

Each time you run with a different date/prefix, you get a new CSV file:

```bash
# First run
python scripts/run_pipeline.py paths.json --date "19thmay"
# Creates: csv_outputs/19thmay_pre_processing_score.csv

# Second run  
python scripts/run_pipeline.py paths.json --date "20thjune"
# Creates: csv_outputs/20thjune_pre_processing_score.csv

# Third run (change input files first, then run)
python scripts/run_pipeline.py paths.json --date "run_v2"
# Creates: csv_outputs/run_v2_pre_processing_score.csv
```

### Alternative: Using `--output-prefix`

The `--output-prefix` option is an alias for `--date`:

```bash
python scripts/run_pipeline.py paths.json --output-prefix "experiment_1"
# Creates: csv_outputs/experiment_1_pre_processing_score.csv
```

## CSV Output Format

The CSV file contains a single row with all key metrics:

| Column | Description |
|--------|-------------|
| **pre_processing_score** | Main apex score (0-100) |
| **verification_status** | One of: VERIFIED, UNVERIFIED_NO_CPS, UNVERIFIED_INSUFFICIENT_CPS, etc. |
| **reference_frame_score_score** | Building block score (Reference Frame) |
| **geotag_integrity_score_score** | Building block score (Geotag Integrity) |
| **gcp_coord_trust_score_score** | Building block score (GCP Coordinate Trust) |
| **survey_design_score_score** | Building block score (Survey Design) |
| **cp_count** | Number of check points |
| **cp_distribution_coverage** | CP distribution coverage (0-1) |
| **cp_independence_m** | Minimum CP-GCP distance in meters |
| **cp_sigma_score** | CP sigma score |
| **VIEW_PP_CP_COORD** | CP coordinate artifact score |
| **VIEW_PP_GCP_COORD** | GCP coordinate artifact score |
| **VIEW_PP_GEOTAG** | Geotag artifact score |
| **total_flags** | Number of flags raised |
| **global_gate_triggered** | Whether global gate was triggered |
| **generated_at** | ISO-8601 timestamp |
| **spec_version** | Spec version used |
| **survey_id** | Survey identifier |

## Example Workflow

1. **Prepare input files** for your survey in `sample_data/`:
   - `gcp_coords.csv`
   - `cp_coords.csv`
   - `geotags/` directory with geotagged images
   - `pp_manifest.json`
   - (optional) `processing_report.json`

2. **Run the pipeline** with a meaningful date/name:
   ```bash
   python scripts/run_pipeline.py paths.json --date "survey_2026_05_19"
   ```

3. **Check the results**:
   - JSON details: `outputs/06_pre_processing_score.json`
   - CSV summary: `csv_outputs/survey_2026_05_19_pre_processing_score.csv`

4. **Repeat** with different input files:
   - Update `sample_data/` files
   - Run with a different date prefix
   - Each run generates its own CSV file for easy comparison

## No CSV Export

If you run the pipeline **without** the `--date` or `--output-prefix` option, only JSON outputs are generated (backward compatible):

```bash
python scripts/run_pipeline.py paths.json
# Only JSON outputs, no CSV export
```

## File Organization

```
PreProcessing_CodeBase/
├── outputs/                          # JSON results (standard)
│   ├── 01_inventory.json
│   ├── 02_source_fields.json
│   ├── 03_derived_fields.json
│   ├── 04_indicators.json
│   ├── 05_building_blocks.json
│   ├── 05b_per_artifact_views.json
│   └── 06_pre_processing_score.json
│
├── csv_outputs/                      # CSV results (new)
│   ├── 19thmay_pre_processing_score.csv
│   ├── 20thjune_pre_processing_score.csv
│   └── run_v2_pre_processing_score.csv
│
└── scripts/
    └── run_pipeline.py
```

## Notes

- CSV export is optional - use `--date` when you want it
- Each CSV contains a **single row** with all key metrics from one pipeline run
- Multiple CSV files can be easily combined/compared in spreadsheet applications
- All JSON outputs remain unchanged and are always generated
- The CSV provides a quick summary while JSON contains full details
