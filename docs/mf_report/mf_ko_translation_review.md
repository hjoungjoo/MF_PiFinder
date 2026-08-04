# PiFinder 한국어 번역 검토표 (영어 ↔ 한글)

작성일: 2026-07-11

이 문서는 `python/locale/ko/LC_MESSAGES/messages.po` 에서 `# AI-TRANSLATED (claude)` 로 표시된 항목(총 263개)을 영어 원문과 한글 번역으로 나란히 정리한 검토용 표입니다. 검수 후 문제가 없으면 .po에서 해당 주석을 지우면 됩니다.

- `\n` = 줄바꿈, `%(name)s` / `%%` = 서식 자리표시자(번역에서 그대로 유지).
- 원문 유지 약어: RA/Dec, EQ, Alt/Az, GPS, INDI, OnStep(X), SkySafari, IMU, RAW, Bayer, TCP/USB/IP/MAC/AP/STA/NTP/UTC/SSID, mag 등.
- 비고 열은 제가 판단이 필요하다고 본 항목에만 표기했습니다.


## `PiFinder/main.py`  (1개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | INDI GoTo/Guide\nrestarting | INDI GoTo/Guide\n재시작 중 |  |

## `PiFinder/server.py`  (30개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | Alignment point confirmed | 정렬점을 확정했습니다 |  |
| 2 | Alignment star GoTo requested | 정렬 별 GoTo를 요청했습니다 |  |
| 3 | Backlash motion test stop requested | 백래시 이동 테스트 정지를 요청했습니다 |  |
| 4 | Backlash must be a number | 백래시는 숫자여야 합니다 |  |
| 5 | Backlash must be between 0 and %(max)d | 백래시는 0에서 %(max)d 사이여야 합니다 |  |
| 6 | Backlash settings saved | 백래시 설정을 저장했습니다 |  |
| 7 | Backlash stop requested; waiting for mount-control process stop | 백래시 정지를 요청했습니다. 마운트 제어 프로세스 종료를 기다리는 중 |  |
| 8 | INDI GoTo / Guide settings applied | INDI GoTo / Guide 설정을 적용했습니다 |  |
| 9 | INDI OnStep settings applied | INDI OnStep 설정을 적용했습니다 |  |
| 10 | INDI server restarted and driver connected | INDI 서버를 재시작하고 드라이버를 연결했습니다 |  |
| 11 | Invalid alignment action | 잘못된 정렬 동작 |  |
| 12 | Invalid alignment mode | 잘못된 정렬 모드 |  |
| 13 | Invalid backlash auto mode | 잘못된 백래시 자동 모드 |  |
| 14 | Invalid INDI GoTo method | 잘못된 INDI GoTo 방식 |  |
| 15 | Invalid SkySafari mount status code | 잘못된 SkySafari 마운트 상태 코드 |  |
| 16 | LiveCam | LiveCam |  |
| 17 | Location and UTC time sent via INDI | INDI로 위치와 UTC 시간을 전송했습니다 |  |
| 18 | Motion command sent | 이동 명령을 전송했습니다 |  |
| 19 | Motion test repeats must be a number | 이동 테스트 반복 횟수는 숫자여야 합니다 |  |
| 20 | Motion test repeats must be between 1 and %(max)d | 이동 테스트 반복 횟수는 1에서 %(max)d 사이여야 합니다 |  |
| 21 | Mount-control process is not available | 마운트 제어 프로세스를 사용할 수 없습니다 |  |
| 22 | Multi-point alignment cancelled | 다지점 정렬을 취소했습니다 |  |
| 23 | Multi-point alignment started | 다지점 정렬을 시작했습니다 |  |
| 24 | Select a valid alignment star | 유효한 정렬 별을 선택하세요 |  |
| 25 | SkySafari mount settings applied | SkySafari 마운트 설정을 적용했습니다 |  |
| 26 | Solved GoTo loop continue requested | 해석 좌표 GoTo 루프 계속을 요청했습니다 |  |
| 27 | Solved GoTo motion test started | 해석 좌표 GoTo 이동 테스트를 시작했습니다 |  |
| 28 | This INDI profile uses %(driver)s. OnStepX controls are available only when the active profile driver is LX200 OnStepX. | 이 INDI 프로파일은 %(driver)s을(를) 사용합니다. OnStepX 컨트롤은 활성 프로파일 드라이버가 LX200 OnStepX일 때만 사용할 수 있습니다. |  |
| 29 | unknown driver | 알 수 없는 드라이버 |  |
| 30 | Wi-Fi scan failed | Wi-Fi 검색 실패 |  |

