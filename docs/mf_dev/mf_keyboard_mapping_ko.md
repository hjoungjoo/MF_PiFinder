# MF_PiFinder 키보드 매핑

이 문서는 `mf_pifinder` 브랜치의 USB/Bluetooth 키보드와 GPIO 키패드 입력
매핑을 간단히 정리한다.

## USB/Bluetooth 키보드

| 키 | PiFinder 입력 |
| --- | --- |
| 방향키 | `LEFT`, `UP`, `DOWN`, `RIGHT` |
| Enter / Keypad Enter | `SQUARE` |
| Esc | `LEFT` |
| Backspace | `MINUS` |
| `=` / Keypad `+` | `PLUS` |
| `-` / Keypad `-` | `MINUS` |
| 숫자 `1-9` / Keypad 숫자 | 숫자 press/release |
| `0` / Keypad `0` | 현재 이벤트가 전달되지 않음 |
| Space | 공백 문자 |
| `a-z` | 영문 소문자 |
| `Shift + a-z` | 영문 대문자 |

## Alt 조합

| 키 | PiFinder 입력 |
| --- | --- |
| `Alt + 방향키` | `ALT_LEFT`, `ALT_UP`, `ALT_DOWN`, `ALT_RIGHT` |
| `Alt + =` / `Alt + Keypad +` | `ALT_PLUS` |
| `Alt + -` / `Alt + Keypad -` | `ALT_MINUS` |
| `Alt + 0` / `Alt + Keypad 0` | `ALT_0` |
| `Alt + Enter` / `Alt + Keypad Enter` | `ALT_SQUARE` |

## 길게 누르기

1초 이상 누르면 long key로 처리된다.

| 키 | PiFinder 입력 |
| --- | --- |
| 길게 `Left` | `LNG_LEFT` |
| 길게 `Right` | `LNG_RIGHT` |
| 길게 `Enter` / `Keypad Enter` | `LNG_SQUARE` |
| 길게 `Up` | `UP` 반복 |
| 길게 `Down` | `DOWN` 반복 |

호환용으로 `Shift` 또는 `Ctrl`과 함께 `Left`, `Up`, `Down`, `Right`,
`Enter`를 누르면 각각 `LNG_LEFT`, `LNG_UP`, `LNG_DOWN`, `LNG_RIGHT`,
`LNG_SQUARE`로 처리된다.

## GPIO 키패드

| 키패드 | PiFinder 입력 |
| --- | --- |
| 숫자 키 | 숫자 `0-9` |
| `+` | `PLUS` |
| `-` | `MINUS` |
| 사각/확인 키 | `SQUARE` |
| 방향키 | `LEFT`, `UP`, `DOWN`, `RIGHT` |

GPIO 키패드는 `SQUARE`를 누른 상태에서 방향키, `+`, `-`, `0`을 누르면
해당 `ALT_*` 입력으로 처리된다.

GPIO 키패드의 `0`은 릴리스 때 단발 숫자 입력으로 전달되지만, USB/Bluetooth
키보드의 `0`은 내부에서 "입력 없음" 값과 겹쳐 큐에 전달되지 않는다. 따라서 HID
키보드에서는 `0`에 배정된 화면 동작(예: 마운트 정지)을 사용할 수 없다.

## INDI 마운트 제어

INDI 마운트 제어는 선택 기능이다. 기본 설치는 PiFinder 배포본의 INDI 바이너리
아카이브(`scripts/install_indi_mount_archive.sh`)를 사용한다. INDI 소스나 PiFinder
OnStepX 패치를 수정해야 할 때만 `scripts/install_indi_mount_OnstepX.sh`로 전체 소스
설치·빌드를 수행한다. 설치 후 PiFinder UI에서 다음 설정을 켠 경우에만 동작한다.

```text
Settings > Experimental > Mount Control > On
```

Mount Control이 켜져 있으면 숫자 키는 Object Details 화면, 일반 메뉴, 상태
화면에서 아래 마운트 동작을 보낸다(하나의 공통 맵 — `docs/mf_dev/mf_input_keymap_ko.md`
참고). `1-9`는 USB/Bluetooth 키보드·키패드·GPIO 키패드에서 같은 방식으로
동작하지만, USB/Bluetooth 키보드의 `0`은 위 제한 때문에 동작하지 않는다. 연속 방향 조그는 키보드 문자에도 있고, 전용 INDI Guide 화면도 같은 공통
맵을 쓴다(숫자 키 대각 조그는 제거 — 대각은 키보드 문자로 유지). 객체 리스트에서는
숫자 키가 대신 카탈로그 시퀀스 점프를 입력하고, 문자는 Name Search를 연다.

| 키 | INDI 마운트 동작 |
| --- | --- |
| `0` | 마운트 정지 (GPIO 키패드/개발 키보드만; HID 키보드에서는 미전달) |
| `2` | South 이동 — 키를 누르는 동안 |
| `4` | West 이동 — 키를 누르는 동안 |
| `5` | GoTo — Object Details(선택 객체)에서만 |
| `6` | East 이동 — 키를 누르는 동안 |
| `7` | 현재 PiFinder solve 위치로 마운트 Sync |
| `8` | North 이동 — 키를 누르는 동안 |
| `9` | 슬루 속도 증가 |
| `3` | 슬루 속도 감소 |
| `1` | 미사용 |

기본 방향 키는 누르는 동안 마운트를 이동한다(누르면 시작, 떼면 정지). 누른 만큼
이동한다. `5`(GoTo)는 객체가 선택된 Object Details 화면에서만 동작하며, 일반 메뉴·
상태 화면엔 타겟이 없어 아무 동작도 하지 않는다. step 크기 설정은 없으며, `1`은 더
이상 init/sync하지 않는다 — 기동 시 자동으로 init·sync된다. 이동 속도(슬루
속도)는 `9`(증가) / `3`(감소)으로 정하며, `+`/`-`는 마운트에 관여하지 않는다.

INDI 서버나 마운트 연결에 문제가 있어도 PiFinder 기본 기능은 계속 동작한다.
마운트 연결 상태는 다음 파일에서 확인할 수 있다.

```text
~/PiFinder_data/mount_control_status.json
```
