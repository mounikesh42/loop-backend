# CBMI Chain Library Pattern

**Version:** 1.0  
**Generated from:** base_station v2.1 LOCKED (first chain to fully implement the pattern)  
**Status:** Reference blueprint for propagating the multi-view + library architecture across all CBMI chains

---

## What this document is

This is the **propagation blueprint** for the three-tier chain library pattern developed during the base_station v2.1 lock session. It describes:

1. The three-tier artifact architecture (chain definition / library / reference HTML)
2. The four-severity vocabulary at indicator-band level
3. The three-recommendation vocabulary at chain level
4. The five-tab multi-view HTML reference structure
5. The library JSON schema
6. Hard-gate handling
7. Q-lock decision documentation pattern
8. How to extend the pattern to other chains

When propagating to drone, GCP, check_point, pre_processing, processing, or any of the four analytics chains, follow this document.

---

## Three-tier artifact architecture

Each chain produces **three artifacts** with clear responsibilities:

### Tier 1 — Chain Definition (canonical)

**File pattern:** `<chain>_confidence_score_v<X>_LOCKED.json`

**What it contains:**
- Source files, source fields, derived fields (the evidence)
- Indicators with weights, threshold summaries, gate conditions (the scoring logic)
- Building blocks with weights (the aggregation)
- Score-level metadata (formula, gate condition, tier interpretation, vocabulary documentation)
- Flags catalog
- Problem coverage map

**Responsibility:** This is the **canonical scoring source of truth**. Any system that computes scores reads from this file. Threshold values, indicator weights, hard gate conditions live here.

**Format:** JSON. Also exported as CSV (one per level) and XLSX workbook.

### Tier 2 — Indicator Library (layered annotation)

**File pattern:** `<chain>_indicator_library_v<X>.json`

**What it contains:**
- Per indicator: identity, weight (mirrored from Tier 1 for reference), is_critical_path flag
- Per indicator: verified_statement (one-sentence pass language)
- Per indicator × per band: customer-voice impact text + customer-voice action items
- Per indicator: derivation rationale (threshold reasoning for auditor view)
- Schema notes and authorship metadata

**Responsibility:** This is the **customer-facing language source of truth**. It's a layered annotation ON TOP OF the chain definition. References Tier 1 indicator IDs. When Tier 1 changes weight or threshold ranges, Tier 2 entries for affected indicators need review.

**Format:** JSON.

**Edit workflow:** Domain experts walk the Library — Cards tab of the Tier 3 HTML, refine wording, edit this JSON, regenerate views.

### Tier 3 — Reference Implementation (build artifact)

**File pattern:** `<chain>_multi_view_v<X>.html`

**What it contains:**
- Five tabs: Customer / Internal QA / Auditor / Library — Cards / Library — Structured
- Inline copies (or loader references) of Tier 1 chain metadata and Tier 2 library
- Render functions for each of the five views
- Three pre-built scenarios (Clean / Review / Resurvey) for scenario-driven views
- Library tabs render scenario-independently from Tier 2 content

**Responsibility:** This is the **agreement surface** — where team members read what the chain produces, refine wording, and align on outcomes. NOT the production scoring engine; production scoring runs from Tier 1.

**Format:** Single self-contained HTML file with inline CSS + JavaScript. Design tokens with fallbacks (option iii: works embedded in design system AND standalone in browser).

---

## Four-severity vocabulary (band-level)

Indicators have **bands** (score ranges). Each band has a **severity_of_finding** at the band level. Four values, increasing severity:

| Severity | When | Behavior |
|---|---|---|
| **NONE** | Band passed | No concern; appears as VERIFIED in customer view if in good band |
| **MINOR** | Hygiene signal | Does NOT drive recommendation; visible only in "what passed / what else was checked" expandable; audit-only |
| **MATERIAL** | Warrants review | Drives `review_recommended` chain recommendation; appears in customer-view action items |
| **CRITICAL** | Recollection required | Drives `resurvey_recommended` chain recommendation; appears in customer-view action items as RESURVEY TRIGGER |

