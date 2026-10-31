# Phase 2A — Isolation Forest Model Validation Report
**Date:** 2026-10-03 (revised 2026-10-25)
**Status:** Validation complete. No accuracy/precision/recall exists or can exist for this
model (no ground-truth labels) — this report documents six label-free validation methods
run instead, their results, and what each does and does not prove.

## Revision notes (2026-10-25)

1. **Data provenance corrected.** All mission and telemetry data is simulator-generated
   (confirmed by the Trizlabz senior who supplied it). Earlier wording that called it
   "real" or "real-world" has been corrected.
2. **Baseline composition stated.** The 208-mission baseline is completed missions of one
   mission type (`Mar5`) spanning two calendar days (83 on 2026-03-05, 125 on 2026-03-06).
   It is not a single-day sample.
3. **Injection-test flaws recorded.** See "Limits of the injection tests". The
   `distance_m` "blind spot" is no longer presented as established.

## Data provenance and what it limits

- Every result below describes the model's behavior on **simulator output**. No test here
  involves a physical robot, so none supports a claim about field performance.
- Terms such as errors, battery readings and cancelled missions refer to simulator events.
  Whether the simulator's noise, faults and mission mix resemble a physical fleet is unknown.
- "Anomalous" can only mean unusual relative to the simulator's own behavior.

---

## Why this document exists

Earlier project documentation validated the model through baseline self-scoring and a
manual spot-check of flagged missions. Both are real evidence, but both are weaker than
they initially appeared: self-scoring tests the model against its own training data, and
the manual spot-check is a qualitative judgment call, not a measurement. This report adds
four further tests — seed stability, a contamination mechanism check, a held-out-mission
check, and two rounds of synthetic anomaly injection — run specifically to answer "does
this model actually work," not just "does it run without error."

One correction to prior documentation is recorded here because it was discovered during
this validation work, not before: **`contamination=0.15` has no effect on any score this
project produces.** See Test 2.

---

## Test 1 — Seed stability

**Method:** retrained the Isolation Forest on the same 208-mission baseline with 11
different random seeds (0–9, plus the production seed 42), same `contamination`,
`n_estimators`, and features throughout. Compared the resulting scores and flagged sets
across seeds.

**Result:**
- Mean Spearman rank correlation of scores between seed pairs: **0.965** (1.0 = identical ordering); worst pair: 0.929.
- Mean Jaccard overlap of flagged (Warning+Critical) sets between seed pairs: **0.79**.
- Of the 15 missions flagged by the production model (seed 42), **11 were flagged under
  every one of the 11 seeds tested.** The remaining 4 were flagged in 10, 6, 5, and 3 of
  11 seeds respectively.
- The single highest-scoring mission in the baseline (`736d076b-...`) was labeled
  **Critical in all 11 seeds**, with scores ranging only 0.798–0.820.
- All 9 missions whose label changed across seeds had mean scores within 0.014 of the
  0.55 Normal/Warning cutoff — i.e., every unstable mission was a borderline case, none
  were far-from-threshold missions flipping unpredictably.

**What this proves:** the model's core findings are not an artifact of one lucky random
tree construction. The flagged set is substantially the same regardless of which seed
trains it.

**What this does not prove:** that the flagged missions are genuinely anomalous —
there is no independent ground truth, and because the data is simulator-generated,
"anomalous" can only mean unusual relative to the simulator's own behavior. It also does not
test generalization to unseen data; this is entirely a within-baseline test.

---

## Test 2 — Does `contamination` actually affect the model's output?

**Method:** retrained with `contamination` set to 0.15 (the trained value), `auto`, 0.05,
and 0.30, same seed, same data. Compared `-model.score_samples(X)` output across all four.

**Result:** maximum absolute score difference across all four contamination values:
**0.000e+00.** Identical scores in every case. Confirmed: `contamination` only affects
scikit-learn's `predict()`/`decision_function()` offset, neither of which this project's
scoring pipeline calls (`anomaly_scorer.py` uses `score_samples()` directly with fixed
thresholds).

**Correction to prior documentation:** `project_handoff_v3.md` states contamination was
"anchored to 18% error rate in actual data" as the basis for the model's behavior. That
was the documented *intent*, but it was never actually wired to affect any score. What
decides every label in this project is the fixed thresholds (0.55, 0.75), not
contamination. No rationale for those specific threshold values was found in project
documentation (checked directly — see below).

**Threshold rationale search:** `docs/project_handoff_v3.md` states the 0.55/0.75 band
boundaries as flat values with no surrounding justification — no percentile reference, no
citation to an analysis. `docs/project_review_2026-08-11.md` does not mention either value
at all. **Conclusion: the thresholds are an undocumented judgment call**, not a derived or
validated choice. This does not invalidate them — Test 1 shows they produce stable,
sensible labels in practice — but "stable and sensible in practice" is different from
"statistically derived," and the project should be described accordingly.

---

## Test 3 — Held-out missions (missions never seen during training)

