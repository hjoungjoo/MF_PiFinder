"""Safe calibration of fast RAW solutions against trusted preprocessing.

An asynchronous preprocessed solve is necessarily older than the newest RAW
solve.  Publishing it directly would move the pointing estimate backwards in
time.  This tracker instead learns the small same-frame RAW-to-preprocessed
offset and applies that bias only to future RAW solutions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from PiFinder.solve_acceptance import angular_separation_deg


def _short_delta_deg(target: float, source: float) -> float:
    return (float(target) - float(source) + 180.0) % 360.0 - 180.0


@dataclass(frozen=True)
class BiasStatus:
    accepted_samples: int
    rejected_samples: int
    ready: bool
    camera_ra_deg: float
    camera_dec_deg: float
    target_ra_deg: Optional[float]
    target_dec_deg: Optional[float]
    last_separation_deg: Optional[float]


class PreprocessBiasTracker:
    def __init__(
        self,
        *,
        max_agreement_deg: float = 0.12,
        required_samples: int = 2,
        alpha: float = 0.25,
    ) -> None:
        self.max_agreement_deg = float(max_agreement_deg)
        self.required_samples = max(1, int(required_samples))
        self.alpha = min(1.0, max(0.0, float(alpha)))
        self.reset()

    def reset(self) -> None:
        self._accepted = 0
        self._rejected = 0
        self._camera_ra = 0.0
        self._camera_dec = 0.0
        self._target_ra: Optional[float] = None
        self._target_dec: Optional[float] = None
        self._last_separation: Optional[float] = None

    @property
    def ready(self) -> bool:
        return self._accepted >= self.required_samples

    def _blend(self, current: float, sample: float) -> float:
        if self._accepted == 0:
            return float(sample)
        return float(current) + self.alpha * (float(sample) - float(current))

    def update(
        self,
        raw_solution: Mapping[str, Any],
        trusted_solution: Mapping[str, Any],
    ) -> bool:
        try:
            raw_ra = float(raw_solution["RA"])
            raw_dec = float(raw_solution["Dec"])
            trusted_ra = float(trusted_solution["RA"])
            trusted_dec = float(trusted_solution["Dec"])
        except (KeyError, TypeError, ValueError):
            self._rejected += 1
            return False

        separation = angular_separation_deg(
            raw_ra,
            raw_dec,
            trusted_ra,
            trusted_dec,
        )
        self._last_separation = separation
        if separation > self.max_agreement_deg:
            self._rejected += 1
            return False

        self._camera_ra = self._blend(
            self._camera_ra,
            _short_delta_deg(trusted_ra, raw_ra),
        )
        self._camera_dec = self._blend(
            self._camera_dec,
            trusted_dec - raw_dec,
        )

        try:
            raw_target_ra = float(raw_solution["RA_target"])
            raw_target_dec = float(raw_solution["Dec_target"])
            trusted_target_ra = float(trusted_solution["RA_target"])
            trusted_target_dec = float(trusted_solution["Dec_target"])
        except (KeyError, TypeError, ValueError):
            pass
        else:
            target_ra_sample = _short_delta_deg(trusted_target_ra, raw_target_ra)
            target_dec_sample = trusted_target_dec - raw_target_dec
            if self._target_ra is None or self._target_dec is None:
                self._target_ra = target_ra_sample
                self._target_dec = target_dec_sample
            else:
                self._target_ra = self._blend(self._target_ra, target_ra_sample)
                self._target_dec = self._blend(self._target_dec, target_dec_sample)

        self._accepted += 1
        return True

    def apply(self, solution: Mapping[str, Any]) -> dict:
        corrected = dict(solution)
        if not self.ready:
            return corrected
        try:
            corrected["RA"] = (float(solution["RA"]) + self._camera_ra) % 360.0
            corrected["Dec"] = float(solution["Dec"]) + self._camera_dec
        except (KeyError, TypeError, ValueError):
            return corrected
        if self._target_ra is not None and self._target_dec is not None:
            try:
                corrected["RA_target"] = (
                    float(solution["RA_target"]) + self._target_ra
                ) % 360.0
                corrected["Dec_target"] = (
                    float(solution["Dec_target"]) + self._target_dec
                )
            except (KeyError, TypeError, ValueError):
                pass
        return corrected

    def status(self) -> BiasStatus:
        return BiasStatus(
            accepted_samples=self._accepted,
            rejected_samples=self._rejected,
            ready=self.ready,
            camera_ra_deg=self._camera_ra,
            camera_dec_deg=self._camera_dec,
            target_ra_deg=self._target_ra,
            target_dec_deg=self._target_dec,
            last_separation_deg=self._last_separation,
        )