**Important — vocabulary separation:** The band-level vocabulary (none/minor/material/critical) is **separate from** the chain-level recommendation vocabulary (good_to_go/review_recommended/resurvey_recommended). They serve different layers and must not be conflated. The chain-level recommendation is *derived* from the worst band severity across all indicators (modulo hard gates).

---

## Three-recommendation vocabulary (chain-level)

Chains have an **overall recommendation** as a categorical field:

| Recommendation | When | Source |
|---|---|---|
| `good_to_go` | All indicators in good or minor bands | No material or critical findings |
| `review_recommended` | At least one material finding | No hard gate, no critical findings |
| `resurvey_recommended` | Critical finding OR hard gate fired | Any critical band OR is_critical_path indicator scored 0 |

Note: chain-level recommendation language varies by chain workflow:
- **Capture chains** (drone, base_station, GCP, check_point): `resurvey_recommended`
- **Processing chains** (pre_processing, processing): `reprocess_recommended`
- **Analytics chains** (stockpile, pit, wd, cf): `recompute_recommended`

Same severity logic, different action language matched to chain workflow.

---

## Five-tab multi-view HTML structure

Tier 3 HTML has **exactly five tabs**, in this order:

### Tab 1 — Customer View
- Big headline score (0-100) + categorical recommendation
- One-line rationale
- **Top-3 hero cards** (verified items for good outcomes, action items for review/resurvey)
- **Symmetric expandables**: 
  - Good outcome: one expandable ("What else was checked")
  - Review/Resurvey: two expandables ("Other findings", "What passed")
- NO indicator scores or thresholds visible
- Minor findings hidden unless user expands "What passed/What else was checked"

### Tab 2 — Internal QA View
- Customer headline + recommendation
- `verification_status` categorical field
- `global_gate_condition` audit string
- **Block decomposition**: weighted score bars per block
- **Full indicator decomposition**: all indicators grouped by block, with score pills, severity tags, raised flags
- "Why this score" audit note

### Tab 3 — Auditor View
- Everything in Internal QA
- **Threshold Derivation Methodology** block (judgment vs empirical, calibration roadmap)
- **Per-indicator threshold derivation table**
- **Block weights & aggregation table** with rationale
- **Q-locks applied** documentation
- **Known limitations** list

### Tab 4 — Library — Cards (scenario-independent)
- Every indicator × every band rendered as the actual customer-card preview
- Internal QA preview alongside each band
- Grouped by block, indicators stacked, each individually collapsible
- Expand all / Collapse all controls

### Tab 5 — Library — Structured (scenario-independent)
- Same content as Library — Cards but as dense structured rows
- Grouped by block, indicators stacked, each individually collapsible

**Scenario picker** at top of page controls Tabs 1-3. Hides when Tabs 4-5 are active (library is scenario-independent).

---

## Library JSON schema

Tier 2 file structure:

```json
{
  "_meta": {
    "title": "<chain>_indicator_library",
    "version": "<X> (LOCKED)",
    "generated_at": "<ISO timestamp>",
    "description": "...",
    "three_tier_architecture": { "tier_1_chain_definition": "...", "tier_2_library_this_file": "...", "tier_3_reference_html": "..." },
    "text_authorship_status": "DRAFT | DOMAIN-REFINED | LOCKED",
    "q_locks_applied": ["Q1", ...],
    "indicators_count": <int>,
    "total_bands_count": <int>,
    "critical_path_indicators": ["L3I_..._NNN", ...],
    "schema_notes": { ... }
  },
  "library": {
    "L3I_<CHAIN>_NNN": {
      "id": "L3I_<CHAIN>_NNN",
      "num": "#NN",
      "block": "BB_<CHAIN>_<BLOCK>",
      "weight": <float>,
      "name": "Short Display Name",
      "fullName": "indicator_name_in_chain_definition",
      "is_critical_path": <bool>,
      "verified_statement": "One-sentence statement when in good band",
      "bands": [
        {
          "score_range": [<lo>, <hi>],
          "level": "good" | "review" | "resurvey" | "minor",
          "label": "Short threshold description",
          "impact": "Customer-voice why-it-matters" | null,
          "actions": ["Action 1", "Action 2", ...] | null
        }
      ],
      "derivation": "Threshold reasoning for auditor view",
      "flag": "FLAG_NAME" | null
    }
  }
}
```

