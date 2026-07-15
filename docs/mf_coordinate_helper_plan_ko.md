# MF PiFinder Pointing Coordinate Service

최종 업데이트: 2026-07-13

이 문서는 현재 `mf_pifinder` 브랜치의 상시 좌표 서비스 구현을 기준으로
SkySafari, Web UI, LCD UI, INDI Multi Align이 공통으로 사용할 좌표 흐름을
정리한다.

중요 원칙:

- SkySafari 또는 LX200 입력으로 들어온 target RA/Dec는 요청 좌표 그대로 사용한다.
- 요청 좌표를 J2000/JNow 같은 epoch 이름으로 재해석하거나 변환하지 않는다.
- `pointing.aligned.estimate`는 PiFinder가 계산한 현재 기준 좌표로 그대로 사용한다.
- Alt/Az 변환은 IMU 보정, 표시, 마운트 타입별 해석이 필요한 지점에서만 수행한다.
- 소비자는 좌표를 직접 다시 계산하지 않고 `PointingCoordinateService`가 publish한
  최신 `CoordinateState`를 읽는다.

## 구현 파일

```text
python/PiFinder/pointing_coordinate_service.py
python/PiFinder/pos_server.py
python/PiFinder/mountcontrol_indi.py
python/PiFinder/imu_pi.py
```

관련 테스트:

```text
python/tests/test_pointing_coordinate_service.py
python/tests/test_pos_server.py
python/tests/test_mountcontrol_indi.py
```

디버깅 상태 파일:

```text
/home/pifinder/PiFinder_data/pointing_coordinate_status.json
/home/pifinder/PiFinder_data/mount_control_status.json
```

## 전체 구조

`pos_server.py`는 SkySafari LX200 요청(`:GR#`, `:GD#`)을 받을 때
좌표를 새로 계산하지 않는다. 백그라운드 루프가 갱신해 둔
`PointingCoordinateService.get_state()`의 `current` 좌표를 읽어 LX200 형식으로
응답한다.

```text
PiFinder processes
  IMU process
    -> shared_state.imu()
  Solver/Integrator
    -> shared_state.solution().pointing.aligned.estimate
  INDI Mount process
    -> mount_control_status.json
  POS Server
    -> PointingCoordinateService background loop
    -> SkySafari :GR#/:GD# response
```

좌표 서비스 루프:

```mermaid
flowchart TD
    A[PointingCoordinateService loop] --> B[PiFinder solution 읽기]
    B --> C[IMU sample 읽기]
    C --> D[INDI mount status 읽기]
    D --> E[각 후보 좌표 생성]
    E --> F[우선순위와 상태 gate 판단]
    F --> G[CoordinateState.current publish]
    G --> H[pointing_coordinate_status.json 기록]
    H --> A
    I[SkySafari/Web/LCD/Multi Align] --> J[최신 CoordinateState 읽기]
    G --> J
```

## 후보 좌표

### 1. Solved 좌표

입력:

```text
shared_state.solution().pointing.aligned.estimate.RA
shared_state.solution().pointing.aligned.estimate.Dec
```

유효 조건:

- `solution.has_pointing()`이 true
- `solve_source == CAM`
- 또는 `solve_source == IMU`이지만 plate-solve anchor가 존재함

처리:

- RA/Dec 값을 그대로 사용한다.
- J2000/JNow 변환을 하지 않는다.
- `solve_source == IMU`인데 plate-solve anchor가 없으면 부팅 직후 IMU 추정값으로
  보고 primary solved 좌표로 쓰지 않는다.

### 2. IMU fallback 좌표

입력:

```text
shared_state.imu()
screen_direction
location/time
optional IMU alignment correction
```

처리:

```text
IMU quaternion
  -> camera boresight
  -> raw Alt/Az
  -> optional align correction
  -> smoothing
  -> location/time 기준 RA/Dec
```

IMU smoothing:

- raw Alt/Az 변화량을 기준으로 작은 흔들림을 평균화한다.
- 매우 작은 변화는 강하게 damping한다.
- 중간 변화는 완만하게 따라간다.
- 큰 변화는 사용자가 실제로 망원경을 움직인 것으로 보고 빠르게 반영한다.
- smoothing 전후 값은 모두 status JSON에 기록한다.

관련 status metadata:

```text
imu.metadata.raw_alt
imu.metadata.raw_az
imu.metadata.smoothed_alt
imu.metadata.smoothed_az
imu.metadata.filter_state
imu.metadata.filter_delta_degrees
imu.metadata.quat_norm
imu.metadata.calibration_status
imu.metadata.fusion_mode
imu.metadata.uses_magnetometer
```

