import datetime
import time
from types import SimpleNamespace

import pytest

from PiFinder.pointing_coordinate_service import (
    CoordinateHealth,
    CoordinateSample,
    PointingCoordinateService,
    SOURCE_FUSED,
    SOURCE_IMU,
    SOURCE_MOUNT,
    SOURCE_PIFINDER_IMU_ESTIMATE,
    SOURCE_SOLVE,
    SOURCE_UNAVAILABLE,
)


class DummySolution:
    def __init__(
        self,
        ra_deg,
        dec_deg,
        has_pointing=True,
        solve_source="CAM",
        has_plate_anchor=False,
    ):
        aligned_solve = (
            SimpleNamespace(RA=ra_deg, Dec=dec_deg) if has_plate_anchor else None
        )
        self.pointing = SimpleNamespace(
            aligned=SimpleNamespace(
                solve=aligned_solve,
                estimate=SimpleNamespace(RA=ra_deg, Dec=dec_deg),
            )
        )
        self.estimate_time = 1000.0
        self.solve_source = solve_source
        self.last_solve_success = 900.0 if has_plate_anchor else None
        self._has_pointing = has_pointing

    def has_pointing(self):
        return self._has_pointing


class DummyState:
    def __init__(self, solution=None):
        self._solution = solution

    def solution(self):
        return self._solution

    def imu(self):
        return None

    def location(self):
        return None


def disabled_config(option, default=None):
    if option == "skysafari_imu_fallback":
        return False
    return default


def mount_enabled_config(option, default=None):
    if option == "mount_control":
        return True
    return disabled_config(option, default)


def test_solved_pointing_overrides_synced_mount_readback():
    service = PointingCoordinateService()
    dt = datetime.datetime(2026, 7, 1, 12, tzinfo=datetime.timezone.utc)

    state = service.current_state(
        DummyState(DummySolution(120.0, 20.0)),
        dt,
        config_get=mount_enabled_config,
        mount_status_provider=lambda: {
            "ra": 50.0,
            "dec": 10.0,
            "coordinate_sync": {"active": True, "ra": 50.0, "dec": 10.0},
        },
    )

    assert state.current.source == SOURCE_SOLVE
    assert state.current.valid is True
    assert state.mount.valid is True
    assert state.mount.aligned is True


def test_update_state_publishes_latest_coordinate_state():
    service = PointingCoordinateService()
    dt = datetime.datetime(2026, 7, 1, 12, tzinfo=datetime.timezone.utc)

    assert service.get_state() is None

    state = service.update_state(
        DummyState(DummySolution(120.0, 20.0)),
        dt,
        config_get=disabled_config,
    )

    assert service.get_state() is state
    assert service.get_state().current.source == SOURCE_SOLVE

    service.clear_state()

    assert service.get_state() is None


def test_unanchored_imu_solution_is_not_treated_as_trusted_pointing():
    service = PointingCoordinateService()
    dt = datetime.datetime(2026, 7, 1, 12, tzinfo=datetime.timezone.utc)

    state = service.current_state(
        DummyState(DummySolution(120.0, 20.0, solve_source="IMU")),
        dt,
        config_get=disabled_config,
    )

    assert state.solved.valid is False
    assert "no plate-solve anchor" in state.solved.reason
    assert state.current.source == SOURCE_UNAVAILABLE


def test_anchored_imu_solution_can_supply_pifinder_estimate():
    service = PointingCoordinateService()
    dt = datetime.datetime(2026, 7, 1, 12, tzinfo=datetime.timezone.utc)

    state = service.current_state(
        DummyState(
            DummySolution(
                120.0,
                20.0,
                solve_source="IMU",
                has_plate_anchor=True,
            )
        ),
        dt,
        config_get=disabled_config,
    )

    assert state.solved.valid is True
    assert state.current.source == SOURCE_PIFINDER_IMU_ESTIMATE


