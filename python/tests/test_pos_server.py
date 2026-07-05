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
    def __init__(self, imu_sample, solution=None, dt=None, location=None):
        self._imu_sample = imu_sample
        self._solution = solution
        self._dt = dt
        self._location = location
        self._ui_state = DummyUiState()

    def location(self):
        return self._location or DummyLocation()

    def datetime(self):
        if self._dt == "none":
            return None
        return self._dt or datetime.datetime(2026, 7, 1, 12, tzinfo=pytz.UTC)

    def imu(self):
        return self._imu_sample

    def solution(self):
        return self._solution

    def ui_state(self):
        return self._ui_state


class DummyUiState:
    def __init__(self):
        self.recent = []
        self.new_pushto = False

    def add_recent(self, obj):
        self.recent.append(obj)

    def set_new_pushto(self, value):
        self.new_pushto = value


class DummySolution:
    def __init__(self, has_pointing):
        self._has_pointing = has_pointing

    def has_pointing(self):
        return self._has_pointing


class DummyConfigLocation:
    name = "Pungnap-dong"
    latitude = 37.52704
    longitude = 127.10936
    height = 30.0
    error_in_m = 1000.0


class DummyUnlockedLocation:
    lock = False
    lat = 0.0
    lon = 0.0
    altitude = 0.0


@pytest.fixture(autouse=True)
def reset_imu_alignment_correction():
    pos_server._reset_imu_alignment_correction("test setup")
    pos_server._invalidate_pointing_cache()
    yield
    pos_server._reset_imu_alignment_correction("test teardown")
    pos_server._invalidate_pointing_cache()


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


def test_current_datetime_falls_back_to_system_utc_when_pifinder_time_missing():
    dt = pos_server._current_datetime(DummyState(None, dt="none"))

    assert dt.tzinfo is not None
    assert dt.utcoffset() == datetime.timedelta(0)


def test_observer_location_uses_config_default_when_shared_location_unlocked(
    monkeypatch,
):
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"locations.default": DummyConfigLocation()}),
    )
    location = pos_server._observer_location(
        DummyState(None, location=DummyUnlockedLocation())
    )

    assert location.lock is True
    assert location.lat == pytest.approx(37.52704)
    assert location.lon == pytest.approx(127.10936)
    assert location.source == "CONFIG: Pungnap-dong"


def test_get_telescope_ra_returns_lx200_ra_default_without_pointing():
    pos_server._pointing_cache["time"] = 0.0
    pos_server._pointing_cache["value"] = None

    response = pos_server.get_telescope_ra(DummyState(None, dt="none"), ":GR#")
    assert response == "00:00:00"


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

    def load_config(self):
        return None


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


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"mount_type": "Alt/Az"}, "AT1"),
        ({"mount_type": "EQ"}, "PT1"),
        ({"mount_type": "EQ", "skysafari_lx200_mount_code": "G"}, "GT1"),
        ({"mount_type": "Alt/Az", "skysafari_lx200_mount_code": "P"}, "PT1"),
    ],
)
def test_skysafari_status_reports_configured_mount_mode(monkeypatch, options, expected):
    monkeypatch.setattr(pos_server, "pos_server_config", DummyConfig(options))

    assert pos_server.get_status(None, ":GW#") == expected


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


def test_skysafari_goto_queues_indi_goto_without_refine_until_solved(monkeypatch):
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
            }
        ),
    )

    queued = pos_server._queue_indi_goto_if_enabled(
        DummyState(None, DummySolution(False)), 12.5, -34.25
    )

    assert queued is True
    assert commands.get_nowait() == {
        "type": "goto_target",
        "ra": 12.5,
        "dec": -34.25,
        "refine_after_goto": False,
        "refine_accuracy_arcmin": 10.0,
    }


def test_skysafari_ms_command_triggers_indi_goto(monkeypatch):
    commands = queue.Queue()
    ui_commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(pos_server, "ui_queue", ui_commands, raising=False)
    monkeypatch.setattr(pos_server, "is_stellarium", True)
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"mount_control": True, "skysafari_indi_goto": True}),
    )

    assert pos_server.parse_sr_command(None, ":Sr12:30:00#") == "1"
    assert pos_server.parse_sd_command(DummyState(None), ":Sd-34*15:00#") == "1"

    with pytest.raises(queue.Empty):
        commands.get_nowait()

    assert pos_server.handle_slew_command(DummyState(None), ":MS#") == "0"
    assert ui_commands.get_nowait() == "push_object"
    assert commands.get_nowait() == {
        "type": "goto_target",
        "ra": 187.5,
        "dec": -34.25,
        "refine_after_goto": False,
        "refine_accuracy_arcmin": 10.0,
    }


