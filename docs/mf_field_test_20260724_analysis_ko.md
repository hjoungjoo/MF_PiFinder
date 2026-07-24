# 2026-07-24 현장 테스트 장애 분석 및 수정 계획

> 상태: **분석 완료 — 수정 진행 중** (체크리스트로 항목별 진행)
> 관련 문서: [mf_indi_goto_guide_plan_ko.md](mf_indi_goto_guide_plan_ko.md),
> [mf_goto_mount_source_structure_ko.md](mf_goto_mount_source_structure_ko.md),
> [mf_mountcontrol_indi_flow_ko.md](mf_mountcontrol_indi_flow_ko.md),
> [mf_time_sync_ko.md](mf_time_sync_ko.md),
> [mf_goto_tracking_recovery_analysis_ko.md](mf_goto_tracking_recovery_analysis_ko.md)
>
> 작성: 2026-07-25. 근거 로그: `~/PiFinder_data/pifinder.log`(WARNING 레벨),
> systemd journal(현재 부팅), `/dev/shm/pifinder/*.json`(실내 검증 세션 스냅샷),
> `/etc/fake-hwclock.data`.

## 1. 증상 요약 (사용자 보고)

관측: 2026-07-24 21:00~23:00 KST, OnStepX Alt/Az + INDI. 23:10 귀가 후 전원
재부팅, 이후 로그/상태는 실내 검증 테스트의 것이다.

1. LCD GUI로 얼라인을 진행한 뒤 SkySafari와 카탈로그로 GoTo했을 때, 갑자기
   얼라인이 이상한 곳으로 정렬된 것처럼 보였다. 정상 사용 불가.
2. 이를 피하려고 INDI 마운트 전용으로 동작시키려 했으나 PiFinder 모드처럼
   동작하는 것으로 보였고, 실내에서 같은 설정으로 재현하면 손으로 망원경을
   움직일 때 IMU에 따라 재정렬이 진행됐다.

## 2. 운용 원칙 (용어 확정)

이 문서와 이후 작업에서 용어를 다음처럼 구분한다.

- **정렬** = INDI mount sync (`ON_COORD_SET.SYNC`). 마운트의 좌표 프레임을
  맞추는 동작.
- **얼라인** = PiFinder 내부 정렬 (plate-solve 기반 target_pixel/aligned 축).
  LCD 화면에서 사용자가 수행하는 동작.

사용자 원칙:

1. **정렬은 INDI 마운트로만 전송한다.** SkySafari `:CM#` 등 외부 Sync/Align
   입력은 마운트 정렬로만 반영되어야 하고, PiFinder 얼라인을 건드리면 안 된다.
2. **PiFinder 얼라인은 LCD 메뉴를 통해서만 수행한다.** SkySafari나 자동 로직이
   얼라인을 변경하면 안 된다.
3. 사용자 명령 없이 IMU 품질 좌표로 마운트를 자동 정렬(sync)하는 동작은
   좌표 프레임을 흔들므로 금지되어야 한다.

## 3. 타임라인 재구성

`pifinder.log`에 2026-07-24 부팅이 4회 기록되어 있다 (`Flask app created`
마커, 파일 라인 순서 기준):

| 부팅 | 로그 타임스탬프 | 실제 시각(추정) | 의미 |
| --- | --- | --- | --- |
| 1 | 18:19 → 19:21 | 18:19–19:21 (정상) | 집, 준비. 정상 종료 → fake-hwclock에 ~19:21 저장 |
| 2 | **19:21:41.074** → 20:06+ | **~20:50 → ~21:40** | 현장 전반부. **시계 ~1.5h 지연** |
| 3 | **19:21:41.194** → 19:52+ | **~21:50 → ~22:45** | 현장 후반부(세션 중 재부팅). **시계 ~2.5h 지연** |
| 4 | 23:11 → | 23:10 → (정상) | 집, 실내 검증. NTP로 부팅 직후 시계 복구 |

근거:

- 이 장비에는 RTC가 없다 (`timedatectl`: `RTC time: n/a`). 부팅 시
  fake-hwclock이 마지막 저장값을 복원한다.
- 부팅 2·3·4가 전부 **동일하게 19:21:4x 타임스탬프로 시작**한다. 부팅 1이
  19:21에 정상 종료하며 남긴 저장값을 셋 다 복원한 것이다. (fake-hwclock은
  정상 종료 시와 매시 :17 cron에서 저장한다. 부팅 2·3은 전원 차단으로 끝났고
  타임스탬프 기준 20:17을 넘기지 못해 저장이 갱신되지 않았다.)
