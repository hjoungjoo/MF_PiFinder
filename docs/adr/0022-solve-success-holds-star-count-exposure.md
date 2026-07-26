---
status: accepted
---

# A successful solve holds the star-count exposure

Field measurement (Seoul, 2026-07-26, imx462, gain 30): sweeping manual
exposures while reading the live solver's diagnostics gave a sharp sweet spot
under heavy light pollution —

| exposure | detections (sigma 8) | solved |
| --- | --- | --- |
| 25–50 ms | 0–6 | 0/12 |
| 100 ms | 4–9 | 4/6 |
| 200 ms | 9–13 | 6/6 |
| 400 ms | 2–6 | 1/6 |
| 800 ms | 0–6 | 0/6 |

Two facts break the star-count controller's assumptions at such a site:

1. **The reachable star count tops out below `target_stars`.** The best
   exposure detects ~9–14 stars against a target of 20, so `f < deadband_low`
   and the servo raises exposure — away from the only regime that solves.
2. **Past the sweet spot the count/exposure model inverts.** More exposure
   means *fewer* detections (sky glow eats star contrast), so the raise loses
   stars, which lowers `f`, which raises exposure further: positive feedback
   in the wrong direction. The bright-sky guard that should break this never
   fires — the processed frame's centre mean stays far below the 240 (8-bit)
   threshold on this pipeline even at 1 s.

## Decision

`ExposureStarCountController.update()` takes a `solve_success` flag (wired
from `solve_source == "CAM"`). When the attempt solved and the star fraction
is not *above* the deadband, the controller learns the current exposure as the
anchor, retires any bright ceiling, and holds — a star-count shortfall alone
never pulls the exposure away from a solving regime. Excess stars
(`f > deadband_high`) still step down: shortening a solving exposure keeps the
solve and reduces motion blur.

Convergence from a cold start needs no new mechanism: the recovery ladder
(ADR 0010/0021) already visits 200 ms and shorter rungs, and the first rung
that solves is captured by the hold.

## Alternatives considered

- **Lower `target_stars`.** Fixes this site, mistunes dark sky, and still
  leaves the inverted-model feedback in place at some other site.
- **Lower the bright-sky guard threshold.** The processed-frame mean depends
  on sensor profile stretch; no single constant is right, and the guard would
  still be a proxy. Solve success is the ground truth the proxy approximates.
- **Hill-climbing on the count/exposure slope.** Strictly more general, but
  stateful and noise-sensitive (a passing cloud looks like an inversion).
  Not needed once solving itself anchors the servo.
