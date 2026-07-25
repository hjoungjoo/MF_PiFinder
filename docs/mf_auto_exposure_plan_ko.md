# 자동 노출 — 검출 별 수 컨트롤러 추가 설계

> 상태: **Phase 1~3 구현 완료 — 현장 검증 대기** (2026-07-25)
> 결정 기록: [ADR 0020](adr/0020-star-count-controller-opt-in.md)
> 근거 조사: [mf_auto_exposure_methods_ko.md](mf_auto_exposure_methods_ko.md) (방법 조사, §6 권고안)
> 관련 문서: [docs/ax/camera.md](ax/camera.md) (현행 노출 제어 아키텍처, 정규 소유자),
> [docs/ax/camera/CONTEXT.md](ax/camera/CONTEXT.md) (용어집),
> [ADR 0010](adr/0010-zero-match-recovery-single-ladder.md),
> [mf_solve_motion_gate_review_ko.md](mf_solve_motion_gate_review_ko.md)
>
> **설계 원칙 (사용자 결정, 2026-07-25)**: 기존 기능(매치 수 컨트롤러 +
> zero-match 복구 + 배경 컨트롤러)은 **그대로 유지**한다. 새 방식은
> **옵션으로 추가**하고 사용자가 선택한다. 기본값은 현행(매치 수)이다.

## 1. 목표와 범위

조사 문서 §6 권고안을 구현 가능한 형태로 구체화한다.

**이번 범위 (Phase 1~3):**

1. 검출 센트로이드 수(`Centroids`)를 솔버 진단에 배선 — 양쪽 컨트롤러
   공용 신호 확보 (무해한 선행 변경).
2. 새 **검출 별 수 컨트롤러**(`ExposureStarCountController`) 추가 —
   cedar-server 검증 수치를 출발점으로 사용.
3. 컨트롤러 선택 옵션(config 키 + LCD 메뉴 + 카메라 명령) 배선.

**이번 범위 밖 (후속 Phase, 열어둠):**

- Phase 4: IMU 각속도 기반 이동 블러 동적 노출 상한 (조사 문서 방법 F).
- Phase 5: 검출을 솔브에서 분리한 빠른 케이던스 내부 루프 (방법 G의 완성형).
- 매치 수 컨트롤러 제거/외부 게이트화 — 검출 컨트롤러가 현장 검증된 뒤
  별도 결정.

**비목표:** 게인 피드백 제어(조사 §4 결론: 프로파일 고정 유지), 주간
정렬용 네이티브 AE 변경, SQM 배경 컨트롤러 변경.

## 2. 용어 (CONTEXT.md 준수 + 신규 제안)

- **검출 별 수 컨트롤러 (star-count controller)** *(신규)*: cedar-detect가
  프레임에서 검출한 센트로이드 수를 목표에 맞추는 컨트롤러. 매치 수
  컨트롤러와 같은 층위의 선택지.
  - 피하기: "cedar 컨트롤러"(출처명), "검출 모드"("mode" 회피 규칙).
- **`Centroids`** *(신규, Positioning 교차 용어)*: 최근 솔브 시도에서
  검출된 센트로이드 수. `Matches`처럼 성공/실패 모든 시도에 게시된다.
  `Matches`와의 차이가 원인 구분의 핵심: 검출 0 = 노출/광학 문제,
  검출 N>0 + 매치 0 = 솔버측 문제.
- **앵커 노출 (anchor exposure)** *(신규)*: 검출 별 수 컨트롤러가 "정상
  동작이 확인된" 것으로 기억하는 노출. 클램프 기준(±3스톱)과 폴백
  목적지로 쓰인다. cedar의 캘리브레이션 노출에 해당하되, 별도
  캘리브레이션 단계 없이 운전 중 학습한다(§4.3).
- 기존 용어(노출 체제, 매치 수 컨트롤러, 배경 컨트롤러, zero-match 복구,
  복구 사다리)는 그대로. 구현 완료 시 CONTEXT.md에 위 신규 용어를 추가한다.

## 3. Phase 1 — `Centroids` 신호 배선

검출 수는 이미 계산되지만(`solver.py:539-541`의 `len(centroids)`) 진단에
실리지 않는다. 컨트롤러와 무관하게 먼저 배선한다(진단 가치만으로도 유효).

### 3.1 `types/positioning.py` — `SolveDiagnostics`

```python
@dataclass
class SolveDiagnostics:
    Matches: int = 0
    Centroids: int = 0        # 신규: 검출 센트로이드 수 (모든 시도에 게시)
    RMSE: Optional[float] = None
    ...
```

