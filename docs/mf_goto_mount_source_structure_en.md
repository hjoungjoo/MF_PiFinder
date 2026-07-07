# MF PiFinder GoTo / Mount Control Source Structure

Baseline: `mf_pifinder` branch, 2026-07-08.

This document maps the current SkySafari LX200 flow and the INDI/OnStep
mount-control flow from the source code. Use it as the baseline when debugging
or improving SkySafari position readout, push-to targets, optional INDI
GoTo/Sync forwarding, Multi Align routing, and guide/manual motion.

## Purpose

PiFinder currently connects several input flows through the shared mount-control
and coordinate-service stack.

1. SkySafari connects to PiFinder over a small LX200-compatible server. The
   default behavior reads PiFinder's current pointing and pushes a selected
   target into PiFinder's recent target list.
2. INDI LX200 OnStepX support can set location/time, park/unpark, slew rate, manual motion, sync, and GoTo through the optional mount-control stack.
3. When enabled, SkySafari `:MS#` GoTo and `:CM#` Sync/Align can be forwarded
   to the mount-control queue.
4. When Multi Align is active, SkySafari GoTo/Align are routed into the active
   alignment session instead of the normal PushTo screen.
5. SkySafari guide buttons are bridged to mount-control manual motion.

The compatibility default is push-to only. INDI mount forwarding runs only when
`mount_control` and the SkySafari INDI options are enabled.

## Runtime Process Structure

`python/PiFinder/main.py` starts the main processes.

```text
main.py
  SharedStateObj
  ├─ GPS monitor process
  ├─ Keyboard process
  ├─ Web server process              -> server.py
  ├─ Camera process
  ├─ IMU process
  ├─ Solver process
  ├─ Integrator process              -> shared_state.solution()
  ├─ SkySafariServer process         -> pos_server.py, TCP 4030
  └─ MountControl process(optional)  -> mountcontrol_indi.py
```

Relevant startup points:

- `python/PiFinder/main.py`
  - SkySafari server: `Process(name="SkySafariServer", target=pos_server.run_server, ...)`
  - INDI mount control: `Process(name="MountControl", target=mountcontrol_indi.run, ...)`
- The mount-control process starts only when the `mount_control` config option is `true`.

## Shared State

### Core Objects

`python/PiFinder/state.py`

- `SharedStateObj`
  - `solution()`: current PiFinder pointing estimate.
  - `solve_state()`: cheap validity flag for the current pointing.
  - `location()`: observer location from GPS or manual load.
  - `datetime()`: PiFinder's current time.
  - `ui_state()`: recent targets, current target, push-to flags.

`python/PiFinder/types/positioning.py`

- `PointingEstimate`
  - Canonical pointing structure.
  - The current telescope direction normally comes from `pointing.aligned.estimate`.
  - `RA`, `Dec`, and `Roll` are in degrees.

### Current Telescope Direction

The current direction is read through:

```python
solution = shared_state.solution()
aligned = solution.pointing.aligned.estimate
current_ra = aligned.RA
current_dec = aligned.Dec
```

This value combines plate solving and IMU dead reckoning.

### Current Observer Location

`shared_state.location()` is updated by:

- GPS lock
- Web Locations `Load Location`
- LCD Locations Load
- manual coordinate entry

Manual locations use sources such as `WEB`, `MANUAL`, or `CONFIG: <name>` and are treated as locked positions. Automatic GPS fixes are prevented from overwriting a manual lock, but a later manual selection is allowed to replace the previous manual location.

## Plate Solve / Push-To Pointing Flow

### Solver and Integrator

`python/PiFinder/solver.py`

- Detects stars in camera frames and builds plate-solve results.
- Successful solves are sent to the solver queue as `SuccessfulSolve`.
- Solve data includes camera-axis and aligned-axis values.

`python/PiFinder/integrator.py`

- Combines solver results and IMU samples into `PointingEstimate`.
- Updates the anchor on successful solves.
- Advances `pointing.aligned.estimate` with IMU dead reckoning between solves.
- Publishes the result with `shared_state.set_solution(...)`.

### Object Details Push-To Screen

`python/PiFinder/ui/object_details.py`

- Compares a target object's RA/Dec with the current pointing.
- `_render_pointing_instructions()` calls `calc_utils.aim_degrees(...)`.

`python/PiFinder/calc_utils.py`

