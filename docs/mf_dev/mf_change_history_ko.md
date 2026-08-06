# MF_PiFinder 소스 수정 히스토리

작성일: 2026-06-25
최종 업데이트: 2026-08-05

이 문서는 Raspberry Pi CM5, Raspberry Pi 4, Raspberry Pi 5 계열의 Bookworm
64-bit 환경에서 `mf_pifinder` 브랜치를 동작시키기 위해 PiFinder 저장소 안에 적용한
소스 수정 사항을 파일별로 기록한다.

upstream 원본 소스가 변경되었을 때 재동기화와 패치 재적용 기준으로 사용할 요약은
`docs/mf_dev/mf_upstream_patch_reference_ko.md`를 참고한다.

범위:

- PiFinder 저장소 내부 코드와 문서
- CM5/Pi4/Pi5, Bookworm, IMX462, SSD1351 OLED 대응을 위해 바꾼 PiFinder 코드
- 나중에 같은 변경을 검토하거나 upstream 반영 여부를 판단할 때 필요한 수준의 상세 기록

제외:

- Debian 패키지 설치 과정
- OS 네트워크 설정
- 실제 배선 변경 과정
- 재부팅, 서비스 시작/중지 같은 운영 절차
- 중간 테스트값과 폐기한 설정

## 작업 단위 목차 및 PR 상태

> **참고 (2026-07-23):** 아래 PR 상태와 "PR 재편성 제안"은 2026-06-27 스냅숏이며
> 더 이상 현행이 아니다. 현재는 릴리즈 전이라 `main`에 직접 커밋/푸시한다
> (CLAUDE.md의 pre-release 예외, 2026-07-21). 릴리즈 후 첫 수정부터 브랜치/PR
> 흐름으로 복귀한다. 아래 표는 기능별 범위 파악용 이력으로만 참고할 것.

상태 기준: 2026-06-27 현재 `brickbots/PiFinder`에 열린 `hjoungjoo` Draft PR과
로컬 `mf_pifinder` 통합 브랜치 기준이다.

