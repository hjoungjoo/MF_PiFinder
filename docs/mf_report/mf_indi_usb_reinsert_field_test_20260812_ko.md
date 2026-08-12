# INDI OnStepX USB 분리·재삽입 현장 테스트

날짜: 2026-08-12
상태: **구현 및 실제 케이블 분리·재삽입 검증 완료**

## 목표와 변경 경계

실제 USB serial 케이블을 분리했다가 다시 연결했을 때 다음 순서를 계측해 가장
작고 안전한 복구 방법을 결정한다.

```text
USB remove 인식
→ serial node 소실
→ INDI/mount-control 상태 변화
→ USB add 인식
→ 같은 by-id node 복원
→ 기존 자동 연결 또는 수동 1회 복구 결과
→ 실제 새 좌표/상태 응답 확인
```

1차 시험에서는 소스를 변경하지 않고 복구 단계를 결정했다. 이어서 승인된
재접속 코드만 구현해 같은 실제 케이블로 2차 시험했다. 기존 수동 이동, GoTo,
guide 처리 경로는 변경하지 않았다. 새 로그 파일은 SD에 만들지 않고 udev 실시간
세션, 기존 `/tmp/indiserver.log`, 서비스 private tmpfs의 `pifinder.log`와
`mount_control_status.json`만 사용했다.

## 시험 전 기준 상태

```text
PiFinder service: active
PiFinder main PID: 40601
indiserver PID: 22627
OnStepX driver PID: 22631
USB chipset: 1a86:7523 QinHeng CH340, kernel driver ch341
device node: /dev/ttyUSB0
stable path: /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
INDI CONNECTION.CONNECT: On
INDI CONNECTION_MODE.CONNECTION_SERIAL: On
INDI DEVICE_BAUD_RATE.115200: On
mount-control state: connected
```

일반 진단 shell은 격리된 `/dev`를 보므로 장치가 없는 것처럼 보일 수 있다.
따라서 물리 장치 존재와 udev 이벤트는 host 권한의 결과만 판정에 사용한다.

## 계측 항목

| 구간 | 기록값 |
|---|---|
| 분리 | udev kernel/remove 시각, by-id/ttyUSB0 소실 시각 |
| 분리 후 | INDI `CONNECT`, mount-control `state`, 마지막 좌표 변화, 오류/timeout |
| 재삽입 | udev kernel/add 시각, 생성된 tty와 by-id가 기존 경로와 같은지 |
| 복구 | device connect 요청 횟수, indiserver/driver/PiFinder PID 변경 여부 |
| 검증 | `CONNECT=On`뿐 아니라 새 좌표·OnStep 상태 응답이 들어오는지 |
| 부작용 | unpark/tracking 강제 변경, 반복 retry, queue block, 수동 이동 상태 오염 |

## 사용자 동작과 관측 순서

1. 감시 시작 후 현재 케이블 연결 상태를 기준선으로 기록한다.
2. 사용자가 USB serial 케이블을 **한 번 분리**하고 즉시 알린다.
3. 재연결하지 않은 채 최소 15초 동안 기존 코드의 감지와 retry를 관측한다.
4. 관측자가 요청하면 사용자가 같은 케이블을 **한 번 재삽입**하고 즉시 알린다.
5. 최대 60초 동안 node 안정화, 기존 자동 복구, PID와 상태 변화를 관측한다.
6. 자동 복구가 없으면 설정을 바꾸지 않는 최소 명령부터 한 단계씩 시험한다.

시험 중에는 수동 이동, GoTo, Sync, Web의 Driver Restart를 실행하지 않는다. 이들
명령이 동시에 들어오면 재접속 원인과 결과를 분리할 수 없다.

## 복구 후보의 시험 순서

자동 복구가 되지 않을 때 다음 순서로 한 단계씩 시험한다.

