# Survey: Automatic Camera Exposure & Gain Control Methods

> Status: **survey complete — promoted to design** (2026-07-25):
> the §6 recommendation has been concretized in
> [mf_auto_exposure_plan_ko.md](mf_auto_exposure_plan_ko.md)
> (existing behavior kept, new method added as a selectable option; ko only).
> Related docs: [docs/ax/camera.md](ax/camera.md) (current exposure-control architecture, canonical owner),
> [docs/ax/camera/CONTEXT.md](ax/camera/CONTEXT.md) (glossary),
> [ADR 0010](adr/0010-zero-match-recovery-single-ladder.md) (zero-match recovery ladder),
> [mf_solve_motion_gate_review_en.md](mf_solve_motion_gate_review_en.md) (motion-during-exposure solve gate)
>
> Purpose: lay out the structural problems of the current **solve-result
> (match-count) driven auto-exposure**, and survey alternative/complementary
> methods as grounds for an implementation decision. This document is a
> survey; the final design will be promoted to a separate plan after
> discussion.

## 1. Current implementation summary

The canonical description lives in [docs/ax/camera.md](ax/camera.md). Only
the skeleton needed for the problem analysis is summarized here.

```
solver process                          camera process (get_image_loop)
  tetra3 solve attempt                    capture frame
    └─ Matches (every attempt) ──────► shared_state.solution()
                                          │  only on new last_solve_attempt
                                          ▼
                            ┌─ match-count controller (default)
                            │    └─ Matches == 0 → zero-match recovery ladder
                            └─ background controller (SQM screen only)
                                          ▼
                               set_camera_config(exposure, gain)
```

- **Match-count controller** (`auto_exposure.py::ExposurePIDController`,
  `python/PiFinder/auto_exposure.py:347`): target `Matches` 17, deadband ±5,
  asymmetric PID (conservative descent / aggressive ascent), clamp
  25 ms–1 s. Steps only when a new solve attempt appears.
- **Zero-match recovery** (`auto_exposure.py::ZeroMatchRecovery`,
  `python/PiFinder/auto_exposure.py:65`): after 2 consecutive Matches=0,
  walks the fixed ladder `[400, 800, 1000, 200] ms`, two tries per rung
  (ADR 0010).
- **Background controller** (`auto_exposure.py::ExposureSNRController`):
  SQM screen only. Holds the frame's 10th-percentile ADU just above the
  noise floor, multiplicative ×1.3/÷1.3 steps.
- **Gain is not part of the feedback loop**: fixed per-sensor profile value
  (`sqm/camera_profiles.py` — imx296 15×, imx462 30×, hq 22×) or manual
  menu selection.
- Wiring: `camera_interface.py:298-380` (solve result → controller →
  `set_camera_config`).

## 2. Problems of the current solve-driven approach

| # | Problem | Structural cause |
| --- | --- | --- |
| P1 | **The feedback signal cannot distinguish causes.** Matches=0 arises equally from too dark/bright, defocus, motion during exposure, clouds/obstruction, and solver-side failure. The recovery ladder can only fix exposure, yet cycles (shaking the exposure) on all the other causes too — a limitation explicitly noted as a gotcha in ax/camera.md §7. | `Matches` is the only input; no image statistics (saturation, background, detected star count) are consulted |
| P2 | **Convergence is tied to the solve cadence — slow.** One adjustment step = one solve attempt (hundreds of ms to 1 s+). One full recovery-ladder cycle from a badly wrong exposure = 8 solve attempts. Fast-changing conditions (twilight, moonlight, right after a slew) outrun it. | Controller runs only when `last_solve_attempt` updates |
| P3 | **Match count is an indirect proxy for exposure.** `Matches` is not the number of detected stars but the number tetra3 paired with the catalog — it varies widely at the same exposure with FOV, local star density, and the pattern database. Where the target of 17 is physically unreachable (sparse fields), exposure gets dragged up to the maximum (1 s), worsening hand-shake blur. | The target is "stars the solver used", not "stars in the frame" |
| P4 | **No saturation / bright-sky guard.** Against a bright background (twilight, moon, light pollution) longer exposure does not increase star contrast, but the controller keeps raising it while matches stay low. No image mean / saturation check exists. | Image statistics unused |
| P5 | **Gain sits outside the control loop.** Only exposure is adjusted, so under dark skies exposure grows until it collides with the motion-blur limit of a hand-moved telescope. There is no gain/exposure role policy. | Gain is profile-fixed / manual |
| P6 | **The detected star count already exists but is unused.** The solver extracts centroids via cedar-detect (`solver.py:282-346`; count at `:539-545`). "N detected / 0 matched" (solver-side problem) vs "0 detected" (exposure/optics problem) is distinguishable, but never reaches AE. | Only the match count is wired into `SolveDiagnostics` |
| P7 | **Frames blurred by motion during exposure can pollute the feedback.** The motion-frame gate is unwired ([mf_solve_motion_gate_review_en.md](mf_solve_motion_gate_review_en.md)), so failures from blurred frames enter AE as CAM_FAILED. | Gate not implemented (under discussion in its own doc) |

