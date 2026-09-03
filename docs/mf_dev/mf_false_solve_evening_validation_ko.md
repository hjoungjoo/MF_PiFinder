# 간헐적 좌표 점프 방지와 야간 검증 절차

## 1. 목적

6 mm 광각 렌즈와 도심 광해 환경에서 정상 좌표 사이에 전혀 다른 plate-solve 좌표가
간헐적으로 발행되는 현상을 차단한다. 촬영은 카메라 프로세스에서 계속 이어지고, 이
문서의 후보 필터·왜곡 보정·솔브 검증은 별도 solver 프로세스에서 이미 캡처된 RAW를
처리한다. 따라서 다음 프레임 촬영 시작을 기다리게 하는 동기 대기는 추가하지 않는다.

## 2. 소스 분석 결과

2026-09-02에 저장한 실제 문제 RAW(`/tmp/mf_current_falsecheck.tiff`)와 당시 로그를
재생해 다음 경로를 확인했다.

1. SEP의 배경/형상 필터를 통과한 최종 후보 18개 중 6개가 영상 아래쪽 건물·탑의
   포화 조명이었다. 이 6개는 peak가 모두 4095 ADU였고 flux도 약 8,200–41,000으로
   실제 별 후보의 약 500–2,600보다 훨씬 컸다. 기존 코드는 프레임 전체 포화만
   검사하고 개별 source의 포화는 검사하지 않았다.
2. Tetra3는 내부 false-match 확률 제한 `1e-4` 바로 아래인 `9.542e-5`, 6 matches의
   해도 성공으로 돌려줄 수 있었다. solver는 Matches, RMSE, Prob의 후단 기준 없이
   모든 `RA != None` 결과를 즉시 발행했다.
3. 이전에 신뢰한 좌표와 수십~수백 도 떨어진 단일 결과도 연속성 확인 없이
   `SuccessfulSolve`가 됐다. PointingCoordinateService는 이렇게 들어온 solver 결과를
   높은 품질로 취급하므로 하위 계층에서 막을 수 없었다.
4. 저장된 `imx462_color:6mm` Brown--Conrady 계수는 광각 타일 실험 경로에만 쓰였고,
   실제 우선 경로인 Cedar full/centre와 SEP full/centre에는 적용되지 않았다. 가장자리
   별의 잔차가 커지면서 잘못된 pattern과 정상 pattern의 품질 차이가 줄어드는 상태였다.
5. IMU horizon mask는 IMU 보정 상태가 유효할 때만 사용할 수 있다. 현재 장치에서는
   유효한 자세가 없어 이 기능만으로 영상 아래쪽 지상광을 안정적으로 제거할 수 없다.
6. `wide_solver_enabled=false`여도 중앙이 포화되면 Auto(Star)의 주변 SNR 측정을 위해
   타일 솔버가 실행됐다. 이 진단 실행의 성공 해가 노출 품질뿐 아니라 pointing
   `solution`에도 대입되어, 사용자가 비활성화한 실험 솔버가 밝은 중앙 조건에서만
   좌표를 발행할 수 있었다. 간헐 조건과 직접 일치하는 별도 발행 경로다.

## 3. 적용한 방어 계층

### 3.1 개별 포화 source 제거

SEP centroid마다 원본 12-bit RAW의 3×3 peak를 검사한다. 센서 full scale의 98% 이상인
source는 flux 정렬 전에 제거한다. 같은 문제 RAW의 최종 후보는 18개에서 12개가 됐고,
제거된 6개는 모두 포화 건물 조명이었다. 중앙과 주변의 비포화 별 후보 12개는 유지됐다.

### 3.2 native full-frame 해의 명시적 품질 기준

| 경로 | 최소 Matches | 최대 RMSE | 최대 Prob |
| --- | ---: | ---: | ---: |
| SEP centre/full | 7 | 180 arcsec | `5e-5` |
| Cedar centre/full | 6 | 180 arcsec | `5e-5` |

기존 512 Cedar/Tetra 경로의 판정은 바꾸지 않는다. 임계값은 현장 정상 해의
7–10 matches, RMSE 78–142 arcsec와 경계 해의 6 matches, Prob `9.542e-5`를 분리한다.
한 번 관측된 RMSE 234 arcsec 해는 좌표 안전성을 위해 보류한다.

### 3.3 좌표 연속성 확인

- 이전 신뢰 좌표에서 5° 이내의 결과는 즉시 갱신한다.
- 5°보다 큰 점프는 실패로 확정하지 않고 pending으로 보관한다.
- 15초 안의 다음 독립 프레임이 pending 좌표에서 2° 이내로 다시 풀릴 때만 새 좌표를
  신뢰한다. 실제 망원경 이동도 한 solve interval 뒤 정상 반영된다.
