# Check Point Confidence Score — Recommendations Guide

**Version:** v2.1.0 (LOCKED)  
**Date:** 2026-06-12  
**Chain:** check_point (RTK capture-confidence scoring)

---

## Overview

The check_point confidence score produces a **recommendation** — one of three values — that tells you what to do with the captured RTK check-point data:

| Recommendation | Meaning | Action |
|---|---|---|
| **good_to_go** | RTK capture quality meets specification. Proceed to processing. | Use the data as-is. No re-occupation needed. |
| **review_recommended** | Some quality concerns detected. Review findings; decide whether to accept or re-occupy. | Inspect the specific indicators that fired. Decide case-by-case. |
| **resurvey_recommended** | Critical quality issue. Data does not meet specification. Re-occupy this location. | Plan re-occupation. Fix the root cause (setup, environment, or device). |

This guide explains how the score determines the recommendation, and walks through **real scenarios** that lead to each outcome.

---

## How Recommendations Are Determined

### The Decision Tree

```
Check Point Captured
         ↓
    ┌────────────────────────────────┐
    │ Global Gate: Do ALL points     │
    │ have FLOAT/AUTONOMOUS fix      │
    │ OR catastrophic sigma?         │
    └────────────────────────────────┘
         ↓ YES              ↓ NO
    [score = 0]        Continue
    [HARD GATE]             ↓
         ↓          ┌────────────────────────────────┐
    resurvey        │ Check for CRITICAL findings:   │
    recommended     │ • Position sigma catastrophic  │
                    │ • Antenna height missing       │
                    │ • Fix type permanently FLOAT   │
                    │ • No fix achieved at all       │
                    └────────────────────────────────┘
                         ↓ YES              ↓ NO
                    resurvey          Continue
                    recommended           ↓
                                  ┌────────────────────────────────┐
                                  │ Check for MATERIAL findings:   │
                                  │ • Baseline >50km               │
                                  │ • PDOP >6                      │
                                  │ • Obstruction >30°             │
                                  │ • Cycle slips detected         │
                                  │ • Antenna type mismatch        │
                                  └────────────────────────────────┘
                                       ↓ YES              ↓ NO
                                  review            good_to_go
                                  recommended
```

### Severity Levels (Band-Level)

Each indicator scores into one of **four severity levels**:

| Severity | Meaning | Effect on Recommendation |
|---|---|---|
| **NONE** | Indicator passed with no findings | No impact; contributes to good_to_go |
| **MINOR** | Hygiene signal; audit-only | No impact on recommendation; documented for review |
| **MATERIAL** | Quality concern; warrants review | Triggers review_recommended (unless offset by other good indicators) |
| **CRITICAL** | Quality failure; warrants resurvey | Triggers resurvey_recommended |

---

## Scenario 1: All Points Fixed RTK (good_to_go)

### Situation

You occupied **4 check-points** in good conditions. All achieved **FIXED** RTK with **good position sigma** (≤5cm). Setup was clean: antennas documented, poles stable, clear sky, good PDOP.

### Per-Point Scores

| Point | Sigma | Fix | Baseline | PDOP | Antenna Height | Obstruction | Overall Per-Point |
|---|---|---|---|---|---|---|---|
| CP-001 | 3cm (100) | FIXED (100) | 25km (100) | 2.0 (100) | Documented (95) | Clear (95) | 97 |
| CP-002 | 4cm (95) | FIXED (100) | 28km (100) | 2.2 (100) | Documented (95) | Clear (95) | 97 |
| CP-003 | 3cm (100) | FIXED (100) | 26km (100) | 2.1 (100) | Documented (95) | Clear (95) | 97 |
| CP-004 | 5cm (85) | FIXED (100) | 27km (100) | 2.3 (100) | Documented (95) | Clear (95) | 95 |

### Aggregation (Per-Point Formula)

Each point's score is computed as:
```
per_point_score = 0.45 * completeness + 0.35 * setup + 0.20 * environment
```

