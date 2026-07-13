# 리포트: BNO055 I2C 클럭 스트레칭 문제 해결 (보드 모델 인지 I2C 버스 선택)

- 작성일: 2026-07-13
- 브랜치: `mf_pifinder` (포크 `hjoungjoo/MF_PiFinder`)
- 테스트 하드웨어: Raspberry Pi 4 Model B Rev 1.4, GPIO2/GPIO3에 연결된 BNO055 IMU
- 상태: Pi 4에서 구현·검증 완료. Pi 5 경로는 구현됨(업스트림과 같은 하드웨어 I2C 경로,
  400 kbps) — Pi 5 실기 테스트는 아직 안 함

## 1. 요약

PiFinder에서 간헐적인 전체 시스템 프리징이 발생했다: 무작위 시점에 모든 프로세스
(UI, 솔버, SkySafari 서버, 웹 UI)가 4~5초간 응답을 멈췄다가 풀린다. 원인 사슬은
다음과 같다.

1. BCM2835/BCM2711 I2C 컨트롤러(Pi 1~4)에는 클럭 스트레칭 지원에 잘 알려진 실리콘
   버그가 있다. BNO055 IMU는 SCL 클럭을 상시 스트레칭하므로 일반 버스 속도에서는
   전송이 깨질 수 있다.
2. 업스트림 PiFinder는 이를 하드웨어 버스를 10 kHz로 낮추는 것
   (`dtparam=i2c_arm_baudrate=10000`)으로 우회한다. 이 방법은 깨짐 확률은 줄이지만
   모든 IMU 트랜잭션을 ~40배 느리게 만들어, IMU 프로세스가 커널 I2C 전송 안에서
   대부분의 시간을 보내게 된다 (uninterruptible `D` 상태,
   `wchan=bcm2835_i2c_xfer`).
3. PiFinder는 프로세스 간 상태를 단일 `multiprocessing.BaseManager` 서버
   프로세스로 공유한다. CPU 압박 상황(열 스로틀링도 관측됨:
   `vcgencmd get_throttled` = `0x80000`)에서 느린 I2C에 묶인 IMU 프로세스와 직렬화된
   매니저가 호송(convoy)을 이룬다: 매니저가 느린 클라이언트 뒤에서 막히면 공유
   상태를 만지는 **모든** PiFinder 프로세스가 함께 멈춘다. 이것이 눈에 보이는
   4~5초 프리징이다.

수정은 보드 모델별로 I2C 전송 계층을 선택한다:

- **Pi 5 / CM5** (RP1 I2C 컨트롤러, 클럭 스트레칭 버그 없음): 하드웨어 I2C
  **400 kbps**.
- **Pi 4 이하** (BCM2835 계열 컨트롤러): 같은 물리 핀(GPIO2/GPIO3,
  `/dev/i2c-3`로 노출)에 **소프트웨어(비트뱅잉) `i2c-gpio` 버스**를 사용한다.
  소프트웨어 버스는 클럭 스트레칭을 규격대로 처리한다. 버그가 있는 하드웨어 블록
  (`i2c_arm`)은 핀을 점유하지 못하도록 끈다.

변경 후 Pi 4에서 IMU 프로세스는 더 이상 `D` 상태에 상주하지 않고(프리징 중 기존
~59% 샘플 → 사실상 0), BNO055 판독은 깨끗하며(단위 쿼터니언, I/O 오류 없음),
다중 프로세스 동시 `D` 상태라는 프리징 시그니처도 사라졌다.

## 2. 증상과 진단

- 증상: 무작위 시점에 — 메뉴를 빠르게 전환하면 가장 쉽게 재현 — 시스템 전체
  (모든 PiFinder 프로세스가 동시에)가 4~5초간 멈춘다.
- `/proc/<pid>/stat`을 샘플링하는 "프리즈 캐처" 스크립트가 여러 PiFinder
  프로세스가 동시에 `D` 상태인 2.3초 스톨을 포착했다.
- IMU 프로세스는 샘플의 ~59%에서 `D` 상태였고 `wchan = bcm2835_i2c_xfer` —
  즉 하드웨어 I2C 전송 내부에서 블록되어 있었다.