- 프로세스 시작 직후 native full-frame 해도 같은 방식으로 두 프레임 확인한다.
- 기존 중앙 512 또는 Cedar centre 경로는 초기 anchor를 즉시 만들 수 있지만, native
  경로의 Matches/RMSE/Prob 품질 기준은 먼저 통과해야 한다.
- 보류 중 원래 신뢰 좌표로 복귀하면 잘못된 pending 후보를 즉시 폐기한다.

2026-09-03 고정 장비 미세 흔들림 실측 뒤 다음 stationary 규칙을 추가했다.

- IMU `moving=false`이면 즉시 허용 범위는 1.5 arcmin +
  `(항성시 각속도 × 경과시간 × 1.25)`다. 고정 Alt/Az 장비에서 발생하는 정상적인
  RA/Dec 진행은 막지 않는다.
- 범위를 넘는 좌표는 다음 독립 프레임도 같은 위치를 지지할 때만 확정한다.
- 전처리 solve를 신뢰한 뒤 원본 경로로 fallback하면 변화량이 작아도 한 프레임
  확인한다.
- solver preprocessing이 ON인 초기 raw solve도 한 프레임 확인한 뒤 첫 anchor로
  채택한다.
- `moving=true`인 수동 이동 중에는 이 미세 gate를 적용하지 않는다. 기존 5° 대규모
  점프 확인은 그대로 유지되며, 이동 종료 뒤 연속 solve로 새 위치를 확정한다.

이 계층은 단발성 점프를 막는다. 같은 지상광 pattern이 두 프레임 연속 오검출되는 경우는
포화 source 제거와 품질 기준이 먼저 차단한다. 비포화 지상광의 반복 오검출이 야간에
관측되면 matched centroid의 영상 분포 기준을 실제 자료로 산정해야 하며, 검증 없이
상·하단 고정 마스크를 추가하지 않는다.

### 3.4 활성 렌즈 왜곡 profile 연결

같은 카메라·렌즈 fingerprint로 활성화된 Brown--Conrady profile을 Cedar centre/full과
SEP centre/full의 centroid에 적용한 뒤 Tetra3로 전달한다. 현재 6 mm 자동 하늘 실측
profile의 `k1=-0.0438924`가 이 경로에서도 사용된다. RAW를 재표본화하지 않으므로
별 에너지와 SEP 검출 자체는 변하지 않는다.

### 3.5 Auto(Star) 주변 측정과 pointing 발행 분리

중앙에 달이나 밝은 광원이 있을 때 주변 타일 solve는 계속 실행하고 matched-star SNR을
Auto(Star)에 제공한다. 그러나 `wide_solver_enabled=false`이면 그 해는 exposure quality
전용으로만 보존하고 RA/Dec pointing에는 대입하지 않는다. 사용자가 광각 타일 pointing을
명시적으로 켠 경우에만 합의된 타일 좌표가 연속성 확인 계층으로 전달된다.

## 4. 저녁 현장 검증

### 4.1 시작 조건

1. 6 mm 렌즈와 `Auto(Star)`를 선택하고 카메라를 10분 이상 고정한다.
2. LiveCam의 SEP overlay를 켜고 중앙 별과 영상 아래쪽 건물 조명이 함께 보이는 구도를
   유지한다.
3. 첫 정상 좌표와 알고 있는 기준 천체 방향을 기록한다. 방향을 일부러 바꾸는 테스트 전
   5분 동안 고정 상태를 먼저 기록한다.

### 4.2 로그 판독

다음 로그는 오류가 아니라 방어 계층이 동작했다는 뜻이다.

- `Rejected sep_center solution: matches_below_7`: 약한 6점 SEP 해 차단
- `Rejected ... rmse_too_high`: 잔차가 큰 해 차단
- `Rejected ... false_probability_too_high`: false-match 확률이 큰 해 차단
- `Held ... solution for confirmation: jump_confirmation`: 기존 좌표에서 5° 넘는 첫 해 보류
- `Held ... initial_fullframe_confirmation`: 재시작 뒤 첫 native 해 보류
- `Held ... stationary_change_confirmation`: 정지 중 허용 범위를 넘은 첫 해 보류
- `Held ... raw_fallback_confirmation`: 전처리에서 원본으로 바뀐 첫 해 보류
- `Held ... initial_preferred_confirmation`: 전처리 ON으로 시작한 초기 raw 해 보류
- `Confirmed ... solution on consecutive frames`: 다음 프레임까지 일치해 새 좌표 승인
- `Peripheral tile solve retained for Auto(Star) quality only`: 주변 SNR에는 사용했지만
  비활성화된 타일 pointing 좌표는 발행하지 않음