1. mount-control의 현재 상태를 재평가하고 기존 `connect()` 1회 호출
2. INDI device `CONNECTION`을 disconnect/connect 1회 전환
3. 위 두 방법으로 property가 복원되지 않을 때만 OnStepX driver 1회 재시작
4. indiserver 전체 재시작은 마지막 후보로 남김

앞 단계가 성공하면 뒤 단계는 실행하지 않는다. PiFinder 서비스 전체 재시작은
복구 방법으로 채택하지 않고 비교용 최후 진단으로만 남긴다.

## 합격 판정

- remove/add와 안정 by-id 경로가 각각 한 번 정확히 관측된다.
- 한 분리·재삽입 주기에서 자동 복구 동작은 최대 한 번이다.
- 성공 판정은 cached `CONNECT=On`이 아니라 새 OnStep 응답을 포함한다.
- indiserver/driver/PiFinder의 불필요한 재시작이 없다.
- 기존 park/tracking/slew rate를 임의로 바꾸지 않는다.
- 실패 시 반복 retry storm 없이 원인과 다음 복구 단계가 상태에 남는다.

## 결과 기록

### 1차 분리 결과

```text
kernel remove: monotonic 11965.864826
udev remove 완료: monotonic 11966.000823
PiFinder node absent 관측: 2026-08-12 23:21:59.623 KST
마지막 변화 좌표 관측: 2026-08-12 23:21:57.563 KST
```

분리 후 30초 이상 관측 결과:

- `/dev/ttyUSB0`와 stable by-id node는 정상적으로 사라졌다.
- kernel과 udev는 CH340 `remove`를 각각 한 번 정확히 알렸다.
- INDI `CONNECTION.CONNECT`는 `On`으로 남았다.
- mount-control status는 `state=connected`와 마지막 좌표를 계속 유지했다.
- 좌표 property는 분리 직전 값에서 멈췄지만 오류 상태로 전환되지 않았다.
- PiFinder main, indiserver, OnStepX driver PID는 모두 유지됐다.
- 자동 disconnect/connect 요청이나 프로세스 재시작 로그는 없었다.

따라서 현재 결함은 kernel/udev의 물리 분리 인식 실패가 아니다. mount-control이
설정된 serial node 소실을 감시하지 않고, INDI의 stale `CONNECT=On`과 cached
좌표를 정상으로 취급하는 것이 직접 원인이다.

### 1차 재삽입 결과

```text
kernel add: monotonic 12064.944111
udev add 완료: monotonic 12064.966943
PiFinder node present 관측: 2026-08-12 23:23:37.876 KST
복원 경로: stable by-id 동일, /dev/ttyUSB0 → /dev/ttyUSB1
```

- CH340은 kernel/udev에서 각각 한 번 정상 재등록됐다.
- 동적 tty 번호는 `ttyUSB0`에서 `ttyUSB1`로 바뀌었다.
- stable by-id symlink는 설정과 동일한 이름으로 복원되어 새 tty를 가리켰다.
- 재삽입 후 60초 이상 기다려도 INDI는 stale `CONNECT=On`, mount-control은
  `connected`를 유지했으며 좌표는 분리 직전 값에서 멈췄다.
- 자동 disconnect/connect, driver restart, PiFinder process restart는 없었다.

이 결과는 재삽입 감시에 `/dev/ttyUSB0` 같은 동적 이름을 쓰면 안 되고, 현재
설정의 stable by-id 경로를 기준으로 해야 함을 실장으로 확인한다.

### 기존 CONNECT helper 시험

재삽입 뒤 기존 `connect_indi_onstep_driver()`를 호출한 결과는 다음과 같았다.

```text
ok = true
stdout = LX200 OnStepX already connected
```

stale `CONNECT=On`을 보고 실제 serial open이나 새 응답 검증 없이 성공 처리했다.
따라서 이 helper를 그대로 USB 재삽입 복구에 사용할 수 없다.

### 최소 복구 시험

driver/server/PiFinder를 재시작하지 않고 다음 명령을 한 번만 적용했다.

