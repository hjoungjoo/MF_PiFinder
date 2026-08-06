# MF_PiFinder Source Change History

Date: 2026-06-25
Last updated: 2026-08-05

This document records the source changes applied inside the PiFinder repository
to make the `mf_pifinder` branch work on Raspberry Pi CM5, Raspberry Pi 4, and
Raspberry Pi 5-class Bookworm 64-bit hardware.

For a concise reference used when syncing future upstream source changes and
reapplying MF-specific patches, see `docs/mf_dev/mf_upstream_patch_reference_en.md`.

Scope:

- Code and documentation inside the PiFinder repository
- PiFinder changes for CM5/Pi4/Pi5, Bookworm, IMX462, and SSD1351 OLED support
- Detail useful for future review or possible upstreaming

Not covered:

- Debian package installation
- OS network configuration
- Wiring changes
- Reboots, service start/stop operations, and other runtime procedures
- Intermediate test values and discarded settings

## Work-Area Table of Contents and PR Status

> **Note (2026-07-23):** the PR status below and the "PR Reorganization Proposal"
> are a 2026-06-27 snapshot and no longer current. While this fork is pre-release
> we commit/push directly to `main` (CLAUDE.md pre-release exception, 2026-07-21);
> the branch/PR flow resumes from the first post-release fix. Treat the table
> below as a per-feature scope history only.

Status baseline: open `hjoungjoo` Draft PRs in `brickbots/PiFinder` and the local
`mf_pifinder` integration branch as of 2026-06-27.