- `aim_degrees(shared_state, mount_type, screen_direction, target)`
  - Alt/Az mount: converts target RA/Dec to Alt/Az for the current time/location and compares with `solution.Alt/Az`.
  - EQ mount: compares target RA/Dec directly with aligned RA/Dec.

## SkySafari LX200 Server

`python/PiFinder/pos_server.py`

SkySafari sees PiFinder as an LX200-style telescope.

- TCP port: `4030`
- Process name: `SkySafariServer`
- Protocol: small subset of Meade LX200 commands
- The socket parser handles multiple LX200 commands in one TCP packet and
  commands split across packets. Sequences such as `:MS#:D#` are processed as
  separate protocol messages.

### Reading Current Position

Command mapping:

```text
:GR# -> get_telescope_ra()
:GD# -> get_telescope_dec()
```

`get_telescope_ra(shared_state, _)`

- Reads the latest `CoordinateState.current` published by
  `PointingCoordinateService`.
- The current source can be solve, IMU fallback, synced mount readback, or
  mount+IMU delta.
- Formats current RA degrees as `HH:MM:SS`.

`get_telescope_dec(shared_state, _)`

- Reads Dec degrees from the same current coordinate.
- Formats it as `+DD*MM'SS`.

From SkySafari's point of view, PiFinder reports the current telescope coordinates.

### Target Selection / Push-To Flow

When the user selects a target and taps GoTo in SkySafari, the typical command sequence is:

```text
:SrHH:MM:SS#     set target RA
:Sd+DD*MM:SS#    set target Dec
:MS#             request slew
```

Current PiFinder behavior:

- `:Sr...#`
  - `parse_sr_command()`
  - Stores target RA in the module-level `sr_result`.
- `:Sd...#`
  - `parse_sd_command()`
  - Stores target Dec in the module-level `sd_result`.
- `:MS#`
  - Calls `handle_slew_command(...)`.
  - Runs `handle_goto_command(...)` with the stored RA/Dec.
  - Returns `"0"` to SkySafari to acknowledge slew start.
- `:D#`
  - Returns a distance-bar byte while the INDI mount status is `slewing`,
    `refine_wait`, or `refine_sent`.
  - Returns an empty LX200 response (`#`) after the mount-control status leaves
    those states, allowing SkySafari to clear its "slewing" indicator.

`handle_goto_command(shared_state, ra_parsed, dec_parsed)`

1. Converts RA/Dec to degrees.
2. Stores the SkySafari target exactly as requested in `last_target_coordinates`.
3. Builds a `CompositeObject`.
   - `catalog_code`: `PUSH`
   - `description`: `Skysafari object nr <sequence>`
4. Adds it to `shared_state.ui_state().add_recent(obj)`.
5. Sets `shared_state.ui_state().set_new_pushto(True)`.
6. Sends `ui_queue.put("push_object")`.
7. Queues an INDI GoTo when mount control and SkySafari INDI GoTo are enabled.
8. While Multi Align is active, routes the target to
   `multipoint_align_goto_target` instead of switching to PushTo.

SkySafari GoTo normally pushes the target into PiFinder's recent target flow; if
enabled, the same requested target is also forwarded to INDI GoTo.

### LCD UI Reaction

`python/PiFinder/main.py`

```python
elif ui_command == "push_object":
    menu_manager.jump_to_label("recent")
```

`python/PiFinder/ui/object_list.py`

- On Recent list activation, checks `ui_state.new_pushto()`.
- If set, refreshes the object list and opens Object Details for the pushed target.

`python/PiFinder/ui/object_details.py`

- `PUSH` catalog objects are considered ready without catalog initialization.
- Existing push-to guidance is then displayed.

## INDI / OnStep Web UI

The web INDI page lives in the Flask server.

`python/PiFinder/server.py`

Routes:

```text
GET  /indi
GET  /indi/current_values
POST /indi/driver
POST /indi/restart
POST /indi/park
POST /indi/slew_rate
POST /indi/motion
POST /indi/location_time
```

Template:

- `python/views/indi_mount.html`

### Web Control Method

The web UI mostly uses `indi_getprop` and `indi_setprop` directly rather than the PyIndi mount-control process.

Helpers in `python/PiFinder/sys_utils.py`:

- `get_indi_onstep_properties(...)`
  - Reads the telescope driver name from the active INDI Web Manager profile.
  - Reads `<active driver>.*` properties using `indi_getprop`.