같은 새 방향에서 두 번째 정상 해가 나오면 `confirmed_jump` 조건으로 발행된다. API
좌표 갱신 시각과 연속 두 frame의 SolveDiagnostics도 함께 확인한다.

### 4.3 합격 기준

- 고정 10분 동안 발행된 RA/Dec에 단일 프레임 5° 초과 점프가 없다.
- LiveCam에서 포화 건물 조명에는 SEP 원이 표시되지 않고 중앙 비포화 별은 유지된다.
- `Matches >= 7`, `RMSE <= 180`, `Prob <= 5e-5`인 SEP 해만 발행된다.
- 카메라 방향을 5° 이상 실제로 바꾸면 첫 해는 보류되고 15초 안의 두 번째 일치 해에서
  새 위치가 발행된다.
- 캡처 frame ID는 계속 증가하며 solver 처리 때문에 촬영 간격이 추가로 늘지 않는다.
- 정상 필드에서 품질 탈락이 과도하면 임계값을 즉시 완화하지 말고 해당 RAW와
  Matches/RMSE/Prob를 저장해 정상/오검출 분포를 다시 비교한다.

## 5. 회귀 검사

- `test_sep_detect.py`: 개별 포화 source만 제거하고 비포화 별을 보존한다.
- `test_solve_acceptance.py`: RA 0° wrap, 초기 full-frame 확인, 큰 점프 확인, 만료,
  원래 좌표 복귀, stationary sky-rate allowance, raw fallback 확인, 이동 중 bypass를
  검사한다.
- `test_sep_shadow.py`: SEP 6점 경계 해 차단과 왜곡 보정 적용을 검사한다.
- `test_solver_cedar_fullframe.py`: Cedar full-frame 품질 차단과 왜곡 보정 적용을 검사한다.
- 저장한 실제 문제 RAW 재생 결과: 최종 후보 18 → 12, 제거된 최종 후보 6개 모두
  4095 ADU 포화 지상광.

## 6. 광각 솔브 좌표 안정화

Stationary 확인 gate는 잘못된 단발 좌표를 막지만, 6 mm 광각 영상에서 정상으로
확정된 solve 자체의 1–4 arcmin 산포까지 제거하지는 않는다. SkySafari와 Web에
제공하는 `PointingCoordinateService` 출력에 다음 후단 안정화를 적용한다.

- 서로 다른 정상 `CAM` solve 5개만 구면 단위 벡터로 평균한다. 같은 solve를 반복
  조회하거나 `CAM_FAILED`가 보존한 좌표는 평균 창에 다시 넣지 않는다.
- 고정 카메라는 Alt/Az에서 평균한 뒤 현재 시각 RA/Dec로 변환한다. 따라서 지구 자전에
  따른 정상적인 RA 진행을 지연시키지 않는다.
- 추적 장비는 RA/Dec 평균이 적합하다. 첫 5개 solve의 적도/수평 산포를 비교해 더 작은
  좌표계를 고르고, 관측 중에는 좌표계를 바꾸지 않는다. 좌표계 재선택은 실제 IMU 이동,
  0.25° 이상의 새 위치, 위치 변경 또는 명시적 상태 초기화 뒤에만 한다.
- IMU `moving=true` 또는 IMU dead-reckoning 좌표는 평균을 우회하고 창을 즉시 비운다.
  따라서 수동 이동 반응과 최신 plate solve/IMU anchor 계약은 바뀌지 않는다.
- 첫 4개 solve는 원시 확정 좌표를 그대로 제공한다. 3개만으로 좌표계를 정했을 때
  초기 노이즈 때문에 적도/수평 좌표계를 잘못 고르는 실측 사례가 있어, 정확성을 위해
  5개가 모두 찬 뒤 평균을 시작한다.

2026-09-03 고정 장비에서 최종 구현을 45초 실측했다. 23개 상태 표본과 13개 독립
solve 동안 선택 frame은 전부 `horizontal`, window는 전부 5로 유지됐다.

| 지표 | 원시 정상 solve | SkySafari용 평균 좌표 |
|---|---:|---:|
| 연속 step 중앙값 | 1.18 arcmin | 0.51 arcmin |
| 연속 step p95 | 2.73 arcmin | 0.79 arcmin |
| 연속 step 최대 | 4.42 arcmin | 0.99 arcmin |

평균 좌표 표본은 약 2초 간격이므로 중앙값 0.51 arcmin에는 고정 Alt/Az 장비의 정상
항성시 진행이 포함된다. 개발 중 sliding window마다 frame을 다시 고르는 방식은
`horizontal ↔ equatorial` 전환 시 최대 28.65 arcmin 점프를 만들었고, 위 frame lock으로
제거했다.
