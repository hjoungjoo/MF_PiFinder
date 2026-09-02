#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
Full-frame cedar primary path (solver_cedar_fullframe).

Covers the solver-side adapter added for the plan in
docs/mf_dev/mf_cedar_fullframe_primary_plan_ko.md:

* _count_in_crop keeps SolveDiagnostics.Centroids in 512-crop semantics
  for auto-exposure.
* _solve_cedar_fullframe maps target_pixel into the rotated canvas,
  solves at native FOV, and maps y/x_target back to 512 space -- the
  same contract sep_shadow.solve fulfils for the SEP fallback.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from PiFinder import solver
from PiFinder import solver_frame_map as sfm
from PiFinder.mf_wide_distortion import undistort_global_centroids

FULL_H, FULL_W = 1080, 1920
CROP_W = 980
TARGET_512 = (300.0, 340.0)


class _FakeSharedState:
    def target_pixel(self):
        return TARGET_512


class _FakeT3:
    def __init__(self, solution):
        self.solution = solution
        self.calls = []

    def solve_from_centroids(self, cents, canvas, **kwargs):
        self.calls.append({"cents": cents, "canvas": canvas, **kwargs})
        return dict(self.solution)


@pytest.mark.unit
def test_count_in_crop_uses_centred_window():
    # imx462: crop window is y 50..1030, x 470..1450
    centroids = [
        (540.0, 960.0),  # dead centre -> in
        (51.0, 471.0),  # just inside both edges -> in
        (49.0, 960.0),  # above the crop -> out
        (540.0, 1451.0),  # right of the crop -> out
        (1035.0, 960.0),  # below the crop -> out
    ]
    assert solver._count_in_crop(centroids, (FULL_H, FULL_W), CROP_W) == 2
    assert solver._count_in_crop([], (FULL_H, FULL_W), CROP_W) == 0
    assert solver._count_in_crop(None, (FULL_H, FULL_W), CROP_W) == 0


@pytest.mark.unit
def test_solve_cedar_fullframe_maps_like_sep_path():
    fake = _FakeT3(
        {
            "RA": 10.0,
            "Dec": 20.0,
            "Roll": 30.0,
            "y_target": 100.0,
            "x_target": 200.0,
            "Matches": 7,
            "RMSE": 90.0,
            "Prob": 1e-6,
        }
    )
    centroids = [(540.0, 960.0), (100.0, 100.0), (900.0, 1800.0)]

    solution = solver._solve_cedar_fullframe(
        fake,
        centroids,
        (FULL_H, FULL_W),
        rotation_deg=90.0,
        crop_width_px=CROP_W,
        shared_state=_FakeSharedState(),
    )

    assert len(fake.calls) == 1
    call = fake.calls[0]

    # Native-FOV solve on the rotated canvas, exactly as sep_shadow.solve.
    _, canvas = sfm.rotate_centroids(
        np.asarray(centroids, dtype=np.float64), (FULL_H, FULL_W), 90.0
    )
    assert tuple(call["canvas"]) == tuple(canvas)
    expected_fov = sfm.fov_estimate_deg(canvas[1], CROP_W)
    assert call["fov_estimate"] == pytest.approx(expected_fov)
    assert call["fov_max_error"] == pytest.approx(expected_fov / 3.0)
    expected_tp = sfm.map_target_pixel_to_frame(TARGET_512, canvas, CROP_W)
    assert tuple(call["target_pixel"]) == pytest.approx(tuple(expected_tp))

    # Fast-fail: junk full-frame detections must not burn the 1 s default
    # (LP ascent test 2026-08-03: 0.4 Hz attempt rate starved SEP rescue).
    assert call["solve_timeout"] == solver.CEDAR_FF_SOLVE_TIMEOUT_MS

    # y/x_target comes back in 512 space for the alignment chain.
    expected_back = sfm.map_frame_pixel_to_target((100.0, 200.0), canvas, CROP_W)
    assert (solution["y_target"], solution["x_target"]) == pytest.approx(
        tuple(expected_back)
    )


@pytest.mark.unit
def test_solve_cedar_fullframe_swallows_solver_errors():
    class _Boom:
        def solve_from_centroids(self, *a, **k):
            raise RuntimeError("boom")

    solution = solver._solve_cedar_fullframe(
        _Boom(),
        [(1.0, 1.0)],
        (FULL_H, FULL_W),
        rotation_deg=90.0,
        crop_width_px=CROP_W,
        shared_state=_FakeSharedState(),
    )
    assert solution == {}


