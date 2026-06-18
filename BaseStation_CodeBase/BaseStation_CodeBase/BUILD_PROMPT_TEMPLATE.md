# Provenance-Pipeline Build Prompt — Template

This prompt builds a scoring pipeline from a frozen provenance-bundle spec.
It was authored after building the **drone PPK** pipeline end-to-end (Stages 1
through 3d + smoke testing) and is generalised so the same disciplined
process can be repeated for any other provenance bundle (base station,
GCP subsystem, processing universe, etc.).

To use this template for a new subsystem (e.g. **base station PPK**):

1. Create a fresh project folder, e.g. `BaseStation_CodeBase/`.
2. Drop the spec bundle into `<project_root>/<bundle_folder>/` (mirroring how
   `drone_provenance_ppk/` looks in the drone project).
3. Drop the real sample data into `<project_root>/sample_data/<subfolder>/`.
4. Paste THIS file's content into a new Claude session as the project prompt.
5. Replace every `<<<…>>>` placeholder below with the subsystem-specific
   values from the spec's sheet 01.
6. Answer Step 0's four comprehension questions before any code lands.

The build will produce an identical artifact tree:
`outputs/01_inventory.json` → `02_source_fields.json` → `03_derived_fields.json`
→ `04_indicators.json` → `05_building_blocks.json` + `05b_*.json` →
`06_<apex_score_name>.json`, plus a `tests/scenarios/` smoke harness.

---

## 0. Project context — fill in for the new subsystem

```
PROJECT_ROOT       = <<<absolute path to the new project folder>>>
SUBSYSTEM_NAME     = <<<e.g. base_station_ppk>>>
SPEC_BUNDLE_FOLDER = <<<e.g. base_provenance_ppk>>>
SPEC_JSON_FILENAME = <<<e.g. base_provenance_ppk.json>>>
APEX_SCORE_NAME    = <<<e.g. base_score>>>          ← drone called this drone_score
DRONE_SCORE_BUNDLE_PATH = <<<for cross-bundle reference if needed>>>
```

The spec bundle is **the single source of truth**. Read it at runtime; never
hardcode weights, thresholds, formulas, or band conditions.

---

## 1. Folders that exist when you start

```
<PROJECT_ROOT>/
├── <SPEC_BUNDLE_FOLDER>/              # the frozen provenance bundle
│   ├── <SPEC_JSON_FILENAME>           # canonical JSON
│   ├── *.csv                          # per-sheet CSVs (reference)
│   └── *.xlsx                         # Excel mirror (reference)
│
└── sample_data/                       # real survey inputs for this subsystem
    └── <subfolder per source>/        # e.g. base_rinex/, base_metadata.json/
```

## 2. Folders you will create

```
<PROJECT_ROOT>/
├── paths.json                         # config (you write this in Step 1)
├── scripts/                           # all pipeline code
│   ├── run_pipeline.py
│   ├── stage1_inventory.py
│   ├── compute_derived.py
│   ├── compute_indicators.py
│   ├── compute_blocks.py
│   ├── compute_<apex>.py
│   ├── test_scenarios.py              # smoke-test harness (Step 12)
│   └── parsers/
│       ├── parse_<source_a>.py
│       ├── parse_<source_b>.py
│       └── ...                        # one per source file in sheet 01
│
├── outputs/                           # per-stage JSON artifacts (auto-created)
│   ├── 01_inventory.json
│   ├── 02_source_fields.json
│   ├── 03_derived_fields.json
│   ├── 04_indicators.json
│   ├── 05_building_blocks.json
│   ├── 05b_<parallel_deliverable>.json # if the spec has a parallel block (CAL_CONF in drone)
│   └── 06_<apex>_score.json
│
├── cache/                             # external-API responses (if any)
│   └── <api_name>/
│
└── tests/                             # smoke-test outputs
    └── scenarios/<scenario_name>/
```

---

## 3. The pipeline shape (build in this strict order)

### Stage 1 — Discovery & inventory
Walk the input folders, identify files by extension + content, verify expected
counts, write `outputs/01_inventory.json`. **Hard-fail** the pipeline if
anything *critical* is missing (each subsystem defines its own critical set
from sheet 01).

### Stage 2 — Parse to canonical source-field JSON
One parser per source file in sheet 01. Each parser emits the L1F_* fields
it owns (per sheet 02), plus a `parser_meta` block with provenance / trust
info.  Then **merge** all parser outputs into `outputs/02_source_fields.json`.

At the merge step, compute any **cross-parser source fields** (e.g. in the
drone pipeline, `pre_buffer_sec` = `flight_start_utc - obs_start_utc` needs
both the RINEX parser AND the BIN parser).

