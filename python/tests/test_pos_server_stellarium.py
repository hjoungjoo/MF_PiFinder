"""Unit tests for the LX200 position server.

`pos_server` talks to SkySafari and Stellarium over the same socket, and the two
clients disagree about framing, epoch and which commands must be answered.
Nearly all of that lives in pure functions, so it can be exercised without a
socket.  These tests pin the arithmetic and the parsing, and guard the handful
of places where a Stellarium accommodation could leak into a SkySafari session.
"""

import datetime
from unittest.mock import MagicMock

import pytest
import pytz

from PiFinder import pos_server


@pytest.fixture(autouse=True)
def reset_module_state(monkeypatch):
    """Never access the running mount, config or command queues in protocol tests."""
    pos_server._reset_lx200_session()
    monkeypatch.setattr(pos_server, "pos_server_config", None)
    monkeypatch.setattr(pos_server, "_mount_control_status", lambda: {})
    monkeypatch.setattr(pos_server, "mountcontrol_queue", None)
    monkeypatch.setattr(pos_server, "goto_guide_queue", None)
    monkeypatch.setattr(pos_server, "_stop_skysafari_guide_keepalive", lambda: False)
    yield
    pos_server._reset_lx200_session()


def make_shared_state(lat=42.36, lon=-71.06, tz="America/New_York", when=None):
    """A shared_state stub with just the accessors pos_server reads."""
    shared_state = MagicMock()
    shared_state.location.return_value = MagicMock(lat=lat, lon=lon, timezone=tz)
    if when is None:
        shared_state.local_datetime.return_value = None
    else:
        shared_state.local_datetime.return_value = when.astimezone(pytz.timezone(tz))
    return shared_state


# --------------------------------------------------------------------------
# Degrees to degrees/arcminutes
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, expected",
    [
        # The cases the string-surgery implementation got wrong: any fractional
        # part below 0.17 degrees came out ten times too small, and a fraction
        # that scaled past 59 produced an out-of-range "*60".
        (42.36, "+42*22"),
        (42.10, "+42*06"),
        (42.05, "+42*03"),
        (-71.06, "-71*04"),
        (42.00, "+42*00"),
        # Rounding must carry into the degrees field rather than emit "*60".
        (42.999, "+43*00"),
        (0.0, "+00*00"),
        (-0.004, "-00*00"),
        (89.5, "+89*30"),
        (-89.99, "-89*59"),  # 89 deg 59.4 min rounds down, not up to *60
        (-89.999, "-90*00"),  # ...but 89 deg 59.94 min does carry
    ],
)
def test_deg_to_dm_latitude(value, expected):
    assert pos_server._deg_to_dm(value, 2) == expected


@pytest.mark.unit
def test_deg_to_dm_pads_to_requested_width():
    assert pos_server._deg_to_dm(7.5, 2) == "+07*30"
    assert pos_server._deg_to_dm(7.5, 3) == "+007*30"


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, expected",
    [
        # Meade counts site longitude positive westward over 000-359, so a
        # western site loses its sign and an eastern one wraps.
        (-71.06, "071*04"),
        (13.4, "346*36"),
        (0.0, "000*00"),
        (-179.99, "179*59"),
        (179.99, "180*01"),
        # Just east of Greenwich must not round up into a nonexistent 360*00.
        (0.001, "000*00"),
    ],
)
def test_lon_to_meade_dm(value, expected):
    result = pos_server._lon_to_meade_dm(value)
    assert result == expected
    assert 0 <= int(result.split("*")[0]) < 360


# --------------------------------------------------------------------------
# Site echo
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_site_latitude_round_trips_what_the_client_set():
    assert pos_server.set_latitude(None, "#:St+42*21#") == "1"
    assert pos_server.get_latitude(make_shared_state(), None) == "+42*21"


@pytest.mark.unit
def test_site_latitude_survives_the_seconds_form():
    """The old input_str[4:10] slice silently truncated this to '+42*21'."""
    assert pos_server.set_latitude(None, "#:St+42*21:36#") == "1"
    assert pos_server.get_latitude(make_shared_state(), None) == "+42*21"


@pytest.mark.unit
def test_site_latitude_keeps_the_sign_without_the_prefix():
    """':St+42*21#' unprefixed used to lose its sign to the slice."""
    assert pos_server.set_latitude(None, ":St-42*21#") == "1"
    assert pos_server.get_latitude(make_shared_state(), None) == "-42*21"


@pytest.mark.unit
def test_site_longitude_round_trips_what_the_client_set():
    assert pos_server.set_longitude(None, "#:Sg071*04#") == "1"
    assert pos_server.get_longitude(make_shared_state(), None) == "071*04"