- 기본값 0 (`Matches`와 같은 이유 — 자동 노출이 int를 기대).
- 필드명은 tetra3 스타일 대문자 표기(`Matches`와 나란히)로 통일.

### 3.2 `solver.py`

| 지점 | 변경 |
| --- | --- |
| `_build_successful_solve()` (`:357`) | 시그니처에 `centroid_count: int` 추가, `SolveDiagnostics(Centroids=centroid_count, ...)` |
| `_build_failed_solve()` (`:409`) | 시그니처에 `centroid_count: int = 0` 추가, 동일 배선 |
| 성공 경로 (`:594`) | `centroid_count=len(centroids)` 전달 |
| 실패 경로 (`:639`) | `centroid_count=len(centroids)` 전달 — 검출 0개로 솔브를 건너뛴 경우(`:545`)와 "검출은 됐지만 매치 실패" 경우가 이 값으로 구분된다 |
| 예외 경로 (`:652`) | **기본값 0 유지** (이 시점의 `centroids` 변수는 이전 루프의 잔재일 수 있어 전달하지 않는다) |

### 3.3 하위 호환

- `SuccessfulSolve`/`FailedSolve` → integrator → `shared_state.solution()`
  경로는 diagnostics를 그대로 통과시키므로 다른 소비자 영향 없음.
- 기존 매치 수 컨트롤러는 `Matches`만 읽으므로 동작 불변.

## 4. Phase 2 — 검출 별 수 컨트롤러

`auto_exposure.py`에 `ExposureStarCountController` 클래스를 추가한다.
기존 `ExposurePIDController`, `ZeroMatchRecovery`, `ExposureSNRController`는
**수정하지 않는다** (복구 사다리는 새 인스턴스로 재사용).

### 4.1 파라미터 (cedar-server 검증값 = 출발점)

| 파라미터 | 기본값 | 출처/근거 |
| --- | --- | --- |
| `target_stars` | 20 | cedar `star_count_goal` 기본값 |
| `ema_alpha` | 0.5 | cedar 검출 수 EMA |
| `deadband_low` / `deadband_high` | 0.8 / 1.6 | cedar 비대칭 데드밴드 — 부족(f<0.8)엔 즉시, 과잉(f>1.6)엔 관대 |
| `min_stars_for_control` | 4 | cedar: 미만이면 슬루/구름으로 보고 조정 대신 앵커 복귀 |
| `anchor_stop_range` | ±3스톱 (anchor/8 ~ anchor×8) | cedar 클램프 |
| `min_exposure` / `max_exposure` | 25 000 / 1 000 000 µs | 기존 매치 수 컨트롤러와 동일 절대 클램프 |
| `bright_sky_mean` | 240 (8-bit) | cedar 밝은 하늘 가드 |
| `bright_roi` | 중앙 256×256 (512×512 프레임의 중앙 절반) | cedar "중앙 height/2 정사각" 관례 |
| `initial_anchor` | 400 000 µs | 출하 기본 노출 = 복구 사다리 1단과 동일 |

### 4.2 조정 법칙

```python
def update(self, centroid_count, current_exposure, center_mean=None):
    # 1) 검출 0개 → zero-detection 복구 (기존 사다리 재사용)
    if centroid_count == 0:
        self._zero_count += 1
        return self._recovery.handle(current_exposure, self._zero_count)
    if self._recovery.is_active():
        self._recovery.reset()          # 검출 복귀 → 복구 종료
        self._ema = None                # 복구 여행이 EMA를 오염시키지 않게
    self._zero_count = 0

    # 2) 검출 소수(<4) → 슬루/구름으로 판단, 앵커로 복귀 (조정 안 함)
    if centroid_count < self.min_stars_for_control:
        return self._anchor if current_exposure != self._anchor else None

    # 3) EMA 갱신
    self._ema = (centroid_count if self._ema is None
                 else self.ema_alpha * centroid_count
                      + (1 - self.ema_alpha) * self._ema)
    f = self._ema / self.target_stars

    # 4) 밝은 하늘 가드: 별이 부족한데 배경이 이미 밝으면 올리지 않는다
    if f < 1.0 and center_mean is not None and center_mean > self.bright_sky_mean:
        return self._anchor if current_exposure != self._anchor else None

    # 5) 데드밴드 안 → 현재 노출을 앵커로 학습, 조정 없음
    if self.deadband_low <= f <= self.deadband_high:
        self._anchor = current_exposure
        return None

    # 6) 조정: 별 수 ∝ 노출 근사 → 나눗셈 법칙
    new_exposure = int(current_exposure / f)
    new_exposure = clamp(new_exposure, self._anchor // 8, self._anchor * 8)
    new_exposure = clamp(new_exposure, self.min_exposure, self.max_exposure)
    return new_exposure if new_exposure != current_exposure else None
```

