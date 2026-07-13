# Report: BNO055 I2C clock-stretching fix (model-aware I2C bus selection)

- Date: 2026-07-13
- Branch: `mf_pifinder` (fork `hjoungjoo/MF_PiFinder`)
- Test hardware: Raspberry Pi 4 Model B Rev 1.4, BNO055 IMU on GPIO2/GPIO3
- Status: implemented and verified on Pi 4; Pi 5 path implemented (same code path as
  upstream, at 400 kbps), not yet tested on Pi 5 hardware

## 1. Summary

PiFinder exhibited intermittent whole-system freezes: every process (UI, solver,
SkySafari server, web UI) would stop responding for 4–5 seconds at random moments,
then resume. The root cause chain is:

1. The BCM2835/BCM2711 I2C controller (Pi 1–4) has a well-known silicon bug in its
   clock-stretching support. The BNO055 IMU stretches the SCL clock routinely, so
   transfers can be corrupted at normal bus speeds.
2. Upstream PiFinder works around this by slowing the hardware bus to 10 kHz
   (`dtparam=i2c_arm_baudrate=10000`). This reduces the corruption probability but
   makes every IMU transaction ~40x slower, so the IMU process spends most of its
   life inside the kernel I2C transfer (uninterruptible `D` state,
   `wchan=bcm2835_i2c_xfer`).
3. PiFinder shares state between processes through a single
   `multiprocessing.BaseManager` server process. Under CPU pressure (thermal
   throttling was also observed: `vcgencmd get_throttled` = `0x80000`), the
   slow-I2C-bound IMU process plus the serialized manager form a convoy: when the
   manager blocks behind a slow client, **every** PiFinder process that touches
   shared state stalls together. That is the visible 4–5 s freeze.

The fix selects the I2C transport per board model:

- **Pi 5 / CM5** (RP1 I2C controller, no clock-stretching bug): hardware I2C at
  **400 kbps**.
- **Pi 4 and earlier** (BCM2835-family controller): a **software (bit-banged)
  `i2c-gpio` bus** on the same physical pins (GPIO2/GPIO3, exposed as
  `/dev/i2c-3`), which implements clock stretching correctly. The buggy hardware
  block (`i2c_arm`) is turned off so it cannot claim the pins.

After the change on the Pi 4, the IMU process no longer camps in `D` state
(previously ~59% of samples during freezes, now effectively 0), BNO055 reads are
clean (unit quaternions, no I/O errors), and the multi-process freeze signature
(several PiFinder processes simultaneously in `D`) is gone.

## 2. Symptom and diagnosis

- Symptom: at random times — most easily reproduced by rapidly switching menus —
  the entire system (all PiFinder processes at once) froze for 4–5 s.
- A "freeze catcher" script sampling `/proc/<pid>/stat` caught a 2.3 s stall with
  multiple PiFinder processes in `D` state simultaneously.
- The IMU process was in `D` state in ~59% of samples with
  `wchan = bcm2835_i2c_xfer` — i.e. blocked inside the hardware I2C transfer.
- Contributing pressure: CPU load ~5.4 on 4 cores and `get_throttled = 0x80000`
  (soft temperature limit had occurred; 76.9 °C observed) — partly caused by dev
  tooling on the test unit, but the freeze also reproduces on a pure PiFinder
  system.
- Because all inter-process state flows through one GIL-bound
  `StateManager`/`BaseManager` process, one stuck client serializes everyone —
  matching the observed "everything freezes together" behaviour.

## 3. Background: the BCM2835 I2C clock-stretching bug

The BCM2835 (and its descendants up to BCM2711 in the Pi 4) I2C block samples SCL
at a fixed point and does not guarantee the minimum SCL high time when a slave
performs clock stretching. When a slave (like the BNO055, which stretches on
almost every read) releases the clock at an unfortunate moment, the controller can
emit an extremely short (~40 ns) SCL pulse; the slave then delivers a corrupted
byte. References:

- https://www.advamation.com/knowhow/raspberrypi/rpi-i2c-bug.html
- https://github.com/raspberrypi/linux/issues/254
- https://github.com/raspberrypi/linux/issues/4884

