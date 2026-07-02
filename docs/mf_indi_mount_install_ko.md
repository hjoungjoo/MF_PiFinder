# MF PiFinder INDI 마운트 제어

이 문서는 Raspberry Pi 4와 Raspberry Pi 5 Bookworm 64-bit 빌드에서 사용할 수 있는 선택형 INDI 마운트 제어 작업을 설명합니다.

이 기능은 기본값이 꺼짐입니다. `mount_control` 설정을 켜기 전까지 일반 PiFinder 설치에서는 PyIndi를 import하지 않고 INDI 마운트 제어 프로세스도 시작하지 않습니다.

설치 스크립트는 Raspberry Pi 4 Model B Bookworm 64-bit에서 검증했습니다. Pi 5와 CM5도 같은 Bookworm 64-bit 패키지와 aarch64 빌드 경로를 사용하며, 스크립트에는 Pi 4 전용 경로나 모델별 분기가 없습니다.

## 현재 범위

INDI 마운트 제어는 실험 기능입니다. 먼저 INDI Telescope Simulator로 테스트하고, 실제 마운트는 실내의 안전한 상태에서 충분히 확인한 뒤 야외에서 사용하세요.

이번 1차 통합 범위는 다음과 같습니다.

- PyIndi를 통한 INDI 서버 연결
- telescope/mount 장치 자동 감지
- PiFinder의 위치와 UTC 시간 동기화
- PiFinder plate-solve RA/Dec 기준 마운트 Sync
- Object Details 화면에 표시된 대상 GoTo
- Stop 명령
- 작은 RA/Dec 오프셋 기반 수동 이동

구버전 참고 브랜치에 있던 자동 target refinement, drift compensation, INDI alignment subsystem 관리 기능은 이번 1차 모듈화 포트에는 포함하지 않았습니다.

## INDI 지원 설치

PiFinder 체크아웃에서 전용 설치 스크립트를 실행합니다.

```bash
cd ~/PiFinder
bash scripts/install_indi_mount.sh
```

이 스크립트는 INDI, INDI third-party 드라이버, PyIndi, INDI Web Manager, Chrony GPS 시간 동기화 지원을 설치합니다. 컴파일 중에는 `pifinder` 서비스를 잠시 멈추고, 완료 후 다시 시작합니다.

INDI Web Manager는 현재 `FastAPI 0.103.2`, `Starlette 0.27.0`, `Uvicorn 0.23.2`, `AnyIO 3.7.1` 조합으로 고정되어 있습니다. 최신 Starlette 계열에서는 INDI Web Manager의 기존 템플릿 호출 방식과 맞지 않아 Web UI 루트 페이지가 `500 Internal Server Error`를 반환할 수 있습니다.

필요하면 환경 변수로 버전과 빌드 병렬 수를 바꿀 수 있습니다.

```bash
INDI_VERSION=v2.1.6 INDI_3RDPARTY_VERSION=v2.1.6.2 JOBS=2 bash scripts/install_indi_mount.sh
```

Pi 4에서는 메모리 여유를 위해 기본 `JOBS=2`를 권장합니다. Pi 5나 CM5에서는 냉각과 전원 상태가 안정적이면 `JOBS=3` 또는 `JOBS=4`로 빌드 시간을 줄일 수 있습니다.

### Pi 4/Pi 5 공용 바이너리 아카이브 설치

소스 빌드 대신 미리 만든 Bookworm 64-bit/aarch64 아카이브를 사용할 수 있습니다.

```bash
cd ~/PiFinder
bash scripts/install_indi_mount_archive.sh dist/mf-pifinder-indi-bookworm-arm64-v2.2.3.1-current.tar.gz
```

전체 PiFinder 설치 스크립트인 `pifinder_setup.sh`에서도 같은 아카이브 설치 경로를 사용할 수 있습니다.

```bash
cd ~
PIFINDER_INDI_ARCHIVE="$HOME/PiFinder/dist/mf-pifinder-indi-bookworm-arm64-v2.2.3.1-current.tar.gz" \
  bash "$HOME/PiFinder/pifinder_setup.sh"
```