def test_unsynced_mount_readback_is_not_used_as_primary_coordinate():
    service = PointingCoordinateService()
    dt = datetime.datetime(2026, 7, 1, 12, tzinfo=datetime.timezone.utc)

    state = service.current_state(
        DummyState(),
        dt,
        config_get=mount_enabled_config,
        mount_status_provider=lambda: {"ra": 50.0, "dec": 10.0, "updated": 1000.0},
    )

    assert state.mount.valid is True
    assert state.mount.aligned is False
    assert state.health.mount_pre_alignment_only is True
    assert state.current.source == SOURCE_UNAVAILABLE
    assert state.current.valid is False


def test_parked_mount_readback_is_excluded_from_coordinate_candidates():
    service = PointingCoordinateService()
    dt = datetime.datetime(2026, 7, 1, 12, tzinfo=datetime.timezone.utc)

    state = service.current_state(
        DummyState(),
        dt,
        config_get=mount_enabled_config,
        mount_status_provider=lambda: {
            "ra": 50.0,
            "dec": 10.0,
            "updated": 1000.0,
            "park_state": "Parked",
            "state": "connected",
        },
    )

    assert state.mount.valid is False
    assert "parked" in state.mount.reason
    assert state.current.source == SOURCE_UNAVAILABLE


def test_synced_mount_readback_can_supply_no_solve_coordinate():
    service = PointingCoordinateService()
    dt = datetime.datetime(2026, 7, 1, 12, tzinfo=datetime.timezone.utc)

    state = service.current_state(
        DummyState(),
        dt,
        config_get=mount_enabled_config,
        mount_status_provider=lambda: {
            "ra": 50.0,
            "dec": 10.0,
            "updated": 1000.0,
            "coordinate_sync": {
                "active": True,
                "synced": True,
                "ra": 50.0,
                "dec": 10.0,
            },
        },
    )

    assert state.current.source == SOURCE_MOUNT
    assert state.current.radec() == pytest.approx((50.0, 10.0))
    assert state.mount.aligned is True


def test_synced_mount_uses_imu_delta_after_anchor_not_absolute_imu_position():
    service = PointingCoordinateService()
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")
    mount = CoordinateSample(
        ra_deg=10.0,
        dec_deg=10.0,
        source=SOURCE_MOUNT,
        valid=True,
        aligned=True,
        timestamp=100.0,
        metadata={"coordinate_sync": {"synced_at": 1.0}},
    )
    imu_anchor = CoordinateSample(
        ra_deg=100.0,
        dec_deg=20.0,
        source=SOURCE_IMU,
        valid=True,
        timestamp=100.0,
    )
    health = CoordinateHealth()

    first = service._select_current(solved, imu_anchor, mount, health)

    assert first.source == SOURCE_FUSED
    assert first.radec() == pytest.approx((10.0, 10.0))

    imu_moved = CoordinateSample(
        ra_deg=110.0,
        dec_deg=25.0,
        source=SOURCE_IMU,
        valid=True,
        timestamp=101.0,
    )
    health = CoordinateHealth()
    moved = service._select_current(solved, imu_moved, mount, health)

    assert moved.source == SOURCE_FUSED
    moved_ra, moved_dec = moved.radec()
    assert moved_dec > 14.0
    assert 18.0 < moved_ra < 22.0


def test_mount_heartbeat_without_position_change_does_not_clear_imu_delta():
    service = PointingCoordinateService()
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")
    mount = CoordinateSample(
        ra_deg=10.0,
        dec_deg=10.0,
        source=SOURCE_MOUNT,
        valid=True,
        aligned=True,
        timestamp=100.0,
        metadata={"coordinate_sync": {"synced_at": 1.0}},
    )
    imu_anchor = CoordinateSample(
        ra_deg=100.0,
        dec_deg=20.0,
        source=SOURCE_IMU,
        valid=True,
        timestamp=100.0,
    )
    service._select_current(solved, imu_anchor, mount, CoordinateHealth())

    heartbeat_mount = CoordinateSample(
        ra_deg=10.0,
        dec_deg=10.0,
        source=SOURCE_MOUNT,
        valid=True,
        aligned=True,
        timestamp=105.0,
        metadata={"coordinate_sync": {"synced_at": 1.0}},
    )
    imu_moved = CoordinateSample(
        ra_deg=110.0,
        dec_deg=25.0,
        source=SOURCE_IMU,
        valid=True,
        timestamp=105.0,
    )

    moved = service._select_current(
        solved, imu_moved, heartbeat_mount, CoordinateHealth()
    )

    assert moved.radec()[1] > 14.0


