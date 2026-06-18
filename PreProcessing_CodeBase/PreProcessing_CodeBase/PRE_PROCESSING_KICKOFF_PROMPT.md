# Pre-Processing Provenance Pipeline — Session Kickoff Prompt

> Paste the contents of this file (or the body below the `---`) as the FIRST
> message to a new Claude Code session opened in your new
> `PreProcessing_CodeBase/` folder. Attach the companion file
> **`BUILD_PROMPT_TEMPLATE_v2.md`** to the same session — that is the
> comprehensive 12-step build guide Claude will follow step by step.
>
> Copy BOTH files into the new folder before you start. From inside your new
> `PreProcessing_CodeBase/` (a sibling of `GCP_CodeBase/` and
> `CheckPoint_CodeBase/` under `Loop_CodeBase/`):
> ```
> cp ../GCP_CodeBase/BUILD_PROMPT_TEMPLATE_v2.md ./
> cp ../PRE_PROCESSING_KICKOFF_PROMPT.md ./
> ```

---

You are building the **Pre-Processing** provenance scoring pipeline. This is the
**fifth** subsystem in the Capture quality-scoring family; four are already
shipped end-to-end and serve as reference:

| Subsystem | Status | Location | Use as |
|---|---|---|---|
| Drone PPK | shipped | `../Drone_CodeBase/` | reference |
| Base Station PPK | shipped end-to-end | `../BaseStation_CodeBase/` | reference |
| GCP PPK | shipped end-to-end (v2.0.0) | `../GCP_CodeBase/` | reference (cross-point aggregation + problem map) |
| Check Point (RTK) | shipped end-to-end (v1.0.0) | `../CheckPoint_CodeBase/` | **PRIMARY scaffolding lift — cleanest + self-validating harness** |
| **Pre-Processing** | **building now** | `<this folder>` | — |

The disciplined build process is documented in the attached
**`BUILD_PROMPT_TEMPLATE_v2.md`**. That file is the process source of truth —
read it before doing anything else. The **spec bundle** (which I will provide)
is the data source of truth.

## ⚠️ Why this subsystem is fundamentally different — READ THIS FIRST

The four prior subsystems are all **per-instance hardware capture-confidence
scores**: each scores how trustworthy one device's data-capture was (a drone
flight, a base-station occupation, a GCP occupation, an RTK check-point
capture). They are deliberately **runtime-independent** of each other.

**Pre-processing is almost certainly NOT that.** It is the layer that sits
*above* the capture scores and evaluates **survey-design adequacy and
cross-document consistency** — the very class of problems the other four
explicitly **deferred to `pre_processing_score`**. From the Check Point build
alone, these were dispositioned `OWNED_BY_PRE_PROCESSING`:

- **Reference Frame / Datum / CRS / Epoch mismatch** (CATASTROPHIC, COMMON)
- **Geoid / Vertical Datum mismatch** (CATASTROPHIC, COMMON)
- **Site Calibration / Localization applied** (HIGH)
- **Check points not spatially independent from GCPs** (HIGH)
- **Too few check points for statistical validity** (HIGH)
- **Check points clustered geographically** (HIGH)
- **No check points designated** (CATASTROPHIC — split with a Stage-1 null state)

GCP and the others deferred their own survey-design problems here too. So
pre-processing is the **cross-document / survey-level** subsystem.

**The meta-lesson from the prior four builds, stated as a hard rule:** every
subsystem diverged from its sibling at the **data layer**, and the divergence
was bigger than it first looked. Check Point looked like a GCP twin but was
**RTK, not PPK** — which invalidated the entire parser layer (no RINEX, no NAV,
no PDOP computation) even though the 5-stage / 3-block scaffolding was
identical. **Pre-processing will very likely diverge the MOST of all five.**
Treat *every* structural assumption below as a **hypothesis to verify against
the spec at Step 0**, never as something to bake in.

## What likely carries forward (anticipate — then verify)

1. **The 5-stage pipeline shape** — Stage 1 inventory → Stage 2 parse/merge →
   Stage 3a derived → 3b indicators → 3c building-block rollups → 3d apex score.
