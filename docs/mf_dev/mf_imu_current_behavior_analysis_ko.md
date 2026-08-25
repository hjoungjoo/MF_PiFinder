# MF PiFinder IMU 현재 동작 분석

- 문서 상태: **living — 기준선 + P0 구현 반영**
- 분석일: 2026-08-25 (KST)
- 분석 기준: `main` / `d71a5b4e667d43237ba5ccff7ab47e66b883afb4`
- P0 구현일: 2026-08-25 (working tree, 미커밋)
- 대상 센서: Bosch BNO055 (`adafruit-circuitpython-bno055`)
- 분석 방법: 현재 소스·기본 설정·장치 설정·관련 테스트·기존 설계 문서 정적 추적

이 문서는 IMU 개선 전 기준선과 2026-08-25에 반영한 P0 안전 개선을 한곳에서
이해하기 위한 문서다. 센서
드라이버만 설명하지 않고, IMU 샘플이 카메라·solver·integrator·절전·UI·
SkySafari/INDI 좌표 서비스·telemetry로 전달되는 전체 흐름을 다룬다.

실장 센서의 노이즈·지연·드리프트를 새로 계측한 보고서는 아니다. 분석 시점에
PiFinder 서비스가 실행 중이지 않아 새로운 실시간 캡처는 하지 않았다. 문서의
실측 수치는 기존 프로젝트 문서와 코드 주석에 기록된 결과를 인용한 것이며,
그 외 평가는 현재 코드에서 직접 확인한 동작이다.

관련 정규 문서:

- Positioning 용어·데이터 모델: [`../ax/positioning/CONTEXT.md`](../ax/positioning/CONTEXT.md)
- plate solve/IMU 통합 구조: [`../ax/positioning.md`](../ax/positioning.md)
- 외부 좌표 선택·mount+IMU 융합: [`mf_coordinate_helper_plan_ko.md`](mf_coordinate_helper_plan_ko.md)
- compass 보정 사용법: [`mf_imu_compass_calibration_ko.md`](mf_imu_compass_calibration_ko.md)
- I2C clock stretching 대응: [`mf_i2c_clock_stretching_fix_ko.md`](mf_i2c_clock_stretching_fix_ko.md)
- 노출 중 이동 solve gate 검토: [`mf_solve_motion_gate_review_ko.md`](mf_solve_motion_gate_review_ko.md)

---

## 1. 한눈에 보는 결론

현재 PiFinder에서 IMU는 하나의 기능이 아니라 다음 세 계통에 동시에 쓰인다.

1. **plate solve 사이의 pointing estimate 진행**
   - 마지막 성공 solve와 같은 프레임의 IMU quaternion을 anchor로 잡는다.
   - 이후 현재 quaternion과의 회전을 적용해 camera/aligned estimate를 갱신한다.
   - 이 경로의 이동 deadband는 고정값 `0.06°`이다.
2. **이동 감지·절전 해제·UI 표시**
   - 연속 quaternion 성분의 L1 차이와 hysteresis로 `moving`을 만든다.
   - 사용자 `Sensitivity` 설정은 이 경로의 임계값만 배율 조정한다.
3. **SkySafari/INDI용 무해결 좌표와 mount 교란 보정**
   - raw IMU 자세를 Alt/Az로 바꾸고 별도 smoothing을 적용한다.
   - aligned mount가 있으면 빠른 IMU 변화만 외란 delta로 누적한다.
   - 이 경로는 `PointingCoordinateService`가 5 Hz로 갱신한다.

따라서 “IMU 감도”라는 단일 표현과 달리 실제로는 서로 다른 임계값·필터·갱신률을
가진 세 동작이 존재한다. 현재 설정 메뉴의 `Sensitivity`는 dead-reckoning 정확도나
SkySafari smoothing을 바꾸지 않는다.

구현의 장점은 명확하다.

- plate solve가 절대 기준을 반복해서 재설정하므로 IMUPLUS의 yaw drift를 장기간
  절대 위치로 오인하지 않는다.
- camera axis와 aligned axis를 하나의 dead-reckoner에서 함께 진행해 target pixel
  정렬 오프셋을 유지한다.
- BNO055의 I2C clock stretching 문제를 보드 세대별 bus 선택으로 우회한다.
- quaternion을 프로세스 사이에서 4개 float로 직렬화해 알려진 메모리 누수를 피한다.
- mount가 스스로 움직이는 구간과 BNO055 재수렴 구간을 외란으로 누적하지 않도록
  별도의 mount+IMU gate가 있다.
- telemetry record/replay가 실제 integrator 경로를 재사용한다.
- 실행 중 sensor read 오류를 sample health로 게시하고, 연속 3회 실패하면 fake
  driver로 격리한 뒤 물리 센서를 지수 backoff로 다시 초기화한다.
- live consumer는 calibration·health·quaternion norm·1초 freshness를 한 계약으로
  검사하며, solver도 frame epoch 기준으로 유효한 sample만 anchor로 채택한다.

P0 반영 뒤에도 남은 주요 개선 후보는 다음과 같다.

- IMU process 자체가 예기치 않게 종료되는 경우를 main process가 감지·재시작하는
  supervisor는 아직 없다. 다만 정상 sampling loop의 sensor 예외는 process 안에서
  격리·복구한다.
- 물리 카메라의 노출 중 이동량 `imu_delta`는 측정되지만 solver gate로 연결되지 않았다.
- `Sensitivity = Off`는 IMU tracking을 끄지 않으며 큰 움직임은 `moving`으로 잡힐 수 있다.
- quaternion artifact/flip을 버린 주기에도 timestamp가 새로 게시될 수 있어
  “새 자세의 측정 시각”이라는 의미가 흐려진다.
- calibration level 0에서 최대 약 30 Hz warning을 낼 수 있다.
- driver/filter/monitor의 핵심 실패 동작을 직접 고정하는 단위 테스트가 부족하다.

이 항목들은 16장에서 근거·영향·권고 순서와 함께 자세히 정리한다.

---

## 2. 분석 시점 장치의 유효 설정

장치 모델은 `Raspberry Pi 5 Model B Rev 1.0`이다. 사용자 config에 없는 값은
`default_config.json`에서 보충되므로 분석 시점의 IMU 관련 유효 설정은 다음과 같다.

| 항목 | 유효값 | 출처 | 실제 영향 |
|---|---:|---|---|
| `screen_direction` | `flat3` | 사용자 config | IMU frame→camera frame 고정 회전 |
| `imu_threshold_scale` | `1` | 기본값 | `moving` 시작/종료 임계값 배율 |
| `imu_use_magnetometer` | `false` | 기본값 | IMUPLUS 사용, magnetometer 미사용 |
| `imu_auto_calibration_store` | `true` | 기본값 | NDOF일 때만 자동 load/save에 관여 |
| `telemetry_raw_imu` | `false` | 기본값 | gyro/linear acceleration 추가 읽기 안 함 |
| `skysafari_imu_fallback` | `true` | 기본값 | 외부 좌표 서비스의 raw IMU fallback 허용 |
| `sleep_timeout` | `30s` | 기본값 | 30초 idle 후 sleep, IMU movement로 wake |
| `mount_control` | `true` | 사용자 config | aligned mount+IMU delta 경로 사용 가능 |
| `mount_type` | `Alt/Az` | 기본값 | mount fusion context 및 진단 frame |

`~/PiFinder_data/imu_bno055_calibration.json`은 분석 시점에 존재하지 않았다. 현재
compass가 꺼져 있어 시작 시 자동 calibration load 자체도 실행되지 않는다.

설정 변경과 런타임 반영 방식:

- `Sensitivity`, `Compass` 메뉴 변경은 PiFinder를 재시작한다.
- calibration Save/Load/Clear는 `imu_command_queue`로 실행 중 IMU process에 전달된다.
- `telemetry_raw_imu`는 IMU process 시작 때 읽는다. 별도의 런타임
  `set_raw_capture` 명령 구현도 있지만 현재 일반 UI/API에서 호출하는 경로는 없다.
