#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
Unit tests for the SEP fallback backoff (SepShadowRunner).

A failed fallback solve costs up to solve_timeout (1 s) of solver CPU;
on persistently unsolvable scenes (indoors, thick cloud) that recurs on
every attempt. The backoff skips a growing number of attempts after
consecutive failures, but must re-arm immediately when the SEP count
jumps -- a cloud gap opening on real stars must not wait out a backoff
window.
"""

import numpy as np
import pytest

from PiFinder import solver_frame_map as sfm
from PiFinder.sep_shadow import SepShadowRunner


class DummyShared:
    def __init__(self):
        self._overlay = None

    def sep_overlay(self):
        return self._overlay

    def set_sep_overlay(self, v):
        self._overlay = v


def _runner(tmp_path):
    return SepShadowRunner(
        shadow_enabled=False,
        fallback_enabled=True,
        sigma=3.5,
        rotation_deg=90.0,
        crop_width_px=980,
        csv_path=tmp_path / "shadow.csv",
    )


def _tick(runner, n=1):
    """Advance the per-attempt counter the way detect() does."""
    runner._attempt_counter += n


@pytest.mark.unit
class TestOverlayPublish:
    """The overlay ships once per attempt AFTER the solve outcome, so the
    confirmed/candidate split is never clobbered by the next detect."""

    def test_publish_carries_matched_in_frame_space(self, tmp_path):
        runner = _runner(tmp_path)  # rotation 90
        frame_hw = (64, 48)
        star = np.array([[20.0, 30.0]])
        runner._last_overlay = {
            "centroids": star.tolist(),
            "frame_hw": list(frame_hw),
            "masked": 0,
            "sigma": 4.0,
            "timestamp": 1.0,
        }
        rotated, _ = sfm.rotate_centroids(star, frame_hw, 90.0)
        runner._attach_matched_overlay(
            {"RA": 1.0, "matched_centroids": rotated.tolist()}
        )
        shared = DummyShared()
        runner.publish_overlay(shared)
        entry = shared.sep_overlay()
        assert entry is not None
        my, mx = entry["matched"][0]
        assert abs(my - 20.0) < 1e-6 and abs(mx - 30.0) < 1e-6

        # entry is consumed: a second publish must not re-ship stale data
        shared.set_sep_overlay(None)
        runner.publish_overlay(shared)
        assert shared.sep_overlay() is None

    def test_failed_solve_publishes_candidates_only(self, tmp_path):
        runner = _runner(tmp_path)
        runner._last_overlay = {
            "centroids": [[20.0, 30.0]],
            "frame_hw": [64, 48],
            "masked": 0,
            "sigma": 4.0,
            "timestamp": 1.0,
        }
        runner._attach_matched_overlay({"RA": None})
        shared = DummyShared()
        runner.publish_overlay(shared)
        assert "matched" not in shared.sep_overlay()


@pytest.mark.unit
class TestFallbackBackoff:
    def test_first_attempt_always_allowed(self, tmp_path):
        runner = _runner(tmp_path)
        _tick(runner)
        assert runner.fallback_should_attempt(28) is True

    def test_failures_open_growing_skip_windows(self, tmp_path):
        runner = _runner(tmp_path)
        _tick(runner)
        runner.record_fallback_result(False, 28)
        # streak 1 -> skip 2 attempts
        _tick(runner)
        assert runner.fallback_should_attempt(28) is False
        _tick(runner)
        assert runner.fallback_should_attempt(28) is True
        runner.record_fallback_result(False, 28)
        # streak 2 -> skip 4 attempts
        _tick(runner, 3)
        assert runner.fallback_should_attempt(28) is False
        _tick(runner)
        assert runner.fallback_should_attempt(28) is True

    def test_skip_window_caps_at_eight_attempts(self, tmp_path):
        runner = _runner(tmp_path)
        for _ in range(10):  # streak far past the cap
            _tick(runner)
            runner.record_fallback_result(False, 28)
        _tick(runner, 8)
        assert runner.fallback_should_attempt(28) is True

    def test_sep_count_jump_rearms_immediately(self, tmp_path):
        """Cloud gap opens on stars: masked count jumps 5 -> 30. The
        rescue solve must run right away, not wait out the window."""
        runner = _runner(tmp_path)
        _tick(runner)
        runner.record_fallback_result(False, 20)
        _tick(runner)
        assert runner.fallback_should_attempt(20) is False
        assert runner.fallback_should_attempt(30) is True  # >= 1.5x

    def test_success_and_production_solve_clear_the_streak(self, tmp_path):
        runner = _runner(tmp_path)
        _tick(runner)
        runner.record_fallback_result(False, 28)
        runner.record_fallback_result(True, 28)
        _tick(runner)
        assert runner.fallback_should_attempt(28) is True

        runner.record_fallback_result(False, 28)
        runner.note_solved()
        _tick(runner)
        assert runner.fallback_should_attempt(28) is True
