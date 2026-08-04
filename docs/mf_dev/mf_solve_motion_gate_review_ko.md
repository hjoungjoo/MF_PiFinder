# 검토: 노출 중 이동 프레임의 솔브 게이트 미배선 (solve motion gate)

작성: 2026-07-16. 상태: **검토용 — 코드 수정 없음, 협의 후 결정.**

## 요약

카메라 노출 중에 망원경이 움직인 프레임을 솔버가 거르지 않는다. 이동량을
거부하는 파라미터(`max_imu_ang_during_exposure`)와 이동량 측정값(`imu_delta`)이
코드에 **둘 다 이미 존재하지만 서로 연결되어 있지 않다**. 그 결과 느린~중간
속도(수 arcmin/s ~ 1 deg/s)로 움직이는 동안 성공한 솔브가 다음 두 가지 오류를
만든다:

1. 솔브 좌표 자체가 노출 중 평균 위치로 편향된다 (별상이 번진 만큼).
2. IMU dead-reckoning의 기준쌍(솔브 좌표 ↔ IMU anchor)이 시간적으로 어긋나,
   **다음 솔브가 올 때까지 모든 IMU 예측 좌표가 그 오프셋을 유지**한다.

오프셋이 트래킹 가이드의 외란 임계값(15')을 넘으면 실제로는 움직이지 않았는데
disturbed → 복구 슬루가 오발될 수 있고, 가이드 펄스도 편향된 솔브를 기준으로
잘못된 방향/크기로 나간다.

## 배경: 좌표 파이프라인

```text
camera_interface (노출)
  ├─ imu_start = 노출 시작 시점 IMU 샘플
  ├─ [노출]
  ├─ imu_end   = 노출 종료 시점 IMU 샘플
  ├─ imu_delta = |imu_end - imu_start| (deg)   <- 노출 중 이동량, 측정만 함
  └─ metadata = {exposure_end, imu: imu_end, imu_delta, ...}

solver
  ├─ is_new_image: exposure_end > last_solve_attempt 만 확인
  ├─ (이동량 확인 없음)                          <- 문제 지점
  ├─ 솔브 성공 시 SuccessfulSolve{camera, aligned, imu_anchor=metadata.imu.quat}
  └─ imu_anchor = 노출 "종료" 시점 포즈

integrator (_apply_successful_solve)
  ├─ estimate 셀 = 솔브 좌표로 스냅
  └─ idr.solve(camera, aligned, imu_anchor)     <- q_eq2x 재계산

이후 솔브 공백 동안: estimate = idr.predict(현재 IMU)  <- q_eq2x 오류가 그대로 전파
```

## 문제 상세 (코드 근거)

### 1. 게이트 파라미터가 정의만 되어 있음

`python/PiFinder/solver.py:437`:

```python
def solver(
    ...
    max_imu_ang_during_exposure=1.0,  # Max allowed turn during exp [degrees]
):
```

이 파라미터는 함수 시그니처에 존재하는 유일한 등장이다. 함수 본문 어디에서도
읽지 않는다 (`grep -n max_imu_ang python/PiFinder/solver.py` → 437행 한 줄).

### 2. 이동량은 측정되지만 debug 모드에서만 사용됨

`python/PiFinder/camera_interface.py:270-296`: 노출 전후 IMU quat 차이로
`pointing_diff`를 계산하고 `imu_delta`(deg)로 metadata에 넣는다. 그러나:

```python
# Make image available
if debug and abs(pointing_diff) > 0.01:
    # Check if we moved and return a blank image
    camera_image.paste(self._blank_capture())
else:
    camera_image.paste(base_image)
```

**debug 모드에서만** 이동 프레임을 blank로 대체한다. 실 운용 모드에서는 이동
프레임이 그대로 솔버에 전달되고, `imu_delta`는 아무도 읽지 않는다.

### 3. IMU anchor가 노출 종료 시점 포즈

`python/PiFinder/camera_interface.py:291`: `"imu": imu_end`.
`python/PiFinder/solver.py:382-384`: `imu_anchor = last_image_metadata["imu"].quat`.

노출 중 움직였다면 솔브 좌표는 대략 노출 "중간"의 (번진) 위치, anchor는 노출
"끝"의 포즈다. `ImuDeadReckoning.solve()`는 이 둘이 같은 시점이라고 가정하고
`q_eq2x`(EQ→IMU 기준 프레임 회전)를 푼다:

```python
q_eq2x = q_eq2cam * (q_x2imu * q_imu2cam).conj()
```

솔브-anchor 시점 차이만큼 `q_eq2x`가 틀어지고, 이후 `predict()`가 내놓는 모든
estimate 좌표가 같은 크기의 오프셋을 갖는다. **이 오프셋은 다음 성공 솔브까지
지속된다** (자연 감쇠 없음).

### 오프셋 크기 추정

- 오프셋 ≈ 노출 중간~종료 사이의 이동량 ≈ `imu_delta / 2`
- 이동이 빠르면 별이 흘러 솔브 자체가 실패하므로 자연 상한이 있다. 그러나
  짧은 노출(0.2~0.4 s)에서는 0.1~1 deg/s대 이동에서도 솔브가 성공할 수 있고,
  이때 오프셋은 **수 arcmin ~ 수십 arcmin** — 트래킹 가이드 외란 임계값(15')을
  넘을 수 있는 크기다.
- 정상 운용에서 노출 중 이동(사이드리얼 추적 ~15"/s, 가이드 펄스 ~37"/펄스)은
  1' 미만으로 무해하다. 문제는 손 조작 감속 구간, 복구 슬루 종료 직후, 바람
  등의 중간 속도 구간이다.

### 파급 영향

| 소비자 | 영향 |
|---|---|
| LCD/SkySafari/Web 표시 좌표 | 솔브 순간 편향 좌표로 스냅, 이후 IMU 예측에 오프셋 지속 |
| 트래킹 가이드 외란 감지 | 오프셋 > 15'/tick 이면 가짜 disturbed → settle → **불필요한 sync+GoTo 복구** |
| 가이드 펄스 (`_current_plate_solve`) | 순수 CAM 솔브 셀을 쓰므로 편향된 솔브를 그대로 신뢰 → 잘못된 보정 펄스 (다음 정상 솔브에서 자가 수정) |
| Multi-point align / backlash | 솔브 좌표를 기준점으로 쓰는 절차가 편향된 값을 채택할 수 있음 |

### 부수 발견: 이미지-메타데이터 race (2차 이슈)

`camera_interface`는 이미지 paste(286행) → metadata 게시(296행) 순서로 쓰고,
솔버는 metadata 확인 → `camera_image.copy()` 순서로 읽는다. 이동 중에는 프레임
N의 metadata에 프레임 N+1의 이미지가 매칭될 수 있어 같은 계열의 (솔브, anchor)
불일치를 만든다. 정지 상태에서는 무해. 본 게이트가 들어가면 이동 중 프레임
자체가 걸러지므로 실질 위험도 함께 줄어든다 (별도 수정은 선택).

## 제안 수정

### 옵션 A (권장): 솔버에서 이동 프레임 스킵

`solver.py`의 `is_new_image` 확인 직후에 게이트를 추가한다:

```python
is_new_image = last_image_metadata["exposure_end"] > last_solve_attempt
if not is_new_image:
    continue

# 노출 중 이동 게이트: 움직이며 찍힌 프레임은 솔브하지 않는다.
# 솔브 좌표가 번진 위치로 편향되고, IMU anchor(노출 종료 포즈)와
# 시간이 어긋나 다음 솔브까지 estimate 전체가 오프셋을 갖게 된다.
imu_delta = float(last_image_metadata.get("imu_delta") or 0.0)
if imu_delta > max_imu_ang_during_exposure:
    last_solve_attempt = last_image_metadata["exposure_end"]
    logger.debug(
        "Skipping solve: moved %.2f deg during exposure (max %.2f)",
        imu_delta, max_imu_ang_during_exposure,
    )
    continue
```

핵심 설계 포인트:

- `last_solve_attempt`를 갱신해 같은 프레임을 매 루프 재검사하지 않는다.
- 조용히 스킵하며 `FailedSolve`를 **보내지 않는다**. 자동 노출은
  CAMERA_FAILED 결과로 동작하는데, 이동 프레임은 노출 품질과 무관하므로
  노출 조정 입력에서 제외하는 것이 맞다. (반론이 있으면 협의 — 아래 결정
  사항 3)
- integrator/dead-reckoning은 수정 불필요: 게이트에 걸린 프레임은
  `SuccessfulSolve`가 생성되지 않으므로 anchor 재계산도 일어나지 않고,
  기존 estimate가 IMU로 계속 전진한다 (현행 FailedSolve와 동일 경로).

### 임계값 권장: 기본 1.0 → 0.25 deg

| 후보 | 근거 | 비고 |
|---|---|---|
| 1.0 deg (현 기본값) | 원 의도 추정치 | 오프셋 최대 ~30' — 외란 임계값(15') 초과 허용이라 부족 |
| **0.25 deg (권장)** | 오프셋 최대 ~7.5' < 15' 외란 임계값; 정상 운용 이동(추적 15"/s, 펄스 37")의 30배 이상 여유 | 바람/진동으로 인한 정상 솔브 드롭 위험 낮음 |
| 0.1 deg | 더 엄격 | 강풍 등에서 솔브 드롭 증가 가능; 필요시 config로 |

BNO055 노출 중 노이즈 플로어는 0.01~0.05 deg 수준이므로 0.25 deg는 오탐과
충분히 분리된다.

### 옵션 B (후속 과제, 선택): anchor 시점 개선

`imu_start`/`imu_end`를 모두 metadata에 실어 중간 시점 포즈(slerp)를 anchor로
쓰면 잔여 불일치가 절반으로 준다. 옵션 A 게이트(0.25 deg) 이후 잔여 오차는
최대 ~7.5'라서 비용 대비 효과가 낮음 — 보류 권장.

### 옵션 C (후속 과제, 선택): 이미지-메타데이터 race 제거

솔버가 `camera_image.copy()` 후 metadata를 재확인해 `exposure_end`가 바뀌었으면
그 프레임을 버리는 방식. 옵션 A로 실질 위험이 줄므로 우선순위 낮음.

## 검증 계획

1. **단위 테스트**: solver 루프 게이트를 함수로 뽑거나 최소한
   `imu_delta`/`max_imu_ang_during_exposure` 경계 케이스를 검증
   (초과 → 스킵 + attempt 갱신 / 이하 → 솔브 진행).
2. **실장비**: 밤에 솔브가 도는 상태에서 경통을 천천히(~0.5 deg/s) 밀며,
   - journal에 스킵 로그가 찍히는지,
   - 밀기 종료 후 estimate 오프셋(다음 솔브 전 IMU 예측 vs 다음 솔브 값의
     차이)이 게이트 이전 대비 줄었는지 확인.
3. **회귀**: 정지 상태 장시간 운용에서 솔브 성공률이 떨어지지 않는지
   (게이트 오탐 없음) 확인.

## 결정 필요 사항 (협의)

1. **임계값**: 0.25 deg 기본 채택 여부, config 노출 여부
   (`solver_max_imu_ang_during_exposure` 등).
2. **적용 범위**: 옵션 A만 먼저 / B·C 포함 여부.
3. **스킵 방식**: 조용한 스킵(권장) vs `FailedSolve` 게시(자동 노출·진단에
   이동 프레임도 반영하고 싶은 경우).
4. **debug 모드 blank 처리**(camera_interface.py:282)와의 관계: 게이트가
   들어가면 debug 전용 blank는 중복이므로 정리할지.

## 참조 코드 위치

- `python/PiFinder/solver.py:437` — 미사용 파라미터
- `python/PiFinder/solver.py:504-519` — is_new_image 게이트 (제안 삽입 지점)
- `python/PiFinder/solver.py:382-384` — imu_anchor 캡처
- `python/PiFinder/camera_interface.py:267-296` — imu_delta 측정·metadata 게시
- `python/PiFinder/integrator.py:210-254` — 솔브 적용 + dead-reckoner 재시드
- `python/PiFinder/pointing_model/imu_dead_reckoning.py:77-95` — q_eq2x 계산
- `python/PiFinder/mountcontrol_indi.py:1525-1542` — 가이드 펄스의 솔브 소비
