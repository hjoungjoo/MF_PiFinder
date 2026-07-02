# MF PiFinder Mount-Mode Compatibility Plan

Date: 2026-07-03

This document is the working checklist for keeping the SkySafari, no-solve IMU
fallback, INDI mount-control, and OnStepX integration usable on both Alt/Az and
equatorial-class mounts.

## Goals

- Report the correct SkySafari/LX200 mount mode for both `mount_type = "Alt/Az"`
  and `mount_type = "EQ"`.
- Before plate solving, convert the IMU's physical Alt/Az direction to RA/Dec
  for SkySafari.
- Before plate solving, allow SkySafari GoTo + manual centering + Sync/Align to
  calibrate the IMU fallback by comparing the last GoTo target with the current
  IMU direction.
- Once plate solving succeeds, prefer PiFinder's solved pointing and reset the
  no-solve IMU correction.
- Keep INDI GoTo, Sync, and guide/manual motion on generic INDI telescope
  interfaces instead of Alt/Az-specific assumptions.

## Source Audit

| Area | Current state | Action |
| --- | --- | --- |
| PiFinder push-to UI | `calc_utils.aim_degrees()` uses Alt/Az deltas for Alt/Az mounts and RA/Dec deltas otherwise. | Keep and regression-test |
| SkySafari current coordinates | `pos_server.get_telescope_ra/dec()` returns solved JNOW coordinates when available, otherwise IMU Alt/Az converted to RA/Dec. | Keep |
| SkySafari status `:GW#` | Previously returned `AT1` unconditionally. | Make it follow `mount_type` plus override setting |
| no-solve IMU correction | SkySafari Sync stores the difference between the last GoTo target and current IMU Alt/Az. | Reset on plate solve |
| INDI GoTo | `goto_target` sends RA/Dec through INDI `EQUATORIAL_EOD_COORD`. | Mount-independent |
| INDI guide/manual move | Sends `north/south/east/west` guide motion through the active INDI driver. | Driver-independent, hardware-test required |
| OnStepX location/time | Shown only for the OnStepX driver. | Keep OnStepX-specific |

## SkySafari LX200 Status Policy

PiFinder answers SkySafari `:GW#` with an LX200-style status string.

Default policy:

| PiFinder config | Response |
| --- | --- |
| `mount_type = "Alt/Az"` | `AT1` |
| `mount_type = "EQ"` | `PT1` |

Meaning:

- First character: mount geometry. `A` means Alt/Az, `P` means
  polar/equatorial-class.
- Second character: tracking state. PiFinder currently reports `T`.
- Third character: alignment state. PiFinder keeps `1` for compatibility.

Some mount/app combinations may prefer a separate German-equatorial code. Use
the web UI or this config override when needed:

```text
INDI > SkySafari Mount Mode > SkySafari LX200 Mount Code
```

```json
"skysafari_lx200_mount_code": "G"
```

Supported values:

| Value | Meaning |
| --- | --- |
| `auto` | Choose from PiFinder `mount_type` |
| `A` | Force Alt/Az |
| `P` | Force polar/equatorial |
| `G` | Force German equatorial |

## No-Solve IMU Alignment Flow

1. PiFinder has no plate-solved pointing yet.
2. The user picks a bright object in SkySafari and sends GoTo.
3. PiFinder records the last SkySafari target RA/Dec.
4. The user manually or electronically centers the object in the eyepiece.
5. The user presses SkySafari Sync/Align.
6. PiFinder converts the target RA/Dec to Alt/Az for the current time/location.
7. PiFinder stores the difference between target Alt/Az and current IMU Alt/Az.
8. Until plate solve succeeds, SkySafari position replies use corrected IMU
   Alt/Az converted back to RA/Dec.
9. Once plate solve succeeds, the correction is reset and solved pointing wins.

This is mount-axis independent because the IMU measures physical sky direction
and SkySafari receives RA/Dec.

## Implementation Checklist

- [x] Add no-solve IMU correction state
- [x] Reset IMU correction when solved pointing is available
- [x] Avoid PiFinder plate-solve align while there is no solve
- [x] Make SkySafari `:GW#` follow `mount_type`
- [x] Add `skysafari_lx200_mount_code` override
- [x] Add shared SkySafari mount-mode settings to the INDI web UI
- [x] Add unit tests
- [x] Restart service and verify status
- [ ] Test real SkySafari Alt/Az profile
- [ ] Test real SkySafari EQ/German profile

## Test Matrix

### Unit Tests

| Test | Expected |
| --- | --- |
| `mount_type = "Alt/Az"` and `:GW#` | `AT1` |
| `mount_type = "EQ"` and `:GW#` | `PT1` |
| `skysafari_lx200_mount_code = "G"` | `GT1` |
| no-solve Sync | IMU correction active |
| solved pointing available | IMU correction inactive |
| SkySafari GoTo with INDI GoTo off | push-to target only |
| SkySafari GoTo with INDI GoTo on | `goto_target` queued |
| mount_control off | position reporting and push-to only |

### Hardware Tests

| Step | Alt/Az | EQ/equatorial |
| --- | --- | --- |
| SkySafari connection | stays connected and updates position | stays connected and updates position |
| Pre-solve position | IMU fallback position appears | IMU fallback position appears |
| GoTo target push | Object Details target is created | Object Details target is created |
| INDI GoTo on | mount starts/completes GoTo | mount starts/completes GoTo |
| no-solve Sync correction | local pointing improves | local pointing improves |
| after plate solve | solved pointing wins and correction resets | solved pointing wins and correction resets |
| guide/manual move | motion while held, stop on release | driver N/S/E/W motion, stop on release |

## Notes

- No-solve IMU correction is only an initial pointing aid, not a replacement for
  plate solving.
- With `mount_type = "EQ"`, PiFinder push-to UI shows RA/Dec deltas.
- SkySafari telescope profile mount type should match PiFinder `mount_type`
  whenever possible.
- Non-OnStepX INDI drivers may not show the OnStepX location/time panel, but
  GoTo/Sync uses standard INDI telescope properties where available.
