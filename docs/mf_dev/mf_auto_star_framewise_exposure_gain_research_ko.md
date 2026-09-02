# Auto(Star) 프레임 단위 노출·게인 제어 개선 조사 및 구현안

> 상태: **framewise v2 구현·실내/구름 실기 검증 완료, 맑은 밤 A/B 대기**
> (2026-09-02)
>
> 요청 목표: Auto(Star)가 솔브 결과를 기다리지 않고 **매 캡처 프레임을
> 측정하여 가능한 가장 이른 프레임에 노출과 게인을 반영**한다. 어두운
> 하늘에서는 IMX462/290 프로파일 기본 게인 30을 유지한다. 밝은 하늘이나
> 도시광을 반사하는 구름에서도 **게인이 높을수록 참별 검출과 솔브에
> 유리하면 그대로 유지**하고, 포화 또는 잡음 오검출 때문에 좌표 계산을
> 방해한다는 증거가 있을 때만 낮춘다.
>
> 현장 제약: 현재 구름이 많아 별 하늘 A/B 테스트는 수행하지 않았다. 이
> 문서는 구현 경로, 안전장치, 무천체 테스트와 다음 맑은 밤의 검증 기준을
> 확정하기 위한 설계 문서다.
>
> 선행 문서: [자동 노출 방법 조사](mf_auto_exposure_methods_ko.md),
> [현행 Auto(Star) 설계](mf_auto_exposure_plan_ko.md),
> [2026-07-26 현장 검증](../mf_report/mf_auto_exposure_field_review_20260726_ko.md),
> [카메라 아키텍처](../ax/camera.md)

> 구현 반영(2026-08-28): `buffer_count=3, queue=False`, DMA request 조기
> release, 실제 메타데이터/drop 계측, atomic exposure+gain control 제출,
> 512 image+metadata의 atomic latest-wins envelope, full RAW `frame_id` 검증을
> 코드와 단위 테스트에 반영했다. 주변 matched-star SNR과 gain 상태기계를
> 사용하는 Auto(Star) v2 actuator는 아직 활성화하지 않았다.
>
> 실기 확인(동일 일자, IMX290/462): 서비스 재시작 후 실제 400 ms 노출에서
> request-held 4~5 ms, release 이후 처리 32~41 ms, 안정 구간 drop 0을
> 확인했다. 기존 화면 절전 경로가 캡처를 약 30초씩 중단하던 문제도 발견하여
> 제거했다. 수정 뒤 화면 절전 상태에서도 2.5초 동안 accepted sequence가
> 22프레임 증가했다. 25 ms 노출처럼 처리보다 센서가 빠른 구간은 drop으로
> 계수되고 backlog는 만들지 않았다. 이 수치는 당시 장치 상태의 표본이며
> 장기 p95는 추가 운전 데이터로 확정한다.

> 노출 안정화 반영(2026-09-02, IMX462 실기): Auto(Star)가 중앙 production
> crop만 보던 문제를 수정하여 gated Cedar와 Cedar/SEP 전체 프레임 합의 수를
> 사용한다. SQM SNR override도 Auto(Star) 선택 시 확실히 해제한다. 최근
> 노출별 검출 수 중앙값으로 비단조 광해 응답을 학습하여, 긴 노출에서 후보가
> 무너지면 실측 최적 노출을 90초 유지한 뒤 재탐색한다. 밝은/구름 낀 실제
> 시야에서 기존 400/800/1000 ms 반복 순환이 사라졌고, 유지 만료 후
> 400→200 ms를 짧게 재평가한 다음 17/17 시도를 200 ms에 유지했다. Gain은
> 참별/오검출 품질 증거가 아직 없으므로 프로파일 30(실제 29.51)을 유지했다.
> 이 단계는 여전히 솔버 결과 주기의 exposure 안정화이며, 매 RAW 프레임
> 주변 matched-star SNR과 자동 gain actuator는 후속 구현 범위다.

> Framewise v2 구현·실기 반영(2026-09-02): 3×3 주변 RAW p50/MAD/p90/p99/
> p999/포화율, 중앙 달·bloom connected-component 제외, 실제 메타데이터 기반
> pending/apply 확인, 비대칭 exposure 제어, gain 사다리 시험/rollback 및
> full-frame/주변 타일 catalog-match RAW SNR 요약을 구현했다. 기존 솔브 주기
> Auto(Star)는 v2 활성 중 카메라 값을 쓰지 않는다. IMX462 실기에서 제어
> 계산 7.2~10.4 ms, 전체 후처리 41.8~48.2 ms, request-held 4.0~5.5 ms,
> 관측 drop 0이었다. 이 장치에서 command→applied 지연은 최대 7 accepted
> frames로 재측정되어 timeout은 실측+2인 9 frames로 정했다. 별이 없는 구름
> 조건에서는 포화 탈출과 pending windup 방지만 검증했으며 gain 품질 판정과
> §12 야간 합격 기준은 다음 맑은 밤에 검증해야 한다. 기능은
> `camera_auto_star_framewise` opt-in으로 유지한다.

> 광해 시야 추가 검증(2026-09-02): gain 30/200 ms에서 주변 p999가
> 3969/4095까지 올라간 첫 프레임에 100 ms 하향을 제출했고 7 accepted frames
> 뒤 실제 100 ms, p999 2222, 포화율 0으로 복구했다. 이후 gain 30/100 ms에서
> 100→15 단계 시험이 한 번 발생했으나 gain 15/약 197 ms도 catalog match가
> 0이어서 gain 30/약 99 ms로 rollback했다. 후보 수 감소만으로 낮은 gain을
> 유지하지 않고 실제 solve 성공과 3개 이상 match 개선을 요구하도록 수정했으며,
> 실패한 gain 시험은 90초 cooldown한다. 최종 관측 구간은 gain 30/100 ms,
> p50 1173~1184, p999 2152~2182, 포화율 0, direction reversal 0이었다.
> 숫자 gain만 lock하고 `Profile`은 자동 gain 사다리를 unlock한다. 낮은 gain이
> 실제 품질 개선으로 유지된 뒤에는 양호한 주변 solve 5회가 연속될 때 한 단계
> 높은 gain을 같은 총 노광량으로 재시험하고 품질이 나빠지지 않을 때만 기본
> 고gain 방향으로 복귀한다.

## 1. 결론

권고안은 **카메라 프로세스 안의 2중 루프**다.

1. **빠른 내부 루프**는 매 RAW 프레임을 중앙 하나가 아닌 공간 격자로 나눠
   배경, MAD, 상위 퍼센타일, 포화율을 계산한다. 이 루프는 포화 방지와
   급격한 광량 변화의 안전 경계만 즉시 다룬다.
2. **느린 외부 루프**의 목적 함수는 영상의 깨끗함이 아니라 **좌표 계산에
   쓰인 참별**이다. 주변부 솔브에서 카탈로그와 매치된 별의 SNR, Matches,
   RMSE와 검출 후보 과잉을 사용하여 내부 루프의 목표와 최근 성공 앵커를
   보정한다. 직접 카메라 값을 쓰지 않아 두 컨트롤러가 싸우지 않게 한다.
3. 밝아질 때는 포화 방지가 우선이므로 한 번에 크게 낮춘다. 어두워질 때는
   구름이 별을 가린 상황을 노출 부족으로 오판하지 않도록 천천히 올리거나
   최근 솔브 성공값을 유지한다.
4. 총 노광량 목표를 먼저 구한 뒤 노출과 게인으로 분배한다. 기본은 높은
   게인이며, `[30, 15, 8, 4, 2, 1]` 하향은 밝기만으로 실행하지 않는다.
   실제 포화 또는 “후보는 많은데 주변부 catalog match가 되지 않는”
   오검출 압력이 확인되어야 한다. 실제 허용 최댓값은 카메라 프로파일과
   드라이버 `camera_controls`의 교집합으로 제한한다.
5. 적용 여부는 반드시 해당 프레임의 Picamera2 메타데이터
   `ExposureTime`/`AnalogueGain`으로 확인한다. 요청값을 적용값으로
   간주하면 안 된다.

구현 스택은 새 의존성을 추가하지 않고 기존 **SEP + SciPy `ndimage` +
NumPy + Tetra3**를 재사용한다. SEP는 주변 catalog-match 별의 local
background aperture SNR, SciPy는 달/포화 mask, Tetra3는 참별 판정에 쓴다.

