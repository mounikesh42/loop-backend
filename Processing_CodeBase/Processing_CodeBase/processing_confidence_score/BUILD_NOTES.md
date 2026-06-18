# Processing Confidence Score — v1.1 FINAL Bundle

Generated: 2026-06-02 08:05 UTC
Version: 1.1.0 (CLOSED — single bundle)

## What this is

Closed bundle for processing_score v1.1 — the CBMI chain that evaluates Agisoft Metashape
processing report + deliverable files + processing manifest. Owns check-point residual
analysis as the moment-of-truth survey accuracy measurement.

v1.1 = v1.0 indicator layer + 5 per-deliverable views (additive extension).

## Architecture

- **Single scalar processing_score** per survey (v1.0 layer)
- **Four blocks**: BA Quality 0.30 / Image Matching 0.30 / Control & Verification 0.25 / Deliverable Output 0.15
- **Five per-deliverable views** (v1.1 additions): ortho, dsm, dtm, point_cloud, mesh_3d
- **Evidence model**: Option A — processing report REQUIRED; processing_score = null when absent
- **Engine support v1**: Agisoft Metashape v1.7.x
- **Parser**: keys off section headers (not page numbers) for Agisoft version resilience
- **Runtime independence**: chain-level. Per-deliverable views read same v1.0 indicators.

## Counts

- 4 source files
- 90 source fields
- 37 derived fields
- 38 indicators across 4 blocks
- 64 flags (1 global gate, 2 flag-only catastrophic/critical, scoring + advisory flags)
- 39 catalogued problems (7 inherited + 32 native)
- **5 per-deliverable views** (v1.1)

All weights sum to 1.0 ✓ (block weights, indicator weights within blocks, view weights within views)

## Per-Deliverable Views (v1.1)

Each view is a diagnostic READOUT, not a separate score. Same v1.0 indicators, recombined with
view-specific weights to answer: "What is the quality story for THIS specific deliverable?"

| View | Top emphasis | Top 3 indicators (by weight) |
|------|--------------|------------------------------|
| ortho_score | Horizontal accuracy + frame correctness | CV1(0.15), BA1+BA2(0.10 each), DO1(0.10) |
| dsm_score | Depth quality + vertical accuracy | IM2(0.15), CV1(0.12), IM5(0.10) |
| dtm_score | Ground classification + filtering + vertical | DO4(0.20), IM5(0.12), IM2(0.10) |
| point_cloud_score | Depth-driven density + 3D accuracy | IM2(0.18), CV1(0.12), IM5(0.08) |
| mesh_3d_score | Depth + alignment continuity + 3D accuracy | IM2(0.15), BA2(0.10), CV1(0.10) |

**File missing handling:** When a deliverable file is absent (e.g., user uploaded report + ortho
+ DSM but not DTM), the corresponding view returns null with reason=<type>_file_not_uploaded.
processing_score itself remains unaffected.

## Hard Gate (zeroing processing_score)

- PROC_OUTPUT_CRS_MISMATCH — output CRS contradicts project requirement (CATASTROPHIC)

## Flag-only signals (CATASTROPHIC/CRITICAL but score still computes)

- PROC_NO_MARKERS_AT_ALL — zero markers (CATASTROPHIC; bundle PPK-anchored only)
- PROC_NO_GCPS_USED — markers exist but all as CPs (CRITICAL; PPK-only)

## Verification Status Field

Separate categorical field, not score-gating:
- VERIFIED_RESIDUALS_PASS / MARGINAL / FAIL — CPs ≥5 with CP_RMSE thresholds
- UNVERIFIED_INSUFFICIENT_CPS — 1-4 CPs
- UNVERIFIED_NO_CPS — zero CPs

## Pressure-Test Results

### v1.0 chain-level (3 reference reports, accuracy_target=5 cm):

| Report | Configuration | processing_score | Rank |
|--------|--------------|------------------|------|
| A | 3 CPs, 0 GCPs (PPK only) | 84.3 | ✓ worst |
| B | 8 CPs, 0 GCPs (more verification) | 84.6 | ✓ middle |
| C | 3 GCPs + 5 CPs (proper control) | 87.1 | ✓ best |

Rank ordering A < B < C confirms block weights at 0.30/0.30/0.25/0.15.

### v1.1 per-deliverable views (15 score computations):

