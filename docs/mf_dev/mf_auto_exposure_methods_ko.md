# 카메라 자동 노출·게인 제어 방법 조사

> **통합 관리 지점**: 자동 노출+SEP 솔빙 보강의 현행 상태는 [mf_sep_fullframe_impl_ko.md](mf_sep_fullframe_impl_ko.md)에서 관리한다 (이 문서는 이력).

> 상태: **조사 완료 — 설계 승격됨** (2026-07-25):
> §6 권고안은 [mf_auto_exposure_plan_ko.md](mf_auto_exposure_plan_ko.md)
> (기존 기능 유지 + 옵션 추가 설계)로 구체화되었다.
> 관련 문서: [docs/ax/camera.md](../ax/camera.md) (현행 노출 제어 아키텍처, 정규 소유자),
> [docs/ax/camera/CONTEXT.md](../ax/camera/CONTEXT.md) (용어집),
> [ADR 0010](../adr/0010-zero-match-recovery-single-ladder.md) (zero-match 복구 사다리),
> [mf_solve_motion_gate_review_ko.md](mf_solve_motion_gate_review_ko.md) (노출 중 이동 프레임 게이트)
>
> 목적: 현행 **솔브 결과(매치 수) 기반 자동 노출**의 구조적 문제를 정리하고,
> 대체/보강 가능한 방법들을 조사해 구현 방향 결정의 근거를 만든다.
> 이 문서는 조사(survey)이며, 확정 설계는 협의 후 별도 계획으로 승격한다.

## 1. 현행 구현 요약

정규 서술은 [docs/ax/camera.md](../ax/camera.md)에 있다. 여기서는 문제 분석에 필요한
뼈대만 요약한다.

```
솔버 프로세스                          카메라 프로세스 (get_image_loop)
  tetra3 솔브 시도                        프레임 캡처
    └─ Matches (성공/실패 매번) ────► shared_state.solution()
                                          │  새 last_solve_attempt일 때만
                                          ▼
                            ┌─ 매치 수 컨트롤러 (기본)
                            │    └─ Matches == 0 → zero-match 복구 사다리
                            └─ 배경 컨트롤러 (SQM 화면 전용)
                                          ▼
                               set_camera_config(exposure, gain)
```

- **매치 수 컨트롤러** (`auto_exposure.py::ExposurePIDController`,
  `python/PiFinder/auto_exposure.py:347`): 목표 `Matches` 17, 데드밴드 ±5,
  비대칭 PID(하향 보수적/상향 공격적), 클램프 25 ms–1 s. 새 솔브 시도가
  있을 때만 1스텝 동작.
- **Zero-match 복구** (`auto_exposure.py::ZeroMatchRecovery`,
  `python/PiFinder/auto_exposure.py:65`): 연속 2회 Matches=0이면 고정 사다리
  `[400, 800, 1000, 200] ms`를 각 2회씩 순환(ADR 0010).
- **배경 컨트롤러** (`auto_exposure.py::ExposureSNRController`): SQM 화면
  전용. 프레임 10퍼센타일 ADU를 노이즈 플로어 바로 위로 유지, ×1.3/÷1.3
  곱셈 스텝.
- **게인은 피드백 대상이 아니다**: 센서 프로파일 고정값
  (`sqm/camera_profiles.py` — imx296 15×, imx462 30×, hq 22×) 또는 수동 메뉴.
- 배선: `camera_interface.py:298-380` (솔브 결과 → 컨트롤러 →
  `set_camera_config`).

## 2. 현행 솔브 기반 방식의 문제점

