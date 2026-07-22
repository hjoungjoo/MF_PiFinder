import datetime
import queue
import time

import pytest
import pytz
import quaternion

from PiFinder import nonsidereal, pos_server, track_freq_policy
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
def sidereal_mount_status(monkeypatch):
    """Keep the tracking-frequency policy off the live mount status file.

    track_freq_policy._mount_status reads /dev/shm on a running PiFinder, so
    without this a GoTo test's queue contents depend on whatever frequency the
    real mount happens to be at. Default to sidereal (nothing to reset); tests
    that exercise the reset override it.
    """
    monkeypatch.setattr(track_freq_policy, "_mount_status", lambda: {})
    # Keep the ephemeris out of GoTo tests too; planet identification has its
    # own coverage in test_track_freq_policy.py.
    monkeypatch.setattr(
        track_freq_policy, "planet_at_coordinates", lambda *args, **kwargs: None
    )


@pytest.fixture(autouse=True)
def reset_imu_alignment_correction():
    pos_server._stop_skysafari_guide_keepalive()
    pos_server._reset_imu_alignment_correction("test setup")
    pos_server._coordinate_service.clear_state()
    pos_server._invalidate_pointing_cache()
    yield
    pos_server._stop_skysafari_guide_keepalive()
    pos_server._reset_imu_alignment_correction("test teardown")
    pos_server._coordinate_service.clear_state()
    pos_server._invalidate_pointing_cache()


@pytest.mark.unit
def test_imu_altaz_requires_calibrated_sample():
    sample = ImuSample(
        quat=quaternion.quaternion(1, 0, 0, 0),
        timestamp=0.0,
        status=0,
    )

    assert pos_server._imu_altaz_degrees(sample, "right") is None


@pytest.mark.unit
def test_imu_fallback_returns_radec_for_calibrated_sample():
    sample = ImuSample(
        quat=quaternion.quaternion(1, 0, 0, 0),
        timestamp=0.0,
        status=3,
    )

    pointing = pos_server._imu_fallback_pointing(
        DummyState(sample), datetime.datetime(2026, 7, 1, 12, tzinfo=pytz.UTC)
    )

    assert pointing is not None
    ra_deg, dec_deg = pointing
    assert 0.0 <= ra_deg < 360.0
    assert -90.0 <= dec_deg <= 90.0


@pytest.mark.unit
def test_current_datetime_falls_back_to_system_utc_when_pifinder_time_missing():
    dt = pos_server._current_datetime(DummyState(None, dt="none"))

    assert dt.tzinfo is not None
    assert dt.utcoffset() == datetime.timedelta(0)


@pytest.mark.unit
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


@pytest.mark.unit
def test_get_telescope_ra_returns_lx200_ra_default_without_pointing():
    pos_server._coordinate_service.clear_state()

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
@pytest.mark.unit
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
@pytest.mark.unit
def test_format_dec_degrees_normalizes_seconds(dec_deg, expected):
    assert pos_server._format_dec_degrees(dec_deg) == expected


class DummyConfig:
    def __init__(self, options):
        self.options = options

    def get_option(self, option, default=None):
        return self.options.get(option, default)

    def load_config(self):
        return None


@pytest.mark.unit
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


