# MF PiFinder Time Sync

This document describes PiFinder's integrated time-sync feature. `chronyd` is the preferred default owner of the Linux system clock. PiFinder reads `chronyc tracking` for UI status, observes GPS and PiFinder SNTP candidates as auxiliary sources, and can optionally manage RTC sync requests and software PPS ticks.

The whole feature is `Off` by default. When `Time Sync` is turned `On`, the default source mode is `Chrony`, and chronyd continues to discipline the system clock. PiFinder's built-in SNTP check is `Off` by default to avoid duplicating chronyd; enable it only as a fallback/check source.

## UI Settings

Settings path:

```text
Settings > Advanced > Time Sync
```

Status path:

```text
Tools > Place & Time > Time Sync
```

Main UI items:

| UI item | Config key | Default | Meaning |
| --- | --- | --- | --- |
| `Time Sync` | `time_sync_enabled` | `Off` | Master switch for integrated time sync |
| `Source Mode` | `time_sync_source_mode` | `Chrony` | Select `Chrony`, `Best`, `GPS`, or `NTP` |
| `Clock Manager` | `time_sync_clock_manager` | `Chrony` | Select `Chrony`, `PiFinder`, or `Off` for Linux system clock ownership |
| `Chrony Source` | `chrony_time_sync` | `On` | Observe chronyd as a PiFinder time source |
| `GPS Source` | `gps_time_sync` | `On` | Observe GPS time candidates |
| `PiFinder NTP` | `ntp_time_sync` | `Off` | Observe PiFinder's built-in SNTP candidates |
| `PiFinder NTP Server` | `ntp_server`, `ntp_server_custom` | `pool.ntp.org` | Select a PiFinder SNTP server or enter a custom server |
| `RTC Sync` | `rtc_sync` | `Off` | Request RTC sync from the selected time |
| `Software PPS` | `software_pps` | `Off` | Emit software periodic ticks |

Default NTP server list:

```text
pool.ntp.org
time.google.com
time.cloudflare.com
time.nist.gov
Custom
```

When `Custom` is selected, PiFinder opens the server entry screen immediately. After saving, `PiFinder NTP Server` is automatically set to `Custom`, and the entered address is stored in `ntp_server_custom`. This setting is only for PiFinder's built-in SNTP check; it does not rewrite chronyd's server list.

## Default Config

Important defaults in `default_config.json`:

```json
"time_sync_enabled": false,
"time_sync_source_mode": "chrony",
"time_sync_clock_manager": "chrony",
"chrony_time_sync": true,
"chrony_poll_interval_seconds": 30,
"chrony_timeout_seconds": 1.0,
"chrony_stale_seconds": 120,
"gps_time_sync": true,
"ntp_time_sync": false,
"ntp_server": "pool.ntp.org",
"ntp_server_custom": "",
"ntp_poll_interval_seconds": 300,
"ntp_timeout_seconds": 1.0,
"ntp_max_delay_ms": 1500,
"ntp_stale_seconds": 900,
"rtc_sync": false,
"software_pps": false
```

## Source Selection

In `Chrony` mode, PiFinder selects the system clock state managed by chronyd. PiFinder records `chronyc tracking` details such as stratum, reference, leap status, and offsets in the status file; the UI shows the chrony state, reference, and offset.

In `Best` mode, PiFinder compares Chrony, stable GPS candidates, and valid PiFinder SNTP candidates. GPS is judged by `valid`, `tAcc`, recent sample jitter, and stale age. PiFinder SNTP is judged by response validity, stratum, round-trip delay, root dispersion, and stale age.

When multiple candidates are usable, PiFinder selects the source with the smaller estimated quality value. If PiFinder SNTP delay is above `ntp_max_delay_ms`, that source is marked `low_quality` and is not selected.

## System Clock and RTC

The recommended model is for `chronyd` to manage the Linux system clock while PiFinder provides status, source observation, RTC sync assistance, and software PPS ticks.

Install or check chronyd:

```bash
cd ~/PiFinder
./scripts/install_chrony_time_sync.sh install
./scripts/install_chrony_time_sync.sh status
```

The main PiFinder service keeps normal user permissions. Actual RTC writes, or the explicit `Clock Manager = PiFinder` legacy system-clock mode, require the separate root helper service.

Before final outdoor testing, start with dry-run mode:

```bash
cd ~/PiFinder
./scripts/install_gps_time_sync_helper.sh enable-dry-run
```

Switch to real RTC write mode only when dry-run results are correct:

```bash
cd ~/PiFinder
./scripts/install_gps_time_sync_helper.sh enable
```

The helper validates each request before running `/usr/sbin/hwclock`. In the explicit `Clock Manager = PiFinder` legacy/fallback mode, it can also run `/usr/bin/date` for system-clock writes. It checks that the request belongs to the current boot, is fresh, has a valid selected time source, and comes from a `stable` monitor state.

## Status Files

The status path keeps the existing filename for compatibility:

```text
~/PiFinder_data/gps_time_status.json
```

Important fields:

| Field | Meaning |
| --- | --- |
| `state` | Integrated time-sync state |
| `clock_manager` | System clock ownership mode |
| `selected` | Currently selected time source and time |
| `chrony` | chronyd tracking state |
| `latest` | Last GPS time sample |
| `ntp` | Last PiFinder SNTP query result |
| `sources.chrony` | chronyd source state and candidate |
| `sources.gps` | GPS source state and candidate |
| `sources.ntp` | PiFinder SNTP source state and candidate |
| `system_clock_sync` | PiFinder helper system-clock request state |
| `rtc_sync` | RTC sync request state |
| `software_pps` | Software PPS tick state |
| `helper` | Last root-helper result |

Helper request file:

```text
~/PiFinder_data/gps_time_sync_request.json
```

Helper status file:

```text
~/PiFinder_data/gps_time_sync_helper_status.json
```

The filenames are retained for compatibility with existing installs and the helper service.

## Test

Run unit tests:

```bash
cd ~/PiFinder/python
pytest tests/test_gps_time_sync.py tests/test_gps_time_sync_helper.py tests/test_gps_time_sync_status_ui.py -q
```

Watch hardware status:

```bash
chronyc tracking
chronyc sources -v
watch -n 1 cat ~/PiFinder_data/gps_time_status.json
```
