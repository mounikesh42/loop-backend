# Check Point PPK Pipeline — Session Kickoff Prompt

> Paste the contents of this file (or the body below the `---`) as the FIRST
> message to a new Claude Code session opened in your new
> `CheckPoint_CodeBase/` folder. Attach the companion file
> **`BUILD_PROMPT_TEMPLATE_v2.md`** to the same session — that is the
> comprehensive 12-step build guide Claude will follow step by step.
>
> Copy BOTH files into the new folder before you start. From inside your new
> `CheckPoint_CodeBase/` (a sibling of `GCP_CodeBase/` under `Loop_CodeBase/`):
> ```
> cp ../GCP_CodeBase/BUILD_PROMPT_TEMPLATE_v2.md ./
> cp ../CHECKPOINT_KICKOFF_PROMPT.md ./
> ```

---

You are building the **Check Point PPK** (Check Point, Post-Processed
Kinematic) provenance scoring pipeline. This is the **fourth** subsystem in
the Capture quality-scoring family; three are already shipped and serve as
reference:

| Subsystem | Status | Location | Use as |
|---|---|---|---|
| Drone PPK | shipped | `../Drone_CodeBase/` | reference |
| Base Station PPK | shipped end-to-end | `../BaseStation_CodeBase/` | reference |
| GCP PPK | shipped end-to-end (v2.0.0) | `../GCP_CodeBase/` | **PRIMARY lift source — closest twin** |
| **Check Point PPK** | **building now** | `<this folder>` | — |

The disciplined build process is documented in the attached
**`BUILD_PROMPT_TEMPLATE_v2.md`**. That file is the process source of truth —
read it before doing anything else. The **spec bundle** (below) is the data
source of truth.

## Why GCP is the primary reference

A check point is surveyed with the **same hardware and the same GNSS PPK
occupation** as a ground control point — the difference is its *role*: a
check point is **withheld from the bundle adjustment and used to measure the
accuracy** of the reconstruction, whereas a GCP constrains it. So the
**occupation-quality machinery is almost certainly a near-clone of GCP**:
multi-occupation points, device-type branching (CB_X / AeroPoint / DGPS /
OTHER), per-point indicators, cross-point aggregation, the same 5-stage
pipeline. GCP's own code already carries `device_role = CHECK_POINT` as a
first-class value, and GCP's problem map explicitly deferred problems #1 ("No
Check Points") and #20 ("Too Many Check Points, Few GCPs") to *this* future
subsystem.

**Treat that as a hypothesis to verify against the spec, not an assumption to
bake in.** Likely real differences to look for:
- Gating on `device_role == CHECK_POINT` instead of `GCP`.
- Check-point-specific problems: spatial independence from the GCP set,
  sufficient count, distribution across the site, "not used in adjustment."
  Some of these need cross-doc inputs (the GCP set, site area / DTM) and may
  be deferred to `pre_processing_score` or the Learning Engine — exactly as
  GCP deferred them.
- Possibly different apex block weights and a different indicator count.

## Project context — fill in any `<<<…>>>` you can; ask for what you can't

```
PROJECT_ROOT         = <<<absolute path to this CheckPoint_CodeBase folder>>>
SUBSYSTEM_NAME       = check_point_ppk                          ← confirm
SPEC_BUNDLE_FOLDER   = <<<e.g. check_point_confidence_score>>>  ← I'll confirm
SPEC_JSON_FILENAME   = <<<e.g. check_point_confidence_score.json>>> ← I'll confirm
APEX_SCORE_NAME      = <<<e.g. check_point_score>>>             ← read from spec
GCP_REFERENCE_CODE   = ../GCP_CodeBase/scripts/                 ← primary lift
GCP_REFERENCE_SPEC   = ../GCP_CodeBase/gcp_confidence_score/    ← compare bundles
```

## Folder layout

**You provide these INPUTS before Step 0** (mirror the GCP layout exactly):

```
CheckPoint_CodeBase/
├── BUILD_PROMPT_TEMPLATE_v2.md          ← copy from GCP_CodeBase
├── CHECKPOINT_KICKOFF_PROMPT.md         ← this file
├── <SPEC_BUNDLE_FOLDER>/                ← the frozen check-point bundle, e.g.:
│   ├── check_point_confidence_score.json   (master — the single source of truth)
│   ├── 01_source_files.csv
│   ├── 02_source_fields.csv
│   ├── 03_derived_fields.csv
│   ├── 04_indicators.csv
│   ├── 05_building_blocks.csv
│   ├── 06_check_point_score.csv  +  06b_..._meta.csv
│   ├── 07_flags.csv
│   ├── 08_problem_coverage_map.csv
│   └── *.xlsx / *.html (optional companions)
└── sample_data/
    ├── checkpoint_rinex_point_1/        (one folder per occupation)
    │   ├── log*.??O                     (RINEX observation)
    │   ├── log*.??P / .??N              (RINEX nav, if present)
    │   ├── user_input.json              (operator form)
    │   ├── hardware.json                (4-tier override, if RINEX header stripped)
    │   └── oplog.json                   (only for DGPS devices)
    ├── checkpoint_rinex_point_2/ …
    └── checkpoint_rinex_point_3/ …
```