@pytest.mark.unit
def test_skysafari_guide_move_sends_keepalive_until_stop(monkeypatch):
    class FakeTimer:
        timers = []

        def __init__(self, interval, function, args=()):
            self.interval = interval
            self.function = function
            self.args = args
            self.cancelled = False
            self.started = False
            self.daemon = False

        def start(self):
            self.started = True
            FakeTimer.timers.append(self)

        def cancel(self):
            self.cancelled = True

        def fire(self):
            if not self.cancelled:
                self.function(*self.args)

    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(pos_server.threading, "Timer", FakeTimer)
    monkeypatch.setattr(
        pos_server, "pos_server_config", DummyConfig({"mount_control": True})
    )

    assert pos_server.handle_guide_move(None, ":Mn#") is None

    assert commands.get_nowait() == {
        "type": "manual_movement",
        "direction": "north",
        "lease_seconds": pos_server._GUIDE_LEASE_SECONDS,
    }
    assert len(FakeTimer.timers) == 1
    assert FakeTimer.timers[-1].interval == pos_server._GUIDE_KEEPALIVE_SECONDS

    FakeTimer.timers[-1].fire()
    assert commands.get_nowait() == {
        "type": "manual_movement_keepalive",
        "direction": "north",
        "lease_seconds": pos_server._GUIDE_LEASE_SECONDS,
    }

    with pos_server._guide_motion_lock:
        pos_server._guide_motion_state["next_restart_at"] = time.monotonic() - 1.0
    FakeTimer.timers[-1].fire()
    assert commands.get_nowait() == {
        "type": "manual_movement",
        "direction": "north",
        "lease_seconds": pos_server._GUIDE_LEASE_SECONDS,
    }

    assert pos_server.handle_guide_stop(None, ":Qn#") is None
    assert commands.get_nowait() == {"type": "stop_movement"}
    assert FakeTimer.timers[-1].cancelled


@pytest.mark.unit
def test_skysafari_guide_move_survives_short_command_connection(monkeypatch):
    class FakeTimer:
        timers = []

        def __init__(self, interval, function, args=()):
            self.interval = interval
            self.function = function
            self.args = args
            self.cancelled = False
            self.daemon = False

        def start(self):
            FakeTimer.timers.append(self)

        def cancel(self):
            self.cancelled = True

    class FakeSocket:
        def __init__(self):
            self.reads = [b":Mn#", b""]
            self.closed = False

        def settimeout(self, _timeout):
            return None

        def setsockopt(self, *_args):
            return None

        def recv(self, _size):
            return self.reads.pop(0)

        def send(self, _data):
            return None

        def close(self):
            self.closed = True

    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(pos_server.threading, "Timer", FakeTimer)
    monkeypatch.setattr(
        pos_server, "pos_server_config", DummyConfig({"mount_control": True})
    )

    fake_socket = FakeSocket()
    pos_server.handle_client(fake_socket, DummyState(None))

    assert fake_socket.closed
    assert commands.get_nowait() == {
        "type": "manual_movement",
        "direction": "north",
        "lease_seconds": pos_server._GUIDE_LEASE_SECONDS,
    }
    with pytest.raises(queue.Empty):
        commands.get_nowait()
    assert pos_server._guide_motion_state["direction"] == "north"
    assert len(FakeTimer.timers) == 1


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"mount_type": "Alt/Az"}, "AT1"),
        ({"mount_type": "EQ"}, "PT1"),
        ({"mount_type": "EQ", "skysafari_lx200_mount_code": "G"}, "GT1"),
        ({"mount_type": "Alt/Az", "skysafari_lx200_mount_code": "P"}, "PT1"),
    ],
)
@pytest.mark.unit
def test_skysafari_status_reports_configured_mount_mode(monkeypatch, options, expected):
    monkeypatch.setattr(pos_server, "pos_server_config", DummyConfig(options))

    assert pos_server.get_status(None, ":GW#") == expected


@pytest.mark.unit
def test_skysafari_guide_stop_queues_indi_stop(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(
        pos_server, "pos_server_config", DummyConfig({"mount_control": True})
    )

    assert pos_server.handle_guide_stop(None, ":Q#") is None

    assert commands.get_nowait() == {"type": "stop_movement"}


@pytest.mark.unit
def test_skysafari_guide_move_ignored_when_mount_control_disabled(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(
        pos_server, "pos_server_config", DummyConfig({"mount_control": False})
    )

    assert pos_server.handle_guide_move(None, ":Me#") is None

    with pytest.raises(queue.Empty):
        commands.get_nowait()


@pytest.mark.unit
def test_skysafari_goto_routes_to_goto_guide_by_default(monkeypatch):
    guide_commands = queue.Queue()
    monkeypatch.setattr(pos_server, "goto_guide_queue", guide_commands)
    monkeypatch.setattr(pos_server, "mountcontrol_queue", queue.Queue())
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"mount_control": True}),
    )

    queued = pos_server._queue_indi_goto_if_enabled(
        DummyState(None, DummySolution(True)), 12.5, -34.25
    )

    assert queued is True
    assert guide_commands.get_nowait() == {
        "type": "goto_target",
        "ra": 12.5,
        "dec": -34.25,
    }


