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

    def _ensure_backlash_safe_position(self):
        return True


class SafetyMountControl(MountControlIndi):
    def __init__(self):
        super().__init__(Queue(), Queue(), None)
        self.statuses = []
        self.stopped = False

    def _write_controller_status(self, state, message="", **extra):
        self.statuses.append((state, message, extra))

    def stop_mount(self):
        self.stopped = True
        return True


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


def test_auto_backlash_fails_without_fresh_imu():
    mount = DummyMountControl()

    assert not mount.auto_calculate_backlash("ra")

    assert mount._backlash_auto is not None
    assert mount._backlash_auto["axis"] == "RA"
    assert mount._backlash_auto["state"] == "failed"
    assert len(mount.applied_properties) == 1
    assert any(
        "TELESCOPE_ABORT_MOTION.ABORT=On" in prop
        for prop in mount.applied_properties[0]
    )


def test_auto_backlash_refuses_compass_mode():
    mount = DummyMountControl(shared_state=object())
    mount._current_imu_sample = lambda: SimpleNamespace(
        uses_magnetometer=True,
        fusion_mode="ndof",
        calibration_status=(3, 3, 3, 3),
    )

    assert not mount.auto_calculate_backlash("ra")

    assert mount._backlash_auto is not None
    assert mount._backlash_auto["state"] == "failed"
    assert "Compass" in mount._backlash_auto["message"]
    assert len(mount.applied_properties) == 1
    assert any(
        "TELESCOPE_ABORT_MOTION.ABORT=On" in prop
        for prop in mount.applied_properties[0]
    )


def test_auto_backlash_refuses_unstable_imu_baseline(monkeypatch):
    monkeypatch.setattr(mci, "BACKLASH_AUTO_TRACKING_SETTLE_SECONDS", 0)
    monkeypatch.setattr(mci, "BACKLASH_AUTO_STABILITY_RETRIES", 1)
    mount = DummyMountControl(shared_state=object())
    mount.backlash_ra = 0
    mount.backlash_de = 0
    mount.slew_rate = 8
    goto_calls = []

    mount._current_imu_sample = lambda: SimpleNamespace(
        uses_magnetometer=False,
        fusion_mode="imuplus",
        calibration_status=(0, 3, 1, 0),
    )
    mount._wait_for_imu_sample = lambda timeout=3.0: object()
    mount._wait_for_imu_stable = lambda seconds=3.0: {
        "quat": object(),
        "spread_deg": 0.2,
        "threshold_deg": 0.8,
        "samples": 3,
    }
    mount.refresh_backlash = lambda: (mount.backlash_ra, mount.backlash_de)
    mount._read_driver_slew_rate = lambda: mount.slew_rate
    mount._read_tracking_enabled = lambda: False
    mount.set_slew_rate = lambda rate: True
    mount.stop_mount = lambda: True
    mount._read_current_position = lambda: (30.0, 20.0)
    mount._goto_target_and_wait = lambda *args, **kwargs: goto_calls.append(args)

    assert not mount.auto_calculate_backlash("ra")

    assert mount._backlash_auto is not None
    assert mount._backlash_auto["state"] == "failed"
    assert "baseline noise" in mount._backlash_auto["message"]
    assert goto_calls == []


def test_auto_backlash_stops_before_reset_when_imu_safety_fails(monkeypatch):
    monkeypatch.setattr(mci, "BACKLASH_AUTO_TRACKING_SETTLE_SECONDS", 0)
    mount = DummyMountControl(shared_state=object())
    mount.backlash_ra = 12
    mount.backlash_de = 34
    mount.slew_rate = 6
    roundtrips = []

    mount._current_imu_sample = lambda: SimpleNamespace(
        uses_magnetometer=False,
        fusion_mode="imuplus",
        calibration_status=(3, 3, 1, 0),
    )
    mount._wait_for_imu_sample = lambda timeout=3.0: object()
    mount.refresh_backlash = lambda: (mount.backlash_ra, mount.backlash_de)
    mount._read_driver_slew_rate = lambda: mount.slew_rate
    mount._read_tracking_enabled = lambda: False
    mount.set_slew_rate = lambda rate: True
    mount.stop_mount = lambda: True
    mount._ensure_backlash_safe_position = lambda: False
    mount._measure_backlash_goto_roundtrip = lambda *args, **kwargs: roundtrips.append(
        args
    )

    assert not mount.auto_calculate_backlash("ra")

    assert roundtrips == []
    assert not any(
        "Backlash.Backlash RA=0" in prop
        for props in mount.applied_properties
        for prop in props
    )