`PIFINDER_INSTALL_INDI_ARCHIVE`는 기본값이 `auto`입니다. `dist/mf-pifinder-indi-bookworm-arm64-*.tar.gz` 파일이 있거나 `PIFINDER_INDI_ARCHIVE`가 지정되어 있으면 INDI 지원을 설치하고, 없으면 일반 PiFinder 설치만 진행합니다. 강제로 끄려면 다음처럼 실행합니다.

```bash
PIFINDER_INSTALL_INDI_ARCHIVE=false bash "$HOME/PiFinder/pifinder_setup.sh"
```

새 바이너리 아카이브를 만들 때는 다음 스크립트를 사용합니다.

```bash
cd ~/PiFinder
bash scripts/package_indi_mount_archive.sh
```

최신 소스 빌드 스크립트는 Pi 5에서 빌드하더라도 Pi 4 호환성을 위해 `-march=native`, `-mcpu=*`, `-mtune=*`를 제거하고 `-march=armv8-a`를 사용합니다.

## 마운트 드라이버 설정

INDI Web Manager를 엽니다.

```text
http://pifinder.local:8624
```

mDNS 이름이 동작하지 않으면 PiFinder IP 주소를 사용합니다.

```text
http://<pifinder-ip>:8624
```

Profile을 만들고 사용하는 마운트에 맞는 telescope driver를 선택합니다. 필요하면 Auto Start와 Auto Connect를 켠 뒤 profile을 시작합니다. 흔한 드라이버는 EQMod, LX200, iOptron, Celestron, Telescope Simulator입니다.

활성 INDI profile이 `LX200 OnStepX`를 사용할 때는 PiFinder 웹 UI의 다음 영역에서 연결 방식을 설정할 수 있습니다.

```text
INDI > LX200 OnStepX Driver Connection
```

USB 연결은 감지된 `/dev/serial/by-id`, `/dev/ttyUSB*`, `/dev/ttyACM*` 목록에서 선택하거나 수동으로 포트 이름을 입력합니다. 네트워크 연결은 AP에 접속된 장치 목록에서 IP를 선택하거나, 목록에 없으면 IP/host와 TCP port를 수동으로 입력합니다. OnStep 네트워크 연결의 기본 TCP port는 `9999`입니다.

## PiFinder INDI 웹 메뉴

PiFinder 웹 UI 상단 메뉴에는 `INDI` 항목이 별도로 표시됩니다. 이 페이지에서 INDI Web Manager로 바로 이동하고, 실행 중인 INDI profile에서 active driver 이름을 읽습니다. OnStepX 전용 설정과 제어 영역은 active driver가 `LX200 OnStepX`일 때만 표시됩니다.

### Current INDI Driver State

활성 INDI profile, active driver, 사용 가능한 driver 속성을 표시합니다. OnStepX의 연결 방식, serial/network 설정, OnStep 위치, OnStep UTC 시간은 INDI profile이 시작되고 `LX200 OnStepX` 드라이버가 로드된 뒤에 표시됩니다.

### Location and Time

`Location and Time` 영역은 `LX200 OnStepX`에서 표시되며 PiFinder의 현재 위치와 UTC 시간을 OnStep에 전송합니다.

- 위치는 GPS lock이 있으면 GPS/loaded location 값을 사용합니다.
- GPS lock이 없으면 `GPS Lock: Not locked`로 표시하고, PiFinder `Locations`의 기본 위치를 `Location to Send`로 사용합니다.
- UTC 시간 입력칸은 화면을 열어 둔 동안 초 단위로 계속 갱신됩니다.
- `Reload Current Values`는 PiFinder 위치/시간과 OnStep의 현재 위치/시간 표시를 다시 읽습니다.
- `Send Location and Time`을 누르면 서버가 요청을 받은 바로 그 시점의 PiFinder system UTC를 다시 계산해서 OnStep에 전송합니다. 따라서 브라우저나 휴대폰 시간이 틀려 있어도 최종 전송 시간은 PiFinder 기준입니다.
- LX200 OnStepX 드라이버는 PiFinder용 커스텀 INDI 드라이버입니다. 위치/시간 동기화는 INDI `GEOGRAPHIC_COORD`/`TIME_UTC` 전체 벡터를 통해 처리하며, 드라이버 내부에서 OnStep LX200 명령으로 변환합니다.
- `indi_setprop` CLI로 일부 element만 쓰는 방식은 피합니다. PiFinder는 PyIndi 전체 벡터 전송을 사용합니다.
- 한국 시간대처럼 UTC+9인 환경에서 INDI `TIME_UTC.OFFSET`은 `+9.00`으로 전송되고, 드라이버가 OnStep의 `:SG-09:00#` convention으로 변환합니다.