Known workarounds: (a) slow the bus so stretching (almost) never happens — the
current upstream approach, with the latency cost described above; (b) use the
BNO055 in UART mode; (c) use a software `i2c-gpio` bus, which implements clock
stretching per spec. The Pi 5's RP1 I2C controller does not have this bug, so no
workaround is needed there.

Option (c) was chosen because it needs no hardware changes, keeps the same wiring
and device addresses, and removes both failure modes at once (corruption *and* the
10 kHz latency).

## 4. Changes

Five pieces, all model-aware at runtime/install time — no configuration option is
required, the right path is chosen automatically.

### 4.1 New module: `python/PiFinder/i2c_bus.py`

Single place that decides which bus to hand out. Full source:

```python
#!/usr/bin/python
# -*- coding:utf-8 -*-
"""Model-aware I2C bus factory.

Raspberry Pi 5 (and Compute Module 5) drive I2C through the RP1 controller,
which honours clock stretching correctly, so those boards use the hardware
I2C bus at full speed.

Raspberry Pi 4 and earlier use the BCM2835/BCM2711 I2C block, which has a
well-documented clock-stretching bug: when a slave stretches the clock the
controller can emit a too-short SCL pulse and corrupt the transfer.  The
BNO055 IMU stretches the clock routinely, so on those boards PiFinder uses a
software (bit-banged) i2c-gpio bus on ``/dev/i2c-3`` instead, which respects
clock stretching.  ``pifinder_setup.sh`` provisions that overlay at install
time; this module simply selects the matching bus at runtime.
"""

import logging
from typing import Optional

logger = logging.getLogger("I2C")

# Bus number provisioned by the i2c-gpio overlay in pifinder_setup.sh.
SOFTWARE_I2C_BUS = 3


def _board_model() -> str:
    """Return the device-tree model string, or "" when unavailable."""
    try:
        with open("/proc/device-tree/model", "rb") as handle:
            return handle.read().decode("utf-8", "replace").rstrip("\x00").strip()
    except OSError:
        return ""


def uses_hardware_i2c(model: Optional[str] = None) -> bool:
    """Return True when this board should use the hardware I2C bus (Pi 5)."""
    if model is None:
        model = _board_model()
    return "Raspberry Pi 5" in model or "Compute Module 5" in model


def get_i2c():
    """Return an I2C bus object appropriate for this board.

    Pi 5 uses the hardware bus via ``board.I2C()``; earlier boards use the
    software i2c-gpio bus (``/dev/i2c-3``) via ``adafruit_extended_bus`` to
    work around the BCM2835/BCM2711 clock-stretching bug.
    """
    if uses_hardware_i2c():
        import board

        logger.debug("Using hardware I2C bus (board.I2C)")
        return board.I2C()

    from adafruit_extended_bus import ExtendedI2C

    logger.debug("Using software i2c-gpio bus /dev/i2c-%d", SOFTWARE_I2C_BUS)
    return ExtendedI2C(SOFTWARE_I2C_BUS)
```

### 4.2 `python/PiFinder/imu_pi.py` — use the factory

```diff
@@ -11,7 +11,7 @@ import math
 from PiFinder import config, imu_calibration
 from PiFinder.multiproclogging import MultiprocLogging
 from PiFinder.types.positioning import ImuSample
-import board
+from PiFinder.i2c_bus import get_i2c
 import adafruit_bno055
 import logging
 import quaternion  # Numpy quaternion
@@ -30,7 +30,7 @@ class Imu:

     def __init__(self):
         cfg = config.Config()
-        i2c = board.I2C()
+        i2c = get_i2c()
         self.sensor = adafruit_bno055.BNO055_I2C(i2c)
```

### 4.3 `python/PiFinder/hardware_detect.py` — same factory, still import-safe

This module probes the BQ25895 charger (0x6A) to auto-detect the Rev-4 display.
The charger sits on the same physical pins, so it is reachable on the software
bus as well.

