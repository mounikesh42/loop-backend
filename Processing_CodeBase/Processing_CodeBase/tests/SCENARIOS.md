# Processing pipeline — smoke-test scenarios (Step 12)

`scripts/test_scenarios.py` is a **self-validating** harness: each scenario
deep-copies the baseline Stage-2 `source_fields`, applies a mutator, re-runs
Stages 3a→3d into `tests/scenarios/<name>/`, and asserts the result against a
pinned `EXPECT` entry — **`processing_score` ±0.05 AND the exact deduped flag-id
set AND `verification_status`**. Any drift flips the exit code to non-zero.

- **59 scenarios** (Pass 1: 47 spec-internal band walk · Pass 2: 12 from the CBMI
  Processing Problems v0.2 sheet).
- **0 validation failures**, **55/64 flags exercised**, **all 39 problems mapped**.
- Each scenario directory is **self-contained** (mutated `02_source_fields.json`
  + `03`–`06` envelopes) and **byte-deterministic** (`generated_at` omitted).

> Validated against spec **v1.1.1**. See the v1.1.1 clarifications below.

## Baseline — the real Report-A-like survey

A genuine Agisoft Metashape v1.7.6 report: **PPK-anchored, 3 check points, 0 GCPs,
EPSG:4326 (geographic) deliverables, reprojection 1.45 px**. It scores
**`processing_score = 84.7`**, `verification_status = UNVERIFIED_INSUFFICIENT_CPS`,
with **9 standing flags**:

| flag | why |
|---|---|
| `PROC_NO_GCPS_USED` (CRITICAL) | 0 control points (PPK only) |
| `PROC_OUTPUT_CRS_GEOGRAPHIC` (CRITICAL) | EPSG:4326 is geographic, not projected |
| `PROC_CP_RMSE_MARGINAL` | CP RMSE 5.9 cm = 1.18× the 5 cm target |
| `PROC_CP_COUNT_INSUFFICIENT` · `PROC_GCP_COUNT_INSUFFICIENT` | 3 CPs / 0 GCPs |
| `PROC_CAMERA_POS_ELEVATED` · `PROC_DEPTH_QUALITY_MEDIUM` · `PROC_DEM_RES_COARSE` | quality bands |
| `PROC_PER_DELIVERABLE_FITNESS` (INFORMATIONAL) | always-fire delivery-layer handoff |

The baseline is a realistic survey (not a synthetic 100), so each scenario's
`EXPECT` flag set is the **full deduped set** (baseline ± the change).

## Renorm cross-check

Each pinned score was cross-checked against the block-renorm identity for one
indicator *i* in block *B* moving band `s_old → s_new`:

```
apex' = apex + W_B · (w_i / A_B) · (s_new − s_old)
```

with apex=84.7 and active weights **A: BA 1.0, IM 1.0, CV 0.71 (4 N/A in the
no-GCP baseline), DO 0.92 (DEM-void 035 N/A on v1.7)**. Worked example —
`ba_convergence_fail` (reproj 4.0): BA1 100→0 (w 0.20) plus the coupled BA7 100→30
(w 0.10) ⇒ ΔBA = −27 ⇒ apex' = 84.7 + 0.30·(−27) = **76.6** ✓.

## Pass 1 — spec-internal band walk (47)

Walks every block's failure bands + the gate + the flag-only signals +
verification transitions:

- **BA**: convergence-fail / reproj-high / reproj-elevated, camera align
  partial→poor→severe, camera-pos severe, precalib-not-loaded, camera-model
  mismatch, self-calib ill-conditioned, reproj-outliers (mild) + reproj-severe-outliers,
  optimization-incomplete.
- **IM**: alignment-accuracy low→critical, depth-quality low, tiepoints
  sparse→very-sparse, multiplicity low→very-low, filtering over/insufficient,
  marker weak→insufficient, atmospheric composite.
