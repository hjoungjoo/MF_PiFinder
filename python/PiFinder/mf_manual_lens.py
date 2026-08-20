"""MF manual focal-length override helpers (millimetres, one decimal place)."""

from __future__ import annotations

from typing import Final


MANUAL_LENS_KEY: Final[str] = "manual"
MIN_FOCAL_LENGTH_MM: Final[float] = 0.1
MAX_FOCAL_LENGTH_MM: Final[float] = 99.9


def normalise_manual_focal_length(value) -> float | None:
    """Return a one-decimal mm focal length, or ``None`` for a blank override."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        focal_length = round(float(value), 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("Manual lens focal length must be a number") from exc
    if not MIN_FOCAL_LENGTH_MM <= focal_length <= MAX_FOCAL_LENGTH_MM:
        raise ValueError(
            f"Manual lens focal length must be {MIN_FOCAL_LENGTH_MM:.1f}–{MAX_FOCAL_LENGTH_MM:.1f} mm"
        )
    return focal_length


def manual_focal_from_state(shared_state) -> float | None:
    """Read and validate the optional override without breaking legacy state."""

    getter = getattr(shared_state, "camera_lens_focal_length_mm", None)
    if not callable(getter):
        return None
    try:
        return normalise_manual_focal_length(getter())
    except (TypeError, ValueError):
        return None
