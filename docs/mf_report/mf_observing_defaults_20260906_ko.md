# 2026-09-06 관측 튜닝의 설치 기본값

새 설치에서 별 기반 자동 노출과 적응형 솔빙이 바로 활성화되도록
`default_config.json`에 다음 값을 저장한다.

| 설정 | 기본값 |
| --- | --- |
| `camera_exp` | `auto_star` |
| `camera_auto_star_framewise` | `true` |
| `livecam_solver_preprocess_enabled` | `true` (기존 LiveCam 내부 기본값도 동일) |
| `solver_preprocess_mode` | `auto` |
| `solver_preprocess_async` | `false` (기존 호환 설정) |
| `solver_optics_fov_gate` | `true` |
| `solver_optics_fullframe_fov` | `true` |

RAW 솔빙이 연속 성공하면 비동기로 전환하고 실패하면 해당 프레임을
동기로 복구한다. 전처리 프레임 제공·확인 간격은 변경하지 않는다.
광해에 의한 노출 과감소 방지와 별 재탐색은 자동 노출 코드의 기본 동작이다.

SkySafari 정렬은 정지 상태의 유효한 직전 솔빙(최대 5초)을 사용한다.
새 솔빙을 기다리지 않고 응답하며, 유효한 결과가 없으면 즉시 실패를 반환한다.
Push 화면 하단의 작은 이동 상태·솔빙 시도 간격 표시와 `WAIT` 축약도 포함한다.

카메라 종류·렌즈·마운트 연결 주소와 현장 정렬 픽셀은 장비별 설정으로 유지한다.
기존 `PiFinder_data/config.json`의 명시적인 사용자 설정은 새 기본값보다 우선한다.
설치 및 업데이트는 서브모듈 초기화 후 공통 helper로 tetra3 import 링크를 만든다.

검증은 저장 설정이 없는 임시 데이터 디렉터리에서 실제 Config와 LiveCam
설정 로더를 실행하고, 기존 저장값 우선순위와 적응형 정책 전환을 확인한다.
관련 자동 노출·정렬·프로토콜·솔버 회귀 테스트와 설치 링크 테스트도 실행한다.

업로드 대상만 추출한 사본에서 회귀 테스트 312개와 Push 화면 smoke 테스트
2개가 통과했다. 해당 사본의 전체 Python 코드·테스트 Ruff 검사 및 변경한
Python 파일 17개의 포맷 검사, 설치 스크립트 Bash 구문 검사도 통과했다.
변경 파일 10개의 별도 타입 검사와 커밋 훅의 전체 194개 소스 타입 검사도 통과했다.

커밋 훅의 smoke 수집은 업로드 대상 밖의 미추적 chart/equipment/GPS 테스트가
남아 있는 작업 디렉터리에서 ImportError 3건으로 실패했다. 업로드 사본에서는
동일한 `.nox/smoke_tests` 환경의 `pytest -m smoke`로 7개 모두 통과했다.
따라서 동일 검사를 완료한 뒤 해당 커밋에 한해 중복 nox 훅을 생략했다.
