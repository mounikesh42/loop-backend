# Processing Provenance Pipeline — Session Kickoff Prompt

> Paste the contents of this file (or the body below the `---`) as the FIRST
> message to a new Claude Code session opened in your new `Processing_CodeBase/`
> folder. Attach the companion file **`BUILD_PROMPT_TEMPLATE_v2.md`** to the same
> session — that is the comprehensive 12-step build guide Claude follows step by
> step.
>
> Copy BOTH files into the new folder before you start. From inside your new
> `Processing_CodeBase/` (a sibling of `PreProcessing_CodeBase/` etc. under
> `Loop_CodeBase/`):
> ```
> cp ../PreProcessing_CodeBase/BUILD_PROMPT_TEMPLATE_v2.md ./
> cp ../PROCESSING_KICKOFF_PROMPT.md ./
> ```

---

You are building the **Processing** provenance scoring pipeline. This is the
**sixth** subsystem in the Capture/Survey quality-scoring family; five are
already shipped end-to-end and serve as reference:

| Subsystem | Status | Location | Use as |
|---|---|---|---|
| Drone PPK | shipped | `../Drone_CodeBase/` | reference |
| Base Station PPK | shipped end-to-end | `../BaseStation_CodeBase/` | reference |
| GCP PPK | shipped end-to-end (v2.0.0) | `../GCP_CodeBase/` | reference (cross-point aggregation + problem map) |
| Check Point (RTK) | shipped end-to-end (v1.0.0) | `../CheckPoint_CodeBase/` | reference (self-validating harness origin) |
| Pre-Processing | shipped end-to-end (v1.1.0) | `../PreProcessing_CodeBase/` | **PRIMARY scaffolding lift — closest analogue (per-artifact views, evidence tiers, path-awareness, self-validating EXPECT harness)** |
| **Processing** | **building now** | `<this folder>` | — |

The disciplined build process is documented in the attached
**`BUILD_PROMPT_TEMPLATE_v2.md`**. That file is the process source of truth — read
it before doing anything else. The **spec bundle** (which I will provide) is the
data source of truth.

## ⚠️ Why this subsystem is different — READ THIS FIRST

The first four subsystems are **per-instance hardware capture-confidence scores**
(one drone flight / base occupation / GCP occupation / RTK check-point). The
fifth, **Pre-Processing**, is the **survey-level** layer that scores survey-design
adequacy and cross-document reference-frame consistency from pre-processing
*artifacts* + an operator manifest (it evaluates the inputs to photogrammetry).

