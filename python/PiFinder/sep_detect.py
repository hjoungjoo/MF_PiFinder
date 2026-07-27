#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
SEP (Source Extractor) star detection on the full-sensor RAW frame.

The production detector (cedar-detect) works on the processed 8-bit
512x512 solver frame, where the 12->8-bit stretch has already crushed
faint stars into a couple of levels (see
docs/mf_auto_exposure_field_review_20260726_ko.md). This module detects
in the 12-bit domain instead, on the *uncropped* sensor frame:

1. 2x2 bin the Bayer mosaic (mean of each RGGB quad). Mandatory: the
   per-channel sky response otherwise shows up as checkerboard pattern
   noise that buries faint stars; binning also doubles SNR.
2. Estimate and subtract a mesh background (``sep.Background``) -- this
   removes light-pollution gradients and cloud glow, which is exactly
   the failure mode of a global threshold under a Seoul sky.
3. Extract sources against the local background RMS with a small
   matched filter, then rank by flux.

Returned centroids are in FULL-frame pixel coordinates (y, x), ready
for the solver-frame mapping in ``solver_frame_map``.

``sep`` is an optional dependency: importing this module never fails,
and ``detect_stars`` returns None when sep is unavailable.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger("Solver.SepDetect")

_sep = None
_sep_import_failed = False

# 3x3 gaussian-ish matched filter (SExtractor convention) -- correlates
# neighbouring pixels so PSF-shaped bumps beat single-pixel noise.
MATCHED_FILTER = np.array(
    [[1.0, 2.0, 1.0], [2.0, 4.0, 2.0], [1.0, 2.0, 1.0]], dtype=np.float32
)


def _sep_module():
    """Import sep lazily; remember a failure so we log it only once."""
    global _sep, _sep_import_failed
    if _sep is None and not _sep_import_failed:
        try:
            import sep

            _sep = sep
        except ImportError:
            _sep_import_failed = True
            logger.warning("sep not installed; SEP detection disabled")
    return _sep


@dataclass
class SepDetection:
    """Result of one SEP extraction, in full-frame pixel coordinates."""

    centroids: np.ndarray  # (N, 2) float (y, x), flux-descending
    fluxes: np.ndarray  # (N,) float, same order
    background_median: float  # binned-domain ADU
    background_rms: float  # binned-domain ADU
    elapsed_ms: float


def bin2x2(frame: np.ndarray) -> np.ndarray:
    """Mean-bin each 2x2 (Bayer quad) block; trims odd edges."""
    arr = np.asarray(frame)
    h, w = arr.shape[0] // 2 * 2, arr.shape[1] // 2 * 2
    arr = arr[:h, :w].astype(np.float32)
    return (
        arr[0::2, 0::2] + arr[0::2, 1::2] + arr[1::2, 0::2] + arr[1::2, 1::2]
    ) * 0.25


def detect_stars(
    raw_frame: np.ndarray,
    sigma: float = 3.5,
    minarea: int = 3,
    max_stars: int = 48,
) -> Optional[SepDetection]:
    """
    Detect stars on a raw sensor frame (uint16 mosaic, any shape).

    Args:
        raw_frame: 2D raw sensor array (Bayer mosaic or mono).
        sigma: Extraction threshold in units of the local background RMS.
        minarea: Minimum connected pixels above threshold.
        max_stars: Keep at most this many, brightest (by flux) first.

    Returns:
        SepDetection with centroids in full-frame (y, x) pixels, or None
        if sep is unavailable or the frame is unusable.
    """
    sep = _sep_module()
    if sep is None:
        return None
    arr = np.asarray(raw_frame)
    if arr.ndim != 2 or arr.shape[0] < 8 or arr.shape[1] < 8:
        return None

    t0 = time.perf_counter()
    binned = bin2x2(arr)
    # sep requires C-contiguous native-endian float32
    data = np.ascontiguousarray(binned, dtype=np.float32)
    bkg = sep.Background(data, bw=32, bh=32)
    data_sub = data - bkg.back()
    objects = sep.extract(
        data_sub,
        thresh=sigma,
        err=bkg.rms(),
        filter_kernel=MATCHED_FILTER,
        minarea=minarea,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    order = np.argsort(objects["flux"])[::-1][:max_stars]
    top = objects[order]
    # A binned pixel (i, j) covers full-res pixels (2i, 2i+1) x (2j, 2j+1),
    # so its centre sits at 2*coord + 0.5 in full-frame coordinates.
    centroids = np.column_stack((top["y"] * 2.0 + 0.5, top["x"] * 2.0 + 0.5))

    return SepDetection(
        centroids=centroids,
        fluxes=np.asarray(top["flux"], dtype=np.float64),
        background_median=float(bkg.globalback),
        background_rms=float(bkg.globalrms),
        elapsed_ms=elapsed_ms,
    )
