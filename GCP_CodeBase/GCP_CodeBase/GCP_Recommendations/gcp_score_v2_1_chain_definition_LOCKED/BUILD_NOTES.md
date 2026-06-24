# GCP Confidence Score — v2.1 LOCKED

Generated: 2026-06-11 03:40 UTC
Version: 2.1.0 (LOCKED)

## What this is

Tier 1 chain definition bundle for GCP — the canonical scoring source of truth. All Q-GCP-1/2/3 locks applied during the v2.1 lock session.

Second chain to fully implement the three-tier multi-view + library pattern (after base_station v2.1). First chain to introduce **per-point semantics** and **chain-not-applicable (null) handling**.

## Three-tier artifacts for GCP v2.1

- **Tier 1 (this bundle):** `gcp_confidence_score_v2_1_LOCKED.json` — canonical scoring
- **Tier 2 (separate):** `gcp_indicator_library_v2_1.json` — layered customer-voice text
- **Tier 3 (separate):** `gcp_multi_view_v1_LOCKED.html` — five-tab reference implementation

See `cbmi_chain_library_pattern.md` for the propagation blueprint.

## What's different about GCP vs base_station

**Per-point chain.** GCP is the first CBMI chain with explicit per-point semantics:
- Each survey may have multiple GCPs (typically 4-12)
- Indicator scores computed per-point; chain score = average of per-point scores
- Per-point findings surface in customer view with point-ID attribution
- Internal QA view exposes per-point breakdown
- Library text uses `{point_id}` placeholder; rendering layer substitutes actual point IDs

**Hard gate is conditional.** Chain-level hard gate fires only when EVERY designated GCP has coverage = 0. Per-point coverage failures degrade individual points but don't kill the chain. Antenna_height_missing is per-point gate only (not chain-level) — affected point's contribution minimized but chain continues from other points.

**Null case exists.** NO_DESIGNATED_GCPS (survey designed without GCPs) returns null, NOT score = 0. This is a survey-design choice, not a failure. First chain to formally handle "chain not applicable" semantically.

**Device type conditionality.** Three device classes (DGPS, CB_X / AEROPOINT, OTHER) with different indicator logic per class. CB_X / AEROPOINT auto-score 100 on antenna_height (factory-known).

## Architecture

- **3 blocks**: Capture Completeness & Integrity 0.45 / Per-point Setup & Documentation Confidence 0.35 / Per-point Observation Environment 0.20
- **10 indicators** across the 3 blocks
- **1 chain-level hard gate** (Q-GCP-1):
  - L3I_GCP_001 occupation_coverage_score — when EVERY designated GCP has score=0
- **1 per-point gate**:
  - L3I_GCP_005 gcp_antenna_height_documented_score — degrades point only, not chain
- **4-severity vocabulary** at indicator-band level: none / minor / material / critical
- **3-recommendation vocabulary** at chain level: good_to_go / review_recommended / resurvey_recommended
- **Null handling**: gcp_score = null when NO_DESIGNATED_GCPS

## Q-GCP locks applied

| Q | Decision |
|---|---|
| Q-GCP-1 | One chain-level hard gate (all-points-coverage-failed). Antenna_height_missing is per-point gate ONLY, not chain-level. |
| Q-GCP-2 | NO_DESIGNATED_GCPS → chain returns null (not applicable), not score=0. Survey-design choice, not a failure. |
| Q-GCP-3 | Customer view aggregates top-3 findings across all points with per-point attribution (e.g., "Antenna height issue at GCP-003"). |

## Files in this bundle

- `gcp_confidence_score_v2_1_LOCKED.json` — master JSON
- `gcp_confidence_score_v2_1_LOCKED.xlsx` — Excel workbook
- `gcp_provenance_v2_1_LOCKED.html` — full provenance documentation
- `01_source_files.csv` — 3 source files
- `02_source_fields.csv` — source fields
- `03_derived_fields.csv` — derived fields
- `04_indicators.csv` — 10 indicators
- `05_building_blocks.csv` — 3 blocks
- `06_gcp_score.csv` — block composition
- `06b_score_meta.csv` — score metadata
- `07_flags.csv` — flags catalog
- `08_problem_coverage_map.csv` — problem coverage

## Pattern doc updates (from GCP propagation)

GCP introduces two new pattern concepts not present in base_station:

1. **Per-point chain semantics** — indicator scores per-point, chain score aggregates. `{point_id}` placeholder convention in library. Findings attributed to specific points in customer view.

2. **Chain-not-applicable (null) handling** — distinct from chain-failed (score=0). Survey-design choices that make a chain inapplicable should return null, not zero. Composite scoring downstream must respect null vs zero.

Both should be added to the pattern doc. Recommend doing the update once we see how the next chain (probably check_point or pre_processing) handles its own variations, so the pattern doc absorbs multiple chain learnings at once.

## Library text status

CLAUDE-DRAFTED PLACEHOLDER. Per pattern Q1 (structure-first propagation), library text is placeholder pending domain refinement. Same status as base_station v2.1. Library-wide text refinement pass will happen after all 9 chains have Tier 2 libraries built.

## Known limitations

1. Per-point chain score = simple average. Future enhancement: weighted average (e.g., perimeter points weighted higher than interior).
2. Device type conditionality (CB_X / AEROPOINT) handled but not exhaustively tested.
3. Multipath via C/N0 variance — heuristic proxy, not measured PPK residual.
4. Ionospheric risk requires manual NOAA SWPC Kp index lookup — automation pending.
5. Per-point pattern is new to CBMI — first chain to introduce it. Pattern doc update pending.
6. NO_DESIGNATED_GCPS handling assumes downstream composite scoring respects null. Verification pending.
7. Library text is Claude-drafted placeholder.

## Status of the CBMI chain library (post-GCP)

| Chain | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| drone | locked | — | — |
| base_station | LOCKED v2.1 | LOCKED v2.1 | LOCKED v3 |
| **gcp** | **LOCKED v2.1** | **LOCKED v2.1** | **LOCKED v1** |
| check_point | locked v1 | — | — |
| pre_processing | locked v1.1 | — | — |
| processing | locked v1.1 | — | — |
| stockpile_analytics | locked v1.0 | — | — |
| pit_analytics | locked v1.0 | — | — |
| wd_analytics | locked v1.0 | — | — |
| cf_analytics | locked v1.0 | — | — |

2 chains fully patterned, 7 to go.