**Processing is the next layer down: it scores the photogrammetric reconstruction
itself and the deliverables it produced.** Where pre-processing asked "are the
inputs trustworthy and the frame correct?", processing asks "did the
reconstruction run well, is it accurately georeferenced, and are the deliverables
fit for use?" The problems the other subsystems explicitly **deferred to
`processing_score`** live here — e.g. Pre-Processing #32 *Stage-2 Target Detection
Failure (target washed/blown away)* was handed off with: *"Stays in future
processing_score: ODM-detected missing-target flag."* Several siblings deferred
their downstream accuracy checks here too (e.g. "caught downstream at
processing-stage check-point residuals").

**The meta-lesson from the prior five builds, stated as a hard rule:** every
subsystem diverged from its sibling at the **data layer**, and the divergence was
bigger than it first looked (Check Point was RTK not PPK → no RINEX; Pre-Processing
read EXIF + CSV + a manifest, no GNSS at all). **Processing will very likely
diverge the MOST at the parser layer of any build so far** — its inputs are
**binary geospatial deliverables** (rasters, point clouds, meshes) and a rich
**processing/quality report**, not text/CSV/EXIF. Treat *every* structural
assumption below as a **hypothesis to verify against the spec at Step 0**, never
as something to bake in.

**You flagged that the processing bundle has *per-deliverable scores* (like
Pre-Processing's per-artifact views).** Confirm at Step 0 whether these are a
**parallel deliverable** (re-weightings emitted as `05b`, NOT feeding the apex —
the Pre-Processing pattern) or whether each deliverable is **scored and then
aggregated INTO the apex** (a genuinely different aggregation). This is the single
most important structural question of the build.

## What likely carries forward (anticipate — then verify)

1. **The 5-stage pipeline shape** — Stage 1 inventory → Stage 2 parse/merge →
   Stage 3a derived → 3b indicators → 3c building-block rollups (+ per-deliverable
   views) → 3d apex score.
2. **The envelope contract + determinism** — every artifact is
   `{spec_version, config_used, generated_at, stage, data}`; **no timestamps in
   the data block**; `sort_keys`; scores to 1 decimal, ratios to 4.
3. **Option-B per-indicator eval functions** — the spec almost certainly stores
   threshold bands as prose only (every prior subsystem did).
4. **The apex pattern** — `apex = Σ weight_i · block_score_i`, weights read from
   the spec at runtime, never hardcoded; a global gate; a null/verification path.
5. **The per-artifact/per-deliverable views as a PARALLEL deliverable** — lift the
   Pre-Processing Stage-3c `per_artifact_views` machinery (`05b_*.json`,
   re-weight existing indicator scores, inherited gates, null-on-empty). The
   per-deliverable scores are very likely this same pattern.
6. **The two redistribution mechanisms** — path-N/A (`applies_to_paths`) and
   evidence-tier-N/A (report absent → advisory) drop an indicator and renormalise
   the block weights. Pre-Processing's `na_redistribute` primitive ports.
7. **The self-validating smoke harness** — Pre-Processing's `EXPECT` table
   (per-scenario expected apex ± tol + exact flag set + verification field, exits
   non-zero on drift) + the analytical renorm formula
   `apex = 100 − W_block·w_ind·(100−band)/A_block` for hand-computing each EXPECT.
   Lift it from the start. See `../PreProcessing_CodeBase/tests/SCENARIOS.md`.
8. **The two-pass scenario design + problem-coverage map** — Pass 1 spec-internal
   band-walk, Pass 2 from your Processing Problems sheet, plus the ~40-row OWNED /
   verification / handoff map.
9. **Placeholder lifecycle** — operator-pending inputs carry `_status: PLACEHOLDER`;
   Stage 1 emits `PLACEHOLDER_INPUTS_DETECTED`.
10. **A non-gating quality field?** — Pre-Processing reported check-point quality
    via a non-gating `verification_status`. Processing may keep an analogous
    field, OR (more likely) **promote GCP/CP RMSE to a real SCORING block** —
    because at processing time you finally HAVE the bundle-adjustment residuals.
    Verify which.

## What likely does NOT carry forward (the divergences to expect)

Confirm each at Step 0:

- **Binary geospatial deliverable parsing — the heaviest new parser layer.** Its
  "source files" are likely the **deliverables themselves**: orthomosaic / DSM /
  DTM **GeoTIFF**, dense **point cloud** (LAS/LAZ), **3D mesh** (OBJ/PLY), contours,
  tiles — plus a **processing/quality report** (Pix4D quality report, Metashape
  report, ODM `stats`/logs) and a processing manifest. Reading these needs real
  geospatial libraries (**rasterio/GDAL** for rasters, **laspy** for point clouds,
  maybe **trimesh**) — a much bigger dependency footprint than `piexif`. **Confirm
  the exact source set from spec sheet 01 and whether the spec expects file-content
  parsing or report-declared metadata.** (Lesson from Pre-Processing: the optional
  TBC report parser was deferred to v2 — expect a similar "report-declared first,
  deep-file-parse later" tiering here.)
- **The processing/quality report is probably FIRST-CLASS, not optional.** In
  Pre-Processing the report was optional (advisory when absent). For Processing the
  report likely carries the core evidence (reprojection error, tie-point density,
  camera-calibration residuals, GCP/CP RMSE, GSD achieved, point-cloud density) —
  so it may be **critical**, inverting Pre-Processing's optional-report posture.
- **GCP/CP RMSE = the real accuracy verification (now a first-class signal).**
  Pre-Processing deferred check-point accuracy to a non-gating field because it had
  no residuals. Processing HAS them (from the bundle adjustment) — expect a
  **georeferencing-accuracy block** that scores GCP control RMSE and CP check RMSE,
  possibly with a catastrophic gate.
- **Per-deliverable scores may FEED the apex** (not just a parallel view) — confirm
  the aggregation. If each deliverable (ortho/DSM/DTM/cloud/mesh) is scored and
  rolled into the apex, Stage 3c is a different shape than Pre-Processing's.
- **Real raster / point-cloud analysis in Stage 3a** — coverage/hole detection on
  the ortho/DSM, point density and ground-classification ratios on the cloud,
  resolution vs target GSD, mesh watertightness. Expect genuine geospatial compute
  (the way Pre-Processing had 2-D hull geometry, but heavier), not just band
  lookups.
- **Large sample data.** Real deliverables are GB-scale. The gold-standard
  PLACEHOLDER set will likely need **tiny synthetic deliverables** (a small
  GeoTIFF + a small LAS) and/or a **report-declared** baseline — we did the
  EXIF-image synthesis for Pre-Processing; here we may synthesize a minimal raster
  + point cloud, or lean on a structured report. Decide at Step 1/2.
- **The global gate / null state will be different** — likely gated on
  "reconstruction failed" / "GCP RMSE catastrophic" / "required deliverable
  missing" / "output CRS wrong", not a per-point coverage failure.

## Project context — fill in any `<<<…>>>` you can; ask for what you can't

```
PROJECT_ROOT         = <<<absolute path to this Processing_CodeBase folder>>>
SUBSYSTEM_NAME       = processing                              ← confirm from spec
SPEC_BUNDLE_FOLDER   = <<<e.g. processing_confidence_score>>>      ← I'll confirm
SPEC_JSON_FILENAME   = <<<e.g. processing_confidence_score.json>>> ← I'll confirm
APEX_SCORE_NAME      = <<<e.g. processing_score>>>             ← read from spec
PER_DELIVERABLE_VIEWS = <<<confirm: parallel deliverable (05b) vs apex-feeding>>>
SCAFFOLD_REFERENCE   = ../PreProcessing_CodeBase/scripts/      ← primary lift (framework + harness + views)
SECONDARY_REFERENCE  = ../CheckPoint_CodeBase/scripts/         ← cross-point aggregation, IF deliverables aggregate like points
```

## Folder layout

**You provide these INPUTS before Step 0** (mirror the sibling layout; exact
shapes confirmed at Step 0 from spec sheet 01):

```
Processing_CodeBase/
├── BUILD_PROMPT_TEMPLATE_v2.md             ← copy from a sibling
├── PROCESSING_KICKOFF_PROMPT.md            ← this file
├── <SPEC_BUNDLE_FOLDER>/                    ← the frozen processing bundle:
│   ├── processing_confidence_score.json       (master — single source of truth)
│   ├── 01_source_files.csv
│   ├── 02_source_fields.csv
│   ├── 03_derived_fields.csv
│   ├── 04_indicators.csv
│   ├── 05_building_blocks.csv
│   ├── 06_<apex>_score.csv  +  06b_..._meta.csv
│   ├── 07_flags.csv
│   ├── 08_problem_coverage_map.csv
│   ├── 09_per_deliverable_*.csv (if a per-deliverable/views sheet exists)
│   └── *.xlsx / *.html (optional companions)  + Processing Problems sheet
└── sample_data/                            ← real (or synthesized) processing outputs;
    └── <shape per spec sheet 01>              likely deliverables (GeoTIFF / LAS /
                                               mesh) + quality report + manifest
```

If real sample data does not exist yet (likely — deliverables are large), say so —
we will **synthesize a spec-faithful gold-standard set** (as we did for
Pre-Processing's EXIF geotags), marked `_status: PLACEHOLDER`, that scores the apex
near 100 so the baseline scenario is a clean control. For Processing this may be a
tiny synthetic raster + point cloud and/or a structured quality report.

**The build GENERATES these** (output of Steps 1–12 — do NOT create at Step 0):

```
├── paths.json                              ← Step 1
├── scripts/  (run_pipeline.py, stage1_inventory.py, stage2_merge.py,
│              stage3a_derived.py, stage3b_indicators.py, stage3c_blocks.py,
│              stage3d_score.py, common.py, parsers/, test_scenarios.py,
│              make_sample_data.py)
├── outputs/  (01_inventory.json … 06_<apex>_score.json  + 05b_per_deliverable*.json)
├── tests/scenarios/  (one self-contained dir per scenario + _summary.json
│                      + _pass2_problem_coverage.{csv,json} + SCENARIOS.md)
└── cache/  (only if an external API is consumed)
```

## Binding process constraints (identical to the prior five builds)

1. **STOP after every step and wait for my explicit "OK"** before the next.
   Step 12 (smoke-test harness) is the final step. Do not chain steps.
2. **Never hardcode weights, thresholds, or formulas** — read them from the spec
   bundle at runtime. Sanctioned exceptions: Option-B per-indicator eval functions
   for prose-only bands, and engineering tuneables declared as named constants in
   `*_meta.tuneables`.
3. **Run an explicit 6–10 check audit on the high-impact steps (7, 10, 12)** and
   report pass/fail before claiming done.
4. **Determinism:** NO timestamps inside the data block (only the envelope's
   `generated_at`); for the test harness, omit `generated_at` entirely so artifacts
   are byte-stable across runs.
5. **Lift scaffolding from `../PreProcessing_CodeBase/scripts/`, but verify each
   file against the processing spec before trusting it.** `common.py`, the
   orchestrator skeleton, the envelope/determinism contract, the per-artifact-views
   machinery, and the harness framework should port with light edits; the parsers,
   derived geometry/raster analysis, and indicator eval functions will be largely
   new — prove each lifted file still applies; don't assume it.
6. **Bake in the self-validating harness from v1:** per-scenario `EXPECT` of apex
   score + exact flag set (+ any verification field), exit non-zero on mismatch —
   not report-only. Hand-compute each EXPECT with the renorm formula, then pin from
   a capture run.

## What I'll share during the build

- **The processing spec bundle** — the frozen JSON + per-sheet CSVs. I'll drop it
  into `<SPEC_BUNDLE_FOLDER>/` and confirm the exact folder/filename at Step 0.
  (Note: the Pre-Processing spec was bumped v1.0 → v1.1 mid-build — at Step 0
  machine-verify the counts in `_meta.counts` against the array lengths, and
  re-check if I hand you an updated bundle.)
- **The Processing Problems sheet** (`xlsx`/`csv`) — when you reach Step 12, I'll
  attach it. It seeds the **Pass 2** real-world scenarios with concrete numerical
  examples drawn from the prose cells, and each problem is mapped OWNED /
  verification / OUT-OF-SCOPE exactly as the prior five problem maps were.
- **Sample data** — real if I have it; otherwise OK me to synthesize a
  gold-standard placeholder set (tiny synthetic deliverables / structured report).
- **Decisions on spec-vague thresholds** — when you ask (e.g. reprojection-error
  bands, GCP/CP RMSE multipliers, point-density target, coverage/hole %, GSD
  tolerance, deliverable-completeness rules), I'll give a direction or tell you to
  default to the industry-standard rubric (Section 7i / 9 of the template).

## Start now — Step 0 (comprehension only — STOP at the end)

Per Section 12 of `BUILD_PROMPT_TEMPLATE_v2.md`:

1. **List the contents** of this folder and confirm the spec bundle path +
   filename. Machine-verify `_meta.counts` vs the actual array lengths.
2. **Read** `<SPEC_BUNDLE_FOLDER>/<SPEC_JSON_FILENAME>` end to end.
3. **Answer the comprehension questions** (confirm your bundle's sheet numbering):
   - The N **source files** (sheet 01) — `file_id` and `file_name`. **What ARE
     they?** (deliverable rasters/point-clouds/meshes? a quality report? a
     manifest? upstream artifacts?) And which are CRITICAL vs OPTIONAL.
   - The M **building blocks** (sheet 05/06) with their **weights in the apex** and
     the indicators within each.
   - **The per-deliverable scores** — how many deliverables, which indicators each
     consumes, and **whether they feed the apex or are a parallel `05b` deliverable**.
   - All **flag** names (sheet 07) grouped by `raised_at_stage`, with severity.
   - The **apex score formula** (sheet 06/08) — blocks, weights, global-gate
     condition, null/verification handling.
   - The **problem-coverage map** (sheet 08) — how many problems, which look OWNED
     here vs deferred elsewhere, and which were handed *to* processing by the
     other five subsystems.
4. **Produce a scaffolding-diff report** — the most valuable part. Be explicit:
   - **What are the source files really** — and does scoring **read file content**
     (GeoTIFF/LAS/mesh) or **report-declared metadata**? Which geospatial libs
     does that imply (rasterio/GDAL, laspy, trimesh), and what's the v1-vs-v2
     tiering (report-declared now, deep-file-parse later)?
   - **Per-deliverable scores: parallel view (05b) or apex-feeding?** Does the
     GCP/CheckPoint `mean − k·(100−min)` cross-point aggregator apply (deliverables
     aggregated like points), or is Stage 3c a single-survey weighted rollup +
     per-deliverable views like Pre-Processing?
   - **Is GCP/CP RMSE a scoring block now** (vs Pre-Processing's non-gating
     verification_status)? Is there a catastrophic accuracy gate?
   - Which Pre-Processing scripts port **as-is** (almost certainly `common.py`, the
     orchestrator skeleton, the envelope/determinism contract, the views machinery,
     the harness framework), which need **edits** (Stage 1 classification, Stage 2
     merge), which are **entirely new** (the deliverable parsers, raster/point-cloud
     derived analysis, indicator evals)?
   - New indicators/flags with no sibling analogue? (Expect reprojection error,
     tie-point density, GCP/CP RMSE, point density/classification, coverage/holes,
     GSD-achieved, deliverable completeness, mesh integrity.)
   - Real **raster / point-cloud computation** in Stage 3a?
   - Threshold bands prose-only (**Option B**) or machine-evaluatable (Option A)?
   - What does the **global gate / null/verification state** trigger on?
   - Is the chain **runtime-independent** (reads processing artifacts, NOT
     `pre_processing_score` / sibling outputs), as the prior five held? Confirm.
5. **STOP.** Wait for my OK before Step 1.

Do **not** create folders, write code, write `paths.json`, or run anything yet —
Step 0 is pure comprehension. The build begins only after I review your
comprehension + scaffolding-diff report and say OK.

---

## Reference paths — lift scaffolding from Pre-Processing first

Processing scaffolding to port (verify each against the processing spec — the
data-layer files will be the heaviest rewrites of any build so far):

| Reference file (PreProcessing_CodeBase/scripts/) | What it does | Likely port effort |
|---|---|---|
| `common.py` | envelope + determinism helpers | **as-is → very low** |
| `run_pipeline.py` | orchestrator (takes `paths.json`), per-stage wall time | rename outputs + wire `05b` → low |
| `stage1_inventory.py` | survey-level input discovery + placeholder detection + critical-set policy | **rework** classification to the deliverable/report/manifest set → medium |
| `parsers/parse_manifest.py` | 40-field JSON manifest (validators, soft-enums, per-artifact dicts) | adapt to the processing manifest → medium |
| `parsers/parse_geotags.py` | EXIF reader (piexif) | **drop / replace** → new |
| `parsers/parse_gcp_coords.py` / `parse_cp_coords.py` | CSV coord sets (shared `_read_coord_csv`) | reuse IF residuals arrive as CSV → low-medium |
| `parsers/parse_report.py` | optional structured-JSON report, format-tiered | **adapt + expand** — report likely first-class → medium-high |
| `parsers/parse_*` (NEW) | GeoTIFF (ortho/DSM/DTM), LAS/LAZ point cloud, mesh, quality-report extraction | **new** → high |
| `parsers/geometry.py` | dependency-free 2-D geometry (hull, bbox, distances) | reuse; add raster/cloud helpers → medium |
| `stage2_merge.py` | survey-level source-field assembly + per-source audit + previews | rework to deliverable assembly → medium-high |
| `stage3a_derived.py` | 37 derived (frame-consistency, fractions, 2-D geometry, σ-ratios) | **rewrite** — raster/point-cloud + RMSE + completeness derivations → high |
| `stage3b_indicators.py` | Option-B per-indicator eval + trace block + path/evidence N/A + sigma aggregation | framework ports; all eval fns new → medium-high |
| `stage3c_blocks.py` | block rollups + per-artifact views (05b) + 2 internal gates + dual redistribution | framework + views machinery port; per-deliverable shape per spec → medium |
| `stage3d_score.py` | apex weighted sum + global gate (force-to-0) + verification field + all-stage flag aggregation | framework ports; gate/verification per spec → medium |
| `make_sample_data.py` | deterministic, self-verifying gold-standard generator | **rewrite** — synthetic deliverables/report → high |
| `test_scenarios.py` | self-validating EXPECT harness + Pass-1/Pass-2 + problem map + SCENARIOS.md | lift framework + EXPECT pattern + renorm formula; scenarios new → medium |

Sibling scenario directories to study:
`../PreProcessing_CodeBase/tests/scenarios/` (63 self-validating scenarios) and
`../PreProcessing_CodeBase/tests/SCENARIOS.md` (the scenario-design rationale).

Lift whatever's reusable. For each lifted file, **prove it still applies to the
processing spec — don't blindly copy.** This subsystem is the reconstruction +
deliverable-quality layer; its data layer (binary geospatial files + a rich
quality report) differs more from the five upstream scores than they differ from
each other.

End of kickoff prompt.
