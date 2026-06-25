# Raspberry Pi CM5 Bookworm 64-bit PiFinder 설치 매뉴얼

이 문서는 Raspberry Pi Compute Module 5(CM5), Raspberry Pi OS Bookworm
64-bit 환경에 brickbots/PiFinder `release` 브랜치를 설치한 절차를 정리한
것입니다.

공식 PiFinder 문서는 안정적인 사용에는 배포 이미지를 권장하고, 직접 설치는
주로 이미지 제작자/개발자용 절차라고 설명합니다. 또한 공식 직접 설치 절차는
Raspberry Pi OS Legacy Bullseye를 기준으로 작성되어 있습니다. CM5 Bookworm에서는
아래 차이를 반드시 고려해야 합니다.

- 부트 설정 파일은 `/boot/config.txt`가 아니라 `/boot/firmware/config.txt`입니다.
- Python은 3.11이며, `pip` 전역 설치에는 `--break-system-packages`가 필요합니다.
- Bookworm 기본 네트워크 관리는 NetworkManager입니다. PiFinder의 Wi-Fi/AP 전환
  스크립트는 `dhcpcd`, `wpa_supplicant`, `hostapd`, `dnsmasq` 모델을 전제로 합니다.
- 저장소의 Nox 설정은 Python 3.9를 요구합니다. Bookworm 기본 Python 3.11에서는
  직접 `pytest`/`ruff`를 실행하거나 Nox에 `--force-python 3.11`을 사용합니다.

## 현재 장비에 적용한 설치 상태

- 소스 위치: `/home/pifinder/PiFinder`
  - 이 장비는 OS 사용자를 `pifinder`로 만든 사례입니다.
  - 새로 설치할 때는 원하는 사용자명과 hostname을 사용해도 됩니다.
- 브랜치: `release`
- 서브모듈: 초기화 완료
- Python 런타임/개발 의존성: 설치 완료
- 데이터 디렉터리: `/home/pifinder/PiFinder_data`
- systemd 서비스: `pifinder`, `cedar_detect`, `pifinder_splash` enable 완료
- CM5 부트 설정: `/boot/firmware/config.txt`에 PiFinder용 SPI/I2C/PWM/UART 설정 추가
- 원격 SSH 보호를 위해 `dhcpcd`, `dnsmasq`, `hostapd` 자동 시작은 비활성화
- Bookworm 호환 패치: PiFinder가 `/boot/firmware/config.txt`를 우선 사용하도록
  `PiFinder.boot_config`를 추가하고 카메라 전환/표시 코드를 수정
- CM5/Pi 5 SPI 호환 패치: `/dev/spidev0.0`가 없고 `/dev/spidev10.0`만 있는
  경우에도 OLED/LCD 디스플레이 초기화가 가능하도록 SPI 포트를 자동 선택
- CM5/Pi 5 UART 주의: `dtoverlay=uart3`는 Pi 5 계열에서 GPIO8/9를 사용하므로
  SSD1351 OLED의 `CS=GPIO8/CE0` 배선과 충돌합니다. 이 회로에서는 `uart3`를
  끄고 GPS용 UART는 GPIO4/5의 `dtoverlay=uart2-pi5`를 사용합니다.
- IMX462 카메라: Bookworm 펌웨어에는 `imx462.dtbo`가 있으므로 전용 오버레이를
  사용합니다. CM5 IO 보드의 `CAM0`에 연결한 경우에는 `cam0` 파라미터가 필요합니다.
  `CAM1`에 연결하는 경우에는 `cam0` 없이 기본 오버레이를 사용합니다.

재부팅해야 부트 오버레이와 사용자 그룹 변경이 완전히 적용됩니다. 원격 접속 중이면
재부팅은 모든 확인을 끝낸 뒤 마지막에 하십시오.

## 단순 사용자용 설치

이 절차는 소스 수정 없이 PiFinder를 실행하려는 사용자를 위한 것입니다. CM5
Bookworm에서는 공식 설치 스크립트를 그대로 실행하지 말고 아래처럼 나누어 진행하는
것을 권장합니다.

### 1. 기본 OS 준비

