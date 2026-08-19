"""Offline coverage for the non-active full-frame optical FOV hook."""

import pytest

from PiFinder import solver_frame_map as sfm


@pytest.mark.unit
def test_default_fullframe_fov_is_the_existing_twelve_degree_mapping():
    assert sfm.fov_estimate_deg(980, 980) == pytest.approx(12.0)
    assert sfm.fov_estimate_deg(1920, 980) == pytest.approx(23.510204)


@pytest.mark.unit
def test_fullframe_fov_scales_from_an_explicit_crop_fov():
    assert sfm.fov_estimate_deg(980, 980, 10.38) == pytest.approx(10.38)
    assert sfm.fov_estimate_deg(1920, 980, 10.38) == pytest.approx(20.3363265)


@pytest.mark.unit
@pytest.mark.parametrize("crop, base", [(0, 12.0), (980, 0.0), (980, -1.0)])
def test_fullframe_fov_rejects_invalid_geometry(crop, base):
    with pytest.raises(ValueError):
        sfm.fov_estimate_deg(1920, crop, base)
