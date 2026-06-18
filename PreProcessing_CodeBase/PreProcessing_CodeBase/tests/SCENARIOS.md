# Smoke-Test Scenario Design & Rationale

How the 63 scenarios in `scripts/test_scenarios.py` were derived, what each one
changes and why, how it moves `pre_processing_score`, and whether the result
matched expectation. Numbers are from the committed `tests/scenarios/_summary.json`
(the authoritative run). A machine-readable companion is
`tests/scenarios/_scenario_analysis.csv`.

## A. Design principles

1. **Baseline-anchored, single-variable isolation.** Every scenario deep-copies
   the gold-standard baseline (`pre_processing_score = 100.0`, `VERIFIED`) and
   applies the *smallest* mutation that targets one thing, so the whole score
   delta is attributable to that change. (A few are intentionally multi-variable
   — the "coupled" and "stress" cases, flagged in §E.)
2. **Two-pass coverage.**
   - **Pass 1 (34) — spec-internal band-walk:** baseline, each of the 3 global
     gates, one scenario per threshold band of each indicator across the 4
     blocks, each `verification_status` state, and an all-flags stress case.
   - **Pass 2 (29) — Problems-sheet-driven + flag-family completion:** one mutator
     per concrete number in `CBMI_PreProcessing_Problems_v1.0.xlsx`, plus the
     families Pass 1 can't reach without changing survey *mode*: CUSTOMER_SUPPLIED
     path, report-present, CORS path, and the CP bands.
3. **Each change lands a derived field in a specific threshold band**, forcing a
   known indicator to a known score and firing a known flag.
4. **Expected score computed analytically, then captured-and-pinned.** Each
   expected apex was hand-computed (formula below), confirmed against a capture
   run, and pinned as the EXPECT invariant. The harness exits non-zero on any
   later score/flag drift — that is what makes it self-validating.

## B. How a change moves the score

For a single-indicator drop:

```
apex = 100 − W_block × w_indicator × (100 − S_indicator) / A_block
```

| term | meaning | values |
|---|---|---|
| `W_block` | block weight in apex | REF 0.35 · GEO 0.30 · GCT 0.25 · SD 0.10 |
| `w_indicator` | indicator weight within its block | from spec |
| `S_indicator` | band score the indicator fell to | 0 / 30 / 50 / 60 / 70 / 80 / 88 |
| `A_block` | **active** block weight after N/A redistribution | REF 0.93 · GEO 0.97 · GCT 0.70 · SD 1.0 |

`A_block < 1` because on the baseline (LOCAL_BASE_PPK, no report) some indicators
are N/A and redistribute their weight onto the rest — so a surviving indicator
carries more than its nominal weight. This is why a GCT defect bites hardest
(A=0.70) and an SD defect is "clean" (A=1.0).

**Three outcome classes — the score only ever moves three ways:**
- **Decrease** (a scored defect) — always down from 100, never up.
- **→ 0** (a global gate: wrong CRS / wrong projection / GCP autonomous).
- **Held at 100** (a check-point problem) — the 4 CP indicators are `view_only`
  (not in any apex block), so they change `verification_status` but cannot touch
  the apex. This is the headline design property.

Worked example (`geoid_mismatch`): REF, w=0.20, band 0, A=0.93 →
`100 − 0.35·0.20·100/0.93 = 92.5`. ✔

## C. Pass 1 — spec-internal (34)

Baseline: CRS WGS84, geoid EGM2008, units m, height orthometric, projection
UTM 43N, gcp-path LOCAL_BASE_PPK, 12 FIXED geotags (=12 captured), baseline 2.5 km,
base window 08:30–10:30 ⊇ flight 09:05–09:55, 16 GCPs σ 0.008 / 20 CPs σ 0.010
(target 0.02), 9 km² site (20 px target), report absent.