def test_synced_mount_motion_uses_mount_readback_until_stationary():
    service = PointingCoordinateService()
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")
    slewing_mount = CoordinateSample(
        ra_deg=10.0,
        dec_deg=10.0,
        source=SOURCE_MOUNT,
        valid=True,
        aligned=True,
        timestamp=100.0,
        metadata={
            "state": "slewing",
            "goto_motion_active": True,
            "coordinate_sync": {"synced_at": 1.0},
        },
    )
    imu_moved = CoordinateSample(
        ra_deg=120.0,
        dec_deg=40.0,
        source=SOURCE_IMU,
        valid=True,
        timestamp=100.0,
    )
    health = CoordinateHealth()

    current = service._select_current(solved, imu_moved, slewing_mount, health)

    assert current.source == SOURCE_MOUNT
    assert current.radec() == pytest.approx((10.0, 10.0))
    assert current.metadata["motion_active"] is True
    assert "mount motion/settle active" in health.warnings[0]


def test_synced_manual_motion_uses_mount_readback_until_stationary():
    service = PointingCoordinateService()
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")
    manual_mount = CoordinateSample(
        ra_deg=20.0,
        dec_deg=30.0,
        source=SOURCE_MOUNT,
        valid=True,
        aligned=True,
        timestamp=100.0,
        metadata={
            "state": "manual_motion",
            "manual_motion_direction": "east",
            "coordinate_sync": {"synced_at": 1.0},
        },
    )
    imu_moved = CoordinateSample(
        ra_deg=120.0,
        dec_deg=40.0,
        source=SOURCE_IMU,
        valid=True,
        timestamp=100.0,
    )
    health = CoordinateHealth()

    current = service._select_current(solved, imu_moved, manual_mount, health)

    assert current.source == SOURCE_MOUNT
    assert current.radec() == pytest.approx((20.0, 30.0))
    assert current.metadata["motion_active"] is True
    assert "mount motion/settle active" in health.warnings[0]


def test_common_readback_priority_overrides_legacy_motion_state():
    service = PointingCoordinateService()
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")
    mount = CoordinateSample(
        ra_deg=20.0,
        dec_deg=30.0,
        source=SOURCE_MOUNT,
        valid=True,
        aligned=True,
        timestamp=100.0,
        metadata={
            "state": "connected",
            "mount_motion_active": False,
            "mount_motion_type": "goto_refine_settle",
            "mount_readback_priority": True,
            "coordinate_sync": {"synced_at": 1.0},
        },
    )
    imu_moved = CoordinateSample(
        ra_deg=120.0,
        dec_deg=40.0,
        source=SOURCE_IMU,
        valid=True,
        timestamp=100.0,
    )
    health = CoordinateHealth()

    current = service._select_current(solved, imu_moved, mount, health)

    assert current.source == SOURCE_MOUNT
    assert current.radec() == pytest.approx((20.0, 30.0))
    assert current.metadata["motion_active"] is False
    assert current.metadata["readback_priority"] is True
    assert "mount motion/settle active" in health.warnings[0]