- `skysafari_imu_fallback`은 integrator나 LCD tracking을 끄는 전역 IMU 스위치가
  아니다. `PointingCoordinateService`의 fallback 후보만 끈다.

---

## 3. 구성 요소와 소유권

| 구성 요소 | 책임 | 주요 입출력 |
|---|---|---|
| `i2c_bus.py` | 보드별 I2C bus 선택 | Pi 5/CM5 hardware I2C, 이전 보드 software I2C |
| `imu_pi.py::Imu` | BNO055 설정·읽기·기초 필터·movement 판정 | native quaternion, calibration, optional raw vectors |
| `imu_pi.py::imu_monitor` | 명령 처리·샘플 생성·공유 상태 게시 | `shared_state.set_imu(ImuSample)` |
| `imu_fake.py` | fake hardware 및 물리 IMU 초기화 실패 fallback | 두 사용 형태의 동작이 서로 다름 |
| `imu_calibration.py` | BNO055 offset/radius 파일 저장·적용 | `imu_bno055_calibration.json` |
| `types/positioning.py::ImuSample` | 프로세스 간 IMU 데이터 계약 | quaternion/timestamp/status/moving/raw vectors |
| `camera_interface.py` | 노출 시작·종료 sample 비교, frame metadata 작성 | `imu`, `imu_delta` |
| `solver.py` | 성공 solve에 frame-end IMU quaternion 첨부 | `SuccessfulSolve.imu_anchor` |
| `integrator.py` | solve anchor와 IMU로 canonical pointing 진행 | `PointingEstimate` |
| `imu_dead_reckoning.py` | quaternion 기반 camera/aligned 예측 수학 | `solve()`, `predict()` |
| `main.py::PowerManager` | sleep/wake | `ImuSample.moving` 소비 |
| `pointing_coordinate_service.py` | 외부용 solve/IMU/mount 후보 선택·융합 | `CoordinateState` |
| `pos_server.py` | 5 Hz 좌표 서비스 실행, SkySafari IMU align | LX200 RA/Dec, status JSON |
| `ui/status.py`, `ui/base.py` | 상태·tube attitude·아이콘 | calibration/quaternion/moving 표시 |
| `api_extensions.py` | `/api/imu`, `/api/status` | JSON-friendly `ImuSample.to_dict()` |
| `telemetry.py` | IMU/solve record·replay | JSONL event stream |

전체 데이터 흐름은 다음과 같다.

```text
BNO055
  │  I2C, nominal 30 Hz
  ▼
imu_pi.Imu.update()
  ├─ calibration gate
  ├─ quaternion validation/artifact filter
  ├─ moving hysteresis
  └─ optional gyro + linear acceleration
  ▼
imu_monitor → shared_state.imu(): ImuSample
  ├─ Camera ── exposure start/end delta ── frame metadata
  │                                     └─ Solver ── SuccessfulSolve.imu_anchor
  │                                                   ▼
  │                                              Integrator
  │                                                   └─ PointingEstimate
  ├─ PowerManager ── sleep wake
  ├─ LCD/Status/API ── 표시·진단
  ├─ Telemetry ── record/replay
  └─ PointingCoordinateService
       ├─ raw IMU fallback
       └─ aligned mount + gated IMU disturbance delta
```

---

## 4. 프로세스 시작과 장애 fallback

### 4.1 시작 순서

`main.py`는 공유 상태 manager를 만든 뒤 대략 Webserver → Camera → IMU → Solver →
Integrator → Position server 순서로 process를 시작한다. Camera와 IMU 사이에는 1초
대기가 있지만 그 대기는 Camera 시작 직후에 있으므로, IMU가 준비되기 전에 첫 camera
frame이 만들어질 수 있다.

그 결과 초기 frame은 `metadata["imu"] = None`일 수 있다. 이 frame이 성공적으로
solve되면:

- camera/aligned solve와 estimate는 정상 게시된다.
- `SuccessfulSolve.imu_anchor`는 `None`이다.
- dead-reckoner는 NaN sentinel로 solve를 시도하고 초기화되지 않는다.
- 다음 성공 solve가 유효한 IMU sample을 함께 가질 때까지 IMU 진행은 일어나지 않는다.

이는 안전한 degraded 동작이지만 “첫 solve 직후 움직였는데 좌표가 따라오지 않는” 짧은
창을 만들 수 있다.

### 4.2 물리 IMU 초기화 실패

bus open, BNO055 생성, mode 설정 또는 초기 calibration load 중 예외가 발생하면
`imu_pi.imu_monitor`는 같은 process 안에서 `imu_fake.Imu`로 전환한다.

- console에 물리 IMU 오류와 `DEGRADED_OPS IMU`를 보낸다.
- main UI는 “Degraded / Check Status”를 표시한다.
- invalid `ImuSample`을 계속 공유 상태에 게시한다.
- status는 0, timestamp는 0이고 valid tracking은 시작되지 않는다.
- 최초 1초 뒤 물리 IMU 재초기화를 시도하며, 계속 실패하면 최대 30초까지 지수
  backoff한다.

### 4.3 fake hardware 실행 모드와의 차이

`--fakehardware`는 `imu_pi.imu_monitor`가 아니라 `imu_fake.imu_monitor`를 직접
사용한다. 이 monitor는 sleep loop만 돌고 `shared_state.set_imu()`를 호출하지 않는다.
따라서 다음 두 fake 경로는 동일하지 않다.

| 경로 | 공유 IMU sample | command queue |
|---|---|---|
| 실제 실행 중 물리 초기화 실패 | unhealthy sample을 반복 게시하고 물리 센서 재시도 | 소비하며 unsupported 응답 가능 |
| `--fakehardware` | 게시하지 않음 (`None` 유지) | 인자는 받지만 소비하지 않음 |

### 4.4 초기화 이후 장애

각 `Imu.update()`는 `_update_imu_safely()` 경계 안에서 실행된다.
`calibration_status`, `quaternion` 등 일반 sensor read가 예외를 내면:

1. 예외가 sampling loop 밖으로 전파되지 않는다.
2. 같은 shared sample에 `sensor_healthy=false`, 연속 오류 수, 마지막 오류를 기록한다.
3. `moving=false`로 강제해 오류 상태가 wake/movement로 해석되지 않게 한다.
4. 연속 3회 실패하면 fake driver로 교체하고 status/calibration/mode를 invalid로
   바꾼다.
5. 1초부터 최대 30초까지 지수 backoff로 물리 `Imu()`를 다시 만든다.
6. 재생성만으로 healthy를 선언하지 않고, 실제 새 I2C transaction의
   `last_io_time`이 전진한 뒤에만 오류 counter와 메시지를 지운다.

raw gyro/acceleration은 optional telemetry이므로 이 두 추가 read의
`OSError`/`RuntimeError`는 기존처럼 raw 값만 `None`으로 만들고 orientation read를
실패시키지 않는다.

남은 경계는 process-level crash다. 현재 main process에는 IMU process가 sensor 예외
외의 이유로 종료됐을 때 이를 감지해 재시작하는 supervisor가 없다. 이 경우에도 live
consumer의 1초 freshness gate가 마지막 sample 사용을 중단하지만 process 자동 복구는
별도 개선이 필요하다.

---

## 5. 센서 초기화와 calibration

### 5.1 I2C bus

`get_i2c()`는 device-tree model 문자열로 bus를 선택한다.

- Pi 5/CM5: `board.I2C()`, setup 기준 400 kbit/s hardware I2C.
- Pi 4 이하: GPIO2/GPIO3의 `i2c-gpio`, `/dev/i2c-3` software I2C.

