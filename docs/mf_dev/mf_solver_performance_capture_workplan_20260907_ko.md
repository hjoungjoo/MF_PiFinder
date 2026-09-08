# 광해별 솔빙 속도·정확도 검토 및 단계별 시험 작업서

작성: 2026-09-07. 기준 코드: `5d59a442` 이후 자료 수집 기능 추가 작업.

2026-09-08 후속: 다음 실제 관측의 개선 우선순위·GoTo/펄스 검증·채택 기준은
[솔빙·GoTo 통합 관측 작업서](mf_solver_goto_observation_workplan_20260908_ko.md)를 먼저 사용한다.
현장 작성용 [기록표](mf_solver_goto_field_sheet_20260908_ko.md)를 함께 제공한다.
이 문서는 기존 수집 기능의 사용법과 저장 형식 참고 자료로 유지한다.

## 1. 이번 작업 범위

구현한 것은 **시험 자료 수집 기능**이다. 아래 성능 개선안은 아직 적용하지 않았다.
전처리 실행 간격, RAW/전처리 선택 정책, 노출·게인, 품질·연속성 임계값,
마운트 제어는 변경하지 않는다. 수집은 기본 OFF이며 명시적으로 시작해야 한다.
단계 선택은 실험 이름표일 뿐 해당 최적화를 활성화하는 기능이 아니다.

목표는 광해가 강하거나 약할 때 **승인된 새 좌표를 빠르고 꾸준하게 공급하면서
실제 정렬 지점의 오차를 악화시키지 않는 것**이다. 실내 기능 점검은 별이 있는
하늘의 성능 검증을 대신하지 않는다.

## 2. 최종 코드 검토

| 항목 | 확인된 사실 | 제안 및 주의점 |
| --- | --- | --- |
| 불가능한 탐색 | native Cedar는 최종 최소 6매치, SEP는 7매치를 요구하지만 더 적은 후보로 시작하는 호출이 있음 | 경로별 최종 기준보다 후보가 적으면 사전 제외. RAW 512의 기존 정책에 일괄 적용하지 않음 |
| SEP 검출 잔류 | 전처리 우선 상태에서 RAW 대체 솔빙을 생략해도 SEP 검출 호출은 남음 | 별 표시·후보 통계·backoff 용도를 분리한 뒤 필요한 시점에 검출 |
| RAW 재조회 | 메인은 대응 RAW를 확보하지만 SEP는 공유 상태에서 최신 RAW를 다시 읽음 | 이미 확보한 동일 RAW를 직접 전달. 프레임 ID 검사를 없애지 않음 |
| 전환 조건 | raw_solved는 전처리 이전 대체 경로도 포함. 3회 패턴 성공과 bias 준비 조건이 분리됨 | 실제 경로·승인률·시간·잔차·보정 준비 상태를 함께 평가 |
| 비동기 범위 | 백그라운드는 전처리/SEP 검출, 후속 Cedar 검출·좌표 계산은 메인에서 실행 | 완전 분리는 후순위. Tetra3 캐시와 Cedar 공유 메모리를 독립 소유해야 함 |
| 전환 부하 | clear_pending은 실행 중 작업을 중단하지 않음. 동기 복귀는 누적 창을 초기화 | 실행 중 작업과 새 동기 작업의 CPU 경합, 재워밍업 공백 측정 |
| 시간 누적 | 최대 5프레임이며 실제 누적 시간 길이는 일정하지 않음 | 프레임 건너뛰기·별 이동과 합성 영상 centroid 편향 확인 |
| 메모리 | np.stack/float32 변환과 중간 배열이 반복됨 | 버퍼 재사용 후보. 연산 순서 변경 시 픽셀·centroid 동등성 검증 |
| 지표 | 기존 processing_ms는 큐 전달·integrator 반영까지 포함하지 않음 | 입력 확보→실제 공유 좌표 반영의 monotonic 지연을 별도 평가 |
| 정확도 | RMSE는 별 매칭 잔차이며 절대 지향 오차가 아님 | RA_target/Dec_target, 기준 좌표 오차, 정지 산포, 모드 전환 차이를 함께 평가 |

근거 소스: `solver.py`, `sep_shadow.py`, `solver_scheduling.py`,
`preprocess_bias.py`, `solve_acceptance.py`, `mf_star_only_preprocess.py`,
`latest_frame_worker.py`, `tetra3/cedar_detect_client.py`.

