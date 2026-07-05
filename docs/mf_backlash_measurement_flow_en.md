# MF PiFinder Backlash Measurement Flow

This document describes the `compass_goto_loop` automatic measurement mode used
by INDI > Settings > Backlash. The internal mode name remains
`compass_goto_loop` for compatibility, but the actual motion command uses INDI
standard timed pulse guiding instead of GoTo.

## Core Rules

- The calculation method remains unchanged.
- Only the commanded motion method changes.
- The previous motion pattern moved both mount axes by `+offset` according to the
  mount type.
- The revised motion pattern moves only one active axis at a time.
- GoTo is not used for the backlash measurement moves because OnStep/INDI can
  automatically enable tracking after GoTo.
- Actual movement is sent through `TELESCOPE_TIMED_GUIDE_NS/WE` timed pulse
  guide commands.
- Alt/Az mounts run separate `AZ` and `ALT` axis tests.
- EQ mounts run separate `RA` and `DEC` axis tests.
- The active axis moves by a pulse-guide duration calculated from the configured
  offset, while the inactive axis is not commanded.
- Mount travel is still calculated as
  `actual mount readback after pulse - actual mount readback from the previous settled record`.
- IMU travel is still calculated as
  `IMU after pulse - IMU before that leg`.
- Signed motion error is still `mount travel - IMU travel`.
- The backlash candidate is still the absolute value of the signed motion error
  converted to arc-seconds.
- Legs where the mount-vs-IMU travel difference is at least 1 degree are still
  excluded from statistics because they may indicate an IMU jump or bad sampling
  moment.
- Remaining values are still sorted, the lowest 30% and highest 30% are
  discarded, and the middle 40% mean is used as the recommendation.

In short: filtering, statistics, and recommendations do not change. The test only
changes the physical motion to one-axis timed pulse guiding so tracking does not
get enabled by GoTo.

## Pulse Guide Defaults

```text
offset = 2.0 degrees
INDI GUIDE_RATE = 96x sidereal (OnStepX maps this to rate selector 8 / Half-Max)
2-degree pulse duration = about 4.99 seconds
default repeat count = 10, adjustable from 1 to 50 in the web UI
settle wait after pulse completion = 0.5 seconds
pause between repeats = 1.0 second
```

Pulse duration is calculated as:

```text
duration_seconds = offset_deg / (rate_multiplier * 15.041067 / 3600)
```

PiFinder keeps the motion command itself on `TELESCOPE_TIMED_GUIDE_*`, then sets
INDI `GUIDE_RATE` to 96x sidereal before the test starts. The OnStepX driver maps
that value to OnStep rate selector 8, Half-Max. The real physical speed can
depend on the mount configuration, so PiFinder estimates it as 96x sidereal for
test timing.

Note: if the OnStep firmware has `GUIDE_SEPARATE_PULSE_RATE` enabled and limits
`pulseRateSelect` to 1x or slower, `TELESCOPE_TIMED_GUIDE_*` will not actually
run at 96x. PiFinder verifies the `GUIDE_RATE` readback after setting it, and
does not start the test if the requested rate was not applied.

## Per-Axis Expected Motion Points

### Alt/Az Mounts

Alt/Az mode follows the OnStep Axis1/Axis2 naming order:

```text
Axis1 = AZ
Axis2 = ALT
```

For a start point of `Alt 10, Az 20` and an offset of 2 degrees:

```text
AZ-axis test:
  S_az = Alt 10, Az 20
  T_az = Alt 10, Az 22

ALT-axis test:
  S_alt = Alt 10, Az 20
  T_alt = Alt 12, Az 20
```

During the AZ-axis test, only the AZ pulse direction is commanded. During the
ALT-axis test, only the ALT pulse direction is commanded. Inactive-axis movement
is not removed from the records; it remains visible for diagnosing mechanical
coupling, coordinate conversion issues, or IMU sampling problems.

### EQ Mounts

EQ mode uses this order:

```text
Axis1 = RA
Axis2 = DEC
```

For a start point of `RA 100, DEC 20` and an offset of 2 degrees:

```text
RA-axis test:
  S_ra = RA 100, DEC 20
  T_ra = RA 102, DEC 20

DEC-axis test:
  S_dec = RA 100, DEC 20
  T_dec = RA 100, DEC 22
```

During the RA-axis test, only the RA pulse direction is commanded. During the
DEC-axis test, only the DEC pulse direction is commanded.

## Flow