이 분기는 BNO055의 clock stretching과 BCM2835/BCM2711 hardware I2C 문제를 피하기
위한 것이다. 분석 장치는 Pi 5이므로 hardware I2C 경로를 선택한다.

### 5.2 fusion mode

| 설정 | BNO055 mode | 센서 입력 | 의미 |
|---|---|---|---|
| `imu_use_magnetometer=false` | IMUPLUS | accelerometer + gyro + fusion | 기본. yaw는 상대 기준이며 drift 가능 |
| `imu_use_magnetometer=true` | NDOF | accel + gyro + magnetometer + fusion | 절대 heading 개선 가능, 자기장·보정에 민감 |

센서는 native axis quaternion을 반환한다. driver에서 축을 미리 바꾸지 않고,
`ImuDeadReckoning._q_imu2cam(screen_direction)`이 하드웨어 배치 회전을 담당한다.

지원되는 `screen_direction`은 `left`, `right`, `straight`, `flat3`, `flat`,
`as_bloom`이다. 알 수 없는 값은 integrator의 `ImuDeadReckoning` 생성에서
`ValueError`를 내므로 integrator process가 시작되지 않는다.

### 5.3 tracking calibration 판정

BNO055는 `(system, gyro, accel, magnetometer)` 각각 0~3을 보고한다. PiFinder가
`ImuSample.status`로 게시하는 값은 전체 system level이 아니라 **gyro level**이다.

```text
calibration_status = (sys, gyro, accel, mag)
status = calibration_status[1]
ImuSample.is_calibrated() = (status == 3)
```

따라서:

- gyro=0이면 quaternion을 읽지 않고 해당 update를 끝낸다.
- gyro=1 또는 2이면 quaternion과 movement는 갱신할 수 있지만 integrator와 raw
  fallback은 sample을 calibrated로 인정하지 않는다.
- gyro=3이면 IMUPLUS tracking에 사용할 수 있다.
- NDOF도 raw fallback/dead-reckoning의 최소 gate 자체는 gyro=3이다.
- NDOF의 “Calibrated!” 메시지와 auto-save는 네 component가 모두 3이어야 한다.

즉 “tracking 가능”과 “compass 전체 보정 완료”는 의도적으로 다른 상태다.

### 5.4 calibration 파일

파일: `~/PiFinder_data/imu_bno055_calibration.json`

저장 필드:

- accelerometer offsets
- magnetometer offsets
- gyroscope offsets
- accelerometer radius
- magnetometer radius

자동 동작:

- NDOF + `imu_auto_calibration_store=true`일 때만 시작 시 load한다.
- 같은 조건에서 모든 component가 3이 되면 실행당 한 번 auto-save한다.
- IMUPLUS 기본 모드에서는 자동 load/save를 하지 않는다.

수동 동작:

- Save: 현재 센서 값을 calibration 수준과 무관하게 즉시 저장한다.
- Load: 실행 중 센서 property에 값을 적용한다.
- Clear: 파일만 지운다. 이미 센서에 적용된 offset을 현재 session에서 초기화하지는 않는다.

현재 파일 format에는 `version=1`, `sensor=BNO055`가 기록된다. load 시 sensor와 필드
존재는 검사하지만 `version` 호환성은 검사하지 않는다. save는 일반 파일 overwrite이며
config 저장과 달리 temp file + fsync + atomic rename을 쓰지 않는다.

실행 중 수동 Load가 자세 출력을 점프시켜도 integrator anchor를 reset/reseed하는
연결은 없다. 다음 plate solve가 들어오면 자연히 새 anchor로 교정된다.

---

## 6. 30 Hz sample 처리의 정확한 순서

### 6.1 cadence

`imu_sample_frequency`라는 이름의 값은 frequency가 아니라 period이며 `1/30`초다.

1. `Imu.update()`가 마지막 sample로부터 1/30초가 안 됐으면 즉시 return한다.
2. monitor는 update·명령·publish에 쓴 시간을 period에서 빼고 남은 시간만 sleep한다.
3. 정상 물리 경로는 nominal 30 Hz read/publish를 목표로 한다.
4. manager proxy pickle 비용을 줄이기 위해 monitor 자체도 pacing한다.

이 pacing은 과거 hot loop의 약 19% CPU 사용과 반복 pickle memory 문제를 줄이기 위해
들어갔다.

### 6.2 update 순서

한 sensor update는 다음 순서다.

```text
period gate
  → calibration_status read/log
  → status = gyro calibration level
  → gyro level 0이면 중단
  → NDOF 전체 3이면 선택적 auto-save
  → quaternion read
  → float 변환 + norm 검사
  → 선택적 gyro/linear_acceleration read
  → last_read_time 갱신
  → 이전 accepted quaternion과 성분 차이 계산
  → exact 0.0078125 artifact filter
  → >1.5 flip filter / 11회 지속 시 history reset
  → avg_quat에 최신값 저장
  → movement hysteresis 갱신
```

### 6.3 quaternion 유효성 검사

- convention: scalar-first `(w, x, y, z)`.
- 각 component를 float로 변환할 수 있어야 한다.
- norm은 finite이고 `0.8 <= norm <= 1.2`여야 한다.
- accepted quaternion을 driver 단계에서 명시적으로 normalize하지는 않는다.
- downstream dead-reckoning은 합성 결과를 normalize한다.

norm 허용 범위는 단위 quaternion의 정상 오차보다 넓은 방어 범위다. reject count,
마지막 reject 사유, 연속 I/O 오류 수는 공유 상태나 API에 게시되지 않는다.

### 6.4 artifact/flip filter

이전 accepted quaternion과의 차이는 실제 회전각이 아니라 다음 L1 성분합이다.

```text
reading_diff = |Δw| + |Δx| + |Δy| + |Δz|
```

두 특수 처리가 있다.

1. `reading_diff == 0.0078125`이면 BNO055 정지 진동 artifact로 보고 버린다.
2. `reading_diff > 1.5`이면 quaternion sign flip 또는 noise로 보고 버린다.
   10회 연속 뒤 11번째에는 현재 quaternion으로 history를 reset해 영구 고착을 막는다.

quaternion의 `q`와 `-q`가 같은 회전을 나타내는 double-cover는 integrator의
각도차 함수에서는 올바르게 처리된다. 그러나 driver의 L1 filter는 sign-invariant가
아니어서 별도의 큰 차이 heuristic으로 우회한다. 큰 실제 회전과 sign flip을
수학적으로 구분하지는 않는다.

또한 `last_read_time`은 artifact/flip filter보다 먼저 갱신된다. 따라서 filter가
현재 quaternion을 버린 주기에도 monitor는:

- 새 timestamp를 게시하고
- 이전 `avg_quat`을 다시 게시한다.

이 동작은 I2C read 시각은 맞지만 `timestamp`를 “게시 quaternion이 실제로 채택된
시각”으로 해석하면 맞지 않는다.

### 6.5 movement hysteresis

기본 임계값:

```text
start moving: reading_diff > 0.0005 × imu_threshold_scale
stop moving : reading_diff < 0.0003 × imu_threshold_scale
```

start와 stop 사이에 hysteresis가 있어 경계에서 flag가 빠르게 토글되는 것을 막는다.
메뉴값은 다음과 같다.

| 메뉴 | scale | start | stop |
|---|---:|---:|---:|
| High | 0.5 | 0.00025 | 0.00015 |
| Medium | 1 | 0.0005 | 0.0003 |
| Low | 2 | 0.0010 | 0.0006 |
| Very Low | 3 | 0.0015 | 0.0009 |
| Off | 100 | 0.0500 | 0.0300 |

단위는 degree나 radian이 아니라 quaternion component L1 합이다. 자세에 따라 같은
물리 회전도 component 변화량이 달라질 수 있으므로 감도값을 각도 임계값으로 직접
환산할 수 없다.