def test_skysafari_ms_command_does_not_push_ui_during_multipoint_align(monkeypatch):
    commands = queue.Queue()
    ui_commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(pos_server, "ui_queue", ui_commands, raising=False)
    monkeypatch.setattr(pos_server, "is_stellarium", True)
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)
    monkeypatch.setattr(
        pos_server,
        "_mount_control_status",
        lambda: {"multipoint_align": {"active": True}},
    )
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"mount_control": True, "skysafari_indi_goto": False}),
    )

    assert pos_server.parse_sr_command(None, ":Sr12:30:00#") == "1"
    assert pos_server.parse_sd_command(DummyState(None), ":Sd-34*15:00#") == "1"
    assert pos_server.handle_slew_command(DummyState(None), ":MS#") == "0"

    with pytest.raises(queue.Empty):
        ui_commands.get_nowait()
    assert commands.get_nowait() == {
        "type": "multipoint_align_goto_target",
        "ra": 187.5,
        "dec": -34.25,
        "name": "SkySafari Target",
    }


def test_skysafari_ms_command_returns_error_without_target(monkeypatch):
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)

    assert pos_server.handle_slew_command(DummyState(None), ":MS#") == "1"


def test_distance_bars_follow_mount_control_slew_state(monkeypatch):
    monkeypatch.setattr(pos_server, "_skysafari_slew_started_at", 0.0)
    monkeypatch.setattr(pos_server, "_skysafari_saw_mount_slew", False)
    monkeypatch.setattr(
        pos_server, "_mount_control_status", lambda: {"state": "slewing"}
    )

    assert pos_server.get_distance_bars(None, ":D#") == "\x7f"

    monkeypatch.setattr(
        pos_server, "_mount_control_status", lambda: {"state": "connected"}
    )
    assert pos_server.get_distance_bars(None, ":D#") == ""


def test_distance_bars_show_initial_grace_after_slew_command(monkeypatch):
    monkeypatch.setattr(pos_server, "_skysafari_saw_mount_slew", False)
    monkeypatch.setattr(pos_server, "_mount_control_status", lambda: {})

    pos_server._mark_skysafari_slew_started()

    assert pos_server.get_distance_bars(None, ":D#") == "\x7f"