## `PiFinder/ui/callbacks.py`  (5개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | IMU Cal Clear | IMU 보정 삭제 |  |
| 2 | IMU Cal Load | IMU 보정 불러오기 |  |
| 3 | IMU Cal Save | IMU 보정 저장 |  |
| 4 | IMU command\nunavailable | IMU 명령\n사용 불가 |  |
| 5 | NTP server\nunchanged | NTP 서버\n변경 없음 |  |

## `PiFinder/ui/indi.py`  (2개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | Left mode/cancel | 왼쪽 모드/취소 |  |
| 2 | Release stop  Left back | 떼면 정지  왼쪽 뒤로 |  |

## `PiFinder/ui/menu_structure.py`  (9개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | GoTo Method | GoTo 방식 |  |
| 2 | GoTo Recovery | GoTo 복구 |  |
| 3 | Goto/Guide | GoTo/Guide |  |
| 4 | INDI Mount | INDI 마운트 |  |
| 5 | pool.ntp.org | pool.ntp.org |  |
| 6 | time.cloudflare.com | time.cloudflare.com |  |
| 7 | time.google.com | time.google.com |  |
| 8 | time.nist.gov | time.nist.gov |  |
| 9 | Tracking Guide | 추적 가이드 |  |

## `views/base.html`  (6개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | Fullscreen | 전체 화면 |  |
| 2 | Gray | 회색 |  |
| 3 | Red Night | 야간 적색 |  |
| 4 | Resume Fullscreen | 전체 화면 복귀 |  |
| 5 | Theme | 테마 |  |
| 6 | Web theme | 웹 테마 |  |