```text
LX200 OnStepX.CONNECTION.DISCONNECT=On
```

그 뒤에는 mount-control의 기존 자동 연결 동작만 관측했다.

```text
23:26:06.776  INDI CONNECT=Off, 좌표 property 제거 확인
23:26:08.776  mount-control 자동 연결 시작
23:26:10.122  INDI CONNECT=On 확인
23:26:13.511  분리 전 값과 다른 새 좌표 확인
```

결과:

- stale 상태 해제 후 약 2.0초에 기존 자동 연결이 시작됐다.
- 약 3.3초에 `CONNECT=On`, 약 6.7초에 새 좌표가 확인됐다.
- PiFinder PID `40601`, indiserver PID `22627`, driver PID `22631`은 유지됐다.
- 연결 설정은 USB/by-id/115200으로 유지됐다.
- 최종 상태는 `connected`, `Tracking`, `UnParked`였고 좌표 갱신이 계속됐다.
- driver, indiserver, PiFinder 전체 재시작은 필요하지 않았다.

시험 중 반복 `indi_getprop`가 만든 indiserver client 접속/종료 로그는 계측
부하이며 driver 재시작이나 serial 복구 실패가 아니다. 구현 후 평상시에는 이런
고빈도 CLI polling을 사용하지 않는다.

## 최적 복구안

1차 실측에 따른 최소 복구 경로는 다음과 같다.

```text
configured stable by-id 존재
→ node 소실 감지: USB_ABSENT latch, connected=false
→ node가 같은 by-id로 재등장하고 2초 안정
→ INDI device DISCONNECT 1회로 stale 상태 제거
→ device CONNECT 1회
→ connect 이후 새 OnStep 상태/좌표 callback 검증
→ 성공하면 HEALTHY, 같은 분리 주기 추가 복구 금지
```

구현 세부 원칙:

- udev subscriber 또는 1초 path 검사 중 더 단순하고 장애 격리가 쉬운 stable
  by-id path 검사를 mount-control loop에 둔다. CLI polling은 사용하지 않는다.
- node가 없는 동안 현재 무기한 auto-connect loop가 돌지 않게 한다.
- 재등장 debounce가 끝난 뒤 `DISCONNECT → CONNECT`는 한 주기당 정확히 한 번만
  수행한다.
- `CONNECT=On`은 성공 조건이 아니다. connect generation 이후 들어온 새 좌표와
  OnStep 상태 응답을 확인해야 한다.
- 1단계 성공 시 driver/indiserver/PiFinder 재시작을 하지 않는다. property 자체가
  사라진 경우에만 driver 재시작을 별도 2단계 후보로 둔다.

## 구현 전에 해결할 부작용

현재 mount-control `connect()`는 연결할 때마다 위치/시간 sync, unpark, tracking
on을 시도한다. 이번 장비는 분리 전에도 `UnParked/Tracking`이어서 최종 상태가
같았지만, parked 또는 tracking-off 상태에서는 사용자 상태를 바꿀 수 있다.

USB 재삽입 복구 구현은 일반 초기 connect와 분리해야 한다.

- 분리 직전 park/tracking/slew-rate 상태를 snapshot한다.
- 재접속만으로 unpark나 tracking on을 강제하지 않는다.
- 컨트롤러 reset/시간·위치 유실이 확인된 경우에만 신뢰 가능한 값을 재동기화한다.
- 수동 이동/GoTo/guide 명령은 재전송하지 않는다.
- 새 telemetry 확인 전에는 `connected`를 게시하지 않는다.

따라서 권고안은 **서비스나 driver 재시작이 아니라 stable by-id 재등장 후 INDI
device session만 1회 재연결**하는 것이다. 이번 실측에서 가장 빠르고 영향 범위가
작았으며, 2차 구현에는 상태 보존과 fresh-response 검증을 함께 적용했다.

## 구현 후 2차 실제 재삽입 시험

