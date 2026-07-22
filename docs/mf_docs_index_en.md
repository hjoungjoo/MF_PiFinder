# MF_PiFinder Development Docs Index

Entry point for the `docs/mf_*` docs the `MF_PiFinder` fork added during
development. Read the relevant doc before working in an area (don't reverse-infer
intent from code alone). For the higher-level structural reference (glossary,
architecture, decision records) see [CONTEXT-MAP.md](../CONTEXT-MAP.md),
`docs/ax/*`, `docs/adr/*`.

Status labels: **living** = kept in sync with source / **install** = install &
ops guide / **plan** = pre- or partial implementation / **one-time** = completed
analysis/verification record (not maintained).

Last updated: 2026-07-23. The KO version (`mf_docs_index_ko.md`) is authoritative.

## INDI mount — coordinates & pointing (core)

| Doc | ko/en | Status | Summary |
|---|---|---|---|
| [coordinate_helper_plan](mf_coordinate_helper_plan_en.md) | both | living ★ | Authoritative `PointingCoordinateService` spec — coordinate candidates (solved/IMU/mount), selection priority, mount+IMU delta fusion (velocity gate, rotation tracker, tracking catch-up budget). **Canonical owner of coordinate selection / telemetry-gate semantics.** |
| [goto_mount_source_structure](mf_goto_mount_source_structure_en.md) | both | living ★ | Full SkySafari→mount source map (processes/queues, `pos_server` LX200 handling, push/forwarding/multi-align routing). **Canonical owner of SkySafari `:Sr/:Sd/:MS/:CM` + GoTo/Sync forwarding.** |
| [mountcontrol_indi_flow](mf_mountcontrol_indi_flow_en.md) | both | living | `mountcontrol_indi.py` flow (main loop, status-file schema, connect sequence, command dispatch). **Canonical owner of the `mountcontrol_queue` dispatch table.** |
| [indi_goto_guide_plan](mf_indi_goto_guide_plan_en.md) | both | living | `indi_goto_guide_service` GoTo/Guide state machine, tracking-guide disturbance recovery, tracking-frequency policy. |
| [multipoint_align_flow](mf_multipoint_align_flow_en.md) | both | living | Multi-Point Align detailed flow (**canonical owner**; others summarize + cite). |
| [backlash_measurement_flow](mf_backlash_measurement_flow_en.md) | both | living | Auto-backlash `compass_goto_loop` (**canonical owner**; others summarize + cite). |
| [mount_mode_compatibility](mf_mount_mode_compatibility_en.md) | both | plan (mostly built) | Alt/Az vs EQ SkySafari compatibility audit/checklist. |
| [indi_mount_install](mf_indi_mount_install_en.md) | both | install | INDI mount install/usage guide. |

## Catalog · web UI

| Doc | ko/en | Status | Summary |
|---|---|---|---|
| [web_catalogs_dev](mf_web_catalogs_dev_ko.md) | ko | living | On-device web catalog pages (routes, filters, push, unified search with designation-prefix ranking). |
| [large_catalog_lazy_load](mf_large_catalog_lazy_load_ko.md) | ko | living | Large catalog (WDS) lazy load. |
| [location_catalog](mf_location_catalog_en.md) | both | living | GeoNames offline location catalog. |
| [raw_live_stack_plan](mf_raw_live_stack_plan_en.md) | both | living | LiveCam RAW preview / rolling live stack. |

## Install · platform · system

| Doc | ko/en | Status | Summary |
|---|---|---|---|
| [bookworm_install](mf_bookworm_install_en.md) | both | install | Bookworm install / path foundation. |
| [pifinder_new_device_tasks](mf_pifinder_new_device_tasks_en.md) | both | install | New-device setup task list. |
| [pifinder_rpi4_pi5_compatibility](mf_pifinder_rpi4_pi5_compatibility_en.md) | both | living | Pi4/5/CM5 board + GPS/UART compatibility. |
| [wifi_apsta](mf_wifi_apsta_en.md) | both | living | Simultaneous AP+STA Wi-Fi mode. |
| [time_sync](mf_time_sync_en.md) | both | living | GPS/NTP/RTC/PPS integrated time sync (system clock). |
| [i2c_clock_stretching_fix](mf_i2c_clock_stretching_fix_en.md) | both | living | I2C clock-stretching fix. |

## Input · UI · sensors

| Doc | ko/en | Status | Summary |
|---|---|---|---|
| [input_controls](mf_input_controls_en.md) | both | living | Input controls overview. |
| [input_keymap](mf_input_keymap_en.md) | both | living | Key map. |
| [keyboard_mapping](mf_keyboard_mapping_en.md) | both | living | BT/USB HID keyboard mapping. |
| [imu_compass_calibration](mf_imu_compass_calibration_en.md) | both | living | Optional BNO055 NDOF magnetometer calibration. |

## Analysis · review · plans (non-living, history/backlog)

| Doc | ko/en | Status | Summary |
|---|---|---|---|
| [goto_tracking_recovery_analysis](mf_goto_tracking_recovery_analysis_ko.md) | ko | **one-time** (2026-07-18) | OnStepX GoTo no-op (wedge) failure analysis + recovery verification. |
| [indi_onstep_driver_test_checklist](mf_indi_onstep_driver_test_checklist_ko.md) | ko | **one-time** (2026-07-01) | INDI driver vs raw LX200 pre-integration verification record. |
| [solve_motion_gate_review](mf_solve_motion_gate_review_en.md) | both | **plan (unbuilt)** | Unwired solve gate for frames exposed during motion (pending decision). |
| [gps_aiding_plan](mf_gps_aiding_plan_en.md) | both | **plan (pre-impl)** | u-blox GPS aiding (MGA-INI/DBD) design draft. |

## Meta · history · process

| Doc | ko/en | Status | Summary |
|---|---|---|---|
| [change_history](mf_change_history_en.md) | both | living | Full source-change history (by feature/file). The PR-status table is a 2026-06-27 snapshot (now: direct-to-main). |
| [upstream_patch_reference](mf_upstream_patch_reference_en.md) | both | living | `brickbots/PiFinder` rebase/merge reference. |
| [feature_review_checklist](mf_feature_review_checklist_en.md) | both | living | Feature review checklist. |
| [ko_translation_review](mf_ko_translation_review.md) | single | living | Korean UI translation review. |

## Canonical owners for overlapping topics

When several docs cover the same mechanism, the authoritative description lives in
one place and the rest keep only a summary + cross-reference (update the owner
first when editing).

| Topic | Canonical owner | Docs that only summarize |
|---|---|---|
| SkySafari `:Sr/:Sd/:MS/:CM` + GoTo/Sync forwarding | goto_mount_source_structure | indi_goto_guide_plan, mount_mode_compatibility, coordinate_helper_plan, upstream_patch_reference |
| Coordinate selection priority · mount telemetry-gate semantics | coordinate_helper_plan | mountcontrol_indi_flow, goto_mount_source_structure |
| `mountcontrol_queue` command dispatch table | mountcontrol_indi_flow | goto_mount_source_structure |
| Multi-Point Align detail | multipoint_align_flow | mountcontrol_indi_flow, indi_mount_install |
| Backlash measurement detail | backlash_measurement_flow | mountcontrol_indi_flow, indi_mount_install |
| Location/Time sync convention (`:SG` sign, PyIndi full-vector) | goto_mount_source_structure | indi_mount_install, mountcontrol_indi_flow, coordinate_helper_plan (auto-resync only is unique) |
