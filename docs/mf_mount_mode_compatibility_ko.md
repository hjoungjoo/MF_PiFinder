# MF PiFinder 마운트 모드 호환성 점검 계획

작성일: 2026-07-03

이 문서는 SkySafari, IMU no-solve fallback, INDI mount control, OnStepX 연동이
Alt/Az 전용 가정에 묶이지 않고 적도의 계열 마운트에서도 동작하도록 확인하기 위한
기준 문서다. 이후 수정과 현장 테스트는 이 문서의 항목을 기준으로 진행한다.

## 목표

- PiFinder `mount_type = "Alt/Az"`와 `"EQ"` 모두에서 SkySafari 위치 응답이
  올바른 마운트 모드로 보이게 한다.
- plate solve 전에는 IMU의 실제 수평 방향을 RA/Dec로 변환해서 SkySafari에
  제공한다.
- plate solve 전 SkySafari GoTo 후 사용자가 수동으로 별을 중앙에 놓고
  Sync/Align을 누르면, 마지막 GoTo 대상과 현재 IMU 방향 차이를 보정값으로
  저장한다.
- plate solve가 성공하면 PiFinder의 solve 기반 pointing을 우선하고, no-solve
  IMU 보정값은 즉시 초기화한다.
- INDI GoTo, Sync, guide/manual movement는 특정 마운트 형식에 묶지 않고
  INDI telescope driver의 RA/Dec 및 guide/motion interface를 통해 동작한다.

## 현재 소스 점검 결과

| 영역 | 현재 상태 | 조치 |
| --- | --- | --- |
| PiFinder push-to UI | `calc_utils.aim_degrees()`가 `mount_type == "Alt/Az"`이면 Alt/Az 차이, 그 외에는 RA/Dec 차이를 계산한다. | 기존 구조 유지, 회귀 테스트 대상 |
| SkySafari 현재 좌표 | `pos_server.get_telescope_ra/dec()`는 solve 좌표가 있으면 solve 기반 JNOW, 없으면 IMU Alt/Az를 RA/Dec로 변환한다. | 유지 |
| SkySafari status `:GW#` | 기존에는 항상 `AT1`을 반환해서 Alt/Az처럼 보였다. | `mount_type`과 override 설정을 반영하도록 수정 |
| no-solve IMU 보정 | SkySafari Sync 시 마지막 GoTo 대상과 현재 IMU Alt/Az 차이를 저장한다. | solve 성공 시 초기화 보장 |
| INDI GoTo | `goto_target`은 RA/Dec를 INDI `EQUATORIAL_EOD_COORD`로 전달한다. | 마운트 독립으로 유지 |
| INDI guide/manual move | `north/south/east/west` guide motion을 INDI driver에 전달한다. | 마운트 독립으로 유지, 실제 장치별 확인 필요 |
| OnStepX 위치/시간 | OnStepX driver일 때만 표시/동작한다. | OnStepX 전용으로 유지 |

## SkySafari LX200 Status 정책

PiFinder는 SkySafari의 `:GW#` 요청에 LX200-style status 문자열을 반환한다.

기본 정책:

| PiFinder 설정 | 반환 |
| --- | --- |
| `mount_type = "Alt/Az"` | `AT1` |
| `mount_type = "EQ"` | `PT1` |

문자 의미:

- 첫 글자: mount geometry. `A`는 Alt/Az, `P`는 polar/equatorial 계열로 사용한다.
- 두 번째 글자: tracking 상태. PiFinder는 현재 `T`로 응답한다.
- 세 번째 글자: alignment 상태. PiFinder는 기존 호환성을 유지하기 위해 `1`로 응답한다.

일부 mount/app 조합은 German equatorial을 별도 코드로 기대할 수 있다. 이 경우
웹 UI에서 다음 메뉴로 값을 바꾸거나 설정 파일에서 직접 override할 수 있다.

```text
INDI > SkySafari Mount Mode > SkySafari LX200 Mount Code
```

```json
"skysafari_lx200_mount_code": "G"
```

지원 값:

| 값 | 의미 |
| --- | --- |
| `auto` | `mount_type` 기준 자동 선택 |
| `A` | Alt/Az로 강제 |
| `P` | Polar/equatorial로 강제 |
| `G` | German equatorial로 강제 |

