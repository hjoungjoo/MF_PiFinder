# Review: unwired solve gate for frames exposed during motion (solve motion gate)

Written: 2026-07-16. Status: **for review — no code changes yet; decide after discussion.**

## Summary

The solver does not reject frames whose exposure overlapped telescope motion.
Both the rejection parameter (`max_imu_ang_during_exposure`) and the measured
motion amount (`imu_delta`) **already exist in the code but are not connected
to each other**. As a result, a solve that succeeds while the scope moves at
slow-to-moderate speed (a few arcmin/s up to ~1 deg/s) introduces two errors:

1. The solved coordinate itself is biased toward the mid-exposure (smeared)
   position.
2. The IMU dead-reckoning reference pair (solve coordinate ↔ IMU anchor) is
   temporally mismatched, so **every IMU-predicted coordinate carries that
   offset until the next successful solve**.

If the offset exceeds the tracking guide's disturbance threshold (15'), a
false `disturbed` → recovery slew can fire even though nothing physically
moved, and guide pulses correct against the biased solve.

## Background: the coordinate pipeline

```text
camera_interface (exposure)
  ├─ imu_start = IMU sample at exposure start
  ├─ [exposure]
  ├─ imu_end   = IMU sample at exposure end
  ├─ imu_delta = |imu_end - imu_start| (deg)   <- measured, then unused
  └─ metadata = {exposure_end, imu: imu_end, imu_delta, ...}

solver
  ├─ is_new_image: only checks exposure_end > last_solve_attempt
  ├─ (no motion check)                          <- the gap
  ├─ on success: SuccessfulSolve{camera, aligned, imu_anchor=metadata.imu.quat}
  └─ imu_anchor = pose at exposure END

integrator (_apply_successful_solve)
  ├─ estimate cells snap to the solve
  └─ idr.solve(camera, aligned, imu_anchor)     <- recomputes q_eq2x

Between solves: estimate = idr.predict(current IMU)  <- q_eq2x error propagates
```

## Details (code evidence)

### 1. The gate parameter is defined but never read

`python/PiFinder/solver.py:437`:

```python
def solver(
    ...
    max_imu_ang_during_exposure=1.0,  # Max allowed turn during exp [degrees]
):
```

This is the parameter's only occurrence; nothing in the function body reads it.

### 2. Motion is measured but only used in debug mode

`python/PiFinder/camera_interface.py:270-296` computes `pointing_diff` from the
before/after IMU quats and publishes it as `imu_delta` (deg). However:

```python
# Make image available
if debug and abs(pointing_diff) > 0.01:
    # Check if we moved and return a blank image
    camera_image.paste(self._blank_capture())
else:
    camera_image.paste(base_image)
```

Only **debug mode** blanks a moved frame. In production the moved frame goes to
the solver unmodified, and nobody reads `imu_delta`.

### 3. The IMU anchor is the exposure-END pose

`python/PiFinder/camera_interface.py:291`: `"imu": imu_end`.
`python/PiFinder/solver.py:382-384`: `imu_anchor = last_image_metadata["imu"].quat`.

If the scope moved during the exposure, the solved coordinate is roughly the
(smeared) mid-exposure position while the anchor is the end-of-exposure pose.
`ImuDeadReckoning.solve()` assumes they are simultaneous when solving
`q_eq2x` (the EQ→IMU reference-frame rotation):

```python
q_eq2x = q_eq2cam * (q_x2imu * q_imu2cam).conj()
```

`q_eq2x` is wrong by the solve-vs-anchor gap, and every subsequent `predict()`
carries the same offset. **The offset persists until the next successful
solve** (no natural decay).

### Offset magnitude estimate

- Offset ≈ motion between mid-exposure and exposure end ≈ `imu_delta / 2`.
- Fast motion trails the stars and the solve fails on its own, so there is a
  natural cap. But with short exposures (0.2–0.4 s) a solve can succeed at
  0.1–1 deg/s, leaving an offset of **several arcmin up to tens of arcmin** —
  enough to cross the tracking guide's 15' disturbance threshold.
