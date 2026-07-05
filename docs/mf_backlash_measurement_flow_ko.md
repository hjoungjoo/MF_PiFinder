# MF PiFinder Backlash Measurement Flow

이 문서는 INDI > Settings > Backlash의 자동 측정 모드 중
`compass_goto_loop` 방식을 설명한다. 내부 모드 이름은 기존 호환성을 위해
`compass_goto_loop`로 유지하지만, 실제 이동 명령은 GoTo가 아니라 INDI 표준
timed pulse guide를 사용한다.

## 핵심 원칙

- 계산 방식은 기존과 동일하게 유지한다.
- 바뀌는 것은 마운트를 움직이는 명령 방식뿐이다.
- 기존 방식은 마운트 타입에 따라 두 축을 동시에 `+offset` 방향으로 움직였다.
- 수정 방식은 한 번에 한 축만 움직인다.
- GoTo는 OnStep/INDI 드라이버에서 tracking을 자동으로 켤 수 있으므로 백래시
  측정 이동에는 사용하지 않는다.
- 실제 이동은 `TELESCOPE_TIMED_GUIDE_NS/WE` timed pulse guide로 수행한다.
- Alt/Az 마운트는 `AZ` 축 테스트와 `ALT` 축 테스트를 분리한다.
- EQ 마운트는 `RA` 축 테스트와 `DEC` 축 테스트를 분리한다.
- active 축은 pulse guide 시간으로 offset만큼 움직이고, inactive 축은 명령하지 않는다.
- 각 pulse 이동의 마운트 이동량은 기존처럼
  `pulse 완료 후 실제 마운트 좌표 - 직전 안정화 레코드의 실제 마운트 좌표`로 계산한다.
- 각 이동의 IMU 이동량도 기존처럼
  `이동 완료 후 IMU 좌표 - 이동 직전 IMU 좌표`로 계산한다.
- signed 이동 차이는 기존처럼 `마운트 이동량 - IMU 이동량`이다.
- 백래시 후보값은 기존처럼 signed 이동 차이의 절대값을 arc-second로 변환한 값이다.
- signed 이동 차이가 1도 이상인 leg는 기존처럼 IMU 이상 동작 또는 저장 시점 문제
  가능성이 있으므로 통계에서 제외한다.
- 남은 값은 기존처럼 정렬한 뒤 하위 30%와 상위 30%를 버리고,
  가운데 40%의 평균을 추천값으로 사용한다.

즉, 계산/필터/추천값 산출은 바꾸지 않는다. 검증성을 높이고 tracking 자동 ON을
피하기 위해 실제 이동 명령만 축별 timed pulse guide로 바꾼다.

## Pulse Guide 기본값

```text
offset = 2.0도
INDI GUIDE_RATE = 96x sidereal (OnStepX maps this to rate selector 8 / Half-Max)
2도 pulse 시간 = 약 4.99초
기본 반복 횟수 = 10회 (웹 UI에서 1~50회로 변경 가능)
pulse 종료 후 안정화 대기 = 0.5초
반복 사이 대기 = 1.0초
```

이동 시간은 다음 식으로 계산한다.

```text
duration_seconds = offset_deg / (rate_multiplier * 15.041067 / 3600)
```

PiFinder는 pulse guide 명령 자체는 `TELESCOPE_TIMED_GUIDE_*`로 유지하고,
테스트 시작 전에 INDI `GUIDE_RATE`를 96x sidereal로 설정한다. OnStepX
드라이버는 이 값을 OnStep의 rate selector 8, 즉 Half-Max로 매핑한다.
실제 속도는 마운트 설정에 따라 달라질 수 있어 PiFinder는 테스트 시간을
계산할 때 96x sidereal로 추정한다.

주의: OnStep 펌웨어에서 `GUIDE_SEPARATE_PULSE_RATE`가 켜져 있고
`pulseRateSelect`가 1x 이하로 제한되어 있으면 `TELESCOPE_TIMED_GUIDE_*`
명령은 96x로 동작하지 않는다. PiFinder는 `GUIDE_RATE` 설정 후 실제
readback을 확인하며, 요청값이 적용되지 않으면 테스트를 시작하지 않는다.

## 축별 기대 이동점

### Alt/Az 마운트

Alt/Az 모드에서는 OnStep Axis1/Axis2 이름에 맞춰 다음 순서로 테스트한다.

```text
Axis1 = AZ
Axis2 = ALT
```

예를 들어 현재 시작점이 `Alt 10, Az 20`, offset이 2도이면:

```text
AZ 축 테스트:
  S_az = Alt 10, Az 20
  T_az = Alt 10, Az 22

ALT 축 테스트:
  S_alt = Alt 10, Az 20
  T_alt = Alt 12, Az 20
```