중요한 현실 제약이 하나 있다. **연속 스트리밍에서 프레임 N을 본 뒤 센서의
물리적 N+1 프레임에 새 값을 보장하는 것은 현재 장치에서 불가능하다.**
저장소의 실측 주석에는 IMX290/462가 노출 변경 뒤 기존 노출 프레임을 정확히
3장 전달한다고 기록되어 있고(`camera_interface.py::_settle_exposure`),
libcamera도 센서별 gain/exposure delay를 처리한다. 따라서 달성 가능한 계약은
다음과 같다.

> 매 프레임 판단하고 즉시 제어를 제출하되, 센서 파이프라인이 허용하는 가장
> 이른 프레임에 적용하며, 실제 적용 프레임은 메타데이터로 식별한다.

카메라를 매번 stop/set/start하면 첫 유효 프레임에 값을 강제할 수 있지만,
재시작 시간과 프레임 손실 때문에 프레임 단위 자동 제어에는 부적합하다.

## 2. 현행 Auto(Star)가 느린 이유

현재 데이터 흐름은 다음과 같다.

```text
프레임 N 캡처
  → camera_image/shared_state 복사
  → 별도 solver 프로세스가 검출·솔브
  → last_solve_attempt가 갱신됨
  → 이후 카메라 루프가 결과를 발견
  → ExposureStarCountController.update()
  → 노출만 set_controls()
  → 센서 지연 뒤 적용
```

구체적인 한계는 다음과 같다.

- `camera_interface.py:595-703`의 Auto(Star)는 **새 솔브 결과가 있을 때만**
  실행된다. 캡처 프레임 속도보다 솔브 케이던스가 제어 속도를 결정한다.
- 피드백은 해당 시점의 `base_image`가 아니라 비동기로 도착한
  `SolveDiagnostics.Centroids`다. 솔브 결과의 원본 프레임과 현재 카메라
  프레임 사이에 시간차가 있다.
- `ExposureStarCountController`의 출력은 노출 시간 하나뿐이다. 게인은
  프로파일 기본값(IMX462/290은 30) 또는 사용자의 마지막 수동값에 고정된다.
- 밝은 하늘 가드는 처리된 8-bit 중앙 평균 하나를 사용한다. 포화율,
  RAW 페데스탈, 게인별 응답과 실제 적용 메타데이터를 제어 모델에 쓰지 않는다.
- `CameraPI.set_camera_config()`는 `AeEnable`, `AnalogueGain`, `ExposureTime`을
  세 번의 `set_controls()` 호출로 나눠 보낸다. 노출·게인 한 쌍을 한 요청으로
  제출하는 편이 전이 프레임의 해석과 추적에 유리하다.
- `self.exposure_time`과 `self.gain`은 요청 상태다. 실제 프레임의 값은
  `last_frame_metadata`에 따로 있는데 기존 컨트롤러는 요청 상태를 피드백
  기준으로 사용한다.

즉 기존 컨트롤러를 단순히 “매 루프마다 호출”하면 해결되지 않는다. 같은
솔브 결과를 여러 번 재사용하고, 아직 적용되지 않은 요청을 현재값으로
오인해 연속 보정하면서 과조정하게 된다.

## 3. 제어 목표와 비목표

### 3.1 목표

- 모든 정상 RAW 프레임을 한 번씩 평가한다.
- 솔버 왕복을 제거해 밝기 급변에 대한 명령 제출 지연을 1 캡처 루프 이내로
  줄인다.
- 실제 적용된 노출·게인 쌍과 그 프레임의 통계를 정확히 연결한다.
- 어두운 하늘에서는 게인 30을 기본값으로 사용한다.
- 어느 정도의 노이즈를 허용하고 **참별 검출 수·catalog match·솔브 성공률을
  최우선**으로 한다. 영상의 매끄러움은 평가 지표가 아니다.
- 밝은 하늘/밝은 구름에서도 고게인이 검출에 유리하면 유지한다. 포화 또는
  잡음 후보가 별로 오인되어 패턴 매칭을 방해할 때만 게인을 낮춘다.
- 중앙에 달이나 강한 광원이 있어도 중앙 ROI를 전체 하늘의 대표값으로 쓰지
  않고, 유효한 주변부 솔브와 주변 RAW 영역에서 SNR을 계산한다.
- 어두운 구름, 렌즈 가림, 슬루를 “더 많은 노출이 필요한 하늘”로 오판하지
  않는다.
- 수동 노출, 수동 게인, 주간 native AE, SQM용 배경 컨트롤러를 침범하지
  않는다.
- IMX296처럼 프로파일 최대 게인이 15인 센서에도 같은 코드가 동작한다.

### 3.2 비목표

- 완전히 흐려 별이 없는 프레임에서 솔브를 만들어내는 것.
- 첫 구현에서 모든 센서에 공통인 최종 임계값을 확정하는 것.
- LiveCam의 `Stretched` 표시 밝기를 제어 신호로 사용하는 것. 이 모드는
  프레임별 퍼센타일 스트레치라 광량 변화가 상쇄된다.
- 매 프레임 카메라를 재시작하여 문자 그대로 N+1 적용을 강제하는 것.

## 4. 목적 함수와 게인의 정확한 해석

PiFinder의 목적은 영상 촬영이 아니라 좌표 계산과 추적이다. 따라서 목적
함수의 우선순위는 다음과 같아야 한다.

1. 올바른 좌표를 내는 솔브 성공
2. catalog match 수와 매치된 참별의 강건 SNR
3. 좌표 계산 시간을 늘리는 과도한 검출 후보와 오검출 억제
4. 포화·이동 블러 억제
5. 영상의 시각적 노이즈 — **제어 목적이 아님**

밝은 하늘에서 게인을 낮출 수는 있지만, “게인을 낮추면 영상이 깨끗해진다”는
이유만으로 낮추면 안 된다.

- 구름/광해가 밝을 때 지배적인 것은 대개 **광자 샷 노이즈**다. 아날로그
  게인을 낮춰도 이미 들어온 광자의 샷 노이즈 자체는 사라지지 않는다.
- 고게인은 같은 센서 전자 수를 더 큰 ADU로 만들므로 읽기 노이즈가 중요한
  어두운 환경에는 유리할 수 있다.
- 반대로 밝은 환경에서는 읽기 노이즈의 비중이 작다. 고게인의 이득은
  줄고, 입력 다이내믹레인지와 포화 여유를 잃는 비용이 커진다.
- 같은 출력 밝기를 유지할 수 있고 이동 블러가 허용된다면 **더 긴 노출 +
  더 낮은 게인**은 더 많은 광자를 모으므로 별 SNR에 유리할 수 있다.

실제로 2026-07-26 서울 스윕에서 200 ms/gain 15가 200 ms/gain 30보다
검출 수가 같거나 많았다. 그러나 이 한 사례를 “밝으면 항상 gain 15” 규칙으로
일반화하지 않는다. 동일 프레임 조건에서 **솔브 성공, Matches, 매치 별 SNR,
후보/매치 비율**이 개선되는지를 보고 gain 경계를 정한다.

### 4.1 검출 수와 참별 수는 다르다

`Centroids`가 많다는 사실만으로 노출·게인이 좋다고 판정할 수 없다. 구름
무늬, 웜픽셀, 포화 경계와 고게인 잡음이 후보를 늘릴 수 있다. 반대로 catalog
match는 최소한 별 패턴과 일치했다는 강한 증거다.

외부 루프에는 다음과 같은 점수를 사용한다. 정확한 가중치는 shadow 결과로
정하지만 우선순위는 고정한다.

```text
quality = solve_success_reward
        + w_match × peripheral_matches
        + w_snr × robust_peripheral_matched_star_snr
        - w_candidate × unmatched_candidate_pressure
        - w_saturation × usable_region_saturation
        - w_latency × solve_time
```

`unmatched_candidate_pressure`는 `candidates - matches`를 곧바로 “가짜 별
개수”라고 부르지 않는다. 매치에 사용되지 않은 진짜 별도 있기 때문이다.
대신 후보 수가 급증했는데 Matches와 SNR은 줄고 전 경로가 실패하는 패턴을
**오검출 압력의 proxy**로 사용한다.

### 4.2 중앙 달을 배제한 주변부 SNR

