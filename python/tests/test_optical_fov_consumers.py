"""Optical-train FOV consumers: SQM, sweep provenance and chart geometry."""

import json

import pytest

from PiFinder.optics import OpticalTrainResolver, build_optical_train
from PiFinder.plot import frustum_box
from PiFinder.sqm.camera_profiles import get_camera_profile
from PiFinder.sqm.radiometer import radiometric_sqm
from PiFinder.sqm.save_sweep_metadata import save_sweep_metadata


@pytest.mark.unit
def test_resolver_rebuilds_when_lens_or_camera_changes():
    resolver = OpticalTrainResolver()
    default = resolver.resolve("imx462", "16mm")
    assert resolver.resolve("imx462", "16mm") is default
    wide = resolver.resolve("imx462", "12mm")
    assert wide is not default
    assert wide.fov_degrees > default.fov_degrees


@pytest.mark.unit
def test_radiometer_uses_the_passed_optical_train_width():
    profile = get_camera_profile("imx462")
    sample = {
        "exposure_sec": 1.0,
        "background_per_pixel": profile.bias_offset + 500.0,
        "pixels_per_side": 512,
    }
    default, default_details = radiometric_sqm(sample, profile)
    wide_fov = build_optical_train("imx462", "12mm").fov_degrees
    wide, wide_details = radiometric_sqm(sample, profile, field_width_degrees=wide_fov)

    assert default is not None and wide is not None
    assert default_details["radiometric_fov_degrees"] == pytest.approx(
        build_optical_train("imx462").fov_degrees
    )
    assert wide_details["radiometric_fov_degrees"] == pytest.approx(wide_fov)
    assert wide > default  # wider field means lower flux density and darker SQM


@pytest.mark.unit
def test_sweep_metadata_records_the_configured_lens(tmp_path):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    save_sweep_metadata(
        sweep_dir=sweep_dir,
        observer_lat=37.5,
        observer_lon=127.1,
        camera_type="imx462",
        lens_key="12mm",
    )
    data = json.loads((sweep_dir / "sweep_metadata.json").read_text())
    assert data["camera"]["lens"] == "12mm"
    assert data["camera"]["radiometric_fov_degrees"] == pytest.approx(
        build_optical_train("imx462", "12mm").fov_degrees
    )


@pytest.mark.unit
def test_chart_frustum_tracks_the_declared_camera_field():
    size = (128, 128)
    assert frustum_box(size, 10.2, None) is None
    narrow = frustum_box(size, 20.0, build_optical_train("imx462", "16mm").fov_degrees)
    wide = frustum_box(size, 20.0, build_optical_train("imx462", "12mm").fov_degrees)
    assert narrow is not None and wide is not None
    assert (wide[2] - wide[0]) > (narrow[2] - narrow[0])
