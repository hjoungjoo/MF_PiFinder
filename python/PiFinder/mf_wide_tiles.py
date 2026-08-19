"""Pure 16-mm-equivalent crop planning for MF wide-angle lenses.

The planner never resizes a frame.  It describes square, overlapping crops in
the supplied rectified/native canvas; the caller retains ownership of the
actual distortion remap and detector invocation.  Keeping it pure makes lens
changes easy to test and prevents this experimental path from altering the
current production crop.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Final


DEFAULT_OVERLAP: Final[float] = 0.20


@dataclass(frozen=True)
class MFRect:
    """Half-open pixel rectangle in a frame with ``(height, width)`` shape."""

    y: int
    x: int
    height: int
    width: int

    @property
    def y_end(self) -> int:
        return self.y + self.height

    @property
    def x_end(self) -> int:
        return self.x + self.width


@dataclass(frozen=True)
class MFWideTile:
    """One original-resolution 16-mm-equivalent solver crop."""

    tile_id: str
    rect: MFRect
    row: int
    column: int
    is_central: bool


@dataclass(frozen=True)
class MFWideTilePlan:
    """A lens/FOV-specific plan, regenerated on every lens geometry change."""

    frame_hw: tuple[int, int]
    full_fov_degrees: float
    tile_fov_degrees: float
    overlap: float
    tiles: tuple[MFWideTile, ...]

    @property
    def central_tile(self) -> MFWideTile:
        return next(tile for tile in self.tiles if tile.is_central)


def _tile_pixels(
    frame_width_px: int, full_fov_degrees: float, tile_fov_degrees: float
) -> int:
    if frame_width_px <= 0:
        raise ValueError("frame width must be positive")
    if not 0 < tile_fov_degrees <= full_fov_degrees < 180:
        raise ValueError("FOVs must satisfy 0 < tile <= full < 180")
    # Gnomonic/tangent scaling rather than linear angle scaling preserves the
    # requested central 16-mm-equivalent angular extent for a rectilinear map.
    ratio = math.tan(math.radians(tile_fov_degrees) / 2.0) / math.tan(
        math.radians(full_fov_degrees) / 2.0
    )
    return max(1, min(frame_width_px, round(frame_width_px * ratio)))


def _axis_starts(length: int, tile: int, overlap: float) -> tuple[int, ...]:
    if tile > length:
        raise ValueError("tile cannot exceed the frame axis")
    if not 0 <= overlap < 0.5:
        raise ValueError("overlap must be in [0, 0.5)")
    if tile == length:
        return (0,)
    stride = max(1, round(tile * (1.0 - overlap)))
    starts = list(range(0, length - tile + 1, stride))
    final = length - tile
    if starts[-1] != final:
        starts.append(final)
    centred = round((length - tile) / 2.0)
    if centred not in starts:
        starts.append(centred)
    return tuple(sorted(set(starts)))


def _tile_id(row: int, column: int, central_row: int, central_column: int) -> str:
    dy, dx = row - central_row, column - central_column
    if dy == 0 and dx == 0:
        return "C"
    vertical = "N" if dy < 0 else "S" if dy > 0 else ""
    horizontal = "W" if dx < 0 else "E" if dx > 0 else ""
    if abs(dy) <= 1 and abs(dx) <= 1:
        return vertical + horizontal
    return f"{vertical or 'C'}{abs(dy) if dy else ''}{horizontal or 'C'}{abs(dx) if dx else ''}"


def plan_wide_tiles(
    frame_hw: tuple[int, int],
    full_fov_degrees: float,
    sixteen_mm_fov_degrees: float,
    *,
    overlap: float = DEFAULT_OVERLAP,
) -> MFWideTilePlan:
    """Return original-resolution crops covering a wide rectified canvas.

    ``sixteen_mm_fov_degrees`` is the current sensor's measured 16-mm crop
    FOV, not the historical fixed 12-degree production solver value.
    """

    height, width = (int(frame_hw[0]), int(frame_hw[1]))
    tile_width = _tile_pixels(width, full_fov_degrees, sixteen_mm_fov_degrees)
    # The production 16-mm crop is square.  Keep that angular unit square in
    # the native/rectified pixel grid and refuse a lens/frame that cannot fit
    # it rather than resize its height.
    tile_side = min(tile_width, height)
    if tile_side <= 0:
        raise ValueError("frame dimensions must be positive")
    ys = _axis_starts(height, tile_side, overlap)
    xs = _axis_starts(width, tile_side, overlap)
    central_y = min(
        range(len(ys)), key=lambda index: abs(ys[index] + tile_side / 2 - height / 2)
    )
    central_x = min(
        range(len(xs)), key=lambda index: abs(xs[index] + tile_side / 2 - width / 2)
    )
    tiles = tuple(
        MFWideTile(
            _tile_id(row, column, central_y, central_x),
            MFRect(y, x, tile_side, tile_side),
            row,
            column,
            row == central_y and column == central_x,
        )
        for row, y in enumerate(ys)
        for column, x in enumerate(xs)
    )
    return MFWideTilePlan(
        (height, width),
        float(full_fov_degrees),
        float(sixteen_mm_fov_degrees),
        float(overlap),
        tiles,
    )


def crop_tile(frame, tile: MFWideTile):
    """Return a direct pixel crop; deliberately no resize/interpolation."""

    rect = tile.rect
    return frame[rect.y : rect.y_end, rect.x : rect.x_end]


def tiles_are_adjacent(left: MFWideTile, right: MFWideTile) -> bool:
    """True when tiles overlap or share an edge in the planned grid."""

    if left.tile_id == right.tile_id:
        return False
    a, b = left.rect, right.rect
    y_overlap = min(a.y_end, b.y_end) - max(a.y, b.y)
    x_overlap = min(a.x_end, b.x_end) - max(a.x, b.x)
    return y_overlap >= 0 and x_overlap >= 0