def test_synced_mount_reanchors_imu_delta_after_motion_stops():
    service = PointingCoordinateService()
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")
    slewing_mount = CoordinateSample(
        ra_deg=10.0,
        dec_deg=10.0,
        source=SOURCE_MOUNT,
        valid=True,
        aligned=True,
        timestamp=100.0,
        metadata={
            "state": "slewing",
            "goto_motion_active": True,
            "coordinate_sync": {"synced_at": 1.0},
        },
    )
    imu_during_motion = CoordinateSample(
        ra_deg=120.0,
        dec_deg=40.0,
        source=SOURCE_IMU,
        valid=True,
        timestamp=100.0,
    )
    service._select_current(
        solved, imu_during_motion, slewing_mount, CoordinateHealth()
    )

    stopped_mount = CoordinateSample(
        ra_deg=11.0,
        dec_deg=11.0,
        source=SOURCE_MOUNT,
        valid=True,
        aligned=True,
        timestamp=105.0,
        metadata={"state": "connected", "coordinate_sync": {"synced_at": 1.0}},
    )
    imu_at_stop = CoordinateSample(
        ra_deg=130.0,
        dec_deg=45.0,
        source=SOURCE_IMU,
        valid=True,
        timestamp=105.0,
    )
    current = service._select_current(
        solved, imu_at_stop, stopped_mount, CoordinateHealth()
    )

    assert current.source == SOURCE_MOUNT

    service._mount_motion_hold_until = time.monotonic() - 0.1
    current = service._select_current(
        solved, imu_at_stop, stopped_mount, CoordinateHealth()
    )

    assert current.source == SOURCE_FUSED
    assert current.radec() == pytest.approx((11.0, 11.0))


def test_mount_readback_change_extends_imu_delta_hold_after_status_is_connected():
    service = PointingCoordinateService()
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")
    service._last_mount_motion_radec = (10.0, 10.0)
    service._mount_motion_hold_until = time.monotonic() - 0.1
    mount_moved = CoordinateSample(
        ra_deg=10.1,
        dec_deg=10.0,
        source=SOURCE_MOUNT,
        valid=True,
        aligned=True,
        timestamp=105.0,
        metadata={"state": "connected", "coordinate_sync": {"synced_at": 1.0}},
    )
    imu = CoordinateSample(
        ra_deg=120.0,
        dec_deg=40.0,
        source=SOURCE_IMU,
        valid=True,
        timestamp=105.0,
    )
    health = CoordinateHealth()

    current = service._select_current(solved, imu, mount_moved, health)

    assert current.source == SOURCE_MOUNT
    assert current.radec() == pytest.approx((10.1, 10.0))
    assert service._mount_motion_hold_until > time.monotonic()


def test_imu_altaz_smoothing_damps_small_jitter_and_resets_on_large_motion():
    service = PointingCoordinateService()

    alt, az, state, delta = service._smooth_imu_altaz(10.0, 20.0)
    assert (alt, az, delta) == pytest.approx((10.0, 20.0, 0.0))
    assert state == "initial"

    alt, az, state, delta = service._smooth_imu_altaz(10.1, 20.1)
    assert state == "smoothed_small_jitter"
    assert 10.0 < alt < 10.1
    assert 20.0 < az < 20.1
    assert delta > 0.0

    alt, az, state, delta = service._smooth_imu_altaz(18.0, 40.0)
    assert state == "reset_large_motion"
    assert (alt, az) == pytest.approx((18.0, 40.0))
    assert delta >= 5.0


def test_multipoint_mount_sync_status_marks_mount_as_aligned():
    service = PointingCoordinateService()

    sample = service.mount_sample_from_status(
        {
            "ra": 42.0,
            "dec": -5.0,
            "multipoint_align": {
                "pifinder_mount_synced": True,
                "completed_points": 0,
            },
        }
    )

    assert sample.valid is True
    assert sample.aligned is True


def test_multipoint_completed_points_without_sync_do_not_align_mount():
    service = PointingCoordinateService()

    sample = service.mount_sample_from_status(
        {
            "ra": 42.0,
            "dec": -5.0,
            "multipoint_align": {
                "active": False,
                "completed_points": 3,
                "pifinder_mount_synced": False,
            },
        }
    )

    assert sample.valid is True
    assert sample.aligned is False


def _fused_mount(ra_deg, dec_deg, synced_at=1.0, **extra_metadata):
    metadata = {"state": "connected", "coordinate_sync": {"synced_at": synced_at}}
    metadata.update(extra_metadata)
    return CoordinateSample(
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        source=SOURCE_MOUNT,
        valid=True,
        aligned=True,
        timestamp=100.0,
        metadata=metadata,
    )


