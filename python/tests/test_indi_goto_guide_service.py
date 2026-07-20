"""Tracking-guide settle/recovery behavior of the INDI GoTo/Guide service."""

from multiprocessing import Queue

import PiFinder.indi_goto_guide_service as iggs
from PiFinder.indi_goto_guide_service import IndiGotoGuideService


class DummyMountQueue:
    def __init__(self):
        self.commands = []

    def put(self, command):
        self.commands.append(command)


def _make_service(monkeypatch, clock):
    monkeypatch.setattr(iggs.time, "monotonic", lambda: clock[0])
    service = IndiGotoGuideService(Queue(), DummyMountQueue(), None)
    service.config_values = {
        "indi_tracking_guide_enabled": True,
        "indi_tracking_guide_settle_seconds": 4.0,
        "indi_tracking_guide_motion_arcmin": 15.0,
        "indi_tracking_guide_threshold_arcmin": 10.0,
        "indi_tracking_guide_goto_recovery_enabled": True,
        "indi_tracking_guide_goto_threshold_deg": 0.5,
        "indi_tracking_guide_manual_retarget_enabled": False,
    }
    # Disturbed position 2 deg north of the tracking target: well above the
    # 0.5 deg GoTo recovery threshold.
    service.tracking_target_ra = 100.0
    service.tracking_target_dec = 20.0
    monkeypatch.setattr(
        service,
        "_mount_status_summary",
        lambda: {"available": True, "state": "connected"},
    )
    service._pointing = {
        "usable_for_goto": True,
        "current": {"ra": 100.0, "dec": 22.0},
        "imu": {"metadata": {"moving": False}},
    }
    monkeypatch.setattr(service, "_refresh_pointing_status", lambda: service._pointing)
    monkeypatch.setattr(service, "_write_status", lambda **kwargs: None)
    return service


def _set_imu_moving(service, moving):
    service._pointing["imu"]["metadata"]["moving"] = moving


def test_recovery_starts_after_settle_when_motion_ends(monkeypatch):
    clock = [1000.0]
    service = _make_service(monkeypatch, clock)

    # First tick baselines the coordinate and opens a fresh settle window.
    service._tick_tracking_guide()
    assert service.tracking_guide_state == "settling"

    # Stationary, IMU quiet: settle completes after 4 s, recovery fires.
    for _ in range(4):
        clock[0] += 1.0
        service._tick_tracking_guide()

    assert service.tracking_guide_state == "recovering_goto"
    commands = service.mountcontrol_queue.commands
    assert [c["type"] for c in commands[-2:]] == ["sync", "goto_target"]
    assert commands[-1]["ra"] == 100.0
    assert commands[-1]["dec"] == 20.0


def test_lingering_imu_flag_cannot_delay_recovery_indefinitely(monkeypatch):
    clock = [1000.0]
    service = _make_service(monkeypatch, clock)
    _set_imu_moving(service, True)

    service._tick_tracking_guide()
    assert service.tracking_guide_state == "disturbed"

    # Coordinate is perfectly still but the IMU flag stays set (micro-sway):
    # the flag blocks recovery only up to 2x the settle window (8 s here).
    for _ in range(7):
        clock[0] += 1.0
        service._tick_tracking_guide()
        assert service.tracking_guide_state == "disturbed"

    clock[0] += 1.0
    service._tick_tracking_guide()

    assert service.tracking_guide_state == "recovering_goto"
    assert service.tracking_imu_flag_overridden is True


def test_coordinate_motion_still_blocks_recovery(monkeypatch):
    clock = [1000.0]
    service = _make_service(monkeypatch, clock)
    service._tick_tracking_guide()

    # The coordinate keeps jumping >15' per tick: recovery must stay blocked
    # no matter how long it goes on (this is a real ongoing push).
    for i in range(20):
        clock[0] += 1.0
        service._pointing["current"]["dec"] = 22.5 + (0.5 if i % 2 else 0.0)
        service._tick_tracking_guide()
        assert service.tracking_guide_state == "disturbed"


