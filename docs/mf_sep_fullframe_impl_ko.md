# SEP 풀프레임 검출 경로 — 구현 문서

> 상태: **구현 완료 — 현장 평가 진행 중** (2026-07-28)
> 배경/근거: [mf_auto_exposure_field_review_20260726_ko.md](mf_auto_exposure_field_review_20260726_ko.md)
> (병목 = 광해 하늘에서의 검출 감도, §4 대안 B+C 채택)
> 관련: [docs/ax/camera.md](ax/camera.md) §3b/§6b,
> [ADR 0022](adr/0022-solve-success-holds-star-count-exposure.md)
>
> 목적: 검출 별 수가 부족해 솔브가 불가능하던 서울 광해 하늘에서, **12-bit
> 비크롭 풀프레임 + 배경 제거(SEP)** 검출 경로를 프로덕션(cedar-detect)과
> 나란히 돌려 한 번의 관측으로 A/B 판정을 내릴 수 있게 한다. 프로덕션
> 경로는 무수정이며, 실험 코드는 예외를 전파하지 않는다.

## 1. 설계 요약

| 선택 | 이유 |
| --- | --- |
| **비크롭 풀프레임** (1920×1080) | 크롭(980²) 대비 시야 2.16배 = 검출 후보 별 ~2배. 광해 하늘에서 부족한 것이 바로 별 수 |
| **12-bit 도메인 검출** | 현행 8-bit 스트레치(÷15.9)는 배경 위 +30 ADU 별을 ~2계조로 압축 — 검출기 도달 전에 정보 소실 |
| **2×2 Bayer 비닝** | RGGB 채널 감도 차가 모자이크에서 체커보드 패턴 노이즈가 됨(비닝 없인 합성 별 12개 중 2개만 회수). SNR도 2배 |
| **SEP 메시 배경 제거** | 광해 그라디언트·구름 글로우가 전역 임계를 오염시키는 것이 실패의 핵심. `sep.Background`(32² 메시)+국소 RMS 임계가 이를 직접 해소 |
| **섀도 우선, 폴백은 옵트인** | 프로덕션 솔브에 영향 0인 상태로 매 시도 A/B 데이터 축적. 폴백은 cedar 실패 시에만 실솔브 시도 |

## 2. 데이터 흐름

```
카메라 프로세스 (camera_pi.capture)
  RAW 캡처(uint16 풀프레임)
    ├─ (실험 on) shared_state.set_solver_raw({frame, ts, exp, gain})  ← 비크롭
    └─ 크롭 → …기존 파이프라인… → camera_image(512²)      ← 프로덕션 무변경

솔버 프로세스 (solver.py, 시도마다)
  cedar-detect(512², σ8) → tetra3 솔브        ← 프로덕션 경로 그대로
  sep_shadow.detect(solver_raw)               ← SEP 병행 실행
    ├─ 섀도 CSV 1행 기록 (cedar vs SEP 비교)
    └─ (폴백 on & cedar 실패 & SEP≥8 & 정렬 비진행)
         sep_shadow.solve() → 성공 시 그 솔루션이 정규 체인에 공급
```

## 3. 모듈별 구현

### 3.1 `sep_detect.py` — 검출

`detect_stars(raw_frame, sigma=3.5, minarea=3, max_stars=48, edge_margin_px=48,
saturation_level=None)`:

1. `bin2x2` 평균 비닝(float32) → `sep.Background(bw=32,bh=32)` 추정·차감
2. `sep.extract(thresh=σ, err=bkg.rms(), filter_kernel=3×3 가우시안)` —
   국소 RMS 기준 임계 + 매치드 필터
3. **품질 필터 3종 (1차 야간 가동에서 도출, §6.1)**:
   - 가장자리 마진: 프레임 경계 48px 이내 제외 (비네팅 아티팩트)
   - 양성 플럭스만 통과 (0·음수 = 배경 모델 잔재)
   - 포화 가드: 내부(중앙 1/2) 중앙값 ≥ 0.98×풀스케일이면 **정직한 0** 반환
4. 플럭스 내림차순 상위 48개, **풀프레임 픽셀 좌표**(비닝 좌표×2+0.5)로 반환

`sep`은 선택 의존성 — 미설치 시 None 반환으로 전 경로 무해화.

### 3.2 `solver_frame_map.py` — 좌표 정합 (추적 무결성의 핵심)

프로덕션 솔브 프레임 = 크롭→512 리사이즈→stage-5 회전. SEP 솔브는 **같은
회전을 적용한 "회전된 풀프레임"**에서 수행해 RA/Dec/Roll·정렬 의미를
동일하게 유지한다.

