# MF PiFinder Backlash Measurement Flow

This document describes the `compass_goto_loop` automatic measurement mode used
by INDI > Settings > Backlash.

The mode now uses INDI GoTo movement again. The OnStepX driver `GUIDE_RATE`
support remains available in the driver, but Auto Backlash no longer changes
`GUIDE_RATE` and no longer sends `TELESCOPE_TIMED_GUIDE_*` commands.

## Core Rules

- The test moves one active axis at a time.
- Alt/Az mounts test `AZ` first, then `ALT`.
- EQ mounts test `RA` first, then `DEC`.
- Each axis uses a fixed start point `S` and an active-axis offset point `T`.
- Only the active-axis coordinate differs between `S` and `T`.
- PiFinder records mount coordinates and IMU coordinates after every settled
  GoTo leg.
- Mount travel is calculated from the previous settled mount readback to the
  current settled mount readback.
- IMU travel is calculated from the previous settled IMU pose to the current
  settled IMU pose.
- Signed motion error is `mount travel - IMU travel`.
- Backlash candidates are the absolute signed motion error in arc-seconds.
- Legs where the mount-vs-IMU travel difference is at least 1 degree are
  excluded from statistics.
- The remaining candidates are sorted; the lowest 30% and highest 30% are
  discarded; the middle 40% mean is used as the recommendation.

Because OnStep/INDI can enable tracking after GoTo, PiFinder disables tracking
before the test and again after every GoTo leg.

GoTo completion is guarded against OnStepX's near-destination refinement. A leg
is not considered complete on the first idle sample. PiFinder waits for INDI to
report idle and for coordinate readback to remain stable for a stable window and,
when the OnStep status text is available, waits for `:GU#` to return `N` (`No
goto`). This avoids recording IMU data during the firmware's near-destination
settle wait before the final fine approach.

## Defaults

```text
offset = 2.0 degrees
default repeat count = 10, adjustable from 1 to 50 in the web UI
stable idle/position window before GoTo completion = 4.0 seconds
settle wait after GoTo completion = 0.5 seconds
pause before each return leg = 1.0 second
GoTo timeout = 180 seconds
```

## Axis Targets

### Alt/Az Mounts

For a start point of `Alt 10, Az 20` and an offset of 2 degrees:

```text
AZ-axis test:
  S_az = Alt 10, Az 20
  T_az = Alt 10, Az 22

ALT-axis test:
  S_alt = Alt 10, Az 20
  T_alt = Alt 12, Az 20
```

PiFinder converts these Alt/Az targets to RA/Dec before sending INDI GoTo.

### EQ Mounts

For a start point of `RA 100, DEC 20` and an offset of 2 degrees:

```text
RA-axis test:
  S_ra = RA 100, DEC 20
  T_ra = RA 102, DEC 20

DEC-axis test:
  S_dec = RA 100, DEC 20
  T_dec = RA 100, DEC 22
```

## Flow

```text
[Start]
  |
  v
[Enable IMU compass/NDOF mode if needed]
  |
  v
[Wait for MAG calibration = 3]
  |
  v
[User presses Continue Motion Test]
  |
  v
[Connect to INDI mount]
  |
  v
[Require Unparked state]
  |
  v
[Read tracking state and turn tracking Off]
  |
  v
[Sync mount coordinates to current IMU Alt/Az]
  |
  v
[For each active axis]
  |
  +--> [Read current mount position]
  |
  +--> [Calculate anti-offset init target]
  |
  +--> [GoTo init target, wait complete, tracking Off, settle 0.5s]
  |
  +--> [Read actual start S from mount]
  |
  +--> [Calculate fixed target T from S + active-axis offset]
  |
  +--> [Record initial mount/IMU coordinates]
  |
  +--> [GoTo T warm-up, tracking Off, settle, record offset initial]
  |
  +--> [Repeat N times]
         |
         +--> [pause 1.0s]
         +--> [GoTo S, tracking Off, settle, record return leg]
         +--> [GoTo T, tracking Off, settle, record offset leg]
  |
  v
[Filter records and calculate recommendations]
  |
  v
[Stop mount, restore tracking only if it was originally On and test completed]
```

## Recorded Values

Each leg keeps enough data to debug the estimate:

- `mount_start_*`: previous settled mount readback.
- `mount_end_*`: mount readback after the current GoTo settles.
- `command_start_*`: nominal command start point for the leg.
- `target_*`: GoTo target for the leg.
- `imu_start_*`: previous settled IMU pose.
- `imu_end_*`: IMU pose after the current GoTo settles.
- `mount_delta_*`: `mount_end - mount_start`.
- `imu_delta_*`: `imu_end - imu_start`.
- `motion_difference_*`: `mount_delta - imu_delta`.
- `motion_backlash_*_arcsec`: absolute per-axis candidate.

The web UI shows a short summary. Detailed records remain available in the
mount-control status and logs for debugging.
