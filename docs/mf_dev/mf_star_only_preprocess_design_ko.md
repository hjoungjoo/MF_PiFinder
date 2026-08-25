# MF 별빛 보존 RAW 전처리 및 중앙/전체 프레임 솔빙 설계

최종 업데이트: 2026-08-26

## 1. 결정과 목표

6mm 광각에서 타일별 솔빙은 달·건물광을 피해 여러 타일을 실제로 풀 수 있었지만,
주변 왜곡에 따른 위치/Roll 편차와 합의군 선택 문제 때문에 안정적인 좌표 발행 경로로
사용하지 않는다. 신규 광각 경로는 타일 솔빙을 중단하고 기존의 다음 두 단계만 유지한다.

1. 기존 중앙 crop 솔빙
2. 기존 전체 프레임 솔빙

두 솔버에 넣기 전에 원본 16-bit RAW에서 달, 건물광, 포화 halo, 구름의 큰 구조를
제거하고 별과 유사한 점광원 신호만 남긴다. 핵심 목표는 단순한 밝기 임계값이 아니다.
구름 사이로 잠시 보이는 별과 흐린 별도 여러 프레임의 약한 증거를 누적해 보존해야
한다.

타일 코드는 즉시 삭제하지 않고 feature flag를 끈 채 rollback 자료로 남긴다. 신규
코드는 기존 원본을 최소 변경하기 위해 `mf_` 접두 파일에 격리한다.

## 2. 2026-08-26 기준 데이터

신규 고정 corpus:

`PiFinder_data/captures/mf_replay/20260826_0016_star_only_preprocess_6mm`

- `imx462_color + 6mm`, 200ms, gain 29.512
- 중앙 달, 하단 건물광, 밝은 halo와 일부 구름, 상단 별
- lossless 16-bit RAW 20장, 모두 고유 frame ID이고 시간 순서가 단조 증가
- 전체 포화율 5.54–5.62%, median 12-bit ADU 2121–2126
- 기존 SEP 27–40개, clear-window gate 후 9–14개
- 픽셀 시간 표준편차 median 127 ADU, p90 186, p99 228
- 촬영 전 mean-10 스택도 별도 보존
- 재현 스크립트 `analyze_star_only.py`, 네 개의 star-only TIFF와
  `analysis.json`을 같은 디렉터리에 보존

함께 사용하는 회귀 corpus:

- `20260825_2248_cloud_6mm`: 구름 구조 오검출
- `20260825_2305_cloud_6mm`: 어두운 clear window의 반복 점광원
- `20260825_2324_cloud_gate_live_6mm`: 구름 통과와 정상 다중 타일 해
- `20260825_2334_moon_building_6mm`: 중앙 달·하단 건물광
- `20260825_2352_manual_exposure_sweep_6mm`: 50–400ms 노출 비교
- `20260826_0006_manual_mean10_stack_6mm`: mean-10에서 별 SNR 개선
- 기존 맑은 별 필드 및 16mm 정상 solve corpus

## 3. 전체 블록 다이어그램

```mermaid
flowchart LR
    A[16-bit RAW 연속 프레임] --> B[입력/광학 fingerprint 검사]
    B --> C[전역 광원·포화 구조 분석]
    B --> D[10x10 국소 robust background/RMS]
    C --> E[hard mask: 포화 core·고정 건물광]
    C --> F[soft weight: halo·구름·gradient]
    D --> G[배경 차감·local whitening]
    E --> G
    F --> G
    G --> H[PSF matched response·형상 gate]
    H --> I[시간축 반복성·역분산 누적]
    I --> J[원래 좌표의 star-only 16-bit frame]
    J --> K[기존 중앙 crop Cedar/Tetra]
    K -->|실패| L[기존 전체 프레임 Cedar/Tetra]
    K -->|성공| M[기존 Integrator]
    L -->|성공| M
    L -->|실패| N[FailedSolve / 마지막 정상 추정 유지]
```

## 4. 처리 원칙

### 4.1 좌표 불변

왜곡 보정을 위한 resample, 축소, 확대를 하지 않는다. 배경값과 신뢰도만 바꾸며 별의
centroid 위치는 원본 센서 좌표를 유지한다. 출력은 원본과 같은 높이·너비의 16-bit
star-only frame이다.