| Work area | Current status | PR/branch | Main scope |
| --- | --- | --- | --- |
| Bookworm install and path foundation | Draft PR exists | [#499](https://github.com/brickbots/PiFinder/pull/499), `pr/bookworm-install-foundation` | `pifinder_paths.sh`, install/update/migration scripts, systemd units, Bookworm path docs |
| Raspberry Pi 4/5/CM5 board and GPS/UART profile | Draft PR exists | [#505](https://github.com/brickbots/PiFinder/pull/505), `pr/board-gps-uart-profile` | `board_config.py`, `gps_port=auto`, GPSD device/baud sync, GPS Port menu |
| Camera preview/focus/gain control | Draft PR exists | [#501](https://github.com/brickbots/PiFinder/pull/501), `pr/focus-gain-preview` | focus preview, bright-background threshold, camera gain profile/runtime control, LCD preview script |
| Korean UI localization | Draft PR exists | [#500](https://github.com/brickbots/PiFinder/pull/500), `pr/korean-localization` | `python/locale/ko`, `ko` language menu entry, CJK font/restart handling |
| Bluetooth/USB HID keyboard support | Draft PR exists | [#506](https://github.com/brickbots/PiFinder/pull/506), `pr/bluetooth-keyboard-support` | libinput key mapping, text-entry keycodes, Bluetooth keyboard scan/pair/connect UI, reconnect |
| INDI mount control | Draft PR exists | [#503](https://github.com/brickbots/PiFinder/pull/503), `pr/indi-mount-control` | optional INDI mount process, object details sync, LX200 OnStepX custom-driver patch, install script, INDI docs |
| INDI Multi Align shared flow | No Draft PR yet | local `mf_pifinder` worktree | shared `MultiPointAlignController`, Web/LCD/SkySafari session unification, deferred OnStepX native align start, stale native align `:SX09,0#` reset, PiFinder coordinate sync verification, recoverable GoTo failure/target clear, real OnStepX hardware testing |
| Integrated GPS/NTP/RTC/software PPS time sync | Draft PR exists | [#504](https://github.com/brickbots/PiFinder/pull/504), `pr/time-sync-sources` | GPS/NTP best-source selection, helper service, dry-run/real clock sync, status UI, time-sync docs |
| Wi-Fi AP+STA simultaneous mode and AP settings | No Draft PR yet | local `mf_pifinder` worktree | `wlan0` STA + `uap0` AP, STA channel tracking, STA band preference, configurable AP IP, AP WPA2 password setting, AP+STA internet sharing option, OS Wi-Fi profile import, scanned SSID selection, shared Pi 4/5 Wi-Fi mode |
| Locations catalog | No Draft PR yet | local `mf_pifinder` worktree | GeoNames-based offline location catalog, country/region/district/city selection, coordinate/altitude/source prefill, North Korea excluded |
| Web UI red night theme and PWA fullscreen app mode | No Draft PR yet | local `mf_pifinder` worktree | red night theme, per-browser theme storage, PWA manifest, service worker, PWA icons |
| Optional IMU compass heading | No Draft PR yet | local `mf_pifinder` worktree | optional BNO055 NDOF magnetometer fusion mode, IMU calibration status, automatic calibration save/load, manual calibration menu |
| SkySafari/INDI mount-mode compatibility | No Draft PR yet | local `mf_pifinder` worktree | Alt/Az/EQ SkySafari LX200 status, optional GoTo/Sync forwarding, SkySafari guide keepalive bridge, no-solve IMU alignment correction, mount-mode compatibility checklist |
| Pointing Coordinate Service | No Draft PR yet | local `mf_pifinder` worktree | shared coordinate service for SkySafari/Web/LCD/INDI Multi Align, requested target coordinates used as-is, IMU smoothing, mount readback priority during GoTo/manual motion, Reset Pointing discards the SkySafari IMU alignment correction and re-syncs the mount to the raw IMU coordinate; mount-motion isolation hardening (INDI 2.x updateProperty 1 Hz readback, leak rollback, raw-IMU delta tracking, post-motion settle gate; 2026-07-17, see `mf_coordinate_helper_plan`) |
| INDI GoTo/Guide service | No Draft PR yet | local `mf_pifinder` worktree | separate `indi_goto_guide_service` process (GoTo/Guide policy state machine) layered over `mountcontrol_indi` executor; SkySafari GoTo routed through the service; PiFinder-driven manual approach loop + correction pass with final INDI GoTo; tracking guide target managed by the service; tracking-guide disturbance recovery (settle-detect then 3° banded pulse-guide vs sync+GoTo re-acquisition, separate GoTo-recovery On/Off); web GoTo/Guide status panel + LCD GoTo Recovery toggle; 10-deg recovery error cap removed and GoTo Type label unified (2026-07-17); SkySafari/GoTo settings reorganization (2026-07-19: GoTo Type gained `off` as the single GoTo-forwarding switch; `skysafari_indi_goto` and `indi_goto_refine_once` options and the LCD guide-screen key-5 Refine toggle removed; `skysafari_indi_sync` defaults to on; pre-solve SkySafari Align IMU alignment always on; Refine Accuracy input moved to GoTo/Guide settings; SkySafari Mount Mode card moved above GoTo/Guide settings; Object Details key-5 GoTo routed through the GoTo/Guide service); tracking-frequency policy unified (2026-07-20: web catalog push, LCD keypad 5, and SkySafari `:MS#` all share `track_freq_policy`; SkySafari carries no object type so its target is matched against the planet ephemeris by position [6' tolerance, `skysafari_planet_track_freq` default on] while web/LCD keep using `obj_type`; per-path queue guards fixed a defect that silently dropped multi-point align GoTos when the GoTo/Guide queue was absent; `unit` markers added across `test_pos_server.py`; a matching-only `planet_positions_of_date()` computes equinox-of-date positions, fixing the ~22' precession mismatch between SkySafari (JNow) and `calc_planets()` (J2000) by moving the ephemeris to the request's frame rather than converting the requested coordinate; `TrackFreqPolicy: INFO` added to the diagnostic `logconf_indi.json` -- see `mf_web_catalogs_dev_ko` P6-2); see `mf_indi_goto_guide_plan` and `mf_goto_mount_source_structure` |
| LiveCam RAW preview and live stack | No Draft PR yet | local `mf_pifinder` worktree | `raw_live_stack`/`livecam_config` RAW preview and rolling live-stack kept off the solver path; camera backends publish one RAW frame, web API renders display PNG/JPEG/WebP; stack modes (mean/sum/max), sizing/zoom, reset-defaults control; web camera exposure/gain controls (`camera_controls`, `/api/camera/controls`, `camera_command_queue` wired to the web process); see `mf_raw_live_stack_plan` |
| Change history and PR regrouping documentation | No Draft PR yet | local `mf_pifinder` worktree | this document's work-area table of contents, PR status, and regrouping guidance |
| Final integration branch | Not an upstream PR | `origin/mf_pifinder` plus local uncommitted changes | integration branch used for install and hardware testing across the features above |

## Suggested PR Regrouping

The current Draft PRs were split very narrowly, which makes review context harder
to follow. The following grouping is easier to maintain.

| Suggested new PR group | Include | Existing Draft PR handling |
| --- | --- | --- |
| Platform/Bookworm/RPi4-RPi5 compatibility | Bookworm install/path foundation + board/GPS UART profile | Combine #499 and #505, or expand #499 and close #505 |
| Camera usability | focus preview, camera gain, camera LCD preview | Keep #501 or expand it with camera-specific docs |
| Input devices | Bluetooth keyboard, USB HID key mapping, keyboard mapping docs | Use #506 as the base |
| Optional INDI mount integration | INDI mount process, install script, object sync, INDI keyboard mapping notes | Keep #503 |
| INDI Multi Align refinement | shared Multi Align session controller, OnStepX stale align reset, Web/LCD/SkySafari flow docs | Include in the INDI PR or split into an OnStepX advanced-functions PR |
| Integrated time sync | GPS/NTP/RTC/software PPS, helper service, status UI | Keep #504 |
| Network connectivity | AP/Client/AP+STA Wi-Fi modes, virtual AP services, STA band preference, configurable AP IP, AP security/password, optional AP+STA internet sharing, OS Wi-Fi profile import, scanned SSID selection, web/device network UI | New Draft PR needed |
| Locations catalog | GeoNames-based offline location catalog, country/region/district/city selector, coordinate prefill | New Draft PR needed |
| Web observing UI | red night theme, PWA/fullscreen app mode | New Draft PR needed |
| Optional IMU compass heading | BNO055 NDOF option, automatic/manual calibration, status UI | New Draft PR needed |
| SkySafari/INDI mount-mode compatibility | Alt/Az/EQ SkySafari LX200 status, SkySafari GoTo/Sync forwarding, guide keepalive bridge, no-solve IMU correction, INDI mount-mode verification docs | New Draft PR needed |
| Pointing Coordinate Service | always-running coordinate service combining `pointing.aligned.estimate`, IMU fallback, and INDI mount readback; SkySafari coordinate response; mount progress readback during GoTo/manual motion | Include in SkySafari/INDI mount-mode PR or split as a coordinate-service PR |
| INDI GoTo/Guide service | `indi_goto_guide_service` process, SkySafari GoTo routing, PiFinder manual approach + correction GoTo, tracking guide target management | Include in the INDI PR or split as a GoTo/Guide service PR |
| LiveCam RAW preview and live stack | `raw_live_stack`/`livecam_config`, RAW preview + rolling stack, web render endpoint, stack/sizing/zoom/reset controls, web camera exposure/gain controls | New Draft PR needed |
| Korean localization | Korean locale and CJK language handling | Keep #500 separate because the locale file is large |

Documentation should travel with the feature PR that needs it. For example, INDI
docs belong with the INDI PR, and Time Sync docs belong with the Time Sync PR.

## Final Source Change List

Changed or added PiFinder files:

```text
python/PiFinder/boot_config.py
python/PiFinder/board_config.py
python/PiFinder/api_extensions.py
python/PiFinder/camera_interface.py
python/PiFinder/main.py
python/PiFinder/gps_gpsd.py
python/PiFinder/gps_ubx.py
python/PiFinder/gps_ubx_parser.py
python/PiFinder/gps_time_sync.py
python/PiFinder/gps_time_sync_helper.py
python/PiFinder/indi_multipoint_align.py
python/PiFinder/pointing_coordinate_service.py
python/PiFinder/mountcontrol_indi.py
python/PiFinder/server.py
python/PiFinder/sys_utils.py
python/PiFinder/switch_camera.py
python/PiFinder/keyboard_interface.py
python/PiFinder/keyboard_pi.py
python/PiFinder/ui/base.py
python/PiFinder/ui/callbacks.py
python/PiFinder/ui/fonts.py
python/PiFinder/ui/bluetooth_keyboard.py
python/PiFinder/ui/menu_manager.py
python/PiFinder/ui/menu_structure.py
python/PiFinder/ui/gps_time_sync_status.py
python/PiFinder/ui/object_details.py
python/PiFinder/ui/textentry.py
python/PiFinder/displays.py
python/PiFinder/ui/preview.py
python/locale/ko/LC_MESSAGES/messages.po
python/locale/ko/LC_MESSAGES/messages.mo
python/views/base.html
python/views/css/style.css
python/views/js/init.js
python/views/manifest.webmanifest
python/views/service-worker.js
python/views/images/pwa-icon-192.png
python/views/images/pwa-icon-512.png
python/tests/test_web_theme_static.py
python/tests/test_wifi_apsta_static.py
python/tests/test_sys_utils.py
python/tests/test_pointing_coordinate_service.py
python/views/network.html
python/views/tools.html
pi_config_files/pifinder.service
pi_config_files/pifinder_apsta_prepare.service
pi_config_files/pifinder_apsta_monitor.service
pi_config_files/pifinder_gps_time_sync.service
pi_config_files/pifinder_splash.service
pi_config_files/cedar_detect.service
pi_config_files/smb.conf
pifinder_paths.sh
pifinder_setup.sh
pifinder_update.sh
pifinder_post_update.sh
switch-ap.sh
switch-apsta.sh
switch-cli.sh
migration_source/v1.x.x.sh
migration_source/v2.1.0.sh
migration_source/v2.2.1.sh
migration_source/v2.2.2.sh
migration_source/v2.4.0.sh
migration_source/v2.6.0.sh
migration_source/mf_apsta_wifi.sh
migration_source/mf_wifi_settings.sh
migrate_db.sql
default_config.json
scripts/camera_lcd_preview.py
scripts/import_initial_wifi_networks.py
scripts/pifinder_apsta.sh
scripts/install_indi_mount.sh
scripts/install_indi_mount_OnstepX.sh
scripts/patches/indi-v2.2.3.1-onstepx.patch
scripts/install_chrony_time_sync.sh
scripts/install_gps_time_sync_helper.sh
docs/mf_dev/mf_bookworm_install_ko.md
docs/mf_dev/mf_bookworm_install_en.md
docs/mf_dev/mf_change_history_ko.md
docs/mf_dev/mf_change_history_en.md
docs/mf_dev/mf_indi_mount_install_ko.md
docs/mf_dev/mf_indi_mount_install_en.md
docs/mf_dev/mf_multipoint_align_flow_ko.md
docs/mf_dev/mf_multipoint_align_flow_en.md
docs/mf_dev/mf_wifi_apsta_ko.md
docs/mf_dev/mf_wifi_apsta_en.md
docs/mf_dev/mf_keyboard_mapping_ko.md
docs/mf_dev/mf_keyboard_mapping_en.md
docs/mf_dev/mf_pifinder_new_device_tasks_ko.md
docs/mf_dev/mf_pifinder_new_device_tasks_en.md
docs/mf_dev/mf_pifinder_rpi4_pi5_compatibility_ko.md
docs/mf_dev/mf_pifinder_rpi4_pi5_compatibility_en.md
docs/mf_dev/mf_time_sync_ko.md
docs/mf_dev/mf_time_sync_en.md
docs/mf_dev/mf_mount_mode_compatibility_ko.md
docs/mf_dev/mf_mount_mode_compatibility_en.md
docs/mf_dev/mf_coordinate_helper_plan_ko.md
docs/mf_dev/mf_coordinate_helper_plan_en.md
```

Comparison against the original source:

```text
Baseline: currently checked-out PiFinder Git HEAD

Tracked source diff:
default_config.json              modified
migrate_db.sql                   modified
pi_config_files/*.service        modified
pi_config_files/smb.conf         modified
pifinder_setup.sh                modified
pifinder_update.sh               modified
pifinder_post_update.sh          modified
switch-ap.sh                     modified
switch-cli.sh                    modified
migration_source/*.sh            modified
python/PiFinder/api_extensions.py modified
python/PiFinder/camera_interface.py  modified
python/PiFinder/displays.py       modified
python/PiFinder/main.py           modified
python/PiFinder/switch_camera.py  modified
python/PiFinder/keyboard_interface.py modified
python/PiFinder/keyboard_pi.py    modified
python/PiFinder/sys_utils.py      modified
python/PiFinder/ui/base.py        modified
python/PiFinder/ui/callbacks.py   modified
python/PiFinder/ui/fonts.py       modified
python/PiFinder/ui/menu_manager.py modified
python/PiFinder/ui/menu_structure.py modified
python/PiFinder/ui/textentry.py   modified
python/PiFinder/ui/preview.py     modified
python/views/tools.html           modified

New PiFinder files:
python/PiFinder/boot_config.py
python/PiFinder/ui/bluetooth_keyboard.py
python/locale/ko/LC_MESSAGES/messages.po
python/locale/ko/LC_MESSAGES/messages.mo
pifinder_paths.sh
scripts/camera_lcd_preview.py
docs/mf_dev/mf_bookworm_install_ko.md
docs/mf_dev/mf_bookworm_install_en.md
docs/mf_dev/mf_change_history_ko.md
docs/mf_dev/mf_change_history_en.md
```

No PiFinder source changes outside this list were found during the recheck. The
file-by-file notes below are based on the actual diff between the current working
tree and the original source.

Important final values:

```text
SSD1351 SPI speed: 32000000 Hz
Focus bright-background threshold: 220.0
Pi camera startup gain: camera profile analog_gain
camera_exp config value in use: auto
Default gps_port: auto
Resolved gps_port: CM5/Pi5 -> /dev/ttyAMA2, Pi4 -> /dev/ttyAMA3, fallback -> /dev/ttyAMA1
Keyboard HID input: GPIO keypad + USB/Bluetooth libinput
Menu languages: en, de, fr, es, ko, zh
Install user/path model: current OS user, not hard-coded pifinder
```

## `python/PiFinder/boot_config.py`

This is a new file.

### Added API

```python
def get_boot_config_path() -> Path:
    firmware_config = Path("/boot/firmware/config.txt")
    if firmware_config.exists():
        return firmware_config
    return Path("/boot/config.txt")
```

### Purpose

Some PiFinder code previously hard-coded `/boot/config.txt`. Raspberry Pi OS
Bookworm uses `/boot/firmware/config.txt` as the active boot config path, so
camera switching and camera type display could read or write the wrong file on
CM5 Bookworm.

### Behavior Change

- Prefer `/boot/firmware/config.txt` when it exists.
- Fall back to `/boot/config.txt` for legacy Raspberry Pi OS compatibility.
- Keep the OS path difference in one helper instead of duplicating it in UI and
  camera switching code.

## `python/PiFinder/switch_camera.py`

The camera overlay switching code was updated for the Bookworm boot config path
and the IMX462 overlay.

### Before

- Read and wrote `/boot/config.txt` directly.
- Internally rewrote `imx462` requests to `imx290`.
- Added `clock-frequency=74250000` only for `imx290`.

### After

- Uses `get_boot_config_path()` to find the active boot config file.
- Does not force `imx462` to `imx290`.
- Adds `clock-frequency=74250000` for both `imx290` and `imx462` when needed.
- Keeps the existing flow of commenting out old `dtoverlay=imx...` entries and
  enabling the selected overlay.
- Updates the `switch_boot()` docstring to describe the active boot config path
  and root requirement more accurately.

### Code-Level Change

```python
from PiFinder.boot_config import get_boot_config_path

boot_config_path = get_boot_config_path()
```

Before:

```python
with open("/boot/config.txt", "r") as boot_in:
```

After:

```python
with open(boot_config_path, "r") as boot_in:
```

Before:

```python
if cam_type == "imx462":
    cam_type = "imx290"
```

After:

```python
# imx462 is no longer forced to imx290
```

### Expected Effect

- On CM5 Bookworm, camera switching edits the actual `/boot/firmware/config.txt`.
- The dedicated `imx462.dtbo` overlay available in Bookworm firmware can be used
  directly.
- Older imx290-compatible setups and direct imx462 overlay setups are both
  supported.

## `python/PiFinder/ui/callbacks.py`

The camera type display callback was updated to read the Bookworm boot config
path, and callbacks were added for the runtime camera gain and GPS port menus.

### Before

- `get_camera_type()` opened `/boot/config.txt` directly.
- On CM5 Bookworm, UI camera information could disagree with the active boot
  config.

### After

- Uses `get_boot_config_path()`.
- Keeps the UI mapping from `imx290` to `imx462` for older installs that used
  the imx290-compatible overlay for IMX462 hardware.
- Reads the current runtime gain from `shared_state.last_image_metadata()` for
  the gain menu selection marker.
- Reads the active camera type's `CameraProfile.analog_gain` for the `Profile`
  gain label.
- Sends gain selections to the camera queue as `set_gain:<value>`.
- Sends `set_gain:profile` when the `Profile` item is selected.
- `update_gpsd_baud_rate()` now reads both `gps_baud_rate` and `gps_port`.
- GPS baud and port menu changes call
  `sys_utils.check_and_sync_gpsd_config(baud_rate, gps_port)`.
- `switch_language()` treats `ko` and `zh` as CJK languages and restarts
  PiFinder after the language change.

### Code-Level Change

```python
from PiFinder.boot_config import get_boot_config_path
```

```python
with open(get_boot_config_path(), "r") as boot_in:
    boot_lines = list(boot_in)
```

### Expected Effect

- Camera settings/status UI reads the active Bookworm boot config.
- Existing users of the imx290-compatible IMX462 setup still see the expected
  camera type.
- The gain menu checkmark reflects the runtime gain instead of the saved
  `camera_gain` config value.
- GPS port and baud changes update gpsd through the same callback.
- Selecting Korean restarts PiFinder with the CJK font needed for Hangul glyphs
  on the OLED.

## `python/PiFinder/sys_utils.py`

GPSD synchronization now checks both serial device and baud rate.

### Before

- `check_and_sync_gpsd_config(baud_rate)` only compared `/etc/default/gpsd`
  `GPSD_OPTIONS`.
- `update_gpsd_config(baud_rate)` only rewrote `GPSD_OPTIONS`.
- Port settings such as `DEVICES="/dev/ttyAMA1"` could not be changed from the UI.

### After

- Added a `DEFAULT_GPSD_DEVICE` fallback.
- Extended the API to `check_and_sync_gpsd_config(baud_rate, device=DEFAULT_GPSD_DEVICE)`.
- Compares both `DEVICES` and `GPSD_OPTIONS` in `/etc/default/gpsd`.
- Calls `update_gpsd_config(baud_rate, device)` when either value differs.
- `update_gpsd_config()` rewrites both `DEVICES=...` and `GPSD_OPTIONS=...`.
- Missing lines are added if needed.
- gpsd is restarted after writing the updated config, matching the previous flow.

### Expected Effect

- CM5 boards where the GPS UART appears as `/dev/ttyAMA2` can keep that setting
  through the UI.
- On PiFinder restart, `/etc/default/gpsd` is automatically synchronized to the
  selected port and baud rate.
- After the Pi4/Pi5 compatibility cleanup, the default config uses
  `gps_port: auto`; the `board_config` profile selects the board-specific
  default port.

### Added Bluetooth Keyboard Helpers

Added `bluetoothctl` wrappers and device parsing helpers for the Bluetooth
keyboard UI.

Main added functions:

```python
def list_bluetooth_devices() -> list[dict[str, Any]]
def scan_bluetooth_devices(scan_seconds: int = 12) -> list[dict[str, Any]]
def connect_bluetooth_device(address: str) -> str
def disconnect_bluetooth_device(address: str) -> str
def remove_bluetooth_device(address: str) -> str
def reconnect_bluetooth_keyboards() -> int
def auto_reconnect_bluetooth_keyboards(...) -> int
```

Implementation details:

- Runs `bluetoothctl` through `subprocess`.
- Cleans ANSI escapes and prompt text before parsing `Device <MAC> <name>` lines.
- Keeps scan stdout and also parses scan-response/name-change events such as
  `[CHG] Device <MAC> Name: ...` and `[CHG] Device <MAC> Alias: ...`.
- If the initial advertising entry only exposes a MAC-like name, the later scan
  response name is preferred when available.
- Calls `info <MAC>` for each device to read `paired`, `trusted`, `connected`,
  `blocked`, and `icon` state.
- During scan, runs `agent KeyboardDisplay`, `default-agent`, `pairable on`, and
  `scan on`.
- `reconnect_bluetooth_keyboards()` prefers paired devices that look like
  keyboards, and falls back to all paired devices when no keyboard-like device
  is identifiable.
- `auto_reconnect_bluetooth_keyboards()` retries several times after PiFinder
  startup so late Bluetooth controller/HID readiness is handled.
- Auto reconnect skips devices that are already connected and only attempts
  disconnected paired/trusted devices.

Expected effect:

- PiFinder UI can scan, connect, reconnect, disconnect, and remove Bluetooth
  keyboards.
- USB keyboards work without pairing through the same libinput input path, while
  Bluetooth keyboards use that path after pairing.
- After PiFinder service restart or OS reboot, paired/trusted Bluetooth keyboards
  are automatically reconnected when possible.

### Removed Hard-Coded Username/Home Paths

Some install/runtime paths assumed that the OS user was always `pifinder` and
that user data lived under `/home/pifinder/PiFinder_data`. To allow several
PiFinders on the same network, the code now supports device-specific OS
usernames and hostnames.

Changes:

- `BACKUP_PATH` is now based on `utils.data_dir / "PiFinder_backup.zip"`.
- WiFi mode switching calls `utils.pifinder_dir / "switch-*.sh"` instead of
  `/home/pifinder/PiFinder/switch-*.sh`.
- Backup source files are calculated from `utils.data_dir`.
- The software update script path is calculated from
  `utils.pifinder_dir / "pifinder_update.sh"`.
- The NixOS migration script path is also based on `utils.pifinder_dir`.

Expected effect:

- If the OS user is `scope-a` or `scope-b`, backup, restore, update, and WiFi
  mode switching follow that user's PiFinder installation.
- The hostname chosen during Raspberry Pi OS setup is preserved and can still be
  changed from the web Network page.

## `pifinder_paths.sh`, Install/Update/Migration Scripts

Added a shared helper, `pifinder_paths.sh`, and removed `/home/pifinder`
assumptions from the install/update shell scripts.

### Added Helper

```bash
PIFINDER_USER
PIFINDER_HOME
PIFINDER_REPO_DIR
PIFINDER_DATA_DIR
pifinder_render_config <template> <target>
pifinder_boot_config_path
```

### Changed Files

```text
pifinder_setup.sh
pifinder_update.sh
pifinder_post_update.sh
switch-ap.sh
switch-cli.sh
migration_source/v1.x.x.sh
migration_source/v2.1.0.sh
migration_source/v2.2.1.sh
migration_source/v2.2.2.sh
migration_source/v2.4.0.sh
migration_source/v2.6.0.sh
migrate_db.sql
```

Implementation details:

- `pifinder_setup.sh` refuses to run directly as root, so the repository is not
  accidentally cloned as root into the user's home directory.
- System-level actions still use `sudo` inside the script.
- The default repo path is `$HOME/PiFinder`; the default data path is
  `$HOME/PiFinder_data`.
- `pifinder_update.sh` and `pifinder_post_update.sh` derive the repo path from
  the script location.
- Migration scripts use `PIFINDER_REPO_DIR`, `PIFINDER_DATA_DIR`, and
  `PIFINDER_USER`.
- `switch-ap.sh` and `switch-cli.sh` update `wifi_status.txt` relative to their
  own script directory.
- Shared boot config detection prefers `/boot/firmware/config.txt` on Bookworm
  and falls back to `/boot/config.txt` on legacy systems.

Expected effect:

- A Raspberry Pi Imager install can use any desired OS username and hostname,
  instead of requiring `pifinder`.
- Multiple devices can use names such as `scope-a.local` and `scope-b.local` to
  reduce mDNS collisions.
- Update and migration scripts are no longer tied to `/home/pifinder`.

### mDNS reliability (2026-08-06)

`pifinder_setup.sh` now applies two settings that fix `<hostname>.local`
resolving intermittently from Android phones.

- Disable WiFi power save: brcmfmac drops multicast frames (mDNS queries)
  while the radio dozes. PCs mask this with caching and retries, but Android's
  `.local` resolver times out fast, so the name flaps. The script writes
  `wifi.powersave = 2` (off) to
  `/etc/NetworkManager/conf.d/wifi-powersave.conf`.
- Stop avahi from advertising IPv6: wlan0 only carries a link-local (`fe80::`)
  address, and advertising it as an AAAA record makes IPv6-preferring Android
  clients try an unconnectable zone-less `fe80::` address. The script sets
  `use-ipv6=no` and `publish-aaaa-on-ipv4=no` in `avahi-daemon.conf`.

## `pi_config_files/*.service`, `pi_config_files/smb.conf`

Systemd and Samba files now act as install-time templates.

### Before

```text
User=pifinder
WorkingDirectory=/home/pifinder/PiFinder/python
guest account = pifinder
path=/home/pifinder/PiFinder_data
```

### After

```text
User=__PIFINDER_USER__
WorkingDirectory=__PIFINDER_REPO_DIR__/python
guest account = __PIFINDER_USER__
path=__PIFINDER_DATA_DIR__
```

`pifinder_render_config()` replaces placeholders with concrete values during
installation.

### Expected Effect

- systemd services run as the custom OS user.
- Samba shares the custom user's `PiFinder_data` directory.

## `python/PiFinder/api_extensions.py`, `python/views/tools.html`

Web/API behavior was adjusted so custom-user installs do not show or use stale
`pifinder` paths.

Changes:

- `/api/camera/debug` now uses `utils.debug_dump_dir` instead of a hard-coded
  `/home/pifinder/...` path.
- The Tools password-change text no longer names the fixed `pifinder` account;
  it refers to the current system user account.
- The Korean locale entry for that text was updated.

Expected effect:

- Debug frame API and password-change wording match the actual installed OS
  user.

## `python/PiFinder/main.py`

Startup gpsd synchronization now includes the GPS port.

### After

- Reads `gps_port` along with `gps_baud_rate`.
- Falls back to `sys_utils.DEFAULT_GPSD_DEVICE` when `gps_port` is missing.
- Calls `sys_utils.check_and_sync_gpsd_config(baud_rate, gps_port)`.
- Adds `ko` and `zh` to the allowed development/test `--lang` command-line
  values.

### Expected Effect

- The GPS port selected from the menu survives service restarts because gpsd is
  synchronized on startup.
- Korean UI can be started directly with a command such as
  `python -m PiFinder.main --lang ko`.

## `python/PiFinder/camera_interface.py`

The existing runtime `set_gain` command was extended.

### Before

- Only `set_gain:<integer>` was supported.
- Gain values were converted with `int()`.
- There was no command for returning to the camera profile's default gain.

### After

- Added `get_default_gain()`.
- Backends with `self.profile.analog_gain`, such as the Pi camera backend, return
  that value as the default gain.
- Backends without a profile use the current `self.gain` when available, or
  `1.0` as a fallback.
- Added support for `set_gain:profile`.
- Numeric gains are parsed with `float()` so non-integer values are possible.
- Console/log output uses `g` formatting so `30.0` is shown as `30`.

### Code-Level Change

```python
def get_default_gain(self) -> float:
    profile = getattr(self, "profile", None)
    if profile is not None and hasattr(profile, "analog_gain"):
        return float(profile.analog_gain)
    return float(getattr(self, "gain", 1.0))
```

```python
if gain_value == "profile":
    self.gain = self.get_default_gain()
else:
    self.gain = float(gain_value)
```

### Expected Effect

- PiFinder still starts with the original profile default gain.
- The current running camera gain changes only when the user selects a gain from
  the menu.
- Selecting `Profile` returns to the camera profile default, regardless of the
  saved `camera_gain` config value.

## `python/PiFinder/keyboard_interface.py`

Added a text keycode range so physical keyboard character input can travel
through the existing PiFinder keyboard queue.

Added API:

```python
TEXT_BASE = 1000

def text_key(char: str) -> int
def is_text_key(keycode: int) -> bool
def text_from_keycode(keycode: int) -> str
```

Expected effect:

- Alphabetic keyboard input can be represented without colliding with the
  existing number, `ALT_*`, or `LNG_*` keycodes.

## `python/PiFinder/keyboard_pi.py`

The libinput physical-keyboard mapping was expanded so USB and Bluetooth HID
keyboards can be used alongside the GPIO keypad.

### Before

- The libinput physical keyboard path handled only arrow keys, Enter, and a few
  keypad `+/-` keys.
- Number keys, keypad numbers, Space, Esc, Backspace, and long/alt shortcuts were
  not mapped.
- The old keypad `+/-` event-code mapping could be reversed relative to Linux
  input event codes.

### After

- Linux input key code constants were added at the top of the file.
- Added `self.physical_pressed` to track Alt/Ctrl/Shift modifier state.
- Added `self.physical_press_times`, `self.physical_last_repeat_times`,
  `self.physical_hold_sent`, and `self.physical_press_modifiers` to track real
  hold timing for USB/Bluetooth keyboards.
- Added `self.text_physical_key_mapping` so alphabet keys produce real text
  input.
- Added `self.physical_key_mapping` for normal USB/Bluetooth keyboard keys.
- Added `self.alt_physical_key_mapping` to map `Alt+key` combinations to
  PiFinder `ALT_*` input events.
- `self.long_physical_key_mapping` is now used by both real long press handling
  and compatibility `Shift/Ctrl+key` shortcuts.
- Holding `Left`, `Right`, or `Enter/KP Enter` for one second sends the matching
  long-key event and suppresses the normal release event.
- Holding `Up` or `Down` for one second repeats normal `UP/DOWN` events, matching
  the GPIO keypad behavior.
- `Alt+key` combinations take priority over long press handling. The modifier
  state from the initial press is preserved, so releasing `Alt` first still
  produces the intended `ALT_*` event.

Important mappings:

```text
Arrow keys          -> LEFT/UP/DOWN/RIGHT
Enter/KP Enter      -> SQUARE
Space               -> actual space text input
Esc                 -> LEFT
Backspace           -> MINUS/Delete
0-9 top row         -> number input
0-9 keypad          -> number input
= or KP+            -> PLUS
- or KP-            -> MINUS
a-z                 -> actual text input
Shift+a-z           -> uppercase text input
Alt+Arrow           -> ALT_LEFT/ALT_UP/ALT_DOWN/ALT_RIGHT
Alt+= or Alt+KP+    -> ALT_PLUS
Alt+- or Alt+KP-    -> ALT_MINUS
Alt+0               -> ALT_0
Alt+Enter           -> ALT_SQUARE
Hold Left/Right 1s  -> LNG_LEFT/LNG_RIGHT
Hold Enter 1s       -> LNG_SQUARE
Hold Up/Down 1s     -> repeated UP/DOWN
Shift/Ctrl+Arrow    -> LNG_* compatibility shortcut
Shift/Ctrl+Enter    -> LNG_SQUARE compatibility shortcut
```

The previous compact single-key shortcuts (`q/a/z`, `w/s/e/d/r/f/g`,
`i/j/k/l/m`) are not used in the USB/Bluetooth libinput path because they
conflict with real alphabetic input.

Expected effect:

- The default `keyboard_pi` backend can accept USB and Bluetooth keyboards
  without switching to the DISPLAY-dependent `keyboard_local.py` backend.
- GPIO keypad matrix scanning remains unchanged.
- Object search and name-entry screens can receive real alphabetic input
  without multi-tap conversion.
- USB/Bluetooth keyboards can trigger the marking menu, return-to-top, and
  recent-object actions by physically holding the key.

## `python/PiFinder/main.py`, `python/PiFinder/ui/base.py`, `python/PiFinder/ui/menu_manager.py`, `python/PiFinder/ui/textentry.py`

Added Bluetooth keyboard auto-reconnect and the path that carries alphabetic
keycodes into UI text entry.

Changes:

- Imports `threading`.
- Adds `start_bluetooth_keyboard_autoreconnect()`.
- In real Pi hardware mode, starts `sys_utils.auto_reconnect_bluetooth_keyboards()`
  in a daemon thread.
- The thread starts after PiFinder subprocesses are launched so it does not block
  startup UI display.
- The main loop checks `KeyboardInterface.is_text_key(keycode)` before the
  special-key branch.
- Text keycodes are converted back to characters with
  `KeyboardInterface.text_from_keycode(keycode)`.
- Added `MenuManager.key_text(char)` to forward text to the active UI module.
- Added the default `UIModule.key_text(char)` hook.
- `UITextEntry.key_text(char)` appends the received character directly to
  `current_text` and refreshes search results.

Expected effect:

- Alphabet keys from Bluetooth/USB keyboards enter actual text in PiFinder search
  and text-entry screens.
- The existing numeric keypad multi-tap input remains available.
- Paired/trusted Bluetooth keyboards are automatically reconnected after PiFinder
  starts.

## `python/PiFinder/displays.py`

The display layer was updated for CM5/Pi 5 SPI device numbering and SSD1351 OLED
stability.

### Before

- Display classes called `spi(device=0, port=0, bus_speed_hz=...)` directly.
- Display initialization could fail when `/dev/spidev0.0` did not exist.
- The SSD1351 default SPI speed was `40000000` Hz.

### After

- Added the `display_spi(bus_speed_hz)` helper.
- Checks `/dev/spidev0.0` and `/dev/spidev10.0`, using whichever exists first.
- Falls back to `port=0`, `device=0` when neither path is visible.
- Sets the SSD1351 default SPI speed to `32000000` Hz.
- Allows `DisplaySSD1351(bus_speed_hz=...)` so test tools can override speed.
- Routes `DisplaySSD1333`, `DisplayST7789_128`, and `DisplayST7789` through the
  same SPI helper.
- Adds `pathlib.Path` so the helper can test SPI device-node existence.

### Added Helper

```python
def display_spi(bus_speed_hz: int):
    for port, device in ((0, 0), (10, 0)):
        if Path(f"/dev/spidev{port}.{device}").exists():
            return spi(device=device, port=port, bus_speed_hz=bus_speed_hz)
    return spi(device=0, port=0, bus_speed_hz=bus_speed_hz)
```

### SSD1351 Change

Before:

```python
serial = spi(device=0, port=0, bus_speed_hz=40000000)
```

After:

```python
def __init__(self, bus_speed_hz=32000000):
    serial = display_spi(bus_speed_hz=bus_speed_hz)
```

### Expected Effect

- Displays initialize when CM5 exposes SPI as `/dev/spidev10.0`.
- The SSD1351 OLED uses 32MHz as the stable default instead of the problematic
  40MHz setting.
- Test scripts can compare SPI speeds by passing `bus_speed_hz`.

## Camera Gain Initialization

This item is not a final source change. It records a reviewed change that was
reverted so the current behavior matches the original source. In the final
working tree, `python/PiFinder/camera_pi.py` has no Git diff.

Final retained behavior:

- `CameraPI.__init__()` accepts only `exposure_time`, matching the original code.
- Initial gain comes from the camera profile's `analog_gain`, not from the
  `camera_gain` config value.
- For the IMX462 profile, the startup gain is `30.0`.
- Even if `/home/pifinder/PiFinder_data/config.json` and `default_config.json`
  contain `camera_gain: 20`, that value is not applied during initial Pi camera
  startup.
- Runtime commands such as `set_gain` remain available, but they do not change
  the startup gain.
- The existing `exp_save` flow that saves `camera_gain` is left unchanged.

Code shape kept identical to the original:

```python
def __init__(self, exposure_time) -> None:
```

```python
self.gain = self.profile.analog_gain
```

```python
camera_hardware = CameraPI(exposure_time)
```

With this decision, PiFinder's observation auto-exposure starts from the profile
gain, as in the original source.

## `python/PiFinder/ui/fonts.py`

Font selection was updated so Korean also uses a font with CJK glyph coverage.

### Before

- `sarasa-mono-sc-light-nerd-font+patched.ttf` was used only when
  `language == "zh"`.
- Adding a Korean locale alone could still leave Hangul glyphs unavailable in
  the default Roboto Mono font family.

### After

- Uses the Sarasa CJK font when `lang in ["ko", "zh"]`.
- Keeps the existing behavior of disabling the Pillow layout engine for CJK
  font rendering.
- The related source comments are in English.

### Expected Effect

- Korean menu text can render on the OLED when `ko` is selected.
- Existing Chinese font behavior is unchanged.

## `python/PiFinder/ui/menu_structure.py`

Added a camera gain menu immediately after the exposure menu, added a GPS port
menu under GPS settings, added a keyboard settings menu under Advanced, and
added Korean to the language menu.

### Added Language Menu Entry

```text
Settings > User Pref... > Language > Korean
```

Implementation:

- Added `Language: ko` to the gettext extraction markers.
- Added a Language menu item with `name: _("Korean")` and `value: "ko"`.
- Keyboard input behavior was not changed; USB/Bluetooth alphabet keys continue
  to enter Latin characters only.

Expected effect:

- Korean UI can be selected from the PiFinder device menu.
- After language selection, the callback restarts PiFinder so the Korean CJK
  font is loaded.

### Added Menu

```text
Camera Gain
```

Location:

- Immediately after `Camera Exp`
- Immediately before `WiFi Mode`

### Menu Behavior

- Uses the same `UITextMenu` single-select pattern as `Camera Exp`.
- Uses `label: camera_gain` so the Focus screen marking menu can jump directly
  to it.
- Does not use `config_option`.
- Displays selection state from `callbacks.get_camera_gain_selection`, based on
  the runtime camera gain rather than saved config.
- Sends the selected value to the camera process through `callbacks.set_gain`.

### Choices

```text
Profile
1x
2x
4x
8x
12x
15x
16x
20x
22x
24x
30x
```

The `Profile` item returns to the current camera profile's default gain. For
IMX462 this is displayed as `30x`.

### Added GPS Menu

```text
GPS Settings > GPS Port
```

Choices:

```text
ttyAMA1  -> /dev/ttyAMA1
ttyAMA2  -> /dev/ttyAMA2
serial0  -> /dev/serial0
ttyAMA0  -> /dev/ttyAMA0
ttyAMA10 -> /dev/ttyAMA10
ttyS0    -> /dev/ttyS0
ttyACM0  -> /dev/ttyACM0
ttyUSB0  -> /dev/ttyUSB0
```

`GPS Port` and `GPS Baud Rate` use the same post callback to update
`/etc/default/gpsd`.

### Added Keyboard Menu

```text
Settings > Advanced > Keyboard
```

Implementation:

- Imports `UIBluetoothKeyboard`.
- Uses `label: keyboard_settings`.
- On entry, reads the Bluetooth device list and exposes per-device actions.

Expected effect:

- Bluetooth keyboard connection is available from the Advanced settings menu.
- USB keyboards work through the same `keyboard_pi` input backend as soon as
  they are attached.

## `python/locale/ko/LC_MESSAGES/messages.po`, `messages.mo`

Added a new gettext catalog for the Korean UI.

### Generation

- Extracted messages from the current Python source with Babel.
- Added Korean translations in `messages.po`.
- Compiled `messages.mo` with `pybabel compile -d python/locale -l ko`.

### Translation Policy

- Common astronomy terms were translated into Korean where natural, including
  galaxy, open cluster, globular cluster, nebula, dark nebula, planetary nebula,
  double star, seeing, transparency, polar alignment, and chart.
- Terms that are clearer as-is, such as `RA/DEC`, `DSO`, `SQM`, `Gain`,
  `Profile`, `T9`, `Multi-Tap`, catalog names, device names, and port names,
  remain in English.
- 380 of 712 extracted strings were translated, focused on the core PiFinder UI
  and menus. The remaining strings intentionally fall back to the English
  source text instead of rendering blank text.

### Expected Effect

- Selecting Korean displays the main PiFinder device UI in Korean.
- Untranslated strings remain readable in English.

## `python/PiFinder/ui/bluetooth_keyboard.py`

New UI module for Bluetooth keyboard pairing and connection.

### UI harness sys_utils mock gap fix (2026-08-07)

Running `nox -s ui_tests` (the all-screen key sweep) on the device made the
Bluetooth screen's Reconnect execute the **real** `pause_wifi_for_bt_pairing()`,
cutting WiFi for 60-90 s (observed 2026-08-07 08:15; dropped the SSH session).
Cause: `test_ui_modules.py`'s `_inert_sys_utils` fixture patched a hardcoded
list of three modules, but screens bind the real module at import time
(`sys_utils = utils.get_sys_utils()`), so screens added later
(bluetooth_keyboard, sqm, equipment) sat outside the mock. Fix: dynamically
patch the `sys_utils` attribute of every loaded PiFinder module, so future
screens are covered automatically. Verified on-device: full sweep 277 passed
with zero nmcli/WiFi activity in the journal. When running the suite on a
device, also consider the `PIFINDER_USE_FAKE_SYS_UTILS=1` safety net.

### Menu Items

```text
Scan / Pair
Reconnect
Refresh
<cached or scanned Bluetooth devices>
```

Device prefixes:

```text
* connected device
+ paired device
- discovered/unpaired device
```

The list prioritizes the device name on the small OLED and does not append the
MAC address suffix. If no usable name is available, or the name itself is only a
MAC address, it falls back to a short label such as `Unknown 12:34`. The MAC
address is shown after selecting a device, on the action menu detail line as
`MAC ...12:34:56`.

### Device Action Menu

For a selected device, the UI offers:

```text
Pair+Connect or Pair Again
Connect
Disconnect
Remove
Cancel
```

### Pairing Behavior

- Starts `bluetoothctl` as a separate process.
- Sets `agent KeyboardDisplay`, `default-agent`, and `pairable on`, then runs
  `pair <MAC>`.
- Reads output in non-blocking mode and displays progress on the OLED.
- When output contains a passkey such as `Passkey: 123456`, the screen shows
  `Type 123456` so the user can enter it on the Bluetooth keyboard.
- Automatically sends `yes` for `Confirm passkey`, `Authorize service`, and
  `Accept pairing` prompts.
- After successful pairing, or when the device is already paired, sends
  `trust <MAC>` and `connect <MAC>`.
- Pressing Left cancels the pairing process and returns to the list.

Expected effect:

- Bluetooth keyboard pairing can be attempted from the PiFinder screen and
  keypad without an SSH terminal.
- After connection, the keyboard appears as `/dev/input/event*` and is handled
  through the `keyboard_pi.py` libinput mapping.

## `python/PiFinder/mountcontrol_indi.py`, INDI Mount Control

INDI mount control is optional. A basic PiFinder install continues to work
without it. The separate process starts only after the user installs the INDI
dependencies with `scripts/install_indi_mount.sh` and enables `mount_control`.

### Main Settings

```json
"mount_control": false,
"mount_control_indi_host": "localhost",
"mount_control_indi_port": 7624
```

### Behavior

- `main.py` checks `mount_control` and starts the `mountcontrol_indi.run()`
  process only when enabled.
- INDI server connection failures, missing PyIndi, and missing mount devices are
  recorded in the status file and console messages while the rest of PiFinder
  continues running.
- `mount_control_status.json` stores a compact status snapshot for logs, debug,
  and web inspection.
- The object details screen sends sync/goto/stop/manual-step commands through
  the mount queue.
- Shutdown sends a mount-control shutdown command first, then terminates the
  process if it does not exit.

### Web INDI Menu And LX200 OnStep Control

Added `python/views/indi_mount.html` and the `/indi` route in
`python/PiFinder/server.py`, making INDI a dedicated top-level web menu between
`Equipment` and `Tools`.

- The `INDI Web Manager` button opens port `8624` on the current PiFinder host.
- `Current INDI Driver State` reads LX200 OnStep connection mode,
  serial/network settings, location, UTC time, park state, and slew-rate state
  through `indi_getprop`.
- `LX200 OnStep Driver Connection` supports USB Serial and Network TCP. USB mode
  lists `/dev/serial/by-id`, `/dev/ttyUSB*`, and `/dev/ttyACM*` candidates;
  network mode can select an IP from the AP connected-device list or use manual
  host/IP entry.
- `Location and Time` uses the GPS/loaded location when PiFinder has a GPS lock,
  otherwise it uses the default PiFinder location. `Reload Current Values`
  refreshes both PiFinder and OnStep values, and the UTC field keeps ticking
  while the page is open.
- `Send Location and Time` does not trust a stale browser-supplied timestamp.
  The Flask route recalculates PiFinder system UTC when it receives the POST and
  sends that timestamp to OnStep.
- `Mount Control` displays Home state, Park state, and raw `:GU#` mount status
  separately so `At Home` is not confused with `Parked`; it also adds At Home,
  Return Home, Park, Unpark, Set-Park, native OnStep 0-9 Slew Rate selection,
  and press-and-hold direction movement.
- The OnStepX `Settings` area adds manual Backlash read/write controls using
  the driver's `Backlash.Backlash RA` and `Backlash.Backlash DEC` properties.
  Manual writes do not move the mount. The UI labels those same driver
  properties as `AZ`/`ALT` in Alt/Az mode and `RA`/`DEC` in EQ mode.
- Hardware testing showed that tracking can contaminate Backlash measurement.
  Automatic Backlash therefore disables tracking before measurement and restores
  the original tracking state only after successful completion.
- Auto Backlash uses the historical internal name `compass_goto_loop` for
  compatibility, but the current measurement movement is INDI GoTo again.
  PiFinder disables tracking before the test and again after every GoTo leg so
  OnStep's automatic post-GoTo tracking does not contaminate the measured
  coordinate delta.
  Alt/Az tests `AZ` and `ALT`; EQ tests `RA` and `DEC`, one active axis at a
  time.
- GoTo completion handling now waits for a stable idle window and, when
  OnStep status is available, the `:GU#` `N` (`No goto`) state before recording
  Backlash mount/solved samples. This avoids sampling during OnStepX's
  near-destination settle wait before the final fine approach.
- Auto Backlash no longer requires IMU Compass/NDOF mode or MAG calibration.
  It requires a fresh plate-solved `PointingCoordinateService.solved`
  coordinate, syncs the mount coordinates to solved RA/Dec before the GoTo
  loop, records each GoTo leg's mount start/end coordinates and PiFinder solved
  start/end coordinates, filters out legs where mount-vs-solved travel differs
  by at least 1 degree, trims the lowest/highest 30%, and reports the middle
  40% mean by movement direction.
- Auto Backlash no longer resets Backlash to zero, does not apply calculated
  values automatically, and does not change the input fields during periodic UI
  refresh. The user reviews the recommendation and writes values with
  `Save Backlash`.
- 2026-07-03 live RA/DE GoTo round-trip tests reached the `999 arc-sec` limit
  on both axes even at 20 degrees because that was the PiFinder/INDI write limit
  at the time. PiFinder, the web UI, and the OnStepX driver write path now use
  `3600 arc-sec`, matching the OnStep firmware and the INDI property metadata.
  When the limit is reached, the automatic value is shown as low-confidence and
  is not applied automatically.
- The OnStepX driver patch now makes `GUIDE_RATE` writable for the OnStepX
  device, maps the requested value to OnStep rate selectors, and reads the
  actual pulse-guide rate back from the device. Auto Backlash no longer depends
  on `GUIDE_RATE`, but the writable/readback behavior remains part of the
  OnStepX driver compatibility patch. The source installer applies this patch
  and the binary archive was rebuilt with the patched OnStepX driver.
- The INDI binary archive can now be stored as `.tar.gz.part-*` split files in
  git. The archive installer rebuilds the full archive from those parts and
  verifies the `.sha256` checksum; the packaging script creates split parts
  automatically for large archives.
- Direction movement is handled through AJAX: pressing a button sends the motion
  command, and pointer up/cancel/leave sends stop.
- Red Night theme CSS now covers selects/dropdowns/tables so INDI controls do
  not flash white, and the Materialize select sizing was adjusted to reduce
  clipped option text.

### Docs and Install Files

```text
docs/mf_dev/mf_indi_mount_install_ko.md
docs/mf_dev/mf_indi_mount_install_en.md
scripts/install_indi_mount.sh
docs/mf_dev/mf_keyboard_mapping_ko.md
docs/mf_dev/mf_keyboard_mapping_en.md
```

## `python/PiFinder/gps_time_sync.py`, Integrated Time Sync

GPS, Chrony, PiFinder SNTP, RTC, and software PPS are managed as one Time Sync
feature. The master switch is `Off` by default; when enabled, chronyd is the
default Linux system-clock manager.

### Main Settings

```json
"time_sync_enabled": false,
"time_sync_source_mode": "chrony",
"time_sync_clock_manager": "chrony",
"chrony_time_sync": true,
"gps_time_sync": true,
"ntp_time_sync": false,
"ntp_server": "pool.ntp.org",
"software_pps": false,
"rtc_sync": false
```

### Behavior

- In the default `chrony` mode, PiFinder reads `chronyc tracking` and chronyd
  owns the Linux system clock.
- In `best` mode, PiFinder compares Chrony, GPS, and PiFinder SNTP candidates.
- PiFinder's built-in SNTP check is `Off` by default to avoid duplicating
  chronyd; it can be enabled as a fallback/check source.
- The main PiFinder process keeps normal user permissions. RTC writes and the
  explicit `Clock Manager = PiFinder` fallback system-clock mode are handled by
  the root `gps_time_sync_helper.py` service.
- The helper has separate dry-run and real-write modes, and it does not write
  the system clock in the default chrony configuration.
- Status UI is available at `Tools > Place & Time > Time Sync`.
- Settings UI is available at `Settings > Advanced > Time Sync`.

### Docs and Install Files

```text
docs/mf_dev/mf_time_sync_ko.md
docs/mf_dev/mf_time_sync_en.md
pi_config_files/pifinder_gps_time_sync.service
scripts/install_chrony_time_sync.sh
scripts/install_gps_time_sync_helper.sh
```

## Wi-Fi AP+STA Simultaneous Mode

The previous `Client` or `AP` single-mode selection was extended with `AP+STA`.
This mode keeps `wlan0` as the STA for internet access and updates while the
virtual `uap0` AP interface serves the PiFinder AP for phone/tablet control.

### Behavior

- Added `AP+STA` to the web `Tools > Network` page and the device
  `Settings > WiFi Mode` menu.
- `switch-apsta.sh` applies `/etc/dhcpcd.conf.apsta` and enables
  `pifinder_apsta_prepare`, `pifinder_apsta_monitor`, `dnsmasq`, and `hostapd`.
- `scripts/pifinder_apsta.sh prepare` creates `uap0` and assigns
  `10.10.10.1/24`.
- `scripts/pifinder_apsta.sh monitor` watches the STA channel. When it changes,
  it updates `hostapd.conf` `channel`/`hw_mode` and restarts `hostapd`.
- `switch-ap.sh` and `switch-cli.sh` stop the AP+STA monitor and remove `uap0`.
- Pi 4 and Pi 5 use the same layout: `uap0` is added on top of the default
  `wlan0` interface.

### Docs and Install Files

```text
docs/mf_dev/mf_wifi_apsta_ko.md
docs/mf_dev/mf_wifi_apsta_en.md
pi_config_files/dhcpcd.conf.apsta
pi_config_files/pifinder_apsta_prepare.service
pi_config_files/pifinder_apsta_monitor.service
scripts/pifinder_apsta.sh
switch-apsta.sh
```

## Locations Catalog

Added a country/region/county-or-district/city selector to the web
`Locations > Add New Location` form so known coordinates can be used as defaults.

### Main Files

```text
python/PiFinder/data/location_catalog.json
python/PiFinder/location_catalog.py
python/views/location_form.html
python/views/locations.html
scripts/build_location_catalog.py
docs/mf_dev/mf_location_catalog_ko.md
docs/mf_dev/mf_location_catalog_en.md
python/tests/test_location_catalog.py
```

### Behavior

- Generated an offline JSON catalog from GeoNames `cities5000`, `countryInfo`,
  `admin1CodesASCII`, and `admin2Codes`.
- Korea is augmented with the country-specific GeoNames `KR.zip` extract, so
  Seoul district/neighborhood selection is much more detailed.
- North Korea is excluded at build time by country code `KP`.
- The server exposes step-by-step APIs for countries, regions, districts, and
  places instead of sending the whole JSON catalog to the browser.
- Selecting a place fills the existing add-location form with name, latitude,
  longitude, altitude, error, and source defaults.
- Manual coordinate entry and DMS entry continue to work.
- `scripts/build_location_catalog.py` can regenerate the catalog from fresh
  GeoNames source files.

## Web UI Red Night Theme and PWA App Mode

A red night theme was added so the web UI is less disruptive during observing,
and PWA metadata was added so phones and tablets can launch the web UI from the
home screen like an app.

### Main Files

```text
python/PiFinder/server.py
python/views/base.html
python/views/css/style.css
python/views/js/init.js
python/views/manifest.webmanifest
python/views/service-worker.js
python/views/images/pwa-icon-192.png
python/views/images/pwa-icon-512.png
python/tests/test_web_theme_static.py
```

### Behavior

- The web UI offers `Gray` and `Red Night` themes.
- Theme choice is stored in browser `localStorage`, so each client device keeps
  its own preference.
- A `Fullscreen` button was added to the desktop and mobile menus so users can
  explicitly enter fullscreen mode.
- Because the Fullscreen API can exit fullscreen on navigation, internal menu
  navigation from fullscreen mode shows a `Resume Fullscreen` recovery button on
  the next page.
- Log page log-line colors remain the original level colors.
- The manifest uses `display: fullscreen`, while PiFinder's internal nav/footer
  stay visible.
- The service worker is a minimal pass-through worker with no caching, so live
  UI behavior is not changed.
- (2026-07-25) Materialize's light widget defaults are now themed for **both**
  themes. They had only been overridden under `html[data-theme="red"]`, so on the
  Gray theme a `select.browser-default` rendered as a white box
  (`rgba(255,255,255,0.9)`) carrying the theme's light text -- unreadable on
  LiveCam and Logs. The same gap left the Gray theme with a near-black select
  caret, a `#fff` dropdown panel, `#fafafa` modals, and a white side menu. Every
  rule was already variable-driven, so the `html[data-theme="red"]` prefix was
  simply dropped, and browser-default selects got the treatment already proven in
  catalogs.css (`.pfcat`): a custom arrow plus `color-scheme: dark`, now applied
  site-wide. Materialize's `rgba(0,0,0,0.42)` disabled-input colour (invisible on
  a dark surface) was fixed alongside.
- (2026-07-25) Two more instances of the same root cause, found on the INDI page.
  (1) `table.striped` tints odd rows `rgba(242,242,242,0.5)`, a pale band over a
  dark card, leaving the `.grey-text` body at 1.2:1. Row backgrounds now come
  from the theme variables, and `.grey-text` inside striped tables is promoted to
  `--pf-text` the way Red Night already did (4.7-5.7:1). (2) The empty checkbox
  box is `2px solid #5a5a5a`, which is 1.1:1 against the grey theme's card -- the
  INDI GoTo/Guide options looked like there were none. The outline now uses
  `--pf-text` (4.7:1 grey, 5.2:1 Red Night), the tick `--pf-link`, and the
  checkbox caption takes body text colour instead of Materialize's `#9e9e9e`
  label colour. The outline colour **must be scoped to `:not(:checked)`**:
  Materialize renders the tick from that same `::before`, rotated 40deg with its
  top/left borders transparent, so colouring all four sides makes the tick show
  up as a tilted rectangle (this was hit once and is now pinned by
  `test_checkbox_outline_colour_never_reaches_the_checked_state`). The
  `style.css?v=` cache buster in `base.html` was bumped to 7.

## `default_config.json`

Added the default GPS port option.

```json
"gps_port": "auto"
```

The `auto` default resolves by board model: CM5/Pi5 use `/dev/ttyAMA2`, Pi4 uses
`/dev/ttyAMA3`, and other boards fall back to `/dev/ttyAMA1`.

## `python/PiFinder/ui/preview.py`

The Focus screen was changed so bright or near-saturated scenes do not appear
black or solid-colored. The Focus marking menu also gained a direct shortcut to
the gain menu.

### Root Cause

The existing Focus screen display stretch is tuned for dark night-sky frames. It
maps the measured background to black so faint stars stand out. That is useful
for astronomical frames, but in bright scenes the background itself can be very
high. The result can be a black preview or an already-clipped 8-bit frame with
no visible detail.

The raw camera frame was arriving correctly, so the fix changes only the display
path and does not change exposure or gain.

### Added Constant

```python
STRETCH_BRIGHT_BACKGROUND = 220.0
```

Meaning:

- If the focus detector's background estimate is at or above this value, treat
  the frame as a bright/saturated display case.
- In that case, skip the normal dark-sky stretch.

### `_apply_stretch()` Change

Bright backgrounds bypass the normal stretch.

```python
if black >= STRETCH_BRIGHT_BACKGROUND:
    return image_obj
```

This is display-only. It does not change focus measurement or camera settings.

### Added `_orient_camera_image()`

Applies the same orientation rules used by the normal camera image path to the
raw-derived display image.

Behavior:

- Prefer `camera_rotation` when configured.
- Otherwise rotate based on `screen_direction`, matching the existing camera
  loop behavior.

### Added `_raw_display_image()`

Builds a raw-derived display image for bright scenes.

Processing steps:

1. Read the latest raw array from `self.shared_state.cam_raw()`.
2. Return no fallback if the array is not two-dimensional.
3. Convert to `float32`.
4. Trim to even dimensions.
5. Average nominal Bayer 2x2 blocks.
6. Build an 8-bit display image using 1.0 and 99.5 percentiles.
7. If the two percentile values differ by 1 ADU or less, treat the frame as a
   saturated or nearly flat bright raw frame and display it as white.
8. Apply `_orient_camera_image()`.

Reason for 2x2 averaging:

- IMX462 is reported by the driver as an `SRGGB12`-style format, but the actual
  hardware can behave like a mono sensor.
- Averaging nominal 2x2 Bayer blocks reduces checker-pattern artifacts on mono
  hardware.
- This is display-only and does not alter solver or focus-measurement data.

Reason for the flat-bright raw case:

- In bright conditions, the raw frame can be almost fully saturated, making the
  1.0 and 99.5 percentiles equal.
- Stretching that with `high = low + 1` makes `(arr - low)` zero everywhere,
  which maps the whole Focus preview to black.
- The raw fallback is only used after the frame has already been classified as
  bright, so a no-span raw fallback should stay bright rather than turn black.

### `update()` Display Path Change

Previous path:

```text
camera_image copy -> resize_for_display -> _apply_stretch -> red mask -> screen
```

Bright-background path after the change:

```text
shared_state.cam_raw -> 2x2 average -> percentile stretch -> orientation
-> resize_for_display -> red mask -> screen
```

Dark observation frame path after the change:

```text
existing camera_image focus-stretch path is preserved
```

Actual branch behavior:

- Starts with `display_image = raw_image` and `stretch_display = True`.
- If `_stretch_black >= STRETCH_BRIGHT_BACKGROUND`, tries `_raw_display_image()`.
- If the raw fallback succeeds, replaces `display_image` with the raw-derived
  image and sets `stretch_display = False`.
- If the raw fallback is unavailable, the existing image is used, but
  `_apply_stretch()` still bypasses the dark-sky stretch for bright backgrounds.
- The common resize, `L` conversion, and red-mask display path is then used.

### Expected Effect

- Bright scenes no longer collapse to black in the Focus screen.
- Details can still be shown even when the already-processed 8-bit frame is
  clipped.
- Exposure and gain are not changed.
- Dark observation frames continue to use the existing focus-stretch behavior.
- From the Focus screen, the `Gain` shortcut opens the `Camera Gain` menu, matching
  the existing `Exposure` shortcut pattern.

## `scripts/camera_lcd_preview.py`

Added a hardware diagnostic tool for checking camera raw input and SSD1351 OLED
output separately from the full PiFinder service.

### Script Role

- This is a diagnostic script, not part of the normal PiFinder runtime path.
- It owns the camera and OLED directly, so it must not run at the same time as
  the PiFinder service.
- It is kept so camera/LCD/SPI behavior can be rechecked quickly later.

### Main Features

- Opens `Picamera2` directly and captures the raw stream.
- Uses `PiFinder.sqm.camera_profiles` to apply camera profile crop/rotation.
- Averages nominal Bayer 2x2 blocks to create a mono display image.
- Uses percentile stretch to create an 8-bit LCD frame.
- Supports temporal smoothing for display noise reduction.
- Lets SSD1351 SPI speed be selected with `--spi-hz`.
- Supports native auto-exposure with `--auto-exposure`.
- Saves the latest displayed frame to `/tmp/camera_lcd_preview_latest.png`.

### Implementation Details

- Adds `REPO_ROOT/python` to `sys.path` so the script can import the `PiFinder`
  package when launched directly.
- Calls `DisplaySSD1351(bus_speed_hz=args.spi_hz)` only for `--display ssd1351`,
  making SSD1351 SPI speed tests explicit.
- Configures Picamera2 with `create_still_configuration({"size": (512, 512)}, raw={"size": profile.raw_size, "format": profile.format})`.
- With auto exposure enabled it sets only `AeEnable=True`; otherwise it sets
  manual `AnalogueGain` and `ExposureTime`.
- Captures raw data with `request.make_array("raw").copy().view(np.uint16)` and
  uses request metadata for the exposure/gain overlay.
- Handles `SIGINT` and `SIGTERM` so the camera is stopped and closed cleanly.
- Runs until `--duration` expires when it is greater than zero; `0` means run
  until stopped by the user.
- Creates the snapshot parent directory and saves the latest displayed frame at
  roughly one-second intervals.

### Important Options

```text
--display          default ssd1351
--spi-hz           default 32000000
--auto-exposure    use libcamera native AE
--exposure-us      manual exposure time, default 100
--gain             manual analogue gain, default 1.0
--fps              display update limit, default 2
--brightness       display brightness
--denoise          display-only temporal smoothing, default 0.70
--min-contrast     minimum contrast window for display stretch, default 256.0
--snapshot         latest displayed frame output path
--duration         stop after a fixed duration, default 0.0
--red              red night-vision rendering
--no-overlay       hide FPS/exposure/gain overlay
```

### Final Recommended Command

```bash
sudo systemctl stop pifinder
cd /home/pifinder/PiFinder
python3 scripts/camera_lcd_preview.py \
  --display ssd1351 \
  --spi-hz 32000000 \
  --auto-exposure \
  --fps 4 \
  --brightness 255 \
  --denoise 0.82 \
  --min-contrast 512 \
  --snapshot /tmp/camera_lcd_preview_latest.png
```

### Return To PiFinder

```bash
sudo systemctl start pifinder
```

### Expected Effect

- Camera and LCD can be checked without going through PiFinder UI or solver.
- OLED SPI issues can be separated from camera input issues.
- The selected SSD1351 32MHz value can be revalidated later.

## Auto-exposure — star-count controller option (2026-07-25)

Addressing the structural problems of match-count-driven auto-exposure
(cause conflation, catalog dependence, no bright-sky guard — see
[mf_auto_exposure_methods_en.md](mf_auto_exposure_methods_en.md)), a
cedar-server-style detected-star-count servo was added as an **opt-in
option**. Existing controllers, recovery, and the default are untouched.
Design: [mf_auto_exposure_plan_ko.md](mf_auto_exposure_plan_ko.md) (ko),
decision: [ADR m0020](../adr/m0020-star-count-controller-opt-in.md).

- New `python/PiFinder/auto_exposure_starcount.py`:
  `ExposureStarCountController` (target 20 detected, EMA α=0.5, deadband
  0.8–1.6, division step, anchor ±3-stop clamp, center-ROI mean>240
  guard, <4-star anchor fallback, 0 detected → reuses the recovery ladder).
- `types/positioning.py`: `SolveDiagnostics.Centroids` (published on
  every attempt).
- `solver.py`: `centroid_count` wired into both builders (exception path
  reports 0).
- `camera_interface.py` / `camera_pi.py`: new `camera_exp` value
  `"auto_star"` — `set_exp:auto_star` selects the star-count controller;
  dispatch branch with lazy creation. No separate config key.
- `ui/menu_structure.py` / `ui/callbacks.py`: "Star" item added to
  the Camera Exp menu (with live exposure suffix) — reachable from the
  focus screen's marking menu (long press → Exposure).
- i18n: "Star" translated for de/es/fr/ko/zh (AI-TRANSLATED
  markers), .mo recompiled.
- Tests: 21 new in `tests/test_auto_exposure_starcount.py`; full unit
  suite (754) passing.

## WiFi not restored during BT pairing fixed (2026-07-26)

Field incident: pairing a joystick (VR-PARK) froze the pairing screen and
left SSH/web dead until a reboot; the pairing itself had succeeded. The
WiFi pause is intentional (BLE coexistence; 35s cap + 60s watchdog), but
restoration failed.

Analysis (previous boot's journal was volatile; confirmed from code):

- The device has no RTC, so `time.time()` jumps when GPS/NTP corrects the
  clock after boot. Every pairing timeout (35s WiFi resume, 90s pair
  timeout, post-done cleanup) compared wall-clock against `pair_started`,
  so a backward jump froze them all — exactly the observed stuck screen
  with WiFi never returning. The 60s watchdog (`sleep` is monotonic)
  remains, but repeated retries keep re-arming pauses.
- The 35s resume only ran inside the pairing screen's update loop, so a
  screen that stops updating leaves only the watchdog.

Fixes: pairing timing switched to `time.monotonic()`;
`pause_wifi_for_bt_pairing()` now also arms an in-process
`threading.Timer` (35s) that resumes WiFi regardless of the UI loop
(resume is idempotent; the detached 60s watchdog stays as the last
resort).

Note: starting a pairing from the web remote cuts the operator's own
connection by design (coexistence) — pair from the LCD.

## Joystick button mapping (2026-07-26)

Map buttons of a Bluetooth-paired joystick/gamepad to PiFinder functions.
libinput (`keyboard_pi.py`) ignores joystick-class devices, so a
connected controller's buttons never reached the UI.

- New `python/PiFinder/joystick_input.py`: evdev reader thread (daemon in
  the main process). Rescans devices every 3s (BT event nodes appear
  late), handles EV_KEY buttons and ABS_HAT0X/Y hat axes (d-pads that
  report axes; `HAT0X-`-style pseudo-buttons). Pure mapping/dispatch is
  `JoystickDispatcher` (testable). Mapping persists in config
  `joystick_mapping` ({action: button_id}).
- Two action families, deliberately: **keypad** actions inject normal
  keycodes (arrows; GoTo = the keypad's 5, so it starts a GoTo on Object
  Details); **mount** actions go straight to the mountcontrol queue
  regardless of screen: hold-to-move N/S/W/E with the guide screen's
  lease/keepalive scheme (a dead reader can never leave the mount
  slewing), slew rate +/-, Tracking Off (new `set_tracking` queue
  command wired to the existing, previously uncalled readback-confirming
  method). Mount actions are ignored while `mount_control` is off.
- New `python/PiFinder/ui/joystick.py`: Settings > Advanced > Joystick —
  per-function bindings, capture mode (next pressed button binds; one
  button serves one function), live button tester showing the name and
  the kernel event code number (distinct physical buttons sending the
  same code surface as the same name; the number makes that visible),
  Clear All.
- `mf_pifinder_setup.sh`: install python3-evdev (already installed on
  this device).
- i18n: 9 ko strings, zh/de/es/fr for the menu name.
- Tests: 12 in `tests/test_joystick_input.py`; full unit suite (799)
  passing.

### Manual-motion 8 s re-send (2026-08-07)

Fixed held-button motion stopping at ~11 s on the joystick and ~10 s on the
LCD guide screen. The mount process refuses to extend one `manual_movement`
past its 10 s continuous-hold cap (`MANUAL_MOTION_MAX_CONTINUOUS_SECONDS`) on
keepalives alone; senders must periodically re-send the full command
(ui/base.py guide keys and pos_server already did, these two only sent
keepalives).

- `joystick_input.py` / `ui/indi.py`: new `MANUAL_MOTION_RESTART_INTERVAL =
  8.0` — while held, re-send `manual_movement` every 8 s instead of a
  keepalive, resetting the 10 s counter.
- The same investigation also measured OnStep firmware auto-stopping guides
  after ~7 s (the cause common to every input path); fixed on the firmware
  side (2026-08-07). The remaining 11 s stop after that fix was this missing
  re-send.
- Tests: one long-hold re-send case added to `test_joystick_input.py` (13).

## Bluetooth settings menu — joystick support, renamed (2026-07-26)

Settings > Advanced "Keyboard" is now "Bluetooth", and the
keyboard-only reconnect filter covers joysticks/gamepads. The
pairing/connect UI itself never cared about device type; the
keyboard-specific parts were the name and the reconnect filter.

- `ui/menu_structure.py` / `ui/bluetooth_keyboard.py`: menu/title renamed
  (label `keyboard_settings` and code identifiers kept).
- `sys_utils.py`: new `is_bluetooth_input_device()` — keyboard keywords
  plus joystick/joypad/gamepad/controller and common controller brandings
  (8BitDo, DualShock, DualSense, Joy-Con), icons
  `input-gaming`/`input-mouse`. Reconnect and startup auto-reconnect use
  it; `is_bluetooth_keyboard()` kept for compatibility.
- i18n: "Bluetooth" msgid for ko/zh/de/es/fr, .mo recompiled.
- Tests: 4 detection tests; full unit suite (787) passing.

Note: this covers **connection management** (scan/pair/auto-reconnect).
Mapping a connected joystick's buttons to UI keys is separate work — the
input layer (`keyboard_pi.py`) only processes libinput keyboard events,
and libinput usually does not classify joysticks as keyboards
(controllers with a keyboard mode being the exception).

## Locations edit form label overlap fixed (2026-07-26)

In Location Management's Edit Location modal the labels ("Latitude
(Decimal)" etc.) rendered on top of the server-prefilled values.
Materialize only floats a label above its value when the label carries
`active`; the edit-modal labels did not, and the page never called
`M.updateTextFields()`.

- `views/locations.html`: `class="active"` on the six prefilled labels,
  `M.updateTextFields()` on DOMContentLoaded and on modal `onOpenEnd`,
  and after the DMS toggle fills fields programmatically.
- `views/location_form.html`: `class="active"` on the add form's two
  prefilled labels (`error_in_m`=10, `source`="Manual Entry").

## LiveCam Raw Display mode does what its name says (2026-07-26)

Following the observation that the preview normalizes brightness even in
`raw_display` mode: `DisplayFrameBuilder.build()` applied the percentile
stretch unconditionally, so `raw_display` and `stretched` were identical
in code (the only mode branch was `bayer_2x2_average`). The design doc
lists them as distinct modes; the distinction was never implemented.

- `raw_live_stack.py`: `raw_display` now renders with a fixed linear
  mapping (`ADU x 255/(2^bit_depth-1)`, bit depth parsed from the raw
  format name "SRGGB12", dtype fallback) — gain/exposure changes show up
  as brightness. `stretched`/`bayer_2x2_average` keep the percentile
  stretch. A `sum` stack can clip on the fixed scale (inherent to
  summing); inspect sum stacks in stretched mode.
- `livecam_config.py`: `PREVIEW_MODE_RAW` constant.
- Tests: linearity (double signal -> double pixels), stretched-mode
  normalization kept, bit-depth fallback; full unit suite (783) passing.

## LiveCam status shows applied sensor values / RAW levels (2026-07-26)

Verified the report that changing gain shows no difference on the LiveCam
page. Conclusion: gain is applied correctly (requested 1/15/20/30 ->
driver 1.0/14.79/19.5/29.51, RAW p50 332 -> 3022). The preview's
percentile stretch normalizes each frame's own histogram to the full
display range, so a global multiplication (gain) cancels out of the
rendered image by construction — an unchanged image is expected. The LCD
focus screen uses absolute scaling (bias subtract -> digital gain ->
255/4095) so it does change, and its background-anchored EMA stretch can
make the frames right after a gain switch look odd. In low-power sleep
frames refresh only about once a minute, so a stale mid-transition frame
lingers — a large part of the perceived oddness.

- `views/livecam.html`: status panel gains "Sensor (applied)"
  (driver-reported gain/exposure) and "RAW Levels (p1/p50/p99.5)" rows,
  so gain/exposure changes the stretch erases are visible as numbers.
  The data was already in `/api/camera/raw-stack/status`; it just was
  not displayed.
- i18n: two ko strings added (AI-TRANSLATED), .mo recompiled.

## Auto-exposure reaches fast shutter speeds (2026-07-26)

Field-observed (Seoul, imx462): manual 25 ms solves repeatedly, but
switching to `auto_star` never gets back there. Two bounds kept the
exposure above the shutter speed that works; both are relaxed. Decision:
[ADR m0021](../adr/m0021-auto-exposure-reaches-fast-shutter.md), which narrows
ADR 0010's 200 ms floor to the ladder's first pass.

- `auto_exposure.py` — recovery never searched below its floor. ADR 0010
  floored the ladder at `[400, 800, 1000, 200] ms` on the reasoning that
  going shorter cannot beat 200 ms even under a bright sky. Under heavy
  light pollution that fails: this site solves at 25 ms and detects
  nothing at 400 ms–1 s, so recovery ping-ponged between 400 ms and 1 s
  indefinitely (observed). A failed pass of the long rungs now continues
  into `[100, 50, 25] ms` rather than replaying them; the dark-sky common
  case still costs 8 attempts.
- `auto_exposure_starcount.py` — the anchor bound was unreachable by
  construction. Adjustments clamp to anchor/8..anchor×8, the anchor
  starts as a 400 ms guess and is only relearned inside the deadband, so
  where the working exposure is shorter than anchor/8 (25 ms < 50 ms) the
  servo asked to go shorter every frame, sat pinned at exactly 50 ms and
  returned "no change" — never reaching the deadband, so the anchor never
  updated. The anchor now follows the boundary after `reanchor_after` (3)
  consecutive clamps in the same direction; an unclamped step or a
  direction flip resets the streak, so a single odd frame still cannot
  fling the exposure.
- `auto_exposure_starcount.py` / `auto_exposure.py` — every arm of the
  control law moved up or sideways. With both bounds relaxed, a debug
  trace showed a closed cycle: `1s -> bright-sky guard (center ROI mean
  251 > 240) -> anchor 400ms -> 0 detected -> recovery climbs -> 800ms ->
  6-8 detected, short of the target of 20 -> raise to 1s -> guard ...`.
  Two inversions: (1) the guard **halves** the exposure instead of
  returning to the anchor and records a ceiling there, capping later
  raises so the servo cannot climb back into the frame that just proved
  to be sky glow; the ceiling is retired by a deadband hit or a clearly
  dark frame (mean below `bright_clear_mean`, 120 — the gap from 240 is
  deliberate hysteresis). (2) Zero-detection recovery takes a descending
  ladder `[200, 100, 50, 25] ms` when the caller can see the frame is
  sky-glow limited; the prior is chosen once at activation and a failed
  pass escalates into the rungs the chosen ladder left out. The
  match-count controller has no brightness signal and is unchanged.
- Tests: 6 ladder tests in `tests/test_auto_exposure.py` (escalation and
  the bright ladder, plus the wrap test updated) and 7 in
  `tests/test_auto_exposure_starcount.py` (re-anchoring, bright ceiling);
  full unit suite (783) passing.
- On-device (Seoul, imx462): after switching to `auto_star` the recovery
  ladder was logged walking `400 -> 800 -> 1000 -> 200 -> [escalation] ->
  100 -> 50 -> 25 ms`, reaching 25000µs. It previously ping-ponged
  between 400 ms and 1 s indefinitely.

## LiveCam camera settings are committed (2026-07-25)

Settings changed on the LiveCam page reverted after navigating to another
page and back. Two causes; the first is the root one. Gain not being
persisted is intended behaviour and was left alone (a restart returns to
the camera profile default; only the Exp Save item commits a gain) — the
intent is now commented at the `set_gain` handler so it does not get
"fixed" later.

- `config.py` — cross-process config clobbering (root cause).
  `config.json` is shared by the main/UI, camera and web processes, each
  holding a `Config` loaded at its own start. `set_option()` dumped that
  in-memory snapshot over the file, so saving one setting reverted every
  key another process had changed since (the camera process saving
  `camera_exp` undid the LiveCam settings the web UI had just written).
  Writes now re-read and merge the current file first
  (`_refresh_from_disk()`), and are written to a temp file and renamed
  (`os.replace()`) so no reader sees a half-written config.
  `reset_filters()` merges the same way. A corrupt file keeps the
  in-memory copy, so one write can never turn into a full reset.
- `camera_interface.py` — Auto exposure modes were never saved.
  Only manual exposures were written to `camera_exp`;
  `set_exp:auto`/`auto_star` was not, so the config and both UIs kept
  showing the previous manual value while the camera ran auto, and the
  choice was lost on restart. Auto modes are now recorded
  (`set_exp:native` stays unsaved — it is the temporary daytime-align
  mode).
- `config.py` — readers did not see the saved value either. Selecting
  Star on the LiveCam page changed the config and the camera, but the
  main/UI process kept serving the `Config` it loaded at startup, so the
  Camera Exp menu checkmark and the focus-screen suffix still showed the
  old exposure until something called `load_config()`. `get_option()` now
  reloads when the file's `(mtime, size)` changed, re-checked at most
  every `REFRESH_INTERVAL` (0.25s) so draw-loop lookups do not stat per
  call (measured 1.64µs/call). equipment/locations still come from the
  in-memory objects, rebuilt only by an explicit `load_config()`.
- `api_extensions.py` — the web Apply recorded the change later than the
  LCD menu did. The Camera Exp menu writes its `config_option` on
  selection (`ui/text_menu.py`) and its post_callback queues `set_exp`;
  the web endpoint only queued, leaving the write to the camera process.
  An idle unit in low-power sleep drains the camera queue once per ~60
  loop passes, so until then the page and the menu both reported the
  previous exposure. The endpoint now records the exposure first and
  queues after, like the menu. Gain stays unrecorded — the Camera Gain
  menu does not write config either.
- Tests: new `tests/test_config.py` (10: cross-process merge, atomic
  write, corrupt file, read refresh, recheck interval), new
  `tests/test_api_camera_controls.py` (7: the real endpoint driven
  through a Flask test client — exposure recorded and queued, gain
  queued only, clamped value recorded, invalid input changes nothing);
  full unit suite (771) passing. On-device: after a service restart
  `auto_star` survives, a camera-side exposure save keeps the LiveCam
  `low_percentile`, a long-lived `Config` reader picks up a web-side
  change, and right after Apply — before the camera drains its queue —
  the page and config already report the new exposure.

## Wi-Fi recovery tool — BT-coexistence firmware wedge response (2026-08-05)

Response to the same-day incident where BT joystick pairing killed the
STA and only two power cycles recovered it. The CYW43455 shares one
2.4 GHz radio between Wi-Fi and BT; high-duty BT phases (pairing,
boot-time reconnect storms) can wedge the brcmfmac firmware's STA state
machine, which no service restart can fix (kernel/firmware layer).

- `scripts/mf_wifi_recover.sh`: stop units (monitor/hostapd/dnsmasq,
  NetworkManager) → delete uap0 → reload brcmfmac_wcc/brcmfmac/brcmutil
  (chip firmware reset) → bring back in boot order (NM → prepare → AP
  units) → report. Log: `PiFinder_data/wifi_recover.log`. Measured: full
  cycle 11 s on a healthy stack, STA reassociated immediately, AP back
  up, SSH session survived.
- LCD: Settings > Advanced > **WiFi Recover** (Confirm/Cancel, same
  pattern as shutdown). `callbacks.recover_wifi` →
  `sys_utils.recover_wifi()` (exception-isolated; failure shows "WiFi
  still down"). Verified on the real device screen.
- i18n: four new msgids translated into all five languages.
- Field rules from the incident analysis: pair at home, never reboot
  with the joystick powered on; the mount (2.4 GHz-only ESP32 client on
  the AP) pins the AP — and via the AP+STA same-channel constraint the
  STA — to 2.4 GHz, so a 5 GHz escape is not available in this setup.

## Last two deferred upstream items ported — SQM colour (#560), Focus multi-star (#531) (2026-08-05)

The two remaining deferred items were ported under the MF-first rule.
Details live in the sync reference's 2026-08-04 section (items updated
to applied).

- **#560 (`fde9beaa`)**: sky-colour radiometric zero point — the mono
  misdetection trap is blocked by a `profile.mono` first-refusal in
  `_mosaic_phase_is_rggb`; imx462 keeps the constant zero point (no
  ~+0.74 mag drift), pinned by an MF regression test. Upstream's constant
  refit (15.25→15.159, −0.09 mag) is accepted as a calibration
  improvement.
- **#531 (`b7fa9e8a`)**: the 4-mode Focus rewrite adopted, with the
  fork's three preview features re-implemented (guide keys, Gain marking
  menu, daytime raw render → Image mode). A wholesale positioning.py
  take that clobbered MF fields was caught by tests and backed out. Docs
  converged on the post-#546 state (closing #546/#547). Verified
  headless: all 4 modes render, GAIN menu jumps, debug-camera solving
  works.
- Full suite: 1,105 pass. The only unported remainder is the five i18n
  string-wrapping hunks (#562).

## SSD1333 four-axis brightness port (partial port of upstream #568+#570, 2026-08-05)

With the plan to adopt the SSD1333 (176×176) panel confirmed, the
deferred upstream brightness redesign was ported partially. Commits
`0cb8314e` (#568) + `31f0a5c5` (#570) — pre-charge voltage as a fourth
brightness axis, and the dimming policy refit from a measured response
surface (knee curve + table lookup) instead of the by-eye gamma.

- Taken: the `displays.py`/`ssd1333_device.py` driver, 17 brightness
  tests, and the model docs (revised ADR 0023, `docs/ax/display/`
  CONTEXT+response).
- Excluded: 44 measurement journals, runner scripts, and the bench
  harnesses (`panel_photometry`/`precharge_sweep`) — ~6,250 lines of
  standalone tooling needing a photometer rig; fetch from upstream if
  this fork's panel ever needs re-characterizing (MF note at the top of
  the response doc).
- MF-first preservation verified: `display_spi()` Pi5 helper,
  `__init__(bus_speed_hz)` (SSD1333 at 40MHz), MF `rotate=0`,
  `get_display(spi_speed_hz)`, display auto-detection untouched —
  markers re-checked after each merge.
- Incidental repair: `test_hardware_detect_display.py` had been left
  patching the old `board` attribute after July's `get_i2c` migration
  (cc7ae95e) — invisible because it carried no pytest marker and was
  always deselected from `-m "smoke or unit"` runs. Rewritten against
  the `get_i2c` seam, given a unit marker, probe-error fallback test
  added. Full suite: 1,047 pass.
- No behaviour change on the current device (SSD1351). When the SSD1333
  panel is connected: a non-rev4 board has no BQ25895 marker, so
  auto-detection stays on ssd1351 — pass `--display ssd1333` in the
  service ExecStart.

## Release check now points at the fork (2026-08-05)

The Software screen's release check read brickbots' `release/version.txt`,
so upstream publishing 2.6.1 would show "Update Now" on fork devices
against a foreign release (recorded as pending in the 2026-08-04 sync
round). The NixOS migration gate (`migration_gate.json`) had the same
problem — upstream flipping `nixos_for_everyone` could remotely trigger a
migration this fork excludes.

- `ui/software.py`: both URLs now point at `hjoungjoo/MF_PiFinder`'s
  release branch. The fork has no release branch yet, so both 404
  (verified live).
- **New "Unknown" display branch**: `update_needed()` deliberately returns
  True on unparseable input (pinned by upstream tests), so a failed fetch
  (network down, or no release published) used to surface as "Update Now".
  An "Unknown" release version now renders "Release info / unavailable"
  and offers no update.
- i18n: two new msgids (`Release info`, `unavailable`) translated into all
  five languages (AI-TRANSLATED markers).
- Cutting a release only requires `version.txt` on the release branch for
  the check to work; `pifinder_update.sh` already pulls the fork's own
  release branch, so it needed no change.
- **Version scheme (same-day follow-up)**: `version.txt` moved to an
  `m`-prefixed scheme (`2.6.0` → `m2.6.0`, user decision) to distinguish
  fork releases from upstream's. Display paths (splash/web/API) show the
  string verbatim — unaffected. `update_needed()` now parses via
  `_semver_tuple()`, which strips the `m` prefix before comparing —
  without it, `int("m2")` raised into the error bias (True), so devices
  would show "Update Now" forever, including right after updating. Four
  prefix tests added (equal-versions=False is the critical one).
  Migrations gate on marker files, not the version string — verified
  unaffected.

## Install script naming — `pifinder_setup.sh` is now the fork installer (2026-08-04)

The fork's installer sat under a separate name (`mf_pifinder_setup.sh`), so
cloning the repo and running `pifinder_setup.sh` installed the upstream
release instead. The primary name now belongs to the fork installer.

- `mf_pifinder_setup.sh` → `pifinder_setup.sh` (fork installer is the default).
- The upstream installer is preserved as `pifinder_setup.sh.bak`, kept as the
  comparison base for upstream re-syncs.
- **Clone branch fixed**: `--branch mf_pifinder` → `--branch main`. The
  `mf_pifinder` branch no longer exists on origin (hjoungjoo/MF_PiFinder), so
  installing with this script failed at the clone step. The install-command URL
  in the header was updated to `main/pifinder_setup.sh` as well.
- `python/tests/test_wifi_apsta_static.py` reads `pifinder_setup.sh` directly —
  all 6 still pass against the new content (same AP+STA provisioning).

## Documentation Files

### `docs/mf_dev/mf_bookworm_install_ko.md`

Korean Bookworm installation flow for the `mf_pifinder` branch, based on the CM5
Bookworm 64-bit install procedure.

PiFinder-specific content includes:

- PiFinder repository location and branch
- PiFinder dependency installation
- PiFinder systemd service installation
- PiFinder data directory layout
- Custom OS username/hostname installation instead of requiring `pifinder`
- PiFinder developer-mode test commands
- PiFinder peripheral check commands
- The Bookworm boot config path PiFinder needs to handle

### `docs/mf_dev/mf_bookworm_install_en.md`

English version of `mf_bookworm_install_ko.md`.

### `docs/mf_dev/mf_change_history_ko.md`

The Korean source change history document.

### `docs/mf_dev/mf_change_history_en.md`

The English source change history document.

### `docs/mf_dev/mf_pifinder_new_device_tasks_ko.md`

Korean checklist for installing and verifying the `mf_pifinder` branch on a new
Raspberry Pi device.

### `docs/mf_dev/mf_pifinder_new_device_tasks_en.md`

English version of `mf_pifinder_new_device_tasks_ko.md`.

### `docs/mf_dev/mf_pifinder_rpi4_pi5_compatibility_ko.md`

Korean summary of Pi4/Pi5/CM5 board profiles, automatic defaults, and
verification steps.

### `docs/mf_dev/mf_pifinder_rpi4_pi5_compatibility_en.md`

English version of `mf_pifinder_rpi4_pi5_compatibility_ko.md`.

## Final Behavior Baseline

With the current source changes, PiFinder is expected to behave as follows:

- On Bookworm, PiFinder code prefers `/boot/firmware/config.txt`.
- On legacy systems, `/boot/config.txt` fallback remains available.
- Install/update scripts use the current OS user's `$HOME/PiFinder` and
  `$HOME/PiFinder_data` by default.
- systemd and Samba configs are rendered at install time with the actual OS user
  and home paths.
- Choosing a unique hostname during Raspberry Pi OS setup reduces
  `<hostname>.local` mDNS collisions.
- IMX462 is no longer forced to the imx290 overlay.
- SSD1351 OLED default SPI speed is `32MHz`.
- Display initialization can use `/dev/spidev10.0`.
- Initial Pi camera gain uses the camera profile's `analog_gain`, matching the
  original source.
- The `Camera Gain` menu can adjust runtime gain and return to the original
  profile default with `Profile`.
- `GPS Settings > GPS Port` can select the gpsd serial device.
- This CM5 unit currently uses `/dev/ttyAMA2` at `115200` baud for GPS.
- `Settings > Advanced > Keyboard` can scan for and connect Bluetooth keyboards.
- USB and Bluetooth keyboards are mapped into PiFinder input through the default
  `keyboard_pi` libinput path.
- Plain alphabet keys from USB/Bluetooth keyboards enter real text in search and
  text-entry screens.
- USB/Bluetooth keyboard `Alt` combinations produce `ALT_*` events.
- Holding USB/Bluetooth keyboard `Left`, `Right`, or `Enter/KP Enter` for one
  second produces long-key events.
- Holding USB/Bluetooth keyboard `Up` or `Down` for one second repeats normal
  `UP/DOWN` events.
- USB/Bluetooth keyboard `Shift` or `Ctrl` long-key shortcuts are retained for
  compatibility.
- Paired/trusted Bluetooth keyboards are automatically reconnected in the
  background when the PiFinder service starts.
- `Settings > User Pref... > Language` can select Korean.
- Korean UI uses the Sarasa CJK font, and PiFinder restarts immediately after
  the language change so the font is reloaded.
- Keyboard character input remains English alphabet input in Korean UI.
- Bright Focus screen frames use the raw-derived display fallback.
- Dark observation Focus screen frames keep the existing focus-stretch path.
- `scripts/camera_lcd_preview.py` provides isolated camera-to-LCD diagnostics.

## Pi4 Bookworm Compatibility Follow-Up

Raspberry Pi 4 Bookworm 64-bit hardware testing showed that the CM5 GPS default
port did not match Pi4, so GPS port selection now has a board-aware automatic
default.

- Changed the `default_config.json` `gps_port` default to `auto`.
- Added `python/PiFinder/board_config.py`, defining `pi5_class`, `pi4`, and
  `legacy` profiles for board-specific UART overlays and default GPS ports.
- `sys_utils.get_default_gpsd_device()` now resolves through the `board_config`
  profile: CM5/Pi5 use `/dev/ttyAMA2`, Pi4 uses `/dev/ttyAMA3`, and other
  boards use `/dev/ttyAMA1`.
- `pifinder_paths.sh` uses matching `pi5_class`/`pi4`/`legacy` helpers so install
  time UART overlays and gpsd `DEVICES` defaults follow the same model.
- Added `Auto` and `/dev/ttyAMA3` entries to `GPS Settings > GPS Port`.
- The setup script uses the same board detection when writing the initial
  `/etc/default/gpsd` `DEVICES` value.
- On the Pi4 test unit, gpsd identified the u-blox receiver on `/dev/ttyAMA3` at
  115200bps. Indoor testing still had no GPS fix, so outdoor antenna testing is
  still pending.
- Bookworm BlueZ did not accept `bluetoothctl paired-devices`, so Bluetooth
  listing now uses `bluetoothctl devices Paired`.
- The tested `K06 BLE Keyboard` stayed paired/trusted/connected but did not
  create a `/dev/input/event*` device with the default BlueZ input settings.
- Enabling `UserspaceHID=true` and `LEAutoSecurity=true` in
  `/etc/bluetooth/input.conf`, then restarting Bluetooth, created
  `/dev/input/event4`; `libinput debug-events` confirmed arrow-key input.
- The setup script now applies the same BlueZ input settings during new
  installs.
- Added `docs/mf_dev/mf_pifinder_rpi4_pi5_compatibility_ko.md` to summarize Pi4/Pi5/CM5
  profiles, install-time defaults, and verification steps in one place.

## Verified Items

Source-level check:

```bash
bash -n \
  /home/pifinder/PiFinder/pifinder_paths.sh \
  /home/pifinder/PiFinder/pifinder_setup.sh \
  /home/pifinder/PiFinder/pifinder_update.sh \
  /home/pifinder/PiFinder/pifinder_post_update.sh \
  /home/pifinder/PiFinder/switch-ap.sh \
  /home/pifinder/PiFinder/switch-cli.sh \
  /home/pifinder/PiFinder/migration_source/v1.x.x.sh \
  /home/pifinder/PiFinder/migration_source/v2.1.0.sh \
  /home/pifinder/PiFinder/migration_source/v2.2.1.sh \
  /home/pifinder/PiFinder/migration_source/v2.2.2.sh \
  /home/pifinder/PiFinder/migration_source/v2.4.0.sh \
  /home/pifinder/PiFinder/migration_source/v2.6.0.sh

python3 -m py_compile \
  /home/pifinder/PiFinder/python/PiFinder/api_extensions.py \
  /home/pifinder/PiFinder/python/PiFinder/main.py \
  /home/pifinder/PiFinder/python/PiFinder/sys_utils.py \
  /home/pifinder/PiFinder/python/PiFinder/keyboard_interface.py \
  /home/pifinder/PiFinder/python/PiFinder/keyboard_pi.py \
  /home/pifinder/PiFinder/python/PiFinder/camera_interface.py \
  /home/pifinder/PiFinder/python/PiFinder/ui/base.py \
  /home/pifinder/PiFinder/python/PiFinder/ui/callbacks.py \
  /home/pifinder/PiFinder/python/PiFinder/ui/fonts.py \
  /home/pifinder/PiFinder/python/PiFinder/ui/bluetooth_keyboard.py \
  /home/pifinder/PiFinder/python/PiFinder/ui/menu_manager.py \
  /home/pifinder/PiFinder/python/PiFinder/ui/menu_structure.py \
  /home/pifinder/PiFinder/python/PiFinder/ui/textentry.py \
  /home/pifinder/PiFinder/python/PiFinder/ui/preview.py \
  /home/pifinder/PiFinder/python/PiFinder/displays.py \
  /home/pifinder/PiFinder/scripts/camera_lcd_preview.py
```

Korean locale check:

```bash
pybabel compile -d python/locale -l ko
python3 - <<'PY'
import gettext
tr = gettext.translation('messages', 'python/locale', languages=['ko'])
_ = tr.gettext
for s in ['Start', 'Focus', 'Chart', 'Objects', 'GPS Port', 'Keyboard', 'Korean']:
    print(f'{s} -> {_(s)}')
PY
```

PiFinder service check:

```bash
systemctl status pifinder --no-pager --full
journalctl -u pifinder -n 80 --no-pager
```

Screen/API check:

```bash
curl -fsS http://127.0.0.1/api/screen -o /tmp/pifinder_screen.png
curl -fsS http://127.0.0.1/api/camera/raw -o /tmp/pifinder_camera_raw.png
```

These commands are included for documentation. This document does not cover OS
installation or hardware assembly procedures.