중앙에 달이 있으면 중앙 crop은 포화되고 별 검출·솔브·SNR 계산이 모두
실패할 수 있다. 따라서 중앙 단일 솔브나 중앙 단일 ROI를 SNR의 필수 입력으로
두지 않는다.

현행 솔버에는 이미 두 종류의 주변 근거가 있다.

- 기본 4단 경로: `cedar_center → sep_center → cedar_full → sep_full`과
  `CedarRawCentroids`, `CedarGatedCentroids`, `CedarCenterCentroids`,
  `SepCentroids`
- 선택형 타일 경로: 타일별 `centroids`, `matches`, `RMSE`, 중앙 포화 판정,
  시도/후보/합의 타일(`TileScores`, `TileAttempted`, `TileAccepted`)

하지만 현재 진단만으로는 full-frame의 **매치된 별이 중앙인지 주변인지**
알 수 없다. 구현 시 solver가 `matched_centroids`를 좌표계 때문에 제거하기
직전에 AE 전용 요약을 계산해야 한다.

```python
{
    "frame_sequence": int,
    "source": "peripheral_full" | "peripheral_tile" | "center",
    "region_ids": tuple[str, ...],
    "matched_stars": int,
    "candidate_stars": int,
    "snr_p25": float | None,
    "snr_median": float | None,
    "rmse": float | None,
    "solve_success": bool,
    "center_contaminated": bool,
}
```

별 하나의 빠른 SNR proxy는 원본 RAW의 매치 좌표에서 aperture와 local
annulus로 계산한다.

```text
signal = aperture_sum - aperture_pixels × median(local_annulus)
noise  = max(1.4826 × MAD(local_annulus) × sqrt(aperture_pixels), epsilon)
SNR_proxy = signal / noise
```

전자/ADU 변환 보정 전까지는 절대 물리 SNR이 아니라 같은 센서·gain 설정을
비교하는 proxy로 쓴다. 평균 대신 하위 25퍼센타일과 중앙값을 함께 사용하면
밝은 별 몇 개가 전체 평가를 지배하지 않는다.

영역 선택 규칙:

1. 중앙 포화 또는 달 오염 판정 시 중앙 region을 SNR 집계에서 제외한다.
2. full-frame 솔브가 성공하면 `matched_centroids` 중 중앙 오염 mask 밖의
   별만 골라 주변 SNR을 계산한다.
3. 중앙 오염 때문에 full-frame까지 실패하면 기존 타일 계획을 재사용한
   **measurement-only 주변 솔브**를 실행한다. 이 경로는
   `wide_solver_enabled`나 주변 좌표 발행 설정에 의존하지 않고 Auto(Star)의
   품질 측정만 제공해야 한다. 중앙 정상 프레임에는 추가 비용을 쓰지 않는다.
4. 주변부에서 catalog match에 성공한 full-frame/tile 결과를 우선한다.
5. 주변 타일 하나만 유효해도 **AE 품질 측정에는 사용 가능**하다. 다만
   좌표 발행은 기존의 주변 타일 합의 규칙을 그대로 지켜야 한다. AE 측정이
   포인팅 안전 규칙을 완화해서는 안 된다.
6. 여러 주변 region이 유효하면 Matches로 가중한 강건 중앙값을 사용한다.
7. 주변 결과가 하나도 없으면 중앙 달 프레임으로 gain을 올리지 않는다.
   최근 주변 성공 앵커를 유지하고 포화 안전 하향만 허용한다.

이때 “주변 솔브를 AE에 사용”한다는 말은 두 역할을 구분한다.

- **AE 측정:** 품질 게이트를 통과한 단일 주변 솔브도 참별/SNR 근거로 사용.
- **좌표·추적 발행:** 현행 정책이 요구하는 주변 합의 또는 검증된 recovery
  규칙을 통과한 경우에만 사용.

따라서 중앙 달 때문에 AE 측정이 멈추지는 않지만, 단일 주변 결과 하나가
잘못된 좌표를 발행하도록 안전 기준을 낮추지도 않는다.

### 4.3 권장 제어 알고리즘: 지연 인지형 supervisory state machine

일반 PID 하나로 `Centroids`를 목표값에 맞추는 방식은 권장하지 않는다.
후보 수는 gain에 대해 단조롭지 않고, 구름·웜픽셀·포화 경계 때문에 갑자기
늘 수 있으며, 센서 적용 지연까지 있다. 오차가 크다는 이유로 gain을 계속
올리는 PID는 오검출을 더 키우는 양의 피드백이 될 수 있다.

권장안은 두 시간척도를 분리한 **지연 인지형 supervisory controller**다.

1. **매 프레임 안전·광량 루프:** 주변 grid의 background, MAD, p999,
   포화율을 보고 노출을 먼저 조정한다. pending control이 실제 메타데이터로
   확인될 때까지 추가 명령을 내리지 않는다.
2. **솔브 품질 루프:** catalog match가 있는 프레임만 사용해 gain 상태를
   결정한다. 기본은 gain 30을 유지하고, 포화 또는 반복되는 오검출 압력이
   실제 솔브 품질을 해칠 때만 한 단계 낮춘다.
3. **시험 후 유지/복귀:** gain을 낮출 때 처음에는
   `exposure × gain`이 비슷하도록 노출을 보상하되 motion 한계를 넘지 않는다.
   적용 지연 뒤 같은 주변 region의 Matches, SNR, 후보 압력을 비교하여
   개선되면 유지하고 아니면 이전 gain으로 복귀한다.

gain은 연속 최적화보다 `[30, 15, 8, 4, 2, 1]` 같은 이산 사다리가 적합하다.
각 상태 전이에 최소 유지 프레임, K회 연속 품질 근거, 약 0.5 stop의
히스테리시스를 둔다. 한 번의 솔브 실패나 구름 프레임으로 gain을 바꾸지
않는다.

```text
on_frame(frame, applied_metadata):
    pending 상태를 실제 ExposureTime/AnalogueGain과 대조
    주변 grid 광량/포화 안전 한계를 계산
    if hard_saturation:
        노출을 즉시 단축(gain 변경은 보류)
    elif pending:
        hold
    else:
        deadband와 rate limit 안에서 다음 노출 요청

on_solve(frame_id, peripheral_quality):
    요청값이 아니라 그 frame_id의 실제 메타데이터와 결합
    if 주변 match 없음:
        최근 성공 anchor 유지; 포화 안전 하향만 허용
    elif solve/Matches/SNR 양호:
        현재 gain 유지; 높은 gain 자체를 벌점으로 두지 않음
    elif K회 연속 (포화 또는 후보 압력 증가) and Matches/SNR 저하:
        다음 낮은 gain을 시험하고 노출 보상
        적용 후 품질이 개선되지 않으면 rollback
```

Bayesian optimization, contextual bandit, MPC는 초기 구현에 권장하지 않는다.
이들은 행동별 보상이 충분히 자주 관측된다는 전제가 필요한데, 솔브 보상은
느리고 구름·시야·별 밀도에 따라 비정상적이며 중앙 달 프레임에서는 빠질 수
있다. 먼저 shadow telemetry를 축적한 뒤 gain 사다리의 임계값을 오프라인으로
튜닝하는 편이 안전하고 설명 가능하다.

### 4.4 라이브러리 조사와 선택

현재 설치 상태는 `numpy 1.26.4`, `scipy 1.17.1`, `sep 1.4.1`이며
`astropy`, `photutils`, `opencv`는 설치되어 있지 않다. 결론은 **새 런타임
의존성 없이 기존 SEP·SciPy·NumPy·Tetra3를 재사용**하는 것이다.

