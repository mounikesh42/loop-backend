# Provenance-Pipeline Build Prompt — Template v2

This prompt builds a scoring pipeline from a frozen provenance-bundle spec.
It is the **v2** revision, authored after building the **drone PPK** and
**base station PPK** pipelines end-to-end. Every pattern, audit, and decision
discovered during those builds is baked in here.

To use this template for a new subsystem (e.g. **GCP PPK**, **processing
universe**, etc.):

1. Create a fresh project folder, e.g. `GCP_CodeBase/`.
2. Drop the spec bundle into `<project_root>/<bundle_folder>/` (mirroring how
   `drone_provenance_ppk/` and `base_station_confidence_score/` look in their
   project folders).
3. Drop the real sample data into `<project_root>/sample_data/<subfolder>/`.
4. Paste THIS file's content into a new Claude session as the project prompt.
5. Replace every `<<<…>>>` placeholder below with the subsystem-specific
   values from the spec's sheet 01.
6. Answer Step 0's four comprehension questions before any code lands.
7. **Operator (you) shares throughout the build:** any CBMI Problems sheet
   (xlsx/csv) for the subsystem — used at Step 12 Pass 2 to seed real-world
   scenarios with concrete numerical examples drawn from the sheet's prose.

The build will produce an identical artifact tree:
`outputs/01_inventory.json` → `02_source_fields.json` →
`03_derived_fields.json` → `04_indicators.json` → `05_building_blocks.json`
+ `05b_*.json` if a parallel deliverable exists → `06_<apex_score_name>.json`,
plus a `tests/scenarios/` smoke harness with 17+ scenarios.

---

## Changelog from v1 → v2

| Section | What changed |
|---|---|
| Section 6 (Build process) | Added explicit **audit step** after Steps 7 and 10 (high-impact ones); each audit runs 6-10 independent checks before closing |
| Section 7 (Cross-stage patterns) | Added 7f (NAV-driven PDOP), 7g (georinex hybrid), 7h (Placeholder detection), 7i (Threshold rubric), 7j (Self-contained scenario dirs) |
| Section 8 (NEW — Placeholder lifecycle) | Full documentation of the placeholder pattern, Stage 1 warning, operator handoff |
| Section 9 (NEW — Threshold rubric) | Industry-standard values for common GNSS thresholds; CLEARLY WRONG / DEFENSIBLE / ALREADY RIGHT classification |
| Section 12 (Step 12 — Smoke tests) | Pass 2 real-world scenarios driven by sheet-prose concrete numerical examples; problem-coverage map output (CSV+JSON) |
| Multiple stages | Mutated source_fields persisted per scenario for self-contained reproducibility |
| Stage 3a | Tuneables surfaced in `stage3a_meta.tuneables`; engineering picks vs spec values clearly distinguished |
| Stage 3b | Tuneables surfaced in `stage3b_meta.tuneables`; per-indicator eval functions (Option B) the default — spec sheets usually have prose-only threshold summaries |
| Stage 3c | Per-block weight-sum audit (must equal 1.0 — spec self-consistency check) |
| Stage 3d | All-stages-flag aggregation with `_origin_stage` preservation; `_handoff_crossdoc_candidates` kept separate from `all_flags_aggregated` |

---

## 0. Project context — fill in for the new subsystem

```
PROJECT_ROOT       = <<<absolute path to the new project folder>>>
SUBSYSTEM_NAME     = <<<e.g. gcp_ppk>>>
SPEC_BUNDLE_FOLDER = <<<e.g. gcp_provenance_ppk>>>
SPEC_JSON_FILENAME = <<<e.g. gcp_provenance_ppk.json>>>
APEX_SCORE_NAME    = <<<e.g. gcp_score>>>          ← drone called this drone_score; base called this base_station_score
DRONE_SCORE_BUNDLE_PATH         = <<<for cross-bundle reference if needed>>>
BASE_STATION_SCORE_BUNDLE_PATH  = <<<for cross-bundle reference if needed>>>
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
    └── <subfolder per source>/        # e.g. gcp_rinex/, gcp_metadata.json/
```

## 2. Folders you will create

```
<PROJECT_ROOT>/
├── paths.json                         # config (you write this in Step 1)
├── scripts/                           # all pipeline code
│   ├── run_pipeline.py
│   ├── stage1_inventory.py
│   ├── stage2_merge.py
│   ├── compute_derived.py
│   ├── compute_indicators.py
│   ├── compute_blocks.py
│   ├── compute_<apex>.py
│   ├── test_scenarios.py              # smoke-test harness (Step 12)
│   └── parsers/
│       ├── parse_<source_a>.py
│       ├── parse_<source_b>.py
│       ├── parse_nav.py               # if RINEX NAV ephemeris is consumed
│       ├── gnss_orbits.py             # if PDOP / sat geometry is computed
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
│   └── <api_name>/                    # e.g. noaa_swpc/, egm2008/
│
└── tests/                             # smoke-test outputs
    └── scenarios/
        ├── <scenario_name>/
        │   ├── 02_source_fields.json  # MUTATED input for this scenario
        │   ├── 03_derived.json
        │   ├── 04_indicators.json
        │   ├── 05_blocks.json
        │   └── 06_apex.json
        ├── _summary.json              # cross-scenario diff target
        ├── _pass2_problem_coverage.csv
        └── _pass2_problem_coverage.json
```

**NOTE (new in v2):** Each scenario directory is **self-contained** — it
includes the mutated source-fields envelope, so any reviewer can audit a
scenario without reading the mutator code.

---

## 3. The pipeline shape (build in this strict order)