`pre_score_ingestion` flags (per sheet 09) fire here — e.g. count-mismatch
checks across sources.

### Stage 3a — Compute derived fields
`scripts/compute_derived.py` reads source fields + spec sheet 03. Topologically
sort derivations (some L2D depend on other L2D). Emit
`outputs/03_derived_fields.json`. Notes block per field explains any
approximations, heuristics, or fallbacks.

### Stage 3b — Compute indicators with thresholds and flags
`scripts/compute_indicators.py` reads source + derived JSON + sheet 04 & 05.
Evaluate each indicator's threshold bands in `band_order` top-down — **first
match wins**.

Every indicator output includes a **trace block**:
```json
{
  "indicator_id": "L3I_…",
  "indicator_name": "…",
  "score": 72,
  "band_matched": "TH_…",
  "condition": "…",
  "input_value": …,
  "flags_raised": [ … ]
}
```

`threshold_band` and `internal_gate` flags fire here.

### Stage 3c — Roll up building blocks
`scripts/compute_blocks.py` reads indicators + spec sheets 06 & 07. Compute
each block's weighted score per its `formula_expression`. Apply block-internal
gates per sheet 06 (e.g. drone's `image_validity_score < 30 → image_capture_score = 0`).
If the spec defines a parallel deliverable (drone's CAL_CONF), emit it as
`outputs/05b_*.json`.

### Stage 3d — Compute apex score
`scripts/compute_<apex>.py` reads blocks + spec sheet 08. Apply the global
gate (if any). Compute the weighted sum using **spec-derived weights at
runtime, never hardcoded**. **Aggregate flags from ALL prior stages** into
the apex output's `all_flags_aggregated` so one artifact carries the full
flag audit trail.

### Orchestrator
`scripts/run_pipeline.py` reads `paths.json` and runs Stages 1 → 2 → 3a → 3b →
3c → 3d in order, halting on hard failures.

### Smoke-test harness (Step 12)
`scripts/test_scenarios.py` mutates the baseline `02_source_fields.json` per
scenario, re-runs Stages 3a → 3d into `tests/scenarios/<name>/`, captures
drone_score / block scores / flag list, and reports a side-by-side table.

---

## 4. `paths.json` — write this FIRST (Step 1)

```json
{
  "survey_id": "<<<e.g. sample_data>>>",
  "spec_version": "<<<read from spec _meta.version>>>",
  "spec_file": "<<<SPEC_BUNDLE_FOLDER>>>/<<<SPEC_JSON_FILENAME>>>",
  "inputs": {
    "<<<source_a_name>>>_folder": "sample_data/<<<source_a_subfolder>>>/",
    "<<<source_b_name>>>_file":   "sample_data/<<<source_b_filename>>>",
    ...
  },
  "outputs": {
    "stage1_inventory":          "outputs/01_inventory.json",
    "stage2_source_fields":      "outputs/02_source_fields.json",
    "stage3_derived":            "outputs/03_derived_fields.json",
    "stage3_indicators":         "outputs/04_indicators.json",
    "stage3_building_blocks":    "outputs/05_building_blocks.json",
    "stage3_<parallel>":         "outputs/05b_<parallel>.json",
    "stage3_<apex>":             "outputs/06_<apex>_score.json"
  },
  "options": {
    "<api_name>_cache_dir": "cache/<api_name>/",
    "<api_name>_timeout_sec": 30,
    "fail_fast": true,
    "log_level": "INFO"
  }
}
```

Every stage script takes this config path as its only argument and reads
inputs/outputs from it. **Never hardcode paths.**

---

## 5. Non-negotiable rules (carried over from drone build)

1. **Spec is source of truth.** If you find yourself writing
   `if ratio >= 0.99: return 100`, STOP — that 0.99 and 100 must come from
   sheet 05 at runtime. Same for all weights and formulas.

2. **Every output JSON has this envelope shape:**
   ```json
   {
     "spec_version": "<from spec _meta.version>",
     "config_used": <resolved paths.json content>,
     "generated_at": "<ISO timestamp>",
     "stage": "<stage name>",
     "data": { ... actual stage output ... }
   }
   ```

3. **Determinism.** Sort dict keys in JSON output. Round scores to 1 decimal,
   ratios to 4 decimals. No embedded timestamps in the `data` block — only
   in the envelope's `generated_at`.

4. **Flag wiring matches sheet 09's `raised_at_stage` column exactly:**
   - `threshold_band` flags: raised inside `compute_indicators.py`
   - `internal_gate` flags: raised inside `compute_blocks.py` when the gate trips
   - `global_gate` flags: raised inside `compute_<apex>.py`
   - `pre_score_ingestion` flags: raised by Stage 2 parsers or at merge

5. **Fail loudly on missing source fields.** If sheet 02 says field X exists
   and Stage 2 didn't produce it, halt — don't pass `None` to Stage 3.

6. **Document every honest approximation / fallback in `_notes`.** E.g. drone
   used a heuristic PDOP estimate and a BIN-CAM fallback for missing EXIF GPS.
   Each shipped with a `notes` entry explaining the choice.

7. **Spec inconsistencies get fixed via version bump.** Drone shipped a v1.1.2
   patch for a typo in TH_010. Don't silently work around — document and bump
   minor version, preserving previous_version + changelog.

---

## 6. Build process — STRICT stage-by-stage with closeout checklists

You will NOT build all stages in one go. After each step below you STOP and
wait for the operator's explicit OK to continue. Do not anticipate the next
step.

### Step 0: Comprehension
Read `<SPEC_BUNDLE_FOLDER>/<SPEC_JSON_FILENAME>` and list:
- The N source files from sheet 01 (`file_id` and `file_name`)
- The M building blocks from sheet 06 with their PPK weights
- All flag names from sheet 09 grouped by `raised_at_stage`
- The apex score formula from sheet 08

STOP. Wait for OK.

### Step 1: Folder structure + `paths.json` + stub `run_pipeline.py`
Create the folder skeleton above. Write `paths.json`. Stub
`run_pipeline.py` should accept the config path and print
"Stage 1 not implemented yet" cleanly.

Run it; show the operator the stub output.

STOP. Wait for OK.

### Step 2: Stage 1 inventory
Build `scripts/stage1_inventory.py`. Run against real `sample_data/`. Show
the operator `outputs/01_inventory.json`. Surface any warnings (e.g.
duplicate files, mixed formats) but only hard-fail on truly critical absences.

**Closeout checklist for Step 2:**
- All folders walked? (yes/no with paths)
- All extensions classified? (list)
- Warnings raised? (count + list)
- Hard failures raised? (count + list)
- Envelope shape matches rule 2? (yes)

STOP. Wait for OK.

### Steps 3–6: Parsers + Stage 2 merge
One step per parser. After each parser is built:
- Run it standalone, show summary output
- Print the **closeout checklist** for that source's sheet 02 rows
- For each L1F_* field: status (✅ produced / ⏸ deferred to merge / ⚠ honest
  empty), value, notes if any
- Highlight which sheet 02 rows the parser implemented
- List any flags the parser raised

After all parsers built, build the **Stage 2 merge** in
`scripts/run_pipeline.py`:
- Run each parser's `parse()` function
- Merge L1F_* fields into one envelope
- Compute any cross-parser source fields
- Evaluate pre_score_ingestion flags that need multiple parsers
  (e.g. drone's CAM_COUNT_MISMATCH compares BIN cam_record_count vs
  total_images from parse_images)
- Write `outputs/02_source_fields.json`

**Closeout checklist for Step 6:**
- Total source fields produced = N (matches spec `_meta.audit_counts.source_fields`)
- Cross-parser computations performed (list with values)
- Stage 2 flags raised (list with severity)
- Per-parser `parser_meta` carried forward into envelope

STOP. Wait for OK.

### Step 7: Stage 3a — derived fields
Build `scripts/compute_derived.py`. For each L2D_* field in sheet 03:
- Identify dependencies (L1F_* and/or L2D_*)
- Topologically sort derivations
- For complex derivations (geometry, polygons, time-series stats), implement
  the algorithm faithfully
- For derivations the spec admits are uncomputable from available data,
  return `None` with a `_notes` entry explaining honestly

**Expected encounter:** real-data conditions where spec-defined fields are
genuinely null. Examples from drone build:
- PDOP requires per-epoch ephemeris (spec acknowledges this)
- EXIF-GPS-dependent overlap when the camera has no GPS chip
- Per-epoch acquisition time when only aggregates are surfaced

For each null, decide between:
- **Honest null + note** — when no data exists
- **Approximation with documentation** — when aggregate data implies the value
  (drone used `sat_count_min ≥ 4 AND cn0_mean ≥ 30` → acquisition_time = 0)
- **Cross-source fallback** — when another source carries equivalent info
  (drone's BIN CAM positions as fallback for missing EXIF GPS)

**Project the indicator-level consequences BEFORE writing the eval** —
if a null in L2D_X cascades to a "bottom band" in L3I_Y, that may fire a
spurious flag. Decide: fallback (preferred) or honest null + spec amendment.

**Closeout checklist for Step 7:**
- Total derived fields = N (matches sheet 03 row count)
- Null derived fields counted; reason for each in `_notes`
- Cruise/window filters applied where the spec mandates them
- Cross-stage dependencies surfaced for the merge step

STOP. Wait for OK.

### Step 8: Stage 3b — indicators with threshold bands and flags
Build `scripts/compute_indicators.py`. **Choose your evaluator strategy
upfront:**

**Option A — generic expression evaluator** that reads `condition_expression`
strings from sheet 05 and evaluates them in a restricted namespace. *Risk:*
spec authors often write prose ("X is absent", "Y is mixed", "API_UNAVAILABLE
AND Z == calm"), uppercase `AND`/`OR`, and shorthand variable reuse ("min"
referring to a prior `min(...)`) that can't be safely Python-eval'd.

**Option B — per-indicator eval function** (one Python function per indicator).
*Pros:* handles all the prose; auditable. *Cons:* maintenance cost when spec
adds indicators. **Drone build chose Option B.**

Whichever you pick, **score values and flag names ALWAYS come from the spec at
runtime**, never hardcoded.

Every indicator emits the trace block format from Section 3.

**Closeout checklist for Step 8:**
- Total indicators evaluated = N (matches sheet 04 row count)
- Total threshold rows referenced = (matches sheet 05)
- Internal-gate flags raised (canonical home in Stage 3c, not here)
- Hard-rule indicators (like drone's L3I_GNSS_004) correctly score 0 when
  triggered
- Any spec inconsistencies found? (e.g. drone caught TH_010 dead-zone) →
  decide: fix via spec patch with version bump OR document + workaround

STOP. Wait for OK.

### Step 9: Stage 3c — building-block rollups
Build `scripts/compute_blocks.py`. For each block in sheet 06:
- Get composition (indicator_id → weight) from sheet 07
- Verify weight sum == 1.0 (audit check)
- Compute weighted sum
- Apply block-internal gate per sheet 06 if defined
- Round score to 1 decimal

If sheet 06 defines a parallel deliverable (drone's CAL_CONF with
`weight_in_<apex>_ppk = 0`), emit it as `outputs/05b_*.json` separately.

**Closeout checklist for Step 9:**
- All blocks computed = M (matches sheet 06)
- Internal gates checked (which triggered, which didn't)
- Parallel deliverable surfaced separately
- Hand-math verification of one block

STOP. Wait for OK.

### Step 10: Stage 3d — apex score
Build `scripts/compute_<apex>.py`. Read blocks + spec sheet 08:
- Compute `<apex>_score = Σ weight_i × block_score_i` (weights from spec)
- Apply global gate if defined (e.g. drone's
  `image_capture_score == 0 → drone_score = 0`)
- Aggregate flags from Stage 2 (`_flags_raised_stage2`), Stage 3b
  (`flags_raised_stage3b`), Stage 3c (`flags_raised_stage3c`), and Stage 3d's
  own gate-triggered flag into a single `all_flags_aggregated` array

Each aggregated flag retains its `_origin_stage` tag.

**Closeout checklist for Step 10:**
- Apex formula expression from spec.<apex>.metadata
- Global gate condition + action documented
- Flag aggregation: count by severity + by origin_stage
- Parallel deliverable score (if applicable) surfaced as a separate
  non-contributing entry

STOP. Wait for OK.

### Step 11: Wire orchestrator end-to-end and run fresh
- Clear `outputs/` (preserve `cache/` to skip API hits)
- Run `python3 scripts/run_pipeline.py paths.json` end-to-end
- Show the full output tree
- Verify all envelopes carry the correct `spec_version`
- Capture wall-time per stage

STOP. Wait for OK.

### Step 12: Smoke-test harness + scenarios
Build `scripts/test_scenarios.py` per the drone harness pattern:

- Reads baseline `outputs/02_source_fields.json`
- For each scenario: applies source/derived/parser_meta overrides + flag
  injections; runs Stages 3a → 3d into `tests/scenarios/<name>/`
- Reports side-by-side table: drone_score / block scores / flags

**Scenario design — TWO passes:**

**Pass 1 — spec-internal coverage (~10–12 scenarios):**
- Baseline (control)
- Each gate trigger (internal + global)
- Each enum-band drop (e.g. SELF_CALIBRATED, MISMATCH)
- Each fallback path (e.g. API_UNAVAILABLE proxy)
- Each `threshold_band` flag (high wind, outdated calibration, etc.)
- Perfect-survey ceiling check
- All-flags-fire stress test

**Pass 2 — real-world gap analysis cases:**
When the operator provides a gap-analysis CSV/XLSX (like CBMI's
gap_analysis_enriched.csv was provided for drone):
- Add one scenario per **Fully Covered** gap (verifies coverage claim)
- Add one scenario per **Partial / Field Gap** with a derivable proxy (e.g.
  altitude variance as proxy for VTOL transition)
- Skip **Hard Gap**, **Disabled**, **Out of Scope** entries (no fields to mutate)

**Closeout checklist for Step 12:**
- Total scenarios run = N
- Crashes = 0 (any crash is a real bug to investigate)
- Score directional changes match expectations (per-scenario)
- Deviations from expectation? — investigate honestly:
  - If pipeline didn't fire an expected flag → may be a real gap in spec
    coverage (note for gap analysis)
  - If pipeline scored higher/lower than expected → trace the cascade
- Smoke test exposed any data-integrity issues? (e.g. drone smoke test
  caught a RINEX↔BIN date mismatch in the baseline data)

STOP. Final closeout.

---

## 7. Cross-stage patterns to watch for (lessons from drone build)

### 7a. Hardware override (SRC_UI_02 pattern)
If a source's standardised metadata fields are commonly stripped (drone's
RINEX header REC/ANT often blank after u-blox→RINEX conversion), introduce
an **optional operator override file**:
- New source `SRC_UI_02 Hardware Override` (sidecar, not in frozen spec until
  formalised)
- Located at `sample_data/<subsystem>/hardware.json`
- Parser uses **4-tier resolution priority**:
  1. Authoritative source header (e.g. RINEX REC field)
  2. Operator override file
  3. Inferred from comment/string fields
  4. Empty string + parser_meta note
- Each L1F_* value carries `<field>_source` tag in parser_meta

This lets the spec stay frozen while the operator fills in real-world gaps.

### 7b. Cross-source consistency checks
Drone's `FLG_019 CAM_COUNT_MISMATCH` (BIN CAM count vs SD image count) fires
at merge because both sources must be present. Add similar checks for any
field the spec defines but only verifies cross-source — e.g. for base station:
RINEX date vs survey-form date, antenna height in form vs RINEX header, etc.

### 7c. Fallback chains for missing modern features
Drone's BIN-CAM-position fallback for missing EXIF GPS rescued 6 derived
fields and 3 indicators that would otherwise have null-fail-scored. For base
station, anticipate similar fallbacks (e.g. inferred receiver model from
COMMENT lines if header is stripped).

### 7d. Spec-driven score floor probes
The "perfect_survey" scenario rarely reaches 100 on the first try — it caps
where structural source fields can't be overridden through L1F. Note the
ceiling as feedback to spec authors (may indicate redundant indicators or
miscalibrated weights).

### 7e. Cross-bundle integrity
If multiple provenance bundles exist (drone, base station, GCP), eventually
the apex score in each may need a **cross-bundle flag** (e.g. base RINEX day
≠ drone BIN day → both subsystems should fire). Track these as candidate
spec amendments, not silent passes.

---

## 8. Closeout checklist template (use after every step)

```
## Step N closeout — <STAGE NAME>

### Coverage: K / N of <spec sheet> rows
[per-field/per-block table with status, value, notes]

### Flags raised: <count> at this stage
[per-flag table]

### Implementation choices on record:
[numbered list — e.g. "Chose Option B eval over Option A because…"]

### Open items unchanged from earlier:
[carryforward list]

### Net verdict:
[blocking / not blocking / requires decision before next step]
```

---

## 9. Open-items log (maintained throughout the build)

Maintain a running log with three sections:
- **Fully closed** — what shipped + verified
- **Deferred by design** — happens in a later named step
- **Quality-of-life deferrals** — non-blocking, may revisit (datetime
  normalisation, validity-check upgrades, optional caches, etc.)
- **Spec amendment candidates** — typos, inconsistencies, missing fields
  surfaced during build (each one gets a version-bump proposal)

Drone build's final open-items log had: 6 quality-of-life deferrals (none
blocking), 1 spec patch landed (v1.1.2), 0 hard failures, 0 unresolved bugs.

---

## 10. Start now — Step 0

Read `<SPEC_BUNDLE_FOLDER>/<SPEC_JSON_FILENAME>` and answer the four
comprehension questions above. Do not do anything else yet.

After Step 0, wait for the operator's "OK" before Step 1.

Throughout the build, the operator may share real-world scenario sheets,
gap analyses, or known data quirks. **Treat these as inputs to the smoke
test scenarios in Step 12 (Pass 2)** — they should shape the test suite,
not the production pipeline code.