`Off`도 boolean disable이 아니다. 충분히 큰 움직임은 0.05를 넘으므로 movement가
켜질 수 있다. 더 중요하게는 이 scale이 integrator의 `0.06°` deadband나 외부 좌표
서비스의 smoothing/rate gate에는 전혀 적용되지 않는다.

---

## 7. 공유 데이터 계약과 freshness

`ImuSample` 필드:

| 필드 | 의미 |
|---|---|
| `quat` | scalar-first native IMU quaternion |
| `timestamp` | IMU process가 성공 read로 기록한 `time.time()` epoch |
| `status` | BNO055 gyro calibration level |
| `moving` | driver L1+hysteresis 이동 flag |
| `calibration_status` | `(sys, gyro, accel, mag)` |
| `fusion_mode` | `imuplus`, `ndof`, `unknown` |
| `uses_magnetometer` | magnetometer 사용 여부 |
| `gyro` | optional angular velocity, rad/s |
| `accel` | optional linear acceleration, m/s², gravity 제거 |
| `sensor_healthy` | 마지막 update 경로에 sensor 오류가 없는지 |
| `consecutive_errors` | 연속 sensor update 예외 수 |
| `last_error` | 마지막 예외의 type과 message |
| `last_success_time` | 마지막 성공 I2C transaction epoch |

`numpy.quaternion`을 그대로 pickle하면 현재 고정 dependency
`numpy-quaternion==2023.0.4`에서 누수가 발생하므로 `__getstate__`는 `(w,x,y,z)`
float tuple로 바꾸고 consumer process에서 다시 quaternion으로 만든다.

공유 상태는 latest-value slot 하나다. queue/history가 아니므로 느린 consumer는
중간 sample을 건너뛰고 최신 snapshot만 본다. 이는 pointing/UI에는 적합하지만
고주파 raw motion 분석에는 원본 30 Hz 보존을 보장하지 않는다.

P0에서 다음 계약을 추가했다.

```text
is_calibrated     = status == 3
orientation_valid = calibrated + sensor_healthy + finite quaternion
                    + 0.8 <= norm <= 1.2
is_fresh          = 유효 timestamp + age <= 1.0초
is_usable         = orientation_valid + is_fresh
```

`age_seconds()`는 아직 orientation read가 없거나 timestamp가 비정상이면 `None`을
반환한다. wall clock이 뒤로 보정돼 sample이 미래로 보이는 경우는 age 0으로 clamp한다.
live consumer는 `is_usable()`을 사용한다. 반면 telemetry replay와 frame에 이미 결합된
historical sample은 현재 wall clock과 비교하면 항상 stale이므로, 재생/프레임 내부
수학에는 age를 제외한 `orientation_valid()`를 사용한다.

이 계약은 “process alive”를 직접 측정하지 않는다. 대신 process가 멈춰 timestamp가
전진하지 않으면 1초 안에 모든 live consumer가 sample을 거부한다. 새 health 필드가
없는 과거 pickle은 `__setstate__`에서 healthy/zero-error 기본값을 보충한다.

---

## 8. camera와 solver에서의 IMU 동작

### 8.1 노출 중 이동량

camera는 각 물리 노출의 앞뒤에 `shared_state.imu()`를 읽는다.

```text
imu_start = exposure 직전 latest sample
capture
imu_end   = exposure 직후 latest sample
imu_delta = angular_diff(imu_start.quat, imu_end.quat), degree
```

frame metadata에는 `imu=imu_end`, `imu_delta`, exposure start/end가 들어간다.
`get_quat_angular_diff`는 double-cover를 처리한다. P0 이후 start와 end가 모두
각 endpoint의 실제 exposure start/end epoch에서 `is_usable()`일 때만 delta를 계산한다.
한쪽이라도 unhealthy/stale/uncalibrated이면 기존 metadata 계약대로 `imu_delta=0.0`을
게시한다. 긴 노출 때문에 start sample을 현재 wall clock 기준으로 잘못 stale 처리하지
않는다.

한계:

- start/end 사이 30 Hz trajectory 전체가 아니라 두 endpoint 차이만 본다.
- 왕복 진동은 endpoint가 같으면 0에 가까울 수 있다.
- camera publish와 metadata publish가 하나의 atomic object가 아니어서 이동 중
  image/metadata generation race 가능성이 기존 검토 문서에 기록돼 있다.

### 8.2 현재 movement frame 처리

test/debug image 경로는 angular diff가 0.01 rad를 넘으면 blank image로 바꿀 수 있다.
하지만 실제 물리 camera 경로는 움직인 frame도 그대로 solver에 전달한다.

`solver.py`의 현재 loop는 frame freshness(`exposure_end > last_solve_attempt`)만
확인하며 `metadata["imu_delta"]`를 solve reject 조건으로 사용하지 않는다. 기존
`max_imu_ang_during_exposure` 파라미터도 현재는 연결돼 있지 않다.

따라서 노출 중 움직이며 얻은 성공 solve는:

- 노출 동안 번진/평균화된 별 위치에서 camera/aligned 좌표를 만들 수 있고
- 노출 종료 시점 `imu_end.quat`을 anchor로 사용한다.

solve 좌표의 유효 시점과 anchor 시점이 어긋나면 그 오프셋이 다음 성공 solve까지
dead-reckoning에 유지될 수 있다. 상세 영향과 제안은 별도 motion-gate 문서가 정규
검토 기록이다.

### 8.3 성공 solve의 IMU anchor

solver는 frame metadata의 IMU sample이 frame epoch에서 `is_usable()`일 때만
`SuccessfulSolve.imu_anchor`로 옮긴다. freshness의 `now`는 solve 완료 시각이 아니라
`metadata["exposure_end"]`를 사용한다. 긴 solve 처리 시간 때문에 정상 frame sample을
잘못 stale로 판정하지 않으면서 다음을 모두 차단한다.

- calibration 전 sample
- runtime sensor error가 표시된 sample
- frame 노출 종료보다 1초 이상 오래된 sample
- NaN/Inf 또는 norm 범위 `0.8..1.2` 밖의 quaternion

gate를 통과하지 못해도 plate solve 자체는 camera-only 성공으로 게시되고
`imu_anchor=None`이 된다. 따라서 zero quaternion normalize나 불량 anchor 기반
dead-reckoning을 시작하지 않는다.

---

## 9. Integrator dead-reckoning

### 9.1 canonical pointing model

Integrator가 소유하는 `PointingEstimate`는 두 axis × 두 state다.

| | plate-solve truth (`solve`) | 현재값 (`estimate`) |
|---|---|---|
| camera optical axis | `camera.solve` | `camera.estimate` |
| aligned eyepiece axis | `aligned.solve` | `aligned.estimate` |

성공 solve 직후 두 estimate는 solve와 같다. IMU는 이후 estimate만 진행하며 solve
cell은 바꾸지 않는다.

### 9.2 anchor 설정 수학

고정 하드웨어 회전:

```text
q_imu2cam = f(screen_direction)
```

성공 solve에서:

```text
q_eq2cam      = pointing_to_quaternion(camera.solve)
q_eq2aligned  = pointing_to_quaternion(aligned.solve)
q_eq2x        = q_eq2cam × conjugate(q_anchor × q_imu2cam)
q_cam2aligned = conjugate(q_eq2cam) × q_eq2aligned
```

이후 sample에서:

```text
q_eq2cam(now)     = q_eq2x × q_imu(now) × q_imu2cam
q_eq2aligned(now) = q_eq2cam(now) × q_cam2aligned
```

`q_cam2aligned`가 target pixel로 배운 camera↔eyepiece 정렬 회전을 보존한다. 매 성공
solve마다 누적하지 않고 새 값으로 교체하므로 이전 오차가 계속 합산되지 않는다.

### 9.3 IMU 적용 gate

Integrator가 sample을 적용하려면 모두 만족해야 한다.