## `views/indi_mount.html`  (133개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | %(axis)s Backlash | %(axis)s 백래시 |  |
| 2 | %(driver)s Driver Connection | %(driver)s 드라이버 연결 |  |
| 3 | %(driver)s driver properties are not available. Start the INDI profile first. | %(driver)s 드라이버 속성을 사용할 수 없습니다. 먼저 INDI 프로파일을 시작하세요. |  |
| 4 | %(driver)s is selected in the active INDI profile. OnStepX-specific setup and motion controls are hidden for this driver. | 활성 INDI 프로파일에 %(driver)s이(가) 선택되어 있습니다. 이 드라이버에서는 OnStepX 전용 설정과 이동 컨트롤이 숨겨집니다. |  |
| 5 | Active Driver | 활성 드라이버 |  |
| 6 | Active INDI | 활성 INDI |  |
| 7 | Align Progress | 정렬 진행 |  |
| 8 | Align Status | 정렬 상태 |  |
| 9 | Alignment Star | 정렬 별 |  |
| 10 | Applied | 적용됨 |  |
| 11 | Apply GoTo / Guide Settings | GoTo / Guide 설정 적용 |  |
| 12 | Apply SkySafari Settings | SkySafari 설정 적용 |  |
| 13 | Apply to INDI | INDI에 적용 |  |
| 14 | At Home | 홈 위치 |  |
| 15 | Auto Calculation | 자동 계산 |  |
| 16 | Auto calculation requested | 자동 계산을 요청했습니다 |  |
| 17 | Auto Method | 자동 방식 |  |
| 18 | By movement direction | 이동 방향별 |  |
| 19 | calculated | 계산됨 |  |
| 20 | Calculated Backlash | 계산된 백래시 |  |
| 21 | Cancel Align | 정렬 취소 |  |
| 22 | Command sent | 명령을 전송했습니다 |  |
| 23 | Confidence | 신뢰도 |  |
| 24 | Confirm Point | 정렬점 확정 |  |
| 25 | Connection | 연결 |  |
| 26 | Connection Type | 연결 방식 |  |
| 27 | Continue Motion Test | 이동 테스트 계속 |  |
| 28 | Continue requested | 계속을 요청했습니다 |  |
| 29 | Could not read current values | 현재 값을 읽을 수 없습니다 |  |
| 30 | Current Align Star | 현재 정렬 별 |  |
| 31 | Current Backlash | 현재 백래시 |  |
| 32 | Current INDI Driver State | 현재 INDI 드라이버 상태 |  |
| 33 | direction delta | 방향 편차 |  |
| 34 | East | 동 |  |
| 35 | Effective Coordinates | 적용 좌표 | 적용/유효 좌표 |
| 36 | Elevation m | 해발고도 m | 해발고도(천구 고도 아님) |
| 37 | Estimated | 추정 |  |
| 38 | excluded | 제외 |  |
| 39 | filtered | 필터됨 |  |
| 40 | Filtered | 필터됨 |  |
| 41 | Filtered %(filtered)s / %(sample)s, warm-up %(warmup)s, excluded %(excluded)s, spread %(spread)s%%, direction delta %(delta)s%% | 필터 %(filtered)s / %(sample)s, 워밍업 %(warmup)s, 제외 %(excluded)s, 산포 %(spread)s%%, 방향 편차 %(delta)s%% |  |
| 42 | Forward SkySafari Align/Sync to INDI even when GoTo forwarding is off | GoTo 전달이 꺼져 있어도 SkySafari 정렬/동기화를 INDI로 전달 |  |
| 43 | Forward SkySafari GoTo and Align/Sync to INDI | SkySafari GoTo 및 정렬/동기화를 INDI로 전달 |  |
| 44 | German EQ | 독일식 EQ | EQ 원문 유지(독일식 적도의) |
| 45 | GoTo / Guide Settings | GoTo / Guide 설정 |  |
| 46 | GoTo / Guide Status | GoTo / Guide 상태 |  |
| 47 | GoTo Selected Star | 선택한 별로 GoTo |  |
| 48 | GPS Lock | GPS 고정 |  |
| 49 | Guide Error | 가이드 오차 |  |
| 50 | Guide State | 가이드 상태 |  |
| 51 | Hold | 유지 | 누름 유지 버튼 |
| 52 | Home State | 홈 상태 |  |
| 53 | INDI Driver | INDI 드라이버 |  |
| 54 | INDI Driver Readback | INDI 드라이버 리드백 |  |
| 55 | INDI GoTo | INDI GoTo |  |
| 56 | INDI Profile | INDI 프로파일 |  |
| 57 | INDI Server Host | INDI 서버 호스트 |  |
| 58 | INDI Server Port | INDI 서버 포트 |  |
| 59 | INDI Web Manager | INDI Web Manager |  |
| 60 | Last Action | 마지막 동작 |  |
| 61 | Location and Time | 위치 및 시간 |  |
| 62 | Location Source | 위치 소스 |  |
| 63 | Location to Send | 전송할 위치 |  |
| 64 | mag | mag | 단위 라벨, 원문 유지(등급 미사용) |
| 65 | Manual | 수동 |  |
| 66 | Manual entry | 수동 입력 |  |
| 67 | Manual IP or Host | 수동 IP 또는 호스트 |  |
| 68 | Manual Serial Port | 수동 시리얼 포트 |  |
| 69 | Motion Command | 이동 명령 |  |
| 70 | Motion Test Details | 이동 테스트 상세 |  |
| 71 | Motion Test Repeats | 이동 테스트 반복 |  |
| 72 | Multi-Point Align | 다지점 정렬 |  |
| 73 | NE | 북동 |  |
| 74 | Network Host | 네트워크 호스트 |  |
| 75 | Network Port | 네트워크 포트 |  |
| 76 | Network TCP | 네트워크 TCP |  |
| 77 | No /dev/serial/by-id, /dev/ttyUSB, or /dev/ttyACM ports are currently visible. Connect the USB-serial cable and reload this page, or use manual entry if the port name is known. | 현재 보이는 /dev/serial/by-id, /dev/ttyUSB, /dev/ttyACM 포트가 없습니다. USB-시리얼 케이블을 연결하고 이 페이지를 다시 불러오거나, 포트 이름을 알면 수동 입력을 사용하세요. |  |
| 78 | No driver | 드라이버 없음 |  |
| 79 | No USB serial ports detected | 감지된 USB 시리얼 포트 없음 |  |
| 80 | North | 북 |  |
| 81 | Not at Home | 홈 위치 아님 |  |
| 82 | NW | 북서 |  |
| 83 | One-shot solve refine after INDI GoTo | INDI GoTo 후 1회 해석 정밀 보정 |  |
| 84 | OnStep Location | OnStep 위치 |  |
| 85 | OnStep Network Device | OnStep 네트워크 장치 |  |
| 86 | OnStep TCP Port | OnStep TCP 포트 |  |
| 87 | OnStep UTC Time | OnStep UTC 시간 |  |
| 88 | Park State | 파크 상태 |  |
| 89 | Parked | 파크됨 |  |
| 90 | Parking | 파크 중 |  |
| 91 | Parking Failed | 파크 실패 |  |
| 92 | PiFinder Mount Type | PiFinder 마운트 종류 |  |
| 93 | PiFinder UTC Time | PiFinder UTC 시간 |  |
| 94 | Polar / EQ | 적도의 / EQ | EQ 원문 유지 |
| 95 | Raw Mount Status | 원시 마운트 상태 |  |
| 96 | Reading | 읽는 중 |  |
| 97 | Records | 기록 | 기록 수 |
| 98 | Recovery | 복구 |  |
| 99 | Refine Accuracy arcmin | 정밀 보정 정확도 arcmin |  |
| 100 | Reload Current Values | 현재 값 다시 읽기 |  |
| 101 | Restart requested | 재시작을 요청했습니다 |  |
| 102 | Restarting INDI | INDI 재시작 중 |  |
| 103 | Save Backlash | 백래시 저장 |  |
| 104 | SE | 남동 |  |
| 105 | Send Location and Time | 위치 및 시간 전송 |  |
| 106 | Serial Mode | 시리얼 모드 |  |
| 107 | Serial Port | 시리얼 포트 |  |
| 108 | Service | 서비스 |  |
| 109 | SkySafari LX200 Mount Code | SkySafari LX200 마운트 코드 |  |
| 110 | SkySafari Mount Mode | SkySafari 마운트 모드 |  |
| 111 | Slew Rate | 슬루 속도 |  |
| 112 | Solved-coordinate GoTo round-trip test; repeated return/offset GoTo moves are recorded and filtered by direction before recommending candidate backlash values. | 해석 좌표 GoTo 왕복 테스트. 반복적인 복귀/오프셋 GoTo 이동을 기록하고 방향별로 필터링한 뒤 후보 백래시 값을 추천합니다. |  |
| 113 | South | 남 |  |
| 114 | Spread | 산포 |  |
| 115 | Start Align | 정렬 시작 |  |
| 116 | Start Motion Test | 이동 테스트 시작 |  |
| 117 | Starting auto calculation | 자동 계산 시작 |  |
| 118 | STOP | 정지 |  |
| 119 | Stop | 정지 |  |
| 120 | Stopping motion test | 이동 테스트 정지 중 |  |
| 121 | SW | 남서 |  |
| 122 | TCP Mode | TCP 모드 |  |
| 123 | Tracking Guide GoTo Recovery (re-slew when off target by more than 3 deg) | 추적 가이드 GoTo 복구 (목표에서 3° 이상 벗어나면 재슬루) |  |
| 124 | Unparked | 언파크됨 |  |
| 125 | Updated | 업데이트됨 |  |
| 126 | USB Serial | USB 시리얼 |  |
| 127 | USB Serial Port | USB 시리얼 포트 |  |
| 128 | Use SkySafari Align to calibrate IMU before solve | 해석 전 SkySafari 정렬로 IMU 보정 |  |
| 129 | UTC Time | UTC 시간 |  |
| 130 | Waiting at Home | 홈에서 대기 중 |  |
| 131 | warm-up | 워밍업 |  |
| 132 | West | 서 |  |
| 133 | Working | 처리 중 |  |

