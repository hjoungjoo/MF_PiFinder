# Camera architecture: exposure control

This document describes how PiFinder decides the camera exposure time —
the three exposure regimes, the feedback controllers inside
solver-driven auto-exposure, and zero-match recovery.

It focuses on the runtime path in the camera process:

- `PiFinder/auto_exposure.py` — the match-count/background controllers
  and recovery logic.
- `PiFinder/auto_exposure_starcount.py` — the opt-in star-count
  controller (see §3b).
- `PiFinder/camera_interface.py` — `get_image_loop`, the capture loop that
  wires solve results and UI commands into the controllers.

Glossary: [`camera/CONTEXT.md`](./camera/CONTEXT.md). Decision record for
the recovery consolidation: [ADR 0010](../adr/0010-zero-match-recovery-single-ladder.md).

---

## 1. Data flow

```
solver process                          camera process (get_image_loop)
  tetra3 solve attempt                    capture frame
    └─ Matches (every attempt,              │
       success or failure) ──────────► shared_state.solution()
                                            │  new last_solve_attempt only
                                            ▼
                              ┌─ match-count controller (default)
                              │    └─ Matches == 0 → zero-match recovery
                              ├─ star-count controller (opt-in via
                              │  camera_exp = "auto_star")
                              │    └─ Centroids == 0 → zero-match recovery
                              └─ background controller (SQM screen only)
                                   └─ reads shared_state.noise_floor()  ◄── SQM
                                            │
                                            ▼
                                   set_camera_config(exposure, gain)

UI / main process ── command_queue ──► "set_exp:…", "set_gain:…",
                                       "set_ae_mode:…", "exp_up/dn/save", …
```

Feedback is naturally rate-limited: a controller runs only when a solve
result with a **new** `last_solve_attempt` timestamp appears
(`camera_interface.py`, the `_last_solve_time` check), and only for
solve sources `CAM` / `CAM_FAILED` — failed attempts feed the loop too,
because `Matches` is published on every attempt (see Positioning).

## 2. Exposure regimes

Exactly one of three authorities decides exposure at any moment:

| Regime | Entered by | Exposure decided by |
| --- | --- | --- |
| Solver-driven auto-exposure | `set_exp:auto` / `set_exp:auto_star` (menu "Auto" / "Auto Star", or restored from `camera_exp: "auto"` / `"auto_star"` at startup) | match-count, star-count, or background controller |
| Native auto-exposure | `set_exp:native` (daytime alignment only) | the camera driver |
| Manual exposure | `set_exp:<µs>` (menu), `exp_up` / `exp_dn` | the user |

Transitions worth knowing:

- **Daytime alignment** (`ui/align_daytime.py`) enters native AE on
  activation and restores the prior setting on exit (`set_exp:auto` or the
  saved manual value). On backends with no native AE (debug / non-Pi),
  the fallback is a fixed 1 ms daylight exposure
  (`DAYTIME_AE_FALLBACK_EXPOSURE`).
- **Any manual nudge wins**: `exp_up` / `exp_dn` silently drop both
  auto-exposure regimes. The new value is *not* persisted until
  `exp_save`, which also writes `camera_gain`.
- Selecting a manual value from the menu persists it to `camera_exp`
  immediately; selecting "Auto" persists the string `"auto"`, "Auto Star"
  the string `"auto_star"`.

## 3. Match-count controller

`ExposurePIDController` (`auto_exposure.py`). Steers exposure so the
solver keeps matching a healthy number of stars.

- **Target match count** 17, **deadband** ±5 (no adjustment within
  12–22 matches).
- **Asymmetric gains**: conservative descent when there are too many
  matches, aggressive ascent when too few — being too dark is the costly
  direction at night.
- **Rate limiting** applies only to decreases (`update_interval` 0.5 s);
  increases respond immediately.
- **Integral hygiene**: the integral resets when the error changes sign,
  and anti-windup backs out the integral contribution when the output
  clamps to `[min_exposure, max_exposure]` = [25 ms, 1 s].

## 3b. Star-count controller (opt-in)

`ExposureStarCountController` (`auto_exposure_starcount.py`). An
alternative to the match-count controller, selected as a fourth Camera
Exp menu item: "Auto Star" persists `camera_exp: "auto_star"` and sends
`set_exp:auto_star` (the plain "Auto" item stays the match-count
controller). Living inside the Camera Exp menu keeps it reachable from
the focus/preview screen's marking menu (long press → Exposure), so the
controller can be switched while watching the focus strip. The choice
only swaps which controller runs in the default branch — the background
controller still takes over while the SQM screen is active, and all
regime transitions (manual nudges, native AE, `exp_save`) are unchanged.