- 현장에는 인터넷이 없어(PiFinder AP) chronyd의 NTP 소스가 없었고, GPS
  refclock(`gps1`)은 시간을 공급하지 못했다(7.A에서 원인 검증). PiFinder의
  시간 동기 관찰 프레임워크는 기본값 Off라
  (`gps_time_status.json`: `time_sync_enabled=false`) 경고조차 표시되지
  않았다. 결과적으로 시계를 고칠 수단이 없었다.
- 현재 부팅(4)의 journal에서 시계가 `19:21:23`(systemd 서비스 시작 스탬프) →
  `23:10:58`(NTP 동기)로 점프한 기록이 확인된다. `uptime -s` = 23:10:25.
- 관측 창(21:00~23:00) 스탬프의 로그 라인이 하나도 없다는 사실 자체가 현장
  세션이 틀린 시계로 돌았음을 뒷받침한다.

## 4. 문제 1 분석: 현장 세션이 1.5~2.5시간 틀린 시계로 운용됨

시계 오차 1.5~2.5시간 = **항성시(LST) 오차 22°~38°**.

- **plate solve와 LCD 얼라인은 시계와 무관**하다 (카메라 영상 vs 항성 DB).
  그래서 얼라인 직후에는 정상으로 보였다.
- 그러나 마운트 연결 시 PiFinder는 `sync_location_time()`으로 **틀린 시간을
  OnStepX에 전송**했다 (부팅 직후 1차 시도는 `Could not set INDI TIME_UTC`로
  실패 로그가 남았고, 자동 재연결 경로에서 이후 전송된다). Alt/Az 마운트는
  RA/Dec↔물리 방향 변환에 이 시간(LST)을 쓴다.
- 정렬(sync)한 별 근처에서는 sync가 오차를 국소적으로 흡수하므로 맞아 보이지만,
  **하늘에서 멀리 떨어진 대상을 GoTo하면 수십 도 단위로 어긋난다.**
  "갑자기 얼라인이 이상한 곳으로 정렬된 것처럼 보임"과 일치한다.
- 세션 중 재부팅(부팅 3)은 시계를 다시 19:21로 복원시켜 오차를 오히려 키웠다.

증폭 요인 (당시 활성):

- **GoTo Type = PiFinder 모드**가 켜져 있었다. 이 모드는 GoTo마다
  "현재 융합좌표로 mount 정렬 → GoTo"를 반복하는데, 틀린 프레임 위에서
  수렴하지 못했다. 로그(부팅 2, 스탬프 19:25:10 = 실제 ~20:55):
  `PiFinder GoTo stopped: GoTo error did not improve`.
- 스탬프 19:48:19(실제 ~21:18): `Could not read INDI GoTo busy state after
  180.6s; assuming complete` — GoTo 완료 판정 불가 180초. 이 "무동작 GoTo →
  180초 → error did not improve" 패턴은
  [mf_goto_tracking_recovery_analysis_ko.md](mf_goto_tracking_recovery_analysis_ko.md)의
  **OnStepX 웨지(wedge) 증상**과 같은 서명이므로, 당시 컨트롤러 웨지가
  겹쳤을 가능성도 있다 (현장 증거만으로는 확정 불가).
- 추적 가이드 외란 복구(아래 5장)도 틀린 프레임 위에서 sync를 반복했다.

## 5. 문제 2 분석: 추적 가이드 외란 복구가 IMU 좌표로 마운트를 정렬함

실내 검증(부팅 4, 시계 정상)으로 확정:

- `indi_goto_method = indi_mount`는 **정상 적용**되어 있었다
  (`indi_goto_guide_status.json`: `goto_method=indi_mount`,
  `phase=indi_mount_goto`). "PiFinder 모드처럼 보인" 것은 GoTo Type이 아니라
  **추적 가이드(Tracking Guide)의 외란 복구**다. 이 기능은 GoTo Type과
  무관하게 동작한다.
- 복구 시퀀스: 물리적 이동 감지(disturbed) → 정착(settle) → 오차 >
  `indi_tracking_guide_goto_threshold_deg`(0.5°)이면 **"현재 융합좌표로 mount
  정렬(sync) → 원래 타깃으로 GoTo"** —
  `indi_goto_guide_service.py::_begin_tracking_recovery_goto()`.
  이때 좌표 소스(solve/IMU)를 **구분하지 않는다.**