## `views/livecam.html`  (41개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | Accepted / Rejected | 채택 / 거부 |  |
| 2 | Actual size | 실제 크기 |  |
| 3 | Apply | 적용 |  |
| 4 | Bayer 2x2 Avg | Bayer 2x2 평균 |  |
| 5 | Color | 컬러 |  |
| 6 | Color Mode | 컬러 모드 |  |
| 7 | Cropped RAW | 잘라낸 RAW |  |
| 8 | Display Shape | 표시 형상 |  |
| 9 | Display Size (0 = Original) | 표시 크기 (0 = 원본) |  |
| 10 | Download | 다운로드 |  |
| 11 | Frame Count | 프레임 수 |  |
| 12 | Frame Source | 프레임 소스 |  |
| 13 | High Percentile | 상위 백분위 |  |
| 14 | Image Format | 이미지 형식 |  |
| 15 | Image zoom | 이미지 확대 |  |
| 16 | Input Frame | 입력 프레임 |  |
| 17 | Last Error | 마지막 오류 |  |
| 18 | Latest RAW Preview | 최신 RAW 미리보기 |  |
| 19 | Live Stack | 라이브 스택 |  |
| 20 | LiveCam preview | LiveCam 미리보기 |  |
| 21 | Low Percentile | 하위 백분위 |  |
| 22 | Max | 최대 |  |
| 23 | Mean | 평균 |  |
| 24 | Original RAW | 원본 RAW |  |
| 25 | Output | 출력 |  |
| 26 | Preview | 미리보기 |  |
| 27 | Preview Mode | 미리보기 모드 |  |
| 28 | Processing is off or no RAW frame is available. | 처리가 꺼져 있거나 사용 가능한 RAW 프레임이 없습니다. |  |
| 29 | Processing On | 처리 켜짐 |  |
| 30 | Raw Display | RAW 표시 | RAW 표시(프리뷰 모드) |
| 31 | RAW Shape | RAW 형상 |  |
| 32 | RAW Type | RAW 타입 |  |
| 33 | Reset Defaults | 기본값으로 초기화 |  |
| 34 | Reset Stack | 스택 초기화 |  |
| 35 | Stack Frames (Max 500) | 스택 프레임 (최대 500) |  |
| 36 | Stack Mode | 스택 모드 |  |
| 37 | Stack On | 스택 켬 |  |
| 38 | Stretched | 스트레치 |  |
| 39 | Sum | 합산 |  |
| 40 | Zoom in | 확대 |  |
| 41 | Zoom out | 축소 |  |

