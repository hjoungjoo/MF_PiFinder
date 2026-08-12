# INDI OnStepX 연결 설정 불일치 수정·실장 검증

날짜: 2026-08-12
범위: 설정 불일치 해소만 검증. USB 재삽입 자동 재접속, 통신 health 감시,
수동 이동 로직은 변경하거나 시험하지 않음.

## 결론

INDI가 실제 사용하는 USB 설정과 PiFinder `config.json`의 누락 상태를 재현했고,
수정 후 INDI live 설정이 PiFinder mirror로 정확히 한 번 동기화되었다. 드라이버
transport를 덮어쓰지 않았으며 서비스는 `connected` 상태로 복귀했다.

## 수정 전 실측

INDI live와 `~/.indi/LX200 OnStepX_config.xml`은 서로 같았다.

```text
connection_type = usb
serial_port = /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
serial_baud = 115200
```

반면 PiFinder `config.json`에는 다음 7개 설정이 모두 없었다.

```text
onstep_connection_type
onstep_serial_port
onstep_serial_baud
onstep_network_host
onstep_network_port
mount_control_indi_host
mount_control_indi_port
```

따라서 UI나 직접 LX200 기능이 코드 기본값인 network/9600을 볼 수 있고, 실제
INDI driver는 저장 XML의 USB/115200을 쓰는 이중 소스 불일치가 존재했다.

## 자동 테스트

실행:

```text
cd python
pytest -q tests/test_config.py tests/test_sys_utils.py tests/test_mountcontrol_indi.py
```

결과:

```text
146 passed in 1.17s
```

검증 항목:

- 여러 연결 키의 lock 기반 atomic batch 저장과 다른 프로세스 변경 보존
- default fallback과 실제 저장값 구분
- INDI live USB/network property 파싱
- INDI XML USB 설정 파싱
- active transport 필드만 비교
- live readback이 요청 mode/port/baud와 다르면 `CONFIG_SAVE`를 실행하지 않음
- `CONFIG_SAVE` 실패 시 PiFinder 설정을 성공 처리하지 않음
- live INDI가 있으면 driver에 재적용하지 않고 PiFinder mirror만 import
- live/XML이 없을 때만 PiFinder mirror를 1회 적용하고 readback 검증

전체 suite도 실행했으며 결과는 `1744 passed, 177 skipped, 11 failed`였다.
11건은 이번 변경 파일과 무관한 기존 테스트/구현 불일치다.

- `test_multiproclogging.py` 4건: 문자열 log path에 `.parent`를 호출
- `test_radec_entry.py` 6건: 현재 생성자와 과거 테스트 인자 불일치
- `test_ui_modules.py` 1건: `DummyGuideScreen` smoke coverage 목록 누락

범위를 설정 불일치 수정으로 제한하기 위해 이 세 영역은 변경하지 않았다.

## 실장 검증

수정 소스로 `pifinder.service`를 재시작했다. 시작 reconciliation은
`indi_live`를 선택했고 `config.json`에는 다음 5개 키만 새로 기록되었다.

```text
mount_control_indi_host = localhost
mount_control_indi_port = 7624
onstep_connection_type = usb
onstep_serial_port = /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
onstep_serial_baud = 115200
```

비활성 network 설정은 만들어 내지 않았으며, 재시작 전후 비교에서 다른 기존
설정 변경은 없었다. tmpfs 상태 파일의 최종 핵심 값은 다음과 같았다.

```json
{
  "connection_config_valid": true,
  "connection_config_reconciled": true,
  "connection_config_source": "indi_live",
  "onstep_connection_type": "usb",
  "onstep_serial_baud": 115200,
  "onstep_serial_port": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
  "state": "connected"
}
```

서비스 상태는 `active`였고, tmpfs 로그에서 mirror reconciliation 뒤 자동 INDI
연결 완료를 확인했다. 이번 변경은 별도 SD 로그를 추가하지 않는다.

동일 설정으로 최신 소스를 다시 시작한 결과
`connection_config_reconciled=false`, `state=connected`였다. 재시작 전후
`config.json`의 mtime(`1786542273`), 크기(`1625` bytes), SHA-256
(`dc93e1aa23307dcda0f506f3e19508dd155dc52e70045548494a9cc24e78a243`)이
모두 같아, 이미 일치할 때 SD에 반복 기록하지 않음을 확인했다.

## 남은 범위

USB 케이블 분리·재삽입 1회 복구와 위치·시간을 포함한 지속 통신 health 감시는
별도 과제로 남겨 두었다. 해당 정책은 개발 문서 승인 뒤 순차 구현해야 한다.