설계 메모:

- **PID가 아니다.** cedar처럼 비례 나눗셈 1스텝 — 별 수∝노출 근사에서
  1~3스텝 내 수렴하고, 적분 와인드업·게인 튜닝 문제가 없다. 기존 PID의
  비대칭 정신(부족엔 빠르게/과잉엔 관대)은 데드밴드 비대칭(0.8/1.6)이
  대신한다.
- **zero-match 복구와의 관계**: 사다리(`ZeroMatchRecovery`)를 재사용하되
  트리거가 다르다 — 기존 컨트롤러는 "매치 0", 새 컨트롤러는 **"검출 0"**.
  이것이 ADR 0010이 명시한 책임 범위("노출이 크게 틀렸을 때만")를 신호
  수준에서 비로소 강제한다: 초점 흐림/솔버 실패로 검출은 되는데 매치가
  0인 프레임에서는 사다리가 돌지 않는다.
- **앵커 학습**: 데드밴드 안에 들어온 노출만 앵커로 저장. cedar의 1회성
  캘리브레이션 대신 운전 중 학습을 택한 이유 — PiFinder는 부팅 후 즉시
  운용이 시작되고, 복구 사다리(400 ms 시작)가 초기 탐색을 이미 담당한다.
  앵커는 재시작 시 `initial_anchor`로 초기화(영속화하지 않음, v1).
- **`center_mean`은 호출자가 계산해 전달**: `get_image_loop`에 이미
  `base_image`(512×512 L)가 있으므로 중앙 crop 평균(`np.mean`)을 솔브
  주기당 1회 계산 — 비용 무시 가능. 이미지가 없는 경로(테스트)에서는
  `None`으로 가드 생략.
- `reset()`, `get_status()` (target, ema, anchor, zero_count, recovery
  active)를 기존 컨트롤러와 같은 형태로 제공.

### 4.3 케이던스

v1은 기존과 동일하게 **새 솔브 시도가 있을 때만** 1스텝 동작한다
(`_last_solve_time` 게이트 재사용). 검출이 솔버 프로세스 안에서 솔브와
함께 일어나는 현 구조에서는 그 이상 빨라질 수 없다. 솔브 없이 검출만
고속으로 돌리는 것은 Phase 5로 분리(솔버 루프 개편 필요).

## 5. Phase 3 — 선택 옵션 배선

### 5.1 config 키

`default_config.json`:

```json
"camera_ae_controller": "match_count"
```

- 값: `"match_count"`(기본, 현행 유지) | `"star_count"`(신규).
- 알 수 없는 값 → `match_count`로 폴백 + 경고 로그 (ADR 0010의 stale
  config 처리 관례와 동일).
- 기존 사용자: 키가 없으면 기본값 적용 → **동작 변화 없음.**

주의: 기존 `set_ae_mode:pid|snr`(SQM 화면 스코프, 비영속)과는 별개 축이다.
이 키는 "기본 컨트롤러가 무엇인가"를 정하고, SQM 화면이 활성인 동안
배경 컨트롤러가 우선하는 기존 규칙은 그대로 둔다(§5.3).

### 5.2 카메라 명령

`camera_interface.py` 명령 파서에 추가 (기존 `set_ae_mode` 블록과 나란히):

```
set_ae_controller:match_count | set_ae_controller:star_count
```

- 유효값이면 `_ae_controller_choice` 갱신 + 해당 컨트롤러 인스턴스
  생성/reset + console 표시(`CAM: AE=Star Count` 등).
- config 저장은 명령이 아니라 메뉴 콜백에서(기존 `set_exposure` 콜백
  관례와 동일하게 `config_option`이 저장을 담당).

### 5.3 `camera_interface.py` 디스패치

상태 필드 추가: `_ae_controller_choice: str`("match_count"|"star_count"),
`_auto_exposure_star: Optional[ExposureStarCountController]`.

- **초기화** (`:184-192`): `camera_exp == "auto"`일 때
  `camera_ae_controller` 키를 읽어 선택 컨트롤러를 준비한다. 기존 gate
  (`_auto_exposure_enabled and _auto_exposure_pid`)가 매치 수 컨트롤러
  객체를 요구하므로 **`_auto_exposure_pid`는 선택과 무관하게 항상 생성**
  (기존 gotcha 유지 — 게이트 조건을 건드리지 않는 최소 변경).
