# Check Point Confidence Score — v2.1 LOCKED

Generated: 2026-06-11 09:59 UTC
Version: 2.1.0 (LOCKED)

## What this is

Tier 1 chain definition bundle for check_point — canonical RTK capture-confidence score. All Q-CP-1 through Q-CP-5 locks applied.

Fourth chain to fully implement the three-tier pattern. Simplest chain (14 indicators, 3 blocks). Completes the **capture quadrant** (drone + base_station + GCP + check_point).

## Three-tier artifacts for check_point v2.1

- **Tier 1 (this bundle):** `check_point_confidence_score_v2_1_LOCKED.json`
- **Tier 2 (separate):** `check_point_indicator_library_v2_1.json`
- **Tier 3 (separate):** `check_point_multi_view_v1_LOCKED.html`

## What's architecturally distinctive about check_point

**Simplest chain.** 14 indicators, 3 blocks, no deduplication needed. Per-point RTK scoring.

**Novel aggregation formula.** Mean-minus-worst: `mean - 0.25 * (100 - worst_point_score)`. Penalizes outliers but doesn't ignore them. Q-CP-3 locks this formula.

**Two per-point gates (not chain-level).** L3I_CP_002 (fix_type FLOAT/AUTONOMOUS) and L3I_CP_005 (antenna_height missing) both zero the affected point. Other points continue to score. Aggregation dilutes impact across fleet.

**Complex global gate.** Fires when ALL points fail in at least one dimension: `(all points have bad fix_type) OR (all points have catastrophic sigma)`. Different from drone's independent hard gates.

**Runtime independent.** Does NOT read from other capture chains. Purely device exports + operation logs + user input + timing context + NOAA Kp API.

## Architecture

- **3 blocks**: Capture Completeness 0.45 / Setup Confidence 0.35 / Observation Environment 0.20
- **14 per-point indicators**
- **2 per-point gates** (affect individual points; aggregation penalizes):
  - L3I_CP_002 fix_type_score (FLOAT/AUTONOMOUS)
  - L3I_CP_005 antenna_height_documented (missing)
- **Complex global gate** (all points must fail):
  - `(every point has fix_type == 0) OR (every point has sigma == 0)`
- **Per-point aggregation** (Q-CP-3 LOCKED):
  - `mean - 0.25 * (100 - worst_point)`
  - k=0.25 penalty coefficient is operationally calibrated

## Q-CP locks applied

| Q | Decision |
|---|---|
| Q-CP-1 | Complex global gate retained as-is. Documents operational condition: all points failed in at least one dimension. |
| Q-CP-2 | Fix type promoted to per-point gate. FLOAT/AUTONOMOUS on one point zeros that point; other points continue. |
| Q-CP-3 | Per-point aggregation formula mean - 0.25*(100 - worst) LOCKED as v2.1 design. k=0.25 is operationally calibrated. |
| Q-CP-4 | No device-type special handling. All devices (CB_X, AEROPOINT, DGPS, OTHER) treated uniformly. |
| Q-CP-5 | No new pattern concepts. Propagates existing patterns: per-point, per-point gates, null case. |

## Files in this bundle

- `check_point_confidence_score_v2_1_LOCKED.json` — master JSON
- `check_point_confidence_score_v2_1_LOCKED.xlsx` — Excel workbook (8 sheets)
- `check_point_provenance_v2_1_LOCKED.html` — full provenance
- `01_source_files.csv` — 3 source files
- `02_source_fields.csv` — 38 source fields
- `03_derived_fields.csv` — 16 derived fields
- `04_indicators.csv` — 14 indicators
- `05_building_blocks.csv` — 3 blocks
- `06_check_point_score.csv` — score meta
- `07_flags.csv` — 30 flags

## Known limitations

1. Single-occupation only. No repeatability check (no dual-occupation data yet).
2. Sigma may be absent if export configured to omit precision columns — flag signals user to re-export.
3. Multipath detection weak (RTK captures instantaneous position, not extended variance like PPK).
4. Kp ionospheric index under-detects equatorial scintillation (relevant for India).
5. False fix (wrong integer ambiguity) documented but NOT detected from single-occupation data.
6. Antenna-height-missing gate dilutes via aggregation — one bad point in N reduces score by (1/N) × penalty.
7. Library text is Claude-drafted placeholder.

## Status of the CBMI chain library (post-check_point)

| Chain | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| **check_point** | **LOCKED v2.1** | **LOCKED v2.1** | **LOCKED v1** |
| drone | LOCKED v2.1 | LOCKED v2.1 | LOCKED v1 |
| base_station | LOCKED v2.1 | LOCKED v2.1 | LOCKED v3 |
| gcp | LOCKED v2.1 | LOCKED v2.1 | LOCKED v1 |
| (4 processing/analytics chains locked v1.0-v1.1, awaiting propagation) | — | — | — |

**Capture quadrant COMPLETE. 4 of 10 chains fully patterned.**