1. 이번 loop에 새 성공 solve를 적용하지 않았음.
2. dead-reckoner가 유효한 anchor로 초기화됨.
3. `estimate.imu_anchor`가 있음.
4. live에서는 `imu.is_usable()`, telemetry replay에서는
   `imu.orientation_valid()`가 true.
5. anchor quaternion과 현재 quaternion의 회전각이 `0.06°`보다 큼.
6. `predict()`가 유효한 camera/aligned 쌍을 반환함.

여기서 비교 대상은 직전 IMU sample이 아니라 **마지막 성공 solve의 anchor**다.
따라서 anchor에서 0.06°를 한 번 넘은 뒤에는 scope가 멈춰도 각 sample이 anchor보다
계속 멀기 때문에 estimate timestamp/source가 IMU로 갱신될 수 있다. 작은 움직임을
누적 적분하는 구조가 아니라 현재 절대 quaternion을 anchor frame에 직접 투영하는
구조이므로 중간 sample 누락이 각도 누적으로 증폭되지는 않는다.

`ImuSample.moving` flag는 이 gate에서 사용하지 않는다. Sensitivity를 Off로 해도
calibrated quaternion과 0.06° deadband 조건이 맞으면 pointing은 진행한다.

### 9.4 게시와 timing

IMU 적용 성공 시:

- `camera.estimate`, `aligned.estimate` 갱신
- `estimate_time = imu.timestamp`
- `solve_source = IMU`
- 새 aligned RA/Dec에서 constellation과 Alt/Az 재계산
- deep copy를 `shared_state.set_solution()`에 게시

`last_published_time`보다 estimate epoch가 커야 한다. 같은 stale sample을 integrator가
반복 poll해도 동일 timestamp면 재게시하지 않는다.

### 9.5 failed solve

failed solve는 기존 solve cells, estimate cells, IMU anchor를 보존하고:

- 최신 diagnostics/attempt time 갱신
- `solve_source = CAM_FAILED`
- auto-exposure가 실패를 즉시 보도록 무조건 게시

그 뒤 같은 loop에서 IMU가 anchor로부터 0.06°보다 멀면 estimate를 다시 진행하고
source를 `IMU`로 바꾼다. 정지 상태라 deadband 안이면 `CAM_FAILED`가 유지되지만
LCD/shared `PointingEstimate`에는 마지막 estimate가 남아 있다.

### 9.6 replay

Telemetry replay 중에는 live solver queue를 버리고 녹화된 `SuccessfulSolve`,
`FailedSolve`, `ImuSample`을 동일한 apply/advance 함수로 통과시킨다. replay 종료 시
estimate와 dead-reckoner를 unanchored 상태로 reset한다.

---

## 10. 이동 감지, 절전, UI, 상태/API

### 10.1 절전

`PowerManager`는 awake 상태에서 keyboard/UI activity만 idle timer에 반영한다. timeout
후 sleep에 들어가며 sleep 상태에서 `shared_state.imu().is_usable()`과 `moving`이 모두
true면 wake한다. stale/unhealthy sample의 과거 movement flag로는 깨우지 않는다.

중요한 세부 동작:

- awake 상태에서 IMU movement는 `last_activity`를 갱신하지 않는다.
- 즉 scope를 계속 움직여도 다른 activity가 없으면 timeout 시점에 일단 sleep으로
  전환될 수 있다.
- 다음 main loop에서 moving이 계속 true이면 곧바로 wake한다.
- 매우 느린 motor motion이 movement threshold 아래면 sleep을 막거나 깨우지 못한다.
- Camera는 sleep 중 약 30초마다 한 번만 주기 capture한다.

### 10.2 title bar

UI는 usable sample의 `moving`이 true이면 “마지막 camera solve 뒤로 움직이지 않음”
상태를 false로 만든다.
새 camera solve가 들어오면 다시 true가 된다. 따라서 camera icon의 밝기/표시와
push-to 숫자의 신뢰 표현은 driver movement flag에 영향을 받는다.

### 10.3 Status 화면

표시 항목:

- `Moving`/`Static` 또는 `Stale`/`Error`와 gyro status level
- sample age
- fusion mode와 `S/G/A/M` component level
- quaternion `(qw,qx)` / `(qy,qz)`
- `T.ALT`, `T.TILT`, `T.HDG`

Tube attitude는 quaternion에 `screen_direction`의 `q_imu2cam`을 적용한 뒤 camera
boresight를 ENU frame으로 해석한다. IMUPLUS에서는 `T.HDG` 절대값보다 변화량만
의미가 있다. tube attitude는 usable sample일 때만 계산한다. 현재 화면은 sample
age와 stale/error를 구분하지만 process PID/alive, 상세 I2C error 수, quaternion norm은
표시하지 않는다.

### 10.4 Web API

`GET /api/imu`와 `/api/status`의 `imu` 항목은 `ImuSample.to_dict()` 결과를 반환한다.
quaternion은 `[w,x,y,z]`, tuple은 JSON list다. health 필드와 계산된
`age_seconds`, `fresh`, `usable`도 포함한다. `/api/imu`는 sample이 없거나 unusable이면
503, usable이면 200을 반환한다. `/api/status`는 전체 상태 snapshot endpoint이므로
HTTP 200을 유지하고 nested IMU 필드로 상태를 판별한다.

API는 읽기 전용이다. calibration Save/Load/Clear 또는 raw capture를 제어하는 IMU
POST endpoint는 없다.

---

## 11. SkySafari/INDI 외부 좌표 경로

이 절은 IMU 관점의 요약이다. 전체 source priority와 mount 상태 계약의 정규 소유자는
`mf_coordinate_helper_plan_ko.md`다.

### 11.1 5 Hz 좌표 서비스

Position server process의 background thread가 0.2초마다 다음 후보를 만든다.

1. `solved`: canonical `PointingEstimate.aligned.estimate`
2. `imu`: raw IMU 자세에서 만든 fallback RA/Dec
3. `mount`: cached INDI mount readback

현재 우선순위:

```text
plate solve 또는 plate-anchored PiFinder IMU estimate
  > aligned mount + gated IMU disturbance delta
  > aligned mount only
  > raw IMU fallback
  > unavailable
```

### 11.2 raw IMU fallback 전제

모두 필요하다.

- `skysafari_imu_fallback=true`
- location 존재 및 `lock=true` (configured default location도 사용 가능)
- datetime 존재
- `imu.is_usable()` (`status==3`, healthy, finite/unit quaternion, age 1초 이하)
- quaternion이 camera boresight로 변환 가능

처리 순서:

```text
native IMU quaternion
  → q_imu2cam(screen_direction)
  → camera boresight ENU vector
  → raw Alt/Az
  → optional session-only SkySafari alignment offsets
  → adaptive smoothing
  → current location/time의 RA/Dec
```

IMUPLUS의 raw azimuth는 임의 yaw 기준이다. 첫 solve 전 절대 하늘 좌표로 쓰려면
SkySafari Align으로 현재 target과 raw IMU Alt/Az 사이 offset을 설정할 수 있다.
이 offset은 memory only이며 plate solve가 생기거나 Reset Pointing을 수행하면 지운다.

### 11.3 fallback smoothing

이 filter는 driver `moving`과 무관하며 이전 **smoothed** Alt/Az에 대한 구면 변화량을
사용한다.

| 변화량 | alpha | 상태 |
|---:|---:|---|
| 최초 sample | 1.0 | `initial` |
| `< 0.3°` | 0.06 | `smoothed_small_jitter` |
| `0.3° ~ <1.5°` | 0.25 | `smoothed_motion` |
| `1.5° ~ <5°` | 0.65 | `tracking_large_motion` |
| `>= 5°` | reset/1.0 | `reset_large_motion` |

raw와 smoothed 값, filter state, quaternion norm, calibration/mode/raw vectors는
pointing coordinate status metadata에 기록된다.

