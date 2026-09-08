# u-blox GPS 간헐적 식별 실패 — 장애 분석 및 자동 복구 개선 보고서

| 항목 | 내용 |
|---|---|
| 보고서 ID | MF-GPS-20260909-01 |
| 작성일 / 시간대 | 2026-09-09 / KST (UTC+09:00) |
| 대상 | `mfpi5`, Raspberry Pi 5, PiFinder UBlox 백엔드 |
| 변경 전 기준 커밋 | `bad7cb30fe31ebd1ebd74e1f0d936f7edf4e1a65` |
| 요청 | GPS가 간헐적으로 인식되지 않는 원인 확인, 적절한 재발 대응 구현 및 정식 보고 |
| 상태 | 구현·149개 회귀 테스트·TCP 재현·실장 적용 완료; 자연 재발 시 자동 복구 성공률은 추후 관측 |

## 1. 결론

이번 장애에서 GPS 모듈은 정상적으로 NMEA를 송신했지만, gpsd가 장치를
`NMEA0183`로만 인식한 상태에 머물렀다. PiFinder의 `gps_type=ublox` 백엔드는
UBX 프레임만 해석하므로 수신 데이터가 있어도 GPS 통신·위성 상태에 반영되지 않았다.

실제 모듈에 설정 변경 없는 **UBX-MON-VER 버전 조회 1회**를 전달하자
`SPG 5.10 / PROTVER 34.10` 응답이 도착했고, gpsd가 `u-blox`로 식별한 뒤
정상 UBX 출력이 시작됐다. PiFinder 수신도 재개됐고, 이후 위성 3개를 사용하는
2D 위치 고정을 확인했다. 유형을 `GPSD (generic)`으로 바꿀 필요는 없었다.

확정한 직접 원인은 **u-blox 식별 및 UBX 출력 초기화가 완료되지 않은 상태의 지속**이다.
최초 식별 실패의 계기(부팅 시점, 모듈 준비 지연, 일시적인 응답 유실 등)는
해당 시점의 상세 로그가 없어 확정하지 않았다. 이번 보완은 그 원인을 추정해
설정을 바꾸는 대신, 관측된 장애 상태에 한정해 검증된 조회 동작을 재시도한다.

## 2. 장애 환경과 실측 증거

| 항목 | 관측 결과 |
|---|---|
| OS | Linux `6.12.93+rpt-rpi-2712`, aarch64 |
| gpsd | `3.22`, PID `1273`, `/usr/sbin/gpsd -s 115200 /dev/ttyAMA2` |
| UART | `uart2-pi5`, GPIO4 TXD2 / GPIO5 RXD2, 115200 bps |
| PiFinder | `gps_type=ublox`, `gps_port=auto`, `gps_baud_rate=115200` |
| 포트 소유 | `/dev/ttyAMA2`는 gpsd만 열고 있었음 |
| 서비스 상태 | gpsd와 PiFinder 모두 active, 로컬 TCP 2947 연결 유지 |
| 전압 상태 | `vcgencmd get_throttled`: `0x0` |
| 모듈 버전 응답 | `ROM SPG 5.10 (7b202e)`, HW `000A0000`, `PROTVER=34.10` |

시간순 진단 기록:

1. **03:29경**: gpsd/PiFinder 서비스, UART 설정·소유자와 커널 로그 확인.
   서비스 중단, 직렬 포트 중복 점유, 저전압 징후는 관측되지 않았다.
2. **03:30경, 18초 JSON 구독**: `SKY` 541건, `TPV` 180건.
   `mode=1`, 사용 위성 `uSat=0`. 수신 데이터 자체는 계속 도착했다.
3. **03:30경, 7초 raw 구독**: 48,447 bytes, UBX 동기 바이트 0건.
   `$GNRMC`, `$GNGGA`, `$GNGSA`, `$GPGSV` 등 NMEA 문장만 확인했다.
   GGA의 fix quality는 `0`, 위성 수는 `00`이었다.
4. **03:33:21경**: gpsd를 통해 MON-VER 조회 1회 전송.
   수신기 리셋, baud 변경, 영구 설정 저장, 서비스 재시작은 수행하지 않았다.
5. **이후 8초**: MON-VER 응답 4건과 UBX 설정 응답 및 NAV 메시지가 수신됐다.
   NAV-PVT / NAV-POSECEF / NAV-VELECEF / NAV-SAT 각각 79건,
   NAV-DOP / NAV-TIMEGPS / NAV-EOE 각각 78건, 체크섬 오류 0건.
   추가 응답과 설정 명령은 재식별 후 gpsd의 초기화 과정에서 발생했다.
   `DEVICES`가 `driver=u-blox`, `native=1`로 변경됐다.