### Stage 1 — Discovery & inventory
Walk the input folders, identify files by extension + content, verify expected
counts, write `outputs/01_inventory.json`. **Hard-fail** the pipeline if
anything *critical* is missing (each subsystem defines its own critical set
from sheet 01).

**NEW in v2:** Scan `sample_data/` JSON files for a top-level
`"_status": "PLACEHOLDER"` field. Emit `PLACEHOLDER_INPUTS_DETECTED` warning
listing each file. This catches the case where operators run a real survey
without replacing the test placeholders we generate during the build.

### Stage 2 — Parse to canonical source-field JSON
One parser per source file in sheet 01. Each parser emits the L1F_* fields
it owns (per sheet 02), plus a `parser_meta` block with provenance / trust
info.  Then **merge** all parser outputs into `outputs/02_source_fields.json`.

At the merge step, compute any **cross-parser source fields** (e.g. in the
drone pipeline, `pre_buffer_sec` = `flight_start_utc - obs_start_utc` needs
both the RINEX parser AND the BIN parser).

`pre_score_ingestion` flags (per sheet 09) fire here — e.g. count-mismatch
checks across sources. **For base station, no such flags existed; surface
this fact explicitly in `merge_notes` so a reader knows the contract.**

### Stage 3a — Compute derived fields
`scripts/compute_derived.py` reads source fields + spec sheet 03. Topologically
sort derivations (some L2D depend on other L2D). Emit
`outputs/03_derived_fields.json`. Notes block per field explains any
approximations, heuristics, or fallbacks.

**NEW in v2:** All engineering thresholds (acquisition stability, truncation
tolerance, disturbance composite thresholds, battery-min, supported RINEX
versions) MUST surface in `stage3a_meta.tuneables` so a reviewer can see them
in one place.

### Stage 3b — Compute indicators with thresholds and flags
`scripts/compute_indicators.py` reads source + derived JSON + sheet 04 & 05.
Evaluate each indicator's threshold bands in `band_order` top-down — **first
match wins**.

**Choice of evaluator strategy:**
- **Option A — generic expression evaluator** — if spec sheet 05 has
  machine-readable `condition_expression` strings, parse and eval them in a
  restricted namespace.
- **Option B — per-indicator eval function** — one Python function per
  indicator. **Required when spec stores threshold bands as prose only**
  (base station case; likely true for GCP too).

Every indicator output includes a **trace block**:
```json
{
  "indicator_id": "L3I_…",
  "indicator_name": "…",
  "score": 72,
  "band_matched": "TH_…",
  "condition": "…",
  "input_values": {…},
  "gate_triggered": true/false,
  "gate_action_spec": "…" | null,
  "flags_raised": [ … ]
}
```

`threshold_band` flags fire here. `internal_gate` flags fire at Stage 3c per
template rule 4.

**NEW in v2:** All engineering thresholds (multipath low/high, Kp threshold,
slips/hr boundary, battery-min, mid-band scores for "unconfirmed" paths) MUST
surface in `stage3b_meta.tuneables`.