| 후보 | 판단 | 사용 위치 또는 제외 이유 |
|---|---|---|
| **SEP 1.4.1** | 채택 | 이미 검출 경로와 의존성에 포함. 공간 가변 background/RMS와 벡터화된 aperture photometry로 주변 매치 별 SNR 계산 |
| **Tetra3** | 유지 | catalog와 기하적으로 일치한 참별 게이트. 후보 수가 아니라 match를 품질의 중심으로 사용 |
| **SciPy `ndimage`** | 채택 | 포화 connected component, 달/번짐 mask 확장, region labeling. 이미 설치되어 추가 비용 없음 |
| **NumPy** | 채택 | median/MAD, p25, 히스테리시스, 짧은 ring buffer와 점수 집계 |
| cedar-detect | 유지 | 빠른 1차 후보 검출. SEP는 fallback 및 정밀 광도 측정 역할 |
| Photutils/Astropy | 보류 | DAOStarFinder와 풍부한 PSF 도구는 유용하지만 현재 목적은 SEP로 충족. 추가 의존성과 메모리/배포 부담이 큼 |
| OpenCV/scikit-image | 제외 | 일반 blob/morphology 기능은 SciPy/SEP와 중복되고 catalog truth를 제공하지 않음 |
| libcamera native AEGC | 보조만 | 평균 밝기 기반 AEGC는 주변 catalog Matches/SNR과 오검출 압력을 목적 함수로 받을 수 없음 |
| tetra3rs | 보류 | 추적 힌트 기능은 흥미롭지만 현재 alpha API로 solver를 교체할 이유가 없음 |
| ML 오검출 분류기 | 보류 | 라벨 코퍼스와 기기별 재학습 필요. 현 단계에서는 morphology + catalog match가 더 직접적이고 검증 가능 |

#### SEP로 매치 별 SNR 계산

기존 solver가 full-frame/tile의 `matched_centroids`를 좌표계 정리 과정에서
제거하기 **직전**에 좌표와 해당 RAW를 결합한다. 가능하면 배경 제거 배열,
`sep.Background.rms()`의 공간 RMS map, 달/포화 mask를 사용한다.

```python
flux, fluxerr, flag = sep.sum_circle(
    background_subtracted_raw,
    matched_x,
    matched_y,
    aperture_radius,
    err=background.rms(),
    gain=None,
    mask=moon_and_saturation_mask,
    bkgann=(annulus_inner, annulus_outer),
)
snr = flux / np.maximum(fluxerr, epsilon)
```

여기서 SEP의 `gain`은 **검출기의 전자/ADU 변환 gain**이다. Picamera2의
`AnalogueGain` 배율이 아니므로 `30`을 넘기면 계산이 틀린다. 센서와 각
analogue gain 상태별 e-/ADU 변환을 photon-transfer 방식으로 보정하기
전에는 `gain=None`과 RMS map으로 일관된 상대 SNR proxy를 계산한다.

집계 전에는 다음을 적용한다.

- SEP 오류 flag가 있거나 aperture가 mask/프레임 경계와 겹치는 별 제외
- 중앙 달/포화 connected component와 dilation margin 안의 별 제외
- full-resolution 좌표와 2×2 binned RAW 좌표의 변환을 명시적으로 수행
- 별별 SNR의 p25와 median, 사용 별 수를 함께 저장
- 최소 유효 match 수 미달이면 SNR 수치 대신 `insufficient`로 기록

달 mask는 단순 중앙 원보다 `scipy.ndimage.label`로 포화 connected
component를 찾고 `binary_dilation`으로 bloom/halo 여유를 주는 방식이 낫다.
달이 중앙에서 벗어나도 동작하며, 고도·수평선·비네팅 정적 mask와 합칠 수
있다. local annulus는 남은 완만한 배경 기울기를 줄이고, catalog match는
노이즈 peak가 SNR 표본에 들어오는 것을 막는다.

## 5. 제안 아키텍처

```text
CameraPI.capture()
  │
  ├─ RAW + 실제 ExposureTime/AnalogueGain/SensorTimestamp
  │
  ├─ SpatialFrameRadiometry (매 프레임, 카메라 프로세스)
  │    중앙+주변 grid별 background / MAD / saturation / gradient
  │
  ▼
AutoStarFrameController ── pending/applied 추적 ──► ExposureGainAllocator
  ▲                                                    │
  │                                                    ▼
  ├─ 최근 솔브 성공 앵커                         단일 atomic set_controls
  ├─ 주변 catalog match/SNR/RMSE                     │
  ├─ fresh cloud_flag                                 ▼
  └─ IMU motion limit                           센서 지연 후 실제 적용
                                                       │
                                                       └─ metadata로 확인
```

### 5.1 소유권

빠른 컨트롤러는 반드시 **카메라 프로세스**가 소유한다. 웹/API 큐나 solver
프로세스를 왕복하지 않는다. 단, “카메라 프로세스 소유”는 센서 캡처를
컨트롤러가 기다린다는 뜻이 아니다. DMA request의 RAW와 메타데이터를 로컬
메모리로 넘기고 request를 즉시 release한 뒤, **센서가 다음 프레임을 노출하는
동안** 통계와 제어를 계산한다.

Auto(Star) v2가 활성화된 동안 카메라 값을 쓰는 주체는 하나여야 한다.
기존 `ExposureStarCountController`는 직접 노출을 변경하지 않고 다음만 외부
루프 입력으로 제공한다.

- 최근 솔브 성공 노출·게인 앵커
- 주변부의 catalog match 수·매치 별 SNR·RMSE
- 검출 후보가 늘지만 match가 늘지 않는 오검출 압력
- 내부 루프 배경 목표를 천천히 올리거나 낮출 품질 힌트

SQM 화면의 `snr` 제어가 활성화되면 framewise Auto(Star)는 일시 정지한다.
native AE 또는 manual 체제로 바뀌면 pending 요청과 학습 상태를 초기화한다.

### 5.2 기존 per-frame 계측 재사용

`camera_pi.py`는 이미 매 RAW 프레임에
`collect_radiometer_sample()`을 호출한다. 이 함수는 중앙 80%의 희소 그리드로
다음을 저비용 계산한다.

- 배경 중앙값
- MAD
- 4분면 중앙값과 배경 그라디언트
- 실제 노출 시간과 자체 sequence

현재 방식 그대로 중앙값 하나만 반환하면 달이 중앙에 있을 때 사용할 수 없다.
희소 샘플 비용은 유지하되 solver-valid 영역을 3×3 또는 현재 렌즈의 타일
계획으로 나누고, 중앙과 주변 region을 각각 반환하도록 확장한다. 비네팅
가장자리, 지평선 mask, 사용자 제외 타일은 SNR 집계에서 제외한다. 다음 필드를
추가하면 빠른 AE에 필요한 대부분의 값이 갖춰진다.

```python
{
    "sequence": int,
    "sensor_timestamp_ns": int | None,
    "actual_exposure_us": float,
    "actual_analogue_gain": float,
    "actual_digital_gain": float | None,
    "regions": {
        "C": {
            "background_p50_adu": float,
            "background_mad_adu": float,
            "p90_adu": float,
            "p99_adu": float,
            "p999_adu": float,
            "saturated_fraction": float,
            "background_gradient": float,
            "usable": bool,
        },
        "U": {"...": "..."},
        "L": {"...": "..."},
        "R": {"...": "..."},
        "D": {"...": "..."},
    },
}
```

통계는 crop 적용 뒤, bias subtraction 전의 profile bit-depth RAW에서
계산한다. LiveCam 표시 프레임이나 8-bit 변환 결과를 사용하지 않는다.

### 5.3 무중단 캡처와 처리 과부하 정책

#### 현행 코드 확인

`camera_pi.py::initialize()`는 `create_still_configuration()`에
`buffer_count`와 `queue`를 지정하지 않는다. 현재 설치된 Picamera2 0.3.31의
still 기본값은 `buffer_count=1`, `queue=True`다. 버퍼가 하나뿐이면 Picamera2는
파이프라인 정지를 피하기 위해 완료 프레임을 내부 latest queue에 보관하지
않지만, 애플리케이션이 받은 request를 release할 때까지 그 버퍼를 재사용할
수 없다.

현행 `CameraPI.capture()`의 순서는 다음과 같다.

```text
capture_request() 대기
  → full RAW를 NumPy로 copy
  → metadata 읽기
  → request.release()
  → LiveCam 선택/누적 처리
  → crop/rotate + radiometer
  → Manager proxy로 cropped/full RAW 복사
  → float 변환/stretch/resize/PIL
  → camera loop에서 rotate/paste/metadata 발행
  → Auto(Star) 확인
  → 다음 capture_request()
```

따라서 request release 뒤의 무거운 처리는 다음 센서 노출과 겹칠 수 있지만,
**RAW 복사가 끝나 release하기 전까지는 단일 버퍼 경계에서 다음 요청을 위한
여유가 없다.** 또한 Auto(Star) 판단이 PIL/Manager 복사 뒤에 있어, 짧은
노출에서는 이미 여러 센서 프레임이 지나간 뒤 제어가 제출될 수 있다.

