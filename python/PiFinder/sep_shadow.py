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
  normal pointing chain, so tracking works from it. Hybrid alignment:
  an in-progress alignment also runs through this path when cedar
  fails; the returned y/x_target is mapped back into rotated-512 space
  (solver_frame_map.map_frame_pixel_to_target), so the normal alignment
  chain consumes it unchanged.

All entry points are defensive: any exception is logged and swallowed,
so the experiment can never take down the production solver.

Config keys (restart to apply): ``solver_shadow_detect``,
``solver_sep_fallback``, ``solver_sep_sigma``.

CSV: ``solver_shadow_log.csv`` in the tmpfs log dir (one row per solve
attempt -- steady small appends belong in RAM, not on the SD card, per
the same policy as the app logs). The web Logs page's "Save to SD"
snapshot includes it; like the logs, it is lost on power-off unless
saved.
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
    "sep_masked",
]

# A solver_raw older than this no longer matches the attempt being logged
# (camera wedged, SEP path disabled mid-run); skip rather than mislabel.
MAX_FRAME_AGE_S = 15.0
# Fallback solve timeout. A 500 ms cap was field-tried 2026-08-04 but the
# A/B window was confounded by a scene change (SEP detections 17 -> 13,
# thin cloud); no solve-rate evidence either way, so the conservative 1 s
# stays until a same-frame offline A/B decides (field-test report).
FALLBACK_SOLVE_TIMEOUT_MS = 1000