def test_handle_client_sends_empty_lx200_response(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.recv_values = [b":D#", b""]
            self.sent = []

        def settimeout(self, _timeout):
            return None

        def setsockopt(self, *_args):
            return None

        def recv(self, _size):
            return self.recv_values.pop(0)

        def send(self, data):
            self.sent.append(data)

        def close(self):
            return None

    fake_socket = FakeSocket()
    monkeypatch.setitem(pos_server.lx_command_dict, "D", lambda *_args: "")

    pos_server.handle_client(fake_socket, DummyState(None))

    assert fake_socket.sent == [b"#"]


def test_handle_client_processes_multiple_lx200_commands_in_one_packet(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.recv_values = [b":D#:GW#", b""]
            self.sent = []

        def settimeout(self, _timeout):
            return None

        def setsockopt(self, *_args):
            return None

        def recv(self, _size):
            return self.recv_values.pop(0)

        def send(self, data):
            self.sent.append(data)

        def close(self):
            return None

    fake_socket = FakeSocket()
    monkeypatch.setitem(pos_server.lx_command_dict, "D", lambda *_args: "")
    monkeypatch.setitem(pos_server.lx_command_dict, "GW", lambda *_args: "AT1")

    pos_server.handle_client(fake_socket, DummyState(None))

    assert fake_socket.sent == [b"#", b"AT1"]


@pytest.mark.parametrize("status", ["AT1", "PT1", "GT1"])
def test_lx200_mount_status_response_is_not_hash_terminated(status):
    assert pos_server._format_lx200_response(status) == status.encode()


def test_handle_client_processes_split_lx200_command(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.recv_values = [b":D", b"#", b""]
            self.sent = []

        def settimeout(self, _timeout):
            return None

        def setsockopt(self, *_args):
            return None

        def recv(self, _size):
            return self.recv_values.pop(0)

        def send(self, data):
            self.sent.append(data)

        def close(self):
            return None

    fake_socket = FakeSocket()
    monkeypatch.setitem(pos_server.lx_command_dict, "D", lambda *_args: "")

    pos_server.handle_client(fake_socket, DummyState(None))

    assert fake_socket.sent == [b"#"]


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


def test_skysafari_sync_queues_indi_sync_when_goto_forwarding_enabled(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(pos_server, "align_command_queue", None)
    monkeypatch.setattr(pos_server, "align_response_queue", None)
    monkeypatch.setattr(pos_server, "last_target_j2000", (42.0, 15.5))
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig(
            {
                "mount_control": True,
                "skysafari_indi_goto": True,
                "skysafari_indi_sync": False,
                "skysafari_pifinder_align": False,
            }
        ),
    )

    assert pos_server.handle_sync_command(DummyState(None), ":CM#") == (
        "Coordinates matched."
    )

    assert commands.get_nowait() == {
        "type": "sync",
        "ra": 42.0,
        "dec": 15.5,
    }


def test_skysafari_sync_prefers_current_sr_sd_over_previous_goto(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(pos_server, "align_command_queue", None)
    monkeypatch.setattr(pos_server, "align_response_queue", None)
    monkeypatch.setattr(pos_server, "last_target_j2000", (12.5, -34.25))
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)
    monkeypatch.setattr(pos_server, "is_stellarium", True)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig(
            {
                "mount_control": True,
                "skysafari_indi_sync": True,
                "skysafari_pifinder_align": False,
                "skysafari_imu_align_without_solve": False,
            }
        ),
    )

    assert pos_server.parse_sr_command(None, ":Sr02:00:00#") == "1"
    assert pos_server.parse_sd_command(None, ":Sd+10*00:00#") == "1"
    assert pos_server.handle_sync_command(DummyState(None), ":CM#") == (
        "Coordinates matched."
    )

    assert commands.get_nowait() == {
        "type": "sync",
        "ra": 30.0,
        "dec": 10.0,
    }


def test_skysafari_sync_sets_imu_alignment_without_plate_solve(monkeypatch):
    dt = datetime.datetime(2026, 7, 1, 12, tzinfo=pytz.UTC)
    target_j2000 = (100.0, -20.0)
    location = DummyLocation()
    pos_server.sf_utils.set_location(location.lat, location.lon, location.altitude)
    target_alt, target_az = pos_server.sf_utils.radec_to_altaz(
        target_j2000[0], target_j2000[1], dt
    )
    raw_alt = target_alt - 1.25
    raw_az = (target_az - 12.5) % 360.0

    monkeypatch.setattr(pos_server, "last_target_j2000", target_j2000)
    monkeypatch.setattr(
        pos_server, "_imu_altaz_degrees", lambda *_args: (raw_alt, raw_az)
    )
    monkeypatch.setattr(
        pos_server,
        "_align_pifinder_if_enabled",
        lambda *_args: pytest.fail("PiFinder plate-solve align should not run"),
    )
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig(
            {
                "mount_control": False,
                "skysafari_imu_align_without_solve": True,
                "skysafari_pifinder_align": True,
                "skysafari_indi_sync": False,
            }
        ),
    )

    assert (
        pos_server.handle_sync_command(
            DummyState(None, DummySolution(False), dt=dt, location=location), ":CM#"
        )
        == "Coordinates matched."
    )

    assert pos_server._imu_alignment_correction["active"] is True
    corrected_alt, corrected_az = pos_server._apply_imu_alignment_correction(
        raw_alt, raw_az
    )
    assert corrected_alt == pytest.approx(target_alt)
    assert pos_server._wrap_angle_delta_degrees(corrected_az - target_az) == (
        pytest.approx(0.0)
    )


def test_solved_pointing_resets_imu_alignment_correction(monkeypatch):
    pos_server._imu_alignment_correction.update(
        {
            "active": True,
            "alt_offset": 1.0,
            "az_offset": 2.0,
            "set_at": 1.0,
            "target_j2000": (100.0, -20.0),
        }
    )
    monkeypatch.setattr(
        pos_server, "_solved_pointing_jnow", lambda *_args: (12.0, 34.0)
    )

    assert pos_server._current_pointing_jnow(DummyState(None)) == (12.0, 34.0)

    assert pos_server._imu_alignment_correction["active"] is False


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