9월 3일 광해 A/B 기록은 원본 품질 솔브 0/30, 전처리 28/29, 전처리 연속성 승인
27/29였다. 당시 원본 실패 탐색 중앙값 약 611ms, 전처리 약 2124ms, 전처리 후
탐색 약 10ms였다. **과거 설정과 CPU 경합 조건의 결과이며 현재 속도 기준은 아니다.**

## 3. 현장 수집 사용법

이 코드가 반영된 PiFinder 애플리케이션을 재시작한 뒤 사용한다. 수집 기능 추가만으로
서비스를 재시작하거나 실제 수집을 시작하지 않는다. 실제 관측 중 재시작은 이동을
종료한 뒤 수행한다. 웹 주소는 기존 PiFinder 주소 뒤에 `/solver-capture`를 붙인다.

예: `http://pifinder.local/solver-capture` (별도 웹 포트를 쓰면 그 주소를 유지).

1. **광해 정도만 선택하고 기록 시작**을 누른다. RAW·솔빙 결과·시간·설정·소스가
   자동 저장된다. 기본은 `baseline`, RAW 포함, 최대 60초/120시도/프로세스별 512MiB다.
2. 일반 수집에는 다른 설정이 필요 없다. 세부 비교 시험에서는 접힌 **세부 시험 설정**을
   열어 단계·메모·수집 내용을 바꿀 수 있다. 저장 부하 비교가 필요하면 **결과·시간만**
   60초와 같은 조건의 **RAW와 결과** 60초를 별도 수집한다.
3. 시작 요청 뒤 solver와 integrator 상태가 해당 session_id의 `recording`인지 확인한다.
   요청만 보이고 상태가 없으면 프로세스가 새 코드를 로드했는지 확인한다.
4. 선택 사항: 광원 방향·이동 시작/종료 등은 세부 시험 설정에 메모를 쓰고
   **현재 구간 표시**를 누른다. 구름·이동·실내 scene의 명시적 분류는 CLI로 가능하다.
   이미 시작한 솔빙은 이전 표시를 유지하고 다음 처리 프레임부터 새 표시를 사용한다.
   약 0.5초의 제어 확인 지연이 있으며, 프레임이 없으면 구간 이벤트도 기록되지 않는다.
5. 자동 종료 또는 **기록 종료** 후 두 기록 프로세스의 `complete` 또는 `error`를 확인한다.
   종료 전에 시작한 프레임/대기 저장분은 포함될 수 있다. `draining` 중에는 기다린다.
6. `directory`, `reason`, `dropped_records`, `raw_frames`를 확인해 원본 폴더를 보존한다.

CLI도 같은 요청을 사용한다. 웹과 CLI를 동시에 시작하면 중복 세션은 거부된다.

```bash
cd /home/pifinder/PiFinder/python

# 수집 부하가 작은 기준 구간
.venv/bin/python -m PiFinder.solver_capture start --scene light_pollution --stage baseline --mode telemetry --duration 60 --note '광해 방향, 고정 경통'
.venv/bin/python -m PiFinder.solver_capture status
.venv/bin/python -m PiFinder.solver_capture stop

# 앞 구간 저장 종료를 확인한 뒤 원본 수집
.venv/bin/python -m PiFinder.solver_capture start --scene light_pollution --stage baseline --mode raw --duration 60 --max-frames 120 --max-mib 512
.venv/bin/python -m PiFinder.solver_capture mark --scene transition --note '고도 상승 시작'
.venv/bin/python -m PiFinder.solver_capture mark --scene dark_sky --note '이동 종료, 별이 보이는 방향'
.venv/bin/python -m PiFinder.solver_capture stop
```

기본값은 최대 60초/120시도/기록 프로세스별 512MiB이며 먼저 도달한 제한에서
그 기록 작업자가 종료한다. 남은 디스크 공간이 약 128MiB 이하가 되면 종료한다.
용량 제한은 기록·영상 본문에 적용하며 초기 소스 사본·manifest·상태 파일은 별도다.
RAW 수집 기본은 솔버가 사용한 모든 시도를 대상으로 한다. 필요하면 `--raw-every 1`
등으로 **파일 저장만** 간격을 둘 수 있지만, 시간 누적 재생의 연속성이 줄어든다.
이는 전처리 실행 간격을 조절하지 않는다. 기록 작업자가 제한으로 종료해도 요청의
유효 시간이 남을 수 있으므로, 새 수집 전 `stop`과 종료 상태를 확인한다.

## 4. 저장 형식과 보존

