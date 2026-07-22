# MF_PiFinder 개발 문서 인덱스

포크(`MF_PiFinder`)가 개발하며 추가한 `docs/mf_*` 문서의 진입점이다. 작업 전
관련 문서를 먼저 읽는다(코드만 보고 의도를 역추론하지 않는다). 상위 구조
레퍼런스(용어집·아키텍처·결정 기록)는 [CONTEXT-MAP.md](../CONTEXT-MAP.md),
`docs/ax/*`, `docs/adr/*`를 참조.

상태 라벨: **living** = 소스에 맞춰 계속 갱신 / **install** = 설치·운영 가이드 /
**plan** = 구현 전/부분 구현 계획 / **1회성** = 완료된 분석·검증 기록(유지 대상 아님).

최종 갱신: 2026-07-23.

## INDI 마운트 — 좌표·포인팅 (핵심)

| 문서 | ko/en | 상태 | 요약 |
|---|---|---|---|
| [coordinate_helper_plan](mf_coordinate_helper_plan_ko.md) | 둘 다 | living ★ | `PointingCoordinateService` 권위 스펙 — 좌표 후보(solved/IMU/mount), 선택 우선순위, mount+IMU 델타 융합(속도 게이트·회전 tracker·추적 따라잡기 예산). **좌표 선택/telemetry 게이트 의미의 정규 소유자.** |
| [goto_mount_source_structure](mf_goto_mount_source_structure_ko.md) | 둘 다 | living ★ | SkySafari→마운트 전체 소스맵(프로세스/큐, `pos_server` LX200 처리, push/forwarding/multi-align 라우팅). **SkySafari `:Sr/:Sd/:MS/:CM` + GoTo/Sync forwarding의 정규 소유자.** |
| [mountcontrol_indi_flow](mf_mountcontrol_indi_flow_ko.md) | 둘 다 | living | `mountcontrol_indi.py` 흐름도(메인 루프, 상태 파일 스키마, 연결 순서, 명령 분배). **`mountcontrol_queue` 명령 분배 표의 정규 소유자.** |
| [indi_goto_guide_plan](mf_indi_goto_guide_plan_ko.md) | 둘 다 | living | `indi_goto_guide_service` GoTo/Guide 상태머신, 추적 가이드 외란 복구, 트래킹 주파수 정책. |
| [multipoint_align_flow](mf_multipoint_align_flow_ko.md) | 둘 다 | living | Multi-Point Align 상세 흐름(**정규 소유자**; 타 문서는 요약+참조). |
| [backlash_measurement_flow](mf_backlash_measurement_flow_ko.md) | 둘 다 | living | 자동 백래시 측정 `compass_goto_loop`(**정규 소유자**; 타 문서는 요약+참조). |
| [mount_mode_compatibility](mf_mount_mode_compatibility_ko.md) | 둘 다 | plan(대부분 구현) | Alt/Az vs EQ SkySafari 호환성 감사·체크리스트. |
| [indi_mount_install](mf_indi_mount_install_ko.md) | 둘 다 | install | INDI 마운트 설치·사용 가이드. |

## 카탈로그 · 웹 UI

| 문서 | ko/en | 상태 | 요약 |
|---|---|---|---|
| [web_catalogs_dev](mf_web_catalogs_dev_ko.md) | ko | living | 기기 웹 카탈로그 페이지(라우트·필터·push·통합검색 지정번호 정렬). |
| [large_catalog_lazy_load](mf_large_catalog_lazy_load_ko.md) | ko | living | 대형 카탈로그(WDS) lazy load. |
| [location_catalog](mf_location_catalog_ko.md) | 둘 다 | living | GeoNames 기반 오프라인 위치 카탈로그. |
| [raw_live_stack_plan](mf_raw_live_stack_plan_ko.md) | 둘 다 | living | LiveCam RAW 프리뷰/롤링 라이브 스택. |

## 설치 · 플랫폼 · 시스템

