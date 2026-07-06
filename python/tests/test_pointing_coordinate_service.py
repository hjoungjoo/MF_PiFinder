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