### Global gates (→ 0)
| Scenario | Mutation | Why | Apex | Flag |
|---|---|---|---|---|
| wrong_crs_datum | project_required_crs → NAD83(2011) | crs_match=False → REF gate → global gate | 0.0 | WRONG_CRS_DATUM |
| wrong_projection | declared_projection → UTM 44N | declared zone ≠ geotag-lon zone (43) | 0.0 | WRONG_PROJECTION |
| gcp_autonomous | declared_path_gcp → AUTONOMOUS | gcp_path_acceptable=False → GCT gate | 0.0 | GCP_AUTONOMOUS_PATH |

### Reference Frame (W 0.35, A 0.93)
| Scenario | Mutation | Why (band→score) | Apex (Δ) | Flag |
|---|---|---|---|---|
| geoid_mismatch | project_required_geoid → GEOID18 | geoid≠project → 0 (w0.20) | 92.5 (−7.5) | GEOID_MISMATCH |
| height_inconsistent | declared_height_mode[gcp] → ellipsoidal | mixed → 30 (w0.15) | 96.0 (−4.0) | HEIGHT_MODE_INCONSISTENT |
| output_crs_mismatch | all crs_in_exif → NAD83 | artifact≠declared → 0 (w0.10) | 96.2 (−3.8) | OUTPUT_CRS_MISMATCH |
| units_mismatch | project_required_units → US Survey ft | units≠project → 0 (w0.05) | 98.1 (−1.9) | UNITS_MISMATCH |
| localization_undisclosed | localization_applied_declared → null | not disclosed → 60 (w0.02) | 99.7 (−0.3) | LOCALIZATION_UNDISCLOSED |
| provenance_mixed | realization_epoch[gcp] → ITRF2014@2020 | mixed realizations → 50 (w0.01) | 99.8 (−0.2) | MIXED_PROVENANCE |

### Geotag Integrity (W 0.30, A 0.97)
| Scenario | Mutation | Why (band→score) | Apex (Δ) | Flag |
|---|---|---|---|---|
| wrong_base_paired | base_file_id → null | base pairing fails → 0 (w0.25) | 92.3 (−7.7) | WRONG_BASE_PAIRED |
| geotag_not_fixed | 8/12 → FLOAT (0.33) | <0.50 → 0 (w0.20) | 93.8 (−6.2) | GEOTAG_NOT_FIXED |
| geotag_poor_fix | 5/12 → FLOAT (0.58) | 0.50–0.80 → 30 | 95.7 (−4.3) | GEOTAG_POOR_FIX |
| geotag_partial_fix | 1/12 → FLOAT (0.92) | 0.80–0.95 → 70 | 98.1 (−1.9) | GEOTAG_PARTIAL_FIX |
| geotags_incomplete | captured 12 → 15 (0.80) | 0.80–0.95 → 50 (w0.15) | 97.7 (−2.3) | GEOTAGS_INCOMPLETE |
| sparse_tiepoints | overlap 80/70 → 60/50 | <65/<55 → 40 (w0.08) | 98.5 (−1.5) | SPARSE_TIEPOINTS_RISK |
| long_baseline | baseline 2.5 → 15 km | 10–20 km → 70 (w0.10) | 99.1 (−0.9) | LONG_BASELINE |
| sensor_mismatch | per_image[0] camera_serial → OTHER-SN | 2 distinct serials → 50 (w0.03) | 99.5 (−0.5) | SENSOR_METADATA_MISMATCH |
| monsoon_conditions | flight_conditions → monsoon | adverse advisory → 70 (w0.01) | 99.9 (−0.1) | FLIGHT_CONDITION_RISK |
| insufficient_overlap | base_session_start → 09:30 | base no longer covers flight → base_pairing=0 AND overlap=30 (coupled) | 90.1 (−9.9) | WRONG_BASE_PAIRED + INSUFFICIENT_OVERLAP |

