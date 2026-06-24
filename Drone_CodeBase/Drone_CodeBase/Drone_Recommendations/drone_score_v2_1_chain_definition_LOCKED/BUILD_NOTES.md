# Drone Confidence Score — v2.1 LOCKED

Generated: 2026-06-11 08:57 UTC
Version: 2.1.0 (LOCKED)

## What this is

Tier 1 chain definition bundle for drone — the canonical scoring source of truth for PPK-workflow drone surveys. All Q-DRONE-1 through Q-DRONE-5 locks applied during the v2.1 lock session.

Third chain to fully implement the three-tier multi-view + library pattern (after base_station v2.1 and GCP v2.1). With drone locked, the **capture trio (drone → base_station → GCP) is now complete**.

Largest chain to date: 4 blocks, 21 indicators (after Q-DRONE-1 dedup), 3 chain-level hard gates, 1 null-supported indicator.

## Three-tier artifacts for drone v2.1

- **Tier 1 (this bundle):** `drone_confidence_score_v2_1_LOCKED.json` — canonical scoring
- **Tier 2 (separate):** `drone_indicator_library_v2_1.json` — layered customer-voice text
- **Tier 3 (separate):** `drone_multi_view_v1_LOCKED.html` — five-tab reference implementation

See `cbmi_chain_library_pattern.md` for the propagation blueprint.

## What's different about drone vs base_station and GCP

**Largest chain so far.** 4 blocks (vs 3 for base_station and GCP). 21 indicators after Q-DRONE-1 dedup (vs 11 for base_station, 10 for GCP).

**Three chain-level hard gates.** First chain with multiple chain-level hard gates. Any of three indicators failing fires the chain-level hard gate:
- L3I_IMG_001 image_validity_score == 0 → CRITICAL_IMAGE_FAILURE
- L3I_GNSS_001 rover_coverage_score == 0 → RINEX_CRITICAL_GAP
- L3I_FC_001 mission_coverage_score == 0 → COVERAGE_GAP

global_gate_condition lists all three with OR.

**Indicator-level null pattern (NEW).** First chain to introduce indicator-level null handling. When the Open-Meteo wind API is unavailable, L3I_FC_005 returns null (not fabricated score). Block aggregation renormalizes across measured indicators only. Distinct from GCP's chain-level null (NO_DESIGNATED_GCPS) — drone's null is per-indicator within a chain that continues to score.

**External API dependency.** First chain depending on external infrastructure (Open-Meteo). Q-DRONE-4 introduces principled handling: don't fabricate, return null, surface infrastructure limitation explicitly.

**ArduPilot .BIN log parsing.** Several indicators (mission_completion, wind_condition, altitude_consistency) depend on parsing binary flight controller logs. Implementation prerequisite noted.

## Architecture

- **4 blocks**: Image Capture Quality 0.40 / Rover GNSS Quality 0.30 / Mission Execution Quality 0.20 / Camera Calibration Confidence 0.10
- **21 indicators** across the 4 blocks (was 23; Q-DRONE-1 removed duplicate calibration indicators from BB_IMG_CAPTURE)
- **3 chain-level hard gates** (Q-DRONE-2):
  - L3I_IMG_001 image_validity_score == 0 (existing)
  - L3I_GNSS_001 rover_coverage_score == 0 (promoted)
  - L3I_FC_001 mission_coverage_score == 0 (promoted)
- **1 null-supported indicator** (Q-DRONE-4):
  - L3I_FC_005 wind_condition_score returns null when Open-Meteo API unavailable
- **4-severity vocabulary** at indicator-band level: none / minor / material / critical
- **3-recommendation vocabulary** at chain level: good_to_go / review_recommended / resurvey_recommended

## Q-DRONE locks applied

