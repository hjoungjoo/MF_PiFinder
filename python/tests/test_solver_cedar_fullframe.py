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

import numpy as np
import pytest

from PiFinder import solver
from PiFinder import solver_frame_map as sfm

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
        {"RA": 10.0, "Dec": 20.0, "Roll": 30.0, "y_target": 100.0, "x_target": 200.0}
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