```diff
@@ -11,9 +11,9 @@ older PiFinder hardware keeps the SSD1351 default.
 import logging

 try:
-    import board
+    from PiFinder.i2c_bus import get_i2c
 except Exception:
-    board = None
+    get_i2c = None


 logger = logging.getLogger("HardwareDetect")
@@ -22,11 +22,11 @@ BQ25895_ADDRESS = 0x6A


 def i2c_present(address: int) -> bool:
-    """Return True when an I2C address ACKs on the default board I2C bus."""
-    if board is None:
+    """Return True when an I2C address ACKs on the board I2C bus."""
+    if get_i2c is None:
         raise RuntimeError("blinka / board unavailable; no I2C bus")

-    i2c = board.I2C()
+    i2c = get_i2c()
     locked = False
     try:
         while not i2c.try_lock():
```

### 4.4 `python/requirements.txt` — new dependency

`Adafruit-Blinka`'s `board.I2C()` is hard-wired to the default hardware bus;
`adafruit-extended-bus` provides `ExtendedI2C(n)` to open an arbitrary
`/dev/i2c-n` while staying Blinka-compatible (so `adafruit_bno055` is unchanged).

```diff
 adafruit-blinka==8.12.0
 adafruit-circuitpython-bno055
+adafruit-extended-bus==1.0.2
 cheroot==10.0.0
```

### 4.5 `pifinder_setup.sh` — model-aware boot configuration

The unconditional `i2c_arm=on` + `i2c_arm_baudrate=10000` lines are replaced by a
branch on the existing `pifinder_board_profile` helper (from
`pifinder_paths.sh`). The two paths are kept mutually exclusive by deleting the
other path's lines before appending, so re-running setup after moving an SD card
between board generations converges to the right config.

```diff
@@ -161,13 +161,41 @@ fi
 BOOT_CONFIG="$(pifinder_boot_config_path)"
 for line in \
     "dtparam=spi=on" \
-    "dtparam=i2c_arm=on" \
-    "dtparam=i2c_arm_baudrate=10000" \
     "dtoverlay=pwm,pin=13,func=4" \
     "$(pifinder_uart_overlay)"
 do
     grep -qxF "${line}" "${BOOT_CONFIG}" || echo "${line}" | sudo tee -a "${BOOT_CONFIG}"
 done
+
+# I2C for the BNO055 IMU (and the BQ25895 charger on Rev-4 boards).
+#
+# Pi 5 / CM5 drive I2C through the RP1 controller, which honours clock
+# stretching, so hardware I2C at 400 kbps is safe there.  Pi 4 and earlier use
+# the BCM2835/BCM2711 I2C block, which has a known clock-stretching bug that
+# corrupts transfers with a clock-stretching device like the BNO055.  On those
+# boards, use a software (bit-banged) i2c-gpio bus on the same SDA/SCL pins
+# (GPIO2/GPIO3 -> /dev/i2c-3) instead, and disable the hardware i2c_arm block
+# so it does not fight the software bus for the pins.  Keep the two paths
+# mutually exclusive by removing the other path's lines first.
+if [[ "$(pifinder_board_profile)" == "pi5_class" ]]; then
+    sudo sed -i \
+        -e '/^dtoverlay=i2c-gpio/d' \
+        "${BOOT_CONFIG}"
+    for line in \
+        "dtparam=i2c_arm=on" \
+        "dtparam=i2c_arm_baudrate=400000"
+    do
+        grep -qxF "${line}" "${BOOT_CONFIG}" || echo "${line}" | sudo tee -a "${BOOT_CONFIG}"
+    done
+else
+    sudo sed -i \
+        -e '/^dtparam=i2c_arm=on/d' \
+        -e '/^dtparam=i2c_arm_baudrate=/d' \
+        "${BOOT_CONFIG}"
+    I2C_GPIO_OVERLAY="dtoverlay=i2c-gpio,i2c_gpio_sda=2,i2c_gpio_scl=3,bus=3"
+    grep -qxF "${I2C_GPIO_OVERLAY}" "${BOOT_CONFIG}" \
+        || echo "${I2C_GPIO_OVERLAY}" | sudo tee -a "${BOOT_CONFIG}"
+fi
 if [[ "$(pifinder_uart_overlay)" == "dtoverlay=uart2-pi5" ]]; then
     sudo sed -i 's/^dtoverlay=uart3/#dtoverlay=uart3/' "${BOOT_CONFIG}"
 fi
```