For CP-001:
```
0.45 * 100 + 0.35 * 95 + 0.20 * 95 = 45 + 33.25 + 19 = 97.25 → 97
```

### Fleet Aggregation (Mean-Minus-Worst)

```
mean = (97 + 97 + 97 + 95) / 4 = 96.5
worst = 95
check_point_score = 96.5 - 0.25 * (100 - 95) = 96.5 - 1.25 = 95.25 → 95
```

### Findings

- **Global gate:** No (all points FIXED, sigma good)
- **Critical findings:** None
- **Material findings:** None
- **Minor findings:** None (or only audit-level)

### Recommendation

**✓ good_to_go**

**Justification:** Score 95 (Gold tier). All points achieved FIXED RTK. No CRITICAL or MATERIAL findings. Setup and environment were both clean. Proceed to processing without re-occupation.

**Next steps:**
1. Use the four captured points as-is.
2. Pass coordinates to pre_processing subsystem.
3. Document check_point_score=95 in deliverable metadata.

---

## Scenario 2: Mixed Quality — Some Float, Some High Sigma (review_recommended)

### Situation

You occupied **3 check-points**. CP-001 and CP-003 are good. CP-002 achieved **FLOAT** (integer ambiguity not resolved), and CP-004 has high sigma (15cm, borderline). Setup documentation incomplete on CP-002.

### Per-Point Scores

| Point | Sigma | Fix | Baseline | PDOP | Antenna Height | Obstruction | Overall |
|---|---|---|---|---|---|---|---|
| CP-001 | 3cm (100) | FIXED (100) | 25km (100) | 2.0 (100) | Documented (95) | Clear (95) | 97 |
| CP-002 | 8cm (75) | **FLOAT (0)** | 48km (75) | 3.5 (90) | **Not doc'd (0)** | Obstructed (60) | **42** |
| CP-003 | 4cm (95) | FIXED (100) | 26km (100) | 2.1 (100) | Documented (95) | Clear (95) | 97 |
| CP-004 | 15cm (50) | FIXED (100) | 32km (90) | 3.0 (95) | Documented (95) | Slightly obstructed (75) | **75** |

### Fleet Aggregation

```
mean = (97 + 42 + 97 + 75) / 4 = 77.75
worst = 42
check_point_score = 77.75 - 0.25 * (100 - 42) = 77.75 - 14.5 = 63.25 → 63
```

### Findings

- **Global gate:** No (not ALL points failed; 2 of 4 are good)
- **Critical findings (per-point):**
  - **CP-002:** FLOAT fix (per-point gate fires) → point zeroed in aggregation
  - **CP-002:** Antenna height missing (per-point gate fires) → point zeroed
- **Material findings (chain-level):**
  - CP-004 high sigma (15cm, above 5cm target)
  - CP-002 obstruction (reduces PDOP)

### Recommendation

**⚠ review_recommended**

**Justification:** Score 63 (Bronze tier, borderline Marginal). Two of four points have CRITICAL per-point findings (CP-002 is effectively unusable). CP-004 has material findings (high sigma). Recommendation is to review and decide:
- **Option A (Accept):** Use CP-001 and CP-003 as primary points; note CP-004 with caveats; discard CP-002.
- **Option B (Re-occupy):** Re-occupy CP-002 (fix antenna height doc + get FIXED RTK). Re-occupy CP-004 under clearer conditions (lower sigma).

### Next Steps (Your Decision)

**Path A: Accept with notes**
1. Use CP-001, CP-003 as primary check-points (both score ~97).
2. Document CP-004 at score 75 with notation: "higher uncertainty; use with discretion."
3. Discard CP-002 (FLOAT + antenna height missing = unusable).
4. Pass to pre_processing with review notes.
5. Downstream processing will flag that one point was discarded.

**Path B: Re-occupy**
1. Return to site with corrected setup:
   - CP-002: Measure and document antenna height. Allocate 5+ minutes for FIXED RTK convergence.
   - CP-004: Move to location with clearer sky (lower obstruction) or allocate more convergence time.