2. **The envelope contract + determinism** — every artifact is
   `{spec_version, config_used, generated_at, stage, data}`; **no timestamps in
   the data block**; `sort_keys`; scores to 1 decimal, ratios to 4.
3. **Option-B per-indicator eval functions** — the spec almost certainly stores
   threshold bands as prose only (every prior subsystem did).
4. **The apex pattern** — `apex = Σ weight_i · block_score_i`, weights read from
   the spec at runtime, never hardcoded; a global gate; a null-handling path.
5. **The self-validating smoke harness** — the Check Point harness's `EXPECT`
   table (per-scenario expected score ± tolerance + exact flag set, exits
   non-zero on drift) is the current best pattern. Lift it from the start.
6. **The two-pass scenario design + problem-coverage map** — Pass 1
   spec-internal coverage, Pass 2 from your Pre-Processing Problems sheet, plus
   the 30-ish-row OWNED / SPLIT / OUT-OF-SCOPE map.
7. **Placeholder lifecycle** — operator-pending inputs carry
   `_status: PLACEHOLDER`; Stage 1 emits `PLACEHOLDER_INPUTS_DETECTED`.

## What likely does NOT carry forward (the divergences to expect)

These are the things most likely to be **different** — confirm each at Step 0:

- **No RINEX / RTK device parsing.** Pre-processing probably does not read raw
  GNSS at all. Its "source files" are more likely: project **CRS / datum /
  geoid / epoch declarations**, the **GCP point set**, the **check-point set**,
  the **base-station coordinate**, **site geometry / DTM / reconstruction
  extent**, and possibly the **upstream subsystem score outputs themselves**.
- **Probably NOT per-occupation / multi-point-loop scored.** The prior four
  loop over N occupations and aggregate `mean − k·(100 − min)`. Pre-processing
  is survey-level — there may be **one survey**, not N points, so the
  cross-point aggregator (GCP/Check Point Stage 3c) may **not apply at all**, or
  applies to *sets* (GCP set vs CP set) rather than occupations.
- **Cross-document at runtime.** The prior four were deliberately
  runtime-independent. Pre-processing may legitimately **consume other bundles'
  outputs** (base coordinate, the GCP set, the CP set) — which inverts the
  `_handoff_crossdoc_candidates` pattern: here the cross-doc inputs are
  **first-class**, not deferred. Confirm what it reads and whether that breaks
  the independence contract by design.
- **Geometry / spatial math is likely central** — point distribution,
  clustering, independence radius, convex-hull coverage of the reconstruction
  extent. Expect real computational geometry in Stage 3a (the way GCP had
  NAV-driven PDOP), not just band lookups.
- **The global gate / null handling will be different** — likely gated on "no
  CRS declared" / "no reconstruction frame" / "zero control points," not on a
  per-point coverage failure.

## Project context — fill in any `<<<…>>>` you can; ask for what you can't

```
PROJECT_ROOT         = <<<absolute path to this PreProcessing_CodeBase folder>>>
SUBSYSTEM_NAME       = pre_processing                          ← confirm from spec
SPEC_BUNDLE_FOLDER   = <<<e.g. pre_processing_confidence_score>>>   ← I'll confirm
SPEC_JSON_FILENAME   = <<<e.g. pre_processing_confidence_score.json>>> ← I'll confirm
APEX_SCORE_NAME      = <<<e.g. pre_processing_score>>>         ← read from spec
SCAFFOLD_REFERENCE   = ../CheckPoint_CodeBase/scripts/         ← primary lift (framework + harness)
SECONDARY_REFERENCE  = ../GCP_CodeBase/scripts/                ← cross-point aggregation + problem map, IF it applies
```

## Folder layout

**You provide these INPUTS before Step 0** (mirror the sibling layout; exact
shapes confirmed at Step 0 from spec sheet 01):

