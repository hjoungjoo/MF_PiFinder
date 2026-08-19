"""Coverage for isolated original-resolution wide-field helpers."""

import numpy as np
import pytest

from PiFinder.mf_wide_consensus import MFWideAttitudeCandidate, build_consensus
from PiFinder.mf_wide_tiles import crop_tile, plan_wide_tiles


pytestmark = pytest.mark.unit


def test_tile_plan_has_central_original_resolution_crop():
    plan = plan_wide_tiles((1080, 1920), 39.12, 10.4028)
    central = plan.central_tile
    frame = np.arange(1080 * 1920, dtype=np.uint16).reshape(1080, 1920)
    cropped = crop_tile(frame, central)

    assert central.tile_id == "C"
    assert cropped.shape == (central.rect.height, central.rect.width)
    assert cropped[0, 0] == frame[central.rect.y, central.rect.x]
    assert central.rect.width < frame.shape[1]


def test_wider_lens_produces_more_16mm_equivalent_tiles():
    four = plan_wide_tiles((1080, 1920), 39.12, 10.4028)
    eight = plan_wide_tiles((1080, 1920), 20.14, 10.4028)
    assert len(four.tiles) > len(eight.tiles)


def test_two_tile_consensus_requires_adjacent_matching_pair():
    candidates = [
        MFWideAttitudeCandidate("C", 10.0, 20.0, 30.0, 20, 0.1),
        MFWideAttitudeCandidate("E", 10.01, 20.0, 30.02, 18, 0.1),
    ]
    result = build_consensus(
        candidates,
        adjacent=lambda left, right: {left, right} == {"C", "E"},
        pair_position_limit_deg=0.05,
        pair_roll_limit_deg=0.1,
        multi_position_limit_deg=0.2,
        multi_roll_limit_deg=0.3,
    )
    assert result is not None
    assert result.method == "adjacent_pair"


def test_pair_or_single_outside_safety_limits_is_not_published():
    candidates = [
        MFWideAttitudeCandidate("C", 10.0, 20.0, 30.0, 20, 0.1),
        MFWideAttitudeCandidate("E", 12.0, 20.0, 30.0, 20, 0.1),
    ]
    assert (
        build_consensus(
            candidates,
            adjacent=lambda _left, _right: True,
            pair_position_limit_deg=0.05,
            pair_roll_limit_deg=0.1,
            multi_position_limit_deg=0.2,
            multi_roll_limit_deg=0.3,
        )
        is None
    )