위치: `~/PiFinder_data/captures/solver_sessions/<session_id>/`

| 파일 | 내용 |
| --- | --- |
| manifest.json | 스키마 버전, 요청 조건, 관련 설정, Git HEAD, 주요 소스 SHA-256, Python/OS/boot ID |
| source/ | 수집 시작 시 주요 처리 모듈의 소스 사본. 로컬 미커밋 변경도 보존 |
| solver.jsonl | 시도별 프레임/시각/IMU 메타데이터, 구간, 승인·보류, 후보 좌표/매칭, 처리 경로·시간·후보 수, 편향/스케줄링/광학 정보 |
| solver_NNNNNN.npz | 같은 frame_id의 원본 센서 방향 RAW와 실제 512 솔버 입력. 숫자 배열만, 무손실·무압축 |
| integrator.jsonl | 솔브 메시지별 공유 상태 게시 여부와 게시 직후 monotonic 시각, 실제 pointing/진단 정보 |
| solver_summary.json / integrator_summary.json | 종료 이유, 저장 건수·바이트·큐 누락 건수 |

각 RAW 파일의 파일명·SHA-256·바이트 수는 대응 solver.jsonl 레코드에 있다.
파일을 임시 이름으로 쓰고 이름 변경을 완료한 다음 JSONL에 연결한다. 갑작스러운
전원 차단 시 `.part`나 마지막 불완전 JSONL 줄이 남을 수 있다. 요약 없는 폴더도
삭제하지 말고 불완전 세션으로 보존한다. 자동으로 기존 관측 자료를 지우지 않는다.

RAW는 **카메라 전체 프레임이 아니라 솔버가 실제 사용한 프레임**이다. 카메라의
중간 프레임 건너뛰기와 저장 큐 누락은 다른 현상이다. 카메라 frame_sequence/
frame_id/촬영 시각과 recorder sequence/dropped_records를 함께 확인한다.
필요한 전체 센서 연속 스트림 수집 기능은 이번 범위에 포함하지 않는다.
누적 창에 들어갔지만 솔버 기록 큐에서 누락된 프레임은 복원할 수 없다. 또한 전체
별 데이터베이스와 외부 warm-pixel map은 자동 복사하지 않는다. 정확한 재생 시험 전
사용한 데이터베이스·보정 파일을 별도로 보관하고, 누적 초기화 이후 연속 구간을 확보한다.

저장은 별도 스레드에서 한다. solver 큐는 2개, integrator 큐는 32개로 제한한다.
저장이 밀리면 새 기록을 버리고 누락 수를 늘린다. 원본 복사와 JSON 변환 비용,
CPU·메모리·SD 경합은 0이 아니므로 telemetry/RAW 기준 구간 비교가 필요하다.
RAW와 처리 후 이미지는 서로 대체하지 않는다. 전처리 영상은 이번 기능에서 별도
저장하지 않으며 원본으로 재생한다. 자료는 시험 담당 PC로 폴더째 복사할 수 있다.

```bash
# 예: 상태에 나온 실제 session_id로 대체
.venv/bin/python -m PiFinder.solver_capture report /home/pifinder/PiFinder_data/captures/solver_sessions/SESSION_ID
```

report는 읽기 전용이며 RAW·소스 사본 SHA-256, 불완전 파일, 단계/환경별 건수와 지연 p95를
출력한다. 무결성 오류는 종료 코드 2다. `complete=false`, `state=error`, 큐 누락과
게시 짝 누락도 별도로 확인한다. ZIP/NPZ를 읽을 때는 `np.load(path, allow_pickle=False)`를
사용한다. 자동 보고서는 절대 지향 오차나 오솔브 진실값을 만들어내지 않는다.

## 5. 시간·좌표 해석 계약

- solver의 `input_ready_monotonic_ns`: 대응 입력을 확보한 직후. 노출 자체와
  그 이전의 카메라 처리·IPC 읽기 시간은 포함하지 않는다.
- `queue_put_return_monotonic_ns`: 솔빙 결과 큐 전달 호출 직후.
- integrator의 `published_monotonic_ns`: shared_state.set_solution 반환 직후.
- report의 `input_ready_to_publication_ms`: 같은 session/frame_id/exposure_end이고
  솔버 승인이 있으며 실제 게시된 두 기록만 연결한다. 서로 다른 boot의 monotonic
  시각을 빼거나, 게시 누락에 이전 좌표를 대입하지 않는다.