```
PreProcessing_CodeBase/
├── BUILD_PROMPT_TEMPLATE_v2.md             ← copy from GCP_CodeBase
├── PRE_PROCESSING_KICKOFF_PROMPT.md        ← this file
├── <SPEC_BUNDLE_FOLDER>/                    ← the frozen pre-processing bundle:
│   ├── pre_processing_confidence_score.json   (master — single source of truth)
│   ├── 01_source_files.csv
│   ├── 02_source_fields.csv
│   ├── 03_derived_fields.csv
│   ├── 04_indicators.csv
│   ├── 05_building_blocks.csv
│   ├── 06_<apex>_score.csv  +  06b_..._meta.csv
│   ├── 07_flags.csv
│   ├── 08_problem_coverage_map.csv
│   └── *.xlsx / *.html (optional companions)
└── sample_data/                            ← real (or synthesized) survey inputs;
    └── <shape per spec sheet 01>              the SHAPE is a Step-0 question —
                                               likely project config + point sets +
                                               CRS/geoid declarations, NOT RINEX
```

If real sample data does not exist yet, say so — we will **synthesize a
spec-faithful gold-standard set** (as we did for Check Point), marked
`_status: PLACEHOLDER`, that scores the apex near 100 so the baseline scenario
is a clean control.

**The build GENERATES these** (output of Steps 1–12 — do NOT create at Step 0):

```
├── paths.json                              ← Step 1
├── scripts/  (run_pipeline.py, stage1_inventory.py, stage2_merge.py,
│              stage3a_derived.py, stage3b_indicators.py, stage3c_blocks.py,
│              stage3d_score.py, common.py, parsers/, test_scenarios.py)
├── outputs/  (01_inventory.json … 06_<apex>_score.json)
├── tests/scenarios/  (one self-contained dir per scenario + _summary.json
│                      + _pass2_problem_coverage.{csv,json})
└── cache/  (only if an external API like NOAA/geoid-model is consumed)
```

## Binding process constraints (identical to the prior four builds)

1. **STOP after every step and wait for my explicit "OK"** before the next.
   Step 12 (smoke-test harness) is the final step. Do not chain steps.
2. **Never hardcode weights, thresholds, or formulas** — read them from the
   spec bundle at runtime. Sanctioned exceptions: Option-B per-indicator eval
   functions for prose-only bands, and engineering tuneables declared as named
   constants in `*_meta.tuneables`.
3. **Run an explicit 6–10 check audit on the high-impact steps (7, 10, 12)**
   and report pass/fail before claiming done.
4. **Determinism:** NO timestamps inside the data block (only the envelope's
   `generated_at`); for the test harness, omit `generated_at` entirely so
   artifacts are byte-stable across runs.
5. **Lift scaffolding from `../CheckPoint_CodeBase/scripts/`, but verify each
   file against the pre-processing spec before trusting it.** Because this
   subsystem diverges most at the data layer, expect parsers / derived /
   indicators to be largely new — prove each lifted file still applies; don't
   assume it.
6. **Bake in the Check Point improvement:** the smoke harness must
   **self-validate** (per-scenario `EXPECT` of score + exact flag set, exit
   non-zero on mismatch) from the first version — not be report-only.

## What I'll share during the build

- **The pre-processing spec bundle** — the frozen JSON + per-sheet CSVs. I'll
  drop it into `<SPEC_BUNDLE_FOLDER>/` and confirm the exact folder/filename at
  Step 0.
- **The Pre-Processing Problems sheet** (`xlsx`/`csv`) — when you reach Step 12,
  I'll attach it. It seeds the **Pass 2** real-world scenarios with concrete
  numerical examples drawn from the prose cells, and each problem is mapped
  OWNED / SPLIT / OUT-OF-SCOPE exactly as the prior four problem maps were.
- **Sample data** — real if I have it; otherwise OK me to synthesize a
  gold-standard placeholder set.
- **Decisions on spec-vague thresholds** — when you ask (e.g. independence
  radius, minimum control count, clustering metric, CRS-match tolerance), I'll
  give a direction or tell you to default to the industry-standard rubric
  (Section 7i / 9 of the template).

## Start now — Step 0 (comprehension only — STOP at the end)

Per Section 12 of `BUILD_PROMPT_TEMPLATE_v2.md`:

1. **List the contents** of this folder and confirm the spec bundle path +
   filename.
