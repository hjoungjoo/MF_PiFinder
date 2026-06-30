# MF PiFinder GoTo / Mount Control 소스 구조

작성 기준: `mf_pifinder` 브랜치, 2026-07-01.

이 문서는 SkySafari push-to 사용 흐름과 INDI/OnStep mount control 흐름을 소스 기준으로 정리한다. 앞으로 SkySafari에서 선택한 대상을 INDI GoTo로 연결하거나, PiFinder 내부 target 선택과 GoTo 편의 기능을 개선할 때 이 문서를 기준 구조로 사용한다.

## 목적

현재 PiFinder에는 서로 다른 두 흐름이 공존한다.

1. SkySafari가 PiFinder에 LX200 프로토콜로 접속해 현재 PiFinder가 보고 있는 하늘 위치를 읽고, 사용자가 선택한 대상 좌표를 PiFinder의 recent target으로 밀어 넣는 push-to 흐름.
2. INDI LX200 OnStep 드라이버를 통해 위치/시간, park/unpark, slew rate, 수동 이동, sync, GoTo를 수행하는 mount-control 흐름.

현재 SkySafari의 `GoTo` 명령은 실제 마운트 GoTo가 아니라 PiFinder UI에 대상 좌표를 전달하는 push-to 이벤트로 동작한다. 앞으로 이 지점에 실제 INDI GoTo를 연결할 수 있다.

## 실행 프로세스 구조

주요 프로세스는 `python/PiFinder/main.py`에서 시작된다.

```text
main.py
  SharedStateObj
  ├─ GPS monitor process
  ├─ Keyboard process
  ├─ Web server process              -> server.py
  ├─ Camera process
  ├─ IMU process
  ├─ Solver process
  ├─ Integrator process              -> shared_state.solution()
  ├─ SkySafariServer process         -> pos_server.py, TCP 4030
  └─ MountControl process(optional)  -> mountcontrol_indi.py
```

관련 시작 위치:

- `python/PiFinder/main.py`
  - SkySafari server: `Process(name="SkySafariServer", target=pos_server.run_server, ...)`
  - INDI mount-control: `Process(name="MountControl", target=mountcontrol_indi.run, ...)`
- INDI mount-control 프로세스는 `mount_control` config가 `true`일 때만 시작된다.

## 공유 상태 구조

### 핵심 객체

`python/PiFinder/state.py`

- `SharedStateObj`
  - `solution()`: 현재 PiFinder가 추정하는 pointing 상태.
  - `solve_state()`: 현재 pointing이 유효한지 빠르게 확인하는 캐시.
  - `location()`: GPS 또는 수동 Load로 설정된 관측 위치.
  - `datetime()`: PiFinder 기준 시간.
  - `ui_state()`: recent target, current target, push-to 플래그 등 UI 상태.

`python/PiFinder/types/positioning.py`

- `PointingEstimate`
  - canonical pointing 구조.
  - 현재 망원경 방향은 보통 `pointing.aligned.estimate`를 사용한다.
  - `RA`, `Dec`, `Roll`은 degrees 단위다.

### 현재 망원경 방향

현재 PiFinder가 생각하는 망원경 방향은 다음 경로로 읽는다.

```python
solution = shared_state.solution()
aligned = solution.pointing.aligned.estimate
current_ra = aligned.RA
current_dec = aligned.Dec
```

이 값은 plate solve와 IMU dead-reckoning을 통합한 결과다.

### 현재 관측 위치

`shared_state.location()`은 다음 상황에서 갱신된다.

- GPS lock
- 웹 Locations의 `Load Location`
- LCD Locations의 Load
- 수동 좌표 입력

수동 위치는 `WEB`, `MANUAL`, `CONFIG: <name>` source로 들어오며 lock된 위치로 취급된다. 자동 GPS 업데이트는 수동 lock을 덮지 않도록 보호된다. 단, 사용자가 다시 수동 위치를 선택하면 기존 수동 lock 위에 새 수동 위치가 적용된다.