def test_backlash_safety_moves_to_safe_imu_altitude(monkeypatch):
    mount = SafetyMountControl()
    altaz_reads = iter([(5.0, 180.0), (45.0, 180.0)])
    goto_calls = []

    mount._backlash_auto = {}
    mount._current_imu_altaz = lambda: next(altaz_reads)
    mount._safe_backlash_target_radec = lambda az: (123.0, 45.0)
    mount._goto_target_and_wait = (
        lambda ra, dec, label, timeout: goto_calls.append((ra, dec, label, timeout))
        or True
    )
    mount._wait_for_backlash_baseline = lambda label: {
        "quat": object(),
        "spread_deg": 0.01,
        "threshold_deg": 0.04,
        "samples": 5,
    }

    assert mount._ensure_backlash_safe_position()

    assert goto_calls == [
        (
            123.0,
            45.0,
            "Safe backlash test position",
            mci.BACKLASH_AUTO_SAFE_GOTO_TIMEOUT_SECONDS,
        )
    ]
    assert mount._backlash_auto["state"] == "running"
    assert "confirmed" in mount._backlash_auto["message"]


def test_backlash_safety_fails_when_safe_goto_is_blocked():
    mount = SafetyMountControl()
    goto_calls = []

    mount._backlash_auto = {}
    mount._current_imu_altaz = lambda: (5.0, 180.0)
    mount._safe_backlash_target_radec = lambda az: (123.0, 45.0)
    mount._goto_target_and_wait = (
        lambda ra, dec, label, timeout: goto_calls.append((ra, dec, label, timeout))
        and False
    )

    assert not mount._ensure_backlash_safe_position()

    assert goto_calls
    assert mount.stopped
    assert mount._backlash_auto["state"] == "failed"
    assert "OnStep may have blocked" in mount._backlash_auto["message"]


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


def test_auto_backlash_completes_with_mocked_goto_roundtrip(monkeypatch):
    monkeypatch.setattr(mci, "BACKLASH_AUTO_TRACKING_SETTLE_SECONDS", 0)
    mount = DummyMountControl(shared_state=object())
    mount.backlash_ra = 5
    mount.backlash_de = 7
    mount.slew_rate = 4
    slew_rates = []
    roundtrips = []

    mount._current_imu_sample = lambda: SimpleNamespace(
        uses_magnetometer=False,
        fusion_mode="imuplus",
        calibration_status=(3, 3, 1, 0),
    )
    mount._wait_for_imu_sample = lambda timeout=3.0: object()

    def fake_roundtrip(cfg, repeat_index, move_degrees, **kwargs):
        roundtrips.append((cfg["axis"], repeat_index, move_degrees))
        return {
            "valid": True,
            "move_degrees": move_degrees,
            "commanded_degrees": 5.0,
            "outward_angle_deg": 5.0,
            "reverse_angle_deg": 4.95,
            "error_degrees": 0.05,
            "estimated_arcsec": 180 + repeat_index,
        }

    mount._measure_backlash_goto_roundtrip = fake_roundtrip
    mount.refresh_backlash = lambda: (mount.backlash_ra, mount.backlash_de)
    mount._read_driver_slew_rate = lambda: mount.slew_rate
    tracking_reads = [True, False]
    mount._read_tracking_enabled = (
        lambda: tracking_reads.pop(0) if tracking_reads else True
    )

    def fake_set_slew_rate(rate):
        mount.slew_rate = rate
        slew_rates.append(rate)
        return True

    mount.set_slew_rate = fake_set_slew_rate
    mount.stop_mount = lambda: True

    assert mount.auto_calculate_backlash("ra")

    assert mount._backlash_auto is not None
    assert mount._backlash_auto["axis"] == "RA"
    assert mount._backlash_auto["state"] == "complete"
    assert mount._backlash_auto["estimated_value"] == 182
    assert len(mount._backlash_auto["measurements"]) == 3
    assert roundtrips == [
        ("RA", 1, 5.0),
        ("RA", 2, 5.0),
        ("RA", 3, 5.0),
        ("RA", 1, 5.0),
        ("RA", 2, 5.0),
        ("RA", 3, 5.0),
    ]
    assert mount._backlash_auto["verification_average_residual_arcsec"] == 182
    assert mount._backlash_auto["verification_error_rate_percent"] == 100.0
    assert any(
        "Backlash.Backlash RA=5" in prop
        for props in mount.applied_properties
        for prop in props
    )
    assert any(
        "Backlash.Backlash DEC=7" in prop
        for props in mount.applied_properties
        for prop in props
    )
    assert any(
        "Backlash.Backlash RA=182" in prop
        for props in mount.applied_properties
        for prop in props
    )
    assert any(
        "TELESCOPE_TRACK_STATE.TRACK_OFF=On" in prop
        for props in mount.applied_properties
        for prop in props
    )
    assert any(
        "TELESCOPE_TRACK_STATE.TRACK_ON=On" in prop
        for props in mount.applied_properties
        for prop in props
    )
    assert slew_rates[0] == 5
    assert slew_rates[-1] == 4