def _fused_imu(ra_deg, dec_deg):
    return CoordinateSample(
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        source=SOURCE_IMU,
        valid=True,
        timestamp=100.0,
    )


def _build_disturbance_offset(service, monkeypatch, clock):
    """Push the IMU fast (5 deg/s) so a +5 deg dec offset is applied."""
    import PiFinder.pointing_coordinate_service as pcs

    monkeypatch.setattr(pcs.time, "monotonic", lambda: clock[0])
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")

    first = service._select_current(
        solved, _fused_imu(100.0, 20.0), _fused_mount(10.0, 10.0), CoordinateHealth()
    )
    assert first.source == SOURCE_FUSED
    assert first.radec() == pytest.approx((10.0, 10.0))

    clock[0] += 1.0
    pushed = service._select_current(
        solved, _fused_imu(100.0, 25.0), _fused_mount(10.0, 10.0), CoordinateHealth()
    )
    assert pushed.source == SOURCE_FUSED
    assert pushed.radec()[1] == pytest.approx(15.0)
    return solved


def test_disturbance_offset_holds_after_scope_stops(monkeypatch):
    service = PointingCoordinateService()
    clock = [1000.0]
    solved = _build_disturbance_offset(service, monkeypatch, clock)

    # Scope stationary for 5 minutes: the offset must not decay back.
    for _ in range(5):
        clock[0] += 60.0
        held = service._select_current(
            solved,
            _fused_imu(100.0, 25.0),
            _fused_mount(10.0, 10.0),
            CoordinateHealth(),
        )

    assert held.source == SOURCE_FUSED
    assert held.radec()[1] == pytest.approx(15.0)
    assert held.metadata["imu_delta_gate"] == "hold"


def test_disturbance_offset_survives_mount_motion(monkeypatch):
    service = PointingCoordinateService()
    clock = [1000.0]
    solved = _build_disturbance_offset(service, monkeypatch, clock)

    # Mount slews itself; the IMU sees that motion too. Readback has priority
    # during the slew, but the offset must survive it.
    clock[0] += 1.0
    slewing = service._select_current(
        solved,
        _fused_imu(101.0, 26.0),
        _fused_mount(11.0, 10.0, goto_motion_active=True, state="slewing"),
        CoordinateHealth(),
    )
    assert slewing.source == SOURCE_MOUNT
    assert slewing.radec() == pytest.approx((11.0, 10.0))

    # Motion over, hold window expired: fused = live readback + held offset,
    # and the mount-driven IMU movement was not double-counted.
    clock[0] += 10.0
    service._mount_motion_hold_until = clock[0] - 0.1
    resumed = service._select_current(
        solved,
        _fused_imu(101.0, 26.0),
        _fused_mount(11.0, 10.0),
        CoordinateHealth(),
    )
    assert resumed.source == SOURCE_FUSED
    assert resumed.radec()[1] == pytest.approx(15.0)
    assert resumed.radec()[0] == pytest.approx(11.0)


def test_fused_base_follows_live_readback_without_losing_offset(monkeypatch):
    service = PointingCoordinateService()
    clock = [1000.0]
    solved = _build_disturbance_offset(service, monkeypatch, clock)

    # A tiny readback move (guide pulse, below the motion-detect threshold)
    # shifts the fused base directly; the offset stays applied.
    clock[0] += 1.0
    nudged = service._select_current(
        solved,
        _fused_imu(100.0, 25.0),
        _fused_mount(10.004, 10.0),
        CoordinateHealth(),
    )
    assert nudged.source == SOURCE_FUSED
    assert nudged.radec()[0] == pytest.approx(10.004)
    assert nudged.radec()[1] == pytest.approx(15.0)


