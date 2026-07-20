"""Tests for PiFinder.track_freq_policy (LCD GoTo tracking-frequency policy)."""

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
        track_freq_policy.track_freq_command_for_target(None, FakeSharedState())
        is None
    )


@pytest.mark.unit
def test_unknown_planet_name_leaves_frequency_untouched():
    command = track_freq_policy.track_freq_command_for_target(
        _planet_object("Nibiru"), FakeSharedState()
    )
    assert command is None
