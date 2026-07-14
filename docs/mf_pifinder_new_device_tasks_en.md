# New Device Checklist

Date: 2026-06-26

This document is the install and verification checklist for the
`hjoungjoo/MF_PiFinder` fork's `mf_pifinder` branch on a new Raspberry Pi device.
Raspberry Pi 4, Raspberry Pi 5, and CM5 should be checked against the board
profiles documented in `docs/mf_pifinder_rpi4_pi5_compatibility_en.md`.

For background, read:

```text
docs/mf_pifinder_rpi4_pi5_compatibility_en.md
docs/mf_change_history_en.md
docs/mf_bookworm_install_en.md
```

## Goals

The core goals for a new device are:

1. Confirm the `mf_pifinder` branch installs on the new OS.
2. Confirm the CM5/Bookworm changes do not break Raspberry Pi 4 behavior.
3. Confirm Raspberry Pi 5-class boards use the `pi5_class` UART/GPS/SPI paths.
4. If problems appear, save logs and fix them on the same branch.

## Preparation

Recommended OS:

```text
Raspberry Pi OS Bookworm 64-bit
```

Raspberry Pi Imager settings:

```text
SSH: enable
hostname: choose a name that does not collide with existing devices
username: preferably not pifinder
Wi-Fi: configure if needed
```

Example:

```text
hostname: mf-pi4-test
username: mfpi4
```

Recommended network order:

```text
1. Wired LAN + SSH
2. Direct monitor/keyboard
3. Wi-Fi-only SSH
```

Notes:

- If remote access depends only on Wi-Fi, leave network mode changes and reboot
  until the end.
- Connect hardware in stages instead of attaching the camera, LCD, IMU, and GPS
  all at once.
- Always power off before connecting the camera ribbon cable.

## 1. Initial Device Check

```bash
hostname -I
cat /etc/os-release
uname -a
python3 --version
id
groups
```

Create a log directory:

```bash
mkdir -p ~/mf-pifinder-test-logs
```

Save the initial state:

```bash
{
  date
  hostnamectl
  cat /etc/os-release
  uname -a
  python3 --version
  id
  groups
  nmcli device status 2>/dev/null || true
  ip addr
} | tee ~/mf-pifinder-test-logs/00_initial_state.txt
```

## 2. Clone Source

```bash
sudo apt update
sudo apt install -y git

cd ~
git clone --recursive --branch mf_pifinder https://github.com/hjoungjoo/MF_PiFinder.git PiFinder
cd ~/PiFinder
```

Check the branch and commit:

```bash
git status --short --branch
git log --oneline --decorate -n 5
git submodule status
```

Expected state:

```text
branch: mf_pifinder
remote: hjoungjoo/MF_PiFinder
```

## 3. Read Compatibility And Change Notes

```bash
cd ~/PiFinder
sed -n '1,220p' docs/mf_pifinder_rpi4_pi5_compatibility_en.md
sed -n '1,180p' docs/mf_change_history_en.md
```

When continuing in a new Codex conversation, start with:

```text
I want to test the PiFinder CM5/Bookworm work on a new Raspberry Pi device.
The branch is hjoungjoo/MF_PiFinder mf_pifinder.
Please read docs/mf_pifinder_rpi4_pi5_compatibility_en.md,
docs/mf_pifinder_new_device_tasks_en.md,
and docs/mf_change_history_en.md, then continue.
If install or hardware testing finds problems, I want to fix them on the same branch.
```

## 4. Save Pre-Install State

```bash
cd ~/PiFinder

{
  date
  git status --short --branch
  git rev-parse HEAD
  git remote -v
  ls -l /boot/config.txt /boot/firmware/config.txt 2>/dev/null || true
  grep -n "dtparam\|dtoverlay\|camera_auto_detect" /boot/config.txt /boot/firmware/config.txt 2>/dev/null || true
} | tee ~/mf-pifinder-test-logs/01_before_install.txt
```

## 5. Run Installer

Important:

```text
Do not run sudo ./pifinder_setup.sh
```

Run the installer as the normal OS user:

```bash
cd ~/PiFinder
./pifinder_setup.sh 2>&1 | tee ~/mf-pifinder-test-logs/02_pifinder_setup.log
```

Watch for:

- `apt-get install` failures
- successful `dhcpcd` package install
- gpsd configuration prompts or hangs
- `pip install --break-system-packages` failures
- `hip_main.dat` download success
- service/Samba template rendering success
- user group update success

If installation fails, save this summary and stop:

```bash
{
  date
  git -C ~/PiFinder status --short --branch
  tail -n 120 ~/mf-pifinder-test-logs/02_pifinder_setup.log
  systemctl status pifinder cedar_detect pifinder_splash --no-pager 2>/dev/null || true
} | tee ~/mf-pifinder-test-logs/02_install_failed_summary.txt
```