6. **03:33:22**: PiFinder 메인 로그에 해당 실행의 첫 `GPS Time` 기록.
7. **03:33:58–03:34:09경, 10초 재확인**: TPV 100건 모두 `mode=2`,
   사용 위성 3개, 관측 구간 최고 C/N0 29. PiFinder 상태 파일의 최신
   `NAV-PVT` 수신도 확인했다.

초기 진단 수치는 대화 중 실행한 명령 출력에서 옮긴 값이며, 당시 raw 전체
스트림을 파일로 보관한 것은 아니다. 위성 위치 고정은 통신 복구 후 별도로
관측된 결과이며, 버전 조회만으로 위성 수신 품질이 개선됐다고 단정하지 않는다.

## 3. 원인 분석과 수정 범위

### 3.1 확인한 경로

```text
u-blox가 NMEA 송신
  → gpsd가 NMEA0183 상태에 머묾
  → PiFinder UBX 파서에는 해석 가능한 UBX가 없음
  → GPS 미수신처럼 표시되고 자동 재식별 경로도 없음

MON-VER 조회
  → 유효한 UBX 버전 응답
  → gpsd의 u-blox 식별 및 정상 출력 초기화
  → PiFinder NAV 메시지 처리 재개
```

gpsd 3.22는 u-blox 식별 이벤트에서 출력 모드를 설정하고, 초기 버전 조회로
MON-VER를 사용한다. 이는 관측된 복구 순서와 일치한다.
[gpsd 3.22 u-blox 드라이버](https://raw.githubusercontent.com/ntpsec/gpsd/release-3.22/drivers/driver_ubx.c)

### 3.2 함께 발견한 스트림 처리 결함

- WATCH 직후 응답을 별도로 읽고 버리면, 같은 TCP read에 합쳐진 첫 UBX
  프레임과 장치 보고도 버릴 수 있었다. 이제 정규 파서가 모두 처리한다.
- `0xB5`와 `0x62`가 TCP read 경계에서 나뉘면 기존 검색 경로가 `0xB5`를
  버릴 수 있었다. 마지막 `0xB5`를 다음 read까지 보존한다.

이 두 항목은 코드 검토와 회귀 테스트로 확인한 견고성 개선이다.
이번 실기 장애의 최초 원인으로 입증된 것은 아니다.

## 4. 구현 명세

| 조건 / 제한 | 구현 |
|---|---|
| 적용 경로 | 실제 gpsd에 연결된 UBX 백엔드만 |
| 대상 선택 | gpsd `DEVICES`가 단일 로컬 `/dev/` 장치를 보고하고 driver가 `NMEA0183` 또는 `u-blox`일 때 |
| NMEA 판정 | 완전한 RMC/GGA/GSA/GSV/GLL/VTG/ZDA 문장, XOR 체크섬 검증 |
| 최초 조회 | 유효한 NMEA 3건 이상, 10초 이상 지속, 유효 UBX 부재 |
| 수신 간격 | NMEA 간격이 5초를 넘으면 관측 구간 초기화 |
| 재조회 간격 | 최소 30초, 벽시계 대신 monotonic 사용 |
| 시도 상한 | TCP 연결당 3회, 쓰기 실패도 포함 |
| 성공 시 | 유효 UBX가 오면 관측 구간 초기화, 누적 시도 횟수는 유지 |
| 상한 이후 | 경고 1회 기록 후 추가 조회 중단, NMEA 관측은 계속 |
| 전송 | 명시적 장치 경로를 포함한 gpsd `?DEVICE`의 `hexdata` |
| 명령 바이트 | `b5 62 0a 04 00 00 0e 34` — UBX-MON-VER poll |
| drain 제한 | 최대 2초; 연결이 끊기면 기존 종료·재연결 경로 사용 |
| 진단 표시 | NMEA만 수신한 read에서 `?NMEA` 통신 이벤트 발행 |
| 로그 | `GPS.parser.recovery`, 기본 설정에서도 보이는 WARNING |

전송 형식은 gpsd 문서의 MON-VER 예제와 동일하며, 장치 경로는 보고된 값을 사용한다.
[gpsd DEVICE 명령](https://gpsd.io/gpsd_json.html#_device)

정상 UBX, 무응답, 잡음, 체크섬 오류만 있는 입력, 모호한 복수 장치,
읽기 전용 장치 보고, 닫힌 writer, 기록 파일 재생에는 복구 명령을 보내지 않는다.
바이너리 payload 내부의 NMEA처럼 보이는 문자열도 탐지 대상에서 제외한다.
일반 GPSD 백엔드 및 위치·시간 유효성 판정은 변경하지 않는다.

조회는 gpsd를 경유한다. 직접 UART 접근, gpsd 재시작, 수신기 리셋,
GPS aiding 주입, 플래시 저장 명령을 추가하지 않았다. 단, **gpsd가 장치를
재식별하면 기존 기능에 따라 수신기의 현재 출력 설정을 변경할 수 있다.**
`?NMEA`는 위치·시간·위성 수를 갱신하는 데이터가 아니라 통신 진단용이다.

변경 파일:

- `python/PiFinder/gps_ubx_recovery.py`: NMEA 관측, 장치 선택, 재시도 제한.
- `python/PiFinder/gps_ubx_parser.py`: 스트림 보존, 탐지·조회 통합, `?NMEA` 이벤트.
- `python/tests/test_gps_ubx_recovery.py`: 장애·정상·경계 조건 회귀 테스트.
- `python/tests/test_gps_ubx_dispatch.py`: 새 이벤트 전달 및 위치/시간 비간섭 검증.
- `python/tests/test_gps_time_sources.py`: 기존 comms 선행 이벤트를 반영한 테스트 교정.
- `docs/ax/gps/CONTEXT.md`: 현행 동작 계약.
- 본 보고서 및 개발 문서 인덱스: 장애·검증 기록과 진입점.

## 5. 검증 결과

### 5.1 자동 회귀 및 정적 검사

다음 9개 테스트 파일에서 **149개 통과(3.52초)**:

```sh
cd /home/pifinder/PiFinder/python
pytest -q tests/test_gps_ubx_recovery.py tests/test_gps_ubx_parser.py \
  tests/test_gps_ubx_dispatch.py tests/test_status_gps_comms.py \
  tests/test_gps_time_sources.py tests/test_gps_time_sync.py \
  tests/test_gps_time_sync_status_ui.py tests/test_server_gps_update.py \
  tests/test_gps_time_sync_helper.py
```

신규 검증에는 10/40/70초 조회·상한, 복구 응답 후 정상 NAV 처리, JSON/NMEA/UBX
분할 수신, 정상 UBX와 NMEA 혼합, 바이너리 payload 안의 NMEA, 잡음·체크섬
오류·무응답, 복수 장치·다른 driver·읽기 전용 보고, 파일 재생,
drain 시간 초과·BrokenPipe, 복구 반복 시 예산 유지, 텍스트 버퍼 상한을 포함했다.

변경 Python 5개 파일의 `ruff check`와 `ruff format` 확인도 통과했다.

첫 회귀 실행에서는 137개 통과·기존 시간 테스트 2개 실패가 있었다.
수정 전 HEAD의 파서를 임시 로드해 두 실패를 동일하게 재현했다.
두 테스트는 이미 존재하는 `comms` 이벤트를 무시하고 첫 큐 항목을 시간으로
가정하고 있었다. 이제 `("comms", "NAV-PVT")`를 먼저 검증한 뒤 기존 시간
내용 검증을 수행한다. 이 교정을 위해 운영 시간 처리 코드는 변경하지 않았다.

### 5.2 실제 TCP 및 정상 장치 검증

격리된 TCP gpsd 대역 서버를 만들고, 실제 `UBXParser.connect()` 및
`parse_messages()`를 실행했다. monotonic 시계나 대기 시간을 모의하지 않고
유효한 NMEA를 0.2초 간격으로 보냈다.

- **10.0608초 후 MON-VER 1회 전송**, 장치 경로와 명령 바이트 일치.
- NMEA 진단 이벤트 51건 후 버전 응답 이벤트 1건, NAV-EOE 1건 처리.
- `NMEA-only ... probe 1/3`, `UBX traffic resumed ...` 로그 확인.
- 이 서버는 gpsd 역할의 합성 서버이며 실제 gpsd 초기화 코드까지 재현한 것은 아니다.
  실제 gpsd의 재식별 동작 증거는 §2의 자연 발생 장애 복구 기록이다.

이후 수정된 파서를 추가 클라이언트로 실제 gpsd에 연결해 **12.0024초** 읽었다.

- NAV-PVT 119건, NAV-SAT 119건, NAV-DOP 120건 등 정상 메시지 처리.
- 해당 구간 PVT는 모두 2D, 체크섬 오류 0건, **자동 조회 0회**.
- NAV-VELECEF 등 기존 미지원 메시지는 기존 규약대로 `?0111`, `?0126`으로 표시.

검증 스크립트·JSON 결과는 §7의 증거 폴더에 보관했다. 시험 중 실제 모듈의
설정을 변경하거나 장애를 고의로 만들지 않았다.

### 5.3 실장 적용

적용 전 PiFinder PID는 `25064`, 마운트는 `usb_absent`, 이동 없음,
GoTo/Guide는 `idle`, 활성 추적 대상 없음이었다.

**03:46:51 KST**에 PiFinder 서비스만 재시작했다. 새 PID `120531`의 active,
`NRestarts=0`을 확인했다. gpsd PID는 전후 모두 `1273`으로 유지됐다.
GPS 설정은 `ublox / auto / 115200`으로 유지됐다.

03:47:00 메인 로그에서 GPS 시간 수신 재개를 확인했다. 03:47:29 상태 파일은
최신 `NAV-PVT` 수신 경과 약 0.0006초, 시간 샘플 누적 624건이었다.
이 시점의 `lock_type=0`으로 **위치 고정은 없는 상태**였다.
`valid=true`는 해당 시간 샘플의 유효성이고 위치 고정 성공을 뜻하지 않는다.
따라서 실장 판정은 UBX 통신·파서 재개 확인이며, 지속적인 위치 고정이나
위성 신호 개선까지 완료한 것으로 판정하지 않는다.

**03:48:35–03:48:40 KST**에 실제 gpsd를 별도로 5초 구독한 최종 재확인에서는
`driver=u-blox`, `native=1`이 유지됐으며 TPV 51건 모두 `mode=2`,
사용 위성 3개로 **2D 위치 고정이 다시 확인**됐다. 이 구간 최고 C/N0는 24였다.
적용 후 무고정→2D 변동도 함께 기록하며, 이를 3D 고정 또는 수신 품질 안정화로
확대 해석하지 않는다.

## 6. 한계와 후속 관측

- 최초 식별 응답이 누락된 이유는 미확정이다. 이번 자동 복구 로그로 이후의
  발생 시점·조회 횟수·응답 재개 여부를 남긴다.
- 3회 상한은 **연결 단위**이며 프로세스 수명 전체의 상한은 아니다.
  새 TCP 연결은 새 예산으로 시작한다. 정상 UBX 응답만으로 예산이 재충전되지는 않는다.
- 무응답, 실제 UART 배선 단절, 지속적인 깨진 바이너리는 이번 재시도의 대상이 아니다.
- UBX 통신 복구는 위성 위치 고정 또는 정밀도 확보를 보장하지 않는다.
  이번 초기 복구 후 확인한 상태도 3D가 아닌 2D였다.
- 정상 장치를 고의로 NMEA 전용 모드로 바꾸거나 전원을 반복 차단하지 않았다.
  자연 발생 장애의 실제 MON-VER 복구, 합성 장애 TCP 시험, 정상 장치 수신 검증을
  구분해 기록한다. 향후 실제 재발 시 자동 복구 성공률은 추가 관측이 필요하다.

## 7. 보관 자료와 원복

로컬 증거 폴더:

`/home/pifinder/PiFinder_data/captures/analysis/20260909_gps_ubx_recovery/`

- `baseline.txt`, `gps_ubx_parser.py.before`: 수정 기준 및 변경 전 운영 파서.
- `test_summary.json`: 테스트 실행 출력에서 옮긴 요약(원시 pytest 로그가 아님).
- `validate_tcp.py`, `tcp_validation.json`: TCP 재현·정상 장치 검증 코드 및 결과.
- `deployment.json`: 적용 전후 PID·시각·GPS 설정·상태, 운영 소스 SHA-256.
- `post_restart_gps_log.txt`, `post_restart_gpsd.json`: 적용 후 GPS 로그·별도 gpsd 구독 결과.

원복 시 다른 변경을 포함한 저장소 전체를 되돌리지 않는다. 이후 추가 수정이
없는지 확인한 뒤, 보관된 이전 `gps_ubx_parser.py`를 복원하고 PiFinder를
재시작하면 기존 수신 동작으로 돌아간다. 새 helper 파일은 이전 파서에서
참조하지 않는다. gpsd 설정·GPS 유형·baud 및 수신기 영구 설정의 원복은 필요 없다.