- Normal-operation motion during exposure (sidereal tracking ~15"/s, guide
  pulses ~37"/pulse) stays under 1' and is harmless. The risky window is
  hand-motion deceleration, the tail of a recovery slew, wind — the
  intermediate-speed regime.

### Impact

| Consumer | Effect |
|---|---|
| LCD/SkySafari/Web displayed coordinate | Snaps to the biased solve, then all IMU predictions carry the offset |
| Tracking-guide disturbance detection | Offset > 15'/tick → false `disturbed` → settle → **unnecessary sync+GoTo recovery** |
| Guide pulses (`_current_plate_solve`) | Consume the pure CAM solve cell, so they trust the biased solve → wrong correction pulses (self-heals on the next clean solve) |
| Multi-point align / backlash | Procedures that reference solve coordinates can adopt a biased value |

### Side finding: image/metadata race (secondary)

`camera_interface` pastes the image (line 286) then publishes metadata (line
296); the solver checks metadata then copies `camera_image`. While moving,
frame N's metadata can pair with frame N+1's image — the same class of
(solve, anchor) mismatch. Harmless when stationary. The proposed gate filters
moving frames anyway, which mostly defuses this too (separate fix optional).

## Proposed change

### Option A (recommended): skip moved frames in the solver

Add the gate right after the `is_new_image` check in `solver.py`:

```python
is_new_image = last_image_metadata["exposure_end"] > last_solve_attempt
if not is_new_image:
    continue

# Exposure-motion gate: do not solve frames captured while moving.
# The solved position is biased toward the smeared mid-exposure pointing
# and mismatches the IMU anchor (end-of-exposure pose), leaving every
# estimate offset until the next solve.
imu_delta = float(last_image_metadata.get("imu_delta") or 0.0)
if imu_delta > max_imu_ang_during_exposure:
    last_solve_attempt = last_image_metadata["exposure_end"]
    logger.debug(
        "Skipping solve: moved %.2f deg during exposure (max %.2f)",
        imu_delta, max_imu_ang_during_exposure,
    )
    continue
```

Design points:

- Update `last_solve_attempt` so the same frame is not rechecked every loop.
- Skip silently — do **not** push a `FailedSolve`. Auto-exposure keys off
  CAMERA_FAILED results, and a moved frame says nothing about exposure
  quality, so it should not feed the exposure controller. (Open for
  discussion — decision point 3 below.)
- No integrator/dead-reckoning changes needed: a gated frame produces no
  `SuccessfulSolve`, so no anchor reseed happens and the existing estimate
  keeps advancing on IMU (same path as a FailedSolve today).

### Threshold recommendation: default 1.0 → 0.25 deg

| Candidate | Rationale | Notes |
|---|---|---|
| 1.0 deg (current default) | presumed original intent | max offset ~30' — exceeds the 15' disturbance threshold; insufficient |
| **0.25 deg (recommended)** | max offset ~7.5' < 15' threshold; >30x margin over normal-operation motion (tracking 15"/s, pulse 37") | low risk of dropping valid solves from wind/vibration |
| 0.1 deg | stricter | may drop solves in strong wind; expose via config if needed |

The BNO055 noise floor over an exposure is ~0.01–0.05 deg, so 0.25 deg is well
separated from false positives.

### Option B (follow-up, optional): better anchor timing

Publish both `imu_start`/`imu_end` and anchor on the mid-exposure pose (slerp),
halving the residual mismatch. With the 0.25 deg gate the residual is already
≤ ~7.5', so the cost/benefit is weak — recommend deferring.

### Option C (follow-up, optional): remove the image/metadata race

Have the solver re-read metadata after `camera_image.copy()` and discard the
frame if `exposure_end` changed. Low priority once Option A is in.

## Verification plan

1. **Unit test**: extract the gate (or test the boundary cases directly):
   above threshold → skip + attempt updated; at/below → solve proceeds.
2. **Hardware**: with solves running at night, push the tube slowly
   (~0.5 deg/s) and check (a) skip logs appear in the journal, (b) the
   post-push estimate offset (IMU prediction vs next solve) shrinks compared
   to before the gate.
3. **Regression**: long stationary run — solve success rate must not drop
   (no false gating).

## Decision points (for discussion)

1. **Threshold**: adopt 0.25 deg default? expose as config
   (`solver_max_imu_ang_during_exposure`)?
2. **Scope**: Option A only first, or include B/C?
3. **Skip style**: silent skip (recommended) vs publishing a `FailedSolve`
   (if moved frames should feed auto-exposure/diagnostics).
4. **Debug blanking** (camera_interface.py:282): redundant once the gate is
   in — clean up or keep?

## Code references

- `python/PiFinder/solver.py:437` — unused parameter
- `python/PiFinder/solver.py:504-519` — is_new_image gate (proposed insertion point)
- `python/PiFinder/solver.py:382-384` — imu_anchor capture
- `python/PiFinder/camera_interface.py:267-296` — imu_delta measurement/metadata
- `python/PiFinder/integrator.py:210-254` — solve application + dead-reckoner reseed
- `python/PiFinder/pointing_model/imu_dead_reckoning.py:77-95` — q_eq2x math
- `python/PiFinder/mountcontrol_indi.py:1525-1542` — guide-pulse solve consumption
