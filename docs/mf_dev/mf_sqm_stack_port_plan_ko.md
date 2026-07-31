# upstream SQM 스택(#532/#542/#543/#544) 이식 분석과 계획

작성일: 2026-07-30
상태: **소프트웨어 이식 완료 (2026-07-30, Phase 0~4)** — 남은 완료 조건은
§4.1 야간 재검증(웜맵 재생성 + σ/게이트 확인)과 SQM 위저드 1회 실행.
커밋: Phase 1 `e22559cf`, Phase 2 `c9180555`, Phase 3a `78f6cd78`,
3b `d004e27b`, 3c `b1a5d8f5`, 3d `c23b1cf4`, 3e `4e584f8f`.
계획 대비 실적 차이: rev4 rename hunk 제외(#539 보류 유지), radiometric
1 Hz 발행 로그는 MF 정책에 따라 DEBUG로 강등, upstream 테스트 8개 파일
전량 수용(test_solver_cedar_client 제외 — 기존 MF 이식본 유지).

대상 upstream 커밋 (적용 순서 기준):

| 커밋 | PR | 내용 |
| --- | --- | --- |
| `b5b16883` | #532 | 자가보정 SQM 전면 개편 (radiometer 우선, raw-green 측광, B−V/Gaia 색보정, wing/cloud/black-level 추정기) |
| `b36cb8c6` | #542 | #532의 cedar-detect 핸드오프 변경 revert |
| `5ef6a1b2` | #543 | #532가 끊은 카메라 테스트모드 토글 복원 |
| `69fe28c2` | #544 | 스케일 인지 측광 반경, tracked black level 우선, 위저드 캡처 수정, full-sensor 스윕 아카이브 |

## 1. 업스트림 아키텍처 변화 요약

SQM이 **"솔브 의존 → 솔브 독립"** 으로 뒤집힌다:

- **Radiometric 경로 (신규, 주력)**: 카메라 프로세스가 매 프레임 raw 크롭의
  중앙 80% 희소 그리드 중앙값을 `sqm_radiometer_sample`로 게시 → 솔버 루프가
  `update_radiometric_sqm()`으로 1초 주기 SQM 발행. **솔브 없이 동작**하고
  구름/솔브 실패에도 값이 유지된다. `SQMState.source="Radiometer"`.
- **Stellar 경로 (기존, 진단으로 강등)**: 기존 별 측광은 10초 주기
  `update_sqm(..., publish=False)` 진단이 되어 mzero/구름 판별/광학 감쇠
  후보만 공급. 처리 8-bit 이미지 대신 **raw green** 측광(`cam_raw()` 직접
  사용, `image_processed` 인자 삭제).
- 지원 모듈 6종 신규: `black_level.py`(롤링 절편 페데스탈 추적),
  `radiometer.py`, `wings.py`(aperture wing 보정), `clouds.py`,
  `color_index.py`(HIP B−V), `gaia_ref.py`(Gaia G, 신규 데이터
  `astro_data/hip_gaia_g.npz` 829KB). **전부 PiFinder 내부 import 없는 순수
  모듈** (color/gaia는 utils만).
- `sqm_details`가 **replace → merge**(`{**prev, **new}`)로 바뀜 — 두 생산자가
  같은 dict에 쓰기 때문. 소비자 입장에서 가장 큰 의미 변화.
- `*_processed` 카메라 프로파일 4종 삭제, `get_camera_profile()`이 사본
  (`dataclasses.replace`) 반환으로 변경.
- `ui/sqm_correction.py` 삭제 (이미 메뉴에서 고아였음; SWEEP으로 대체).

## 2. MF와의 상호작용 — 좋은 소식부터

**radiometric SQM은 우리 LP 갭을 정확히 메운다.** 현재 MF 코드는 SEP 폴백
솔브에서 `matched_centroids`를 의도적으로 pop해서(좌표공간 혼합 방지 펜스,
solver.py:673-674) SQM이 cedar 솔브에서만 갱신된다. 목표 조건(광해 하늘)에서는
SEP가 솔브를 전량 수행하므로 **SQM이 사실상 죽어 있다**. 업스트림 radiometric
경로는 솔브 자체가 필요 없으므로 이 갭이 구조적으로 해소된다. stellar 진단이
cedar 솔브에서만 도는 것은 그대로지만(펜스 유지), 주 SQM 값은 항상 나온다.

**cedar 핸드오프는 안전.** #532의 cedar 변경 4건(reopen_shmem 등)은 #542가
전부 revert — pre-#532와 바이트 동일임을 확인. 따라서 **그 hunk들은 아예
적용하지 않는다**. 우리 `PFCedarDetectClient`(hybrid + RemoveIPC 포트
d1875e04, solver.py:276-294/336-351/366-371)는 건드릴 필요 없음.

**#543도 사실상 무관.** 우리 트리는 #532의 `debug→test_mode_on` 개명을 받은
적이 없어 테스트모드가 살아 있다. camera_interface를 이식할 때 **커밋별이
아니라 post-#544 최종 상태 기준으로 이식**하면 #543은 자동 포함되고 깨진
중간 상태를 경유하지 않는다.

## 3. 파일별 충돌 지도

| 파일 | MF 변경(머지베이스 이후) | upstream 변경 | 병합 난이도 |
| --- | --- | --- | --- |
| `sqm/` 6개 신규 모듈 + 테스트 + `hip_gaia_g.npz` | 없음 | 신규 | **없음** — 그대로 수용 |
| `sqm/sqm.py` | 로그 레벨 1줄 (`c0ca4dcc`) | 전면 개편 | 낮음 — upstream 수용 + 로그 강등 재적용 |
| `sqm/camera_profiles.py` | `mono` 필드 +10줄 (`9e18749e`) | 필드 대거 추가, `*_processed` 삭제, **bias_offset 값 변경** | 중간 — mono 재적용, §4.1 리스크 |
| `sqm/noise_floor.py` | 없음 | 보정 모델 중심 재작성 | **없음** |
| `ui/sqm.py`, `ui/sqm_sweep.py` | 없음 | 라벨/보정파일명/스윕 강화 | **없음** |
| `ui/sqm_calibration.py` | timez 치환만 | 전면 재작성 (+#544 캡처 수정) | 낮음 — upstream 수용 후 `datetime.now()`→`timez` 재적용 (ADR 0018) |
| `ui/sqm_correction.py` | timez 치환만 | **삭제** | 없음 — 삭제 수용, ko `.po` 잔재 정리 |
| `state.py` | solver_raw/sep_overlay/livecam/datetime +104 | solve_image_rotation, sqm_radiometer_sample +32 | 낮음 — 서로 다른 필드 추가 |
| `telemetry.py` / `integrator.py` / `types/positioning.py` / `ui/base.py` | 소규모 (Centroids 필드, IMU 가드 등) | 소규모 (radio 이벤트, matched_catID, SQM 게이트) | 낮음 — 전부 직교 |
| `auto_exposure.py` | m0021 ladder 개편 | `ExposureSNRController.from_camera_profile` **삭제** + 포맷 churn | 낮음 — 영역 다름, §4.3 확인 |
| `camera_pi.py` | solver_raw 게시, 스테이지 캡처, mono raw 저장 +141 | radiometer 샘플 게시, sensor_temp, full-sensor TIFF(속성 개명) +98 | **중간** — capture() 수동 병합 |
| `camera_interface.py` | AE 디스패치 게이트 확장, gain 정책, 스테이지 덤프, 0.01s 큐, auto_star +288 | solve_rotation 일원화+게시, actual_exposure_us, set_exp_transient, capture 재작업, 스윕 기록 +310 | **높음** — §4.2 |
| `solver.py` | hybrid 루프 +172, RemoveIPC 포트 | SQM 배선 전면 교체 (+500, cedar 부분 제외) | **높음** — §4.4 |
| `timez.py`, `ui/timeentry.py` | (timez는 MF/upstream 공통) | **ruff 포맷 churn만** | 적용 생략 |
| `python/result`, `result-lib` | — | **/nix/store 심링크 (사고 유입)** | **적용 금지** |

## 4. 리스크 (등급순)

### 4.1 ⚠️ 최상 — `bias_offset` 값 변경이 프로덕션 솔브 이미지를 바꾼다

`camera_pi.py:173`의 캡처 파이프라인(크롭→**bias 감산**→디지털 게인→8-bit
스트레치)이 `profile.bias_offset`을 직접 쓴다. upstream은 imx462/imx290을
50→**238**, imx296을 32→60으로 바꾼다(raw 12-bit 실측 페데스탈; imx462
표준 OB 240과 부합). 값 자체는 아마 더 정확하지만:

- 512 솔브 이미지의 배경/스트레치가 달라져 **cedar σ8, SEP σ4+게이트 튜닝,
  웜픽셀 맵(`sep_warm_pixels.npy`)이 전부 그 위에서 재검증 대상**이 된다.
- 검증된 순도 83~91%, 야간 솔브율 95%는 bias 50 기준 실측이다.

선택지:
1. **(권장) 프로파일 분리 없이 수용하되, 야간 재검증을 이식 완료 조건으로
   명시** — 웜맵 재생성(`python -m PiFinder.sep_warm_map`) + σ/게이트 스윕
   1회. 값이 옳다면 검출은 오히려 개선될 수 있다.
2. 파이프라인용 bias와 SQM용 bias를 분리(필드 추가) — 이식 diff가 커지고
   upstream과 영구 분기. 재검증 실패 시의 후퇴안으로만.

### 4.2 높음 — `camera_interface.py` 수동 병합

같은 `get_image_loop()` 안에서 양쪽이 크게 갈라졌다. 보존해야 할 MF 항목:

- AE 디스패치 게이트 `("CAM","CAM_FAILED","IMU")` + per-attempt 성공 판정
  (`solution.last_solve_success == solve_attempt_time`) — m0022 배선
- `_ae_controller_choice`/`auto_star` 분기, `set_exp` auto 모드 config 저장
- gain 런타임 전용 정책 (`set_gain:` config 미기록, `"profile"` 센티널)
- `command_queue.get(timeout=0.01)` — **테스트 없는 성능 항목, 조용히
  0.1로 되돌아가기 쉬움**
- 스테이지 덤프/`_publish_solver_raw`/LiveCam 게시 블록
- 테스트모드: 우리 `debug` 플래그 구조 유지 or upstream `test_mode_on`
  최종형(#543 포함) 채택 — 어느 쪽이든 **post-#544 상태 기준**
- upstream의 `solve_rotation` 일원화(`set_solve_image_rotation` 게시)는
  수용하되 stage-5 회전 규칙 변화가 없는지
  `test_sep_detect.py::test_stage5_rotation_matches_camera_interface_rules`로
  확인 (SEP 좌표계 tripwire)

### 4.3 중간 — noise_floor / SNR AE의 의미 변화

upstream은 `update_sqm`의 `set_noise_floor()` 쓰기를 삭제(어차피 키 불일치로
불발이었음)하고 `ExposureSNRController.from_camera_profile()`을 삭제한다.
결과적으로 `noise_floor()`는 기본값 10.0에 고정되고 SNR 컨트롤러(SQM 화면의
`ae_mode:snr`)는 그 값으로 돈다. 우리 주력 AE는 star-count(m0020)라 실사용
영향은 작지만, `camera_interface`가 `from_camera_profile`을 호출하고 있으면
이식 시 함께 제거해야 한다. SQM 화면 진입 시 AE 거동을 이식 후 한 번 확인.

### 4.4 높음 — `solver.py` SQM 배선 이식

cedar/SEP 하이브리드 루프 구조는 유지하고 SQM 관련만 교체한다:

- 신규 헬퍼 4종(`_extract_raw_photometry_image`, `_scale_solution_centroids`,
  `_derotate_centroids`, `_scaled_photometry_radii`)과
  `update_radiometric_sqm` 추가, `update_sqm` 교체
- 루프 배선: lazy calculator(+`_processed` 접미 제거), 추정기 4종 생성,
  매 이미지 `sqm_radiometer_sample()` 소비, 10초 stellar 게이트,
  `matched_catID` pop 위치(“`_build_successful_solve` 이후”) 준수
- **MF 펜스 유지**: SEP 폴백 솔브의 matched_* pop은 그대로 → stellar 진단은
  cedar 솔브 전용 (LP에서 wing/cloud 추정기가 미조건 상태로 남는 것은 수용;
  radiometric 값은 `optics_attenuation_correction` 없이 발행됨)
- `_build_successful_solve`에 `matched_catID` 추가 시 우리 `centroid_count`
  (SEP 카운트 게시) 파라미터와 공존 확인
- **RemoveIPC 포트(d1875e04)와 `PFCedarDetectClient` 전체는 불가침**

### 4.5 중간 — imx462 mono와 raw-green 추출

`radiometer.extract_photometry_image`는 `profile.format`이 `SRGGB*`면 Bayer
green 2사이트 평균(half-res)을 만든다. 우리 실측으로 imx462는 mono이므로
green 평균은 “동일 픽셀 2/4 서브샘플 평균”이 된다 — 오류는 아니고 upstream
imx462 보정값(rad zp 15.25, band 0.53 등)도 그 경로 기준 실측이므로 **초기엔
upstream 경로 그대로 사용**한다. mono full-res 경로 전환은 보정값 재적합이
필요하므로 후속 튜닝 항목으로만 남긴다(§7).

### 4.6 낮음 — 기타

- `sqm_details` merge 의미 변화: 우리 쪽 추가 소비자는 api_extensions의
  `/api/sqm`(그대로 dict 반환)뿐 — 영향 없음, 키가 늘어날 뿐.
- `ui/sqm.py`의 `sqm_altitude_corrected` 표시가 None(공란)이 됨 — upstream
  의도된 동작.
- 보정 파일명에서 `_processed` 접미 제거 — **기기에 기존 보정 파일 없음
  확인 완료**, 마이그레이션 불요. 이식 후 위저드 1회 실행 필요.
- upstream `ui/sqm_calibration.py`/신규 모듈의 `datetime.now()` 잔재 → ADR
  0018 위반이므로 이식 시 `timez` 치환 일괄 확인.
- ADR `0024-sqm-raw-green-photometry-redesign`(업스트림 0020 충돌 정리로 개명,
  2026-08-04), `0022-sqm-radiometer-first`는
  숫자 그대로 수용(숫자=upstream 규칙). `docs/ax/sqm*`, `docs/ax/camera*`
  변경은 우리 문서와 병합.
- 스테디스테이트 로깅 DEBUG 정책(`c0ca4dcc`) — upstream 신규 info 로그 중
  주기성 있는 것이 없는지 이식 후 로그 census 1회.

## 5. 단계별 이식 계획

각 단계는 독립 커밋 + 테스트 그린을 조건으로 다음 단계 진행.

- **Phase 0 — 기준선**: 관련 스위트 전부 그린 확인(§6 목록), 현재 main 태그.
- **Phase 1 — 순수 모듈**: `sqm/{black_level,radiometer,wings,clouds,
  color_index,gaia_ref}.py`(post-#544 상태) + `hip_gaia_g.npz` +
  `test_{black_level,black_level_lease,clouds,radiometer}.py` +
  `scripts/{evaluate_radiometer_archive,report_sqm_production_archive}.py`.
  기존 코드 무변경, 무위험. (`benchmark_sqm_pipeline.py`는 solver 내부 의존
  → Phase 4로.)
- **Phase 2 — sqm 패키지 코어**: `sqm.py`, `camera_profiles.py`(+mono 재적용),
  `noise_floor.py`, `save_sweep_metadata.py`, `__init__.py`, `ui/sqm.py`,
  `ui/sqm_sweep.py`, `ui/sqm_correction.py` 삭제, upstream ADR 2건, 테스트
  (`test_sqm` 확장판 + mono 테스트 병합, `test_sqm_calibration`,
  `test_sweep_frame_record`). **bias_offset 변경이 여기서 들어옴** — 이
  시점부터 §4.1 재검증 플래그 활성.
- **Phase 3 — 플럼빙**: `state/telemetry/integrator/types/ui-base`(직교 추가)
  → `camera_pi.py` → `camera_interface.py`(§4.2 체크리스트) →
  `solver.py`(§4.4) → `ui/sqm_calibration.py`(+timez) → `test_solver_sqm.py`.
  전부 post-#544 최종 상태 기준, cedar hunk와 nix 심링크 제외.
- **Phase 4 — 검증**: §6 스위트 전체 + `benchmark_sqm_pipeline.py` 이식 +
  실기 확인: (a) radiometric SQM이 솔브 없이 발행되는지, (b) 솔브 사이클
  ~0.3s 유지(§4.2 타임아웃 포함), (c) LP 하늘에서 SEP 솔브율 회귀 없는지 —
  필요 시 웜맵 재생성 + σ 스윕, (d) SQM 위저드 1회 실행, (e) 로그 census.

## 6. 회귀 방지 앵커 (불가침 목록)

이식 중 어떤 단계에서도 다음이 깨지면 중단·원복:

| 불변식 | 앵커 |
| --- | --- |
| cedar-512 경로가 매 시도 선행, SEP는 구조 무변경 | ADR m0023 §1 (리뷰 불변식) |
| SEP 폴백 솔브 좌표 무결성 | `test_sep_fullframe_solve.py` 2건 |
| 웜픽셀 마스크가 top-N 캡보다 선행 | `test_sep_detect.py::TestWarmPixelMap` |
| stage-5 회전 규칙 = PIL 고정 | `test_sep_detect.py::TestRotationConvention`, `test_stage5_rotation_matches_camera_interface_rules` |
| SEP 폴백 백오프 | `test_sep_shadow.py::TestFallbackBackoff` |
| RemoveIPC 복구 | `test_solver_cedar_client.py` 3건 |
| 솔브 성공 노출 홀드 + anchor trust | `test_auto_exposure_starcount.py` 7건 |
| fast-shutter 도달성 | `test_auto_exposure.py::TestZeroMatchRecovery` |
| imx462 mono | `test_sqm.py::test_mono_flags_ignore_driver_bayer_label`, `test_raw_live_stack.py` 3건 |
| gain 미기록 정책 | `test_api_camera_controls.py` |
| 스테이지 덤프 회전/tmpfs 정책 | `test_camera_stage_dump.py` |
| 큐 타임아웃 0.01s / 솔브 사이클 0.3s | **테스트 없음 — 수동 diff 확인 필수** |

## 7. 후속(이식 범위 외) 튜닝 항목

- bias 238 기준 웜맵/σ 재검증 결과 반영 (§4.1)
- mono full-res 측광 경로 + imx462 보정값 재적합 (radiometric/sqm_band_offset)
- SEP 솔브에서 stellar 진단 허용 여부 (frame 좌표 → 512 매핑 후 update_sqm)
  — wing/cloud 추정기를 LP 하늘에서도 조건화할 유일한 방법
- `ko` locale의 sqm_correction 잔재 문자열 정리
