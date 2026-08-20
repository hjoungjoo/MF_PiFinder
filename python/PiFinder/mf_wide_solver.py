"""Isolated execution policy for the opt-in MF wide-angle tile solver.

The module deliberately has no process loop or shared-state writes.  The
legacy solver supplies one fresh, profile-rotated RAW frame plus detector and
tetra adapters, and receives either one normalised solution or a diagnostic
result.  Keeping that boundary narrow makes the feature flag a real rollback:
when it is off, none of this code is called.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

import numpy as np

from PiFinder import solver_frame_map as sfm
from PiFinder.mf_livecam_tiles import (
    active_focal_length_mm,
    excluded_tile_ids,
    optics_key,
)
from PiFinder.mf_wide_consensus import MFWideAttitudeCandidate, build_consensus
from PiFinder.mf_wide_tiles import (
    MFWideTile,
    MFWideTilePlan,
    crop_tile,
    plan_wide_tiles,
    tiles_are_adjacent,
)


# This is intentionally below 10.0 mm: 10 mm remains display/edit capable,
# but follows the well-tested production solver until its own field approval.
WIDE_SOLVER_MAX_FOCAL_MM = 10.0
TILE_SOLVE_TIMEOUT_MS = 350
CENTRAL_SATURATED_PIXELS = 64
PAIR_POSITION_LIMIT_DEG = 0.08
PAIR_ROLL_LIMIT_DEG = 0.15
MULTI_POSITION_LIMIT_DEG = 0.25
MULTI_ROLL_LIMIT_DEG = 0.40


@dataclass(frozen=True)
class MFWideSolveResult:
    """Outcome of one guarded wide-tile attempt."""

    solution: dict[str, Any] | None
    solve_path: str
    central_saturated: bool
    attempted_tile_ids: tuple[str, ...]
    candidate_tile_ids: tuple[str, ...]
    consensus_tile_ids: tuple[str, ...]
    centroid_count: int
    reason: str = ""


Detector = Callable[[np.ndarray], Iterable[tuple[float, float]]]
Solve = Callable[
    [np.ndarray, tuple[int, int], tuple[float, float], float], dict[str, Any]
]
CentroidRectifier = Callable[[MFWideTile, np.ndarray], np.ndarray]


def wide_solver_eligible(
    enabled: object, lens_key: str | None, manual_focal_length_mm: object
) -> bool:
    """True only for an explicit <10 mm optical selection and opt-in flag."""

    focal = active_focal_length_mm(lens_key, manual_focal_length_mm)
    return bool(enabled) and focal is not None and focal < WIDE_SOLVER_MAX_FOCAL_MM


def configured_excluded_tiles(
    raw_store: object,
    camera_type: str | None,
    lens_key: str | None,
    manual_focal_length_mm: object,
) -> set[str]:
    """Read only exclusions belonging to the current optical train."""

    if not isinstance(raw_store, dict):
        return set()
    return excluded_tile_ids(
        raw_store.get(optics_key(camera_type, lens_key, manual_focal_length_mm), [])
    )


def central_is_saturated(frame: np.ndarray, tile: MFWideTile, level: float) -> bool:
    """Conservatively recognise a moon/bright-source saturated central crop.

    A few saturated star pixels are normal and must not trigger a costly
    peripheral run.  A connected-component test is deliberately left for the
    final measurement/debug pass; this fixed count is deterministic and easy
    to inspect in field logs.
    """

    crop = np.asarray(crop_tile(frame, tile))
    return bool(np.count_nonzero(crop >= float(level)) >= CENTRAL_SATURATED_PIXELS)


def _tile_target_pixel(
    tile: MFWideTile,
    frame_hw: tuple[int, int],
    rotation_deg: float,
    production_target_yx: tuple[float, float],
    crop_width_px: int,
) -> tuple[float, float]:
    """Map the 512 production target into a rotated local tile canvas.

    Tetra3 accepts a target outside the tile.  That is crucial here: it lets
    every peripheral solution report the *camera optical centre*, rather than
    the centre of the peripheral crop.  The returned RA/Dec can consequently
    be compared directly by the consensus gate.
    """

    corners = np.asarray(
        [
            (tile.rect.y, tile.rect.x),
            (tile.rect.y, tile.rect.x_end - 1),
            (tile.rect.y_end - 1, tile.rect.x),
            (tile.rect.y_end - 1, tile.rect.x_end - 1),
        ],
        dtype=np.float64,
    )
    rotated_corners, _ = sfm.rotate_centroids(corners, frame_hw, rotation_deg)
    origin_yx = np.min(rotated_corners, axis=0)
    _, canvas = sfm.rotate_centroids(np.empty((0, 2)), frame_hw, rotation_deg)
    target = sfm.map_target_pixel_to_frame(production_target_yx, canvas, crop_width_px)
    return (float(target[0] - origin_yx[0]), float(target[1] - origin_yx[1]))


def _rotated_local_centroids(
    centroids: Iterable[tuple[float, float]], rotation_deg: float
) -> np.ndarray:
    points = np.asarray(list(centroids), dtype=np.float64).reshape(-1, 2)
    rotated, _ = sfm.rotate_centroids(points, (512, 512), rotation_deg)
    return rotated


def _normalise_centre_solution(solution: dict[str, Any]) -> dict[str, Any]:
    """Use RA/Dec at the common camera optical centre for voting/publication."""

    out = dict(solution)
    if out.get("RA_target") is not None and out.get("Dec_target") is not None:
        out["RA"] = out["RA_target"]
        out["Dec"] = out["Dec_target"]
    return out


def _candidate(
    tile: MFWideTile, solution: dict[str, Any]
) -> MFWideAttitudeCandidate | None:
    try:
        if solution.get("RA") is None or solution.get("Dec") is None:
            return None
        return MFWideAttitudeCandidate(
            tile.tile_id,
            float(solution["RA"]),
            float(solution["Dec"]),
            float(solution["Roll"]),
            int(solution.get("Matches") or 0),
            max(1e-6, float(solution.get("RMSE") or 1.0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def solve_wide_tiles(
    *,
    frame: np.ndarray,
    plan: MFWideTilePlan,
    excluded_tile_ids: set[str],
    saturation_level: float,
    rotation_deg: float,
    crop_width_px: int,
    production_target_yx: tuple[float, float],
    tile_fov_degrees: float,
    detect_primary: Detector,
    detect_fallback: Detector | None,
    solve: Solve,
    rectify_centroids: CentroidRectifier | None = None,
) -> MFWideSolveResult:
    """Try C first, then all usable peripheral tiles and guarded consensus.

    The caller invokes this only after the existing centred tier did not
    yield a solution.  A normal C tile result is still valid on its own.  A
    saturated/failed C tile makes every non-excluded peripheral tile eligible;
    one peripheral result never leaves this function as a position.
    """

    arr = np.asarray(frame)
    central = plan.central_tile
    central_saturated = central_is_saturated(arr, central, saturation_level)
    attempted: list[str] = []
    candidate_solutions: list[tuple[MFWideTile, dict[str, Any], int]] = []

    def run_tile(tile: MFWideTile) -> tuple[dict[str, Any] | None, int]:
        attempted.append(tile.tile_id)
        raw_centroids = list(detect_primary(crop_tile(arr, tile)) or ())
        if len(raw_centroids) < 4 and detect_fallback is not None:
            raw_centroids = list(detect_fallback(crop_tile(arr, tile)) or ())
        if len(raw_centroids) < 4:
            return None, len(raw_centroids)
        target = _tile_target_pixel(
            tile,
            plan.frame_hw,
            rotation_deg,
            production_target_yx,
            crop_width_px,
        )
        local_centroids = np.asarray(raw_centroids, dtype=np.float64).reshape(-1, 2)
        if rectify_centroids is not None:
            local_centroids = np.asarray(
                rectify_centroids(tile, local_centroids), dtype=np.float64
            ).reshape(-1, 2)
        result = (
            solve(
                _rotated_local_centroids(local_centroids, rotation_deg),
                (512, 512),
                target,
                tile_fov_degrees,
            )
            or {}
        )
        if result.get("RA") is None:
            return None, len(raw_centroids)
        return _normalise_centre_solution(result), len(raw_centroids)

    if central.tile_id not in excluded_tile_ids and not central_saturated:
        solution, count = run_tile(central)
        if solution is not None:
            return MFWideSolveResult(
                solution, "wide_central", False, tuple(attempted), ("C",), ("C",), count
            )

    # A central exclusion is treated like an unusable centre: it is a user
    # instruction, so only the peripheral consensus may publish.
    for tile in plan.tiles:
        if tile.is_central or tile.tile_id in excluded_tile_ids:
            continue
        solution, count = run_tile(tile)
        if solution is not None:
            candidate_solutions.append((tile, solution, count))

    candidates = [
        candidate
        for tile, solution, _count in candidate_solutions
        if (candidate := _candidate(tile, solution)) is not None
    ]
    tile_by_id = {tile.tile_id: tile for tile in plan.tiles}
    consensus = build_consensus(
        candidates,
        adjacent=lambda left, right: tiles_are_adjacent(
            tile_by_id[left], tile_by_id[right]
        ),
        pair_position_limit_deg=PAIR_POSITION_LIMIT_DEG,
        pair_roll_limit_deg=PAIR_ROLL_LIMIT_DEG,
        multi_position_limit_deg=MULTI_POSITION_LIMIT_DEG,
        multi_roll_limit_deg=MULTI_ROLL_LIMIT_DEG,
    )
    if consensus is None:
        return MFWideSolveResult(
            None,
            "wide_no_consensus",
            central_saturated,
            tuple(attempted),
            tuple(candidate.tile_id for candidate in candidates),
            (),
            sum(count for _tile, _solution, count in candidate_solutions),
            "need_adjacent_pair_or_three_tile_consensus",
        )

    # Preserve normal tetra diagnostics from the best residual candidate,
    # then replace the attitude with the robust consensus value.
    best = min(
        (
            (tile, solution)
            for tile, solution, _count in candidate_solutions
            if tile.tile_id in consensus.tile_ids
        ),
        key=lambda item: float(item[1].get("RMSE") or float("inf")),
    )[1]
    solution = dict(best)
    solution.update(
        {
            "RA": consensus.ra_deg,
            "Dec": consensus.dec_deg,
            "Roll": consensus.roll_deg,
            "RA_target": consensus.ra_deg,
            "Dec_target": consensus.dec_deg,
            "wide_consensus_tiles": list(consensus.tile_ids),
        }
    )
    return MFWideSolveResult(
        solution,
        f"wide_{consensus.method}",
        central_saturated,
        tuple(attempted),
        tuple(candidate.tile_id for candidate in candidates),
        consensus.tile_ids,
        sum(count for _tile, _solution, count in candidate_solutions),
    )


def build_plan_for_optics(
    frame_hw: tuple[int, int], full_fov_degrees: float, sixteen_mm_fov_degrees: float
) -> MFWideTilePlan:
    """Named seam for the solver; keeps the source geometry testable."""

    return plan_wide_tiles(frame_hw, full_fov_degrees, sixteen_mm_fov_degrees)