## no-solve IMU 정렬 흐름

1. PiFinder가 아직 plate solve를 갖고 있지 않다.
2. SkySafari 사용자가 밝고 찾기 쉬운 별을 선택하고 GoTo를 누른다.
3. PiFinder는 마지막 SkySafari target RA/Dec를 저장한다.
4. 사용자가 마운트를 수동/가이드 조작으로 움직여 아이피스 중앙에 별을 놓는다.
5. SkySafari에서 Sync/Align을 누른다.
6. PiFinder는 target RA/Dec를 현재 시간/위치 기준 Alt/Az로 변환한다.
7. 현재 IMU Alt/Az와 target Alt/Az의 차이를 보정값으로 저장한다.
8. 이후 plate solve 전 SkySafari 위치 응답은 IMU Alt/Az에 이 보정값을 적용한 뒤
   RA/Dec로 변환한다.
9. plate solve가 성공하면 보정값을 초기화하고 solve 기반 pointing으로 전환한다.

이 흐름은 mount axis가 Alt/Az인지 EQ인지에 의존하지 않는다. IMU는 실제 하늘
수평 방향을 측정하고, SkySafari에는 항상 RA/Dec를 반환하기 때문이다.

## 구현 체크리스트

- [x] no-solve IMU 보정값 저장 구조 추가
- [x] solve 좌표 사용 가능 시 IMU 보정값 초기화
- [x] no-solve Sync에서 PiFinder plate-solve align을 호출하지 않도록 분리
- [x] SkySafari `:GW#`가 `mount_type`을 반영하도록 수정
- [x] `skysafari_lx200_mount_code` override 추가
- [x] INDI 웹 UI에 SkySafari mount mode 공통 설정 추가
- [x] 관련 unit test 추가
- [x] 서비스 재시작 후 상태 확인
- [ ] 실제 SkySafari Alt/Az profile 연결 확인
- [ ] 실제 SkySafari EQ/German profile 연결 확인

## 테스트 항목

### Unit Test

| 테스트 | 기대 결과 |
| --- | --- |
| `mount_type = "Alt/Az"`에서 `:GW#` | `AT1` |
| `mount_type = "EQ"`에서 `:GW#` | `PT1` |
| `skysafari_lx200_mount_code = "G"` | `GT1` |
| no-solve Sync | IMU 보정값 active |
| solve 좌표 사용 가능 | IMU 보정값 inactive |
| SkySafari GoTo with INDI GoTo off | 기존 push-to target만 생성 |
| SkySafari GoTo with INDI GoTo on | `goto_target` queue 생성 |
| mount_control off | SkySafari 위치 응답과 push-to만 동작 |

### Hardware Test

| 단계 | Alt/Az | EQ/적도의 |
| --- | --- | --- |
| SkySafari 연결 | 연결 유지, 좌표 갱신 | 연결 유지, 좌표 갱신 |
| plate solve 전 위치 | IMU fallback 위치 표시 | IMU fallback 위치 표시 |
| GoTo 대상 push | Object Details 대상 생성 | Object Details 대상 생성 |
| INDI GoTo on | mount GoTo 시작/완료 | mount GoTo 시작/완료 |
| no-solve Sync 보정 | 같은 별 주변 좌표 개선 | 같은 별 주변 좌표 개선 |
| plate solve 후 | solve 위치로 전환, 보정 초기화 | solve 위치로 전환, 보정 초기화 |
| guide/manual move | 누르는 동안 이동, release/stop 정지 | driver 기준 N/S/E/W 이동, release/stop 정지 |

## 주의 사항

- IMU no-solve 보정은 plate solve를 대체하지 않는다. 초기 탐색 보조용이다.
- `mount_type = "EQ"`일 때 PiFinder push-to UI는 RA/Dec 차이를 보여준다.
- SkySafari의 telescope profile mount type과 PiFinder `mount_type`은 가능한 한
  일치시키는 것이 좋다.
- OnStepX가 아닌 INDI driver는 위치/시간 UI가 다르게 보일 수 있지만, GoTo/Sync는
  표준 INDI telescope property가 있으면 같은 경로를 사용한다.