solver 쪽은 `last_image_metadata`가 새로울 때 Manager의 `camera_image` 최신값을
복사한다. 큐를 무한히 쌓지는 않으므로 결과적으로 오래된 프레임을 건너뛰는
latest-wins에 가깝다. 다만 이미지, full RAW, 메타데이터가 서로 다른 proxy
호출로 발행되어 같은 프레임이라는 원자적 보장은 없다. 처리 지연이 커질수록
이 pairing race도 함께 해결해야 한다.

#### 권장 기본 정책: 촬영 우선, 오래된 프레임 폐기

좌표 계산과 추적에는 모든 과거 프레임을 늦게 처리하는 것보다 최신 관측의
나이가 짧은 것이 중요하다. 따라서 기본 정책은 버퍼링이 아니라
**bounded latest-wins/drop-oldest**로 정한다.

```text
libcamera sensor pipeline:  F0 ─ F1 ─ F2 ─ F3 ─ F4 ─ F5 ─►  (항상 연속)
                                  │         │         │
accepted processing:             P0────────┘         P4────►
dropped on overload:                        F1 F2 F3

원칙: 센서를 기다리게 하지 않는다.
      처리 슬롯이 없으면 새 request를 즉시 release하거나 이전 READY를 폐기한다.
      PROCESSING 중인 메모리는 덮어쓰지 않는다.
```

구체적인 1차 수정안:

1. still configuration을 **`buffer_count=3, queue=False`**로 명시한다.
   triple buffer는 한 버퍼를 애플리케이션이 복사하는 동안 다음 센서 요청이
   진행될 여유를 준다. `queue=False`는 처리자가 다시 요청할 때 이미 완료된
   오래된 한 장을 돌려주는 Picamera2 cache를 끈다.
2. request를 잡고 있는 critical section에는 RAW copy, 실제 metadata 읽기,
   최소 frame id 생성만 둔다. 파일 저장, Manager proxy, PIL, LiveCam,
   solver용 변환은 모두 release 뒤로 둔다.
3. sparse spatial radiometry와 framewise controller를 release 직후로 옮긴다.
   이 계산은 다음 노출과 겹치며, LiveCam/solver publish보다 우선한다.
4. `set_controls`는 노출과 gain을 한 dict로 제출한다. 제출을 위해 캡처를
   stop/start하거나 settle frame을 동기적으로 버리지 않는다.
5. 처리 시간이 노출/FrameDuration보다 길면 중간 센서 프레임은 Picamera2에서
   재순환되어 폐기한다. 다음 처리 입력은 완료 cache에 오래 쌓인 프레임이
   아니라, 이전 처리 중 이미 진행 중이었거나 이후 완료되는 fresh frame이며
   과거 프레임 backlog를 만들지 않는다.

`flush=True`는 기본으로 쓰지 않는다. queue cache를 끈 상태에서 이것까지
사용하면 이미 정상적으로 노출 중인 fresh frame도 버리고 그 다음 프레임을
기다려 제어 관측 지연이 한 노출만큼 늘 수 있다.

1차 변경 뒤에도 release 이후의 처리 때문에 “소프트웨어가 받아 평가하는
프레임률”이 부족하면 2차로 acquisition과 processing을 분리한다.

- 고정 크기 RAW slot 3개: `FREE`, `READY`, `PROCESSING`
- acquisition은 완료 request를 빈 slot에 복사하고 즉시 release
- 빈 slot이 없으면 기다리지 않고 incoming을 drop
- READY가 여러 개면 가장 최신 것만 남기고 이전 READY를 drop
- worker는 radiometry/preview/solver publish를 수행
- 카메라 control mailbox는 depth 1, 최신 target이 이전 미적용 target을 대체

Python `multiprocessing.Queue`에 full RAW를 무제한으로 넣는 방식은 쓰지
않는다. feeder thread 뒤에 메모리 backlog가 숨어 지연과 OOM을 만들 수 있다.
고정 shared-memory ring 또는 카메라 프로세스 내부의 고정 slot을 사용한다.

#### 프레임 pairing과 드롭 계측

모든 산출물에는 같은 `frame_id`를 붙인다. 우선순위는 libcamera
`SensorTimestamp`; 없으면 카메라 프로세스의 단조 증가 sequence를 쓴다.

```python
FrameEnvelope(
    frame_id: int,
    sensor_timestamp_ns: int | None,
    exposure_start_ns: int | None,
    exposure_end_ns: int,
    actual_exposure_us: float,
    actual_gain: float,
    raw_slot: int | None,
    image_512: object | None,
)
```

solver는 이미지와 메타데이터를 별도로 읽지 않고 같은 envelope/slot generation을
확인해야 한다. slot을 읽는 동안 generation이 바뀌면 결과를 사용하지 않고
최신 slot을 다시 읽는다.

`SensorTimestamp` 차이와 `FrameDuration`으로 센서 프레임 간격을 추정하고
다음을 별도 계수한다.

- `sensor_completed` 또는 timestamp 기반 추정 프레임 수
- `accepted_for_processing`
- `drop_no_free_slot`
- `drop_replaced_ready`
- `drop_stale_before_solve`
- `capture_to_control_ms`, `capture_to_solve_start_ms`
- processing p50/p95/max와 실제 frame duration

프레임이 버려지는 것은 오류가 아니라 명시된 overload 동작이다. 다만 drop률과
관측 age가 합격 기준을 넘으면 LiveCam 같은 선택 기능을 먼저 감속하고,
radiometry와 solver 입력이 가장 높은 우선순위를 갖는다.

## 6. 빠른 내부 루프

### 6.1 기본 모델

선형·비포화 구간에서 배경 신호를 다음처럼 근사한다.

```text
B = max(P50_raw - pedestal(gain, temperature), epsilon)
H = exposure_us × analogue_gain
B ≈ scene_flux × H
```

유효한 주변 region의 강건 목표 배경 `B_target`에 필요한 총 노광량은 다음
한 스텝으로 예측한다. 이는 최적화 목적이 아니라 **포화/광량 안전 경계의
초기 추정**이다. 최종 target의 상향·하향은 §4의 주변 솔브 품질이 승인한다.

```text
ratio    = B_target / B
H_target = clamp(H_actual × ratio, H_min, H_max)
```

단, `pedestal`은 현재 프로파일의 단일 `bias_offset`을 무조건 쓰면 안 된다.
IMX462의 238 ADU는 gain 30에서 측정된 값이다. gain 1~30을 자동으로 오갈
예정이면 최소한 게인 사다리 각 점의 dark-frame 중앙값을 측정해
`pedestal_by_gain`을 선형 보간해야 한다. 이 캘리브레이션 전에는 절대 ADU
목표보다 포화 가드와 동일 설정 대비 상대 변화에 더 큰 가중치를 둔다.

### 6.2 노출·게인 분배

총 노광량 `H_target`을 구한 뒤 두 변수를 독립 PID로 제어하지 않는다. 독립
루프 두 개는 같은 밝기 오차를 동시에 보상해 진동하기 쉽다. 하나의
allocator가 다음 우선순위로 쌍을 만든다.

1. 현재 IMU 움직임으로 허용 노출 상한 `t_motion_max`를 계산한다.
2. 정지 시 선호 노출 `t_preferred`를 적용한다. 초기 후보는 현장 스위트스팟과
   응답 속도를 절충한 100~200 ms이며 최종값은 리플레이/야간 A/B로 정한다.
3. 기본 gain은 프로파일 상한(30 또는 15)이다. 높은 gain으로 참별 검출과
   match가 유지되는 동안 낮추지 않는다.
4. 먼저 노출로 총 노광량을 맞춘다. gain 하향은 포화가 노출 안전 하한에서도
   남거나, 후보 과잉/주변 match 감소가 반복되거나, 한 단계 낮은 gain의
   shadow 품질 점수가 더 좋다는 증거가 있을 때만 승인한다.
5. 매우 밝아 gain 1에서도 목표를 넘으면 노출을 25 ms 아래의 드라이버 허용
   최솟값까지 줄인다. 기존 25 ms는 솔빙 경험 범위이지 센서 절대 하한은
   아니므로, v2의 하한은 `camera_controls["ExposureTime"]`과 별도 안전
   설정에서 결정한다.

초기 이산 사다리:

```text
IMX462/290: 30 → 15 → 8 → 4 → 2 → 1
IMX296:     15 → 8  → 4 → 2 → 1
```