- 가중 요인: 4코어에 CPU 부하 ~5.4, `get_throttled = 0x80000`(소프트 온도 제한
  이력, 76.9 °C 관측) — 테스트 장비의 개발 도구 영향도 있지만, 순수 PiFinder
  시스템에서도 프리징은 재현된다.
- 모든 프로세스 간 상태가 GIL에 묶인 단일 `StateManager`/`BaseManager` 프로세스를
  거치므로, 하나의 클라이언트가 멈추면 전부가 직렬화된다 — 관측된 "모두 같이
  멈춤" 동작과 일치한다.

## 3. 배경: BCM2835 I2C 클럭 스트레칭 버그

BCM2835(그리고 Pi 4의 BCM2711까지 이어지는 후속) I2C 블록은 SCL을 고정 시점에
샘플링하며, 슬레이브가 클럭 스트레칭을 할 때 SCL 최소 하이 시간을 보장하지
않는다. 슬레이브(거의 모든 읽기에서 스트레칭하는 BNO055 같은)가 불운한 순간에
클럭을 놓으면 컨트롤러가 극단적으로 짧은(~40 ns) SCL 펄스를 낼 수 있고,
슬레이브는 깨진 바이트를 내보낸다. 참고 자료:

- https://www.advamation.com/knowhow/raspberrypi/rpi-i2c-bug.html
- https://github.com/raspberrypi/linux/issues/254
- https://github.com/raspberrypi/linux/issues/4884

알려진 우회책: (a) 스트레칭이 (거의) 일어나지 않도록 버스를 느리게 — 현재
업스트림 방식이며 위에서 설명한 지연 비용이 있음; (b) BNO055를 UART 모드로 사용;
(c) 클럭 스트레칭을 규격대로 구현하는 소프트웨어 `i2c-gpio` 버스 사용. Pi 5의
RP1 I2C 컨트롤러에는 이 버그가 없으므로 우회가 필요 없다.

(c)를 선택한 이유: 하드웨어 변경이 필요 없고, 배선과 디바이스 주소가 그대로이며,
두 가지 실패 모드(데이터 깨짐 *그리고* 10 kHz 지연)를 한 번에 제거하기 때문이다.

## 4. 변경 사항

다섯 부분이며, 모두 런타임/설치 시점에 모델을 인지한다 — 설정 옵션 없이 올바른
경로가 자동 선택된다.

### 4.1 신규 모듈: `python/PiFinder/i2c_bus.py`

어떤 버스를 내줄지 결정하는 단일 지점. 전체 소스:

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

### 4.2 `python/PiFinder/imu_pi.py` — 팩토리 사용

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

### 4.3 `python/PiFinder/hardware_detect.py` — 같은 팩토리, import-safe 유지

이 모듈은 Rev-4 디스플레이 자동 감지를 위해 BQ25895 충전 IC(0x6A)를 조사한다.
충전 IC도 같은 물리 핀에 있으므로 소프트웨어 버스에서 똑같이 접근된다.

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

### 4.4 `python/requirements.txt` — 의존성 추가

`Adafruit-Blinka`의 `board.I2C()`는 기본 하드웨어 버스에 고정되어 있다.
`adafruit-extended-bus`는 Blinka 호환을 유지하면서 임의의 `/dev/i2c-n`을 여는
`ExtendedI2C(n)`을 제공한다 (따라서 `adafruit_bno055`는 수정 불필요).

```diff
 adafruit-blinka==8.12.0
 adafruit-circuitpython-bno055
+adafruit-extended-bus==1.0.2
 cheroot==10.0.0
```

### 4.5 `pifinder_setup.sh` — 모델 인지 부팅 설정

무조건적인 `i2c_arm=on` + `i2c_arm_baudrate=10000` 라인을 기존
`pifinder_board_profile` 헬퍼(`pifinder_paths.sh`)에 대한 분기로 교체했다.
두 경로는 추가 전에 상대 경로의 라인을 먼저 삭제해 상호 배타적으로 유지되므로,
SD 카드를 다른 세대 보드로 옮겨 setup을 재실행해도 올바른 설정으로 수렴한다.

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

### 4.6 결과 `/boot/firmware/config.txt` (관련 라인)

Pi 4 이하:

```
dtparam=spi=on
dtoverlay=pwm,pin=13,func=4
dtoverlay=uart3
dtoverlay=i2c-gpio,i2c_gpio_sda=2,i2c_gpio_scl=3,bus=3
# (dtparam=i2c_arm=on / i2c_arm_baudrate 는 제거됨)
```

