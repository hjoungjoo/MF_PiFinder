# MF PiFinder Time Sync

This document describes PiFinder's time synchronization architecture. The only
manager of the Linux system clock is `chronyd`. chronyd automatically selects
the more accurate of the NTP pool (when a network is available) and the GPS SHM
refclock fed by gpsd (in the field). PiFinder never writes the clock itself: it
observes `chronyc tracking` state and GPS time candidates for the UI, and only
forwards RTC sync requests to the root helper when enabled.

Reduced on 2026-07-25
([mf_field_test_20260724_analysis_ko.md](mf_field_test_20260724_analysis_ko.md)
item A3): the PiFinder-side SNTP client, software PPS, direct system-clock
writes (`Clock Manager = PiFinder`) and the `Best/GPS/NTP` source modes were
removed. Source selection is chronyd's job; PiFinder is an observer.
`Time Sync` now defaults to `On` (observation only, so it is safe).

## Time supply chain (field)

```text
u-blox GPS ── /dev/ttyAMA3 ──> gpsd (-n required) ──┬─> NTP SHM(0) ──> chronyd
                                                    └─> TCP 2947 ──> PiFinder gps_ubx
Internet (when available) ── NTP pool ──────────────────────────────> chronyd
chronyd ──> system clock (sole writer)
```

Key system configuration (managed idempotently by
`scripts/install_chrony_time_sync.sh install|configure`):

- `/etc/default/gpsd`: `GPSD_OPTIONS="-n -s <baud>"` — without `-n`, gpsd does
  not feed the NTP SHM segments when only watch clients are attached
  (measured on 2026-07-25).
- `/etc/chrony/chrony.conf`: `refclock SHM 0 poll 3 refid gps1` and
  `makestep 1 -1` — on an RTC-less board a large offset is stepped even when
  the GPS fix arrives long after boot.

## UI Settings

Settings path:

```text
Settings > Advanced > Time Sync
```

Status path:

```text
Tools > Place & Time > Time Sync
```

UI items:

| UI item | Config key | Default | Meaning |
| --- | --- | --- | --- |
| `Time Sync` | `time_sync_enabled` | `On` | Master switch for observation/status |
| `Chrony Source` | `chrony_time_sync` | `On` | Observe chronyd state |
| `GPS Source` | `gps_time_sync` | `On` | Observe GPS time candidates (diagnostics) |
| `RTC Sync` | `rtc_sync` | `Off` | Request RTC updates (for future RTC hardware) |

## Default configuration

Key defaults in `default_config.json`:

```json
"time_sync_enabled": true,
"chrony_time_sync": true,
"chrony_poll_interval_seconds": 30,
"chrony_timeout_seconds": 1.0,
"chrony_stale_seconds": 120,
"gps_time_sync": true,
"gps_time_sync_min_samples": 5,
"gps_time_sync_window_seconds": 120,
"gps_time_sync_stale_seconds": 30,
"gps_time_sync_max_tacc_ns": 1000000000,
"gps_time_sync_stable_jitter_ms": 250,
"gps_time_sync_stable_offset_ms": 1000,
"rtc_sync": false,
"rtc_sync_min_interval_seconds": 3600
```

Legacy keys still present in older configs (`ntp_*`, `software_pps*`,
`time_sync_source_mode`, `time_sync_clock_manager`,
`time_sync_system_clock*`) are ignored.

## Selection / state model

- `selected` is only ever the Chrony candidate, present while chronyd reports
  a synchronized (`stable`) tracking state. When chronyd is `unsynced`,
  `selected` is null and the state mirrors the chrony/GPS observation states.
- GPS candidates are observed for diagnostics only: `tAcc`, sample jitter and
  staleness drive `stable/collecting/low_quality/unstable/stale`.
- Detecting a never-synchronized clock after boot (stale fake-hwclock time)
  and holding off mount time sync is planned as item A4 of the analysis doc.

## System clock and RTC

chronyd manages the system clock; PiFinder does not write it.

Install/configure chronyd:

```bash
cd ~/PiFinder
./scripts/install_chrony_time_sync.sh install    # install + time chain config
./scripts/install_chrony_time_sync.sh configure  # time chain config only (idempotent)
./scripts/install_chrony_time_sync.sh status
```

To adopt an RTC and allow actual writes, the root helper is required:

```bash
cd ~/PiFinder
./scripts/install_gps_time_sync_helper.sh enable-dry-run   # verify first
./scripts/install_gps_time_sync_helper.sh enable           # real writes
```

The helper validates the request file before running `/usr/sbin/hwclock`:
the request must belong to the current boot session and reference a valid
selected time source. (The helper's system-clock write path is no longer
requested by the monitor; it will be cleaned up together with the RTC
decision, analysis item A6.)

## Status files

```text
/dev/shm/pifinder/gps_time_status.json      # status (tmpfs, lost on reboot)
~/PiFinder_data/gps_time_sync_request.json  # helper request
~/PiFinder_data/gps_time_sync_helper_status.json
```

Key fields:

| Field | Meaning |
| --- | --- |
| `state` / `message` | Combined state (reflects chrony selection) |
| `clock_manager` | Always `chrony` |
| `selected` | Currently selected time source (Chrony) or null |
| `chrony` | chronyd tracking state |
| `latest` / `offset` / `samples` | Latest GPS time sample and statistics |
| `sources.chrony` / `sources.gps` | Per-source state and candidate |
| `rtc_sync` | RTC sync request state |
| `helper` | Last result from the root helper |

## Tests

Unit tests:

```bash
cd ~/PiFinder/python
pytest tests/test_gps_time_sync.py tests/test_gps_time_sync_helper.py \
  tests/test_gps_time_sync_status_ui.py tests/test_gps_time_sources.py -q
```

On-device checks:

```bash
chronyc tracking
chronyc sources -v      # gps1 Reach must be non-zero for GPS supply
watch -n 1 cat /dev/shm/pifinder/gps_time_status.json
```
