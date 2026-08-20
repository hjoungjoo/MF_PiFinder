"""Unit coverage for the isolated opt-in wide tile execution policy."""

import numpy as np
import pytest

from PiFinder.mf_wide_solver import solve_wide_tiles, wide_solver_eligible
from PiFinder.mf_wide_tiles import plan_wide_tiles


pytestmark = pytest.mark.unit


def _four_stars(_frame):
    return [(10.0, 10.0), (20.0, 20.0), (30.0, 30.0), (40.0, 40.0)]


def test_normal_central_tile_publishes_its_camera_centre_target():
    plan = plan_wide_tiles((512, 512), 20.0, 10.0)
    seen_targets = []

    def solve(_centroids, _size, target, _fov):
        seen_targets.append(target)
        return {
            "RA": 100.0,
            "Dec": 20.0,
            "RA_target": 101.0,
            "Dec_target": 21.0,
            "Roll": 4.0,
            "Matches": 8,
            "RMSE": 1.0,
        }

    result = solve_wide_tiles(
        frame=np.zeros((512, 512), dtype=np.uint16),
        plan=plan,
        excluded_tile_ids=set(),
        saturation_level=4095,
        rotation_deg=90.0,
        crop_width_px=512,
        production_target_yx=(256.0, 256.0),
        tile_fov_degrees=10.0,
        detect_primary=_four_stars,
        detect_fallback=None,
        solve=solve,
    )

    assert result.solve_path == "wide_central"
    assert result.solution["RA"] == 101.0
    assert result.solution["Dec"] == 21.0
    assert result.consensus_tile_ids == ("C",)
    assert len(seen_targets) == 1


def test_saturated_centre_requires_and_accepts_an_adjacent_pair():
    plan = plan_wide_tiles((512, 1024), 20.0, 10.0)
    frame = np.zeros((512, 1024), dtype=np.uint16)
    centre = plan.central_tile.rect
    frame[centre.y : centre.y_end, centre.x : centre.x_end] = 4095

    def solve(_centroids, _size, _target, _fov):
        return {
            "RA": 42.0,
            "Dec": -12.0,
            "RA_target": 42.0,
            "Dec_target": -12.0,
            "Roll": 9.0,
            "Matches": 9,
            "RMSE": 0.5,
        }

    result = solve_wide_tiles(
        frame=frame,
        plan=plan,
        excluded_tile_ids=set(),
        saturation_level=4095,
        rotation_deg=0.0,
        crop_width_px=512,
        production_target_yx=(256.0, 512.0),
        tile_fov_degrees=10.0,
        detect_primary=_four_stars,
        detect_fallback=None,
        solve=solve,
    )

    assert result.central_saturated is True
    assert result.solve_path == "wide_adjacent_pair"
    assert set(result.consensus_tile_ids) == {"W", "E"}
    assert result.solution["RA"] == 42.0


def test_single_peripheral_solution_is_held_not_published():
    plan = plan_wide_tiles((512, 1024), 20.0, 10.0)
    frame = np.full((512, 1024), 4095, dtype=np.uint16)
    calls = 0

    def sparse_detector(_frame):
        nonlocal calls
        calls += 1
        return _four_stars(_frame) if calls == 1 else ()

    result = solve_wide_tiles(
        frame=frame,
        plan=plan,
        excluded_tile_ids=set(),
        saturation_level=4095,
        rotation_deg=0.0,
        crop_width_px=512,
        production_target_yx=(256.0, 512.0),
        tile_fov_degrees=10.0,
        detect_primary=sparse_detector,
        detect_fallback=None,
        solve=lambda *_args: {
            "RA": 42.0,
            "Dec": -12.0,
            "RA_target": 42.0,
            "Dec_target": -12.0,
            "Roll": 9.0,
        },
    )

    assert result.solution is None
    assert result.solve_path == "wide_no_consensus"
    assert result.candidate_tile_ids == ("W",)


@pytest.mark.parametrize(
    ("enabled", "lens", "manual", "expected"),
    [
        (False, "6mm", None, False),
        (True, "6mm", None, True),
        (True, "10mm", None, False),
        (True, "manual", 6.0, True),
    ],
)
def test_wide_solver_eligibility_is_explicit_and_strictly_below_ten_mm(
    enabled, lens, manual, expected
):
    assert wide_solver_eligible(enabled, lens, manual) is expected
