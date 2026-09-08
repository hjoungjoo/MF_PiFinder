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
  failure. These indicate incoming frames, not a valid fix or time.
- **Comms row**: `GPS MSG` on STATUS shows the last reported event and its age;
  `NAV-` is omitted for space. Before any event it reads `--`.

`CommsPublisher` caps reporting at 20 Hz without throttling parsing or normal
fix/time/satellite messages. Main timestamps receipt using `time.monotonic()`;
STATUS uses the same clock so GPS-driven wall-clock changes cannot invert
the displayed age. This is age since receipt by main, not a precise serial
arrival timestamp. During bursts, events suppressed by the rate cap are not
displayed individually.

See [ADR 0032](../../adr/0032-ubx-parser-yields-undecodable-frames.md).