## Plate Solve / Push-To 기준 위치 흐름

### Solver와 Integrator

`python/PiFinder/solver.py`

- 카메라 이미지에서 별을 인식하고 plate solve 결과를 만든다.
- 성공 결과는 `SuccessfulSolve`로 solver queue에 전달된다.
- solve 결과에는 camera axis와 aligned axis가 포함된다.

`python/PiFinder/integrator.py`

- solver 결과와 IMU 샘플을 합쳐 `PointingEstimate`를 유지한다.
- plate solve 성공 시 기준점을 갱신한다.
- solve 사이에는 IMU dead-reckoning으로 `pointing.aligned.estimate`를 진행시킨다.
- `shared_state.set_solution(...)`으로 현재 pointing을 publish한다.

### Object Details push-to 화면

`python/PiFinder/ui/object_details.py`

- 대상 객체의 RA/Dec와 현재 pointing을 비교해서 push-to 안내를 표시한다.
- `_render_pointing_instructions()`에서 `calc_utils.aim_degrees(...)`를 호출한다.

`python/PiFinder/calc_utils.py`

- `aim_degrees(shared_state, mount_type, screen_direction, target)`
  - Alt/Az mount일 때: target RA/Dec를 현재 시간/위치 기준 Alt/Az로 변환하고 현재 `solution.Alt/Az`와 비교한다.
  - EQ mount일 때: target RA/Dec와 현재 aligned RA/Dec 차이를 계산한다.

## SkySafari LX200 서버 구조

`python/PiFinder/pos_server.py`

SkySafari는 PiFinder에 LX200 telescope처럼 접속한다.

- TCP port: `4030`
- 실행 프로세스: `SkySafariServer`
- 프로토콜: Meade LX200 style command subset

### SkySafari가 현재 위치를 읽는 흐름

명령 매핑:

```text
:GR# -> get_telescope_ra()
:GD# -> get_telescope_dec()
```

`get_telescope_ra(shared_state, _)`

- `shared_state.solution()`에서 `pointing.aligned.estimate`를 읽는다.
- 내부 값은 J2000 기준 degrees로 보고, 현재 시간 epoch으로 변환해 `HH:MM:SS` 형태로 반환한다.

`get_telescope_dec(shared_state, _)`

- 같은 pointing에서 Dec를 읽는다.
- 현재 시간 epoch으로 변환해 `+DD*MM'SS` 형태로 반환한다.

SkySafari 입장에서는 PiFinder가 “현재 망원경이 바라보는 좌표를 알려주는 telescope”처럼 보인다.

### SkySafari target 선택 / push-to 흐름

SkySafari에서 사용자가 대상을 선택하고 GoTo를 누르면 일반적으로 다음 명령 순서가 들어온다.

```text
:SrHH:MM:SS#     target RA 설정
:Sd+DD*MM:SS#    target Dec 설정
:MS#             slew 요청
```

현재 PiFinder 구현:

- `:Sr...#`
  - `parse_sr_command()`
  - target RA를 임시 전역 변수 `sr_result`에 저장한다.
- `:Sd...#`
  - `parse_sd_command()`
  - target Dec를 파싱하고, 앞서 받은 RA와 함께 `handle_goto_command(...)`를 호출한다.
- `:MS#`
  - `respond_zero()`만 호출한다.
  - 실제 동작은 현재 `:Sd` 처리 시점에서 이미 수행된다.

`handle_goto_command(shared_state, ra_parsed, dec_parsed)`

현재 이름은 `goto`지만 실제 마운트 GoTo가 아니다. 동작은 다음과 같다.

1. RA/Dec를 degrees로 변환한다.
2. SkySafari 입력은 JNOW로 보고 J2000으로 변환한다.
3. `CompositeObject`를 만든다.
   - `catalog_code`: `PUSH`
   - `description`: `Skysafari object nr <sequence>`
4. `shared_state.ui_state().add_recent(obj)`
5. `shared_state.ui_state().set_new_pushto(True)`
6. `ui_queue.put("push_object")`
7. SkySafari에는 성공 응답 `"1"`을 반환한다.

