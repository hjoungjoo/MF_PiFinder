---
status: accepted
---

# cedar + SEP hybrid solving is the production architecture

The fork's confirmed target condition is a heavily light-polluted sky
where only a handful of stars are visible (maintainer decision,
2026-07-28). Two nights of field measurement (Seoul, imx462, full
record in `docs/mf_sep_fullframe_impl_ko.md` §6) settle how solving is
structured there:

| condition | cedar-512 (stock) | SEP full-frame 12-bit |
| --- | --- | --- |
| target LP sky (twilight, 07-28) | 0.7–1.2 detections, **0 direct solves** | 15–20 detections, carried 100% of solves (88–98% rate in the good stretches) |
| good dark sky (07-29) | solves directly, but still failed 2 of 6 starry bench frames | 41–44 matched (sensitivity leader) |
| 40-min mixed session (07-29) | 1,919 solves | 1,711 rescues — **95% combined** |

Accuracy through the hybrid is 7–17″ (1σ) solve-to-solve in both
conditions; false solves across both nights: zero (tetra3 rejection is
the final defence).

## Decision

1. **The hybrid is permanent, not an experiment.** The stock path
   (crop → 8-bit → 512 → cedar-detect → tetra3) runs first and
   unchanged on every attempt; when it fails, the SEP path (uncropped
   12-bit frame → 2×2 bin → mesh background → σ4.0 → quality gates)
   solves from the same frame and feeds the normal chain through the
   proven coordinate mappings (`solver_frame_map`). Alignment uses the
   same priority order.
2. **Enabled by default.** `solver_shadow_detect` and
   `solver_sep_fallback` ship `true`; `solver_sep_sigma` ships 4.0.
   The A/B shadow CSV stays on because it is the tuning corpus and its
   cost is one SEP pass (~100 ms) per attempt on tmpfs.
3. **Detection quality is owned by gates, not the threshold.** σ4.0
   with the warm-pixel map, point-source shape gate and cluster gate
   (all thresholds measured against tetra3-matched ground truth) keeps
   solved-frame purity at 83–91%. Raising σ instead was measured to
   cost real stars (§6.5).
4. **cedar full-frame is considered and deferred.** On starry frames,
   cedar on the uncropped 8-bit frame matches 3× the 512 path at ~95%
   purity (§6.7) — but in the target LP sky its 8-bit input still
   under-detects where SEP carries, and the two-tier hybrid already
   reaches 95–100% solve rates. A third tier would add latency and
   complexity without a demonstrated solve-rate gain. Revisit if a
   condition appears where cedar-512 and SEP both fail and full-frame
   cedar would not.

## Consequences

- Fresh installs of the fork get LP-sky solving out of the box; the
  512 production path remains byte-identical for upstream parity.
- The solver spends up to ~1.1 s extra on attempts where cedar fails
  and SEP tries (bounded by exponential backoff on unsolvable scenes,
  instantly re-armed on a detection jump).
- The warm-pixel map is a per-device calibration artifact
  (`~/PiFinder_data/sep_warm_pixels.npy`); regenerate from dark
  corpora as the sensor ages (`python -m PiFinder.sep_warm_map`).
- Remaining validation is operational, not architectural: telescope
  alignment/push-to precision, and seasonal warm-map refresh cadence.