### Stage 3c — Roll up building blocks
`scripts/compute_blocks.py` reads indicators + spec sheets 06 & 07. Compute
each block's weighted score per its `formula_expression`. Apply block-internal
gates per sheet 06 (e.g. drone's `image_validity_score < 30 → image_capture_score = 0`).
If the spec defines a parallel deliverable (drone's CAL_CONF), emit it as
`outputs/05b_*.json`.

**NEW in v2:** Per-block **weight-sum audit** — sum of indicator weights
within a block MUST equal 1.0 (spec self-consistency). If not, raise a
warning at Stage 3c. The drone and base builds both passed this; future spec
revisions could regress.

### Stage 3d — Compute apex score
`scripts/compute_<apex>.py` reads blocks + spec sheet 08. Apply the global
gate (if any). Compute the weighted sum using **spec-derived weights at
runtime, never hardcoded**. **Aggregate flags from ALL prior stages** into
the apex output's `all_flags_aggregated` so one artifact carries the full
flag audit trail.

**Each aggregated flag retains its `_origin_stage` tag.** A flag's
"origin stage" is the stage where the value was first emitted (parser,
3a composite, 3b threshold, 3c internal_gate, 3d global_gate). Use
`_tag_if_missing()` to backfill the tag if upstream forgot.

**NEW in v2:** Stage 2's `_handoff_crossdoc_candidates` MUST be preserved
into the apex envelope's `data._handoff_crossdoc_candidates` — these are NOT
"raised" flags but deferred items for the cross-bundle stage. Keep them
separate from `all_flags_aggregated`.

### Orchestrator
`scripts/run_pipeline.py` reads `paths.json` and runs Stages 1 → 2 → 3a → 3b →
3c → 3d in order, halting on hard failures.

### Smoke-test harness (Step 12)
`scripts/test_scenarios.py` mutates the baseline `02_source_fields.json` per
scenario, re-runs Stages 3a → 3d into `tests/scenarios/<name>/`, captures
the apex score / block scores / flag list, and reports a side-by-side table.

**NEW in v2:** Each scenario directory also writes the **mutated**
`02_source_fields.json` — directories are self-contained.

---

## 4. `paths.json` — write this FIRST (Step 1)

```json
{
  "survey_id": "<<<e.g. sample_data>>>",
  "subsystem": "<<<e.g. gcp_ppk>>>",
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

## 5. Non-negotiable rules (carried + extended from v1)

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
   - `threshold_band` flags: raised inside `compute_indicators.py` (Stage 3b)
   - `internal_gate` flags: raised inside `compute_blocks.py` (Stage 3c) when the gate trips
   - `global_gate` flags: raised inside `compute_<apex>.py` (Stage 3d)
   - `composite` flags: raised inside `compute_derived.py` (Stage 3a) — the composite-flag derivation evaluates the condition
   - `handoff` flags: raised inside `compute_derived.py` (Stage 3a) — including always-fire handoffs (drone autonomous_seed pattern)
   - `handoff_crossdoc` flags: NOT raised at Stage 1; preserved as `_handoff_crossdoc_candidates` in Stage 2 merge and carried into apex envelope
   - `pre_score_ingestion` flags: raised by Stage 2 parsers or at merge

5. **Fail loudly on missing source fields.** If sheet 02 says field X exists
   and Stage 2 didn't produce it, halt — don't pass `None` to Stage 3.

6. **Document every honest approximation / fallback in `_notes`.** E.g. base
   used a multipath proxy without elevation binning, a quadratic GLONASS
   propagation, and skipped BeiDou GEO rotation. Each shipped with a `notes`
   entry explaining the choice.

7. **Spec inconsistencies get fixed via version bump.** Don't silently work
   around — document and bump minor version, preserving previous_version +
   changelog.

8. **NEW: All engineering thresholds surface in `stage*_meta.tuneables`.**
   If you picked a number that the spec didn't define, every artifact must
   make that visible in one place so a reviewer can challenge it.

9. **NEW: Placeholder values carry `_status: PLACEHOLDER`.** Any JSON file
   you write with operator-pending values gets a top-level `_status` key.
   Stage 1 inventory detects and warns.

10. **NEW: Each scenario directory is self-contained.** Including the
    mutated `02_source_fields.json`. No reading mutator code to understand
    what the input was.

---

## 6. Build process — STRICT stage-by-stage with closeout checklists + audits

You will NOT build all stages in one go. After each step below you STOP and
wait for the operator's explicit OK to continue. Do not anticipate the next
step.

For **high-impact steps (7, 10, 12)** the build is followed by an explicit
**audit** with 6-10 independent checks before closing.

### Step 0: Comprehension
Read `<SPEC_BUNDLE_FOLDER>/<SPEC_JSON_FILENAME>` and list:
- The N source files from sheet 01 (`file_id` and `file_name`)
- The M building blocks from sheet 06 with their weights
- All flag names from sheet 09 (or wherever flags live in your spec)
  grouped by `raised_at_stage`
- The apex score formula from sheet 08

**Surface divergences from drone-pattern explicitly:** Does this spec have a
parallel deliverable like drone's CAL_CONF? Are there cross-document flags?
Are threshold bands prose-only (Option B required) or machine-evaluatable
(Option A)?

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

**Critical-set policy:** A "critical" file is one whose absence makes the
pipeline emit a meaningless score. Document the policy explicitly in the
step closeout. For base, only RINEX OBS was critical — OPLOG and FORM
absence is handled by spec-defined degrade-to-unconfirmed paths.

**Placeholder detection:** Scan all JSON inputs for top-level `_status:
PLACEHOLDER`. Emit `PLACEHOLDER_INPUTS_DETECTED` warning with the file list.

**Closeout checklist for Step 2:**
- All folders walked? (yes/no with paths)
- All extensions classified? (list)
- Warnings raised? (count + list)
- Hard failures raised? (count + list)
- Placeholder detection wired? (yes)
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

**RINEX parsing (if applicable):**
- Use **manual streaming parser** for the body (the drone and base builds
  both produced ~2 s wall time on ~150 MB files)
- Optionally swap header parsing to **georinex.rinexheader()** for vendor
  robustness — this is the "hybrid" pattern from base (see Section 7g)
- Don't use `georinex.load()` for the body — it's ~600× slower than manual
  streaming in our benchmarks
- Implement Hardware Override 4-tier resolution per Section 7a if header
  fields (MARKER, REC, ANT) get stripped by vendor conversion (u-blox case)

**NAV parsing + PDOP (if applicable):**
- If the spec wants `pdop_per_epoch`, choose **option A (real NAV
  propagation, ~500 lines)** or option B/C documented earlier
- Sample PDOP every 30 s (PDOP varies slowly; 174 samples over an 87-min
  session is plenty)
- Use 10° elevation mask (RTKLIB / TBC / Leica industry standard)
- Implement Keplerian propagation for GPS / Galileo / QZSS / BeiDou (MEO),
  linear PZ-90 for GLONASS. Document BeiDou GEO and GLONASS RK4 as known
  approximations.

After all parsers built, build the **Stage 2 merge** in
`scripts/stage2_merge.py`:
- Run each parser's `parse()` function
- Merge L1F_* fields into one envelope
- Audit field counts per spec source-file mapping (per-source-audit block)
- Compute any cross-parser source fields
- Evaluate `pre_score_ingestion` flags that need multiple parsers
- Preserve any `handoff_crossdoc` candidates separately
- Write `outputs/02_source_fields.json`

**Closeout checklist for Step 6:**
- Total source fields produced = N (matches spec `_meta.audit_counts.source_fields`)
- Per-source audit clean (expected vs produced count per SRC_*)
- Cross-parser computations performed (list with values)
- Stage 2 flags raised (list with severity)
- `_handoff_crossdoc_candidates` listed (count + flag_ids)
- Per-parser `parser_meta` carried forward into envelope

STOP. Wait for OK.

### Step 7: Stage 3a — derived fields
Build `scripts/compute_derived.py`. For each L2D_* field in sheet 03:
- Identify dependencies (L1F_* and/or L2D_*)
- Topologically sort derivations (Tier 1: L1F-only, Tier 2: depends on Tier 1)
- For complex derivations (geometry, polygons, time-series stats), implement
  the algorithm faithfully
- For derivations the spec admits are uncomputable from available data,
  return `None` with a `_notes` entry explaining honestly

**Standard tuneables to surface in `stage3a_meta.tuneables`:**
- `ACQUISITION_NSAT_THRESHOLD = 8`, `ACQUISITION_STABILITY_SEC = 10`
  (acquisition_time computation)
- `BATTERY_MIN_ADEQUATE_PCT = 10.0` (from any device-log schema's x-on-low note)
- `TRUNCATION_TOLERANCE_SEC = 3.0` (tightened from 5.0 in v2 — real
  truncation shows 30+ s deltas)
- `DISTURBANCE_GAP_GT_5S_COUNT`, `_CYCLE_SLIPS_PER_HOUR`,
  `_CN0_STD_DBHZ_MEAN` (composite-flag thresholds)
- `SUPPORTED_RINEX_VERSIONS` (set)

**Expected encounter:** real-data conditions where spec-defined fields are
genuinely null. Examples:
- PDOP requires per-epoch ephemeris (handled via NAV-driven propagation per
  Section 7f)
- EXIF-GPS-dependent overlap when the camera has no GPS chip
- Per-epoch acquisition time when only aggregates are surfaced

For each null, decide between:
- **Honest null + note** — when no data exists
- **Approximation with documentation** — when aggregate data implies the value
- **Cross-source fallback** — when another source carries equivalent info

**Project the indicator-level consequences BEFORE writing the eval** —
if a null in L2D_X cascades to a "bottom band" in L3I_Y, that may fire a
spurious flag. Decide: fallback (preferred) or honest null + spec amendment.

**Closeout checklist for Step 7:**
- Total derived fields = N (matches sheet 03 row count)
- Per-kind counts match spec (scoring/composite_flag/handoff/external)
- Null derived fields counted; reason for each in `_notes`
- Cruise/window filters applied where the spec mandates them
- Cross-stage dependencies surfaced for the merge step
- Tuneables surfaced in `stage3a_meta.tuneables`

**Audit pattern for Step 7 (run BEFORE closing):**
1. Field-id alignment vs spec sheet 03 (zero missing, zero extra, zero kind mismatches)
2. JSON envelope serializes without `default=str` (datetime-leak detector)
3. Hand-recompute 2-3 key values (coverage_ratio, pre/post buffer, PDOP filter)
4. All `input_field_ids` resolve to real source or derived keys
5. Flag emission matches derived values (composite + handoff)
6. Edge cases: remove each source instance one at a time → graceful null degrade
7. Re-run determinism: byte-identical output (apart from timestamps)

STOP. Wait for OK.

### Step 8: Stage 3b — indicators with threshold bands and flags
Build `scripts/compute_indicators.py`. **Choose your evaluator strategy
upfront** (Section 3, Option A vs B). Drone and base both used Option B.

Whichever you pick, **score values and flag names ALWAYS come from the spec at
runtime**, never hardcoded.

Every indicator emits the trace block format from Section 3.

**Standard tuneables to surface in `stage3b_meta.tuneables`:**
- `MULTIPATH_STD_LOW_DBHZ`, `_HIGH_DBHZ` (multipath C/N0 variance boundaries)
- `SLIPS_PER_HOUR_LOW = 100.0` (tightened from 200 in v2 — industry
  "elevated cycle slip" threshold)
- `KP_HIGH_THRESHOLD = 5.0` (NOAA G1 minor storm convention)
- `BATTERY_MIN_ADEQUATE_PCT`
- All "unconfirmed" / "partial" mid-band scores (60-80) for null-input paths

**Closeout checklist for Step 8:**
- Total indicators evaluated = N (matches sheet 04 row count)
- Total threshold rows referenced = (matches sheet 05 if it exists)
- Internal-gate flags raised (canonical home in Stage 3c, not here)
- Hard-rule indicators correctly score 0 when triggered
- Any spec inconsistencies found? → decide: fix via spec patch with
  version bump OR document + workaround
- Tuneables surfaced in `stage3b_meta.tuneables`

STOP. Wait for OK.

### Step 9: Stage 3c — building-block rollups
Build `scripts/compute_blocks.py`. For each block in sheet 06:
- Get composition (indicator_id → weight) from sheet 07
- **Audit weight sum == 1.0 (spec self-consistency)** — surface in
  `weight_sum_audit` per block
- Compute weighted sum
- Apply block-internal gate per sheet 06 if defined
- Round score to 1 decimal

If sheet 06 defines a parallel deliverable (drone's CAL_CONF with
`weight_in_<apex>_ppk = 0`), emit it as `outputs/05b_*.json` separately.

**Closeout checklist for Step 9:**
- All blocks computed = M (matches sheet 06)
- Weight-sum audits all PASS (zero failures)
- Internal gates checked (which triggered, which didn't)
- Parallel deliverable surfaced separately
- Hand-math verification of one block

STOP. Wait for OK.

### Step 10: Stage 3d — apex score
Build `scripts/compute_<apex>.py`. Read blocks + spec sheet 08:
- Compute `<apex>_score = Σ weight_i × block_score_i` (weights from spec)
- Apply global gate if defined (e.g. drone's
  `image_capture_score == 0 → drone_score = 0`)
- **Audit apex weight sum == 1.0**
- Aggregate flags from Stage 2 (`_flags_raised_stage2`), Stage 3a
  (`flags_raised_stage3a`), Stage 3b (`flags_raised_stage3b`), Stage 3c
  (`flags_raised_stage3c`), and Stage 3d's own gate-triggered flag into a
  single `all_flags_aggregated` array
- Preserve Stage 2's `_handoff_crossdoc_candidates` separately

Each aggregated flag retains its `_origin_stage` tag. Use `_tag_if_missing()`
to backfill if upstream forgot.

**Contributions list ordering:** Use spec-formula order (not alphabetical)
so the decomposition reads left-to-right against the prose formula.

**Closeout checklist for Step 10:**
- Apex formula expression from `spec.<apex>.metadata`
- Global gate condition + action documented
- Flag aggregation: count by severity + by origin_stage
- Parallel deliverable score (if applicable) surfaced as a separate
  non-contributing entry
- `_handoff_crossdoc_candidates` carried through

**Audit pattern for Step 10 (run BEFORE closing):**
1. JSON envelope serializes without `default=str`
2. Every spec.`<apex>` key surfaced somewhere in the envelope
3. Apex math hand-recompute matches emitted
4. Apex weight-sum audit (= 1.0)
5. Flag aggregation total = Σ per-stage counts (no flag dropped/duplicated)
6. Every aggregated flag has `_origin_stage` + `flag_id`
7. Global-gate trip path end-to-end (force coverage=0 → COMPLETE=0 → apex=0
   → CRITICAL_FAILURE fires) — verify cascade with all flags present
8. Scores rounded to 1 decimal (rule 3)
9. Contributions list in spec-formula order
10. Re-run determinism (byte-identical apart from timestamps)

STOP. Wait for OK.

### Step 11: Wire orchestrator end-to-end and run fresh
- Clear `outputs/` (preserve `cache/` to skip API hits)
- Run `python3 scripts/run_pipeline.py paths.json` end-to-end
- Show the full output tree
- Verify all envelopes carry the correct `spec_version`
- Capture wall-time per stage (per-envelope `stage*_meta.wall_time_sec`)

STOP. Wait for OK.

### Step 12: Smoke-test harness + scenarios
Build `scripts/test_scenarios.py` per the harness pattern:

- Reads baseline `outputs/02_source_fields.json`
- For each scenario: applies source/derived/parser_meta overrides + flag
  injections; runs Stages 3a → 3d into `tests/scenarios/<name>/`
- **Writes the mutated `02_source_fields.json` per scenario** (NEW in v2 —
  self-contained directories)
- Reports side-by-side table: `<apex>_score` / block scores / flags

**Scenario design — TWO passes:**

**Pass 1 — spec-internal coverage (~15-17 scenarios):**
- `baseline` (control)
- Each gate trigger (internal + global)
- Each enum-band drop (e.g. SELF_CALIBRATED, MISMATCH)
- Each fallback path (e.g. API_UNAVAILABLE proxy)
- Each `threshold_band` flag
- Each composite flag and conditional handoff
- Perfect-survey ceiling check (implicit in baseline if 100 achievable)
- `all_flags_stress` — compose N+ flags without tripping global gate

**Pass 2 — spec-band completion (~3-5 scenarios):**
Find spec bands not yet hit by Pass 1 and add one scenario per band:
- cache-OK path differentiated from cache-miss
- single-vertical measurement band
- ad-hoc-point band

**Pass 2 real-world — sheet-driven (~5-10 scenarios):**
When the operator provides a CBMI Problems sheet (xlsx/csv):
1. Read the prose cells (description, recommendation, gap) — not just
   the coverage-status column
2. Extract concrete numerical examples ("1.800m vs 1.865m correct = 65mm",
   "14 minutes when 45 needed", "battery dipped to 3%", "Kp 6.2 forecast")
3. One mutator per quoted numerical condition
4. Each mutator docstring quotes the sheet verbatim

**Write a problem-coverage map** (`tests/scenarios/_pass2_problem_coverage.{csv,json}`):
Maps every problem from the sheet to:
- `cbmi_coverage_class` (FULLY COVERED / PARTIAL / NOT COVERED)
- `cbmi_stage` (Stage 1 / Stage 2 / LE / OUT OF SCOPE)
- `scenarios` (list of scenario names that exercise it)
- `verification_status` (VERIFIED / DEFERRED_HANDOFF / DEFERRED_SPEC_GAP / OUT_OF_SCOPE)

**Closeout checklist for Step 12:**
- Total scenarios run = N (target 25+ once Pass 2 real-world is in)
- Crashes = 0 (any crash is a real bug to investigate)
- Score directional changes match expectations (per-scenario)
- Deviations from expectation? — investigate honestly:
  - If pipeline didn't fire an expected flag → may be a real gap in spec
    coverage (note for spec amendments)
  - If pipeline scored higher/lower than expected → trace the cascade
- Smoke test exposed any data-integrity issues?
- Hand-math one non-trivial scenario (e.g. `all_flags_stress`) to confirm
  apex math reproduces
- Problem-coverage map: VERIFIED count + DEFERRED + OUT_OF_SCOPE = total
  spec problems

STOP. Final closeout.

---

## 7. Cross-stage patterns to watch for (lessons from drone + base builds)

### 7a. Hardware override (SRC_UI_02 pattern)
If a source's standardised metadata fields are commonly stripped (drone's
RINEX header REC/ANT often blank after u-blox→RINEX conversion; same for
base), introduce an **optional operator override file**:
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
at merge because both sources must be present. Base's truncation check
(OPLOG `session_end_utc` vs RINEX `obs_end_utc`) fires at Stage 3a as a
composite-flag derivation. Add similar checks for any field the spec defines
but only verifies cross-source.

### 7c. Fallback chains for missing modern features
Drone's BIN-CAM-position fallback for missing EXIF GPS rescued 6 derived
fields and 3 indicators. Base's PDOP-from-NAV path (Section 7f below) is
analogous. Identify which fields are "uncomputable from primary source
alone" and document the fallback chain.

### 7d. Spec-driven score floor probes
The "perfect_survey" scenario rarely reaches 100 on the first try — it caps
where structural source fields can't be overridden through L1F. Note the
ceiling as feedback to spec authors (may indicate redundant indicators or
miscalibrated weights). **For base, the gold-standard placeholder hit 100
exactly — surface this as confirmation of consistent spec math.**

### 7e. Cross-bundle integrity
If multiple provenance bundles exist (drone, base station, GCP), eventually
the apex score in each may need a **cross-bundle flag** (e.g. base RINEX day
≠ drone BIN day → both subsystems should fire). Track these as candidate
spec amendments, not silent passes. **Preserve them as
`_handoff_crossdoc_candidates` in the Stage 2 merge envelope and forward to
the apex envelope.**

### 7f. NAV-driven PDOP computation (NEW in v2)
If the spec wants `pdop_per_epoch` and you have a NAV file:

**Option A — full broadcast ephemeris computation** (~500 lines new code or
new dep). Recommended for production-grade PDOP.

Implementation:
1. `scripts/parsers/parse_nav.py` — RINEX 3.x NAV parser (Keplerian for
   G/E/J/C, state-vector for R/S)
2. `scripts/parsers/gnss_orbits.py` — propagators:
   - `propagate_keplerian()` (ICD-GPS-200 Algorithm 30) for GPS / Galileo /
     QZSS / BeiDou-MEO
   - `propagate_glonass()` linear PZ-90 (P₀ + V·dt + ½A·dt²) — known
     approximation vs RK4
   - `compute_pdop()` from geometry matrix inversion (numpy.linalg.inv on
     the 4×4 normal equations)
3. **Elevation mask = 10°** (industry standard; was 5° in v1)
4. PDOP sampling cadence = 30 s (PDOP varies slowly; 174 samples over an
   87-min session = sufficient)
5. Time-frame conversion: GPS/Galileo/QZSS toc in GPS time; BeiDou toc =
   GPS-14 s; GLONASS toc = UTC ≈ GPS-18 s (leap seconds)
6. Known approximations to document:
   - BeiDou GEO sats use MEO Kepler (skipping rotation step) — PDOP impact
     <0.001 verified
   - GLONASS quadratic vs RK4 — <0.0001 PDOP impact at ±30 min validity
   - Galileo sats without NAV records silently excluded

### 7g. georinex hybrid (NEW in v2)
For RINEX parsing robustness vs vendor edge cases:

| Component | Use | Why |
|---|---|---|
| Header | `georinex.rinexheader()` (1 ms) | Battle-tested for vendor quirks, multi-line SYS/#/OBS TYPES, edge offsets |
| Body | **manual streaming** (~2 s for 144 MB) | `georinex.load()` is ~600× slower; xarray construction is CPU-bound |

The hybrid keeps the speed of the manual parser AND gets library-grade
header parsing for free. Pattern:

```python
import georinex as gr

def _parse_header(path):
    raw = gr.rinexheader(path)
    # Adapt raw dict → internal header dict shape
    # (60-char strings with col-slice extraction for ANT/REC/PGM)
    ...
```

If RINEX 2.x body or Hatanaka compression appears in production uploads,
add a targeted `gr.load()` fallback path (accepts the slowdown for those
files only).

### 7h. Placeholder detection at Stage 1 (NEW in v2)
When you write operator-pending placeholder JSON files during the build
(e.g. `sample_data/operation_log/operation_log.json` for an absent OPLOG
instance), include a top-level marker:

```json
{
  "_note": "PLACEHOLDER instance ... operator should overwrite with real values",
  "_status": "PLACEHOLDER",
  "session_completed_normally": true,
  ...
}
```

Stage 1 inventory scans all input JSON for `_status: PLACEHOLDER` and emits
`PLACEHOLDER_INPUTS_DETECTED` warning listing each file. This catches the
case where the operator runs a real survey without replacing the test data.

### 7i. Industry-standard threshold rubric (NEW in v2)
When the spec writes a band qualitatively ("low / moderate / high"), use
this rubric to pick the numeric threshold:

| Tier | Definition | Action |
|---|---|---|
| **CLEARLY WRONG** | Industry / spec convention is unambiguous and you picked something off | Change it (e.g. PDOP mask 5° → 10° for survey-grade GNSS) |
| **DEFENSIBLE** | Multiple defensible choices; literature supports a range | Pick one, document rationale in `_notes`, surface in `tuneables` |
| **ALREADY RIGHT** | Matches industry / spec convention exactly | Keep, reference the convention in a code comment |

Common GNSS thresholds and their "right" values:
- **PDOP elevation mask**: 10° (RTKLIB / TBC / Leica standard); 5° is too lenient
- **Kp high threshold**: 5.0 (NOAA SWPC G1 minor storm convention)
- **Battery min adequate**: 10% (from any device-log schema's x-on-low note)
- **Truncation tolerance**: 2-3 s (real truncation shows 30+ s; clock skew <2 s)
- **Slips per hour low**: 100 (industry "elevated" threshold)
- **Acquisition**: 8 sats / 10 s stability (4+ minimum for fix, 8+ for high-quality)
- **Multipath C/N0 std boundaries**: spec-dependent on whether proxy includes
  elevation variance; document the proxy method

Run this rubric explicitly when the operator asks "what other decisions
have you made silently?" Categorise every numeric threshold by these three
tiers.

### 7j. Self-contained scenario directories (NEW in v2)
Each `tests/scenarios/<name>/` directory MUST be self-contained — it
includes the mutated input (`02_source_fields.json`) plus all four
downstream envelopes (`03_derived.json` → `06_apex.json`). Anyone auditing
a scenario can answer "what was the input?" without reading the mutator
code.

Pattern:

```python
def _run_one(name, mutator, ...):
    stage2 = copy.deepcopy(baseline_stage2)
    mutator(stage2)
    s3a = compute_derived.run(...)
    s3b = compute_indicators.run(...)
    s3c = compute_blocks.run(...)
    s3d = compute_base_score.run(...)

    out_dir = SCENARIOS_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "02_source_fields.json").write_text(...)  # NEW in v2
    (out_dir / "03_derived.json").write_text(...)
    (out_dir / "04_indicators.json").write_text(...)
    (out_dir / "05_blocks.json").write_text(...)
    (out_dir / "06_apex.json").write_text(...)
```

---

## 8. The placeholder lifecycle (NEW in v2)

When sample data is missing operator-entered fields (OPLOG, FORM, etc.),
the disciplined pattern is:

### 8.1 Creating placeholders
At the appropriate step (e.g. Step 4 for OPLOG, Step 5 for FORM):
1. Ask the operator (via `AskUserQuestion`) whether to write a placeholder
2. If yes, write a JSON file with:
   - Top-level `_status: PLACEHOLDER`
   - Top-level `_note` explaining the values are mock data
   - Real-looking values keyed to the actual RINEX session timing
   - Healthy-session patterns (so the baseline scenario hits the top band
     of every indicator)

Example for OPLOG:
```json
{
  "_note": "PLACEHOLDER instance. Mirrors RINEX session timing with healthy semantics. Operator should overwrite with the actual device-exported log.",
  "_status": "PLACEHOLDER",
  "session_completed_normally": true,
  "unexpected_shutdown_count": 0,
  "battery_start_pct": 92,
  "battery_end_pct": 68,
  "battery_min_pct": 68,
  "session_end_utc": "2026-05-19T12:28:20Z",
  "raw_log_download_confirmed": true
}
```

### 8.2 Stage 1 detection
`stage1_inventory.py` walks all input JSON looking for `_status:
PLACEHOLDER` and emits a warning at Stage 1 (Section 7h above).

### 8.3 Operator handoff
When closing the build:
- List every placeholder file in the final closeout
- Document the values the operator must replace
- The placeholder warning at Stage 1 stays visible until the operator
  overwrites the file

---

## 9. Threshold rubric for spec-prose-vague decisions (NEW in v2)

When the operator asks "what other decisions have you made silently?",
present a HIGH / MEDIUM / LOW impact + PLACEHOLDER table:

### 9.1 HIGH-IMPACT (numeric thresholds affecting scores)
For each threshold:
| Where | What I picked | "Right" by industry/spec | Verdict | Score impact |
|---|---|---|---|---|

Use Section 7i's industry-standard values for the "Right" column. Verdict:
- ⚠️ **CLEARLY WRONG** → change
- ✅ **DEFENSIBLE** → keep, document
- ✅ **ALREADY RIGHT** → keep

### 9.2 MEDIUM-IMPACT (algorithm / interpretation)
Things like:
- Manual vs library parsing
- Welford vs two-pass for stats
- Quadratic vs RK4 GLONASS propagation
- BeiDou GEO Kepler approximation
- Multipath proxy without elevation binning
- "Reused" interpretation for handoff flags

Most of these are DEFENSIBLE; document with rationale.

### 9.3 LOW-IMPACT (convention / cosmetic)
- Timestamp format (6-digit µs ISO)
- Output filename conventions
- Sort_keys for determinism
- Per-source folder layout in paths.json

These are mostly fine; list briefly for completeness.

### 9.4 PLACEHOLDER VALUES (operator must replace)
- Hardware override placeholders (marker_name, antenna_type, receiver_type, firmware_version)
- OPLOG placeholder (session completion + battery + timestamps)
- FORM placeholder (antenna setup + flight times)

These won't be "fixed" by the build; they're meant to be operator-replaced.
Document each placeholder file path and what it drives downstream.

---

## 10. Closeout checklist template (use after every step)

```
## Step N closeout — <STAGE NAME>

### Coverage: K / N of <spec sheet> rows
[per-field/per-block table with status, value, notes]

### Tuneables surfaced (where applicable):
[list of named constants in stage*_meta.tuneables]

### Flags raised: <count> at this stage
[per-flag table]

### Implementation choices on record:
[numbered list — e.g. "Chose Option B eval over Option A because…"]

### Open items unchanged from earlier:
[carryforward list]

### Spec amendment candidates surfaced:
[list — typos, qualitative thresholds, missing fields]

### Net verdict:
[blocking / not blocking / requires decision before next step]
```

For steps 7, 10, and 12 — also include the **audit table** (6-10 independent
checks) before closing.

---

## 11. Open-items log (maintained throughout the build)

Maintain a running log with five sections:
- **Fully closed** — what shipped + verified
- **Deferred by design** — happens in a later named step
- **Quality-of-life deferrals** — non-blocking, may revisit (datetime
  normalisation, validity-check upgrades, optional caches, etc.)
- **Spec amendment candidates** — typos, inconsistencies, missing fields
  surfaced during build (each one gets a version-bump proposal)
- **Placeholder files awaiting operator data** (NEW in v2) — hardware
  override, OPLOG, FORM, etc.

Drone build's final open-items log had: 6 quality-of-life deferrals (none
blocking), 1 spec patch landed (v1.1.2), 0 hard failures, 0 unresolved bugs.

Base build's final open-items log had: 4 threshold-tightening fixes
applied (PDOP mask, truncation tolerance, slips/hr, ad_hoc_point band),
1 spec amendment candidate (height_mode field for GCP analog), 3
handoff_crossdoc deferrals (need rover bundle), 3 placeholder files
(hardware/oplog/form).

---

## 12. Start now — Step 0

Read `<SPEC_BUNDLE_FOLDER>/<SPEC_JSON_FILENAME>` and answer the four
comprehension questions above. Do not do anything else yet.

After Step 0, wait for the operator's "OK" before Step 1.

Throughout the build, the operator may share real-world scenario sheets,
gap analyses, or known data quirks. **Treat these as inputs to the smoke
test scenarios in Step 12 (Pass 2)** — they should shape the test suite,
not the production pipeline code.

---

## Appendix A — File-by-file reference structure (base station example)

```
BaseStation_CodeBase/
├── BUILD_PROMPT_TEMPLATE.md           # v1 (drone-derived)
├── BUILD_PROMPT_TEMPLATE_v2.md        # THIS FILE
├── base_station_confidence_score/     # frozen spec bundle
│   ├── base_station_confidence_score.json
│   ├── *.csv (per-sheet)
│   └── *.xlsx
├── sample_data/
│   ├── base_rinex/                    # real RINEX OBS + NAV
│   ├── operator_log/
│   │   ├── operation_log_schema.json
│   │   └── operation_log.json         # PLACEHOLDER (operator overwrites)
│   ├── user_input/
│   │   ├── user_input_schema.json
│   │   └── user_input.json            # PLACEHOLDER (operator overwrites)
│   └── hardware.json                  # PLACEHOLDER (Hardware Override)
├── paths.json
├── scripts/
│   ├── run_pipeline.py                # orchestrator
│   ├── stage1_inventory.py
│   ├── stage2_merge.py
│   ├── compute_derived.py             # Stage 3a — 24 L2D fields
│   ├── compute_indicators.py          # Stage 3b — 11 L3I indicators
│   ├── compute_blocks.py              # Stage 3c — 3 BB blocks
│   ├── compute_base_score.py          # Stage 3d — apex
│   ├── test_scenarios.py              # Step 12 harness — 26 scenarios
│   └── parsers/
│       ├── parse_rinex.py             # hybrid (georinex header + manual body)
│       ├── parse_nav.py
│       ├── gnss_orbits.py
│       ├── parse_oplog.py
│       └── parse_user_input.py
├── outputs/                           # production envelopes
│   ├── 01_inventory.json              # 6 kB
│   ├── 02_source_fields.json          # 247 kB (36 L1F)
│   ├── 03_derived_fields.json         # 12 kB (24 L2D)
│   ├── 04_indicators.json             # 9 kB (11 L3I)
│   ├── 05_building_blocks.json        # 7 kB (3 BB)
│   └── 06_base_station_score.json     # 5 kB (apex)
├── cache/
│   └── noaa_swpc/                     # for L2D kp_index
└── tests/
    └── scenarios/                     # 26 self-contained scenario dirs
        ├── baseline/
        │   ├── 02_source_fields.json
        │   ├── 03_derived.json
        │   ├── 04_indicators.json
        │   ├── 05_blocks.json
        │   └── 06_apex.json
        ├── coverage_gate_trip/
        ├── antenna_height_missing/
        ├── ... 23 more scenarios ...
        ├── _summary.json
        ├── _pass2_problem_coverage.csv
        └── _pass2_problem_coverage.json
```

---

## Appendix B — Final closeout template

After Step 12, write this final summary:

```markdown
## Final Build Closeout — <SUBSYSTEM_NAME>

### Pipeline state
| Artifact | Size | Content |
|---|---|---|
| outputs/01_inventory.json | x kB | inventory |
| ... | ... | ... |

### Scoring math
- Baseline (gold-standard placeholder) apex: <SCORE>
- Coverage-gate-trip apex: 0.0 (global gate)
- Stress-test apex: <SCORE>
- All flag wiring per sheet 09 raised_at_stage column verified

### Engineering decisions on record
- All tuneables surfaced in stage3a_meta.tuneables / stage3b_meta.tuneables
- 4 threshold changes applied per industry-standard rubric (Section 7i):
  - PDOP elevation mask: 10°
  - Truncation tolerance: 3 s
  - Slips per hour low: 100
  - ad_hoc_point band: 50
- Placeholder detection wired at Stage 1

### Spec amendment candidates
- [list]

### Open items
- Placeholder files awaiting operator data:
  - sample_data/hardware.json
  - sample_data/<sourceA>/<instance>.json
  - sample_data/<sourceB>/<instance>.json
- Cross-document flags deferred to pre_processing stage (need other bundles):
  - [list FLG_*]

### What you (operator) should do next
1. Replace placeholder values in:
   - sample_data/hardware.json
   - <other placeholder files>
2. Populate cache/<api>/<date>.json for offline external lookups
3. Re-run the pipeline:
   `python3 scripts/run_pipeline.py paths.json`
4. Re-run the smoke tests:
   `python3 scripts/test_scenarios.py`
5. Wire into the cross-bundle stage when other subsystems land
```

---

End of template v2.
