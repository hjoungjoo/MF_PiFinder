# MF PiFinder 입력 조작법 (키패드 & 키보드)

기준: `mf_pifinder` 브랜치, 2026-07-11.

이 문서는 PiFinder UI가 키패드/키보드 입력을 처리하는 방식을 소스에서 정확히
정리한 참조 문서입니다. 모든 화면이 공유하는 전역 동작과, 특수하게 동작하는
화면을 함께 다루며, 알려진 불일치와 목표 모델(권장안)까지 담아 입력 처리를 하나의
합의된 스펙에 맞춰 수정할 수 있게 합니다.

관련 문서: `docs/mf_keyboard_mapping_ko.md`는 물리 키 -> PiFinder 입력 이벤트
매핑(어떤 키가 어떤 이벤트를 내는지)을 다룹니다. 이 문서는 그 이벤트로 UI가
무엇을 하는지를 다룹니다.

## 1. 입력 소스와 이벤트 인코딩

입력 소스는 세 가지이며, **모두 같은 이벤트를 내보내지 않습니다.**

| 소스 | 모듈 | 숫자 | 문자 | 비고 |
| --- | --- | --- | --- | --- |
| GPIO 키패드 (기기) | `keyboard_pi.py` | **press/release** (`NUMBER_PRESS_BASE=3000`, `RELEASE=3100`) | 없음 | `SQUARE` 홀드 + 키 = `ALT_*` |
| USB/블루투스 HID 키보드 (기기) | `keyboard_pi.py` (libinput) | **press/release** | **press/release** (`TEXT_PRESS/RELEASE`) | Alt/Ctrl/Shift 조합 = `ALT_*` / `LNG_*` |
| 개발용 호스트 키보드 | `keyboard_local.py` (`--keyboard local`) | **단발 0-9** (`key_number`) | 매핑된 키만 | pyhotkey, 개발용 |

**핵심 결과(대부분 불일치의 근원):** 실제 하드웨어(키패드 + USB/BT)에서는 숫자·
문자 키가 **press/release** 이벤트로 도착하지만, 개발용 키보드는 **단발 숫자**를
냅니다. 따라서 화면이 어떻게 반응하는지는 그 화면이 `key_number`를 구현했는지
`key_number_press`/`key_number_release`를 구현했는지에 달려 있습니다.

키 이벤트 코드(`keyboard_interface.py`): 기본키 `LEFT=20 UP=21 DOWN=22 RIGHT=24
PLUS=11 MINUS=12 SQUARE=13`; `ALT_*=101..110`; `LNG_*=200..204`; 숫자·문자는 위
press/release base; 단발 숫자는 `keycode < 10`.

## 2. 전역 조작 (menu_manager — 모든 화면 공통)

`main.py`가 큐를 읽어 `menu_manager.key_*`를 호출하고, menu_manager는 키를 직접
처리(help 오버레이, 마킹 메뉴, 뒤로/점프)하거나 활성 화면 `self.stack[-1].key_*`로
전달합니다.

| 입력 | 전역 동작 |
| --- | --- |
| `LEFT` | 뒤로: 현재 화면을 스택에서 pop (화면의 `key_left()`가 `False`를 반환하면 유지) |
| `LNG_LEFT` | 메뉴 최상위로 점프(루트로 리셋) |
| `LNG_RIGHT` | 가장 최근 객체의 Object Details로 점프 |
| `LNG_SQUARE` | 현재 화면의 **마킹 메뉴** 토글 |
| `SQUARE` | 마킹 메뉴가 열려 있으면 한 단계 뒤로/닫기, 아니면 화면으로 전달 |
| `UP`/`DOWN`/`RIGHT`/`PLUS`/`MINUS`/숫자/문자 | 활성 화면으로 전달 |
| help 오버레이 열림 | 아무 키나 닫기, `UP`/`DOWN`은 도움말 이미지 페이지 이동 |
| `ALT_PLUS`/`ALT_MINUS` | 디스플레이 밝기 증가/감소 (전역) |
| `ALT_0` | 스크린샷 |
| `ALT_LEFT` | 카메라 이미지 저장 |
| `ALT_RIGHT` | 전체 디버그 덤프 저장(이미지 + solution + 상태) |