## 3. Surveyed methods

### Method A — Detected-star-count servo (cedar-server) ★ most direct precedent

Implemented in [cedar-server](https://github.com/smroid/cedar-server)
(Steven Rosenthal), which uses the same solver stack as PiFinder
(cedar-detect/cedar-solve). The signal is the **number of stars
(centroids) detected by cedar-detect — not the match count** — in a
two-tier design.

**A-1. One-shot calibration** (`server/src/calibrator.rs`):

- Searches for the exposure that yields the target detected star count
  (`star_count_goal`, default **20**).
- Adjustment law: model **detected star count ≈ proportional to
  exposure**. `new_exp = prev_exp / (count / goal)`. Converged within
  0.8–1.2×, at most 3 iterations.
- Rationale: 2.5× exposure ≈ +1 magnitude of limiting depth ≈ ~3× star
  count near mag 5 — the linear approximation is good enough for modest
  excursions.
- Companion calibration: at 1 ms exposure, raise the **black-level
  offset** until fewer than 0.1% of pixels read zero, preventing black
  crush (preserves faint-star detection).

**A-2. Continuous per-frame servo** (`server/src/detect_engine.rs`): runs
every frame, needing only detection — no solve.

```text
detected stars < 4      → fallback exposure (last known-good / calibrated)  # slewing/clouds
otherwise:
  ma = EMA of star count (α=0.5)
  f  = ma / star_count_goal
  f < 1.0 and center-ROI mean > 240 (8-bit) → fallback   # bright-sky guard
  f < 0.8 or f > 1.6    → exposure = prev / f            # asymmetric deadband
                           (clamped to calibrated ±3 stops and [min,max])
  else                  → remember current exposure as known-good fallback
```

- **Gain is fixed outside the loop**: at night an "optimal gain" is set
  once — for RPi cameras the sensor's max analog gain (**IMX296 → 15×**;
  identical to PiFinder's imx296 profile value). Reasoning: past the
  read-noise knee, and with 8-bit output, the dynamic-range loss is
  irrelevant.
- cedar-detect itself uses an adaptive threshold (σ × estimated image
  noise), so detection tolerates a wide exposure range — a coarse servo
  suffices.

From PiFinder's perspective this directly resolves **P1 (0 detected vs 0
matched), P3 (catalog-independent), P4 (bright-sky guard), and P2 (can run
per frame on detection alone, without a solve)**. The detector is already
in our pipeline.

### Method B — Image-statistics (histogram/mean/percentile) servo

Match a frame brightness statistic to a target, without counting stars.