적용 구현:

```text
stable by-id 1초 감시
→ 분리 즉시 usb_absent latch 및 일반 auto-connect 억제
→ 동일 by-id 재등장 후 2초 debounce
→ CONNECTION.DISCONNECT 1회
→ 위치/시간 sync·unpark·tracking-on을 생략한 보존 모드 connect
→ 새 좌표와 새 OnStep Status callback 모두 확인
→ 기존 park/tracking/slew-rate/track-frequency 검증·복원
→ healthy 또는 recovery_failed로 종료
```

실제 계측 결과:

```text
구현 적용 PiFinder PID: 73491
분리 kernel remove: monotonic 13245.166482
분리 udev 완료: monotonic 13245.328742
mount-control usb_absent: 2026-08-12 23:43:18.139 KST

재삽입 kernel add: monotonic 13358.468108
재삽입 udev 완료: monotonic 13358.490010
mount-control return 감지: 2026-08-12 23:45:12.283 KST
session reset 1회 시작: 2026-08-12 23:45:14.287 KST
fresh telemetry 복구 완료: 2026-08-12 23:45:15.956 KST
```

- 분리 상태에서는 `connection_health=usb_absent`, `serial_present=false`,
  `recovery_attempt=0`이 유지됐고 반복 연결 시도는 없었다.
- 재삽입 때 같은 by-id 경로가 `/dev/ttyUSB0`을 가리켰고, 2초 debounce 뒤 session
  reset이 정확히 한 번 실행됐다.
- reset 시작부터 fresh telemetry 성공까지 약 1.67초, return 감지부터 최종
  성공까지 약 3.67초였다.
- 성공은 cached `CONNECT=On`이 아니라 새 `EQUATORIAL_EOD_COORD`와 새
  `OnStep Status` callback 두 가지로 확인했다.
- 최종 상태는 `connection_health=healthy`, `recovery_attempt=1`,
  `recovery_reason=usb_reinsert`, `connected`, `Unparked`, slew rate 9였고 좌표가
  계속 갱신됐다.
- PiFinder PID 73491, indiserver PID 22627, OnStepX driver PID 22631은 시험 전후
  동일했다. driver/server/service restart는 없었다.
- USB/by-id/115200 설정 파일의 hash, mtime, 크기도 바뀌지 않았다.

분리 직후 이미 큐에 있던 마지막 좌표 callback이 상태 파일의 최상위 `state`를
잠깐 `connected`로 덮는 표시 경합도 시험에서 발견했다. `connected=false`인 동안
좌표 callback은 좌표 generation만 기록하고 상태를 쓰지 않도록 보완했으며, 늦은
구형 client disconnect callback도 client generation이 다르면 무시하도록 했다.

## 자동 테스트 결과

```text
python/tests/test_mountcontrol_indi.py: 78 passed
mountcontrol + sys_utils + config 집중 회귀: 154 passed
compileall: passed
git diff --check: passed
```

테스트는 같은 분리 주기당 1회 복구, 초기 부팅 시 장치 부재와 실제 재삽입의 구분,
실패·예외 latch, fresh telemetry 필수 조건, 위치/시간 sync·unpark·tracking 강제
금지, stale client callback 차단, 늦은 좌표 callback 상태 보호를 포함한다.

늦은 좌표 callback 보완까지 반영한 최종 소스는 23:52:04에 서비스에 다시
적재했다. 새 PiFinder PID는 77695이고 기존 indiserver PID 22627과 OnStepX driver
PID 22631은 유지됐다. 최종 상태는 `healthy`, `serial_present=true`, stable
by-id, USB, 115200, `connected`, `Tracking`, `Unparked`, slew rate 9이며 설정 파일
hash와 mtime도 1차 시험 전 값과 동일하다. 이 마지막 서비스 재기동은 소스 적재
확인용이며, 앞의 자동 재삽입 합격 결과에는 포함하지 않는다.