### 마킹 메뉴 모델 (`marking_menus.py`)

- `LNG_SQUARE`로 열림, 정지된 스크린샷 위에 4분할 파이로 렌더링.
- 네 개의 `MarkingMenuOption`(`up` 기본 `HELP`, `down`, `left`, `right`)을 가짐.
  열려 있는 동안 `LEFT`/`UP`/`DOWN`/`RIGHT`가 해당 옵션을 선택하고, `SQUARE`는 한
  단계 뒤로(스택이 비면 닫힘).
- 옵션은 중첩 마킹 메뉴 열기, HELP 표시, 라벨 메뉴로 `menu_jump`(예:
  `filter_options`, `shutdown`, `camera_gain`), 콜백 실행 중 하나를 함.

### 죽은/미처리 입력 (현재 어디서도 동작 없음)

- `LNG_UP` / `LNG_DOWN` (200/201 발생) — `main.py`가 디스패치하지 않고
  `menu_manager.key_long_up/down`은 `pass`. 그 결과
  `UIObjectList.key_long_up/key_long_down`은 도달 불가 죽은 코드.
- `ALT_UP` / `ALT_DOWN` / `ALT_SQUARE` — 키보드가 내보내지만 `main.py`가 처리하지
  않고 화면으로도 전달하지 않음.

## 3. 기본 화면 기본값 (`UIModule`)

화면이 재정의하지 않으면:

| 입력 | 기본값 |
| --- | --- |
| `LEFT` | `True` 반환 -> 화면 pop(뒤로) |
| `RIGHT` / `UP` / `DOWN` | 동작 없음 |
| `SQUARE` | `cycle_display_mode()` (화면 표시 모드 순환) |
| `PLUS` / `MINUS` / 숫자 / 문자 | 동작 없음 |
| 숫자 **press** | `key_number()`로 폴백(탭이 개별 핸들러를 실행); 숫자 **release** = 동작 없음 |

## 4. 표준 메뉴 — `UITextMenu` (및 리스트 파생)

`UITextMenu`는 **`GuideKeyMixin`**(§6)을 상속하므로, 표준 메뉴에서 숫자·문자·`+`·
`-` 키는 **마운트 제어가 켜져 있을 때** INDI 마운트 가이드로 가로채이고, 그 외에는
동작이 없습니다.

| 입력 | 동작 |
| --- | --- |
| `UP` / `DOWN` | 하이라이트 스크롤 (`menu_scroll`) |
| `RIGHT` | 선택: 항목 `callback` 실행, 또는 서브메뉴 `class` 진입, 또는 `config_option` 값 설정(아래), 이후 메뉴 `post_callback` |
| `LEFT` | 뒤로(pop) |
| `SQUARE` | `cycle_display_mode` (일반 메뉴에서는 보통 무효) |
| 숫자 / 문자 / `+` / `-` | **GuideKeyMixin** -> 마운트 가이드(마운트 ON) 또는 무효 |

config-option 메뉴(`RIGHT`):
- `single`: 값 하나 설정. `filter.*` 옵션은 값이 바뀌면 상위 메뉴로 자동 복귀.
- `multi`: 항목을 선택 집합에 토글; `Select All` / `Select None`은 일괄 토글.

### 리스트 파생 (모두 `UITextMenu` + `GuideKeyMixin` 상속)

- **`UIObjectList`**: `RIGHT`는 하이라이트된 객체의 Object Details를 엶; `SQUARE`는
  표시 모드 `LOCATE -> NAME -> INFO` 순환; **숫자는 카탈로그 시퀀스를 입력해
  점프**(예: 45 -> M45, `key_number`). 정렬·필터는 마킹 메뉴(`Sort` 중첩 MM,
  `Filter` -> `filter_options`).
