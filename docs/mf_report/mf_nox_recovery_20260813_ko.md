# 2026-08-13 NOX 실패 원인 및 복구 리포트

작성일: 2026-08-13  
대상: `16fbaf4c` 이후 오늘 추가된 solver 진단·캐스케이드와 INDI serial 변경  
상태: 로컬 전체 NOX 실행 통과 / 원격 Python 3.9 workflow 확인 예정

## 현상

마지막 성공 커밋 `f4c6a0d8` 다음의 push부터 GitHub Actions NOX가 연속 실패했다.
부분 기능 시험과 Raspberry Pi Python 3.11 시험은 통과했지만 push 전 CI 전체
세션을 실행하지 않아 실패가 이후 커밋에 누적됐다.

## 확인된 원인

1. `solver.py`에 Python 3.10부터 지원되는 `int | None` annotation 8개가 추가됐다.
   NOX는 Python 3.9에서 모듈을 import하므로 smoke/unit/UI test collection이 모두
   `TypeError`로 중단됐다.
2. `solver.py`가 Ruff 0.4.8의 format 결과와 일치하지 않았다.
3. INDI 자동탐색 telemetry 검증에서 `self.client`의 Optional narrowing이 mypy에
   증명되지 않았다.
4. runtime requirement에 `pyserial`을 추가했지만 개발 requirement에 type stub을
   추가하지 않았다. 첫 mypy pass 뒤 자동 설치된 stub 때문에 기존
   `# type: ignore`가 두 번째 strict pass에서 unused ignore로 실패했다.

## 수정

- solver 진단 인자를 `Optional[int]`로 바꿔 Python 3.9에서 import 가능하게 했다.
- Ruff 0.4.8로 전체 Python tree의 format을 맞췄다.
- INDI client를 local 변수로 고정하고 명시적 `None` 분기 뒤 상태를 조회했다.
- `types-pyserial`을 `requirements_dev.txt`에 추가하고 불필요한 ignore를 제거했다.

## 검증 기준과 결과

CI workflow와 같은 순서로 다음 범위를 검사했다.

```text
lint: passed
format: 286 files formatted
mypy: 170 source files, no issues
smoke: 7 passed
unit: 1164 passed
ui_tests: 277 passed, 2 skipped
```

위 결과는 기존 가상환경에서 명령을 따로 실행한 결과뿐 아니라, 새 `.nox` 세션을
만들어 다음 단일 명령을 실행한 결과와도 일치한다.

```bash
nox -s lint format type_hints smoke_tests unit_tests ui_tests
```

로컬 NOX는 장비에 설치된 Python 3.11 fallback을 사용했다. 따라서 Python 3.9
최종 호환 판정은 아래 원격 workflow 결과로 확정한다.

최종 완료 조건은 수정 커밋 push 뒤 GitHub Actions
`nox -s lint format type_hints smoke_tests unit_tests ui_tests`가 Python 3.9에서
성공하는 것이다. 이후 Python 변경은 집중 시험 결과만으로 push하지 않고 이 전체
세션과 원격 workflow 결과를 함께 확인한다.
