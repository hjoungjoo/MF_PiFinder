# When the Bayer label lies: a mono IMX462 and the sky-colour SQM zero point (2026-08-05)

> Community write-up from the MF_PiFinder fork
> ([hjoungjoo/MF_PiFinder](https://github.com/hjoungjoo/MF_PiFinder)), shared
> for other PiFinder builders and anyone shipping camera-based sky-quality
> metering.
> Korean version: [mf_mono_sqm_colour_guard_20260805_ko.md](mf_mono_sqm_colour_guard_20260805_ko.md)
>
> TL;DR: upstream's excellent sky-colour zero-point correction
> (#560, ADR 0026) keys "is this a colour sensor?" off the **driver's format
> label**, and at least one common IMX462 module carries a colour label on a
> sensor that measures as true mono. On such a module the correction applies
> cleanly, every bundled test passes, and the published SQM silently shifts
> **~+0.74 mag darker** — permanently, in every sky. We ported the feature
> with a one-line guard; the interesting part is why nothing else would have
> caught it.

## Background: the module

This fork runs a PiFinder on an IMX462 board (1920×1080, 12-bit) in
heavily light-polluted Seoul. The Linux driver binds it as `imx462lqr`
(the colour variant) and labels raw frames `SRGGB12`.

Early in our light-pollution solving work we measured the sensor's
Bayer-phase response directly: averaging each mosaic phase separately over
full frames, **R/G = B/G = 1.000 ± 0.001 under every condition we could
throw at it** — broadband daylight, sodium/LED urban night sky, indoor
lighting. A real colour filter array cannot do that: an urban sky must show
R ≫ B through an RGGB mosaic. The module is a true mono sensor behind a
colour label. (The "colour noise" people see from these modules is a
debayer artifact: a viewer that trusts the label assigns pixel noise to
R/G/B by row/column parity.)

Two practical notes from that investigation:

1. **You cannot ask the sensor.** IMX462LQR (colour) and IMX462LLR (mono)
   are the same silicon with identical register maps; the CFA is an
   optical layer with no I2C-readable presence bit. That is exactly why
   the Raspberry Pi `imx462` overlay has a *manual* `mono` parameter — the
   kernel cannot autodetect it either. The only reliable test is the
   photometric one above.
2. Since the label cannot be trusted, our camera profile carries an
   explicit measured flag: `CameraProfile.mono = True`, with the format
   label left as `SRGGB12` because the raw bit-depth handling still needs
   it.

That flag is the whole story below.

## Upstream's #560: a genuinely good idea

Upstream PiFinder recently made the radiometric SQM's zero point a
function of measured sky colour (PR #560, ADR 0026). The physics is sound:
the radiometer measures sky in the *sensor's* passband while reference
SQM meters measure V-band, and the conversion between the two depends on
the sky's spectrum. Light pollution is sodium/LED and green-weighted
(measured R/G 0.83–0.89); dark-site airglow is grey and NIR-rich
(R/G 1.00–1.04). A single constant split the difference and was wrong at
both ends — about 0.10 mag dark under light pollution, about 0.85 mag
bright at a dark site. Keying the zero point to the frame's own R/G fixes
both, on a sensor that actually measures colour:

```
zero_point = radiometric_zero_point
           + slope * (clamp(R/G, 0.83..1.04) - 0.85)     # slope = 5.544 for imx462
```

## The trap: the gate trusts the label

The correction turns on when `_mosaic_phase_is_rggb(profile)` says the
frame's mosaic can be sampled for colour. That guard checks three things:
the format label starts with `SRGGB`, no rotation that would transpose or
flip the CFA, and an even crop origin on both axes. All three are the
*right* checks for protecting the mosaic phase — but none of them asks
whether there is a mosaic at all. It infers that from the driver label.

Our profile: `format="SRGGB12"`, `rotation_90=0`, even crop origins.
The gate passes. The colour term switches on for a sensor with no CFA.

What happens next is quiet. A mono sensor's R/G is pinned at 1.00, which
sits inside the calibrated range, near the top. The model reads it as
*dark-site airglow spectrum* and applies almost the full positive
correction. We verified on this device's own archived raw frames
(bias-subtracted, upstream's exact post-#560 sampler):

| frame | measured R/G | zero-point shift |
|---|---|---|
| stages_20260729_115801 | 0.9979 | +0.820 mag |
| stages_20260729_115811 | 1.0032 | +0.849 mag |
| stages_20260729_115831 | 1.0000 | +0.832 mag |
| stages_20260729_115852 | 1.0027 | +0.846 mag |

Net of the accompanying constant refit (15.25 → 15.159), the published
SQM moves **≈ +0.74 mag darker — in every sky, forever**, because a mono
sensor has no colour with which to ever move back. The error is largest
exactly where this fork operates: under light pollution, the device would
claim a sky ~0.75 mag darker than truth.

Two secondary effects make it worse than a fixed offset:

- **Quantisation jitter.** At night the background sits a few ADU above
  the pedestal, and both colour medians are 12-bit integers. A 1 ADU
  median split at ~10 ADU of signal moves the zero point by ~0.55 mag —
  random frame-to-frame noise across the 1.16 mag clamp span, on top of
  the bias.
- **Non-transferable constants.** The imx462 slope and pivot were fitted
  on hardware that genuinely measures colour. Even the refit constant was
  not fitted on a mono module — it is merely the least-wrong fallback.

## Why nothing caught it

This is the part worth sharing. The port was textbook-clean:

- `git cherry-pick` applied with **zero conflicts** — the fork's SQM files
  were byte-identical to upstream's parent commit.
- **All 28 bundled tests passed unmodified.** One of them,
  `test_shipped_colour_profiles_hold_the_phase_invariants`, even asserts
  that imx462 *is* a colour profile — green, on a false premise, because
  the test checks the same label the gate does.
- No crash, no warning, no changed failure mode. Just a plausible number,
  shifted.

A wrong R/G does not fail loudly — upstream's own ADR says as much about
phase slips. A missing CFA is the same failure class one level up: the
sampler reads perfectly valid medians from sites that aren't red or green,
and every downstream consumer sees an ordinary corrected value. The only
defence is knowledge the code cannot derive at runtime: *what the sensor
physically is.* That has to enter as measured configuration.

## The fix: measured truth outranks the label

One clause, first in the gate:

```python
def _mosaic_phase_is_rggb(profile) -> bool:
    if getattr(profile, "mono", False):
        return False  # measured mono: the Bayer label lies, R/G is always ~1
    if not str(getattr(profile, "format", "")).upper().startswith("SRGGB"):
        return False
    ...
```

With the guard, a mono profile never gets colour fields in its samples,
so the zero point stays the profile constant — bit-identical behaviour to
pre-#560 apart from upstream's deliberate constant refit (−0.09 mag,
which we accept as a calibration improvement). The `hq` camera, a real
colour sensor, keeps the full colour term.

Test changes that came with it:

- The shipped-profile invariant test now asserts imx462/imx290 **refuse**
  colour (they ship `mono=True` here) while `hq` still holds the phase
  invariants.
- Four sampler-mechanics tests keep exercising the colour path via
  `dataclasses.replace(profile, mono=False)` — they test the mosaic
  sampler, not the shipped profile.
- A new regression pin, `test_measured_mono_imx462_keeps_the_constant_zero_point`,
  locks the end-to-end behaviour: no colour fields in the sample, effective
  zero point equals the profile constant.

After deploying: live SQM on the device reads the same as before the port
(13.1–13.2 under this evening's clouds) — no +0.74 step.

## Takeaways for other builders

1. **If you run an IMX290/IMX327/IMX462 module, test it.** Average the
   four Bayer phases separately over a daylight frame and a night frame.
   If R/G and B/G are both 1.00 within noise under *different* light
   sources, you have a mono sensor regardless of what the driver says —
   and any colour-keyed processing (this correction, white balance,
   debayer) is operating on noise.
2. **Gate colour features on measured colour capability, not the format
   label.** The label describes the driver binding, which describes the
   device-tree overlay, which describes what the board vendor *claimed*.
   None of those measured anything.
3. **Clean merges and green tests are not evidence of correct behaviour**
   when the change keys off configuration that can be wrong. The failure
   here was invisible to every mechanical check; it was caught only
   because the port was reviewed against a fact recorded months earlier
   in the fork's docs ("this sensor is measured mono"). Write those facts
   down where the next merge will trip over them.
4. For upstream and similar projects: a `mono` (or `measured_cfa`) field
   on the camera profile is cheap, and letting it veto the colour gate
   makes the mislabeled-module case safe by default. Modules with lying
   labels are common in the IMX462 aftermarket.

## References

- Fork port commit: `fde9beaa` (upstream `b28f7d9d`, PR #560; guard +
  test adjustments described above).
- Upstream design rationale: `docs/adr/0026-radiometric-zero-point-keyed-to-sky-colour.md`.
- Mono measurement record (Korean): `docs/mf_dev/mf_sep_fullframe_impl_ko.md` §6.4.
- Sync-round analysis that flagged the trap before porting:
  `docs/mf_dev/mf_upstream_patch_reference_ko.md`, 2026-08-04 section.