def test_sync_clears_disturbance_offset(monkeypatch):
    service = PointingCoordinateService()
    clock = [1000.0]
    solved = _build_disturbance_offset(service, monkeypatch, clock)

    # A mount sync re-establishes the frame. The sync jumped the readback, so
    # the motion hold shows the readback first; once it expires the fused
    # coordinate must be the plain readback — the offset is cleared, not held.
    clock[0] += 1.0
    synced = service._select_current(
        solved,
        _fused_imu(100.0, 25.0),
        _fused_mount(12.0, 14.9, synced_at=2.0),
        CoordinateHealth(),
    )
    assert synced.radec() == pytest.approx((12.0, 14.9))

    clock[0] += 10.0
    service._mount_motion_hold_until = clock[0] - 0.1
    settled = service._select_current(
        solved,
        _fused_imu(100.0, 25.0),
        _fused_mount(12.0, 14.9, synced_at=2.0),
        CoordinateHealth(),
    )
    assert settled.source == SOURCE_FUSED
    assert settled.radec() == pytest.approx((12.0, 14.9))


def test_gate_hysteresis_captures_weak_slip_head_and_tail(monkeypatch):
    """Reproduces the hardware-captured weak stall-slip pattern (2026-07-16):
    rates ramp 0.033 -> 0.06 -> 0.02 -> 0.01 deg/s. With enter=0.03/exit=0.015
    the episode accumulates from the first 0.033 tick through the 0.02 tail,
    and releases at 0.01."""
    import PiFinder.pointing_coordinate_service as pcs

    service = PointingCoordinateService()
    clock = [1000.0]
    monkeypatch.setattr(pcs.time, "monotonic", lambda: clock[0])
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")

    dec = 20.0
    mount = _fused_mount(10.0, 10.0)

    def tick(dec_step):
        nonlocal dec
        clock[0] += 1.0
        dec += dec_step
        return service._select_current(
            solved, _fused_imu(100.0, dec), mount, CoordinateHealth()
        )

    service._select_current(solved, _fused_imu(100.0, dec), mount, CoordinateHealth())

    assert tick(0.004).metadata["imu_delta_gate"] == "hold"  # artifact floor
    assert tick(0.033).metadata["imu_delta_gate"] == "fast_follow"  # enters
    assert tick(0.060).metadata["imu_delta_gate"] == "fast_follow"  # peak
    assert tick(0.020).metadata["imu_delta_gate"] == "fast_follow"  # tail kept
    released = tick(0.010)  # exits
    assert released.metadata["imu_delta_gate"] == "hold"
    assert tick(0.020).metadata["imu_delta_gate"] == "hold"  # below re-entry

    # Applied dec offset = 0.033 + 0.060 + 0.020 only.
    assert released.metadata["imu_delta_applied_dec"] == pytest.approx(0.113)
    assert released.radec()[1] == pytest.approx(10.0 + 0.113)


def _fused_imu_altaz(alt_deg, az_deg, dt):
    """IMU sample carrying raw alt/az metadata, radec derived for consistency."""
    from PiFinder.calc_utils import sf_utils

    ra_deg, dec_deg = sf_utils.altaz_to_radec(alt_deg, az_deg, dt)
    return CoordinateSample(
        ra_deg=ra_deg % 360.0,
        dec_deg=dec_deg,
        alt_deg=alt_deg,
        az_deg=az_deg,
        source=SOURCE_IMU,
        valid=True,
        timestamp=100.0,
        metadata={"raw_alt": alt_deg, "raw_az": az_deg},
    )


