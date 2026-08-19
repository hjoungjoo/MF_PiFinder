"""Offline unit coverage for the passive optical-train foundation."""

import pytest

from PiFinder.optics import (
    LENSES,
    build_optical_train,
    get_lens,
    identify_lens_from_fitted_fov,
)
from PiFinder.sqm.camera_profiles import get_camera_profile


@pytest.mark.unit
def test_default_train_keeps_existing_16mm_geometry():
    train = build_optical_train("imx462")
    assert train.lens.key == "16mm"
    assert train.lens_stated is False
    assert train.fov_degrees == pytest.approx(10.38, abs=0.03)


@pytest.mark.unit
def test_colour_variant_inherits_sensor_geometry():
    assert build_optical_train("imx296_color").fov_degrees == pytest.approx(
        build_optical_train("imx296").fov_degrees
    )


@pytest.mark.unit
def test_explicit_lens_changes_only_the_calculated_train():
    default = build_optical_train("imx462")
    wide = build_optical_train("imx462", "12mm")
    assert wide.lens_stated is True
    assert wide.fov_degrees > default.fov_degrees


@pytest.mark.unit
def test_fitted_fov_identifies_only_supported_close_lens():
    profile = get_camera_profile("imx462")
    assert identify_lens_from_fitted_fov(profile, 10.40) == "16mm"
    assert identify_lens_from_fitted_fov(profile, 7.0) is None


@pytest.mark.unit
def test_lens_registry_rejects_unknown_key():
    assert LENSES["16mm"].menu_label == "16mm"
    with pytest.raises(ValueError):
        get_lens("not-a-lens")
