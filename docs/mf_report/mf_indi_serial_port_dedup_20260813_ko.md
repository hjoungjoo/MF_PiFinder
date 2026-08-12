# INDI USB Serial 포트 목록 중복 제거

날짜: 2026-08-13
상태: **구현·자동 테스트·실장 검증 완료**

## 현상

Web UI의 `INDI > LX200 OnStepX Driver Connection > USB Serial Port`에서 같은
`ttyUSB` 장치가 두 개처럼 보였다.

## 실장 확인

현재 장치의 host 경로는 다음과 같다.

```text
/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 -> ../../ttyUSB1
/dev/ttyUSB1                                     (character device)
```

기존 `list_onstep_serial_ports()` 결과:

```json
[
  {
    "path": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
    "resolved": "/dev/ttyUSB1"
  },
  {
    "path": "/dev/ttyUSB1",
    "resolved": "/dev/ttyUSB1"
  }
]
```

두 항목은 실제 포트 두 개가 아니라 동일한 character device의 stable alias와
dynamic 이름이다. 앞선 USB 재삽입 시험에서 tty 번호가 `ttyUSB0 → ttyUSB1`로
바뀌었으므로 사용자가 관측한 `ttyUSB0` 중복과 현재 `ttyUSB1` 중복은 같은
원인이다.

## 직접 원인

현재 함수는 다음 경로 pattern을 각각 수집한 뒤 **문자열 path를 key**로 사용한다.

```text
/dev/serial/by-id/*
/dev/ttyUSB*
/dev/ttyACM*
```

by-id와 tty 경로 문자열은 다르므로 실제 `realpath`가 같아도 두 항목이 남는다.

## 수정 정책

```text
후보 path 수집
→ realpath 계산
→ 같은 realpath끼리 그룹화
→ by-id 우선 대표 선택
→ by-id가 없으면 ttyUSB/ttyACM 유지
→ 대표 항목만 Web UI에 반환
```

이 수정은 표시 후보만 정리한다. INDI port, baud, CONFIG_SAVE, PiFinder mirror,
mount-control 연결 및 USB 재삽입 복구 상태는 변경하지 않는다.

## 시험 항목

- 같은 target의 by-id + ttyUSB가 by-id 한 항목으로 합쳐진다.
- 서로 다른 ttyUSB target 두 개는 두 항목으로 유지된다.
- by-id가 없는 ttyACM/ttyUSB는 사라지지 않는다.
- 반환 순서가 안정적이다.
- 기존 sys_utils/Web 관련 회귀 테스트를 통과한다.

## 구현 결과

`list_onstep_serial_ports()`가 path 문자열 대신 resolved target별로 후보를
그룹화하도록 변경했다. 같은 target에서는 다음 우선순위로 대표 경로를 선택한다.

```text
/dev/serial/by-id/...
→ /dev/serial/by-path/...
→ /dev/ttyUSB* 또는 /dev/ttyACM*
```

현재 수집 pattern에는 by-id와 ttyUSB/ttyACM이 포함되며, by-path 우선순위는 향후
자동 탐색 후보가 확장될 때도 같은 함수를 안전하게 사용할 수 있도록 정의했다.

## 검증 결과

자동 테스트:

```text
python/tests/test_sys_utils.py: 65 passed
sys_utils + fake + config + Web static + mountcontrol 집중 회귀: 189 passed
ruff: passed
git diff --check: passed
```

추가 테스트는 다음을 고정한다.

- 같은 `/dev/ttyUSB0`을 가리키는 by-id와 ttyUSB alias가 by-id 하나로 합쳐짐
- 서로 다른 ttyUSB target과 by-id 없는 ttyACM fallback은 그대로 유지됨

실제 장비의 수정 후 결과:

```json
[
  {
    "path": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
    "label": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 (/dev/ttyUSB1)",
    "resolved": "/dev/ttyUSB1"
  }
]
```

기존 두 항목이 stable by-id 한 항목으로 정리됐다. 확인 중 INDI 상태는 계속
`CONNECT=On`, port는 같은 by-id, baud는 115200으로 유지됐으며 설정 적용,
disconnect 또는 `CONFIG_SAVE`는 실행하지 않았다.

## 서비스 반영 확인

수정 코드를 실제 Web UI 프로세스에 반영하기 위해 `pifinder` 서비스만 재시작했다.
INDI 서버와 드라이버 설정을 변경하거나 별도로 재시작하지 않았다.

```text
pifinder.service: active
ActiveEnterTimestamp: 2026-08-13 00:23:55 KST
MainPID: 86117
```

재시작 후 mount-control 상태도 다음과 같이 정상 유지됐다.

```text
connection_health: healthy
connected: true
connection_type: USB
serial_present: true
serial_stable: true
port: /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
baud: 115200
park: Unparked
tracking: On
slew_rate: 9
```

따라서 브라우저에서 INDI 탭을 새로 고치면 동일 장치를 뜻하던 두 항목 대신
stable by-id 항목 하나만 표시되어야 한다.