### 4.2 전역 hard mask는 최소화

전체 프레임 통계와 큰 연결 성분으로 다음만 완전히 제외한다.

- 센서 full scale의 98% 이상인 포화 core
- 포화 core에 연결된 큰 halo의 내부 안전 반경
- 여러 프레임에서 같은 위치와 넓이로 반복되는 고정 건물광/기구 구조
- 센서 가장자리의 기존 금지 영역

구름은 hard mask로 지우지 않는다. 구름 셀에도 별이 보일 수 있으므로 배경과 RMS에
따른 soft weight만 낮춘다. 달/건물광 mask도 과도하게 팽창시키지 않고, mask 밖의
점광원은 계속 평가한다.

### 4.3 10×10 국소 처리와 다중 scale

10×10 full-resolution cell마다 sigma-clipped median과 MAD 기반 RMS를 구한다. 셀
경계가 별 centroid를 이동시키지 않도록 background/RMS map은 bilinear interpolation
한다. 10×10만 사용하면 큰 halo 기울기를 놓치므로 32×32 및 96×96 robust background를
함께 계산한다.

```text
B(x,y) = robust combination(B10, B32, B96)
Z(x,y) = max(0, RAW(x,y) - B(x,y)) / max(RMS10(x,y), noise_floor)
```

별이 포함된 cell이 스스로 background를 올리는 것을 막기 위해 상위 outlier를 반복
제외한 median을 사용한다. 국소 밝기가 높다는 이유만으로 cell 전체를 버리지 않는다.

IMX462 컬러 RAW는 네 Bayer 위상의 median이 약 1,800–2,473 ADU로 크게 다르다. 네
위상을 섞어서 배경을 계산하면 이 차이가 고주파 점광원처럼 남는다. 따라서 10/32/96
배경과 MAD RMS, DoG 응답을 각각의 2×2 CFA 위상에서 독립 계산한 뒤 원래 픽셀 위치에
다시 합친다. demosaic/resample을 하지 않으므로 solver centroid 좌표는 바뀌지 않는다.

### 4.4 PSF 증거

whitened residual에 3×3/5×5 Gaussian matched filter를 적용한다. 다음 특성은 별
신뢰도를 올린다.

- 양의 중심 peak와 주변으로 감소하는 profile
- 제한된 semi-major 크기와 원형도
- 작은 connected area
- 인접 프레임에서 비슷한 centroid

넓은 구름 결, 달 halo, 건물 모서리는 gradient/면적/비대칭 때문에 낮은 가중치를
받는다. 단일 프레임의 강한 peak 하나만으로 최종 star-only 출력에 full strength를
주지 않는다.

### 4.5 희미한 구름 사이 별 보존

단순 temporal median은 절반 미만의 프레임에서 보인 별을 삭제하므로 사용하지 않는다.
각 프레임의 local-whitened PSF evidence를 누적한다.

```text
E = sum(clamp(Z_psf, 0, z_cap) * inverse_variance_weight)
P = number of frames with Z_psf >= weak_threshold
star_strength = E * persistence_weight(P, N)
```

- `P>=2`인 약한 반복 신호는 보존한다.
- 한 프레임에서만 나타난 구름 glint/cosmic/hot 신호는 강도를 제한한다.
- 흐린 프레임도 weight를 낮출 뿐 0으로 만들지 않는다.
- warm-pixel map과 고정 위치 반복성은 별 지속성과 별도로 먼저 제거한다.

시간 반복만으로는 고정 hot pixel도 별로 강화되므로 공간 PSF gate를 함께 적용한다.
약한 임계값을 넘은 연결 성분 중 3–30픽셀인 compact component만 인정하고, 인정된
core 둘레 3픽셀만 원래 residual을 복원한다. 반복 compact component는 100%, 한
프레임에서만 나타난 compact component는 20% 강도로 보존한다. 단일 픽셀과 큰 구름
결은 제거된다. 거의 모든 배경이 0이 되어 SEP의 RMS가 0이 되는 수치적 특이점을
막기 위해 출력에는 64 ADU pedestal와 ±3 ADU의 결정론적 저레벨 dither를 넣는다.
이 dither만 있는 제어 영상에서는 SEP 후보가 검출되지 않는다.

