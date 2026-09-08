"""Bounded u-blox identification retry for a live gpsd NMEA-only stream."""

import json
import logging
import re

logger = logging.getLogger("GPS.parser.recovery")

NMEA_ONLY_SECONDS = 10.0
NMEA_MAX_GAP_SECONDS = 5.0
PROBE_INTERVAL_SECONDS = 30.0
MAX_PROBES = 3  # Per connection, including failed writes; UBX does not reset it.
MAX_TEXT_BYTES = 8192
MON_VER_HEX = "b5620a0400000e34"
NMEA_SENTENCE = re.compile(
    rb"\$[A-Z]{2}(?:RMC|GGA|GSA|GSV|GLL|VTG|ZDA),[ -~]*\*[0-9A-Fa-f]{2}"
)


class UBXRecovery:
    """Observe text outside UBX frames; never scan binary payloads as NMEA.

    A single gpsd-advertised device is required before sending a poll. The
    connection's attempt budget survives successful responses, so a flapping
    receiver cannot cause an unbounded stream of identification commands.
    """

    def __init__(self):
        self.text = bytearray()
        self.device = None
        self.nmea_since = None
        self.last_nmea = None
        self.nmea_count = 0
        self.last_ubx = None
        self.attempts = 0
        self.last_probe = None
        self.awaiting_ubx = False
        self.exhausted_logged = False

    def feed_text(self, data, now):
        """Return whether complete, checksum-valid navigation NMEA arrived."""
        self.text.extend(data)
        seen = False
        while b"\n" in self.text:
            line, _, rest = self.text.partition(b"\n")
            self.text = bytearray(rest)
            if len(line) > MAX_TEXT_BYTES:
                continue
            line = line.rstrip(b"\r")
            if line.startswith(b"{"):
                self._device_report(line)
            elif len(line) <= 256 and NMEA_SENTENCE.fullmatch(line):
                checksum = 0
                for value in line[1:-3]:
                    checksum ^= value
                if checksum != int(line[-2:], 16):
                    continue
                if (
                    self.last_nmea is None
                    or now - self.last_nmea > NMEA_MAX_GAP_SECONDS
                ):
                    self.nmea_since = now
                    self.nmea_count = 0
                self.last_nmea = now
                self.nmea_count += 1
                seen = True
        if len(self.text) > MAX_TEXT_BYTES:
            self.text.clear()
        return seen

    def _device_report(self, line):
        try:
            report = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(report, dict) or report.get("class") != "DEVICES":
            return
        # Clear a previous target if gpsd now advertises an ambiguous pool.
        self.device = None
        devices = report.get("devices")
        if not isinstance(devices, list) or len(devices) != 1:
            return
        device = devices[0]
        if not isinstance(device, dict):
            return
        path = device.get("path")
        if (
            isinstance(path, str)
            and path.startswith("/dev/")
            and device.get("driver") in ("NMEA0183", "u-blox")
            and not device.get("readonly", False)
        ):
            self.device = path

    def observe_ubx(self, now):
        """Any checksum-valid UBX frame proves binary communication is alive."""
        self.text.clear()
        self.last_ubx = now
        self.nmea_since = None
        self.last_nmea = None
        self.nmea_count = 0
        if self.awaiting_ubx:
            logger.warning(
                "UBX traffic resumed after MON-VER probe %d/%d on %s",
                self.attempts,
                MAX_PROBES,
                self.device,
            )
            self.awaiting_ubx = False

    def next_probe(self, now):
        """Reserve a bounded attempt; caller sends it only on a live stream."""
        if (
            self.device is None
            or self.nmea_since is None
            or self.nmea_count < 3
            or now - self.nmea_since < NMEA_ONLY_SECONDS
            or now - self.last_nmea > NMEA_MAX_GAP_SECONDS
            or (self.last_ubx is not None and now - self.last_ubx < NMEA_ONLY_SECONDS)
            or (
                self.last_probe is not None
                and now - self.last_probe < PROBE_INTERVAL_SECONDS
            )
        ):
            return None
        if self.attempts >= MAX_PROBES:
            if not self.exhausted_logged:
                logger.warning(
                    "UBX identification retry limit reached on %s; "
                    "NMEA continues, no further probes on this connection",
                    self.device,
                )
                self.exhausted_logged = True
            return None
        self.attempts += 1
        self.last_probe = now
        self.awaiting_ubx = True
        logger.warning(
            "NMEA-only GPS stream for %.1fs on %s; MON-VER probe %d/%d",
            now - self.nmea_since,
            self.device,
            self.attempts,
            MAX_PROBES,
        )
        command = {"path": self.device, "hexdata": MON_VER_HEX}
        return (
            "?DEVICE=" + json.dumps(command, separators=(",", ":")) + ";\n"
        ).encode()
