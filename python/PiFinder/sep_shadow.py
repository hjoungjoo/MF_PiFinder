#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
Shadow / fallback runner for the SEP full-frame detection path.

Purpose: evaluate the SEP candidate (12-bit uncropped detection, see
docs/mf_auto_exposure_field_review_20260726_ko.md) against the
production cedar-detect path in a single field session:

* **Shadow**: on every solve attempt, also run SEP on the uncropped raw
  frame and append one CSV row comparing both detectors. Zero effect on
  the production solve.
* **Fallback** (opt-in on top of shadow data): when the production
  solve fails and SEP found enough stars, attempt a real solve from the
  SEP centroids in the rotated full frame -- the solution feeds the
  normal pointing chain, so tracking works from it. Guarded so an
  in-progress alignment never runs through this path (its y/x_target
  would be in full-frame space).

All entry points are defensive: any exception is logged and swallowed,
so the experiment can never take down the production solver.

Config keys (restart to apply): ``solver_shadow_detect``,
``solver_sep_fallback``, ``solver_sep_sigma``.
CSV: ``PiFinder_data/solver_shadow_log.csv``.
"""

import csv
import logging
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from PiFinder import sep_detect, utils
from PiFinder import solver_frame_map as sfm
from PiFinder.sep_detect import SepDetection
from PiFinder.sqm.camera_profiles import get_camera_profile

logger = logging.getLogger("Solver.SepShadow")

CSV_FIELDS = [
    "timestamp",
    "exposure_us",
    "gain",
    "cedar_centroids",
    "matches",
    "solved",
    "sep_centroids",
    "sep_top_flux",
    "sep_bkg",
    "sep_rms",
    "sep_ms",
    "fallback_used",
    "fallback_rmse",
]

# A solver_raw older than this no longer matches the attempt being logged
# (camera wedged, SEP path disabled mid-run); skip rather than mislabel.
MAX_FRAME_AGE_S = 15.0


@dataclass
class SepRun:
    """One SEP pass over the freshest full-frame raw."""

    detection: SepDetection
    frame_hw: tuple
    exposure_us: Optional[float]
    gain: Optional[float]


class SepShadowRunner:
    def __init__(
        self,
        shadow_enabled: bool,
        fallback_enabled: bool,
        sigma: float,
        rotation_deg: float,
        crop_width_px: int,
        min_fallback_stars: int = 8,
        saturation_level: Optional[float] = None,
        csv_path=None,
    ):
        self.shadow_enabled = shadow_enabled
        self.fallback_enabled = fallback_enabled
        self.sigma = sigma
        self.rotation_deg = rotation_deg
        self.crop_width_px = crop_width_px
        self.min_fallback_stars = min_fallback_stars
        self.saturation_level = saturation_level
        self.csv_path = csv_path or (utils.data_dir / "solver_shadow_log.csv")
        logger.info(
            "SEP shadow runner: shadow=%s fallback=%s sigma=%.1f "
            "rotation=%.0f° crop_width=%dpx log=%s",
            shadow_enabled,
            fallback_enabled,
            sigma,
            rotation_deg,
            crop_width_px,
            self.csv_path,
        )

    @classmethod
    def create_if_enabled(cls, cfg, camera_type: Optional[str]):
        """Build a runner from config, or None when the path is disabled
        or the camera profile (crop geometry) is not resolvable yet."""
        try:
            shadow = bool(cfg.get_option("solver_shadow_detect"))
            fallback = bool(cfg.get_option("solver_sep_fallback"))
            if not (shadow or fallback):
                return None
            if not camera_type:
                return None
            profile = get_camera_profile(camera_type)
            crop_width = int(
                profile.raw_size[0] - profile.crop_x[0] - profile.crop_x[1]
            )
            rotation = sfm.stage5_rotation_deg(
                cfg.get_option("screen_direction"),
                cfg.get_option("camera_rotation"),
            )
            sigma = float(cfg.get_option("solver_sep_sigma") or 3.5)
            return cls(
                shadow_enabled=shadow,
                fallback_enabled=fallback,
                sigma=sigma,
                rotation_deg=rotation,
                crop_width_px=crop_width,
                saturation_level=float(2**profile.bit_depth - 1),
            )
        except Exception:
            logger.exception("SEP shadow runner init failed; disabled")
            return None

    def detect(self, shared_state) -> Optional[SepRun]:
        """Run SEP on the freshest published full-frame raw, or None."""
        try:
            entry = shared_state.solver_raw()
            if not entry or "frame" not in entry:
                return None
            if time.time() - float(entry.get("timestamp") or 0) > MAX_FRAME_AGE_S:
                return None
            frame = np.asarray(entry["frame"])
            detection = sep_detect.detect_stars(
                frame, sigma=self.sigma, saturation_level=self.saturation_level
            )
            if detection is None:
                return None
            return SepRun(
                detection=detection,
                frame_hw=(frame.shape[0], frame.shape[1]),
                exposure_us=entry.get("exposure_us"),
                gain=entry.get("gain"),
            )
        except Exception:
            logger.exception("SEP shadow detect failed")
            return None

    def solve(self, t3, run: SepRun, shared_state) -> Optional[dict]:
        """Solve from SEP centroids in the rotated full frame.

        Rotation, canvas size, fov and target_pixel are all mapped so the
        resulting RA/Dec/Roll -- and the aligned pointing at target_pixel
        -- carry the exact semantics of the production 512-frame solve
        (see solver_frame_map).
        """
        try:
            cents, canvas = sfm.rotate_centroids(
                run.detection.centroids, run.frame_hw, self.rotation_deg
            )
            target_pixel = sfm.map_target_pixel_to_frame(
                shared_state.target_pixel(), canvas, self.crop_width_px
            )
            fov = sfm.fov_estimate_deg(canvas[1], self.crop_width_px)
            solution = t3.solve_from_centroids(
                cents,
                canvas,
                fov_estimate=fov,
                fov_max_error=fov / 3.0,
                match_max_error=0.005,
                return_matches=True,
                target_pixel=target_pixel,
                solve_timeout=1000,
            )
            return solution
        except Exception:
            logger.exception("SEP fallback solve failed")
            return None

    def log_attempt(
        self,
        exposure_us,
        gain,
        cedar_count: int,
        matches,
        solved: bool,
        run: Optional[SepRun],
        fallback_used: bool = False,
        fallback_rmse=None,
    ) -> None:
        """Append one attempt-comparison row; never raises."""
        if not self.shadow_enabled:
            return
        try:
            row = {
                "timestamp": f"{time.time():.3f}",
                "exposure_us": exposure_us,
                "gain": gain,
                "cedar_centroids": cedar_count,
                "matches": matches,
                "solved": int(bool(solved)),
                "sep_centroids": len(run.detection.centroids) if run else "",
                "sep_top_flux": (
                    f"{run.detection.fluxes[0]:.0f}"
                    if run and len(run.detection.fluxes)
                    else ""
                ),
                "sep_bkg": f"{run.detection.background_median:.1f}" if run else "",
                "sep_rms": f"{run.detection.background_rms:.2f}" if run else "",
                "sep_ms": f"{run.detection.elapsed_ms:.0f}" if run else "",
                "fallback_used": int(bool(fallback_used)),
                "fallback_rmse": (
                    f"{fallback_rmse:.2f}" if fallback_rmse is not None else ""
                ),
            }
            write_header = not self.csv_path.exists()
            with open(self.csv_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerow(row)
        except Exception:
            logger.exception("SEP shadow CSV append failed")
