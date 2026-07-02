# MF_PiFinder Upstream Patch Reference

Created: 2026-07-03

This document is a practical reference for future sync work from
`brickbots/PiFinder` into the `mf_pifinder` branch.  It records which patches
have already been applied, which upstream changes were intentionally skipped,
and which MF-specific areas must be preserved when the original source changes.

Goals:

- Separate already-applied upstream changes from MF-specific patches.
- Identify files that are likely to conflict during future upstream syncs.
- Provide a repeatable checklist for review and verification.

## Current Baseline

Local branch:

- `mf_pifinder`

Upstream comparison target:

- `brickbots/PiFinder main`

As of 2026-07-03, the branch includes these selected upstream changes:

- NixOS PR build CI
- case/accessory STL changes
- observing-list CSV import improvements
- UTC-aware datetime handling
- Set Time/Date self-gating when no location lock exists
- OBJ_TYPES single-source refactor

MF-only follow-up:

- SSD1333 automatic display detection, separated from the larger Rev-4 hardware patch

This is not a full change history.  For feature-by-feature history, see
`docs/mf_change_history_en.md`.

## Upstream Changes Already Applied

| Area | Status | Notes |
| --- | --- | --- |
| NixOS PR build CI | Applied | GitHub Actions and manifest scripts; no runtime impact |
| case/accessory files | Applied | STL/JPG/README changes only |
| Observing-list CSV import | Applied | `obslist_formats.py`, docs, tests |
| UTC-aware datetime | Applied | Adds `timez.py`; touches state/server/callback time handling |
| Set Time/Date self-gate | Applied | Manual time/date UI is inert until a location lock exists |
| OBJ_TYPES single-source | Applied | Type filter menu is generated from `OBJ_TYPES` |

Do not reapply these changes during the next upstream sync.

## Rev-4 Hardware Patch: Intentionally Not Fully Applied

The upstream Rev-4 hardware enablement patch has not been merged wholesale.

Skipped features:

- BQ25895 battery telemetry
- BQ25895 fast-charge runtime configuration writes
- sound/earcon buzzer subsystem
- GPIO15 hardware power button
- GPIO14 gpio-poweroff latch
- battery titlebar icon
- Raspberry Pi red power LED control

Reason:

- The upstream patch assumes Rev-4-specific GPIO/I2C/PWM wiring.
- GPIO14 poweroff latch can be unsafe on boards without the matching circuit.
- Sound should be optional and probably default OFF for observing use.
- Charger writes should be gated and hardware-tested before becoming active.

Partially applied:

- SSD1333 display auto-detection only

Current implementation:

- `python/PiFinder/hardware_detect.py`
- `python/PiFinder/main.py`
- `python/PiFinder/splash.py`
- `python/tests/test_hardware_detect_display.py`

Behavior:

- Uses BQ25895 I2C address `0x6A` ACK as the Rev-4/SSD1333 display marker.
- Defaults to `ssd1333` when detected.
- Falls back to `ssd1351` if detection fails, Blinka import fails, or GPIO/I2C access fails.
- The `--display` command-line option still overrides auto-detection.

Future Rev-4 work should be split into small pieces.  Do not merge
battery/sound/power/latch in one step.

## MF-Specific Patch Areas

These areas are either not present upstream or intentionally differ in
`mf_pifinder`.

### Platform / Bookworm / Pi4-Pi5-CM5

Key files:

- `pifinder_paths.sh`
- `pifinder_setup.sh`
- `pifinder_update.sh`
- `pifinder_post_update.sh`
- `python/PiFinder/board_config.py`
- `python/PiFinder/boot_config.py`
- `python/PiFinder/sys_utils.py`
- `python/PiFinder/displays.py`
- `pi_config_files/*.service`

Preserve:

- Prefer `/boot/firmware/config.txt` on Bookworm, fallback to `/boot/config.txt`.
- Render `PiFinder_data`, systemd, and Samba paths for the current OS user.
- Select GPS UART defaults by board profile.
- Use `uart2-pi5` on Pi5/CM5 to avoid OLED CS conflicts.
- Support both `/dev/spidev0.0` and `/dev/spidev10.0`.

### Camera / Focus / Gain

Key files:

- `python/PiFinder/camera_interface.py`
- `python/PiFinder/ui/preview.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/PiFinder/ui/callbacks.py`
- `scripts/camera_lcd_preview.py`

Preserve:

- Focus preview behavior.
- Runtime/profile camera gain controls.
- LCD camera preview debug script.

### Korean Localization

Key files:

- `python/locale/ko/LC_MESSAGES/messages.po`
- `python/locale/ko/LC_MESSAGES/messages.mo`
- `python/PiFinder/ui/fonts.py`
- `python/PiFinder/ui/menu_structure.py`

Preserve:

- `ko` in the language menu.
- CJK font handling.
- Restart notice flow for language changes.

### Bluetooth / USB HID Keyboard

Key files:

- `python/PiFinder/keyboard_interface.py`
- `python/PiFinder/keyboard_pi.py`
- `python/PiFinder/ui/bluetooth_keyboard.py`
- `python/PiFinder/ui/textentry.py`
- `python/PiFinder/ui/menu_structure.py`

Preserve:

- libinput-based HID keyboard mapping.
- Bluetooth scan/pair/connect UI.
- INDI Guide page-only `qwe/asd/zxc` mapping.
- Guide motion press/release and timeout fail-safe behavior.

### Integrated Time Sync

Key files:

- `python/PiFinder/gps_time_sync.py`
- `python/PiFinder/gps_time_sync_helper.py`
- `python/PiFinder/ui/gps_time_sync_status.py`
- `scripts/install_chrony_time_sync.sh`
- `scripts/install_gps_time_sync_helper.sh`
- `pi_config_files/pifinder_gps_time_sync.service`

Preserve:

- `chronyd` is the primary clock manager.
- PiFinder UI manages GPS/NTP/RTC/helper status.
- Actual system-clock writes happen in the privileged helper/service layer.
- INDI/OnStep time sync must use the current PiFinder UTC time, not stale user
  form input.

### Wi-Fi AP+STA

Key files:

- `scripts/pifinder_apsta.sh`
- `scripts/import_initial_wifi_networks.py`
- `python/PiFinder/sys_utils.py`
- `python/PiFinder/server.py`
- `python/views/network.html`
- `pi_config_files/pifinder_apsta_prepare.service`
- `pi_config_files/pifinder_apsta_monitor.service`
- `pi_config_files/dhcpcd.conf.apsta`

Preserve:

- STA/AP/AP+STA modes.
- AP+STA AP channel follows the STA channel.
- Configurable AP IP/security/password.
- AP+STA internet sharing option is default OFF.
- User-facing warning about load and lower throughput when sharing internet.
- Initial OS Wi-Fi profiles can be imported.
- New STA entries can use scanned SSIDs.
- STA band preference policy is preserved.

### Locations Catalog

Key files:

- `python/PiFinder/location_catalog.py`
- `python/PiFinder/data/location_catalog.json`
- `scripts/build_location_catalog.py`
- `python/views/locations.html`
- `python/views/location_form.html`

Preserve:

- Country/region/district/city lookup fills coordinates and altitude.
- North Korea is excluded.
- South Korea uses more detailed administrative location data.
- Manually loaded locations can be used indoors without a GPS lock.
- Red Night theme must not leak bright select/form/action tooltip colors.

### Web Theme / PWA

Key files:

- `python/views/base.html`
- `python/views/css/style.css`
- `python/views/js/init.js`
- `python/views/manifest.webmanifest`
- `python/views/service-worker.js`
- `python/views/images/pwa-icon-192.png`
- `python/views/images/pwa-icon-512.png`

Preserve:

- Red Night theme uses dark red observing-safe UI colors.
- Log content keeps its original semantic colors.
- Android PWA fullscreen/theme-color behavior.
- No extra theme bar under navigation.

### INDI / OnStepX

Key files:

- `python/PiFinder/mountcontrol_indi.py`
- `python/PiFinder/pos_server.py`
- `python/PiFinder/ui/indi.py`
- `python/views/indi_mount.html`
- `scripts/install_indi_mount.sh`
- `scripts/install_indi_mount_OnstepX.sh`
- `scripts/install_indi_mount_archive.sh`
- `scripts/package_indi_mount_archive.sh`
- `scripts/patches/indi-v2.2.3.1-onstepx.patch`

Preserve:

- INDI is optional.  A base PiFinder install must not require INDI.
- OnStepX is a custom INDI driver name; do not overwrite the original LX200
  OnStep driver.
- Read the active driver name from the INDI profile.
- Use OnStepX-specific UI/behavior only when the active driver is OnStepX.
- Location/time sync must use the current PiFinder UTC time.
- Generic INDI mount paths must remain available for non-OnStepX mounts.
- INDI restart stops server/profile/driver and starts them again, then connects
  when possible.

### LCD INDI UI

Key files:

- `python/PiFinder/ui/indi.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/PiFinder/ui/base.py`
- `python/PiFinder/keyboard_pi.py`

Preserve:

- INDI entry at the bottom of the Start menu.
- INIT/STATUS/GUIDE pages.
- Guide keypad layout `789 / 4 6 / 123`.
- `qwe/asd/zxc` mapping only inside the Guide page.
- Key 5 is not a guide-motion key.
- Motion is press-to-move, release-to-stop.
- Timeout/fail-safe stop protects against freezes or missed key releases.
- Top-bar `I` indicator reflects INDI connection health.

### SkySafari / Mount Mode Integration

Key files:

- `python/PiFinder/pos_server.py`
- `python/PiFinder/mountcontrol_indi.py`
- `python/PiFinder/imu_pi.py`
- `python/PiFinder/imu_calibration.py`

Preserve:

- SkySafari `:Sr/:Sd` stores target coordinates.
- SkySafari `:MS#` is GoTo.
- SkySafari `:CM#` is Sync/Align.
- `:CM#` prefers the most recent parsed `Sr/Sd` target.
- If GoTo forwarding is enabled, Align/Sync can also be forwarded to INDI/OnStep.
- Before solving, IMU fallback/correction may be used.
- After a successful solve, IMU alignment correction is reset.
- OnStep-specific behavior is gated by driver capability/name.

### IMU Compass / Calibration

Key files:

- `python/PiFinder/imu_pi.py`
- `python/PiFinder/imu_calibration.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/PiFinder/ui/callbacks.py`

Preserve:

- Magnetometer/compass fusion is optional.
- Default IMU behavior remains stable.
- Calibration is auto-saved/loaded when possible.
- Manual save/load/clear controls remain available.

## High-Conflict Files

Review these first during upstream sync:

```text
default_config.json
pifinder_setup.sh
pifinder_post_update.sh
python/PiFinder/main.py
python/PiFinder/server.py
python/PiFinder/sys_utils.py
python/PiFinder/sys_utils_fake.py
python/PiFinder/displays.py
python/PiFinder/splash.py
python/PiFinder/keyboard_interface.py
python/PiFinder/keyboard_pi.py
python/PiFinder/pos_server.py
python/PiFinder/mountcontrol_indi.py
python/PiFinder/ui/base.py
python/PiFinder/ui/callbacks.py
python/PiFinder/ui/menu_manager.py
python/PiFinder/ui/menu_structure.py
python/views/base.html
python/views/css/style.css
python/views/network.html
python/views/locations.html
python/views/indi_mount.html
```

Most sensitive files:

- `main.py`: startup processes, display/GPS/camera/keyboard selection
- `server.py`: web routes, network/location/INDI APIs
- `sys_utils.py`: privileged system operations, Wi-Fi, chrony, INDI helpers
- `keyboard_pi.py`: GPIO keypad, HID keyboard, guide fail-safe
- `ui/menu_structure.py`: upstream menu additions often conflict with MF menus
- `ui/base.py`: titlebar/status/UI helpers
- `pos_server.py`: SkySafari LX200 protocol, GoTo/Sync/Guide/IMU fallback

## Recommended Sync Procedure

1. Check current state:

```bash
git status --short --branch
git remote -v
git fetch upstream main
git rev-list --left-right --count upstream/main...HEAD
git log --oneline --left-right --cherry-pick upstream/main...HEAD --max-count=80
```

2. Inspect upstream changes:

```bash
git diff --stat HEAD...upstream/main
git diff --name-status HEAD...upstream/main
```

3. Dry-run conflicts:

```bash
git merge-tree --write-tree HEAD upstream/main
```

4. Apply in small groups:

- Docs/CI/assets first.
- Runtime Python changes by feature.
- Hardware side effects separately.
- Always manually inspect INDI, network, time sync, and SkySafari files.

5. Minimum verification:

```bash
python -m compileall -q python/PiFinder
python -m pytest \
  python/tests/test_hardware_detect_display.py \
  python/tests/test_obj_types_docs.py \
  python/tests/test_menu_struct.py \
  python/tests/test_time_date_gate.py \
  python/tests/test_state_datetime.py \
  python/tests/test_obslist_formats.py \
  python/tests/test_obslist_resolve.py \
  python/tests/test_pos_server.py \
  python/tests/test_mountcontrol_indi.py \
  python/tests/test_web_theme_static.py \
  python/tests/test_wifi_apsta_static.py \
  python/tests/test_location_catalog.py \
  python/tests/test_sys_utils.py
```

6. Hardware verification:

- Pi4 Bookworm 64-bit
- Pi5 or CM5 Bookworm 64-bit
- Camera preview/focus
- GPS lock/unlock and manual location load
- Bluetooth keyboard key press/release
- Web Red Night theme
- AP+STA and AP client list
- INDI Web UI and LCD INDI Guide stop fail-safe
- SkySafari GoTo/Align/Guide path

## Known Test Caveats

The full `python -m pytest python/tests` suite may still include unrelated
environment/test-API failures.

Known causes as of 2026-07-03:

- `test_multiproclogging.py`: depends on `pifinder_logconf.json` path
- `test_radec_entry.py`: tests expect an older constructor/API shape
- `test_ui_modules.py`: sweeps `key_*` methods without arguments, conflicting
  with methods such as `key_number_press(number)`

Use the minimum verification list above first, then triage full-suite failures
by the first traceback.

## When To Update This Document

Update this document when:

- upstream significantly changes `main.py`, `server.py`, `sys_utils.py`,
  `ui/menu_structure.py`, or `pos_server.py`
- any Rev-4 battery/sound/power feature is partially applied
- INDI generic and OnStepX-specific paths are refactored
- SkySafari Align/GoTo/Guide policy changes
- chronyd/time-sync policy changes
- AP+STA network services or policies change