def test_auto_backlash_fails_when_goto_motion_is_not_measurable(monkeypatch):
    monkeypatch.setattr(mci, "BACKLASH_AUTO_TRACKING_SETTLE_SECONDS", 0)
    monkeypatch.setattr(mci, "BACKLASH_AUTO_GOTO_REPEATS", 1)
    monkeypatch.setattr(mci, "BACKLASH_AUTO_GOTO_MAX_DEGREES", 10.0)
    mount = DummyMountControl(shared_state=object())
    mount.backlash_ra = 0
    mount.backlash_de = 0
    mount.slew_rate = 8
    move_degrees_seen = []

    mount._current_imu_sample = lambda: SimpleNamespace(
        uses_magnetometer=False,
        fusion_mode="imuplus",
        calibration_status=(0, 3, 1, 0),
    )
    mount._wait_for_imu_sample = lambda timeout=3.0: object()
    mount.refresh_backlash = lambda: (mount.backlash_ra, mount.backlash_de)
    mount._read_driver_slew_rate = lambda: mount.slew_rate
    mount._read_tracking_enabled = lambda: False
    mount.set_slew_rate = lambda rate: True
    mount.stop_mount = lambda: True

    def fake_roundtrip(cfg, repeat_index, move_degrees, **kwargs):
        move_degrees_seen.append(move_degrees)
        return {
            "valid": False,
            "reason": "return_motion_too_small",
            "move_degrees": move_degrees,
            "commanded_degrees": move_degrees,
            "reverse_angle_deg": 0.0,
        }

    mount._measure_backlash_goto_roundtrip = fake_roundtrip

    assert not mount.auto_calculate_backlash("de")

    assert mount._backlash_auto is not None
    assert mount._backlash_auto["state"] == "failed"
    assert "did not produce reliable IMU motion" in mount._backlash_auto["message"]
    assert move_degrees_seen == [5.0, 10.0]


def test_auto_backlash_retries_larger_angle_when_estimate_saturates(monkeypatch):
    monkeypatch.setattr(mci, "BACKLASH_AUTO_TRACKING_SETTLE_SECONDS", 0)
    monkeypatch.setattr(mci, "BACKLASH_AUTO_GOTO_REPEATS", 1)
    mount = DummyMountControl(shared_state=object())
    mount.backlash_ra = 0
    mount.backlash_de = 0
    mount.slew_rate = 8
    move_degrees_seen = []

    mount._current_imu_sample = lambda: SimpleNamespace(
        uses_magnetometer=False,
        fusion_mode="imuplus",
        calibration_status=(0, 3, 1, 0),
    )
    mount._wait_for_imu_sample = lambda timeout=3.0: object()
    mount.refresh_backlash = lambda: (mount.backlash_ra, mount.backlash_de)
    mount._read_driver_slew_rate = lambda: mount.slew_rate
    mount._read_tracking_enabled = lambda: False
    mount.set_slew_rate = lambda rate: True
    mount.stop_mount = lambda: True

    def fake_roundtrip(cfg, repeat_index, move_degrees, **kwargs):
        move_degrees_seen.append(move_degrees)
        if move_degrees < 10:
            return {
                "valid": False,
                "reason": "estimate_saturated",
                "move_degrees": move_degrees,
                "commanded_degrees": move_degrees,
                "estimated_arcsec": mci.BACKLASH_MAX_VALUE,
            }
        return {
            "valid": True,
            "move_degrees": move_degrees,
            "commanded_degrees": move_degrees,
            "outward_angle_deg": move_degrees,
            "reverse_angle_deg": move_degrees - 0.05,
            "error_degrees": 0.05,
            "estimated_arcsec": 180,
        }

    mount._measure_backlash_goto_roundtrip = fake_roundtrip

    assert mount.auto_calculate_backlash("ra")

    assert move_degrees_seen == [5.0, 10.0, 10.0, 10.0, 10.0]
    assert mount._backlash_auto is not None
    assert mount._backlash_auto["estimated_value"] == 180


