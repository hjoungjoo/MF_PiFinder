# MF_PiFinder 다음 작업 인수인계

작성일: 2026-06-26

이 문서는 `hjoungjoo/MF_PiFinder` fork의 `mf_pifinder` 브랜치를
Raspberry Pi 4에서 이어서 테스트하고, 발견한 문제를 같은 브랜치에 반영한 뒤
upstream Pull Request를 준비하기 위한 작업 노트이다.

## 현재 기준

저장소:

```text
fork:     https://github.com/hjoungjoo/MF_PiFinder
upstream: https://github.com/brickbots/PiFinder
```

작업 브랜치:

```text
mf_pifinder
```

현재 기준 커밋:

```text
d64aee8f Add CM5 Bookworm hardware support
```

브랜치 기준:

```text
origin/main
```

이 브랜치에는 Raspberry Pi CM5 + Bookworm 64-bit에서 동작시키기 위해 진행한
PiFinder 소스 변경과 문서가 포함되어 있다. Raspberry Pi 4 테스트 후 문제를
수정하면 같은 `mf_pifinder` 브랜치에 추가 커밋으로 반영한다.

## 이어서 작업할 때 Codex에게 줄 요약

새 장비나 새 대화에서 이어서 작업할 때 아래 문장을 먼저 전달한다.

```text
PiFinder CM5/Bookworm 작업을 Pi4에서 이어서 테스트하려고 해.
작업 브랜치는 hjoungjoo/MF_PiFinder의 mf_pifinder이고,
현재 기준 커밋은 d64aee8f Add CM5 Bookworm hardware support야.
docs/mf_pifinder_next_steps_ko.md,
docs/cm5_change_history_ko.md,
docs/cm5_bookworm_install_ko.md를 읽고 이어서 진행해줘.
Pi4 설치/하드웨어 테스트 중 생기는 문제를 같은 브랜치에 반영하고 싶어.
```

## 관련 문서

먼저 읽을 문서:

```text
docs/mf_pifinder_next_steps_ko.md
docs/cm5_change_history_ko.md
docs/cm5_bookworm_install_ko.md
```

영문 대응 문서:

```text
docs/cm5_change_history_en.md
docs/cm5_bookworm_install_en.md
```

## 주요 변경 범위

현재 브랜치에 포함된 큰 작업 범위는 다음과 같다.

- Raspberry Pi OS Bookworm boot config 경로 대응
- CM5/Pi 5의 `/boot/firmware/config.txt` 처리
- IMX462 카메라 overlay와 clock 설정
- SSD1351 OLED 표시와 SPI 장치 선택
- Focus 화면의 밝은 환경 표시 보정
- 카메라 gain 메뉴 추가
- GPS 포트 선택 메뉴와 gpsd 설정 동기화
- USB/Bluetooth 키보드 입력 지원
- Bluetooth 키보드 연결 UI와 자동 재연결
- 한국어 UI locale 추가
- 설치 사용자명 `pifinder` 하드코딩 제거
- systemd/Samba 설정 템플릿화
- 설치/업데이트/마이그레이션 스크립트 경로 정리
- 카메라-to-LCD 테스트 스크립트 추가
- CM5 Bookworm 설치 문서와 변경 히스토리 문서 추가

## Pi4 테스트 목표

Pi4 테스트의 목적은 다음 두 가지를 분리해서 확인하는 것이다.

1. 새 설치가 정상적으로 되는지 확인한다.
2. CM5/Pi 5 대응 코드가 기존 Pi4 계열 동작을 깨지 않는지 확인한다.

특히 아래 항목을 확인한다.

- `pifinder`가 아닌 사용자명으로 설치해도 동작하는지
- `/boot/config.txt`와 `/boot/firmware/config.txt` 경로 처리가 맞는지
- Pi4의 SPI 장치(`/dev/spidev0.0`)가 정상 선택되는지
- Pi5/CM5용 `/dev/spidev10.0` fallback이 Pi4 동작을 방해하지 않는지
- Camera Type 메뉴에서 Pi4용 카메라 전환이 정상인지
- GPS Port 메뉴가 Pi4의 UART/USB GPS에서 정상인지
- USB/Bluetooth 키보드 입력이 GPIO 키패드 입력을 방해하지 않는지
- 한국어 메뉴와 CJK 폰트 표시가 깨지지 않는지
- 설치 스크립트가 원격 네트워크를 예상치 못하게 끊지 않는지

## Pi4 준비 권장값

가능하면 첫 테스트는 Raspberry Pi OS Bookworm 64-bit에서 진행한다.

권장 OS 설정:

```text
OS: Raspberry Pi OS Bookworm 64-bit
SSH: enabled
hostname: mf-pi4-test 등 고유한 이름
username: pifinder가 아닌 이름 권장
```

