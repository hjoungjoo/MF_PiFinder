#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
Ground-light rejection for the full-frame cedar path.

Covers the two independently-switchable filters added 2026-08-04 after a
building's window lights fed cedar 28-33 star-like centroids that could
never pattern-match:

* sep_detect.filter_plain_centroids -- the SEP fallback's quality gates
  (edge margin, saturation sample, warm-pixel map, cluster gate) applied
  to flux-less cedar centroids.
* horizon_mask -- IMU-derived per-pixel altitude; detections looking
  below MIN_ALT_DEG are dropped.
"""

import numpy as np
import pytest
import quaternion

from PiFinder import horizon_mask, sep_detect
from PiFinder import solver_frame_map as sfm
from PiFinder.pointing_model.imu_dead_reckoning import ImuDeadReckoning
from PiFinder.types.positioning import ImuSample

FULL_H, FULL_W = 1080, 1920
CROP_W = 980


# ---------------------------------------------------------------- gates


@pytest.mark.unit
def test_plain_gate_drops_dense_cluster_keeps_isolated():
    frame = np.full((FULL_H, FULL_W), 100, dtype=np.uint16)
    cluster = [(500.0 + i * 10, 500.0 + i * 10) for i in range(5)]  # windows
    isolated = [(200.0, 1200.0), (900.0, 300.0)]
    kept = sep_detect.filter_plain_centroids(cluster + isolated, frame)
    assert len(kept) == 2
    assert {tuple(p) for p in kept} == {tuple(p) for p in isolated}


@pytest.mark.unit
def test_plain_gate_edge_margin_and_saturation():
    frame = np.full((FULL_H, FULL_W), 100, dtype=np.uint16)
    frame[398:403, 598:603] = 4095  # blown-out ground light
    pts = [
        (10.0, 500.0),  # inside edge margin -> drop
        (400.0, 600.0),  # saturated -> drop
        (700.0, 900.0),  # clean -> keep
    ]
    kept = sep_detect.filter_plain_centroids(pts, frame, saturation_level=4095.0)
    assert [tuple(p) for p in kept] == [(700.0, 900.0)]


@pytest.mark.unit
def test_plain_gate_warm_pixel_map():
    frame = np.full((FULL_H, FULL_W), 100, dtype=np.uint16)
    warm = np.array([[300, 400]], dtype=np.int32)
    pts = [(301.0, 401.0), (600.0, 600.0)]
    kept = sep_detect.filter_plain_centroids(pts, frame, warm_pixel_map=warm)
    assert [tuple(p) for p in kept] == [(600.0, 600.0)]


# --------------------------------------------------------- horizon mask


def _sample_for_cam(q_cam: quaternion.quaternion) -> ImuSample:
    """ImuSample whose imu quat yields exactly ``q_cam`` for flat3."""
    q_imu = q_cam * ImuDeadReckoning._q_imu2cam("flat3").conj()
    return ImuSample(quat=q_imu.normalized(), timestamp=0.0, status=3)


def _axis_angle(axis, angle_rad):
    axis = np.asarray(axis, dtype=np.float64)
    axis /= np.linalg.norm(axis)
    half = angle_rad / 2.0
    return quaternion.quaternion(np.cos(half), *(np.sin(half) * axis)).normalized()


@pytest.mark.unit
def test_horizon_mask_zenith_keeps_everything():
    sample = _sample_for_cam(quaternion.one)  # boresight straight up
    pts = [(100.0, 100.0), (540.0, 960.0), (1000.0, 1800.0)]
    kept, dropped = horizon_mask.filter_ground_centroids(
        pts, (FULL_H, FULL_W), 90.0, sample, "flat3", CROP_W
    )
    assert dropped == 0 and len(kept) == 3


@pytest.mark.unit
def test_horizon_mask_at_horizon_splits_frame():
    # Boresight on the northern horizon: half the (wide) frame axis looks
    # below the horizon, so low-altitude detections must drop while
    # high-altitude ones survive.
    q_cam = _axis_angle([1, 0, 0], -np.pi / 2)
    sample = _sample_for_cam(q_cam)
    pts = [(540.0, float(x)) for x in range(60, FULL_W - 60, 120)]
    alts = horizon_mask.centroid_altitudes(
        pts, (FULL_H, FULL_W), 90.0, sample.quat, "flat3", CROP_W
    )
    assert alts is not None
    # Wide axis spans roughly +-(FOV * 1920/980)/2 around 0
    assert alts.min() < -5.0 and alts.max() > 5.0
    # Monotonic along the frame axis (single horizon crossing)
    diffs = np.diff(alts)
    assert np.all(diffs > 0) or np.all(diffs < 0)

    kept, dropped = horizon_mask.filter_ground_centroids(
        pts, (FULL_H, FULL_W), 90.0, sample, "flat3", CROP_W
    )
    assert dropped == int((alts < horizon_mask.MIN_ALT_DEG).sum())
    assert len(kept) + dropped == len(pts)
    kept_alts = horizon_mask.centroid_altitudes(
        kept, (FULL_H, FULL_W), 90.0, sample.quat, "flat3", CROP_W
    )
    assert kept_alts is not None and (kept_alts >= horizon_mask.MIN_ALT_DEG).all()


@pytest.mark.unit
def test_horizon_mask_passthrough_when_uncalibrated():
    sample = ImuSample(quat=quaternion.one, timestamp=0.0, status=1)
    pts = [(540.0, 60.0)]
    kept, dropped = horizon_mask.filter_ground_centroids(
        pts, (FULL_H, FULL_W), 90.0, sample, "flat3", CROP_W
    )
    assert dropped == 0 and kept is pts


@pytest.mark.unit
def test_horizon_mask_plate_scale_matches_frame_map():
    # Two points one crop-width apart along the wide axis must differ by
    # about SOLVER_FOV_DEG in altitude when the horizon runs through the
    # frame (small-angle regime).
    q_cam = _axis_angle([1, 0, 0], -np.pi / 2)
    sample = _sample_for_cam(q_cam)
    pts = [(540.0, 470.0), (540.0, 470.0 + CROP_W)]
    alts = horizon_mask.centroid_altitudes(
        pts, (FULL_H, FULL_W), 90.0, sample.quat, "flat3", CROP_W
    )
    assert alts is not None
    assert abs(abs(alts[1] - alts[0]) - sfm.SOLVER_FOV_DEG) < 0.6