def test_altaz_mount_hand_swing_applies_delta_in_altaz_space():
    """Regression for the 2026-07-19 hardware repro: on an alt/az mount an
    az-only hand swing must produce an az-only fused change, never the
    below-horizon dive of the old RA/Dec component transplant."""
    from PiFinder.calc_utils import sf_utils

    dt = datetime.datetime(2026, 7, 18, 15, 0, tzinfo=datetime.timezone.utc)
    location = SimpleNamespace(lat=37.527, lon=127.109, altitude=50.0)
    sf_utils.set_location(location.lat, location.lon, location.altitude)

    service = PointingCoordinateService()
    service._fusion_context = {
        "dt": dt,
        "location": location,
        "mount_type": "Alt/Az",
    }
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")
    # Mount synced somewhere in the west at alt ~20.
    mount_ra, mount_dec = sf_utils.altaz_to_radec(20.0, 275.0, dt)
    mount = _fused_mount(mount_ra % 360.0, mount_dec)

    anchor = service._select_current(
        solved, _fused_imu_altaz(15.0, 340.0, dt), mount, CoordinateHealth()
    )
    assert anchor.source == SOURCE_FUSED
    assert anchor.metadata["fusion_frame"] == "altaz"
    assert anchor.radec() == pytest.approx((mount_ra % 360.0, mount_dec))

    # Hand-swing az by +60 deg at constant alt (fast: consecutive ticks).
    moved = service._select_current(
        solved, _fused_imu_altaz(15.0, 40.0, dt), mount, CoordinateHealth()
    )
    assert moved.source == SOURCE_FUSED
    moved_ra, moved_dec = moved.radec()
    moved_alt, moved_az = sf_utils.radec_to_altaz(moved_ra, moved_dec, dt, atmos=False)
    # Az follows the swing; alt stays at the mount's altitude.
    assert moved_az == pytest.approx((275.0 + 60.0) % 360.0, abs=1.5)
    assert moved_alt == pytest.approx(20.0, abs=1.5)
    # The old transplant dove far below the horizon on exactly this move.
    assert moved_alt > 0.0


def test_eq_mount_delta_stays_component_additive_without_cos_rescale():
    """EQ mount FALLBACK path (no dt/location context, so the rotation
    tracker cannot run): the delta must be added component-wise with no
    cos(dec) transplant."""
    service = PointingCoordinateService()
    service._fusion_context = {
        "dt": None,
        "location": None,
        "mount_type": "Equatorial",
    }
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")
    mount = _fused_mount(10.0, 10.0)

    first = service._select_current(
        solved, _fused_imu(100.0, 60.0), mount, CoordinateHealth()
    )
    assert first.metadata["fusion_frame"] == "equatorial"
    assert first.radec() == pytest.approx((10.0, 10.0))

    moved = service._select_current(
        solved, _fused_imu(110.0, 60.0), mount, CoordinateHealth()
    )
    # +10 deg RA at the IMU must be +10 deg RA on the mount, dec unchanged.
    assert moved.radec() == pytest.approx((20.0, 10.0), abs=1e-6)


def test_altaz_rotation_tracker_survives_zenith_crossing():
    """The az-component tracker is singular at the zenith — a small physical
    motion over the top legitimately flips az by ~180, which component
    accumulation books as garbage. The rotation tracker must follow the swing
    over the zenith and land the fused pointing on the far side."""
    import math as m

    from PiFinder.calc_utils import sf_utils

    dt = datetime.datetime(2026, 7, 18, 15, 0, tzinfo=datetime.timezone.utc)
    location = SimpleNamespace(lat=37.527, lon=127.109, altitude=50.0)
    sf_utils.set_location(location.lat, location.lon, location.altitude)

    service = PointingCoordinateService()
    service._fusion_context = {
        "dt": dt,
        "location": location,
        "mount_type": "Alt/Az",
    }
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")
    mount_ra, mount_dec = sf_utils.altaz_to_radec(75.0, 0.0, dt)
    mount = _fused_mount(mount_ra % 360.0, mount_dec)

    # Hand swing along the meridian over the zenith: 20 deg of physical arc.
    path = [
        (80.0, 0.0),
        (85.0, 0.0),
        (89.0, 0.0),
        (89.0, 180.0),
        (85.0, 180.0),
        (80.0, 180.0),
    ]
    current = None
    for alt, az in path:
        current = service._select_current(
            solved, _fused_imu_altaz(alt, az, dt), mount, CoordinateHealth()
        )

    assert current is not None
    assert current.metadata["fusion_method"] == "rotation"
    ra, dec = current.radec()
    alt_f, az_f = sf_utils.radec_to_altaz(ra, dec, dt, atmos=False)
    # Mount at (75, az 0) carried 20 deg over the top must land at (85, az 180).
    sep = m.degrees(
        m.acos(
            max(
                -1.0,
                min(
                    1.0,
                    m.sin(m.radians(alt_f)) * m.sin(m.radians(85.0))
                    + m.cos(m.radians(alt_f))
                    * m.cos(m.radians(85.0))
                    * m.cos(m.radians(az_f - 180.0)),
                ),
            )
        )
    )
    assert sep < 2.0


