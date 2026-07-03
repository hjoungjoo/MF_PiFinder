"""Shared INDI alignment helpers."""

from __future__ import annotations

from typing import Any


ALIGN_POINT_MIN = 1
ALIGN_POINT_MAX = 9
DEFAULT_ALIGN_POINTS = 3

BRIGHT_ALIGN_STARS: list[dict[str, Any]] = [
    {"name": "Sirius", "ra": 101.287155, "dec": -16.716116, "mag": -1.46},
    {"name": "Canopus", "ra": 95.987958, "dec": -52.695661, "mag": -0.74},
    {"name": "Arcturus", "ra": 213.915300, "dec": 19.182410, "mag": -0.05},
    {"name": "Vega", "ra": 279.234735, "dec": 38.783689, "mag": 0.03},
    {"name": "Capella", "ra": 79.172327, "dec": 45.997991, "mag": 0.08},
    {"name": "Rigel", "ra": 78.634467, "dec": -8.201638, "mag": 0.13},
    {"name": "Procyon", "ra": 114.825493, "dec": 5.224993, "mag": 0.34},
    {"name": "Betelgeuse", "ra": 88.792939, "dec": 7.407064, "mag": 0.42},
    {"name": "Altair", "ra": 297.695827, "dec": 8.868322, "mag": 0.77},
    {"name": "Aldebaran", "ra": 68.980163, "dec": 16.509302, "mag": 0.87},
    {"name": "Spica", "ra": 201.298247, "dec": -11.161319, "mag": 0.98},
    {"name": "Antares", "ra": 247.351915, "dec": -26.432002, "mag": 1.06},
    {"name": "Pollux", "ra": 116.328958, "dec": 28.026199, "mag": 1.14},
    {"name": "Fomalhaut", "ra": 344.412693, "dec": -29.622236, "mag": 1.16},
    {"name": "Deneb", "ra": 310.357979, "dec": 45.280338, "mag": 1.25},
    {"name": "Regulus", "ra": 152.092962, "dec": 11.967209, "mag": 1.35},
]


def clamp_align_points(value: Any) -> int:
    try:
        points = int(float(value))
    except (TypeError, ValueError):
        points = DEFAULT_ALIGN_POINTS
    return max(ALIGN_POINT_MIN, min(ALIGN_POINT_MAX, points))


def get_align_star(name: str | None) -> dict[str, Any] | None:
    if not name:
        return None
    normalized = name.strip().casefold()
    for star in BRIGHT_ALIGN_STARS:
        if star["name"].casefold() == normalized:
            return dict(star)
    return None


def next_align_star(completed: list[dict[str, Any]]) -> dict[str, Any]:
    used_names = {str(star.get("name", "")).casefold() for star in completed}
    for star in BRIGHT_ALIGN_STARS:
        if star["name"].casefold() not in used_names:
            return dict(star)
    return dict(BRIGHT_ALIGN_STARS[len(completed) % len(BRIGHT_ALIGN_STARS)])
