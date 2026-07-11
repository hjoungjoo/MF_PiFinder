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
| 숫자 `0-9` / Keypad 숫자 | 숫자 `0-9` |
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

## INDI 마운트 제어

INDI 마운트 제어는 선택 기능이다. `scripts/install_indi_mount.sh`로 INDI
지원을 설치하고 PiFinder UI에서 다음 설정을 켠 경우에만 동작한다.

```text
Settings > Experimental > Mount Control > On
```

Mount Control이 켜져 있으면 숫자 키는 Object Details 화면, 일반 메뉴, 상태
화면에서 아래 마운트 동작을 보낸다(하나의 공통 맵 — `docs/mf_input_keymap_ko.md`
참고). USB/Bluetooth 키보드의 숫자 키와 keypad 숫자 키, GPIO 숫자 키가 같은 방식으로
동작한다. 연속 방향 조그는 키보드 문자에도 있고, 전용 INDI Guide 화면은 자체 조그
방식을 유지한다. 객체 리스트에서는 숫자 키가 대신 카탈로그 시퀀스 점프를 입력하고,
문자는 Name Search를 연다.

| 키 | INDI 마운트 동작 |
| --- | --- |
| `0` | 마운트 정지 |
| `2` | South 이동 — 키를 누르는 동안 |
| `4` | West 이동 — 키를 누르는 동안 |
| `5` | GoTo — Object Details(선택 객체)에서만 |
| `6` | East 이동 — 키를 누르는 동안 |
| `7` | 현재 PiFinder solve 위치로 마운트 Sync |
| `8` | North 이동 — 키를 누르는 동안 |
| `1`, `3`, `9` | 미사용 |

기본 방향 키는 누르는 동안 마운트를 이동한다(누르면 시작, 떼면 정지). 누른 만큼
이동한다. `5`(GoTo)는 객체가 선택된 Object Details 화면에서만 동작하며, 일반 메뉴·
상태 화면엔 타겟이 없어 아무 동작도 하지 않는다. step 크기 설정은 없으며, `1`은 더
이상 init/sync하지 않는다 — 기동 시 자동으로 init·sync된다. 이동 속도는 슬루
속도(`+`/`-`)로 정한다.

INDI 서버나 마운트 연결에 문제가 있어도 PiFinder 기본 기능은 계속 동작한다.
마운트 연결 상태는 다음 파일에서 확인할 수 있다.

```text
~/PiFinder_data/mount_control_status.json
```