1. Raspberry Pi OS Bookworm 64-bit를 설치합니다.
2. 사용자 이름과 hostname은 원하는 고유한 이름으로 만듭니다. 여러 대를 같이
   쓸 예정이면 예를 들어 `scope-a`, `scope-b`처럼 서로 다르게 지정합니다.
3. SSH와 Wi-Fi를 Raspberry Pi Imager에서 미리 설정합니다. 이후 mDNS 접속 주소는
   `<hostname>.local`이 됩니다.
4. 최초 접속 후 현재 네트워크가 안정적인지 확인합니다.

```bash
hostname -I
nmcli device status

export PI_USER="$(id -un)"
export PI_HOME="$(getent passwd "$PI_USER" | cut -d: -f6)"
export PF_REPO="$PI_HOME/PiFinder"
export PF_DATA="$PI_HOME/PiFinder_data"
```

### 2. 소스 받기

```bash
cd "$PI_HOME"
git clone --recursive --branch release https://github.com/brickbots/PiFinder.git
```

이미 받은 저장소가 있으면 다음으로 갱신합니다.

```bash
cd "$PF_REPO"
git fetch --all
git checkout release
git pull
git submodule update --init --recursive
```

### 3. Debian 패키지 설치

서비스가 자동 시작되어 네트워크를 흔들지 않게, 원격 작업 중에는 `policy-rc.d`로
자동 시작을 잠시 막는 것이 안전합니다.

```bash
sudo bash -c '
set -e
trap "rm -f /usr/sbin/policy-rc.d" EXIT
printf "%s\n" "#!/bin/sh" "exit 101" > /usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git python3-pip python3-venv python3-dev build-essential pkg-config \
  samba samba-common-bin dnsmasq hostapd dhcpcd gpsd \
  libinput10 libcap2-bin libjpeg-dev zlib1g-dev libfreetype6-dev \
  liblcms2-dev libopenjp2-7-dev libtiff-dev libffi-dev libssl-dev \
  python3-picamera2 rpicam-apps i2c-tools spi-tools
'
```

### 4. Python 런타임 의존성 설치

```bash
cd "$PF_REPO"
sudo python3 -m pip install --break-system-packages -r python/requirements.txt
```

### 5. 데이터 디렉터리와 서비스 설정

```bash
source "$PF_REPO/pifinder_paths.sh"

sudo install -d -o "$PI_USER" -g "$PI_USER" -m 755 \
  "$PF_DATA" \
  "$PF_DATA/captures" \
  "$PF_DATA/obslists" \
  "$PF_DATA/screenshots" \
  "$PF_DATA/solver_debug_dumps" \
  "$PF_DATA/logs" \
  "$PF_DATA/migrations"

printf Client > "$PF_REPO/wifi_status.txt"

sudo cp "$PF_REPO/pi_config_files/gpsd.conf" /etc/default/gpsd
pifinder_render_config "$PF_REPO/pi_config_files/smb.conf" /etc/samba/smb.conf
pifinder_render_config "$PF_REPO/pi_config_files/pifinder.service" /lib/systemd/system/pifinder.service
pifinder_render_config "$PF_REPO/pi_config_files/pifinder_splash.service" /lib/systemd/system/pifinder_splash.service
pifinder_render_config "$PF_REPO/pi_config_files/cedar_detect.service" /lib/systemd/system/cedar_detect.service

sudo systemctl daemon-reload
sudo systemctl enable cedar_detect pifinder pifinder_splash smbd nmbd gpsd.socket
```

하드웨어 접근 그룹도 확인합니다.

```bash
for group in input video render dialout gpio i2c spi; do
  getent group "$group" >/dev/null && sudo usermod -aG "$group" "$PI_USER"
done
```

### 6. CM5 Bookworm 부트 설정

`/boot/firmware/config.txt`에 다음 값이 있는지 확인하고 없으면 추가합니다.

```bash
sudo cp -a /boot/firmware/config.txt /boot/firmware/config.txt.before-pifinder
for line in \
  "dtparam=spi=on" \
  "dtparam=i2c_arm=on" \
  "dtparam=i2c_arm_baudrate=10000" \
  "dtoverlay=pwm,pin=13,func=4" \
  "dtoverlay=uart2-pi5"
do
  grep -qxF "$line" /boot/firmware/config.txt || echo "$line" | sudo tee -a /boot/firmware/config.txt
done
```