- **`UIObsList`**: `RIGHT`는 폴더로 진입하거나 `.skylist`를 로드해 객체 목록으로
  엶; `LEFT` 뒤로.
- **`UILocationList`**: `RIGHT`는 위치별 액션 메뉴(Load / Delete / Rename)를 엶;
  `UP`/`DOWN`으로 이동; `LEFT`는 액션 메뉴 닫기 또는 뒤로.
- **`UIEquipment`** (`GuideKeyMixin` + `UIModule`): `UP`/`DOWN`으로 망원경/아이피스
  행 전환; `RIGHT`로 해당 선택 메뉴 열기.

## 5. 특수 키 동작 화면

### Object Details — `UIObjectDetails` (커스텀, GuideKeyMixin 아님)

| 입력 | 동작 |
| --- | --- |
| `UP` / `DOWN` | 목록의 이전/다음 객체 |
| `LEFT` | 뒤로(최근 목록에 추가) |
| `RIGHT` | **Log** 화면 열기(pointing solution 필요) |
| `SQUARE` | 표시 모드 순환: DESC / LOCATE / POSS / SDSS / Contrast |
| `PLUS` / `MINUS` | 마운트 ON: 슬루 속도 +/-("Speed +/-"); 마운트 OFF: 아이피스 시야(FOV) 순환 / 설명 스크롤 |
| 숫자 **탭** (`key_number`) | 개별 INDI 마운트 명령: 0=정지 1=Init+Sync 2=남(스텝) 3=스텝- 4=서 5=**타겟 GoTo** 6=동 7=**Sync** 8=북 9=스텝+ |
| 숫자 **press/release** (`key_number_press`) | 마운트 ON: 8방향 **홀드 이동** 가이드(1=SW 2=S 3=SE 4=W 6=E 7=NW 8=N 9=NE; 0/5=정지; 떼면 정지) |
| 문자 (HID) | 8방향 홀드 이동 가이드(q/w/e/a/s/d/z/x/c, s=정지) |

### 텍스트 / 숫자 입력

- **`UITextEntry`**: 멀티탭 또는 T9 텍스트 입력. 숫자키가 글자를 순환(멀티탭)하거나
  숫자를 입력(T9/검색). `SQUARE` 글자/기호 세트 토글; `PLUS` 공백 삽입; `MINUS`
  삭제; 길게 `MINUS` 전체 삭제; `LEFT` 확정 또는 뒤로; `RIGHT` 검색 결과 표시;
  HID 문자는 바로 추가.
- **`UIDateEntry`** (위치/GPS 고정 필요): 숫자가 yyyy/mm/dd 칸을 채움(자동 진행);
  `MINUS` 삭제 / 이전 칸; `RIGHT` 진행 / 확정; `LEFT` 이전 칸 또는 취소.
- **`UILocationEntry`**: 숫자가 위도/경도/고도 칸을 채움; `MINUS` 삭제 / 이전 칸;
  `PLUS` 부호 토글(N/S, E/W); `RIGHT` 위도->경도->고도 흐름 진행; `LEFT` 이전 칸
  또는 취소.

### INDI 마운트 화면 (`indi.py`)

- **`UIIndiInit`**: 숫자로 개별 1회 명령 — 1=Init 2=위치/시간 Sync 3=Park 4=홈
  설정 5=홈 복귀 6=Unpark 7=Park 설정 8=드라이버 재시작; `SQUARE` = Init.
- **`UIIndiBacklash`**: 숫자로 선택 축의 백래시 값 입력(0-999); `PLUS`**와** `MINUS`
  **둘 다** RA<->DE 축 토글; `RIGHT` 자동 백래시 실행; `SQUARE` 두 축 저장.
- **`UIIndiGuide`**: `0`/`5`는 개별 토글(0=가이드 보정 on/off, 5=1회 정밀보정
  on/off); 방향 숫자 `1-4/6-9`와 문자는 **press/홀드 이동** 가이드(떼면 정지);
  `PLUS`/`MINUS` 슬루 속도 +/-; `SQUARE` 현재 solve로 마운트 Sync.