2. Re-capture both points.
3. Run check_point scoring again on the revised dataset.

---

## Scenario 3: Critical Issue — Global Gate Fires (resurvey_recommended)

### Situation

You occupied **2 check-points** in difficult conditions. CP-001 achieved only **AUTONOMOUS** fix (no corrections received), and CP-002 achieved **FLOAT** (base station too distant). Neither achieved FIXED RTK.

### Per-Point Scores

| Point | Sigma | Fix | Baseline | PDOP | Antenna Height | Obstruction | Overall |
|---|---|---|---|---|---|---|---|
| CP-001 | 500cm (0) | **AUTONOMOUS (0)** | 80km (0) | 5.0 (50) | Documented (95) | Clear (95) | **15** |
| CP-002 | 200cm (0) | **FLOAT (0)** | 75km (0) | 4.5 (60) | Documented (95) | Obstructed (60) | **18** |

### Global Gate Evaluation

```
Does EVERY point have (fix_type == 0) OR (sigma == 0)?
  CP-001: fix_type == 0 (AUTONOMOUS) → YES
  CP-002: fix_type == 0 (FLOAT) → YES
  Result: YES — ALL points failed in this dimension
```

### Recommendation

**✗ resurvey_recommended**

**Justification:** **Global gate FIRED**. Both points have bad fix type (FLOAT/AUTONOMOUS, not FIXED). This means RTK never converged to integer ambiguity; output is at GPS-level accuracy (3-5m), not centimeter-level. The captured data does not meet RTK specification.

### Findings

- **Global gate:** **FIRED** — all points have fix_type = FLOAT/AUTONOMOUS
- **Critical findings:**
  - CP-001 AUTONOMOUS fix (no RTK correction)
  - CP-002 FLOAT fix (ambiguity not resolved)
  - Both points have very high sigma (meters, not cm)

### Root Causes

1. **Base station / NTRIP corrections unavailable** — RTK receiver couldn't get real-time corrections
2. **Baseline too long** — 75-80km is beyond typical RTK range without advanced corrections
3. **Poor satellite geometry** — PDOP 4.5-5.0 indicates suboptimal constellation
4. **Insufficient convergence time** — didn't allocate enough time for FIXED fix to resolve

### Next Steps (Mandatory Re-occupation)

1. **Diagnose the root cause:**
   - Check whether NTRIP service was active at the time
   - Verify base station (or CORS) was within RTK range
   - Check satellite forecast for the time of day you occupied

2. **Plan re-occupation with corrected setup:**
   - Use closer CORS reference (if possible)
   - Allow **5-10 minutes** for RTK convergence at each point
   - Choose time of day with better satellite geometry (if PDOP forecast permits)
   - Verify NTRIP / base station corrections are active before starting

3. **Capture fresh data** with fixed setup, then re-score

4. **Document what went wrong** (for your field procedures)

---

## Scenario 4: Single Bad Point Among Good Points (review_recommended)

### Situation

You occupied **5 check-points**. Four are excellent (FIXED, ≤5cm sigma, clean setup). One point (CP-3) has a problem: **antenna height not documented**, even though fix type and sigma are OK.

### Per-Point Scores

| Point | Sigma | Fix | Baseline | PDOP | **Antenna Height** | Obstruction | Overall |
|---|---|---|---|---|---|---|---|
| CP-001 | 3cm (100) | FIXED (100) | 25km (100) | 2.0 (100) | Documented (95) | Clear (95) | 97 |
| CP-002 | 4cm (95) | FIXED (100) | 26km (100) | 2.1 (100) | Documented (95) | Clear (95) | 97 |
| **CP-003** | **4cm (95)** | **FIXED (100)** | **25km (100)** | **2.0 (100)** | **Missing (0)** | **Clear (95)** | **67** |
| CP-004 | 3cm (100) | FIXED (100) | 27km (100) | 2.3 (100) | Documented (95) | Clear (95) | 97 |
| CP-005 | 5cm (85) | FIXED (100) | 28km (100) | 2.2 (100) | Documented (95) | Clear (95) | 95 |