사다리는 UI preset이 아니라 센서가 실제 반환하는 gain에 맞춰야 한다. 각
경계에 약 0.5 stop의 히스테리시스를 두고, 경계 안에서는 현재 gain을
유지한다. 한 단계 낮춘 뒤 주변 솔브 품질이 개선되지 않으면 높은 gain으로
복귀한다. 날씨가 변하는 두 프레임을 단순 비교하지 않고, 실제 설정이 적용된
프레임의 공간 통계와 같은 region의 품질만 비교한다.

### 6.3 비대칭 응답

밝기 급증과 어둠은 위험이 대칭이 아니다.

| 상태 | 판정 예 | 동작 |
|---|---|---|
| 포화 위험 | 유효 주변 region의 `p999`가 white level 근접 또는 포화율 초과 | pending 여부와 무관하게 노출 우선 즉시 하향. 노출 안전 하한에서도 남으면 gain 하향 |
| 밝은 하늘/밝은 구름 | 주변 배경 flux 상승 | 고게인을 유지한 채 노출을 먼저 줄임 |
| 잡음 오검출 | 후보 급증 + 주변 Matches/SNR 하락 또는 전 경로 실패가 반복 | gain 한 단계 하향 shadow/적용, 품질 개선 시 유지 |
| 정상 데드밴드 | 배경·포화·솔브 품질 정상 | 유지 |
| 맑은 어두운 하늘 | 주변 match/SNR 존재 | gain 30 우선, 필요 시 노출을 천천히 증가 |
| 어두운 구름/가림 | 최근 성공 뒤 배경과 별 신호가 함께 급락 | 최근 성공 앵커 유지; 노출·게인 상승 금지 |
| 슬루 | IMU 이동량 초과 | 노출 상한 축소 또는 제어 학습 일시 정지 |

하향은 빠르게, 상향은 예를 들어 한 번의 실제 적용당 최대 +0.5 stop으로
제한한다. 이는 갑자기 밝아진 구름에서 포화된 3프레임을 더 만드는 것보다,
잠시 어두운 프레임을 허용하는 편이 안전하기 때문이다.

### 6.4 구름 처리

RAW 배경 하나만으로 어두운 맑은 하늘과 어두운 구름을 완전히 구분할 수
없다. 그래서 다음 신호를 결합한다.

- 최근 90초 안의 성공 솔브 앵커(현행 anchor trust 재사용)
- 주변 region의 catalog Matches, matched-star SNR, RMSE
- full-frame/tile 후보 수와 match 이용률
- SQM의 `cloud_flag`가 fresh할 때만 보조 사용
- region별 `(p99 또는 p999 - p50) / max(1.4826×MAD, epsilon)` 형태의
  빠른 점광원 대비 proxy
- 배경 4분면/메시의 구조적 contrast(밝은 구름의 비균일성)

`cloud_flag`나 주변 솔브는 매 프레임 도착하지 않으므로 안전 방향으로
fail-closed 한다. 최근 성공 직후 배경과 주변 점광원 대비가 동시에 급락하면
노출을 올리지 않고 앵커를 유지한다. 중앙에 달이 있어도 주변부가 정상
match/SNR을 제공하면 그 결과로 계속 제어한다. 반대로 주변 region의 포화는
구름 판정이 없어도 즉시 노출을 하향한다.

## 7. 센서 지연과 pending 제어

프레임마다 새 계산을 한다고 매 프레임 서로 다른 명령을 센서 큐에 넣으면
안 된다. 프레임 N+1, N+2가 아직 이전 설정으로 촬영됐는데 이를 새 요청의
결과로 오인하여 같은 방향으로 계속 보정하기 때문이다.

컨트롤러 상태에 다음을 둔다.

```python
requested = (exposure_us, gain, request_sequence, requested_at)
applied   = (metadata_exposure_us, metadata_gain, frame_sequence)
pending   = requested 값과 applied 값이 허용오차 밖이면 True
```

동작 규칙:

1. 모든 프레임을 측정하고 상태/진단은 갱신한다.
2. 일반 보정은 pending 요청이 실제 메타데이터에서 확인될 때까지 새 값을
   누적하지 않는다.
3. 포화 위험 하향만 pending을 덮어쓸 수 있다. 덮어쓴 최신 안전 목표 하나만
   유지한다.
4. 요청값과 실제값 비교 허용오차는 exposure 2%, gain은 센서 양자화 오차를
   반영한다.
5. 일정 프레임 수 안에 적용되지 않으면 `control_apply_timeout`을 기록하고
   보수적 안전값으로 전환하되 카메라 재시작은 자동 수행하지 않는다.

이 방식은 “매 프레임 평가”를 유지하면서 지연 시스템의 runaway를 막는다.
정상 상태의 유효 조정 속도는 센서 적용 지연당 1회이며, 솔버 주기당 1회인
현재보다 훨씬 빠르다.

### 7.1 atomic control 제출

노출·게인은 한 호출로 제출한다.

```python
camera.set_controls({
    "AeEnable": False,
    "ExposureTime": int(exposure_us),
    "AnalogueGain": float(gain),
})
```

현재 Picamera2는 `ExposureTime`/`AnalogueGain`을 설정할 때 각 수동 mode를
자동 처리하지만 `AeEnable=False`를 함께 두면 의도가 명확하고 구버전 호환도
유지된다. 프레임 시간 상한이 긴 노출을 clamp하지 않도록 카메라 시작 시
`FrameDurationLimits`의 실제 범위도 확인하고 구성해야 한다. 실제 적용값은
항상 메타데이터가 최종 진실이다.

## 8. 상태 전이와 다른 제어 체제

| 이벤트 | 동작 |
|---|---|
| `set_exp:auto_star` 진입 | framewise 상태 reset, 프로파일 gain에서 시작, 최근 유효 메타데이터를 첫 applied 값으로 사용 |
| 수동 exposure 선택 | framewise 즉시 종료, pending 폐기 |
| native AE 진입 | framewise 종료; driver AEGC가 유일한 소유자 |
| SQM `snr` 진입 | framewise actuator 일시 중지, SQM 컨트롤러 소유 |
| SQM 종료 | 새 프레임 메타데이터로 재초기화한 뒤 framewise 재개 |
| 수동 gain 명령 | 초기 구현에서는 gain lock으로 해석하고 노출만 자동 제어; Auto(Star)를 다시 선택하면 lock 해제 |
| 카메라 재시작 | 영속 학습값 사용 금지, 프로파일 기본과 실제 첫 메타데이터에서 시작 |

수동 gain 처리 방식은 UI에 `Auto/Locked`를 표시해야 한다. 사용자가 gain을
명시했는데 다음 프레임에 자동으로 덮어쓰는 동작은 피한다.

## 9. 구현 위치

### 9.1 신규 순수 제어 모듈

`python/PiFinder/auto_exposure_framewise.py`를 제안한다.

- `FrameExposureSample`: 실제 프레임 설정 + RAW 통계
- `ExposureGainTarget`: 요청 exposure/gain + reason + safety 여부
- `AutoStarFrameController`: 데드밴드, pending, 앵커, 구름/포화 상태
- `ExposureGainAllocator`: 총 노광량을 센서별 exposure/gain으로 분배

하드웨어나 shared state를 import하지 않는 순수 모듈로 만들어 리플레이와
단위 테스트가 실제 실행 경로와 같은 코드를 사용하게 한다.

### 9.2 기존 파일 변경점

| 파일 | 변경 |
|---|---|
| `sqm/radiometer.py` | 기존 희소 샘플에 상위 퍼센타일·포화율·실제 gain 메타데이터 추가 |
| `camera_pi.py` | `buffer_count=3, queue=False`; request 조기 release; capture별 radiometry 저장; atomic exposure/gain 적용; 지원 범위 조회 |
| `camera_interface.py` | 무거운 publish보다 먼저 framewise controller 호출; 기존 Auto(Star)는 외부 품질 입력으로 축소; requested/applied 분리 |
| `state.py` | 이미지/RAW/메타데이터에 공통 frame id를 주는 bounded latest-frame envelope 또는 slot generation 추가 |
| `solver.py` | matched 좌표를 제거하기 전에 중앙/주변 region별 AE 품질(SNR proxy, Matches, candidates, RMSE)을 계산; 주변 타일 단독 해는 AE 측정에만 허용 |
| `types/positioning.py` | 프레임 식별자와 압축된 `ExposureQuality` 진단 추가; 포인팅 좌표 계약은 변경하지 않음 |
| `camera_controls.py` | 자동 gain 소유권/lock 상태 정규화가 필요하면 추가 |
| `api_extensions.py` | 실제 applied exposure/gain과 frame id, SensorTimestamp, request wait/held, processing, 추정 drop 노출; 향후 controller 상태 추가 |
| `views/livecam.html` | Auto(Star) 상태에 실제 gain, 제어 이유, pending/apply lag 표시 |
| `sqm/camera_profiles.py` | 검증 후 gain별 pedestal/read-noise 또는 별도 calibration table 연결 |

