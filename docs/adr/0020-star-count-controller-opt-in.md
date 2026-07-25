---
status: accepted
---

# Auto-exposure: keep the match-count controller, add the star-count controller as an opt-in

The match-count controller steers exposure with `Matches` — the number of
stars tetra3 paired against the catalog. That signal has structural
problems documented in `docs/mf_auto_exposure_methods_ko.md` (P1–P7): it
cannot distinguish "too dark" from defocus, motion, clouds, or
solver-side failure (all arrive as `Matches=0`, so the recovery ladder
cycles on causes it cannot fix); it depends on catalog density and FOV,
so sparse fields drag exposure to the 1 s maximum; and it has no
bright-sky guard.

We add a second controller that steers with **`Centroids`** — the number
of stars cedar-detect extracted from the frame, now published on every
solve attempt alongside `Matches`. The control law and defaults are
cedar-server's field-proven exposure servo (same solver stack): target 20
detected stars, EMA α=0.5, asymmetric deadband (act below 0.8×, tolerate
to 1.6×), a single division step (`new = current / (ema/target)`),
adjustments clamped to ±3 stops around a learned known-good anchor, a
center-ROI mean>240 bright-sky guard, and a <4-star slewing fallback.
Zero-*detection* walks the existing recovery ladder — which finally
enforces ADR 0010's scope at the signal level: a star-filled but
unsolvable frame no longer triggers recovery.

**The default does not change.** The controller choice rides on
`camera_exp` itself: the Camera Exp menu gains a "Star" item that
persists `"auto_star"`, next to the existing "Auto" (match-count). One
menu is deliberate — the focus/preview screen's marking menu jumps to
Camera Exp, so the user can switch controllers and manual exposures from
the same place while watching the focus strip. (A separate `Camera AE`
menu with a `camera_ae_controller` key was built first and removed the
same day for this reason.) Reasons for opt-in
rather than replacement: the match-count controller is the shipped,
field-tested default and the new signal's numbers (target, deadband,
guard threshold) come from cedar's optics, not ours — they need A/B field
validation first. The new controller lives in its own module
(`auto_exposure_starcount.py`); existing controllers and recovery are
untouched, and the ladder is reused as a separate instance.

Deviation from cedar: no one-shot exposure calibration. The anchor is
learned while running (any exposure that lands in the deadband), because
PiFinder starts observing immediately and the recovery ladder's 400 ms
first rung already covers the cold-start search. The anchor is not
persisted across restarts.

## Consequences

- Two "controller choice" axes now exist and must not be conflated: the
  pid/snr split stays screen-scoped and non-persisted (SQM override);
  match_count/star_count is a persisted user option for the default
  branch. The AE gate still requires the match-count controller object to
  exist regardless of choice.
- `SolveDiagnostics` gains `Centroids` (default 0, like `Matches`),
  published on success and failure; the solver's exception path reports 0
  because its centroid list may be stale.
- Whether star_count becomes the default — and whether the match-count
  controller survives as a slow outer quality gate — is deferred until
  field A/B results (see `docs/mf_auto_exposure_plan_ko.md` §7 검증).