**The build GENERATES these** (do NOT create them during Step 0 — they are
the output of Steps 1–12):

```
├── paths.json                           ← Step 1
├── scripts/  (run_pipeline.py, stage1_inventory.py, stage2_merge.py,
│              stage3a_derived.py, stage3b_indicators.py, stage3c_blocks.py,
│              stage3d_score.py, common.py, parsers/, test_scenarios.py)
├── outputs/  (01_inventory.json … 06_check_point_score.json)
├── tests/scenarios/  (one self-contained dir per scenario + _summary.json
│                      + _pass2_problem_coverage.{csv,json})
└── cache/noaa_swpc/  (Kp index cache, if iono indicator exists)
```

If any input is missing when you start, ask — don't guess.

## Binding process constraints (same as the prior three builds)

1. **STOP after every step and wait for my explicit "OK"** before the next.
   Step 12 (smoke-test harness) is the final step.
2. **Never hardcode weights, thresholds, or formulas** — read them from the
   spec bundle at runtime. Sanctioned exceptions: Option-B per-indicator eval
   functions for prose-only bands, and engineering tuneables declared as
   named constants in `*_meta.tuneables`.
3. **Run an explicit 6–10 check audit on the high-impact steps (7, 10, 12).**
4. **Determinism:** NO timestamps inside the data block (only the envelope's
   `generated_at`); for the test harness, omit `generated_at` entirely so
   artifacts are byte-stable across runs.
5. **Lift from `../GCP_CodeBase/scripts/`, but verify each file against the
   check-point spec before trusting it.** GCP is the closest twin, so most
   files should port with minimal change — prove it, don't assume it.

## What carries forward from GCP (anticipate these)

1. **Multi-occupation structure** — N points, each scored per-point, then a
   per-block cross-point aggregation `mean − k·(100 − min)` (GCP used
   `k = 0.25`), then the weighted apex. Confirm the weights/k from the spec.
2. **Device-type branching** — CB_X / AeroPoint auto-known antenna height;
   DGPS expects an oplog; OTHER raises an "unrecognized device" flag. Check
   whether the check-point spec keeps the same device set.
3. **georinex hybrid** (Section 7g) — `gr.rinexheader()` for the header,
   manual streaming for the body. `georinex` is installed.
4. **4-tier hardware override** (Section 7a) — `hardware.json` fills RINEX
   header fields stripped by u-blox/Emlid conversion.
5. **NAV-driven PDOP** (Section 7f) — `parse_nav.py` + `gnss_orbits.py`,
   10° elevation mask. (GCP derived PDOP but did not consume it in an
   indicator — check whether check point does.)
6. **Placeholder lifecycle** (Section 8) — operator-pending inputs carry
   `_status: PLACEHOLDER`; Stage 1 emits `PLACEHOLDER_INPUTS_DETECTED`.
7. **Two-gate pattern** — a per-point coverage gate (one point fails → that
   point zeroed + a per-point flag) vs. a survey-level global gate (every
   role-point gated → apex 0 / null + a global flag). Confirm both exist.
8. **Self-contained scenario dirs** (Section 7j) — Step 12 writes the mutated
   `02_source_fields.json` + all five stage outputs into each
   `tests/scenarios/<name>/`.

## Cross-bundle considerations specific to Check Point

- The PPK baseline for a check point comes from the **base station** of the
  same survey — so cross-bundle candidates (base-vs-CP timing, antenna model
  consistency, baseline distance) belong in
  `_handoff_crossdoc_candidates` in the Stage 2 envelope, forwarded to the
  apex envelope — **not** raised at Stage 1.
- **Independence from the GCP set** is the defining check-point concern. If
  the spec scores it, it needs the GCP point set as a cross-doc input; if it
  can't, expect it deferred to `pre_processing_score` (document the deferral
  in the Pass-2 map, as GCP did for its 15 out-of-scope problems).
- The spec defines what is in scope. Read the flags sheet and the
  problem-coverage map carefully — do **not** assume check point mirrors GCP
  exactly.