IMX462 카메라를 사용할 때는 자동 감지를 끄고 전용 오버레이를 추가합니다. CM5 IO
보드의 `CAM0` 포트를 쓰는 경우:

```bash
sudo sed -i 's/^camera_auto_detect=1/#camera_auto_detect=1/' /boot/firmware/config.txt
grep -qxF "dtoverlay=imx462,cam0,clock-frequency=74250000" /boot/firmware/config.txt || \
  echo "dtoverlay=imx462,cam0,clock-frequency=74250000" | sudo tee -a /boot/firmware/config.txt
```

`CAM1` 포트를 쓰는 경우에는 `cam0`를 빼서
`dtoverlay=imx462,clock-frequency=74250000`처럼 설정합니다.

### 7. 네트워크 관련 주의

원격 SSH가 Wi-Fi 위에 있다면 `dhcpcd`, `dnsmasq`, `hostapd`를 바로 켜지 마십시오.
현재 장비처럼 NetworkManager로 Wi-Fi에 연결한 상태에서는 다음처럼 PiFinder AP
서비스를 꺼 두는 편이 안전합니다.

```bash
sudo systemctl disable dhcpcd dnsmasq hostapd
```

PiFinder의 Wi-Fi/AP 전환 메뉴까지 공식 이미지처럼 쓰려면 로컬 콘솔, 유선 LAN,
또는 쉽게 복구할 수 있는 물리 접근을 확보한 뒤 별도로 전환하십시오.

### 8. 마지막에 재부팅

모든 작업을 끝낸 뒤 마지막에 재부팅합니다.

```bash
sudo reboot
```

재접속 후 확인합니다.

```bash
systemctl status pifinder cedar_detect pifinder_splash
journalctl -u pifinder -n 100 --no-pager
```

선택 사항으로 카탈로그 이미지를 받을 수 있습니다. 약 5GB 이상이며 오래 걸릴 수
있습니다.

```bash
cd "$PF_REPO/python"
python3 -m PiFinder.get_images
```

## 개발자용 설치

개발자는 단순 사용자 설치에 더해 개발 의존성과 테스트 도구를 설치합니다.

### 1. 개발 의존성 설치

```bash
cd "$PF_REPO"
sudo python3 -m pip install --break-system-packages -r python/requirements_dev.txt
```

### 2. Fork/remote 구성

본인 GitHub fork에 push하려면 origin을 fork로 바꾸고 upstream을 원본으로 둡니다.

```bash
cd "$PF_REPO"
git remote rename origin upstream
git remote add origin git@github.com:<YOUR_ID>/PiFinder.git
git fetch --all
```

쓰기 권한 없이 읽기만 할 때는 현재 `origin` 그대로 두어도 됩니다.

### 3. Bookworm에서 테스트 실행

저장소의 `noxfile.py`는 Python 3.9를 지정합니다. Bookworm 기본 Python 3.11에서는
아래 명령을 우선 사용합니다.

```bash
cd "$PF_REPO/python"
python3 -m ruff check PiFinder tests
python3 -m ruff format PiFinder tests
python3 -m pytest -m smoke
```

Nox를 꼭 쓰려면 다음처럼 Python 3.11을 강제로 지정합니다.

```bash
python3 -m nox --force-python 3.11 -s smoke_tests
```

### 4. 명령행 실행/디버깅

서비스와 수동 실행을 동시에 띄우면 같은 하드웨어를 잡으려 해서 충돌할 수 있습니다.
수동 실행 전 서비스를 멈춥니다.

```bash
sudo systemctl stop pifinder cedar_detect
```

실제 PiFinder 하드웨어에서 실행합니다.

```bash
cd "$PF_REPO/python"
python3 -m PiFinder.main -x
```

하드웨어 없이 UI/카탈로그 쪽을 개발할 때는 fake 옵션을 사용합니다.

```bash
cd "$PF_REPO/python"
python3 -m PiFinder.main -fh -k local --camera debug --display pg_128 -x
```

Cedar detect 서버를 따로 띄워야 할 때는 별도 터미널에서 실행합니다.

```bash
"$PF_REPO/bin/cedar-detect-server-aarch64" -p 50551
```

### 5. 코드 수정 뒤 반영

