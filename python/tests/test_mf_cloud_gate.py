"""Unit tests for the isolated MF structured-cloud candidate gate."""

import numpy as np
import pytest

from PiFinder.mf_cloud_gate import (
    select_clear_window_candidates,
    wide_cloud_gate_enabled,
)


pytestmark = pytest.mark.unit


def test_gate_is_limited_to_sub_ten_mm_optics():
    assert wide_cloud_gate_enabled("6mm", None) is True
    assert wide_cloud_gate_enabled("8mm", None) is True
    assert wide_cloud_gate_enabled("10mm", None) is False
    assert wide_cloud_gate_enabled("16mm", None) is False
    assert wide_cloud_gate_enabled("manual", 6.5) is True
    assert wide_cloud_gate_enabled("manual", 10.0) is False


def test_uniform_background_passes_every_candidate_unchanged():
    background = np.full((32, 32), 1200.0)
    points = np.array([[2.0, 3.0], [20.0, 21.0]])
    result = select_clear_window_candidates(background, points, enabled=True)

    assert result.active is False
    assert result.keep.tolist() == [True, True]
    assert result.background_limit is None


def test_structured_cloud_keeps_only_dark_clear_window_candidates():
    background = np.linspace(1000.0, 4000.0, 100).reshape(10, 10)
    points = np.array([[0.0, 0.0], [1.0, 5.0], [5.0, 5.0], [9.0, 9.0]])
    result = select_clear_window_candidates(background, points, enabled=True)

    assert result.active is True
    assert result.contrast > 0.55
    assert result.keep.tolist() == [True, True, False, False]
    assert result.background_limit == pytest.approx(np.percentile(background, 20))


def test_disabled_or_invalid_gate_fails_open():
    points = np.array([[1.0, 1.0], [2.0, 2.0]])
    disabled = select_clear_window_candidates(
        np.arange(100.0).reshape(10, 10), points, enabled=False
    )
    invalid = select_clear_window_candidates(np.array([1.0, 2.0]), points, enabled=True)

    assert disabled.keep.tolist() == [True, True]
    assert invalid.keep.tolist() == [True, True]


def test_invalid_candidate_shape_does_not_raise():
    result = select_clear_window_candidates(
        np.ones((8, 8)), np.array([1.0, 2.0, 3.0]), enabled=True
    )

    assert result.active is False