### 11.4 `CAM_FAILED` 경계

외부 좌표 서비스의 solved 후보는 source가 `CAM` 또는 plate anchor가 있는 `IMU`일
때만 유효하다. `CAM_FAILED`는 `PointingEstimate`에 보존된 estimate가 있어도 solved
후보에서 거부한다.

따라서 failed solve 직후:

- LCD/Web의 canonical solution은 마지막 estimate를 계속 보유한다.
- integrator가 같은/다음 loop에 0.06° 이상 IMU 진행을 하면 source가 `IMU`가 되어
  외부 좌표도 다시 plate-anchored estimate를 쓴다.
- 정지 상태라 source가 `CAM_FAILED`에 머물면 Positioning service는 preserved estimate
  대신 aligned mount 또는 raw IMU fallback으로 내려간다.

이는 “failed solve에서도 마지막 estimate를 보존한다”는 integrator 정책과 외부
좌표 선택 정책 사이의 의미 차이다. 의도된 source 격리인지, 외부 좌표의 순간 전환
원인인지 개선 전에 명시적으로 결정해야 한다.

### 11.5 mount+IMU disturbance delta

plate-anchored solved 후보가 없고 mount가 usable+aligned일 때, IMU는 mount 절대 좌표와
평균되지 않는다. mount readback을 기준으로 빠른 물리 외란만 offset으로 누적한다.

핵심 gate:

- update: 5 Hz
- 외란 episode 진입: `0.03°/s` 이상
- episode 유지/탈출: `0.015°/s`
- mount motion 종료 후 quiet: 1.5초
- mount readback motion hold: 1.5초
- tracking catch-up budget cap: 축별 3.0°
- zenith guard: IMU altitude 80° 이상에서 boresight minimal-arc 사용

모든 mount type의 우선 경로는 Alt/Az boresight 단위벡터의 per-tick 회전을 mount
frame으로 옮기는 quaternion tracker다. 이는 IMUPLUS yaw offset과 zenith azimuth
singularity를 피한다.

mount가 GoTo/manual/pulse로 스스로 움직이는 동안에는:

- mount readback을 우선한다.
- IMU 기준점은 전진시키되 외란 offset을 누적하지 않는다.
- 기존 외란 offset은 보존한다.
- motion 종료 뒤 BNO055의 자세 slide가 충분히 조용해질 때까지 재무장하지 않는다.

sidereal tracking보다 BNO055 출력이 느리게 멈췄다가 약 0.3°씩 따라잡는 현상을 실제
push로 오인하지 않도록, 예상 tracking motion을 budget으로 쌓고 같은 방향 catch-up
component를 상쇄한다.

### 11.6 stale sample 영향

raw fallback과 mount+IMU disturbance 입력은 모두 `imu.is_usable()`을 통과해야 한다.
timestamp가 1초 이상 전진하지 않거나 sensor health가 false가 되면 raw IMU 후보를
만들지 않고 mount fusion에도 새 IMU delta를 공급하지 않는다. 따라서 고정된 마지막
quaternion을 current datetime으로 계속 RA/Dec 변환하는 동작은 차단된다.

plate-anchored canonical estimate도 live integrator에서 같은 freshness gate를 사용한다.
마지막으로 계산된 pointing snapshot 자체를 삭제하지는 않지만 stale IMU로 epoch/source를
새로 진행하지 않는다.

---

## 12. Telemetry

### 12.1 기록

`telemetry_record=true`이거나 런타임 recording 명령이 들어오면
`~/PiFinder_data/telemetry/<session>/session.jsonl`에 기록한다.

IMU event:

```text
t     sample timestamp
e     "imu"
q     [w,x,y,z], 소수점 5자리
mv    moving
st    gyro calibration level
gyro  optional
accel optional
ok    sensor healthy
ec    consecutive error count
err   last error
lst   last successful I2C transaction epoch
```

- 같은 timestamp와 같은 health signature는 중복 기록하지 않는다. timestamp가 멈춰도
  health/error 상태가 바뀌면 새 event를 기록한다.
- healthy stationary sample은 10개 중 1개만 기록한다.
- moving sample은 모두 기록한다.
- unhealthy sample은 stationary decimation을 우회해 상태 전이를 보존한다.
- raw gyro/accel을 얻으려면 별도의 추가 I2C read가 필요하다.
- calibration component tuple, fusion mode, magnetometer 사용 여부는 현재 IMU event에
  기록하지 않는다.

### 12.2 buffer와 손실

recorder는 최대 300 line deque를 쓰고 background thread가 약 5초마다 flush한다.
producer가 flush보다 빠르면 가장 오래된 event가 자동 탈락하며 drop count를 센다.

### 12.3 replay fidelity

replay는 quaternion, timestamp, status, moving, gyro, accel과 health/error 필드를
복원한다. P0 이전 recording은 health 필드가 없으므로 healthy/zero-error로 읽는다.
기록하지 않은 `calibration_status`, `fusion_mode`, `uses_magnetometer`는 기본값으로 돌아간다.
pointing 수학과 movement gate 검증에는 충분하지만 NDOF/IMUPLUS mode별 현상을 완전히
재현하는 format은 아니다.

---

## 13. 임계값과 갱신률 통합표

| 계층 | 값 | 기준/단위 | 사용처 | Sensitivity 영향 |
|---|---:|---|---|---|
| sensor read | 30 Hz nominal | wall-clock period | BNO055 read/publish | 없음 |
| live freshness | `<=1.0 s` | sample timestamp age | 모든 live IMU consumer | 없음 |
| recovery trigger | 3회 연속 오류 | sensor update exception | fake 격리·재초기화 시작 | 없음 |
| reinitialize backoff | 1 s → 최대 30 s | monotonic retry schedule | 물리 IMU 자동 복구 | 없음 |
| driver artifact | `0.0078125` exact | quaternion L1 diff | 정지 진동 reject | scale 전 선처리 |
| driver flip | `>1.5` | quaternion L1 diff | sign flip/noise reject | scale 전 선처리 |
| movement start | `0.0005×scale` | quaternion L1 diff | wake/UI/telemetry | **있음** |
| movement stop | `0.0003×scale` | quaternion L1 diff | wake/UI/telemetry | **있음** |
| integrator deadband | `>0.06°` | anchor↔current 회전각 | canonical estimate 진행 | 없음 |
| fallback update | 5 Hz | service loop | SkySafari/INDI 좌표 | 없음 |
| fallback smoothing | 0.3/1.5/5° | previous smoothed↔raw Alt/Az | raw IMU fallback | 없음 |
| mount delta enter | 0.03°/s | raw boresight step rate | 외란 누적 시작 | 없음 |
| mount delta exit | 0.015°/s | raw boresight step rate | episode 유지/종료 | 없음 |
| post-mount quiet | 1.5 s | rate < exit 지속 | 외란 감지 재무장 | 없음 |
| mount motion hold | 1.5 s | readback/motion state | mount 우선 유지 | 없음 |
| tracking budget cap | 3.0°/axis | expected-unreported motion | catch-up snap 상쇄 | 없음 |
| zenith guard | 80° altitude | raw IMU Alt | vector minimal-arc 전환 | 없음 |
| stationary telemetry | 1/10 sample | `moving=false` | JSONL downsample | movement를 통해 간접 영향 |

한 설정이 모든 행을 조정하지 않는다는 점이 현재 tuning에서 가장 중요한 구조적
사실이다.

---

## 14. 대표 상태 전이

### 14.1 정상 부팅 → 첫 solve

```text
IMU process 시작
  → calibration status 게시
  → gyro level 3
  → 30 Hz quaternion 게시
  → camera frame 끝 sample을 metadata에 첨부
  → solver 성공
  → integrator가 camera/aligned + IMU anchor로 dead-reckoner seed
  → solve_source=CAM
```

