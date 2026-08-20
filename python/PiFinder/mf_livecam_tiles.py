"""MF wide-angle LiveCam tile overlay and persistent exclusion helpers.

This module owns the web-facing description only.  It does not change the
camera frame or the production solver; both remain on their established path
until the wide tile solver is enabled separately.
"""

from __future__ import annotations

from typing import Any

from PiFinder.mf_manual_lens import normalise_manual_focal_length
from PiFinder.mf_wide_tiles import MFWideTilePlan, plan_wide_tiles
from PiFinder.optics import get_lens, lens_is_stated, optical_train_for_profile


MAX_WIDE_FOCAL_LENGTH_MM = 10.0
EXCLUDED_TILES_CONFIG_KEY = "mf_wide_excluded_tiles_by_optics"


def active_focal_length_mm(
    lens_key: str | None, manual_focal_length_mm: object = None
) -> float | None:
    """Return the selected focal length, preferring a valid manual override."""

    try:
        manual = normalise_manual_focal_length(manual_focal_length_mm)
    except ValueError:
        manual = None
    if manual is not None:
        return manual
    if lens_is_stated(lens_key):
        return get_lens(str(lens_key)).nominal_focal_length_mm
    return None


def wide_tiles_enabled(
    lens_key: str | None, manual_focal_length_mm: object = None
) -> bool:
    """Whether LiveCam should expose the tile tools for the selected lens."""

    focal_length = active_focal_length_mm(lens_key, manual_focal_length_mm)
    return focal_length is not None and focal_length <= MAX_WIDE_FOCAL_LENGTH_MM


def optics_key(
    camera_type: str | None, lens_key: str | None, manual_focal_length_mm: object = None
) -> str:
    """Stable storage key so exclusions never leak to another optical train."""

    focal_length = active_focal_length_mm(lens_key, manual_focal_length_mm)
    focal = "unknown" if focal_length is None else f"{focal_length:.1f}mm"
    try:
        manual = normalise_manual_focal_length(manual_focal_length_mm)
    except ValueError:
        manual = None
    lens = "manual" if manual is not None else (lens_key or "auto")
    return f"{camera_type or 'unknown'}:{lens}:{focal}"


def excluded_tile_ids(raw_value: object) -> set[str]:
    """Accept only simple tile identifiers from persisted JSON data."""

    if not isinstance(raw_value, (list, tuple, set)):
        return set()
    return {value for value in raw_value if isinstance(value, str) and value}


def overlay_payload(
    *,
    camera_type: str | None,
    lens_key: str | None,
    manual_focal_length_mm: object,
    frame_hw: tuple[int, int] | None,
    excluded_ids: object = (),
) -> dict[str, Any]:
    """Build normalized tile rectangles for an arbitrary LiveCam frame."""

    focal_length = active_focal_length_mm(lens_key, manual_focal_length_mm)
    payload: dict[str, Any] = {
        "enabled": wide_tiles_enabled(lens_key, manual_focal_length_mm),
        "focal_length_mm": focal_length,
        "optics_key": optics_key(camera_type, lens_key, manual_focal_length_mm),
        "tiles": [],
        "overlaps": [],
    }
    if not payload["enabled"] or frame_hw is None:
        return payload

    try:
        height, width = (int(frame_hw[0]), int(frame_hw[1]))
        if height <= 0 or width <= 0:
            return payload
        # resolve_camera_profile is intentionally used by the resolver in the
        # regular optics path.  Import here keeps this MF module independent
        # of the API/server lifecycle.
        from PiFinder.optics import resolve_camera_profile

        profile = resolve_camera_profile(camera_type or "")
        # Pass the validated numeric override, not the API/config-shaped
        # input object.  Besides matching the resolver contract this keeps
        # equivalent values such as "6.0" and 6.0 on the same optical path.
        full_train = optical_train_for_profile(profile, lens_key, focal_length)
        sixteen_train = optical_train_for_profile(profile, "16mm")
        plan = plan_wide_tiles(
            (height, width), full_train.fov_degrees, sixteen_train.fov_degrees
        )
    except (TypeError, ValueError):
        return payload

    selected = excluded_tile_ids(excluded_ids)
    payload["tiles"] = _normalised_tiles(plan, selected)
    payload["overlaps"] = _normalised_overlaps(plan)
    return payload


