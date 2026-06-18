# Pre-Processing Confidence Score

The **fifth** subsystem in the CBMI Capture quality-scoring family — the
**survey-level provenance** layer that sits above the four per-instance capture
scores (drone / base / GCP / check-point). It evaluates survey-design adequacy
and cross-document reference-frame consistency from pre-processing **artifacts**
(geotagged images, GCP/CP coordinate files), an operator **manifest**, and an
optional **processing report**.

- **Spec:** `pre_processing_confidence_score/pre_processing_confidence_score.json` (v1.1.0, frozen) — the single source of truth.
- **Apex:** `pre_processing_score = 0.35·reference_frame + 0.30·geotag_integrity + 0.25·gcp_coord_trust + 0.10·survey_design`
- **Runtime-independent:** does **not** read drone/base/gcp/check_point score outputs — only pre-processing artifacts + manifest + optional report.

## Quick start

```bash
pip install -r requirements.txt        # piexif (+ Pillow, numpy from your env)
python3 scripts/run_pipeline.py paths.json     # full pipeline -> outputs/
python3 scripts/test_scenarios.py paths.json   # 63 self-validating scenarios (exit!=0 on drift)
python3 scripts/make_sample_data.py            # regenerate the gold-standard PLACEHOLDER set
```

SQLite helper commands:

```bash
python3 scripts/dbx.py run paths.json pre_processing.db
python3 scripts/dbx.py list-tables pre_processing.db
python3 scripts/dbx.py extract pre_processing.db stage3_pre_processing_score
```

Baseline (gold-standard placeholder): `pre_processing_score = 100.0`,
`verification_status = VERIFIED`.

## Pipeline

| Stage | Module | Output |
|---|---|---|
| 1 Inventory | `stage1_inventory.py` | `outputs/01_inventory.json` |
| 2 Merge (survey-level) | `stage2_merge.py` + `parsers/` | `outputs/02_source_fields.json` (62 fields) |
| 3a Derived | `stage3a_derived.py` + `parsers/geometry.py` | `outputs/03_derived_fields.json` (37 L2D) |
| 3b Indicators | `stage3b_indicators.py` | `outputs/04_indicators.json` (38 L3I, Option B) |
| 3c Blocks + views | `stage3c_blocks.py` | `outputs/05_building_blocks.json` + `05b_per_artifact_views.json` |
| 3d Apex + verification | `stage3d_score.py` | `outputs/06_pre_processing_score.json` |

Every artifact is an envelope `{spec_version, config_used, generated_at, stage, data}`
— deterministic, no timestamps inside `data`, scores to 1 decimal.

## What makes this subsystem different

- **Survey-level**, not per-occupation: the 5 parsers run **once**. The
  `mean − 0.25·(100−min)` aggregation lives **inside** the `gcp_sigma` / `cp_sigma`
  indicators (not a Stage-3c cross-point loop).
- **`verification_status`** (VERIFIED / UNVERIFIED_NO_CPS / …): a parallel
  categorical field for check-point quality that **never gates the score** —
  surveys can be high-quality without check points.
- **Per-artifact views** (`05b`): three "artifact fitness" scores (geotag / GCP /
  CP) that re-weight the same indicator scores; a **parallel deliverable** that
  does not feed the apex.
- **No null state**: the apex always computes. The **global gate**
  (`PP_WRONG_CRS_DATUM` / `PP_WRONG_PROJECTION` / `PP_GCP_AUTONOMOUS_PATH`) forces
  `pre_processing_score = 0`.
- **Two-reason N/A redistribution**: indicators redistribute their block weight
  when path-N/A (`applies_to_paths`) or evidence-N/A (report absent).

## Testing

`scripts/test_scenarios.py` — **self-validating** (per-scenario EXPECT of score
± tol + exact flag set + verification_status; exits non-zero on drift):
- **63 scenarios** (34 Pass-1 spec-internal + 29 Pass-2 from the CBMI Problems sheet v1.0)
- **63 / 67 flags** exercised (4 unreachable by design — see `OPEN_ITEMS.md`)
- **42-problem coverage map** → `tests/scenarios/_pass2_problem_coverage.{csv,json}`
- each `tests/scenarios/<name>/` is self-contained (`02`→`06` + `05b`)

## Layout

```
pre_processing_confidence_score/   frozen spec bundle (JSON master + CSV sheets + Problems v1.0)
sample_data/                       gold-standard PLACEHOLDER survey (operator replaces)
scripts/  (+ parsers/)             pipeline + harness + sample-data generator
outputs/                           production envelopes (01–06 + 05b)
tests/scenarios/                   63 self-contained scenario dirs + summary + coverage map
paths.json                         config (inputs / outputs / options)
OPEN_ITEMS.md                      deferrals, spec-amendment candidates, placeholder files
```

See `BUILD_PROMPT_TEMPLATE_v2.md` for the disciplined 12-step build process this
subsystem was built with.
