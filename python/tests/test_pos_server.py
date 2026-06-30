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
    def __init__(self, imu_sample, solution=None):
        self._imu_sample = imu_sample
        self._solution = solution

    def location(self):
        return DummyLocation()

    def datetime(self):
        return datetime.datetime(2026, 7, 1, 12, tzinfo=pytz.UTC)

    def imu(self):
        return self._imu_sample

    def solution(self):
        return self._solution


class DummySolution:
    def __init__(self, has_pointing):
        self._has_pointing = has_pointing

    def has_pointing(self):
        return self._has_pointing


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


def test_skysafari_goto_queues_indi_goto_when_enabled_and_solved(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig(
            {
                "mount_control": True,
                "skysafari_indi_goto": True,
                "indi_goto_refine_once": True,
                "indi_goto_refine_accuracy_arcmin": 4.5,
            }
        ),
    )

    queued = pos_server._queue_indi_goto_if_enabled(
        DummyState(None, DummySolution(True)), 12.5, -34.25
    )

    assert queued is True
    assert commands.get_nowait() == {
        "type": "goto_target",
        "ra": 12.5,
        "dec": -34.25,
        "refine_after_goto": True,
        "refine_accuracy_arcmin": 4.5,
    }


def test_skysafari_goto_skips_indi_goto_until_solved(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"mount_control": True, "skysafari_indi_goto": True}),
    )

    queued = pos_server._queue_indi_goto_if_enabled(
        DummyState(None, DummySolution(False)), 12.5, -34.25
    )

    assert queued is False
    with pytest.raises(queue.Empty):
        commands.get_nowait()


def test_skysafari_sync_queues_indi_sync_when_enabled(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(pos_server, "align_command_queue", None)
    monkeypatch.setattr(pos_server, "align_response_queue", None)
    monkeypatch.setattr(pos_server, "last_target_j2000", (12.5, -34.25))
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig(
            {
                "mount_control": True,
                "skysafari_indi_sync": True,
                "skysafari_pifinder_align": False,
            }
        ),
    )

    assert pos_server.handle_sync_command(DummyState(None), ":CM#") == (
        "Coordinates matched."
    )

    assert commands.get_nowait() == {
        "type": "sync",
        "ra": 12.5,
        "dec": -34.25,
    }


def test_skysafari_sync_returns_no_target_without_coordinates(monkeypatch):
    monkeypatch.setattr(pos_server, "last_target_j2000", None)
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"skysafari_pifinder_align": False}),
    )

    assert pos_server.handle_sync_command(DummyState(None), ":CM#") == "No target."
