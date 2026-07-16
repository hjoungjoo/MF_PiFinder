# MF_PiFinder upstream 패치 기준 문서

작성일: 2026-07-03

이 문서는 `brickbots/PiFinder` 원본 소스가 변경되었을 때 `mf_pifinder`
브랜치에 다시 적용하거나 유지해야 할 패치 내용을 빠르게 판단하기 위한 기준 문서이다.

목표:

- upstream 변경을 가져올 때 이미 적용한 패치와 의도적으로 제외한 패치를 구분한다.
- 충돌 가능성이 높은 파일과 기능 경계를 미리 확인한다.
- 다음 재동기화 작업에서 테스트와 검토 순서를 재사용한다.

## 현재 기준점

로컬 기준 브랜치:

- `mf_pifinder`

비교 대상 upstream:

- `brickbots/PiFinder main`

2026-07-03 기준 최근 반영 상황:

- upstream selected commits applied:
  - NixOS PR build CI
  - case/accessory STL changes
  - observing list CSV import improvements
  - UTC-aware datetime handling
  - Set Time/Date self-gate when no location lock exists
  - OBJ_TYPES single-source refactor
- local MF-only patch:
  - SSD1333 automatic display detection, separated from the larger Rev-4 hardware patch

2026-07-13 추가 반영:

- upstream selected commits applied:
  - Stellarium 2.0 observing list import (#527, `39412ac`)
  - catalog filter cache: skip re-filtering unchanged catalogs on list open (#526, `f704a26`)
  - UBlox GPS NAV-SVINFO/NAV-SAT 디코딩 수정 (#524, `9cb0060`) — `gps_ubx_parser.py`/
    테스트는 clean 적용, `gps_ubx.py`는 NAV-PVT 핸들러에서 MF 시간 처리와 upstream
    numSV used-count를 둘 다 유지하도록 수동 병합
- 검토 후 이번엔 제외:
  - NixOS 마이그레이션 3건 (#523 `e22ac48`, #521 `0621d15`, #517 `02e6b30`) — MF의
    NixOS 이관 지원 여부 미결정
  - state datetime tz 수정 (#508, `e64f0b6`) — timez.py 기준 이미 반영됨, 재적용 시
    제외한 Rev-4 state.py 변경이 딸려오므로 하지 않음
  - Rev-4 hardware enablement (#498, `e82b809`) — 정책상 제외 유지

주의:

- 이 문서는 전체 변경 히스토리 문서가 아니다.
- 상세 기능 기록은 `docs/mf_change_history_ko.md`를 참고한다.
- 이 문서는 upstream 재동기화와 패치 재적용 기준에 집중한다.

## upstream에서 이미 반영한 변경

다음 upstream 변경은 `mf_pifinder`에 반영되어 있다.

| 영역 | 상태 | 비고 |
| --- | --- | --- |
| NixOS PR build CI | 적용됨 | 런타임 영향 없음. GitHub Actions와 manifest script 추가 |
| case/accessory files | 적용됨 | 코드 영향 없음. STL/JPG/README 변경 |
| Observing list CSV import | 적용됨 | `obslist_formats.py`, docs, tests 적용 |
| Observing list Stellarium 2.0 import | 적용됨 | #527 `39412ac`. `obslist_formats.py` Stellarium reader 확장 |
| catalog filter cache | 적용됨 | #526 `f704a26`. `catalog_base.py` 추가, 변경 없는 카탈로그 재필터 생략 |
| UBlox GPS NAV-SVINFO/NAV-SAT 디코딩 수정 | 적용됨 | #524 `9cb0060`. `gps_ubx_parser.py` 오프셋 수정, `gps_ubx.py` NAV-PVT 수동 병합 |
| UTC-aware datetime | 적용됨 | `timez.py` 추가, `state.py`, `server.py`, callback 시간 처리 변경 |
| Set Time/Date self-gate | 적용됨 | 위치 lock이 없으면 수동 시간/날짜 설정 UI가 inert 상태로 메시지 표시 |
| OBJ_TYPES single-source | 적용됨 | Type filter menu가 `OBJ_TYPES`에서 생성됨 |

이 변경들은 다음 upstream sync 때 중복 적용하지 않는다.

## upstream에서 의도적으로 제외한 Rev-4 하드웨어 변경

upstream의 Rev-4 hardware enablement 패치는 아직 전체 적용하지 않았다.

제외한 기능:

- BQ25895 battery telemetry
- BQ25895 fast-charge runtime configuration writes
- sound/earcon buzzer subsystem
- GPIO15 hardware power button
- GPIO14 gpio-poweroff latch
- battery titlebar icon
- Raspberry Pi red power LED shutdown

제외 이유:

- Rev-4 전용 GPIO/I2C/PWM 가정이 Pi4/Pi5/CM5 호환 경로에 영향을 줄 수 있다.
- GPIO14 poweroff latch는 하드웨어 배선이 맞지 않으면 위험할 수 있다.
- sound/earcon은 관측 환경에서 기본 OFF 정책이 필요하다.
- battery charger write 동작은 하드웨어 검증 후 별도 옵션으로 넣는 것이 안전하다.

부분 적용한 기능:

- SSD1333 display auto-detection only

현재 구현:

- `python/PiFinder/hardware_detect.py`
- `python/PiFinder/main.py`
- `python/PiFinder/splash.py`
- `python/tests/test_hardware_detect_display.py`

동작:

- BQ25895 I2C address `0x6A` ACK를 Rev-4/SSD1333 display marker로 사용한다.
- 감지 성공 시 기본 display hardware는 `ssd1333`이다.
- 감지 실패, Blinka import 실패, GPIO/I2C 접근 실패 시 기존 기본값 `ssd1351`로 fallback한다.
- `--display` 명령행 옵션이 있으면 자동 감지보다 우선한다.

다음에 Rev-4 변경을 추가로 가져올 때:

- battery/sound/power/latch를 한 번에 병합하지 않는다.
- `HardwareCapabilities` 같은 공통 타입을 추가하더라도 기존 `hardware_detect.py`의
  import-safe fallback을 유지한다.
- GPIO14 poweroff latch는 별도 설치 옵션과 명확한 문서가 필요하다.

## MF 전용 주요 패치 영역

다음 영역은 upstream에 아직 없거나 MF 브랜치에서 다르게 동작한다.
upstream 변경 시 이 기능들이 깨지지 않는지 우선 확인한다.

### Platform / Bookworm / Pi4-Pi5-CM5

주요 파일:

- `pifinder_paths.sh`
- `pifinder_setup.sh`
- `pifinder_update.sh`
- `pifinder_post_update.sh`
- `python/PiFinder/board_config.py`
- `python/PiFinder/boot_config.py`
- `python/PiFinder/sys_utils.py`
- `python/PiFinder/displays.py`
- `pi_config_files/*.service`

보존해야 할 정책:

- Bookworm boot config는 `/boot/firmware/config.txt` 우선, legacy는 `/boot/config.txt`.
- `PiFinder_data`와 systemd/Samba 경로는 현재 OS 사용자 기준으로 렌더링한다.
- Pi4/Pi5/CM5 보드 profile에 따라 GPS UART default가 달라진다.
- Pi5/CM5는 OLED CS 충돌을 피하기 위해 `uart2-pi5` 경로를 사용한다.
- SPI 장치는 `/dev/spidev0.0`과 `/dev/spidev10.0` 모두 지원한다.

### Camera / Focus / Gain

주요 파일:

- `python/PiFinder/camera_interface.py`
- `python/PiFinder/ui/preview.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/PiFinder/ui/callbacks.py`
- `scripts/camera_lcd_preview.py`

보존해야 할 정책:

- focus preview와 camera gain runtime/profile 설정을 유지한다.
- LCD preview script는 하드웨어 디버깅용으로 유지한다.
- upstream camera 변경 시 exposure/gain menu callback 충돌을 확인한다.

### Korean localization

주요 파일:

- `python/locale/ko/LC_MESSAGES/messages.po`
- `python/locale/ko/LC_MESSAGES/messages.mo`
- `python/PiFinder/ui/fonts.py`
- `python/PiFinder/ui/menu_structure.py`

보존해야 할 정책:

- 언어 메뉴에서 `ko`를 유지한다.
- CJK font와 restart 안내 흐름을 유지한다.
- upstream i18n 업데이트 후 Korean `.po` drift를 확인한다.

### Bluetooth / USB HID keyboard

주요 파일:

- `python/PiFinder/keyboard_interface.py`
- `python/PiFinder/keyboard_pi.py`
- `python/PiFinder/ui/bluetooth_keyboard.py`
- `python/PiFinder/ui/textentry.py`
- `python/PiFinder/ui/menu_structure.py`

보존해야 할 정책:

- libinput 기반 HID keyboard event mapping을 유지한다.
- Bluetooth scan/pair/connect UI를 유지한다.
- INDI guide 이동용 `qwe/asd/zxc` 추가 키맵을 유지한다 (Guide page와
  `GuideKeyMixin` 기반 passive 화면).
- key press/release가 필요한 guide motion은 release/timeout fail-safe를 유지한다.

### Integrated time sync

주요 파일:

- `python/PiFinder/gps_time_sync.py`
- `python/PiFinder/gps_time_sync_helper.py`
- `python/PiFinder/ui/gps_time_sync_status.py`
- `scripts/install_chrony_time_sync.sh`
- `scripts/install_gps_time_sync_helper.sh`
- `pi_config_files/pifinder_gps_time_sync.service`

보존해야 할 정책:

- 기본 시간 관리는 `chronyd` 중심이다.
- PiFinder time sync UI는 GPS/NTP/RTC 상태와 helper를 관리한다.
- 실제 시스템 시간 변경은 privileged helper/service 층에서 수행한다.
- INDI/OnStep으로 보낼 시간은 사용자가 입력한 값이 아니라 PiFinder가 사용하는 현재
  정확한 UTC 시간이어야 한다.

### Wi-Fi AP+STA

주요 파일:

- `scripts/pifinder_apsta.sh`
- `scripts/import_initial_wifi_networks.py`
- `python/PiFinder/sys_utils.py`
- `python/PiFinder/server.py`
- `python/views/network.html`
- `pi_config_files/pifinder_apsta_prepare.service`
- `pi_config_files/pifinder_apsta_monitor.service`
- `pi_config_files/dhcpcd.conf.apsta`

보존해야 할 정책:

- Wi-Fi mode는 STA/AP/AP+STA를 지원한다.
- AP+STA에서는 STA channel을 기준으로 AP virtual interface를 재시작한다.
- AP IP는 설정 가능해야 한다.
- AP security/password 설정을 유지한다.
- AP+STA internet sharing은 option이며 default OFF이다.
- 설명 문구에는 부하와 속도 저하 가능성을 안내한다.
- OS 초기 설치 시 등록된 STA profile을 PiFinder 목록으로 가져온다.
- 새 STA 추가 시 주변 SSID scan 목록을 사용할 수 있어야 한다.
- STA band preference는 2.4G/5G 선택 정책을 유지한다.

### Locations catalog

주요 파일:

- `python/PiFinder/location_catalog.py`
- `python/PiFinder/data/location_catalog.json`
- `scripts/build_location_catalog.py`
- `python/views/locations.html`
- `python/views/location_form.html`

보존해야 할 정책:

- 국가/지역/군구/도시 선택으로 좌표와 고도를 자동 입력한다.
- 북한 데이터는 제외한다.
- 한국은 행정구역 데이터를 섞어 비교적 상세한 선택을 지원한다.
- 수동 위치 선택은 실내 GPS unlock 상태에서도 PiFinder location source로 사용 가능해야 한다.
- Red Night theme에서 form/select/action tooltip 색상이 하얗게 튀지 않아야 한다.

### Web UI theme / PWA

주요 파일:

- `python/views/base.html`
- `python/views/css/style.css`
- `python/views/js/init.js`
- `python/views/manifest.webmanifest`
- `python/views/service-worker.js`
- `python/views/images/pwa-icon-192.png`
- `python/views/images/pwa-icon-512.png`

보존해야 할 정책:

- Red Night theme는 관측 중 암시야를 해치지 않는 적색 UI여야 한다.
- Logs page의 log content 색은 원래 의미 색을 유지한다.
- Android PWA 전체화면에서 theme color와 display mode를 유지한다.
- 메뉴 이동 후 fullscreen/PWA 상태가 불필요하게 깨지지 않도록 한다.
- Theme 선택은 navigation의 select로만 제공하고, 별도 bar는 제거 상태를 유지한다.

### INDI / OnStepX

주요 파일:

- `python/PiFinder/mountcontrol_indi.py`
- `python/PiFinder/indi_align.py`
- `python/PiFinder/indi_backlash_calibration.py`
- `python/PiFinder/indi_goto_guide_service.py`
- `python/PiFinder/indi_multipoint_align.py`
- `python/PiFinder/pos_server.py`
- `python/PiFinder/ui/indi.py`
- `python/views/indi_mount.html`
- `scripts/install_indi_mount_OnstepX.sh`
- `scripts/install_indi_mount_archive.sh`
- `scripts/package_indi_mount_archive.sh`
- `scripts/patches/indi-v2.2.3.1-onstepx.patch`

보존해야 할 정책:

- INDI 기능은 optional이다. 기본 PiFinder 설치만으로 INDI가 강제 설치되면 안 된다.
- OnStepX는 커스텀 INDI driver 이름이며, 원본 LX200 OnStep driver를 직접 덮어쓰지 않는다.
- INDI profile에서 active driver name을 읽고, OnStepX일 때만 OnStepX 전용 화면/동작을 사용한다.
- OnStepX 위치/시간 sync는 driver readback 표시와 실제 OnStep 값이 일관되도록 유지한다.
- OnStep 위치/시간 설정 시 PiFinder 현재 UTC 시간을 사용한다.
- OnStepX `Backlash`는 OnStep 펌웨어와 맞춘 0..3600 arc-sec 범위를 유지한다.
- OnStepX `GUIDE_RATE`는 driver 호환성과 향후 guide-rate 제어를 위해
  writable/readback 동작을 유지하는 것이 좋다. 현재 Auto Backlash는 INDI
  GoTo를 사용하므로 `GUIDE_RATE`에 의존하지 않는다.
- OnStepX가 아닌 일반 INDI mount에서는 generic INDI path를 유지한다.
- INDI restart는 server/profile/driver를 모두 정지 후 다시 시작하고, 가능하면 자동 connect한다.

### LCD INDI UI

주요 파일:

- `python/PiFinder/ui/indi.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/PiFinder/ui/base.py`
- `python/PiFinder/keyboard_pi.py`

보존해야 할 정책:

- Start 메뉴 하단에 INDI 항목을 둔다.
- INIT/STATUS/GUIDE 페이지를 유지한다.
- Guide page는 숫자키 `2/4/6/8`(동서남북)과 `qwe/asd/zxc` 키맵을 사용하고,
  `9/3`은 slew rate 조절이다. 대각선 이동은 키보드 문자키에만 있다.
- 5키는 guide motion에 사용하지 않는다.
- motion은 press-to-move, release-to-stop 방식이다.
- freeze나 key release 누락 시 timeout/fail-safe stop을 유지한다.
- 상단 bar의 `I` indicator는 INDI 연결 정상/문제 상태를 표시한다.

### SkySafari / mount mode integration

주요 파일:

- `python/PiFinder/pos_server.py`
- `python/PiFinder/pointing_coordinate_service.py`
- `python/PiFinder/mountcontrol_indi.py`
- `python/PiFinder/imu_pi.py`
- `python/PiFinder/imu_calibration.py`

보존해야 할 정책:

- SkySafari `:Sr/:Sd`는 target 좌표 저장이다.
- SkySafari `:MS#`는 GoTo 처리이다.
- SkySafari `:CM#`는 Sync/Align 처리이다.
- `:CM#` 처리 시 직전에 들어온 `Sr/Sd` target을 우선 사용한다.
- GoTo forwarding이 켜져 있으면 Align/Sync도 INDI/OnStep에 전달할 수 있어야 한다.
- solve 전에는 IMU fallback/보정값을 사용할 수 있다.
- solve가 성공하면 IMU alignment correction은 초기화한다.
- Reset Pointing은 IMU alignment correction을 폐기하고, 솔빙이 없으면 raw
  (보정 미적용) IMU 좌표로 마운트를 재-sync한다 — 잘못된 target으로 정렬했을 때
  IMU 원좌표로 복구하는 유일한 수단이다.
- mount mode가 Alt/Az, EQ, 기타 INDI mount에서 동작할 수 있도록 OnStep 전용 코드는 driver
  capability/name으로 gate한다.

### IMU compass / calibration

주요 파일:

- `python/PiFinder/imu_pi.py`
- `python/PiFinder/imu_calibration.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/PiFinder/ui/callbacks.py`

보존해야 할 정책:

- magnetometer/compass fusion은 option이다.
- 기본 동작은 기존 IMU 안정성을 해치지 않아야 한다.
- calibration은 자동 저장/로드를 우선하고, 수동 save/load/clear 메뉴를 제공한다.
- calibration 상태 UI는 실제 BNO055 상태를 반영한다.

## 충돌 가능성이 높은 파일

upstream sync 때 먼저 확인할 파일:

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

특히 다음 파일은 기능 경계가 많이 겹친다.

- `main.py`: startup process, display selection, GPS/camera/keyboard selection, time sync queue
- `server.py`: web routes, network/location/INDI APIs, time/location push
- `sys_utils.py`: privileged system operations, Wi-Fi, chrony, INDI service helpers
- `keyboard_pi.py`: GPIO keypad, HID keyboard, guide fail-safe
- `ui/menu_structure.py`: upstream menu additions과 MF menu additions가 자주 충돌
- `ui/base.py`: titlebar/status indicators/theme-independent LCD UI helpers
- `pos_server.py`: SkySafari LX200 protocol, GoTo/Sync/Guide/IMU fallback

## upstream sync 권장 절차

1. 현재 상태 확인:

```bash
git status --short --branch
git remote -v
git fetch upstream main
git rev-list --left-right --count upstream/main...HEAD
git log --oneline --left-right --cherry-pick upstream/main...HEAD --max-count=80
```

2. 변경 범위 확인:

```bash
git diff --stat HEAD...upstream/main
git diff --name-status HEAD...upstream/main
```

3. 충돌 dry-run:

```bash
git merge-tree --write-tree HEAD upstream/main
```

4. 적용 기준:

- 문서/CI/asset 같은 runtime 영향이 적은 변경부터 적용한다.
- Python runtime 변경은 기능 단위로 cherry-pick하거나 별도 sync branch에서 merge한다.
- Rev-4 hardware patch처럼 hardware side effect가 큰 변경은 쪼개서 적용한다.
- OnStepX/INDI/Network/Time sync 파일은 automatic resolution을 믿지 말고 diff를 읽는다.

5. 최소 검증:

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

6. 하드웨어 검증:

- Pi4 Bookworm 64-bit
- Pi5 또는 CM5 Bookworm 64-bit
- Camera preview/focus
- GPS lock/unlock and manual location load
- Bluetooth keyboard key press/release
- Web Red Night theme
- AP+STA and AP client list
- INDI Web UI and LCD INDI Guide stop fail-safe
- SkySafari GoTo/Align/Guide path

## 알려진 테스트 주의사항

전체 `python -m pytest python/tests`는 현재 일부 기존 테스트가 환경/테스트 API 문제로
실패할 수 있다.

2026-07-03 확인된 대표 원인:

- `test_multiproclogging.py`: `pifinder_logconf.json` 경로 의존
- `test_radec_entry.py`: 테스트가 기대하는 생성자/API와 현재 코드 불일치
- `test_ui_modules.py`: `key_number_press(number)` 같은 인자 필요 key method를 무인자로
  sweep하는 테스트 구조

따라서 upstream sync 후에는 위의 최소 검증 목록을 우선 기준으로 삼고, 전체 테스트 실패는
첫 traceback을 기준으로 실제 회귀인지 기존 테스트 불일치인지 분리한다.

## 다음에 문서를 갱신해야 하는 경우

다음 변경이 발생하면 이 문서를 갱신한다.

- upstream main에서 `main.py`, `server.py`, `sys_utils.py`, `ui/menu_structure.py`,
  `pos_server.py`가 크게 바뀐 경우
- Rev-4 battery/sound/power 기능 중 일부를 추가 적용한 경우
- INDI generic path와 OnStepX-specific path를 다시 분리하거나 합친 경우
- SkySafari Align/GoTo/Guide 처리 정책이 바뀐 경우
- chronyd/time sync 정책이 바뀐 경우
- AP+STA 네트워크 정책이나 service 이름이 바뀐 경우