## 6. Pre-Reboot Check

If installation completed, save state before rebooting:

```bash
{
  date
  git -C ~/PiFinder status --short --branch
  groups
  ls -l /boot/config.txt /boot/firmware/config.txt 2>/dev/null || true
  grep -n "dtparam\|dtoverlay\|camera_auto_detect" /boot/config.txt /boot/firmware/config.txt 2>/dev/null || true
  systemctl is-enabled pifinder cedar_detect pifinder_splash 2>/dev/null || true
  systemctl status pifinder cedar_detect pifinder_splash --no-pager 2>/dev/null || true
} | tee ~/mf-pifinder-test-logs/03_before_reboot.txt
```

If remote access is Wi-Fi-only, confirm the reconnect address first:

```bash
hostname -I
```

## 7. First Reboot

```bash
sudo reboot
```

After reconnecting:

```bash
mkdir -p ~/mf-pifinder-test-logs

{
  date
  hostname -I
  groups
  systemctl status pifinder cedar_detect pifinder_splash --no-pager
} | tee ~/mf-pifinder-test-logs/04_after_reboot_services.txt

journalctl -u pifinder -b --no-pager > ~/mf-pifinder-test-logs/04_pifinder_after_reboot.log
journalctl -u cedar_detect -b --no-pager > ~/mf-pifinder-test-logs/04_cedar_detect_after_reboot.log
```

## 8. Hardware Connection Order

Connect and verify hardware in stages:

```text
1. LCD / keypad / IMU
2. Camera
3. GPS
4. USB keyboard
5. Bluetooth keyboard
```

Check logs after each stage:

```bash
journalctl -u pifinder -b -n 200 --no-pager
```

## 9. LCD / Keypad / IMU

After connecting, inspect device nodes:

```bash
ls -l /dev/i2c-* /dev/spidev* 2>/dev/null
i2cdetect -y 1
```

Check:

- On Pi4, `/dev/spidev0.0` appears.
- OLED/LCD turns on.
- Keypad input responds.
- IMU appears on I2C.
- PiFinder service logs have no display or IMU errors.

Save logs:

```bash
{
  date
  ls -l /dev/i2c-* /dev/spidev* 2>/dev/null || true
  i2cdetect -y 1 || true
} | tee ~/mf-pifinder-test-logs/05_lcd_keypad_imu.txt
```

## 10. Camera

Power off before connecting the camera ribbon.

Check cameras:

```bash
rpicam-hello --list-cameras
```

If the camera type needs switching:

```bash
cd ~/PiFinder
sudo python3 python/PiFinder/switch_camera.py imx477
# or imx296 / imx462
sudo reboot
```

After reboot:

```bash
rpicam-hello --list-cameras
rpicam-still -o ~/mf-pifinder-test-logs/camera-test.jpg --timeout 2000
journalctl -u pifinder -b -n 300 --no-pager | tee ~/mf-pifinder-test-logs/06_camera_journal.txt
```

Check:

- Camera is detected on Pi4.
- `switch_camera.py` edits the correct boot config file.
- Pi4 camera ports do not get an unnecessary `cam0` parameter.
- Focus screen is not just black.
- Exposure/gain menus work.

## 11. GPS

After connecting GPS:

```bash
ls -l /dev/serial* /dev/ttyAMA* /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
systemctl status gpsd gpsd.socket --no-pager
cgps -s
```

Check in PiFinder:

```text
Settings > Advanced > GPS Settings > GPS Port
```

Check:

- The actual GPS port appears in the menu.
- Changing the port updates `/etc/default/gpsd`.
- gpsd restarts or reconnects.
- GPS lock state is reflected in the PiFinder UI.

Pi4 + `uart3` overlay:

- Internal UART GPS should appear as `/dev/ttyAMA3`.
- `GPS Port` default `auto` resolves to `/dev/ttyAMA3` on Pi4.
- If the u-blox receiver is configured for 115200bps, set `GPS Baud Rate` to
  `115200` and confirm gpsd reports `driver:"u-blox"`.
- If the receiver is detected but `TPV mode=1`, `nSat=0/uSat=0`, treat it as an
  antenna/sky-view/cold-start issue rather than a serial communication problem.

Save logs:

```bash
{
  date
  ls -l /dev/serial* /dev/ttyAMA* /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || true
  systemctl status gpsd gpsd.socket --no-pager || true
} | tee ~/mf-pifinder-test-logs/07_gps.txt
```

## 12. USB / Bluetooth Keyboard

USB keyboard:

```bash
ls -l /dev/input/by-id /dev/input/by-path 2>/dev/null
```