**Validation rules:**
- Every `L3I_*` indicator ID in Tier 2 MUST exist in Tier 1
- Each `bands` array must have at least 2 entries (good + at least one non-good)
- `is_critical_path: true` requires a `score_range: [0,0]` band with `level: "resurvey"`
- Good bands have `impact: null` and `actions: null` (only verified_statement applies)
- Non-good bands MUST have non-null `impact` and `actions`

---

## Hard-gate handling

A chain has **zero or more hard gates**. Each hard gate is a critical-path indicator that, when scored 0, forces the overall chain score to 0 regardless of other indicators.

**Implementation:**
- Tier 1: indicator has `gate_action` describing the hard gate behavior; score-level `global_gate_condition` lists ALL hard-gate conditions joined with OR
- Tier 2: indicator has `is_critical_path: true`
- Tier 3: scoring logic checks all critical-path indicators for score 0 before computing weighted aggregate

**Per chain — typical hard gate count:**

| Chain | Hard gates | Examples |
|---|---|---|
| drone | TBD | (review existing chain) |
| base_station | 2 | coverage_score (BB_BASE_COMPLETE), antenna_height (BB_BASE_SETUP) |
| gcp | TBD | (review existing chain) |
| check_point | TBD | (review existing chain) |
| pre_processing | TBD | (review existing chain) |
| processing | 1 | output_crs_unverified (or similar global CRS gate) |
| stockpile_analytics | 3 | upstream_crs, feature_below_floor, degenerate_polygon |
| pit_analytics | 3 | upstream_crs, feature_below_floor, degenerate_polygon |
| wd_analytics | 3 | upstream_crs, feature_below_floor, degenerate_polygon |
| cf_analytics | 3 | upstream_crs, feature_below_floor (two-volume), degenerate_polygon |

---

## Q-lock decision documentation pattern

During a chain lock, real questions emerge that require explicit team decisions. Document them as **Qn locks**:

1. List the Q in `_meta.q_locks_applied` in Tier 1 with brief description
2. Tag affected indicator fields with `v2_1_q_lock_note` (or version-equivalent)
3. Tag affected flag entries with `v2_1_q_lock` annotation
4. Document in Tier 1 score meta `tier_interpretation` field
5. Reference in Tier 3 HTML Auditor view methodology block

**Why this matters:** Without explicit Q-lock documentation, future readers can't trace WHY a band has its current severity. The Q-locks are the audit trail for design decisions.

---

## Extending the pattern to a new chain

**For each new chain (drone, GCP, etc.), the propagation workflow is:**

### Step 1 — Verify Tier 1 chain definition is current
- Read existing chain definition JSON
- Confirm indicator weights, block weights, threshold ranges are final
- Identify hard gates (critical-path indicators)
- Identify any pending Q-locks (parked design questions)

### Step 2 — Resolve any parked questions
- Surface parked questions explicitly
- User answers each with documented rationale
- Apply Q-lock decisions to Tier 1 (add `v2_1_q_lock_note` to indicators, downgrade/upgrade flag severities)
- Update score meta with `global_gate_condition`, `severity_of_finding_vocabulary`, `verification_status_field`, `tier_interpretation`

### Step 3 — Build Tier 2 library
- For each indicator, draft `verified_statement` (one sentence)
- For each band, draft `impact` (customer-voice why-it-matters) and `actions` (2-3 customer-voice action items)
- For each indicator, draft `derivation` (threshold rationale)
- Mark `text_authorship_status: DRAFT` until domain refinement

### Step 4 — Build Tier 3 HTML
- Copy v3 base_station template
- Replace `BLOCKS`, `INDICATOR_LIBRARY`, `SCENARIOS`, `GLOBAL_GATE_CONDITION` constants
- Adjust recommendation labels (capture/processing/analytics-specific language)
- Verify all 5 tabs render correctly
- Verify scenario-driven and library tabs both work

