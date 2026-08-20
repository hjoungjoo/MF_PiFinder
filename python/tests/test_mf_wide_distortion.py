"""Tests for native-coordinate MF Brown--Conrady centroid correction."""

import numpy as np
import pytest

from PiFinder.mf_wide_distortion import active_coefficients, undistort_global_centroids


pytestmark = pytest.mark.unit


def test_invalid_profile_never_activates_a_correction():
    assert active_coefficients(None) is None
    assert active_coefficients({"model": "none"}) is None
    assert (
        active_coefficients({"model": "brown_conrady", "coefficients": {"k1": "bad"}})
        is None
    )


def test_zero_profile_preserves_native_centroids():
    points = np.array([[10.0, 20.0], [300.0, 400.0]])
    corrected = undistort_global_centroids(
        points, (512, 512), {"k1": 0.0, "k2": 0.0, "k3": 0.0, "p1": 0.0, "p2": 0.0}
    )
    assert np.allclose(corrected, points)


def test_barrel_profile_moves_an_edge_centroid_outward_when_undistorting():
    point = np.array([[255.5, 500.0]])
    corrected = undistort_global_centroids(
        point, (512, 512), {"k1": -0.1, "k2": 0.0, "k3": 0.0, "p1": 0.0, "p2": 0.0}
    )
    assert corrected[0, 1] > point[0, 1]