- `rotate_centroids(cents, hw, angle)`: 90° 배수는 정수 정확 매핑(캔버스
  치수 교환), 임의각은 중심 회전(PIL expand=False 동치). **부호 규약은
  PIL `Image.rotate` 실동작에 테스트로 고정** — stage 5가 쓰는 바로 그 함수.
- `map_target_pixel_to_frame(tp, hw, crop_w)`: 정렬점(target_pixel, 회전된
  512 공간에 영속)을 회전된 풀프레임으로 변환. **크롭이 중심 대칭이고
  리사이즈가 등방이므로 두 회전이 상쇄되어 "중심 기준 ×(crop_w/512)
  스케일"로 환원**된다(모듈 docstring에 증명). config·기존 정렬 무변경.
- `fov_estimate_deg(width, crop_w)`: 플레이트 스케일 12°/980px에서 산출
  (풀프레임 가로 23.5°; 패턴 DB `default_database.npz`는 max_fov 30°로 수용).

**검증** (`tests/test_sep_fullframe_solve.py`): tetra3 자체 별표를 두 경로로
각각 투영·솔브 → **Roll 편차 0.000°, 정렬점 편차 20″**(평면 피팅 잔차 수준),
매치 46 vs 27(풀프레임 이득). 추적·정렬·푸시투 체인이 건드려지지 않음을
증명하는 테스트이며 unit 스위트에서 상시 회귀 검증된다.

### 3.3 `sep_shadow.py` — 러너

- `SepShadowRunner.create_if_enabled(cfg, camera_type)`: config·카메라
  프로파일(크롭 기하·비트깊이)에서 구성. 카메라 타입 공유 전이면 None →
  솔버 루프가 재시도.
- `detect()`: `solver_raw`(15초 이내 신선도) → `detect_stars`. 예외는 로그
  후 None — **실험이 프로덕션 솔버를 죽일 수 없다**(전 진입점 동일 원칙).
- `solve()`: 회전·정렬점·FOV 매핑 후 `t3.solve_from_centroids`.
- `log_attempt()`: 시도당 CSV 1행. 스키마: `timestamp, exposure_us, gain,
  cedar_centroids, matches, solved, sep_centroids, sep_top_flux, sep_bkg,
  sep_rms, sep_ms, fallback_used, fallback_rmse`.

**폴백 규칙** (solver.py 배선):

- 조건: cedar 경로 솔브 실패 ∧ SEP ≥ 8개 ∧ **정렬 절차 비진행**(정렬의
  y/x_target이 풀프레임 공간으로 나오면 안 되므로 배제).
- 성공 시: `matched_centroids`/`matched_stars` 제거(풀프레임 좌표가 SQM
  측광(512 프레임)에 섞이는 것 차단) 후 솔루션을 정규 체인에 공급.
  `Centroids`는 SEP 검출 수로 게시 — 자동 노출의 솔브 홀드(ADR 0022)가
  "실제로 솔브된 노출"에 앵커되도록.

### 3.4 카메라 배선 (`camera_interface.py`, `camera_pi.py`)

- `solver_shadow_detect ∨ solver_sep_fallback`일 때만 `set_solver_raw`
  발행(프로파일 rot90 적용, 크롭 미적용). 끄면 발행 비용 0.
- 스테이지 덤프에 `00_raw_full`(비크롭) 포함 — 오프라인 벤치가 풀프레임
  경로와 같은 입력을 보도록.
- **자동 코퍼스 수집**: 솔브 10연속 실패 시 3분 쿨다운으로 스테이지 덤프
  자동 저장(`camera_auto_dump`). 실패하는 밤이 곧 벤치 자료가 된다.

### 3.5 자동 노출 보완 — 앵커 트러스트 (같은 기간, dd010295)

현장 체감 문제 "솔브는 되는데 노출이 계속 출렁임"의 해법. 솔브 성공이
**신뢰 창(`anchor_trust_s` 90 s, 재솔브마다 갱신)**을 열고, 창 안에서는
실패 시도가 탐색 대신 솔브된 앵커를 유지한다:

- 검출 0 (구름 통과): `trusted_zero_limit`(8연속)까지 앵커 유지, 초과 시 사다리
- 검출 1–3: 유지(저검출 탈출 비발동) / 목표 미달 4개+: 상향 없이 유지
- 예외 우선순위 유지: 밝은 프레임은 즉시 하향(포화 방어), 별 과잉은 하향

### 3.6 config·저장 정책

