"""Coverage for isolated original-resolution wide-field helpers."""

import numpy as np
import pytest

from PiFinder.mf_wide_consensus import MFWideAttitudeCandidate, build_consensus
from PiFinder.mf_wide_tiles import (
    crop_tile,
    optimal_tile_size_px,
    plan_tiles_for_focal,
    plan_vertical_recovery_tiles,
    plan_wide_tiles,
)


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


def test_tile_size_targets_solver_fov_without_ever_upscaling_a_small_crop():
    # IMX462's 980px central crop: 6mm remains at the 512px floor, 10mm
    # reaches the ~11.6-degree target, and 16mm stays at its calibrated crop.
    assert optimal_tile_size_px(26.6481, 980) == 512
    assert optimal_tile_size_px(16.1752, 980) == 702
    assert optimal_tile_size_px(10.4028, 980) == 980


def test_plan_uses_lens_specific_size_for_both_grid_strategies():
    six = plan_tiles_for_focal(
        (1080, 1920), 26.6481, 10.0, 6.0, central_tile_size_px=980
    )
    wide = plan_tiles_for_focal(
        (1080, 1920), 20.1442, 10.0, 8.0, central_tile_size_px=980
    )
    recovery = plan_tiles_for_focal(
        (1080, 1920), 16.1752, 10.0, 10.0, central_tile_size_px=980
    )

    assert {tile.rect.width for tile in six.tiles} == {640}
    assert {tile.rect.height for tile in six.tiles} == {640}
    assert len(six.tiles) == 15
    assert {tile.rect.width for tile in wide.tiles} == {564}
    assert {tile.rect.height for tile in wide.tiles} == {564}
    assert {tile.rect.width for tile in recovery.tiles} == {702}
    assert {tile.rect.height for tile in recovery.tiles} == {702}


def test_six_mm_field_size_does_not_leak_when_lens_changes():
    six = plan_tiles_for_focal(
        (1080, 1920), 26.6481, 10.0, 6.0, central_tile_size_px=980
    )
    manual_neighbour = plan_tiles_for_focal(
        (1080, 1920), 25.0, 10.0, 6.1, central_tile_size_px=980
    )
    eight = plan_tiles_for_focal(
        (1080, 1920), 20.1442, 10.0, 8.0, central_tile_size_px=980
    )

    assert six.central_tile.rect.width == 640
    assert manual_neighbour.central_tile.rect.width == 512
    assert eight.central_tile.rect.width == 564


def test_normal_lenses_use_a_central_crop_sized_three_by_three_recovery_grid():
    plan = plan_vertical_recovery_tiles((1080, 1920), 12.0, tile_size_px=980)

    assert plan.strategy == "recovery_grid"
    assert [tile.tile_id for tile in plan.tiles] == [
        "UL",
        "U",
        "UR",
        "L",
        "C",
        "R",
        "DL",
        "D",
        "DR",
    ]
    assert all(tile.rect.width == tile.rect.height == 980 for tile in plan.tiles)
    assert plan.central_tile.rect.x + plan.central_tile.rect.width / 2 == 1920 / 2
    assert plan.central_tile.rect.y + plan.central_tile.rect.height / 2 == 1080 / 2
    assert {tile.rect.x for tile in plan.tiles} == {0, 470, 940}
    assert {tile.rect.y for tile in plan.tiles} == {0, 50, 100}
    assert plan_tiles_for_focal(
        (1080, 1920), 12.0, 10.0, 16.0, central_tile_size_px=980
    ).strategy == ("recovery_grid")


def test_tile_ids_follow_display_rotation_in_video_udlr_coordinates():
    plan = plan_vertical_recovery_tiles(
        (1080, 1920),
        12.0,
        tile_size_px=980,
        display_rotation_degrees=90,
    )

    # The raw-frame E crop is the visible video's U crop after 90° CCW.
    top = next(tile for tile in plan.tiles if tile.tile_id == "U")
    assert (top.row, top.column) == (1, 2)
    assert top.rect.x == 940
    assert top.rect.y == 50


def test_vertical_recovery_keeps_squares_on_a_portrait_sensor_frame():
    plan = plan_vertical_recovery_tiles((1920, 1080), 12.0, tile_size_px=980)

    assert len(plan.tiles) == 9
    assert all(tile.rect.width == tile.rect.height == 980 for tile in plan.tiles)
    assert plan.central_tile.rect.x + plan.central_tile.rect.width / 2 == 1080 / 2
    assert plan.central_tile.rect.y + plan.central_tile.rect.height / 2 == 1920 / 2


def test_recovery_grid_rejects_a_requested_square_larger_than_either_axis():
    with pytest.raises(ValueError, match="too small"):
        plan_vertical_recovery_tiles((900, 1920), 12.0, tile_size_px=980)


def test_two_tile_consensus_requires_adjacent_matching_pair():
    candidates = [
        MFWideAttitudeCandidate("C", 10.0, 20.0, 30.0, 20, 0.1),
        MFWideAttitudeCandidate("R", 10.01, 20.0, 30.02, 18, 0.1),
    ]
    result = build_consensus(
        candidates,
        adjacent=lambda left, right: {left, right} == {"C", "R"},
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
        MFWideAttitudeCandidate("R", 12.0, 20.0, 30.0, 20, 0.1),
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
