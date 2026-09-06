# 비동기 전처리 결과 미반영으로 인한 솔빙 불가

## 현장 증상

2026-09-06 낮은 고도 지상 조명 자동 노출 보정 뒤, 사용자가 별이 보이는
고도로 이동했다. 자동 노출은 약 340~420 ms까지 확보됐지만 지향 좌표가
`unavailable`이고 Auto(Star) 피드백의 별 매칭은 계속 0이었다.

로그에는 `raw_solved=False`와 함께
`selected=preprocessed_cedar_center/preprocessed_cedar_full`이 반복됐다.
`_solve_center_first_remainder()`는 RA가 있는 성공 결과에만 경로를 선택하므로,
이는 전처리 솔브가 실제 성공했음을 뜻한다.

## 원인과 조치

`solver_preprocess_async=true` 경로는 완료된 전처리 결과를 원본 솔브의
좌표 편향 보정에만 사용한다. 더 최신 원본 프레임 뒤에 과거 결과를 게시하지
않도록, 원본 솔브 실패 시 성공한 전처리 결과 대신 `solution = {}`로 처리한다.
원본에서는 별이 부족하고 전처리에서만 솔빙이 되는 시야에서는 계속 좌표가 없다.

현재 장치 설정과 `default_config.json`의 `solver_preprocess_async`를 `false`로
전환했다. 기존 동기 경로는 프레임과 솔브 결과를 함께 처리하며, 전처리 결과도
품질·연속성 검증을 거쳐 게시한다. 비동기 결과를 무조건 게시하도록 변경하지 않았다.
동기 전처리 때문에 원본 우선 비동기 모드보다 솔브 갱신 간격이 길어질 수 있다.

## 검증

설정 JSON 구문 및 변경분 공백 검사, 프레임 대응·전처리 편향 관련 테스트
11개가 통과했다. 서비스 재시작 후 21:30:08 실기 상태에서 `source=solve`,
`solved.valid=true`, `solve_success=true`, 별 매칭 12개를 확인했다.
그 시점 노출은 422488 us였으며 자동 모드를 유지했다.