def test_auto_backlash_restores_tracking_off_after_goto_enables_it(monkeypatch):
    monkeypatch.setattr(mci, "BACKLASH_AUTO_TRACKING_SETTLE_SECONDS", 0)
    mount = DummyMountControl(shared_state=object())
    mount.backlash_ra = 0
    mount.backlash_de = 0
    mount.slew_rate = 6
    tracking_reads = [False, True]
    tracking_writes = []

    mount._current_imu_sample = lambda: SimpleNamespace(
        uses_magnetometer=False,
        fusion_mode="imuplus",
        calibration_status=(0, 3, 1, 0),
    )
    mount._wait_for_imu_sample = lambda timeout=3.0: object()
    mount.refresh_backlash = lambda: (mount.backlash_ra, mount.backlash_de)
    mount._read_driver_slew_rate = lambda: mount.slew_rate

    def fake_read_tracking():
        if tracking_reads:
            return tracking_reads.pop(0)
        return False

    mount._read_tracking_enabled = fake_read_tracking
    mount.set_tracking = lambda enabled: tracking_writes.append(enabled) or True
    mount.set_slew_rate = lambda rate: True
    mount.stop_mount = lambda: True
    mount._measure_backlash_goto_roundtrip = lambda *args, **kwargs: {
        "valid": True,
        "commanded_degrees": 5.0,
        "outward_angle_deg": 5.0,
        "reverse_angle_deg": 4.99,
        "error_degrees": 0.01,
        "estimated_arcsec": 36,
    }

    assert mount.auto_calculate_backlash("ra")

    assert tracking_writes == [False]


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


def test_backlash_eq_ra_plan_does_not_cos_correct():
    mount = DummyMountControl()

    eq_plan = mount._axis_goto_plan("ra", 10.0, 60.0, 5.0, "eq")
    altaz_plan = mount._axis_goto_plan("ra", 10.0, 60.0, 5.0, "altaz")

    assert eq_plan["target_ra"] == 15.0
    assert eq_plan["commanded_degrees"] == 5.0
    assert altaz_plan["target_ra"] > eq_plan["target_ra"]
    assert 4.9 < altaz_plan["commanded_degrees"] < 5.1


def test_backlash_roundtrip_uses_outward_position_for_reverse(monkeypatch):
    mount = DummyMountControl(shared_state=object())
    cfg = mount._backlash_axis_config("ra")
    positions = [(10.0, 20.0), (16.0, 20.0), (11.0, 20.0)]
    goto_calls = []
    imu_angles = [5.0, 4.9]

    mount._read_current_position = lambda: positions.pop(0)
    mount._wait_for_backlash_baseline = lambda label: {
        "quat": object(),
        "spread_deg": 0.01,
        "threshold_deg": 0.04,
        "samples": 5,
    }
    mount._goto_target_and_wait = (
        lambda ra, dec, label: goto_calls.append((ra, dec, label)) or True
    )
    mount._imu_angle_diff_deg = lambda quat: imu_angles.pop(0)

    measurement = mount._measure_backlash_goto_roundtrip(
        cfg, 1, 5.0, mount_model="eq"
    )

    assert measurement is not None
    assert measurement["valid"]
    assert goto_calls[0][0] == 15.0
    assert goto_calls[1][0] == 11.0
    assert goto_calls[1][0] != 10.0
    assert measurement["estimated_arcsec"] == 360


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
