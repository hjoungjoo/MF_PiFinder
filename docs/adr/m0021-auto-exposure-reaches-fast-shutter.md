---
status: accepted
---

# Auto-exposure must be able to reach fast shutter speeds

Field observation (Seoul, 2026-07-26, imx462): with manual 25 ms the solver
matched repeatedly. Switching to `auto_star` never got back there. Two separate
bounds pinned the exposure above the shutter speed that actually worked, and
the same shape applies to `auto` (match-count) through the shared ladder.

**The recovery ladder never searched below its floor.** ADR 0010 floored the
zero-match ladder at 200 ms on the reasoning that "below 200 ms a frame is
unlikely to pick up enough stars to solve — even under a bright sky". Under
heavy light pollution that premise does not hold: this site solves at 25 ms and
detects nothing at 400 ms–1 s, where the frame is sky glow. Recovery therefore
cycled `[400, 800, 1000, 200] ms` indefinitely — observed ping-ponging between
400 ms and 1 s — because a wrap restarted the same four rungs.

We keep ADR 0010's ordering and its floor **for the first pass**, and append
`[100, 50, 25] ms` once that pass has failed, continuing into them rather than
replaying the long rungs. The dark-sky common case still costs 8 attempts
before anything is added, so ADR 0010's cost argument survives; what changes is
that a full failure escalates the search instead of looping on it.

**The star-count controller's anchor bound was unreachable-by-construction.**
Adjustments are clamped to anchor/8..anchor×8 around a learned known-good
anchor (ADR m0020), but the anchor starts as a shipped guess (400 ms) and is
only relearned when a reading lands inside the deadband. Where the working
exposure is shorter than anchor/8 — 25 ms is well under 400 ms/8 = 50 ms — the
servo asks to go shorter on every frame, is pinned at 50 ms, and returns "no
change"; the deadband is never reached, so the anchor never updates. Observed:
the exposure settled at exactly 50 ms and stayed there.

After `reanchor_after` (3) consecutive clamps in the same direction, the anchor
follows the boundary it was pinned at. A sustained ask is evidence the anchor
is wrong; a single odd frame is not, and still cannot fling the exposure — that
is what the bound is for. Any unclamped step or a direction flip resets the
streak.

**Every arm of the control law moved up or sideways.** With both bounds
relaxed, a debug trace of the same site showed a closed cycle:

    1s    -> bright-sky guard (center ROI mean 251 > 240) -> anchor 400ms
    400ms -> 0 detected -> recovery climbs -> 800ms
    800ms -> 6-8 detected, short of the target of 20 -> raise to 1s -> guard

Nothing in it walks down, so the fast shutter stayed unreachable no matter how
far the bounds were widened. Two inversions close it:

- The bright-sky guard **halves** the exposure instead of returning to the
  anchor, and records a ceiling at that point. The ceiling caps later raises,
  so a darker-looking frame that is still short of stars cannot climb straight
  back into the exposure that just proved to be sky glow. A deadband hit
  retires the ceiling — a working exposure means that sky is gone.
- Zero-detection recovery takes a **descending** ladder `[200, 100, 50, 25] ms`
  when the caller can see the frame is sky-glow limited. The prior is chosen
  once, at activation, so a later frame cannot restart the walk mid-ladder.

Only the star-count controller passes that brightness signal; the match-count
controller has no view of the frame and keeps the night-time ladder unchanged.

## Consequences

- Recovery's worst case grows: a sky where nothing is detectable at any
  exposure now cycles 7 rungs (14 attempts) instead of 4 (8). The first pass is
  unchanged.
- Under a genuinely bright sky the exposure now ratchets toward the floor
  rather than oscillating. The ceiling is retired by a deadband hit, by a
  clearly dark frame (center ROI mean below `bright_clear_mean`, 120), or by a
  controller reset. The gap between 240 (set) and 120 (release) is the point:
  releasing at the guard threshold itself would let a frame that merely dipped
  below it restart the climb-and-guard cycle, while never releasing would
  starve the servo when dusk ends.
- ADR 0010's 200 ms floor is narrowed, not revoked: it still governs the first
  pass. Its stated reason ("going shorter is unlikely to beat 200 ms even under
  a bright sky") is contradicted by this site and should not be relied on
  again.
- The anchor can now drift by a factor of 8 per re-anchor, bounded by
  `[min_exposure, max_exposure]`. It is still not persisted across restarts, so
  every session re-learns from the 400 ms guess.
- Both changes are search-behaviour only: no new config key, no menu, and the
  deadband/target/guard values from ADR m0020 are untouched.