이 흐름 때문에 SkySafari GoTo는 현재 “PiFinder recent target으로 push”로 동작한다.

### LCD UI 반응

`python/PiFinder/main.py`

```python
elif ui_command == "push_object":
    menu_manager.jump_to_label("recent")
```

`python/PiFinder/ui/object_list.py`

- Recent list가 활성화될 때 `ui_state.new_pushto()`를 확인한다.
- 새 push-to가 있으면 object list를 갱신하고 바로 object details 화면으로 들어간다.

`python/PiFinder/ui/object_details.py`

- `PUSH` catalog code는 외부 catalog 초기화 없이 바로 표시 가능하다.
- 이후 기존 push-to 방식으로 방향 안내를 보여준다.

## INDI / OnStep 웹 UI 구조

웹 INDI 페이지는 Flask server 안에 있다.

`python/PiFinder/server.py`

주요 route:

```text
GET  /indi
GET  /indi/current_values
POST /indi/driver
POST /indi/restart
POST /indi/park
POST /indi/slew_rate
POST /indi/motion
POST /indi/location_time
```

템플릿:

- `python/views/indi_mount.html`

### 웹 UI의 제어 방식

웹 UI는 PyIndi 프로세스 큐를 거치지 않고 주로 `indi_getprop` / `indi_setprop` CLI를 사용한다.

관련 helper:

`python/PiFinder/sys_utils.py`

- `get_indi_onstep_properties(...)`
  - `indi_getprop`로 `LX200 OnStep.*` 속성을 읽는다.
- `apply_indi_onstep_connection(...)`
  - LX200 OnStep driver의 USB/network 연결 속성을 설정한다.
- `apply_indi_onstep_properties(...)`
  - INDI 속성 목록을 `indi_setprop`로 적용한다.
- `restart_indi_web_manager(...)`
  - `indiwebmanager.service` 재시작.
- `connect_indi_onstep_driver(...)`
  - INDI driver의 `CONNECTION.CONNECT=On` 적용.
- `sync_onstep_location_time_exclusive(...)`
  - INDI driver가 위치/시간을 적용하지 못할 때 fallback.
  - INDI Web Manager를 잠깐 중지하고 OnStep LX200 TCP/serial 명령을 직접 보낸 뒤 다시 시작한다.

### 위치/시간 동기화

`POST /indi/location_time`

현재 흐름:

1. 웹 form의 `latitude`, `longitude`, `elevation`, `utc_time`을 읽는다.
2. `build_indi_location_time_properties(...)`로 INDI 속성을 만든다.
3. `apply_indi_onstep_properties(...)`로 LX200 OnStep driver에 적용한다.
4. 실제 `GEOGRAPHIC_COORD` readback이 목표 좌표와 맞는지 확인한다.
5. 안 맞으면 `sync_onstep_location_time_exclusive(...)` fallback을 실행한다.
6. 다시 INDI driver가 목표 좌표를 보고할 때까지 polling한다.

주의할 점:

- INDI raw longitude는 0..360 eastward convention이다.
- OnStep web UI 표시는 동/서 부호 convention이 다르게 보일 수 있다.
- PiFinder UI는 이 값을 구분해 표시한다.
  - `OnStep Location`: OnStep web UI와 같은 DMS 스타일 표시. INDI readback이 분 단위로 잘리면 PiFinder가 마지막으로 성공적으로 보낸 위치 캐시를 사용한다.
  - `Effective Coordinates`: 앞으로 기능 코드가 사용해야 하는 decimal 좌표. PiFinder가 성공적으로 동기화한 고정밀 위치를 우선하고, 없으면 INDI driver readback으로 fallback한다.
  - `INDI Driver Readback`: INDI driver가 직접 보고한 원본 값. LX200 OnStep driver에서는 위도/경도가 분 단위로 보이거나 고도가 0으로 보일 수 있으므로 진단용으로만 사용한다.

### 웹 수동 이동

`POST /indi/motion`

