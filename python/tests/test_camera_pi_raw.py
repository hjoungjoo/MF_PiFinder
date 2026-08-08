"""Tests for normalizing delivered raw frames to profile bit-depth units.

Pi 5 / CM5 (PiSP frontend) delivers raw only as 16-bit samples with the
sensor's bits MSB-aligned — a SRGGB12 request comes back as SRGGB16 with
values x16 — while Pi 4's Unicam returns true profile-depth values. Every
downstream consumer (bias offsets, the 8-bit stretch, saturation checks,
SQM calibration) assumes profile units, so CameraPI downshifts at capture.
"""

import pytest

from PiFinder.camera_pi import raw_downshift

pytestmark = pytest.mark.unit


def test_pisp_msb_aligned_16bit_is_shifted_down_to_profile_depth():
    assert raw_downshift("SRGGB16", 12) == 4  # Pi 5, imx462/imx290
    assert raw_downshift("R16", 10) == 6  # Pi 5, mono imx296
    assert raw_downshift("SBGGR16", 10) == 6  # Pi 5, colour imx296


def test_true_profile_depth_formats_pass_through_unshifted():
    assert raw_downshift("SRGGB12", 12) == 0  # Pi 4 Unicam
    assert raw_downshift("R10", 10) == 0
    # Packed CSI2P names carry the sensor depth as their first digit run.
    assert raw_downshift("SRGGB12_CSI2P", 12) == 0


def test_unparseable_formats_are_treated_as_profile_depth():
    assert raw_downshift("", 12) == 0
    assert raw_downshift("MONO", 12) == 0


def test_a_shallower_delivery_never_shifts_negative():
    assert raw_downshift("SRGGB10", 12) == 0
