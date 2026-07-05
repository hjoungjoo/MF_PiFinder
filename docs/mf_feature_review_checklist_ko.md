# MF_PiFinder 기능 검토 및 테스트 체크리스트

작성일: 2026-07-03

이 문서는 `brickbots/PiFinder` `main` 브랜치와 현재 `mf_pifinder` 브랜치를 비교해,
MF_PiFinder에 추가되었거나 원본과 다르게 수정된 기능을 검토/테스트 항목으로 정리한
목록이다.

기준:

- 비교 대상: `upstream/main` (`https://github.com/brickbots/PiFinder/tree/main`)
- 현재 소스: `mf_pifinder`
- 비교 시점: 2026-07-03
- 명령 기준:
  - `git fetch upstream main`
  - `git rev-list --left-right --count upstream/main...HEAD`
  - `git diff --stat upstream/main...HEAD`
  - `git diff --name-status upstream/main...HEAD`

비교 요약:

- upstream에는 있지만 MF에 전체 적용하지 않은 주요 변경:
  - Rev-4 battery/sound/power hardware enablement 전체 패치
- MF에 추가/수정된 주요 영역:
  - Bookworm/RPi4/RPi5/CM5 설치 및 보드 profile
  - AP+STA Wi-Fi
  - Bluetooth/USB HID keyboard
  - Red Night/PWA Web UI
  - Locations catalog
  - chronyd 중심 시간 관리
  - INDI/OnStepX/SkySafari mount integration
  - LCD INDI UI
  - IMU compass/calibration
  - SSD1333 display auto-detection
  - 한국어 UI
  - camera focus/gain/preview 개선

관련 문서:

- `docs/mf_upstream_patch_reference_ko.md`: upstream 재동기화와 패치 재적용 기준
- `docs/mf_change_history_ko.md`: 전체 변경 히스토리
- `docs/mf_pifinder_rpi4_pi5_compatibility_ko.md`: Pi4/Pi5/CM5 Bookworm 호환성 요약
- `docs/mf_indi_mount_install_ko.md`: INDI 설치/운영
- `docs/mf_wifi_apsta_ko.md`: AP+STA Wi-Fi
- `docs/mf_time_sync_ko.md`: 시간 동기화
- `docs/mf_keyboard_mapping_ko.md`: 키보드 매핑

## 테스트 우선순위

| 우선순위 | 의미 |
| --- | --- |
| P0 | 장치 부팅/설치/기본 관측 기능에 직접 영향. 반드시 테스트 |
| P1 | 주요 기능. 실제 장비나 네트워크 환경에서 테스트 권장 |
| P2 | 보조 기능 또는 문서/개발 편의. 회귀 확인 위주 |

## 1. Platform / Bookworm / Raspberry Pi 4, 5, CM5 호환성

우선순위: P0

주요 변경:

- Bookworm 64-bit 기본 설치 경로 지원
- `/boot/firmware/config.txt` 우선, legacy `/boot/config.txt` fallback
- 현재 OS 사용자 기준으로 `PiFinder_data`, systemd, Samba 경로 처리
- Pi4/Pi5/CM5 보드별 GPS UART profile
- Pi5/CM5에서 OLED CS 충돌을 피하기 위한 `uart2-pi5` 사용
- `/dev/spidev0.0`, `/dev/spidev10.0` 양쪽 SPI 지원
- SSD1333 display auto-detection 추가

주요 파일:

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

검토 포인트:

- [ ] 새 OS 설치 후 `pifinder_setup.sh`가 일반 사용자로 끝까지 실행되는가
- [ ] Pi4에서 `gps_port=auto`가 `/dev/ttyAMA3`로 해석되는가
- [ ] Pi5/CM5에서 `gps_port=auto`가 `/dev/ttyAMA2`로 해석되는가
- [ ] boot config가 실제 사용 중인 경로에 적용되는가
- [ ] `uart3`와 OLED CE0/GPIO8 충돌이 Pi5/CM5에서 발생하지 않는가
- [ ] `spidev0.0`만 있는 장비와 `spidev10.0`만 있는 장비 모두 동작하는가
- [ ] SSD1333 marker 감지 실패 시 기존 SSD1351 기본 동작으로 fallback 되는가
- [ ] splash와 main UI가 같은 display selection을 사용하는가