## What I'll share during the build

- **CBMI Check Point Problems sheet** (`xlsx`/`csv`) — when you reach Step 12,
  I'll attach it. It seeds the **Pass 2** real-world scenarios with concrete
  numerical examples drawn from the prose cells, and each problem is mapped
  OWNED / SPLIT / OUT-OF-SCOPE exactly as GCP's 22-problem map was.
- **Operator-pending data** — I'll OK placeholder JSONs when you ask, or give
  real values directly.
- **Decisions on spec-vague thresholds** — when you ask (e.g. PDOP mask,
  Kp cutoff, truncation tolerance), I'll give a direction or tell you to
  default to the industry-standard rubric (Section 7i / 9).

## Start now — Step 0 (comprehension only — STOP at the end)

Per Section 12 of `BUILD_PROMPT_TEMPLATE_v2.md`:

1. **List the contents** of this folder and confirm the spec bundle path +
   filename.
2. **Read** `<SPEC_BUNDLE_FOLDER>/<SPEC_JSON_FILENAME>` end to end.
3. **Answer the comprehension questions** (by content — confirm your bundle's
   sheet numbering; in GCP these were sheets 01 / 05 / 07 / 08):
   - The N **source files** (sheet 01) — `file_id` and `file_name`.
   - The M **building blocks** (sheet 05/06) with their **weights in the
     apex** and the indicators within each.
   - All **flag** names (sheet 07) grouped by `raised_at_stage`, with severity.
   - The **apex score formula** (sheet 06/08) — blocks, weights, global-gate
     condition, null-handling.
   - The **problem-coverage map** (sheet 08) — how many problems, and which
     look OWNED vs deferred.
4. **Produce a GCP-diff report** — explicitly, the most valuable part:
   - Which GCP stages/parsers port **as-is**, which need **edits**, which are
     **new**? (Walk `../GCP_CodeBase/scripts/` file by file.)
   - Does check point reuse the multi-occupation shape unchanged?
   - Is the apex gated on `device_role == CHECK_POINT`?
   - New indicators or flags GCP doesn't have? Missing ones GCP has?
   - Cross-document flags (`handoff_crossdoc`)? Independence-from-GCP scoring?
   - Threshold bands prose-only (Option B) or machine-evaluatable (Option A)?

5. **STOP.** Wait for my OK before Step 1.

Do **not** create folders, write code, write `paths.json`, or run anything
yet — Step 0 is pure comprehension. The build begins only after I review your
comprehension + GCP-diff report and say OK.

---

## Reference paths — lift from GCP first

GCP code to port (verify each against the check-point spec):

| GCP file | What it does | Likely port effort |
|---|---|---|
| `scripts/run_pipeline.py` | orchestrator (takes `paths.json` arg) | rename outputs → low |
| `scripts/stage1_inventory.py` | multi-point discovery + placeholder detection | glob/role tweak → low |
| `scripts/parsers/parse_rinex.py` | georinex hybrid header + manual body streamer | as-is → very low |
| `scripts/parsers/parse_nav.py` | RINEX 3.x broadcast NAV parser | as-is → very low |
| `scripts/parsers/gnss_orbits.py` | Keplerian + GLONASS propagation + PDOP | as-is → very low |
| `scripts/parsers/parse_oplog.py` | DGPS oplog (device-type-aware) | as-is → low |
| `scripts/parsers/parse_user_input.py` | operator form (role/device/flight-window) | field-set check → low |
| `scripts/stage2_merge.py` | per-point source-field assembly → `02_source_fields.json` | field-map per spec → medium |
| `scripts/stage3a_derived.py` | per-point derived fields (+ tuneables) | per spec sheet 03 → medium |
| `scripts/stage3b_indicators.py` | per-indicator eval functions (Option B) | per spec sheet 04 → medium |
| `scripts/stage3c_blocks.py` | block rollups + cross-point aggregation | per spec sheet 05 → medium |
| `scripts/stage3d_score.py` | apex weighted sum + global gate + flag aggregation | per spec sheet 06 → medium |
| `scripts/common.py` | shared helpers | as-is → very low |
| `scripts/test_scenarios.py` | smoke harness (Pass 1 spec-coverage + Pass 2 problems) | per new flags/problems → medium |

GCP scenario directories to study: `../GCP_CodeBase/tests/scenarios/`
(21 scenarios; one defect per scenario for flag isolation, plus anchors,
no-flag enum-band negative tests, and an all-flags stress case).

Lift whatever's reusable. For each lifted file, prove it still applies to the
check-point spec — don't blindly copy.

End of kickoff prompt.