@pytest.mark.unit
def test_site_setters_reject_garbage():
    assert pos_server.set_latitude(None, "#:Stnonsense#") == "0"
    assert pos_server.stellarium_latitude == ""
    assert pos_server.set_longitude(None, "#:Sgnonsense#") == "0"
    assert pos_server.stellarium_longitude == ""


@pytest.mark.unit
def test_site_falls_back_to_the_gps_fix():
    shared_state = make_shared_state(lat=42.36, lon=-71.06)
    assert pos_server.get_latitude(shared_state, None) == "+42*22"
    assert pos_server.get_longitude(shared_state, None) == "071*04"


# --------------------------------------------------------------------------
# Date, time and UTC offset
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_date_and_time_are_local_and_locale_independent():
    """22:30 UTC on 15 March is still the 15th, 18:30, in New York."""
    when = datetime.datetime(2026, 3, 15, 22, 30, 15, tzinfo=pytz.utc)
    shared_state = make_shared_state(tz="America/New_York", when=when)

    assert pos_server.get_current_date(shared_state, None) == "03/15/26"
    assert pos_server.get_current_time(shared_state, None) == "18:30:15"


@pytest.mark.unit
def test_local_date_can_differ_from_the_utc_date():
    """01:30 UTC on the 16th is still the evening of the 15th in New York."""
    when = datetime.datetime(2026, 3, 16, 1, 30, 0, tzinfo=pytz.utc)
    shared_state = make_shared_state(tz="America/New_York", when=when)

    assert pos_server.get_current_date(shared_state, None) == "03/15/26"
    assert pos_server.get_current_time(shared_state, None) == "21:30:00"


@pytest.mark.unit
@pytest.mark.parametrize(
    "tz, expected",
    [
        # LX200 wants the hours to ADD to local time to reach UTC, so the sign
        # is inverted relative to the zone's own offset.
        ("America/New_York", "+04"),  # UTC-4 in June
        ("Europe/Berlin", "-02"),  # UTC+2 in June
        ("UTC", "+00"),
        ("Asia/Kolkata", "-05.5"),  # UTC+5:30 uses the sHH.H form
    ],
)
def test_utc_offset_sign_and_shape(tz, expected):
    when = datetime.datetime(2026, 6, 21, 12, 0, 0, tzinfo=pytz.utc)
    shared_state = make_shared_state(tz=tz, when=when)
    assert pos_server.get_utc_offset(shared_state, None) == expected


@pytest.mark.unit
def test_clock_commands_are_silent_before_a_time_fix():
    """No GPS lock yet: answer nothing rather than crash the server process."""
    shared_state = make_shared_state(when=None)
    assert pos_server.get_current_date(shared_state, None) is None
    assert pos_server.get_current_time(shared_state, None) is None
    assert pos_server.get_utc_offset(shared_state, None) is None
    assert pos_server.handle_frame(":GL#", shared_state) is None


class ScriptedSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks) + [b""]
        self.sent = []
        self.closed = False

    def settimeout(self, timeout):
        pass

    def setsockopt(self, *args):
        pass

    def recv(self, size):
        return self.chunks.pop(0)

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


PUSH_WIRE = b"#\x06#:Sr21:33:27##:Sd-00:49:24##:MS#"


@pytest.mark.unit
@pytest.mark.parametrize("cut", range(len(PUSH_WIRE) + 1))
def test_stellarium_goto_survives_every_tcp_split_once(monkeypatch, cut):
    calls = []
    monkeypatch.setattr(
        pos_server,
        "handle_goto_command",
        lambda state, ra, dec: calls.append((ra, dec)) or "1",
    )
    client = ScriptedSocket([c for c in (PUSH_WIRE[:cut], PUSH_WIRE[cut:]) if c])
    pos_server.handle_client(client, make_shared_state())
    assert client.sent == [b"P", b"1", b"1", b"0"]
    assert calls == [((21, 33, 27), (-1, 0, 49, 24))]
    assert client.closed
    assert not pos_server.is_stellarium
    assert pos_server.sr_result is pos_server.sd_result is None