| 문서 | ko/en | 상태 | 요약 |
|---|---|---|---|
| [bookworm_install](mf_bookworm_install_ko.md) | 둘 다 | install | Bookworm 설치/경로 기반. |
| [pifinder_new_device_tasks](mf_pifinder_new_device_tasks_ko.md) | 둘 다 | install | 신규 기기 셋업 작업 목록. |
| [pifinder_rpi4_pi5_compatibility](mf_pifinder_rpi4_pi5_compatibility_ko.md) | 둘 다 | living | Pi4/5/CM5 보드·GPS/UART 호환성. |
| [wifi_apsta](mf_wifi_apsta_ko.md) | 둘 다 | living | AP+STA 동시 Wi-Fi 모드. |
| [time_sync](mf_time_sync_ko.md) | 둘 다 | living | GPS/NTP/RTC/PPS 통합 시간 동기화(시스템 클럭). |
| [i2c_clock_stretching_fix](mf_i2c_clock_stretching_fix_ko.md) | 둘 다 | living | I2C 클럭 스트레칭 수정. |

## 입력 · UI · 센서

| 문서 | ko/en | 상태 | 요약 |
|---|---|---|---|
| [input_controls](mf_input_controls_ko.md) | 둘 다 | living | 입력 컨트롤 전반. |
| [input_keymap](mf_input_keymap_ko.md) | 둘 다 | living | 키맵. |
| [keyboard_mapping](mf_keyboard_mapping_ko.md) | 둘 다 | living | BT/USB HID 키보드 매핑. |
| [imu_compass_calibration](mf_imu_compass_calibration_ko.md) | 둘 다 | living | 선택형 BNO055 NDOF 지자계 보정. |

## 분석 · 검토 · 계획 (비-living, 이력/백로그)

| 문서 | ko/en | 상태 | 요약 |
|---|---|---|---|
| [goto_tracking_recovery_analysis](mf_goto_tracking_recovery_analysis_ko.md) | ko | **1회성**(2026-07-18) | OnStepX GoTo 무동작(wedge) 장애 분석·복구 검증. |
| [indi_onstep_driver_test_checklist](mf_indi_onstep_driver_test_checklist_ko.md) | ko | **1회성**(2026-07-01) | INDI 드라이버 vs 직접 LX200 사전 검증 기록. |
| [solve_motion_gate_review](mf_solve_motion_gate_review_ko.md) | 둘 다 | **plan(미구현)** | 노출 중 이동 프레임 솔브 게이트 미배선 검토(협의 대기). |
| [gps_aiding_plan](mf_gps_aiding_plan_ko.md) | 둘 다 | **plan(구현 전)** | u-blox GPS aiding(MGA-INI/DBD) 설계 초안. |

## 메타 · 이력 · 프로세스

| 문서 | ko/en | 상태 | 요약 |
|---|---|---|---|
| [change_history](mf_change_history_ko.md) | 둘 다 | living | 전체 소스 수정 이력(기능·파일별). PR 상태 표는 2026-06-27 스냅숏(현재 직접 main 푸시). |
| [upstream_patch_reference](mf_upstream_patch_reference_ko.md) | 둘 다 | living | `brickbots/PiFinder` 리베이스/머지 레퍼런스. |
| [feature_review_checklist](mf_feature_review_checklist_ko.md) | 둘 다 | living | 기능 리뷰 체크리스트. |
| [ko_translation_review](mf_ko_translation_review.md) | 단일 | living | 한국어 UI 번역 리뷰. |

## 중복 주제의 정규 소유자

여러 문서가 같은 메커니즘을 다룰 때, 권위 있는 서술은 아래 한 곳에 두고 나머지는
요약+상호참조만 유지한다(수정 시 소유자를 먼저 갱신).

| 주제 | 정규 소유자 | 요약만 두는 문서 |
|---|---|---|
| SkySafari `:Sr/:Sd/:MS/:CM` + GoTo/Sync forwarding | goto_mount_source_structure | indi_goto_guide_plan, mount_mode_compatibility, coordinate_helper_plan, upstream_patch_reference |
| 좌표 선택 우선순위 · mount telemetry 게이트 의미 | coordinate_helper_plan | mountcontrol_indi_flow, goto_mount_source_structure |
| `mountcontrol_queue` 명령 분배 표 | mountcontrol_indi_flow | goto_mount_source_structure |
| Multi-Point Align 상세 | multipoint_align_flow | mountcontrol_indi_flow, indi_mount_install |
| Backlash 측정 상세 | backlash_measurement_flow | mountcontrol_indi_flow, indi_mount_install |
| Location/Time sync 규약(`:SG` 부호, PyIndi full-vector) | goto_mount_source_structure | indi_mount_install, mountcontrol_indi_flow, coordinate_helper_plan(자동 재sync만 고유) |