### GCP Coord Trust (W 0.25, A 0.70 — hits hardest per unit)
| Scenario | Mutation | Why (band→score) | Apex (Δ) | Flag |
|---|---|---|---|---|
| gcp_sigma_reject | per_gcp[0] σ 0.008 → 0.15 (7.5×) | >5× → GCP 0 → aggregate 68.8 | 96.7 (−3.3) | GCP_SIGMA_REJECT |
| gcp_sigma_high | σ → 0.05 (2.5×) | 2–5× → GCP 30 → aggregate 78.1 | 97.7 (−2.3) | GCP_SIGMA_HIGH |
| gcp_sigma_marginal | σ → 0.03 (1.5×) | 1–2× → GCP 70 → aggregate 90.6 | 99.0 (−1.0) | GCP_SIGMA_MARGINAL |
| coord_misparse | per_gcp[0] easting/northing swapped | outside site polygon → 0 (w0.05) | 98.2 (−1.8) | COORD_MISPARSE |
| gcp_id_partial | per_gcp[1] id → "GCP01" (dup) | minor id issue → 70 (w0.10) | 98.9 (−1.1) | GCP_ID_PARTIAL_MISMATCH |

The three σ rows demonstrate the `mean − 0.25·(100−min)` aggregation: one bad GCP
of 16 pulls the aggregate down by `0.25·(100−min)`.

### Survey Design (W 0.10, A 1.0)
| Scenario | Mutation | Why (band→score) | Apex (Δ) | Flag |
|---|---|---|---|---|
| undersized_network | reconstruction_extent_m2 9 → 30 km² | 16 < required(34) → 30 (w0.40). Only count flag (count uses extent_m2, distribution uses polygon → decoupled) | 97.2 (−2.8) | GCP_COUNT_INSUFFICIENT |
| target_invisible | target/gsd 50/2.5 → 5/5 (1 px) | <2 px → 30 (w0.15) | 99.0 (−1.0) | TARGET_INVISIBLE |
| target_marginal | → 12/5 (2.4 px) | 2–3 px → 60 | 99.4 (−0.6) | TARGET_MARGINAL |
| vegetation_dtm | site_cover open → vegetated | veg+DTM advisory → 30 (w0.05) | 99.7 (−0.3) | VEG_DTM_UNRELIABLE |

### verification_status — apex HELD at 100
| Scenario | Mutation | Apex | verification_status / Flags |
|---|---|---|---|
| no_check_points | per_cp → [] | 100.0 | UNVERIFIED_NO_CPS · NO_INDEPENDENT_VERIFICATION + NO_CHECK_POINTS |
| insufficient_cps | per_cp → first 3 | 100.0 | UNVERIFIED_INSUFFICIENT_CPS · CP_COUNT_INSUFFICIENT + CP_SEVERELY_CLUSTERED |
| cp_clustered | CPs collapsed into one corner | 100.0 | UNVERIFIED_CP_CLUSTERED · CP_SEVERELY_CLUSTERED |
| cp_not_independent | CPs ~3 m from GCPs | 100.0 | UNVERIFIED_CP_NOT_INDEPENDENT · CP_GCP_OVERLAPPING |

### Stress
| Scenario | Mutation | Apex (Δ) | Flags |
|---|---|---|---|
| all_flags_stress | geoid + 15 km baseline + 1 GCP @1.5× + 2.4 px target + monsoon | 89.9 (−10.1) | GEOID + LONG_BASELINE + FLIGHT_RISK + GCP_SIGMA_MARGINAL + TARGET_MARGINAL |

## D. Pass 2 — Problems-sheet + flag-family completion (29)

