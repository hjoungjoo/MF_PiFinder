"""MF wide-lens clear-window gate for structured cloud backgrounds.

The gate is deliberately subtractive: it never invents a centroid or moves
one.  On a strongly non-uniform background it keeps only detections in the
darkest background windows, where stable point sources survived in the
2026-08-25 6-mm replay corpus.  Uniform and normally graded skies pass
through byte-for-byte at the centroid-list boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from PiFinder.mf_livecam_tiles import active_focal_length_mm


CLOUD_CONTRAST_THRESHOLD: Final[float] = 0.55
CLEAR_WINDOW_PERCENTILE: Final[float] = 20.0


@dataclass(frozen=True)
class MFCloudWindowSelection:
    keep: np.ndarray
    active: bool
    contrast: float
    background_limit: float | None


def wide_cloud_gate_enabled(
    lens_key: str | None, manual_focal_length_mm: object
) -> bool:
    """Enable only for the sub-10-mm optical train selected right now."""

    focal = active_focal_length_mm(lens_key, manual_focal_length_mm)
    return focal is not None and 0 < focal < 10.0


def select_clear_window_candidates(
    background_map: np.ndarray,
    candidate_yx: np.ndarray,
    *,
    enabled: bool,
    contrast_threshold: float = CLOUD_CONTRAST_THRESHOLD,
    clear_percentile: float = CLEAR_WINDOW_PERCENTILE,
) -> MFCloudWindowSelection:
    """Return a mask that removes bright-cloud detections when warranted.

    ``candidate_yx`` is expressed in the same binned coordinate system as
    SEP's background map.  Cloud contrast is robustly measured as
    ``(P90 - P10) / P50``.  The fixed thresholds come from two independent
    cloudy 6-mm sequences and eight star-rich replay frames; see the MF replay
    report in ``PiFinder_data/captures/mf_replay``.

    Any malformed input fails open so this optional gate cannot cost the
    established detector path.
    """

    try:
        points = np.asarray(candidate_yx, dtype=np.float64).reshape(-1, 2)
        pass_all = np.ones(len(points), dtype=bool)
        if not enabled or len(points) == 0:
            return MFCloudWindowSelection(pass_all, False, 0.0, None)
        background = np.asarray(background_map, dtype=np.float64)
        finite = background[np.isfinite(background)]
        if background.ndim != 2 or finite.size < 16:
            return MFCloudWindowSelection(pass_all, False, 0.0, None)
        p10, p50, p90 = np.percentile(finite, (10.0, 50.0, 90.0))
        contrast = float((p90 - p10) / max(abs(float(p50)), 1.0))
        if not np.isfinite(contrast) or contrast <= float(contrast_threshold):
            return MFCloudWindowSelection(pass_all, False, contrast, None)
        limit = float(np.percentile(finite, float(clear_percentile)))
        y = np.clip(np.rint(points[:, 0]).astype(int), 0, background.shape[0] - 1)
        x = np.clip(np.rint(points[:, 1]).astype(int), 0, background.shape[1] - 1)
        keep = np.isfinite(background[y, x]) & (background[y, x] <= limit)
        return MFCloudWindowSelection(keep, True, contrast, limit)
    except Exception:
        # The caller can only consume a mask matching its candidate count.
        # Production always supplies a valid (N, 2) array; this defensive
        # path exists so the optional gate can never raise on bad input.
        try:
            count = len(candidate_yx)
        except Exception:
            count = 0
        return MFCloudWindowSelection(np.ones(count, dtype=bool), False, 0.0, None)