def test_short_imu_flag_episode_extends_settle_normally(monkeypatch):
    clock = [1000.0]
    service = _make_service(monkeypatch, clock)
    service._tick_tracking_guide()

    # IMU flag set for 2 s, then clears: recovery waits 4 s from the LAST
    # IMU-moving tick, not from the coordinate baseline.
    for _ in range(2):
        clock[0] += 1.0
        _set_imu_moving(service, True)
        service._tick_tracking_guide()
        assert service.tracking_guide_state == "disturbed"

    _set_imu_moving(service, False)
    for _ in range(3):
        clock[0] += 1.0
        service._tick_tracking_guide()
        assert service.tracking_guide_state == "settling"

    clock[0] += 1.0
    service._tick_tracking_guide()
    assert service.tracking_guide_state == "recovering_goto"


def test_target_below_altitude_limit_abandons_without_slew(monkeypatch):
    clock = [1000.0]
    service = _make_service(monkeypatch, clock)
    monkeypatch.setattr(service, "_tracking_target_altitude_deg", lambda: 5.0)

    service._tick_tracking_guide()

    assert service.tracking_guide_state == "failed"
    assert service.tracking_target_ra is None
    assert service.tracking_target_dec is None
    commands = [c["type"] for c in service.mountcontrol_queue.commands]
    assert "goto_target" not in commands
    assert "stop_movement" in commands


def test_target_above_altitude_limit_recovers_normally(monkeypatch):
    clock = [1000.0]
    service = _make_service(monkeypatch, clock)
    monkeypatch.setattr(service, "_tracking_target_altitude_deg", lambda: 45.0)

    service._tick_tracking_guide()
    for _ in range(4):
        clock[0] += 1.0
        service._tick_tracking_guide()

    assert service.tracking_guide_state == "recovering_goto"


def test_large_recovery_error_still_recovers(monkeypatch):
    clock = [1000.0]
    service = _make_service(monkeypatch, clock)
    # Current position 15 deg away from the target (e.g. a large hand-slew):
    # recovery has no error cap, only the target-altitude guard.
    service._pointing["current"]["dec"] = 35.0

    service._tick_tracking_guide()
    for _ in range(4):
        clock[0] += 1.0
        service._tick_tracking_guide()

    assert service.tracking_guide_state == "recovering_goto"
    assert service.tracking_target_ra is not None


def test_clear_tracking_target_command(monkeypatch):
    clock = [1000.0]
    service = _make_service(monkeypatch, clock)

    assert service.handle_command({"type": "clear_tracking_target"}) is True

    assert service.tracking_target_ra is None
    assert service.tracking_target_dec is None
    service._tick_tracking_guide()
    assert service.tracking_guide_state == "waiting_target"


def test_suspend_blocks_corrections_until_new_goto(monkeypatch):
    clock = [1000.0]
    service = _make_service(monkeypatch, clock)

    assert service.handle_command({"type": "suspend_tracking_guide"}) is True
    assert service.tracking_guide_suspended is True

    # Stationary through a full settle window: no recovery or pulse commands
    # may be issued while suspended.
    service._tick_tracking_guide()
    for _ in range(5):
        clock[0] += 1.0
        service._tick_tracking_guide()

    assert service.tracking_guide_state == "suspended"
    assert service.mountcontrol_queue.commands == []

    # A new GoTo lifts the suspension.
    service._handle_goto_target({"type": "goto_target", "ra": 100.0, "dec": 20.0})
    assert service.tracking_guide_suspended is False


def test_suspend_lifts_after_manual_move_settles(monkeypatch):
    clock = [1000.0]
    service = _make_service(monkeypatch, clock)
    service.handle_command({"type": "suspend_tracking_guide"})

    # Baseline tick, then a user manual move ends (the motion branch arms this
    # flag in production; set it directly here) and the coordinate settles.
    service._tick_tracking_guide()
    service.manual_retarget_pending = True
    for _ in range(5):
        clock[0] += 1.0
        service._tick_tracking_guide()

    assert service.tracking_guide_suspended is False
    # Manual re-target is disabled in this config, so after the suspension
    # lifts the 2 deg error goes straight to GoTo recovery.
    assert service.tracking_guide_state == "recovering_goto"