기존 `auto_exposure_starcount.py`를 즉시 제거하지 않는다. 현장 검증 전에는
feature flag로 old/v2를 A/B할 수 있어야 한다.

```json
"camera_auto_star_framewise": false
```

기본 off로 shadow 운전을 마친 뒤 opt-in, 야간 성공 기준을 통과한 후 기본
on으로 전환한다. 사용자 설정에 세부 임계값을 대량 노출하지 말고, 초기에는
진단 로그와 개발 config만 둔다.

## 10. 관측성과 로그

프레임마다 INFO 로그를 남기면 SD 카드와 CPU를 낭비한다. 상태 변화나 실제
적용 확인 시에만 INFO, 프레임 샘플은 ring buffer/DEBUG로 둔다.

권장 진단 레코드:

```json
{
  "frame": 1234,
  "sensor_timestamp_ns": 987654321000,
  "actual": {"exposure_us": 200000, "gain": 15.0},
  "raw_regions": {
    "C": {"p50": 4095, "sat_frac": 0.31, "usable": false},
    "U": {"p50": 354, "mad": 12, "p999": 811, "sat_frac": 0.0}
  },
  "solve_quality": {
    "source": "peripheral_tile",
    "matches": 9,
    "snr_median": 7.4,
    "candidate_pressure": 3.1
  },
  "scene": "bright_cloud",
  "target": {"exposure_us": 100000, "gain": 15.0},
  "reason": "peripheral_saturation_exposure_down_gain_held",
  "pending": true,
  "applied_after_frames": null,
  "pipeline": {
    "processing_ms": 34.2,
    "frame_duration_ms": 200.0,
    "capture_to_control_ms": 5.1,
    "dropped_since_last": 0
  }
}
```

실제 적용 프레임에서 `applied_after_frames`를 채운다. 최소 지표:

- command→applied 프레임 지연 분포
- gain별 체류 시간
- 시간당 제어 변경 횟수와 방향 반전 횟수
- 포화 프레임 비율
- 배경 데드밴드 체류율
- 중앙/주변별 솔브 성공률, candidates/Matches 분포, matched-star SNR
- gain 변경 전후 같은 주변 region의 품질 점수와 오검출 압력
- 최근 성공 앵커를 구름 때문에 유지한 횟수
- sensor timestamp 간격으로 추정한 frame drop 수와 원인별 drop counter
- capture→control, capture→solver 시작의 p50/p95/max 관측 age
- processing time/frame duration 비율과 Picamera2 request 대기 시간

## 11. 구름 없는 상태에서 가능한 검증

### 11.1 순수 시뮬레이션

센서 모델에 3프레임 제어 지연을 넣는다.

```text
raw_signal[n] = pedestal(gain[n])
              + sky_flux[n] × exposure[n] × gain[n]
              + shot_noise + read_noise(gain[n])
```

필수 시나리오:

- 어두운 맑은 하늘 → gain 30 유지
- 일정 하늘에서 작은 광량 변화 → 데드밴드 유지, 진동 없음
- 밝은 구름 8배 급증 → 최초 측정 직후 하향 제출, 적용 후 포화 탈출
- 중앙 달 포화 + 주변 참별 → 중앙 region 제외, 주변 match/SNR로 제어 유지
- 중앙 달 포화 + 주변 결과 없음 → gain 상향 금지, 포화 안전 하향만 허용
- 고게인 후보 100개/매치 0, 저게인 후보 25개/매치 9 → 저게인 유지
- 고게인 후보 40개/매치 15, 저게인 후보 18개/매치 8 → 고게인 유지
- 어두운 구름 0.1배 급락 → 최근 성공 앵커 유지, 1 s/30 폭주 없음
- 구름 통과 후 복귀 → 이전 앵커로 빠른 복귀
- 슬루 중 IMU 상한 축소
- 노출·gain 양자화와 clamp
- pending 중 연속 stale 프레임 → 명령 누적/적분 windup 없음
- processing time이 frame duration의 0.5×/1×/2×/5×일 때 센서가 멈추지
  않고, backlog가 유한하며, 1× 초과에서 오래된 READY가 폐기됨
- image/RAW/metadata의 frame id가 다르면 solver가 사용하지 않음

### 11.2 저장 RAW 리플레이

기존 exposure sweep TIFF와 stage dump를 시간순으로 재생한다. 각 프레임의
원래 exposure/gain으로 scene flux를 정규화한 뒤, 제어기가 선택한 새 쌍에
대한 예상 RAW 통계를 합성한다. 맑음·광해·포화·구름 코퍼스를 각각
분리하여 old Auto(Star)와 v2의 다음을 비교한다.

- 목표 진입까지 필요한 실제 적용 횟수
- 포화 탈출 시간
- gain 30 고정 대비 예상 헤드룸
- 중앙 달 프레임에서 중앙 전용/주변부 품질 집계의 결과 비교
- 후보 수가 아닌 catalog match/SNR 기준 gain 선택 정확도
- 제어 방향 반전/진동
- 기존 솔브 성공 프레임의 exposure/gain 영역을 벗어나는 비율

### 11.3 실내 실기 테스트

별이 없어도 일정 LED 조명과 밝기 단계 변화로 다음은 검증할 수 있다.

- 이 센서/현재 libcamera에서 exposure와 gain 각각의 실제 적용 지연
- atomic pair가 같은 메타데이터 프레임에 적용되는지
- gain 사다리와 히스테리시스
- 포화 급감 복구
- `FrameDurationLimits`와 긴 노출의 실제 clamp
- manual/native/SQM 전환 시 소유권 충돌 없음
- `buffer_count=1`과 `3/queue=False`의 SensorTimestamp 연속성 및 request-held
  gap 비교
- 인위적으로 worker를 지연시켰을 때 RSS가 계속 증가하지 않고 drop counter만
  증가하는지 확인
- LiveCam/Manager publish를 느리게 했을 때도 capture→control 시간이 영향을
  받지 않는지 확인

이 테스트는 설정을 영속화하지 않는 별도 진단 명령으로 수행하고, 종료 시
원래 모드와 값을 복원해야 한다.

## 12. 다음 맑은 밤의 합격 기준

old/v2를 같은 하늘 구간에서 교대로 운전한다. 최소한 어두운 하늘, 도시광,
얇은 구름, 밝은 구름 구간을 포함한다.

1. 밝기 급증을 본 뒤 **한 캡처 루프 안에 하향 명령이 제출**될 것.
2. 실제 적용은 측정된 센서 지연 범위 안에 있고, 적용 프레임이 메타데이터로
   추적될 것.
3. 95% 이상의 정상 프레임이 포화 안전 범위 안에 있을 것. 정확한 퍼센타일
   임계는 shadow 데이터로 확정한다.
4. 맑은 어두운 구간의 gain 중앙값이 30일 것. 밝은/구름 구간도 고게인이
   주변 match/SNR에 유리하면 유지하고, 포화·오검출 압력이 실제로 감소하며
   솔브 품질이 좋아지는 구간에서만 gain이 낮아질 것.
5. dark cloud에서 exposure/gain이 상한으로 사냥하지 않을 것.
6. old Auto(Star) 대비 솔브 성공률이 악화되지 않고, 첫 성공까지 걸리는
   시간이 단축될 것.
7. gain 경계에서 반복 왕복하는 진동이 시간당 허용 횟수 이내일 것.
8. 카메라 프레임률과 solver 처리량의 회귀가 5% 이내일 것.
9. 중앙에 달을 둔 프레임에서 중앙 SNR이 없어도 주변부 유효 솔브가 있으면
   제어와 좌표 추적이 유지되고, 주변부가 없으면 잘못된 gain 상향을 하지 않을 것.