- **`UIIndiMultiPointAlign`** (`UIIndiGuide` 확장): 단계별 마법사. 설정 단계에서는
  숫자가 개별(정렬점 수/모드 선택), ADJUST 단계에서는 같은 방향 숫자·문자가 홀드
  이동 조그. `UP`/`DOWN`, `LEFT`/`RIGHT`, `SQUARE`가 마법사 단계 전환을 담당.

### 정렬 화면

- **`UIAlign`**: `SQUARE` 정렬 모드 토글(나갈 때 정렬 저장); 정렬 모드에서
  `UP`/`DOWN`/`RIGHT`/`LEFT`가 별 선택 이동; `PLUS`/`MINUS` 확대/축소; `1` 레티클
  리셋, `0` 취소(정렬 모드에서만).
- **`UIAlignDaytime`**: `SQUARE` 시작 / 저장; 사분면 숫자 `7 9 1 3`으로 영역 선택;
  화살표는 정밀 모드 전환 및 1px 이동; `0` 취소; `PLUS`/`MINUS` 노출 +/-.
- **`UIPolarAlign`**: `SQUARE` 마법사 진행; `MINUS` 취소/뒤로; `0` 계산(AIM에서
  solve 2개 이상일 때).

### 정보 / 유틸 화면

- **`UILog`**: `RIGHT`는 현재 항목 실행(로그 & 종료 / 평점 순환 / 서브메뉴 /
  아이피스); `UP`/`DOWN` 항목 이동; 숫자는 현재 별점(관측성/매력도) 설정.
- **`UIChart`**: `PLUS`/`MINUS` 확대/축소; `SQUARE` 카메라 시야로 FOV 리셋.
- **`UIPreview`** (`GuideKeyMixin`): `PLUS`/`MINUS`는 **확대/축소**(믹스인의 슬루
  속도를 재정의), `SQUARE`는 포커스/HUD 오버레이 토글; 숫자·문자는 마운트 ON이면
  여전히 가이드.
- **`UIConsole`**: `UP`/`DOWN` 로그 스크롤; 숫자는 개발 단축키(0=카메라 디버그
  토글, 아무 숫자나 고정 디버그 시각 설정).
- **수동 상태 화면** (`UIGPSStatus`, `UIGPSTimeSyncStatus`, `UIIndiStatus`): 순수
  `GuideKeyMixin` — 화살표는 로컬 동작, 숫자·문자·`+`·`-`는 마운트 ON이면 가이드.

## 6. GuideKeyMixin — 전 화면 숫자·문자 가로채기

`GuideKeyMixin`(`base.py`)은 `key_number`, `key_number_press`,
`key_number_release`, `key_text*`, `key_plus`, `key_minus`를 재정의해 INDI 마운트
가이드(`_guide_*`)로 보냅니다. `UITextMenu`(따라서 모든 표준 메뉴·리스트),
`UIEquipment`, `UIPreview`, 수동 상태 화면이 상속합니다.

- 숫자 1-9 -> 방위 이동(1=SW 2=S 3=SE 4=W 6=E 7=NW 8=N 9=NE), 0/5=정지.
- 문자 q/w/e/a/s/d/z/x/c -> 방위 이동, s=정지.
- `+`/`-` -> 슬루 속도 증가/감소.
- 모두 `_guide_mount_queue`를 거치며, `mount_control`이 꺼져 있으면 `None`을 반환해
  키는 **무효**. 화살표와 `SQUARE`는 믹스인이 건드리지 않음.

믹스인이 `key_number_press`를 가이드로 직행시키고(**`key_number`로 폴백하지 않음**),
그래서 서브클래스 자체의 `key_number` 핸들러는 **물리 키패드/HID(press/release)에서
도달 불가**하고 개발용 키보드의 단발 숫자에서만 실행됩니다.