- `apply_indi_onstep_connection(...)`
  - Configures LX200 OnStep USB/network connection properties.
- `apply_indi_onstep_properties(...)`
  - Applies property lists with `indi_setprop`.
- `restart_indi_web_manager(...)`
  - Restarts `indiwebmanager.service`.
- `connect_indi_onstep_driver(...)`
  - Applies `CONNECTION.CONNECT=On`.
- `apply_indi_onstep_location_time(...)`
  - Primary location/time sync path with the active INDI telescope driver.
  - Uses PyIndi full-vector updates for `GEOGRAPHIC_COORD` and `TIME_UTC`.
- `sync_onstep_location_time_exclusive(...)`
  - OnStep-only legacy/fallback path.
  - Stops INDI Web Manager, sends direct OnStep LX200 TCP/serial commands, starts INDI again, and reconnects the driver.

### Location / Time Sync

`POST /indi/location_time`

Current flow:

1. Reads form `latitude`, `longitude`, `elevation`, and `utc_time`.
2. Recalculates PiFinder UTC when the server receives the request.
3. Runs `apply_indi_onstep_location_time(...)`.
4. The helper connects to the running INDI server with PyIndi and updates the
   complete `GEOGRAPHIC_COORD` and `TIME_UTC` vectors.
5. With the LX200 OnStepX driver, the driver converts the INDI longitude/time
   conventions to OnStep LX200 commands and preserves seconds plus elevation.

Important conventions:

- Do not use `indi_setprop` CLI one-element writes for `GEOGRAPHIC_COORD` or
  `TIME_UTC`; testing showed that it can zero unspecified vector elements.
  PiFinder uses PyIndi full-vector updates instead.
- Direct LX200 sync remains available only as an OnStep-specific fallback when
  the driver cannot be trusted.
- OnStep `:SG` is the value added to local time to obtain UTC. Korea is
  therefore `-09:00`, which is the opposite sign convention from INDI
  `TIME_UTC.OFFSET=+9.00`.
- INDI raw longitude uses 0..360 eastward degrees.
- OnStep Web UI may display longitude with a different sign convention.
- The PiFinder UI keeps these values separate.
  - `OnStep Location`: DMS display matching the OnStep web UI. With the fixed driver this includes seconds and elevation.
  - `Effective Coordinates`: decimal coordinates future feature code should use. This prefers PiFinder's successfully synced high-precision location and falls back to INDI driver readback.
  - `INDI Driver Readback`: raw values reported directly by the INDI driver.

### Web Manual Motion

`POST /indi/motion`

- Pressing a direction button enables `TELESCOPE_MOTION_NS` and/or `TELESCOPE_MOTION_WE`.
- Releasing sends `TELESCOPE_ABORT_MOTION.ABORT=On`.
- The browser sends keepalives, and the server uses a motion lease timer as a safety stop.

## INDI MountControl Process

`python/PiFinder/mountcontrol_indi.py`

This is optional and starts only when `mount_control` is enabled.

### Communication Path

```text
LCD UI / Object Details / INDI Guide
  -> mountcontrol_queue.put(command dict)
  -> MountControlIndi.handle_command()
  -> PyIndi client
  -> INDI server localhost:7624
  -> LX200 OnStep driver
  -> OnStep mount
```

### Status File

MountControl writes:

```text
~/PiFinder_data/mount_control_status.json
```

Readers:

- LCD top bar status: `python/PiFinder/ui/base.py`
- LCD INDI status page: `python/PiFinder/ui/indi.py`

### Command Dicts

Handled by `MountControlIndi.handle_command(...)`:

```text
{"type": "init"}
{"type": "restart_driver"}
{"type": "sync", "ra": <deg>, "dec": <deg>}
{"type": "goto_target", "ra": <deg>, "dec": <deg>}
{"type": "stop_movement"}
{"type": "manual_movement", "direction": "...", "lease_seconds": ...}
{"type": "manual_movement_keepalive", "direction": "...", "lease_seconds": ...}
{"type": "increase_slew_rate"}
{"type": "reduce_slew_rate"}
{"type": "set_slew_rate", "rate": 0..9}
{"type": "refresh_slew_rate"}
{"type": "sync_location_time"}
{"type": "park_action", "action": "park|unpark|set_home|return_home|set_park"}
{"type": "multipoint_align_start", "mode": "manual|auto", "points": 1..9}
{"type": "multipoint_align_select_star", "star_name": "...", "goto": true|false}
{"type": "multipoint_align_goto_target", "ra": <deg>, "dec": <deg>, "name": "..."}
{"type": "multipoint_align_confirm", "source": "ui|skysafari|web"}
{"type": "multipoint_align_clear_target"}
{"type": "multipoint_align_cancel"}
```

