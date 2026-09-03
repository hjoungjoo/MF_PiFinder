"""Offline quality gates for the on-sky distortion measurement session."""

import numpy as np
import pytest

from PiFinder.mf_distortion_calibration import (
    DistortionCalibrationSession,
    DistortionFrameMeasurement,
    measure_distortion_frame,
)


pytestmark = pytest.mark.unit


def _matched_field():
    return np.asarray(
        [
            (500, 750),
            (510, 760),
            (490, 740),
            (500, 400),
            (500, 1100),
            (150, 750),
            (850, 750),
            (250, 500),
            (750, 1000),
            (500, 50),
            (500, 1450),
            (100, 100),
            (100, 1400),
            (900, 100),
            (900, 1400),
            (250, 50),
            (750, 1450),
            (50, 300),
        ],
        dtype=np.float64,
    )


class _Tetra:
    def __init__(
        self,
        *,
        matched=None,
        replay_rmses=(150, 110, 80, 65, 60, 70, 80, 95, 120, 150, 170),
    ):
        self.calls = []
        self.matched = _matched_field() if matched is None else matched
        self.replay_rmses = replay_rmses

    def solve_from_centroids(self, centroids, canvas, **kwargs):
        self.calls.append((np.asarray(centroids), canvas, kwargs))
        return {
            "RA": 42.0,
            "Dec": 21.0,
            "Matches": len(self.matched),
            "RMSE": self.replay_rmses[len(self.calls) - 1],
            "matched_centroids": self.matched,
        }


def _candidates(count=50):
    y = np.linspace(20.0, 980.0, count)
    x = np.linspace(20.0, 1480.0, count)
    return np.column_stack((y, x))


def test_frame_fit_requires_candidates_before_using_tetra():
    t3 = _Tetra()
    result = measure_distortion_frame(
        t3,
        _candidates(20),
        (1000, 1500),
        rotation_deg=0,
        crop_width_px=1500,
        base_fov_degrees=30.0,
    )

    assert result.accepted is False
    assert result.reason == "not_enough_candidates"
    assert t3.calls == []


def test_frame_fit_searches_and_replay_validates_brown_distortion():
    t3 = _Tetra()
    result = measure_distortion_frame(
        t3,
        _candidates(),
        (1000, 1500),
        rotation_deg=0,
        crop_width_px=1500,
        base_fov_degrees=30.0,
    )

    assert result.accepted is True
    assert result.measurement is not None
    assert result.measurement.k1 == pytest.approx(-0.15)
    assert result.measurement.radial_bins["edge"] >= 3
    assert len(t3.calls) == 11
    assert all(call[2]["distortion"] == 0.0 for call in t3.calls)


def test_frame_fit_rejects_matches_without_full_field_coverage():
    t3 = _Tetra(matched=np.tile([[500.0, 750.0]], (18, 1)))
    result = measure_distortion_frame(
        t3,
        _candidates(),
        (1000, 1500),
        rotation_deg=0,
        crop_width_px=1500,
        base_fov_degrees=30.0,
    )

    assert result.accepted is False
    assert result.reason == "not_enough_field_coverage"


def test_frame_fit_requires_a_material_replay_improvement():
    t3 = _Tetra(replay_rmses=(160, 150, 140, 130, 120, 110, 105, 103, 101, 100, 120))
    result = measure_distortion_frame(
        t3,
        _candidates(),
        (1000, 1500),
        rotation_deg=0,
        crop_width_px=1500,
        base_fov_degrees=30.0,
    )

    assert result.accepted is False
    assert result.reason == "no_rmse_improvement"


def test_session_saves_only_after_five_stable_frames():
    session = DistortionCalibrationSession("imx462_color", "6mm", 123)
    for index, k1 in enumerate((-0.041, -0.043, -0.042, -0.044, -0.0425)):
        session.samples.append(
            DistortionFrameMeasurement(
                k1=k1,
                candidates=50,
                matches=20,
                radial_bins={"central": 3, "mid": 8, "edge": 5},
                rmse_before_arcsec=100.0 + index,
                rmse_after_arcsec=60.0 + index,
                fitted_rmse_arcsec=55.0,
                ra=42.0,
                dec=21.0,
            )
        )

    assert session.ready() is True
    coefficients, summary = session.profile_values()
    assert coefficients["k1"] == pytest.approx(-0.0425)
    assert summary["frames"] == 5
    assert summary["radial_bins"] == {"central": 15, "mid": 40, "edge": 25}
