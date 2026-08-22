"""Unit coverage for the isolated opt-in wide tile execution policy."""

import numpy as np
import pytest

from PiFinder.mf_wide_solver import solve_wide_tiles, tile_solver_eligible
from PiFinder.mf_wide_tiles import plan_vertical_recovery_tiles, plan_wide_tiles


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
    assert set(result.consensus_tile_ids) == {"L", "R"}
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
    assert result.candidate_tile_ids == ("L",)


def test_recovery_grid_publishes_a_valid_peripheral_tile_target():
    plan = plan_vertical_recovery_tiles((1080, 1920), 12.0, tile_size_px=980)
    calls = 0
    solve_sizes = []

    def top_only_detector(_frame):
        nonlocal calls
        calls += 1
        return _four_stars(_frame) if calls == 1 else ()

    def solve(_centroids, size, _target, _fov):
        solve_sizes.append(size)
        return {
            "RA": 42.0,
            "Dec": -12.0,
            "RA_target": 42.5,
            "Dec_target": -12.5,
            "Roll": 9.0,
            "Matches": 9,
            "RMSE": 0.5,
        }

    result = solve_wide_tiles(
        frame=np.zeros((1080, 1920), dtype=np.uint16),
        plan=plan,
        excluded_tile_ids=set(),
        saturation_level=4095,
        rotation_deg=0.0,
        crop_width_px=980,
        production_target_yx=(256.0, 256.0),
        tile_fov_degrees=8.0,
        detect_primary=top_only_detector,
        detect_fallback=None,
        solve=solve,
    )

    assert result.solve_path == "recovery_ul"
    assert result.attempted_tile_ids == ("UL", "U", "UR", "L", "R", "DL", "D", "DR")
    assert result.consensus_tile_ids == ("UL",)
    assert result.solution["RA"] == 42.5
    assert solve_sizes == [(980, 980)]
    assert result.tile_scores[0].as_diagnostic() == {
        "id": "UL",
        "centroids": 4,
        "solved": True,
        "matches": 9,
        "rmse": 0.5,
        "reason": "",
    }
    assert all(
        score.reason == "fewer_than_four_centroids" for score in result.tile_scores[1:]
    )


def test_recovery_grid_accepts_numpy_centroids_from_sep_fallback():
    plan = plan_vertical_recovery_tiles((1080, 1920), 12.0, tile_size_px=980)

    result = solve_wide_tiles(
        frame=np.zeros((1080, 1920), dtype=np.uint16),
        plan=plan,
        excluded_tile_ids=set(),
        saturation_level=4095,
        rotation_deg=0.0,
        crop_width_px=980,
        production_target_yx=(256.0, 256.0),
        tile_fov_degrees=10.0,
        detect_primary=lambda _frame: (),
        # SEP returns an ndarray. Its truth value is intentionally undefined.
        detect_fallback=lambda _frame: np.asarray(_four_stars(_frame)),
        solve=lambda *_args: {
            "RA": 42.0,
            "Dec": -12.0,
            "RA_target": 42.5,
            "Dec_target": -12.5,
            "Roll": 9.0,
            "Matches": 9,
            "RMSE": 0.5,
        },
    )

    assert result.solve_path == "recovery_ul"
    assert result.consensus_tile_ids == ("UL",)


@pytest.mark.parametrize(
    ("enabled", "lens", "manual", "expected"),
    [
        (False, "6mm", None, False),
        (True, "6mm", None, True),
        (True, "10mm", None, True),
        (True, "16mm", None, True),
        (True, "manual", 6.0, True),
    ],
)
def test_tile_solver_eligibility_requires_an_explicit_lens_and_opt_in_flag(
    enabled, lens, manual, expected
):
    assert tile_solver_eligible(enabled, lens, manual) is expected