### 4.6 Resulting `/boot/firmware/config.txt` (relevant lines)

Pi 4 and earlier:

```
dtparam=spi=on
dtoverlay=pwm,pin=13,func=4
dtoverlay=uart3
dtoverlay=i2c-gpio,i2c_gpio_sda=2,i2c_gpio_scl=3,bus=3
# (dtparam=i2c_arm=on / i2c_arm_baudrate removed)
```

Pi 5 / CM5:

```
dtparam=spi=on
dtparam=i2c_arm=on
dtparam=i2c_arm_baudrate=400000
dtoverlay=pwm,pin=13,func=4
dtoverlay=uart2-pi5
```

## 5. Verification (Raspberry Pi 4 Model B Rev 1.4)

After reboot with the new configuration:

1. **Bus present, device detected.** `/dev/i2c-3` exists
   (`/sys/bus/i2c/devices/i2c-3` = the i2c-gpio adapter); the hardware `i2c-1`
   is gone. `i2cdetect -y 3` ACKs the BNO055 at `0x28`.
2. **Sensor data is clean.** Direct read over the software bus (service stopped):
   chip responds (temperature 55 °C); after the first warm-up sample, every
   quaternion has norm exactly 1.0; Euler angles stable and consistent with the
   resting attitude; gyro values change slightly sample-to-sample, proving each
   read is a fresh, successful bus transaction:

   ```
   [1] quat=(0.8865, -0.0907, 0.4538, -0.0001) |q|=1.0  euler=(0.0, -53.56, 15.69)  gyro=(0.003, -0.008, -0.001)
   [2] quat=(0.8865, -0.0907, 0.4538, -0.0001) |q|=1.0  euler=(0.0, -53.56, 15.69)  gyro=(0.0, -0.001, 0.0)
   ...
   ```
3. **No I2C errors in logs.** Since boot, zero occurrences of
   `Failed to get sensor` / `non-unit quaternion` / `Remote I/O` / `errno 121`.
4. **D-state pressure gone.** The IMU process (holder of `/dev/i2c-3`) samples as
   `S`/`R`; a 5 s system-wide sweep found at most **1** process in `D` at any
   instant and **0** I2C-related `D` states — versus ~59% `D`
   (`bcm2835_i2c_xfer`) for the IMU process and multiple simultaneous `D`
   processes during freezes before the change.

## 6. Trade-offs and notes

- **CPU cost of bit-banging:** `i2c-gpio` burns CPU in the kernel for each edge.
  In practice this is far cheaper than what it replaces: at 10 kHz the hardware
  bus kept the IMU process blocked ~40x longer per transaction than a normal
  100 kHz transfer. The software bus runs near the default `i2c-gpio` speed
  (~62 kHz with default `i2c_gpio_delay_us=2`, timing not guaranteed), which is
  several times faster than the old 10 kHz while being correct under clock
  stretching.
- **Same pins, same wiring.** GPIO2/GPIO3 are reused; no hardware change. All
  devices on the PiFinder I2C header (BNO055, BQ25895 on Rev-4) move to bus 3
  together, and both in-tree consumers go through the same `get_i2c()` factory.
- **Upgrades on existing installs:** re-running `pifinder_setup.sh` converges the
  boot config (it deletes the other path's lines first) and installs the new
  dependency; a reboot is required for the overlay to take effect.
- **Pi 5 at 400 kbps:** untested on real Pi 5 hardware so far; the BNO055
  datasheet allows up to 400 kHz, and RP1 handles clock stretching, but if an
  issue surfaced there the conservative fallback is simply omitting the
  baudrate line (100 kHz default).
- **Alternative considered:** BNO055 UART mode would also avoid the bug but
  requires rewiring and a UART, which PiFinder already uses for GPS.