@pytest.mark.unit
@pytest.mark.parametrize("prefix", ("", "#"))
@pytest.mark.parametrize("separator", ("*", ":"))
def test_coordinates_only_stage_until_ms_never_move_on_align_input(
    monkeypatch, prefix, separator
):
    goto = MagicMock(return_value="1")
    monkeypatch.setattr(pos_server, "handle_goto_command", goto)
    pos_server.handle_frame("#\x06", None)
    assert pos_server.handle_frame(f"{prefix}:Sr05:34:32#", None) == "1"
    assert pos_server.handle_frame(f"{prefix}:Sd-00{separator}30:00#", None) == "1"
    goto.assert_not_called()
    assert pos_server.handle_frame(":MS#", None) == "0"
    goto.assert_called_once_with(None, (5, 34, 32), (-1, 0, 30, 0))


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    (
        ":Sr24:00:00#",
        ":Sr05:60:00#",
        ":Sr05:00:60#",
        ":Srbad#",
        ":Sd+91*00:00#",
        ":Sd+90*00:01#",
        ":Sd+20*60:00#",
        ":Sd+20*00:60#",
        ":Sdbad#",
    ),
)
def test_invalid_coordinate_cannot_reuse_previous_goto_target(monkeypatch, bad):
    goto = MagicMock()
    monkeypatch.setattr(pos_server, "handle_goto_command", goto)
    pos_server.parse_sr_command(None, ":Sr05:34:32#")
    pos_server.parse_sd_command(None, ":Sd+20*00:00#")
    assert pos_server.handle_frame(bad, None) == "0"
    assert pos_server.handle_frame(":MS#", None) == "1"
    goto.assert_not_called()


@pytest.mark.unit
def test_new_ra_requires_a_new_declination(monkeypatch):
    goto = MagicMock()
    monkeypatch.setattr(pos_server, "handle_goto_command", goto)
    pos_server.parse_sr_command(None, ":Sr05:34:32#")
    pos_server.parse_sd_command(None, ":Sd+20*00:00#")
    pos_server.parse_sr_command(None, ":Sr06:34:32#")
    assert pos_server.handle_frame(":MS#", None) == "1"
    goto.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("ack", (False, True))
def test_abort_keeps_real_mf_stop_and_gates_only_reply(monkeypatch, ack):
    stop = MagicMock()
    monkeypatch.setattr(pos_server, "handle_guide_stop", stop)
    pos_server.is_stellarium = ack
    assert pos_server.handle_frame(":Q#", None) == ("1" if ack else None)
    stop.assert_called_once_with(None, ":Q#")


@pytest.mark.unit
@pytest.mark.parametrize("ack", (False, True))
def test_distance_bars_keep_real_mount_movement_for_both_apps(monkeypatch, ack):
    pos_server.is_stellarium = ack
    monkeypatch.setattr(
        pos_server, "_mount_control_status", lambda: {"state": "slewing"}
    )
    assert pos_server.handle_frame(":D#", None) == "\x7f#"
    monkeypatch.setattr(pos_server, "_mount_control_status", lambda: {"state": "idle"})
    assert pos_server.handle_frame(":D#", None) == "#"


@pytest.mark.unit
def test_status_keeps_mf_mount_geometry_override(monkeypatch):
    pos_server.is_stellarium = True
    monkeypatch.setattr(pos_server, "_skysafari_lx200_mount_code", lambda: "G")
    assert pos_server.handle_frame(":GW#", None) == "GT1"


@pytest.mark.unit
def test_complete_handshake_does_not_set_gps_clock_or_mount_site():
    state = make_shared_state(
        lat=37.5,
        lon=127.0,
        tz="Asia/Seoul",
        when=datetime.datetime(2026, 9, 6, tzinfo=pytz.UTC),
    )
    client = ScriptedSocket(
        [
            b"#\x06#:St-42*21##:Sg071*04##:Gt##:Gg#"
            b"#:SG-09##:SL03:04:05##:SC09/06/26##:GC##:GL##:GG#"
        ]
    )
    pos_server.handle_client(client, state)
    assert client.sent == [
        b"P",
        b"1",
        b"1",
        b"-42*21#",
        b"071*04#",
        b"1",
        b"1",
        b"1Updating Planetary Data#                         #",
        b"09/06/26#",
        b"09:00:00#",
        b"-09#",
    ]
    assert state.location().lat == 37.5
    assert state.location().lon == 127.0
    state.set_location.assert_not_called()
    state.set_datetime.assert_not_called()
    assert not pos_server.stellarium_latitude
    assert not pos_server.stellarium_longitude


