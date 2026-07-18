#!/usr/bin/python
# -*- coding:utf-8 -*-
"""Shared pointing coordinate selection for SkySafari/Web/LCD consumers.

The service does not own ``shared_state.solution()``.  It reads the PiFinder
estimate, IMU orientation, and cached INDI mount status, then returns a
purpose-ready coordinate state.  Mount readback is deliberately gated by an
explicit sync/alignment flag so a freshly connected mount cannot override the
PiFinder/IMU pointing before the two coordinate systems have been matched.
"""

from __future__ import annotations

import datetime
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

import numpy as np
import quaternion

from PiFinder.calc_utils import sf_utils
from PiFinder.pointing_model.imu_dead_reckoning import ImuDeadReckoning


logger = logging.getLogger("PointingCoordinateService")


ConfigGetter = Callable[[str, Any], Any]
DefaultLocationProvider = Callable[[], Any]
MountStatusProvider = Callable[[], dict[str, Any]]


QUALITY_HIGH = "high"
QUALITY_MEDIUM = "medium"
QUALITY_LOW = "low"
QUALITY_INVALID = "invalid"

SOURCE_SOLVE = "solve"
SOURCE_PIFINDER_IMU_ESTIMATE = "pifinder_imu_estimate"
SOURCE_IMU = "imu_fallback"
SOURCE_MOUNT = "mount"
SOURCE_FUSED = "mount_imu_delta"
SOURCE_UNAVAILABLE = "unavailable"

MODE_SOLVED_PRIMARY = "SOLVED_PRIMARY"
MODE_IMU_PRIMARY_UNSOLVED = "IMU_PRIMARY_UNSOLVED"
MODE_MOUNT_REFERENCE_PRIMARY = "MOUNT_REFERENCE_PRIMARY"
MODE_MOUNT_ONLY_SYNCED = "MOUNT_ONLY_SYNCED"
MODE_UNAVAILABLE = "UNAVAILABLE"

IMU_SMOOTH_SMALL_MOVE_DEGREES = 0.3
IMU_SMOOTH_MODERATE_MOVE_DEGREES = 1.5
IMU_SMOOTH_RESET_DEGREES = 5.0
IMU_SMOOTH_SMALL_ALPHA = 0.06
IMU_SMOOTH_MODERATE_ALPHA = 0.25
IMU_SMOOTH_LARGE_ALPHA = 0.65
MOUNT_IMU_DELTA_HOLD_SECONDS = 1.5
MOUNT_READBACK_MOVING_DELTA_DEGREES = 0.005
# Hysteresis rate gate for the mount+IMU-delta fusion. The IMU delta exists to
# catch physical disturbances (a bump, a push, a motor stall slip). While the
# mount is tracking, the smoothed IMU lags the slow (~15"/s) tracking motion,
# so the raw delta drifts at near-sidereal rate and would drag the fused
# coordinate off target without bound — that artifact floor was measured at a
# very stable 0.004-0.005 deg/s (indoors, no wind).
#
# A single threshold chops weak stall-slip events (measured head/tail rates
# 0.02-0.06 deg/s) into fragments, capturing only ~1/3 of the displacement.
# Hence hysteresis: an episode STARTS only above the enter rate (~7x the
# artifact floor, so tracking artifact and outdoor wind rumble never start
# one) and then keeps accumulating down to the exit rate (~4x, so a real
# event's slow tail is captured but outdoor micro-sway cannot keep an episode
# alive indefinitely). Outside an episode, steps are discarded while the
# already-applied offset is HELD (a stopped scope must keep its disturbed
# coordinate — it must not crawl back to the mount readback). The offset
# clears only when the mount frame is re-established by a sync.
IMU_DELTA_ENTER_RATE_DEG_PER_SEC = 0.03
IMU_DELTA_EXIT_RATE_DEG_PER_SEC = 0.015
# After a mount-driven slew the BNO055's internal fusion re-converges for a
# while (the orientation "slides" without any physical motion), at rates above
# the accumulation gate. Require the IMU to stay below the exit rate for this
# long after mount motion before disturbance accumulation may resume, so the
# slide is never booked as a physical push. Together with
# MOUNT_IMU_DELTA_HOLD_SECONDS this sets the total quiet time after a slew
# before IMU disturbance detection re-arms (~3 s).
IMU_DELTA_POST_MOTION_QUIET_SECONDS = 1.5


def _as_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _as_float_vector(value: Any) -> Optional[list[float]]:
    if value is None:
        return None
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return result if all(math.isfinite(item) for item in result) else None


def _wrap_angle_delta_degrees(delta: float) -> float:
    return ((delta + 180.0) % 360.0) - 180.0


def clamp_altitude_degrees(altitude: float) -> float:
    return max(-90.0, min(90.0, altitude))


def valid_radec(ra_deg: Any, dec_deg: Any) -> Optional[Tuple[float, float]]:
    ra = _as_float(ra_deg)
    dec = _as_float(dec_deg)
    if ra is None or dec is None:
        return None
    if dec < -90.0 or dec > 90.0:
        return None
    return ra % 360.0, dec


def angular_separation_degrees(
    ra_a_deg: float, dec_a_deg: float, ra_b_deg: float, dec_b_deg: float
) -> float:
    ra_a = math.radians(ra_a_deg)
    dec_a = math.radians(dec_a_deg)
    ra_b = math.radians(ra_b_deg)
    dec_b = math.radians(dec_b_deg)
    cos_sep = math.sin(dec_a) * math.sin(dec_b) + math.cos(dec_a) * math.cos(
        dec_b
    ) * math.cos(ra_a - ra_b)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def altaz_separation_degrees(
    alt_a_deg: float, az_a_deg: float, alt_b_deg: float, az_b_deg: float
) -> float:
    return angular_separation_degrees(az_a_deg, alt_a_deg, az_b_deg, alt_b_deg)


def _radec_to_unit_vector(ra_deg: float, dec_deg: float) -> np.ndarray:
    ra = math.radians(ra_deg)
    dec = math.radians(dec_deg)
    cos_dec = math.cos(dec)
    return np.array(
        [cos_dec * math.cos(ra), cos_dec * math.sin(ra), math.sin(dec)],
        dtype=float,
    )


def _unit_vector_to_radec(vector: np.ndarray) -> Tuple[float, float]:
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0 or not math.isfinite(norm):
        raise ValueError("Cannot convert a zero vector to RA/Dec")
    x, y, z = vector / norm
    ra = math.degrees(math.atan2(y, x)) % 360.0
    dec = math.degrees(math.asin(max(-1.0, min(1.0, float(z)))))
    return ra, dec


def _weighted_radec(
    a: Tuple[float, float], b: Tuple[float, float], a_weight: float, b_weight: float
) -> Tuple[float, float]:
    vector = (
        _radec_to_unit_vector(a[0], a[1]) * float(a_weight)
        + _radec_to_unit_vector(b[0], b[1]) * float(b_weight)
    )
    return _unit_vector_to_radec(vector)