### 14.2 solve 뒤 scope 이동

```text
현재 quaternion이 solve anchor에서 0.06° 초과
  → camera/aligned estimate 예측
  → estimate_time=sample timestamp
  → solve_source=IMU
  → LCD/Web/SkySafari가 plate-anchored estimate 사용
```

### 14.3 solve 실패

```text
FailedSolve
  → 기존 solve/estimate/anchor 보존
  → diagnostics 갱신
  → solve_source=CAM_FAILED
  → shared solution 즉시 게시
  → 충분한 IMU 이동이면 곧 source=IMU
  → 정지면 외부 좌표 서비스만 mount/raw fallback으로 전환 가능
```

### 14.4 sleep/wake

```text
30초 UI idle
  → power_state=0, display/camera 저전력
  → movement L1 start threshold 초과
  → power_state=1
  → camera 정상 cadence 복귀
```

### 14.5 첫 solve 없는 SkySafari Align

```text
location/time + calibrated IMU
  → raw IMU Alt/Az
  → 요청 RA/Dec의 Alt/Az와 offset 계산
  → session-only alt/az correction 저장
  → IMU_PRIMARY_UNSOLVED 좌표 제공
  → 첫 plate solve 또는 Reset에서 correction 제거
```

### 14.6 aligned mount 외란

```text
plate solved 후보 없음 + mount aligned
  → mount readback을 reference로 anchor
  → IMU rate가 0.03°/s 넘으면 episode 시작
  → physical rotation delta만 q_off에 누적
  → 정지 후 q_off 유지
  → mount sync/location reset에서 anchor/offset 재설정
```

---

## 15. 현재 강점

1. **절대 기준과 상대 motion의 역할 분리**
   - plate solve는 truth, IMU는 estimate progression이라는 경계가 명확하다.
2. **camera/aligned 이중 axis 보존**
   - target pixel로 생긴 optical offset을 IMU 이동 중에도 quaternion 회전으로 유지한다.
3. **현재 자세 투영 방식**
   - gyro angular velocity를 시간 적분하지 않고 BNO055 fusion quaternion의 현재값을
     anchor에 직접 적용해 consumer sample 누락이 곧 적분 손실이 되지 않는다.
4. **failed solve 내구성**
   - solve 실패가 마지막 유효 pointing과 anchor를 지우지 않는다.
5. **double-cover 처리**
   - 실제 angular difference 계산은 `q`/`-q`를 같은 회전으로 본다.
6. **프로세스 직렬화 안전성**
   - hot-path quaternion pickle leak을 우회하고 monitor loop를 pacing한다.
7. **보드별 I2C 안정화**
   - Pi 4 이하 clock stretching 문제와 Pi 5 성능을 분리한다.
8. **mount motion 격리**
   - GoTo/manual/pulse와 post-motion BNO055 slide를 실제 외란과 분리한다.
9. **zenith/yaw offset 대응**
   - mount fusion은 scalar RA/Dec 차분보다 boresight rotation tracker를 우선한다.
10. **재현 기반**
    - synthetic drift test, dead-reckoning equivalence, mount fusion 상태 테스트,
      telemetry replay가 존재한다.

---

## 16. 개선 후보와 우선순위

아래는 현재 동작 분석에서 도출한 개선 목록이다. P0는 2026-08-25에 구현했으며,
나머지는 실장 telemetry로 임계값과 실패 빈도를 확인한 뒤 진행하는 것이 좋다.

### P0 — runtime IMU health와 stale 차단 — 구현 완료

#### P0-1. sampling loop 예외 격리·복구

**구현:**

- per-read 예외 처리와 연속 오류 counter
- 3회 연속 실패 뒤 fake driver 격리와 1~30초 sensor/bus reinitialize backoff
- 실제 성공 I2C transaction에서만 counter reset과 healthy 복귀
- 첫 오류부터 explicit unhealthy publication과 movement 해제

main-level process liveness supervision은 P0 sensor-read 경계 밖의 별도 최후 방어선으로
남아 있다.

#### P0-2. freshness 계약 추가

**구현:** sample age와 health를 명시적으로 분리했다.

```text
orientation_valid = calibration + finite unit quaternion
sample_fresh      = now - timestamp <= threshold
sensor_healthy    = process/read error state
```

live Integrator, PowerManager, PointingCoordinateService, camera delta, UI와 API/status가
공통 1초 `is_usable()` gate를 사용한다. raw IMU fallback과 mount fusion도 stale
sample을 invalid 후보로 제외한다. historical telemetry replay와 frame 후처리는 현재
wall clock age 대신 `orientation_valid()` 또는 frame epoch를 사용한다.

#### P0-3. calibration 전/invalid anchor gate

**구현:** solver는 frame metadata sample이 calibrated, healthy, fresh-at-exposure-end,
finite, norm-valid일 때만 anchor를 만들고, 아니면 camera-only 성공 solve로 처리한다.

### P1 — movement frame과 timestamp 의미 정리

#### P1-1. 노출 중 이동 solve gate 연결

**현재:** `imu_delta`를 측정하지만 실제 solver가 읽지 않는다.

**영향:** motion-blurred solve와 frame-end anchor의 epoch 불일치가 다음 solve까지
estimate bias로 남을 수 있다.

**개선 방향:** 기존 `mf_solve_motion_gate_review_ko.md`의 결정을 갱신하고 threshold,
skip-vs-failed semantics, image/metadata atomicity를 함께 확정한다.

#### P1-2. accepted orientation timestamp 분리

**현재:** artifact/flip reject 뒤에도 새 `last_read_time` + 이전 quaternion이 게시될 수
있다.

**개선 방향:** 최소 두 시각을 구분한다.

- `sensor_read_time`: I2C read 성공
- `orientation_time`: 현재 게시 quaternion이 실제 accepted된 시각

또는 reject 시 sample quaternion/timestamp를 함께 유지하고 health counter만 별도
갱신한다.

#### P1-3. quaternion-native movement metric

**현재:** component L1 차이는 sign과 자세에 의존한다.

**개선 방향:** `abs(dot(q_prev,q_now))` 또는 normalized relative quaternion의 회전각을
사용해 sign-invariant degree/radian threshold로 통일한다. BNO055 exact artifact를 별도
현상으로 유지할지 실측 corpus로 검증한다.

### P1 — 설정 의미와 source 정책

#### P1-4. `Sensitivity Off` 의미 수정

선택지는 둘 중 하나다.

- 이름을 `Wake sensitivity`로 좁히고 Off를 `Very insensitive`처럼 표현
- 실제 전역 IMU disable을 도입해 movement, dead-reckoning, fallback 정책을 명확히 끔

현재처럼 “Off이지만 일부 IMU 경로는 계속 동작”하는 의미가 가장 혼란스럽다.

#### P1-5. `CAM_FAILED` preserved estimate 정책 정합

Integrator는 preserved estimate를 유효 pointing으로 유지하지만 외부 좌표 서비스는
`CAM_FAILED` source를 거부한다. 다음 중 하나를 결정해야 한다.

- plate anchor + preserved estimate를 medium quality solved 후보로 인정
- 현재 전환을 의도된 정책으로 유지하되 status/문서에서 source change를 명확히 표시
- 실패 attempt와 pointing source를 한 enum에 함께 넣지 않고 별도 필드로 분리

세 번째가 데이터 모델상 가장 명확하다. `last_attempt_success`와 “현재 estimate의
생산자”는 서로 다른 사실이다.

### P2 — 진단·보정·테스트

#### P2-1. health telemetry 추가 확장

P0에서 sample age/healthy/error count/last error/last success를 shared state, Status,
API와 telemetry record/replay에 추가했다. 남은 권장 진단:

- effective read/publish Hz와 process liveness
- last error time
- accepted/rejected count와 reject reason
- quaternion norm
- movement angular delta/threshold
- calibration file load/save 상태
- process/fallback state