### PyIndi Client

`PiFinderIndiClient`

- Connects to the INDI server.
- Auto-detects a telescope-like device.
- Receives `EQUATORIAL_EOD_COORD` updates and writes current mount RA/Dec to status.
- Provides helper methods for number, switch, and text properties.

### GoTo

`MountControlIndi.goto_target(ra_deg, dec_deg)`

1. Calls `connect()` to prepare the INDI server and telescope device.
2. Sets `ON_COORD_SET.SLEW=On`.
3. Sets `EQUATORIAL_EOD_COORD.RA=<ra_hours>` and `DEC=<dec_deg>`.
4. Writes `state="slewing"`, `target_ra`, and `target_dec` to status.
5. The mount-control loop watches INDI busy state and writes
   `state="connected"` with `GoTo complete` when the slew finishes.

RA input is degrees and is sent to INDI as hours.

```python
{"RA": (ra_deg % 360.0) / 15.0, "DEC": dec_deg}
```

### Sync

`MountControlIndi.sync_mount(ra_deg, dec_deg)`

1. Sets `ON_COORD_SET.SYNC=On`.
2. Sends current solve RA/Dec to `EQUATORIAL_EOD_COORD`.
3. Sets `ON_COORD_SET.TRACK=On`.
4. Turns tracking on.
5. Writes the current mount position to status.

### Manual Motion

`MountControlIndi.manual_move(direction, lease_seconds)`

- Sends OnStep INDI motion properties directly.
- Some East/West mapping is intentionally reversed internally to match the visual direction used by the UI.
- If the lease expires, `stop_mount()` is called automatically.
- SkySafari guide commands are managed by an additional keepalive timer in
  `pos_server.py`. It queues `manual_movement_keepalive` every 0.4 seconds and
  a fresh `manual_movement` every 8 seconds so the mount-control 10-second
  continuous-motion guard does not stop a held SkySafari button.
- A SkySafari TCP command connection closing is not treated as a stop. Motion
  stops on `:Q#`, `:Qn#`, `:Qs#`, `:Qe#`, `:Qw#`, or the 60-second safety limit.

## LCD INDI UI

`python/PiFinder/ui/menu_structure.py`

Current menu path:

```text
Start
  INDI
    STATUS
    INIT
      Connect / Init
      Send Location/Time
      Park
      Unpark
      Set Home
      Return Home
      Set-Park
      Restart INDI
    Guide
```

Screen implementation:

- `python/PiFinder/ui/indi.py`
  - `UIIndiStatus`
  - `UIIndiGuide`
  - `UIIndiBase`
- `UIIndiInit` exists, but the current menu uses a `UITextMenu` for INIT actions.

### STATUS

`UIIndiStatus`

- Reads `mount_control_status.json`.
- Displays state, message, age, device, RA, Dec, speed, step, and target RA/Dec.

### INIT

Current implementation is callback-based.

`python/PiFinder/ui/callbacks.py`

- `indi_init`
- `indi_sync_location_time`
- `indi_park`
- `indi_unpark`
- `indi_set_home`
- `indi_return_home`
- `indi_set_park`
- `indi_restart_driver`

Each callback sends a command dict to the mount-control queue.

### Guide

`UIIndiGuide`

- Uses the camera image as the background.
- Numeric keys and text keys perform manual motion.
- Numeric layout:

```text
7 8 9
4   6
1 2 3
```

- `+`, `-`: change slew rate.
- `Square`: sync mount to the current PiFinder solve.
- Key press starts motion; key release stops motion.
- Keepalive and lease logic reduce the risk of motion continuing through UI freeze.

## INDI GoTo from Object Details

`python/PiFinder/ui/object_details.py`

When Mount Control is enabled, Object Details numeric keys send mount-control commands.

Current mapping:

```text
0 stop
1 init + sync current solve if available
2 south
3 reduce step
4 west
5 GoTo current object
6 east
7 sync current solve
8 north
9 increase step
```

