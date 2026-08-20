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


def test_tile_plan_uses_odd_overlapping_512px_squares_with_an_exact_centre_tile():
    plan = plan_wide_tiles((1080, 1920), 39.12, 10.4028)
    rows = sorted({tile.row for tile in plan.tiles})
    columns = sorted({tile.column for tile in plan.tiles})
    central = plan.central_tile

    assert len(rows) % 2 == 1
    assert len(columns) % 2 == 1
    assert central.row == len(rows) // 2
    assert central.column == len(columns) // 2
    assert central.rect.x + central.rect.width / 2 == 1920 / 2
    assert central.rect.y + central.rect.height / 2 == 1080 / 2
    assert {tile.rect.width for tile in plan.tiles} == {512}
    assert {tile.rect.height for tile in plan.tiles} == {512}
    assert plan.overlap == 0.20
    assert any(
        left.rect.x < right.rect.x_end and right.rect.x < left.rect.x_end
        for left in plan.tiles
        for right in plan.tiles
        if left.row == right.row and left.column < right.column
    )


def test_tile_count_is_independent_of_lens_fov():
    four = plan_wide_tiles((1080, 1920), 39.12, 10.4028)
    eight = plan_wide_tiles((1080, 1920), 20.14, 10.4028)
    assert len(four.tiles) == len(eight.tiles)


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
