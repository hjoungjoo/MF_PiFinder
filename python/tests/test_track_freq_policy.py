"""Tests for PiFinder.track_freq_policy (LCD GoTo tracking-frequency policy)."""

import math
from types import SimpleNamespace

import pytest

from PiFinder import nonsidereal, track_freq_policy


class FakeSharedState:
    def location(self):
        return SimpleNamespace(lat=37.527, lon=127.109, altitude=30.0, lock=True)

    def datetime(self):
        return None  # policy falls back to system UTC


def _planet_object(name):
    return SimpleNamespace(obj_type="Pla", names=[name], catalog_code="PL")


def _dso_object():
    return SimpleNamespace(obj_type="Gx", names=["M31"], catalog_code="M")


@pytest.mark.unit
def test_planet_target_gets_feed_forward_rate():
    command = track_freq_policy.track_freq_command_for_target(
        _planet_object("Moon"), FakeSharedState()
    )
    assert command is not None
    assert command["type"] == "set_track_freq"
    assert command["label"] == "Moon"
    # The Moon moves eastward: the mount must run slower than sidereal.
    assert command["hz"] < nonsidereal.SIDEREAL_FREQ_HZ
    assert nonsidereal.MIN_FREQ_HZ <= command["hz"] <= nonsidereal.MAX_FREQ_HZ


@pytest.mark.unit
def test_static_target_resets_only_when_active(monkeypatch):
    monkeypatch.setattr(
        track_freq_policy, "_mount_status", lambda: {"track_freq_hz": 59.2}
    )
    command = track_freq_policy.track_freq_command_for_target(
        _dso_object(), FakeSharedState()
    )
    assert command == {"type": "reset_track_freq"}

    monkeypatch.setattr(track_freq_policy, "_mount_status", lambda: {})
    assert (
        track_freq_policy.track_freq_command_for_target(
            _dso_object(), FakeSharedState()
        )
        is None
    )


@pytest.mark.unit
def test_no_object_behaves_like_static(monkeypatch):
    monkeypatch.setattr(track_freq_policy, "_mount_status", lambda: {})
    assert (
        track_freq_policy.track_freq_command_for_target(None, FakeSharedState()) is None
    )


def _stub_planets(monkeypatch, planets):
    """Pin the ephemeris so tolerance behaviour does not depend on today's sky.

    ``planets`` maps body name to an (ra_deg, dec_deg) pair.
    """
    monkeypatch.setattr(
        track_freq_policy,
        "planet_positions_of_date",
        lambda shared_state: planets,
    )


@pytest.mark.unit
def test_coordinates_on_a_planet_are_identified(monkeypatch):
    _stub_planets(
        monkeypatch,
        {
            "JUPITER": (120.0, 20.0),
            "MARS": (200.0, -10.0),
        },
    )
    assert (
        track_freq_policy.planet_at_coordinates(120.0, 20.0, FakeSharedState())
        == "JUPITER"
    )


@pytest.mark.unit
def test_coordinates_just_outside_tolerance_are_not_a_planet(monkeypatch):
    _stub_planets(monkeypatch, {"JUPITER": (120.0, 20.0)})
    inside = 0.9 * track_freq_policy.PLANET_MATCH_TOLERANCE_DEG
    outside = 1.1 * track_freq_policy.PLANET_MATCH_TOLERANCE_DEG

    assert (
        track_freq_policy.planet_at_coordinates(120.0, 20.0 + inside, FakeSharedState())
        == "JUPITER"
    )
    assert (
        track_freq_policy.planet_at_coordinates(
            120.0, 20.0 + outside, FakeSharedState()
        )
        is None
    )


def _located_shared_state():
    from PiFinder.calc_utils import sf_utils

    shared_state = FakeSharedState()
    location = shared_state.location()
    sf_utils.set_location(location.lat, location.lon, location.altitude)
    return shared_state


@pytest.mark.unit
def test_lx200_coordinate_quantization_still_matches_the_planet():
    """SkySafari's :Sr/:Sd carry 1s of RA and 1" of Dec, so a real GoTo target
    arrives truncated. The match tolerance must absorb that."""
    shared_state = _located_shared_state()
    ra_deg, dec_deg = track_freq_policy.planet_positions_of_date(shared_state)[
        "JUPITER"
    ]

    # Truncate exactly as the LX200 target commands do.
    ra_quantized = math.floor(ra_deg / 15.0 * 3600.0) / 3600.0 * 15.0
    dec_quantized = math.copysign(math.floor(abs(dec_deg) * 3600.0) / 3600.0, dec_deg)

    assert (
        track_freq_policy.planet_at_coordinates(
            ra_quantized, dec_quantized, shared_state
        )
        == "JUPITER"
    )


@pytest.mark.unit
def test_matching_uses_equinox_of_date_not_j2000():
    """LX200 clients speak the mount's frame (equinox of date). Matching a
    SkySafari coordinate against J2000 positions is off by precession -- 22'
    in 2026 -- which silently reset a real Venus GoTo to sidereal."""
    from PiFinder.calc_utils import sf_utils

    shared_state = _located_shared_state()
    dt = track_freq_policy._observation_time(shared_state)
    of_date = track_freq_policy.planet_positions_of_date(shared_state)["VENUS"]
    j2000 = sf_utils.calc_planets(dt)["VENUS"]["radec"]

    separation = track_freq_policy._angular_separation_deg(*of_date, *j2000)
    # The two frames must actually differ, or this test proves nothing.
    assert separation > track_freq_policy.PLANET_MATCH_TOLERANCE_DEG

    assert track_freq_policy.planet_at_coordinates(*of_date, shared_state) == "VENUS"
    assert track_freq_policy.planet_at_coordinates(*j2000, shared_state) != "VENUS"


@pytest.mark.unit
def test_coordinate_policy_feeds_forward_planet_rate(monkeypatch):
    _stub_planets(monkeypatch, {"MOON": (120.0, 20.0)})
    # The stub returns one fixed position, so pin the rate separately; the
    # finite-difference path itself is covered by the real-ephemeris tests.
    monkeypatch.setattr(track_freq_policy, "planet_dra_dt", lambda name, state: 0.55)
    command = track_freq_policy.track_freq_command_for_coordinates(
        120.0, 20.0, FakeSharedState()
    )
    assert command is not None
    assert command["type"] == "set_track_freq"
    assert command["label"] == "Moon"
    assert command["hz"] < nonsidereal.SIDEREAL_FREQ_HZ


@pytest.mark.unit
def test_coordinate_policy_resets_static_target(monkeypatch):
    _stub_planets(monkeypatch, {"JUPITER": (120.0, 20.0)})
    monkeypatch.setattr(
        track_freq_policy, "_mount_status", lambda: {"track_freq_hz": 60.12627}
    )
    command = track_freq_policy.track_freq_command_for_coordinates(
        10.0, -40.0, FakeSharedState()
    )
    assert command == {"type": "reset_track_freq"}


@pytest.mark.unit
def test_unknown_planet_name_leaves_frequency_untouched():
    command = track_freq_policy.track_freq_command_for_target(
        _planet_object("Nibiru"), FakeSharedState()
    )
    assert command is None
