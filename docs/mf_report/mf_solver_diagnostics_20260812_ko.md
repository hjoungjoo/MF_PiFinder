# 풀프레임 4단 기본 활성화·솔버 진단 개선 실기 리포트 (2026-08-12)

## 목적

좋은 하늘에서 SEP는 충분한 별을 검출하는데 Cedar가 실패한 것처럼 보인
현상을 분석하고, 풀프레임 4단 경로의 기본 활성화 상태와 실제 동작을
검증했다. 기존 `/api/solution`의 `Centroids`는 **최종 성공 경로의 검출 수**라
SEP가 구제하면 Cedar 검출 수가 가려졌다. 따라서 단순 별 개수 부족인지,
Cedar 게이트 손실인지, tetra3 패턴 매칭 실패인지 구분할 수 없었다.

## 당일 변경

1. `default_config.json`에서 `solver_cedar_fullframe`과
   `solver_center_first`를 `true`로 전환했다. 이에 따라 기본 경로는
   Cedar 중앙 → Cedar 전체 → SEP 중앙 → SEP 전체의 4단 캐스케이드다.
   `solver_cedar_ff_gates=true`, `solver_sep_fallback=true`도 유지했다.
2. `SolveDiagnostics`와 `/api/solution`에 다음 관측 필드를 추가했다.

| 필드 | 의미 |
| --- | --- |
| `CedarRawCentroids` | Cedar 풀프레임 원검출 수 |
| `CedarGatedCentroids` | 품질·지평선 게이트 후 수 |
| `CedarCenterCentroids` | 중앙 정사각 1단 투입 수 |
| `SepCentroids` | 같은 프레임 SEP 검출 수 |

기존 `Centroids`는 자동 노출 및 외부 API 호환성을 위해 변경하지 않았다.
새 필드는 검출기가 실행되지 않았거나 구버전 메시지이면 `null`이다.

## 시험 조건

- 장비: 현재 연결된 PiFinder, Raspberry Pi 5, `imx462_color`
- 노출/게인: 1.0 s / 30
- 하늘 밝기: SQM 약 16.97 mag/arcsec²
- 표시 프레임: 980×980 기준 p50 65/255, 사분면 중앙값 63–68,
  포화 비율 0.006%
- 절차: 단위·회귀 테스트 후 `pifinder.service`를 재시작하고
  `/api/solution`의 연속 성공 결과를 확인

## 결과

| 표본 | 성공 경로 | Cedar 원검출 | 게이트 후 | 중앙 투입 | SEP | 매치 | RMSE |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `cedar_ff_center` | 114 | 90 | 64 | 48 | 38 | 57.2″ |
| 2 | `cedar_ff` | 120 | 88 | 58 | 48 | 71 | 16.6″ |

회귀 테스트는 `test_pointing_estimate.py`, `test_api_extensions.py`,
`test_solver_cedar_fullframe.py`의 **36개 전부 통과**했다. 서비스 재시작 후
`pifinder.service`가 active 상태이며 새 API 필드가 실제 응답에 포함됨을
확인했다.

## 판정

관측 당시 Cedar는 별을 못 본 것이 아니다. 원검출 114–120개 중 게이트 후
88–90개, 중앙 경로에도 58–64개가 남았고 Cedar가 두 종류의 풀프레임 경로로
직접 성공했다. 따라서 앞서 SEP 성공의 `Centroids=48`만 보고 Cedar 검출
부족으로 해석할 수 없었던 것이 핵심 진단 문제였다.

새 필드로 이후 실패를 다음과 같이 즉시 분류할 수 있다.

- `CedarRawCentroids`가 낮음: 노출·초점·구름·Cedar σ8 감도 문제
- 원검출 대비 `CedarGatedCentroids` 급감: 포화·엣지·웜픽셀·클러스터 또는
  지평선 게이트 문제
- Cedar 수는 충분하나 SEP만 성공: Cedar 센트로이드 품질/분포 또는 tetra3
  패턴 매칭 단계 문제
- 두 검출 수 모두 충분하나 전 경로 실패: FOV·좌표 변환·DB/타임아웃을
  우선 조사

결론적으로 풀프레임 4단 기본 경로는 현재 하늘에서 정상 작동하며, 이번
변경은 솔빙 정책을 추가로 바꾸지 않고 실패 원인을 관측할 수 있게 했다.