```text
[Start]
  |
  v
[Check IMU Compass mode]
  |
  +-- Off or not using NDOF --> [Ask for Compass On] -> [Restart PiFinder] -> [End]
  |
  v
[Wait for IMU MAG calibration = 3]
  |
  +-- Not ready --> [User moves/rotates device] -> [Wait for Continue]
  |
  v
[Connect to INDI]
  |
  v
[Check mount state]
  |
  +-- Parked --> [Report Unpark required] -> [End]
  |
  +-- Tracking On --> [Turn Tracking Off]
  |
  v
[Set INDI GUIDE_RATE = 96x (OnStepX Half-Max)]
  |
  v
[Read current IMU coordinates]
  |
  v
[Sync mount coordinates to current IMU coordinates]
  |
  +-- Action:
  |      Convert IMU Alt/Az to current-time/current-location RA/DEC
  |      Sync the INDI mount to that RA/DEC
  |      Turn Tracking back Off because Sync may enable it
  |
  v
[Read current mount coordinates]
  |
  v
[Choose active-axis list]
  |
  +-- Alt/Az mount: AZ -> ALT
  |
  +-- EQ mount: RA -> DEC
  |
  v
<For each active axis>
  |
  v
[Prepare active-axis safe start point]
  |
  +-- Alt/Az mount:
  |      active = AZ: logical west pulse moves Az - offset
  |      active = ALT: logical south pulse moves Alt - offset
  |
  +-- EQ mount:
         active = RA: logical west pulse moves RA - offset
         active = DEC: logical south pulse moves DEC - offset
  |
  v
[Pulse Guide INIT]
  |
  v
[Wait for pulse duration and 0.5-second settle]
  |
  +-- Not recorded for statistics:
  |      INIT only moves the active axis to a safe test start point.
  |
  v
[Read current mount coordinates again]
  |
  v
[Calculate fixed active-axis command points S/T]
  |
  +-- Alt/Az mount:
  |      active = AZ:
  |        S = current Alt, current Az
  |        T = expected current Alt, current Az + offset
  |
  |      active = ALT:
  |        S = current Alt, current Az
  |        T = expected current Alt + offset, current Az
  |
  |      Actual commands are logical east/north or west/south timed pulses
  |
  +-- EQ mount:
         active = RA:
           S = current RA, current DEC
           T = expected current RA + offset, current DEC

         active = DEC:
           S = current RA, current DEC
           T = expected current RA, current DEC + offset

         Actual commands are logical east/north or west/south timed pulses
  |
  v
[Store initial record]
  |
  +-- Stored:
  |      active_axis
  |      current mount RA/DEC and Alt/Az
  |      current IMU Alt/Az and IMU RA/DEC when conversion is possible
  |      label = initial <active axis>
  |
  v
[Warm-up: S -> T]
  |
  v
[Wait 0.5 seconds for settle and store mount/IMU record]
  |
  +-- Used as a record but excluded from statistics:
  |      active_axis retained
  |      label = offset initial <active axis>
  |      direction = offset
  |      warmup = true
  |
  v
<Repeat N times for this active axis, default 10>
  |
  +--> [Return leg: active-axis -offset pulse from T toward S]
  |       |
  |       v
  |     [Use previous settled record A as the start value]
  |       |
  |       v
  |     [Logical west or south pulse]
  |       |
  |       v
  |     [Wait for pulse duration and 0.5-second settle]
  |       |
  |       v
  |     [Store mount/IMU record B]
  |       |
  |       v
  |     [Run the existing calculation]
  |       - mount_delta = actual mount B - mount coordinates from previous settled record A
  |       - imu_delta = IMU B - IMU coordinates from previous settled record A
  |       - signed_error = mount_delta - imu_delta
  |
  +--> [Offset leg: active-axis +offset pulse from S toward T]
          |
          v
        [Use previous settled record B as the start value]
          |
          v
        [Logical east or north pulse]
          |
          v
        [Wait for pulse duration and 0.5-second settle]
          |
          v
        [Store mount/IMU record A]
          |
          v
        [Run the existing calculation]
          - mount_delta = actual mount A - mount coordinates from previous settled record B
          - imu_delta = IMU A - IMU coordinates from previous settled record B
          - signed_error = mount_delta - imu_delta
  |
  v
[Continue with the next active axis]
  |
  v
[After all axes, run the existing statistics]
  |
  +-- Exclude warm-up and degraded IMU-heading legs
  +-- Exclude legs with mount-vs-IMU travel difference >= 1 degree
  +-- Sort direction/axis values low to high
  +-- Drop lowest 30%
  +-- Drop highest 30%
  +-- Use the remaining middle 40% mean as the recommendation
  +-- Per-axis movement-direction statistics:
      Alt/Az: AZ+, AZ-, ALT+, ALT-
      EQ: RA+, RA-, DEC+, DEC-
  |
  v
[Show detailed leg data and summary; export CSV]
  |
  v
[Restore original INDI GUIDE_RATE]
  |
  v
[Restore original Tracking state]
  |
  v
[End]
```

## Key CSV Fields

- `active_axis`: the axis intentionally moved by this leg. Alt/Az uses `az` or
  `alt`; EQ uses `ra` or `dec`.
- `movement_frame`: `altaz` or `radec`.
- `command_start_altitude`, `command_start_azimuth`: fixed command start point
  for the leg. This is recorded for checking only and is not used for
  mount-travel calculation.
- `target_altitude`, `target_azimuth`: fixed command target point for the leg.
- `mount_start_altitude`, `mount_start_azimuth`: start-record coordinates for
  the current leg. This is the settled record saved after the previous pulse,
  not a newly captured pre-pulse sample.
- `mount_end_altitude`, `mount_end_azimuth`: actual mount readback after the
  pulse move.
- `mount_delta_altitude`, `mount_delta_azimuth`: actual mount end minus the
  actual mount coordinates from the previous settled record.
- `imu_delta_alt`, `imu_delta_az`: IMU travel across the leg.
- `motion_difference_alt_arcsec`, `motion_difference_az_arcsec`: signed motion
  error.
- `motion_backlash_alt_arcsec`, `motion_backlash_az_arcsec`: backlash candidate
  from absolute signed error.
- `raw_estimated_arcsec`: combined signed-error magnitude. Values at or over
  1 degree are excluded from statistics.
- `motion_difference_threshold_rejected`: whether the leg was excluded by the
  1-degree threshold.
- `motion_difference_threshold_rejected_axes`: axes that crossed the threshold.
- `direction_stats.*.recommended_trimmed_mean`: unchanged calculation; mean of
  the middle 40% after the 1-degree threshold and 30% low/high trim.
- `axis_direction_stats.*.recommended_trimmed_mean`: unchanged calculation;
  recommendation split by active axis movement direction. Alt/Az mounts report
  `AZ+`, `AZ-`, `ALT+`, and `ALT-`; EQ mounts report `RA+`, `RA-`, `DEC+`, and
  `DEC-`.

Azimuth and RA use the shortest angular delta across the 0/360-degree boundary.
