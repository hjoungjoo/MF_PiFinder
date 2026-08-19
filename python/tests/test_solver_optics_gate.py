"""Unit coverage for the opt-in ordinary-solver optical FOV gate."""

import pytest

from PiFinder import solver


class _State:
    def __init__(self, camera_type, lens_key):
        self._camera_type = camera_type
        self._lens_key = lens_key

    def camera_type(self):
        return self._camera_type

    def camera_lens(self):
        return self._lens_key


@pytest.mark.unit
def test_stated_sixteen_mm_uses_its_derived_gate():
    estimate, error = solver._optical_fov_gate_params(_State("imx462_color", "16mm"))
    assert estimate == pytest.approx(10.4028, abs=0.001)
    assert error == pytest.approx(estimate * 0.15)


@pytest.mark.unit
def test_unstated_lens_spans_the_supported_imx462_lenses():
    estimate, error = solver._optical_fov_gate_params(_State("imx462", ""))
    assert estimate - error <= 10.4028 * 0.85
    assert estimate + error >= 12.4382 * 1.15


@pytest.mark.unit
def test_bad_camera_keeps_the_legacy_gate():
    assert solver._optical_fov_gate_params(_State("not-a-camera", "16mm")) == (
        12.0,
        4.0,
    )


@pytest.mark.unit
def test_stated_sixteen_mm_crop_fov_is_used_by_fullframe_stage():
    assert solver._optical_crop_fov(_State("imx462_color", "16mm")) == pytest.approx(
        10.4028, abs=0.001
    )


@pytest.mark.unit
def test_bad_fullframe_state_keeps_legacy_crop_fov():
    assert solver._optical_crop_fov(_State("not-a-camera", "16mm")) == pytest.approx(
        12.0
    )