### 3. Mount readback 좌표

입력:

```text
/home/pifinder/PiFinder_data/mount_control_status.json
```

주요 필드:

```text
state
ra / dec
park_state
driver_mount_status
raw_mount_status
coordinate_sync
multipoint_align
mount_motion_active
mount_motion_type
mount_readback_priority
goto_motion_active
goto_refine_pending
manual_motion_direction
target_ra / target_dec
target_error_deg
goto_wait_seconds
```

mount 후보 제외 조건:

- disconnected/disconnecting/error/fault/failed/server_offline/driver_offline 상태
- Parked 상태
- RA/Dec readback 없음

정렬 전 mount readback:

- `mount.valid = true`일 수 있다.
- 하지만 PiFinder와 mount가 아직 sync/alignment 되지 않았으면 `mount.aligned = false`.
- 이 경우 current 좌표에 섞지 않고 diagnostic으로만 기록한다.

## 좌표 선택 우선순위

현재 구현의 우선순위:

```text
1. SOLVED_PRIMARY
   plate solve 또는 plate-solve anchor가 있는 PiFinder estimate

2. MOUNT_REFERENCE_PRIMARY
   mount가 usable + synced/aligned이고 IMU도 valid인 경우
   단, mount가 확실히 정지한 상태일 때만 mount anchor + IMU delta 사용

3. MOUNT_ONLY_SYNCED
   mount가 usable + synced/aligned이지만 IMU가 invalid인 경우

4. IMU_PRIMARY_UNSOLVED
   solve 없음, mount sync 전 또는 mount unusable, IMU valid

5. UNAVAILABLE
   사용할 좌표 없음
```

정렬 전에는 mount와 IMU 절대 좌표가 크게 다를 수 있으므로 평균내지 않는다.
mount readback은 PiFinder와 sync된 뒤에만 current 좌표 후보가 된다.

## Mount + IMU Delta

mount가 PiFinder와 sync/alignment 된 뒤에는 다음 방식으로 보정한다.

```text
anchor_imu   = sync 시점 IMU fallback RA/Dec (delta 기준점)

applied_delta = 속도 게이트를 통과해 누적된 IMU delta (아래 참조)
current = 실시간 mount readback + applied_delta
```

(2026-07-16 수정: base가 anchor 시점의 mount 좌표 스냅숏에서 **실시간 mount
readback**으로 바뀌었다. 펄스/슬루로 readback이 움직이면 fused가 즉시 따라가고,
re-anchor가 필요 없어져 re-anchor로 인한 외란 오프셋 소실이 사라졌다.)

의도:

- mount 절대 좌표와 IMU 절대 좌표를 평균내지 않는다.
- mount는 장기 기준점으로 사용한다.
- IMU는 mount 정지 상태에서 사람이 강제로 움직였거나 충격을 준 경우처럼 빠른 변화량을
  감지하는 보조 입력으로 사용한다.

anchor reset 조건 (reset 시 applied 외란 오프셋도 함께 초기화):

- anchor 없음
- `coordinate_sync` 또는 `multipoint_align` sync key 변경 (= sync로 마운트
  좌표계가 재정립된 경우)

mount readback 이동은 더 이상 reset 사유가 아니다. fused의 base가 실시간
readback이므로(아래 2026-07-16 수정) 펄스/슬루는 base를 통해 바로 반영되고,
readback 이동으로 reset하면 실제 물리 외란 오프셋이 소리 없이 지워진다.

### IMU delta 속도 게이트 (2026-07-12 추가)