- **allsky** ([AllskyTeam mode_mean.cpp](https://github.com/AllskyTeam/allsky/blob/master/src/mode_mean.cpp)):
  servos the masked image mean (normalized 0–1) toward a target mean.
  Key idea: **a single integer ladder `exposureLevel =
  log2(gain × exposure_s) × steps²` unifies gain and exposure** — one
  loop covers 20+ stops day↔night. Step size is a polynomial in the
  deviation, damped by weighted history + a linear forecast.
- PiFinder's existing **background controller** (10th-percentile ADU ↔
  noise floor) is a small implementation of this family.
- Limitation: **a brightness statistic says nothing about whether stars
  are detectable.** Servoing the light-polluted background to a target
  mean can leave star detection over- or under-exposed. Useful as
  auxiliary guards (saturation ceiling, background floor), unsuitable as
  the primary signal.

### Method C — Star-SNR servo (PHD2)

[PHD2](https://github.com/OpenPHDGuiding/phd2/blob/master/src/myframe.cpp)
servos on a single guide star's internal SNR metric (target 6.0):
`newExp = exp × (target/SNR)²` (assuming SNR ∝ √exposure), with
asymmetric smoothing (α=0.20 rising / 0.15 falling). Proven and smooth,
but it is a **single-star** criterion — the wrong metric for plate solving,
which needs a *count* of stars. Generalized to many stars it effectively
converges to Method A (+ the detection σ threshold).

### Method D — Threshold pixel count (spacecraft star trackers)

Adjust exposure by the number of pixels exceeding high/low thresholds
([SPARCS et al.](https://arxiv.org/pdf/2507.03102)). Extremely cheap, but
fooled by hot pixels, planets, and light pollution — no advantage for us,
since cedar-detect is already available.

### Method E — Native libcamera/picamera2 AEC

`rpi.agc` is a mean-luminance-target controller: on a star field (>99.9%
near-black) it pushes shutter and gain to maximum just to lift the
background, and under light pollution it exposes for the sky glow — the
metric itself is wrong, and convergence takes many frames. Both allsky and
cedar replace it at night with their own loops, and PiFinder already sets
`AeEnable=False` (`camera_pi.py:61-64`). The ecosystem consensus is that
it is **unusable beyond the current daytime-alignment role
(`set_exp:native`)**
([picamera2 #592](https://github.com/raspberrypi/picamera2/discussions/592)).

### Method F — Model-based ceiling: motion blur & brightness limits

Not feedback, but a complementary **physically computed exposure ceiling**.

- Star-tracker literature ([Sensors 2014, PMC4003974](https://pmc.ncbi.nlm.nih.gov/articles/PMC4003974/)):
  star-trail length ∝ angular rate × exposure. Once the trail exceeds
  ~1 PSF, longer exposure buys almost no detection depth (optimum
  ~31 ms at 1°/s, ~18 ms at 2°/s).
- PiFinder has IMU angular rate, so a dynamic ceiling
  `max_exp_motion ≈ k / ω` is possible — structurally blocking "long
  exposures wasted while moving" (P5, P7) on a hand-moved telescope. At
  rest the ceiling lifts, allowing long exposures under dark skies.

### Method G — Hybrid: detection servo (inner loop) + solve-quality gate (outer loop)

The realistic combination. Method A as the primary loop, plus:

- **Inner loop (fast)**: detected-star-count servo + bright-sky guard +
  motion-blur ceiling (F). Runs at frame/detection cadence without solves.
- **Outer loop (slow)**: solve results (`Matches`, success rate) slowly
  trim the target detected-star count — e.g. raise the target when "25
  detected but solves keep failing". The current match-count controller's
  wisdom (asymmetry, deadband) moves into this layer.
- The zero-match recovery ladder shrinks to a last resort for
  "0 detected and no guard active" — it stops cycling on P1's false
  triggers (defocus, clouds, solver-side failure).

## 4. Gain policy survey

| Strategy | Source | Gist |
| --- | --- | --- |
| **Fixed high gain + exposure-only servo** | cedar | At night, fix max analog gain once (IMX296 15×). Past the read-noise knee, and with 8-bit output, the DR loss is irrelevant. One control variable → simple loop, stable detection threshold |
| Unified gain·exposure ladder | allsky | Single `log2(gain×exp)` level across the full day/night range. Right when one loop must span daylight too |
| Gain first → exposure second | literature synthesis | With a motion-blur constraint: "raise gain to the read-noise knee first; raise exposure only up to the blur/brightness limits" |

Implication for PiFinder: the current profile gain (imx296 15×) already
equals cedar's night-optimal value. **There is little need to put gain in
the feedback loop**; discrete scheduling — stepping gain down one notch
when the bright-sky guard trips — looks sufficient. (Daytime alignment
stays delegated to native AE as today.)

## 5. Comparison

| Method | Signal | Needs solve | P1 cause separation | P2 speed | P3 density-independent | P4 brightness guard | Cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Current (match count) | Matches | yes | ✗ | slow | ✗ | ✗ | — |
| A detected-star servo | detected centroid count | **no** (detection only) | ◎ | fast | ◎ | ◎ (guard included) | medium (wire count + replace controller) |
| B image statistics | mean/percentile | no | △ | very fast | ◎ | ◎ | small (extend background controller) |
| C star SNR | star flux/noise | no | △ | fast | △ | △ | medium |
| D threshold pixels | pixels over threshold | no | ✗ | very fast | △ | △ | small |
| E native AEC | mean luminance | no | ✗ | medium | ✗ | ✗ | 0 (unsuitable) |
| F motion-model ceiling | IMU angular rate | no | (complement) | — | — | — | small |
| G hybrid A+F+solve gate | detected count + Matches | outer loop only | ◎ | fast | ◎ | ◎ | medium–large |

## 6. Recommendation (draft for discussion)

1. **Replace the primary signal: match count → detected star count
   (Method A).** This is the core change. cedar-server — same solver
   stack — has field-proven numbers to start from (goal 20 stars, EMA
   α=0.5, deadband 0.8–1.6, ±3-stop clamp, mean>240 guard, <4-star
   fallback). The detected count is already computed in `solver.py`;
   wiring one `Centroids` field into `SolveDiagnostics` is the minimal
   change. A further step decouples detection from solving to run the
   loop at a faster cadence.
2. **Add the dynamic motion-blur ceiling (Method F)** — compute an
   exposure ceiling from IMU angular rate, cutting both wasted long
   exposures during hand motion and feedback pollution. It shares its
   ingredients (IMU delta) with the motion-frame solve gate
   ([mf_solve_motion_gate_review_en.md](mf_solve_motion_gate_review_en.md)),
   so design them together.
3. Shrink the zero-match recovery ladder to fire **only on "0 detected"**
   (the signal replacement finally makes ADR 0010's scoped responsibility
   enforceable).
4. Keep gain out of the feedback loop, fixed at the current profile value
   (§4). Consider only a one-notch step-down when the bright-sky guard
   trips.
5. Whether the existing match-count controller survives as the outer
   quality gate (Method G's slow loop) or is removed is decided at
   implementation time.

## 7. References

- cedar-server [calibrator.rs](https://github.com/smroid/cedar-server/blob/main/server/src/calibrator.rs) ·
  [detect_engine.rs](https://github.com/smroid/cedar-server/blob/main/server/src/detect_engine.rs) ·
  [cedar-camera rpi_camera.rs](https://github.com/smroid/cedar-camera/blob/main/src/rpi_camera.rs) ·
  [cedar-detect](https://github.com/smroid/cedar-detect)
- PHD2 [myframe.cpp](https://github.com/OpenPHDGuiding/phd2/blob/master/src/myframe.cpp) ·
  [manual](https://openphdguiding.org/man-dev/Advanced_settings.htm)
- allsky [mode_mean.cpp](https://github.com/AllskyTeam/allsky/blob/master/src/mode_mean.cpp) ·
  [flicker issue #228](https://github.com/thomasjacquin/allsky/issues/228)
- Star-tracker exposure optimization [Sensors 2014 (PMC4003974)](https://pmc.ncbi.nlm.nih.gov/articles/PMC4003974/) ·
  [SPARCS dynamic exposure control](https://arxiv.org/pdf/2507.03102)
- picamera2 astro discussions [#592](https://github.com/raspberrypi/picamera2/discussions/592) ·
  [#175](https://github.com/raspberrypi/picamera2/discussions/175)
- [SkySolve](https://github.com/githubdoe/skysolve) (manual-exposure control group) ·
  [FRAMOS IMX296 spec](https://framos.com/products/sensors/area-sensors/imx296lqr-c-22545/)
