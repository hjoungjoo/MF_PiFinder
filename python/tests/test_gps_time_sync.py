import datetime
import json

import pytz

from PiFinder.gps_time_sync import ChronyClient, GpsTimeSyncMonitor


class FakeClock:
    def __init__(self, unix=1_700_000_000.0, monotonic=100.0):
        self.unix = unix
        self.monotonic = monotonic

    def time(self):
        return self.unix

    def monotonic_time(self):
        return self.monotonic

    def advance(self, seconds):
        self.unix += seconds
        self.monotonic += seconds


class FakeRequestWriter:
    def __init__(self, ok=True):
        self.ok = ok
        self.requests = []
        self.clear_count = 0

    def write_request(self, payload):
        self.requests.append(payload)
        if not self.ok:
            return {"ok": False, "message": "request write failed"}
        return {"ok": True, "message": "request write ok"}

    def clear_request(self):
        self.clear_count += 1


class FakeChronyClient:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def query(self, timeout_seconds=1.0):
        self.calls.append(timeout_seconds)
        return self.results.pop(0)


STABLE_CHRONY_RESULT = {
    "ok": True,
    "state": "stable",
    "message": "chronyd is tracking a time source",
    "reference_id": "7986D768 (121.134.215.104)",
    "reference_name": "121.134.215.104",
    "stratum": 3,
    "leap_status": "Normal",
    "rms_offset_seconds": 0.0007,
}


def utc(second):
    return datetime.datetime(2026, 1, 1, 0, 0, second, tzinfo=pytz.UTC)


def read_status(path):
    return json.loads(path.read_text())


def test_gps_time_monitor_marks_stable_after_enough_samples(tmp_path):
    clock = FakeClock()
    status_file = tmp_path / "gps_time_status.json"
    monitor = GpsTimeSyncMonitor(
        enabled=True,
        min_samples=3,
        stable_jitter_ms=100,
        stable_offset_ms=500,
        status_file=status_file,
        time_fn=clock.time,
        monotonic_fn=clock.monotonic_time,
    )

    for second, offset in [(1, 0.05), (2, 0.04), (3, 0.06)]:
        gps_dt = utc(second)
        reference_dt = gps_dt - datetime.timedelta(seconds=offset)
        monitor.observe_time(
            {"time": gps_dt, "tAcc": 10_000, "source": "GPS"}, reference_dt
        )
        clock.advance(1)

    status = read_status(status_file)
    assert status["state"] == "stable"
    assert status["samples"]["count"] == 3
    assert status["offset"]["latest_seconds"] == 0.06
    assert status["offset"]["jitter_seconds"] < 0.03


def test_gps_time_monitor_flags_low_quality_time_accuracy(tmp_path):
    clock = FakeClock()
    status_file = tmp_path / "gps_time_status.json"
    monitor = GpsTimeSyncMonitor(
        enabled=True,
        max_tacc_ns=500_000,
        status_file=status_file,
        time_fn=clock.time,
        monotonic_fn=clock.monotonic_time,
    )

    gps_dt = utc(10)
    monitor.observe_time(
        {"time": gps_dt, "tAcc": 5_000_000, "source": "GPS"},
        gps_dt,
    )

    status = read_status(status_file)
    assert status["state"] == "low_quality"
    assert status["latest"]["tAcc_ns"] == 5_000_000


def test_gps_time_monitor_flags_invalid_candidate(tmp_path):
    clock = FakeClock()
    status_file = tmp_path / "gps_time_status.json"
    monitor = GpsTimeSyncMonitor(
        enabled=True,
        status_file=status_file,
        time_fn=clock.time,
        monotonic_fn=clock.monotonic_time,
    )

    gps_dt = utc(11)
    monitor.observe_time(
        {
            "time": gps_dt,
            "valid": False,
            "source": "GPSD-SKY",
            "satellites_seen": 1,
            "satellites_used": 0,
        },
        gps_dt,
    )

    status = read_status(status_file)
    assert status["state"] == "low_quality"
    assert status["latest"]["valid"] is False
    assert status["latest"]["source"] == "GPSD-SKY"
    assert status["latest"]["satellites_seen"] == 1
    assert status["latest"]["satellites_used"] == 0


def test_gps_time_monitor_marks_samples_stale(tmp_path):
    clock = FakeClock()
    status_file = tmp_path / "gps_time_status.json"
    monitor = GpsTimeSyncMonitor(
        enabled=True,
        stale_seconds=5,
        status_file=status_file,
        time_fn=clock.time,
        monotonic_fn=clock.monotonic_time,
    )

    gps_dt = utc(20)
    monitor.observe_time({"time": gps_dt, "source": "GPS"}, gps_dt)
    clock.advance(6)
    monitor.poll()

    status = read_status(status_file)
    assert status["state"] == "stale"


def test_startup_status_clears_stale_file_when_disabled(tmp_path):
    clock = FakeClock()
    status_file = tmp_path / "gps_time_status.json"
    status_file.write_text('{"state": "stable"}')
    monitor = GpsTimeSyncMonitor(
        enabled=False,
        status_file=status_file,
        time_fn=clock.time,
        monotonic_fn=clock.monotonic_time,
    )

    monitor.write_startup_status()

    status = read_status(status_file)
    assert status["enabled"] is False
    assert status["state"] == "disabled"