테스트 항목:

- [ ] Pi4 Bookworm 64-bit fresh install
- [ ] Pi5 또는 CM5 Bookworm 64-bit fresh install
- [ ] `systemctl status pifinder cedar_detect pifinder_splash`
- [ ] `ls /dev/spidev* /dev/ttyAMA*`
- [ ] Web UI 접속
- [ ] LCD/OLED splash 표시
- [ ] LCD/OLED main UI 표시
- [ ] GPS 포트 자동 선택 확인
- [ ] camera preview 확인

## 2. Camera Preview / Focus / Gain

우선순위: P1

주요 변경:

- focus preview 개선
- 밝은 배경 threshold 조정
- camera gain profile/runtime 선택
- LCD camera preview debug script 추가

주요 파일:

- `python/PiFinder/camera_interface.py`
- `python/PiFinder/ui/preview.py`
- `python/PiFinder/ui/callbacks.py`
- `python/PiFinder/ui/menu_structure.py`
- `scripts/camera_lcd_preview.py`

검토 포인트:

- [ ] 기존 focus workflow가 유지되는가
- [ ] camera gain을 profile default로 되돌릴 수 있는가
- [ ] runtime gain 변경이 실제 camera metadata와 일치하는가
- [ ] Pi4와 Pi5/CM5에서 camera overlay 차이가 문제를 만들지 않는가

테스트 항목:

- [ ] 낮은 gain/high gain 설정 전환
- [ ] profile gain 선택
- [ ] focus preview에서 별 또는 밝은 점 표시
- [ ] `scripts/camera_lcd_preview.py` 실행
- [ ] IMX462 camera 동작 확인

## 3. Korean UI Localization

우선순위: P1

주요 변경:

- 한국어 locale 추가
- 언어 메뉴에 `ko` 추가
- CJK font 처리
- 언어 변경 후 restart 안내

주요 파일:

- `python/locale/ko/LC_MESSAGES/messages.po`
- `python/locale/ko/LC_MESSAGES/messages.mo`
- `python/PiFinder/ui/fonts.py`
- `python/PiFinder/ui/menu_structure.py`

검토 포인트:

- [ ] 언어 메뉴에서 Korean 선택 가능
- [ ] LCD에서 한글이 깨지지 않는가
- [ ] Web UI에서 한글이 정상 표시되는가
- [ ] upstream i18n 변경 후 Korean `.po`가 누락되지 않았는가

테스트 항목:

- [ ] 언어를 Korean으로 변경
- [ ] 재시작 후 LCD menu 확인
- [ ] Web UI navigation/title/button 확인
- [ ] 로그/에러 메시지 표시 확인

## 4. Bluetooth / USB HID Keyboard

우선순위: P0

주요 변경:

- libinput 기반 HID keyboard event 처리
- Bluetooth keyboard scan/pair/connect UI
- USB keyboard 입력 지원
- 텍스트 입력용 키코드 확장
- INDI Guide page 전용 `qwe/asd/zxc` 방향 키맵
- Guide motion release/fail-safe stop 보완

주요 파일:

- `python/PiFinder/keyboard_interface.py`
- `python/PiFinder/keyboard_pi.py`
- `python/PiFinder/ui/bluetooth_keyboard.py`
- `python/PiFinder/ui/textentry.py`
- `python/PiFinder/ui/indi.py`
- `python/PiFinder/ui/menu_structure.py`

검토 포인트:

- [ ] Bluetooth keyboard가 paired/connected 상태에서 `/dev/input/event*`로 잡히는가
- [ ] key press와 release가 모두 들어오는가
- [ ] 일반 메뉴 입력과 INDI Guide page 전용 입력이 충돌하지 않는가
- [ ] 장시간 key hold 중 freeze/SSH 지연이 있어도 mount motion이 멈추는가
- [ ] AP+STA/Wi-Fi 변경 후 Bluetooth keyboard reconnect가 유지되는가

테스트 항목:

- [ ] Bluetooth keyboard pair
- [ ] Bluetooth keyboard reconnect after reboot
- [ ] USB keyboard 연결
- [ ] LCD menu navigation
- [ ] Text entry
- [ ] INDI Guide 방향키 press/release
- [ ] Guide motion 중 Bluetooth 연결 해제
- [ ] Guide motion timeout stop

## 5. Web UI Red Night Theme / PWA

우선순위: P1

주요 변경:

- Red Night theme 추가
- browser별 theme 저장
- PWA manifest/service worker/icon 추가
- Android PWA fullscreen/theme-color 대응
- navigation theme selector 통합
- Locations/tooltip/select/form 색상 보정

주요 파일:

- `python/views/base.html`
- `python/views/css/style.css`
- `python/views/js/init.js`
- `python/views/manifest.webmanifest`
- `python/views/service-worker.js`
- `python/views/images/pwa-icon-192.png`
- `python/views/images/pwa-icon-512.png`
- `python/views/locations.html`
- `python/views/location_form.html`

검토 포인트:

- [ ] Red Night에서 흰색/밝은 색 UI가 남지 않는가
- [ ] Logs page의 log semantic color는 유지되는가
- [ ] PWA 설치 후 전체화면 진입이 되는가
- [ ] Android navigation/status bar가 theme color를 따르는가
- [ ] 메뉴 이동 후 PWA/전체화면 상태가 불필요하게 깨지지 않는가
- [ ] theme selector가 navigation에만 보이는가

테스트 항목:

- [ ] Chrome desktop theme 변경
- [ ] Android Chrome theme 변경
- [ ] Android PWA 설치
- [ ] PWA fullscreen navigation
- [ ] Logs page color 확인
- [ ] Locations add/edit form 확인
- [ ] Tooltips/action buttons 색상 확인

## 6. Wi-Fi AP / STA / AP+STA

우선순위: P0

주요 변경:

- STA/AP/AP+STA mode 지원
- `uap0` virtual AP interface 생성
- STA channel 기반 AP channel 재시작
- AP IP 설정
- AP WPA2 security/password 설정
- AP+STA internet sharing option, default OFF
- OS 초기 Wi-Fi profile import
- 주변 SSID scan 후 STA profile 추가
- STA band preference
- AP connected device list 표시

주요 파일:

- `scripts/pifinder_apsta.sh`
- `scripts/import_initial_wifi_networks.py`
- `python/PiFinder/sys_utils.py`
- `python/PiFinder/server.py`
- `python/views/network.html`
- `pi_config_files/pifinder_apsta_prepare.service`
- `pi_config_files/pifinder_apsta_monitor.service`
- `pi_config_files/dhcpcd.conf.apsta`
- `switch-apsta.sh`

검토 포인트:

- [ ] STA only mode 정상
- [ ] AP only mode 정상
- [ ] AP+STA mode 정상
- [ ] AP+STA에서 AP client가 PiFinder Web UI에 접속 가능한가
- [ ] AP+STA internet sharing ON/OFF가 동작하는가
- [ ] STA channel 변경 시 AP channel 재설정이 되는가
- [ ] AP IP 변경 후 dnsmasq/dhcp lease가 정상인가
- [ ] STA band preference가 NetworkManager profile과 일치하는가
- [ ] STA망에서 PiFinder Web 접속이 안 되는 경우 client isolation 여부를 구분할 수 있는가

테스트 항목:

- [ ] AP mode에서 `10.10.10.1` 접속
- [ ] AP+STA에서 STA 인터넷 연결
- [ ] AP client 인터넷 공유 ON
- [ ] AP client 인터넷 공유 OFF
- [ ] OnStep device AP 접속 및 통신
- [ ] AP connected device list 표시
- [ ] STA SSID scan/add
- [ ] 기존 OS Wi-Fi profile import
- [ ] 2.4G/5G band preference 변경
- [ ] STA router client isolation 환경 확인

## 7. Locations Catalog

우선순위: P1

주요 변경:

- offline location catalog 추가
- country/state/district/city lookup
- 좌표/고도/source 자동 입력
- 한국 상세 행정구역 보강
- 북한 제외
- manual loaded location을 실내 GPS unlock 상태에서도 사용 가능하게 처리

