# Intermittent u-blox GPS Identification Failure — Incident Analysis and Automatic Recovery Report

[한국어 보고서](mf_gps_ubx_recovery_20260909_ko.md)

| Item | Details |
|---|---|
| Report ID | MF-GPS-20260909-01 |
| Date / time zone | 2026-09-09 / KST (UTC+09:00) |
| System | `mfpi5`, Raspberry Pi 5, PiFinder UBlox backend |
| Baseline commit | `bad7cb30fe31ebd1ebd74e1f0d936f7edf4e1a65` |
| Request | Investigate intermittent GPS identification failures, implement appropriate recovery, and provide a formal report |
| Status | Implementation, 149 regression tests, TCP reproduction, and device deployment complete; automatic recovery success during natural recurrences remains to be observed |

## 1. Findings

During this incident, the GPS module continued transmitting valid NMEA data,
but gpsd remained in the `NMEA0183` identification state. PiFinder's
`gps_type=ublox` backend decodes UBX frames only, so incoming data was not
reflected in its GPS communication and satellite status.

Sending **one UBX-MON-VER version poll**, which does not itself change receiver
configuration, produced a `SPG 5.10 / PROTVER 34.10` response. gpsd then identified
the receiver as `u-blox` and enabled normal UBX output. PiFinder resumed receiving
data, and a 2D position fix using three satellites was subsequently observed.
Changing the GPS type to `GPSD (generic)` was unnecessary.

The confirmed immediate cause was **a persistent state in which u-blox
identification and UBX output initialization had not completed**. The trigger
for the initial identification failure—such as startup timing, delayed receiver
readiness, or a temporarily lost response—could not be established because
detailed logs from that point were unavailable. The improvement retries the
poll that was demonstrated to recover this observed state, without assuming
an unverified underlying cause.

## 2. Environment and Measured Evidence

| Item | Observation |
|---|---|
| OS | Linux `6.12.93+rpt-rpi-2712`, aarch64 |
| gpsd | `3.22`, PID `1273`, `/usr/sbin/gpsd -s 115200 /dev/ttyAMA2` |
| UART | `uart2-pi5`, GPIO4 TXD2 / GPIO5 RXD2, 115200 bps |
| PiFinder | `gps_type=ublox`, `gps_port=auto`, `gps_baud_rate=115200` |
| Port ownership | Only gpsd had `/dev/ttyAMA2` open |
| Services | gpsd and PiFinder were active; the local TCP 2947 connection remained established |
| Voltage status | `vcgencmd get_throttled`: `0x0` |
| Receiver version | `ROM SPG 5.10 (7b202e)`, HW `000A0000`, `PROTVER=34.10` |

Diagnostic timeline, in KST:

1. **Around 03:29:** Checked gpsd/PiFinder services, UART configuration and
   ownership, and kernel logs. No service outage, competing serial-port owner,
   or indication of undervoltage was observed.
2. **Around 03:30, 18-second JSON subscription:** Received 541 `SKY` and 180
   `TPV` reports. The receiver reported `mode=1` and `uSat=0`. Data continued
   arriving despite the absence of a position fix.
3. **Around 03:30, 7-second raw subscription:** Received 48,447 bytes with
   zero UBX sync sequences. Only NMEA sentences, including `$GNRMC`, `$GNGGA`,
   `$GNGSA`, and `$GPGSV`, were observed. GGA fix quality was `0`, with a
   satellite count of `00`.
4. **Around 03:33:21:** Sent one MON-VER poll through gpsd. No receiver reset,
   baud-rate change, persistent configuration save, or service restart was
   performed.
5. **During the next 8 seconds:** Received four MON-VER responses, UBX
   configuration responses, and NAV messages: 79 each of NAV-PVT,
   NAV-POSECEF, NAV-VELECEF, and NAV-SAT; 78 each of NAV-DOP, NAV-TIMEGPS,
   and NAV-EOE; zero checksum errors. The additional responses and
   configuration commands occurred during gpsd initialization after
   identification. `DEVICES` changed to `driver=u-blox`, `native=1`.
6. **03:33:22:** PiFinder's main log recorded the first `GPS Time` entry for
   that process run.
7. **Around 03:33:58–03:34:09, 10-second follow-up:** All 100 TPV reports
   indicated `mode=2`, with three satellites used. The highest C/N0 in the
   interval was 29. Fresh `NAV-PVT` reception was also confirmed in
   PiFinder's status file.

The initial diagnostic figures were transcribed from command output during
the investigation; the complete raw stream was not saved to a file at that
time. The position fix was a separate observation after communication
recovered. These results do not establish that the version poll itself
improved satellite signal quality.

## 3. Cause Analysis and Change Scope

### 3.1 Confirmed sequence

```text
u-blox transmits NMEA
  → gpsd remains in the NMEA0183 state
  → PiFinder's UBX parser has no UBX frames to decode
  → GPS appears inactive, with no automatic identification retry

MON-VER poll
  → valid UBX version response
  → gpsd identifies u-blox and initializes normal output
  → PiFinder resumes NAV message processing
```