**Method:** the Isolation Forest scores 234 Mar5-pattern missions in production but was
only trained on 208 of them. Loaded the actual trained `model.pkl`/`scaler.pkl` (no
retraining) and scored the other 26 to see how the deployed model treats missions it has
never seen.

**Result:** held-out missions scored far higher on average than the baseline scores
itself (mean 0.70 vs. 0.44; 92% landed Warning/Critical vs. 7% for the baseline;
Mann-Whitney U test p < 0.0001). Investigating further: 20 of the 26 held-out missions
had non-zero error features, meaning they were excluded from the baseline for a known,
expected reason (the baseline was built as the zero-error-feature subset) — scoring them
higher is unsurprising, not strong evidence of anything.

The remaining 6 held-out missions had zero error features, identical to the baseline's
selection criterion by that measure alone, yet were still excluded. Investigating why
found the real reason: **all 6 have `status = cancelled`**, versus the baseline's
constant `status = completed`. Their `waypoint_completion_rate` was 0.0 or 0.33 against
the baseline's constant 1.0; several had zero distance traveled and zero speed. The
baseline's actual selection rule was "completed missions with zero error features," not
error features alone.

**What this proves:** the model correctly scores aborted, incomplete missions as highly
anomalous compared to a baseline of fully-completed ones. A reasonable and expected
result, not a surprising one.

**What this does not prove:** that the model generalizes well to genuinely *normal*,
unseen data. No such held-out missions existed to test against — every one of the 26 was
either non-clean by error features or non-clean by completion status. This question
remains open and is not resolvable with the data currently available.

---

## Test 4 — Synthetic anomaly injection (univariate)

