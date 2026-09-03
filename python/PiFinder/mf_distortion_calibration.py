"""On-sky radial-distortion calibration for a selected camera/lens pair.

The LCD only arms/cancels a session.  Actual fitting runs in the solver after
the normal pointing result has already been queued, so camera capture remains
continuous.  A profile is returned only after several full-frame solves agree
and matched stars cover the centre, middle and edge of the sensor.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
from typing import Any, Mapping

import numpy as np

from PiFinder import solver_frame_map as sfm
from PiFinder.mf_wide_distortion import undistort_global_centroids
from PiFinder.solve_acceptance import angular_separation_deg


MIN_CANDIDATES = 40
MIN_MATCHES = 18
MIN_CENTRAL_MATCHES = 3
MIN_MID_MATCHES = 6
MIN_EDGE_MATCHES = 3
REQUIRED_FRAMES = 5
MAX_SESSION_SAMPLES = 12
MAX_RMSE_ARCSEC = 180.0
MIN_RMSE_IMPROVEMENT = 0.03
MAX_K1_MAD = 0.015
MAX_K1_ABS = 0.5
CALIBRATION_SOLVE_TIMEOUT_MS = 1500
TETRA_DISTORTION_RANGE = (-0.25, 0.25)
# tetra3 and the persistent Brown model normalise radius differently, and the
# fitted FOV absorbs part of tetra3's centre scale. Replay a small bracket in
# the actual production model instead of trusting one analytic conversion.
BROWN_REPLAY_SCALES = (0.35, 0.50, 0.75, 1.0)


@dataclass(frozen=True)
class DistortionFrameMeasurement:
    k1: float
    candidates: int
    matches: int
    radial_bins: dict[str, int]
    rmse_before_arcsec: float | None
    rmse_after_arcsec: float
    fitted_rmse_arcsec: float
    ra: float
    dec: float


@dataclass(frozen=True)
class DistortionObservation:
    accepted: bool
    reason: str
    candidates: int
    measurement: DistortionFrameMeasurement | None = None


def tetra_k_to_brown_k1(
    tetra_k: float,
    raw_frame_hw: tuple[int, int],
    tetra_canvas_hw: tuple[int, int],
) -> float:
    """Convert tetra3's width/2 radial coefficient to our corner scale.

    tetra3 represents the non-linear part as ``k * (2r/width)^2`` while
    :mod:`mf_wide_distortion` uses ``k1 * (r/corner_radius)^2``.  The constant
    centre scale in tetra3 is absorbed by its fitted focal length, leaving
    this direct scale conversion for the radial term.
    """

    value = float(tetra_k)
    raw_h, raw_w = map(float, raw_frame_hw)
    canvas_width = float(tetra_canvas_hw[1])
    if not math.isfinite(value) or raw_h <= 0 or raw_w <= 0 or canvas_width <= 0:
        raise ValueError("invalid distortion conversion geometry")
    corner_radius = math.hypot(raw_h / 2.0, raw_w / 2.0)
    return value * 4.0 * corner_radius**2 / canvas_width**2


def _radial_bin_counts(
    matched_centroids: np.ndarray, canvas_hw: tuple[int, int]
) -> dict[str, int]:
    points = np.asarray(matched_centroids, dtype=np.float64).reshape(-1, 2)
    height, width = map(float, canvas_hw)
    centre = np.array([(height - 1.0) / 2.0, (width - 1.0) / 2.0])
    corner_radius = math.hypot(height / 2.0, width / 2.0)
    radii = np.linalg.norm(points - centre, axis=1) / max(corner_radius, 1.0)
    return {
        "central": int(np.count_nonzero(radii < 0.33)),
        "mid": int(np.count_nonzero((radii >= 0.33) & (radii < 0.66))),
        "edge": int(np.count_nonzero(radii >= 0.66)),
    }


def _solved(solution: Mapping[str, Any] | None, minimum_matches: int) -> bool:
    if not solution or solution.get("RA") is None or solution.get("Dec") is None:
        return False
    try:
        rmse = solution.get("RMSE")
        if rmse is None:
            return False
        rmse_value = float(rmse)
        return (
            int(solution.get("Matches") or 0) >= minimum_matches
            and math.isfinite(rmse_value)
            and rmse_value <= MAX_RMSE_ARCSEC
        )
    except (TypeError, ValueError):
        return False


def measure_distortion_frame(
    t3,
    centroids_yx,
    frame_hw: tuple[int, int],
    *,
    rotation_deg: float,
    crop_width_px: int,
    base_fov_degrees: float,
) -> DistortionObservation:
    """Fit and independently replay-check one full-frame star field."""

    centroids = np.asarray(centroids_yx, dtype=np.float64).reshape(-1, 2)
    if len(centroids) < MIN_CANDIDATES:
        return DistortionObservation(False, "not_enough_candidates", len(centroids))

    rotated, canvas = sfm.rotate_centroids(centroids, frame_hw, rotation_deg)
    fov = sfm.fov_estimate_deg(
        canvas[1], crop_width_px, base_fov_degrees=base_fov_degrees
    )
    solve_kwargs = {
        "fov_estimate": fov,
        "fov_max_error": fov / 3.0,
        "match_max_error": 0.005,
        "return_matches": True,
        "solve_timeout": CALIBRATION_SOLVE_TIMEOUT_MS,
    }
    fitted = t3.solve_from_centroids(
        rotated, canvas, distortion=TETRA_DISTORTION_RANGE, **solve_kwargs
    )
    if not _solved(fitted, MIN_MATCHES):
        return DistortionObservation(False, "distortion_fit_failed", len(centroids))

    matched_value = fitted.get("matched_centroids")
    matched = np.asarray(
        [] if matched_value is None else matched_value, dtype=np.float64
    )
    bins = _radial_bin_counts(matched, canvas)
    if (
        bins["central"] < MIN_CENTRAL_MATCHES
        or bins["mid"] < MIN_MID_MATCHES
        or bins["edge"] < MIN_EDGE_MATCHES
    ):
        return DistortionObservation(False, "not_enough_field_coverage", len(centroids))

    try:
        converted_k1 = tetra_k_to_brown_k1(
            float(fitted["distortion"]), frame_hw, canvas
        )
    except (KeyError, TypeError, ValueError):
        return DistortionObservation(False, "invalid_distortion", len(centroids))
    if not math.isfinite(converted_k1):
        return DistortionObservation(False, "unsafe_distortion", len(centroids))

    replayed: list[tuple[float, Mapping[str, Any]]] = []
    for scale in BROWN_REPLAY_SCALES:
        candidate_k1 = converted_k1 * scale
        if abs(candidate_k1) > MAX_K1_ABS:
            continue
        coefficients = {
            "k1": candidate_k1,
            "k2": 0.0,
            "k3": 0.0,
            "p1": 0.0,
            "p2": 0.0,
        }
        corrected = undistort_global_centroids(centroids, frame_hw, coefficients)
        corrected_rotated, corrected_canvas = sfm.rotate_centroids(
            corrected, frame_hw, rotation_deg
        )
        if corrected_canvas != canvas:
            continue
        checked_candidate = t3.solve_from_centroids(
            corrected_rotated, canvas, distortion=0.0, **solve_kwargs
        )
        if _solved(checked_candidate, MIN_MATCHES):
            replayed.append((candidate_k1, checked_candidate))
    if not replayed:
        return DistortionObservation(False, "corrected_replay_failed", len(centroids))

    k1, checked = min(replayed, key=lambda item: float(item[1]["RMSE"]))

    separation = angular_separation_deg(
        float(fitted["RA"]),
        float(fitted["Dec"]),
        float(checked["RA"]),
        float(checked["Dec"]),
    )
    if separation > 0.25:
        return DistortionObservation(
            False, "corrected_coordinate_mismatch", len(centroids)
        )

    baseline = t3.solve_from_centroids(rotated, canvas, distortion=0.0, **solve_kwargs)
    rmse_before = float(baseline["RMSE"]) if _solved(baseline, MIN_MATCHES) else None
    rmse_after = float(checked["RMSE"])
    if rmse_before is not None and rmse_after > rmse_before * (
        1.0 - MIN_RMSE_IMPROVEMENT
    ):
        return DistortionObservation(False, "no_rmse_improvement", len(centroids))

    measurement = DistortionFrameMeasurement(
        k1=k1,
        candidates=len(centroids),
        matches=int(checked["Matches"]),
        radial_bins=bins,
        rmse_before_arcsec=rmse_before,
        rmse_after_arcsec=rmse_after,
        fitted_rmse_arcsec=float(fitted["RMSE"]),
        ra=float(checked["RA"]),
        dec=float(checked["Dec"]),
    )
    return DistortionObservation(True, "accepted", len(centroids), measurement)


class DistortionCalibrationSession:
    """Accumulate stable per-frame measurements into one stored profile."""

    def __init__(self, camera_type: str, lens_key: str, request_id: int):
        self.camera_type = str(camera_type)
        self.lens_key = str(lens_key)
        self.request_id = int(request_id)
        self.samples: list[DistortionFrameMeasurement] = []
        self.last_reason = "requested"

    def add(self, observation: DistortionObservation) -> None:
        self.last_reason = observation.reason
        if observation.accepted and observation.measurement is not None:
            self.samples.append(observation.measurement)
            del self.samples[:-MAX_SESSION_SAMPLES]

    def ready(self) -> bool:
        if len(self.samples) < REQUIRED_FRAMES:
            return False
        values = [sample.k1 for sample in self.samples]
        median = statistics.median(values)
        mad = statistics.median(abs(value - median) for value in values)
        return mad <= MAX_K1_MAD

    def profile_values(self) -> tuple[dict[str, float], dict[str, Any]]:
        if not self.ready():
            raise ValueError("distortion calibration is not stable yet")
        samples = self.samples
        values = [sample.k1 for sample in samples]
        before = [
            sample.rmse_before_arcsec
            for sample in samples
            if sample.rmse_before_arcsec is not None
        ]
        after = [sample.rmse_after_arcsec for sample in samples]
        bins = {
            name: sum(sample.radial_bins[name] for sample in samples)
            for name in ("central", "mid", "edge")
        }
        coefficients = {
            "k1": statistics.median(values),
            "k2": 0.0,
            "k3": 0.0,
            "p1": 0.0,
            "p2": 0.0,
        }
        summary: dict[str, Any] = {
            "frames": len(samples),
            "radial_bins": bins,
            "matches_median": statistics.median(sample.matches for sample in samples),
            "k1_mad": statistics.median(
                abs(value - coefficients["k1"]) for value in values
            ),
            "median_rmse_after_arcsec": statistics.median(after),
            "method": "tetra3_radial_fit_brown_replay_v1",
        }
        if before:
            summary["median_rmse_before_arcsec"] = statistics.median(before)
            summary["rmse_improvement_fraction"] = 1.0 - (
                summary["median_rmse_after_arcsec"]
                / summary["median_rmse_before_arcsec"]
            )
        return coefficients, summary

    def status(
        self, *, state: str | None = None, candidates: int = 0
    ) -> dict[str, Any]:
        return {
            "state": state or ("collecting" if self.samples else "waiting_stars"),
            "request_id": self.request_id,
            "camera_type": self.camera_type,
            "lens_key": self.lens_key,
            "accepted_frames": len(self.samples),
            "required_frames": REQUIRED_FRAMES,
            "last_reason": self.last_reason,
            "last_candidates": int(candidates),
            "minimum_candidates": MIN_CANDIDATES,
        }