- 실내(솔브 없음)에서는 융합좌표가 IMU 기반이므로, 손으로 움직이면 IMU 좌표로
  마운트가 재정렬된다. 증거 (00:12:39 KST, 실내):

```text
mount_control_status.json
  coordinate_sync = {ra: 250.643, dec: -30.732, source: sync_mount,
                     synced_at: 00:12:39}
pointing_coordinate_status.json (동시점)
  solved.reason = "no solved pointing"
  selected_source = "mount_imu_delta"
  imu_mount_separation_degrees = 165.8
indi_goto_guide_status.json
  tracking_guide_recovery_count = 1
```

- 추가로 `tracking_guide_state = enabled`로 실내에서도 pulse guide 보정이
  계속 전송되고 있었다 (오차 4.1′ 기준).

## 6. 운용 원칙과 충돌하는 코드 경로

| # | 경로 | 위치 | 현재 상태 | 원칙 위반 |
| --- | --- | --- | --- | --- |
| 1 | SkySafari `:CM#` → PiFinder 얼라인(target_pixel) 덮어쓰기 | `pos_server.py::_align_pifinder_if_enabled()` | `skysafari_pifinder_align` 기본 **true**, UI 노출 없음 | 원칙 2 |
| 2 | SkySafari `:CM#` → 무솔브 IMU 얼라인 보정 | `pos_server.py::_set_imu_alignment_from_target_if_no_solve()` | **옵션 없이 항상 켜짐**. config의 `skysafari_imu_align_without_solve` 키는 코드가 읽지 않는 잔재 | 원칙 2 |
| 3 | 추적 가이드 외란 복구 → 융합좌표(IMU 포함)로 mount 정렬 | `indi_goto_guide_service.py::_begin_tracking_recovery_goto()` | 좌표 소스 게이트 없음 | 원칙 3 |
| 4 | PiFinder GoTo 모드 반복 정렬 / final sync, pointing reset 시 IMU 정렬 | `indi_goto_guide_service.py::_send_sync_and_goto()`, `_send_final_sync_once()`, `pos_server.py::_align_mount_to_imu_on_reset()` | 무솔브 시 IMU 좌표로 sync | 원칙 3 |

## 7. 수정 계획 체크리스트

우선순위 순. 항목별로 완료 시 체크하고 검증 내용을 기록한다.

### A. 시간 신뢰성 (문제 1 근본 대책)

설계 원칙 (2026-07-25 사용자 결정,
[mf_time_sync_ko.md](mf_time_sync_ko.md) 기반 재정리):

- **현장(무네트워크): GPS가 위치와 시간을 모두 동기**해야 한다.
- **네트워크가 있으면: GPS와 NTP 중 더 정확한 쪽을 자동 선택**한다.
- system clock의 유일한 관리자는 **chronyd**로 유지한다. chronyd는 이미
  NTP pool + gpsd SHM refclock(`refclock SHM 0 refid gps1`) 구성이라 소스
  자동 선택(더 정확한 쪽)이 기본 동작이다. 기존 시간 동기 기능 중 이
  구조와 겹치거나 clock을 이중으로 쓰는 부가 기능은 제거하고 핵심만 남긴다.

현재 확인된 상태 (2026-07-25 실사):

```text
chronyd active — pool 2.debian.pool.ntp.org + refclock SHM 0 (gps1)
gpsd active — /dev/ttyAMA3 @115200 단독 소유 (포트 경합 없음)
PiFinder GPS = gps_ubx.py, gpsd TCP 2947 경유 UBX 수신 (경합 없음)
chrony gps1 = Reach 0 (실내라 fix 없음 — 현장 fix 시 공급 여부 미검증)
chrony makestep = "1 3" (부팅 초기 3회 업데이트에만 스텝 허용)
time_sync_enabled = false (기본값 Off → 관찰/경고 전부 꺼짐)
```

