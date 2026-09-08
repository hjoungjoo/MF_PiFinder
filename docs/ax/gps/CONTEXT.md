# GPS (MF fork)

The GPS process supplies position, time, satellite counts and communication
events to main through `gps_queue`. The last category is diagnostic only.

- **Fix**: a position that passes the backend's existing accuracy checks.
- **Time**: receiver time, governed by MF's separate validity and time-sync
  rules. Communication events never lock position or set system time.
- **Satellites**: MF retains four values: seen (signal-locked), used (in fix),
  in view, top C/N0 values.
  NAV-SAT remains preferred while fresh, with NAV-SVINFO fallback and
  NAV-PVT used-count handling unchanged.
- **Event**: any decoded UBX message or an undecodable-frame marker.
  GPSD publishes `TPV`/`SKY`; the fake backend publishes `FAKE`.
- **Marker**: `?XXYY` for an unsupported UBX class/id, `?CKSUM` for a checksum
  failure, `?NMEA` for checksum-valid navigation NMEA without a valid UBX
  frame in the same read batch. These indicate incoming frames, not a valid
  fix or time. NMEA is not decoded into navigation data by the UBX backend.
- **Comms row**: `GPS MSG` on STATUS shows the last reported event and its age;
  `NAV-` is omitted for space. Before any event it reads `--`.

`CommsPublisher` caps reporting at 20 Hz without throttling parsing or normal
fix/time/satellite messages. Main timestamps receipt using `time.monotonic()`;
STATUS uses the same clock so GPS-driven wall-clock changes cannot invert
the displayed age. This is age since receipt by main, not a precise serial
arrival timestamp. During bursts, events suppressed by the rate cap are not
displayed individually.

See [ADR 0032](../../adr/0032-ubx-parser-yields-undecodable-frames.md).

## NMEA-only u-blox identification recovery

The live UBX parser observes complete text lines outside UBX frames. If gpsd
advertises one local `/dev/` receiver as `NMEA0183` or `u-blox`, and at least
three checksum-valid navigation NMEA sentences span 10 seconds without valid
UBX, it sends a `MON-VER` poll through gpsd's `?DEVICE` command with an explicit
device path. A gap over 5 seconds in NMEA resets the observation window.
This lets gpsd identify the receiver and run its normal UBX configuration.

Probes are at least 30 seconds apart, capped at three per TCP connection,
including failed writes. Successful UBX responses reset the NMEA observation
window, but do not replenish the attempt budget. A new connection starts a
new budget. Silence, noise, ambiguous devices, file replay and the generic
GPSD backend never trigger this recovery. Drain waits are capped at 2 seconds.
No service restart, baud change, reset, aiding injection or persistent receiver
configuration command is sent by this code. gpsd can change the receiver's
active output configuration as a result of identifying it.

Probe attempts, resumed UBX traffic and retry exhaustion are logged at WARNING
under `GPS.parser.recovery`, visible with the default logging configuration.
UBX traffic resuming is distinct from obtaining a position fix.

Incident evidence, tests and deployment results:
[2026-09-09 report (Korean)](../../mf_report/mf_gps_ubx_recovery_20260909_ko.md).