2. **Read** `<SPEC_BUNDLE_FOLDER>/<SPEC_JSON_FILENAME>` end to end.
3. **Answer the comprehension questions** (confirm your bundle's sheet
   numbering — in the prior builds these were sheets 01 / 05 / 07 / 08):
   - The N **source files** (sheet 01) — `file_id` and `file_name`. **What ARE
     they?** (config? point sets? upstream bundle outputs? a DTM?)
   - The M **building blocks** (sheet 05/06) with their **weights in the apex**
     and the indicators within each.
   - All **flag** names (sheet 07) grouped by `raised_at_stage`, with severity.
   - The **apex score formula** (sheet 06/08) — blocks, weights, global-gate
     condition, null-handling.
   - The **problem-coverage map** (sheet 08) — how many problems, and which look
     OWNED here vs deferred elsewhere.
4. **Produce a scaffolding-diff report** — the most valuable part. Be explicit:
   - **Is it survey-level or per-occupation?** Does the GCP/Check Point
     per-point loop + `mean − k·(100 − min)` cross-point aggregator apply, or is
     Stage 3c something else entirely?
   - **What are the source files really** — and does scoring **read other
     subsystems' outputs at runtime** (cross-document), breaking the
     runtime-independence contract the prior four held?
   - Which Check Point/GCP scripts port **as-is** (almost certainly `common.py`,
     the orchestrator skeleton, the envelope/determinism contract, the harness
     framework), which need **edits** (Stage 1 inventory classification, Stage 2
     merge), which are **entirely new** (the parsers, derived geometry,
     indicators)?
   - New indicators/flags with no sibling analogue? (Expect CRS/datum/geoid
     consistency, spatial independence, count sufficiency, distribution.)
   - Is there real **computational geometry** in Stage 3a (distribution,
     clustering, hull coverage, independence radius)?
   - Threshold bands prose-only (**Option B**) or machine-evaluatable
     (Option A)?
   - What does the **global gate / null state** trigger on?
5. **STOP.** Wait for my OK before Step 1.

Do **not** create folders, write code, write `paths.json`, or run anything yet
— Step 0 is pure comprehension. The build begins only after I review your
comprehension + scaffolding-diff report and say OK.

---

## Reference paths — lift scaffolding from Check Point first

Pre-processing scaffolding to port (verify each against the pre-processing
spec — the data-layer files will be the heaviest rewrites of any build so far):

| Reference file (CheckPoint_CodeBase/scripts/) | What it does | Likely port effort |
|---|---|---|
| `common.py` | envelope + determinism helpers | **as-is → very low** |
| `run_pipeline.py` | orchestrator (takes `paths.json` arg), per-stage wall time | rename outputs → low |
| `stage1_inventory.py` | input discovery + placeholder detection | **rework** classification to the new source set → medium |
| `parsers/parse_rtk_export.py` | RTK device export reader | **drop / replace** — new source types → new |
| `parsers/parse_oplog.py` | session log | likely **drop** → n/a |
| `parsers/parse_form.py` | operator form | adapt to a project-config parser → medium |
| `parsers/parse_*` (NEW) | CRS/datum/geoid config, GCP set, CP set, base coord, site geometry | **new** → high |
| `stage2_merge.py` | per-instance source-field assembly | rework to survey-level assembly → medium-high |
| `stage3a_derived.py` | per-point derived fields | **rewrite** — spatial geometry, frame-consistency derivations → high |
| `stage3b_indicators.py` | Option-B per-indicator eval + trace block | framework ports; all eval fns new → medium-high |
| `stage3c_blocks.py` | block rollups (+ cross-point aggregation) | framework ports; aggregation likely **replaced** (survey-level) → medium |
| `stage3d_score.py` | apex weighted sum + global gate + flag aggregation | framework ports; gate/null per spec → medium |
| `test_scenarios.py` | smoke harness w/ **self-validating EXPECT** + Pass-1/Pass-2 + problem map | lift framework + EXPECT pattern; scenarios new → medium |

Sibling scenario directories to study: `../CheckPoint_CodeBase/tests/scenarios/`
(52 scenarios, self-validating) and `../GCP_CodeBase/tests/scenarios/`.

Lift whatever's reusable. For each lifted file, **prove it still applies to the
pre-processing spec — don't blindly copy.** This subsystem is the cross-document
survey-design layer; its fundamental nature differs more from the four capture
scores than they differ from each other.

End of kickoff prompt.
