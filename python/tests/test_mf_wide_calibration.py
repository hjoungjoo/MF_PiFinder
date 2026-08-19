"""Unit coverage for the isolated MF wide-lens calibration store."""

import pytest

from PiFinder.mf_wide_calibration import (
    CALIBRATION_STORE_OPTION,
    CalibrationProfileStore,
    ManualTvDistortion,
    build_manual_tv_profile,
    initial_k1_from_tv,
)
from PiFinder.sqm.camera_profiles import get_camera_profile


pytestmark = pytest.mark.unit


class _Config:
    def __init__(self):
        self.values = {}

    def get_option(self, key, default=None):
        return self.values.get(key, default)

    def set_option(self, key, value):
        self.values[key] = value


def _tv(direction="barrel"):
    return ManualTvDistortion(
        tv_distortion_percent=10.0,
        direction=direction,
        reference_image_height_mm=8.0,
        reference_kind="semi_height",
        source_note="datasheet p. 3",
    )


def test_manual_tv_scales_for_the_smaller_sensor_footprint():
    profile = get_camera_profile("imx462")
    k1 = initial_k1_from_tv(profile, _tv())
    assert k1 < 0
    assert abs(k1) < 0.10


def test_manual_tv_profile_keeps_input_and_zeroes_unknown_terms():
    profile = get_camera_profile("imx462")
    result = build_manual_tv_profile("imx462", "4mm", profile, _tv(), revision=1)
    assert result["source"] == "manual_tv"
    assert result["provisional"] is True
    assert result["coefficients"]["k1"] < 0
    assert result["coefficients"]["k2"] == 0
    assert result["tv_input"]["source_note"] == "datasheet p. 3"


def test_completed_manual_profile_persists_and_restores_for_same_lens():
    cfg = _Config()
    profile = get_camera_profile("imx462")
    store = CalibrationProfileStore(cfg)
    saved = store.save_manual_tv("imx462", "4mm", profile, _tv())

    restored = CalibrationProfileStore(cfg).load_active("imx462", "4mm", profile)
    assert restored is not None
    assert restored["id"] == saved["id"]
    assert CALIBRATION_STORE_OPTION in cfg.values


def test_calibration_is_not_reused_after_lens_change():
    cfg = _Config()
    profile = get_camera_profile("imx462")
    store = CalibrationProfileStore(cfg)
    store.save_manual_tv("imx462", "4mm", profile, _tv())

    assert store.load_active("imx462", "6mm", profile) is None


def test_automatic_profile_uses_the_same_persistent_restore_path():
    cfg = _Config()
    profile = get_camera_profile("imx462")
    store = CalibrationProfileStore(cfg)
    automatic = build_manual_tv_profile("imx462", "4mm", profile, _tv(), revision=3)
    automatic["id"] = "auto-imx462-4mm-3"
    automatic["source"] = "auto_sky"
    automatic["provisional"] = False

    store.save_profile("imx462", "4mm", profile, automatic)

    restored = CalibrationProfileStore(cfg).load_active("imx462", "4mm", profile)
    assert restored is not None
    assert restored["id"] == "auto-imx462-4mm-3"


def test_tv_requires_reference_geometry_and_direction():
    profile = get_camera_profile("imx462")
    bad = ManualTvDistortion(2.0, "unknown", 0.0, "semi_height")
    with pytest.raises(ValueError):
        initial_k1_from_tv(profile, bad)
