"""Tests for the SSD1333 display auto-detection (BQ25895 I2C marker).

The probe goes through PiFinder.i2c_bus.get_i2c (software I2C bus 3 on
Pi 4 and earlier, hardware bus otherwise), so the tests patch the
module-level ``get_i2c`` seam — not Blinka's ``board``, which the
implementation stopped using when the fork moved to the shared bus
helper (cc7ae95e).
"""

import pytest

from PiFinder import hardware_detect

pytestmark = pytest.mark.unit


class _FakeI2C:
    def __init__(self, addresses):
        self.addresses = addresses
        self.unlocked = False

    def try_lock(self):
        return True

    def scan(self):
        return self.addresses

    def unlock(self):
        self.unlocked = True


def test_default_display_falls_back_without_i2c(monkeypatch):
    monkeypatch.setattr(hardware_detect, "get_i2c", None)
    assert hardware_detect.detect_ssd1333_display() is False
    assert hardware_detect.default_display_hardware() == "ssd1351"


def test_default_display_selects_ssd1333_when_marker_present(monkeypatch):
    i2c = _FakeI2C([hardware_detect.BQ25895_ADDRESS])
    monkeypatch.setattr(hardware_detect, "get_i2c", lambda: i2c)
    assert hardware_detect.detect_ssd1333_display() is True
    assert hardware_detect.default_display_hardware() == "ssd1333"
    assert i2c.unlocked is True


def test_default_display_uses_ssd1351_when_marker_absent(monkeypatch):
    i2c = _FakeI2C([])
    monkeypatch.setattr(hardware_detect, "get_i2c", lambda: i2c)
    assert hardware_detect.detect_ssd1333_display() is False
    assert hardware_detect.default_display_hardware() == "ssd1351"
    assert i2c.unlocked is True


def test_probe_error_falls_back_to_ssd1351(monkeypatch):
    def _broken():
        raise OSError("no I2C bus")

    monkeypatch.setattr(hardware_detect, "get_i2c", _broken)
    assert hardware_detect.detect_ssd1333_display() is False
    assert hardware_detect.default_display_hardware() == "ssd1351"