## 7. 알려진 불일치 (수정 후보)

1. **Object Details의 개별 마운트 명령이 실제 하드웨어에서 가려짐.** 마운트 ON인
   기기에서 숫자 press/release는 홀드 이동 가이드로 가므로, `key_number`의
   GoTo(5)/Sync(7)/Init(1)/스텝(3,9)이 실행되지 않음 — 키패드로 타겟 GoTo/Sync
   불가. 개발용 키보드에서만 실행됨.
2. **Object List 카탈로그 숫자 점프가 키패드에서 죽어 있음.** `UIObjectList`는
   `key_number`만 구현하는데 키패드/HID는 press/release를 보내고 `GuideKeyMixin`이
   가이드로 소비 -> 시퀀스 번호 점프는 개발용 키보드에서만 동작.
3. **표준 메뉴가 마운트 조이스틱이 됨.** 마운트 ON이면 모든 `UITextMenu`(전 메뉴·
   리스트)의 숫자·문자·`+`·`-`가 마운트 가이드로 바뀌어, 탐색 중 의도치 않게 마운트가
   움직이기 쉬움.
4. **개발용 키보드와 기기가 근본적으로 다름**(단발 숫자 vs press/release), 그래서
   `--keyboard local` 테스트와 실제 하드웨어의 동작이 갈림.
5. **죽은 키**: `LNG_UP`/`LNG_DOWN`, `ALT_UP`/`ALT_DOWN`/`ALT_SQUARE`가 발생하지만
   디스패치되지 않음.
6. **`UIIndiBacklash`**: `PLUS`와 `MINUS`가 동일 동작(축 토글).
7. **문서 vs 코드**: `mf_keyboard_mapping`은 Object Details 개별 숫자 명령을
   키패드/HID/GPIO 동작으로 제시하지만, 실제 하드웨어에서는 가이드에 가려짐(#1).

## 8. 제안 목표 모델 (논의용)

목표: 키패드와 키보드에서 동일하게 예측 가능한 하나의 체계로, 각 화면이 분기 로직을
재구현하지 않고도 개별 동작과 홀드 이동 가이드를 모두 유지.

- **디스패치 계층에 탭 vs 홀드 레이어 도입**(main.py / 키보드 드라이버): 숫자·문자
  press 시 타이머 시작; 빠른 릴리스(~400 ms 미만)는 단발 **탭**(`key_number(n)` /
  `key_text(c)`)을, 홀드 임계 초과는 **홀드 시작**(`key_number_press`)과 릴리스 시
  **홀드 종료**(`key_number_release`)를 발생. 그러면 키패드와 키보드가 같은 탭/홀드
  이벤트를 공급하고, 개발용 키보드의 단발 숫자도 탭으로 통일됨.
- **하나의 규약 정의:** 탭 = 개별 동작(GoTo/Sync/선택/점프/입력), 홀드 = 마운트
  가이드 / 자동 반복. `GuideKeyMixin`은 홀드 -> 가이드로 매핑하고 탭은 화면의 개별
  핸들러에 맡김(그러면 Object List 점프와 Object Details GoTo가 기기에서도 동작).
- **마운트 가이드를 게이트**해서 일반 탐색 메뉴에서는 화면이 opt-in하지 않는 한
  숫자를 가로채지 않게 함(예: Object Details / Preview / 상태 화면만 가이드, 일반
  메뉴는 아님).
- **죽은 키 정리** — `LNG_UP/DOWN`, `ALT_UP/DOWN`을 실제 동작에 연결하거나 매핑에서
  제거.
- **`UIIndiBacklash`** `PLUS`/`MINUS`를 구분(예: +/-로 값 조정, 또는 하나는 축
  토글·다른 하나는 다른 유용한 동작).
- **`mf_keyboard_mapping`을 최종 동작과 재동기화**.

정확한 탭 임계값과 어떤 화면이 가이드에 opt-in할지 결정한 뒤 이 문서에 맞춰
구현하면 됩니다.