def _selection_spans(
    centres: list[float], length: int
) -> dict[int, tuple[float, float]]:
    """Split an axis at the midpoint between each neighbouring tile centre."""

    boundaries = [0.0]
    boundaries.extend((left + right) / 2.0 for left, right in zip(centres, centres[1:]))
    boundaries.append(float(length))
    return {
        index: (boundaries[index], boundaries[index + 1])
        for index in range(len(centres))
    }


def _normalised_tiles(
    plan: MFWideTilePlan, excluded_ids: set[str]
) -> list[dict[str, Any]]:
    """Return non-overlapping hit regions mapped to the real crop candidates.

    Solver crops intentionally overlap.  Rendering those literal crop boxes
    makes a web editor impossible to use, because a click can hit multiple
    candidates.  The overlay therefore divides the frame at crop-centre
    midpoints: each displayed cell selects exactly one real solver crop.
    """

    height, width = plan.frame_hw
    rows = sorted({tile.row for tile in plan.tiles})
    columns = sorted({tile.column for tile in plan.tiles})
    row_centres = [
        next(
            tile.rect.y + tile.rect.height / 2.0
            for tile in plan.tiles
            if tile.row == row
        )
        for row in rows
    ]
    column_centres = [
        next(
            tile.rect.x + tile.rect.width / 2.0
            for tile in plan.tiles
            if tile.column == column
        )
        for column in columns
    ]
    y_spans = _selection_spans(row_centres, height)
    x_spans = _selection_spans(column_centres, width)
    return [
        {
            "id": tile.tile_id,
            "x": x_spans[tile.column][0] / width,
            "y": y_spans[tile.row][0] / height,
            "width": (x_spans[tile.column][1] - x_spans[tile.column][0]) / width,
            "height": (y_spans[tile.row][1] - y_spans[tile.row][0]) / height,
            # The logical cell above is a non-overlapping click target.  Keep
            # the actual native 512px crop too, so LiveCam can show exactly
            # what an exclusion affects in the future solver.
            "crop_x": tile.rect.x / width,
            "crop_y": tile.rect.y / height,
            "crop_width": tile.rect.width / width,
            "crop_height": tile.rect.height / height,
            "central": tile.is_central,
            "excluded": tile.tile_id in excluded_ids,
        }
        for tile in plan.tiles
    ]


def _normalised_overlaps(plan: MFWideTilePlan) -> list[dict[str, float]]:
    """Return non-overlapping cells covered by two or more native tiles."""

    height, width = plan.frame_hw
    xs = sorted(
        {edge for tile in plan.tiles for edge in (tile.rect.x, tile.rect.x_end)}
    )
    ys = sorted(
        {edge for tile in plan.tiles for edge in (tile.rect.y, tile.rect.y_end)}
    )
    overlaps: list[dict[str, float]] = []
    for y0, y1 in zip(ys, ys[1:]):
        for x0, x1 in zip(xs, xs[1:]):
            centre_y, centre_x = (y0 + y1) / 2.0, (x0 + x1) / 2.0
            coverage = sum(
                tile.rect.y <= centre_y < tile.rect.y_end
                and tile.rect.x <= centre_x < tile.rect.x_end
                for tile in plan.tiles
            )
            if coverage >= 2:
                overlaps.append(
                    {
                        "x": x0 / width,
                        "y": y0 / height,
                        "width": (x1 - x0) / width,
                        "height": (y1 - y0) / height,
                    }
                )
    return overlaps
