"""Pure centred planning for native, overlapping square wide-angle tiles.

Every tile is at least 512x512 source pixels, independent of the lens FOV.
Tiles are centred as an odd grid so the central crop remains on the optical
centre.  The planner never resizes a frame; overlap preserves edge coverage
and is exposed separately for the LiveCam display.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Final


MIN_TILE_SIZE_PX: Final[int] = 512
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


def _centred_axis_starts(
    length: int, tile_size: int, overlap: float
) -> tuple[int, ...]:
    """Return an odd, edge-covering, centre-symmetric series of tile starts."""

    if length < tile_size:
        raise ValueError("frame is smaller than the minimum native tile size")
    if not 0 <= overlap < 0.5:
        raise ValueError("overlap must be in [0, 0.5)")
    if length == tile_size:
        return (0,)
    preferred_stride = max(1, round(tile_size * (1.0 - overlap)))
    required = math.ceil((length - tile_size) / preferred_stride) + 1
    count = required if required % 2 else required + 1
    span = length - tile_size
    return tuple(round(index * span / (count - 1)) for index in range(count))


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
    """Return centred, native square tiles covering the full canvas.

    ``full_fov_degrees`` and ``sixteen_mm_fov_degrees`` are retained as
    diagnostic metadata for the eventual per-tile WCS calculation.  They do
    not control native tile size: every tile is ``MIN_TILE_SIZE_PX`` square.
    """

    height, width = (int(frame_hw[0]), int(frame_hw[1]))
    if not 0 < sixteen_mm_fov_degrees <= full_fov_degrees < 180:
        raise ValueError("FOVs must satisfy 0 < tile <= full < 180")
    tile_size = MIN_TILE_SIZE_PX
    ys = _centred_axis_starts(height, tile_size, overlap)
    xs = _centred_axis_starts(width, tile_size, overlap)
    central_y = len(ys) // 2
    central_x = len(xs) // 2
    tiles = tuple(
        MFWideTile(
            _tile_id(row, column, central_y, central_x),
            MFRect(y, x, tile_size, tile_size),
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