- 이 지연에는 기록 기능의 일부 부하가 포함된다. `capture_prepare_ms`는 기록
  준비 자체의 복사·직렬화 시간이며 디스크 작업 시간은 아니다.
- `raw_cascade_ms`는 전처리 이전 경로 전체, `raw_extract_ms`는 1차 검출이다.
  `raw_sep_wait_ms`는 SEP 준비·검출 대기 구간이며 백그라운드 SEP의 순수 CPU 시간과
  같지 않다. `stages`에는 실행된 중앙/전체 대체 경로의 경과 시간과 품질 솔브 여부가 있다.
- `preprocess_detect_ms`는 비동기에서는 과거 프레임의 worker 처리 시간이다.
  `preprocess_frame_id`, `generation`, `preprocess_background`를 함께 읽고
  현재 RAW의 처리 시간에 단순 합산하지 않는다. 비활성/워밍업의 0은 빠른 성공이 아니다.
- `candidate`는 연속성 검사 전 후보다. accepted=false이면 게시 좌표가 아니다.
  후보가 빈 경우는 품질 거부 또는 무패턴 등을 포함하며 세부 사유가 모두 분리되지는 않는다.
- frame_id와 exposure_end는 반드시 함께 사용한다. 재시작 뒤 frame_id가 재사용될 수 있다.
  manifest의 소스는 **기록 시작 시 디스크 파일**이므로 변경 뒤 프로세스 재시작 없이
  측정하면 실제 로드 코드와 다를 수 있다.
- 현재 수집 자료로는 API/LX200 송신 지연을 자동 측정하지 않는다. 외부 앱 체감 지연을
  평가할 때는 별도 클라이언트 관측 시각이 필요하다.

## 6. 단계별 실험 및 통과 기준

한 번에 하나의 변경만 적용한다. 각 단계는 기준→변경→기준 복귀 순서로 반복하고
광해 적음/강함/국소 광원/구름/이동 후 정착을 각각 수집한다. 정지 비교 중 수동 노출과
Auto Star 자료를 섞지 않는다. Auto Star 비교는 별도 그룹으로 분리한다.

| 단계 태그 | 구현·시험할 항목 | 필수 검증 |
| --- | --- | --- |
| baseline | 수정 전 telemetry와 RAW 각각 기록 | 수집 부하, 저장 누락, 승인률, 프레임 대응 확인 |
| candidate_gate | 최종 최소 매칭 수를 만족할 수 없는 탐색 제외 | 제외된 후보가 현행 경로에서 승인될 수 없음을 확인. RAW 512 정책 보존 |
| frame_reuse | 메인에서 확보한 대응 RAW를 SEP에 전달 | 카메라가 다음 프레임을 게시하는 경쟁 조건, frame_id/배열 동일성, 복구율 |
| lazy_sep | 필요 시점에 RAW SEP 검출 | LiveCam overlay, 후보 통계, backoff 상태 보존. 이미 필요한 전처리 간격 불변 |
| path_budget | 경로별 승인률/시간에 따른 탐색 순서·예산 | 광해 변화 시 재시도 회복, 어려운 정상 패턴 누락 여부, 실패 시간 감소 |
| mode_policy | 실제 성공 경로·품질·bias 준비를 함께 판단 | 경계선 성공 반복, 3회 성공 후 즉시 실패, RAW/전처리 좌표 차이 |
| async_worker | 후속 검출·좌표 계산까지 분리 | 독립 Tetra3/Cedar 메모리, generation 폐기, 오래된 좌표 역게시 금지, CPU 경합 |
| temporal_state | 작업자/누적 상태 수명·버퍼 최적화 | 이동/광학/정렬 초기화, 실제 누적 시간, centroid 편향, 수치 출력 비교 |
| validation | 선택한 개선 조합을 긴 구간에서 재검증 | p50/p95/최대 무갱신 시간, 정확도, 설정 변화·종료·재시작 |

모든 단계의 공통 통과 조건:

- 같은 RAW에서 기준이 정확하게 승인한 해를 이유 없이 잃지 않을 것.
- 매칭·잔차·연속성 기준을 느슨하게 해서 얻은 속도 향상을 개선으로 세지 않을 것.
- 기준 천체/독립 해로 검증한 RA_target/Dec_target 오차와 p95 산포가 악화되지 않을 것.
  기준 자체를 진실값으로 간주하지 않는다. 시간 추세 제거는 산포용이며 지연 편향을
  숨길 수 있으므로 원시 좌표와 시간 지연도 함께 평가한다.