주요 파일:

- `python/PiFinder/location_catalog.py`
- `python/PiFinder/data/location_catalog.json`
- `scripts/build_location_catalog.py`
- `python/views/locations.html`
- `python/views/location_form.html`
- `python/PiFinder/server.py`

검토 포인트:

- [ ] Country 선택 후 다음 select 목록이 정상 필터링되는가
- [ ] 한국 주소가 충분히 상세한가
- [ ] location name 자동 입력/변경이 자연스러운가
- [ ] Save Location이 정상 저장되는가
- [ ] default location 지정이 정상인가
- [ ] GPS unlock 상태에서도 수동 location이 PiFinder/INDI에 반영되는가
- [ ] Red Night theme에서 form/select 색상이 적절한가

테스트 항목:

- [ ] 서울/송파/풍납동 선택 후 저장
- [ ] 다른 한국 지역 저장
- [ ] 해외 주요 도시 저장
- [ ] default location 변경
- [ ] location reload
- [ ] INDI page PiFinder Location 업데이트
- [ ] OnStep Send Location and Time 확인

## 8. Integrated Time Sync / chronyd

우선순위: P0

주요 변경:

- GPS/NTP/RTC/software PPS 통합 시간 관리
- chronyd 중심 정책으로 정리
- privileged helper service 분리
- GPS/NTP/RTC 상태 UI
- custom NTP server 설정
- Set Time/Date는 location lock이 없으면 self-gate
- PiFinder UTC-aware datetime handling 적용

주요 파일:

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

검토 포인트:

- [ ] 기본 clock manager가 chronyd 중심으로 동작하는가
- [ ] GPS 신호가 약하거나 unlock 상태에서 graceful degradation 되는가
- [ ] NTP network unavailable 상태에서 timeout/오류 처리가 안정적인가
- [ ] custom NTP server가 저장/적용되는가
- [ ] Pi5 RTC 경로가 문제를 만들지 않는가
- [ ] Set Time/Date는 location lock이 없으면 실행되지 않는가
- [ ] INDI/OnStep에 보낼 시간은 PiFinder current UTC time인가

테스트 항목:

- [ ] 실내 GPS unlock
- [ ] 실외 GPS lock
- [ ] NTP available
- [ ] NTP unavailable
- [ ] custom NTP server 입력
- [ ] chronyc sources/tracking 확인
- [ ] Time Sync LCD status
- [ ] Web status/API 확인
- [ ] OnStep time sync 후 OnStep Web UI 시간 확인

## 9. INDI Mount / OnStepX

우선순위: P0

주요 변경:

- optional INDI mount process
- INDI install scripts
- INDI archive package/install scripts
- OnStepX custom INDI driver patch flow
- INDI Web UI menu/page
- LX200 OnStep/OnStepX network/serial setup UI
- OnStep location/time sync 개선
- INDI restart
- active driver name/profile 기반 동작 분리
- generic INDI mount path 유지

주요 파일:

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

검토 포인트:

- [ ] 기본 PiFinder 설치만으로 INDI가 강제 설치되지 않는가
- [ ] INDI 설치 script가 Pi4/Pi5 모두에서 동작하는가
- [ ] OnStepX driver가 원본 LX200 OnStep driver를 덮어쓰지 않는가
- [ ] active driver가 OnStepX일 때만 OnStepX 전용 UI가 보이는가
- [ ] USB serial 목록이 표시되는가
- [ ] network host/port 목록과 manual entry가 정상 동작하는가
- [ ] INDI restart가 server/profile/driver를 모두 정리하고 재시작하는가
- [ ] Home 상태, Park 상태, 원시 `:GU#` 상태가 분리 표시되는가
- [ ] 수동 Backlash가 마운트 이동 없이 `Backlash.Backlash RA/DEC`를 읽고 쓰는가
- [ ] Auto Backlash가 Compass/NDOF 모드를 요구하고 MAG calibration 3까지 대기하는가
- [ ] Auto Backlash가 시작 전 mount 좌표를 IMU Alt/Az 기준으로 sync하고 tracking을 끄는가
- [ ] Auto Backlash가 시작 전 OnStepX `GUIDE_RATE`를 Half-Max/96x 추정값으로 설정하고 readback을 검증하는가
- [ ] `GUIDE_RATE` readback이 요청값과 다르면 마운트를 움직이지 않고 실패 처리하는가
- [ ] Alt/Az mount에서는 `AZ`/`ALT`, EQ mount에서는 `RA`/`DEC`를 한 축씩 timed pulse guide로 반복 이동하는가
- [ ] Auto Backlash가 각 pulse-guide leg의 시작 mount 좌표, 종료 mount 좌표, 시작/종료 IMU 좌표를 기록하는가
- [ ] mount delta와 IMU delta 차이가 1도 이상인 leg는 제외하고, 남은 값의 상하 30%를 버린 middle 40% 평균을 표시하는가
- [ ] Auto Backlash가 Alt/Az에서는 `AZ+/-`, `ALT+/-`, EQ에서는 `RA+/-`, `DEC+/-`처럼 실제 이동 방향별 추천값을 분리 표시하는가
- [ ] Auto Backlash 결과는 계산값 표시만 하고, 입력칸 변경이나 `Save Backlash` 전 적용을 하지 않는가
- [ ] driver 통신 불량 시 기본 PiFinder 기능이 멈추지 않는가

테스트 항목:

- [ ] INDI 미설치 상태에서 기본 PiFinder 동작
- [ ] `install_indi_mount_OnstepX.sh` 설치
- [ ] INDI Web Manager 접속
- [ ] OnStepX profile start/connect
- [ ] LX200 OnStepX network TCP setup
- [ ] LX200 OnStepX USB serial setup
- [ ] Restart INDI
- [ ] OnStep Web UI, 직접 LX200 `:GU#`, PiFinder INDI Home/Park 상태 비교
- [ ] 현재 Backlash RA/DEC 읽기
- [ ] UI에서 Backlash RA/DEC를 수동 저장하고 driver 값이 변경되는지 확인
- [ ] Auto Backlash가 Compass/NDOF 모드를 활성/요구하고 MAG calibration 3까지 대기하는지 확인
- [ ] Auto Backlash가 motion test 중 tracking을 끄고 완료/실패 후 원래 tracking 상태만 복구하는지 확인
- [ ] Auto Backlash가 Backlash RA/DEC를 0으로 초기화하거나 적용/원복하지 않고,
      계산 후보값만 사용자 검토용으로 표시하는지 확인
- [ ] Auto Backlash가 완료/실패 후 원래 INDI `GUIDE_RATE`를 복구하는지 확인
- [ ] compass timed pulse loop가 신뢰 가능한 mount/IMU 이동 기록을 만들 수 없는 경우,
      값을 적용하지 않고 실패 메시지를 표시하는지 확인
- [ ] INDI server stop 상태에서 PiFinder UI 동작
- [ ] OnStep device offline 상태에서 PiFinder 동작

## 10. LCD INDI UI

우선순위: P0

주요 변경:

- LCD Start menu 하단 INDI 항목
- INIT / STATUS / GUIDE 페이지
- INIT actions: connect/init, send location/time, park/unpark, set home, return home, set-park, restart
- STATUS periodic update
- GUIDE keypad overlay
- `789 / 4 6 / 123` guide direction layout
- key press-to-move, release-to-stop
- `qwe/asd/zxc` keyboard mapping only inside Guide page
- `I` top-bar indicator

주요 파일:

- `python/PiFinder/ui/indi.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/PiFinder/ui/base.py`
- `python/PiFinder/keyboard_pi.py`

검토 포인트:

- [ ] Start menu 하단에 INDI가 보이는가
- [ ] INIT menu 항목이 화면을 넘지 않는가
- [ ] Restart action이 INIT에서 보이는가
- [ ] STATUS가 주기적으로 갱신되는가
- [ ] Guide 숫자 layout이 실제 화면 위치와 일치하는가
- [ ] 5키는 guide motion에 사용되지 않는가
- [ ] motion 중 key release 누락 시 timeout stop이 동작하는가
- [ ] Bluetooth keyboard로 start/stop 모두 동작하는가
- [ ] 상단 `I` 표시가 연결 상태에 따라 정상/점멸하는가