### Fleet Aggregation

```
mean = (97 + 97 + 67 + 97 + 95) / 5 = 90.6
worst = 67
check_point_score = 90.6 - 0.25 * (100 - 67) = 90.6 - 8.25 = 82.35 → 82
```

### Findings

- **Global gate:** No (points are mostly good)
- **Critical findings:** CP-003 antenna height missing (per-point gate fires for that point)
- **Material findings:** None at chain level
- **Per-point analysis:**
  - CP-001, 002, 004, 005: excellent (all ~95-97)
  - CP-003: **fails antenna height gate** → score 67 (Bronze tier)

### Recommendation

**⚠ review_recommended**

**Justification:** Score 82 (Silver tier). Four of five points are excellent. However, CP-003 has a CRITICAL per-point finding: antenna height missing. The per-point gate fires, zeroing that point's contribution to setup confidence. The aggregation formula penalizes this outlier by approximately 8 points, bringing the overall score from ~91 to ~82.

### Next Steps (Your Decision)

**Option A: Try to recover CP-003**
1. Return to CP-003 location with field photos/notes from original occupation
2. Measure the antenna height retroactively if still marked in the field
3. If height can be recovered: update the data, re-score, likely → **good_to_go**
4. If height cannot be recovered: proceed to Option B

**Option B: Discard CP-003, use 4-point fleet**
1. Use only CP-001, 002, 004, 005 (all ~95-97 scores)
2. Recompute fleet aggregation with 4 points:
   ```
   mean = (97 + 97 + 97 + 95) / 4 = 96.5
   worst = 95
   score = 96.5 - 0.25 * 5 = 94.75 → 95
   ```
3. New score: **95 (Gold tier)** → **good_to_go**
4. Document that CP-003 was unusable due to missing antenna height

**Option C: Accept CP-003 as-is with caveats**
1. Use all 5 points at score 82
2. Pass to pre_processing with notation: "CP-003 antenna height unknown; vertical accuracy reduced for that point"
3. Downstream processing will flag the missing height and may exclude CP-003 from certain analyses

---

## Scenario 5: Environmental Challenge — Poor Geometry (review_recommended)

### Situation

You occupied **3 check-points** in a challenging urban environment (surrounded by buildings). All points achieved **FIXED** RTK with acceptable sigma, but PDOP was elevated (5.5-6.0) due to satellite obstruction. All points documented properly.

### Per-Point Scores

| Point | Sigma | Fix | Baseline | **PDOP** | Antenna Height | **Obstruction** | Overall |
|---|---|---|---|---|---|---|---|
| CP-001 | 6cm (80) | FIXED (100) | 30km (100) | **5.5 (25)** | Documented (95) | **Obstructed (50)** | **74** |
| CP-002 | 7cm (70) | FIXED (100) | 31km (100) | **5.8 (20)** | Documented (95) | **Obstructed (45)** | **70** |
| CP-003 | 5cm (85) | FIXED (100) | 29km (100) | **5.5 (25)** | Documented (95) | **Obstructed (50)** | **75** |

### Fleet Aggregation

```
mean = (74 + 70 + 75) / 3 = 73
worst = 70
check_point_score = 73 - 0.25 * (100 - 70) = 73 - 7.5 = 65.5 → 66
```

### Findings

- **Global gate:** No (all points FIXED with reasonable sigma)
- **Critical findings:** None
- **Material findings:**
  - PDOP 5.5-5.8 (threshold is 3-4 for ideal)
  - Obstruction >30° (buildings blocking sky view)
  - All three points show environmental stress

### Recommendation

**⚠ review_recommended**

**Justification:** Score 66 (Bronze/Marginal tier). All points technically achieved FIXED RTK, so the global gate did NOT fire. However, the **observation environment** (PDOP + obstruction) was poor, which triggered MATERIAL findings on all three points. The combination of environmental challenges degraded the overall score.