def test_eq_mount_uses_rotation_tracker_and_survives_imu_yaw_offset():
    """EQ mount with full context must use the rotation tracker: the scalar
    RA/Dec path measures deltas in the IMU's own az frame, whose arbitrary
    imuplus yaw offset bends a pure polar-axis rotation into a large fake Dec
    component (measured on hardware: +15 RA read as +11.4 RA / +9.9 Dec at a
    -53 deg offset). The rotation tracker's psi0 mapping must recover the true
    motion."""
    from PiFinder.calc_utils import sf_utils

    dt = datetime.datetime(2026, 7, 18, 15, 0, tzinfo=datetime.timezone.utc)
    location = SimpleNamespace(lat=37.527, lon=127.109, altitude=50.0)
    sf_utils.set_location(location.lat, location.lon, location.altitude)

    service = PointingCoordinateService()
    service._fusion_context = {
        "dt": dt,
        "location": location,
        "mount_type": "Equatorial",
    }
    solved = CoordinateSample.invalid(SOURCE_SOLVE, "test")
    yaw_off = -53.0  # arbitrary imuplus yaw frame offset

    # Mount synced: readback equals the scope's true pointing.
    alt0, az0 = 40.0, 200.0
    mount_ra, mount_dec = sf_utils.altaz_to_radec(alt0, az0, dt)
    mount = _fused_mount(mount_ra % 360.0, mount_dec)

    def imu_at(true_alt, true_az):
        # The IMU reports its own yaw-shifted azimuth for the true pointing.
        return _fused_imu_altaz(true_alt, (true_az + yaw_off) % 360.0, dt)

    first = service._select_current(
        solved, imu_at(alt0, az0), mount, CoordinateHealth()
    )
    assert first.metadata["fusion_method"] == "rotation"
    assert first.metadata["fusion_frame"] == "equatorial"
    assert first.radec() == pytest.approx((mount_ra % 360.0, mount_dec))

    # Physical hand rotation of +15 deg about the POLAR axis, in 5-deg steps:
    # true pointing RA increases, Dec constant.
    current = first
    for dra in (5.0, 10.0, 15.0):
        true_ra = (mount_ra + dra) % 360.0
        true_alt, true_az = sf_utils.radec_to_altaz(true_ra, mount_dec, dt, atmos=False)
        current = service._select_current(
            solved, imu_at(true_alt, true_az), mount, CoordinateHealth()
        )

    fused_ra, fused_dec = current.radec()
    assert _wrap(fused_ra - (mount_ra + 15.0)) == pytest.approx(0.0, abs=1.0)
    assert fused_dec == pytest.approx(mount_dec, abs=1.0)


def test_fusion_anchor_resets_when_observer_location_moves():
    service = PointingCoordinateService()
    here = SimpleNamespace(lat=37.5, lon=127.0, altitude=30.0)

    # First observation just records the site -- nothing to reset yet.
    service._mount_imu_anchor = {"sentinel": "anchor"}
    service._imu_delta_tracker = {"sentinel": "tracker"}
    service._reset_fusion_on_location_change(here)
    assert service._mount_imu_anchor == {"sentinel": "anchor"}
    assert service._fusion_location == (37.5, 127.0, 30.0)

    # GPS jitter (a few metres) must NOT reset the anchor.
    service._reset_fusion_on_location_change(
        SimpleNamespace(lat=37.50001, lon=127.00001, altitude=30.0)
    )
    assert service._mount_imu_anchor == {"sentinel": "anchor"}

    # A real relocation (~1.5 km) discards the anchor and tracker so they
    # rebuild for the new site.
    service._reset_fusion_on_location_change(
        SimpleNamespace(lat=37.51, lon=127.01, altitude=30.0)
    )
    assert service._mount_imu_anchor is None
    assert service._imu_delta_tracker is None
    assert service._fusion_location == (37.51, 127.01, 30.0)


def _wrap(delta):
    return ((delta + 180.0) % 360.0) - 180.0