Bluetooth state:

```bash
bluetoothctl show
bluetoothctl devices
bluetoothctl devices Paired
bluetoothctl info <MAC>
ls -l /dev/input /dev/input/by-id /dev/input/by-path 2>/dev/null
journalctl -u bluetooth -b -n 120 --no-pager
```

PiFinder menu:

```text
Settings > Advanced > Keyboard
```

Check:

- Bluetooth scan shows device names.
- Devices that only show a MAC address are still selectable.
- Pair+Connect succeeds.
- Auto reconnect works after restart.
- Alphabet keys enter real alphabet characters.
- Space is handled as space.
- Real long presses produce long-key actions.
- USB keyboard and GPIO keypad do not interfere with each other.
- If the keyboard says connected but no keys work, first check whether a new
  `/dev/input/event*` node exists.
- If no keyboard event node exists and `bluetoothd` shows HID Information or
  Report Reference read failures, classify it as a BlueZ/HID issue before
  PiFinder input mapping.
- On Pi4 Bookworm, `K06 BLE Keyboard` created an event device after enabling
  `UserspaceHID=true` and `LEAutoSecurity=true` in `/etc/bluetooth/input.conf`.
  On existing installs, restart with `sudo systemctl restart bluetooth` and
  `sudo systemctl restart pifinder` after changing that file.
- Use `libinput debug-events --device /dev/input/eventX` to confirm real key
  events.

## 13. Korean UI

Select Korean in PiFinder:

```text
Settings > User Pref... > Language > Korean
```

Check:

- Korean menus appear after restart.
- Korean glyphs are not broken.
- Astronomy terms are not awkward.
- Untranslated strings fall back naturally to English.

## 14. Data To Send Codex When Problems Occur

If a problem appears, run:

```bash
mkdir -p ~/mf-pifinder-test-logs

{
  date
  hostnamectl
  cat /etc/os-release
  uname -a
  python3 --version
  id
  groups
  git -C ~/PiFinder status --short --branch
  git -C ~/PiFinder log --oneline --decorate -n 5
  git -C ~/PiFinder rev-parse HEAD
  ls -l /boot/config.txt /boot/firmware/config.txt 2>/dev/null || true
  grep -n "dtparam\|dtoverlay\|camera_auto_detect" /boot/config.txt /boot/firmware/config.txt 2>/dev/null || true
  ls -l /dev/i2c-* /dev/spidev* /dev/serial* /dev/ttyAMA* /dev/ttyUSB* /dev/ttyACM* /dev/video* /dev/media* /dev/input/event* 2>/dev/null || true
  systemctl status pifinder cedar_detect pifinder_splash gpsd gpsd.socket --no-pager || true
} | tee ~/mf-pifinder-test-logs/problem-summary.txt

journalctl -u pifinder -b --no-pager > ~/mf-pifinder-test-logs/problem-pifinder.log
journalctl -u cedar_detect -b --no-pager > ~/mf-pifinder-test-logs/problem-cedar-detect.log
dmesg > ~/mf-pifinder-test-logs/problem-dmesg.log
```

Start the Codex handoff like this:

```text
I hit a problem while testing the mf_pifinder branch on a new Pi4 device.
I followed docs/mf_pifinder_new_device_tasks_en.md.
The problem is <short description>.
Please inspect the logs below and fix it.
```

## 15. Push Fixes To GitHub

After fixing a problem:

```bash
cd ~/PiFinder
git status --short
git add -A
git commit -m "Fix Pi4 <problem summary>"
git push
```

Examples:

```bash
git commit -m "Fix Pi4 SPI display detection"
git commit -m "Fix setup package install on Bookworm"
git commit -m "Update Pi4 install checklist"
```

If a Draft PR already exists for `mf_pifinder`, pushing updates the PR
automatically.

## 16. PR Preparation

When opening a pull request on GitHub:

```text
base repository: brickbots/PiFinder
base branch: main
head repository: hjoungjoo/MF_PiFinder
compare branch: mf_pifinder
```

Open it as a Draft PR first.

After Pi4 testing:

- Summarize remaining issues.
- Ask maintainers whether the large changes should be split into feature PRs.
- Before requesting review, add the latest branch log and test results to the PR
  body.

## 17. Forbidden Or Risky Actions

- Do not test network switching blindly when remote access is Wi-Fi-only.
- Do not run `sudo ./pifinder_setup.sh`.
- Do not replace `/usr/bin/python3` with another version.
- Do not push directly to upstream `brickbots/PiFinder` `release` or `main`.
- Do not paste authentication tokens or GitHub tokens into chat.
- Do not run `git reset --hard` or `git checkout -- <file>` before reviewing
  local changes.
