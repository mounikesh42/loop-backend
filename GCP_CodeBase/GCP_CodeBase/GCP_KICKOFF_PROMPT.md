# GCP PPK Pipeline — Session Kickoff Prompt

> Paste the contents of this file (or the body below the `---`) as the FIRST
> message to a new Claude Code session opened in `GCP_CodeBase/`. Attach the
> companion file **`BUILD_PROMPT_TEMPLATE_v2.md`** to the same session — that
> is the comprehensive build guide Claude will follow step by step.

---

You are building the **GCP PPK** (Ground Control Point, Post-Processed
Kinematic) provenance scoring pipeline. This is the **third** subsystem in
the Capture quality-scoring family; two are already shipped and serve as
reference:

| Subsystem | Status | Location |
|---|---|---|
| Drone PPK | shipped | `../drone_provenance_ppk/` (spec only) — code lives elsewhere |
| Base Station PPK | shipped end-to-end | `../BaseStation_CodeBase/` — full reference build |
| **GCP PPK** | **building now** | `<this folder>` |

The disciplined build process is documented in the attached
**`BUILD_PROMPT_TEMPLATE_v2.md`**. That file is the source of truth — read
it before doing anything else. It captures every pattern, audit, and
engineering decision from the prior two builds.

## Project context — fill in any `<<<…>>>` you can; ask me for what you can't

```
PROJECT_ROOT         = <<<absolute path to this GCP_CodeBase folder>>>
SUBSYSTEM_NAME       = gcp_ppk
SPEC_BUNDLE_FOLDER   = <<<e.g. gcp_provenance_ppk>>>           ← I'll confirm
SPEC_JSON_FILENAME   = <<<e.g. gcp_provenance_ppk.json>>>      ← I'll confirm
APEX_SCORE_NAME      = <<<e.g. gcp_score>>>                    ← read from spec
DRONE_SCORE_BUNDLE_PATH        = ../drone_provenance_ppk/      ← reference only
BASE_STATION_SCORE_BUNDLE_PATH = ../BaseStation_CodeBase/base_station_confidence_score/
```

## What's already in this folder when you start

You should expect to find at minimum:
- `<SPEC_BUNDLE_FOLDER>/` — the frozen provenance bundle (JSON + per-sheet CSVs + xlsx)
- `sample_data/<one or more source subfolders>/` — real survey inputs
- `BUILD_PROMPT_TEMPLATE_v2.md` — comprehensive build guide (attached)
- this kickoff prompt

If something is missing, ask me — don't guess.

## Carrying forward from the base station build

These patterns from `BaseStation_CodeBase/` are highly likely to apply here
too — anticipate them:

1. **Hardware Override pattern** (Section 7a of v2) — if RINEX header fields
   are stripped by u-blox/Emlid conversion, drop `sample_data/hardware.json`
   with operator-known values; parser uses 4-tier resolution.
2. **georinex hybrid** (Section 7g) — `gr.rinexheader()` for header parsing,
   manual streaming for body. `georinex` is installed (`1.16.2`).
3. **NAV-driven PDOP** (Section 7f) — if `pdop_per_epoch` is in sheet 02,
   you'll need `parse_nav.py` + `gnss_orbits.py` (already written for base,
   can be lifted with minimal change). 10° elevation mask is industry standard.
4. **Placeholder lifecycle** (Section 8) — for any operator-pending source
   (likely a User Input form analogous to base's antenna setup record), write
   a placeholder JSON with `_status: PLACEHOLDER` and Stage 1 will warn about it.
5. **Threshold rubric** (Section 9) — when the spec uses prose ("low /
   moderate / high"), use industry-standard values (PDOP mask 10°, Kp 5.0,
   battery 10%, truncation 3 s, slips/hr 100, acquisition 8 sats/10 s).
6. **Self-contained scenario directories** (Section 7j) — write the mutated
   `02_source_fields.json` per scenario so each `tests/scenarios/<name>/`
   directory is fully auditable on disk.

## Cross-bundle considerations specific to GCP

GCP measurement is structurally different from base station:
- A GCP survey typically has **multiple occupations** of multiple ground
  control marks (stop-and-go RINEX, or one RINEX per point).
- Each occupation needs its own antenna height, occupation duration,
  measurement quality.
- The PPK baseline for GCP comes from the **base station** of the same
  survey — so cross-bundle flags will likely include base-vs-GCP timing,
  antenna model consistency, baseline distance.
- These cross-bundle flags should be preserved as
  `_handoff_crossdoc_candidates` in the Stage 2 merge envelope (per rule 4)
  and forwarded to the apex envelope, NOT raised at Stage 1.

The spec itself defines what's in scope. Read sheets 01 / 06 / 07 / 09
carefully — don't assume GCP follows the base-station pattern exactly.

## What I'll share during the build

- **CBMI GCP Problems sheet** (`xlsx` or `csv`) — when you reach Step 12,
  I'll attach this. It seeds Pass 2 real-world scenarios with concrete
  numerical examples drawn directly from the prose cells (same way the
  base station sheet drove `rw_height_conflict_65mm` etc.).
- **Operator-pending data** — for any source that arrives as a schema only,
  I'll OK placeholder JSONs when you ask, OR provide real values directly.
- **Decisions on spec-vague thresholds** — when you ask "PDOP mask 5° vs
  10°?" or similar, I'll give a direction or ask you to default to
  industry-standard per Section 7i.

## Start now — Step 0

Per Section 12 of `BUILD_PROMPT_TEMPLATE_v2.md`:

1. **List the contents** of this folder and confirm the spec bundle path.
2. **Read** `<SPEC_BUNDLE_FOLDER>/<SPEC_JSON_FILENAME>`.
3. **Answer the four comprehension questions** in Step 0:
   - The N source files from sheet 01 (`file_id` and `file_name`)
   - The M building blocks from sheet 06 with their weights in the apex
   - All flag names from sheet 09 grouped by `raised_at_stage`
   - The apex score formula from sheet 08
4. **Surface divergences** from the base-station pattern explicitly:
   - Does this spec have a parallel deliverable like drone's CAL_CONF?
   - Are there cross-document flags (`handoff_crossdoc`)?
   - Are threshold bands prose-only (Option B required) or
     machine-evaluatable (Option A)?
   - Does the multi-occupation GCP shape change anything fundamental?

5. **STOP.** Wait for my OK before Step 1.

Do **not** create folders, write code, or run anything yet — Step 0 is
pure comprehension. The build only begins after I review your comprehension
report and say OK.

---

## Reference paths

- v2 template: `<<<path to attached BUILD_PROMPT_TEMPLATE_v2.md>>>`
- Base station reference code: `../BaseStation_CodeBase/scripts/`
  - parsers/parse_rinex.py (georinex hybrid header + manual body streamer)
  - parsers/parse_nav.py (RINEX 3.x broadcast NAV parser)
  - parsers/gnss_orbits.py (Keplerian + GLONASS propagation + PDOP)
  - compute_derived.py (24 L2D fields with tuneables surfaced)
  - compute_indicators.py (per-indicator eval functions, Option B)
  - compute_blocks.py (weight-sum audit + internal gates)
  - compute_base_score.py (apex weighted sum + global gate + flag aggregation)
  - test_scenarios.py (26-scenario harness with Pass 1 + Pass 2 real-world)
- Base station scenario directories: `../BaseStation_CodeBase/tests/scenarios/`

Lift whatever's reusable. For each lifted file, verify it still applies to
GCP's spec — don't blindly copy.

End of kickoff prompt.