- 프레임 오결합, 과거 좌표 역게시, NaN, 새로운 오솔브가 없을 것. 관측 표본에서 0회는
  모든 환경에서의 오솔브 확률 0을 증명하지 않는다.
- 실제 승인 좌표의 갱신 간격과 입력 확보→게시 지연이 개선될 것. 성공 프레임의
  평균 T_solve만 짧아진 결과는 불충분하다.
- 저장 누락이 많은 구간, 두 프로세스의 세션이 다른 구간, 불완전 원본은 성능 승인
  자료에서 분리한다. 변경 효과가 측정 흔들림보다 작으면 보류하고 반복한다.

## 7. 실내 재생 자료와 남은 작업

기존 자료: `PiFinder_data/captures/mf_replay/20260903_light_pollution_ab`,
`20260903_cloud_coordinate_jitter`, `20260904_exposure_saturation_sweep` 및
8월의 달/건물광·구름 자료. RAW의 표시 회전을 되돌리는 규칙은 각 README를 따른다.

`python/scripts/replay_star_preprocess_ab.py`는 과거 비교 도구다. 현재 운영 코드와
노출·게인 fingerprint, 병렬 작업 수, 초기 RAW 경로, 타임아웃, 실제 시각과
적응형 스케줄러 재현이 다르므로 **그대로 실행한 수치를 현행 성능으로 채택하지 않는다.**

이번 NPZ/JSONL 수집과 무결성·지연 보고 기능은 구현했다. 현행 운영 경로를 그대로
구동하는 NPZ 재생 엔진, 실제 도착 시각에 따른 가상 스케줄러, 단계별 최적화,
독립 기준 좌표를 이용한 정확도 자동 판정은 후속 작업이다. 수집 자료는 그 작업에
필요한 원본/입력/메타데이터/설정/결과를 보존한다. 성공한 프레임만 추려 저장하지 않는다.

## 8. 구현 검증 기록

임시 디렉터리와 합성 배열로 OFF 무동작, 요청 검증, RAW 무손실·프레임 대응,
실패 기록, 구간 고정, 저장 샘플링, 시간/건수/용량 제한, 큐 누락, 디스크 오류,
두 프로세스 결과 연결, 원본·소스 체크섬 훼손, 웹 제어·인증, 기존 탐색 순서 보존을 검사했다.

2026-09-07 검증 결과:

- 기존 작업 변경을 제외한 HEAD 기반 검증 사본에 이번 코드만 적용:
  `pytest -m 'unit or smoke' -q` **1607 passed**, 857 deselected.
- 실제 작업 디렉터리의 수집·솔버·좌표 반영·API 관련 검사: **115 passed**.
- 검증 사본 전체 Ruff 검사 및 342개 Python 파일 포맷 검사 통과.
- 변경한 4개 모듈 mypy 검사 통과. 프로젝트 설정상 untyped 함수 본문 검사는 제한적이다.
- 기존 Tetra3의 `np.math` 사용 중단 예정 경고 8건. 테스트 실패는 없다.

브라우저 화면 조작, 실제 카메라 수집·마운트 이동·자동 노출 변경은 실행하지 않았다.
웹 경로·제어·인증은 Flask 테스트 클라이언트로 확인했다.
현장에서 첫 수집 후 프로세스별 complete와 무결성 보고를 반드시 확인한다.

## 9. 현장 시험 결과 작성 양식

각 단계마다 아래 표를 복사해 채운다. 자동 보고에 없는 항목은 별도 분석으로
계산하고, 측정하지 않은 정확도는 통과로 표시하지 않는다.

| 항목 | 기준 구간 | 변경 구간 |
| --- | --- | --- |
| session_id / 단계 / scene | | |
| 소스 버전·변경 내용 | | |
| 기준 천체·시각·고도·주변 광원·구름 | | |
| 고정/이동 및 구간 메모 | | |
| RAW/telemetry, 저장·누락 건수, 무결성 | | |
| 시도 수 / 승인 수 / 실제 게시 짝 수 | | |
| 입력 확보→게시 p50 / p95 | | |
| 승인 좌표 갱신 간격 p50 / p95 / 최대 공백 | | |
| 독립 기준 대비 지향 오차 / 정지 산포 | | |
| 모드 전환 전후 좌표 차이 / 첫 승인까지 시간 | | |
| 오솔브·오결합·역게시 및 특이사항 | | |

판정: 통과 / 보류 / 실패. 근거, 반복 횟수, 다음 시험 조건을 남긴다.
