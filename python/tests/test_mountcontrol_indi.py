import time
from multiprocessing import Queue

from PiFinder import sys_utils
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

    assert any("Backlash.RA=12" in prop for prop in mount.applied_properties[-1])
    assert any("Backlash.DE=34" in prop for prop in mount.applied_properties[-1])
    assert mount.backlash_ra == 12
    assert mount.backlash_de == 34


def test_auto_backlash_is_staged_without_hardware_motion():
    mount = DummyMountControl()

    assert mount.auto_calculate_backlash("ra")

    assert mount._backlash_auto is not None
    assert mount._backlash_auto["axis"] == "RA"
    assert mount._backlash_auto["state"] == "pending_hardware_test"
    assert mount.applied_properties == []


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