**Method:** using the deployed `model.pkl`/`scaler.pkl`, took 50 randomly sampled baseline
missions (seed=7) and corrupted one of the 7 model features at a time, by 3/5/8 standard
deviations (using the scaler's own mean/std), in both directions. Measured what fraction
were pushed into Warning/Critical — a real detection rate, since the injected anomaly is
known by construction.

**Result:**
- **Mean detection rate across all features/magnitudes/directions: 32.3%.**
- Strongly asymmetric by direction — e.g., `distance_m` decreasing: 90% detected;
  `distance_m` increasing: 16% detected, same magnitudes.
- Detection rates were *identical* at 3, 5, and 8 SD for every feature/direction except
  `hour_of_day` decreasing (32% / 38% / 38%) — investigated further in Test 5.
- Weakest combinations (near the baseline's own 12% false-positive floor):
  `hour_of_day` increasing (6%), `distance_efficiency` increasing (14%),
  `pct_time_moving` increasing (14%).

**Caveat (added 2026-10-25):** the 32.3% figure, the magnitudes and the direction
asymmetries are affected by design flaws in this test; see "Limits of the injection
tests". Treat the numbers as indicative only.

**What this proves:** on isolated, single-feature corruption, the model's detection is
real but uneven — strong in some feature/direction combinations, close to indistinguishable
from noise in others.

**What this does not prove (without Test 5):** whether 32.3% is a meaningful floor, or an
artifact of testing magnitudes that were already past the point where the model's
sensitivity saturates.

---

## Test 5 — Sensitivity sweep and multivariate injection

**Method, Part A:** repeated the injection on `distance_m` (the most sensitive feature
from Test 4) at finer magnitudes — 0.25, 0.5, 1, 1.5, 2, 3, 5, 8 SD — to find where
detection actually changes, rather than jumping straight to large values.

**Result, Part A:** for `distance_m` decreasing, detection rose gradually and
genuinely — 12% → 28% → 44% → 72% → 90% — before flattening from 3 SD onward. This
is consistent with saturation: the model becomes more sensitive as corruption grows, then
plateaus once a point is reliably isolated in the first split. For `distance_m`
increasing, detection never rose at all — 14% → 16% → 16%, flat from 0.25 SD through
8 SD, sitting at the same level as the baseline's own false-positive rate throughout.
**This is not established as a model weakness.** Production preprocessing caps
`distance_m` at 13.96, but this test injected values far above the cap, and the scaler SD
used (2.96) is over three times the baseline's own (0.92). At the smallest shifts
(0.25-1 SD, mostly within the cap) detection also stayed at 14-16%, which is suggestive,
but the test needs a re-run with the production caps applied and baseline-scale
magnitudes before this is called a blind spot.

**Method, Part B:** compared single-feature injection (`avg_speed_ms` decreased,
`duration_efficiency` increased, each alone) against injecting both simultaneously — the
exact pattern the manual spot-check found in the flagged missions — at matched
magnitudes (1, 2, 3, 5 SD).

**Result, Part B:**

| Magnitude (SD) | Speed only | Duration only | Combined |
|---|---|---|---|
| 1 | 16.0% | 40.0% | **56.0%** |
| 2 | 28.0% | 40.0% | **58.0%** |
| 3 | 28.0% | 40.0% | **58.0%** |
| 5 | 28.0% | 40.0% | **58.0%** |

Combining two individually-weak-to-moderate signals produced detection meaningfully
above either one alone, at every magnitude tested.

**Note (added 2026-10-25):** at 3 and 5 SD the speed shift pushes `avg_speed_ms` below
zero for nearly all missions, so only the 1 and 2 SD rows are physically plausible. The
conclusion rests on those rows (combined 56-58% vs 16-28% and 40% for the single
features).

**What this proves:** the model's real operating strength is multivariate — it is
substantially better at catching realistic, multi-feature anomalies than the univariate
test in isolation suggested, and this specifically matches the pattern found in actual
flagged missions during the earlier manual spot-check. The weak-looking 32.3% univariate
figure from Test 4 likely understates the model's practical performance on the kind of anomaly
it was actually built to catch.

**What this does not resolve:** the `distance_m`-increasing result from Part A is
unconfirmed (see the correction above) and is neither explained nor ruled out by this
result.

---

## Limits of the injection tests (added 2026-10-25)

Tests 4 and 5 have design flaws found after the original write-up. They do not affect
Tests 1-3.

1. **Magnitudes were set in the wrong units.** Shifts were expressed in the scaler's
   standard deviation, which was fit on 292 missions (including non-baseline ones) and is
   much wider than the 208-mission baseline's own spread for several features, e.g.
   `pct_time_moving` (scaler SD 25.9 vs baseline SD 4.7) and `distance_m` (2.96 vs 0.92).
   "3 SD" therefore meant many baseline standard deviations for those features.
2. **Some injected values were impossible.** `pct_time_moving` above 100% for most
   missions at +1 SD and for all at +2 SD or more; `avg_speed_ms` below zero for nearly
   all missions at -3 SD or more (and a few at -2 SD); negative `distance_m` for some
   missions at -3 SD or more; `hour_of_day` above 24 at large magnitudes.
3. **Production caps were not applied.** `feature_extractor.py` caps `distance_m` at 13.96
   and `duration_efficiency` at 14.48 before scoring. The injection scripts scored the
   shifted values directly, so the model was shown inputs it would never receive in use.
4. **Small, fixed design.** One sample of 50 baseline missions (seed 7) and magnitudes
   chosen by judgment.

**What still stands:** Tests 1-3, and the direction of the combined-vs-single comparison
in Test 5 Part B (both arms used the same method, and the 1 and 2 SD rows are plausible).
**What does not:** the 32.3% figure, the per-direction asymmetries (including the
`distance_m` increasing result), and the magnitude labels.

**Unverified hypothesis for the identical results at 3, 5 and 8 SD:** Isolation Forest
chooses split points inside the training data's range, so any value beyond that range may
be treated the same however far out it is. This was not tested.

**Fix needed:** re-run with magnitudes in baseline standard deviations, values clipped to
physical bounds, and the production caps applied.

---

## Overall verdict

| Question | Answer |
|---|---|
| Is the model random / unstable? | No — Test 1 shows its core flags are stable across 11 independent retrainings. |
| Does `contamination=0.15` do anything? | No — Test 2 proves it has zero effect on any score this project produces. The real decision boundary is the undocumented 0.55/0.75 thresholds. |
| Does it generalize to unseen *normal* data? | Unknown — Test 3 found no genuinely comparable held-out normal missions to test against. Still an open question. |
| Does it detect injected anomalies? | Mixed — Test 4 found 32.3% mean univariate detection with strong directional asymmetry, but the test design (see "Limits of the injection tests") makes the absolute rates and asymmetries unreliable; a possible blind spot for `distance_m` increasing is unconfirmed. |
| Does it perform better on realistic, multi-feature anomalies? | Yes — Test 5 Part B shows meaningfully higher detection when combining features in the pattern the flagged missions show. |
| Does it work on data from physical robots? | Untested — all data is simulator-generated. |

**This project does not have, and cannot produce, an accuracy, precision, or recall
number** — there are no ground-truth anomaly labels for any mission, and no amount of
further testing changes that. What it has instead is six independent, label-free checks,
each measuring a different aspect of model behavior, with results that are specific,
numeric, and reproducible from the scripts in `anomaly_pipeline/` and `analysis/`.

The honest summary, if asked to state it in one line: **the model's core detections are
stable and not random, its most useful trait appears to be that it detects
multi-feature anomalies better than isolated single-feature ones, and it has a possible
blind spot (missions traveling further than expected) that the current injection test
cannot confirm, so it should not yet be treated as a complete detector. All of this was
measured on simulator-generated data only.**

## Known limitations carried into this report

- No ground-truth anomaly labels exist or can be obtained from this dataset.
- The 0.55/0.75 label thresholds have no documented derivation.
- Generalization to genuinely novel, normal data remains untested — no suitable held-out
  sample exists in current data.
- All data is simulator-generated; nothing here has been tested on physical-robot data.
- The baseline is one mission type (`Mar5`) over two calendar days, so it covers a narrow
  range of behavior.
- The injection tests (Tests 4 and 5) have the design flaws listed above; the
  `distance_m`-increasing result and the other directional asymmetries are unconfirmed
  until the tests are re-run.