10. 카메라가 active인 동안 연속 SensorTimestamp 간격에 애플리케이션 처리
    시간만큼의 정지 구간이 생기지 않을 것. 과부하 시 RSS/backlog는 bounded이고
    drop counter가 증가할 것.
11. 정상 부하의 capture→control p95가 한 frame duration보다 작고, 과부하
    상태에서도 solver가 처리하는 입력의 age가 설정 상한을 넘지 않을 것.

## 13. 단계별 구현 순서

### Phase 0 — 계측만 추가

- actual exposure/gain, region별 RAW percentiles/saturation, SensorTimestamp를
  한 프레임 레코드로 연결한다.
- 현행 `buffer_count=1`과 진단용 `3/queue=False`에서 request 보유 시간,
  timestamp 간격, processing time, drop 추정을 A/B한다.
- solver에서 matched 좌표가 제거되기 전에 중앙/주변 AE 품질 요약을 shadow로
  기록한다. 좌표 발행 경로는 바꾸지 않는다.
- 현재 장치의 exposure/gain 적용 지연을 실내에서 측정한다.
- 동작 변경 없음.

### Phase 1 — 무중단 캡처 기반과 순수 컨트롤러

- 검증된 `buffer_count=3, queue=False`, request 조기 release, 공통 frame id를
  적용한다. 처리 과부하 테스트에서 bounded drop을 확인한다.
- `AutoStarFrameController`와 allocator를 구현한다.
- 합성 시나리오 및 기존 RAW 리플레이 테스트를 통과시킨다.
- gain별 pedestal/read-noise 실내 캘리브레이션을 수집한다.

### Phase 2 — shadow mode

- 매 프레임 목표 exposure/gain만 계산하고 실제 카메라에는 쓰지 않는다.
- old Auto(Star)의 실제값과 v2 추천값을 로그로 비교한다.
- 구름 낀 현재 조건에서도 포화/밝은 구름 하향 판단은 검토할 수 있다.

### Phase 3 — opt-in actuator

- `camera_auto_star_framewise=true`에서만 v2가 카메라를 소유한다.
- atomic controls, pending 확인, API/LiveCam 상태 표시를 배선한다.
- 실내 밝기 스텝과 체제 전환 테스트 후 배포한다.

### Phase 4 — 야간 A/B와 기본 전환

- §12 기준을 만족한 뒤 gain 경계, target background, motion cap을 확정한다.
- 한 릴리스 동안 즉시 old 방식으로 돌아갈 수 있게 유지한다.
- 충분한 현장 데이터 뒤 v2를 기본으로 하고 legacy 제거는 별도 결정한다.

## 14. 구현 전 확정할 항목

| 항목 | 권고 초안 | 확정 방법 |
|---|---|---|
| `B_target` | 숫자 하드코딩 보류 | 기존 **주변 성공** RAW의 gain/exposure 정규화 분포 |
| 포화 가드 | region별 p999 + saturated fraction 이중 조건 | 중앙 달/주변 하늘 shadow histogram |
| `t_preferred` | 정지 시 100~200 ms | 서울 스윕 + motion A/B |
| gain 사다리 | 30/15/8/4/2/1, 높은 gain 우선, 프로파일 상한 clamp | driver 실제값 + 주변 Matches/SNR + 후보 압력 |
| gain 히스테리시스 | 약 0.5 stop | 시뮬레이션 방향 반전률 |
| 상향 rate limit | 적용 1회당 최대 +0.5 stop | dark transition 응답/구름 사냥 비교 |
| pending timeout | 실측 적용 지연 + 2프레임 | Phase 0 실기 측정 |
| dark-cloud hold | 최근 성공 90 s 재사용 | cloudy shadow + 다음 맑은 밤 |
| SNR 공간 집계 | 중앙 오염 제외, 주변 match 별 p25/median | 중앙 달 + 주변 별 코퍼스 |
| 수동 gain | Auto(Star) 안에서는 gain lock | UI/API 호환 테스트 |
| Picamera2 request | `buffer_count=3`, `queue=False` | SensorTimestamp 연속성·메모리·drop A/B |
| 처리 과부하 | latest-wins/drop-oldest, 무제한 backlog 금지 | 0.5×~5× 인위 지연 테스트 |

## 15. 외부 근거와 버전

조사 장치(2026-08-28): `python3-picamera2 0.3.31-1`,
`python3-libcamera 0.5.2+rpt20250903-1~bpo12+1`.

- [Picamera2 매뉴얼](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf):
  `ExposureTime`, `AnalogueGain`, `DigitalGain`, `FrameDurationLimits`의 의미와
  captured metadata를 통한 실제값 확인. configuration의 `buffer_count`는
  카메라 request/buffer 세트 수이며 still 기본값은 1, preview는 4, video는
  6이다. 버퍼 증가는 frame drop을 줄일 수 있지만 메모리를 더 사용한다.
- [Picamera2 request queue 구현](https://github.com/raspberrypi/picamera2/blob/main/picamera2/picamera2.py):
  `queue=False` 또는 단일 buffer에서 completed-request cache 길이를 0으로
  두고, 남는 완료 request를 release하여 libcamera로 재순환하는 동작과
  `capture_request(flush=...)`의 timestamp gate 확인 근거.
- [Picamera2 CompletedRequest 구현](https://github.com/raspberrypi/picamera2/blob/main/picamera2/request.py):
  request reference가 0이 될 때 buffer를 allocator/libcamera에 반환하므로,
  request-held 구간을 최소화해야 한다는 근거.
- [libcamera control 정의](https://github.com/raspberrypi/libcamera/blob/main/src/libcamera/control_ids_core.yaml):
  AEGC, manual exposure/gain mode, 메타데이터 의미, frame duration 계약.
- [Raspberry Pi pipeline의 delayed controls](https://github.com/raspberrypi/libcamera/blob/main/src/libcamera/pipeline/rpi/common/pipeline_base.cpp):
  센서별 gain/exposure delay로 delayed control writer를 구성하는 구현.
- [Raspberry Pi 카메라 노출 모드 문서](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/camera/rpicam_options_common.adoc):
  같은 총 노광을 짧은 노출/높은 gain 또는 긴 노출/낮은 gain으로 분배하는
  표준 exposure profile과 frame duration 제한.
- [Picamera2 수동 노출·게인 답변](https://github.com/raspberrypi/picamera2/discussions/592):
  `set_controls`와 프레임 메타데이터를 사용하는 공식 maintainer 설명.
- [Picamera2 제어 지연 논의](https://github.com/raspberrypi/picamera2/discussions/152):
  연속 스트리밍에서 센서/요청 큐 지연 때문에 즉시 적용을 보장할 수 없고,
  메타데이터로 적용 프레임을 식별해야 한다는 maintainer 설명.
- [SEP 공식 문서](https://sep.readthedocs.io/en/stable/):
  공간 가변 background/noise 추정, source extraction, NumPy 배열 기반의 빠른
  aperture photometry를 제공하는 C 기반 라이브러리.
- [SEP aperture photometry 문서](https://sep.readthedocs.io/en/latest/apertures.html):
  `sum_circle`, local background annulus, `err`/`var`, flux error와 detector
  conversion gain의 정의. Picamera2 analogue multiplier와 구분해야 한다.
- [Photutils 공식 detection 문서](https://photutils.readthedocs.io/en/latest/user_guide/detection.html):
  DAOStarFinder 계열 기능 비교 근거. 현재 요구는 이미 설치된 SEP로 충족된다.
- [Photutils 변경 기록](https://github.com/astropy/photutils/blob/main/CHANGES.rst):
  최신 계열의 Python/NumPy 및 Astropy·SciPy·Matplotlib·scikit-image 의존성
  확인 근거.
- [ESA Tetra3](https://github.com/esa/tetra3):
  centroid의 기하 패턴을 별 catalog와 대조하는 기존 solver를 참별 게이트로
  재사용하는 근거.
- [Astrometry.net 설계 논문](https://arxiv.org/abs/0910.2233):
  source detection 뒤 기하학적 catalog 검증을 분리하는 강건한 blind
  astrometry 구조의 근거.
- [SPARCS 동적 노출 제어 논문](https://arxiv.org/abs/2111.10322):
  완료된 최신 노출의 측정값으로 다음 노출을 결정하는 프레임 피드백 구조의
  선행 사례. 과학 목표와 하드웨어가 달라 임계값을 직접 전용하지는 않는다.