- **CV**: cp_rmse_fail, cp-outlier-severe, z/xy high→severe, marker-pix-severe,
  role-mismatch, **gcp-typo** (#26, report XYZ vs pp coord file), **no-markers
  (CATASTROPHIC flag-only)**, gcp-rmse high→reject, gcp-count-marginal,
  reconstruction-drift composite, **verified_pass / verified_fail** (8-CP configs
  that flip `verification_status`).
- **DO**: **crs_mismatch_gate (CATASTROPHIC → apex 0, all views null)**,
  internal-transform-wrong, dsm-as-dtm, dem-res-very-coarse, deliverable-missing,
  software-drift.
- **all_flags_stress**: simultaneous non-gate flags (apex 69.0) without the gate.

## Pass 2 — CBMI Processing Problems v0.2 sheet (12)

Concrete numbers from the sheet's prose: #1 reproj 3.2 px, #2 136/142 aligned,
#12 low depth, #20 CP RMSE 11 cm (moment-of-truth), #22 one CP at 18 cm, #19 GCP
RMSE 9 cm, #25 role swap, #31 UTM-47N required vs WGS84 (CATASTROPHIC gate), #36
capture geoid/CRS misconfig, #39 DSM-as-DTM, #34 point-cloud+mesh missing, #32 DEM
10.9 cm vs 2.72 cm GSD (= 4×, the baseline value).

## Problem-coverage map (`_pass2_problem_coverage.{csv,json}`)

All **39** problems: **35 VERIFIED**, **3 DEFERRED_SPEC_GAP**, **1 DEFERRED_HANDOFF**.

- **DEFERRED_SPEC_GAP** (#27 GCPs clustered, #28 GCPs no vertical coverage, #38 DEM
  voids): need data a v1.7 Agisoft report does **not** emit — absolute GCP positions
  (#27/#28) or void statistics (#38). v2.
- **DEFERRED_HANDOFF** (#35 per-deliverable fitness): owned by the future delivery
  layer; surfaced as the always-fire `PROC_PER_DELIVERABLE_FITNESS`.
- #26 (GCP typo) is now **VERIFIED in logic** via `gcp_typo` — the within-noise
  coordinate comparison works; real-data detection still awaits a report version
  that emits absolute marker XYZ (v2).

## Flags not exercised (9 / 64 — by design)

| flag | reason |
|---|---|
| `PROC_GCPS_CLUSTERED_IN_BUNDLE` (046), `PROC_GCPS_SEVERELY_CLUSTERED` (047), `PROC_GCPS_NO_VERTICAL_COVERAGE_MARGINAL` (048), `PROC_GCPS_NO_VERTICAL_COVERAGE` (049) | need **absolute GCP positions** the v1.7 report does not emit (024/025 always N/A). v2. |
| `PROC_DEM_INTERPOLATED_VOIDS` (058) | needs a **void fraction** the v1.7 report omits; 035 is N/A on v1.7. v2. |
| `PROC_SOFTWARE_VERSION_BUGGY` (062) | v2 known-buggy list (zero scoring weight in v1). |
| `PROC_CP_OUTLIER_REJECT` (039), `PROC_TARGET_DETECTION_FAILURE` (063) | a 3-CP set cannot produce a per-CP ratio ≥3. Reachable only with larger CP sets. |
| `PROC_MARKER_PIX_HIGH` (052) | adjacent mid-band of `per_marker_pix_error` (the SEVERE band is exercised). |

## v1.1.1 clarifications (applied to spec + implementation)

1. **Global gate force-to-0** — `PROC_OUTPUT_CRS_MISMATCH` force-zeros the apex
   (not via 0.15-weight block arithmetic); spec `global_gate_action` reworded.
2. **Views null on the CRS gate** — when the gate trips, all per-deliverable views
   return null (`reason=output_crs_mismatch`), since every deliverable is in the
   wrong frame.
3. **DEM-void 035 → N/A** when the report omits a void fraction (no optimistic
   default); drops the baseline 84.9 → 84.7.
4. **GCP-coord (021) is a real within-noise comparison** (was a stub); always N/A
   on v1.7 reports (no abs marker XYZ).
5. **Precalibration (005) penalty is asymmetric** — only "expected-but-not-loaded".

## Running

```
python3 scripts/test_scenarios.py            # validate (exit non-zero on drift)
python3 scripts/test_scenarios.py --capture  # print values for re-pinning EXPECT
```
