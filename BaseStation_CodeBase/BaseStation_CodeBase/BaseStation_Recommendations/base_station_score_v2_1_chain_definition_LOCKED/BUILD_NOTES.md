# Base Station Confidence Score — v2.1 LOCKED

Generated: 2026-06-10 13:31 UTC
Version: 2.1.0 (LOCKED)

## What this is

Tier 1 chain definition bundle for base_station — the canonical scoring source of truth. All Q1-Q6 locks applied from offline review session.

This bundle is one of three artifacts for base_station v2.1:
- **Tier 1 (this bundle):** Chain definition (canonical) — `base_station_confidence_score_v2_1_LOCKED.json`
- **Tier 2 (separate):** Indicator library (layered annotation) — `base_station_indicator_library_v2_1.json`
- **Tier 3 (separate):** Reference implementation HTML — `base_station_multi_view_v3.html`

See `cbmi_chain_library_pattern.md` for the propagation blueprint to other chains.

## Architecture

- **3 blocks**: Data Completeness & Integrity 0.45 / Setup & Documentation Confidence 0.35 / Observation Environment Quality 0.20
- **11 indicators** across the 3 blocks
- **2 hard gates** (per Q1, Q6 locks):
  - L3I_BASE_001 coverage_score (BB_BASE_COMPLETE) — flight gap detection
  - L3I_BASE_005 antenna_height_documented_score (BB_BASE_SETUP) — height missing
- **4-severity vocabulary** at indicator-band level: none / minor / material / critical
- **3-recommendation vocabulary** at chain level: good_to_go / review_recommended / resurvey_recommended
- Confidence-only output: score (0-100) + tier + recommendation
- Hard-gate behavior: any hard gate fires → overall score = 0

## Q-locks applied (6 decisions documented)

| Q | Decision |
|---|---|
| Q1 | antenna_height = true HARD GATE (forces overall score to 0) |
| Q2 | RINEX version unsupported = material (not critical) — data fixable via conversion |
| Q3 | session interrupted stays critical for v2.1; v2 enhancement (flight-window overlap) pending |
| Q4 | SLOW_BASE_ACQUISITION + BASE_LOG_DOWNLOAD_UNCONFIRMED = minor (hygiene signals, audit-only) |
| Q5 | minor findings hidden from default customer view; visible only in expandables |
| Q6 | global_gate_condition lists both hard gates explicitly |

## Files in this bundle

- `base_station_confidence_score_v2_1_LOCKED.json` — master JSON, canonical chain definition
- `base_station_confidence_score_v2_1_LOCKED.xlsx` — Excel workbook with 10 sheets
- `base_station_provenance_v2_1_LOCKED.html` — full provenance documentation
- `01_source_files.csv` — 3 source files
- `02_source_fields.csv` — source fields
- `03_derived_fields.csv` — derived fields
- `04_indicators.csv` — 11 indicators
- `05_building_blocks.csv` — 3 blocks
- `06_base_station_score.csv` — block composition
- `06b_score_meta.csv` — score metadata
- `07_flags.csv` — flags catalog
- `08_problem_coverage_map.csv` — problem coverage

## Known limitations (stated, not hidden)

1. Indicator thresholds are first-principles calibrated, not yet empirically validated. Quarterly retrospective calibration against S3-retained CBEI verification CP RMSE outcomes is the next deliverable.
2. Multipath score is a risk proxy via C/N0 variance — actual error magnitude only emerges in pre_processing residuals.
3. Antenna type match is type-string consistency only — not true ANTEX phase-center calibration.
4. Ionospheric risk currently requires manual Kp index lookup against NOAA SWPC — automation pending.
5. Setup verification (L3I_BASE_006) score 50 (reused mark unverified) is conceptually a third-state pattern (provenance unverifiable) introduced fully in cf_analytics v1.0 — chain-library retrofit pending.
6. Session interrupted (Q3 lock): v2 enhancement to check timestamp overlap with flight window is pending.
7. Library text in Tier 2 is Claude-drafted placeholder. Domain refinement is pending per Q1 confirmation during the lock session.

## Three-tier artifact responsibilities (recap)

| Tier | Artifact | What it owns |
|---|---|---|
| 1 | This bundle's JSON | Chain definition — scoring logic, weights, thresholds, hard gates |
| 2 | Library JSON | Customer-voice text — verified statements, impacts, actions, derivations |
| 3 | Multi-view HTML | Agreement surface — how the chain looks to customer / QA / auditor / library reviewers |

## What's next (cross-chain propagation)

The base_station v2.1 LOCKED bundle plus the pattern documentation establish the blueprint. Library-wide propagation across the other 8 chains (drone, GCP, check_point, pre_processing, processing, stockpile_analytics, pit_analytics, wd_analytics, cf_analytics) follows the workflow in `cbmi_chain_library_pattern.md`.

One chain per session. Each gets its own Tier 1 + Tier 2 + Tier 3 artifacts, then library-wide text refinement once all 9 chain libraries exist.