테스트 항목:

- [ ] LCD INIT connect/init
- [ ] LCD send location/time
- [ ] LCD park/unpark
- [ ] LCD set home/return home
- [ ] LCD restart INDI
- [ ] LCD Guide 8방향 motion
- [ ] LCD Guide release stop
- [ ] Bluetooth keyboard Guide motion
- [ ] Web UI로 stop recovery

## 11. SkySafari / LX200 / Mount Mode Integration

우선순위: P0

주요 변경:

- SkySafari LX200 `:Sr/:Sd/:MS#/:CM#` 처리 보강
- solve 전 IMU fallback pointing
- SkySafari GoTo를 INDI mount로 forwarding option
- SkySafari Guide를 INDI guide motion으로 bridge
- SkySafari Align/Sync를 PiFinder/IMU/INDI로 처리
- mount mode compatibility audit
- GoTo 완료/이동 상태 처리 보완
- Alt/Az/EQ 등 다양한 mount mode를 고려한 분리

주요 파일:

- `python/PiFinder/pos_server.py`
- `python/PiFinder/mountcontrol_indi.py`
- `python/PiFinder/imu_pi.py`
- `python/PiFinder/imu_calibration.py`
- `docs/mf_mount_mode_compatibility_ko.md`

검토 포인트:

- [ ] `:Sr/:Sd`는 target 좌표 저장만 하는가
- [ ] `:MS#`는 GoTo로 처리되는가
- [ ] `:CM#`는 Sync/Align으로 처리되는가
- [ ] `:CM#`는 직전 parsed `Sr/Sd` target을 우선 사용하는가
- [ ] GoTo forwarding ON이면 Align/Sync도 INDI/OnStep에 전달되는가
- [ ] solve 전 IMU correction이 적용되는가
- [ ] solve 후 IMU correction이 초기화되는가
- [ ] Alt/Az와 EQ mount mode에서 horizon/coordinate 상태가 잘못 표시되지 않는가
- [ ] SkySafari에서 GoTo 완료 상태가 정상 종료되는가
- [ ] target이 지평선 아래라고 잘못 판단되는 상황이 없는가

테스트 항목:

- [ ] SkySafari Push-To mode
- [ ] SkySafari GoTo mode
- [ ] SkySafari guide buttons
- [ ] SkySafari Align
- [ ] solve 전 IMU fallback
- [ ] solve 후 normal pointing
- [ ] INDI GoTo forwarding OFF
- [ ] INDI GoTo forwarding ON
- [ ] Alt/Az mount
- [ ] EQ mount

## 12. IMU Compass / Calibration

우선순위: P1

주요 변경:

- optional BNO055 magnetometer/compass fusion
- IMU sensitivity 설정 유지
- auto calibration save/load
- manual calibration save/load/clear
- compass/calibration UI menu

주요 파일:

- `python/PiFinder/imu_pi.py`
- `python/PiFinder/imu_calibration.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/PiFinder/ui/callbacks.py`
- `docs/mf_imu_compass_calibration_ko.md`

검토 포인트:

- [ ] 기본 OFF 상태에서 기존 IMU 동작이 안정적인가
- [ ] compass ON 시 heading 개선이 있는가
- [ ] 실내 자기장 간섭에서 오동작이 큰가
- [ ] calibration status가 실제 BNO055 상태와 맞는가
- [ ] auto save/load가 reboot 후 적용되는가
- [ ] manual save/load/clear가 동작하는가
- [ ] solve 성공 후 IMU correction 초기화와 충돌하지 않는가

테스트 항목:

- [ ] Compass OFF
- [ ] Compass ON
- [ ] Calibration auto save
- [ ] Calibration load after reboot
- [ ] Manual save/load/clear
- [ ] SkySafari no-solve pointing
- [ ] Plate solve 후 correction reset

## 13. Observing List CSV Import

우선순위: P2

주요 변경:

- upstream CSV import 개선 반영
- lenient headers
- 다양한 coordinate format 지원
- docs examples 추가
- object type code drift guard와 연계

주요 파일:

- `python/PiFinder/obslist.py`
- `python/PiFinder/obslist_formats.py`
- `docs/ax/catalog/obslist-formats/README.md`
- `docs/ax/catalog/obslist-formats/examples/*`
- `python/tests/test_obslist_formats.py`
- `python/tests/test_obslist_resolve.py`

검토 포인트:

- [ ] 기존 `.pifinder` list import가 깨지지 않는가
- [ ] third-party CSV import가 동작하는가
- [ ] RA hour/degree/sexagesimal/colon format이 처리되는가
- [ ] object type filter와 OBJ_TYPES가 일치하는가

테스트 항목:

- [ ] example CSV import
- [ ] 잘못된 header 처리
- [ ] mixed coordinate format 처리
- [ ] object type filter 적용

## 14. OBJ_TYPES Single Source

우선순위: P2

주요 변경:

- object type code set을 `OBJ_TYPES`로 단일화
- Type filter menu를 `OBJ_TYPES.items()`에서 생성
- docs/default_config drift guard test 추가

주요 파일:

- `python/PiFinder/obj_types.py`
- `python/PiFinder/ui/menu_structure.py`
- `python/tests/test_obj_types_docs.py`
- `default_config.json`

검토 포인트:

- [ ] Type filter menu 순서가 적절한가
- [ ] 표시명이 LCD 폭에 너무 길지 않은가
- [ ] Korean translation에서 object type label이 자연스러운가
- [ ] `default_config.json`의 `filter.object_types`가 모든 type을 포함하는가

테스트 항목:

- [ ] Type filter menu 표시
- [ ] Type filter 선택/해제
- [ ] catalog filtering
- [ ] `test_obj_types_docs.py`

## 15. Documentation / Test / CI / Assets

우선순위: P2

주요 변경:

- MF docs 추가
- upstream patch reference 문서 추가
- feature별 install/test docs 추가
- NixOS PR build CI 반영
- case/accessory assets 반영
- test coverage 추가

주요 파일:

- `docs/mf_*.md`
- `.github/workflows/nixos-pr-build.yml`
- `.github/scripts/*`
- `case/accessories/*`
- `python/tests/test_*.py`

검토 포인트:

- [ ] 문서 이름/언어 쌍이 맞는가
- [ ] 한국어 문서에 대응 영문 문서가 있는가
- [ ] setup/install 문서가 현재 script 이름과 일치하는가
- [ ] GitHub Actions가 fork에서 의도대로 동작하는가
- [ ] asset 변경이 불필요한 PR noise를 만들지 않는가

테스트 항목:

- [ ] 문서 링크 확인
- [ ] install script 이름 확인
- [ ] CI workflow syntax 확인
- [ ] docs/source menu map 확인

## 16. Upstream Rev-4 Hardware Patch: 미적용/부분 적용 항목

우선순위: 검토 전용

현재 상태:

- SSD1333 display auto-detection만 MF 방식으로 부분 적용
- battery/sound/power/latch는 전체 미적용

미적용 항목:

- BQ25895 battery telemetry
- BQ25895 fast-charge configuration writes
- sound/earcon buzzer subsystem
- GPIO15 hardware power button
- GPIO14 gpio-poweroff latch
- battery titlebar icon
- Raspberry Pi red power LED control

검토 포인트:

- [ ] Rev-4 하드웨어가 실제 대상인지 확인
- [ ] GPIO14 poweroff latch 배선이 있는 장비에서만 적용할지 결정
- [ ] sound/earcon default OFF 정책 필요 여부 결정
- [ ] battery charger write 동작을 read-only와 분리할지 결정
- [ ] `HardwareCapabilities` 타입을 가져올 경우 기존 `hardware_detect.py` fallback 유지

## 최소 회귀 테스트 명령

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

## 실제 장비 통합 테스트 순서

권장 순서:

1. PiFinder 서비스 부팅
2. Web UI 접속
3. LCD/OLED UI 확인
4. Camera preview/focus
5. GPS unlock 상태 확인
6. 저장된 location load
7. Time sync 상태 확인
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
19. Plate solve 후 correction reset
20. Reboot 후 설정 유지 확인

## 결과 기록 양식

테스트할 때 아래 형식으로 기록하면 다음 패치 판단에 도움이 된다.

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
