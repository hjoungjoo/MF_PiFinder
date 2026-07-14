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
| SkySafari current coordinates | `pos_server.get_telescope_ra/dec()` return the pointing coordinate service selection: solved pointing when available, otherwise IMU/mount-based fallbacks. | Keep |
| SkySafari status `:GW#` | Previously returned `AT1` unconditionally. | Make it follow `mount_type` plus override setting |
| no-solve IMU correction | SkySafari Sync stores the difference between the sync target (latest `Sr/Sd`, falling back to the last GoTo target) and current IMU Alt/Az. | Reset on plate solve |
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

In the SkySafari/LX200 command flow, `:Sr...#` and `:Sd...#` only store target
coordinates. The following command selects the action:

| Following command | Meaning |
| --- | --- |
| `:MS#` | GoTo the stored target |
| `:CM#` | Sync/Align to the stored target |

When handling `CM#`, PiFinder prefers the most recently received `Sr/Sd`
coordinates over the previous GoTo target. This prevents an Align/Sync on a new
object from accidentally reusing the older GoTo target.

When `skysafari_indi_goto` is enabled, SkySafari Align/Sync is forwarded to
INDI/OnStep along with SkySafari GoTo. `skysafari_indi_sync` remains as an
additional allow option for setups that want Align/Sync forwarding without
GoTo forwarding.

## Implementation Checklist

- [x] Add no-solve IMU correction state
- [x] Reset IMU correction when solved pointing is available
- [x] Avoid PiFinder plate-solve align while there is no solve
- [x] Make SkySafari `:GW#` follow `mount_type`
- [x] Add `skysafari_lx200_mount_code` override
- [x] Make SkySafari `CM#` prefer current `Sr/Sd` coordinates over the previous GoTo target
- [x] Forward SkySafari Align/Sync to INDI/OnStep when GoTo forwarding is enabled
- [x] Treat 0-degree altitude as a valid Alt/Az push-to coordinate
- [x] Render object-list push-to distances when one axis is 0 degrees
- [x] Add shared SkySafari mount-mode settings to the INDI web UI
- [x] Add unit tests
- [x] Restart service and verify status
- [ ] Test real SkySafari Alt/Az profile
- [ ] Test real SkySafari EQ/German profile

## 2026-07-03 Source Audit

The mount-mode-sensitive paths were reviewed again.

| Area | Result | Action |
| --- | --- | --- |
| LCD Settings > Mount Type | `mount_type` is stored as `Alt/Az` or `EQ` and runs the restart callback. | OK |
| Push-to calculation | `calc_utils.aim_degrees()` returns Alt/Az deltas for `Alt/Az`, RA/Dec deltas otherwise. | Fixed 0-degree altitude edge case |
| Object Details display | Passes the same `mount_type` used by `aim_degrees()` to `draw_pointing_instructions()`. | OK |
| Object List display | Renders `aim_degrees()` results into the list distance text. | Fixed 0-degree axis display |
| Polar Align | Always forces `Alt/Az` indicators because polar correction uses physical altitude/azimuth adjusters. | Intentional exception |
| SkySafari `:GW#` | Returns `AT1`/`PT1`/`GT1` from `mount_type` or `skysafari_lx200_mount_code`. | OK |
| SkySafari `:Sr/:Sd/:MS/:CM` | `Sr/Sd` only store coordinates; `MS` selects GoTo and `CM` selects Sync/Align. | Current `Sr/Sd` priority fixed |
| INDI GoTo/Sync/Guide | Uses standard INDI telescope properties and guide/motion commands. | Mount-independent, driver hardware tests still needed |
| Web Equipment telescope `mount_type` | Equipment DB uses `alt/az`/`equatorial`; this is separate from PiFinder runtime `Alt/Az`/`EQ`. | User decision needed for auto-linking |

Decision items:

- Whether changing the active telescope in Equipment should automatically change
  PiFinder's global `mount_type`. This could be convenient, but it may also
  unexpectedly change SkySafari mode and push-to coordinate readouts during an
  observing session.
- Whether to harden the LX200 `Sr/Sd` parser into an explicit transaction
  state. Normal SkySafari sends both values together, but a malformed client
  could send only one new coordinate and leave the other from a previous target.
  This is a broader protocol-state change and should be handled separately.

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
| SkySafari Align/Sync with INDI GoTo on | `sync` queued |
| mount_control off | position reporting and push-to only |
| Alt/Az solution altitude is 0 degrees | valid push-to delta returned |
| Object List movement has one 0-degree axis | actual distance rendered, not `--- ---` |

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