- **디스패치** (`:326-360`): 기존 구조 유지, 기본 분기만 교체.

```
if self._auto_exposure_mode == "snr":        # SQM 화면 — 기존 그대로
    ... 배경 컨트롤러 ...
elif self._ae_controller_choice == "star_count":
    centroid_count = solution.diagnostics.Centroids
    center_mean = np.mean(np.asarray(base_image)[128:384, 128:384])
    new_exposure = self._auto_exposure_star.update(
        centroid_count, self.exposure_time, center_mean)
else:                                         # 기본 — 기존 그대로
    new_exposure = self._auto_exposure_pid.update(
        matched_stars, self.exposure_time)
```

- `set_exp:auto` 처리(`:403-413`)에서 두 컨트롤러 모두 reset.
- 로그 라인(`:367-371`)에 컨트롤러 이름과 (star_count일 때) 검출 수/f값
  포함 — 현장 사후 분석용.

### 5.4 LCD 메뉴

`ui/menu_structure.py` — Camera Exp와 Camera Gain 사이에 추가:

```python
{
    "name": _("Camera AE"),
    "class": UITextMenu,
    "select": "single",
    "config_option": "camera_ae_controller",
    "label": "camera_ae_controller",
    "post_callback": callbacks.set_ae_controller,   # 신규 콜백
    "items": [
        {"name": _("Match Count"), "value": "match_count"},
        {"name": _("Star Count"), "value": "star_count"},
    ],
},
```

- `callbacks.set_ae_controller`: 카메라 큐에
  `set_ae_controller:<value>` 전송 (기존 `set_exposure` 콜백 패턴).
- 신규 문자열("Camera AE", "Match Count", "Star Count")은 i18n 마킹 +
  Babel 파이프라인 통과 필요.
- 메뉴 위치·명칭은 확정 전(§8 열린 질문 Q4).

### 5.5 상태 표시 (선택)

`get_camera_exposure_display`(Auto 항목 서픽스)는 그대로 두되, 로그와
`get_status()`로 충분한지 현장 사용 후 판단. 웹 상태 노출은 이번 범위 밖.

## 6. 동작 비교 (완성 후 기대 상태)

| 상황 | 매치 수 컨트롤러 (기본, 현행 그대로) | 검출 별 수 컨트롤러 (신규 옵션) |
| --- | --- | --- |
| 정상 야간 | Matches 17±5 목표 PID | 검출 EMA/20 이 0.8~1.6 안에 들도록 나눗셈 스텝, 수렴 시 앵커 학습 |
| 희박한 별 영역 | 목표 미달 → 노출 최대로 상승 가능 (P3) | 검출 수는 카탈로그 무관 → 그대로 유지되기 쉬움 |
| 초점 흐림/솔버 실패 (검출 N>0, 매치 0) | zero-match 복구 사다리 순환 (P1) | 사다리 안 돎 — f 기준 정상 제어 유지 |
| 슬루/구름 (검출 <4) | Matches 0 → 사다리 | 앵커 복귀, 사다리는 검출 0일 때만 |
| 박명/달 (밝은 배경) | 매치 적으면 노출 계속 상승 (P4) | 중앙 평균 >240이면 상승 차단, 앵커 복귀 |
| SQM 화면 | 배경 컨트롤러 (기존) | 배경 컨트롤러 (동일 — 선택과 무관) |
| 주간 정렬 | 네이티브 AE (기존) | 네이티브 AE (동일) |

## 7. 구현 체크리스트

### Phase 1 — 신호 배선 (선행, 무해) — 완료 (2026-07-25)

- [x] `SolveDiagnostics.Centroids` 필드 추가 (`types/positioning.py`)
- [x] `_build_successful_solve`/`_build_failed_solve` 시그니처 + 호출부 배선
      (`solver.py` — 예외 경로는 stale 목록 위험 때문에 기본값 0)
- [x] 단위 테스트: `Centroids` 기본값/전달
      (`tests/test_auto_exposure_starcount.py::TestCentroidsDiagnostics`)
- [x] `docs/ax/positioning/CONTEXT.md`에 `Centroids` 항목

### Phase 2 — 컨트롤러 — 완료 (2026-07-25)

- [x] `ExposureStarCountController` 구현 — **신규 파일
      `auto_exposure_starcount.py`** (기존 `auto_exposure.py` 무수정 원칙,
      `ZeroMatchRecovery`는 import 재사용)