Pi 5 / CM5:

```
dtparam=spi=on
dtparam=i2c_arm=on
dtparam=i2c_arm_baudrate=400000
dtoverlay=pwm,pin=13,func=4
dtoverlay=uart2-pi5
```

## 5. 검증 (Raspberry Pi 4 Model B Rev 1.4)

새 설정으로 재부팅한 후:

1. **버스 생성, 디바이스 검출.** `/dev/i2c-3` 존재
   (`/sys/bus/i2c/devices/i2c-3` = i2c-gpio 어댑터). 하드웨어 `i2c-1`은 사라짐.
   `i2cdetect -y 3`에서 BNO055가 `0x28`로 ACK.
2. **센서 데이터 정상.** 소프트웨어 버스로 직접 판독(서비스 중지 상태): 칩 응답
   (온도 55 °C); 첫 웜업 샘플 이후 모든 쿼터니언의 노름이 정확히 1.0; Euler 각은
   정지 자세와 일치하며 안정; 자이로 값이 샘플마다 미세하게 변함 — 매 판독이
   신선하고 성공한 버스 트랜잭션임을 증명:

   ```
   [1] quat=(0.8865, -0.0907, 0.4538, -0.0001) |q|=1.0  euler=(0.0, -53.56, 15.69)  gyro=(0.003, -0.008, -0.001)
   [2] quat=(0.8865, -0.0907, 0.4538, -0.0001) |q|=1.0  euler=(0.0, -53.56, 15.69)  gyro=(0.0, -0.001, 0.0)
   ...
   ```
3. **로그에 I2C 오류 없음.** 부팅 이후 `Failed to get sensor` /
   `non-unit quaternion` / `Remote I/O` / `errno 121` 0건.
4. **D 상태 압박 해소.** IMU 프로세스(`/dev/i2c-3` 보유자)는 `S`/`R`로 샘플링됨.
   5초간 시스템 전체 스윕에서 어느 순간에도 `D` 상태 프로세스는 최대 **1**개,
   I2C 관련 `D` 상태는 **0**건 — 변경 전 IMU 프로세스의 ~59% `D`
   (`bcm2835_i2c_xfer`)와 프리징 중 다중 프로세스 동시 `D`에 대비된다.

## 6. 트레이드오프와 참고 사항

- **비트뱅잉의 CPU 비용:** `i2c-gpio`는 엣지마다 커널에서 CPU를 사용한다.
  실제로는 대체 대상보다 훨씬 저렴하다: 10 kHz 하드웨어 버스는 IMU 프로세스를
  일반 100 kHz 전송보다 트랜잭션당 ~40배 오래 블록시켰다. 소프트웨어 버스는
  기본 `i2c-gpio` 속도(기본 `i2c_gpio_delay_us=2` 기준 ~62 kHz, 타이밍 비보장)
  수준으로 동작해 기존 10 kHz보다 수 배 빠르면서 클럭 스트레칭에도 올바르다.
- **같은 핀, 같은 배선.** GPIO2/GPIO3를 그대로 재사용하며 하드웨어 변경이 없다.
  PiFinder I2C 헤더의 모든 디바이스(BNO055, Rev-4의 BQ25895)가 함께 버스 3으로
  이동하고, 트리 내 두 소비자 모두 같은 `get_i2c()` 팩토리를 거친다.
- **기존 설치의 업그레이드:** `pifinder_setup.sh` 재실행으로 부팅 설정이 수렴
  (상대 경로 라인을 먼저 삭제)하고 새 의존성이 설치된다. 오버레이 적용에는
  재부팅이 필요하다.
- **Pi 5의 400 kbps:** 실제 Pi 5 하드웨어에서는 아직 미검증. BNO055 데이터시트는
  400 kHz까지 허용하고 RP1은 클럭 스트레칭을 처리하지만, 문제가 나타나면
  보수적 대안은 baudrate 라인을 생략하는 것(기본 100 kHz)이다.
- **검토했던 대안:** BNO055 UART 모드도 버그를 피하지만 재배선과 UART가 필요한데,
  PiFinder는 UART를 이미 GPS에 사용 중이다.
