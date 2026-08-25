"""Pure centred planning for native, overlapping square solver tiles.

Every tile is at least 512x512 source pixels and targets a solver-friendly
FOV for the active lens. Tiles are centred as an odd grid so the central crop
remains on the optical centre. The planner never resizes a frame; overlap
preserves edge coverage and is exposed separately for the LiveCam display.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Final


MIN_TILE_SIZE_PX: Final[int] = 512
# The bundled Tetra database supports 10--30 degrees.  11.6 degrees stays
# clear of its lower edge while retaining enough catalogue stars without
# pulling excessive lens distortion into a recovery crop.
TARGET_SOLVER_TILE_FOV_DEGREES: Final[float] = 11.6
DEFAULT_OVERLAP: Final[float] = 0.20
RECOVERY_GRID_SIZE: Final[int] = 3
# Same-frame IMX462 field validation on 2026-08-25 found that the generic
# 512px floor left most 6mm crops with only four to six detections (1/15
# solved), while 640px retained the 3x5 grid and solved 9/15.  Keep this
# empirical exception isolated from the generic FOV calculation so changing
# lenses immediately restores the normal per-lens geometry.
SIX_MM_FOCAL_LENGTH_MM: Final[float] = 6.0
SIX_MM_FIELD_TILE_SIZE_PX: Final[int] = 640


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
    """One original-resolution solver crop."""

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
    strategy: str = "wide_grid"

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
    """Return the user-visible ID in current video coordinates.

    ``U/D/L/R`` deliberately means video up/down/left/right, not celestial
    north/east/south/west.  This single ID is used by LiveCam, exclusions,
    solver diagnostics and API responses.
    """

    dy, dx = row - central_row, column - central_column
    if dy == 0 and dx == 0:
        return "C"
    vertical = "U" if dy < 0 else "D" if dy > 0 else ""
    horizontal = "L" if dx < 0 else "R" if dx > 0 else ""
    vertical_suffix = "" if abs(dy) <= 1 else str(abs(dy))
    horizontal_suffix = "" if abs(dx) <= 1 else str(abs(dx))
    return f"{vertical}{vertical_suffix}{horizontal}{horizontal_suffix}"


def _legacy_tile_id(
    row: int, column: int, central_row: int, central_column: int
) -> str:
    """Return the pre-UDLR raw-frame ID for persisted-setting migration."""

    dy, dx = row - central_row, column - central_column
    if dy == 0 and dx == 0:
        return "C"
    vertical = "N" if dy < 0 else "S" if dy > 0 else ""
    horizontal = "W" if dx < 0 else "E" if dx > 0 else ""
    if abs(dy) <= 1 and abs(dx) <= 1:
        return vertical + horizontal
    return f"{vertical or 'C'}{abs(dy) if dy else ''}{horizontal or 'C'}{abs(dx) if dx else ''}"


def _display_grid_position(
    row: int,
    column: int,
    row_count: int,
    column_count: int,
    display_rotation_degrees: int,
) -> tuple[int, int, int, int]:
    """Map a native grid index into the displayed video coordinate system."""

    rotation = int(display_rotation_degrees) % 360
    if rotation == 90:  # np.rot90(frame, 1), counter-clockwise
        return column_count - 1 - column, row, column_count, row_count
    if rotation == 180:
        return row_count - 1 - row, column_count - 1 - column, row_count, column_count
    if rotation == 270:  # np.rot90(frame, 3), clockwise
        return column, row_count - 1 - row, column_count, row_count
    return row, column, row_count, column_count


def _display_tile_id(
    row: int,
    column: int,
    row_count: int,
    column_count: int,
    display_rotation_degrees: int,
) -> str:
    """Name a native crop by its location in the user-visible video."""

    display_row, display_column, display_rows, display_columns = _display_grid_position(
        row, column, row_count, column_count, display_rotation_degrees
    )
    return _tile_id(
        display_row,
        display_column,
        display_rows // 2,
        display_columns // 2,
    )


def optimal_tile_size_px(
    full_fov_degrees: float,
    central_tile_size_px: int,
) -> int:
    """Return the native square side suited to the active optical train.

    The computed side gives a roughly 11.6-degree crop whenever the sensor
    has enough pixels.  The established 512px minimum prevents an upscale;
    the existing central crop is the upper bound, preserving the calibrated
    16mm/longer-lens geometry.
    """

    full_fov = float(full_fov_degrees)
    central_size = int(central_tile_size_px)
    if not 0 < full_fov < 180:
        raise ValueError("full FOV must be in (0, 180)")
    if central_size < MIN_TILE_SIZE_PX:
        raise ValueError("central tile size must be at least 512px")

    requested = round(central_size * TARGET_SOLVER_TILE_FOV_DEGREES / full_fov)
    # All shipped camera dimensions are even. Keep the crop side even so the
    # centre crop remains exactly centred in pixel coordinates.
    requested -= requested % 2
    return min(central_size, max(MIN_TILE_SIZE_PX, requested))


def plan_wide_tiles(
    frame_hw: tuple[int, int],
    full_fov_degrees: float,
    sixteen_mm_fov_degrees: float,
    *,
    tile_size_px: int = MIN_TILE_SIZE_PX,
    overlap: float = DEFAULT_OVERLAP,
    display_rotation_degrees: int = 0,
) -> MFWideTilePlan:
    """Return centred, native square tiles covering the full canvas.

    ``full_fov_degrees`` and ``sixteen_mm_fov_degrees`` are retained as
    diagnostic metadata for the eventual per-tile WCS calculation.  They do
    do not select a scale factor: ``tile_size_px`` is always a native square
    size determined by the active lens geometry.
    """

    height, width = (int(frame_hw[0]), int(frame_hw[1]))
    if not 0 < full_fov_degrees < 180 or not 0 < sixteen_mm_fov_degrees < 180:
        raise ValueError("FOVs must be in (0, 180)")
    tile_size = int(tile_size_px)
    if tile_size < MIN_TILE_SIZE_PX:
        raise ValueError("tile size must be at least 512px")
    ys = _centred_axis_starts(height, tile_size, overlap)
    xs = _centred_axis_starts(width, tile_size, overlap)
    central_y = len(ys) // 2
    central_x = len(xs) // 2
    tiles = tuple(
        MFWideTile(
            _display_tile_id(
                row,
                column,
                len(ys),
                len(xs),
                display_rotation_degrees,
            ),
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


def plan_vertical_recovery_tiles(
    frame_hw: tuple[int, int],
    full_fov_degrees: float,
    *,
    tile_size_px: int | None = None,
    overlap: float = DEFAULT_OVERLAP,
    display_rotation_degrees: int = 0,
) -> MFWideTilePlan:
    """Return a centred 3x3 recovery plan for normal and telephoto lenses.

    Each crop is a native, lens-specific solver square. The grid touches every
    frame edge so a failed central/full-frame solve can still use stars near
    any corner.
    """

    height, width = (int(frame_hw[0]), int(frame_hw[1]))
    if height <= 0 or width <= 0:
        raise ValueError("frame dimensions must be positive")
    if not 0 <= overlap < 0.5:
        raise ValueError("overlap must be in [0, 0.5)")
    if not 0 < full_fov_degrees < 180:
        raise ValueError("full FOV must be in (0, 180)")

    if tile_size_px is None:
        # Compatibility fallback for direct callers without live solver
        # geometry. The real solver/API always passes crop_width_px.
        divisor = 1.0 + (RECOVERY_GRID_SIZE - 1) * (1.0 - overlap)
        tile_size = math.ceil(height / divisor)
    else:
        tile_size = int(tile_size_px)
    # An even side makes an integer-pixel crop land exactly on the optical
    # centre for the common even-sized sensor dimensions.  It can increase
    # the overlap by one pixel, never leave an edge uncovered.
    if tile_size % 2:
        tile_size += 1
    if tile_size > min(height, width):
        raise ValueError("frame is too small for the requested square recovery tile")

    vertical_span = height - tile_size
    horizontal_span = width - tile_size
    ys = tuple(
        round(index * vertical_span / (RECOVERY_GRID_SIZE - 1))
        for index in range(RECOVERY_GRID_SIZE)
    )
    xs = tuple(
        round(index * horizontal_span / (RECOVERY_GRID_SIZE - 1))
        for index in range(RECOVERY_GRID_SIZE)
    )
    central_row = RECOVERY_GRID_SIZE // 2
    central_column = RECOVERY_GRID_SIZE // 2
    tiles = tuple(
        MFWideTile(
            _display_tile_id(
                row,
                column,
                len(ys),
                len(xs),
                display_rotation_degrees,
            ),
            MFRect(y, x, tile_size, tile_size),
            row,
            column,
            row == central_row and column == central_column,
        )
        for row, y in enumerate(ys)
        for column, x in enumerate(xs)
    )
    return MFWideTilePlan(
        (height, width),
        float(full_fov_degrees),
        float(full_fov_degrees),
        float(overlap),
        tiles,
        "recovery_grid",
    )


def plan_tiles_for_focal(
    frame_hw: tuple[int, int],
    full_fov_degrees: float,
    sixteen_mm_fov_degrees: float,
    focal_length_mm: float,
    *,
    central_tile_size_px: int | None = None,
    overlap: float = DEFAULT_OVERLAP,
    display_rotation_degrees: int = 0,
) -> MFWideTilePlan:
    """Select native FOV-sized tiles and their grid strategy for a lens.

    The 6.0mm optical train uses its field-validated 640px crop when the
    camera's established central crop is large enough. Other named or manual
    focal lengths continue to use the generic FOV calculation.
    """

    reference_size = central_tile_size_px or MIN_TILE_SIZE_PX
    tile_size = optimal_tile_size_px(full_fov_degrees, reference_size)
    if math.isclose(
        float(focal_length_mm), SIX_MM_FOCAL_LENGTH_MM, rel_tol=0.0, abs_tol=0.05
    ):
        tile_size = min(reference_size, SIX_MM_FIELD_TILE_SIZE_PX)

    if focal_length_mm < 10.0:
        return plan_wide_tiles(
            frame_hw,
            full_fov_degrees,
            sixteen_mm_fov_degrees,
            tile_size_px=tile_size,
            overlap=overlap,
            display_rotation_degrees=display_rotation_degrees,
        )
    return plan_vertical_recovery_tiles(
        frame_hw,
        full_fov_degrees,
        tile_size_px=tile_size,
        overlap=overlap,
        display_rotation_degrees=display_rotation_degrees,
    )


def migrate_legacy_tile_ids(tile_ids: set[str], plan: MFWideTilePlan) -> set[str]:
    """Map pre-UDLR persisted IDs onto the same physical planned crops.

    Old IDs used raw-frame N/E/S/W labels while new IDs use displayed-video
    U/D/L/R labels.  The alphabets do not overlap (apart from ``C``), so a
    stored UDLR ID is already current and passes through unchanged.
    """

    rows = sorted({tile.row for tile in plan.tiles})
    columns = sorted({tile.column for tile in plan.tiles})
    legacy_to_current = {
        _legacy_tile_id(
            tile.row,
            tile.column,
            len(rows) // 2,
            len(columns) // 2,
        ): tile.tile_id
        for tile in plan.tiles
    }
    return {legacy_to_current.get(tile_id, tile_id) for tile_id in tile_ids}


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