@pytest.mark.unit
def test_solve_cedar_fullframe_accepts_future_crop_fov_without_mapping_change():
    fake = _FakeT3(
        {
            "RA": 10.0,
            "y_target": 100.0,
            "x_target": 200.0,
            "Matches": 7,
            "RMSE": 90.0,
            "Prob": 1e-6,
        }
    )
    solver._solve_cedar_fullframe(
        fake,
        [(540.0, 960.0)],
        (FULL_H, FULL_W),
        rotation_deg=90.0,
        crop_width_px=CROP_W,
        shared_state=_FakeSharedState(),
        base_fov_degrees=10.38,
    )
    _, canvas = sfm.rotate_centroids(
        np.asarray([(540.0, 960.0)]), (FULL_H, FULL_W), 90.0
    )
    assert fake.calls[0]["fov_estimate"] == pytest.approx(
        sfm.fov_estimate_deg(canvas[1], CROP_W, base_fov_degrees=10.38)
    )


@pytest.mark.unit
def test_solve_cedar_fullframe_undistorts_before_rotation():
    fake = _FakeT3({"RA": 10.0, "Matches": 7, "RMSE": 90.0, "Prob": 1e-6})
    centroids = np.asarray([(80.0, 120.0), (540.0, 960.0)])
    coefficients = {"k1": -0.04, "k2": 0.0, "k3": 0.0, "p1": 0.0, "p2": 0.0}
    solver._solve_cedar_fullframe(
        fake,
        centroids,
        (FULL_H, FULL_W),
        rotation_deg=90.0,
        crop_width_px=CROP_W,
        shared_state=_FakeSharedState(),
        distortion_coefficients=coefficients,
    )
    corrected = undistort_global_centroids(
        centroids,
        (FULL_H, FULL_W),
        coefficients,
    )
    expected, _ = sfm.rotate_centroids(corrected, (FULL_H, FULL_W), 90.0)
    assert fake.calls[0]["cents"] == pytest.approx(expected)


@pytest.mark.unit
def test_solve_cedar_fullframe_rejects_weak_quality():
    fake = _FakeT3({"RA": 10.0, "Dec": 20.0, "Matches": 5, "RMSE": 90.0, "Prob": 1e-6})
    solution = solver._solve_cedar_fullframe(
        fake,
        [(540.0, 960.0)],
        (FULL_H, FULL_W),
        rotation_deg=90.0,
        crop_width_px=CROP_W,
        shared_state=_FakeSharedState(),
    )
    assert solution == {}


@pytest.mark.unit
def test_center_square_subset_selects_max_centered_square():
    # 1920x1080 -> square side 1080, x in [420, 1500)
    pts = [
        (540.0, 960.0),  # centre -> in
        (0.0, 420.0),  # on the left edge of the square -> in
        (1079.0, 1499.0),  # bottom-right inside corner -> in
        (540.0, 419.0),  # just left of the square -> out
        (540.0, 1500.0),  # just right of the square -> out
    ]
    kept = solver._center_square_subset(pts, (1080, 1920))
    assert len(kept) == 3
    assert solver._center_square_subset([], (1080, 1920)).shape == (0, 2)


@pytest.mark.unit
def test_center_first_remainder_prefers_sep_center_before_any_full_frame():
    calls = []

    def stage(name, solution):
        def run():
            calls.append(name)
            return solution

        return run

    solution, path = solver._solve_center_first_remainder(
        (
            ("sep_center", stage("sep_center", {"RA": 1.0})),
            ("cedar_full", stage("cedar_full", {"RA": 2.0})),
            ("sep_full", stage("sep_full", {"RA": 3.0})),
        )
    )

    assert path == "sep_center"
    assert solution["RA"] == 1.0
    assert calls == ["sep_center"]


@pytest.mark.unit
def test_center_first_remainder_uses_full_paths_only_after_center_failure():
    calls = []

    def stage(name, solution):
        def run():
            calls.append(name)
            return solution

        return run

    solution, path = solver._solve_center_first_remainder(
        (
            ("sep_center", stage("sep_center", {})),
            ("cedar_full", stage("cedar_full", {"RA": 2.0})),
            ("sep_full", stage("sep_full", {"RA": 3.0})),
        )
    )

    assert path == "cedar_full"
    assert solution["RA"] == 2.0
    assert calls == ["sep_center", "cedar_full"]


@pytest.mark.unit
def test_auto_star_peripheral_tile_result_cannot_bypass_disabled_wide_pointing():
    candidate = {"RA": 120.0, "Dec": 30.0}
    result = SimpleNamespace(solution=candidate)

    assert solver._wide_result_pointing_solution(result, False) == {}
    assert solver._wide_result_pointing_solution(result, True) is candidate


@pytest.mark.unit
def test_center_first_remainder_uses_sep_full_as_last_resort():
    calls = []

    def stage(name, solution):
        def run():
            calls.append(name)
            return solution

        return run

    solution, path = solver._solve_center_first_remainder(
        (
            ("sep_center", stage("sep_center", {})),
            ("cedar_full", stage("cedar_full", {})),
            ("sep_full", stage("sep_full", {"RA": 3.0})),
        )
    )

    assert path == "sep_full"
    assert solution["RA"] == 3.0
    assert calls == ["sep_center", "cedar_full", "sep_full"]