초기 window는 5프레임으로 시작하고 10프레임은 오프라인 비교 대상으로 둔다. 200ms
기준 5프레임은 약 1초의 광자 정보를 제공하면서 이동 시작 시 stale 좌표 위험을
제한한다.

## 5. 상태기계와 순서도

```mermaid
flowchart TD
    A[새 RAW] --> B{렌즈 < 10mm 및 기능 on?}
    B -->|아니오| C[기존 중앙/전체 경로 그대로]
    B -->|예| D{IMU stationary?}
    D -->|아니오| E[temporal window reset]
    E --> F[단일 프레임 전역+국소 전처리]
    D -->|예| G[fingerprint 일치 확인]
    G -->|불일치| E
    G -->|일치| H[window에 RAW/evidence 추가]
    H --> I{최소 2프레임?}
    I -->|아니오| J[FailedSolve, 누적 대기]
    I -->|예| K[star-only frame 합성]
    F --> K
    K --> L[중앙 solve]
    L -->|성공| M[기존 좌표 발행]
    L -->|실패| N[전체 프레임 solve]
    N -->|성공| M
    N -->|실패| O[FailedSolve]
```

window fingerprint에는 camera type, lens/manual focal, RAW shape, exposure,
gain, rotation, active calibration ID가 포함된다. 하나라도 바뀌면 즉시 reset한다.

## 6. 기존 솔버 연결

- `mf_star_only_preprocess.py`: 전역/국소 background, mask, PSF evidence,
  temporal accumulator, star-only frame 생성
- `mf_star_only_state.py`: fingerprint와 작은 ring buffer, 진단값
- `solver.py`: 광각 feature gate와 중앙/전체 입력 frame 선택만 최소 추가
- 기존 Cedar/Tetra, target-pixel, Integrator 계약은 변경하지 않는다.
- `mf_wide_solver.py` 타일 호출은 실행하지 않는다. 소스는 rollback을 위해 보존한다.

star-only 처리가 예외/NaN/shape 불일치를 만들면 광각에서는 fail-closed로 좌표를
보류한다. 오염된 원본으로 자동 fallback해 구름/달 오발행 위험을 되살리지 않는다.
10mm 이상 또는 기능 off에서는 기존 동작을 그대로 유지한다.

## 7. 진단 API/로그

프레임마다 다음을 기록한다.

- window frame count와 reset reason
- saturation/hard-mask/soft-cloud 비율
- local RMS median/p90
- PSF evidence 후보 수, 반복 후보 수, star-only 최종 후보 수
- 중앙/전체 각각 centroid, matches, RMSE, solve 결과
- 처리 시간과 전체 solve latency

LiveCam에는 원본/배경/star-only/mask를 전환해 볼 수 있는 진단 preview를 추가하되,
기본 화면은 기존 영상을 유지한다.

현재는 Input Frame의 `Star-only preprocessing (5 frames)` 항목으로 star-only 결과를
직접 확인할 수 있다. 이 항목을 선택한 LiveCam 세션에서만 전처리를 실행하며, 다른
Input Frame으로 바꾸면 temporal window를 초기화한다. 이는 확인용 진단 경로이고 기존
solver의 입력이나 좌표 발행 경로는 아직 바꾸지 않는다.

## 8. 단계별 구현

1. **P0 데이터 고정**: 현재 20 RAW와 기존 corpus checksum/metadata 문서화
2. **P1 오프라인 단일 프레임**: multi-scale background와 최소 hard mask 구현,
   centroid 이동 0 검증
3. **P2 시간축 보존**: 5/10프레임 evidence 비교, 흐린 별 recall과 구름 false
   positive 평가
4. **P3 기존 중앙/전체 오프라인 solve**: 타일 없이 solve rate/RMSE 비교
5. **P4 shadow 연결**: 좌표 발행 없이 실시간 진단만 수집
6. **P5 opt-in 발행**: IMU 정지/reset/fail-closed 검증 후 광각에서만 활성
7. **P6 타일 경로 비활성 확정**: 충분한 야간 회귀 뒤 UI/문서 정리

### 8.1 2026-08-26 구현/검증 상태