AZ 축 테스트에서는 AZ 방향 pulse만 보내고, ALT 축 테스트에서는 ALT 방향 pulse만
보낸다. inactive 축의 실제 변화량은 계산에서 제거하지 않고 기존 레코드에 그대로
남겨 축 간 기계적 영향, 좌표 변환 문제, IMU 측정 문제를 확인하는 데 사용한다.

### EQ 마운트

EQ 모드에서는 다음 순서로 테스트한다.

```text
Axis1 = RA
Axis2 = DEC
```

예를 들어 현재 시작점이 `RA 100, DEC 20`, offset이 2도이면:

```text
RA 축 테스트:
  S_ra = RA 100, DEC 20
  T_ra = RA 102, DEC 20

DEC 축 테스트:
  S_dec = RA 100, DEC 20
  T_dec = RA 100, DEC 22
```

RA 축 테스트에서는 RA 방향 pulse만 보내고, DEC 축 테스트에서는 DEC 방향 pulse만
보낸다.

## 순서도

```text
[시작]
  |
  v
[IMU Compass 설정 확인]
  |
  +-- Off 또는 NDOF 미사용 --> [Compass On 설정 안내] -> [PiFinder 재시작 필요] -> [종료]
  |
  v
[IMU MAG calibration = 3 대기]
  |
  +-- 미완료 --> [사용자가 기기를 움직여 캘리브레이션] -> [Continue 대기]
  |
  v
[INDI 연결]
  |
  v
[마운트 상태 확인]
  |
  +-- Parked --> [Unpark 필요 오류] -> [종료]
  |
  +-- Tracking On --> [Tracking Off]
  |
  v
[INDI GUIDE_RATE = 96x (OnStepX Half-Max) 설정]
  |
  v
[현재 IMU 좌표 읽기]
  |
  v
[현재 IMU 좌표를 마운트 좌표로 Sync]
  |
  +-- 동작:
  |      IMU Alt/Az를 현재 시간/위치의 RA/DEC로 변환
  |      INDI Sync로 마운트 좌표를 IMU 방향에 맞춤
  |      Sync 후 켜질 수 있는 Tracking을 다시 Off로 설정
  |
  v
[현재 마운트 좌표 읽기]
  |
  v
[테스트할 축 목록 결정]
  |
  +-- Alt/Az 마운트: AZ -> ALT
  |
  +-- EQ 마운트: RA -> DEC
  |
  v
<각 active 축에 대해 반복>
  |
  v
[active 축 안전 시작점 준비]
  |
  +-- Alt/Az 마운트:
  |      active = AZ 이면 logical west pulse로 Az -offset 이동
  |      active = ALT 이면 logical south pulse로 Alt -offset 이동
  |
  +-- EQ 마운트:
         active = RA 이면 logical west pulse로 RA -offset 이동
         active = DEC 이면 logical south pulse로 DEC -offset 이동
  |
  v
[Pulse Guide INIT]
  |
  v
[pulse 시간 대기 + 0.5초 안정화]
  |
  +-- 저장하지 않음:
  |      INIT 이동은 active 축을 안전한 테스트 시작점으로 옮기는 준비 동작이다.
  |      백래시 통계에는 포함하지 않는다.
  |
  v
[현재 마운트 좌표 다시 읽기]
  |
  v
[active 축 고정 명령점 S/T 계산]
  |
  +-- Alt/Az 마운트:
  |      active = AZ:
  |        S = 현재 Alt, 현재 Az
  |        T = 현재 Alt, 현재 Az + offset 예상점
  |
  |      active = ALT:
  |        S = 현재 Alt, 현재 Az
  |        T = 현재 Alt + offset 예상점, 현재 Az
  |
  |      실제 명령은 GoTo가 아니라 logical east/north 또는 west/south pulse
  |
  +-- EQ 마운트:
         active = RA:
           S = 현재 RA, 현재 DEC
           T = 현재 RA + offset 예상점, 현재 DEC

         active = DEC:
           S = 현재 RA, 현재 DEC
           T = 현재 RA, 현재 DEC + offset 예상점

         실제 명령은 GoTo가 아니라 logical east/north 또는 west/south pulse
  |
  v
[초기 레코드 저장]
  |
  +-- 저장 내용:
  |      active_axis
  |      현재 마운트 RA/DEC 및 Alt/Az
  |      현재 IMU Alt/Az 및 변환 가능하면 IMU RA/DEC
  |      label = initial <active axis>
  |
  v
[Warm-up: S -> T]
  |
  v
[0.5초 안정화 후 마운트/IMU 레코드 저장]
  |
  +-- 계산에는 사용하지만 통계에서는 제외:
  |      active_axis 유지
  |      label = offset initial <active axis>
  |      direction = offset
  |      warmup = true
  |
  v
<active 축 반복 N회, 기본 10회>
  |
  +--> [Return leg: T에서 S 방향으로 active 축 -offset pulse]
  |       |
  |       v
  |     [직전 안정화 레코드 A를 시작값으로 사용]
  |       |
  |       v
  |     [logical west 또는 south pulse]
  |       |
  |       v
  |     [pulse 시간 대기 + 0.5초 안정화]
  |       |
  |       v
  |     [마운트/IMU 레코드 저장 B]
  |       |
  |       v
  |     [기존 계산 그대로 수행]
  |       - mount_delta = 실제 마운트 종료 좌표(B) - 직전 안정화 레코드(A)의 마운트 좌표
  |       - imu_delta = 현재 IMU(B) - 직전 안정화 레코드(A)의 IMU 좌표
  |       - signed_error = mount_delta - imu_delta
  |
  +--> [Offset leg: S에서 T 방향으로 active 축 +offset pulse]
          |
          v
        [직전 안정화 레코드 B를 시작값으로 사용]
          |
          v
        [logical east 또는 north pulse]
          |
          v
        [pulse 시간 대기 + 0.5초 안정화]
          |
          v
        [마운트/IMU 레코드 저장 A]
          |
          v
        [기존 계산 그대로 수행]
          - mount_delta = 실제 마운트 종료 좌표(A) - 직전 안정화 레코드(B)의 마운트 좌표
          - imu_delta = 현재 IMU(A) - 직전 안정화 레코드(B)의 IMU 좌표
          - signed_error = mount_delta - imu_delta
  |
  v
[다음 active 축으로 이동]
  |
  v
[모든 축 완료 후 기존 통계 계산]
  |
  +-- Warm-up 및 IMU heading 불량 leg 제외
  +-- 마운트-IMU 차이 1도 이상 leg 제외
  +-- 방향별/축별 값들을 작은 값부터 정렬
  +-- 하위 30% 제거
  +-- 상위 30% 제거
  +-- 남은 가운데 40% 평균을 추천값으로 사용
  +-- 축 이동 방향별 통계:
      Alt/Az: AZ+, AZ-, ALT+, ALT-
      EQ: RA+, RA-, DEC+, DEC-
  |
  v
[상세 leg 데이터와 요약값 표시 및 CSV 추출]
  |
  v
[원래 INDI GUIDE_RATE 복구]
  |
  v
[Tracking 원래 상태 복구]
  |
  v
[종료]
```

