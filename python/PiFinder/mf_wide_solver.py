"""Isolated execution policy for the opt-in MF tile recovery solver.

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
    plan_tiles_for_focal,
    tiles_are_adjacent,
)


TILE_SOLVE_TIMEOUT_MS = 350
CENTRAL_SATURATED_PIXELS = 64
PAIR_POSITION_LIMIT_DEG = 0.08
PAIR_ROLL_LIMIT_DEG = 0.15
MULTI_POSITION_LIMIT_DEG = 0.25
MULTI_ROLL_LIMIT_DEG = 0.40


@dataclass(frozen=True)
class MFWideTileScore:
    """Per-tile evidence retained even when that tile cannot solve."""

    tile_id: str
    centroid_count: int
    solved: bool
    matches: int = 0
    rmse: float | None = None
    reason: str = ""
    # Catalog-matched coordinates mapped back to the original full RAW.
    # Kept solver-local for Auto(Star) photometry; omitted from diagnostics.
    matched_centroids_raw: tuple[tuple[float, float], ...] = ()

    def as_diagnostic(self) -> dict[str, Any]:
        return {
            "id": self.tile_id,
            "centroids": self.centroid_count,
            "solved": self.solved,
            "matches": self.matches,
            "rmse": self.rmse,
            "reason": self.reason,
        }


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
    tile_scores: tuple[MFWideTileScore, ...] = ()


Detector = Callable[[np.ndarray], Iterable[tuple[float, float]]]
Solve = Callable[
    [np.ndarray, tuple[int, int], tuple[float, float], float], dict[str, Any]
]
CentroidRectifier = Callable[[MFWideTile, np.ndarray], np.ndarray]


def tile_solver_eligible(
    enabled: object, lens_key: str | None, manual_focal_length_mm: object
) -> bool:
    """True for an explicit optical selection and the opt-in recovery flag."""

    focal = active_focal_length_mm(lens_key, manual_focal_length_mm)
    return bool(enabled) and focal is not None and focal > 0


def wide_solver_eligible(
    enabled: object, lens_key: str | None, manual_focal_length_mm: object
) -> bool:
    """Compatibility name for callers predating the non-wide recovery tier."""

    return tile_solver_eligible(enabled, lens_key, manual_focal_length_mm)


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
    centroids: Iterable[tuple[float, float]],
    tile_hw: tuple[int, int],
    rotation_deg: float,
) -> np.ndarray:
    points = np.asarray(list(centroids), dtype=np.float64).reshape(-1, 2)
    rotated, _ = sfm.rotate_centroids(points, tile_hw, rotation_deg)
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
    """Solve native tile crops after the normal solver cascade has failed.

    The wide-grid strategy tries C first, then requires consensus among its
    peripheral tiles. The 10-mm-and-longer recovery grid is used after the
    production centre and full-frame paths have failed; it skips C and each
    peripheral tile reports the common camera optical-centre target.
    """

    arr = np.asarray(frame)
    central = plan.central_tile
    recovery_grid = plan.strategy == "recovery_grid"
    central_saturated = (
        central_is_saturated(arr, central, saturation_level)
        if not recovery_grid
        else False
    )
    attempted: list[str] = []
    candidate_solutions: list[tuple[MFWideTile, dict[str, Any], int]] = []
    tile_scores: list[MFWideTileScore] = []

    def detected_centroids(detector: Detector | None, tile_frame) -> list[Any]:
        """Normalise list/tuple/NumPy detector output without truth testing it."""

        if detector is None:
            return []
        detected = detector(tile_frame)
        return [] if detected is None else list(detected)

    def run_tile(tile: MFWideTile) -> tuple[dict[str, Any] | None, int]:
        attempted.append(tile.tile_id)
        tile_frame = crop_tile(arr, tile)
        raw_centroids = detected_centroids(detect_primary, tile_frame)
        if len(raw_centroids) < 4 and detect_fallback is not None:
            raw_centroids = detected_centroids(detect_fallback, tile_frame)
        if len(raw_centroids) < 4:
            tile_scores.append(
                MFWideTileScore(
                    tile.tile_id,
                    len(raw_centroids),
                    False,
                    reason="fewer_than_four_centroids",
                )
            )
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
                _rotated_local_centroids(
                    local_centroids,
                    (tile.rect.height, tile.rect.width),
                    rotation_deg,
                ),
                (tile.rect.height, tile.rect.width),
                target,
                tile_fov_degrees,
            )
            or {}
        )
        if result.get("RA") is None:
            tile_scores.append(
                MFWideTileScore(
                    tile.tile_id,
                    len(raw_centroids),
                    False,
                    int(result.get("Matches") or 0),
                    (float(result["RMSE"]) if result.get("RMSE") is not None else None),
                    "tetra_no_solution",
                )
            )
            return None, len(raw_centroids)
        normalised = _normalise_centre_solution(result)
        matched_raw: tuple[tuple[float, float], ...] = ()
        if normalised.get("matched_centroids") is not None:
            matched_rotated = np.asarray(
                normalised["matched_centroids"], dtype=np.float64
            ).reshape(-1, 2)
            _, rotated_canvas = sfm.rotate_centroids(
                np.empty((0, 2)),
                (tile.rect.height, tile.rect.width),
                rotation_deg,
            )
            matched_local, _ = sfm.rotate_centroids(
                matched_rotated,
                rotated_canvas,
                (360.0 - rotation_deg) % 360.0,
            )
            matched_global = matched_local + np.asarray(
                [tile.rect.y, tile.rect.x], dtype=np.float64
            )
            matched_raw = tuple(
                (float(point[0]), float(point[1])) for point in matched_global
            )
        tile_scores.append(
            MFWideTileScore(
                tile.tile_id,
                len(raw_centroids),
                True,
                int(normalised.get("Matches") or 0),
                (
                    float(normalised["RMSE"])
                    if normalised.get("RMSE") is not None
                    else None
                ),
                matched_centroids_raw=matched_raw,
            )
        )
        return normalised, len(raw_centroids)

    if (
        not recovery_grid
        and central.tile_id not in excluded_tile_ids
        and not central_saturated
    ):
        solution, count = run_tile(central)
        if solution is not None:
            return MFWideSolveResult(
                solution,
                "wide_central",
                False,
                tuple(attempted),
                ("C",),
                ("C",),
                count,
                tile_scores=tuple(tile_scores),
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
    if recovery_grid:
        if not candidate_solutions:
            return MFWideSolveResult(
                None,
                "recovery_no_solution",
                False,
                tuple(attempted),
                (),
                (),
                0,
                "no_usable_peripheral_tile",
                tuple(tile_scores),
            )
        # These normal/telephoto crops recover an obstructed or saturated
        # centre. A valid peripheral tetra solution already reports the
        # common optical-centre target. If several solve, prefer the lower
        # residual rather than averaging their different native fields.
        best_tile, best_solution, best_count = min(
            candidate_solutions,
            key=lambda item: (
                float(item[1].get("RMSE") or float("inf")),
                -int(item[1].get("Matches") or 0),
            ),
        )
        return MFWideSolveResult(
            best_solution,
            f"recovery_{best_tile.tile_id.lower()}",
            False,
            tuple(attempted),
            tuple(candidate.tile_id for candidate in candidates),
            (best_tile.tile_id,),
            best_count,
            "single_peripheral_recovery",
            tuple(tile_scores),
        )

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
            tuple(tile_scores),
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
        tile_scores=tuple(tile_scores),
    )


def build_plan_for_optics(
    frame_hw: tuple[int, int],
    full_fov_degrees: float,
    sixteen_mm_fov_degrees: float,
    focal_length_mm: float = 4.0,
    central_tile_size_px: int | None = None,
    display_rotation_degrees: int = 0,
) -> MFWideTilePlan:
    """Build geometry with IDs named in the user-visible video direction."""

    return plan_tiles_for_focal(
        frame_hw,
        full_fov_degrees,
        sixteen_mm_fov_degrees,
        focal_length_mm,
        central_tile_size_px=central_tile_size_px,
        display_rotation_degrees=display_rotation_degrees,
    )