Feedback signal: **`Centroids`** (stars cedar-detect extracted from the
frame, published on every attempt) instead of `Matches`. That separates
failure causes the match-count controller cannot: 0 detected is an
exposure/optics problem (recovery's job); N detected with 0 matched is a
solver-side failure (recovery must not run).

Control law and defaults follow cedar-server's exposure servo
(same solver stack, field-proven numbers — see
`docs/mf_auto_exposure_plan_ko.md`):

- **Target** 20 detected stars, smoothed by an EMA (α = 0.5).
- **Asymmetric deadband**: act when `ema/target` < 0.8, tolerate excess
  up to 1.6.
- **Division step**: `new = current / (ema/target)` — star count is
  roughly proportional to exposure, so one step converges in a few
  solves. No PID state.
- **Anchor**: any exposure landing inside the deadband is remembered as
  known-good. Adjustments clamp to anchor ±3 stops, then to the absolute
  [25 ms, 1 s] range. The anchor is also the fallback target and resets
  to 400 ms (the shipped default) on restart.
- **Bright-sky guard**: short of stars but center-ROI mean > 240
  (8-bit) → return to anchor instead of raising exposure.
- **Slewing fallback**: fewer than 4 detected stars → return to anchor,
  no servo step, no recovery.
- **Zero-detection recovery**: `Centroids == 0` walks the same recovery
  ladder (`ZeroMatchRecovery` instance of its own), triggered after 2
  consecutive zero-detection attempts. Exiting recovery clears the EMA
  so the excursion doesn't bias the next step.

## 4. Zero-match recovery

When a solve attempt produces zero `Matches`, the match-count controller
stops trusting its feedback signal and delegates to recovery
(`update()` → `_handle_zero_match` → `ZeroMatchRecovery`).

- **Trigger count** 2: recovery activates on the second consecutive
  zero-match attempt.
- **Recovery ladder**: `[400, 800, 1000, 200]` ms — start at the
  known-safe shipped default, climb first (too-dark dominates at night),
  then one short rung. The ladder floors at 200 ms (ADR 0010): below
  that, a frame is unlikely to pick up enough stars to solve, even under
  a bright sky. Each rung is tried twice (two solve attempts), and the
  ladder wraps until matches return.
- **The floor is recovery's, not the controller's**: the match-count
  controller's clamp range (§3) still reaches down to 25 ms — a
  feedback-justified descent is fine; recovery's blind search below
  200 ms isn't.
- **Exit**: the first nonzero-`Matches` attempt deactivates recovery and
  resets the controller's integral and last-error so the excursion
  doesn't bias the next adjustment.

Recovery's responsibility is exactly one failure cause: **the exposure is
badly wrong** (dusk/dawn, slew into bright sky, returning from daytime
alignment). Defocus, transient blockage, and solver-side failures are
deliberately out of scope — see ADR 0010. That decision also removed the
three alternative strategies (Exponential, Reset, Histogram), the
`ZeroStarHandler` plugin seam, the `set_ae_handler` command, the
Experimental "AE Algo" menu, and the `auto_exposure_zero_star_handler`
config key. Recovery is now the single concrete `ZeroMatchRecovery` class;
stale `auto_exposure_zero_star_handler` values in a user's config are
ignored.

## 5. Background controller

`ExposureSNRController` (`auto_exposure.py` — "SNR" is a misnomer; see
the glossary). Used for SQM measurement, which wants longer, steadier
exposures than match-count control produces.

- Activated screen-scoped: `ui/sqm.py` sends `set_ae_mode:snr` in
  `active()` and `set_ae_mode:pid` in `inactive()`. The controller choice
  is never persisted.
- Feedback signal: the frame's 10th-percentile ADU value ("dark pixel"
  background). Target: sit just above the **noise floor** published by
  SQM (`shared_state.noise_floor()`, consumed at
  `ExposureSNRController.update(..., noise_floor=...)` with a +2 ADU
  margin). Falls back to thresholds derived from the camera profile (bias
  offset, bit depth) when no noise floor is available.
- Adjustments are multiplicative (×1.3 / ÷1.3) for stability; it ignores
  `Matches` entirely and has no zero-match recovery.

This is the consumer side of the SQM → Camera relationship in
`CONTEXT-MAP.md`.

## 6. Diagnostic exposure sweep capture

Unrelated to recovery despite the shared word "sweep":
`capture_exp_sweep` (triggered from the SQM tools UI) captures 100
RAW+processed image pairs across a logarithmic exposure range into
`~/PiFinder_data/captures/sweep_<timestamp>/` with GPS/location metadata,
for offline analysis. Auto-exposure is disabled for the duration.

## 7. Gotchas

- **Shipped default regime is solver-driven auto-exposure.**
  `default_config.json` ships `camera_exp: "auto"`, so auto-exposure —
  including all recovery machinery — runs out of the box. The recovery
  ladder starts at 400 ms (the previous fixed default), so the first-frame
  behavior is unchanged; from there feedback control takes over. Existing
  users keep whatever `camera_exp` their saved config holds (a manual µs
  value or `"auto"`); only fresh installs and config resets get the new
  default. (ADR 0010 deferred this regime choice; it was resolved in
  favor of `"auto"` once the floored single-ladder recovery made
  auto-exposure safe by default.)
- **The AE gate requires the match-count controller object even in
  background mode**: `get_image_loop` checks
  `_auto_exposure_enabled and _auto_exposure_pid` before dispatching to
  either controller.
- **Controller choice is screen-scoped, in-memory only.** A restart while
  the SQM screen was last active comes back in match-count mode.
- **Two different "controller choices" exist.** The pid/snr split
  (`set_ae_mode`) is the screen-scoped, non-persisted SQM override above.
  The match-count/star-count split rides on `camera_exp` itself
  (`"auto"` vs `"auto_star"`, both solver-driven regime values) and is
  persisted like any other Camera Exp selection. The AE gate still
  requires `_auto_exposure_pid` to exist even when star_count is
  selected — the match-count controller object is always created.
- **Failed solves drive feedback.** `CAM_FAILED` results carry
  `Matches = 0` into the controller; that is what makes zero-match
  recovery possible at all, but it also means solver-side failures look
  identical to darkness from the camera's point of view. This conflation
  is specific to the match-count controller — the star-count controller
  reads `Centroids` and does not walk the ladder while stars are still
  being detected (§3b).
- **Zero `Matches` ≠ empty frame.** A star-filled but unsolvable frame
  (defocus, motion, distortion) walks the same recovery path. By ADR 0010
  recovery does not try to fix those — expect ladder cycling until the
  underlying cause clears.
