import json
import time
from multiprocessing import Queue
from types import SimpleNamespace

from PiFinder import sys_utils
from PiFinder import mountcontrol_indi as mci
from PiFinder.mountcontrol_indi import (
    MountControlIndi,
    radec_separation_arcmin,
    shortest_ra_delta_deg,
)


class DummyMountControl(MountControlIndi):
    def __init__(self, shared_state=None):
        super().__init__(Queue(), Queue(), shared_state)
        self.applied_properties = []
        self.sync_calls = []
        self.goto_calls = []

    def _apply_indi_properties(
        self,
        properties,
        success_state,
        success_message,
        failure_state,
    ):
        self.applied_properties.append(list(properties))
        return {"ok": True}

    def _apply_indi_backlash(self, backlash_ra, backlash_de):
        self.applied_properties.append(
            [
                f"LX200 OnStepX.Backlash.Backlash RA={backlash_ra}",
                f"LX200 OnStepX.Backlash.Backlash DEC={backlash_de}",
            ]
        )
        return {"ok": True}

    def sync_mount(self, ra_deg, dec_deg):
        self.sync_calls.append((ra_deg, dec_deg))
        return True

    def goto_target(
        self,
        ra_deg,
        dec_deg,
        refine_after_goto=False,
        refine_accuracy_arcmin=None,
    ):
        self.goto_calls.append(
            (ra_deg, dec_deg, refine_after_goto, refine_accuracy_arcmin)
        )
        return True


class DummyCommandQueue:
    def __init__(self):
        self.commands = []

    def put(self, command):
        self.commands.append(command)


class DummyPointing:
    def __init__(self, ra, dec):
        self.RA = ra
        self.Dec = dec


class DummyAligned:
    def __init__(self, ra, dec):
        self.solve = DummyPointing(ra, dec)


class DummyPointingMatrix:
    def __init__(self, ra, dec):
        self.aligned = DummyAligned(ra, dec)


class DummySolution:
    def __init__(self, ra, dec, solve_time):
        self.pointing = DummyPointingMatrix(ra, dec)
        self.last_solve_success = solve_time


class DummySharedState:
    def __init__(self, solution):
        self._solution = solution

    def solution(self):
        return self._solution


class DummyLocation:
    lock = True
    lat = 37.52704
    lon = 127.10936
    altitude = 30


class DummySharedStateWithLocation:
    def location(self):
        return DummyLocation()

    def datetime(self):
        return "2026-07-01T14:45:00+00:00"


class DummyConnectedMount(MountControlIndi):
    def __init__(self):
        super().__init__(Queue(), Queue(), None)
        self.client = DummyIndiClient()
        self.device = DummyIndiDevice()
        self.connected = True
        self.statuses = []

    def _write_controller_status(self, state, message="", **extra):
        self.statuses.append((state, message, extra))


class DummyIndiDevice:
    def getDeviceName(self):
        return "LX200 OnStep"


class DummyIndiClient:
    def __init__(self):
        self.switches = []
        self.numbers = []

    def isServerConnected(self):
        return True

    def set_switch(self, device, property_name, element_name):
        self.switches.append((device.getDeviceName(), property_name, element_name))
        return True

    def set_number(self, device, property_name, values):
        self.numbers.append((device.getDeviceName(), property_name, dict(values)))
        return True


def test_manual_motion_deadman_sends_stop_after_expired_lease():
    mount = DummyMountControl()

    assert mount.manual_move("north", lease_seconds=0.3)
    mount._manual_motion_deadline = time.monotonic() - 0.01
    mount._check_manual_motion_deadline()

    assert mount._manual_motion_direction is None
    assert any(
        "TELESCOPE_ABORT_MOTION.ABORT=On" in prop
        for prop in mount.applied_properties[-1]
    )


def test_manual_motion_keepalive_extends_matching_motion():
    mount = DummyMountControl()

    assert mount.manual_move("east", lease_seconds=0.3)
    original_deadline = mount._manual_motion_deadline
    assert mount.manual_motion_keepalive("east", lease_seconds=1.2)

    assert mount._manual_motion_deadline is not None
    assert original_deadline is not None
    assert mount._manual_motion_deadline > original_deadline