# Warm-pixel map: (N, 2) int (y, x) in solver_raw orientation, built by
# ``python -m PiFinder.sep_warm_map`` from stage-dump corpora. Optional --
# missing file just means no masking. Regenerate when the sensor ages or
# after long temperature shifts (warm pixels grow over both).
WARM_MAP_PATH = utils.data_dir / "sep_warm_pixels.npy"


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
        # 5, paired with sigma 4.5: the 2026-07-28 live-sky sweep showed
        # half the genuine rescues carry only 5-7 detections at that
        # threshold (the old gate of 8 was calibrated for sigma 3.5's
        # junk-inflated counts). No observed solve had fewer than 5.
        min_fallback_stars: int = 5,
        saturation_level: Optional[float] = None,
        csv_path=None,
        warm_pixel_map: Optional[np.ndarray] = None,
    ):
        self.shadow_enabled = shadow_enabled
        self.fallback_enabled = fallback_enabled
        self.sigma = sigma
        self.rotation_deg = rotation_deg
        self.crop_width_px = crop_width_px
        self.min_fallback_stars = min_fallback_stars
        self.saturation_level = saturation_level
        self.csv_path = csv_path or (utils.log_dir / "solver_shadow_log.csv")
        self.warm_pixel_map = warm_pixel_map
        # Fallback backoff state (see fallback_should_attempt): a fallback
        # solve on unsolvable input burns up to solve_timeout (1 s) of solver
        # CPU per attempt -- indoors/under cloud that is every attempt.
        self._attempt_counter = 0
        self._fallback_fail_streak = 0
        self._fallback_skip_until = 0
        self._last_failed_sep_count: Optional[int] = None
        # Overlay entry for the in-flight attempt (see publish_overlay)
        self._last_overlay: Optional[dict] = None
        logger.info(
            "SEP shadow runner: shadow=%s fallback=%s sigma=%.1f "
            "rotation=%.0f° crop_width=%dpx warm_pixels=%d log=%s",
            shadow_enabled,
            fallback_enabled,
            sigma,
            rotation_deg,
            crop_width_px,
            0 if warm_pixel_map is None else len(warm_pixel_map),
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
            sigma = float(cfg.get_option("solver_sep_sigma") or 4.0)
            warm_map = None
            try:
                if WARM_MAP_PATH.exists():
                    warm_map = np.asarray(np.load(WARM_MAP_PATH), dtype=np.int32)
                    logger.info(
                        "Loaded %d warm pixels from %s", len(warm_map), WARM_MAP_PATH
                    )
            except Exception:
                logger.exception("Warm-pixel map load failed; continuing unmasked")
                warm_map = None
            return cls(
                shadow_enabled=shadow,
                fallback_enabled=fallback,
                sigma=sigma,
                rotation_deg=rotation,
                crop_width_px=crop_width,
                saturation_level=float(2**profile.bit_depth - 1),
                warm_pixel_map=warm_map,
            )
        except Exception:
            logger.exception("SEP shadow runner init failed; disabled")
            return None

    def fallback_should_attempt(self, sep_count: int) -> bool:
        """Backoff gate for the fallback solve.

        A failed fallback solve costs up to solve_timeout (1 s) of solver
        CPU. When the scene is persistently unsolvable (indoors, thick
        cloud) the SEP count passes the star gate every attempt and that
        cost recurs forever. After each consecutive failure we skip the
        next ``min(2**streak, 8)`` attempts -- but re-arm IMMEDIATELY when
        the SEP count rises to 1.5x the last failed attempt, which is what
        a cloud gap opening on real stars looks like. Rescue solves in a
        star window are therefore not delayed (2026-07-27 field: counts
        jumped from <=5 masked to ~30 when stars appeared).
        """
        if self._fallback_fail_streak == 0:
            return True
        if (
            self._last_failed_sep_count is not None
            and sep_count >= 1.5 * self._last_failed_sep_count
        ):
            return True
        return self._attempt_counter >= self._fallback_skip_until

    def record_fallback_result(self, solved: bool, sep_count: int) -> None:
        if solved:
            self._fallback_fail_streak = 0
            self._last_failed_sep_count = None
            return
        self._fallback_fail_streak += 1
        self._last_failed_sep_count = sep_count
        self._fallback_skip_until = self._attempt_counter + min(
            2**self._fallback_fail_streak, 8
        )

    def note_solved(self) -> None:
        """A production solve succeeded: the sky is workable, so the next
        cedar failure deserves an immediate fallback try again."""
        self._fallback_fail_streak = 0
        self._last_failed_sep_count = None

    def detect(self, shared_state) -> Optional[SepRun]:
        """Run SEP on the freshest published full-frame raw, or None."""
        self._attempt_counter += 1
        try:
            entry = shared_state.solver_raw()
            if not entry or "frame" not in entry:
                return None
            if time.time() - float(entry.get("timestamp") or 0) > MAX_FRAME_AGE_S:
                return None
            frame = np.asarray(entry["frame"])
            detection = sep_detect.detect_stars(
                frame,
                sigma=self.sigma,
                saturation_level=self.saturation_level,
                warm_pixel_map=self.warm_pixel_map,
            )
            if detection is None:
                return None
            # LiveCam overlay entry: NOT published here -- solve() attaches
            # the tetra3-matched subset and publish_overlay() (called once
            # per attempt from the solver, after the outcome is known)
            # publishes the final entry. Publishing candidates-only from
            # here raced the matched republish: the next attempt's detect
            # overwrote it, so the confirmed/candidate split almost never
            # reached the screen.
            self._last_overlay = {
                "centroids": detection.centroids.tolist(),
                "frame_hw": [int(frame.shape[0]), int(frame.shape[1])],
                "masked": detection.masked_count,
                "sigma": self.sigma,
                "timestamp": time.time(),
            }
            return SepRun(
                detection=detection,
                frame_hw=(frame.shape[0], frame.shape[1]),
                exposure_us=entry.get("exposure_us"),
                gain=entry.get("gain"),
            )
        except Exception:
            logger.exception("SEP shadow detect failed")
            return None

    def solve(
        self,
        t3,
        run: SepRun,
        shared_state,
        target_sky_coord=None,
        centroids_override=None,
    ) -> Optional[dict]:
        """Solve from SEP centroids in the rotated full frame.

        Rotation, canvas size, fov and target_pixel are all mapped so the
        resulting RA/Dec/Roll -- and the aligned pointing at target_pixel
        -- carry the exact semantics of the production 512-frame solve
        (see solver_frame_map).

        ``target_sky_coord`` supports the hybrid alignment: when an
        alignment is in progress and the production (cedar) solve cannot
        complete under the target sky, the SEP solve resolves the
        alignment coordinate and its y/x_target is mapped BACK into
        rotated-512 space, so the normal alignment chain (AlignedResult,
        persisted target_pixel) consumes it unchanged.

        ``centroids_override`` solves from a subset (still full-frame
        (y, x) coordinates) instead of the run's full detection list --
        the centre-first cascade passes the centre-square subset here.
        """
        try:
            source = (
                run.detection.centroids
                if centroids_override is None
                else np.asarray(centroids_override, dtype=np.float64)
            )
            cents, canvas = sfm.rotate_centroids(
                source, run.frame_hw, self.rotation_deg
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
                target_sky_coord=target_sky_coord,
                solve_timeout=FALLBACK_SOLVE_TIMEOUT_MS,
            )
            if (
                solution
                and solution.get("RA") is not None
                and solution.get("y_target") is not None
                and solution.get("x_target") is not None
            ):
                ty, tx = sfm.map_frame_pixel_to_target(
                    (float(solution["y_target"]), float(solution["x_target"])),
                    canvas,
                    self.crop_width_px,
                )
                solution["y_target"], solution["x_target"] = ty, tx
            self._attach_matched_overlay(solution)
            return solution
        except Exception:
            logger.exception("SEP fallback solve failed")
            return None

    def _rotate_csv_on_schema_change(self) -> None:
        """Sideline a CSV written with an older field list, once.

        Mixed-width rows break offline analysis; the sidelined file keeps
        its data under ``<name>.old``.
        """
        if getattr(self, "_csv_schema_checked", False):
            return
        self._csv_schema_checked = True
        if not self.csv_path.exists():
            return
        with open(self.csv_path, newline="") as f:
            header = f.readline().strip()
        if header != ",".join(CSV_FIELDS):
            old = self.csv_path.with_suffix(self.csv_path.suffix + ".old")
            self.csv_path.replace(old)
            logger.info("Shadow CSV schema changed; previous file moved to %s", old)

    def _attach_matched_overlay(self, solution) -> None:
        """Attach the tetra3-matched subset to the pending overlay entry.

        Matched centroids come back in the ROTATED canvas; un-rotating
        them (rotate by the complementary angle on the rotated canvas)
        puts them in the same frame space as the overlay's candidate
        list. publish_overlay() ships the combined entry once per
        attempt. Best-effort like every overlay path.
        """
        try:
            overlay = getattr(self, "_last_overlay", None)
            if (
                overlay is None
                or not solution
                or solution.get("RA") is None
                or solution.get("matched_centroids") is None
            ):
                return
            matched = np.asarray(solution["matched_centroids"], dtype=np.float64)
            if matched.ndim != 2 or len(matched) == 0:
                return
            _, canvas = sfm.rotate_centroids(
                np.empty((0, 2)), tuple(overlay["frame_hw"]), self.rotation_deg
            )
            unrot, _ = sfm.rotate_centroids(
                matched, canvas, (360.0 - self.rotation_deg) % 360.0
            )
            overlay["matched"] = unrot.tolist()
        except Exception:
            logger.exception("SEP matched-overlay attach failed")

    def attach_canvas_matched(self, matched_centroids) -> None:
        """Overlay hook for the full-frame cedar primary path.

        Its matched centroids live in the same rotated canvas as a SEP
        solve's, so the un-rotation is identical; the solver hands the
        array separately because the solution message itself is stripped
        of full-frame arrays before SQM sees it."""
        self._attach_matched_overlay(
            {"RA": 0.0, "matched_centroids": matched_centroids}
        )

    def attach_production_matched(self, solution) -> None:
        """Cedar solved this frame: mark its matched stars on the overlay.

        Without this, cedar-solved attempts carried no matched info and
        the overlay showed all-orange exactly when the sky is GOOD (cedar
        priority working as designed -- field report 2026-07-28 night).
        Cedar's matched centroids are in rotated-512 space; each maps to
        the rotated full-frame canvas (map_target_pixel_to_frame, the
        proven centre-scale relation) and is then un-rotated to frame
        space, the same space as the SEP matched path.
        """
        try:
            overlay = self._last_overlay
            if (
                overlay is None
                or not solution
                or solution.get("RA") is None
                or solution.get("matched_centroids") is None
            ):
                return
            m512 = np.asarray(solution["matched_centroids"], dtype=np.float64)
            if m512.ndim != 2 or len(m512) == 0:
                return
            _, canvas = sfm.rotate_centroids(
                np.empty((0, 2)), tuple(overlay["frame_hw"]), self.rotation_deg
            )
            mapped = np.array(
                [
                    sfm.map_target_pixel_to_frame((y, x), canvas, self.crop_width_px)
                    for y, x in m512
                ]
            )
            unrot, _ = sfm.rotate_centroids(
                mapped, canvas, (360.0 - self.rotation_deg) % 360.0
            )
            overlay["matched"] = unrot.tolist()
        except Exception:
            logger.exception("production matched overlay attach failed")

    def publish_overlay(self, shared_state) -> None:
        """Publish this attempt's overlay entry (candidates + any matched
        subset) exactly once, after the solve outcome is known."""
        overlay = getattr(self, "_last_overlay", None)
        if overlay is None or not hasattr(shared_state, "set_sep_overlay"):
            return
        try:
            shared_state.set_sep_overlay(dict(overlay))
        except Exception:
            logger.exception("SEP overlay publish failed")
        self._last_overlay = None

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
                "sep_masked": run.detection.masked_count if run else "",
            }
            self._rotate_csv_on_schema_change()
            write_header = not self.csv_path.exists()
            with open(self.csv_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                if write_header:
                    writer.writeheader()
                writer.writerow(row)
        except Exception:
            logger.exception("SEP shadow CSV append failed")