| # | 문제 | 원인 구조 |
| --- | --- | --- |
| P1 | **피드백 신호가 원인을 구분하지 못한다.** Matches=0은 "너무 어두움/밝음" 외에도 초점 흐림, 노출 중 이동, 구름/가림, 솔버측 실패에서 똑같이 나온다. 복구 사다리는 노출 원인만 고칠 수 있는데, 다른 원인에서도 사다리를 순환하며 노출을 흔든다 (ax/camera.md §7 gotcha로 명시된 한계). | `Matches`가 유일한 입력. 이미지 자체 통계(포화, 배경, 검출 별 수)를 보지 않음 |
| P2 | **수렴이 솔브 주기에 묶여 느리다.** 조정 1스텝 = 솔브 시도 1회(수백 ms~1 s+). 크게 어긋난 노출에서 복구 사다리 1순환 = 솔브 8회. 박명·달빛·슬루 직후처럼 조건이 빠르게 변하면 따라가지 못한다. | 컨트롤러가 `last_solve_attempt` 갱신 시에만 동작 |
| P3 | **매치 수는 노출의 간접 지표다.** `Matches`는 검출 별 수가 아니라 tetra3가 카탈로그와 대응시킨 수 — FOV, 하늘 영역의 별 밀도, 패턴 DB에 따라 같은 노출에서도 크게 다르다. 은하수/희박 영역에서 목표 17이 물리적으로 달성 불가능하면 노출이 최대(1 s)로 끌려 올라간다(손떨림·블러 악화). | 목표가 "솔버가 쓴 별 수"이지 "프레임에 담긴 별 수"가 아님 |
| P4 | **포화/밝은 하늘 가드가 없다.** 밝은 배경(박명, 달, 광해)에서는 노출을 올려도 별 대비가 늘지 않는데, 매치 수가 적으면 계속 올린다. 이미지 평균/포화율 검사가 없다. | 이미지 통계 미사용 |
| P5 | **게인이 제어 루프 밖이다.** 노출만 조절하므로 어두운 하늘에서 노출이 길어져 수동(手動) 망원경의 이동 블러 한계와 충돌한다. 게인·노출의 역할 분담 정책이 없다. | 게인은 프로파일 고정/수동 |
| P6 | **검출 별 수를 이미 갖고 있는데 쓰지 않는다.** 솔버는 cedar-detect로 센트로이드를 추출한다(`solver.py:282-346`, 개수는 `:539-545`). "검출 N개 / 매치 0개"(솔버측 문제)와 "검출 0개"(노출/광학 문제)를 구분할 수 있는데 AE에 전달되지 않는다. | `SolveDiagnostics`에 매치 수만 배선 |
| P7 | **노출 중 이동 프레임이 피드백을 오염**할 수 있다. 이동 프레임 게이트가 미배선이라([mf_solve_motion_gate_review_ko.md](mf_solve_motion_gate_review_ko.md)) 블러 프레임의 실패가 CAM_FAILED로 AE에 들어온다. | 게이트 미구현 (별도 문서에서 협의 중) |

## 3. 조사한 방법들

### 방법 A — 검출 별 수 서보 (cedar-server 방식) ★ 가장 직접적인 선례