def test_manual_motion_keepalive_ignores_other_direction():
    mount = DummyMountControl()

    assert mount.manual_move("east", lease_seconds=0.3)
    original_deadline = mount._manual_motion_deadline
    assert not mount.manual_motion_keepalive("west", lease_seconds=1.2)

    assert mount._manual_motion_deadline == original_deadline


def test_manual_motion_east_west_are_reversed_for_onstep_guide_axis():
    mount = DummyMountControl()

    assert mount.manual_move("east", lease_seconds=0.3)
    assert any(
        "TELESCOPE_MOTION_WE.MOTION_WEST=On" in prop
        for prop in mount.applied_properties[-1]
    )

    assert mount.manual_move("west", lease_seconds=0.3)
    assert any(
        "TELESCOPE_MOTION_WE.MOTION_EAST=On" in prop
        for prop in mount.applied_properties[-1]
    )


def test_set_backlash_sends_indi_backlash_properties():
    mount = DummyMountControl()

    assert mount.set_backlash(12, 34)

    assert any(
        "Backlash.Backlash RA=12" in prop for prop in mount.applied_properties[-1]
    )
    assert any(
        "Backlash.Backlash DEC=34" in prop for prop in mount.applied_properties[-1]
    )
    assert mount.backlash_ra == 12
    assert mount.backlash_de == 34


def test_auto_backlash_enables_compass_when_needed(monkeypatch):
    options = {}

    class FakeConfig:
        def get_option(self, name, default=None):
            return options.get(name, default)

        def set_option(self, name, value):
            options[name] = value

    monkeypatch.setattr(mci.config, "Config", FakeConfig)
    mount = DummyMountControl(shared_state=object())

    assert not mount.auto_calculate_backlash()

    assert options["imu_use_magnetometer"] is True
    assert mount._backlash_auto is not None
    assert mount._backlash_auto["auto_mode"] == mci.BACKLASH_AUTO_MODE_COMPASS_GOTO
    assert mount._backlash_auto["state"] == "restart_required"


def test_auto_backlash_uses_compass_goto_loop(monkeypatch):
    class FakeConfig:
        def get_option(self, name, default=None):
            return True if name == "imu_use_magnetometer" else default

        def set_option(self, name, value):
            raise AssertionError("Compass setting should already be enabled")

    monkeypatch.setattr(mci.config, "Config", FakeConfig)
    mount = DummyMountControl(shared_state=object())
    mount._current_imu_sample = lambda: SimpleNamespace(
        uses_magnetometer=True,
        fusion_mode="ndof",
        calibration_status=(3, 3, 1, 3),
    )
    tracking_writes = []
    mount.connect = lambda: True
    mount.stop_mount = lambda: True
    mount._read_tracking_enabled = lambda: True
    mount.set_tracking = lambda enabled: tracking_writes.append(enabled) or True

    assert mount.auto_calculate_backlash(mode="compass_goto_loop")

    assert mount._backlash_auto is not None
    assert mount._backlash_auto["axis"] == "Mount frame"
    assert mount._backlash_auto["auto_mode"] == mci.BACKLASH_AUTO_MODE_COMPASS_GOTO
    assert mount._backlash_auto["state"] == "waiting_for_calibration"
    assert mount._backlash_auto["repeats"] == mci.BACKLASH_COMPASS_GOTO_REPEATS
    assert mount._backlash_auto["guide_rate"] == mci.BACKLASH_COMPASS_GUIDE_RATE
    assert mount._backlash_auto["guide_rate_label"] == "Half-Max (est. 96x)"
    assert mount._backlash_auto["imu_status"]["heading_ready"] is True
    assert mount._backlash_auto["original_tracking"] is True
    assert tracking_writes == [False]


def test_auto_backlash_accepts_repeat_count():
    mount = DummyMountControl(shared_state=object())
    calls = {}

    def fake_start(repeats=mci.BACKLASH_COMPASS_GOTO_REPEATS, offset_deg=2.0):
        calls["repeats"] = repeats
        calls["offset_deg"] = offset_deg
        return True

    mount.start_backlash_compass_goto_loop = fake_start

    assert mount.auto_calculate_backlash(mode="compass_goto_loop", repeats="7")

    assert calls["repeats"] == 7


