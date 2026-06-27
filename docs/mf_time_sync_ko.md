# MF PiFinder 시간 동기화

이 문서는 PiFinder의 통합 시간 동기화 기능을 설명합니다. Linux system clock의 기본 관리자는 `chronyd`입니다. PiFinder는 `chronyc tracking` 상태를 읽어 UI에 표시하고, GPS/PiFinder SNTP 후보를 보조로 관찰하며, 필요한 경우 RTC 동기화와 소프트웨어 PPS tick을 관리합니다.

기본값은 안전을 위해 전체 기능이 `Off`입니다. `Time Sync`를 `On`으로 바꾸면 기본 소스 모드는 `Chrony`이며, system clock은 계속 chronyd가 관리합니다. PiFinder 자체 SNTP는 chronyd와 중복되지 않도록 기본 `Off`이고, 필요할 때 fallback/check 용도로 켤 수 있습니다.

## UI 설정

설정 위치:

```text
Settings > Advanced > Time Sync
```

상태 확인 위치:

```text
Tools > Place & Time > Time Sync
```

주요 UI 항목:

| UI 항목 | 설정 키 | 기본값 | 의미 |
| --- | --- | --- | --- |
| `Time Sync` | `time_sync_enabled` | `Off` | 통합 시간 동기화 전체 스위치 |
| `Source Mode` | `time_sync_source_mode` | `Chrony` | `Chrony`, `Best`, `GPS`, `NTP` 중 선택 |
| `Clock Manager` | `time_sync_clock_manager` | `Chrony` | Linux system clock 관리자를 `Chrony`, `PiFinder`, `Off` 중 선택 |
| `Chrony Source` | `chrony_time_sync` | `On` | chronyd 상태를 PiFinder 시간 소스로 관찰 |
| `GPS Source` | `gps_time_sync` | `On` | GPS 시간 후보 관찰 |
| `PiFinder NTP` | `ntp_time_sync` | `Off` | PiFinder 자체 SNTP 후보 관찰 |
| `PiFinder NTP Server` | `ntp_server`, `ntp_server_custom` | `pool.ntp.org` | PiFinder 자체 SNTP 서버 목록 선택 또는 커스텀 서버 입력 |
| `RTC Sync` | `rtc_sync` | `Off` | 선택된 시간으로 RTC 동기화 요청 |
| `Software PPS` | `software_pps` | `Off` | 소프트웨어 주기 tick 생성 |

NTP 서버 기본 목록:

```text
pool.ntp.org
time.google.com
time.cloudflare.com
time.nist.gov
Custom
```

`Custom`을 선택하면 바로 서버 주소 입력 화면이 열립니다. 입력을 저장하면 `PiFinder NTP Server`는 자동으로 `Custom`으로 설정되고, 입력한 주소는 `ntp_server_custom`에 저장됩니다. 이 설정은 PiFinder 자체 SNTP 확인용이며, chronyd의 서버 목록을 직접 변경하지 않습니다.

## 기본 설정 값

`default_config.json`의 주요 기본값은 다음과 같습니다.

```json
"time_sync_enabled": false,
"time_sync_source_mode": "chrony",
"time_sync_clock_manager": "chrony",
"chrony_time_sync": true,
"chrony_poll_interval_seconds": 30,
"chrony_timeout_seconds": 1.0,
"chrony_stale_seconds": 120,
"gps_time_sync": true,
"ntp_time_sync": false,
"ntp_server": "pool.ntp.org",
"ntp_server_custom": "",
"ntp_poll_interval_seconds": 300,
"ntp_timeout_seconds": 1.0,
"ntp_max_delay_ms": 1500,
"ntp_stale_seconds": 900,
"rtc_sync": false,
"software_pps": false
```

## 선택 방식

`Chrony` 모드에서는 chronyd가 관리 중인 system clock 상태를 선택합니다. PiFinder는 `chronyc tracking` 결과의 stratum, reference, leap 상태, offset 정보를 상태 파일과 UI에 표시합니다.

