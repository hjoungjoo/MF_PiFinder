# MF PiFinder Backlash Measurement Flow

이 문서는 INDI > Settings > Backlash의 자동 측정 모드인
`compass_goto_loop`의 동작을 정리한다.

현재 이 모드는 다시 INDI GoTo 이동을 사용한다. OnStepX 드라이버의
`GUIDE_RATE` 지원은 드라이버 기능으로 유지하지만, Auto Backlash는 더 이상
`GUIDE_RATE`를 변경하지 않고 `TELESCOPE_TIMED_GUIDE_*` 명령도 보내지 않는다.

## 핵심 원칙

- 한 번에 한 축만 active 축으로 테스트한다.
- Alt/Az 마운트는 `AZ`를 먼저 테스트하고 그 다음 `ALT`를 테스트한다.
- EQ 마운트는 `RA`를 먼저 테스트하고 그 다음 `DEC`를 테스트한다.
- 각 축은 고정 시작점 `S`와 active 축만 offset된 목표점 `T`를 사용한다.
- `S`와 `T` 사이에서 inactive 축 좌표는 바꾸지 않는다.
- PiFinder는 각 GoTo가 완료되고 안정화된 뒤 마운트 좌표와 IMU 좌표를 기록한다.
- 마운트 이동량은 직전 안정화 마운트 readback에서 현재 안정화 마운트 readback까지의 차이다.
- IMU 이동량은 직전 안정화 IMU pose에서 현재 안정화 IMU pose까지의 차이다.
- signed motion error는 `마운트 이동량 - IMU 이동량`이다.
- 백래시 후보값은 signed motion error의 절대값을 arc-second로 변환한 값이다.
- 마운트와 IMU 이동량 차이가 1도 이상인 leg는 IMU 튐 또는 저장 시점 문제로 보고 통계에서 제외한다.
- 남은 후보값을 정렬한 뒤 하위 30%와 상위 30%를 버리고 가운데 40% 평균을 추천값으로 사용한다.

OnStep/INDI는 GoTo 후 tracking을 자동으로 켤 수 있으므로 PiFinder는 테스트
시작 전 tracking을 끄고, 각 GoTo leg가 끝난 뒤에도 다시 tracking을 끈다.

GoTo 완료 판정은 OnStepX 펌웨어의 near-destination refinement를 고려한다.
PiFinder는 첫 idle 샘플만으로 leg를 완료 처리하지 않고, INDI idle 상태와
좌표 readback이 안정 시간 동안 유지되는지 확인한다. 또한 OnStep status
text를 읽을 수 있으면 `:GU#` 응답에 `N`(`No goto`)이 다시 나타날 때까지
기다린다. 이 처리는 펌웨어가 근처 목표점에서 잠깐 settle wait를 한 뒤 최종
미세 접근을 다시 수행하는 동안 IMU 좌표를 기록하는 문제를 막기 위한 것이다.

## 기본값

```text
offset = 2.0도
기본 반복 횟수 = 10회, 웹 UI에서 1~50회로 변경 가능
GoTo 완료 전 stable idle/position 확인 = 4.0초
GoTo 완료 후 안정화 대기 = 0.5초
각 return leg 전 대기 = 1.0초
GoTo timeout = 180초
```

## 축별 목표점

### Alt/Az 마운트

시작점이 `Alt 10, Az 20`이고 offset이 2도이면 다음처럼 테스트한다.

```text
AZ 축 테스트:
  S_az = Alt 10, Az 20
  T_az = Alt 10, Az 22

ALT 축 테스트:
  S_alt = Alt 10, Az 20
  T_alt = Alt 12, Az 20
```

PiFinder는 이 Alt/Az 목표점을 RA/Dec로 변환한 뒤 INDI GoTo를 보낸다.

### EQ 마운트

시작점이 `RA 100, DEC 20`이고 offset이 2도이면 다음처럼 테스트한다.

```text
RA 축 테스트:
  S_ra = RA 100, DEC 20
  T_ra = RA 102, DEC 20

DEC 축 테스트:
  S_dec = RA 100, DEC 20
  T_dec = RA 100, DEC 22
```

## 순서도

```text
[시작]
  |
  v
[필요시 IMU compass/NDOF 모드 켜기]
  |
  v
[MAG calibration = 3 대기]
  |
  v
[사용자가 Continue Motion Test 선택]
  |
  v
[INDI mount 연결]
  |
  v
[Unparked 상태 확인]
  |
  v
[tracking 상태 읽기 및 tracking Off]
  |
  v
[현재 IMU Alt/Az로 mount 좌표 sync]
  |
  v
[각 active 축 반복]
  |
  +--> [현재 mount 위치 읽기]
  |
  +--> [anti-offset init target 계산]
  |
  +--> [init target으로 GoTo, 완료 대기, tracking Off, 0.5초 안정화]
  |
  +--> [mount에서 실제 시작점 S 읽기]
  |
  +--> [S + active-axis offset으로 고정 목표점 T 계산]
  |
  +--> [initial mount/IMU 좌표 기록]
  |
  +--> [T로 warm-up GoTo, tracking Off, 안정화, offset initial 기록]
  |
  +--> [N회 반복]
         |
         +--> [1.0초 대기]
         +--> [S로 GoTo, tracking Off, 안정화, return leg 기록]
         +--> [T로 GoTo, tracking Off, 안정화, offset leg 기록]
  |
  v
[레코드 필터링 및 추천값 계산]
  |
  v
[mount 정지, 테스트 완료 시 원래 tracking이 On이면 tracking 복구]
```

## 기록되는 값

각 leg는 디버깅을 위해 다음 값을 남긴다.

- `mount_start_*`: 직전 안정화 마운트 readback.
- `mount_end_*`: 현재 GoTo 완료 후 마운트 readback.
- `command_start_*`: leg의 명목상 명령 시작점.
- `target_*`: leg의 GoTo 목표점.
- `imu_start_*`: 직전 안정화 IMU pose.
- `imu_end_*`: 현재 GoTo 완료 후 IMU pose.
- `mount_delta_*`: `mount_end - mount_start`.
- `imu_delta_*`: `imu_end - imu_start`.
- `motion_difference_*`: `mount_delta - imu_delta`.
- `motion_backlash_*_arcsec`: 축별 절대 백래시 후보값.

웹 UI에는 짧은 요약만 표시한다. 상세 레코드는 mount-control status와 로그에서
디버깅용으로 확인할 수 있다.
