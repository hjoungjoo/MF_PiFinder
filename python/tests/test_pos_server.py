import datetime
import queue

import pytest
import pytz
import quaternion

from PiFinder import pos_server
from PiFinder.types.positioning import ImuSample


class DummyLocation:
    lock = True
    lat = 37.5
    lon = 127.0
    altitude = 30.0


class DummyState:
    def __init__(self, imu_sample):
        self._imu_sample = imu_sample

    def location(self):
        return DummyLocation()

    def datetime(self):
        return datetime.datetime(2026, 7, 1, 12, tzinfo=pytz.UTC)

    def imu(self):
        return self._imu_sample

    def solution(self):
        return None


def test_imu_altaz_requires_calibrated_sample():
    sample = ImuSample(
        quat=quaternion.quaternion(1, 0, 0, 0),
        timestamp=0.0,
        status=0,
    )

    assert pos_server._imu_altaz_degrees(sample, "right") is None


def test_imu_fallback_returns_jnow_radec_for_calibrated_sample():
    sample = ImuSample(
        quat=quaternion.quaternion(1, 0, 0, 0),
        timestamp=0.0,
        status=3,
    )

    pointing = pos_server._imu_fallback_pointing_jnow(
        DummyState(sample), datetime.datetime(2026, 7, 1, 12, tzinfo=pytz.UTC)
    )

    assert pointing is not None
    ra_deg, dec_deg = pointing
    assert 0.0 <= ra_deg < 360.0
    assert -90.0 <= dec_deg <= 90.0


@pytest.mark.parametrize(
    ("ra_deg", "expected"),
    [
        (0.0, "00:00:00"),
        (15.0, "01:00:00"),
        (359.999, "00:00:00"),
    ],
)
def test_format_ra_degrees_wraps_to_lx200_hms(ra_deg, expected):
    assert pos_server._format_ra_degrees(ra_deg) == expected


@pytest.mark.parametrize(
    ("dec_deg", "expected"),
    [
        (0.0, "+00*00'00"),
        (-37.52704, "-37*31'37"),
        (89.9999, "+90*00'00"),
    ],
)
def test_format_dec_degrees_normalizes_seconds(dec_deg, expected):
    assert pos_server._format_dec_degrees(dec_deg) == expected


class DummyConfig:
    def __init__(self, options):
        self.options = options

    def get_option(self, option, default=None):
        return self.options.get(option, default)


def test_skysafari_guide_move_queues_indi_manual_motion(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(
        pos_server, "pos_server_config", DummyConfig({"mount_control": True})
    )

    assert pos_server.handle_guide_move(None, ":Mn#") is None

    assert commands.get_nowait() == {
        "type": "manual_movement",
        "direction": "north",
        "lease_seconds": pos_server._GUIDE_LEASE_SECONDS,
    }


def test_skysafari_guide_stop_queues_indi_stop(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(
        pos_server, "pos_server_config", DummyConfig({"mount_control": True})
    )

    assert pos_server.handle_guide_stop(None, ":Q#") is None

    assert commands.get_nowait() == {"type": "stop_movement"}


def test_skysafari_guide_move_ignored_when_mount_control_disabled(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(
        pos_server, "pos_server_config", DummyConfig({"mount_control": False})
    )

    assert pos_server.handle_guide_move(None, ":Me#") is None

    with pytest.raises(queue.Empty):
        commands.get_nowait()
