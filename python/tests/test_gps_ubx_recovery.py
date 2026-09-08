"""Regression coverage for NMEA-only u-blox identification stalls."""

import asyncio
import json

import pytest

from PiFinder.gps_ubx_parser import UBXParser
from PiFinder.gps_ubx_recovery import MAX_TEXT_BYTES, MON_VER_HEX, UBXRecovery

pytestmark = pytest.mark.unit


def nmea(body="GNRMC,183045.20,V,,,,,,,080926,,,N,V"):
    checksum = 0
    for value in body.encode():
        checksum ^= value
    return f"${body}*{checksum:02X}\r\n".encode()


def devices(*paths, driver="NMEA0183", **extra):
    return (
        json.dumps(
            {
                "class": "DEVICES",
                "devices": [{"path": p, "driver": driver, **extra} for p in paths],
            }
        )
        + "\r\n"
    ).encode()


def frame(cls=1, msg_id=0x61, payload=b"\0" * 4):
    return UBXParser(None)._generate_ubx_message(cls, msg_id, payload)


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class Reader:
    def __init__(self, clock, chunks):
        self.clock = clock
        self.chunks = iter(chunks)

    async def read(self, _size):
        self.clock.now, data = next(self.chunks, (self.clock.now, b""))
        return data


class Writer:
    def __init__(self):
        self.writes = []
        self.closed = False

    def is_closing(self):
        return self.closed

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    async def no_sleep(_delay):
        pass

    monkeypatch.setattr("PiFinder.gps_ubx_parser.asyncio.sleep", no_sleep)


def run_stream(chunks, *, writer=None, replay=False, handshake=False):
    clock = Clock()
    writer = writer if writer is not None else Writer()
    parser = UBXParser(
        None,
        reader=Reader(clock, chunks),
        writer=writer,
        file_path="recording.ubx" if replay else None,
        clock=clock,
    )

    async def collect():
        if handshake:
            await parser._handle_initial_messages()
        return [m["class"] async for m in parser.parse_messages()]

    return asyncio.run(collect()), writer, parser


def nmea_stream(end=100, path="/dev/ttyAMA2"):
    return [(0, devices(path))] + [(t, nmea()) for t in range(end + 1)]


def test_nmea_only_polls_exact_target_at_10_40_70_seconds(caplog):
    recovery = UBXRecovery()
    recovery.feed_text(devices("/dev/ttyAMA3"), 0)
    sent = []
    for t in range(301):
        assert recovery.feed_text(nmea(), t)
        command = recovery.next_probe(t)
        if command:
            sent.append((t, json.loads(command[len(b"?DEVICE=") : -2])))
    assert sent == [
        (t, {"path": "/dev/ttyAMA3", "hexdata": MON_VER_HEX}) for t in (10, 40, 70)
    ]
    assert caplog.text.count("retry limit reached") == 1


def test_parser_recovers_then_keeps_navigation_messages(caplog):
    chunks = nmea_stream(10)
    chunks += [(11, frame(0x0A, 4, b"version")), (12, frame()), (13, frame())]
    events, writer, parser = run_stream(chunks, handshake=True)
    assert events[-3:] == ["?0A04", "NAV-EOE", "NAV-EOE"]
    assert "?NMEA" in events
    assert writer.writes[0].startswith(b"?WATCH=")
    assert len(writer.writes) == 2
    assert parser._recovery.attempts == 1
    assert "UBX traffic resumed" in caplog.text


def test_handshake_keeps_coalesced_device_report_and_first_ubx():
    events, writer, parser = run_stream(
        [(0, devices("/dev/ttyAMA2") + frame())], handshake=True
    )
    assert events == ["NAV-EOE"]
    assert parser._recovery.device == "/dev/ttyAMA2"
    assert len(writer.writes) == 1  # WATCH only


@pytest.mark.parametrize("split", range(1, 12))
def test_fragmented_ubx_header_and_payload_are_preserved(split):
    packet = frame()
    events, writer, _ = run_stream([(0, packet[:split]), (1, packet[split:])])
    assert events == ["NAV-EOE"]
    assert not writer.writes


def test_fragmented_json_and_nmea_are_detected():
    report = devices("/dev/ttyAMA2")
    sentence = nmea()
    chunks = [(0, report[:25]), (0, report[25:])]
    for t in range(11):
        chunks.extend([(t, sentence[:12]), (t, sentence[12:])])
    events, writer, _ = run_stream(chunks)
    assert events.count("?NMEA") == 11
    assert len(writer.writes) == 1


def test_recent_ubx_suppresses_probe_even_after_nmea_in_same_chunk():
    chunks = [(0, devices("/dev/ttyAMA2"))]
    chunks += [(t, nmea() + frame()) for t in range(101)]
    events, writer, _ = run_stream(chunks)
    assert events.count("NAV-EOE") == 101
    assert not writer.writes