추적 중 실장비에서 발견: mount가 사이드리얼 추적을 하면 readback RA/Dec는 target에
고정되지만, IMU 스무딩 필터가 느린 추적 모션(~15"/s)을 "작은 지터"로 취급해 사실상
얼려버린다. 그 결과 IMU 환산 RA가 사이드리얼 속도로 드리프트하고, raw delta가 무한
누적되어 fused 좌표가 target에서 계속 흘러갔다(분당 ~20'). 이 가짜 드리프트가
추적 가이드의 GoTo 복구를 오발시켜 물리적으로는 오히려 target을 벗어나게 했다.

수정: `_mount_with_imu_delta`가 raw delta 대신 **속도 게이트를 통과한 applied
delta**를 사용한다 (`_gated_imu_delta`).

```text
IMU_DELTA_ENTER_RATE_DEG_PER_SEC = 0.03   (진입)
IMU_DELTA_EXIT_RATE_DEG_PER_SEC  = 0.015  (유지/탈출)
```

**히스테리시스 게이트 (2026-07-16 수정)**: 단일 임계값(구 0.05)은 미약한 탈조
슬립(실측 head/tail 속도 0.02~0.06 deg/s)을 조각내서 변위의 ~1/3만 계측했다
(실장비 캡처: 0.033→0.06→0.02 이벤트에서 0.05 초과 3틱만 누적). 누적 에피소드는
진입 속도(0.03, 실내 무진동에서 실측한 아티팩트 바닥 0.004~0.005의 ~7배 —
야외 바람 잔진동도 에피소드를 시작시키지 못하는 마진) 이상에서 시작하고, 일단
시작되면 탈출 속도(0.015, ~4배) 아래로 떨어질 때까지 계속 누적해 슬립의 느린
head/tail까지 포착한다. 0.03 미만으로만 기어가는 극미세 슬립은 여전히 보이지
않는다(rate가 유일한 판별자; 야간에는 solve가 절대 기준).

- 충격/수동 이동/탈조 슬립 -> `fast_follow`: 오프셋이 fused 좌표에 그대로
  반영되어 외란 감지와 복구가 정확한 오차로 동작한다.
- 추적 아티팩트/센서 드리프트(느림) -> `hold`: 증분은 버려지지만 이미 적용된
  오프셋은 그대로 **유지**된다. 정지한 경통의 좌표는 흐트러진 자리에 머물러야
  하며, mount readback으로 기어 돌아가면 안 된다.
- 마운트 자체 이동(GoTo/manual/펄스) 중 -> `suspended_mount_motion`: readback이
  우선 표시되고 IMU 기준점만 전진시켜(누적 없음) 마운트 스스로의 이동이 외란으로
  잘못 집계되지 않게 한다. 오프셋은 이동이 끝난 뒤에도 살아남는다.
- sync(sync key 변경) 시에만 tracker와 applied가 초기화된다.
- 진단 metadata: `imu_delta_applied_ra/dec`, `imu_delta_gate`,
  `imu_delta_rate_deg_per_sec`.
- 한계: 게이트(0.05 deg/s)보다 느린 실제 외력은 solve 없이는 보이지 않는다.
  야간에는 solve(SOLVED_PRIMARY)가 우선되므로 영향 없다.
- 실장비 end-to-end 검증(2026-07-12, 사용자가 경통을 실제로 밀어 테스트): 밀기
  감지(0.99 deg/s, err 1488') -> disturbed -> sync+GoTo 복구 -> settling 2.9' ->
  enabled 0.0'으로 밀기 전 위치 재획득.
- 참고: GoTo 단계 중에는 mount readback이 우선이므로 GoTo 도중의 밀림은 표시
  좌표에 즉시 반영되지 않고, GoTo 종료 후 corrective/트래킹 가이드가 처리한다.

### 외란 오프셋 유지 (2026-07-16 수정)

실장비 외란 복구 테스트에서 발견: 경통을 밀고 멈추면 fused 좌표가 그 자리에
머물지 않고 (1) **이전 GoTo 좌표로 천천히 되돌아가거나** (2) **한번에 점프**했다.

원인 두 가지:

1. applied delta가 느린 구간에서 tau 120 s로 지수 감쇠(`slow_decay`)했다.
   3도 오프셋이면 초당 ~1.5' 속도로 readback(=이전 target)으로 기어 돌아간다.
   감쇠의 원래 목적(추적 아티팩트 드리프트 소멸)은 속도 게이트가 증분을 아예
   applied에 넣지 않는 것으로 이미 달성되므로, 감쇠는 정상 외란 오프셋만
   갉아먹는 부작용이었다.
2. mount readback이 조금만 움직이거나(18" 지터로도) motion/priority 플래그가
   서면 anchor를 통째로 삭제하고 raw readback을 반환해, 오프셋이 즉시
   소실(=점프)됐다.

수정 (모두 `pointing_coordinate_service.py`):

- `slow_decay` -> `hold`: 느린 구간에서 applied를 유지한다. 오프셋은 sync
  (sync key 변경)로만 지워진다. 복구 경로가 sync + GoTo로 시작하므로 복구가
  일어나면 자연히 초기화된다.
- fused base를 anchor 스냅숏 -> 실시간 mount readback으로 변경. re-anchor가
  불필요해져 readback 이동으로 인한 오프셋 소실이 사라졌다.
- 마운트 자체 이동 중에는 anchor를 지우지 않고 IMU 기준점만 전진
  (`suspended_mount_motion`). 이동 종료 후 fused = 새 readback + 보존된 오프셋.
- 검증: 밀기 후 5분 정지에도 오프셋 유지, 마운트 슬루 통과 후 오프셋 생존,
  미세 readback 이동 시 base 즉시 추종, sync 후 오프셋 초기화 (단위 테스트
  4건 추가).

## GoTo 중 좌표 처리

OnStepX는 GoTo 중에 큰 이동 후 잠시 멈춘 것처럼 보이다가 마지막 정밀 이동을 수행할 수
있다. 이 구간에서 IMU 움직임을 `mount + IMU delta`에 반영하면 target 오차가 생길 수
있으므로 GoTo 중에는 mount readback을 우선한다.

mount-control은 GoTo와 수동 이동 진행 중에도 현재 mount readback을 status에 publish한다.
좌표 서비스가 우선 사용하는 공통 telemetry는 다음이다.

```text
mount_motion_active
  실제 또는 명령상 mount가 움직이는 중이면 true.

mount_motion_type
  manual / goto / goto_refine_settle / guide_correction /
  align_goto / backlash_auto 등의 진단용 분류.

mount_readback_priority
  현재 좌표 계산에서 IMU delta보다 mount readback을 우선해야 하면 true.
  GoTo 마지막 정밀 이동 대기처럼 실제 motion은 아닐 수 있지만 readback을
  우선해야 하는 구간도 여기에 포함한다.
```

기존 세부 필드(`goto_motion_active`, `manual_motion_direction`,
`goto_refine_pending`, `state`)는 디버깅 및 과거 status 호환용으로 유지한다.

```text
MountControlIndi._check_goto_motion()
  -> _read_goto_progress_position()
  -> _write_goto_progress_status()
  -> state = slewing
  -> ra / dec / target_ra / target_dec / target_error_deg 기록

MountControlIndi.manual_move()
  -> _arm_manual_motion_deadline()
  -> _publish_manual_motion_progress(force=True)

MountControlIndi.run()
  -> _publish_manual_motion_progress()
  -> state = manual_motion
  -> ra / dec / manual_motion_direction 기록
```

좌표 서비스는 다음 조건에서 IMU delta를 보류하고 mount readback만 사용한다.

```text
mount_readback_priority == true
mount readback이 최근 tick 대비 계속 변하는 중
```

### 실장비 검증 (2026-07-12)

이 소스 선택 로직 자체는 정상 동작함을 확인했다. 직접 홀드 이동(키패드) 중에는
mount-control이 `state = manual_motion`, `mount_motion_active = true`를 보고하고,
`current.source = mount`로 드라이버 `EQUATORIAL_EOD_COORD`를 부드럽게 추종한다.

주의: 이 로직은 **마운트가 실제로 계속 움직여 mount-control state가 `manual_motion`으로
유지될 때만** 활성화된다. PiFinder GoTo(`indi_goto_method = pifinder`)의 수동 접근에서
마운트가 멈추던 문제는 이 좌표 로직이 아니라, 수동 접근의 모션 lease가 서비스 tick
간격보다 짧아 모션이 만료→정지되어 state가 `connected`로 떨어지고 `mount_imu_delta`
(정지 전용)로 폴백된 것이 원인이다. 자세한 내용은 `mf_indi_goto_guide_plan`의
"실장비 테스트 발견: 수동 접근 모션이 tick 사이에 끊김" 참고.

`mount_readback_priority`가 없는 오래된 status를 읽는 경우에만 fallback으로
`goto_motion_active`, `goto_refine_pending`, `manual_motion_direction`, `state`,
`multipoint_align`, `backlash_auto`를 해석한다.

GoTo 상태가 `connected`로 바뀐 직후에도 readback이 계속 변하면 일정 시간 동안
IMU delta를 계속 보류한다. 현재 hold 시간은 1.5초이다.

이 구조의 기대 동작:

- SkySafari 위치 표시는 GoTo 중 mount readback을 따라간다.
- GoTo 마지막 정밀 이동 중 IMU 움직임이 target 오차로 들어가지 않는다.
- mount가 확실히 정지한 뒤에만 IMU delta를 다시 반영한다.

## SkySafari Target / Sync / Align

SkySafari target 입력:

```text
:SrHH:MM:SS#
:Sd+DD*MM:SS#
:MS#
```

처리 원칙:

- `:Sr/:Sd`로 들어온 좌표를 파싱해 그대로 보관한다(`sr_result`/`sd_result`).
- `:MS#`는 같은 좌표를 `last_target_coordinates`로 저장하고 PiFinder push target 및
  선택적으로 INDI GoTo로 전달한다.
- Multi Align active 중이면 일반 PushTo 화면으로 넘기지 않고
  `multipoint_align_goto_target`으로 라우팅한다.
- `:CM#` Sync/Align은 현재 파싱된 `:Sr/:Sd` 좌표를 우선 사용하고, 없으면 가장 최근
  GoTo target(`last_target_coordinates`)을 그대로 사용한다.
- Multi Align active 중 `:CM#`은 `multipoint_align_confirm`으로 라우팅된다.
- SkySafari guide 입력(`:Mn#`, `:Ms#`, `:Me#`, `:Mw#`)은 target 좌표가 아니라
  수동 이동 명령이다. `pos_server.py`가 keepalive timer를 관리해
  `manual_movement`/`manual_movement_keepalive`를 mount-control에 보내고,
  좌표 서비스는 mount-control이 발행하는 `mount_readback_priority`와
  최신 mount readback을 보고 현재 좌표를 선택한다.
- SkySafari release/stop 입력(`:Q#`, `:Qn#`, `:Qs#`, `:Qe#`, `:Qw#`)은
  `stop_movement`로 라우팅된다. TCP 연결이 닫힌 것만으로는 stop으로 보지 않는다.

정렬 요청 좌표는 confirm 시점의 IMU 좌표가 아니다. 사용자가 마지막으로 선택하거나
SkySafari가 지정한 target을 아이피스 중앙에 맞췄다는 의미이므로, 그 target 좌표를
정렬 좌표로 사용한다.

## Reset Pointing / 좌표 초기화 (2026-07-12 추가)

fused 좌표가 실제 하늘에서 크게 벗어났을 때 운영자가 직접 초기화하는 기능이다 —
플레이트 솔빙이 안 되거나 잘못됐을 때, 또는 실내 테스트에서 IMU 드리프트가 fused
소스에 누적됐을 때. 초기화하면 fusion anchor와 IMU-delta tracker를 버려서 다음
tick에 최적 소스로 다시 기준을 잡는다: 유효한 solve > 정렬된 mount > IMU fallback
(즉 "솔빙이 없으면 IMU를 기준으로 재정리").

메커니즘 (서비스는 `pos_server` 프로세스 내 싱글톤이라 web/UI 프로세스와 큐를
공유하지 않으므로, 백래시 정지-요청 파일 패턴을 그대로 따른다):

1. Web `POST /indi/reset_pointing` (server.py) 또는 LCD 메뉴 콜백
   `callbacks.reset_pointing`가 원자적 요청 파일
   `PiFinder_data/pointing_reset_request.json`(`{requested_at, source}`)을 쓴다.
2. `_coordinate_service_loop`가 매 tick 폴링(pos_server.py의
   `_handle_pointing_reset_request`): 파일을 소비/삭제하고, SkySafari IMU
   정렬 보정(`_imu_alignment_correction`)을 먼저 폐기한 뒤, 솔빙이 없으면
   마운트를 raw IMU에 정렬(아래), `PointingCoordinateService.clear_state()`
   호출, pointing 캐시 무효화, `_pointing_reset_last_at` 기록.
3. `clear_state()`는 `_state`, `_mount_imu_anchor`, `_imu_delta_tracker`,
   `_imu_filter_altaz`, `_mount_motion_hold_until`, `_last_mount_motion_radec`를
   비운다. SkySafari IMU 정렬 보정은 `clear_state()`가 아니라 reset 핸들러가
   위 2번에서 지운다(2026-07-13 수정, `dd045dc`). 이전에는 reset이 보정을
   유지했는데("IMU→하늘 기준이므로 보존" 정책), 솔빙이 없는 환경(실내)에서는
   잘못된 target으로 정렬한 보정을 해제할 수단이 reset뿐인데도 보정이 살아남고,
   마운트→IMU 정렬이 보정이 적용된 IMU 좌표로 sync해 잘못된 정렬이 마운트
   좌표계에 다시 구워졌다. Reset의 의도는 "raw IMU로 원복"이므로 보정도 함께
   폐기한다. 보정이 필요하면 SkySafari sync로 다시 정렬하면 된다.

**솔빙 없음 시 마운트→IMU 정렬**(`_align_mount_to_imu_on_reset`): 솔빙이 없을 때는
`clear_state()`만으로 부족하다 — 마운트가 여전히 "aligned"(이전에 sync됨) 상태라
선택 우선순위가 계속 마운트 좌표를 반환해, 화면이 IMU가 아니라 (벗어난) 마운트
값에 머문다. 그래서 state를 비우기 전에, 솔빙이 무효이고 마운트 컨트롤이 켜져
있으면, 보정 미적용 raw IMU RA/Dec를 새로 계산해
(`_imu_fallback_pointing(..., apply_alignment=False)` — 캐시된 `state.imu`
샘플은 정렬 보정이 이미 적용돼 있어 쓰지 않는다) 그 좌표로 마운트에
`{"type": "sync", ...}`를 큐잉한다. sync는 마운트의 좌표계를 재정의할 뿐 스코프를
움직이지 않으므로, 마운트 readback(따라서 fused 좌표)이 IMU를 따라가게 된다.
솔빙이 유효하면 IMU 정렬은 하지 않고 솔빙이 좌표를 주도한다.

요청 소비 지연은 최대 ~0.2초(서비스 tick 주기 `_POINTING_UPDATE_SECONDS`).

UI:

- Web INDI 페이지: "Location and Time" 다음에 "Pointing Coordinate Service"
  카드 — selected source, mode, quality, RA/Dec(deg), mount separation,
  IMU–mount separation, warnings, 마지막 초기화 시각 표시 + "Reset Pointing" 버튼.
  이 값들은 상태 JSON 파일만 읽는 전용 경량 엔드포인트 `GET /indi/pointing_status`로
  ~1Hz 갱신된다(INDI 속성 셸 조회가 없어 5초짜리 `/indi/current_values`보다 가볍다).
  같은 빠른 엔드포인트가 `mount_control_status`·`goto_guide_status`도 함께 실어,
  라이브 raw 마운트 상태와 goto/추적 가이드 상태도 ~1Hz로 갱신된다. (별도로
  "OnStep UTC Time"은 OnStepX 드라이버가 `TIME_UTC` 속성을 드물게만 갱신하므로
  클라이언트에서 틱시키며, 드라이버가 새 값을 보고할 때만 재시드한다.)
  상태는 `server.py::_pointing_coordinate_status()`가
  평탄화하고, 서비스가 `pointing_coordinate_status.json`에 `last_reset_at`를 추가.
- LCD UI: INDI > INIT > "Reset Pointing" ("Set Location" 다음). 요청 파일을 쓰고
  확인 메시지를 띄우는 단순 액션 항목.

## 디버깅 포인트

좌표가 흔들릴 때 먼저 확인할 파일:

```bash
jq . /home/pifinder/PiFinder_data/pointing_coordinate_status.json
jq . /home/pifinder/PiFinder_data/mount_control_status.json
```

확인 순서:

```text
1. pointing_coordinate_status.json의 mode/current.source 확인
2. IMU raw_alt/raw_az와 smoothed_alt/smoothed_az 차이 확인
3. imu.metadata.filter_state 확인
4. mount.aligned와 coordinate_sync/multipoint_align 확인
5. GoTo 중 mount_control_status.json의 state, ra, dec, target_error_deg 확인
6. health.warnings 확인
```

대표 상태:

```text
IMU_PRIMARY_UNSOLVED:
  solve 없음, mount sync 전, IMU fallback이 현재 좌표

MOUNT_REFERENCE_PRIMARY:
  mount sync 이후, mount 정지 상태, mount anchor + IMU delta 사용

MOUNT_ONLY_SYNCED:
  mount sync 이후, IMU invalid 또는 mount motion/settle active

SOLVED_PRIMARY:
  plate solve 좌표가 최우선
```

## 테스트

현재 관련 테스트:

```bash
python -m pytest \
  python/tests/test_pos_server.py \
  python/tests/test_mountcontrol_indi.py \
  python/tests/test_pointing_coordinate_service.py
```

2026-07-08 기준 확인 결과:

```text
110 passed
```

테스트가 검증하는 주요 항목:

- solved 좌표가 mount/IMU보다 우선됨
- sync 전 mount readback은 current 좌표에 섞이지 않음
- sync 후 mount 정지 상태에서만 IMU delta 반영
- GoTo/refine/readback 이동 중에는 mount readback 우선
- GoTo 중 mount readback progress가 status로 publish됨
- IMU 작은 흔들림 smoothing 적용
- SkySafari target/sync 좌표는 요청 좌표 그대로 사용
- SkySafari guide move는 keepalive 중에는 지속되고 stop command에서 정지