- 방향 버튼을 누르면 `TELESCOPE_MOTION_NS` / `TELESCOPE_MOTION_WE` 속성을 켠다.
- 손을 떼면 `TELESCOPE_ABORT_MOTION.ABORT=On`을 보낸다.
- web page JS는 keepalive를 보내고, 서버는 motion lease timer로 안전 stop을 보강한다.

관련 파일:

- `python/PiFinder/server.py`
- `python/views/indi_mount.html`

## INDI MountControl 프로세스 구조

`python/PiFinder/mountcontrol_indi.py`

이 프로세스는 선택 기능이다. `mount_control` config가 켜졌을 때만 `main.py`에서 시작된다.

### 통신 구조

```text
LCD UI / Object Details / INDI Guide
  -> mountcontrol_queue.put(command dict)
  -> MountControlIndi.handle_command()
  -> PyIndi client
  -> INDI server localhost:7624
  -> LX200 OnStep driver
  -> OnStep mount
```

### 상태 파일

MountControl은 상태를 파일로 기록한다.

```text
~/PiFinder_data/mount_control_status.json
```

읽는 쪽:

- LCD top bar status: `python/PiFinder/ui/base.py`
- LCD INDI status page: `python/PiFinder/ui/indi.py`

### 주요 command dict

`MountControlIndi.handle_command(...)`에서 처리한다.

```text
{"type": "init"}
{"type": "restart_driver"}
{"type": "sync", "ra": <deg>, "dec": <deg>}
{"type": "goto_target", "ra": <deg>, "dec": <deg>}
{"type": "stop_movement"}
{"type": "manual_movement", "direction": "...", "lease_seconds": ...}
{"type": "manual_movement_keepalive", "direction": "...", "lease_seconds": ...}
{"type": "increase_slew_rate"}
{"type": "reduce_slew_rate"}
{"type": "set_slew_rate", "rate": 0..9}
{"type": "refresh_slew_rate"}
{"type": "sync_location_time"}
{"type": "park_action", "action": "park|unpark|set_home|return_home|set_park"}
```

### PyIndi client

`PiFinderIndiClient`

- INDI server에 연결한다.
- telescope-like device를 자동 감지한다.
- `EQUATORIAL_EOD_COORD` update를 받아 현재 mount RA/Dec를 status에 기록한다.
- number/switch/text property set helper를 제공한다.

### GoTo 구현

`MountControlIndi.goto_target(ra_deg, dec_deg)`

현재 동작:

1. `connect()`로 INDI server와 telescope device를 준비한다.
2. `ON_COORD_SET.TRACK=On`을 설정한다.
3. `EQUATORIAL_EOD_COORD.RA=<ra_hours>`, `DEC=<dec_deg>`를 설정한다.
4. 상태 파일에 `state="slewing"`, `target_ra`, `target_dec`를 기록한다.

RA 입력은 degrees이고 INDI에는 hours로 보낸다.

```python
{"RA": (ra_deg % 360.0) / 15.0, "DEC": dec_deg}
```

### Sync 구현

`MountControlIndi.sync_mount(ra_deg, dec_deg)`

현재 동작:

1. `ON_COORD_SET.SYNC=On`
2. `EQUATORIAL_EOD_COORD`에 현재 solve RA/Dec를 보낸다.
3. 다시 `ON_COORD_SET.TRACK=On`
4. tracking on
5. status에 현재 mount position을 기록한다.

### 수동 이동 구현

`MountControlIndi.manual_move(direction, lease_seconds)`

- 현재 OnStep INDI motion property를 직접 켠다.
- 방향 mapping은 OnStep에서 관측자가 보는 화면 방향에 맞추기 위해 일부 East/West가 내부적으로 반전되어 있다.
- lease가 만료되면 `stop_mount()`가 자동 호출된다.

## LCD INDI UI 구조

`python/PiFinder/ui/menu_structure.py`

현재 INDI 메뉴 위치:

```text
Start
  INDI
    STATUS
    INIT
      Connect / Init
      Send Location/Time
      Park
      Unpark
      Set Home
      Return Home
      Set-Park
      Restart INDI
    Guide
```

실제 화면 구현:

- `python/PiFinder/ui/indi.py`
  - `UIIndiStatus`
  - `UIIndiGuide`
  - `UIIndiBase`
- `UIIndiInit` 클래스도 있으나, 현재 menu_structure에서는 INIT이 `UITextMenu`로 구성되어 있다.

### STATUS

`UIIndiStatus`

- `mount_control_status.json`을 읽는다.
- state, message, age, device, RA, Dec, speed, step, target RA/Dec를 표시한다.

### INIT

현재 menu item callback 기반이다.

`python/PiFinder/ui/callbacks.py`

- `indi_init`
- `indi_sync_location_time`
- `indi_park`
- `indi_unpark`
- `indi_set_home`
- `indi_return_home`
- `indi_set_park`
- `indi_restart_driver`

각 callback은 `_send_mount_control(...)`로 mountcontrol queue에 command dict를 넣는다.

### Guide

`UIIndiGuide`

- 카메라 영상을 배경으로 보여준다.
- 숫자키/키보드 문자 입력으로 수동 이동한다.
- 숫자키 mapping:

```text
7 8 9
4   6
1 2 3
```

- `+`, `-`: slew rate 변경.
- `Square`: 현재 PiFinder solve 위치로 mount sync.
- key press에서 motion 시작, key release에서 stop.
- keepalive와 lease를 사용해서 freeze 시 계속 움직이는 위험을 줄인다.

## Object Details에서 INDI GoTo

`python/PiFinder/ui/object_details.py`

Mount Control이 켜져 있으면 Object Details 숫자 키가 mountcontrol command를 보낸다.

현재 mapping:

```text
0 stop
1 init + 현재 solve 위치 sync
2 south
3 step 감소
4 west
5 현재 object GoTo
6 east
7 현재 solve 위치 sync
8 north
9 step 증가
```

`5`가 현재 내부 PiFinder target을 INDI GoTo로 보내는 지점이다.

```python
mountcontrol_queue.put({
    "type": "goto_target",
    "ra": self.object.ra,
    "dec": self.object.dec,
})
```

따라서 PiFinder 내부 catalog object, observing list object, SkySafari에서 PUSH된 object는 모두 Object Details에 올라온 뒤 `5`를 누르면 같은 GoTo 경로를 사용할 수 있다.

## 현재 SkySafari push-to와 INDI GoTo의 차이

### 현재 SkySafari path

```text
SkySafari target selected
  -> LX200 :Sr / :Sd
  -> pos_server.handle_goto_command()
  -> CompositeObject(catalog_code="PUSH")
  -> ui_state.recent
  -> ui_queue "push_object"
  -> LCD Object Details
  -> push-to 안내 표시
```

### 현재 INDI GoTo path

```text
PiFinder Object Details target
  -> number key 5
  -> mountcontrol_queue {"type": "goto_target", "ra": target.ra, "dec": target.dec}
  -> MountControlIndi.goto_target()
  -> INDI EQUATORIAL_EOD_COORD
  -> LX200 OnStep driver
  -> OnStep mount GoTo
```

두 path는 현재 Object Details 화면에서 만난다. SkySafari가 target을 밀어 넣으면 그 target이 Object Details에 표시되고, 사용자가 `5`를 누르면 INDI GoTo가 가능하다.

## 앞으로 GoTo 편의 기능을 붙일 수 있는 지점

### 1. SkySafari GoTo를 자동 INDI GoTo로 연결

수정 후보:

- `python/PiFinder/pos_server.py`
  - `handle_goto_command(...)`

가능한 변경:

- 현재처럼 recent target에 추가한다.
- 추가로 mountcontrol queue에 `goto_target`을 보낸다.

필요한 구조 변경:

- 현재 `pos_server.run_server(shared_state, p_ui_queue, log_queue)`는 `mountcontrol_queue`를 받지 않는다.
- 자동 GoTo를 하려면 `main.py`에서 SkySafariServer process args에 `mountcontrol_queue`를 추가하거나, 다른 broker/API를 만들어야 한다.
- 설정 option이 필요하다.
  - 예: `skysafari_goto_mode = "push_to" | "goto_confirm" | "goto_auto"`

권장:

- 기본값은 기존 호환을 위해 `push_to`.
- `goto_confirm`: SkySafari target을 Object Details로 띄우고 LCD/Web에 GoTo confirm을 제공.
- `goto_auto`: SkySafari `:MS#` 또는 target 수신 시 즉시 mountcontrol `goto_target`.

### 2. `:MS#` 처리 시점으로 의미 정리

현재 `:Sd`에서 target push가 실행되고 `:MS#`는 `respond_zero()`이다.

실제 telescope protocol 의미에 더 맞추려면:

- `:Sr`은 RA 저장.
- `:Sd`는 Dec 저장만.
- `:MS`에서 push-to 또는 GoTo 실행.

주의:

- 기존 SkySafari 동작과 호환성 확인 필요.
- Stellarium 특수 처리는 현재 ACK command로 `is_stellarium`을 판단한다.

### 3. Web INDI 페이지에 target GoTo 추가

수정 후보:

- `python/PiFinder/server.py`
- `python/views/indi_mount.html`

가능한 UI:

- Current PiFinder target 표시.
- Current SkySafari PUSH target 표시.
- `GoTo Current Target`
- `Sync Mount to Current Solve`
- `Stop`

필요한 것:

- 웹 server process에서 `mountcontrol_queue` 접근 가능 여부 확인.
- 현재 web INDI control은 `indi_setprop` 직접 방식이라, GoTo를 direct setprop로 할지 mountcontrol queue를 재사용할지 결정해야 한다.

권장:

- 장기적으로 GoTo/Sync/Stop은 mountcontrol queue로 통일한다.
- 웹의 direct `indi_setprop` 경로는 driver setup, status, fallback, 간단한 수동 제어에 남긴다.

### 4. LCD Object Details GoTo confirm 개선

수정 후보:

- `python/PiFinder/ui/object_details.py`

현재는 숫자 `5`를 누르면 바로 GoTo다. 편의/안전 기능을 추가할 수 있다.

- GoTo 전 confirm 화면.
- target altitude 낮음 경고.
- mount parked 상태이면 unpark 여부 확인.
- slew 중 stop/abort overlay.
- GoTo 후 현재 mount RA/Dec와 target 차이 표시.

### 5. 상태 모델 통합

현재 상태는 두 갈래다.

- PiFinder pointing: `shared_state.solution()`
- INDI mount position: `mount_control_status.json`의 `ra`, `dec`

앞으로 GoTo 편의 기능에는 둘을 모두 보여주는 것이 좋다.

예:

```text
PiFinder solve: RA/Dec
Mount reported: RA/Dec
Target: RA/Dec
Delta solve-target
Delta mount-target
```

## 주의할 위험 지점

### 포트 충돌

OnStep 네트워크/serial 포트는 동시에 여러 client가 붙으면 불안정할 수 있다.

- INDI LX200 OnStep driver가 연결 중이면 직접 LX200 TCP/serial 명령을 피한다.
- 직접 명령이 필요한 경우 `sync_onstep_location_time_exclusive(...)`처럼 INDI를 잠깐 중지하고 독점 접근 후 다시 시작한다.

### 좌표 epoch

SkySafari 입력은 현재 `pos_server.py`에서 JNOW로 보고 J2000으로 변환한다.

- PiFinder internal object RA/Dec는 J2000 기준으로 쓰는 흐름이 많다.
- INDI `EQUATORIAL_EOD_COORD`는 driver가 기대하는 epoch 의미를 확인해야 한다.
- 현재 MountControl GoTo는 object의 RA/Dec degrees를 그대로 INDI에 전달한다.

GoTo 정확도 문제가 보이면 epoch/JNOW/J2000 변환 지점을 우선 확인해야 한다.