### Mount Control

`Mount Control` 영역은 `LX200 OnStepX`에서 표시되며 간단한 초기화/주차/수동 이동 기능을 제공합니다.

- 현재 Park/Unpark 상태를 표시합니다.
- `At Home`, `Return Home`, `Park`, `Unpark`, `Set-Park` 명령을 보낼 수 있습니다.
- Slew Rate는 OnStep의 0-9 단계를 그대로 사용합니다. `0`은 Off, `1`은 `1/2x`, `9`는 `Max`입니다.
- 방향 버튼은 누르고 있는 동안 이동하고, 손을 떼면 정지 명령을 보냅니다.
- 대각선 버튼은 North/South와 East/West 명령을 함께 보냅니다.

이 웹 제어는 INDI 드라이버에 직접 명령을 보내는 보조 UI입니다. Object Details 화면의 숫자 키 기반 Sync/GoTo 기능과 함께 사용할 수 있습니다.

## PiFinder 제어 켜기

PiFinder UI에서 다음 메뉴로 이동합니다.

```text
Tools > Experimental > Mount Control > On
```

이 값을 변경하면 선택형 `MountControl` 프로세스를 깨끗하게 시작하거나 종료하기 위해 PiFinder가 재시작됩니다.

Mount Control 프로세스는 켜져 있어도 시작 직후 INDI에 바로 연결하지 않습니다. Object Details 화면에서 `1`, Sync, GoTo 같은 마운트 명령을 실행할 때 INDI 연결을 초기화합니다.

고급 설정 키는 `default_config.json`에 있습니다.

```json
"mount_control": false,
"mount_control_indi_host": "localhost",
"mount_control_indi_port": 7624,
"onstep_connection_type": "network",
"onstep_serial_port": "",
"onstep_network_host": "",
"onstep_network_port": 9999
```

## Object Details 숫자 키 맵

Mount Control이 켜져 있으면 Object Details 화면의 숫자 키가 마운트 명령을 보냅니다.

| 키 | 동작 |
| --- | --- |
| 0 | 마운트 정지 |
| 1 | INDI 연결 초기화, PiFinder solve가 있으면 Sync |
| 2 | 현재 step 크기만큼 South 이동 |
| 3 | step 크기 줄이기 |
| 4 | 현재 step 크기만큼 West 이동 |
| 5 | 현재 표시 중인 대상 GoTo |
| 6 | 현재 step 크기만큼 East 이동 |
| 7 | 현재 PiFinder solve 위치로 마운트 Sync |
| 8 | 현재 step 크기만큼 North 이동 |
| 9 | step 크기 키우기 |

수동 이동은 현재 마운트 RA/Dec 좌표에서 작은 GoTo 오프셋을 보내는 방식입니다. 기본 step은 1도이고, `3`은 절반으로 줄이며 `9`는 두 배로 키웁니다.

## 로그와 상태 확인

PiFinder 로그에는 `MountControl.Indi` 이름으로 마운트 제어 로그가 남습니다.

상태 파일은 다음 위치에 기록됩니다.

```text
~/PiFinder_data/mount_control_status.json
```

확인에 유용한 명령은 다음과 같습니다.

```bash
systemctl status indiwebmanager.service
systemctl status pifinder.service
journalctl -u indiwebmanager.service -n 100
tail -n 100 ~/PiFinder_data/pifinder.log
```

## 안전 테스트 순서

1. INDI 지원을 설치합니다.
2. INDI Web Manager에서 Telescope Simulator를 시작합니다.
3. PiFinder Mount Control을 켭니다.
4. 아무 대상의 Object Details 화면을 엽니다.
5. `1`을 눌러 초기화합니다.
6. PiFinder solve가 잡힌 뒤 `7`을 눌러 Sync합니다.
7. `5`를 눌러 GoTo를 보냅니다.
8. `0`으로 Stop 동작을 확인합니다.

시뮬레이터 동작을 이해한 뒤 실제 마운트 테스트로 넘어가세요.