| 작업 단위 | 현재 상태 | PR/브랜치 | 주요 범위 |
| --- | --- | --- | --- |
| Bookworm 설치/경로 기반 | Draft PR 있음 | [#499](https://github.com/brickbots/PiFinder/pull/499), `pr/bookworm-install-foundation` | `pifinder_paths.sh`, 설치/업데이트/마이그레이션 스크립트, systemd 서비스, Bookworm 경로 문서 |
| Raspberry Pi 4/5/CM5 보드 및 GPS/UART 프로파일 | Draft PR 있음 | [#505](https://github.com/brickbots/PiFinder/pull/505), `pr/board-gps-uart-profile` | `board_config.py`, `gps_port=auto`, GPSD 장치/baud 동기화, GPS Port 메뉴 |
| 카메라 preview/focus/gain 제어 | Draft PR 있음 | [#501](https://github.com/brickbots/PiFinder/pull/501), `pr/focus-gain-preview` | focus preview, 밝은 배경 threshold, camera gain profile/runtime 제어, LCD preview script |
| 한국어 UI localization | Draft PR 있음 | [#500](https://github.com/brickbots/PiFinder/pull/500), `pr/korean-localization` | `python/locale/ko`, 언어 메뉴의 `ko`, CJK font/restart 처리 |
| Bluetooth/USB HID 키보드 지원 | Draft PR 있음 | [#506](https://github.com/brickbots/PiFinder/pull/506), `pr/bluetooth-keyboard-support` | libinput 키 매핑, 텍스트 입력 키코드, Bluetooth keyboard scan/pair/connect UI, 재연결 |
| INDI 마운트 제어 | Draft PR 있음 | [#503](https://github.com/brickbots/PiFinder/pull/503), `pr/indi-mount-control` | optional INDI mount process, object details sync, LX200 OnStepX 커스텀 드라이버 패치, 설치 스크립트, INDI 문서 |
| INDI Multi Align 공통 흐름 | Draft PR 없음 | 로컬 `mf_pifinder` 작업트리 | 공통 `MultiPointAlignController`, Web/LCD/SkySafari session 통합, OnStepX native align 시작 지연, stale native align `:SX09,0#` reset, PiFinder 좌표 sync 검증, GoTo 실패 시 session 유지/target clear, 실제 OnStepX 장비 테스트 |
| GPS/NTP/RTC/Software PPS 통합 시간 동기화 | Draft PR 있음 | [#504](https://github.com/brickbots/PiFinder/pull/504), `pr/time-sync-sources` | GPS/NTP best-source 선택, helper service, dry-run/real clock sync, status UI, time sync 문서 |
| Wi-Fi AP+STA 동시 모드 및 AP 설정 | Draft PR 없음 | 로컬 `mf_pifinder` 작업트리 | `wlan0` STA + `uap0` AP, STA 채널 추적, STA 밴드 선호, AP IP 설정, AP WPA2 암호 설정, AP+STA 인터넷 공유 옵션, OS Wi-Fi 프로파일 가져오기, 스캔된 SSID 선택, Pi 4/5 공통 Wi-Fi 모드 |
| Locations 위치 카탈로그 | Draft PR 없음 | 로컬 `mf_pifinder` 작업트리 | GeoNames 기반 오프라인 위치 카탈로그, 국가/지역/군구/도시 선택, 좌표/고도/source 자동 입력, 북한 제외 |
| Web UI 적색 야간 테마 및 PWA 전체화면 앱 모드 | Draft PR 없음 | 로컬 `mf_pifinder` 작업트리 | red night theme, 브라우저별 theme 저장, PWA manifest, service worker, PWA icon |
| 선택형 IMU compass 방위 개선 | Draft PR 없음 | 로컬 `mf_pifinder` 작업트리 | 선택형 BNO055 NDOF 지자계 fusion mode, IMU calibration 상태 표시, 자동 calibration 저장/로드, 수동 calibration 메뉴 |
| SkySafari/INDI 마운트 모드 호환성 | Draft PR 없음 | 로컬 `mf_pifinder` 작업트리 | Alt/Az/EQ SkySafari LX200 status, optional GoTo/Sync forwarding, SkySafari guide keepalive bridge, no-solve IMU alignment correction, mount-mode compatibility checklist |
| Pointing Coordinate Service | Draft PR 없음 | 로컬 `mf_pifinder` 작업트리 | SkySafari/Web/LCD/INDI Multi Align 공통 좌표 서비스, 요청 좌표 그대로 사용, IMU smoothing, GoTo/수동 이동 중 mount readback 우선, Reset Pointing 시 SkySafari IMU alignment 보정 해제 후 raw IMU 좌표로 mount re-sync, 마운트 슬루 격리 보강(INDI 2.x updateProperty 1Hz readback, 누출 롤백, raw-IMU 델타 추적, post-motion settle 게이트; 2026-07-17, `mf_coordinate_helper_plan` 참고) |
| INDI GoTo/Guide 서비스 | Draft PR 없음 | 로컬 `mf_pifinder` 작업트리 | `mountcontrol_indi` executor 위에 얹는 별도 `indi_goto_guide_service` 프로세스(GoTo/Guide 정책 상태머신), SkySafari GoTo를 서비스로 라우팅, PiFinder 수동 접근 loop + correction pass 후 최종 INDI GoTo, tracking guide target을 서비스가 관리, 트래킹 가이드 외란 복구(정착 감지 후 3° 경계로 pulse-guide vs sync+GoTo 재획득, GoTo 복구 별도 On/Off), 웹 GoTo/Guide 상태 패널 + LCD GoTo Recovery 토글, 10° 복구 오차 상한 제거·GoTo Type 라벨 통일(2026-07-17), SkySafari/GoTo 설정 개편(2026-07-19: GoTo Type에 `off` 추가로 GoTo 전달 일원화, `skysafari_indi_goto`·`indi_goto_refine_once` 옵션과 LCD 가이드 화면 5번 Refine 토글 제거, `skysafari_indi_sync` 기본 켜짐, solve 전 SkySafari Align IMU 정렬 상시 켜짐, Refine Accuracy 입력을 GoTo/Guide 설정으로 이동, SkySafari Mount Mode 카드를 GoTo/Guide 설정 위로 이동, Object Details 5번 GoTo를 GoTo/Guide 서비스 경유로 변경), 트래킹 주파수 정책 통합(2026-07-20: 웹 카탈로그 push·LCD 키패드 5·SkySafari `:MS#` 세 진입점이 `track_freq_policy` 공유, SkySafari는 천체 종류가 없어 좌표를 에페메리스와 대조해 판별[허용오차 6′, `skysafari_planet_track_freq` 기본 켜짐], 웹/LCD는 `obj_type` 유지, 경로별 큐 검사 분리로 GoTo/Guide 큐 부재 시 multi-point align GoTo가 사라지던 결함 수정, `test_pos_server.py` 전체에 `unit` 마커 부여, 매칭 전용 `planet_positions_of_date()`로 equinox-of-date 위치를 계산해 SkySafari(JNow)와 `calc_planets()`(J2000)의 세차 22′ 불일치 수정[요청 좌표는 변환하지 않고 에페메리스만 맞춤], 진단용 `logconf_indi.json`에 `TrackFreqPolicy: INFO` 추가 — `mf_web_catalogs_dev_ko` P6-2 참고), `mf_indi_goto_guide_plan`·`mf_goto_mount_source_structure` 참고 |
| LiveCam RAW 프리뷰/라이브 스택 | Draft PR 없음 | 로컬 `mf_pifinder` 작업트리 | solver 경로와 분리된 `raw_live_stack`/`livecam_config` RAW 프리뷰·롤링 라이브 스택, 카메라 백엔드가 RAW 프레임 1장 publish → web API가 display용 PNG/JPEG/WebP 렌더링, 스택 모드(mean/sum/max)·크기/줌·기본값 reset 컨트롤, Web 카메라 노출/게인 컨트롤(`camera_controls`, `/api/camera/controls`, `camera_command_queue` 웹 배선), `mf_raw_live_stack_plan` 참고 |
| 변경 히스토리/PR 재편성 문서화 | Draft PR 없음 | 로컬 `mf_pifinder` 작업트리 | 이 문서의 작업 단위 목차, PR 상태, 재편성 기준 |
| 최종 통합 브랜치 | Upstream PR 아님 | `origin/mf_pifinder` + 로컬 미커밋 변경 | 위 기능들을 통합해 실제 장치에서 설치/테스트하는 기준 브랜치 |

## PR 재편성 제안

기존 Draft PR은 기능을 매우 작게 나누었기 때문에 리뷰 맥락을 보존하기 어렵다.
다음 기준으로 재정리하면 관리하기 쉽다.

| 새 PR 묶음 제안 | 포함 후보 | 기존 Draft PR 처리 |
| --- | --- | --- |
| Platform/Bookworm/RPi4-RPi5 compatibility | Bookworm 설치/경로 기반 + 보드/GPS UART 프로파일 | #499와 #505를 하나로 합치거나, #499를 확장하고 #505를 닫는 방식 |
| Camera usability | focus preview, camera gain, camera LCD preview | #501 유지 또는 camera 관련 문서와 함께 확장 |
| Input devices | Bluetooth keyboard, USB HID key mapping, keyboard mapping docs | #506 중심으로 정리 |
| Optional INDI mount integration | INDI mount process, install script, object sync, keyboard mapping의 INDI 항목 | #503 유지 |
| INDI Multi Align refinement | Multi Align 공통 session controller, OnStepX stale align reset, Web/LCD/SkySafari 흐름 문서 | INDI PR에 포함하거나 OnStepX 고급 기능 PR로 분리 |
| Integrated time sync | GPS/NTP/RTC/software PPS, helper service, status UI | #504 유지 |
| Network connectivity | AP/Client/AP+STA Wi-Fi modes, virtual AP services, STA 밴드 선호, AP IP 설정, AP 보안/암호, 선택형 AP+STA 인터넷 공유, OS Wi-Fi 프로파일 가져오기, 스캔된 SSID 선택, web/device network UI | 새 Draft PR 필요 |
| Locations catalog | GeoNames 기반 오프라인 위치 카탈로그, 국가/지역/군구/도시 선택, 좌표 자동 입력 | 새 Draft PR 필요 |
| Web observing UI | red night theme, PWA/fullscreen app mode | 새 Draft PR 필요 |
| 선택형 IMU compass 방위 개선 | BNO055 NDOF 옵션, 자동/수동 calibration, status UI | 새 Draft PR 필요 |
| SkySafari/INDI 마운트 모드 호환성 | Alt/Az/EQ SkySafari LX200 status, SkySafari GoTo/Sync forwarding, guide keepalive bridge, no-solve IMU 보정, INDI mount mode 검증 문서 | 새 Draft PR 필요 |
| Pointing Coordinate Service | `pointing.aligned.estimate`, IMU fallback, INDI mount readback을 통합하는 상시 좌표 서비스, SkySafari 좌표 응답, GoTo/수동 이동 중 mount progress readback | SkySafari/INDI 마운트 모드 PR 또는 별도 좌표 서비스 PR |
| INDI GoTo/Guide 서비스 | `indi_goto_guide_service` 프로세스, SkySafari GoTo 라우팅, PiFinder 수동 접근 + correction GoTo, tracking guide target 관리 | INDI PR에 포함하거나 GoTo/Guide 서비스 PR로 분리 |
| LiveCam RAW 프리뷰/라이브 스택 | `raw_live_stack`/`livecam_config`, RAW 프리뷰 + 롤링 스택, web 렌더 endpoint, 스택/크기/줌/reset 컨트롤, Web 카메라 노출/게인 컨트롤 | 새 Draft PR 필요 |
| Korean localization | Korean locale and CJK language handling | #500은 파일 규모가 커서 별도 유지 권장 |

문서는 각 기능 PR에 필요한 설치/사용 문서를 함께 넣는 방식을 권장한다. 예를 들어
INDI 문서는 INDI PR에, Time Sync 문서는 Time Sync PR에 포함한다.

## 최종 소스 변경 목록

변경 또는 추가된 PiFinder 파일:

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

원본 대비 재검토 결과:

```text
비교 기준: 현재 checkout된 PiFinder Git HEAD

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

이 재검토에서 위 목록 밖의 PiFinder 소스 변경은 발견되지 않았다. 아래 파일별
기록은 현재 작업트리와 원본 소스의 실제 diff를 기준으로 정리했다.

주요 최종값:

```text
SSD1351 SPI speed: 32000000 Hz
Focus bright-background threshold: 220.0
Pi camera startup gain: camera profile analog_gain 사용
camera_exp config value in use: auto
Default gps_port: auto
Resolved gps_port: CM5/Pi5 -> /dev/ttyAMA2, Pi4 -> /dev/ttyAMA3, fallback -> /dev/ttyAMA1
Keyboard HID input: GPIO keypad + USB/Bluetooth libinput
Menu languages: en, de, fr, es, ko, zh
Install user/path model: current OS user, not hard-coded pifinder
```

## `python/PiFinder/boot_config.py`

새로 추가한 파일이다.

### 추가한 API

```python
def get_boot_config_path() -> Path:
    firmware_config = Path("/boot/firmware/config.txt")
    if firmware_config.exists():
        return firmware_config
    return Path("/boot/config.txt")
```

### 수정 목적

PiFinder 기존 코드 일부는 Raspberry Pi boot config 경로를 `/boot/config.txt`로
고정해서 사용한다. Raspberry Pi OS Bookworm에서는 실제 설정 파일이
`/boot/firmware/config.txt`이므로, CM5 Bookworm에서 카메라 전환이나 카메라 타입
표시 기능이 실제 부팅 설정을 보지 못하는 문제가 생긴다.

### 동작 변화

- `/boot/firmware/config.txt`가 있으면 그것을 우선 사용한다.
- 없으면 기존 Raspberry Pi OS Legacy 계열과 호환되도록 `/boot/config.txt`를 사용한다.
- OS 버전별 경로 차이를 `switch_camera.py`, `callbacks.py`에 흩뿌리지 않고 한 곳에 모았다.

## `python/PiFinder/switch_camera.py`

카메라 오버레이 전환 코드가 Bookworm boot config 경로와 IMX462 오버레이를 다루도록 수정했다.

### 변경 전

- `/boot/config.txt`를 직접 읽고 썼다.
- `imx462` 요청을 내부에서 `imx290`으로 바꿨다.
- 새 카메라 오버레이를 추가할 때 `imx290`에만 `clock-frequency=74250000`을 붙였다.

### 변경 후

- `get_boot_config_path()`를 사용해 실제 boot config 파일을 찾는다.
- `imx462`를 더 이상 강제로 `imx290`으로 바꾸지 않는다.
- `imx290`, `imx462` 모두에 대해 필요 시 `clock-frequency=74250000`을 붙인다.
- 기존 `dtoverlay=imx...` 줄을 주석 처리하고 선택한 카메라 오버레이를 활성화하는 기존 흐름은 유지한다.
- `switch_boot()` docstring을 실제 동작에 맞게 boot config/root 표현으로 정리했다.

### 코드 수준 변경

```python
from PiFinder.boot_config import get_boot_config_path

boot_config_path = get_boot_config_path()
```

기존:

```python
with open("/boot/config.txt", "r") as boot_in:
```

수정:

```python
with open(boot_config_path, "r") as boot_in:
```

기존:

```python
if cam_type == "imx462":
    cam_type = "imx290"
```

수정:

```python
# imx462를 imx290으로 강제 변환하지 않음
```

### 기대 효과

- CM5 Bookworm에서 카메라 전환 코드가 실제 `/boot/firmware/config.txt`를 수정한다.
- Bookworm firmware에 있는 `imx462.dtbo`를 직접 사용할 수 있다.
- 오래된 imx290 대체 방식과 새 imx462 직접 오버레이 방식을 모두 수용할 수 있다.

## `python/PiFinder/ui/callbacks.py`

카메라 타입 표시 callback이 Bookworm boot config 경로를 읽도록 수정했고,
카메라 gain 메뉴와 GPS 포트 메뉴에 필요한 callback을 추가했다.

### 변경 전

- `get_camera_type()`가 `/boot/config.txt`를 직접 열었다.
- CM5 Bookworm에서는 실제 active config가 `/boot/firmware/config.txt`라 UI 표시가 실제 설정과 어긋날 수 있었다.

### 변경 후

- `get_boot_config_path()`를 사용한다.
- 기존 설치에서 `dtoverlay=imx290...`로 IMX462를 쓰던 경우를 고려해 UI 표시에서는 `imx290`을 `imx462`로 매핑하는 동작을 유지한다.
- gain 메뉴용으로 현재 runtime gain을 `shared_state.last_image_metadata()`에서 읽는다.
- `Profile` gain 표시용으로 현재 카메라 타입의 `CameraProfile.analog_gain`을 읽는다.
- gain 메뉴에서 선택한 값을 카메라 queue로 `set_gain:<value>` 형태로 보낸다.
- `Profile` 항목을 선택하면 `set_gain:profile`을 보낸다.
- `update_gpsd_baud_rate()`가 `gps_baud_rate`뿐 아니라 `gps_port`도 함께 읽는다.
- GPS baud나 port 메뉴에서 선택이 바뀌면 `sys_utils.check_and_sync_gpsd_config(baud_rate, gps_port)`를 호출한다.
- `switch_language()`가 `ko`와 `zh`를 CJK 언어로 처리해 언어 변경 뒤 PiFinder를 재시작한다.

### 코드 수준 변경

```python
from PiFinder.boot_config import get_boot_config_path
```

```python
with open(get_boot_config_path(), "r") as boot_in:
    boot_lines = list(boot_in)
```

### 기대 효과

- 카메라 설정 메뉴나 상태 표시가 Bookworm의 실제 boot config와 일치한다.
- IMX462를 imx290 호환 오버레이로 쓰던 기존 사용자의 표시도 깨지지 않는다.
- gain 메뉴의 checkmark가 저장된 `camera_gain` 값이 아니라 실제 runtime gain 기준으로 표시된다.
- GPS 포트와 baud rate를 UI에서 선택하면 gpsd 설정이 같은 callback으로 갱신된다.
- 한국어 선택 시 OLED에서 한글 glyph가 깨지지 않도록 CJK 폰트로 다시 시작된다.

## `python/PiFinder/sys_utils.py`

gpsd 설정 동기화가 baud rate만 보던 구조에서 serial device와 baud rate를 함께 보도록 확장했다.

### 변경 전

- `check_and_sync_gpsd_config(baud_rate)`는 `/etc/default/gpsd`의 `GPSD_OPTIONS`만 비교했다.
- `update_gpsd_config(baud_rate)`도 `GPSD_OPTIONS`만 수정했다.
- `DEVICES="/dev/ttyAMA1"` 같은 포트 설정은 UI에서 바꿀 수 없었다.

### 변경 후

- `DEFAULT_GPSD_DEVICE` fallback을 추가했다.
- `check_and_sync_gpsd_config(baud_rate, device=DEFAULT_GPSD_DEVICE)` 형태로 확장했다.
- `/etc/default/gpsd`의 `DEVICES`와 `GPSD_OPTIONS`를 모두 비교한다.
- 둘 중 하나라도 다르면 `update_gpsd_config(baud_rate, device)`를 호출한다.
- `update_gpsd_config()`는 `DEVICES=...`와 `GPSD_OPTIONS=...` 줄을 함께 갱신한다.
- 기존 파일에 해당 줄이 없으면 새 줄을 추가한다.
- 설정을 쓴 뒤 기존처럼 gpsd 서비스를 재시작한다.

### 기대 효과

- CM5처럼 GPS UART가 `/dev/ttyAMA2`에 잡히는 보드도 UI 설정으로 유지할 수 있다.
- PiFinder 재시작 시 `/etc/default/gpsd`가 선택한 포트와 baud로 자동 동기화된다.
- 후속 Pi4/Pi5 호환성 정리 뒤 기본 설정은 `gps_port: auto`가 되었고,
  `board_config` profile이 보드별 기본 포트를 결정한다.

### Bluetooth keyboard helper 추가

Bluetooth 키보드 연결 UI에서 사용할 수 있도록 `bluetoothctl` wrapper와 장치 파싱 함수를 추가했다.

추가한 주요 함수:

```python
def list_bluetooth_devices() -> list[dict[str, Any]]
def scan_bluetooth_devices(scan_seconds: int = 12) -> list[dict[str, Any]]
def connect_bluetooth_device(address: str) -> str
def disconnect_bluetooth_device(address: str) -> str
def remove_bluetooth_device(address: str) -> str
def reconnect_bluetooth_keyboards() -> int
def auto_reconnect_bluetooth_keyboards(...) -> int
```

구현 세부:

- `subprocess`로 `bluetoothctl`을 실행한다.
- ANSI escape와 prompt가 섞인 출력을 정리한 뒤 `Device <MAC> <name>` 형식을 파싱한다.
- 스캔 중 stdout을 버리지 않고 보존해 `[CHG] Device <MAC> Name: ...`와
  `[CHG] Device <MAC> Alias: ...` 형태의 scan response/name change 이벤트를 함께 파싱한다.
- 광고 목록의 초기 이름이 MAC 주소뿐이어도 scan response로 실제 이름이 들어오면 실제 이름을 우선 사용한다.
- 각 장치에 대해 `info <MAC>`를 호출해 `paired`, `trusted`, `connected`, `blocked`, `icon` 상태를 읽는다.
- 스캔 시 `agent KeyboardDisplay`, `default-agent`, `pairable on`, `scan on`을 순서대로 실행한다.
- `reconnect_bluetooth_keyboards()`는 paired 장치 중 keyboard로 보이는 장치를 우선 연결하고, 명확한 keyboard 장치가 없으면 paired 장치를 fallback으로 시도한다.
- `auto_reconnect_bluetooth_keyboards()`는 PiFinder 시작 직후 Bluetooth controller나 HID 장치가 늦게 준비되는 경우를 고려해 여러 번 재시도한다.
- 자동 재접속은 이미 connected인 장치는 건너뛰고, paired/trusted 장치 중 연결되지 않은 장치만 `connect`를 시도한다.

기대 효과:

- PiFinder UI에서 Bluetooth 키보드를 스캔, 연결, 재연결, 해제, 삭제할 수 있다.
- USB 키보드는 별도 설정 없이 기존 libinput 경로로 동작하고, Bluetooth 키보드는 페어링 뒤 같은 입력 경로로 동작한다.
- PiFinder 서비스 재시작이나 OS 재부팅 뒤 paired/trusted Bluetooth 키보드가 있으면 자동 재접속을 시도한다.

### 사용자명/홈 경로 hardcode 제거

기존 설치/런타임 일부는 OS 사용자가 항상 `pifinder`이고 데이터 경로가
`/home/pifinder/PiFinder_data`라고 가정했다. 여러 대의 PiFinder를 같은 네트워크에서
운영하기 위해 OS username과 hostname을 장비별로 다르게 지정할 수 있도록 이 가정을
줄였다.

변경 내용:

- `BACKUP_PATH`를 `utils.data_dir / "PiFinder_backup.zip"` 기반으로 변경했다.
- WiFi mode 전환은 `/home/pifinder/PiFinder/switch-*.sh` 대신
  `utils.pifinder_dir / "switch-*.sh"`를 호출한다.
- backup 대상 파일은 `utils.data_dir`에서 계산한다.
- software update script 경로는 `utils.pifinder_dir / "pifinder_update.sh"`에서 계산한다.
- NixOS migration script 경로도 `utils.pifinder_dir` 기반으로 변경했다.

기대 효과:

- OS 사용자를 `scope-a`, `scope-b`처럼 다르게 만들어도 backup, restore, update,
  WiFi mode 전환 경로가 현재 사용자의 PiFinder 설치 위치를 따른다.
- hostname은 Raspberry Pi OS에서 지정한 값을 유지하고, 웹 Network 화면에서 계속
  변경할 수 있다.

## `pifinder_paths.sh`, 설치/업데이트/마이그레이션 스크립트

새 공통 helper인 `pifinder_paths.sh`를 추가하고, 설치/업데이트 관련 shell script의
`/home/pifinder` 의존성을 제거했다.

### 추가한 helper

```bash
PIFINDER_USER
PIFINDER_HOME
PIFINDER_REPO_DIR
PIFINDER_DATA_DIR
pifinder_render_config <template> <target>
pifinder_boot_config_path
```

### 변경한 파일

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

구현 세부:

- `pifinder_setup.sh`는 root로 직접 실행하지 못하게 막고, 현재 OS 사용자 기준으로 설치한다.
- 필요한 시스템 작업은 스크립트 내부에서만 `sudo`로 실행한다.
- repo 경로는 기본적으로 `$HOME/PiFinder`, 데이터 경로는 `$HOME/PiFinder_data`를 사용한다.
- `pifinder_update.sh`와 `pifinder_post_update.sh`는 스크립트 자신의 위치에서 repo 경로를 계산한다.
- 마이그레이션 스크립트는 `PIFINDER_REPO_DIR`, `PIFINDER_DATA_DIR`, `PIFINDER_USER`를 사용한다.
- `switch-ap.sh`, `switch-cli.sh`는 스크립트 위치 기준으로 `wifi_status.txt`를 갱신한다.
- Bookworm에서는 `/boot/firmware/config.txt`, legacy에서는 `/boot/config.txt`를 사용하도록 helper를 공유한다.

기대 효과:

- Raspberry Pi Imager에서 OS user와 hostname을 `pifinder`가 아닌 원하는 이름으로 만들어도 설치 스크립트가 동작한다.
- 여러 대를 `scope-a.local`, `scope-b.local`처럼 분리해 mDNS 충돌을 줄일 수 있다.
- update/migration도 `/home/pifinder`에 묶이지 않는다.

### mDNS 안정화 (2026-08-06)

안드로이드에서 `<hostname>.local` 접속이 됐다 안 됐다 하는 문제를 잡기 위해
`pifinder_setup.sh`에 두 가지 설정을 추가했다.

- WiFi 절전모드 해제: brcmfmac이 절전 중 멀티캐스트(mDNS 질의)를 유실한다.
  PC는 캐시·재시도로 가려지지만 안드로이드 `.local` 리졸버는 타임아웃이 짧아
  간헐 실패로 드러난다. `/etc/NetworkManager/conf.d/wifi-powersave.conf`에
  `wifi.powersave = 2`(끔)를 기록한다.
- avahi IPv6 광고 차단: wlan0에 link-local(`fe80::`)뿐인데 AAAA로 광고되면
  IPv6를 우선하는 안드로이드가 zone 없는 `fe80::`로 접속을 시도해 실패한다.
  `avahi-daemon.conf`에 `use-ipv6=no`, `publish-aaaa-on-ipv4=no`를 적용한다.

## `pi_config_files/*.service`, `pi_config_files/smb.conf`

service와 Samba 설정 파일을 설치 시 렌더링하는 템플릿으로 변경했다.

### 변경 전

```text
User=pifinder
WorkingDirectory=/home/pifinder/PiFinder/python
guest account = pifinder
path=/home/pifinder/PiFinder_data
```

### 변경 후

```text
User=__PIFINDER_USER__
WorkingDirectory=__PIFINDER_REPO_DIR__/python
guest account = __PIFINDER_USER__
path=__PIFINDER_DATA_DIR__
```

`pifinder_render_config()`가 설치 시 placeholder를 실제 값으로 치환한다.

### 기대 효과

- systemd service가 custom OS user로 실행된다.
- Samba 공유도 custom user와 custom home 아래의 `PiFinder_data`를 사용한다.

## `python/PiFinder/api_extensions.py`, `python/views/tools.html`

custom user 환경에서 웹/API 경로와 안내 문구가 어긋나지 않도록 수정했다.

변경 내용:

- `/api/camera/debug`의 debug dump 경로를 `/home/pifinder/...` 대신 `utils.debug_dump_dir`로 변경했다.
- Tools 화면의 비밀번호 변경 안내 문구에서 고정 계정명 `pifinder`를 제거하고 “현재 시스템 사용자 계정”으로 표현했다.
- 한국어 locale의 해당 문구도 함께 갱신했다.

기대 효과:

- OS 사용자명이 `pifinder`가 아니어도 debug frame API와 비밀번호 변경 안내가 실제 설치 상태와 맞다.

## `python/PiFinder/main.py`

PiFinder 시작 시 gpsd 동기화에 GPS 포트를 포함했다.

### 변경 후

- `gps_baud_rate`와 함께 `gps_port`를 읽는다.
- `gps_port`가 없으면 `sys_utils.DEFAULT_GPSD_DEVICE`를 fallback으로 사용한다.
- `sys_utils.check_and_sync_gpsd_config(baud_rate, gps_port)`를 호출한다.
- 개발/테스트용 `--lang` 인자 허용 목록에 `ko`와 `zh`를 추가했다.

### 기대 효과

- 메뉴에서 선택한 GPS 포트가 서비스 재시작 뒤에도 `/etc/default/gpsd`에 유지된다.
- `python -m PiFinder.main --lang ko`처럼 한국어 UI를 직접 지정해 실행할 수 있다.

## `python/PiFinder/camera_interface.py`

카메라 gain을 런타임에 조정하는 기존 `set_gain` 명령을 확장했다.

### 변경 전

- `set_gain:<정수>` 명령만 처리했다.
- gain 값을 `int()`로 변환했다.
- 카메라 프로파일 기본 gain으로 되돌리는 명령은 없었다.

### 변경 후

- `get_default_gain()`을 추가했다.
- Pi camera처럼 `self.profile.analog_gain`이 있는 backend는 그 값을 기본 gain으로 반환한다.
- profile이 없는 debug/none backend는 현재 `self.gain`이 있으면 그 값을, 없으면 `1.0`을 fallback으로 사용한다.
- `set_gain:profile` 명령을 지원한다.
- 숫자 gain은 `float()`로 처리해 정수 외 값도 받을 수 있게 했다.
- console/log 표시는 `g` format을 사용해 `30.0` 대신 `30`처럼 표시한다.

### 코드 수준 변경

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

### 기대 효과

- PiFinder 시작 시 gain은 원본처럼 프로파일 기본값을 유지한다.
- 사용자가 메뉴에서 gain을 바꿀 때만 현재 실행 중인 카메라 gain이 바뀐다.
- `Profile`을 선택하면 저장된 `camera_gain` 값과 무관하게 카메라 프로파일 기본 gain으로 돌아간다.

## `python/PiFinder/keyboard_interface.py`

물리 키보드에서 들어온 실제 문자 입력을 UI까지 전달하기 위해 text keycode 영역을 추가했다.

추가한 API:

```python
TEXT_BASE = 1000

def text_key(char: str) -> int
def is_text_key(keycode: int) -> bool
def text_from_keycode(keycode: int) -> str
```

기대 효과:

- 숫자/방향/특수키 중심이던 기존 입력 큐에 알파벳 문자 입력을 안전하게 실을 수 있다.
- 기존 `ALT_*`, `LNG_*`, 숫자 keycode와 충돌하지 않는다.

## `python/PiFinder/keyboard_pi.py`

GPIO 키패드와 함께 USB/Bluetooth HID 키보드를 PiFinder 입력으로 사용할 수 있도록 libinput 키 매핑을 확장했다.

### 변경 전

- libinput 물리 키보드 매핑은 방향키, Enter, 일부 keypad `+/-` 정도만 처리했다.
- 숫자키, 숫자패드, Space, Esc, Backspace, long/alt shortcut에 대응하지 않았다.
- 기존 keypad `+/-` event code 매핑이 Linux input code 기준으로 서로 뒤바뀔 수 있었다.

### 변경 후

- Linux input key code 상수를 파일 상단에 명시했다.
- `self.physical_pressed`를 추가해 Alt/Ctrl/Shift 조합 상태를 추적한다.
- `self.physical_press_times`, `self.physical_last_repeat_times`, `self.physical_hold_sent`,
  `self.physical_press_modifiers`를 추가해 USB/Bluetooth 키보드의 실제 hold 시간을 추적한다.
- `self.text_physical_key_mapping`에 알파벳 키를 실제 문자 입력으로 매핑했다.
- `self.physical_key_mapping`에 USB/Bluetooth 키보드용 기본 매핑을 추가했다.
- `self.alt_physical_key_mapping`에 `Alt+키` 조합을 PiFinder `ALT_*` 입력으로 매핑했다.
- `self.long_physical_key_mapping`은 실제 long press와 호환용 `Shift/Ctrl+키` 조합에서 함께 사용한다.
- `Left`, `Right`, `Enter/KP Enter`는 1초 이상 누르면 실제 long key로 처리하고, release 시 일반키 중복 입력을 막는다.
- `Up`, `Down`은 GPIO 키패드처럼 1초 이상 누르면 일반 `UP/DOWN` 반복 입력으로 처리한다.
- `Alt+키` 조합은 long press보다 우선하며, `Alt`를 먼저 떼더라도 처음 눌렀을 때의 modifier 상태를 보존해 `ALT_*`로 처리한다.

주요 매핑:

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

이전의 `q/a/z`, `w/s/e/d/r/f/g`, `i/j/k/l/m` compact single-key shortcut은
실제 알파벳 입력을 방해하므로 USB/Bluetooth libinput 경로에서는 사용하지 않는다.

기대 효과:

- PiFinder service가 기본 `keyboard_pi` backend를 유지한 상태에서 USB 키보드와 Bluetooth 키보드를 모두 입력 장치로 사용할 수 있다.
- X11/Wayland DISPLAY가 필요한 `keyboard_local.py`를 사용하지 않아도 된다.
- GPIO 키패드 동작은 기존 matrix scan 경로를 그대로 유지한다.
- 객체 검색이나 이름 입력 화면에서 알파벳 키를 누르면 multi-tap 변환 없이 실제 문자가 입력된다.
- USB/Bluetooth 키보드도 실제로 키를 길게 눌러 marking menu, top menu 복귀, recent object 이동을 실행할 수 있다.

## `python/PiFinder/main.py`, `python/PiFinder/ui/base.py`, `python/PiFinder/ui/menu_manager.py`, `python/PiFinder/ui/textentry.py`

Bluetooth 키보드 자동 재접속과 알파벳 키코드를 UI text entry까지 전달하는 경로를 추가했다.

변경 내용:

- `threading`을 import했다.
- `start_bluetooth_keyboard_autoreconnect()`를 추가했다.
- 실제 Pi 하드웨어 모드에서만 `sys_utils.auto_reconnect_bluetooth_keyboards()`를 daemon thread로 실행한다.
- 이 thread는 PiFinder 하위 process들이 시작된 뒤 실행해 startup과 UI 표시를 막지 않는다.
- main loop에서 `KeyboardInterface.is_text_key(keycode)`를 특수키보다 먼저 검사한다.
- text keycode면 `KeyboardInterface.text_from_keycode(keycode)`로 실제 문자를 복원한다.
- `MenuManager.key_text(char)`를 추가해 현재 활성 UI module로 문자를 전달한다.
- `UIModule.key_text(char)` 기본 hook을 추가했다.
- `UITextEntry.key_text(char)`는 받은 문자를 `current_text`에 바로 추가하고 검색 결과를 갱신한다.

기대 효과:

- Bluetooth/USB 키보드에서 입력한 알파벳이 PiFinder 검색/텍스트 입력 화면에 실제 글자로 들어간다.
- 기존 숫자 keypad 기반 multi-tap 입력은 그대로 유지된다.
- paired/trusted Bluetooth 키보드는 PiFinder 시작 후 자동 재접속이 시도된다.

## `python/PiFinder/displays.py`

CM5/Pi 5 계열에서 SPI 장치 번호가 기존 Pi 4와 다를 수 있는 점과 SSD1351 OLED의 안정 SPI 속도를 반영했다.

### 변경 전

- 각 디스플레이 클래스가 직접 `spi(device=0, port=0, bus_speed_hz=...)`를 호출했다.
- `/dev/spidev0.0`가 없는 환경에서는 OLED/LCD 초기화가 실패할 수 있었다.
- SSD1351 기본 SPI 속도는 `40000000` Hz였다.

### 변경 후

- `display_spi(bus_speed_hz)` 헬퍼를 추가했다.
- `/dev/spidev0.0`, `/dev/spidev10.0` 순서로 존재 여부를 확인하고 사용한다.
- 둘 다 발견되지 않으면 기존처럼 `port=0`, `device=0`으로 fallback한다.
- `DisplaySSD1351`의 기본 SPI 속도를 `32000000` Hz로 조정했다.
- `DisplaySSD1351` 생성자가 `bus_speed_hz` 인자를 받을 수 있게 했다.
- `DisplaySSD1333`, `DisplayST7789_128`, `DisplayST7789`도 같은 `display_spi()` 헬퍼를 사용하도록 정리했다.
- SPI 장치 파일 존재 확인을 위해 `pathlib.Path` import를 추가했다.

### 추가한 헬퍼

```python
def display_spi(bus_speed_hz: int):
    for port, device in ((0, 0), (10, 0)):
        if Path(f"/dev/spidev{port}.{device}").exists():
            return spi(device=device, port=port, bus_speed_hz=bus_speed_hz)
    return spi(device=0, port=0, bus_speed_hz=bus_speed_hz)
```

### SSD1351 변경

기존:

```python
serial = spi(device=0, port=0, bus_speed_hz=40000000)
```

수정:

```python
def __init__(self, bus_speed_hz=32000000):
    serial = display_spi(bus_speed_hz=bus_speed_hz)
```

### 기대 효과

- CM5에서 SPI 장치가 `/dev/spidev10.0`으로 잡혀도 디스플레이가 초기화된다.
- SSD1351 OLED가 40MHz에서 화면 깨짐이 발생하는 환경에서 32MHz를 기본 안정값으로 사용한다.
- 테스트 스크립트에서는 `DisplaySSD1351(bus_speed_hz=...)`로 SPI 속도를 바꿔 비교할 수 있다.

## 카메라 gain 초기화 동작

이 항목은 최종 소스 변경 사항이 아니라, 검토 후 원본 동작으로 되돌린 내용이다.
최종 작업트리 기준으로 `python/PiFinder/camera_pi.py`는 원본 소스와 동일하며
Git diff가 없다.

최종 유지한 동작:

- `CameraPI.__init__()`는 원본처럼 `exposure_time`만 받는다.
- 초기 gain은 설정 파일의 `camera_gain`이 아니라 카메라 프로파일의 `analog_gain`을 사용한다.
- IMX462 프로파일 기준 초기 gain은 `30.0`이다.
- `/home/pifinder/PiFinder_data/config.json`과 `default_config.json`에 `camera_gain: 20`이 있어도 Pi camera 최초 초기화에는 적용하지 않는다.
- `set_gain` 같은 런타임 명령은 사용할 수 있지만, 최초 시작 gain을 바꾸지는 않는다.
- `exp_save`에서 `camera_gain`을 저장하는 기존 흐름은 그대로 둔다.

원본과 같게 유지한 코드 형태:

```python
def __init__(self, exposure_time) -> None:
```

```python
self.gain = self.profile.analog_gain
```

```python
camera_hardware = CameraPI(exposure_time)
```

이 결정으로 PiFinder의 관측용 자동 노출은 원본처럼 프로파일 gain을 기준으로 시작한다.

## `python/PiFinder/ui/fonts.py`

한국어 메뉴 표시를 위해 CJK glyph를 포함한 폰트를 한국어에서도 사용하도록 수정했다.

### 변경 전

- `language == "zh"`일 때만 `sarasa-mono-sc-light-nerd-font+patched.ttf`를 사용했다.
- 한국어 locale을 추가해도 기본 Roboto Mono 계열 폰트로는 한글이 표시되지 않을 수 있었다.

### 변경 후

- `lang in ["ko", "zh"]`일 때 Sarasa CJK 폰트를 사용한다.
- CJK 폰트 사용 시 기존 중국어 처리와 같이 Pillow layout engine을 끈다.
- 관련 주석은 영어로 유지했다.

### 기대 효과

- `ko` 언어를 선택하면 OLED 메뉴에서 한글 glyph가 표시된다.
- 중국어 UI의 기존 폰트 처리도 그대로 유지된다.

## `python/PiFinder/ui/menu_structure.py`

노출 설정 메뉴 바로 뒤에 카메라 gain 메뉴를 추가했고, GPS 설정 안에 GPS 포트 메뉴를 추가했다.
또한 `Settings > Advanced`에 키보드 설정 메뉴를 추가했고, 언어 메뉴에 한국어를 추가했다.

### 추가한 언어 메뉴

```text
Settings > User Pref... > Language > 한국어
```

구현:

- gettext 추출용 marker에 `Language: ko`를 추가했다.
- Language 메뉴 항목에 `name: _("Korean")`, `value: "ko"`를 추가했다.
- 키보드 입력 방식은 변경하지 않았고, USB/Bluetooth 키보드의 알파벳 입력은 계속 영문 문자 입력으로 동작한다.

기대 효과:

- PiFinder 본체 메뉴에서 한국어 UI를 선택할 수 있다.
- 언어 선택 후 callback이 PiFinder를 재시작하면서 한국어용 CJK 폰트가 적용된다.

### 추가한 메뉴

```text
Camera Gain
```

위치:

- `Camera Exp` 메뉴 바로 다음
- `WiFi Mode` 메뉴 바로 이전

### 메뉴 방식

- `Camera Exp`와 같은 `UITextMenu` 기반 single-select 메뉴다.
- `label`은 `camera_gain`으로 지정해 Focus 화면 marking menu에서 바로 이동할 수 있게 했다.
- `config_option`은 사용하지 않는다.
- 선택값은 저장 config가 아니라 `callbacks.get_camera_gain_selection`에서 읽은 runtime gain 기준으로 표시한다.
- 선택 후 `callbacks.set_gain`을 통해 카메라 프로세스에 명령을 보낸다.

### 선택 항목

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

`Profile` 항목은 현재 카메라 프로파일 기본 gain으로 돌아가는 항목이다. IMX462에서는
`30x`가 표시된다.

### 추가한 GPS 메뉴

```text
GPS Settings > GPS Port
```

선택 항목:

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

`GPS Port`와 `GPS Baud Rate`는 같은 post callback을 사용해 `/etc/default/gpsd`를 갱신한다.

### 추가한 키보드 메뉴

```text
Settings > Advanced > Keyboard
```

구현:

- `UIBluetoothKeyboard` 클래스를 import했다.
- `label`은 `keyboard_settings`로 지정했다.
- 메뉴 진입 시 Bluetooth 장치 목록을 읽고, 장치별 action menu를 제공한다.

기대 효과:

- Advanced 설정 안에서 Bluetooth 키보드를 연결할 수 있다.
- USB 키보드는 연결만 하면 같은 `keyboard_pi` 입력 backend에서 바로 동작한다.

## `python/locale/ko/LC_MESSAGES/messages.po`, `messages.mo`

한국어 UI를 위한 gettext catalog를 새로 추가했다.

### 생성 방식

- 현재 Python 소스에서 Babel로 메시지를 추출했다.
- `messages.po`에는 한국어 번역을 기록했다.
- `messages.mo`는 `pybabel compile -d python/locale -l ko`로 컴파일했다.

### 번역 기준

- 천문 분야에서 일반적으로 쓰는 용어를 우선 사용했다.
- `은하`, `산개성단`, `구상성단`, `성운`, `암흑성운`, `행성상성운`, `이중성`, `삼중성`, `시상`, `투명도`, `극축정렬`, `성도` 같은 용어는 한국어로 번역했다.
- `RA/DEC`, `DSO`, `SQM`, `Gain`, `Profile`, `T9`, `Multi-Tap`, 카탈로그명, 장치명, 포트명처럼 한국어로 바꾸면 어색하거나 식별성이 떨어지는 항목은 영문을 유지했다.
- 전체 추출 문자열 712개 중 핵심 PiFinder UI와 메뉴 중심으로 380개를 한국어로 번역했고, 나머지는 빈 문자열이 아니라 영어 원문을 표시하도록 두었다.

### 기대 효과

- 한국어 메뉴 선택 시 주요 본체 UI가 한국어로 표시된다.
- 아직 번역하지 않은 문자열도 빈 화면이 되지 않고 원문 영어로 표시된다.

## `python/PiFinder/ui/bluetooth_keyboard.py`

Bluetooth 키보드 페어링과 연결을 위한 새 UI 모듈이다.

### 5GHz 대역 인지형 WiFi pause (2026-08-07)

BT 페어링/재연결 시 WiFi를 무조건 끄던 것을 대역 인지형으로 개선.
Bluetooth는 2.4GHz 전용이므로, 활성 WiFi 링크가 전부 5GHz이거나(현 운용:
STA·uap0 모두 ch153/5765MHz — 단일 라디오라 AP가 STA를 따라감) 링크가
없으면 공존 간섭이 없어 pause를 건너뛴다.

- `sys_utils.bt_pairing_needs_wifi_pause()` 신설 — `iw dev <iface> info`의
  채널 주파수로 판정, 판정 불가 시 보수적으로 pause 유지.
- `pause_wifi_for_bt_pairing()`이 실제 pause 여부를 bool로 반환; 호출부
  (bluetooth_keyboard의 링크 컨텍스트·페어링)는 반환값으로 resume 여부 결정
  — 스킵 시 `nmcli connection up`/hostapd 재시작류 복구 부작용도 없음.
- 실장비 검증: 5GHz 상태에서 실제 호출 → 스킵(False)·journal 무흔적·WiFi
  유지. 단위테스트 5종(대역 조합·iw 실패 보수 동작 포함).
- 효과: 5GHz 망 운용 시 원격(SSH/웹) 세션이 BT 연결 작업 중에도 안 끊긴다.
  2.4GHz 링크가 하나라도 있으면 기존 pause 동작 그대로.

### UI 하니스 sys_utils mock 누락 수정 (2026-08-07)

실장비에서 `nox -s ui_tests`(전 화면 키 스위프)를 돌리면 Bluetooth 화면의
Reconnect가 **실물** `pause_wifi_for_bt_pairing()`을 실행해 WiFi가 60~90초
끊기는 사고가 실측됨(2026-08-07 08:15, SSH 세션 단절). 원인:
`test_ui_modules.py`의 `_inert_sys_utils` fixture가 모듈 3곳만 하드코딩
패치하는데, 화면들은 임포트 시점에 `sys_utils = utils.get_sys_utils()`로
실물을 바인딩하므로 이후 추가된 화면(bluetooth_keyboard, sqm, equipment)이
mock 밖에 있었다. 수정: `sys_utils` 모듈 속성을 가진 모든 로드된 PiFinder
모듈을 동적으로 찾아 일괄 패치 — 새 화면이 생겨도 자동 커버. 검증: 장비에서
전체 스위프 277 통과 + journal에 nmcli/WiFi 조작 무흔적. 참고: 장비에서
스위트를 돌릴 때는 `PIFINDER_USE_FAKE_SYS_UTILS=1` 안전망 병용 권장.

### 메뉴 항목

```text
Scan / Pair
Reconnect
Refresh
<cached or scanned Bluetooth devices>
```

장치 표시 prefix:

```text
* connected device
+ paired device
- discovered/unpaired device
```

목록에서는 작은 OLED 폭을 고려해 장치명을 우선 표시하고 MAC 주소 suffix는 붙이지 않는다.
장치명이 없거나 장치명이 MAC 주소로만 들어오면 `Unknown 12:34`처럼 짧은 fallback을 표시한다.
MAC 주소는 장치를 선택한 뒤 action menu의 보조 줄에 `MAC ...12:34:56` 형태로 표시한다.

### 장치 action menu

선택한 장치에 대해 다음 동작을 제공한다.

```text
Pair+Connect 또는 Pair Again
Connect
Disconnect
Remove
Cancel
```

### 페어링 처리

- `bluetoothctl`을 별도 process로 실행한다.
- `agent KeyboardDisplay`, `default-agent`, `pairable on`을 설정한 뒤 `pair <MAC>`을 실행한다.
- output을 non-blocking으로 읽어 OLED에 진행 상태를 표시한다.
- `Passkey: 123456` 형태의 출력이 나오면 `Type 123456`처럼 표시해 사용자가 Bluetooth 키보드에서 입력할 수 있게 한다.
- `Confirm passkey`, `Authorize service`, `Accept pairing` prompt가 나오면 `yes`를 보낸다.
- pairing이 성공하거나 이미 paired 상태이면 `trust <MAC>`, `connect <MAC>`를 이어서 보낸다.
- 왼쪽 키를 누르면 pairing process를 종료하고 목록으로 돌아간다.

기대 효과:

- 원격 접속 없이 PiFinder 화면과 키패드만으로 Bluetooth 키보드 연결을 시도할 수 있다.
- Bluetooth 연결 뒤에는 해당 키보드가 `/dev/input/event*`로 나타나며 `keyboard_pi.py`의 libinput 매핑을 통해 PiFinder 입력으로 동작한다.

## `python/PiFinder/mountcontrol_indi.py`, INDI 마운트 제어

INDI 마운트 제어는 선택 기능이다. 기본 PiFinder 설치만으로는 기존 기능이 동작하고,
`scripts/install_indi_mount.sh`를 실행해 INDI 의존성을 추가 설치한 사용자가
`mount_control`을 켰을 때만 별도 process가 시작된다.

### 주요 설정

```json
"mount_control": false,
"mount_control_indi_host": "localhost",
"mount_control_indi_port": 7624
```

### 동작 방식

- `main.py`가 `mount_control` 설정을 확인한 뒤 `mountcontrol_indi.run()` process를 시작한다.
- INDI 서버 접속 실패, PyIndi 미설치, 마운트 미검출 상태는 상태 파일과 console 메시지로 기록하고 PiFinder 본 기능은 계속 실행한다.
- `mount_control_status.json`에 compact 상태를 기록해 로그/디버그/웹 확인에 사용할 수 있게 했다.
- object details 화면에서 현재 대상에 대한 sync/goto/stop/manual step 명령을 mount queue로 보낸다.
- 종료 시 mount-control process에 shutdown command를 보내고, 응답하지 않으면 terminate한다.

### 웹 INDI 메뉴와 LX200 OnStep 제어

`python/views/indi_mount.html`을 추가하고 `python/PiFinder/server.py`에 `/indi`
라우트를 추가해 INDI를 `Equipment`와 `Tools` 사이의 독립 웹 메뉴로 분리했다.

- `INDI Web Manager` 버튼은 현재 PiFinder host의 `:8624`로 연결한다.
- `Current INDI Driver State`는 LX200 OnStep의 연결 방식, serial/network 설정,
  위치, UTC 시간, Park 상태, Slew Rate 상태를 `indi_getprop`으로 읽어 표시한다.
- `LX200 OnStep Driver Connection`은 USB Serial과 Network TCP를 선택할 수 있다.
  USB는 `/dev/serial/by-id`, `/dev/ttyUSB*`, `/dev/ttyACM*` 후보를 표시하고,
  네트워크는 AP 접속 장치 목록에서 IP를 선택하거나 수동 입력할 수 있다.
- `Location and Time`은 GPS lock이 있으면 GPS/loaded location을, 없으면
  PiFinder 기본 location을 사용한다. `Reload Current Values`로 PiFinder와 OnStep
  현재값을 다시 읽을 수 있고, 화면의 UTC 입력값은 초 단위로 계속 갱신된다.
- `Send Location and Time`은 browser가 보낸 시간을 그대로 쓰지 않고,
  Flask route가 POST를 받은 시점의 PiFinder system UTC를 다시 계산해 OnStep에
  전송한다.
- `Mount Control`에는 `At Home`을 `Parked`로 혼동하지 않도록 Home 상태,
  Park 상태, 원시 `:GU#` 마운트 상태를 분리 표시하고, At Home, Return Home,
  Park, Unpark, Set-Park 명령, OnStep 0-9 Slew Rate 선택, press-and-hold 방향
  이동을 추가했다.
- OnStepX `Settings` 영역에는 driver의 `Backlash.Backlash RA`,
  `Backlash.Backlash DEC` 속성을 사용하는 수동 Backlash 읽기/쓰기 제어를
  추가했다. 수동 저장은 마운트 이동 없이 설정값만 쓴다. Alt/Az 모드에서는
  같은 driver property를 `AZ`/`ALT`로, EQ 모드에서는 `RA`/`DEC`로 표시한다.
- 실제 백래시 테스트 중 tracking이 측정값에 섞일 수 있음을 확인했다. 자동
  Backlash는 시작 전 tracking을 끄고 정상 완료 후에만 원래 tracking 상태를
  복구하도록 보강했다.
- Auto Backlash는 호환성을 위해 내부 이름 `compass_goto_loop`를 유지하지만,
  현재 측정 이동은 다시 INDI GoTo를 사용한다. PiFinder는 테스트 시작 전과
  각 GoTo leg 이후 tracking을 다시 끄므로, OnStep이 GoTo 뒤 자동으로 tracking을
  켜더라도 측정 좌표 delta에 섞이지 않도록 한다. Alt/Az에서는 `AZ`와 `ALT`,
  EQ에서는 `RA`와 `DEC`를 한 축씩 분리 측정한다.
- GoTo 완료 처리는 stable idle window와, OnStep status를 읽을 수 있는 경우
  `:GU#`의 `N`(`No goto`) 상태를 기다린 뒤 Backlash mount/solved 샘플을
  기록하도록 보강했다. OnStepX가 근처 목표점에서 settle wait 후 최종 미세
  접근을 다시 수행하는 동안 측정하는 문제를 막기 위한 처리다.
- Auto Backlash는 더 이상 IMU Compass/NDOF 모드나 MAG calibration을 요구하지
  않는다. 대신 fresh plate-solved `PointingCoordinateService.solved` 좌표를
  요구하고, GoTo loop 전에 solved RA/Dec로 mount 좌표를 sync한다. 각 GoTo
  leg의 mount 시작/종료 좌표와 PiFinder solved 시작/종료 좌표를 기록하고,
  mount-solved 이동 차이가 1도 이상인 leg를 제외한 뒤 하위/상위 30%를 버리고
  가운데 40% 평균을 이동 방향별 추천값으로 표시한다.
- Auto Backlash는 더 이상 Backlash를 0으로 초기화하지 않고, 계산값을 자동
  적용하지 않으며, 주기적 UI 갱신 중 입력칸을 바꾸지 않는다. 사용자가 추천값을
  확인한 뒤 `Save Backlash`로 저장한다.
- 2026-07-03 실제 RA/DE GoTo 왕복 테스트에서는 두 축 모두 20도 왕복에서도
  당시 PiFinder/INDI write 제한이던 `999 arc-sec` 상한에 도달했다. 이후
  OnStep 펌웨어와 INDI property 표시 범위에 맞춰 PiFinder, Web UI, OnStepX
  driver write 제한을 모두 `3600 arc-sec`으로 통일했다. 상한 도달 시 자동
  계산값은 바로 적용하지 않고 낮은 신뢰도로 표시한다.
- OnStepX driver patch는 이제 OnStepX 장치의 `GUIDE_RATE`를 writable로 만들고,
  요청값을 OnStep rate selector로 변환한 뒤 실제 pulse-guide rate를 다시
  읽어 검증한다. Auto Backlash는 더 이상 `GUIDE_RATE`에 의존하지 않지만,
  writable/readback 동작은 OnStepX driver 호환성 패치로 유지한다. 소스 설치
  스크립트는 이 패치를 적용하며, 바이너리 아카이브도 패치된 OnStepX driver로
  다시 생성했다.
- INDI 바이너리 아카이브는 이제 git에 `.tar.gz.part-*` 조각 파일로 저장할 수
  있다. 아카이브 설치 스크립트는 조각을 다시 합친 뒤 `.sha256` checksum을
  검증하고, 패키지 생성 스크립트는 큰 아카이브의 조각 파일을 자동 생성한다.
- 방향 이동은 버튼을 누르고 있는 동안 motion 명령을 보내고, pointer up/cancel/leave
  시 stop 명령을 보내도록 AJAX로 처리한다.
- Red Night theme에서도 select/dropdown/table이 흰색으로 뜨지 않도록 CSS를
  보정했고, Materialize select input의 글자 잘림을 줄이기 위해 높이와 label
  위치를 조정했다.

### 문서/설치 파일

```text
docs/mf_dev/mf_indi_mount_install_ko.md
docs/mf_dev/mf_indi_mount_install_en.md
scripts/install_indi_mount.sh
docs/mf_dev/mf_keyboard_mapping_ko.md
docs/mf_dev/mf_keyboard_mapping_en.md
```

## `python/PiFinder/gps_time_sync.py`, 통합 시간 동기화

GPS, Chrony, PiFinder SNTP, RTC, software PPS를 하나의 Time Sync 기능으로 관리하도록 추가했다.
기본값은 전체 `Off`이며, 사용자가 UI에서 켰을 때 기본 system clock 관리는 `chronyd`가 담당한다.

### 주요 설정

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

### 동작 방식

- 기본 `chrony` 모드에서는 `chronyc tracking` 상태를 읽고, Linux system clock은 chronyd가 관리한다.
- `best` 모드에서는 Chrony, GPS, PiFinder SNTP 후보를 비교한다.
- PiFinder 자체 SNTP는 chronyd와 중복되지 않도록 기본 `Off`이며 fallback/check 용도로 사용할 수 있다.
- PiFinder 본체는 일반 권한으로 실행하고, RTC 쓰기와 명시적 `Clock Manager = PiFinder` fallback system clock 쓰기는 `gps_time_sync_helper.py` root helper service가 처리한다.
- helper는 dry-run 모드와 실제 적용 모드를 분리하며, 기본 chrony 구성에서는 system clock을 직접 쓰지 않는다.
- 상태 UI는 `Tools > Place & Time > Time Sync`에서 확인한다.
- 설정 UI는 `Settings > Advanced > Time Sync`에 추가했다.

### 문서/설치 파일

```text
docs/mf_dev/mf_time_sync_ko.md
docs/mf_dev/mf_time_sync_en.md
pi_config_files/pifinder_gps_time_sync.service
scripts/install_chrony_time_sync.sh
scripts/install_gps_time_sync_helper.sh
```

## Wi-Fi AP+STA 동시 모드

기존 `Client` 또는 `AP` 단일 선택 구조에 `AP+STA` 모드를 추가했다.
이 모드는 `wlan0`을 STA로 유지해 인터넷/업데이트에 사용하고, `uap0` 가상 AP
인터페이스로 스마트폰/태블릿 제어용 PiFinder AP를 동시에 제공한다.

### 주요 동작

- 웹 `Tools > Network`와 기기 `Settings > WiFi Mode`에 `AP+STA` 선택지를 추가했다.
- `switch-apsta.sh`는 `/etc/dhcpcd.conf.apsta`를 적용하고 `pifinder_apsta_prepare`,
  `pifinder_apsta_monitor`, `dnsmasq`, `hostapd`를 활성화한다.
- `scripts/pifinder_apsta.sh prepare`는 `uap0`를 만들고 `10.10.10.1/24`를 설정한다.
- `scripts/pifinder_apsta.sh monitor`는 STA 채널을 감시하고 채널이 바뀌면
  `hostapd.conf`의 `channel`/`hw_mode`를 갱신한 뒤 `hostapd`를 재시작한다.
- `switch-ap.sh`와 `switch-cli.sh`는 AP+STA monitor service를 중지하고 `uap0`를 정리한다.
- Pi 4와 Pi 5 모두 기본 `wlan0` 위에 `uap0`를 추가하는 동일 구조를 사용한다.

### 문서/설치 파일

```text
docs/mf_dev/mf_wifi_apsta_ko.md
docs/mf_dev/mf_wifi_apsta_en.md
pi_config_files/dhcpcd.conf.apsta
pi_config_files/pifinder_apsta_prepare.service
pi_config_files/pifinder_apsta_monitor.service
scripts/pifinder_apsta.sh
switch-apsta.sh
```

## Locations 위치 카탈로그

웹 `Locations > Add New Location`에 국가/지역/군구/도시 선택 기반 좌표 입력 기능을
추가했다.

### 주요 파일

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

### 동작

- GeoNames `cities5000`, `countryInfo`, `admin1CodesASCII`, `admin2Codes`를
  가공해 오프라인 JSON 카탈로그를 만들었다.
- 한국은 GeoNames 국가별 전체 덤프 `KR.zip`을 추가로 섞어 서울/구/동 단위 선택을
  더 자세하게 제공한다.
- 북한은 국가 코드 `KP`를 생성 단계에서 제외했다.
- 서버는 전체 JSON을 브라우저에 직접 보내지 않고, 국가/지역/군구/장소 단계별
  API를 제공한다.
- 장소를 선택하면 기존 위치 추가 form의 이름, 위도, 경도, 고도, 오차, 출처
  필드를 기본값으로 채운다.
- 수동 좌표 입력과 DMS 입력은 그대로 유지한다.
- `scripts/build_location_catalog.py`로 catalog를 다시 생성할 수 있다.

## Web UI 적색 야간 테마 및 PWA 앱 모드

관측 중 웹 UI가 암시야를 덜 해치도록 적색 야간 테마를 추가했고, 모바일/태블릿에서
홈 화면에 추가해 앱처럼 열 수 있도록 PWA 구성을 추가했다.

### 주요 파일

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

### 동작 방식

- `Gray`와 `Red Night` 테마를 선택할 수 있다.
- 선택값은 브라우저 `localStorage`에 저장되므로 장치별로 유지된다.
- 상단 메뉴와 모바일 메뉴에 `Fullscreen` 버튼을 추가해 사용자가 직접 전체화면 모드에 진입할 수 있다.
- Fullscreen API는 페이지 이동 시 해제될 수 있으므로, 전체화면 상태에서 내부 메뉴로 이동하면 다음 페이지에 `Resume Fullscreen` 복구 버튼을 표시한다.
- 로그 페이지의 로그 본문 색상은 기존 level 색상 그대로 유지한다.
- manifest는 `display: fullscreen`을 사용하되, PiFinder 웹 UI 내부의 nav/footer는 유지한다.
- service worker는 캐싱 없이 네트워크 요청을 통과시키는 최소 형태로 두어 실시간 UI 동작에 영향을 주지 않는다.
- (2026-07-25) Materialize의 밝은 기본 위젯 스타일을 **두 테마 공통**으로 바꿨다.
  기존에는 `html[data-theme="red"]` 아래에만 덮어써서, Gray 테마에서
  `select.browser-default`가 흰 상자(`rgba(255,255,255,0.9)`)에 밝은 글자로 나와
  읽을 수 없었다(LiveCam/Logs). 같은 원인으로 Gray 테마에서 select caret(검정),
  dropdown 패널(`#fff`), modal(`#fafafa`), sidenav도 밝은 기본값이었다. 규칙이 모두
  테마 변수 기반이라 `html[data-theme="red"]` 접두사만 떼어 공통 규칙으로 승격했고,
  browser-default select에는 catalogs.css(`.pfcat`)에서 검증된 방식(커스텀 화살표 +
  `color-scheme: dark`)을 전 페이지로 확장했다. Materialize가 비활성 입력을
  `rgba(0,0,0,0.42)`(어두운 배경에서 안 보임)로 칠하는 것도 함께 수정했다.
- (2026-07-25) 같은 원인으로 남아 있던 두 가지를 추가로 고쳤다(INDI 페이지에서 발견).
  ① `table.striped` 홀수 행이 `rgba(242,242,242,0.5)` — 어두운 카드 위에서 밝은 띠가
  되어 `.grey-text` 본문이 밝은 회색 위 회색(1.2:1)이었다. 행 배경을 테마 변수로
  바꾸고, striped 테이블 안의 `.grey-text`는 적색 테마와 동일하게 `--pf-text`로
  승격했다(4.7~5.7:1). ② 체크박스 빈 상자가 `2px solid #5a5a5a` — Gray 테마 카드
  대비 1.1:1로 사실상 안 보였다(INDI GoTo/Guide 옵션들). 외곽선을 `--pf-text`
  (Gray 4.7:1 / Red 5.2:1), 체크 표시를 `--pf-link`로 바꾸고 체크박스 캡션도
  Materialize의 `#9e9e9e` label 색 대신 본문 색을 쓰게 했다. 단, 외곽선 색은
  **`:not(:checked)`로 한정해야 한다** — Materialize는 체크 표시를 같은 `::before`를
  40도 회전시키고 위/왼쪽 테두리를 `transparent`로 두어 만들기 때문에, 네 변을 모두
  칠하면 체크가 기울어진 사각형으로 보인다(실제로 한 번 발생시켰고 회귀 테스트
  `test_checkbox_outline_colour_never_reaches_the_checked_state`로 고정했다).
  `base.html`의 `style.css?v=` 캐시 버스터를 7로 올렸다.

## `default_config.json`

GPS 포트 설정 기본값을 추가했다.

```json
"gps_port": "auto"
```

기본값 `auto`는 보드 모델에 따라 CM5/Pi5는 `/dev/ttyAMA2`, Pi4는 `/dev/ttyAMA3`,
그 외 보드는 `/dev/ttyAMA1`로 해석된다.

## `python/PiFinder/ui/preview.py`

포커스 화면에서 밝은 장면이나 포화에 가까운 장면이 검정 또는 단색처럼 보이는 문제를 해결했다.
또한 Focus 화면 marking menu에서 gain 메뉴로 바로 이동할 수 있게 했다.

### 문제 원인

기존 포커스 화면은 어두운 밤하늘에서 별을 보기 좋게 하기 위해 detector가 계산한 배경값을 검정에 맞추는 stretch를 사용했다. 이 방식은 밤하늘에는 적합하지만, 밝은 장면에서는 배경 자체가 매우 높아서 전체 화면이 검정으로 눌리거나 8-bit 처리 프레임이 포화되어 디테일이 사라질 수 있다.

카메라 raw 프레임은 정상적으로 들어오고 있었으므로, 카메라 노출/게인을 바꾸는 대신 포커스 화면의 표시 경로만 보완했다.

### 추가한 상수

```python
STRETCH_BRIGHT_BACKGROUND = 220.0
```

의미:

- focus detector가 계산한 배경값이 이 값 이상이면 밝은/포화 프레임으로 판단한다.
- 이 경우 기존 dark-sky stretch를 적용하지 않는다.

### `_apply_stretch()` 변경

밝은 배경이면 기존 stretch를 건너뛴다.

```python
if black >= STRETCH_BRIGHT_BACKGROUND:
    return image_obj
```

이 변경은 display-only 처리이며, focus 측정이나 카메라 설정을 변경하지 않는다.

### `_orient_camera_image()` 추가

raw 기반 표시 이미지에도 기존 camera image와 같은 회전 규칙을 적용하기 위해 추가했다.

동작:

- `camera_rotation` 설정이 있으면 그 값을 우선 사용한다.
- 없으면 `screen_direction`에 따라 기존 camera loop와 같은 방향으로 회전한다.

### `_raw_display_image()` 추가

밝은 장면에서 포커스 화면 배경으로 사용할 raw 기반 표시 이미지를 생성한다.

처리 순서:

1. `self.shared_state.cam_raw()`에서 최신 raw 배열을 가져온다.
2. 2차원 raw 배열이 아니면 fallback하지 않는다.
3. `float32`로 변환한다.
4. 배열 크기를 짝수 크기로 맞춘다.
5. nominal Bayer 2x2 블록을 평균한다.
6. 1.0 percentile과 99.5 percentile 기준으로 표시용 8-bit stretch를 만든다.
7. 두 percentile 값의 차이가 1 ADU 이하이면 포화되었거나 거의 평평한 밝은 raw로 보고 흰색 프레임으로 표시한다.
8. `_orient_camera_image()`로 화면 방향을 맞춘다.

2x2 평균을 넣은 이유:

- IMX462가 드라이버에서는 `SRGGB12` 계열로 보고되지만 실제 하드웨어가 모노 센서처럼 동작할 수 있다.
- 2x2 nominal Bayer 블록을 평균하면 모노 센서에서 보이는 checker pattern이 줄어든다.
- 표시용 처리일 뿐, solver나 focus 측정용 raw 데이터를 바꾸지 않는다.

평평한 밝은 raw를 별도로 처리한 이유:

- 밝은 환경에서 raw가 거의 포화되면 1.0 percentile과 99.5 percentile이 같은 값이 될 수 있다.
- 이때 기존처럼 `high = low + 1`로 stretch하면 `(arr - low)`가 0이 되어 전체 화면이 검정으로 매핑된다.
- 포커스 화면의 raw fallback은 이미 밝은 배경으로 분류된 경우에만 사용하므로, percentile span이 없는 프레임은 검정이 아니라 밝은 프레임으로 표시한다.

### `update()` 표시 경로 변경

기존 흐름:

```text
camera_image copy -> resize_for_display -> _apply_stretch -> red mask -> screen
```

수정 후 밝은 배경일 때:

```text
shared_state.cam_raw -> 2x2 average -> percentile stretch -> orientation
-> resize_for_display -> red mask -> screen
```

수정 후 어두운 관측 프레임일 때:

```text
기존 camera_image 기반 focus stretch 경로 유지
```

실제 분기 조건:

- `display_image = raw_image`, `stretch_display = True`로 시작한다.
- `_stretch_black >= STRETCH_BRIGHT_BACKGROUND`이면 `_raw_display_image()`를 시도한다.
- raw fallback 이미지 생성에 성공하면 `display_image`를 raw 기반 이미지로 바꾸고 `stretch_display = False`로 설정한다.
- raw fallback을 만들 수 없으면 기존 이미지를 사용하되, `_apply_stretch()`의 밝은 배경 bypass 때문에 dark-sky stretch는 적용하지 않는다.
- 이후 공통으로 display 크기 resize, `L` 변환, red mask 적용 흐름을 통과한다.

### 기대 효과

- 포커스 화면에서 밝은 장면도 검정으로 눌리지 않는다.
- 8-bit 처리 프레임이 이미 포화되어도 raw 기반 표시 fallback으로 디테일을 볼 수 있다.
- 노출과 gain은 그대로 유지된다.
- 관측용 어두운 장면에서는 기존 포커스 화면 동작을 유지한다.
- Focus 화면에서 기존 `Exposure` shortcut처럼 `Gain` shortcut으로 `Camera Gain` 메뉴에 진입할 수 있다.

## `scripts/camera_lcd_preview.py`

PiFinder 본 서비스와 분리해서 카메라 raw 입력과 SSD1351 OLED 표시를 확인하기 위한 테스트 도구를 추가했다.

### 스크립트 성격

- PiFinder 런타임의 핵심 코드가 아니라 하드웨어 진단용 스크립트다.
- 카메라와 OLED를 직접 점유하므로 PiFinder 서비스와 동시에 실행하면 안 된다.
- 이후 LCD, SPI, 카메라 raw 입력을 빠르게 재검증할 수 있도록 저장했다.

### 주요 기능

- `Picamera2`를 직접 열어 raw stream을 캡처한다.
- `PiFinder.sqm.camera_profiles`의 camera profile을 사용해 crop/rotate를 적용한다.
- nominal Bayer 2x2 블록을 평균해 모노 표시 이미지를 만든다.
- percentile stretch로 LCD 표시용 8-bit 프레임을 만든다.
- temporal smoothing으로 표시용 노이즈를 줄일 수 있다.
- SSD1351 SPI 속도를 `--spi-hz`로 지정할 수 있다.
- 자동 노출은 `--auto-exposure`로 켤 수 있다.
- 마지막 표시 프레임을 `/tmp/camera_lcd_preview_latest.png`에 저장한다.

### 구현 세부

- 스크립트를 저장소 루트 밖에서 실행해도 `PiFinder` 패키지를 import할 수 있도록 `REPO_ROOT/python`을 `sys.path`에 추가한다.
- `--display ssd1351`일 때만 `DisplaySSD1351(bus_speed_hz=args.spi_hz)`를 직접 호출해 SPI 속도 테스트가 가능하게 했다.
- 카메라 설정은 `create_still_configuration({"size": (512, 512)}, raw={"size": profile.raw_size, "format": profile.format})`를 사용한다.
- 자동 노출을 켜면 `AeEnable=True`만 설정하고, 자동 노출을 끄면 `AnalogueGain`과 `ExposureTime`을 수동값으로 설정한다.
- raw 캡처는 `request.make_array("raw").copy().view(np.uint16)`로 가져오고, 노출/gain overlay에는 request metadata를 사용한다.
- `SIGINT`, `SIGTERM`을 처리해 카메라를 정리하고 종료한다.
- `--duration`이 0보다 크면 지정 시간 뒤 종료하고, 0이면 사용자가 중지할 때까지 계속 실행한다.
- snapshot 경로의 parent directory를 만들고, 최신 표시 프레임을 약 1초 간격으로 저장한다.

### 주요 옵션

```text
--display          기본값 ssd1351
--spi-hz           기본값 32000000
--auto-exposure    libcamera native AE 사용
--exposure-us      수동 노출 시간, 기본값 100
--gain             수동 analogue gain, 기본값 1.0
--fps              표시 갱신 제한, 기본값 2
--brightness       디스플레이 밝기
--denoise          표시용 temporal smoothing, 기본값 0.70
--min-contrast     표시 stretch 최소 contrast window, 기본값 256.0
--snapshot         최신 표시 프레임 저장 경로
--duration         지정 시간 후 종료, 기본값 0.0
--red              빨간 night-vision 표시
--no-overlay       FPS/노출/gain 오버레이 숨김
```

### 최종 권장 실행값

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

### PiFinder 복귀

```bash
sudo systemctl start pifinder
```

### 기대 효과

- PiFinder UI나 solver를 거치지 않고 LCD와 카메라를 직접 확인할 수 있다.
- OLED SPI 속도 문제와 카메라 입력 문제를 분리해서 볼 수 있다.
- 이번 작업에서 결정한 SSD1351 `32MHz` 값을 이후에도 쉽게 재확인할 수 있다.

## 자동 노출 — 검출 별 수 컨트롤러 옵션 (2026-07-25)

기존 매치 수 기반 자동 노출의 구조적 문제(원인 미구분, 카탈로그 의존,
밝은 하늘 가드 부재 — [mf_auto_exposure_methods_ko.md](mf_auto_exposure_methods_ko.md))
대응으로, cedar-server 방식의 검출 별 수 서보를 **옵트인 옵션**으로 추가했다.
기존 컨트롤러/복구/기본값은 무수정 유지. 설계:
[mf_auto_exposure_plan_ko.md](mf_auto_exposure_plan_ko.md), 결정:
[ADR m0020](../adr/m0020-star-count-controller-opt-in.md).

- 신규 `python/PiFinder/auto_exposure_starcount.py`:
  `ExposureStarCountController` (목표 검출 20, EMA α=0.5, 데드밴드 0.8~1.6,
  나눗셈 스텝, 앵커 ±3스톱 클램프, 중앙 ROI 평균>240 가드, <4개 앵커 폴백,
  검출 0 → 기존 사다리 재사용).
- `types/positioning.py`: `SolveDiagnostics.Centroids` 추가(모든 시도 게시).
- `solver.py`: 성공/실패 빌더에 `centroid_count` 배선(예외 경로는 0).
- `camera_interface.py`·`camera_pi.py`: `camera_exp`의 새 값
  `"auto_star"` 처리 — `set_exp:auto_star`로 star_count 컨트롤러 선택,
  디스패치 분기(lazy 생성). 별도 config 키 없음.
- `ui/menu_structure.py`·`ui/callbacks.py`: Camera Exp 메뉴에
  "Star" 항목 추가(라이브 노출 서픽스 포함) — 포커스 화면 마킹
  메뉴(롱키 → Exposure)에서 그대로 접근·전환 가능.
- i18n: de/es/fr/ko/zh "Star" 번역(AI-TRANSLATED 마커), .mo 재컴파일.
- 테스트: `tests/test_auto_exposure_starcount.py` 21종 + 기존 754 unit 통과.

## BT 페어링 중 WiFi 미복구 수정 (2026-07-26)

실장비 사고: 조이스틱(VR-PARK) 페어링 중 화면이 멈추고 SSH/웹이 죽은 채
복구되지 않아 재부팅. 페어링 자체는 성공해 있었다. WiFi 정지는 BLE 공존
문제 때문에 의도된 동작이지만(35초 상한 + 60초 워치독), 복구가 안 됐다.

원인 분석(이전 부팅 저널은 휘발되어 코드 검증으로 확정):

- 이 장비는 RTC가 없어(`timedatectl` RTC n/a) 부팅 후 GPS/NTP가 시계를
  맞출 때 `time.time()`이 점프한다. 페어링 화면의 모든 타임아웃
  (`pair_started` 기준 35초 WiFi 복구·90초 페어 타임아웃·완료 후 정리)이
  벽시계 기준이라, 뒤로 점프하면 전부 얼어붙는다 — 멈춘 화면과 WiFi 미복구
  증상 그대로. 60초 워치독(`sleep`은 단조 시계)이 남지만 재시도로 pause가
  반복되면 창이 계속 밀린다.
- 35초 복구는 페어링 화면의 update 루프 안에서만 검사되어, 화면이 갱신을
  멈추면(다른 화면 이동 등) 워치독 하나에만 의존하게 된다.

수정:

- `ui/bluetooth_keyboard.py`: 페어링 타이밍 전부 `time.monotonic()`으로 전환.
- `sys_utils.py`: `pause_wifi_for_bt_pairing()`이 프로세스 내
  `threading.Timer`(35초, `BT_PAIRING_WIFI_APP_RESUME_SECONDS`)로 복구를
  예약 — UI 루프 생존 여부와 무관하게 동작하고, resume은 멱등이라 정상
  경로 복구 후 발화해도 무해. 기존 60초 분리 프로세스 워치독은 유지(최후
  안전망).

참고: 페어링을 웹 원격(/remote)에서 시작하면 WiFi 정지로 조작 화면 자체가
끊긴다 — 공존 제약상 불가피하며, 페어링은 LCD에서 하는 것을 권장.

## 조이스틱 버튼 매핑 (2026-07-26)

블루투스로 페어링한 조이스틱/게임패드의 버튼을 PiFinder 기능에 매핑하는 기능.
libinput(`keyboard_pi.py`)은 조이스틱 클래스 장치를 무시하므로, 연결은 되어도
버튼 입력이 UI에 전달되지 않던 간극을 메운다.

- 신규 `python/PiFinder/joystick_input.py`: evdev 기반 리더 스레드(main 프로세스
  데몬). 3초마다 장치 재검색(BT 연결로 늦게 생기는 event 노드 대응), EV_KEY
  버튼과 ABS_HAT0X/Y 햇 축(십자키를 축으로 보내는 패드용, `HAT0X-` 형식 의사
  버튼) 처리. 순수 매핑/디스패치 로직은 `JoystickDispatcher`로 분리(테스트
  가능). 매핑은 config `joystick_mapping`({action: button_id})에 저장.
- 액션 두 계열(의도적 구분):
  - **키패드 계열** — 키보드 큐에 일반 키코드 주입: 상하좌우 화살표,
    GoTo(키패드 5와 동일 — Object Details에서 GoTo 시작, 다른 화면에서는
    키패드 5의 원래 의미 유지).
  - **마운트 계열** — 화면과 무관하게 mountcontrol 큐 직행: 수동 이동
    상/하/좌/우(north/south/west/east, LCD 가이드 화면과 동일한
    lease 1.2s + keepalive 0.4s 방식이라 리더가 죽어도 마운트가 스스로 정지),
    슬루 속도 +/-, 트래킹 Off(`set_tracking` 명령 신설 — 기존에 호출자 없던
    readback 확인형 `set_tracking()` 메서드를 큐에 배선). `mount_control`
    꺼져 있으면 마운트 계열은 무시.
- 신규 `python/PiFinder/ui/joystick.py`: Settings > Advanced > Joystick(조이스틱)
  메뉴 — 기능별 현재 바인딩 표시, 선택 시 캡처 모드(15초 내 누른 버튼 할당,
  한 버튼은 한 기능만 — 재할당 시 기존 기능에서 회수), "버튼 확인"(눌린 버튼
  id와 커널 이벤트 코드 숫자를 함께 실시간 표시 — 서로 다른 물리 버튼이 같은
  코드를 보내 이름이 겹치는 경우를 숫자로 구분, 캡처 모드라 기존 매핑 발동
  억제), "전체 지우기".
- `mf_pifinder_setup.sh`: python3-evdev 설치 추가(장비에는 설치 완료).
- i18n: ko 9개 문자열(조이스틱/버튼 확인 등), zh 游戏手柄, de/es/fr.
- 테스트: 신규 `tests/test_joystick_input.py` 12종(매핑 정규화, 키패드/마운트
  디스패치, keepalive, 방향 교체 시 이전 릴리스 무시, mount off 무시, 요청
  기능 전체 커버). 전체 799 unit 통과.

### 수동이동 8초 재전송 추가 (2026-08-07)

버튼/키를 계속 눌러도 조이스틱은 ~11초, LCD 가이드 화면은 ~10초에 이동이
멈추던 결함 수정. mountcontrol은 연속이동 상한
(`MANUAL_MOTION_MAX_CONTINUOUS_SECONDS` 10초)을 넘겨서는 keepalive로 lease를
연장해 주지 않으므로, 송신 측이 `manual_movement`를 주기 재전송해야 한다
(ui/base.py 가이드 키·pos_server는 준수, 이 두 곳은 keepalive만 보냄).

- `joystick_input.py`·`ui/indi.py`: `MANUAL_MOTION_RESTART_INTERVAL = 8.0`
  추가 — 홀드 중 8초마다 keepalive 대신 `manual_movement`를 재전송해 10초
  카운터를 리셋.
- 진단 과정에서 OnStep 펌웨어 자체의 ~7초 가이드 자동정지(전 경로 공통 원인)
  도 실측·확인 — 펌웨어 측에서 수정됨(2026-08-07). 펌웨어 수정 후 남아 있던
  조이스틱/LCD 11초 정지가 이 재전송 누락이다.
- 테스트: `test_joystick_input.py`에 장시간 홀드 재전송 1종 추가(13종).

## Bluetooth 설정 메뉴 — 조이스틱 지원, 이름 변경 (2026-07-26)

Settings > Advanced의 "Keyboard" 항목을 "Bluetooth"(ko: 블루투스)로 바꾸고,
키보드 전용이던 재연결 필터를 조이스틱/게임패드까지 넓혔다. 페어링/연결 UI
자체는 원래 장치 종류를 가리지 않았고, 키보드 전용이던 부분은 이름과 재연결
필터뿐이었다.

- `ui/menu_structure.py`·`ui/bluetooth_keyboard.py`: 메뉴/타이틀 "Bluetooth"로
  변경(레이블 `keyboard_settings`와 모듈/클래스명은 유지 — 코드 식별자까지
  바꾸면 diff만 커짐).
- `sys_utils.py`: `is_bluetooth_input_device()` 추가 — 키보드 키워드에 더해
  joystick/joypad/gamepad/controller와 흔한 컨트롤러 브랜딩(8BitDo, DualShock,
  DualSense, Joy-Con), 아이콘 `input-gaming`/`input-mouse` 인식. 재연결
  (`reconnect_bluetooth_keyboards`)과 부팅 자동 재연결이 이 필터를 사용.
  기존 `is_bluetooth_keyboard()`는 호환용으로 유지.
- i18n: "Bluetooth" msgid를 ko(블루투스)/zh(蓝牙)/de/es/fr에 추가, .mo 재컴파일.
- 테스트: 감지 필터 4종 추가(`tests/test_bluetooth_keyboard.py`), 전체 787
  unit 통과.

주의: 이 변경은 **연결 관리**(스캔/페어/자동 재연결)까지다. 연결된 조이스틱의
버튼 입력을 UI 키로 매핑하는 것은 별도 작업 — 현재 입력 계층(`keyboard_pi.py`)은
libinput 키보드 이벤트만 처리하며, libinput은 조이스틱 장치를 키보드로 분류하지
않는 경우가 많다(키보드 모드를 지원하는 컨트롤러는 예외).

## Locations 수정 폼 라벨 겹침 수정 (2026-07-26)

Location Management의 Edit Location 모달에서 라벨("Latitude (Decimal)" 등)이
서버에서 미리 채워진 값 위에 겹쳐 보이던 문제. Materialize는 라벨에 `active`
클래스가 있어야 값 위로 띄우는데, 수정 모달 라벨에 없었고 페이지에서
`M.updateTextFields()`도 호출하지 않았다.

- `views/locations.html`: 미리 채워지는 6개 라벨(name/lat/lon/alt/error/source)에
  `class="active"` 추가, DOMContentLoaded와 모달 `onOpenEnd`에서
  `M.updateTextFields()` 호출, DMS 전환(`toggleFormat`)이 프로그램적으로 채운
  필드도 같은 방식으로 라벨 활성화.
- `views/location_form.html`: 추가 폼의 미리 채워지는 2개 라벨
  (`error_in_m`=10, `source`="Manual Entry")에 `class="active"` 추가.

## LiveCam Raw Display 모드가 이름대로 동작 (2026-07-26)

프리뷰 모드가 `raw_display`인데도 밝기가 정규화된다는 지적으로 확인한 결과,
`DisplayFrameBuilder.build()`가 preview_mode와 무관하게 percentile stretch를
무조건 적용하고 있었다 — `raw_display`와 `stretched`가 코드상 완전히 동일했다
(모드 분기는 `bayer_2x2_average` 하나뿐). 설계 문서(`mf_raw_live_stack_plan_ko.md`
§설정 후보)에는 두 모드가 별개로 나열되어 있으나 구분이 구현되지 않은 상태였다.

- `raw_live_stack.py`: `raw_display`는 센서 비트심도 기반 고정 선형 매핑
  (`ADU × 255/(2^bit_depth−1)`, 비트심도는 raw 포맷명 "SRGGB12"에서 파싱,
  실패 시 dtype 폴백)으로 렌더링 — 게인/노출 변화가 화면 밝기에 그대로
  보인다. `stretched`/`bayer_2x2_average`는 종전 percentile stretch 유지.
  sum 스택은 고정 스케일에서 포화될 수 있음(합산의 본질) — sum 확인은
  stretched 모드 사용.
- `livecam_config.py`: `PREVIEW_MODE_RAW` 상수 추가.
- 테스트: 선형성(2배 신호→2배 픽셀)·stretched 정규화 유지·비트심도 폴백 3종
  추가, 전체 783 unit 통과.

## LiveCam 상태에 센서 적용값/RAW 레벨 표시 (2026-07-26)

게인을 바꿔도 LiveCam 화면에 변화가 없다는 관측을 실측으로 확인했다. 결론:
게인은 정상 적용되고 있었다(요청 1/15/20/30 → 드라이버 1.0/14.79/19.5/29.51,
RAW p50 332→3022). 프리뷰의 percentile stretch가 프레임 자체 히스토그램을 화면
전체 범위로 정규화하므로 **전역 곱(게인)은 수학적으로 표시에서 소거**된다 —
이미지가 안 변하는 게 정상이다. LCD 포커스 화면은 절대 스케일(bias 차감 →
digital gain → 255/4095)이라 변화가 보이고, 배경 앵커 EMA stretch가 게인 전환
직후 과도기 프레임을 이상하게 보이게 할 수 있다. 절전 모드에서는 프레임 갱신이
~1분 간격이라 전환 중의 오래된 프레임이 한동안 남는 것도 "이상함"의 큰 몫이다.

- `views/livecam.html`: 상태 패널에 "센서 (적용값)"(드라이버 보고 게인/노출)과
  "RAW 레벨 (p1/p50/p99.5)" 행 추가 — stretch가 지워버리는 게인/노출 변화를
  숫자로 확인할 수 있다. 데이터는 `/api/camera/raw-stack/status`의 frame 필드에
  이미 있었고 표시만 없었다.
- i18n: ko 두 문자열 추가(AI-TRANSLATED), .mo 재컴파일.

## 자동 노출 — 빠른 셔터속도 도달 (2026-07-26)

현장 관측(서울, imx462): 수동 25ms에서는 solve가 반복 성공하는데, `auto_star`로
바꾸면 그 셔터속도로 돌아가지 못했다. 노출을 위쪽에 묶어 두는 경계가 둘이었고
둘 다 완화했다. 결정: [ADR m0021](../adr/m0021-auto-exposure-reaches-fast-shutter.md)
(ADR 0010의 200ms 하한을 "첫 순회 한정"으로 축소).

- `auto_exposure.py` — 복구 사다리가 하한 아래를 탐색하지 않았다.
  ADR 0010은 "밝은 하늘에서도 200ms보다 짧게 가서 얻을 게 없다"는 전제로
  사다리를 `[400, 800, 1000, 200]ms`로 깔았는데, 광害가 심한 곳에서는 이 전제가
  깨진다(이 현장은 25ms에서 solve, 400ms~1s에서는 검출 0). 결과적으로 400ms↔1s를
  무한 왕복했다(실측). 이제 긴 rung을 한 바퀴 다 실패하면 같은 rung을 재생하지 않고
  `[100, 50, 25]ms`로 이어서 내려간다. 어두운 하늘의 흔한 경로(8회)는 그대로다.
- `auto_exposure_starcount.py` — 앵커 경계가 구조적으로 도달 불가였다.
  조정폭은 앵커/8~앵커×8로 제한되는데 앵커 초기값은 400ms 추정치이고 데드밴드에
  들어와야만 갱신된다. 실제 적정 노출이 앵커/8보다 짧으면(25ms < 400ms/8 = 50ms)
  서보는 매 프레임 더 짧게 요청하지만 50ms에 고정되고 "변경 없음"만 반환한다 →
  데드밴드에 못 들어가니 앵커도 영영 안 바뀐다(실측: 정확히 50000µs에 정착).
  이제 같은 방향으로 `reanchor_after`(3)회 연속 클램프되면 앵커가 그 경계를 따라
  이동한다. 클램프가 풀리거나 방향이 바뀌면 streak은 리셋되므로, 이상 프레임 한
  장이 노출을 튀게 하지는 못한다.
- `auto_exposure_starcount.py`·`auto_exposure.py` — 제어 법칙의 모든 갈래가
  위로만 움직였다. 위 두 경계를 풀고 debug 로그로 다시 관측하니 닫힌 순환이었다:
  `1s → 밝은 하늘 가드(중앙 ROI 평균 251>240) → 앵커 400ms → 검출 0 → 복구 상승
  → 800ms → 6~8개(목표 20 미달) → 1s → 가드 …`. 두 갈래를 뒤집었다.
  (1) 밝은 하늘 가드가 앵커 복귀 대신 **노출을 절반으로** 내리고 그 지점을
  ceiling으로 기억한다. 이후 상승은 ceiling으로 막혀 방금 sky glow로 판명된
  노출로 되돌아가지 못한다. ceiling은 데드밴드 진입, 또는 확실히 어두운 프레임
  (평균 < `bright_clear_mean` 120)에서 해제된다 — 240에서 걸고 120에서 푸는
  간격이 히스테리시스이고, 가드 임계 바로 아래에서 풀면 순환이 되살아난다.
  (2) 검출 0 복구는 프레임이 sky glow 한계임을 아는 호출자에 한해 하강 사다리
  `[200, 100, 50, 25]ms`를 쓴다. prior는 활성화 시점에 한 번만 정해지고, 한
  바퀴 실패하면 반대편 rung으로 확장한다. 매치 수 컨트롤러는 프레임 밝기 신호가
  없어 기존 야간 사다리 그대로다.
- 테스트: `tests/test_auto_exposure.py`에 사다리 에스컬레이션·밝은 사다리 6종
  (+기존 wrap 테스트를 새 동작으로 갱신), `tests/test_auto_exposure_starcount.py`에
  재앵커링·ceiling 7종 추가. 전체 783 unit 통과.
- 실장비 검증(서울, imx462): `auto_star` 전환 후 복구 사다리가
  `400→800→1000→200 → [확장] → 100→50→25ms`로 실제 25000µs까지 하강하는 것을
  로그로 확인. 이전에는 400ms↔1s를 무한 왕복했다.

## LiveCam 카메라 설정 확정 저장 (2026-07-25)

LiveCam 페이지에서 설정을 바꾸고 다른 페이지를 보고 돌아오면 값이
원복되던 문제를 수정했다. 원인은 두 가지였고, 첫 번째가 근본 원인이다.
게인이 저장되지 않는 것은 의도된 동작이라 그대로 두었다(재시작 시 카메라
프로파일 기본값 복귀, 저장은 Exp Save 항목만). 나중에 결함으로 오인해
"고치는" 일이 없도록 `set_gain` 처리부에 의도를 주석으로 남겼다.

- `config.py` — 프로세스 간 config 덮어쓰기(근본 원인).
  `config.json`은 main/UI·카메라·웹 세 프로세스가 각자 시작 시점에 읽은
  `Config` 인스턴스로 공유한다. `set_option()`이 자기 메모리 사본을 그대로
  파일에 덮어써서, 한 프로세스가 값을 저장하면 그 사이 다른 프로세스가 바꾼
  키가 전부 되돌아갔다(카메라 프로세스가 `camera_exp`를 저장하면 웹이 방금
  쓴 LiveCam 설정이 원복). 이제 쓰기 직전에 파일을 다시 읽어 병합한
  뒤(`_refresh_from_disk()`) 저장하고, 임시 파일 + `os.replace()`로
  원자적으로 기록해 다른 프로세스가 쓰다 만 파일을 읽지 않게 했다.
  `reset_filters()`도 같은 병합을 거친다. 파일이 깨져 있으면 메모리
  사본을 유지해 한 번의 저장이 전체 초기화가 되지 않도록 했다.
- `camera_interface.py` — Auto 노출 모드가 저장되지 않던 문제.
  수동 노출만 `camera_exp`에 기록하고 `set_exp:auto`/`auto_star`는 기록하지
  않아서, 카메라는 auto로 돌아도 config·UI는 이전 수동값을 계속 보여주고
  재시작하면 auto 선택이 사라졌다. 이제 auto 모드도 저장한다
  (`set_exp:native`는 주간 정렬용 임시 모드이므로 종전대로 저장하지 않음).
- `config.py` — 저장된 값을 읽는 쪽도 갱신되지 않던 문제.
  LiveCam에서 Star를 선택하면 config와 카메라는 바뀌는데, main/UI 프로세스는
  시작 시 읽은 `Config`를 계속 써서 Camera Exp 메뉴 체크와 포커스 화면 서픽스가
  이전 노출을 그대로 보여줬다(누가 `load_config()`를 호출할 때까지). 이제
  `get_option()`이 파일의 `(mtime, size)` 변화를 보고 다시 읽는다. 재확인은
  `REFRESH_INTERVAL`(0.25초) 간격으로 제한해 draw 루프에서 매 호출 `stat`을
  하지 않는다(실측 1.64µs/호출). equipment/locations는 종전대로 메모리 객체를
  쓰며 명시적 `load_config()`에서만 재구성한다.
- `api_extensions.py` — 웹 Apply의 config 기록 시점이 LCD 메뉴와 달랐다.
  Camera Exp 메뉴는 선택 즉시 `config_option`을 쓰고(`ui/text_menu.py`)
  post_callback이 `set_exp`를 큐에 넣는데, 웹은 명령만 넣고 기록은 카메라
  프로세스가 큐를 비울 때까지 미뤄졌다. 유휴 상태(저전력 sleep)에서는 카메라가
  ~60루프에 한 번만 큐를 비우므로 그때까지 페이지·메뉴 모두 이전 노출을
  보여줬다. 이제 메뉴와 같은 순서로 노출을 먼저 기록하고 명령을 넣는다.
  게인은 Camera Gain 메뉴도 config에 쓰지 않으므로 그대로 기록하지 않는다.
- 테스트: 신규 `tests/test_config.py` 10종(교차 프로세스 병합·원자적 쓰기·
  손상 파일·읽기 갱신·재확인 간격), 신규 `tests/test_api_camera_controls.py`
  7종(Flask 테스트 클라이언트로 실제 엔드포인트 구동 — 노출 즉시 기록·큐잉,
  게인 미기록, 클램프값 기록, 잘못된 값은 무변경), 전체 771 unit 통과.
  실장비 검증: 서비스 재시작 후 `auto_star` 유지, 카메라 노출 저장 시 LiveCam
  `low_percentile` 유지, 장수명 `Config` 리더가 웹 변경 반영, Apply 직후
  카메라가 큐를 비우기 전에도 페이지·config가 새 노출을 보고함.

## 헤드리스 콘솔 부팅 — wf-panel-pi CPU 점유 제거 (2026-07-30)

모니터 없이 운용하는 장비에서 데스크톱 세션의 Wayland 패널(`wf-panel-pi`)이
디스플레이 미부착 상태에서 렌더링 busy-loop에 빠져 코어 1개를 상시 점유했다
(실측 CPU 95%+, 부팅 5시간 동안 누적 290분). PiFinder는 데스크톱과 무관한
systemd 서비스로 동작하므로 데스크톱 부팅 자체를 끈다.

- `mf_pifinder_setup.sh`: "Disable unwanted services" 섹션에 콘솔 자동로그인
  전환 추가 — `raspi-config nonint do_boot_behaviour B2`(콘솔 자동로그인),
  `raspi-config`가 없으면 `sudo systemctl set-default multi-user.target` 폴백.
  재부팅 시점부터 적용된다.
- 실장비 적용(2026-07-30): 부팅 타겟 `multi-user.target` 전환 후
  `systemctl isolate multi-user.target`으로 데스크톱 세션 즉시 종료.
  load average 3.6 → 1.9, `pifinder`/`cedar_detect` 서비스 정상 유지 확인.
- 데스크톱이 다시 필요하면 `sudo raspi-config nonint do_boot_behaviour B4`
  (데스크톱 자동로그인)로 되돌린다. VNC 데스크톱을 쓰려는 경우에도 마찬가지.

## WiFi 복구 도구 — BT 코엑스 펌웨어 웨지 대응 (2026-08-05)

BT 조이스틱 페어링 중 STA가 죽고 재부팅 2회로만 복구된 사건(당일 실측
분석)의 대응책. CYW43455 단일 2.4GHz 라디오를 WiFi/BT가 공유하는 구조에서
BT 고밀도 국면(페어링·부팅 직후 재연결 폭풍)이 brcmfmac 펌웨어의 STA 상태
머신을 웨지시킬 수 있고, 이는 서비스 재시작으로 복구 불가(커널/펌웨어
계층)다.

- `scripts/mf_wifi_recover.sh`: 유닛 정지(monitor/hostapd/dnsmasq,
  NetworkManager) → uap0 삭제 → brcmfmac_wcc/brcmfmac/brcmutil 리로드
  (칩 펌웨어 리셋) → 부팅 순서로 복원(NM→prepare→AP 유닛) → 상태 보고.
  로그: `PiFinder_data/wifi_recover.log`. 실측: 정상 상태에서 전체 사이클
  11초, STA 즉시 재접속·AP 복구, SSH 세션 생존.
- LCD: Settings > Advanced > **WiFi Recover** (Confirm/Cancel, shutdown과
  동일 패턴). `callbacks.recover_wifi` → `sys_utils.recover_wifi()`
  (예외 격리, 실패 시 "WiFi still down" 표시). 실기기 화면 확인 완료.
- i18n: 신규 msgid 4건 5개 언어 번역(AI-TRANSLATED).
- 현장 수칙(사건 분석에서 도출): 페어링은 집에서, 조이스틱 켠 채 재부팅
  금지, 마운트(AP 2.4GHz 클라이언트)는 ESP32 계열이라 5GHz 이전 불가 —
  AP+STA 동일 채널 제약으로 STA도 2.4GHz 고정.

## 보류 업스트림 2건 이식 — SQM 색보정(#560), Focus 멀티스타(#531) (2026-08-05)

동기화 라운드에서 보류했던 마지막 2건을 "MF 수정 우선" 원칙으로 이식했다.
판단 근거·상세는 [mf_upstream_patch_reference_ko.md](mf_upstream_patch_reference_ko.md)
2026-08-04 섹션의 해당 항목(적용 완료로 갱신됨) 참조.

- **#560 (`fde9beaa`)**: 하늘색 기반 radiometric zero point — 함정이었던
  모노 오검출을 `_mosaic_phase_is_rggb`의 `profile.mono` 선행 거부로 차단.
  imx462 SQM은 상수 zero point 유지(~+0.74 mag 왜곡 방지), MF 회귀 핀
  테스트로 고정. upstream 상수 재적합(15.25→15.159)으로 발행 SQM이
  −0.09 mag 이동하는 것은 수용(보정 개선).
- **#531 (`b7fa9e8a`)**: Focus 화면 4모드 재작성 수용 + MF 기능 3종
  (가이드 키, Gain 마킹메뉴, 주간 raw 렌더→Image 모드) 재구현.
  `positioning.py` 전체 채택이 MF 필드를 지우는 것을 테스트로 잡아 복원.
  문서는 post-#546 상태로 수렴(#546/#547 종결). 헤드리스 실기 검증:
  4모드 렌더, GAIN 마킹메뉴 진입, 디버그 카메라 솔빙 정상.
- 전체 스위트 1,105건 통과. 남은 미이식은 i18n 문자열 래핑 5곳(#562)뿐.

## SSD1333 4축 밝기 이식 (upstream #568+#570 부분 이식, 2026-08-05)

SSD1333(176×176) 채택 계획이 확정되어, 보류했던 upstream 밝기 재설계를
부분 이식했다. 커밋 `0cb8314e`(#568)+`31f0a5c5`(#570) — pre-charge 전압을
4번째 밝기 축으로 추가하고, by-eye 감마 대신 실측 응답 표면 기반 knee
커브·테이블 룩업으로 dimming 정책을 재적합한 것.

- 수용: `displays.py`/`ssd1333_device.py` 드라이버, 밝기 테스트 17건,
  모델 문서(ADR 0023 개정, `docs/ax/display/` CONTEXT+response).
- 제외: 측정 저널 44개, 러너 스크립트, 벤치 하네스
  (`panel_photometry`/`precharge_sweep`) ~6,250줄 — 광도계 리그가 있어야
  도는 단독 도구라 우리 패널 재특성화가 필요해질 때 upstream에서 가져온다
  (response 문서 상단 MF note로 안내).
- MF 우선 보존 확인: `display_spi()` Pi5 헬퍼, `__init__(bus_speed_hz)`
  (SSD1333 40MHz), MF `rotate=0`, `get_display(spi_speed_hz)`, 디스플레이
  자동감지 무접촉 — 병합 후 마커 전수 재확인.
- 부수 수리: `test_hardware_detect_display.py`가 7월 `get_i2c` 전환
  (cc7ae95e)을 안 따라가고 옛 `board` 속성을 패치한 채 방치돼 있었다
  (pytest 마커가 없어 `-m "smoke or unit"` 전체 실행에서 항상 제외 —
  그래서 안 보였음). `get_i2c` seam 기준으로 재작성하고 unit 마커 부여,
  프로브 예외 폴백 테스트 추가. 전체 1,047건 통과.
- 현 기기(SSD1351)에서는 동작 무변화. SSD1333 패널 연결 시: rev4 보드가
  아니면 BQ25895 마커가 없어 자동감지가 ssd1351로 남으므로, 서비스
  ExecStart에 `--display ssd1333`을 지정해야 한다.

## 릴리즈 체크를 포크 기준으로 전환 (2026-08-05)

Software 화면의 릴리즈 확인이 brickbots의 `release/version.txt`를 보고 있어서,
upstream이 2.6.1을 발행하면 포크 기기에 남의 릴리즈 기준 "Update Now"가 뜨는
상태였다 (2026-08-04 동기화 조사에서 미결로 기록). NixOS 마이그레이션 게이트
(`migration_gate.json`)도 같은 문제 — upstream이 `nixos_for_everyone`을 켜면
이 포크가 제외한 NixOS 마이그레이션이 원격으로 트리거될 수 있었다.

- `ui/software.py`: 두 URL 모두 `hjoungjoo/MF_PiFinder`의 release 브랜치로
  전환. 포크는 아직 release 브랜치가 없으므로 둘 다 404 (실측 확인).
- **"Unknown" 표시 분기 추가**: `update_needed()`는 파싱 불가 입력에
  의도적으로 True를 반환하므로(업스트림 테스트 고정), fetch 실패(네트워크
  다운 또는 release 미발행)가 곧장 "Update Now"로 이어졌다. 이제 릴리즈
  버전이 "Unknown"이면 "Release info / unavailable"을 표시하고 업데이트를
  권하지 않는다.
- i18n: 신규 msgid 2건(`Release info`, `unavailable`) 5개 언어 번역
  (AI-TRANSLATED 마커).
- 릴리즈를 낼 때는 release 브랜치에 `version.txt`만 있으면 체크가 그대로
  동작한다. `pifinder_update.sh`는 이미 origin(포크)의 release를 pull하므로
  수정 불요.
- **버전 체계 (같은 날 후속)**: upstream 버전과 구분하기 위해 `version.txt`를
  `m` 접두사 체계로 전환 (`2.6.0` → `m2.6.0`, 사용자 결정). 표시 경로
  (스플래시/웹/API)는 문자열 그대로라 무영향. `update_needed()`는
  `_semver_tuple()`로 분리하며 `m` 접두사를 벗기고 비교 — 안 벗기면
  `int("m2")` 예외가 오류 편향(True)으로 흘러 **업데이트 직후에도 영원히
  "Update Now"가 뜨는** 문제가 있었다. 접두사 케이스 테스트 4건 추가
  (동일 버전=False가 핵심). 마이그레이션 게이트는 버전 문자열이 아니라
  마커 파일 기준이라 무영향 확인.

## 설치 스크립트 이름 정리 — `pifinder_setup.sh`가 포크 설치본 (2026-08-04)

포크 설치본이 `mf_pifinder_setup.sh`라는 별도 이름으로 있어서, 저장소를 받아
`pifinder_setup.sh`를 실행하면 upstream 릴리즈가 설치됐다. 대표 이름을 포크
설치본에 넘긴다.

- `mf_pifinder_setup.sh` → `pifinder_setup.sh`(포크 설치본이 기본).
- upstream 설치본은 `pifinder_setup.sh.bak`으로 보존 — upstream 재동기화 시
  비교 기준으로 쓴다.
- **클론 브랜치 수정**: `--branch mf_pifinder` → `--branch main`.
  `mf_pifinder` 브랜치는 origin(hjoungjoo/MF_PiFinder)에 더 이상 없어서,
  이 스크립트로 설치하면 clone 단계에서 실패하는 상태였다. 헤더의 설치 명령
  URL도 `main/pifinder_setup.sh`로 갱신.
- `python/tests/test_wifi_apsta_static.py`가 `pifinder_setup.sh`를 직접 읽는다 —
  새 내용에서도 6건 전부 통과(AP+STA 프로비저닝 동일).

## 문서 파일

### `docs/mf_dev/mf_bookworm_install_ko.md`

CM5 Bookworm 64-bit 설치 절차를 기준으로, `mf_pifinder` 브랜치의 Bookworm 설치
흐름을 한국어로 정리했다.

PiFinder 관련 포함 내용:

- PiFinder 저장소 위치와 branch
- PiFinder 의존성 설치
- PiFinder systemd 서비스 설치
- PiFinder 데이터 디렉터리 구성
- `pifinder`가 아닌 custom OS username/hostname 설치
- PiFinder 개발자 모드 테스트 명령
- PiFinder 주변기기 확인 명령
- CM5 Bookworm에서 PiFinder가 주의해야 할 boot config 경로

### `docs/mf_dev/mf_bookworm_install_en.md`

`mf_bookworm_install_ko.md`의 영문판이다.

### `docs/mf_dev/mf_change_history_ko.md`

현재 문서다. PiFinder 소스 수정 사항을 파일별로 상세 기록한다.

### `docs/mf_dev/mf_change_history_en.md`

소스 수정 히스토리의 영문판이다.

### `docs/mf_dev/mf_pifinder_new_device_tasks_ko.md`

새 Raspberry Pi 디바이스에서 `mf_pifinder` 브랜치를 설치하고 검증하기 위한
한국어 체크리스트다.

### `docs/mf_dev/mf_pifinder_new_device_tasks_en.md`

`mf_pifinder_new_device_tasks_ko.md`의 영문판이다.

### `docs/mf_dev/mf_pifinder_rpi4_pi5_compatibility_ko.md`

Pi4/Pi5/CM5 보드 profile, 자동 설정값, 검증 절차를 한국어로 요약한 문서다.

### `docs/mf_dev/mf_pifinder_rpi4_pi5_compatibility_en.md`

`mf_pifinder_rpi4_pi5_compatibility_ko.md`의 영문판이다.

## 최종 동작 기준

현재 소스 기준으로 기대하는 PiFinder 동작은 다음과 같다.

- Bookworm에서는 PiFinder 코드가 `/boot/firmware/config.txt`를 우선 사용한다.
- Legacy 계열에서는 `/boot/config.txt` fallback이 유지된다.
- 설치/업데이트 스크립트는 현재 OS user의 `$HOME/PiFinder`, `$HOME/PiFinder_data`를 기준으로 동작한다.
- systemd와 Samba 설정은 설치 시 실제 OS user/home 경로로 렌더링된다.
- Raspberry Pi OS 설치 시 hostname을 장비별로 다르게 정하면 `<hostname>.local` mDNS 충돌을 줄일 수 있다.
- IMX462는 imx290으로 강제 변환하지 않고 직접 overlay로 다룰 수 있다.
- SSD1351 OLED 기본 SPI 속도는 `32MHz`다.
- SPI 장치가 `/dev/spidev10.0`으로 잡혀도 디스플레이 초기화가 가능하다.
- Pi camera 최초 gain은 원본처럼 카메라 프로파일의 `analog_gain`을 사용한다.
- `Camera Gain` 메뉴에서 runtime gain을 조정할 수 있고 `Profile`로 원본 기본 gain에 복귀할 수 있다.
- `GPS Settings > GPS Port`에서 gpsd serial device를 선택할 수 있다.
- 이 CM5 장비의 현재 GPS 포트는 `/dev/ttyAMA2`, baud는 `115200`이다.
- `Settings > Advanced > Keyboard`에서 Bluetooth 키보드 스캔/연결을 시도할 수 있다.
- USB 키보드와 Bluetooth 키보드는 기본 `keyboard_pi` libinput 경로로 PiFinder 입력에 매핑된다.
- USB/Bluetooth 키보드의 일반 알파벳은 검색/텍스트 입력 화면에서 실제 문자로 입력된다.
- USB/Bluetooth 키보드의 `Alt` 조합은 `ALT_*`로 처리된다.
- USB/Bluetooth 키보드의 `Left`, `Right`, `Enter/KP Enter`는 1초 이상 누르면 long key로 처리된다.
- USB/Bluetooth 키보드의 `Up`, `Down`은 1초 이상 누르면 일반 `UP/DOWN` 반복 입력으로 처리된다.
- USB/Bluetooth 키보드의 `Shift` 또는 `Ctrl` 조합 long key shortcut은 호환용으로 유지된다.
- paired/trusted Bluetooth 키보드는 PiFinder 서비스 시작 시 백그라운드에서 자동 재접속을 시도한다.
- `Settings > User Pref... > Language`에서 `한국어`를 선택할 수 있다.
- 한국어 UI는 Sarasa CJK 폰트를 사용하며, 언어 변경 직후 PiFinder를 재시작해 폰트를 다시 로드한다.
- 한국어 메뉴에서도 키보드 문자 입력은 현재 영문 알파벳 입력만 지원한다.
- 밝은 장면의 Focus 화면은 raw 기반 표시 fallback을 사용한다.
- 어두운 관측 장면의 Focus 화면은 기존 focus stretch 흐름을 유지한다.
- `scripts/camera_lcd_preview.py`로 PiFinder와 분리된 카메라-to-LCD 진단이 가능하다.

## Pi4 Bookworm 호환성 후속 수정

Raspberry Pi 4 Bookworm 64-bit 실기 테스트에서 CM5용 GPS 포트 기본값이 Pi4와
맞지 않는 문제가 확인되어 보드별 자동 GPS 포트 선택을 추가했다.

- `default_config.json`의 `gps_port` 기본값을 `auto`로 변경했다.
- `python/PiFinder/board_config.py`를 추가해 `pi5_class`, `pi4`, `legacy` profile로
  보드별 UART overlay와 GPS 기본 포트를 정의했다.
- `sys_utils.get_default_gpsd_device()`는 `board_config` profile을 통해 CM5/Pi5는
  `/dev/ttyAMA2`, Pi4는 `/dev/ttyAMA3`, 그 외 보드는 `/dev/ttyAMA1`을 선택한다.
- `pifinder_paths.sh`도 같은 `pi5_class`/`pi4`/`legacy` profile helper를 사용해
  설치 시 UART overlay와 gpsd `DEVICES` 초기값을 정한다.
- `GPS Settings > GPS Port` 메뉴에 `Auto`와 `/dev/ttyAMA3` 항목을 추가했다.
- 설치 스크립트도 같은 보드 판별을 사용해 `/etc/default/gpsd`의 `DEVICES`를
  초기 설정한다.
- Pi4 테스트 장비에서는 `gpsd`가 `/dev/ttyAMA3`, 115200bps에서 u-blox 수신기를
  인식했다. 실내 테스트라 GPS fix는 아직 없고, 야외 안테나 테스트가 남아 있다.
- Bookworm BlueZ에서 `bluetoothctl paired-devices`가 동작하지 않아 Bluetooth
  장치 조회 명령을 `bluetoothctl devices Paired`로 변경했다.
- 테스트한 `K06 BLE Keyboard`는 paired/trusted/connected 상태에서도 기본 설정에서는
  `/dev/input/event*`가 생성되지 않았다.
- `/etc/bluetooth/input.conf`에서 `UserspaceHID=true`, `LEAutoSecurity=true`를
  활성화하고 Bluetooth 데몬을 재시작하자 `/dev/input/event4`가 생성됐고,
  `libinput debug-events`에서 방향키 입력을 확인했다.
- 설치 스크립트가 새 설치 시 같은 BlueZ input 설정을 적용하도록 반영했다.
- `docs/mf_dev/mf_pifinder_rpi4_pi5_compatibility_ko.md`를 추가해 Pi4/Pi5/CM5 보드별
  profile, 설치 시 적용값, 확인 절차를 한 문서에 정리했다.

## 검증한 항목

소스 수준 검증:

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

한국어 locale 검증:

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

PiFinder 서비스 수준 확인:

```bash
systemctl status pifinder --no-pager --full
journalctl -u pifinder -n 80 --no-pager
```

화면/API 확인:

```bash
curl -fsS http://127.0.0.1/api/screen -o /tmp/pifinder_screen.png
curl -fsS http://127.0.0.1/api/camera/raw -o /tmp/pifinder_camera_raw.png
```

이 검증 명령들은 문서 기록용이며, 이 문서는 OS 설치나 하드웨어 조립 절차를 다루지 않는다.