### longitude convention

OnStep Web UI와 INDI raw longitude 표시는 부호 convention이 다르게 보일 수 있다.

- PiFinder 일반 위치: east-positive decimal degrees.
- INDI LX200 OnStep raw longitude: 0..360 eastward.
- OnStep Web UI: west-positive처럼 보이는 표시가 있다.

관련 helper:

- `sys_utils.onstep_longitude_degrees(...)`
- `sys_utils.onstep_web_longitude_degrees(...)`
- `sys_utils.format_onstep_location_display(...)`

### 움직임 안전

수동 guide는 press/release와 lease timeout을 모두 사용한다.

- 웹 UI: JS pointer release + 서버 timer.
- LCD UI: key release + mountcontrol lease.
- freeze 시 lease 만료 후 stop 재시도.

GoTo 자동화에도 abort/stop 경로가 항상 접근 가능해야 한다.

## 관련 config

`default_config.json`

```json
"mount_control": false,
"mount_control_indi_host": "localhost",
"mount_control_indi_port": 7624,
"onstep_connection_type": "network",
"onstep_serial_port": "",
"onstep_network_host": "",
"onstep_network_port": 9999
```

의미:

- `mount_control`
  - LCD mount-control process를 켤지 결정한다.
- `mount_control_indi_host`, `mount_control_indi_port`
  - INDI server 접속 위치.
- `onstep_connection_type`
  - LX200 OnStep driver가 OnStep에 붙는 방식.
- `onstep_serial_port`
  - USB serial 사용 시 port.
- `onstep_network_host`, `onstep_network_port`
  - network TCP 사용 시 OnStep host/port.

## 관련 설치/서비스

설치 문서:

- `docs/mf_indi_mount_install_ko.md`
- `docs/mf_indi_mount_install_en.md`

설치 스크립트:

- `scripts/install_indi_mount.sh`

서비스:

- `pifinder.service`
- `indiwebmanager.service`

기본 포트:

- PiFinder web: 80 또는 8080 fallback
- SkySafari LX200 server: 4030
- INDI server: 7624
- INDI Web Manager: 8624
- OnStep TCP: 9999

## 관련 테스트

현재 직접 관련 테스트:

- `python/tests/test_sys_utils.py`
  - INDI 위치/시간 property 변환.
  - OnStep LX200 직접 명령 변환.
  - longitude 표시 convention.
- `python/tests/test_mountcontrol_indi.py`
  - mount-control command 처리와 상태.
- `python/tests/test_main.py`
  - 수동 위치 reload가 이전 수동 lock 위에 다시 적용되는지.
- `python/tests/skysafari.py`
  - SkySafari LX200 server stress client.

GoTo 편의 기능을 추가할 때 필요한 테스트 후보:

- `pos_server.py`의 `:Sr`, `:Sd`, `:MS` 순서 처리 테스트.
- SkySafari push-to 기본 호환 유지 테스트.
- `goto_auto` option이 켜졌을 때 mountcontrol queue에 정확한 `goto_target` command가 들어가는 테스트.
- mount_control off 상태에서 SkySafari 동작이 기존 push-to로 유지되는 테스트.
- target epoch 변환 테스트.
- GoTo 중 stop/abort command 우선순위 테스트.

## 현재 결론

현재 구조에서 가장 자연스러운 다음 단계는 SkySafari target 수신과 mountcontrol GoTo를 직접 연결하기 전에, 동작 모드를 명확히 나누는 것이다.

권장 모드:

```text
push_to       기존 동작. SkySafari target을 PiFinder Object Details로만 보냄.
goto_confirm target을 띄운 뒤 사용자가 PiFinder에서 GoTo 확인.
goto_auto    SkySafari target 수신 후 INDI GoTo까지 자동 실행.
```

구현상 첫 번째 연결 지점은 `pos_server.handle_goto_command(...)`이지만, 안전한 설계를 위해 `mountcontrol_queue` 전달 방식과 사용자 설정 option을 먼저 추가하는 것이 좋다.
