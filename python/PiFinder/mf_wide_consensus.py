"""Guarded two-or-more-tile attitude consensus for MF wide solving."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Sequence


@dataclass(frozen=True)
class MFWideAttitudeCandidate:
    tile_id: str
    ra_deg: float
    dec_deg: float
    roll_deg: float
    matches: int
    residual: float


@dataclass(frozen=True)
class MFWideConsensus:
    ra_deg: float
    dec_deg: float
    roll_deg: float
    tile_ids: tuple[str, ...]
    method: str


def angular_distance_deg(
    left: MFWideAttitudeCandidate, right: MFWideAttitudeCandidate
) -> float:
    """Great-circle distance between two candidate camera centres."""

    ra1, dec1 = math.radians(left.ra_deg), math.radians(left.dec_deg)
    ra2, dec2 = math.radians(right.ra_deg), math.radians(right.dec_deg)
    cosine = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(
        dec2
    ) * math.cos(ra1 - ra2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def roll_distance_deg(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def _mean_attitude(
    candidates: Sequence[MFWideAttitudeCandidate], method: str
) -> MFWideConsensus:
    weights = [
        max(1, candidate.matches) / max(1e-9, candidate.residual)
        for candidate in candidates
    ]
    x = sum(
        weight
        * math.cos(math.radians(candidate.dec_deg))
        * math.cos(math.radians(candidate.ra_deg))
        for weight, candidate in zip(weights, candidates)
    )
    y = sum(
        weight
        * math.cos(math.radians(candidate.dec_deg))
        * math.sin(math.radians(candidate.ra_deg))
        for weight, candidate in zip(weights, candidates)
    )
    z = sum(
        weight * math.sin(math.radians(candidate.dec_deg))
        for weight, candidate in zip(weights, candidates)
    )
    ra = math.degrees(math.atan2(y, x)) % 360.0
    dec = math.degrees(math.atan2(z, math.hypot(x, y)))
    roll_x = sum(
        weight * math.cos(math.radians(candidate.roll_deg))
        for weight, candidate in zip(weights, candidates)
    )
    roll_y = sum(
        weight * math.sin(math.radians(candidate.roll_deg))
        for weight, candidate in zip(weights, candidates)
    )
    return MFWideConsensus(
        ra,
        dec,
        math.degrees(math.atan2(roll_y, roll_x)) % 360.0,
        tuple(candidate.tile_id for candidate in candidates),
        method,
    )


def build_consensus(
    candidates: Sequence[MFWideAttitudeCandidate],
    *,
    adjacent: Callable[[str, str], bool],
    pair_position_limit_deg: float,
    pair_roll_limit_deg: float,
    multi_position_limit_deg: float,
    multi_roll_limit_deg: float,
) -> MFWideConsensus | None:
    """Return a safe pair or robust multi-tile result, otherwise ``None``.

    Exactly two candidates are accepted only when their planned tiles are
    adjacent and both camera-centre attitudes pass the stricter pair limits.
    For three or more candidates, a medoid selects inliers before averaging.
    """

    if len(candidates) < 2:
        return None
    if len(candidates) == 2:
        left, right = candidates
        if not adjacent(left.tile_id, right.tile_id):
            return None
        if angular_distance_deg(left, right) > pair_position_limit_deg:
            return None
        if roll_distance_deg(left.roll_deg, right.roll_deg) > pair_roll_limit_deg:
            return None
        return _mean_attitude(candidates, "adjacent_pair")

    medoid = min(
        candidates,
        key=lambda candidate: sum(
            angular_distance_deg(candidate, other) for other in candidates
        ),
    )
    inliers = [
        candidate
        for candidate in candidates
        if angular_distance_deg(candidate, medoid) <= multi_position_limit_deg
        and roll_distance_deg(candidate.roll_deg, medoid.roll_deg)
        <= multi_roll_limit_deg
    ]
    if len(inliers) < 3:
        return None
    return _mean_attitude(inliers, "multi_tile")