@pytest.mark.unit
def test_skysafari_goto_resets_active_non_sidereal_frequency(monkeypatch):
    """A SkySafari GoTo that is not on a planet is a sidereal target: a
    frequency left over from an earlier planet GoTo must be reset."""
    mount_commands = queue.Queue()
    monkeypatch.setattr(pos_server, "goto_guide_queue", queue.Queue())
    monkeypatch.setattr(pos_server, "mountcontrol_queue", mount_commands)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"mount_control": True}),
    )
    monkeypatch.setattr(
        track_freq_policy, "_mount_status", lambda: {"track_freq_hz": 60.12627}
    )

    pos_server._queue_indi_goto_if_enabled(
        DummyState(None, DummySolution(True)), 12.5, -34.25
    )

    assert mount_commands.get_nowait() == {"type": "reset_track_freq"}


@pytest.mark.unit
def test_skysafari_goto_to_planet_coordinates_sets_feed_forward_rate(monkeypatch):
    """SkySafari sends no object type, so a planet is recognised by position."""
    mount_commands = queue.Queue()
    monkeypatch.setattr(pos_server, "goto_guide_queue", queue.Queue())
    monkeypatch.setattr(pos_server, "mountcontrol_queue", mount_commands)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"mount_control": True}),
    )
    monkeypatch.setattr(
        track_freq_policy,
        "planet_at_coordinates",
        lambda ra, dec, state, **kwargs: "JUPITER"
        if (ra, dec) == (12.5, -34.25)
        else None,
    )
    monkeypatch.setattr(track_freq_policy, "planet_dra_dt", lambda name, state: 0.5)

    pos_server._queue_indi_goto_if_enabled(
        DummyState(None, DummySolution(True)), 12.5, -34.25
    )

    command = mount_commands.get_nowait()
    assert command["type"] == "set_track_freq"
    assert command["label"] == "Jupiter"
    # Eastward motion means the mount must run slower than sidereal.
    assert command["hz"] < nonsidereal.SIDEREAL_FREQ_HZ


@pytest.mark.unit
def test_skysafari_planet_identification_can_be_disabled(monkeypatch):
    """With the option off, a GoTo sitting exactly on a planet is still
    treated as sidereal -- identification by position is a guess, and the
    user may be targeting a star the planet happens to be occulting."""
    mount_commands = queue.Queue()
    monkeypatch.setattr(pos_server, "goto_guide_queue", queue.Queue())
    monkeypatch.setattr(pos_server, "mountcontrol_queue", mount_commands)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"mount_control": True, "skysafari_planet_track_freq": False}),
    )
    monkeypatch.setattr(
        track_freq_policy, "planet_at_coordinates", lambda *args, **kwargs: "JUPITER"
    )
    monkeypatch.setattr(
        track_freq_policy, "_mount_status", lambda: {"track_freq_hz": 60.12627}
    )

    pos_server._queue_indi_goto_if_enabled(
        DummyState(None, DummySolution(True)), 12.5, -34.25
    )

    assert mount_commands.get_nowait() == {"type": "reset_track_freq"}