## CSV에서 확인할 주요 필드

- `active_axis`: 현재 leg가 의도적으로 움직인 축. Alt/Az는 `az` 또는 `alt`,
  EQ는 `ra` 또는 `dec`.
- `movement_frame`: `altaz` 또는 `radec`.
- `command_start_altitude`, `command_start_azimuth`: 해당 leg의 고정 명령 출발점.
  계산 확인용이며 실제 이동량 계산에는 사용하지 않는다.
- `target_altitude`, `target_azimuth`: 해당 leg의 고정 명령 목표점.
- `mount_start_altitude`, `mount_start_azimuth`: 현재 leg의 시작 레코드 좌표.
  pulse 직전 새 샘플이 아니라 직전 pulse 완료 후 안정화되어 저장된 레코드다.
- `mount_end_altitude`, `mount_end_azimuth`: pulse 이동 완료 후 실제 마운트 readback.
- `mount_delta_altitude`, `mount_delta_azimuth`: 실제 마운트 종료 좌표와 직전
  안정화 레코드의 실제 마운트 시작 좌표의 차이.
- `imu_delta_alt`, `imu_delta_az`: 이동 전후 IMU 차이.
- `motion_difference_alt_arcsec`, `motion_difference_az_arcsec`: signed 이동 차이.
- `motion_backlash_alt_arcsec`, `motion_backlash_az_arcsec`: signed 차이의 절대값
  기반 백래시 후보.
- `raw_estimated_arcsec`: combined signed 차이의 크기. 1도 이상이면 통계 제외.
- `motion_difference_threshold_rejected`: 1도 threshold로 제외되었는지 여부.
- `motion_difference_threshold_rejected_axes`: threshold를 넘긴 축.
- `direction_stats.*.recommended_trimmed_mean`: 기존 방식 그대로, 1도 threshold
  제외 후 하위 30%와 상위 30%를 버리고 남긴 가운데 값들의 평균.
- `axis_direction_stats.*.recommended_trimmed_mean`: 기존 방식 그대로, 실제 active
  축 이동 방향별 추천값. Alt/Az 마운트는 `AZ+`, `AZ-`, `ALT+`, `ALT-`,
  EQ 마운트는 `RA+`, `RA-`, `DEC+`, `DEC-`로 분리된다.

Alt/Az의 Az와 EQ의 RA는 0/360도 경계를 넘을 수 있으므로 항상 최단 각도 차이를 사용한다.