- P0 완료: 동일 조건 16-bit RAW 20장과 frame metadata 보존
- P1 완료: `mf_star_only_preprocess.py`에 CFA 분리 multi-scale background,
  큰 포화 성분 hard mask, soft illumination weight, DoG evidence 구현
- P2 5프레임 경로 완료: 반복 compact PSF와 단일 cloud-gap PSF의 차등 보존 구현
- P3 현재 corpus 완료: 원본은 네 묶음 모두 solve 실패, star-only는 네 묶음 모두
  기존 SEP 중앙 단계에서 solve 성공
- P4 보류: 중·대 스케일의 사용하지 않는 RMS 계산을 제거한 뒤 단일 프레임 처리
  시간은 약 1.37초에서 1.12–1.21초로 줄었고 출력은 픽셀 단위로 동일했다. 그러나
  여전히 기존 실시간 solver loop에 동기 연결하기에는 크므로, 다음 단계에서 저해상도
  background map/버퍼 재사용 또는 별도 worker를 적용한 뒤 shadow로 연결한다.

| RAW 묶음 | 중앙 SEP 수 | Matches | RMSE | RA (deg) | Dec (deg) | Roll (deg) |
|---|---:|---:|---:|---:|---:|---:|
| 01–05 | 20 | 11 | 78.3″ | 313.94483 | -20.09536 | 349.20308 |
| 06–10 | 21 | 13 | 79.9″ | 313.95723 | -20.12025 | 349.24864 |
| 11–15 | 16 | 8 | 86.0″ | 313.98997 | -20.12732 | 349.24413 |
| 16–20 | 29 | 15 | 82.6″ | 314.04517 | -20.09393 | 349.28887 |

20장 촬영 구간은 약 20.9초이고 중앙 해의 RA 이동은 0.1003°이다. 고정 관측 방향의
항성시 이동 예상량 약 0.087°와 같은 규모이며 Dec 편차와 Roll 편차도 각각 약
0.033°, 0.086° 범위다. 네 독립 해가 같은 하늘을 연속 추적한 것으로 판단한다.
Cedar 검출만으로는 아직 풀리지 않았으므로 현재 성공 근거는 기존 중앙/전체 계단의
SEP 단계이며, 실시간 연결 시에도 Cedar 성공을 필수 조건으로 삼지 않는다.

## 9. 승인 기준

- 모든 cloud/Moon/building corpus에서 잘못된 catalog 좌표 0
- 기존 맑은 6mm/16mm 성공 frame의 solve recall 저하 0 또는 사전 합의 범위
- 구름 사이 반복 별은 2프레임 이상이면 hard mask 때문에 일괄 삭제되지 않음
- star-only 전후 catalog-matched centroid 이동 median <0.1px, p95 <0.25px
- 중앙 우선 및 전체 fallback 순서 유지
- IMU 이동/렌즈/노출/회전 변경 즉시 window reset
- 처리+solve latency가 관측용 허용 범위 안이며 메모리 상한 고정

## 10. 현재 결론

mean-10 실측은 별 수와 match 수를 크게 늘렸지만, LiveCam 스택은 현재 solver 입력이
아니며 단순 평균은 이동 시 stale/blur 위험이 있다. 신규 구현은 mean image를 그대로
연결하지 않고 local-whitened PSF evidence를 짧게 누적한다. 이렇게 해야 구름 사이의
희미한 별을 살리면서 달·건물광·구름 구조를 동시에 억제할 수 있다.

현재 corpus에서는 이 원리가 실제 중앙 solve 4/4로 확인됐다. 다만 처리 지연 최적화,
IMU 이동 reset, 다른 구름/맑은 하늘/16mm 회귀를 마치기 전에는 좌표 발행 경로를
활성화하지 않는다. 타일 solver 설정은 비활성 상태를 유지한다.

검증 상태는 신규 단위 테스트 7개, Ruff, 신규 모듈 mypy가 통과했다. 저장소 전체
테스트는 1,909 passed, 177 skipped이며 이번 파일과 무관한 기존 logging/RA·Dec UI/
UI smoke coverage 테스트 11개가 실패했다. 이 11개는 본 기능 범위에서 수정하지 않는다.