Key `5` is the existing internal PiFinder target-to-INDI-GoTo path.

```python
mountcontrol_queue.put({
    "type": "goto_target",
    "ra": self.object.ra,
    "dec": self.object.dec,
})
```

Therefore catalog objects, observing-list objects, and SkySafari `PUSH` objects can all use the same GoTo path once they are shown in Object Details.

## Current SkySafari Push-To / INDI Forwarding / Multi Align Routing

### Default SkySafari Push-To Path

```text
SkySafari target selected
  -> LX200 :Sr / :Sd
  -> pos_server.handle_goto_command()
  -> CompositeObject(catalog_code="PUSH")
  -> ui_state.recent
  -> ui_queue "push_object"
  -> LCD Object Details
  -> push-to guidance
```

This remains the compatibility path when `mount_control` is off or SkySafari
INDI GoTo forwarding is disabled.

### SkySafari INDI GoTo Forwarding Path

```text
SkySafari target selected
  -> LX200 :Sr / :Sd / :MS
  -> pos_server.handle_goto_command()
  -> normal Push-To target storage
  -> mountcontrol_queue {"type": "goto_target", "ra": target.ra, "dec": target.dec}
  -> MountControlIndi.goto_target()
  -> INDI EQUATORIAL_EOD_COORD
  -> active INDI telescope driver
  -> mount GoTo
```

Requirements:

- `mount_control` must be enabled.
- SkySafari INDI GoTo forwarding must be enabled.
- The target coordinate is used exactly as received from SkySafari.

### SkySafari Sync/Align Forwarding Path

```text
SkySafari :CM#
  -> pos_server.handle_sync_command()
  -> choose current :Sr/:Sd or last target coordinate
  -> PiFinder solved/IMU alignment handling
  -> if enabled, mountcontrol_queue {"type": "sync", ...}
```

This is the normal Sync/Align flow when Multi Align is inactive. When Multi
Align is active, Multi Align routing has priority.

### Multi Align Active Path

```text
SkySafari :Sr / :Sd / :MS
  -> multipoint_align_goto_target
  -> GoTo the selected alignment target

SkySafari :CM#
  -> multipoint_align_confirm
  -> confirm the latest GoTo target as an alignment point
```

In this path the SkySafari target does not jump to the normal Object Details
PushTo screen. The active session is detected from
`mount_control_status.json.multipoint_align.active`.

### PiFinder Internal INDI GoTo Path

```text
PiFinder Object Details target
  -> number key 5
  -> mountcontrol_queue {"type": "goto_target", "ra": target.ra, "dec": target.dec}
  -> MountControlIndi.goto_target()
  -> INDI EQUATORIAL_EOD_COORD
  -> LX200 OnStep driver
  -> OnStep mount GoTo
```

Object Details key `5` remains the PiFinder internal target-to-mount path and is
separate from SkySafari forwarding.

## Future Extension Points

### 1. Add Target GoTo to the Web INDI Page

Candidates:

- `python/PiFinder/server.py`
- `python/views/indi_mount.html`

Possible UI:

- Current PiFinder target.
- Current SkySafari PUSH target.
- `GoTo Current Target`
- `Sync Mount to Current Solve`
- `Stop`

Decision needed:

- Use direct `indi_setprop`, or route GoTo/Sync/Stop through the mount-control queue.

Recommendation:

- Long term, GoTo/Sync/Stop should share the mount-control queue.
- Keep direct `indi_setprop` for driver setup, status, fallback, and simple manual controls.

### 2. Improve Object Details GoTo Confirmation

Candidate:

- `python/PiFinder/ui/object_details.py`

Potential safety features:

- Confirmation before GoTo.
- Low altitude warning.
- Parked-state warning or auto-unpark prompt.
- Slewing overlay with stop/abort.
- Show current mount RA/Dec vs target delta after GoTo.

### 3. Unify Status Model

Currently there are two important positions:

- PiFinder pointing: `shared_state.solution()`
- INDI mount position: `mount_control_status.json` `ra`, `dec`

Future GoTo UI should show both.

Example:

```text
PiFinder solve: RA/Dec
Mount reported: RA/Dec
Target: RA/Dec
Delta solve-target
Delta mount-target
```

## Risk Areas

### Port Conflicts

OnStep TCP/serial ports can become unstable when multiple clients share them.