| View | Report A | Report B | Report C | Rank A ≤ B ≤ C |
|------|----------|----------|----------|----------------|
| ortho_score | 85.9 | 85.9 | 86.9 | ✓ |
| dsm_score | 82.7 | 82.7 | 83.5 | ✓ |
| dtm_score | 87.6 | 87.6 | 87.9 | ✓ |
| point_cloud_score | 81.6 | 81.6 | 82.5 | ✓ |
| mesh_3d_score | 82.4 | 82.4 | 83.3 | ✓ |

All 5 views show correct rank ordering. A=B on 4 views is correct behavior — they produce
nearly identical deliverable quality at per-artifact level; chain-level differentiation comes
from statistical sufficiency (CV6) which views deliberately don't emphasize.

## Build History

- **v0.1 DRAFT**: 35 catalog rows (7 inherited + 27 native + 1 borderline-included)
- **v0.2 DRAFT**: User operational review pass — 8 OPERATIONAL tags, 2 severity downgrades
  (#3 self-cal HIGH→MEDIUM compound, #7 max reproj HIGH→MEDIUM advisory), 4 new rows
  (#36 internal transform, #37 localized reconstruction collapse, #38 DEM voids, #39 DSM-as-DTM),
  3 chain-level concerns flagged
- **v1.0**: 
  - Concern 1 (NULL when no report): Option A locked — different from pre-processing's Option B
  - Concern 2 (block weights): 0.30/0.30/0.25/0.15 locked, pressure-test validated
  - Concern 3 (parser fragility): section-header-keyed parsing locked
  - CV1 cp_rmse_score at 0.35 (moment-of-truth)
  - IM7 localized_reconstruction at 0.10 with v1-partial/v2-full documented
  - **Pressure-test caught BA4 issue**: missing b1/b2 penalty too steep (30 → 10 pts);
    softened to 90 (was 70). Problem #6 + flag downgraded to LOW.
- **v1.1 (this bundle)**: 5 per-deliverable views added as configurations over v1.0 indicator layer.
  Strictly additive (v1.0 layer unchanged). All views pressure-tested for rank ordering.

## Known Limitations (stated, not hidden)

- **Processing report REQUIRED** — third-party-processed surveys without report addressed
  by future delivery layer
- **Localized reconstruction collapse (#37)** v1 PARTIAL detection via global proxies;
  full spatial detection of collapse zones v2 (density-map visual analysis)
- **Per-camera position outliers (#8)** v1 ENSEMBLE only; per-camera detail v2
- **Software version known-buggy list (#33)** v2 deliverable
- **CP-GCP statistical independence (GCP#2)** deferred v2
- **High altitude variance (Drone FLG_011)** deferred v2
- **BA4 threshold softened** per v1.0 pressure-test: missing b1/b2 → 90 (was 70)
- **Per-deliverable views are READOUTS not scores** — they don't replace processing_score,
  they diagnose specific output quality

## Next Steps in CBMI

1. **analytics_score chain** — owns volumes/measurements/derived outputs evaluation
2. **capture_score integration layer** — cross-subsystem coupling between drone/base/gcp/cp/pp/processing/analytics
3. **Future delivery layer** — per-customer-use-case deliverable fitness, addresses
   third-party-processed surveys without Agisoft report (the population where processing_score = null)

## Files in this Bundle

- `processing_confidence_score_v1.1.json` — Master JSON — v1.0 indicator layer + v1.1 per-deliverable views
- `processing_confidence_score_v1.1.xlsx` — Excel workbook with 17 sheets (README + v1.0 levels + v1.1 views)
- `processing_provenance_v1.1.html` — Full provenance documentation including 5 per-deliverable views
- `01_source_files.csv` — Level 6 — source files
- `02_source_fields.csv` — Level 5 — source fields
- `03_derived_fields.csv` — Level 4 — derived fields
- `04_indicators.csv` — Level 3 — indicators with weights, thresholds, evidence requirements
- `05_building_blocks.csv` — Level 1 — 4 blocks with weights
- `06_processing_score.csv` — Level 0 — processing_score block composition
- `06b_processing_score_meta.csv` — Level 0 — processing_score metadata
- `07_flags.csv` — All 64 flags with severity, conditions, coverage
- `08_problem_coverage_map.csv` — All 39 problems mapped to indicators/flags
- `09_per_deliverable_views.csv` — Per-deliverable views summary
- `10_view_weights_all.csv` — All view weight maps combined
- `10_view_weights_ortho_score.csv` — Ortho view weight map
- `10_view_weights_dsm_score.csv` — DSM view weight map
- `10_view_weights_dtm_score.csv` — DTM view weight map
- `10_view_weights_point_cloud_score.csv` — Point cloud view weight map
- `10_view_weights_mesh_3d_score.csv` — Mesh 3D view weight map