## `views/location_form.html`  (6개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | City / Place | 도시 / 지역 |  |
| 2 | Country | 국가 |  |
| 3 | County / District | 군 / 구 |  |
| 4 | Lookup Coordinates | 좌표 조회 |  |
| 5 | Manual Entry | 수동 입력 |  |
| 6 | State / Province | 주 / 도 |  |

## `views/locations.html`  (1개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | Could not load location catalog | 위치 카탈로그를 불러올 수 없습니다 |  |

## `views/network.html`  (28개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | 8-63 characters; leave blank to keep current password | 8~63자. 비워 두면 현재 비밀번호를 유지합니다 |  |
| 2 | AP Connected Devices | AP 연결 장치 |  |
| 3 | AP IP Address | AP IP 주소 |  |
| 4 | AP Password | AP 비밀번호 |  |
| 5 | AP Security | AP 보안 |  |
| 6 | AP+STA | AP+STA |  |
| 7 | Clients will reconnect using this address after restart | 재시작 후 클라이언트가 이 주소로 다시 연결됩니다 |  |
| 8 | Connected | 연결됨 |  |
| 9 | Hide current AP password | 현재 AP 비밀번호 숨기기 |  |
| 10 | Host | 호스트 |  |
| 11 | IP | IP |  |
| 12 | Lease only | 리스만 | DHCP 리스 |
| 13 | Link | 링크 |  |
| 14 | MAC | MAC |  |
| 15 | Nearby Wi-Fi | 주변 Wi-Fi |  |
| 16 | No AP clients are currently visible. | 현재 보이는 AP 클라이언트가 없습니다. |  |
| 17 | No current AP password is set. | 현재 AP 비밀번호가 설정되어 있지 않습니다. |  |
| 18 | Off by default. Internet sharing may add system load and can be slow. | 기본값은 꺼짐. 인터넷 공유는 시스템 부하를 늘리고 느려질 수 있습니다. |  |
| 19 | Open | 개방 |  |
| 20 | Prefer 2.4 GHz | 2.4 GHz 우선 |  |
| 21 | Prefer 5 GHz | 5 GHz 우선 |  |
| 22 | Select scanned SSID | 검색된 SSID 선택 |  |
| 23 | Share STA internet in AP+STA mode | AP+STA 모드에서 STA 인터넷 공유 |  |
| 24 | Show current AP password | 현재 AP 비밀번호 표시 |  |
| 25 | STA Band Preference | STA 대역 우선순위 |  |
| 26 | Unchanged | 변경 없음 |  |
| 27 | Use 2.4 GHz when AP+STA clients require a 2.4 GHz AP channel | AP+STA 클라이언트가 2.4 GHz AP 채널을 요구할 때 2.4 GHz 사용 |  |
| 28 | WPA2 Password | WPA2 비밀번호 |  |

## `views/remote.html`  (1개)

| # | English | 한글 | 비고 |
| --- | --- | --- | --- |
| 1 | Long | 경도 | Longitude로 해석 |