### Sheet numbers + remaining bands
| Scenario (sheet #) | Mutation | Why (band→score) | Apex (Δ) | Flag |
|---|---|---|---|---|
| p2_excessive_baseline_35km (#11) | baseline → 35 km | 20–40 km → 40 | 98.1 (−1.9) | EXCESSIVE_BASELINE |
| p2_geotags_severely_incomplete (#10) | captured → 20 (0.60) | <0.80 → 0 | 95.3 (−4.7) | GEOTAGS_SEVERELY_INCOMPLETE |
| p2_height_mode_wrong (#3) | all height → ellipsoidal | consistent but wrong → 0 (vs inconsistent→30) | 94.4 (−5.6) | HEIGHT_MODE_WRONG |
| p2_output_crs_missing (#6) | all crs_in_exif → null | metadata absent → 50 (vs mismatch→0) | 98.1 (−1.9) | OUTPUT_CRS_MISSING |
| p2_gcp_id_major (#19) | per_gcp[0] id → "" | major id failure → 30 | 97.5 (−2.5) | GCP_ID_MISMATCH |
| p2_gcp_count_marginal (#27) | extent_m2 → 18 km² | 16 marginal → 70 | 98.8 (−1.2) | GCP_COUNT_MARGINAL |
| p2_partial_overlap (#13) | base_session_end → 09:50 | overlap 0.90 → base_pairing=0 + overlap=50 (coupled) | 90.7 (−9.3) | WRONG_BASE_PAIRED + PARTIAL_BASE_OVERLAP |
| p2_gcp_clustered (#28) | site polygon side → 3394 m | GCP hull ~70% → 60; same polygon clusters CPs too | 98.6 (−1.4) | GCP_CLUSTERED + CP_CLUSTERED + NO_INDEP_VERIF |
| p2_gcp_severely_clustered (#28) | polygon side → 4500 m | hull ~40% → 30 | 97.5 (−2.5) | GCP_SEVERELY_CLUSTERED + CP_SEVERELY_CLUSTERED + NO_INDEP_VERIF |

### CUSTOMER_SUPPLIED path (activates indicators 7/23/25)
| Scenario (sheet #) | Mutation | Why | Apex (Δ) | Flag |
|---|---|---|---|---|
| p2_customer_no_crs (#4) | path=CUSTOMER, crs=null | no CRS declared → 30 | 98.7 (−1.3) | CUSTOMER_COORDS_NO_CRS |
| p2_customer_wrong_crs (#4) | crs=NAD83 | declared ≠ project → 0 | 98.2 (−1.8) | CUSTOMER_COORDS_WRONG_CRS |
| p2_customer_inadequate (#18) | claim=0.05 m | >0.02 target → 30 | 97.2 (−2.8) | GCP_CUSTOMER_INADEQUATE |
| p2_customer_no_claim (#18) | claim=null | reviewer-blocking → 30 | 97.2 (−2.8) | GCP_CUSTOMER_NO_ACCURACY_CLAIM |
| p2_customer_coords_aged (#21) | coord date → 2023-09-01 (287 d) | 180–365 d → 50 | 98.7 (−1.3) | GCP_COORDS_AGED |
| p2_customer_coords_stale (#21) | coord date → 2022-01-01 (>365 d) | >365 d → 30 | 98.2 (−1.8) | GCP_COORDS_STALE |

### Report-present
| Scenario (sheet #) | Mutation | Why (band→score) | Apex (Δ) | Flag |
|---|---|---|---|---|
| p2_report_settings_mismatch (#36) | report datum → NAD83 | declared ≠ actual → 30 | 99.5 (−0.5) | SETTINGS_DECLARED_ACTUAL_MISMATCH |
| p2_report_tsync_drift (#15) | time_sync max_ms 500 | 100 ms–1 s → 70 | 99.9 (−0.1) | TIME_SYNC_DRIFT |
| p2_report_tsync_severe (#15) | max_ms 2000 | >1 s → 30 | 99.8 (−0.2) | TIME_SYNC_SEVERE |
| p2_report_residual_outliers (#20) | 1 GCP residual over tol | 1–2 → 70 | 99.7 (−0.3) | GCP_RESIDUAL_OUTLIERS |
| p2_report_residual_failures (#20) | 3 residuals over tol | 3+ → 30 | 99.3 (−0.7) | GCP_RESIDUAL_FAILURES |

### CORS path + report
| Scenario (sheet #) | Mutation | Why (band→score) | Apex (Δ) | Flag |
|---|---|---|---|---|
| p2_cors_minor_gap (#12) | path_geotag=CORS, coverage 0.97 | 0.95–1.0 → 70 | 99.8 (−0.2) | CORS_MINOR_GAP |
| p2_cors_major_gap (#12) | coverage 0.90 | <0.95 → 30 | 99.4 (−0.6) | CORS_MAJOR_GAP |
| p2_cors_station_degraded (#22) | path_gcp=CORS, quality degraded | → 50 | 99.7 (−0.3) | CORS_STATION_DEGRADED |
| p2_cors_station_unhealthy (#22) | quality poor | → 0 | 99.3 (−0.7) | CORS_STATION_UNHEALTHY |

### CP bands — apex HELD at 100
| Scenario (sheet #) | Mutation | Apex | verification / Flag |
|---|---|---|---|
| p2_cp_sigma_marginal (#23) | per_cp[0] σ → 0.03 | 100.0 | VERIFIED · CP_SIGMA_MARGINAL |
| p2_cp_sigma_high (#23) | σ → 0.05 | 100.0 | VERIFIED · CP_SIGMA_HIGH |
| p2_cp_sigma_reject (#23) | σ → 0.15 | 100.0 | VERIFIED · CP_SIGMA_REJECT |
| p2_cp_count_weak (#29) | per_cp → 6 spread | 100.0 | UNVERIFIED_CP_CLUSTERED · CP_COUNT_STATISTICAL_WEAK + CP_CLUSTERED |
| p2_cp_too_close (#25) | per_cp[0] 30 m from GCP | 100.0 | UNVERIFIED_CP_NOT_INDEPENDENT · CP_GCP_TOO_CLOSE |

## E. Direction, couplings, and alignment

**Direction** (tally from `_scenario_analysis.csv`): **50** scenarios decreased
from 100 (scored defects — never an increase; the baseline is the ceiling),
**3** went to 0 (global gates), and **10** held at 100 (the gold-standard baseline
plus the 9 check-point scenarios, proving apex immunity to check-point quality).
Magnitude is fully explained by the §B formula: Δ ∝ block weight × indicator
weight × band severity ÷ active block weight.

**Alignment: 0 deviations.** Every actual score equalled the hand-computed value
(±0.05) and every flag set matched exactly — which is why the self-validating
harness exits 0. Four outcomes look surprising but are correct by design:

| Outcome | What happened | Why it's correct |
|---|---|---|
| Coupled flags (insufficient_overlap, p2_partial_overlap) | two GEO flags each | base coverage and session overlap read the same time windows; if the base doesn't cover the flight, both legitimately fail. |
| p2_gcp_clustered shows UNVERIFIED_CP_CLUSTERED | enlarging the polygon clustered GCPs and CPs | both distributions measure against the one reconstruction polygon. The apex drop is from the GCP (scored) side only; the CP side only flips verification_status. |
| undersized_network fired only the count flag | expected count+clustering to couple | they don't: count uses extent_m2, distribution uses the polygon — changing only the area isolates count. |
| CP scenarios held apex at 100 | CP mutations changed nothing in the apex | the 4 CP indicators are view_only (excluded from apex blocks). Intended and verified. |

The one real bug found during the build was *not* a scenario deviation: a 4-entry
Stage-3b dispatch mismatch, caught by the dispatch-correctness audit and fixed
*before* the EXPECT values were pinned (so the pins encode the corrected behaviour).

**Coverage:** 63 / 67 flags exercised. The 4 unreached (ANTENNA_PCO_MISMATCH,
BUGGY_SOFTWARE_VERSION, the two handoffs) are unreachable in v1 by design — see
`../OPEN_ITEMS.md §3`.

## F. Regenerating / self-validation

```bash
python3 scripts/test_scenarios.py paths.json   # exit 0 == all 63 match their pinned EXPECT
```
A score or flag drift in any scenario makes the harness exit non-zero. The EXPECT
table (in `scripts/test_scenarios.py`) is the durable record of intended results;
`tests/scenarios/_scenario_analysis.csv` is a flat export of this document.