def test_backlash_stop_request_aborts_motion_test(monkeypatch, tmp_path):
    stop_request = tmp_path / "mount_control_stop_request.json"
    stop_request.write_text(json.dumps({"requested_at": 1234.0}), encoding="utf-8")
    monkeypatch.setattr(mci, "STOP_REQUEST_FILE", stop_request)
    mount = DummyMountControl(shared_state=object())
    mount._backlash_auto = {
        "auto_mode": mci.BACKLASH_AUTO_MODE_COMPASS_GOTO,
        "state": "running",
    }
    stop_calls = []
    tracking_writes = []
    mount.stop_mount = lambda: stop_calls.append(True) or True
    mount.set_tracking = lambda enabled: tracking_writes.append(enabled) or True

    assert mount._abort_backlash_if_requested("test phase")

    assert stop_calls == [True]
    assert tracking_writes == [False]
    assert mount._backlash_auto["state"] == "stopped"
    assert "test phase" in mount._backlash_auto["message"]
    assert not stop_request.exists()


def test_backlash_goto_wait_requires_target_reached(monkeypatch):
    mount = DummyMountControl(shared_state=object())
    clock = [0.0]
    stop_calls = []

    def fake_monotonic():
        clock[0] += 1.0
        return clock[0]

    monkeypatch.setattr(mci.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(mci.time, "sleep", lambda seconds: None)
    mount._backlash_auto = {}
    mount._indi_mount_is_busy = lambda: False
    mount._read_current_position = lambda: (0.0, 0.0)
    mount.stop_mount = lambda: stop_calls.append(True) or True

    assert not mount._goto_target_and_wait(30.0, 30.0, "blocked", timeout=2.0)

    assert stop_calls == [True]
    assert mount._backlash_auto["state"] == "failed"
    assert "target was not reached" in mount._backlash_auto["message"]


def test_compass_goto_loop_records_initial_offset_and_repeats():
    mount = DummyMountControl(shared_state=object())
    mount._backlash_auto = {
        "auto_mode": mci.BACKLASH_AUTO_MODE_COMPASS_GOTO,
        "repeats": 2,
        "offset_deg": 3.0,
    }
    current_position = [10.0, 20.0]
    pulse_calls = []
    guide_rates = []

    mount.connect = lambda: True
    mount._current_imu_sample = lambda: SimpleNamespace(
        uses_magnetometer=True,
        fusion_mode="ndof",
        calibration_status=(3, 3, 1, 3),
        is_calibrated=lambda: True,
    )
    mount._current_imu_altaz = lambda: (current_position[1], current_position[0])
    mount._imu_altaz_to_radec = lambda alt, az: (az, alt)
    mount._home_park_status_fields = lambda: {"park_state": "Unparked"}
    mount._read_tracking_enabled = lambda: False
    mount._read_current_position = lambda: (current_position[0], current_position[1])
    mount._backlash_mount_model = lambda: "eq"
    mount.set_tracking = lambda enabled: True
    mount._read_driver_guide_rate = lambda: (0.5, 0.5)
    mount.set_guide_rate = lambda rate: guide_rates.append(rate) or True
    mount.sync_mount = lambda ra, dec: current_position.__setitem__(slice(None), [ra % 360.0, dec]) or True
    mount.stop_mount = lambda: True

    def fake_pulse(active_axis, positive, label, offset_deg):
        pulse_calls.append((active_axis, positive, label, round(offset_deg, 3)))
        signed_offset = offset_deg if positive else -offset_deg
        if active_axis == "ra":
            current_position[0] = (current_position[0] + signed_offset) % 360.0
        elif active_axis == "dec":
            current_position[1] += signed_offset
        else:
            raise AssertionError(f"Unexpected test axis {active_axis}")
        return True

    mount._backlash_pulse_axis_and_wait = fake_pulse

    assert mount.continue_backlash_compass_goto_loop()

    records = mount._backlash_auto["coordinate_records"]
    assert [record["label"] for record in records] == [
        "initial RA",
        "offset initial RA",
        "return 1 RA",
        "offset 1 RA",
        "return 2 RA",
        "offset 2 RA",
        "initial DEC",
        "offset initial DEC",
        "return 1 DEC",
        "offset 1 DEC",
        "return 2 DEC",
        "offset 2 DEC",
    ]
    assert pulse_calls == [
        ("ra", False, "init RA", 3.0),
        ("ra", True, "offset initial RA", 3.0),
        ("ra", False, "return 1 RA", 3.0),
        ("ra", True, "offset 1 RA", 3.0),
        ("ra", False, "return 2 RA", 3.0),
        ("ra", True, "offset 2 RA", 3.0),
        ("dec", False, "init DEC", 3.0),
        ("dec", True, "offset initial DEC", 3.0),
        ("dec", False, "return 1 DEC", 3.0),
        ("dec", True, "offset 1 DEC", 3.0),
        ("dec", False, "return 2 DEC", 3.0),
        ("dec", True, "offset 2 DEC", 3.0),
    ]
    assert records[0]["mount_ra"] == 7.0
    assert records[0]["active_axis"] == "ra"
    assert records[6]["active_axis"] == "dec"
    assert records[-1]["mount_ra"] == 10.0
    assert records[-1]["mount_dec"] == 20.0
    assert mount.applied_properties == []
    assert guide_rates == [mci.BACKLASH_COMPASS_GUIDE_RATE, (0.5, 0.5)]
    assert records[0]["imu_status"]["heading_ready"] is True
    assert records[0]["imu_status"]["fully_calibrated"] is False
    analysis = mount._backlash_auto["directional_analysis"]
    assert len(analysis["legs"]) == 10
    assert analysis["direction_stats"]["offset"]["sample_count"] == 4
    assert analysis["direction_stats"]["return"]["sample_count"] == 4
    axis_direction_stats = analysis["axis_direction_stats"]
    assert axis_direction_stats["ra_positive"]["display_label"] == "RA+"
    assert axis_direction_stats["ra_positive"]["sample_count"] == 2
    assert axis_direction_stats["ra_negative"]["display_label"] == "RA-"
    assert axis_direction_stats["ra_negative"]["sample_count"] == 2
    assert axis_direction_stats["dec_positive"]["display_label"] == "DEC+"
    assert axis_direction_stats["dec_positive"]["sample_count"] == 2
    assert axis_direction_stats["dec_negative"]["display_label"] == "DEC-"
    assert axis_direction_stats["dec_negative"]["sample_count"] == 2
    assert mount._backlash_auto["state"] == "complete"


def test_backlash_pulse_guide_uses_timed_guide_properties(monkeypatch):
    mount = DummyMountControl(shared_state=object())
    monkeypatch.setattr(mount, "_backlash_cancelable_sleep", lambda seconds, label: True)
    stop_calls = []
    mount.stop_mount = lambda: stop_calls.append(True) or True

    assert mount._pulse_guide_move_and_wait("east", 1.234, "pulse test")

    assert mount.applied_properties[-1] == [
        "LX200 OnStepX.TELESCOPE_TIMED_GUIDE_WE.TIMED_GUIDE_W=1234"
    ]
    assert stop_calls == [True]


def test_backlash_pulse_duration_uses_default_half_max_rate():
    mount = DummyMountControl(shared_state=object())

    duration = mount._backlash_pulse_duration_seconds(2.0)

    assert round(duration, 2) == 4.99


def test_compass_goto_loop_plan_uses_altaz_offset_for_altaz_mount():
    mount = DummyMountControl(shared_state=object())
    mount._backlash_mount_model = lambda: "altaz"
    mount._radec_to_altaz = lambda ra, dec: (40.0, 50.0)

    def fake_altaz_to_radec(alt, az):
        return (az + 100.0, alt - 10.0)

    mount._altaz_to_radec = fake_altaz_to_radec

    plan = mount._compass_goto_loop_plan(10.0, 20.0, 3.0, active_axis="az")

    assert plan is not None
    assert plan["movement_frame"] == "altaz"
    assert plan["active_axis"] == "az"
    assert plan["start_altitude"] == 40.0
    assert plan["start_azimuth"] == 50.0
    assert plan["target_altitude"] == 40.0
    assert plan["target_azimuth"] == 53.0

    start_command = mount._compass_goto_loop_command(plan, "start")
    target_command = mount._compass_goto_loop_command(plan, "target")

    assert start_command["target_ra"] == 150.0
    assert start_command["target_dec"] == 30.0
    assert target_command["target_ra"] == 153.0
    assert target_command["target_dec"] == 30.0
    assert target_command["target_altitude"] == 40.0
    assert target_command["target_azimuth"] == 53.0

    alt_plan = mount._compass_goto_loop_plan(10.0, 20.0, 3.0, active_axis="alt")
    assert alt_plan is not None
    assert alt_plan["active_axis"] == "alt"
    assert alt_plan["target_altitude"] == 43.0
    assert alt_plan["target_azimuth"] == 50.0
    alt_target_command = mount._compass_goto_loop_command(alt_plan, "target")
    assert alt_target_command["target_ra"] == 150.0
    assert alt_target_command["target_dec"] == 33.0


def test_compass_goto_loop_analysis_uses_actual_pre_goto_mount_start():
    mount = DummyMountControl(shared_state=object())
    ready_imu = {"heading_ready": True}
    records = [
        {
            "sequence": 1,
            "label": "initial",
            "mount_ra": 100.0,
            "mount_dec": 10.0,
            "mount_altitude": 10.1,
            "mount_azimuth": 20.2,
            "imu_ra": 101.0,
            "imu_dec": 11.0,
            "imu_altitude": 11.0,
            "imu_azimuth": 21.0,
            "imu_status": ready_imu,
        },
        {
            "sequence": 2,
            "label": "offset initial",
            "mount_ra": 103.0,
            "mount_dec": 12.9,
            "mount_altitude": 12.9,
            "mount_azimuth": 23.1,
            "imu_ra": 103.1,
            "imu_dec": 13.1,
            "imu_altitude": 13.1,
            "imu_azimuth": 24.9,
            "command_start_ra": 100.0,
            "command_start_dec": 10.0,
            "command_start_altitude": 10.0,
            "command_start_azimuth": 20.0,
            "target_ra": 103.0,
            "target_dec": 13.0,
            "target_altitude": 13.0,
            "target_azimuth": 23.0,
            "movement_frame": "altaz",
            "imu_status": ready_imu,
        },
    ]

    analysis = mount._compass_goto_loop_directional_analysis(records)
    leg = analysis["legs"][0]

    assert leg["command_start_altitude"] == 10.0
    assert leg["command_start_azimuth"] == 20.0
    assert round(leg["mount_start_altitude"], 1) == 10.1
    assert round(leg["mount_start_azimuth"], 1) == 20.2
    assert round(leg["mount_delta_altitude"], 1) == 2.8
    assert round(leg["mount_delta_azimuth"], 1) == 2.9
    assert round(leg["imu_delta_alt"], 1) == 2.1
    assert round(leg["imu_delta_az"], 1) == 3.9
    assert leg["motion_difference_alt_arcsec"] == 2520
    assert leg["motion_difference_az_arcsec"] == -3600
    assert leg["motion_backlash_alt_arcsec"] == 2520
    assert leg["motion_backlash_az_arcsec"] == 3600


def test_compass_goto_loop_analysis_groups_altaz_values_by_axis_direction():
    mount = DummyMountControl(shared_state=object())
    ready_imu = {"heading_ready": True}
    records = [
        {
            "sequence": 1,
            "label": "initial",
            "mount_ra": 100.0,
            "mount_dec": 10.0,
            "mount_altitude": 10.0,
            "mount_azimuth": 20.0,
            "imu_ra": 100.0,
            "imu_dec": 10.0,
            "imu_altitude": 10.0,
            "imu_azimuth": 20.0,
            "imu_status": ready_imu,
        },
        {
            "sequence": 2,
            "label": "offset 1",
            "mount_ra": 103.0,
            "mount_dec": 13.0,
            "mount_altitude": 13.0,
            "mount_azimuth": 23.0,
            "imu_ra": 102.9,
            "imu_dec": 12.9,
            "imu_altitude": 12.9,
            "imu_azimuth": 22.8,
            "target_altitude": 13.0,
            "target_azimuth": 23.0,
            "movement_frame": "altaz",
            "imu_status": ready_imu,
        },
        {
            "sequence": 3,
            "label": "return 1",
            "mount_ra": 100.0,
            "mount_dec": 10.0,
            "mount_altitude": 10.0,
            "mount_azimuth": 20.0,
            "imu_ra": 100.2,
            "imu_dec": 10.2,
            "imu_altitude": 10.2,
            "imu_azimuth": 20.4,
            "target_altitude": 10.0,
            "target_azimuth": 20.0,
            "movement_frame": "altaz",
            "imu_status": ready_imu,
        },
    ]

    analysis = mount._compass_goto_loop_directional_analysis(records)
    axis_direction_stats = analysis["axis_direction_stats"]

    assert axis_direction_stats["alt_positive"]["display_label"] == "ALT+"
    assert axis_direction_stats["alt_positive"]["sample_count"] == 1
    assert axis_direction_stats["az_positive"]["display_label"] == "AZ+"
    assert axis_direction_stats["az_positive"]["sample_count"] == 1
    assert axis_direction_stats["alt_negative"]["display_label"] == "ALT-"
    assert axis_direction_stats["alt_negative"]["sample_count"] == 1
    assert axis_direction_stats["az_negative"]["display_label"] == "AZ-"
    assert axis_direction_stats["az_negative"]["sample_count"] == 1


def test_compass_backlash_filter_uses_middle_40_percent_mean():
    mount = DummyMountControl(shared_state=object())

    stats = mount._filter_compass_backlash_values(
        [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    )

    assert stats["values"] == [40, 50, 60, 70]
    assert stats["excluded_values"] == [10, 20, 30, 80, 90, 100]
    assert stats["trim_low_count"] == 3
    assert stats["trim_high_count"] == 3
    assert stats["trimmed_mean"] == 55.0


def test_compass_goto_loop_restores_tracking_after_test():
    mount = DummyMountControl(shared_state=object())
    mount._backlash_auto = {
        "auto_mode": mci.BACKLASH_AUTO_MODE_COMPASS_GOTO,
        "repeats": 1,
        "offset_deg": 3.0,
    }
    current_position = [10.0, 20.0]
    tracking_writes = []
    guide_rates = []

    mount._current_imu_sample = lambda: SimpleNamespace(
        uses_magnetometer=True,
        fusion_mode="ndof",
        calibration_status=(3, 3, 1, 3),
        is_calibrated=lambda: True,
    )
    mount.connect = lambda: True
    mount._current_imu_altaz = lambda: (current_position[1], current_position[0])
    mount._imu_altaz_to_radec = lambda alt, az: (az, alt)
    mount._home_park_status_fields = lambda: {"park_state": "Unparked"}
    mount._read_tracking_enabled = lambda: True
    mount.set_tracking = lambda enabled: tracking_writes.append(enabled) or True
    mount._read_driver_guide_rate = lambda: (0.5, 0.5)
    mount.set_guide_rate = lambda rate: guide_rates.append(rate) or True
    mount._read_current_position = lambda: (current_position[0], current_position[1])
    mount._backlash_mount_model = lambda: "eq"
    mount.sync_mount = lambda ra, dec: current_position.__setitem__(slice(None), [ra % 360.0, dec]) or True
    mount.stop_mount = lambda: True

    def fake_pulse(active_axis, positive, label, offset_deg):
        signed_offset = offset_deg if positive else -offset_deg
        if active_axis == "ra":
            current_position[0] = (current_position[0] + signed_offset) % 360.0
        elif active_axis == "dec":
            current_position[1] += signed_offset
        return True

    mount._backlash_pulse_axis_and_wait = fake_pulse

    assert mount.continue_backlash_compass_goto_loop()

    assert tracking_writes == [False, False, True]
    assert guide_rates == [mci.BACKLASH_COMPASS_GUIDE_RATE, (0.5, 0.5)]
    assert mount._backlash_auto["state"] == "complete"


def test_read_tracking_enabled_retries_after_empty_property_snapshot(monkeypatch):
    mount = DummyMountControl()
    snapshots = [
        {},
        {"LX200 OnStepX.TELESCOPE_TRACK_STATE.TRACK_OFF": "On"},
    ]

    def fake_get_properties(**kwargs):
        return snapshots.pop(0) if snapshots else {}

    monkeypatch.setattr(sys_utils, "get_indi_onstep_properties", fake_get_properties)
    monkeypatch.setattr(mci.time, "sleep", lambda seconds: None)

    assert mount._read_tracking_enabled() is False


def test_multipoint_align_manual_start_sends_goto_to_selected_star():
    mount = DummyMountControl()

    assert mount.start_multipoint_align("manual", 2, "Vega")

    assert mount._multipoint_align is not None
    assert mount._multipoint_align["active"]
    assert mount._multipoint_align["total_points"] == 2
    assert mount._multipoint_align["current_star"]["name"] == "Vega"
    assert mount.goto_calls[-1][0] == 279.234735
    assert mount.goto_calls[-1][1] == 38.783689


def test_multipoint_align_confirm_advances_until_complete():
    mount = DummyMountControl()

    assert mount.start_multipoint_align("manual", 1, "Altair")
    assert mount.confirm_multipoint_align(source="web")

    assert mount.sync_calls == [(297.695827, 8.868322)]
    assert mount._multipoint_align is not None
    assert not mount._multipoint_align["active"]
    assert mount._multipoint_align["state"] == "complete"
    assert mount._multipoint_align["completed_points"] == 1


def test_multipoint_align_skysafari_confirm_uses_passed_target():
    mount = DummyMountControl()

    assert mount.start_multipoint_align("manual", 1)
    assert mount.confirm_multipoint_align(10.0, 20.0, source="skysafari")

    assert mount.sync_calls == [(10.0, 20.0)]
    assert mount._multipoint_align is not None
    assert mount._multipoint_align["completed"][0]["name"] == "SkySafari Target 1"
    assert mount._multipoint_align["completed"][0]["source"] == "skysafari"


def test_sync_location_time_uses_direct_lx200_for_onstep(monkeypatch):
    mount = DummyMountControl(DummySharedStateWithLocation())
    direct_calls = []
    cache_calls = []

    monkeypatch.setattr(
        mount,
        "_onstep_connection_config",
        lambda: {
            "connection_type": "network",
            "network_host": "10.10.10.12",
            "network_port": 9999,
            "serial_port": "",
            "direct_location_time_sync": True,
        },
    )
    monkeypatch.setattr(
        sys_utils,
        "sync_onstep_location_time_exclusive",
        lambda **kwargs: direct_calls.append(kwargs) or {"ok": True},
    )
    monkeypatch.setattr(
        sys_utils,
        "write_onstep_location_cache",
        lambda *args: cache_calls.append(args),
    )

    assert mount.sync_location_time()

    assert mount.applied_properties == []
    assert direct_calls == [
        {
            "connection_type": "network",
            "latitude": 37.52704,
            "longitude": 127.10936,
            "elevation": 30.0,
            "utc_datetime": "2026-07-01T14:45:00+00:00",
            "network_host": "10.10.10.12",
            "network_port": 9999,
            "serial_port": "",
            "server_host": "localhost",
            "server_port": 7624,
        }
    ]
    assert cache_calls == [(37.52704, 127.10936, 30.0, "2026-07-01T14:45:00+00:00")]


def test_radec_separation_arcmin_handles_small_offsets():
    sep = radec_separation_arcmin(10.0, 20.0, 10.0, 20.1)

    assert 5.9 < sep < 6.1


def test_shortest_ra_delta_wraps_at_zero_hours():
    assert shortest_ra_delta_deg(1.0, 359.0) == 2.0
    assert shortest_ra_delta_deg(359.0, 1.0) == -2.0


def test_goto_refine_syncs_fresh_solve_then_sends_one_regoto():
    solve_time = time.time()
    mount = DummyMountControl(DummySharedState(DummySolution(9.9, 20.0, solve_time)))
    mount._pending_goto_refine = {
        "target_ra": 10.0,
        "target_dec": 20.0,
        "accuracy_arcmin": 1.0,
        "requested_wall": solve_time - 1.0,
        "ready_at": time.monotonic() - 1.0,
        "timeout_at": time.monotonic() + 10.0,
    }

    mount._check_pending_goto_refine()

    assert mount._pending_goto_refine is None
    assert mount.sync_calls == [(9.9, 20.0)]
    assert mount.goto_calls == [(10.0, 20.0, False, None)]


def test_goto_target_uses_slew_mode_for_onstep():
    mount = DummyConnectedMount()

    assert mount.goto_target(132.0, 49.5)

    assert ("LX200 OnStep", "ON_COORD_SET", "SLEW") in mount.client.switches
    assert mount.client.numbers == [
        (
            "LX200 OnStep",
            "EQUATORIAL_EOD_COORD",
            {"RA": 132.0 / 15.0, "DEC": 49.5},
        )
    ]
    assert mount._goto_motion is not None
    assert mount.statuses[-1][0] == "slewing"


def test_goto_motion_completes_when_indi_state_is_not_busy(monkeypatch):
    mount = DummyConnectedMount()
    monkeypatch.setattr(mount, "_read_current_position", lambda: None)
    monkeypatch.setattr(mount, "_indi_mount_is_busy", lambda: False)

    assert mount.goto_target(132.0, 49.5)
    mount._goto_motion["started_at"] = time.monotonic() - 2.0
    mount._check_goto_motion()

    assert mount._goto_motion is None
    assert mount.statuses[-1][0] == "connected"
    assert mount.statuses[-1][1] == "GoTo complete"


def test_goto_motion_waits_while_indi_state_is_busy(monkeypatch):
    mount = DummyConnectedMount()
    monkeypatch.setattr(mount, "_indi_mount_is_busy", lambda: True)

    assert mount.goto_target(132.0, 49.5)
    mount._goto_motion["started_at"] = time.monotonic() - 2.0
    mount._check_goto_motion()

    assert mount._goto_motion is not None
    assert mount.statuses[-1][0] == "slewing"


def test_goto_refine_completes_without_regoto_inside_accuracy():
    solve_time = time.time()
    mount = DummyMountControl(DummySharedState(DummySolution(10.0, 20.0, solve_time)))
    mount._pending_goto_refine = {
        "target_ra": 10.0,
        "target_dec": 20.0,
        "accuracy_arcmin": 1.0,
        "requested_wall": solve_time - 1.0,
        "ready_at": time.monotonic() - 1.0,
        "timeout_at": time.monotonic() + 10.0,
    }

    mount._check_pending_goto_refine()

    assert mount._pending_goto_refine is None
    assert mount.sync_calls == []
    assert mount.goto_calls == []


def test_guide_correction_requires_a_target():
    mount = DummyMountControl()

    assert not mount.toggle_guide_correction(enabled=True)
    assert not mount._guide_correction_enabled


def test_guide_correction_pulses_toward_target_on_fresh_solve():
    solve_time = time.time()
    mount = DummyMountControl(DummySharedState(DummySolution(9.9, 20.0, solve_time)))
    mount._last_goto_target = (10.0, 20.0)

    assert mount.toggle_guide_correction(enabled=True, accuracy_arcmin=1.0)
    mount._guide_correction_next_at = time.monotonic() - 1.0
    mount._check_guide_correction()

    assert mount._manual_motion_direction == "east"
    assert mount._guide_correction_last_solve_time == solve_time


def test_guide_correction_does_not_pulse_inside_accuracy():
    solve_time = time.time()
    mount = DummyMountControl(DummySharedState(DummySolution(10.0, 20.0, solve_time)))
    mount._last_goto_target = (10.0, 20.0)

    assert mount.toggle_guide_correction(enabled=True, accuracy_arcmin=1.0)
    mount._guide_correction_next_at = time.monotonic() - 1.0
    mount._check_guide_correction()

    assert mount._manual_motion_direction is None