### Next Steps (Your Decision)

**Option A: Accept with degraded confidence**
1. All points are FIXED RTK with documented setup → technically usable
2. Document the urban environment constraint: "Captures made in urban canyon with building obstruction; geometry suboptimal but fixed RTK achieved"
3. Flag for downstream processing that these points have elevated uncertainty due to environment
4. Use as-is if project specification allows degraded geometry confidence

**Option B: Re-occupy from different location**
1. If possible, move check-points to open-sky location (parking lot, field, rooftop)
2. Re-capture with PDOP <4 and obstruction <30°
3. Should yield score >85 (Silver tier) → **good_to_go**

**Option C: Accept with longer processing window**
1. Use the three captured points as-is
2. Tell processing subsystem: "These points have geometry-related uncertainty; use them for cross-checking but don't weight them as heavily in the final solution"
3. If you have other high-quality base_station or GCP data, they can anchor the solution

---

## Scenario 6: Ionospheric Storm (Material Finding, review_recommended)

### Situation

You occupied **3 check-points** on a day when the **NOAA Kp index was high (Kp=7, geomagnetic storm)**. Setup was clean, fix was FIXED, but ionospheric disturbance was significant.

### Per-Point Scores

| Point | Sigma | Fix | Baseline | PDOP | Antenna Height | **Ionospheric Risk** | Overall |
|---|---|---|---|---|---|---|---|
| CP-001 | 4cm (95) | FIXED (100) | 26km (100) | 2.2 (100) | Documented (95) | **Kp=7 (30)** | **88** |
| CP-002 | 5cm (85) | FIXED (100) | 25km (100) | 2.0 (100) | Documented (95) | **Kp=7 (30)** | **84** |
| CP-003 | 4cm (95) | FIXED (100) | 27km (100) | 2.1 (100) | Documented (95) | **Kp=7 (30)** | **88** |

### Fleet Aggregation

```
mean = (88 + 84 + 88) / 3 = 86.67
worst = 84
check_point_score = 86.67 - 0.25 * (100 - 84) = 86.67 - 4 = 82.67 → 83
```

### Findings

- **Global gate:** No
- **Critical findings:** None
- **Material findings:** Elevated ionospheric risk on all points (Kp=7)
- **Sigma:** Actually quite good (4-5cm) despite storm
- **Fix type:** All FIXED

### Recommendation

**⚠ review_recommended**

**Justification:** Score 83 (Silver tier). All three points are FIXED with good sigma, which is actually impressive given the geomagnetic storm. However, ionospheric disturbance is documented as a MATERIAL finding because it introduces modeling error. The recommendation is to **review and accept with notation**.

### Next Steps

1. **Document the storm condition:**
   - "Captures made during NOAA Kp=7 geomagnetic storm; ionospheric modeling uncertainty elevated"
   - "Despite storm, RTK achieved FIXED solution with 4-5cm sigma (robust)"

2. **Accept the data as-is** because:
   - All fundamental RTK metrics are good (FIXED fix, good sigma)
   - The ionospheric risk is a modeling concern, not a measurement failure
   - You already occupied the points; re-occupying on a different day is impractical

3. **Pass to downstream processing** with the notation so they know to apply slightly larger error envelopes

---

## Scenario 7: Critical Hardware Failure — No Convergence (resurvey_recommended)

### Situation

You attempted to occupy **2 check-points** but the RTK receiver **never achieved FIXED solution** on either. Both stayed in FLOAT status the entire occupation window. You waited 10+ minutes, but the fix never resolved.

### Per-Point Scores

| Point | Sigma | **Fix** | Baseline | PDOP | Antenna Height | Obstruction | Overall |
|---|---|---|---|---|---|---|---|
| CP-001 | 50cm (40) | **FLOAT (0)** | 35km (85) | 3.5 (90) | Documented (95) | Clear (95) | **60** |
| CP-002 | 60cm (30) | **FLOAT (0)** | 38km (75) | 3.8 (85) | Documented (95) | Clear (95) | **55** |

