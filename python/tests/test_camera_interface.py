#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
Unit tests for CameraInterface._capture_with_timeout - the hot-path capture
guard that keeps a wedged V4L2 capture from freezing the camera process
without ever overlapping two captures on a non-thread-safe camera.
"""

import threading
from typing import Optional, Tuple

import pytest
from PIL import Image

from PiFinder.camera_interface import CameraInterface


class _ScriptedCamera(CameraInterface):
    """A CameraInterface whose capture() behaviour the test drives.

    capture() optionally blocks on ``gate`` (simulating a wedged driver) and
    counts how many times it was actually entered, so tests can assert that the
    guard never launches an overlapping capture.
    """

    def __init__(self):
        self.capture_calls = 0
        self.gate = threading.Event()  # capture() waits on this when blocking
        self.blocking = False
        self.raise_exc: Optional[Exception] = None

    def capture(self) -> Image.Image:
        self.capture_calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.blocking:
            # Simulate a hung capture. The safety timeout means a misbehaving
            # test can never wedge pytest itself.
            self.gate.wait(timeout=5)
        return Image.new("L", (512, 512), 1)


@pytest.mark.unit
class TestCaptureWithTimeout:
    """Tests for the hot-path capture timeout + overlap guard."""

    def test_returns_image_when_capture_completes(self):
        """A capture that returns in time is passed straight through."""
        cam = _ScriptedCamera()
        image = cam._capture_with_timeout(timeout=1)
        assert isinstance(image, Image.Image)
        assert cam.capture_calls == 1
        # Handle is cleared so the next frame starts fresh.
        assert cam._capture_thread is None

    def test_propagates_capture_exception(self):
        """A real capture error surfaces in the caller's thread, not swallowed."""
        cam = _ScriptedCamera()
        cam.raise_exc = ValueError("sensor boom")
        with pytest.raises(ValueError, match="sensor boom"):
            cam._capture_with_timeout(timeout=1)
        # The worker finished (it raised), so nothing is left tracked.
        assert cam._capture_thread is None

    def test_returns_none_on_timeout(self):
        """A wedged capture times out to None (caller uses a blank frame)."""
        cam = _ScriptedCamera()
        cam.blocking = True
        try:
            assert cam._capture_with_timeout(timeout=0.25) is None
            # The stuck capture is kept tracked and still running.
            assert cam._capture_thread is not None
            assert cam._capture_thread.is_alive()
        finally:
            cam.gate.set()
            if cam._capture_thread is not None:
                cam._capture_thread.join(timeout=5)

    def test_guard_prevents_overlapping_capture(self):
        """While one capture is wedged, a second call must not start another.

        This is the core fix: piling concurrent capture_request() calls onto a
        non-thread-safe camera is worse than the freeze the timeout avoids.
        """
        cam = _ScriptedCamera()
        cam.blocking = True
        try:
            # First frame wedges and times out.
            assert cam._capture_with_timeout(timeout=0.25) is None
            wedged_thread = cam._capture_thread
            assert wedged_thread is not None and wedged_thread.is_alive()
            assert cam.capture_calls == 1

            # Second frame, still wedged: returns None immediately WITHOUT
            # entering capture() again (no overlap) and without spawning a
            # new thread.
            assert cam._capture_with_timeout(timeout=0.25) is None
            assert cam.capture_calls == 1
            assert cam._capture_thread is wedged_thread
        finally:
            cam.gate.set()
            if cam._capture_thread is not None:
                cam._capture_thread.join(timeout=5)

    def test_recovers_after_stuck_capture_clears(self):
        """Once the wedged capture returns, normal captures resume."""
        cam = _ScriptedCamera()
        cam.blocking = True
        # Wedge, time out, then let the stuck capture finish.
        assert cam._capture_with_timeout(timeout=0.25) is None
        wedged_thread = cam._capture_thread
        cam.gate.set()
        assert wedged_thread is not None
        wedged_thread.join(timeout=5)

        # gate is now set, so a fresh capture returns immediately. The guard
        # sees the old thread is no longer alive and starts a new capture.
        image = cam._capture_with_timeout(timeout=1)
        assert isinstance(image, Image.Image)
        assert cam.capture_calls == 2
        assert cam._capture_thread is not wedged_thread
        assert cam._capture_thread is None


class _GainCamera(CameraInterface):
    """A CameraInterface that records what set_camera_config() was handed."""

    def __init__(self, profile_gain: float = 30.0):
        self.exposure_time = 400000
        self.gain = profile_gain
        self._profile_gain = profile_gain
        self.applied: list = []

    def get_default_gain(self) -> float:
        return self._profile_gain

    def set_camera_config(
        self, exposure_time: float, gain: float
    ) -> Tuple[float, float]:
        self.applied.append((exposure_time, gain))
        return exposure_time, gain


class _FakeConfig:
    def __init__(self, values: dict):
        self._values = values

    def get_option(self, option, default=None):
        return self._values.get(option, default)


@pytest.mark.unit
class TestRestoreSavedGain:
    """The saved gain has to come back after a restart.

    Gain is a global camera setting the Camera Gain menu and the web LiveCam
    page both write; before this it lived only in the camera process, so every
    restart silently reverted it to the camera profile default.
    """

    def test_saved_gain_is_applied(self):
        cam = _GainCamera()
        assert cam.restore_saved_gain(_FakeConfig({"camera_gain": 4})) == 4.0
        assert cam.gain == 4.0
        assert cam.applied == [(400000, 4.0)]

    def test_profile_keeps_backend_default(self):
        """A "profile" value is a reference, not a number: leave the backend alone."""
        cam = _GainCamera()
        assert cam.restore_saved_gain(_FakeConfig({"camera_gain": "profile"})) is None
        assert cam.gain == 30.0
        assert cam.applied == []

    def test_missing_value_keeps_backend_default(self):
        cam = _GainCamera()
        assert cam.restore_saved_gain(_FakeConfig({})) is None
        assert cam.applied == []

    def test_invalid_value_is_ignored(self):
        cam = _GainCamera()
        assert cam.restore_saved_gain(_FakeConfig({"camera_gain": "bogus"})) is None
        assert cam.gain == 30.0
        assert cam.applied == []