네트워크:

```text
1순위: 유선 LAN + SSH
2순위: 모니터/키보드 연결
3순위: Wi-Fi SSH만 사용
```

Wi-Fi SSH만 사용하는 경우 `dhcpcd`, `hostapd`, `dnsmasq` 설치/설정 중 연결이
끊길 수 있으므로 주의한다. 재부팅은 가능한 한 모든 확인을 끝낸 뒤 마지막에 한다.

## Pi4에서 소스 받기

```bash
cd ~
git clone --recursive --branch mf_pifinder https://github.com/hjoungjoo/MF_PiFinder.git PiFinder
cd ~/PiFinder
git status --short --branch
git log --oneline -n 3
```

기대값:

```text
branch: mf_pifinder
latest known base commit: d64aee8f or later
```

## 설치 전 환경 기록

문제 재현을 위해 설치 전 상태를 기록한다.

```bash
mkdir -p ~/pi4-test-logs

{
  date
  hostnamectl
  cat /etc/os-release
  uname -a
  python3 --version
  id
  groups
  git -C ~/PiFinder status --short --branch
  git -C ~/PiFinder rev-parse HEAD
  ls -l /boot/config.txt /boot/firmware/config.txt 2>/dev/null || true
} | tee ~/pi4-test-logs/before-install.txt
```

## 설치 스크립트 테스트

중요: `sudo ./pifinder_setup.sh`로 실행하지 않는다.

```bash
cd ~/PiFinder
./pifinder_setup.sh 2>&1 | tee ~/pi4-test-logs/pifinder_setup.log
```

설치 중 확인할 사항:

- `apt-get install` 실패 여부
- `dhcpd` 패키지 설치 가능 여부
- `gpsd` 재설정 단계가 입력을 요구하거나 멈추는지
- `pip install --break-system-packages` 실패 여부
- `hip_main.dat` 다운로드 성공 여부
- systemd/Samba 설정 렌더링 성공 여부
- 사용자 그룹 추가 성공 여부

설치가 끝나면 재부팅 전 상태를 기록한다.

```bash
{
  date
  git -C ~/PiFinder status --short --branch
  groups
  ls -l /boot/config.txt /boot/firmware/config.txt 2>/dev/null || true
  grep -n "dtparam\|dtoverlay\|camera_auto_detect" /boot/config.txt /boot/firmware/config.txt 2>/dev/null || true
  systemctl is-enabled pifinder cedar_detect pifinder_splash 2>/dev/null || true
} | tee ~/pi4-test-logs/after-install-before-reboot.txt
```

## 첫 재부팅 후 확인

```bash
sudo reboot
```

재접속 후:

```bash
mkdir -p ~/pi4-test-logs

{
  date
  systemctl status pifinder cedar_detect pifinder_splash --no-pager
} | tee ~/pi4-test-logs/services-after-reboot.txt

journalctl -u pifinder -b --no-pager > ~/pi4-test-logs/pifinder-after-reboot.log
journalctl -u cedar_detect -b --no-pager > ~/pi4-test-logs/cedar-detect-after-reboot.log
```

## 하드웨어 확인 순서

하드웨어는 한 번에 모두 연결하지 말고 순서대로 확인한다.

1. LCD / 키패드 / IMU
2. 카메라
3. GPS
4. USB/Bluetooth 키보드

### LCD / SPI / I2C

```bash
ls -l /dev/i2c-* /dev/spidev* 2>/dev/null
i2cdetect -y 1
```

확인할 점:

- Pi4에서 `/dev/spidev0.0`가 존재하는지
- PiFinder OLED가 `/dev/spidev0.0`로 정상 초기화되는지
- IMU가 I2C에서 보이는지

### 카메라

카메라 리본은 전원을 끄고 연결한다.

```bash
rpicam-hello --list-cameras
```

필요하면 PiFinder 카메라 전환 스크립트를 사용한다.

```bash
cd ~/PiFinder
sudo python3 python/PiFinder/switch_camera.py imx477
# 또는 imx296 / imx462
sudo reboot
```

재부팅 후:

```bash
rpicam-hello --list-cameras
rpicam-still -o ~/pi4-test-logs/camera-test.jpg --timeout 2000
journalctl -u pifinder -b -n 200 --no-pager
```

주의:

- Pi4에서는 보통 CM5 CAM0용 `cam0` 파라미터가 필요하지 않다.
- `switch_camera.py`는 현재 `imx290`/`imx462`에 `clock-frequency=74250000`를 붙인다.
- Pi4에서 이 설정이 문제 없는지 확인한다.

### GPS