- [x] 단위 테스트 21종: 수렴(부족/과잉), 데드밴드-앵커 학습, <4 폴백,
      밝은 하늘 가드, 검출0 → 사다리 위임/복귀, EMA 리셋, 클램프(±3스톱, 절대)
- [x] `get_status()` 테스트

### Phase 3 — 옵션 배선 — 완료 (2026-07-25)

- [x] `default_config.json`에 `camera_ae_controller: "match_count"` 추가
- [x] `set_ae_controller:` 명령 파서 + 시작 시 config 로드(무효값
      match_count 폴백) (`camera_interface.py`)
- [x] 디스패치 분기 — star 컨트롤러는 lazy 생성(기존 SNR 컨트롤러 관례),
      중앙 ROI 평균은 호출부에서 계산 (`camera_interface.py`)
- [x] 메뉴 "Camera AE"(Camera Exp와 Camera Gain 사이) +
      `callbacks.set_ae_controller`
- [x] i18n: `nox -s babel` + de/es/fr/ko/zh 번역(AI-TRANSLATED 마커)
- [x] 검증: lint/format/mypy 통과, smoke 7, unit 754 통과
- [x] 문서: `docs/ax/camera.md` §3b, `docs/ax/camera/CONTEXT.md` 용어,
      [ADR 0020](adr/0020-star-count-controller-opt-in.md)

### 검증 (현장)

- [ ] 실내: debug 카메라로 전환/복귀, 회귀 없음 확인
- [ ] 실외 A/B: 같은 하늘에서 두 컨트롤러의 수렴 시간·최종 노출·솔브
      성공률 로그 비교 (은하수 안/밖, 달 유/무 각 1회 이상)
- [ ] 실외: 초점 링을 일부러 흐트러뜨려 "검출 N>0/매치 0"에서 star_count가
      사다리를 돌지 않는지 확인 (P1 개선의 직접 검증)

## 8. 열린 질문 (구현 전 결정)

| # | 질문 | 초안 입장 |
| --- | --- | --- |
| Q1 | `target_stars` 20(cedar) vs 17(기존 매치 목표)과의 관계 | 20으로 시작. 검출≥매치이므로 두 값은 비교 대상이 아님. 현장 A/B 후 조정 |
| Q2 | 검출 수 EMA를 컨트롤러 안(α=0.5)에서만 쓰는가 | 예 — 원시값은 진단에 그대로 남기고 평활은 컨트롤러 내부 상태 |
| Q3 | 밝은 하늘 가드 발동 시 게인 1단계 하향(조사 §4)도 넣는가 | v1 제외 — 게인 불변 원칙 유지, 가드는 "안 올림+앵커 복귀"만 |
| Q4 | 메뉴 명칭 — "Camera AE"? 항목명 "Star Count"가 사용자에게 자명한가 | 구현 시 결정. 후보: "Auto Exp Type" / 항목 "Standard·Star Detect" |
| Q5 | star_count 선택 시 실패 경로 예외(`Centroids=0`)가 사다리를 촉발할 수 있음 — 예외 빈도가 낮아 무시 가능한가 | trigger_count=2 + 예외는 산발적이므로 무시. 현장 로그로 재확인 |
| Q6 | 앵커를 config에 영속화(재시작 후 즉시 정상 노출)할 것인가 | v1 제외 — 복구 사다리 400 ms 시작이 이미 그 역할. 필요성 확인 후 |

## 9. 위험과 완화

| 위험 | 완화 |
| --- | --- |
| 신규 컨트롤러 결함으로 노출 폭주 | 절대 클램프(25 ms–1 s) + 앵커 ±3스톱 이중 클램프. 기본값이 match_count라 옵트인 사용자만 노출 |
| `Centroids` 배선 실수로 기존 경로 회귀 | Phase 1을 독립 커밋 + 기존 매치 수 경로는 `Matches`만 읽음(불변) 검증 테스트 |
| SQM 화면 전환과의 상호작용 | 디스패치에서 snr 분기가 최우선(기존 순서 유지) — star_count는 snr이 아닐 때만 |
| 수동/네이티브 체제와의 충돌 | 체제 전환 로직(`set_exp:*`, `exp_up/dn/save`) 불변 — 컨트롤러 선택은 솔버 구동 체제 내부에서만 의미 |
| cedar 수치가 우리 광학/센서에 안 맞음 | 파라미터를 생성자 인자로 유지(하드코딩 금지), 현장 A/B 체크리스트로 조정 |