| 키 | 기본 | 의미 |
| --- | --- | --- |
| `solver_shadow_detect` | false | 섀도 A/B 로깅 (+solver_raw 발행) |
| `solver_sep_fallback` | false | SEP 폴백 솔브 |
| `solver_sep_sigma` | 3.5 | SEP 추출 임계(σ) |
| `camera_auto_dump` | false | 실패 스트릭 시 자동 스테이지 덤프 |

(모두 재시작 필요. 테스트 기기엔 전부 on.)

**저장 정책 (2026-07-28 사용자 결정 — SD 쓰기는 명시적 디버깅 시에만)**:
섀도 CSV·앱 로그·스테이지 덤프 모두 **tmpfs**. 덤프는 최근 30세트(~270 MB)
로테이션(`prune_dumps`)으로 `/dev/shm` 고갈 방지. 전원 차단 시 소실 —
남길 세션은 웹 Logs "Save to SD"(로그+CSV 포함) 또는
`GET /api/camera/stages[/<dir>/<file>]` 다운로드로 보존.

## 4. 테스트

- `test_sep_detect.py` (16): 검출·좌표·비닝, PIL 고정 회전 규약,
  target_pixel 매핑, 가장자리/포화 필터
- `test_sep_fullframe_solve.py` (1): 이중 솔브 동등성 (§3.2)
- `test_auto_exposure_starcount.py` (42): 앵커 트러스트 포함 컨트롤러 전체
- `test_camera_stage_dump.py` (7): 무손실 저장·로테이션

## 5. 커밋 이력 (구현 순)

| 커밋 | 내용 |
| --- | --- |
| f59659ec | 실험 본체: sep_detect / solver_frame_map / sep_shadow / 배선 / config |
| b1b1146c | 품질 필터 3종 + 덤프에 raw_full (1차 야간 가동 결과) |
| dd010295 | 앵커 트러스트 (노출 출렁임 해소) |
| 9cc99b31 | 섀도 CSV → tmpfs, Save-to-SD 포함 |
| 38bf0e13 | 스테이지 덤프 → tmpfs + 30세트 로테이션 |

## 6. 야간 검증 기록 (2026-07-27 밤 ~ 07-28 새벽, 서울, 이동 구름)

### 6.1 1차 가동 — 오검출 발견과 필터 도출

σ3.5 원시 추출은 비크롭 프레임에서 시도당 10–45개를 반환했으나 **폴백
솔브 0/218** — 풀프레임 덤프 분석 결과 검출 전원이 크롭 밖
**비네팅 가장자리**(플럭스 덩어리/0/음수)였고, 두꺼운 구름은 100 ms에서도
센서를 포화시켰다(배경 4095). → §3.1의 필터 3종 도입. 필터 후 포화
프레임은 0을 반환하고, tetra3는 밤새 가짜 센트로이드를 전량 기각했다
(오솔브 0 — 최종 방어선 검증).

### 6.2 2차 — 구름 틈 실전 성적

별이 보인 2분 구간(95시도): **솔브 34회(35%)**, 그중 cedar 경로 30 /
**SEP 구제 4**. 솔브된 노출 62–1000 ms(대부분 서보 수렴값) — 구름 두께
변화를 컨트롤러가 추적. 세션 누적 SEP 폴백 솔브 39회. 같은 순간 검출 수
cedar 5.6 vs SEP 39.6(평균; SEP엔 오검출 포함, tetra3 매치 10–12로 실별
충분). 체감 개선 확인 — 남은 불만 "노출 출렁임"은 §3.5로 대응.

### 6.3 판정 현황

- **확정**: 좌표 정합 무결(§3.2), 폴백의 실효(구제 솔브 실증), 방어선
  (오솔브 0), 저장 정책.
- **미확정**: σ 튜닝 — 실별이 σ3.5~5 사이에 걸려 있어(크롭 실측) 상향
  득실은 별이 보이는 하늘의 코퍼스로만 판정 가능. cedar 대체/폴백 유지/
  기각의 최종 방안도 CSV·코퍼스 축적 후 결정.

## 7. 다음 단계

1. 맑은(부분 맑음) 밤 세션 1–2회 — CSV·코퍼스 자동 축적 (조작 불필요)
2. 오프라인 벤치: 코퍼스에 σ 스윕·cedar 대비 순도/센트로이드 정밀도 비교
3. 최종 방안 결정: 폴백 상시화 여부, σ 기본값, cedar와의 역할 분담
   → 결정 시 ADR로 승격, 본 문서는 구현 기록으로 유지