서비스로 돌리는 상태에서 Python 코드를 바꾼 뒤에는 서비스를 재시작합니다.

```bash
sudo systemctl restart cedar_detect pifinder
```

부트 설정, 사용자 그룹, 카메라 오버레이, 네트워크 스택을 바꾼 경우에는 재부팅이
필요합니다. 원격 접속 중이면 반드시 마지막에 수행하십시오.

## 검증 체크리스트

- `python3 -m PiFinder.main -h`가 도움말을 출력한다.
- `python3 -m pytest -m smoke`가 통과한다.
- `/boot/firmware/config.txt`에 PiFinder용 오버레이가 들어 있다.
- `id pifinder`에 `input`, `video`, `render`, `dialout`, `gpio`, `i2c`, `spi`가 보인다.
- `systemctl is-enabled pifinder cedar_detect pifinder_splash`가 `enabled`를 출력한다.
- 원격 Wi-Fi 접속 중에는 `dhcpcd`, `dnsmasq`, `hostapd`가 자동 시작되지 않는다.

## 주변기기 연결 순서

주변기기는 가능하면 전원을 끈 상태에서 하나씩 연결하고, 부팅 후 아래 항목을
확인합니다.

1. OLED/LCD 및 키패드

   ```bash
   ls /dev/spidev*
   sudo systemctl restart pifinder_splash pifinder
   journalctl -u pifinder -b -n 80 --no-pager
   ```

   CM5에서는 `/dev/spidev10.0`만 보여도 정상일 수 있습니다.
   `pinctrl get 8`이 `TXD3`로 나오면 `dtoverlay=uart3`가 OLED CS를 빼앗은
   상태입니다. `/boot/firmware/config.txt`에서 `uart3`를 끄고 재부팅하십시오.

2. IMU(BNO055)

   ```bash
   i2cdetect -y 1
   sudo systemctl restart pifinder
   journalctl -u pifinder -b -n 100 --no-pager
   ```

   기본 주소는 `0x28`입니다. `No I2C device at address: 0x28` 로그가 사라지면
   IMU 인식이 된 것입니다.

3. 카메라

   ```bash
   rpicam-hello --list-cameras
   sudo systemctl stop pifinder
   rpicam-still -n -t 2000 -o /tmp/imx462-test.jpg
   sudo systemctl start pifinder
   journalctl -u pifinder -b -n 120 --no-pager
   ```

   카메라가 없을 때의 기준 출력은 `No cameras available!`입니다. 카메라 연결 후
   이 문구가 사라지고 카메라 모델이 표시되어야 합니다.
   IMX462가 `imx290`으로 보이거나 `Error writing reg 0x303a: -121`,
   `Failed to queue buffer`, `Remote I/O error`가 나오면 목록 인식은 됐지만
   스트림 시작에 실패한 상태입니다. 이 경우 `/boot/firmware/config.txt`의
   카메라 줄이 실제 연결 포트와 맞는지 확인합니다. CM5 IO 보드의 `CAM0`이면
   `dtoverlay=imx462,cam0,clock-frequency=74250000`, `CAM1`이면
   `dtoverlay=imx462,clock-frequency=74250000`을 사용합니다. 그 다음 CSI 케이블
   방향, 카메라 전원, I2C 풀업, 2-lane/4-lane 모듈 종류를 차례로 확인합니다.

4. GPS

   ```bash
   gpspipe -r -n 5
   journalctl -u gpsd -b -n 80 --no-pager
   ```

   UART GPS는 PiFinder의 `GPS Settings > GPS Port`에서 실제 배선 포트를 선택합니다.
   CM5에서 이번 보드는 `/dev/ttyAMA2`, 기본 PiFinder 계열 보드는 보통
   `/dev/ttyAMA1`을 사용합니다. 포트나 baud를 바꾸면 PiFinder가
   `/etc/default/gpsd`의 `DEVICES`와 `GPSD_OPTIONS`를 갱신하고 gpsd를 재시작합니다.

## 참고 링크

- PiFinder 저장소: https://github.com/brickbots/PiFinder
- PiFinder Software Setup: https://pifinder.readthedocs.io/en/release/software.html
- PiFinder Contributors Guide: https://pifinder.readthedocs.io/en/release/dev_guide.html