gpsd 3.22 configures the output mode on the u-blox identification event and
uses MON-VER for its initial version query. This is consistent with the
observed recovery sequence. [gpsd 3.22 u-blox driver](https://raw.githubusercontent.com/ntpsec/gpsd/release-3.22/drivers/driver_ubx.c)

### 3.2 Additional stream-handling defects

- Reading and discarding the initial WATCH response separately could also
  discard the first UBX frames and device report if they arrived in the same
  TCP read. The regular parser now consumes the entire response.
- If `0xB5` and `0x62` arrived on opposite sides of a TCP read boundary, the
  previous search path could discard `0xB5`. A trailing `0xB5` is now retained
  for the next read.

These robustness improvements were supported by code review and regression
tests. Neither was established as the original trigger of this device incident.

## 4. Implementation Specification

| Condition or limit | Implementation |
|---|---|
| Applicable path | UBX backend connected to a live gpsd stream only |
| Target selection | gpsd `DEVICES` reports exactly one local `/dev/` device whose driver is `NMEA0183` or `u-blox` |
| NMEA recognition | Complete RMC/GGA/GSA/GSV/GLL/VTG/ZDA sentences with a valid XOR checksum |
| First poll | At least three valid NMEA sentences spanning at least 10 seconds, without valid UBX |
| Reception gaps | A gap exceeding 5 seconds between NMEA sentences resets the observation window |
| Retry interval | At least 30 seconds, measured with a monotonic clock |
| Attempt limit | Three per TCP connection, including failed writes |
| On success | Valid UBX resets the observation window but preserves the accumulated attempt count |
| After the limit | Log one warning and stop further polls; continue observing NMEA |
| Transport | gpsd `?DEVICE` with `hexdata` and an explicit device path |
| Command bytes | `b5 62 0a 04 00 00 0e 34` — UBX-MON-VER poll |
| Drain timeout | At most 2 seconds; connection failures use the existing close/reconnect path |
| Diagnostic display | Emit a `?NMEA` communication event for a read batch containing NMEA without valid UBX |
| Logging | `GPS.parser.recovery`, at WARNING level, visible with the default configuration |

The command format matches the MON-VER example in the gpsd documentation;
the device path comes from the reported device. [gpsd DEVICE command](https://gpsd.io/gpsd_json.html#_device)

No recovery command is sent for normal UBX reception, silence, noise,
checksum-error-only input, ambiguous multiple devices, a device reported as
read-only, a closed writer, or file replay. NMEA-like strings inside binary
payloads are excluded from detection. The generic GPSD backend and existing
position/time validity decisions are unchanged.

Polls go through gpsd. The code adds no direct UART access, gpsd restart,
receiver reset, GPS aiding injection, or flash-save command. However,
**gpsd may change the receiver's current output configuration through its
existing initialization behavior after identifying the device.** `?NMEA` is
a communication diagnostic event; it does not update position, time, or
satellite counts.

Changed files:

- `python/PiFinder/gps_ubx_recovery.py`: NMEA observation, target selection,
  and retry limits.
- `python/PiFinder/gps_ubx_parser.py`: Stream preservation, detection and
  polling integration, and the `?NMEA` event.
- `python/tests/test_gps_ubx_recovery.py`: Failure, normal-operation, and
  boundary-condition regression tests.
- `python/tests/test_gps_ubx_dispatch.py`: Event delivery and verification
  that the new marker does not supply position/time data.
- `python/tests/test_gps_time_sources.py`: Test corrections accounting for
  the existing preceding communication event.
- `docs/ax/gps/CONTEXT.md`: Current behavioral contract.
- This report and the development documentation indexes: Incident and
  validation records and their entry points.

## 5. Validation Results

### 5.1 Automated regression tests and static checks

**149 tests passed in 3.52 seconds** across these nine test files:

```sh
cd /home/pifinder/PiFinder/python
pytest -q tests/test_gps_ubx_recovery.py tests/test_gps_ubx_parser.py \
  tests/test_gps_ubx_dispatch.py tests/test_status_gps_comms.py \
  tests/test_gps_time_sources.py tests/test_gps_time_sync.py \
  tests/test_gps_time_sync_status_ui.py tests/test_server_gps_update.py \
  tests/test_gps_time_sync_helper.py
```

New coverage includes polls at 10/40/70 seconds and exhaustion of the attempt
limit; normal NAV processing after a recovery response; fragmented JSON,
NMEA, and UBX; mixed normal UBX and NMEA; NMEA inside a binary payload; noise,
checksum errors, and silence; multiple devices, other drivers, and read-only
device reports; file replay; drain timeout and BrokenPipe; preservation of
the attempt budget across repeated recoveries; and the text-buffer size limit.

The five changed Python files also passed `ruff check` and `ruff format`
verification.

The first regression run had 137 passes and two failures in existing time
tests. Both failures were reproduced by temporarily loading the parser from
the pre-change HEAD. Those tests assumed that the first queue item was time
data, overlooking the `comms` event already emitted by the code.
They now first verify `("comms", "NAV-PVT")`, then perform the original
time-content assertions. No production time-processing code was changed to
make these tests pass.

### 5.2 Real TCP transport and normal-device validation

An isolated TCP server acting as a gpsd stand-in was used with the production
`UBXParser.connect()` and `parse_messages()` methods. Valid NMEA was sent at
0.2-second intervals using the real monotonic clock and real waits.

- **One MON-VER poll was sent after 10.0608 seconds**, with the expected
  device path and command bytes.
- The parser processed 51 NMEA diagnostic events, followed by one version
  response event and one NAV-EOE event.
- Logs included `NMEA-only ... probe 1/3` and `UBX traffic resumed ...`.
- This was a synthetic gpsd stand-in; it did not execute gpsd's actual
  initialization code. Evidence for real gpsd identification recovery comes
  from the naturally occurring incident in Section 2.

The modified parser was then connected to the real gpsd as an additional
client and observed for **12.0024 seconds**.

- It processed 119 NAV-PVT, 119 NAV-SAT, 120 NAV-DOP, and other normal messages.
- All PVT reports in this interval indicated 2D; there were zero checksum
  errors and **zero automatic polls**.
- Existing unsupported messages, including NAV-VELECEF, were represented
  by markers such as `?0111` and `?0126`, following the existing contract.

The validation script and JSON results are retained in the evidence folder
listed in Section 7. These tests did not change the actual receiver's
configuration or deliberately induce a fault on it.

### 5.3 Device deployment

Before deployment, PiFinder's PID was `25064`. The mount was `usb_absent`,
with no motion; GoTo/Guide was `idle`, with no active tracking target.

Only the PiFinder service was restarted, at **03:46:51 KST**. The new PID
`120531` was confirmed active, with `NRestarts=0`. gpsd retained PID `1273`
throughout. GPS settings remained `ublox / auto / 115200`.

The main log confirmed resumed GPS time reception at 03:47:00. At 03:47:29,
the status file reported a latest `NAV-PVT` age of approximately 0.0006
seconds and 624 accumulated time samples. At that point, `lock_type=0`
indicated **no position fix**. `valid=true` described the time sample's
validity, not a successful position fix. Deployment validation therefore
confirmed resumed UBX communication and parsing, without establishing
continuous position lock or improved satellite signal quality.

During a final, separate 5-second subscription to the real gpsd at
**03:48:35–03:48:40 KST**, `driver=u-blox` and `native=1` remained set.
All 51 TPV reports indicated `mode=2`, using three satellites, confirming
**a 2D fix again**. The highest C/N0 in that interval was 24. The transition
from no fix to 2D after deployment is recorded as observed; it does not
establish a 3D fix or stable reception quality.

## 6. Limitations and Follow-up Observations

- The reason the initial identification response was missed remains
  unconfirmed. The new recovery logs record subsequent occurrence times,
  poll attempts, and whether UBX traffic resumes.
- The three-attempt limit applies **per connection**, not over the entire
  process lifetime. A new TCP connection starts a new budget. A normal UBX
  response alone does not replenish it.
- Silence, a physical UART wiring disconnection, and persistently corrupted
  binary input are outside the scope of this retry mechanism.
- Restored UBX communication does not guarantee a position fix or a given
  accuracy. The initial fix observed after recovery was 2D, not 3D.
- The working receiver was not deliberately switched to NMEA-only output
  or repeatedly power-cycled. This report distinguishes recovery of the
  natural incident by a real MON-VER poll, the synthetic TCP fault test,
  and normal-device reception checks. Automatic recovery success during
  future natural recurrences requires further observation.

## 7. Retained Evidence and Rollback

Local evidence folder:

`/home/pifinder/PiFinder_data/captures/analysis/20260909_gps_ubx_recovery/`

- `baseline.txt`, `gps_ubx_parser.py.before`: Baseline revision and the
  pre-change production parser.
- `test_summary.json`: Summary transcribed from test output, not a raw
  pytest log.
- `validate_tcp.py`, `tcp_validation.json`: TCP reproduction and normal-device
  validation code and results.
- `deployment.json`: Before/after PIDs, timestamps, GPS settings and status,
  and SHA-256 hashes of deployed production source files.
- `post_restart_gps_log.txt`, `post_restart_gpsd.json`: Post-deployment GPS
  logs and results from a separate gpsd subscription.

For rollback, preserve unrelated repository changes. After checking for
subsequent edits, restore the saved previous `gps_ubx_parser.py` and restart
PiFinder to return to the previous reception behavior. The old parser does
not reference the new helper file. No rollback of gpsd configuration, GPS
type, baud rate, or persistent receiver settings is required.
