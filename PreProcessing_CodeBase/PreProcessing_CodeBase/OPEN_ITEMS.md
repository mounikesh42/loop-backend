# Open-Items Log — pre_processing (spec v1.1.0)

Maintained per BUILD_PROMPT_TEMPLATE_v2.md Section 11. Build complete end-to-end;
nothing below is blocking.

## 1. Fully closed (shipped + verified)
- Stages 1 → 3d + the smoke harness, all audited (3a 20/20, 3b 16/16, 3c 16/16,
  3d 16/16, 12a 11/11, 12b ✓).
- 62/62 source fields, 37/37 derived, 38/38 indicators, 4 blocks, 3 per-artifact
  views, apex + `verification_status`.
- 63 self-validating scenarios; **63/67 flags** exercised; 42-problem coverage map.
- Determinism: no data-block timestamps; scenario artifacts byte-stable.

## 2. Deferred by design (v2 backlog — documented in the spec)
- **Processing report parser**: v1 reads a structured JSON report; real **TBC
  PDF/XML** extraction deferred (a PDF report is recorded present-but-unparsed,
  never assumed healthy). Emlid Studio / RTKLIB parsers also v2.
- **Software-version scoring (#35)**: v1 advisory only — `software_version_score`
  always 100; needs CBMI to maintain a known-buggy-versions list (v2).
- **Approach 3** (independent project-type validation vs expected settings): v2.

## 3. Flags unreachable in v1 (by design — documented in the coverage map)
- `PP_ANTENNA_PCO_MISMATCH` (#14) — no device-reported antenna in PP artifacts;
  `antenna_pco_match` is declared-only, so a mismatch is not detectable.
- `PP_BUGGY_SOFTWARE_VERSION` (#35) — no v1 buggy list (see §2).
- `PP_STAGE2_TARGET_DETECTION_FAILURE` (#32) / `PP_STOCKPILE_BOUNDARY_DISPUTE`
  (#39) — handoffs to future processing_score / volume analytics; **registered,
  not raised** (PP has no artifact evidence to detect them).

## 4. Spec-amendment candidates (surfaced during build)
- **L2D_PP_010** `drone_session_within_base_window`: no external "expected
  base_file_id" source → presence check only.
- **L2D_PP_014** `antenna_pco_match`: no device-actual antenna → declared-only.
- **L2D_PP_015** `sensor_metadata_consistent`: manifest has no camera field + no
  flight-log source → EXIF-internal consistency only.
- **L2D_PP_024** `gcp_id_reconciliation`: manifest carries no GCP-id list →
  coord-file internal consistency only.
- **L2D_PP_029** `gcp_count_adequate`: spec gives no numeric ratio → tuneable,
  **resolved with operator** to `4 + 1.0·area_km²` (marginal at 60%).
- **building_blocks[].indicators_within** prose weights are stale vs the
  authoritative `indicators[].weight_in_block` (both sum to 1.0) — cosmetic.
- **Gate prose** "Zeros pre_processing_score via block-of-blocks" is loose; the
  operative mechanism is the explicit force-to-0 at the Stage-3d global gate.
- **Problems sheet v0.2 vs frozen v1.1.0**: #5 localization polarity
  (sheet *applied→70*; spec *undisclosed→60*); #24 `cp_file_format_score` is not
  an indicator in v1.1.0 (spec routes #24 to `verification_status`). Built to the
  **frozen spec**.

## 5. Engineering tuneables (surfaced in `stage*_meta.tuneables`)
- `gcp_count_adequate`: BASE=4, PER_KM2=1.0, MARGINAL_FACTOR=0.6 (operator-confirmed).
- `BBOX_SANITY_MARGIN_M`=50; datum normalizer; UTM-zone projection method.
- All Stage-3b band edges (sigma multipliers, fix/completeness/baseline/overlap/
  target/coverage/CP bands) + `aggregator_k`=0.25 (spec-given).
- `verification_status`: CP_VERIFIED_MIN_COUNT=5, MIN_COVERAGE=0.80,
  MIN_INDEP_M=50, MIN_SIGMA_SCORE=50.

## 6. Placeholder files awaiting real operator data
- `sample_data/pp_manifest.json` (`_status: PLACEHOLDER`)
- `sample_data/gcp_coords.csv`, `sample_data/cp_coords.csv` (`# _status: PLACEHOLDER`)
- `sample_data/geotags/IMG_0001..0012.jpg` (synthetic EXIF; covered by manifest status)

Stage 1 emits `PLACEHOLDER_INPUTS_DETECTED` until these are replaced with a real
survey. The processing report is intentionally absent in the baseline (exercises
the report-absent redistribution path).
