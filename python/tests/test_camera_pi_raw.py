"""Tests for normalizing delivered raw frames to profile bit-depth units.

Pi 5 / CM5 (PiSP frontend) delivers raw only as 16-bit samples with the
sensor's bits MSB-aligned — a SRGGB12 request comes back as SRGGB16 with
values x16 — while Pi 4's Unicam returns true profile-depth values. Every
downstream consumer (bias offsets, the 8-bit stretch, saturation checks,
SQM calibration) assumes profile units, so CameraPI downshifts at capture.
"""

from types import SimpleNamespace

import pytest

from PiFinder.camera_pi import (
    CONTINUOUS_BUFFER_COUNT,
    CameraPI,
    estimate_sensor_drops,
    raw_downshift,
)

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


class FakeCamera:
    def __init__(self):
        self.configuration_kwargs = None
        self.configured = None
        self.controls = []
        self.started = False

    def create_still_configuration(self, main, **kwargs):
        self.configuration_kwargs = {"main": main, **kwargs}
        return self.configuration_kwargs

    def configure(self, configuration):
        self.configured = configuration

    def camera_configuration(self):
        return {"raw": {"format": "SRGGB12"}}

    def set_controls(self, controls):
        self.controls.append(controls)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


def _camera_without_hardware():
    camera = CameraPI.__new__(CameraPI)
    camera.camera = FakeCamera()
    camera.profile = SimpleNamespace(
        raw_size=(1920, 1080), format="SRGGB12", bit_depth=12
    )
    camera.exposure_time = 200_000
    camera.gain = 30.0
    return camera


def test_still_configuration_is_triple_buffered_without_completed_cache():
    camera = _camera_without_hardware()

    camera.initialize()

    assert camera.camera.configuration_kwargs["buffer_count"] == (
        CONTINUOUS_BUFFER_COUNT
    )
    assert camera.camera.configuration_kwargs["queue"] is False
    assert camera.camera.started is True


def test_manual_exposure_and_gain_are_submitted_atomically():
    camera = _camera_without_hardware()
    camera._camera_started = True

    camera.set_camera_config(100_000, 15.0)

    assert camera.camera.controls == [
        {"AeEnable": False, "AnalogueGain": 15.0, "ExposureTime": 100_000}
    ]


@pytest.mark.parametrize(
    ("previous_ns", "current_ns", "duration_us", "expected"),
    [
        (1_000_000_000, 1_200_000_000, 200_000, 0),
        (1_000_000_000, 1_600_000_000, 200_000, 2),
        (None, 1_600_000_000, 200_000, 0),
        (1_000_000_000, 900_000_000, 200_000, 0),
        (1_000_000_000, 1_600_000_000, None, 0),
    ],
)
def test_sensor_drop_estimate_uses_timestamp_intervals(
    previous_ns, current_ns, duration_us, expected
):
    assert estimate_sensor_drops(previous_ns, current_ns, duration_us) == expected


def test_capture_releases_request_when_raw_copy_fails():
    class Request:
        released = False

        def release(self):
            self.released = True

    request = Request()
    camera = _camera_without_hardware()
    camera.camera.capture_request = lambda: request

    def fail_copy(_request):
        raise RuntimeError("copy failed")

    camera._raw_array = fail_copy

    with pytest.raises(RuntimeError, match="copy failed"):
        camera.capture()

    assert request.released is True