@pytest.mark.unit
def test_skysafari_goto_leaves_sidereal_mount_untouched(monkeypatch):
    mount_commands = queue.Queue()
    monkeypatch.setattr(pos_server, "goto_guide_queue", queue.Queue())
    monkeypatch.setattr(pos_server, "mountcontrol_queue", mount_commands)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"mount_control": True}),
    )
    monkeypatch.setattr(track_freq_policy, "_mount_status", lambda: {})

    pos_server._queue_indi_goto_if_enabled(
        DummyState(None, DummySolution(True)), 12.5, -34.25
    )

    with pytest.raises(queue.Empty):
        mount_commands.get_nowait()


@pytest.mark.unit
def test_multipoint_align_goto_does_not_require_goto_guide_queue(monkeypatch):
    """A multi-point align GoTo only ever touches mount control, so an absent
    GoTo/Guide queue must not drop it -- the same reason the "GoTo Type off"
    bypass above exists."""
    mount_commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", mount_commands)
    monkeypatch.setattr(pos_server, "goto_guide_queue", None)
    monkeypatch.setattr(
        pos_server,
        "_mount_control_status",
        lambda: {"multipoint_align": {"active": True}},
    )
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"mount_control": True, "indi_goto_method": "off"}),
    )

    queued = pos_server._queue_indi_goto_if_enabled(
        DummyState(None, DummySolution(True)), 187.5, -34.25
    )

    assert queued is True
    assert mount_commands.get_nowait() == {
        "type": "multipoint_align_goto_target",
        "ra": 187.5,
        "dec": -34.25,
        "name": "SkySafari Target",
    }


@pytest.mark.unit
def test_skysafari_goto_skipped_when_goto_method_off(monkeypatch):
    guide_commands = queue.Queue()
    monkeypatch.setattr(pos_server, "goto_guide_queue", guide_commands)
    monkeypatch.setattr(pos_server, "mountcontrol_queue", queue.Queue())
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"mount_control": True, "indi_goto_method": "off"}),
    )

    queued = pos_server._queue_indi_goto_if_enabled(
        DummyState(None, DummySolution(True)), 12.5, -34.25
    )

    assert queued is False
    with pytest.raises(queue.Empty):
        guide_commands.get_nowait()


@pytest.mark.unit
def test_skysafari_ms_command_triggers_indi_goto(monkeypatch):
    guide_commands = queue.Queue()
    ui_commands = queue.Queue()
    monkeypatch.setattr(pos_server, "goto_guide_queue", guide_commands)
    monkeypatch.setattr(pos_server, "mountcontrol_queue", queue.Queue())
    monkeypatch.setattr(pos_server, "ui_queue", ui_commands, raising=False)
    monkeypatch.setattr(pos_server, "is_stellarium", True)
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"mount_control": True}),
    )

    assert pos_server.parse_sr_command(None, ":Sr12:30:00#") == "1"
    assert pos_server.parse_sd_command(DummyState(None), ":Sd-34*15:00#") == "1"

    with pytest.raises(queue.Empty):
        guide_commands.get_nowait()

    assert pos_server.handle_slew_command(DummyState(None), ":MS#") == "0"
    assert ui_commands.get_nowait() == "push_object"
    assert guide_commands.get_nowait() == {
        "type": "goto_target",
        "ra": 187.5,
        "dec": -34.25,
    }


@pytest.mark.unit
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
        DummyConfig({"mount_control": True, "indi_goto_method": "off"}),
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


@pytest.mark.unit
def test_skysafari_ms_command_returns_error_without_target(monkeypatch):
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)

    assert pos_server.handle_slew_command(DummyState(None), ":MS#") == "1"


@pytest.mark.unit
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


@pytest.mark.unit
def test_distance_bars_show_initial_grace_after_slew_command(monkeypatch):
    monkeypatch.setattr(pos_server, "_skysafari_saw_mount_slew", False)
    monkeypatch.setattr(pos_server, "_mount_control_status", lambda: {})

    pos_server._mark_skysafari_slew_started()

    assert pos_server.get_distance_bars(None, ":D#") == "\x7f"


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
@pytest.mark.parametrize("status", ["AT1", "PT1", "GT1"])
def test_lx200_mount_status_response_is_not_hash_terminated(status):
    assert pos_server._format_lx200_response(status) == status.encode()