### Step 5 — Validate
- All weights sum to 1.0 (per block and overall)
- Every Tier 2 indicator ID exists in Tier 1
- Hard gates wired consistently across all three tiers
- Q-locks documented in all three tiers
- Scenario pressure-test produces expected tier outputs

### Step 6 — Bundle
- ZIP with naming: `<chain>_score_v<X>_FINAL.zip`
- Contents: JSON master, library JSON, multi-view HTML, CSVs, XLSX, provenance HTML, BUILD_NOTES.md

---

## What stays the same across chains, what varies

### Stays the same
- Three-tier architecture
- Four-severity band vocabulary (none/minor/material/critical)
- Five-tab HTML structure
- Library JSON schema
- Hard-gate handling logic
- Q-lock documentation pattern
- Customer view: top-3 hero + symmetric expandables
- Render layer (basically copied between chains)

### Varies per chain
- Number of blocks (3 for base_station, 6 for analytics chains)
- Number of indicators
- Block weights and indicator weights
- Number and identity of hard gates
- Chain-level recommendation vocabulary (resurvey/reprocess/recompute)
- Library content (different domains, different operator language)
- Three scenarios (chain-appropriate test cases)

---

## Known limitations of the pattern

**One — text quality.** First-pass library text is typically Claude-drafted placeholder. Domain refinement is required before customer-facing deployment. Pattern accommodates this (text_authorship_status field) but doesn't enforce it.

**Two — render layer duplication.** Each chain currently gets its own copy of the render functions. Future improvement: factor render layer into a shared module each chain HTML imports. Cost of current duplication: ~600 lines per chain in JS.

**Three — Tier 2 / Tier 1 sync.** If Tier 1 weights or thresholds change, Tier 2 entries need manual review. No automated check yet. Future improvement: schema validator that flags mismatches.

**Four — calibration data.** All chains use first-principles + industry-convention thresholds. Empirical calibration against S3-retained CBEI outcomes is a future deliverable, not embedded in current Tier 1.

**Five — cross-chain consistency drift.** Each chain locked independently can develop subtle architectural variations (e.g., registration drift placement in pit Block 4 vs WD Block 6). Pattern enables but doesn't enforce cross-chain consistency. A periodic cross-chain consistency review pass is necessary.

---

## File naming conventions

| Tier | File | Format |
|---|---|---|
| 1 | `<chain>_confidence_score_v<X>_LOCKED.json` | JSON master |
| 1 (exports) | `01_source_files.csv` through `08_problem_coverage_map.csv` | CSV |
| 1 (exports) | `<chain>_confidence_score_v<X>_LOCKED.xlsx` | XLSX workbook |
| 1 (exports) | `<chain>_provenance_v<X>_LOCKED.html` | Provenance documentation |
| 2 | `<chain>_indicator_library_v<X>.json` | Library JSON |
| 3 | `<chain>_multi_view_v<X>.html` | Reference HTML |
| Bundle | `<chain>_score_v<X>_FINAL.zip` | Complete bundle |

---

## Glossary

- **Chain** — A confidence-scoring subsystem (drone, base_station, etc.)
- **Indicator** — A leaf scoring node (e.g., coverage_score, antenna_height_documented_score)
- **Block** — A grouping of indicators with weighted aggregation
- **Band** — A score range within an indicator with associated level + label + customer-voice content
- **Hard gate** — A critical-path condition that forces overall chain score to 0
- **Q-lock** — A documented design decision resolving a parked question
- **Tier** — One of three artifact layers (chain definition / library / reference HTML)
- **Critical-path indicator** — An indicator that can fire a hard gate

---

## When in doubt

- **Read the base_station v2.1 LOCKED bundle.** It's the canonical reference implementation. Every other chain should look architecturally similar.
- **Read the cf_analytics_score_v1.0_FINAL bundle.** It demonstrates the pattern applied to a non-base chain (different blocks, different hard gates, three-state reference verification — a chain-library-level extension).
- **Compare CSVs across chains.** Same column structure should mean same field semantics. Divergences signal architectural drift worth surfacing.

---

*End of pattern documentation.*
