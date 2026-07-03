# MF_PiFinder Feature Review and Test Checklist

Created: 2026-07-03

This document lists functional changes and additions in the current
`mf_pifinder` branch compared with `brickbots/PiFinder` `main`.  It is intended
as a review and test checklist.

Baseline:

- Upstream: `upstream/main` (`https://github.com/brickbots/PiFinder/tree/main`)
- Current source: `mf_pifinder`
- Comparison date: 2026-07-03
- Commands used:
  - `git fetch upstream main`
  - `git rev-list --left-right --count upstream/main...HEAD`
  - `git diff --stat upstream/main...HEAD`
  - `git diff --name-status upstream/main...HEAD`

Summary:

- Major upstream patch not fully applied:
  - Full Rev-4 battery/sound/power hardware enablement
- Major MF additions or changed areas:
  - Bookworm/RPi4/RPi5/CM5 install and board profiles
  - AP+STA Wi-Fi
  - Bluetooth/USB HID keyboard
  - Red Night/PWA Web UI
  - Locations catalog
  - chronyd-based time management
  - INDI/OnStepX/SkySafari mount integration
  - LCD INDI UI
  - IMU compass/calibration
  - SSD1333 display auto-detection
  - Korean UI localization
  - camera focus/gain/preview improvements

Related documents:

- `docs/mf_upstream_patch_reference_en.md`: upstream sync and patch reference
- `docs/mf_change_history_en.md`: full change history
- `docs/mf_pifinder_rpi4_pi5_compatibility_en.md`: Pi4/Pi5/CM5 Bookworm compatibility
- `docs/mf_indi_mount_install_en.md`: INDI install/operation
- `docs/mf_wifi_apsta_en.md`: AP+STA Wi-Fi
- `docs/mf_time_sync_en.md`: time sync
- `docs/mf_keyboard_mapping_en.md`: keyboard mappings

## Test Priority

| Priority | Meaning |
| --- | --- |
| P0 | Directly affects boot/install/core observing. Must test |
| P1 | Major feature. Test on real hardware or realistic network conditions |
| P2 | Supporting feature/docs/developer convenience. Regression check |

## 1. Platform / Bookworm / Raspberry Pi 4, 5, CM5 Compatibility

Priority: P0

Main changes:

- Raspberry Pi OS Bookworm 64-bit install support
- Prefer `/boot/firmware/config.txt`, fallback to legacy `/boot/config.txt`
- Render `PiFinder_data`, systemd, and Samba paths for the current OS user
- GPS UART board profiles for Pi4/Pi5/CM5
- Use `uart2-pi5` on Pi5/CM5 to avoid OLED CS conflicts
- Support both `/dev/spidev0.0` and `/dev/spidev10.0`
- SSD1333 display auto-detection

Key files:

- `pifinder_paths.sh`
- `pifinder_setup.sh`
- `pifinder_update.sh`
- `pifinder_post_update.sh`
- `python/PiFinder/board_config.py`
- `python/PiFinder/boot_config.py`
- `python/PiFinder/hardware_detect.py`
- `python/PiFinder/displays.py`
- `python/PiFinder/main.py`
- `python/PiFinder/splash.py`
- `python/PiFinder/sys_utils.py`
- `pi_config_files/*.service`

Review points:

- [ ] Fresh OS install completes with `pifinder_setup.sh` as a normal user
- [ ] Pi4 `gps_port=auto` resolves to `/dev/ttyAMA3`
- [ ] Pi5/CM5 `gps_port=auto` resolves to `/dev/ttyAMA2`
- [ ] Boot config changes go to the active boot config path
- [ ] Pi5/CM5 do not hit `uart3` vs OLED CE0/GPIO8 conflicts
- [ ] Boards with only `spidev0.0` or only `spidev10.0` both work
- [ ] SSD1333 marker detection falls back to SSD1351 when unavailable
- [ ] Splash and main UI use the same display selection

Test items:

- [ ] Pi4 Bookworm 64-bit fresh install
- [ ] Pi5 or CM5 Bookworm 64-bit fresh install
- [ ] `systemctl status pifinder cedar_detect pifinder_splash`
- [ ] `ls /dev/spidev* /dev/ttyAMA*`
- [ ] Web UI access
- [ ] LCD/OLED splash display
- [ ] LCD/OLED main UI
- [ ] GPS port auto-selection
- [ ] Camera preview

## 2. Camera Preview / Focus / Gain

Priority: P1

Main changes:

- Focus preview improvements
- Bright-background threshold adjustment
- Camera gain profile/runtime selection
- LCD camera preview debug script

Key files:

- `python/PiFinder/camera_interface.py`
- `python/PiFinder/ui/preview.py`
- `python/PiFinder/ui/callbacks.py`
- `python/PiFinder/ui/menu_structure.py`
- `scripts/camera_lcd_preview.py`

Review points:

- [ ] Existing focus workflow still works
- [ ] Camera gain can be returned to profile default
- [ ] Runtime gain matches camera metadata
- [ ] Pi4 and Pi5/CM5 camera overlay differences do not break startup

Test items:

- [ ] Switch low/high gain
- [ ] Select profile gain
- [ ] Verify focus preview against a star or bright point
- [ ] Run `scripts/camera_lcd_preview.py`
- [ ] Verify IMX462 camera operation

## 3. Korean UI Localization

Priority: P1

Main changes:

- Korean locale
- `ko` language menu entry
- CJK font handling
- Restart notice after language changes

Key files:

- `python/locale/ko/LC_MESSAGES/messages.po`
- `python/locale/ko/LC_MESSAGES/messages.mo`
- `python/PiFinder/ui/fonts.py`
- `python/PiFinder/ui/menu_structure.py`

Review points:

- [ ] Korean is selectable in the language menu
- [ ] Korean text renders correctly on LCD
- [ ] Korean text renders correctly in Web UI
- [ ] Korean `.po` does not drift after upstream i18n changes

Test items:

- [ ] Change language to Korean
- [ ] Restart and inspect LCD menu
- [ ] Inspect Web UI navigation/title/buttons
- [ ] Inspect log/error messages

## 4. Bluetooth / USB HID Keyboard

Priority: P0

Main changes:

- libinput-based HID keyboard event handling
- Bluetooth keyboard scan/pair/connect UI
- USB keyboard input support
- Additional text-entry keycodes
- INDI Guide page-only `qwe/asd/zxc` direction mapping
- Guide motion release/fail-safe stop handling

Key files:

- `python/PiFinder/keyboard_interface.py`
- `python/PiFinder/keyboard_pi.py`
- `python/PiFinder/ui/bluetooth_keyboard.py`
- `python/PiFinder/ui/textentry.py`
- `python/PiFinder/ui/indi.py`
- `python/PiFinder/ui/menu_structure.py`

Review points:

- [ ] Bluetooth keyboard appears as `/dev/input/event*`
- [ ] Both key press and release events arrive
- [ ] Normal menu input does not conflict with INDI Guide-only mapping
- [ ] Mount motion stops after freezes/SSH latency/missed key release
- [ ] Bluetooth keyboard reconnects after AP+STA/Wi-Fi changes

Test items:

- [ ] Pair Bluetooth keyboard
- [ ] Reconnect after reboot
- [ ] Connect USB keyboard
- [ ] LCD menu navigation
- [ ] Text entry
- [ ] INDI Guide direction press/release
- [ ] Disconnect Bluetooth during Guide motion
- [ ] Guide motion timeout stop

## 5. Web UI Red Night Theme / PWA

Priority: P1

Main changes:

- Red Night theme
- Per-browser theme storage
- PWA manifest/service worker/icons
- Android PWA fullscreen/theme-color support
- Theme selector integrated into navigation
- Locations/select/form/tooltip color fixes

Key files:

- `python/views/base.html`
- `python/views/css/style.css`
- `python/views/js/init.js`
- `python/views/manifest.webmanifest`
- `python/views/service-worker.js`
- `python/views/images/pwa-icon-192.png`
- `python/views/images/pwa-icon-512.png`
- `python/views/locations.html`
- `python/views/location_form.html`

Review points:

- [ ] Red Night theme does not leak bright/white controls
- [ ] Log content keeps semantic colors
- [ ] Installed PWA enters fullscreen
- [ ] Android navigation/status bar follows theme color
- [ ] PWA/fullscreen state is not unnecessarily lost during navigation
- [ ] Theme selector appears only in navigation

Test items:

- [ ] Change theme in desktop Chrome
- [ ] Change theme in Android Chrome
- [ ] Install Android PWA
- [ ] Navigate inside PWA fullscreen
- [ ] Check Logs page colors
- [ ] Check Locations add/edit form
- [ ] Check tooltip/action button colors

## 6. Wi-Fi AP / STA / AP+STA

Priority: P0

Main changes:

- STA/AP/AP+STA modes
- `uap0` virtual AP interface
- AP channel restart based on STA channel
- Configurable AP IP
- AP WPA2 security/password
- AP+STA internet sharing option, default OFF
- Initial OS Wi-Fi profile import
- Scan SSIDs when adding STA profiles
- STA band preference
- AP connected-device list

Key files:

- `scripts/pifinder_apsta.sh`
- `scripts/import_initial_wifi_networks.py`
- `python/PiFinder/sys_utils.py`
- `python/PiFinder/server.py`
- `python/views/network.html`
- `pi_config_files/pifinder_apsta_prepare.service`
- `pi_config_files/pifinder_apsta_monitor.service`
- `pi_config_files/dhcpcd.conf.apsta`
- `switch-apsta.sh`

Review points:

- [ ] STA only works
- [ ] AP only works
- [ ] AP+STA works
- [ ] AP clients can access PiFinder Web UI
- [ ] AP+STA internet sharing ON/OFF works
- [ ] AP channel follows STA channel changes
- [ ] DHCP/dnsmasq works after AP IP changes
- [ ] STA band preference matches NetworkManager profile
- [ ] STA-side Web access failures can be distinguished from router client isolation

Test items:

- [ ] Access `10.10.10.1` in AP mode
- [ ] STA internet in AP+STA mode
- [ ] AP client internet sharing ON
- [ ] AP client internet sharing OFF
- [ ] OnStep device on AP
- [ ] AP connected-device list
- [ ] Scan/add STA SSID
- [ ] Import existing OS Wi-Fi profiles
- [ ] Change 2.4G/5G preference
- [ ] Test STA router client-isolation environment

## 7. Locations Catalog

Priority: P1

Main changes:

- Offline location catalog
- Country/state/district/city lookup
- Coordinate/altitude/source auto-fill
- Detailed South Korea administrative data
- North Korea excluded
- Manual loaded locations usable indoors without GPS lock

Key files:

- `python/PiFinder/location_catalog.py`
- `python/PiFinder/data/location_catalog.json`
- `scripts/build_location_catalog.py`
- `python/views/locations.html`
- `python/views/location_form.html`
- `python/PiFinder/server.py`

Review points:

- [ ] Select lists filter correctly step by step
- [ ] South Korea has sufficient detail
- [ ] Location name auto-fill/update feels correct
- [ ] Save Location works
- [ ] Default location selection works
- [ ] Manual location updates PiFinder/INDI while GPS is unlocked
- [ ] Red Night theme colors remain dark red

Test items:

- [ ] Save Seoul/Songpa/Pungnap-dong
- [ ] Save another Korean location
- [ ] Save a major international city
- [ ] Change default location
- [ ] Reload location
- [ ] Confirm INDI page PiFinder Location update
- [ ] Confirm OnStep Send Location and Time

## 8. Integrated Time Sync / chronyd

Priority: P0

Main changes:

- Integrated GPS/NTP/RTC/software PPS time management
- chronyd-centered policy
- Privileged helper service split
- GPS/NTP/RTC status UI
- Custom NTP server setting
- Set Time/Date self-gates without location lock
- UTC-aware PiFinder datetime handling

Key files:

- `python/PiFinder/gps_time_sync.py`
- `python/PiFinder/gps_time_sync_helper.py`
- `python/PiFinder/ui/gps_time_sync_status.py`
- `python/PiFinder/timez.py`
- `python/PiFinder/state.py`
- `python/PiFinder/ui/timeentry.py`
- `python/PiFinder/ui/dateentry.py`
- `scripts/install_chrony_time_sync.sh`
- `scripts/install_gps_time_sync_helper.sh`
- `pi_config_files/pifinder_gps_time_sync.service`

Review points:

- [ ] chronyd is the primary clock manager
- [ ] Weak/unlocked GPS degrades gracefully
- [ ] NTP unavailable state times out cleanly
- [ ] Custom NTP server is stored/applied
- [ ] Pi5 RTC path does not cause issues
- [ ] Set Time/Date does nothing without location lock
- [ ] INDI/OnStep uses current PiFinder UTC time

Test items:

- [ ] Indoor GPS unlock
- [ ] Outdoor GPS lock
- [ ] NTP available
- [ ] NTP unavailable
- [ ] Enter custom NTP server
- [ ] Check `chronyc sources/tracking`
- [ ] LCD Time Sync status
- [ ] Web status/API
- [ ] OnStep Web UI time after sync

## 9. INDI Mount / OnStepX

Priority: P0

Main changes:

- Optional INDI mount process
- INDI install scripts
- INDI archive package/install scripts
- OnStepX custom INDI driver patch flow
- INDI Web UI page/menu
- LX200 OnStep/OnStepX network/serial setup UI
- Improved OnStep location/time sync
- INDI restart
- Active driver/profile name-based behavior
- Generic INDI mount path preserved

Key files:

- `python/PiFinder/mountcontrol_indi.py`
- `python/PiFinder/pos_server.py`
- `python/PiFinder/ui/indi.py`
- `python/views/indi_mount.html`
- `python/views/tools.html`
- `scripts/install_indi_mount.sh`
- `scripts/install_indi_mount_OnstepX.sh`
- `scripts/install_indi_mount_archive.sh`
- `scripts/package_indi_mount_archive.sh`
- `scripts/patches/indi-v2.2.3.1-onstepx.patch`

Review points:

- [ ] Base PiFinder install does not require INDI
- [ ] INDI install script works on Pi4 and Pi5
- [ ] OnStepX does not overwrite original LX200 OnStep driver
- [ ] OnStepX-only UI appears only for active OnStepX driver
- [ ] USB serial list appears
- [ ] Network host/port list and manual entry work
- [ ] INDI restart stops/starts server/profile/driver
- [ ] PiFinder core features survive bad mount communications

Test items:

- [ ] Base PiFinder without INDI installed
- [ ] Run `install_indi_mount_OnstepX.sh`
- [ ] Access INDI Web Manager
- [ ] Start/connect OnStepX profile
- [ ] Configure LX200 OnStepX Network TCP
- [ ] Configure LX200 OnStepX USB serial
- [ ] Restart INDI
- [ ] PiFinder UI while INDI server is stopped
- [ ] PiFinder while OnStep device is offline

## 10. LCD INDI UI

Priority: P0

Main changes:

- INDI entry at bottom of LCD Start menu
- INIT / STATUS / GUIDE pages
- INIT actions: connect/init, send location/time, park/unpark, set home, return home, set-park, restart
- STATUS periodic update
- GUIDE keypad overlay
- `789 / 4 6 / 123` direction layout
- press-to-move, release-to-stop
- `qwe/asd/zxc` mapping only inside Guide page
- Top-bar `I` indicator

Key files:

- `python/PiFinder/ui/indi.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/PiFinder/ui/base.py`
- `python/PiFinder/keyboard_pi.py`

Review points:

- [ ] INDI appears at bottom of Start menu
- [ ] INIT menu fits the screen
- [ ] Restart action appears in INIT
- [ ] STATUS updates periodically
- [ ] Guide numeric layout matches screen position
- [ ] Key 5 is not used for guide motion
- [ ] Timeout stop works on missed key release
- [ ] Bluetooth keyboard starts and stops motion
- [ ] Top-bar `I` reflects connection state

Test items:

- [ ] LCD INIT connect/init
- [ ] LCD send location/time
- [ ] LCD park/unpark
- [ ] LCD set home/return home
- [ ] LCD restart INDI
- [ ] LCD Guide 8-direction motion
- [ ] LCD Guide release stop
- [ ] Bluetooth keyboard Guide motion
- [ ] Web UI stop recovery

## 11. SkySafari / LX200 / Mount Mode Integration

Priority: P0

Main changes:

- Improved SkySafari LX200 `:Sr/:Sd/:MS#/:CM#` handling
- IMU fallback pointing before solving
- Optional SkySafari GoTo forwarding to INDI
- SkySafari Guide bridge to INDI guide motion
- SkySafari Align/Sync handling for PiFinder/IMU/INDI
- Mount-mode compatibility audit
- GoTo completion/moving-state handling
- Alt/Az/EQ-aware separation

Key files:

- `python/PiFinder/pos_server.py`
- `python/PiFinder/mountcontrol_indi.py`
- `python/PiFinder/imu_pi.py`
- `python/PiFinder/imu_calibration.py`
- `docs/mf_mount_mode_compatibility_en.md`

Review points:

- [ ] `:Sr/:Sd` only stores target coordinates
- [ ] `:MS#` handles GoTo
- [ ] `:CM#` handles Sync/Align
- [ ] `:CM#` prefers the latest parsed `Sr/Sd` target
- [ ] GoTo forwarding ON can also forward Align/Sync
- [ ] IMU correction applies before solving
- [ ] IMU correction resets after a solve
- [ ] Alt/Az and EQ modes do not show wrong horizon/coordinate state
- [ ] SkySafari GoTo completes correctly
- [ ] Targets are not incorrectly rejected as below horizon

Test items:

- [ ] SkySafari Push-To mode
- [ ] SkySafari GoTo mode
- [ ] SkySafari guide buttons
- [ ] SkySafari Align
- [ ] IMU fallback before solving
- [ ] Normal pointing after solving
- [ ] INDI GoTo forwarding OFF
- [ ] INDI GoTo forwarding ON
- [ ] Alt/Az mount
- [ ] EQ mount

## 12. IMU Compass / Calibration

Priority: P1

Main changes:

- Optional BNO055 magnetometer/compass fusion
- Existing IMU sensitivity setting retained
- Auto calibration save/load
- Manual calibration save/load/clear
- Compass/calibration UI menu

Key files:

- `python/PiFinder/imu_pi.py`
- `python/PiFinder/imu_calibration.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/PiFinder/ui/callbacks.py`
- `docs/mf_imu_compass_calibration_en.md`

Review points:

- [ ] Default OFF keeps existing IMU behavior stable
- [ ] Compass ON improves heading when calibrated
- [ ] Indoor magnetic interference is manageable
- [ ] Calibration status matches BNO055 status
- [ ] Auto save/load survives reboot
- [ ] Manual save/load/clear works
- [ ] Does not conflict with correction reset after solving

Test items:

- [ ] Compass OFF
- [ ] Compass ON
- [ ] Calibration auto save
- [ ] Calibration load after reboot
- [ ] Manual save/load/clear
- [ ] SkySafari no-solve pointing
- [ ] Reset correction after plate solve

## 13. Observing List CSV Import

Priority: P2

Main changes:

- Upstream CSV import improvements
- Lenient headers
- Multiple coordinate formats
- Docs examples
- Object type drift guard integration

Key files:

- `python/PiFinder/obslist.py`
- `python/PiFinder/obslist_formats.py`
- `docs/ax/catalog/obslist-formats/README.md`
- `docs/ax/catalog/obslist-formats/examples/*`
- `python/tests/test_obslist_formats.py`
- `python/tests/test_obslist_resolve.py`

Review points:

- [ ] Existing `.pifinder` list import still works
- [ ] Third-party CSV import works
- [ ] RA hour/degree/sexagesimal/colon formats are parsed
- [ ] Object type filter and OBJ_TYPES agree

Test items:

- [ ] Import example CSV
- [ ] Bad header handling
- [ ] Mixed coordinate formats
- [ ] Object type filtering

## 14. OBJ_TYPES Single Source

Priority: P2

Main changes:

- Object type code set centralized in `OBJ_TYPES`
- Type filter menu generated from `OBJ_TYPES.items()`
- Docs/default_config drift guard test

Key files:

- `python/PiFinder/obj_types.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/tests/test_obj_types_docs.py`
- `default_config.json`

Review points:

- [ ] Type filter menu order is acceptable
- [ ] Labels fit LCD width
- [ ] Korean translations are natural
- [ ] `default_config.json` `filter.object_types` includes every type

Test items:

- [ ] Display Type filter menu
- [ ] Select/deselect Type filters
- [ ] Catalog filtering
- [ ] `test_obj_types_docs.py`

## 15. Documentation / Test / CI / Assets

Priority: P2

Main changes:

- MF docs
- Upstream patch reference docs
- Feature-specific install/test docs
- NixOS PR build CI
- case/accessory assets
- Additional tests

Key files:

- `docs/mf_*.md`
- `.github/workflows/nixos-pr-build.yml`
- `.github/scripts/*`
- `case/accessories/*`
- `python/tests/test_*.py`

Review points:

- [ ] Document names/language pairs are consistent
- [ ] Korean-only docs have English counterparts where appropriate
- [ ] Setup/install docs match current script names
- [ ] GitHub Actions behaves as intended in the fork
- [ ] Asset changes do not create unnecessary PR noise

Test items:

- [ ] Check doc links
- [ ] Check install script names
- [ ] Check CI workflow syntax
- [ ] Check docs/source menu map

## 16. Upstream Rev-4 Hardware Patch: Not Applied / Partially Applied

Priority: review only

Current status:

- Only SSD1333 display auto-detection is partially applied in MF style
- battery/sound/power/latch are not applied

Not applied:

- BQ25895 battery telemetry
- BQ25895 fast-charge configuration writes
- sound/earcon buzzer subsystem
- GPIO15 hardware power button
- GPIO14 gpio-poweroff latch
- battery titlebar icon
- Raspberry Pi red power LED control

Review points:

- [ ] Confirm whether Rev-4 hardware is an actual target
- [ ] Decide whether GPIO14 poweroff latch should apply only to explicitly marked boards
- [ ] Decide sound/earcon default OFF policy
- [ ] Decide whether charger writes should be separate from read-only telemetry
- [ ] Preserve current `hardware_detect.py` fallback if adding `HardwareCapabilities`

## Minimum Regression Commands

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

## Real-Hardware Integration Test Order

Recommended order:

1. PiFinder service boot
2. Web UI access
3. LCD/OLED UI
4. Camera preview/focus
5. GPS unlocked state
6. Load saved location
7. Time sync status
8. AP+STA networking
9. Bluetooth keyboard
10. INDI server/profile/driver start
11. OnStepX connection
12. Send Location and Time
13. Web INDI guide motion
14. LCD INDI guide motion
15. SkySafari Push-To
16. SkySafari GoTo forwarding OFF
17. SkySafari GoTo forwarding ON
18. SkySafari Align/Sync
19. Correction reset after plate solving
20. Reboot persistence

## Result Recording Template

```text
Date:
Device:
OS:
Branch / commit:
Network mode:
Mount / driver:
GPS state:

Feature:
Expected:
Result:
Pass/Fail:
Notes:
Logs/screenshots:
```