- Avoid direct LX200 TCP/serial commands while the INDI LX200 OnStep driver is connected.
- If direct commands are required, use the exclusive pattern in `sync_onstep_location_time_exclusive(...)`: stop INDI, send direct commands, start INDI again.

### Coordinate Basis

`pos_server.py` now uses SkySafari/LX200 target coordinates exactly as
requested.

- `:Sr/:Sd` target coordinates are stored directly in `last_target_coordinates`.
- `:MS#`, `:CM#`, and Multi Align confirm use the same target coordinates.
- `pointing.aligned.estimate` is used by the coordinate service without epoch
  conversion.
- Alt/Az conversion is only used for IMU correction, display, or mount-type
  interpretation.

If GoTo/Sync accuracy is off, first check that the requested target is not being
reinterpreted into another coordinate frame, then check mount readback and IMU
smoothing status.

### Longitude Convention

OnStep Web UI and INDI raw longitude may display different sign conventions.

- PiFinder ordinary locations: east-positive decimal degrees.
- INDI LX200 OnStep raw longitude: 0..360 eastward.
- OnStep Web UI may look west-positive.

Relevant helpers:

- `sys_utils.onstep_longitude_degrees(...)`
- `sys_utils.onstep_web_longitude_degrees(...)`
- `sys_utils.format_onstep_location_display(...)`

### Motion Safety

Manual guide uses both press/release events and lease timeouts.

- Web UI: JS pointer release + server timer.
- LCD UI: key release + mount-control lease.
- On freeze, lease expiry triggers stop retries.

Any automatic GoTo feature must keep stop/abort easily available.

## Config

`default_config.json`

```json
"mount_control": false,
"mount_control_indi_host": "localhost",
"mount_control_indi_port": 7624,
"onstep_connection_type": "network",
"onstep_serial_port": "",
"onstep_network_host": "",
"onstep_network_port": 9999
```

Meanings:

- `mount_control`: enable the optional LCD mount-control process.
- `mount_control_indi_host`, `mount_control_indi_port`: INDI server address.
- `onstep_connection_type`: LX200 OnStep driver connection type.
- `onstep_serial_port`: USB serial port.
- `onstep_network_host`, `onstep_network_port`: OnStep TCP host/port.

## Install / Services

Install docs:

- `docs/mf_indi_mount_install_ko.md`
- `docs/mf_indi_mount_install_en.md`

Install script:

- `scripts/install_indi_mount.sh`

Services:

- `pifinder.service`
- `indiwebmanager.service`

Default ports:

- PiFinder web: 80, or 8080 fallback
- SkySafari LX200 server: 4030
- INDI server: 7624
- INDI Web Manager: 8624
- OnStep TCP: 9999

## Tests

Current relevant tests:

- `python/tests/test_sys_utils.py`
  - INDI location/time property conversion.
  - direct OnStep LX200 command conversion.
  - longitude display convention.
- `python/tests/test_mountcontrol_indi.py`
  - mount-control command handling and status.
- `python/tests/test_main.py`
  - manual location reload over an existing manual lock.
- `python/tests/skysafari.py`
  - SkySafari LX200 server stress client.

Suggested future tests:

- `pos_server.py` `:Sr`, `:Sd`, `:MS` ordering.
- Preserve default SkySafari push-to compatibility.
- When SkySafari INDI GoTo forwarding is enabled, verify that the mount-control
  queue receives the expected `goto_target` command.
- When mount control is off, verify SkySafari remains push-to only.
- Verify that SkySafari target coordinates are stored and forwarded as requested
  without coordinate-frame conversion.
- Stop/abort priority while GoTo or manual motion is active.

## Current Conclusion

The current structure supports these modes:

```text
push_to       Default behavior. Push target into PiFinder recent/Object Details.
goto_forward  When enabled, forward SkySafari :MS# to INDI GoTo as well.
sync_forward  When enabled, forward SkySafari :CM# to INDI Sync/Align.
multi_align   While Multi Align is active, route SkySafari GoTo/Align into the session.
guide_bridge  Bridge SkySafari guide buttons to INDI manual motion.
```

Future features should preserve the boundary where `pos_server.py` interprets
target/guide commands and `mountcontrol_indi.py` owns driver I/O and status
publication. SkySafari coordinate replies should continue to read the latest
`PointingCoordinateService` `CoordinateState.current` rather than recalculating
operation-specific coordinates in the POS server.