PiFinder와 같은 솔버 스택(cedar-detect/cedar-solve)을 쓰는
[cedar-server](https://github.com/smroid/cedar-server)(Steven Rosenthal)가
실제 구현한 방식. **매치 수가 아니라 cedar-detect가 검출한 별(센트로이드)
수**를 신호로 쓰고, 2단 구성이다.

**A-1. 1회성 캘리브레이션** (`server/src/calibrator.rs`):

- 목표 검출 별 수(`star_count_goal`, 기본 **20**)가 나오는 노출을 탐색.
- 조정 법칙: **검출 별 수 ≈ 노출에 비례** 모델.
  `new_exp = prev_exp / (검출수 / 목표수)`. 0.8–1.2배 안에 들면 수렴,
  최대 3회 반복.
- 근거: 노출 2.5× ≈ 한계등급 +1등급 ≈ 별 수 ~3×(등급 5 부근) — 소폭
  이동에서는 선형 근사로 충분.
- 부속 캘리브레이션: 1 ms 노출에서 **흑레벨 오프셋**을 "0값 픽셀 <0.1%"까지
  올려 블랙 크러시 방지(희미한 별 검출 보전).

**A-2. 프레임 단위 연속 서보** (`server/src/detect_engine.rs`): 매 프레임
(솔브 없이 검출만으로) 실행.

```text
검출 별 수 < 4          → 폴백 노출(마지막 정상값/캘리브레이션값)  # 슬루/구름
그 외:
  ma = 별 수 EMA(α=0.5)
  f  = ma / star_count_goal
  f < 1.0 이고 중앙 ROI 평균 > 240(8bit) → 폴백    # 밝은 하늘 가드
  f < 0.8 또는 f > 1.6   → exposure = prev / f     # 비대칭 데드밴드
                            (캘리브레이션값 ±3스톱 + [min,max] 클램프)
  그 외                  → 현재 노출을 "정상 폴백값"으로 기억
```

- **게인은 루프 밖 고정**: 야간에는 센서 최적 게인 1회 설정 —
  RPi 카메라는 최대 아날로그 게인(**IMX296 → 15×**; PiFinder imx296
  프로파일과 동일 값). 읽기 노이즈가 평평해지는 지점 + 8-bit 출력에서는
  다이내믹레인지 손실이 무의미하다는 판단.
- cedar-detect 자체가 이미지 노이즈 추정 기반 적응 임계값(σ×noise)을
  쓰므로, 검출이 넓은 노출 범위를 견딘다 → 거친 서보로 충분.

PiFinder 관점의 장점: **P1(검출0 vs 매치0 구분), P3(카탈로그 비의존),
P4(밝은 하늘 가드), P2(솔브 없이 검출만으로 프레임 단위 동작 가능)를 모두
직접 해소**한다. 검출기는 이미 우리 파이프라인에 있다.

### 방법 B — 이미지 통계(히스토그램/평균/퍼센타일) 서보

별을 세지 않고 프레임 밝기 통계를 목표에 맞춘다.

- **allsky** ([AllskyTeam mode_mean.cpp](https://github.com/AllskyTeam/allsky/blob/master/src/mode_mean.cpp)):
  마스킹된 이미지 평균(0–1 정규화)을 목표 평균에 맞추는 서보.
  **`exposureLevel = log2(gain × exposure_s) × steps²` 정수 사다리 하나로
  게인·노출을 통합 제어** — 주야간 20+스톱을 한 루프로 처리. 스텝은
  편차 크기에 따른 다항식 + 가중 이력/선형 예측으로 진동 억제.
- PiFinder의 기존 **배경 컨트롤러**(10퍼센타일 ADU ↔ 노이즈 플로어)가 이
  계열의 소형 구현이다.
- 한계: **밝기 통계는 "별이 검출되는가"와 직접 관련이 없다.** 광해 배경을
  목표 평균으로 맞추면 별 검출에는 과노출/부족일 수 있다. 별 검출 지표의
  보조 가드(포화 상한, 배경 하한)로는 유용하지만 주 신호로는 부적합.

### 방법 C — 별 SNR 서보 (PHD2 방식)

[PHD2](https://github.com/OpenPHDGuiding/phd2/blob/master/src/myframe.cpp)는
가이드 별 1개의 자체 SNR 지표(목표 6.0)에 대해
`newExp = exp × (target/SNR)²` (SNR ∝ √노출 가정), 상승 α=0.20/하강 α=0.15의
비대칭 평활로 서보한다. 검증된 부드러운 제어지만 **단일 별 기준**이라
플레이트 솔빙(별 "개수"가 필요)에는 지표가 어긋난다. 다중 별로 일반화하면
사실상 방법 A(+검출 임계 σ)와 수렴한다.

### 방법 D — 픽셀 임계 카운트 (위성 별추적기 계열)

고/저 임계값을 넘는 픽셀 수로 노출을 가감하는 초경량 방식
([SPARCS 등](https://arxiv.org/pdf/2507.03102)). 계산이 싸지만 핫픽셀·행성·
광해에 취약하고, cedar-detect가 이미 있는 우리에게는 이점이 없다.

### 방법 E — libcamera/picamera2 네이티브 AEC

`rpi.agc`는 평균 휘도 목표 기반이라 별 하늘(99.9% 근흑색)에서는 셔터·게인을
최대로 밀어 배경만 띄운다. 광해에서는 하늘 글로우에 노출을 맞춘다 — 지표
자체가 틀렸고, 수렴도 다중 프레임이 필요하다. allsky·cedar 모두 야간에는
자체 루프로 대체했고, PiFinder도 이미 `AeEnable=False`
(`camera_pi.py:61-64`)다. **주간 정렬 전용(현행 `set_exp:native`) 이상으로는
쓸 수 없다**는 것이 생태계 공통 결론
([picamera2 #592](https://github.com/raspberrypi/picamera2/discussions/592)).

### 방법 F — 모델 기반 상한: 이동 블러·밝기 한계

피드백이 아니라 **노출 상한을 물리 모델로 계산**하는 보강책.

- 별추적기 문헌([Sensors 2014, PMC4003974](https://pmc.ncbi.nlm.nih.gov/articles/PMC4003974/)):
  별상 트레일 길이 ∝ 각속도×노출. 트레일이 PSF ~1개를 넘으면 노출을 늘려도
  검출 한계등급이 거의 늘지 않는다(1°/s에서 최적 ~31 ms, 2°/s에서 ~18 ms).
- PiFinder에는 IMU 각속도가 있으므로 `max_exp_motion ≈ k / ω`로 동적 상한을
  둘 수 있다 — 수동(手動) 망원경에서 "이동 중 긴 노출 낭비"(P5, P7)를
  구조적으로 차단. 정지 시에는 상한이 풀려 어두운 하늘에서 길게 노출.

### 방법 G — 하이브리드: 검출 서보(내부 루프) + 솔브 품질 게이트(외부 루프)

현실적 결합안. 방법 A를 주 루프로 하되:

- **내부 루프(빠름)**: 검출 별 수 서보 + 밝은 하늘 가드 + 이동 블러 상한(F).
  솔브 없이 프레임/검출 주기로 동작.
- **외부 루프(느림)**: 솔브 결과(`Matches`, 성공률)로 목표 검출 별 수를
  천천히 보정 — "검출 25개인데 솔브가 계속 실패"면 목표를 올리는 식.
  기존 매치 수 컨트롤러의 지혜(비대칭, 데드밴드)를 이 층으로 이동.
- zero-match 복구 사다리는 "검출 0개 + 가드 미발동"일 때만 최후 수단으로
  축소 — P1의 오발동(초점/구름/솔버 실패)에서 사다리가 도는 일이 없어진다.

## 4. 게인 정책 조사

| 전략 | 출처 | 요지 |
| --- | --- | --- |
| **고정 고게인 + 노출만 서보** | cedar | 야간엔 최대 아날로그 게인 고정(IMX296 15×). 읽기 노이즈 무릎 이후 + 8-bit 출력에서는 DR 손실 무의미. 제어 변수 1개 → 루프 단순·검출 임계 안정 |
| 게인·노출 통합 사다리 | allsky | `log2(gain×exp)` 단일 레벨로 주야 전 범위. 주간까지 한 루프로 다뤄야 할 때 유효 |
| 게인 우선 → 노출 후순위 | 문헌 종합 | 이동 블러 제약이 있는 장비는 "게인을 읽기노이즈 무릎까지 먼저, 노출은 블러/밝기 한계까지만" |

PiFinder 함의: 현행 프로파일 게인(imx296 15×)이 이미 cedar의 야간 최적값과
일치한다. **게인을 피드백 루프에 넣을 필요는 낮고**, 밝은 하늘 가드 발동 시
게인을 한 단계 내리는 정도의 이산 스케줄링이면 충분해 보인다. (주간 정렬은
현행대로 네이티브 AE 위임.)

## 5. 비교 요약

| 방법 | 신호 | 솔브 의존 | P1 원인구분 | P2 속도 | P3 밀도독립 | P4 밝기가드 | 구현 비용 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 현행 (매치 수) | Matches | 있음 | ✗ | 느림 | ✗ | ✗ | — |
| A 검출 별 수 서보 | 검출 센트로이드 수 | **없음**(검출만) | ◎ | 빠름 | ◎ | ◎(가드 포함) | 중 (검출 수 배선 + 컨트롤러 교체) |
| B 이미지 통계 | 평균/퍼센타일 | 없음 | △ | 매우 빠름 | ◎ | ◎ | 소 (배경 컨트롤러 확장) |
| C 별 SNR | 별 플럭스/노이즈 | 없음 | △ | 빠름 | △ | △ | 중 |
| D 픽셀 임계 | 임계 초과 픽셀 수 | 없음 | ✗ | 매우 빠름 | △ | △ | 소 |
| E 네이티브 AEC | 평균 휘도 | 없음 | ✗ | 중 | ✗ | ✗ | 0 (부적합) |
| F 모션 모델 상한 | IMU 각속도 | 없음 | (보강책) | — | — | — | 소 |
| G 하이브리드 A+F+솔브 게이트 | 검출 수 + Matches | 외부 루프만 | ◎ | 빠름 | ◎ | ◎ | 중~대 |

## 6. 권고 (협의용 초안)

1. **주 신호를 매치 수 → 검출 별 수로 교체(방법 A)** 가 핵심이다. 같은
   솔버 스택의 cedar-server가 수치까지 검증해 둔 설계(목표 20개, EMA α=0.5,
   데드밴드 0.8–1.6, ±3스톱 클램프, 평균>240 가드, <4개 폴백)를 그대로
   출발점으로 쓸 수 있다. 검출 수는 `solver.py`가 이미 계산하고 있어
   `SolveDiagnostics`에 `Centroids` 필드 하나를 배선하면 된다(최소 변경).
   더 나아가면 솔브와 분리해 검출만 빠른 주기로 돌릴 수 있다.
2. **이동 블러 동적 상한(방법 F)** 을 병행 — IMU 각속도로 노출 상한을
   계산해, 수동 이동 중 노출이 길어지는 낭비와 피드백 오염을 차단.
   [mf_solve_motion_gate_review_ko.md](mf_solve_motion_gate_review_ko.md)의
   이동 프레임 게이트와 같은 재료(IMU delta)를 쓰므로 함께 설계.
3. zero-match 복구 사다리는 **"검출 0개"일 때만**으로 축소(ADR 0010의
   책임 범위를 신호 교체로 비로소 강제 가능).
4. 게인은 피드백에 넣지 않고 현행 프로파일 고정 유지(§4). 밝은 하늘 가드
   발동 시 1단계 하향만 검토.
5. 기존 매치 수 컨트롤러는 외부 품질 게이트(방법 G의 느린 루프)로 남길지,
   제거할지는 구현 단계에서 결정.

## 7. 참고 자료

- cedar-server [calibrator.rs](https://github.com/smroid/cedar-server/blob/main/server/src/calibrator.rs) ·
  [detect_engine.rs](https://github.com/smroid/cedar-server/blob/main/server/src/detect_engine.rs) ·
  [cedar-camera rpi_camera.rs](https://github.com/smroid/cedar-camera/blob/main/src/rpi_camera.rs) ·
  [cedar-detect](https://github.com/smroid/cedar-detect)
- PHD2 [myframe.cpp](https://github.com/OpenPHDGuiding/phd2/blob/master/src/myframe.cpp) ·
  [매뉴얼](https://openphdguiding.org/man-dev/Advanced_settings.htm)
- allsky [mode_mean.cpp](https://github.com/AllskyTeam/allsky/blob/master/src/mode_mean.cpp) ·
  [플리커 이슈 #228](https://github.com/thomasjacquin/allsky/issues/228)
- 별추적기 노출 최적화 [Sensors 2014 (PMC4003974)](https://pmc.ncbi.nlm.nih.gov/articles/PMC4003974/) ·
  [SPARCS 동적 노출 제어](https://arxiv.org/pdf/2507.03102)
- picamera2 아스트로 논의 [#592](https://github.com/raspberrypi/picamera2/discussions/592) ·
  [#175](https://github.com/raspberrypi/picamera2/discussions/175)
- [SkySolve](https://github.com/githubdoe/skysolve) (수동 노출 대조군) ·
  [FRAMOS IMX296 스펙](https://framos.com/products/sensors/area-sensors/imx296lqr-c-22545/)
