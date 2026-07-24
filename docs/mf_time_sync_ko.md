# MF PiFinder 시간 동기화

이 문서는 PiFinder의 시간 동기화 구조를 설명합니다. Linux system clock의
유일한 관리자는 `chronyd`입니다. chronyd는 NTP pool(네트워크가 있을 때)과
gpsd가 공급하는 GPS SHM refclock(현장) 중 더 정확한 소스를 자동 선택합니다.
PiFinder는 시계를 직접 쓰지 않고, `chronyc tracking` 상태와 GPS 시간 후보를
관찰해 UI에 표시하며, 필요한 경우 RTC 동기화 요청만 root helper에 전달합니다.

2026-07-25 축소 개편
([mf_field_test_20260724_analysis_ko.md](mf_field_test_20260724_analysis_ko.md)
A3): PiFinder 자체 SNTP client, Software PPS, 직접 system clock 쓰기
(`Clock Manager = PiFinder`), `Best/GPS/NTP` 소스 모드를 제거했습니다. 소스
선택은 chronyd의 역할이고 PiFinder는 관찰자입니다. `Time Sync`는 기본
`On`입니다(관찰 전용이라 위험이 없습니다).

## 시간 공급 체인 (현장 기준)

```text
u-blox GPS ── /dev/ttyAMA3 ──> gpsd (-n 필수) ──┬─> NTP SHM(0) ──> chronyd
                                                └─> TCP 2947 ──> PiFinder gps_ubx
인터넷(있을 때) ── NTP pool ──────────────────────────────────────> chronyd
chronyd ──> system clock (유일한 쓰기 주체)
```

핵심 시스템 설정(`scripts/install_chrony_time_sync.sh install|configure`가
멱등 관리):

- `/etc/default/gpsd`: `GPSD_OPTIONS="-n -s <baud>"` — `-n`이 없으면 gpsd가
  클라이언트 워치만으로는 NTP SHM에 시간을 쓰지 않는다(2026-07-25 실측).
- `/etc/chrony/chrony.conf`: `refclock SHM 0 poll 3 refid gps1`,
  `makestep 1 -1` — RTC 없는 보드에서 부팅 한참 뒤 GPS fix가 와도 큰
  오프셋을 즉시 스텝한다.

## UI 설정

설정 위치:

```text
Settings > Advanced > Time Sync
```

상태 확인 위치:

```text
Tools > Place & Time > Time Sync
```

UI 항목:

| UI 항목 | 설정 키 | 기본값 | 의미 |
| --- | --- | --- | --- |
| `Time Sync` | `time_sync_enabled` | `On` | 시간 동기 관찰/표시 전체 스위치 |
| `Chrony Source` | `chrony_time_sync` | `On` | chronyd 상태 관찰 |
| `GPS Source` | `gps_time_sync` | `On` | GPS 시간 후보 관찰(진단용) |
| `RTC Sync` | `rtc_sync` | `Off` | 선택된 시간으로 RTC 동기화 요청 (RTC 하드웨어 도입 시 사용) |

## 기본 설정 값

`default_config.json`의 주요 기본값:

```json
"time_sync_enabled": true,
"chrony_time_sync": true,
"chrony_poll_interval_seconds": 30,
"chrony_timeout_seconds": 1.0,
"chrony_stale_seconds": 120,
"gps_time_sync": true,
"gps_time_sync_min_samples": 5,
"gps_time_sync_window_seconds": 120,
"gps_time_sync_stale_seconds": 30,
"gps_time_sync_max_tacc_ns": 1000000000,
"gps_time_sync_stable_jitter_ms": 250,
"gps_time_sync_stable_offset_ms": 1000,
"rtc_sync": false,
"rtc_sync_min_interval_seconds": 3600
```

구버전 config에 남아 있는 `ntp_*`, `software_pps*`, `time_sync_source_mode`,
`time_sync_clock_manager`, `time_sync_system_clock*` 키는 무시됩니다.

## 선택/상태 모델

- `selected`(선택된 시간 소스)는 chronyd가 동기 상태(`stable`)일 때의
  Chrony 후보 하나뿐입니다. chronyd가 비동기(`unsynced`)면 `selected`는
  비어 있고, 상태는 chrony/GPS 관찰 상태를 그대로 보여줍니다.
- GPS 후보는 진단용으로만 관찰합니다: `tAcc`, 샘플 jitter, stale 여부로
  `stable/collecting/low_quality/unstable/stale`을 판정합니다.
- "부팅 후 한 번도 동기된 적 없는 시계"(fake-hwclock 잔재) 감지와 마운트
  time sync 보류 게이트는 분석 문서 A4 항목으로 추가 예정입니다.

## System Clock과 RTC

chronyd가 system clock을 관리하므로 PiFinder는 시계를 쓰지 않습니다.

chronyd 설치/설정:

```bash
cd ~/PiFinder
./scripts/install_chrony_time_sync.sh install    # 설치 + 시간 체인 설정
./scripts/install_chrony_time_sync.sh configure  # 시간 체인 설정만(멱등)
./scripts/install_chrony_time_sync.sh status
```

RTC를 도입해 실제 쓰기를 허용하려면 root helper가 필요합니다:

```bash
cd ~/PiFinder
./scripts/install_gps_time_sync_helper.sh enable-dry-run   # 검증
./scripts/install_gps_time_sync_helper.sh enable           # 실제 쓰기
```

helper는 요청 파일을 검증한 뒤에만 `/usr/sbin/hwclock`을 실행합니다.
요청은 같은 부팅 세션의 최신 요청인지, 선택된 시간 소스가 유효한지
확인됩니다. (helper의 system clock 쓰기 경로는 모니터가 더 이상 요청하지
않으며, RTC 도입 결정(A6) 시 함께 정리합니다.)

## 상태 파일

```text
/dev/shm/pifinder/gps_time_status.json      # 상태(tmpfs, 재부팅 시 소실)
~/PiFinder_data/gps_time_sync_request.json  # helper 요청
~/PiFinder_data/gps_time_sync_helper_status.json
```

주요 항목:

| 항목 | 의미 |
| --- | --- |
| `state` / `message` | 통합 상태 (chrony 선택 여부 반영) |
| `clock_manager` | 항상 `chrony` |
| `selected` | 현재 선택된 시간 소스(Chrony) 또는 null |
| `chrony` | chronyd tracking 상태 |
| `latest` / `offset` / `samples` | 마지막 GPS 시간 샘플과 통계 |
| `sources.chrony` / `sources.gps` | 소스별 상태와 후보 |
| `rtc_sync` | RTC 동기화 요청 상태 |
| `helper` | root helper의 마지막 처리 결과 |

## 테스트

단위 테스트:

```bash
cd ~/PiFinder/python
pytest tests/test_gps_time_sync.py tests/test_gps_time_sync_helper.py \
  tests/test_gps_time_sync_status_ui.py tests/test_gps_time_sources.py -q
```

실기 상태 확인:

```bash
chronyc tracking
chronyc sources -v      # gps1 Reach가 0이 아니어야 GPS 공급 정상
watch -n 1 cat /dev/shm/pifinder/gps_time_status.json
```