- [x] **A1. GPS→gpsd→SHM→chrony 시간 공급 체인 실증** — 2026-07-25 새벽
  실외 검증으로 **원인 확정: gpsd 서비스 옵션에 `-n`이 없었다.**
  - 증상: GPS 2D fix + TPV 시간 공급 상태에서도 NTP0 SHM 세그먼트가
    전혀 쓰이지 않음(count=0, valid=0) → chrony `gps1` Reach 0.
  - 진단: gpsd를 포그라운드 `-N -n -D4`로 실행하자 즉시 `ntpshm_put`이
    매초 기록됨. 서비스 모드(클라이언트 워치 기반 활성화, PiFinder는
    `raw:2` 워치)에서는 SHM 공급이 되지 않았다. gpsd 문서대로 시각 동기
    용도에는 `-n`(클라이언트 없이 즉시 폴링)이 필수.
  - 조치: `/etc/default/gpsd`의 `GPSD_OPTIONS`를 `"-n -s 115200"`으로 변경
    (백업: `gpsd.bak-20260725`) 후 `gps1` Reach 3→7, offset +51ms 수신 확인.
  - 검증: 홈 네트워크(NTP 활성)에서 chrony가 NTP를 선택하고 gps1은 후보로
    유지 — "더 정확한 쪽 자동 선택" 동작 확인. GPS 직렬 시간 오차
    ~50ms = LST 오차 ~0.8″로 GoTo에 충분.
  - 남은 확인: 현장(무NTP) 조건에서 gps1 단독 선택 + 큰 오프셋 스텝
    리허설(다음 실외 관측 시). GPS fix 자체가 느린 문제는
    [mf_gps_aiding_plan_ko.md](mf_gps_aiding_plan_ko.md)와 연계.
- [x] **A2. chrony 큰 오프셋 스텝 보장** — `makestep 1 3` → `makestep 1 -1`
  적용(2026-07-25, 백업: `chrony.conf.bak-20260725`). RTC 없는 보드에서 부팅
  한참 뒤 GPS fix가 와도 즉시 스텝한다. 관측 중 큰 스텝은 A5 재동기 훅이
  뒤처리한다.
- [x] **A2b. 시스템 시간 설정의 설치 스크립트 반영** — 2026-07-25 구현.
  `install_chrony_time_sync.sh`에 `configure` 명령을 추가해 다음을 멱등
  관리한다(`install`도 수행): `/etc/default/gpsd`의 `-n` 옵션,
  `/etc/chrony/chrony.conf`의 `refclock SHM 0 poll 3 refid gps1`과
  `makestep 1 -1`. 기 적용된 기기에서 no-op 확인.
  - [ ] 후속: gps1 상수 오프셋(+50ms대 관찰) `offset` 보정값 검토
  - [ ] 후속(선택): 커널 PPS 활용 — `/dev/pps0` 살아 있음(KPPS 감지 확인).
    3D fix 후 PPS 유효성 확인되면 `refclock PPS /dev/pps0 lock gps1`로
    μs급 정밀도 가능. 필수는 아님(50ms로 충분).
- [x] **A3. 시간 동기 기능 축소(핵심만)** — 2026-07-25 구현. chronyd 단일
  클럭 관리자 원칙에 맞춰 정리:
  - 제거: PiFinder 자체 SNTP client, Software PPS,
    `Clock Manager = PiFinder`(직접 system clock 쓰기), `Best/GPS/NTP` 소스
    모드와 관련 config 키/LCD 메뉴/상태 필드 (`gps_time_sync.py` 1703→약
    1000줄).
  - 유지: chrony 상태 관찰/표시, GPS 후보 관찰(진단), RTC 헬퍼 요청 경로
    (RTC 도입 A6까지 기본 Off; helper의 system clock 경로 정리는 A6에서).
  - 기본값 변경: `time_sync_enabled` 기본 **On** (관찰 전용).
  - `selected`는 chronyd 동기 상태일 때의 Chrony 후보만 사용 — A4 게이트의
    판단 근거가 된다.
  - 문서 갱신: [mf_time_sync_ko.md](mf_time_sync_ko.md) /
    [mf_time_sync_en.md](mf_time_sync_en.md) 재작성. 단위 테스트 26개 통과.
- [ ] **A4. "시계 미신뢰" 게이트** — 부팅 후 실제 동기(chrony가 유효 소스로
  스텝/추적) 이력이 없는 fake-hwclock 시간이면:
  - LCD 상태바/웹에 경고 표시
  - **마운트 location/time sync 전송 보류** (틀린 시간을 마운트에 심는 것이
    최악의 동작)
  - GoTo/Multi Align 시작 시 경고
  - 판단 근거: `chronyc tracking`의 reference/leap/last-offset +
    `gps_time_status.json` 관찰 상태.
- [ ] **A5. 시간 점프 시 재동기** — 시간이 크게 점프하면(동기 회복) 마운트
  site/time 재전송 + 추적 타깃 해제. location 변경 자동 재동기(dedf7b58)와
  같은 훅에 time jump 감지 추가.