def test_nmea_inside_ubx_payload_does_not_trigger_probe():
    chunks = [(0, devices("/dev/ttyAMA2"))]
    chunks += [(t, frame(0x0A, 0xFF, nmea())) for t in range(101)]
    events, writer, _ = run_stream(chunks)
    assert set(events) == {"?0AFF"}
    assert not writer.writes


@pytest.mark.parametrize("payload", [b"garbage\r\n", b"$GNRMC,invalid*00\r\n", b"{}\n"])
def test_noise_and_invalid_nmea_never_probe(payload):
    chunks = [(0, devices("/dev/ttyAMA2"))] + [(t, payload) for t in range(101)]
    events, writer, _ = run_stream(chunks)
    assert events == []
    assert not writer.writes


def test_corrupt_ubx_stays_a_checksum_marker_without_probe():
    corrupt = frame()[:-1] + b"\xff"
    events, writer, _ = run_stream([(t, corrupt) for t in range(101)])
    assert set(events) == {"?CKSUM"}
    assert not writer.writes


def test_silence_and_sparse_nmea_do_not_trigger_recovery():
    recovery = UBXRecovery()
    recovery.feed_text(devices("/dev/ttyAMA2"), 0)
    for t in (0, 1, 2):
        recovery.feed_text(nmea(), t)
    assert recovery.next_probe(100) is None
    for t in (100, 110, 120):
        recovery.feed_text(nmea(), t)
        assert recovery.next_probe(t) is None
    events, writer, _ = run_stream([])
    assert events == []
    assert not writer.writes


@pytest.mark.parametrize(
    "report",
    [
        b"",
        devices(),
        devices("/dev/ttyAMA2", "/dev/ttyUSB0"),
        devices("tcp://remote:1234"),
        devices("/dev/ttyAMA2", driver="SiRF"),
        devices("/dev/ttyAMA2", readonly=True),
        b'{"class":"DEVICES","devices":[null]}\n',
        b'{"class":"DEVICES","devices":null}\n',
    ],
)
def test_unidentified_ambiguous_or_unsupported_device_never_receives_poll(report):
    events, writer, _ = run_stream([(0, report + nmea())] + nmea_stream(100)[1:])
    assert "?NMEA" in events
    assert not writer.writes


def test_changed_device_pool_clears_previous_poll_target():
    recovery = UBXRecovery()
    recovery.feed_text(devices("/dev/ttyAMA2"), 0)
    recovery.feed_text(devices("/dev/ttyAMA2", "/dev/ttyUSB0"), 1)
    for t in range(101):
        recovery.feed_text(nmea(), t)
        assert recovery.next_probe(t) is None


def test_file_replay_never_sends_commands_even_if_it_contains_gpsd_json():
    events, writer, _ = run_stream(nmea_stream(), replay=True)
    assert "?NMEA" in events
    assert not writer.writes


def test_closed_writer_never_sends_commands():
    writer = Writer()
    writer.closed = True
    _, writer, _ = run_stream(nmea_stream(), writer=writer)
    assert not writer.writes


def test_failed_drains_count_towards_retry_limit(caplog):
    class SlowWriter(Writer):
        async def drain(self):
            raise asyncio.TimeoutError

    _, writer, parser = run_stream(nmea_stream(), writer=SlowWriter())
    assert len(writer.writes) == parser._recovery.attempts == 3
    assert caplog.text.count("probe drain timed out") == 3


def test_broken_connection_closes_instead_of_repeating_probe():
    class BrokenWriter(Writer):
        def write(self, data):
            super().write(data)
            raise BrokenPipeError

    _, writer, parser = run_stream(nmea_stream(), writer=BrokenWriter())
    assert len(writer.writes) == parser._recovery.attempts == 1
    assert writer.closed


def test_success_does_not_replenish_connection_attempt_budget():
    recovery = UBXRecovery()
    recovery.feed_text(devices("/dev/ttyAMA2"), 0)
    sent = []
    for t in range(201):
        recovery.feed_text(nmea(), t)
        if recovery.next_probe(t):
            sent.append(t)
            recovery.observe_ubx(t)
    assert sent == [10, 40, 70]


def test_no_new_probe_immediately_after_ubx_disappears():
    recovery = UBXRecovery()
    recovery.feed_text(devices("/dev/ttyAMA2"), 0)
    recovery.observe_ubx(100)
    for t in range(101, 111):
        recovery.feed_text(nmea(), t)
        assert recovery.next_probe(t) is None
    recovery.feed_text(nmea(), 111)
    assert recovery.next_probe(111) is not None


def test_unterminated_text_buffer_is_bounded_and_recovers():
    recovery = UBXRecovery()
    recovery.feed_text(b"x" * (MAX_TEXT_BYTES + 1), 0)
    assert len(recovery.text) <= MAX_TEXT_BYTES
    assert recovery.feed_text(nmea(), 1)