### Fleet Aggregation

```
mean = (60 + 55) / 2 = 57.5
worst = 55
check_point_score = 57.5 - 0.25 * (100 - 55) = 57.5 - 11.25 = 46.25 → 46
```

### Global Gate Check

```
Does EVERY point have (fix_type == 0) OR (sigma == 0)?
  CP-001: fix_type == 0 (FLOAT) → YES
  CP-002: fix_type == 0 (FLOAT) → YES
  Result: YES — GLOBAL GATE FIRES
```

### Findings

- **Global gate:** **FIRED**
- **Critical findings:**
  - Both points FLOAT (integer ambiguity never resolved)
  - Both points high sigma (meter-level uncertainty)
  - Effective accuracy: GPS-level (~3-5m), not RTK-level (~5cm)

### Recommendation

**✗ resurvey_recommended**

**Justification:** **Global gate FIRED**. Score 46 (Poor tier). Both check-points failed to achieve RTK FIXED solution. The delivered data is at GPS accuracy, not RTK accuracy. This does not meet specification.

### Root Cause Analysis

Likely causes (in priority order):

1. **NTRIP/base corrections not received** — Check whether RTK service subscription was active
2. **Base station too distant** — Baseline 35-38km is marginal for standard RTK; need advanced corrections
3. **Receiver configuration error** — Wrong NTRIP mountpoint, wrong antenna model, wrong device settings
4. **Hardware malfunction** — RTK receiver may have internal failure; try different device
5. **Environmental obstruction** — Despite "clear sky" notation, check for RF interference or blocked signals

### Next Steps (Mandatory Re-occupation)

1. **Diagnose before re-occupying:**
   - Check RTK receiver logs for "no fix" reason (corrections, ambiguity resolution failure, antenna issue)
   - Verify NTRIP subscription is active and credentials correct
   - Test RTK receiver at a known-good location to verify hardware

2. **Re-occupy with corrected setup:**
   - Closer base station or higher-grade NTRIP corrections
   - Longer convergence window (10-15 minutes, not 5 minutes)
   - Verify receiver is logging (indicators show active)
   - Confirm satellite count is >8

3. **Capture fresh data** and re-score

---

## Scenario 8: Null Case — No Check Points Designated (N/A)

### Situation

The survey was designed **without any RTK check-points**. All positions are from GCPs and drone/base station data. No check_point_score is applicable.

### Recommendation

**∅ Not Applicable**

**Justification:** Survey design did not include CHECK_POINT role. The check_point confidence subsystem is not applicable to this survey. Pass to pre_processing with note: "Survey design uses base_station + GCP only; no RTK check-points designated."

### Downstream Handling

- Pre_processing subsystem records: `check_point_score = null`
- Composite capture_score (hypothetical future OJS integration) weights base_station + GCP only; check_point contribution is 0%
- Survey is valid; just a different capture strategy (monument-based rather than RTK-based)

---

## Quick Reference: When to Re-Occupy

### Clear Cases for **resurvey_recommended**

✗ **Global gate fired** (all points FLOAT/AUTONOMOUS OR all sigma catastrophic)
✗ **Critical per-point findings that cannot be recovered** (antenna height missing on all points, no FIXED achieved on any point)
✗ **Score <40 (Poor tier)**

### Ambiguous Cases for **review_recommended** (Your Decision)

⚠ One or two bad points among several good ones → decide whether to discard or re-occupy
⚠ Score 40-74 (Marginal/Bronze) → depends on whether you can re-occupy and project tolerance
⚠ Material findings (PDOP, obstruction, baseline) → accept with notes OR re-occupy for better conditions

### Clear Cases for **good_to_go**

✓ All points FIXED with σ ≤5cm
✓ Setup clean (antenna height documented, poles stable)
✓ Environment favorable (PDOP <4, obstruction <30°, Kp <5)
✓ **Score >80 (Silver tier)**
✓ **No CRITICAL findings**

---