@pytest.mark.unit
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


@pytest.mark.unit
def test_skysafari_sync_queues_indi_sync_when_enabled(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(pos_server, "align_command_queue", None)
    monkeypatch.setattr(pos_server, "align_response_queue", None)
    monkeypatch.setattr(pos_server, "last_target_coordinates", (12.5, -34.25))
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)
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


@pytest.mark.unit
def test_skysafari_sync_queues_indi_sync_by_default(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(pos_server, "align_command_queue", None)
    monkeypatch.setattr(pos_server, "align_response_queue", None)
    monkeypatch.setattr(pos_server, "last_target_coordinates", (42.0, 15.5))
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig(
            {
                "mount_control": True,
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


@pytest.mark.unit
def test_skysafari_sync_skipped_when_sync_disabled(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(pos_server, "align_command_queue", None)
    monkeypatch.setattr(pos_server, "align_response_queue", None)
    monkeypatch.setattr(pos_server, "last_target_coordinates", (42.0, 15.5))
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig(
            {
                "mount_control": True,
                "skysafari_indi_sync": False,
                "skysafari_pifinder_align": False,
            }
        ),
    )

    assert pos_server.handle_sync_command(DummyState(None), ":CM#") == (
        "Coordinates matched."
    )

    with pytest.raises(queue.Empty):
        commands.get_nowait()


@pytest.mark.unit
def test_skysafari_sync_prefers_current_sr_sd_over_previous_goto(monkeypatch):
    commands = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(pos_server, "align_command_queue", None)
    monkeypatch.setattr(pos_server, "align_response_queue", None)
    monkeypatch.setattr(pos_server, "last_target_coordinates", (12.5, -34.25))
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


@pytest.mark.unit
def test_skysafari_sync_sets_imu_alignment_without_plate_solve(monkeypatch):
    dt = datetime.datetime(2026, 7, 1, 12, tzinfo=pytz.UTC)
    target_coordinates = (100.0, -20.0)
    location = DummyLocation()
    target_alt, target_az = pos_server._requested_coordinates_to_altaz(
        target_coordinates[0], target_coordinates[1], location, dt
    )
    raw_alt = target_alt - 1.25
    raw_az = (target_az - 12.5) % 360.0

    monkeypatch.setattr(pos_server, "last_target_coordinates", target_coordinates)
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)
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


@pytest.mark.unit
def test_solved_pointing_resets_imu_alignment_correction(monkeypatch):
    pos_server._imu_alignment_correction.update(
        {
            "active": True,
            "alt_offset": 1.0,
            "az_offset": 2.0,
            "set_at": 1.0,
            "target_coordinates": (100.0, -20.0),
        }
    )

    class FakeCoordinateState:
        solved = type("Solved", (), {"valid": True})()

        def radec(self):
            return (12.0, 34.0)

    monkeypatch.setattr(pos_server._coordinate_service, "get_state", lambda: None)
    monkeypatch.setattr(
        pos_server._coordinate_service,
        "update_state",
        lambda *_args, **_kwargs: FakeCoordinateState(),
    )

    pos_server._update_coordinate_service_state(DummyState(None))

    monkeypatch.setattr(
        pos_server._coordinate_service,
        "get_state",
        lambda: FakeCoordinateState(),
    )

    assert pos_server._current_pointing(DummyState(None)) == (12.0, 34.0)

    assert pos_server._imu_alignment_correction["active"] is False


@pytest.mark.unit
def test_skysafari_sync_returns_no_target_without_coordinates(monkeypatch):
    monkeypatch.setattr(pos_server, "last_target_coordinates", None)
    monkeypatch.setattr(pos_server, "sr_result", None)
    monkeypatch.setattr(pos_server, "sd_result", None)
    monkeypatch.setattr(
        pos_server,
        "pos_server_config",
        DummyConfig({"skysafari_pifinder_align": False}),
    )

    assert pos_server.handle_sync_command(DummyState(None), ":CM#") == "No target."


class _MutableLocation:
    def __init__(self, lat, lon, altitude=30.0, lock=True):
        self.lat = lat
        self.lon = lon
        self.altitude = altitude
        self.lock = lock


@pytest.fixture()
def location_resync(monkeypatch):
    """Mount location auto-resync with mount_control enabled and a fresh
    (unsynced) module state."""
    commands: queue.Queue = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(
        pos_server,
        "_get_config_option",
        lambda option, default=None: True if option == "mount_control" else default,
    )
    monkeypatch.setattr(pos_server, "_mount_synced_location", None)
    monkeypatch.setattr(pos_server, "_next_location_resync_at", 0.0)
    return commands


@pytest.mark.unit
def test_location_resync_first_lock_syncs_immediately(location_resync):
    state = DummyState(None, location=_MutableLocation(37.5, 127.0))
    pos_server._sync_mount_location_on_change(state)
    assert location_resync.get_nowait() == {"type": "sync_location_time"}
    assert location_resync.empty()


@pytest.mark.unit
def test_location_resync_ignores_gps_jitter(location_resync):
    state = DummyState(None, location=_MutableLocation(37.5, 127.0))
    pos_server._sync_mount_location_on_change(state)
    assert location_resync.get_nowait() == {"type": "sync_location_time"}

    # A few metres of jitter -- allow the recheck window, expect no re-sync.
    pos_server._next_location_resync_at = 0.0
    state._location = _MutableLocation(37.50001, 127.00001)
    pos_server._sync_mount_location_on_change(state)
    assert location_resync.empty()


@pytest.mark.unit
def test_location_resync_on_real_move(location_resync):
    state = DummyState(None, location=_MutableLocation(37.5, 127.0))
    pos_server._sync_mount_location_on_change(state)
    assert location_resync.get_nowait() == {"type": "sync_location_time"}

    # ~1.5 km move past the recheck window -> re-sync.
    pos_server._next_location_resync_at = 0.0
    state._location = _MutableLocation(37.51, 127.01)
    pos_server._sync_mount_location_on_change(state)
    assert location_resync.get_nowait() == {"type": "sync_location_time"}


@pytest.mark.unit
def test_location_resync_rate_limited_between_rechecks(location_resync):
    state = DummyState(None, location=_MutableLocation(37.5, 127.0))
    pos_server._sync_mount_location_on_change(state)
    assert location_resync.get_nowait() == {"type": "sync_location_time"}

    # Real move but still inside the recheck window -> held off.
    pos_server._next_location_resync_at = time.monotonic() + 999.0
    state._location = _MutableLocation(37.51, 127.01)
    pos_server._sync_mount_location_on_change(state)
    assert location_resync.empty()


@pytest.mark.unit
def test_location_resync_skips_unlocked_location(location_resync):
    state = DummyState(None, location=_MutableLocation(37.5, 127.0, lock=False))
    pos_server._sync_mount_location_on_change(state)
    assert location_resync.empty()


@pytest.mark.unit
def test_location_resync_skips_when_mount_control_disabled(monkeypatch):
    commands: queue.Queue = queue.Queue()
    monkeypatch.setattr(pos_server, "mountcontrol_queue", commands)
    monkeypatch.setattr(
        pos_server, "_get_config_option", lambda option, default=None: default
    )
    monkeypatch.setattr(pos_server, "_mount_synced_location", None)
    monkeypatch.setattr(pos_server, "_next_location_resync_at", 0.0)
    state = DummyState(None, location=_MutableLocation(37.5, 127.0))
    pos_server._sync_mount_location_on_change(state)
    assert commands.empty()
