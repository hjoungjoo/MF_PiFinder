# Optical train / FOV 단계적 병합 및 야간 검증 절차

상위 `brickbots/PiFinder`의 optical-train/FOV 변경(#608, #609, #624,
#625, #628)을 MF_PiFinder의 cedar+SEP 하이브리드 솔버와 SQM 보정값을 보존한
채 받아들이기 위한 작업 기준이다.

상태: **기반·렌즈 선언 UI 구현됨 · 야간 검증 대기** (2026-08-19)

## 1. 범위와 안전 경계

FOV(field of view)는 센서만, 또는 렌즈만의 속성이 아니다. 실제 사용 중인
센서의 유효 crop 폭과 렌즈의 **실효 초점거리**를 함께 써서 계산한다. 이를
`optical train`이라고 부른다.

이번 낮/악천후 시간 작업에서 적용한 것은 다음 세 가지뿐이다.

1. `sqm/camera_profiles.py`에 센서 픽셀 피치, 기본 렌즈, 출하 렌즈 목록을
   메타데이터로 추가했다. mono/color 변형은 `replace()`로 이 정보를 그대로
   승계한다.
2. `PiFinder/optics.py`에 FOV, plate scale, 향후 tetra3 FOV gate 후보값,
   fitted FOV의 렌즈 후보 판별을 계산하는 **독립 모듈**을 추가했다.
3. Advanced > Lens 메뉴와 `camera_lens` 설정/shared state를 추가했다. 빈 값은
   `Automatic (not set)`이며, 사용자가 렌즈를 선언하지 않았음을 뜻한다.

아래 항목은 아직 연결하지 않았다. 따라서 이번 커밋만으로 기존 동작이나 현장
표시값이 바뀌지 않는다.

| 보류한 연결 | 현재 유지되는 동작 | 보류 이유 |
|---|---|---|
| 일반 tetra3 FOV gate | 고정 `12.0 +/- 4.0` | 실제 렌즈/센서별 solve 성공률 확인 필요 |
| cedar/SEP full-frame FOV | 기존 frame-map 및 경로별 계산 | crop/resize 좌표계와 함께 검증 필요 |
| radiometric SQM pixel area | 프로파일의 검증된 `radiometric_fov_degrees` | 값 변경은 SQM 절대값을 바꾸므로 기준계 비교 필요 |
| Chart frustum / API FOV | 기존 상수와 solve 진단값 | 화면/API 소비자 호환성 확인 필요 |
| Lens 선언값의 FOV 소비 | 메뉴/config/shared state까지만 구현 | 선언값을 믿어 gate를 좁히기 전 현장 확인 필요 |
| 자동 보정 | 없음 | 잘못된 설정값을 자동으로 덮어쓰지 않기 위함 |

## 2. 현재 기준값과 새 계산값

현재 radiometric SQM에 쓰이는 값은 현장 보정 상수이며 이번 작업에서 변경하지
않는다. 새 계산값은 기본 렌즈(16 mm 또는 HQ 25 mm)와 유효 crop을 사용해 그
기준을 재현하도록 만든 별도 값이다.

| 프로파일 | pixel pitch | 기본 렌즈(실효) | 기존 radiometric FOV | 계산 FOV 목표 |
|---|---:|---:|---:|---:|
| `imx296` / `_color` | 3.45 um | 16 mm (15.61 mm) | 13.71° | 13.71° ± 0.03° |
| `imx462` / `_color` | 2.90 um | 16 mm (15.61 mm) | 10.38° | 10.38° ± 0.03° |
| `imx290` | 2.90 um | 16 mm (15.61 mm) | 10.38° | 10.38° ± 0.03° |
| `hq` | 3.10 um (2x2 bin) | 25 mm (26.0 mm) | 10.34° | 10.34° ± 0.03° |

12 mm 렌즈는 실효 13.04 mm로 등록했다. 이 값은 렌즈 배럴 표기와 구별해야
하며, 기존 16 mm/SQM 보정값을 대체하지 않는다.

## 3. 저녁 전 정적 확인

이 문서 작성 시에는 낮이라 카메라 촬영·실제 solve·SQM 비교를 실행하지 않았다.
야간 시작 전에 아래만 먼저 실행한다.

```bash
cd /home/pifinder/PiFinder
python -m compileall -q python/PiFinder
PYTHONPATH=python pytest -q python/tests/test_optics.py python/tests/test_sqm.py
PYTHONPATH=python pytest -q python/tests/test_nearby.py python/tests/test_ui_modules.py
```

첫 명령은 문법/모듈 import, 두 번째는 새 계산과 기존 SQM 회귀, 세 번째는 이미
반영한 nearby 변경 및 UI 회귀를 나눠 확인한다. 실패하면 그 시점에서 FOV 런타임
연결을 진행하지 않고 실패 로그와 profile/lens 조합을 남긴다.

## 4. 야간 하드웨어 검증 순서

### A. 기준선 확보 (코드 연결 전)

1. 현재 `main`으로 평소 설정 그대로 부팅한다.
2. 카메라 타입, mono/color 변형, 장착 렌즈 배럴 표기, 해상도/crop, 노출/게인을
   기록한다.
3. 맑은 별 영역에서 정상 솔브 10회 이상을 기록한다. 각 시도의 성공 여부, 경로
   (`cedar_center`, `cedar_full`, `sep_center`, `sep_full`), fitted FOV, 시간,
   RA/Dec 오차를 저장한다.
4. 가능하면 같은 프레임/조건에서 현재 SQM 및 기준 SQM-L 값을 기록한다.

이 기록은 뒤 단계의 A/B 기준이다. 기준선 없이 FOV gate를 좁히면 "렌즈 때문인지
하늘/노출 때문인지" 구분할 수 없다.

### B. 계산값 대 fitted FOV 비교

실제 장착 렌즈에 대해 `build_optical_train(camera_type, lens).fov_degrees`를
출력하고 정상 solve의 fitted FOV들과 비교한다.

판정 기준:

- 중앙값이 계산 FOV의 ±5% 안이면 해당 렌즈 후보는 일치다.
- solve별 fitted FOV가 크게 흔들리거나 ±5% 밖이면 렌즈 자동 판별/좁은 gate는
  **보류**한다.
- 지원하지 않는 제3자 렌즈는 `identify_lens_from_fitted_fov()`가 `None`이어야
  한다. 가까운 출하 렌즈로 억지로 설정하거나 config를 쓰면 안 된다.

### C. 일반 tetra3 gate 연결 시험

별도 작업 커밋에서만 `solver.py`의 일반 tetra3 호출에 계산된
`(fov_estimate, fov_max_error)`를 전달한다. 순서는 다음과 같다.

1. **명시 렌즈**: 장착 렌즈를 config로 명시한 경우 계산 FOV의 ±15% gate를 쓴다.
2. **미명시 렌즈**: imx296/imx462/imx290은 출하 16/12 mm 범위를 모두 포함하는
   대칭 gate를 쓴다. 임의의 기본 렌즈 하나로 좁히지 않는다.
3. 각 경우 기준선과 같은 조건에서 10회 이상 solve한다.

통과 조건은 기준선 대비 성공률 저하 없음, timeout 증가 없음, 좌표 오차 악화 없음,
fitted FOV가 입력 gate 안에 안정적으로 남는 것이다. 하나라도 어기면 이 커밋만
되돌리고 기존 `12.0 +/- 4.0`을 유지한다.

### D. cedar/SEP full-frame 연결 시험

일반 tetra3 통과 후 별도 커밋으로 진행한다. `SOLVER_FOV_DEG` 전역 상수를
바꾸지 말고 cedar와 SEP가 이미 사용하는 resize/crop 폭을 인자로 받아 각 경로의
FOV를 계산한다. 이 값은 centroid 좌표, horizon mask, target pixel 변환에도
연결되어 있으므로 경로 하나씩 검증한다.

각 경로에서 중앙/전체 프레임, 정상/밝은 배경 조건을 각각 시험하고 다음을
확인한다.

- solve 결과의 RA/Dec 및 target overlay 위치가 기존과 같은 천체를 가리킨다.
- fitted FOV와 계산 FOV가 같은 정의(가로 crop 폭)인지 확인한다.
- cedar 실패 뒤 SEP 폴백 순서와 timeout 예산이 달라지지 않는다.

### E. SQM, Chart, API

solver 연결과 분리된 마지막 단계다.

1. SQM은 먼저 기존 `radiometric_fov_degrees`와 계산 FOV를 **로그 비교만** 한다.
   같은 sky/reference-meter 조건에서 SQM 차이를 기록한다.
2. 기존 보정 상수를 계산값으로 바꾸는 것은 여러 밤의 기준계 비교가 끝난 뒤에만
   별도 커밋으로 한다. 0.05 mag 이상의 체계적 차이는 즉시 보류한다.
3. Chart camera mask(현재 고정값)와 API의 solve-diagnostic FOV는 표시만 바꾸는
   별도 변경으로 분리하고, 웹 UI와 SkySafari 대상 좌표가 흔들리지 않는지 확인한다.

## 5. 자동 렌즈 판별(나중 단계)

fitted FOV는 optical train 전체를 측정하므로, 센서가 알려진 상태에서는 렌즈 후보를
고르는 근거가 될 수 있다. 다만 이 기능은 현재 연결하지 않았다.

연결 조건은 모두 충족해야 한다.

1. `camera_lens`가 비어 있을 때만 후보 판별을 수행한다. 사용자가 명시한 렌즈는
   자동으로 덮어쓰지 않는다.
2. 출하 렌즈와 계산 FOV의 ±5% 안인 solve만 후보로 인정한다.
3. 같은 후보가 **연속 3회** 성공 solve에서 재현될 때까지 config를 쓰지 않는다.
4. 제3자 렌즈/불일치/solve 실패는 로그만 남기고 설정을 바꾸지 않는다.
5. 설정 저장 실패는 solve 실패로 전파하지 않는다.

## 6. 커밋과 되돌리기 단위

각 단계는 아래처럼 독립 커밋으로 유지한다.

1. 기반 모듈/문서 (현재 단계)
2. Lens config/UI (현재 단계: 동작 변경 없는 명시적 메뉴)
3. 일반 tetra3 gate
4. cedar/SEP full-frame 매핑
5. SQM 보정 전환
6. chart/API 표시와 자동 렌즈 판별

현장 문제가 나면 문제가 난 단계의 커밋만 `git revert <commit>`한다. profile의
기존 `radiometric_fov_degrees`와 solver의 기존 고정 FOV를 먼저 바꾸지 않았기
때문에, 기반 모듈 자체는 현장 동작을 되돌릴 필요가 없다.

## 7. 기록할 결과

야간 종료 후에는 아래를 한 표에 남긴다.

| 일시 | 카메라/변형 | 렌즈 표기 | 경로 | 성공/시도 | fitted FOV 중앙값 | 계산 FOV | SQM/기준값 | 판정 |
|---|---|---|---|---:|---:|---:|---:|---|

이 표와 실패 로그를 바탕으로 다음 단계(일반 tetra3 gate 연결)를 승인한다. 결과가
불충분하면 현재 기반 커밋에서 멈추며, 실행 경로에는 아무 변화가 없으므로 안전하다.