## Summary Table: What Each Indicator Finding Means

| Indicator | Good (NONE) | Minor | Material | Critical | Action When Critical |
|---|---|---|---|---|---|
| **Position Sigma** | ≤5cm | — | 5-15cm | >15cm | Re-occupy with longer convergence time or closer base |
| **Fix Type** | FIXED | — | — | FLOAT / AUTONOMOUS | Re-occupy; check corrections and convergence |
| **Baseline Length** | ≤50km | — | >50km | >100km | Use closer CORS or re-occupy closer to base |
| **PDOP** | <3 | — | 3-6 | >6 | Reschedule to better geometry window |
| **Antenna Height** | Documented | — | — | Missing | Re-measure or re-occupy with documentation |
| **Obstruction** | <30° | — | 30°-60° | >60° | Relocate to open sky or re-occupy |
| **Pole Stability** | Stable | — | Some movement | Unstable | Secure setup and re-occupy |
| **Ionospheric Risk** | Kp <5 | — | Kp 5-7 | Kp >7 + other issues | Accept with notes OR reschedule to quiet period |
| **Fix Hold** | Continuous | No slips | Brief slips | Repeated slips | Diagnose source (multipath, RF) and re-occupy |

---

## Integration with Downstream Workflow

### What Pre-Processing Sees

The pre_processing subsystem receives:
- **check_point_score** (0-100, or null)
- **Recommendation** (good_to_go / review_recommended / resurvey_recommended)
- **Findings list** (all CRITICAL and MATERIAL indicators that fired)
- **Per-point breakdown** (score for each check-point)

### How Pre-Processing Uses It

1. **If good_to_go:** Use all check-points as validation anchors
2. **If review_recommended:** Flag the captured points; let operator decide whether to use or discard
3. **If resurvey_recommended:** Quarantine points; require re-occupation before processing

### What Happens in Final Deliverable

The check_point_score and recommendation appear in:
- QA report (executive summary shows "RTK capture quality: good_to_go")
- Metadata (score 95, 14 flags, 2 MATERIAL findings, etc.)
- Processing notes ("All RTK points passed check_point confidence")

---

## Frequently Asked Questions

### Q: Can I ignore review_recommended and just process?

**A:** Technically yes, but not recommended. A "review_recommended" usually means there's a specific issue (one bad point, high sigma, environmental challenge) that you should make an explicit decision about. You can *accept* the recommendation (use as-is with notes) or *reject* it (re-occupy), but ignoring the signal is poor practice.

### Q: What if my project specification is less strict than the default tiers?

**A:** The check_point score is based on typical survey-grade RTK (5cm sigma, FIXED fix). If your project only requires 10cm accuracy, a score of 70 (Bronze tier, high sigma ~10cm) might be acceptable. Document the spec delta in your project QA notes.

### Q: Can I recover a re-occupy recommendation by just waiting longer?

**A:** Not if the global gate has fired (all points FLOAT). Waiting doesn't resolve integer ambiguity if the corrections aren't good. However, if it's just one point with material findings, and you can re-measure antenna height or move to better sky view, that can help.

### Q: Is the per-point aggregation formula (mean-minus-worst) ever adjusted?

**A:** No. The formula `mean - 0.25 * (100 - worst)` is LOCKED as v2.1 design decision. It won't change unless a major CBMI version update is released.

### Q: Why penalize the worst point instead of just using the mean?

**A:** The worst point reflects real conditions (maybe one location had bad obstruction). A simple mean would hide that. The k=0.25 penalty acknowledges the outlier without over-penalizing (like a worst-case model would). It's a balanced approach.

---

## Contact & Support

For questions about check_point recommendations:
- Review the indicators that triggered findings (see library text)
- Consult your project specification for accuracy tolerance
- Document your decision (accept / re-occupy) in QA notes

For CBMI framework updates or new pattern concepts:
- See cbmi_chain_library_pattern.md
- Reference check_point_indicator_library_v2_1.json for detailed band descriptions

---

**End of Guide**