`Best` 모드에서는 Chrony, 안정적인 GPS 후보, 유효한 PiFinder SNTP 후보를 비교합니다. GPS는 `valid`, `tAcc`, 최근 샘플 jitter, stale 여부를 기준으로 판단합니다. PiFinder SNTP는 응답 유효성, stratum, 왕복 지연, root dispersion, stale 여부를 기준으로 판단합니다.

여러 후보가 모두 사용할 수 있으면 추정 품질값이 더 작은 소스를 선택합니다. PiFinder SNTP 지연이 `ntp_max_delay_ms`보다 크면 `low_quality`로 표시되고 선택 후보에서 제외됩니다.

## System Clock과 RTC

권장 구조는 `chronyd`가 Linux system clock을 관리하고, PiFinder는 상태 표시와 RTC 보조 동기화만 수행하는 방식입니다.

chronyd 설치 및 상태 확인:

```bash
cd ~/PiFinder
./scripts/install_chrony_time_sync.sh install
./scripts/install_chrony_time_sync.sh status
```

PiFinder 본체는 일반 사용자 권한으로 실행됩니다. RTC를 실제로 쓰거나 `Clock Manager`를 `PiFinder`로 바꿔 legacy system clock 쓰기를 사용하려면 별도 root helper 서비스가 필요합니다.

실외 최종 테스트 전에는 helper를 dry-run으로 먼저 확인하는 것을 권장합니다.

```bash
cd ~/PiFinder
./scripts/install_gps_time_sync_helper.sh enable-dry-run
```

RTC 실제 쓰기를 허용하려면 다음으로 전환합니다.

```bash
cd ~/PiFinder
./scripts/install_gps_time_sync_helper.sh enable
```

helper는 요청 파일을 검증한 뒤에만 `/usr/sbin/hwclock`을 실행합니다. `Clock Manager`가 `PiFinder`로 명시된 legacy/fallback 모드에서는 `/usr/bin/date`를 통한 system clock 쓰기도 처리할 수 있습니다. 요청은 같은 부팅 세션의 최신 요청인지, 선택된 시간 소스가 유효한지, 모니터 상태가 `stable`인지 확인됩니다.

## 상태 파일

상태 파일은 기존 경로를 유지합니다.

```text
~/PiFinder_data/gps_time_status.json
```

주요 항목:

| 항목 | 의미 |
| --- | --- |
| `state` | 통합 시간 동기화 상태 |
| `clock_manager` | system clock 관리 방식 |
| `selected` | 현재 선택된 시간 소스와 시간 |
| `chrony` | chronyd tracking 상태 |
| `latest` | 마지막 GPS 시간 샘플 |
| `ntp` | 마지막 PiFinder SNTP 조회 결과 |
| `sources.chrony` | chronyd 소스 상태와 후보 |
| `sources.gps` | GPS 소스 상태와 후보 |
| `sources.ntp` | PiFinder SNTP 소스 상태와 후보 |
| `system_clock_sync` | PiFinder helper system clock 요청 상태 |
| `rtc_sync` | RTC 동기화 요청 상태 |
| `software_pps` | 소프트웨어 PPS tick 상태 |
| `helper` | root helper의 마지막 처리 결과 |

helper 요청 파일:

```text
~/PiFinder_data/gps_time_sync_request.json
```

helper 상태 파일:

```text
~/PiFinder_data/gps_time_sync_helper_status.json
```

파일명은 기존 설치와 helper 서비스 호환성을 위해 유지합니다.

## 테스트

단위 테스트:

```bash
cd ~/PiFinder/python
pytest tests/test_gps_time_sync.py tests/test_gps_time_sync_helper.py tests/test_gps_time_sync_status_ui.py -q
```

실기 상태 확인:

```bash
chronyc tracking
chronyc sources -v
watch -n 1 cat ~/PiFinder_data/gps_time_status.json
```