@dataclass
class CoordinateSample:
    ra_deg: Optional[float] = None
    dec_deg: Optional[float] = None
    epoch: str = "session"
    alt_deg: Optional[float] = None
    az_deg: Optional[float] = None
    source: str = SOURCE_UNAVAILABLE
    quality: str = QUALITY_INVALID
    timestamp: Optional[float] = None
    valid: bool = False
    reason: str = ""
    aligned: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def radec(self) -> Optional[Tuple[float, float]]:
        if not self.valid or self.ra_deg is None or self.dec_deg is None:
            return None
        return self.ra_deg % 360.0, self.dec_deg

    @classmethod
    def invalid(
        cls,
        source: str,
        reason: str,
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> "CoordinateSample":
        return cls(
            source=source,
            quality=QUALITY_INVALID,
            valid=False,
            reason=reason,
            metadata=metadata or {},
        )


@dataclass
class CoordinateHealth:
    selected_source: str = SOURCE_UNAVAILABLE
    warnings: list[str] = field(default_factory=list)
    mount_pre_alignment_only: bool = False
    mount_separation_degrees: Optional[float] = None
    imu_mount_separation_degrees: Optional[float] = None
    mount_delta_degrees: Optional[float] = None
    imu_altaz_delta_degrees: Optional[float] = None


@dataclass
class CoordinateState:
    current: CoordinateSample
    solved: CoordinateSample
    imu: CoordinateSample
    mount: CoordinateSample
    health: CoordinateHealth
    mode: str = MODE_UNAVAILABLE
    weights: dict[str, float] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    def radec(self) -> Optional[Tuple[float, float]]:
        return self.current.radec()


class PointingCoordinateService:
    """Build and select pointing coordinates from solve, IMU, and mount data."""

    def __init__(
        self,
        *,
        mount_weight: float = 0.85,
        imu_weight_when_mount_aligned: float = 0.15,
        max_fusion_separation_degrees: float = 10.0,
    ):
        self.mount_weight = mount_weight
        self.imu_weight_when_mount_aligned = imu_weight_when_mount_aligned
        self.max_fusion_separation_degrees = max_fusion_separation_degrees
        self._mount_imu_anchor: Optional[dict[str, Any]] = None
        self._imu_delta_tracker: Optional[dict[str, Any]] = None
        self._imu_filter_altaz: Optional[Tuple[float, float]] = None
        self._mount_motion_hold_until = 0.0
        self._last_mount_motion_radec: Optional[Tuple[float, float]] = None
        self._last_mount_sample_ts: Optional[float] = None
        self._imu_delta_applied_snapshot: Optional[Tuple[float, float]] = None
        # Frame context for the mount+IMU-delta fusion (dt, location,
        # mount_type), refreshed by current_state(). None (e.g. direct
        # _select_current calls in tests) falls back to the equatorial frame.
        self._fusion_context: Optional[dict[str, Any]] = None
        self._last_health_mount_radec: Optional[Tuple[float, float]] = None
        self._last_health_imu_altaz: Optional[Tuple[float, float]] = None
        self._state_lock = threading.RLock()
        self._state: Optional[CoordinateState] = None

    def update_state(
        self,
        shared_state: Any,
        dt: Any,
        *,
        config_get: ConfigGetter,
        default_location_provider: Optional[DefaultLocationProvider] = None,
        mount_status_provider: Optional[MountStatusProvider] = None,
        imu_alignment_correction: Optional[dict[str, Any]] = None,
    ) -> CoordinateState:
        state = self.current_state(
            shared_state,
            dt,
            config_get=config_get,
            default_location_provider=default_location_provider,
            mount_status_provider=mount_status_provider,
            imu_alignment_correction=imu_alignment_correction,
        )
        with self._state_lock:
            self._state = state
        return state

    def get_state(self) -> Optional[CoordinateState]:
        with self._state_lock:
            return self._state

    def clear_state(self) -> None:
        with self._state_lock:
            self._state = None
            self._mount_imu_anchor = None
            self._imu_delta_tracker = None
            self._imu_filter_altaz = None
            self._mount_motion_hold_until = 0.0
            self._last_mount_motion_radec = None
            self._last_mount_sample_ts = None
            self._imu_delta_applied_snapshot = None

    def solved_sample(self, shared_state: Any, dt: Any) -> CoordinateSample:
        try:
            solution = shared_state.solution()
        except Exception:
            logger.debug("Could not read PiFinder solution", exc_info=True)
            return CoordinateSample.invalid(SOURCE_SOLVE, "solution unavailable")

        if not solution or not solution.has_pointing():
            return CoordinateSample.invalid(SOURCE_SOLVE, "no solved pointing")

        solve_source = self._solution_source_value(solution)
        has_plate_anchor = self._solution_has_plate_anchor(solution)
        if solve_source == "IMU" and not has_plate_anchor:
            return CoordinateSample.invalid(
                SOURCE_SOLVE, "IMU estimate has no plate-solve anchor"
            )
        if solve_source not in {"CAM", "IMU"}:
            return CoordinateSample.invalid(
                SOURCE_SOLVE, f"untrusted solution source {solve_source or 'unknown'}"
            )

        aligned = solution.pointing.aligned.estimate
        radec = valid_radec(getattr(aligned, "RA", None), getattr(aligned, "Dec", None))
        if radec is None:
            return CoordinateSample.invalid(SOURCE_SOLVE, "invalid solved RA/Dec")

        timestamp = _as_float(getattr(solution, "estimate_time", None))
        sample_source = (
            SOURCE_PIFINDER_IMU_ESTIMATE if solve_source == "IMU" else SOURCE_SOLVE
        )
        return CoordinateSample(
            ra_deg=radec[0],
            dec_deg=radec[1],
            epoch="session",
            source=sample_source,
            quality=QUALITY_HIGH if solve_source == "CAM" else QUALITY_MEDIUM,
            timestamp=timestamp,
            valid=True,
            metadata={
                "solve_source": solve_source,
                "has_plate_anchor": has_plate_anchor,
                "source_ra": radec[0],
                "source_dec": radec[1],
            },
        )

    def _solution_source_value(self, solution: Any) -> str:
        raw_source = getattr(solution, "solve_source", None)
        value = getattr(raw_source, "value", raw_source)
        return str(value or "")

    def _solution_has_plate_anchor(self, solution: Any) -> bool:
        try:
            aligned_solve = solution.pointing.aligned.solve
        except Exception:
            aligned_solve = None
        return bool(
            aligned_solve is not None
            and getattr(solution, "last_solve_success", None) is not None
        )

    def imu_altaz_degrees(
        self, imu_sample: Any, screen_direction: str
    ) -> Optional[Tuple[float, float]]:
        if not imu_sample or not imu_sample.is_calibrated():
            return None
        try:
            q_x2cam = (
                imu_sample.quat * ImuDeadReckoning._q_imu2cam(screen_direction)
            ).normalized()
        except (AttributeError, ValueError, ZeroDivisionError):
            logger.debug("Invalid IMU sample", exc_info=True)
            return None

        if not np.isfinite(quaternion.as_float_array(q_x2cam)).all():
            return None

        boresight = q_x2cam * quaternion.quaternion(0, 0, 0, 1) * q_x2cam.conj()
        east, north, up = boresight.x, boresight.y, boresight.z
        norm = math.sqrt(east * east + north * north + up * up)
        if norm <= 0:
            return None

        east, north, up = east / norm, north / norm, up / norm
        alt = math.degrees(math.asin(max(-1.0, min(1.0, up))))
        az = math.degrees(math.atan2(east, north)) % 360.0
        return alt, az

    def observer_location(
        self,
        shared_state: Any,
        default_location_provider: Optional[DefaultLocationProvider] = None,
    ) -> Any:
        try:
            location = shared_state.location()
        except Exception:
            location = None
        if location and getattr(location, "lock", False):
            return location
        if default_location_provider is not None:
            configured = default_location_provider()
            if configured:
                return configured
        return location

    def imu_sample(
        self,
        shared_state: Any,
        dt: Any,
        *,
        config_get: ConfigGetter,
        default_location_provider: Optional[DefaultLocationProvider] = None,
        imu_alignment_correction: Optional[dict[str, Any]] = None,
        apply_alignment: bool = True,
    ) -> CoordinateSample:
        if not bool(config_get("skysafari_imu_fallback", True)):
            return CoordinateSample.invalid(SOURCE_IMU, "disabled")

        location = self.observer_location(shared_state, default_location_provider)
        if not location:
            return CoordinateSample.invalid(SOURCE_IMU, "no location")
        if not getattr(location, "lock", False):
            return CoordinateSample.invalid(SOURCE_IMU, "location unlocked")
        if not dt:
            return CoordinateSample.invalid(SOURCE_IMU, "no datetime")

        screen_direction = str(config_get("screen_direction", "right"))
        imu_sample = shared_state.imu()
        altaz = self.imu_altaz_degrees(imu_sample, screen_direction)
        if altaz is None:
            return CoordinateSample.invalid(SOURCE_IMU, "no calibrated IMU orientation")

        alt, az = altaz

        if (
            apply_alignment
            and imu_alignment_correction
            and imu_alignment_correction.get("active")
        ):
            alt = clamp_altitude_degrees(
                alt + float(imu_alignment_correction.get("alt_offset", 0.0))
            )
            az = (az + float(imu_alignment_correction.get("az_offset", 0.0))) % 360.0

        aligned_alt, aligned_az = alt, az
        smoothed_alt, smoothed_az, filter_state, filter_delta = self._smooth_imu_altaz(
            alt, az
        )
        alt, az = smoothed_alt, smoothed_az

        try:
            sf_utils.set_location(location.lat, location.lon, location.altitude)
            ra_deg, dec_deg = sf_utils.altaz_to_radec(alt, az, dt)
        except Exception:
            logger.debug("Could not convert IMU Alt/Az to RA/Dec", exc_info=True)
            return CoordinateSample.invalid(SOURCE_IMU, "alt/az conversion failed")

        # Unsmoothed counterpart for the disturbance delta tracker: the
        # smoothing filter's slow convergence tail after a large move reads as
        # sustained motion and would keep accumulating into the fused offset
        # long after the scope has stopped. The raw orientation settles
        # immediately, so rate-gating on it ends the episode with the motion.
        raw_ra_deg: Optional[float] = None
        raw_dec_deg: Optional[float] = None
        try:
            raw_ra_deg, raw_dec_deg = sf_utils.altaz_to_radec(
                aligned_alt, aligned_az, dt
            )
            raw_ra_deg = raw_ra_deg % 360.0
        except Exception:
            logger.debug(
                "Could not convert raw IMU Alt/Az to RA/Dec", exc_info=True
            )

        timestamp = _as_float(getattr(imu_sample, "timestamp", None))
        quat_values = None
        quat_norm = None
        try:
            quat_values = _as_float_vector(
                quaternion.as_float_array(getattr(imu_sample, "quat", None))
            )
            if quat_values is not None:
                quat_norm = math.sqrt(sum(value * value for value in quat_values))
        except Exception:
            quat_values = None
            quat_norm = None

        return CoordinateSample(
            ra_deg=ra_deg % 360.0,
            dec_deg=dec_deg,
            epoch="session",
            alt_deg=alt,
            az_deg=az,
            source=SOURCE_IMU,
            quality=QUALITY_LOW,
            timestamp=timestamp,
            valid=True,
            metadata={
                "location_source": getattr(location, "source", ""),
                "screen_direction": screen_direction,
                "moving": bool(getattr(imu_sample, "moving", False)),
                "status": getattr(imu_sample, "status", None),
                "calibration_status": getattr(imu_sample, "calibration_status", None),
                "fusion_mode": getattr(imu_sample, "fusion_mode", ""),
                "uses_magnetometer": bool(
                    getattr(imu_sample, "uses_magnetometer", False)
                ),
                "raw_alt": altaz[0],
                "raw_az": altaz[1],
                "raw_ra": raw_ra_deg,
                "raw_dec": raw_dec_deg,
                "smoothed_alt": alt,
                "smoothed_az": az,
                "filter_state": filter_state,
                "filter_delta_degrees": filter_delta,
                "quat": quat_values,
                "quat_norm": quat_norm,
                "gyro": _as_float_vector(getattr(imu_sample, "gyro", None)),
                "accel": _as_float_vector(getattr(imu_sample, "accel", None)),
                "alignment_applied": bool(
                    apply_alignment
                    and imu_alignment_correction
                    and imu_alignment_correction.get("active")
                ),
            },
        )

    def mount_sample_from_status(
        self, status: Optional[dict[str, Any]]
    ) -> CoordinateSample:
        if not isinstance(status, dict) or not status:
            return CoordinateSample.invalid(SOURCE_MOUNT, "no mount status")

        usable, unusable_reason = self.mount_status_is_usable(status)
        if not usable:
            return CoordinateSample.invalid(
                SOURCE_MOUNT,
                unusable_reason,
                metadata={
                    "state": status.get("state"),
                    "message": status.get("message"),
                    "device": status.get("device"),
                    "park_state": status.get("park_state"),
                    "driver_mount_status": status.get("driver_mount_status"),
                    "raw_mount_status": status.get("raw_mount_status"),
                },
            )

        radec = self._extract_mount_radec(status)
        if radec is None:
            return CoordinateSample.invalid(SOURCE_MOUNT, "no mount RA/Dec readback")

        aligned = self.mount_status_is_aligned(status)
        timestamp = _as_float(status.get("updated")) or _as_float(
            status.get("timestamp")
        )
        return CoordinateSample(
            ra_deg=radec[0],
            dec_deg=radec[1],
            epoch="session",
            source=SOURCE_MOUNT,
            quality=QUALITY_MEDIUM if aligned else QUALITY_LOW,
            timestamp=timestamp,
            valid=True,
            aligned=aligned,
            metadata={
                "state": status.get("state"),
                "message": status.get("message"),
                "device": status.get("device"),
                "park_state": status.get("park_state"),
                "driver_mount_status": status.get("driver_mount_status"),
                "raw_mount_status": status.get("raw_mount_status"),
                "mount_motion_active": status.get("mount_motion_active"),
                "mount_motion_type": status.get("mount_motion_type"),
                "mount_readback_priority": status.get("mount_readback_priority"),
                "goto_motion_active": status.get("goto_motion_active"),
                "goto_refine_pending": status.get("goto_refine_pending"),
                "manual_motion_direction": status.get("manual_motion_direction"),
                "target_ra": status.get("target_ra"),
                "target_dec": status.get("target_dec"),
                "coordinate_sync": status.get("coordinate_sync"),
                "multipoint_align": status.get("multipoint_align"),
                "backlash_auto": status.get("backlash_auto"),
                "usable": True,
            },
        )

    def current_state(
        self,
        shared_state: Any,
        dt: Any,
        *,
        config_get: ConfigGetter,
        default_location_provider: Optional[DefaultLocationProvider] = None,
        mount_status_provider: Optional[MountStatusProvider] = None,
        imu_alignment_correction: Optional[dict[str, Any]] = None,
    ) -> CoordinateState:
        solved = self.solved_sample(shared_state, dt)
        imu = self.imu_sample(
            shared_state,
            dt,
            config_get=config_get,
            default_location_provider=default_location_provider,
            imu_alignment_correction=imu_alignment_correction,
        )
        mount_enabled = bool(config_get("mount_control", False))
        mount_status = mount_status_provider() if mount_enabled and mount_status_provider else {}
        mount = self.mount_sample_from_status(mount_status)

        # Context for the mount+IMU-delta fusion: the delta must be applied in
        # the mount's native axis frame (alt/az mount -> alt/az space, EQ mount
        # -> RA/Dec space), and the alt/az branch needs location + time for the
        # RA/Dec <-> alt/az conversions.
        self._fusion_context = {
            "dt": dt,
            "location": self.observer_location(
                shared_state, default_location_provider
            ),
            "mount_type": str(config_get("mount_type", "Alt/Az")),
        }

        health = CoordinateHealth()
        current = self._select_current(solved, imu, mount, health)
        mode, weights = self._mode_and_weights(solved, imu, mount, current)
        health.selected_source = current.source
        self._annotate_health(current, imu, mount, health)
        return CoordinateState(
            current=current,
            solved=solved,
            imu=imu,
            mount=mount,
            health=health,
            mode=mode,
            weights=weights,
        )

    def _mode_and_weights(
        self,
        solved: CoordinateSample,
        imu: CoordinateSample,
        mount: CoordinateSample,
        current: CoordinateSample,
    ) -> tuple[str, dict[str, float]]:
        if solved.valid:
            return MODE_SOLVED_PRIMARY, {"solve": 1.0, "imu": 0.0, "mount": 0.0}
        if current.source == SOURCE_FUSED:
            return (
                MODE_MOUNT_REFERENCE_PRIMARY,
                {
                    "solve": 0.0,
                    "mount_reference": self.mount_weight,
                    "imu_delta": self.imu_weight_when_mount_aligned,
                },
            )
        if mount.valid and mount.aligned and current.source == SOURCE_MOUNT:
            return MODE_MOUNT_ONLY_SYNCED, {"solve": 0.0, "imu": 0.0, "mount": 1.0}
        if imu.valid and current.source == SOURCE_IMU:
            mount_weight = 0.0
            if mount.valid and not mount.aligned:
                mount_weight = 0.0
            return (
                MODE_IMU_PRIMARY_UNSOLVED,
                {"solve": 0.0, "imu": 1.0, "mount_absolute": mount_weight},
            )
        return MODE_UNAVAILABLE, {"solve": 0.0, "imu": 0.0, "mount": 0.0}

    def _select_current(
        self,
        solved: CoordinateSample,
        imu: CoordinateSample,
        mount: CoordinateSample,
        health: CoordinateHealth,
    ) -> CoordinateSample:
        if solved.valid:
            return solved

        if mount.valid and mount.aligned:
            if imu.valid:
                if not self.mount_sample_allows_imu_delta(mount):
                    # The mount is moving itself (GoTo/manual/pulse): readback
                    # is authoritative and IMU steps caused by that motion must
                    # not count as a disturbance. Keep the anchor and the
                    # accumulated disturbance offset — only suspend
                    # accumulation — so an offset from an earlier physical
                    # push survives the motion instead of vanishing.
                    self._track_imu_reference_without_accumulation(mount, imu)
                    health.warnings.append(
                        "mount motion/settle active; using mount readback until stationary"
                    )
                    return self._mount_readback_sample(mount)
                fused = self._mount_with_imu_delta(mount, imu, health)
                if fused is not None:
                    return fused
            return self._mount_readback_sample(mount)

        if imu.valid:
            self._mount_imu_anchor = None
            if mount.valid and not mount.aligned:
                health.mount_pre_alignment_only = True
                health.warnings.append("mount readback ignored before sync/alignment")
            return imu

        if mount.valid and not mount.aligned:
            self._mount_imu_anchor = None
            health.mount_pre_alignment_only = True
            health.warnings.append("mount readback available but not aligned")

        return CoordinateSample.invalid(
            SOURCE_UNAVAILABLE, "no valid solve, aligned mount, or IMU fallback"
        )

    def _smooth_imu_altaz(self, alt: float, az: float) -> tuple[float, float, str, float]:
        previous = self._imu_filter_altaz
        if previous is None:
            self._imu_filter_altaz = (alt, az % 360.0)
            return alt, az % 360.0, "initial", 0.0

        delta = altaz_separation_degrees(previous[0], previous[1], alt, az)
        if delta >= IMU_SMOOTH_RESET_DEGREES:
            self._imu_filter_altaz = (alt, az % 360.0)
            return alt, az % 360.0, "reset_large_motion", delta

        if delta < IMU_SMOOTH_SMALL_MOVE_DEGREES:
            alpha = IMU_SMOOTH_SMALL_ALPHA
            state = "smoothed_small_jitter"
        elif delta < IMU_SMOOTH_MODERATE_MOVE_DEGREES:
            alpha = IMU_SMOOTH_MODERATE_ALPHA
            state = "smoothed_motion"
        else:
            alpha = IMU_SMOOTH_LARGE_ALPHA
            state = "tracking_large_motion"

        az_delta = _wrap_angle_delta_degrees((az % 360.0) - previous[1])
        smoothed_alt = previous[0] + (alt - previous[0]) * alpha
        smoothed_az = (previous[1] + az_delta * alpha) % 360.0
        self._imu_filter_altaz = (smoothed_alt, smoothed_az)
        return smoothed_alt, smoothed_az, state, delta

    def _mount_readback_sample(self, mount: CoordinateSample) -> CoordinateSample:
        metadata = dict(mount.metadata)
        metadata["motion_active"] = self.mount_sample_is_motion_active(mount)
        metadata["readback_priority"] = self.mount_sample_prefers_readback(mount)
        return CoordinateSample(
            ra_deg=mount.ra_deg,
            dec_deg=mount.dec_deg,
            epoch=mount.epoch,
            source=mount.source,
            quality=mount.quality,
            timestamp=mount.timestamp,
            valid=True,
            aligned=mount.aligned,
            metadata=metadata,
        )

    def _mount_with_imu_delta(
        self,
        mount: CoordinateSample,
        imu: CoordinateSample,
        health: CoordinateHealth,
    ) -> Optional[CoordinateSample]:
        mount_radec = mount.radec()
        imu_radec = imu.radec()
        if mount_radec is None or imu_radec is None:
            return None

        separation = angular_separation_degrees(
            mount_radec[0], mount_radec[1], imu_radec[0], imu_radec[1]
        )
        health.imu_mount_separation_degrees = separation

        if self._mount_imu_anchor_should_reset(mount, imu):
            self._mount_imu_anchor = self._new_mount_imu_anchor(mount, imu)

        anchor = self._mount_imu_anchor
        if anchor is None:
            return None

        imu_delta_ra = _wrap_angle_delta_degrees(imu_radec[0] - anchor["imu_ra"])
        imu_delta_dec = imu_radec[1] - anchor["imu_dec"]
        # Rate-gate the delta: only fast IMU motion (a real push/bump) is applied
        # to the fused coordinate. Slow accumulation — the smoothed IMU lagging
        # sidereal tracking, or sensor drift — is discarded, so the fused
        # coordinate stays anchored to the mount readback while tracking. The
        # base is the LIVE mount readback (not the anchor snapshot), so guide
        # pulses and slews move the fused coordinate directly and never need a
        # re-anchor that would wipe the disturbance offset.
        #
        # The delta is tracked and applied in the MOUNT'S NATIVE AXIS FRAME
        # (frame chosen from the mount_type config):
        # - alt/az mount: a hand push rotates about the alt/az axes, so the
        #   delta is (az, alt) and is added to the mount readback's alt/az. An
        #   az-only push then stays az-only in the fused coordinate. The old
        #   approach (component-wise RA/Dec addition) linearized a spherical
        #   displacement measured at the IMU's declination onto the mount's
        #   declination; for a large hand swing across a big IMU-vs-mount
        #   separation this produced impossible below-horizon coordinates
        #   (reproduced on hardware 2026-07-19: scope alt never below 7 deg,
        #   fused dec dove to -46).
        # - EQ mount: a rotation about the polar axis changes RA by the same
        #   angle at any declination, and the dec-axis changes dec alone, so
        #   the delta is (RA, Dec) added component-wise WITHOUT any cos(dec)
        #   rescaling.
        coords, frame = self._imu_tracker_coords(imu)
        applied_1, applied_2, gate_state, imu_rate = self._gated_imu_delta(
            coords, frame=frame
        )

        fused_radec: Optional[Tuple[float, float]] = None
        frame_metadata: dict[str, Any] = {"fusion_frame": frame}
        if frame == "altaz":
            fused_radec = self._apply_altaz_delta(
                mount_radec, applied_1, applied_2, frame_metadata, anchor, imu
            )
            if fused_radec is None:
                # Conversion failed (location/time raced away): fall back to
                # the equatorial component addition rather than dropping the
                # fused source entirely.
                frame = "equatorial"
                frame_metadata["fusion_frame"] = "equatorial_fallback"
        if fused_radec is None:
            dec_deg = max(-89.9, min(89.9, mount_radec[1] + applied_2))
            ra_deg = (mount_radec[0] + applied_1) % 360.0
            fused_radec = (ra_deg, dec_deg)

        return CoordinateSample(
            ra_deg=fused_radec[0],
            dec_deg=fused_radec[1],
            epoch="session",
            source=SOURCE_FUSED,
            quality=QUALITY_MEDIUM,
            timestamp=max(
                mount.timestamp or 0.0,
                imu.timestamp or 0.0,
            )
            or None,
            valid=True,
            aligned=True,
            metadata={
                "mode": "mount_readback_plus_imu_delta",
                "anchor_mount_ra": anchor["mount_ra"],
                "anchor_mount_dec": anchor["mount_dec"],
                "anchor_imu_ra": anchor["imu_ra"],
                "anchor_imu_dec": anchor["imu_dec"],
                "imu_delta_ra": imu_delta_ra,
                "imu_delta_dec": imu_delta_dec,
                # Frame-specific applied keys: ra/dec for the equatorial frame
                # (kept for compatibility), az/alt for the alt/az frame (set in
                # frame_metadata by _apply_altaz_delta).
                **(
                    {
                        "imu_delta_applied_ra": applied_1,
                        "imu_delta_applied_dec": applied_2,
                    }
                    if frame == "equatorial"
                    else {}
                ),
                "imu_delta_gate": gate_state,
                "imu_delta_rate_deg_per_sec": imu_rate,
                "mount_ra": mount_radec[0],
                "mount_dec": mount_radec[1],
                "imu_ra": imu_radec[0],
                "imu_dec": imu_radec[1],
                "absolute_mount_imu_separation_degrees": separation,
                "sync_key": anchor["sync_key"],
                "motion_active": False,
                **frame_metadata,
            },
        )

    def _apply_altaz_delta(
        self,
        mount_radec: Tuple[float, float],
        applied_az: float,
        applied_alt: float,
        frame_metadata: dict[str, Any],
        anchor: dict[str, Any],
        imu: CoordinateSample,
    ) -> Optional[Tuple[float, float]]:
        """Add the (az, alt) delta to the mount readback in alt/az space.

        Returns the fused RA/Dec, or None when the required location/time
        context is unavailable or a conversion fails (caller falls back to the
        equatorial component addition).

        The RA/Dec is produced DIFFERENTIALLY: fused = mount_radec +
        (radec(mount_altaz + delta) - radec(mount_altaz)). radec_to_altaz and
        altaz_to_radec are not exact inverses (epoch/precession handling
        differs by ~0.3 deg), so an absolute round trip would shift the fused
        coordinate off the mount readback even at zero delta; the differential
        form cancels that bias and guarantees fused == mount when the applied
        delta is zero. atmos=False keeps the conversions refraction-free.
        """
        ctx = self._fusion_context or {}
        dt = ctx.get("dt")
        location = ctx.get("location")
        if dt is None or location is None:
            return None
        try:
            sf_utils.set_location(location.lat, location.lon, location.altitude)
            mount_alt, mount_az = sf_utils.radec_to_altaz(
                mount_radec[0], mount_radec[1], dt, atmos=False
            )
            if mount_alt is None or mount_az is None:
                return None
            fused_az = (mount_az + applied_az) % 360.0
            fused_alt = mount_alt + applied_alt
            # Pole crossing: fold the altitude back into [-90, 90] and swing
            # the azimuth to the far side.
            if fused_alt > 90.0:
                fused_alt = 180.0 - fused_alt
                fused_az = (fused_az + 180.0) % 360.0
            elif fused_alt < -90.0:
                fused_alt = -180.0 - fused_alt
                fused_az = (fused_az + 180.0) % 360.0
            base_ra, base_dec = sf_utils.altaz_to_radec(mount_alt, mount_az, dt)
            moved_ra, moved_dec = sf_utils.altaz_to_radec(fused_alt, fused_az, dt)
            ra_deg = (
                mount_radec[0] + _wrap_angle_delta_degrees(moved_ra - base_ra)
            ) % 360.0
            dec_deg = max(
                -89.9, min(89.9, mount_radec[1] + (moved_dec - base_dec))
            )
        except Exception:
            logger.debug("Alt/Az fusion conversion failed", exc_info=True)
            return None

        frame_metadata.update(
            {
                "mount_alt": mount_alt,
                "mount_az": mount_az,
                "fused_alt": fused_alt,
                "fused_az": fused_az,
                "imu_delta_applied_az": applied_az,
                "imu_delta_applied_alt": applied_alt,
            }
        )
        anchor_alt = anchor.get("imu_alt")
        anchor_az = anchor.get("imu_az")
        imu_altaz = self._imu_raw_altaz(imu)
        if anchor_alt is not None and anchor_az is not None and imu_altaz is not None:
            frame_metadata["imu_delta_az"] = _wrap_angle_delta_degrees(
                imu_altaz[1] - anchor_az
            )
            frame_metadata["imu_delta_alt"] = imu_altaz[0] - anchor_alt
        return ra_deg % 360.0, dec_deg

    def _gated_imu_delta(
        self,
        coords: Tuple[float, float],
        *,
        frame: str = "equatorial",
        accumulate: bool = True,
    ) -> Tuple[float, float, str, float]:
        """Accumulate only fast IMU motion into the applied fusion delta.

        ``coords`` are in the mount's native axis frame: (ra, dec) for the
        equatorial frame, (az, alt) for the alt/az frame — longitude-like axis
        first in both, so the wrap and spherical-rate math is shared. A frame
        change resets the tracker (the accumulated offset is meaningless in
        the other frame).

        Returns (applied_delta_1, applied_delta_2, gate_state, rate_deg_s).
        The gate uses rate hysteresis: an accumulation episode starts above
        IMU_DELTA_ENTER_RATE_DEG_PER_SEC and keeps accumulating until the rate
        drops below IMU_DELTA_EXIT_RATE_DEG_PER_SEC, so a weak slip's slow
        head/tail is captured once the event is underway. Outside an episode,
        steps are discarded but the already-applied offset is HELD — once the
        scope stops, the fused coordinate must stay at the disturbed position,
        not creep back to the mount readback. With ``accumulate=False`` (mount
        moving itself) the IMU reference still advances so the mount's own
        motion is never counted, but the offset is untouched. The tracker
        resets when the anchor is recreated (a sync re-establishes the mount
        frame), which clears the offset.
        """
        now = time.monotonic()
        tracker = self._imu_delta_tracker
        if (
            tracker is None
            or tracker.get("anchor") is not self._mount_imu_anchor
            or tracker.get("frame") != frame
        ):
            self._imu_delta_tracker = {
                "anchor": self._mount_imu_anchor,
                "frame": frame,
                "imu_ra": coords[0],
                "imu_dec": coords[1],
                "t": now,
                "applied_ra": 0.0,
                "applied_dec": 0.0,
                "episode": False,
                "settle_pending": False,
                "quiet_since": None,
            }
            self._imu_delta_applied_snapshot = (0.0, 0.0)
            return 0.0, 0.0, "init", 0.0

        dt = max(1e-3, now - tracker["t"])
        step_ra = _wrap_angle_delta_degrees(coords[0] - tracker["imu_ra"])
        step_dec = coords[1] - tracker["imu_dec"]
        # Same spherical separation formula for both frames: axis 1 is the
        # longitude-like coordinate (ra or az), axis 2 the latitude-like one
        # (dec or alt).
        step_deg = angular_separation_degrees(
            tracker["imu_ra"], tracker["imu_dec"], coords[0], coords[1]
        )
        rate = step_deg / dt

        if not accumulate:
            tracker["episode"] = False
            tracker["settle_pending"] = True
            tracker["quiet_since"] = None
            gate_state = "suspended_mount_motion"
        elif tracker.get("settle_pending"):
            # Post-motion settle: the BNO055 orientation keeps sliding after a
            # slew; wait for a sustained quiet stretch before booking anything
            # as a disturbance again.
            if rate < IMU_DELTA_EXIT_RATE_DEG_PER_SEC:
                if tracker["quiet_since"] is None:
                    tracker["quiet_since"] = now
                if (
                    now - tracker["quiet_since"]
                    >= IMU_DELTA_POST_MOTION_QUIET_SECONDS
                ):
                    tracker["settle_pending"] = False
                    tracker["quiet_since"] = None
            else:
                tracker["quiet_since"] = None
            gate_state = "post_motion_settle"
        else:
            threshold = (
                IMU_DELTA_EXIT_RATE_DEG_PER_SEC
                if tracker.get("episode")
                else IMU_DELTA_ENTER_RATE_DEG_PER_SEC
            )
            if rate >= threshold:
                tracker["applied_ra"] += step_ra
                tracker["applied_dec"] += step_dec
                tracker["episode"] = True
                gate_state = "fast_follow"
            else:
                tracker["episode"] = False
                gate_state = "hold"

        tracker["imu_ra"] = coords[0]
        tracker["imu_dec"] = coords[1]
        tracker["t"] = now
        return (
            tracker["applied_ra"],
            tracker["applied_dec"],
            gate_state,
            rate,
        )

    def _track_imu_reference_without_accumulation(
        self, mount: CoordinateSample, imu: CoordinateSample
    ) -> None:
        """Advance the IMU delta reference while the mount moves itself.

        Called instead of the fusion while mount motion suppresses the IMU
        delta: the tracker's IMU reference must follow the (mount-driven)
        motion so it is not later misread as a physical push, while the
        accumulated disturbance offset stays intact for when the mount is
        stationary again.
        """
        mount_radec = mount.radec()
        imu_radec = imu.radec()
        if mount_radec is None or imu_radec is None:
            return
        if self._mount_imu_anchor_should_reset(mount, imu):
            self._mount_imu_anchor = self._new_mount_imu_anchor(mount, imu)
        coords, frame = self._imu_tracker_coords(imu)
        self._gated_imu_delta(coords, frame=frame, accumulate=False)

    def _imu_tracker_radec(self, imu: CoordinateSample) -> Tuple[float, float]:
        """RA/Dec the delta tracker should difference: raw (unsmoothed) when
        available, so the smoothing tail never reads as motion."""
        raw_ra = _as_float(imu.metadata.get("raw_ra"))
        raw_dec = _as_float(imu.metadata.get("raw_dec"))
        if raw_ra is not None and raw_dec is not None:
            return raw_ra, raw_dec
        radec = imu.radec()
        assert radec is not None
        return radec

    def _imu_raw_altaz(self, imu: CoordinateSample) -> Optional[Tuple[float, float]]:
        """(alt, az) the delta tracker should difference in the alt/az frame:
        raw (unsmoothed) when available, mirroring _imu_tracker_radec."""
        raw_alt = _as_float(imu.metadata.get("raw_alt"))
        raw_az = _as_float(imu.metadata.get("raw_az"))
        if raw_alt is not None and raw_az is not None:
            return raw_alt, raw_az % 360.0
        alt = _as_float(imu.alt_deg)
        az = _as_float(imu.az_deg)
        if alt is not None and az is not None:
            return alt, az % 360.0
        return None

    def _mount_axis_frame(self) -> str:
        """The mount's native axis frame from the mount_type config:
        'altaz' for an alt/az mount, 'equatorial' otherwise (EQ/GEM)."""
        ctx = self._fusion_context or {}
        mount_type = str(ctx.get("mount_type", "")).lower()
        if "alt" in mount_type and "az" in mount_type:
            return "altaz"
        return "equatorial"

    def _imu_tracker_coords(
        self, imu: CoordinateSample
    ) -> Tuple[Tuple[float, float], str]:
        """(coords, frame) for the delta tracker in the mount's native frame.

        alt/az frame coords are ordered (az, alt) — longitude-like axis first,
        matching the (ra, dec) ordering of the equatorial frame — and require
        the fusion context (dt + location) plus an IMU alt/az; anything missing
        falls back to the equatorial frame.
        """
        if self._mount_axis_frame() == "altaz":
            ctx = self._fusion_context or {}
            imu_altaz = self._imu_raw_altaz(imu)
            if (
                ctx.get("dt") is not None
                and ctx.get("location") is not None
                and imu_altaz is not None
            ):
                return (imu_altaz[1], imu_altaz[0]), "altaz"
        return self._imu_tracker_radec(imu), "equatorial"

    def _new_mount_imu_anchor(
        self, mount: CoordinateSample, imu: CoordinateSample
    ) -> dict[str, Any]:
        mount_radec = mount.radec()
        imu_radec = imu.radec()
        assert mount_radec is not None
        assert imu_radec is not None
        imu_altaz = self._imu_raw_altaz(imu)
        return {
            "mount_ra": mount_radec[0],
            "mount_dec": mount_radec[1],
            "imu_ra": imu_radec[0],
            "imu_dec": imu_radec[1],
            "imu_alt": imu_altaz[0] if imu_altaz is not None else None,
            "imu_az": imu_altaz[1] if imu_altaz is not None else None,
            "mount_timestamp": mount.timestamp,
            "sync_key": self._mount_sync_key(mount),
            "created_at": time.time(),
        }

    def _mount_imu_anchor_should_reset(
        self, mount: CoordinateSample, imu: CoordinateSample
    ) -> bool:
        mount_radec = mount.radec()
        imu_radec = imu.radec()
        if mount_radec is None or imu_radec is None:
            return False
        if self._mount_imu_anchor is None:
            return True

        # Re-anchor (which clears the applied disturbance offset) ONLY when a
        # sync re-establishes the mount frame. Mount readback movement alone
        # must not reset: the fused base follows the live readback, and a
        # pulse/slew-triggered reset would silently erase a real physical
        # disturbance offset.
        sync_key = self._mount_sync_key(mount)
        return self._mount_imu_anchor.get("sync_key") != sync_key

    def _mount_sync_key(self, mount: CoordinateSample) -> tuple[Any, ...]:
        sync = mount.metadata.get("coordinate_sync")
        if isinstance(sync, dict):
            return (
                "coordinate_sync",
                sync.get("synced_at"),
                sync.get("ra"),
                sync.get("dec"),
                sync.get("source"),
            )
        multipoint = mount.metadata.get("multipoint_align")
        if isinstance(multipoint, dict):
            return (
                "multipoint_align",
                multipoint.get("started_at"),
                multipoint.get("completed_points"),
                multipoint.get("pifinder_mount_synced"),
            )
        return ("aligned", mount.metadata.get("device"))

    def _annotate_health(
        self,
        current: CoordinateSample,
        imu: CoordinateSample,
        mount: CoordinateSample,
        health: CoordinateHealth,
    ) -> None:
        current_radec = current.radec()
        mount_radec = mount.radec()
        if current_radec is not None and mount_radec is not None:
            health.mount_separation_degrees = angular_separation_degrees(
                current_radec[0],
                current_radec[1],
                mount_radec[0],
                mount_radec[1],
            )
        if imu.valid and mount.valid and health.imu_mount_separation_degrees is None:
            imu_radec = imu.radec()
            if imu_radec is not None and mount_radec is not None:
                health.imu_mount_separation_degrees = angular_separation_degrees(
                    imu_radec[0],
                    imu_radec[1],
                    mount_radec[0],
                    mount_radec[1],
                )
        self._annotate_source_deltas(imu, mount, health)

    def _annotate_source_deltas(
        self,
        imu: CoordinateSample,
        mount: CoordinateSample,
        health: CoordinateHealth,
    ) -> None:
        mount_radec = mount.radec()
        if mount_radec is not None:
            if self._last_health_mount_radec is not None:
                health.mount_delta_degrees = angular_separation_degrees(
                    self._last_health_mount_radec[0],
                    self._last_health_mount_radec[1],
                    mount_radec[0],
                    mount_radec[1],
                )
            self._last_health_mount_radec = mount_radec
        elif self._last_health_mount_radec is not None:
            self._last_health_mount_radec = None

        if imu.valid and imu.alt_deg is not None and imu.az_deg is not None:
            imu_altaz = (float(imu.alt_deg), float(imu.az_deg))
            if self._last_health_imu_altaz is not None:
                health.imu_altaz_delta_degrees = altaz_separation_degrees(
                    self._last_health_imu_altaz[0],
                    self._last_health_imu_altaz[1],
                    imu_altaz[0],
                    imu_altaz[1],
                )
            self._last_health_imu_altaz = imu_altaz
        elif self._last_health_imu_altaz is not None:
            self._last_health_imu_altaz = None

    def _extract_mount_radec(
        self, status: dict[str, Any]
    ) -> Optional[Tuple[float, float]]:
        candidates = [
            status,
            status.get("coordinates"),
            status.get("mount"),
            status.get("readback"),
            status.get("position"),
            status.get("coordinate_sync"),
        ]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for ra_key, dec_key in (
                ("ra", "dec"),
                ("current_ra", "current_dec"),
                ("mount_ra", "mount_dec"),
                ("readback_ra", "readback_dec"),
                ("eod_ra", "eod_dec"),
            ):
                radec = valid_radec(candidate.get(ra_key), candidate.get(dec_key))
                if radec is not None:
                    return radec
        return None

    def mount_status_is_aligned(self, status: dict[str, Any]) -> bool:
        coordinate_sync = status.get("coordinate_sync")
        if isinstance(coordinate_sync, dict):
            if bool(
                coordinate_sync.get("active")
                or coordinate_sync.get("synced")
                or coordinate_sync.get("mount_synced")
            ):
                return True

        multipoint = status.get("multipoint_align")
        if isinstance(multipoint, dict):
            if bool(multipoint.get("pifinder_mount_synced")):
                return True

        return bool(
            status.get("mount_coordinate_synced")
            or status.get("pifinder_mount_synced")
            or status.get("aligned")
        )

    def mount_sample_is_motion_active(self, mount: CoordinateSample) -> bool:
        return self.mount_status_motion_active(mount.metadata)

    def mount_sample_prefers_readback(self, mount: CoordinateSample) -> bool:
        return self.mount_status_prefers_readback(mount.metadata)

    def mount_sample_allows_imu_delta(self, mount: CoordinateSample) -> bool:
        now = time.monotonic()
        mount_radec = mount.radec()
        readback_priority = self.mount_sample_prefers_readback(mount)
        readback_moving = False

        if mount_radec is not None and self._last_mount_motion_radec is not None:
            readback_delta = angular_separation_degrees(
                self._last_mount_motion_radec[0],
                self._last_mount_motion_radec[1],
                mount_radec[0],
                mount_radec[1],
            )
            readback_moving = readback_delta >= MOUNT_READBACK_MOVING_DELTA_DEGREES

        if mount_radec is not None:
            self._last_mount_motion_radec = mount_radec

        # Readback samples arrive slower than the fusion ticks, so IMU steps
        # from a mount slew can accumulate as a bogus "disturbance" during the
        # window before the next readback reveals the motion. Bookkeep per
        # fresh readback sample: while stationary, snapshot the applied offset;
        # the moment a sample shows movement, roll the offset back to the last
        # stationary snapshot, discarding only that leaked window.
        fresh_sample = (
            mount.timestamp is not None
            and mount.timestamp != self._last_mount_sample_ts
        )
        if fresh_sample:
            self._last_mount_sample_ts = mount.timestamp
            if readback_moving:
                self._rollback_imu_delta_to_snapshot()
            else:
                self._snapshot_imu_delta_applied()

        if readback_priority or readback_moving:
            self._mount_motion_hold_until = max(
                self._mount_motion_hold_until,
                now + MOUNT_IMU_DELTA_HOLD_SECONDS,
            )
            return False

        return now >= self._mount_motion_hold_until

    def _snapshot_imu_delta_applied(self) -> None:
        tracker = self._imu_delta_tracker
        if tracker is not None and tracker.get("anchor") is self._mount_imu_anchor:
            self._imu_delta_applied_snapshot = (
                tracker["applied_ra"],
                tracker["applied_dec"],
            )
        else:
            self._imu_delta_applied_snapshot = None

    def _rollback_imu_delta_to_snapshot(self) -> None:
        tracker = self._imu_delta_tracker
        snapshot = self._imu_delta_applied_snapshot
        if (
            tracker is not None
            and snapshot is not None
            and tracker.get("anchor") is self._mount_imu_anchor
        ):
            tracker["applied_ra"], tracker["applied_dec"] = snapshot
            tracker["episode"] = False

    def mount_status_motion_active(self, status: dict[str, Any]) -> bool:
        if not isinstance(status, dict):
            return False

        if "mount_motion_active" in status:
            return bool(status.get("mount_motion_active"))

        return self._legacy_mount_status_motion_active(status)

    def mount_status_prefers_readback(self, status: dict[str, Any]) -> bool:
        if not isinstance(status, dict):
            return False

        if "mount_readback_priority" in status:
            return bool(status.get("mount_readback_priority"))

        return self._legacy_mount_status_motion_active(status)

    def _legacy_mount_status_motion_active(self, status: dict[str, Any]) -> bool:
        if status.get("goto_motion_active") or status.get("goto_refine_pending"):
            return True
        if status.get("manual_motion_direction"):
            return True

        state = str(status.get("state", "")).strip().lower()
        if state in {
            "slewing",
            "refine_wait",
            "refine_sent",
            "guide_correction",
            "align_goto",
            "manual_motion",
        }:
            return True
        if state.startswith("backlash_auto_") and state not in {
            "backlash_auto_complete",
            "backlash_auto_failed",
            "backlash_auto_stopped",
        }:
            return True
        if "slew" in state or "goto" in state or "refine" in state:
            return True
        if "moving" in state or "motion" in state:
            return True

        multipoint = status.get("multipoint_align")
        if isinstance(multipoint, dict):
            align_state = str(multipoint.get("state", "")).strip().lower()
            if "goto" in align_state or "slew" in align_state:
                return True

        backlash = status.get("backlash_auto")
        if isinstance(backlash, dict):
            backlash_state = str(backlash.get("state", "")).strip().lower()
            if backlash_state in {"starting", "running"}:
                return True

        return False

    def mount_status_is_usable(self, status: dict[str, Any]) -> tuple[bool, str]:
        state = str(status.get("state", "")).strip().lower()
        message = str(status.get("message", "")).strip().lower()
        if state in {
            "disconnected",
            "disconnecting",
            "error",
            "fault",
            "failed",
            "server_offline",
            "driver_offline",
        }:
            return False, f"mount state unusable: {state}"
        if "disconnected" in message or "failed" in message:
            return False, "mount message reports disconnected/failed"

        for key in ("park_state", "driver_mount_status"):
            raw = str(status.get(key, "")).strip().lower()
            if raw and "park" in raw and "unpark" not in raw:
                return False, f"mount is parked: {key}={status.get(key)}"

        raw_status = str(status.get("raw_mount_status", ""))
        # OnStep status strings commonly start with P when parked and N when
        # not parked.  Treat only an explicit leading P as unusable here.
        if raw_status.startswith("P"):
            return False, f"mount is parked: raw_mount_status={raw_status}"

        return True, ""