def test_chrony_time_source_is_selected(tmp_path):
    clock = FakeClock(unix=utc(20).timestamp(), monotonic=100.0)
    chrony_client = FakeChronyClient([STABLE_CHRONY_RESULT])
    status_file = tmp_path / "gps_time_status.json"
    monitor = GpsTimeSyncMonitor(
        time_sync_enabled=True,
        chrony_enabled=True,
        status_file=status_file,
        time_fn=clock.time,
        monotonic_fn=clock.monotonic_time,
        chrony_client=chrony_client,
    )

    monitor.poll()

    status = read_status(status_file)
    assert status["state"] == "stable"
    assert status["clock_manager"] == "chrony"
    assert status["selected"]["source"] == "Chrony"
    assert status["selected"]["time"] == utc(20).isoformat()
    assert status["chrony"]["state"] == "stable"
    assert status["chrony"]["reference_name"] == "121.134.215.104"
    assert chrony_client.calls == [1.0]


def test_unsynced_chrony_leaves_no_selected_source(tmp_path):
    clock = FakeClock(unix=utc(20).timestamp(), monotonic=100.0)
    chrony_client = FakeChronyClient(
        [
            {
                "ok": True,
                "state": "unsynced",
                "message": "chronyd is not synchronized",
                "reference_id": "00000000 ()",
                "stratum": 0,
            }
        ]
    )
    status_file = tmp_path / "gps_time_status.json"
    monitor = GpsTimeSyncMonitor(
        time_sync_enabled=True,
        chrony_enabled=True,
        status_file=status_file,
        time_fn=clock.time,
        monotonic_fn=clock.monotonic_time,
        chrony_client=chrony_client,
    )

    monitor.poll()

    status = read_status(status_file)
    assert status["state"] == "unsynced"
    assert status["selected"] is None
    assert status["chrony"]["state"] == "unsynced"


def test_rtc_sync_writes_request_when_chrony_stable(tmp_path):
    clock = FakeClock(unix=utc(2).timestamp(), monotonic=100.0)
    request_writer = FakeRequestWriter()
    chrony_client = FakeChronyClient([STABLE_CHRONY_RESULT])
    status_file = tmp_path / "gps_time_status.json"
    monitor = GpsTimeSyncMonitor(
        time_sync_enabled=True,
        chrony_enabled=True,
        rtc_sync_enabled=True,
        status_file=status_file,
        time_fn=clock.time,
        monotonic_fn=clock.monotonic_time,
        request_writer=request_writer,
        chrony_client=chrony_client,
    )

    monitor.poll()

    status = read_status(status_file)
    assert status["state"] == "stable"
    assert status["rtc_sync"]["state"] == "requested"
    assert status["rtc_sync"]["request_count"] == 1
    assert len(request_writer.requests) == 1
    request = request_writer.requests[0]
    assert request["sync_time"] == utc(2).isoformat()
    assert request["actions"] == {
        "rtc": {"enabled": True, "min_interval_seconds": 3600.0}
    }
    assert request["selected"]["source"] == "Chrony"


def test_rtc_sync_waits_without_stable_time_source(tmp_path):
    clock = FakeClock()
    request_writer = FakeRequestWriter()
    status_file = tmp_path / "gps_time_status.json"
    monitor = GpsTimeSyncMonitor(
        enabled=True,
        rtc_sync_enabled=True,
        status_file=status_file,
        time_fn=clock.time,
        monotonic_fn=clock.monotonic_time,
        request_writer=request_writer,
    )

    gps_dt = utc(10)
    monitor.observe_time(
        {"time": gps_dt, "valid": False, "source": "GPSD-SKY"},
        gps_dt,
    )

    status = read_status(status_file)
    assert status["state"] == "low_quality"
    assert status["rtc_sync"]["state"] == "waiting_for_time_source"
    assert request_writer.requests == []
    assert request_writer.clear_count == 1


def test_status_payload_has_no_removed_sections(tmp_path):
    clock = FakeClock()
    status_file = tmp_path / "gps_time_status.json"
    monitor = GpsTimeSyncMonitor(
        enabled=True,
        status_file=status_file,
        time_fn=clock.time,
        monotonic_fn=clock.monotonic_time,
    )

    payload = monitor.status_payload()

    assert "ntp" not in payload
    assert "software_pps" not in payload
    assert "system_clock_sync" not in payload
    assert "ntp" not in payload["sources"]
    assert payload["clock_manager"] == "chrony"


def test_chrony_tracking_parser_reads_offsets():
    output = """\
Reference ID    : 7986D768 (121.134.215.104)
Stratum         : 3
Ref time (UTC)  : Sat Jun 27 08:53:12 2026
System time     : 0.000058003 seconds fast of NTP time
Last offset     : -0.000031634 seconds
RMS offset      : 0.000702867 seconds
Root delay      : 0.003410386 seconds
Root dispersion : 0.001339925 seconds
Skew            : 0.135 ppm
Leap status     : Normal
"""
    client = ChronyClient(time_fn=lambda: 1000.0)

    result = client._parse_tracking(output)

    assert result["state"] == "stable"
    assert result["reference_name"] == "121.134.215.104"
    assert result["stratum"] == 3
    assert result["system_time_offset_seconds"] == 0.000058003
    assert result["last_offset_seconds"] == -0.000031634
    assert result["rms_offset_seconds"] == 0.000702867