- [ ] **A6. RTC 모듈 추가 검토** (하드웨어, DS3231 등) — A2의 makestep 보완이
  적용되면 우선순위는 낮아지나, 부팅 직후부터 정확한 시간을 갖는 구조적
  해결책. 사용자 결정 사항.

### B. 자동 정렬(sync) 정책 (문제 2 + 원칙 반영)

- [ ] **B5. 자동 정렬 솔브 게이트** — 사용자 명령이 아닌 모든 자동 sync
  (추적 가이드 복구, PiFinder GoTo 반복/final sync)는 좌표 소스가 **신선한
  plate solve일 때만** 전송. IMU/mount_imu_delta 좌표면 sync를 생략하고 상태에
  사유 표시. 복구는 GoTo만 수행하거나 대기.
  - 대상: `indi_goto_guide_service.py` (`_begin_tracking_recovery_goto`,
    `_send_sync_and_goto`, `_send_final_sync_once`)
  - 판단 근거: `pointing_coordinate_status.json`의 `current.source` /
    `solved` 신선도 (`_load_pointing_status` 확장)
- [ ] **B6. `skysafari_pifinder_align` 기본 false + 웹 UI 노출** — `:CM#`은
  INDI 정렬 전용. PiFinder 얼라인은 LCD에서만.
  - 대상: `default_config.json`, `pos_server.py`, `server.py`,
    `views/indi_mount.html`
- [ ] **B7. 무솔브 IMU 얼라인 옵션화** — `skysafari_imu_align_without_solve`
  키를 실제 게이트로 복원, 기본 off. (업스트림 no-solve 부트스트랩 기능은
  옵션으로 유지)
  - 대상: `pos_server.py::_set_imu_alignment_from_target_if_no_solve()`
- [ ] **B8. 자동 정렬 생략 시 상태 표시** — B5 게이트로 sync가 생략되면
  `indi_goto_guide_status.json`과 웹 GoTo/Guide Status에 사유 표시.

### C. 다음 관측 전 임시 설정 (코드 수정 전 방어)

- [ ] **C9.** GoTo Type = INDI Mount 유지, `GoTo Recovery = Off`
  (LCD: INDI Setting > Goto/Guide). 필요시 Tracking Guide 자체 Off.
- [ ] **C10.** config에 `"skysafari_pifinder_align": false` 추가.
- [ ] **C11.** 현장 도착 후 폰 핫스팟으로 NTP 1회 동기 또는 시간 수동 확인
  (A 구현 전까지의 임시 수단).

### D. 진단성

- [ ] **D12. INFO 파일 로깅 프로파일 기동** — 이번 분석에서 파일 로그가
  WARNING만 남아 현장 타임라인 재구성이 어려웠다. `logconf_indi.json` 계열
  INFO 프로파일 적용 (SD 마모 고려해 tmpfs/회전 정책 확인).
- [ ] **D13. Moon 추적 주파수 잔존 점검** — 실내 검증 중
  `track_freq_label=Moon`(58.59Hz)이 고정 좌표 타깃 추적 중에도 남아 있었다.
  recent 목록의 달 대상(고정 좌표) GoTo로 lunar feed-forward가 걸린 뒤 정적
  대상 GoTo에서 sidereal 복원이 동작했는지 추적 주파수 정책
  (`track_freq_policy.py`) 경로 점검. recent/PUSH 행성 좌표가 저장 시점에
  고정되는 문제도 함께 확인.

## 8. 부록: 진단 명령

```bash
# 부팅/시계
uptime -s
timedatectl
cat /etc/fake-hwclock.data          # 마지막 저장 (UTC)
sudo journalctl --list-boots

# 상태 파일 (tmpfs, 재부팅 시 소실 — 문제 발생 시 즉시 복사해 둘 것)
python3 -m json.tool /dev/shm/pifinder/mount_control_status.json
python3 -m json.tool /dev/shm/pifinder/indi_goto_guide_status.json
python3 -m json.tool /dev/shm/pifinder/pointing_coordinate_status.json
python3 -m json.tool /dev/shm/pifinder/gps_time_status.json

# 정렬(sync)이 언제/어떤 좌표로 갔는지
#   mount_control_status.json .coordinate_sync {ra, dec, source, synced_at}
# 추적 가이드 복구 횟수
#   indi_goto_guide_status.json .tracking_guide_recovery_count
```

현장에서 문제 재발 시: 전원을 끄기 전에 위 상태 파일 4개를
`~/PiFinder_data/`로 복사해 두면 재부팅 후에도 분석할 수 있다.