#### P2-2. calibration persistence 강화

- version 검사/migration
- temp file + fsync + atomic rename
- manual Save 전에 보정 상태 경고
- Clear가 “파일만 삭제”임을 UI에 명시
- Load 뒤 dead-reckoner re-anchor 정책 결정

#### P2-3. log rate limit

gyro level 0의 `NOIMU CAL` warning은 nominal 30 Hz까지 발생할 수 있다. 상태 변화 또는
주기 제한 로그로 바꾸면 실제 I2C 오류를 찾기 쉬워진다.

#### P2-4. fake IMU 계약 통일

`--fakehardware`와 물리 실패 fallback이 같은 `ImuSample`/command behavior를 갖도록
통일하면 headless/integration test가 실제 degraded path를 더 잘 재현한다.

#### P2-5. driver/monitor 테스트 추가

현재 자동 테스트가 강한 영역:

- dual-axis dead-reckoning 수학 및 legacy equivalence
- solve→IMU→solve sequence와 synthetic drift
- calibration snapshot field round-trip
- telemetry decimation/serialization/replay
- raw fallback smoothing
- mount+IMU source priority, motion hold, rate hysteresis, zenith, tracking catch-up
- update 중 sensor exception 격리와 성공 I/O 기반 health 복귀
- stale/unhealthy sample의 live consumer 및 solver anchor 차단
- health 상태의 API status code와 telemetry record/replay

현재 직접 고정하지 않는 영역:

- `Imu.update()` calibration gate와 30 Hz cadence
- quaternion norm/artifact/flip behavior
- movement sensitivity/hysteresis와 Off semantics
- monitor loop의 실제 시간 기반 fake 전환/reinitialize backoff end-to-end
- calibration auto load/save 조건과 수동 명령 end-to-end
- camera `imu_delta`→solver gate
- fake 두 경로의 공유 상태 equivalence

---

## 17. 권장 개선 순서와 검증 기준

### 단계 1 — 관측 가능성과 안전 차단 — 코드 구현 완료

1. sample freshness/health model 정의
2. I2C read 예외 격리와 invalid publication
3. status/API/telemetry에 age/error/reject 진단 추가
4. driver/monitor fake-sensor 단위 테스트

완료 기준:

- 실행 중 I2C를 끊었을 때 process가 죽지 않거나 명시적으로 재시작됨
- 제한 시간 안에 IMU source가 invalid로 전환됨
- LCD/API/status에서 stale/error를 구분 가능
- 연결 복구 후 새 sample epoch와 정상 source로 자동 복귀

자동 테스트에서는 예외 격리, health 전이, stale 차단, API/telemetry 계약을 확인했다.
위 완료 기준의 실제 I2C 분리·재연결 동작과 backoff timing은 실장 검증이 남아 있다.

### 단계 2 — motion 품질

1. quaternion-native angular movement metric
2. accepted orientation timestamp 정리
3. solve motion gate 연결 및 image/metadata epoch 검증
4. 실제 stationary/move/stop corpus로 threshold 재결정

완료 기준:

- `q`↔`-q` 전환이 movement나 rejection burst를 만들지 않음
- 같은 물리 각도에 자세별 sensitivity 편차가 작음
- 움직이는 노출의 solve-anchor bias가 정한 상한 이내
- 정지 solve 성공률에 유의한 회귀 없음

### 단계 3 — 정책과 UX

1. Sensitivity의 범위를 wake-only 또는 global로 확정
2. CAM_FAILED source 의미 분리/정합
3. calibration load/save/clear UX와 atomic persistence
4. fake/degraded behavior 통일

완료 기준:

- 사용자 설정명이 실제 영향을 정확히 설명
- LCD/Web/SkySafari가 같은 상황에서 왜 다른 source를 쓰는지 status로 설명 가능
- calibration 변경이 pointing jump를 만들 경우 reset/re-anchor가 예측 가능

### 단계 4 — 실장 회귀

최소 시나리오:

1. IMUPLUS 정지 30분: drift, false moving, sleep/wake
2. IMUPLUS 수동 push: 0.06° deadband 이후 latency/오차
3. NDOF 자력 환경 A/B: heading 안정성과 magnetic disturbance
4. 노출 중 slow/medium/fast move: solve skip/성공과 anchor bias
5. I2C disconnect/reconnect
6. calibration 0→3 전이와 restart/load
7. first solve 전/후 SkySafari Align
8. CAM_FAILED 정지/이동 source 전이
9. aligned Alt/Az 및 EQ mount의 tracking, GoTo, physical push
10. zenith crossing과 tracking catch-up snap

기록할 공통 값:

```text
sample epoch/age, quat/norm, calibration, read errors, reject reason,
moving metric/state, solve source, camera imu_delta,
predicted-vs-next-solve angular error,
coordinate-service mode/source/filter/gate/rate/budget
```

---

## 18. 코드 변경 시 보존해야 할 불변조건

1. IMU는 `solve` cell을 수정하지 않고 `estimate` cell만 진행한다.
2. anchor는 `camera.solve`와 같은 frame epoch의 IMU orientation이어야 한다.
3. camera와 aligned estimate는 같은 dead-reckoner에서 함께 갱신한다.
4. `q_cam2aligned`는 solve마다 교체하며 누적하지 않는다.
5. quaternion 비교는 double-cover를 처리해야 한다.
6. 프로세스 경계를 넘을 때 bare `numpy.quaternion`을 직접 pickle하지 않는다.
7. `estimate_time`은 계산/게시 시간이 아니라 실제 measurement epoch다.
8. failed solve는 마지막 유효 estimate/anchor를 지우지 않는다.
9. mount 자체 motion은 physical disturbance offset으로 누적하지 않는다.
10. 외란 offset은 scope가 멈춘 뒤에도 유지되고 명시적 re-anchor에서만 지운다.
11. IMUPLUS raw heading은 절대 북쪽이 아니라 임의 yaw 기준임을 유지한다.
12. Pi 4 이하 software I2C / Pi 5 hardware I2C 선택을 깨지 않는다.

---

## 19. 소스 및 테스트 참조

핵심 구현:

- `python/PiFinder/i2c_bus.py`
- `python/PiFinder/imu_pi.py`
- `python/PiFinder/imu_fake.py`
- `python/PiFinder/imu_calibration.py`
- `python/PiFinder/types/positioning.py`
- `python/PiFinder/camera_interface.py`
- `python/PiFinder/solver.py`
- `python/PiFinder/integrator.py`
- `python/PiFinder/pointing_model/imu_dead_reckoning.py`
- `python/PiFinder/pointing_model/quaternion_transforms.py`
- `python/PiFinder/pointing_coordinate_service.py`
- `python/PiFinder/pos_server.py`
- `python/PiFinder/telemetry.py`
- `python/PiFinder/main.py`
- `python/PiFinder/ui/status.py`
- `python/PiFinder/ui/base.py`
- `python/PiFinder/api_extensions.py`

주요 테스트:

- `python/tests/test_imu_calibration.py`
- `python/tests/test_imu_runtime.py`
- `python/tests/test_imu_dead_reckoning.py`
- `python/tests/test_imu_dead_reckoning_equivalence.py`
- `python/tests/test_integrator_drift.py`
- `python/tests/test_pointing_coordinate_service.py`
- `python/tests/test_telemetry.py`
- `python/tests/test_pointing_estimate.py`
- `python/tests/test_pos_server.py`
- `python/tests/test_api_imu.py`

이 문서는 위 파일의 현재 동작을 설명하는 living 기준선이다. 추가 개선 구현이 들어오면 최소한
2장(유효 설정), 6장(sample/filter), 9장(integrator), 11장(외부 좌표),
13장(임계값), 16장(backlog)을 함께 갱신해야 한다.