@pytest.mark.unit
def test_next_connection_cannot_inherit_echoes_or_pending_target(monkeypatch):
    goto = MagicMock()
    monkeypatch.setattr(pos_server, "handle_goto_command", goto)
    state = make_shared_state(lat=37.5, lon=127.0)
    first = ScriptedSocket([b"#\x06#:St-42*21##:Sg071*04##:Sr05:34:32##:Sd+20*00:00#"])
    pos_server.handle_client(first, state)
    second = ScriptedSocket([b":Gt#:Gg#:MS#:CM#:Q#"])
    pos_server.handle_client(second, state)
    assert second.sent == [b"+37*30#", b"233*00#", b"1", b"No target.#"]
    goto.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "ra_wire, dec_wire, ra, dec",
    [
        (b":Sr19:52:05#", b":Sd+08*56:10#", (19, 52, 5), (1, 8, 56, 10)),
        (b":Sr21:33:27#", b":Sd-00*49:24#", (21, 33, 27), (-1, 0, 49, 24)),
    ],
)
def test_skysafari_goto_uses_separate_connections(
    monkeypatch, ra_wire, dec_wire, ra, dec
):
    """Field capture: Sr and Sd arrive from different TCP source ports."""
    goto = MagicMock(return_value="1")
    monkeypatch.setattr(pos_server, "handle_goto_command", goto)
    monkeypatch.setitem(pos_server.lx_command_dict, "GR", lambda *_: "19:52:05")
    monkeypatch.setitem(pos_server.lx_command_dict, "GD", lambda *_: "+08*56:10")
    state = make_shared_state()
    for wire, expected in [
        (ra_wire, b"1"),
        (b":GR#", b"19:52:05#"),
        (dec_wire, b"1"),
        (b":GD#", b"+08*56:10#"),
    ]:
        client = ScriptedSocket([wire])
        pos_server.handle_client(client, state, "192.0.2.1")
        assert client.sent == [expected]
        assert client.closed
        goto.assert_not_called()
    client = ScriptedSocket([b":MS#"])
    pos_server.handle_client(client, state, "192.0.2.1")
    assert client.sent == [b"0"]
    goto.assert_called_once_with(state, ra, dec)


@pytest.mark.unit
def test_skysafari_sync_uses_separate_connections(monkeypatch):
    sync = MagicMock(return_value=True)
    monkeypatch.setattr(pos_server, "_queue_indi_sync_if_enabled", sync)
    monkeypatch.setattr(pos_server, "_has_solved_pointing", lambda _: True)
    monkeypatch.setattr(pos_server, "_reset_imu_alignment_correction", lambda _: None)
    monkeypatch.setattr(pos_server, "_get_config_option", lambda *_: "indi_mount")
    state = make_shared_state()
    for wire, expected in [
        (b":Sr19:52:05#", b"1"),
        (b":Sd+08*56:10#", b"1"),
        (b":CM#", b"Coordinates matched.#"),
    ]:
        client = ScriptedSocket([wire])
        pos_server.handle_client(client, state, "192.0.2.1")
        assert client.sent == [expected]
    sync.assert_called_once()
    assert sync.call_args.args == pytest.approx((298.0208333333, 8.9361111111))


@pytest.mark.unit
@pytest.mark.parametrize("different_host", (False, True))
def test_target_not_inherited_after_host_change_or_idle_gap(
    monkeypatch, different_host
):
    goto = MagicMock()
    monkeypatch.setattr(pos_server, "handle_goto_command", goto)
    monkeypatch.setattr(pos_server.time, "monotonic", lambda: 100.0)
    state = make_shared_state()
    first = ScriptedSocket([b":Sr19:52:05#:Sd+08*56:10#"])
    pos_server.handle_client(first, state, "192.0.2.1")
    if not different_host:
        monkeypatch.setattr(pos_server.time, "monotonic", lambda: 161.0)
    second = ScriptedSocket([b":MS#:CM#"])
    pos_server.handle_client(
        second, state, "192.0.2.2" if different_host else "192.0.2.1"
    )
    assert second.sent == [b"1", b"No target.#"]
    goto.assert_not_called()


@pytest.mark.unit
def test_send_failure_still_closes_socket_and_clears_session():
    client = ScriptedSocket([b"#\x06"])
    client.sendall = MagicMock(side_effect=BrokenPipeError)
    pos_server.handle_client(client, make_shared_state())
    assert client.closed
    assert not pos_server.is_stellarium


@pytest.mark.unit
def test_unterminated_command_is_bounded_and_never_dispatched(monkeypatch):
    goto = MagicMock()
    monkeypatch.setattr(pos_server, "handle_goto_command", goto)
    client = ScriptedSocket([b":Sr" + b"1" * 1021] + [b"1" * 1024] * 4)
    pos_server.handle_client(client, make_shared_state())
    assert client.closed
    assert client.sent == []
    goto.assert_not_called()