| Q | Decision |
|---|---|
| Q-DRONE-1 | Deduplicate calibration indicators — removed L3I_IMG_006/007 from BB_IMG_CAPTURE; retained L3I_CAL_001/002 in BB_CAL_CONF. BB_IMG_CAPTURE renormalized to clean fractions (0.34/0.27/0.18/0.12/0.09). |
| Q-DRONE-2 | 3 chain-level hard gates promoted: image_validity (existing), rover_coverage (new), mission_coverage (new). Multiple critical-path indicators independently fire chain-level hard gates. |
| Q-DRONE-3 | BB_CAL_CONF retained as 4th block, weight 0.10. Calibration is operationally distinct from image quality. |
| Q-DRONE-4 | Wind API failure → indicator-level null (NEW pattern). L3I_FC_005 returns null when Open-Meteo API unavailable, NOT fabricated score. Block aggregation renormalizes across measured indicators. |
| Q-DRONE-5 | SELF_CALIBRATED_LENS stays MEDIUM (material). Flagging at review level lets operator decide based on accuracy requirements. |

## Files in this bundle

- `drone_confidence_score_v2_1_LOCKED.json` — master JSON (148 KB)
- `drone_confidence_score_v2_1_LOCKED.xlsx` — Excel workbook (43 KB, 11 sheets)
- `drone_provenance_v2_1_LOCKED.html` — full provenance documentation (28 KB)
- `01_source_files.csv` — 6 source files
- `02_source_fields.csv` — 88 source fields
- `03_derived_fields.csv` — 32 derived fields
- `04_indicators.csv` — 21 indicators
- `05_building_blocks.csv` — 4 blocks
- `06_thresholds.csv` — 92 threshold band rows
- `07_block_composition.csv` — block composition
- `08_drone_score.csv` — block weights
- `08b_score_meta.csv` — score metadata
- `09_flags.csv` — 19 flags

## Pattern doc updates (from drone propagation)

Drone introduces two new pattern concepts not present in earlier chains:

1. **Indicator-level null handling** — distinct from chain-level null (GCP). When a specific indicator's input is unavailable, that indicator returns null and block aggregation renormalizes across measured indicators. Library schema extended with `null_band_supported: true` indicator flag and band entries with `score_range: [null, null]` and `level: "null"`.

2. **Multiple chain-level hard gates** — first chain with 3 critical-path indicators. global_gate_condition lists all gates with OR. Rendering layer attributes hard-gate firing to the specific gate(s) that fired.

Both should be added to the pattern doc. Recommend doing the update after locking 1 more chain (probably check_point or pre_processing) so the doc absorbs multiple chain learnings at once.

## Library text status

CLAUDE-DRAFTED PLACEHOLDER. Per pattern Q1 (structure-first propagation), library text is placeholder pending domain refinement. Same status as base_station v2.1 and GCP v2.1. Library-wide text refinement pass will happen after all chains have Tier 2 libraries built.

## Known limitations

1. Indicator thresholds are first-principles calibrated, not yet empirically validated.
2. Wind condition depends on Open-Meteo API (external dependency) — Q-DRONE-4 introduces null-band handling for API failures.
3. Self-calibrated lens (Q-DRONE-5): operator must decide acceptability based on accuracy requirements.
4. Camera calibration match relies on string comparison — does not validate calibration parameters.
5. Mission completion / interrupted detection depends on ArduPilot .BIN log parsing (implementation prerequisite).
6. Q-DRONE-4 indicator-level null pattern is new — composite scoring downstream must respect indicator-null vs zero.
7. Library text is Claude-drafted placeholder.

## Status of the CBMI chain library (post-drone)

| Chain | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| **drone** | **LOCKED v2.1** | **LOCKED v2.1** | **LOCKED v1** |
| base_station | LOCKED v2.1 | LOCKED v2.1 | LOCKED v3 |
| gcp | LOCKED v2.1 | LOCKED v2.1 | LOCKED v1 |
| check_point | locked v1 | — | — |
| pre_processing | locked v1.1 | — | — |
| processing | locked v1.1 | — | — |
| stockpile_analytics | locked v1.0 | — | — |
| pit_analytics | locked v1.0 | — | — |
| wd_analytics | locked v1.0 | — | — |
| cf_analytics | locked v1.0 | — | — |

**3 of 10 chains fully patterned. 7 to go.**

**Capture trio (drone + base_station + GCP) is now COMPLETE.** Three chains, three Tier-1/Tier-2/Tier-3 sets, three different scoring patterns (single-survey + per-point + per-survey with indicator-level null).