```bash
ls -l /dev/serial* /dev/ttyAMA* /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
systemctl status gpsd gpsd.socket --no-pager
cgps -s
```

PiFinder 메뉴에서 `GPS Port`를 실제 연결 포트로 바꾼 뒤 gpsd 설정이 반영되는지 확인한다.

### USB/Bluetooth 키보드

USB 키보드:

```bash
ls -l /dev/input/by-id /dev/input/by-path 2>/dev/null
```

Bluetooth 키보드:

```bash
bluetoothctl devices
bluetoothctl paired-devices
```

PiFinder 메뉴:

```text
Settings > Advanced > Keyboard
```

확인할 점:

- Scan / Pair에서 장치 이름이 표시되는지
- 이름이 advertise에 없고 scan response로 오는 장치도 표시되는지
- 재시작 후 paired/trusted 키보드 자동 재연결이 되는지
- Space는 Space로 입력되는지
- 알파벳은 실제 알파벳으로 입력되는지
- 실제 long press가 PiFinder long key로 동작하는지

## 문제 발생 시 수집할 정보

문제가 생기면 아래 출력을 저장한다.

```bash
mkdir -p ~/pi4-test-logs

{
  date
  hostnamectl
  cat /etc/os-release
  uname -a
  python3 --version
  id
  groups
  git -C ~/PiFinder status --short --branch
  git -C ~/PiFinder rev-parse HEAD
  ls -l /boot/config.txt /boot/firmware/config.txt 2>/dev/null || true
  grep -n "dtparam\|dtoverlay\|camera_auto_detect" /boot/config.txt /boot/firmware/config.txt 2>/dev/null || true
  ls -l /dev/i2c-* /dev/spidev* /dev/serial* /dev/ttyAMA* /dev/ttyUSB* /dev/ttyACM* /dev/video* /dev/media* 2>/dev/null || true
  systemctl status pifinder cedar_detect pifinder_splash gpsd gpsd.socket --no-pager || true
} | tee ~/pi4-test-logs/problem-summary.txt

journalctl -u pifinder -b --no-pager > ~/pi4-test-logs/problem-pifinder.log
journalctl -u cedar_detect -b --no-pager > ~/pi4-test-logs/problem-cedar-detect.log
dmesg > ~/pi4-test-logs/problem-dmesg.log
```

## 수정 반영 흐름

Pi4에서 문제를 수정한 뒤:

```bash
cd ~/PiFinder
git status --short
git add -A
git commit -m "Fix Pi4 install issue"
git push
```

현재 브랜치가 `mf_pifinder`이고 upstream이 `myfork/mf_pifinder`로 잡혀 있으면
`git push`만으로 GitHub 브랜치가 업데이트된다.

커밋 메시지는 문제별로 짧고 구체적으로 쓴다.

예:

```text
Fix Pi4 boot config path handling
Fix Pi4 SPI display detection
Fix setup script package install on Bookworm
Update Pi4 install notes
```

## PR 진행 계획

GitHub에서 Draft PR을 만들 때 기준은 다음과 같이 한다.

```text
base repository: brickbots/PiFinder
base branch: main
head repository: hjoungjoo/MF_PiFinder
compare branch: mf_pifinder
```

PR 제목 예:

```text
Add Raspberry Pi CM5/Bookworm hardware support
```

Draft PR 본문 초안:

```text
This is a draft PR for CM5/Bookworm and related hardware support.

The branch currently includes:
- Bookworm boot config path handling
- IMX462 overlay handling
- SSD1351 OLED/SPI compatibility work
- GPS port selection
- USB/Bluetooth keyboard support
- Korean UI locale
- install path/user templating
- CM5 Bookworm install and change-history docs

I am testing the same branch on Raspberry Pi 4 before requesting review.
```

Pi4 테스트가 끝나고 큰 문제가 정리되면 Draft 상태를 해제하거나, 관리자와 상의해
기능별로 더 작은 PR로 나눌지 결정한다.

## 주의할 점

- 현재 브랜치는 기능 범위가 크다. upstream에 바로 병합하기에는 리뷰 부담이 클 수 있다.
- 필요하면 나중에 기능별 브랜치로 분리한다.
- `noxfile.py`는 upstream 기준 Python 3.9를 사용하지만, Bookworm 장비는 Python 3.11이다.
- 로컬에서 Nox를 실행할 때는 우선 `nox -P 3.11 -s ...` 방식으로 테스트한다.
- `lint`와 `format` 세션은 소스를 자동 수정할 수 있으므로 실행 전 반드시 작업 상태를 확인한다.
- 네트워크 설정 관련 변경은 원격 SSH를 끊을 수 있으므로 Pi4에서는 유선 LAN이나 물리 접근을 확보하는 것이 좋다.
