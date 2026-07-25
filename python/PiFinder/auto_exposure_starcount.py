#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
Star-count controller for solver-driven auto-exposure.

An alternative to the match-count controller
(``auto_exposure.ExposurePIDController``), selected with the Camera Exp
menu's "Auto Star" item (``camera_exp = "auto_star"``). The feedback
signal is the number of centroids cedar-detect extracted from the frame
(``SolveDiagnostics.Centroids``) rather than the number of stars tetra3
matched against the catalog. That distinction is the point:

* 0 detected -> exposure/optics problem -> zero-match recovery ladder
* N>0 detected but 0 matched -> solver-side problem -> the ladder does
  NOT run (the match-count controller cannot tell these apart)

The control law and defaults follow cedar-server's field-proven exposure
servo (https://github.com/smroid/cedar-server -- calibrator.rs /
detect_engine.rs): detectable star count is modelled as roughly
proportional to exposure, so a single division step converges within a
few solves. Instead of cedar's one-shot calibration, the "anchor"
exposure is learned while running: any exposure that lands inside the
deadband becomes the anchor, which bounds excursions (+/- 3 stops) and
serves as the fallback when the frame is unusable (slewing, clouds,
bright sky).

Design doc: docs/mf_auto_exposure_plan_ko.md.
"""

import logging
from typing import Optional

from PiFinder.auto_exposure import ZeroMatchRecovery

logger = logging.getLogger("AutoExposure.StarCount")


class ExposureStarCountController:
    """
    Drive exposure so cedar-detect keeps finding a target number of stars.

    Same call pattern as the match-count controller: ``update()`` once per
    new solve attempt, returning a new exposure in microseconds or None.
    """

    def __init__(
        self,
        target_stars: int = 20,
        ema_alpha: float = 0.5,
        deadband_low: float = 0.8,
        deadband_high: float = 1.6,
        min_stars_for_control: int = 4,
        anchor_stops: int = 8,
        min_exposure: int = 25000,
        max_exposure: int = 1000000,
        bright_sky_mean: float = 240.0,
        initial_anchor: int = 400000,
        recovery: Optional[ZeroMatchRecovery] = None,
    ):
        """
        Initialize the star-count controller.

        Args:
            target_stars: Detected-centroid count to steer toward.
            ema_alpha: Smoothing factor for the detected-count EMA.
            deadband_low: Act only when count/target falls below this...
            deadband_high: ...or rises above this (asymmetric: shortfall
                is acted on quickly, excess stars are tolerated).
            min_stars_for_control: Below this count the frame is treated
                as slewing/blocked -- return to anchor instead of adjusting.
            anchor_stops: Clamp adjustments to anchor/stops..anchor*stops.
            min_exposure: Absolute exposure floor in microseconds.
            max_exposure: Absolute exposure ceiling in microseconds.
            bright_sky_mean: Center-ROI mean (8-bit) above which exposure
                is not raised -- more light will not add star contrast.
            initial_anchor: Anchor before any deadband hit (shipped
                default exposure, also the recovery ladder's first rung).
            recovery: Zero-match recovery ladder; here it triggers on
                zero *detected* stars, not zero matches.
        """
        self.target_stars = target_stars
        self.ema_alpha = ema_alpha
        self.deadband_low = deadband_low
        self.deadband_high = deadband_high
        self.min_stars_for_control = min_stars_for_control
        self.anchor_stops = anchor_stops
        self.min_exposure = min_exposure
        self.max_exposure = max_exposure
        self.bright_sky_mean = bright_sky_mean

        self._anchor = initial_anchor
        self._initial_anchor = initial_anchor
        self._ema: Optional[float] = None
        self._zero_count = 0
        self._recovery = recovery or ZeroMatchRecovery()

        logger.info(
            f"AutoExposure StarCount: target={target_stars}, "
            f"deadband=[{deadband_low}, {deadband_high}], "
            f"min_stars={min_stars_for_control}, anchor={initial_anchor}µs "
            f"(±{anchor_stops}x), range=[{min_exposure}, {max_exposure}]µs, "
            f"bright_sky_mean={bright_sky_mean}"
        )

    def reset(self) -> None:
        self._anchor = self._initial_anchor
        self._ema = None
        self._zero_count = 0
        self._recovery.reset()
        logger.debug("StarCount controller reset")

    def update(
        self,
        centroid_count: int,
        current_exposure: int,
        center_mean: Optional[float] = None,
    ) -> Optional[int]:
        """
        Update exposure from the latest solve attempt's detected-star count.

        Args:
            centroid_count: Centroids detected in the last solve attempt.
            current_exposure: Current exposure time in microseconds.
            center_mean: Mean pixel value of the frame's center ROI
                (8-bit), or None to skip the bright-sky guard.

        Returns:
            New exposure time in microseconds, or None if no change needed.
        """
        # Exception path: nothing detected at all -- exposure may be badly
        # wrong. Delegate to the recovery ladder (same ladder as the
        # match-count controller, but triggered on detections, not matches).
        if centroid_count == 0:
            self._zero_count += 1
            return self._recovery.handle(current_exposure, self._zero_count)

        if self._recovery.is_active():
            logger.debug(
                f"Recovery successful! Detected {centroid_count} stars at "
                f"{current_exposure}µs, resuming star-count control"
            )
            self._recovery.reset()
            # The recovery excursion must not bias the next adjustment.
            self._ema = None
        self._zero_count = 0

        # Too few stars to trust as a control signal: probably slewing or
        # partially blocked. Hold at the anchor rather than chasing noise.
        if centroid_count < self.min_stars_for_control:
            logger.debug(
                f"StarCount: only {centroid_count} stars "
                f"(<{self.min_stars_for_control}), returning to anchor"
            )
            return self._return_to_anchor(current_exposure)

        if self._ema is None:
            self._ema = float(centroid_count)
        else:
            self._ema = (
                self.ema_alpha * centroid_count + (1.0 - self.ema_alpha) * self._ema
            )
        star_fraction = self._ema / self.target_stars

        # Bright-sky guard: short of stars but the background is already
        # bright -- more exposure adds sky glow, not star contrast.
        if (
            star_fraction < 1.0
            and center_mean is not None
            and center_mean > self.bright_sky_mean
        ):
            logger.debug(
                f"StarCount: bright sky (center mean {center_mean:.0f} > "
                f"{self.bright_sky_mean:.0f}), not raising exposure"
            )
            return self._return_to_anchor(current_exposure)

        # Deadband: close enough. Learn this exposure as the known-good
        # anchor and hold still.
        if self.deadband_low <= star_fraction <= self.deadband_high:
            self._anchor = current_exposure
            return None

        # Star count ~ proportional to exposure, so one division step.
        new_exposure = int(current_exposure / star_fraction)
        new_exposure = max(
            self._anchor // self.anchor_stops,
            min(self._anchor * self.anchor_stops, new_exposure),
        )
        new_exposure = max(self.min_exposure, min(self.max_exposure, new_exposure))

        if new_exposure == current_exposure:
            return None

        logger.debug(
            f"StarCount: ema {self._ema:.1f} / target {self.target_stars} "
            f"= {star_fraction:.2f} → {current_exposure}µs → {new_exposure}µs"
        )
        return new_exposure

    def _return_to_anchor(self, current_exposure: int) -> Optional[int]:
        if current_exposure != self._anchor:
            return self._anchor
        return None

    def get_status(self) -> dict:
        return {
            "target_stars": self.target_stars,
            "ema": self._ema,
            "anchor": self._anchor,
            "zero_count": self._zero_count,
            "recovery_active": self._recovery.is_active(),
            "deadband": (self.deadband_low, self.deadband_high),
            "min_exposure": self.min_exposure,
            "max_exposure": self.max_exposure,
        }
