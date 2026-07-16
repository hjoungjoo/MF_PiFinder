#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
This module is runs a lightweight
server to accept socket connections
and report telescope position
Protocol based on Meade LX200

This is used by SkySafari (iOS, iPadOS)
"""

import socket
import logging
import queue as queue_module
import re
import datetime
import json
import os
import threading
from multiprocessing import Queue
from typing import Optional, Tuple, Union
from PiFinder import config, utils
from PiFinder.calc_utils import FastAltAz, ra_to_deg, dec_to_deg, sf_utils
from PiFinder.composite_object import CompositeObject, MagnitudeObject, SizeObject
from PiFinder.multiproclogging import MultiprocLogging
from PiFinder.pointing_coordinate_service import PointingCoordinateService
from PiFinder.state import Location as StateLocation
from PiFinder.types.positioning import AlignCancel, AlignOnRaDec
import sys
import time

logger = logging.getLogger("PosServer")

sr_result = None
sd_result = None
last_target_coordinates: Optional[Tuple[float, float]] = None
sequence = 0
ui_queue: Queue
mountcontrol_queue: Optional[Queue] = None
goto_guide_queue: Optional[Queue] = None
align_command_queue: Optional[Queue] = None
align_response_queue: Optional[Queue] = None
console_queue: Optional[Queue] = None
is_stellarium = False
pos_server_config: Optional[config.Config] = None

_POINTING_UPDATE_SECONDS = 0.2
_CONFIG_RELOAD_SECONDS = 1.0
_ALIGN_TIMEOUT_SECONDS = 2.0
_GUIDE_LEASE_SECONDS = 1.2
_GUIDE_KEEPALIVE_SECONDS = 0.4
_GUIDE_RESTART_SECONDS = 8.0
_GUIDE_MAX_HOLD_SECONDS = 60.0
_MOUNT_STATUS_CACHE_SECONDS = 0.2
_SKYSAFARI_SLEW_GRACE_SECONDS = 2.0
_SKYSAFARI_SLEW_STATES = {"slewing", "refine_wait", "refine_sent"}
_mount_status_cache = {
    "time": 0.0,
    "value": None,
}
_coordinate_service_thread: Optional[threading.Thread] = None
_coordinate_service_stop: Optional[threading.Event] = None
_skysafari_slew_started_at = 0.0
_skysafari_saw_mount_slew = False
_config_last_loaded = 0.0
_fallback_log_last = 0.0
_pointing_debug_last = {
    "time": 0.0,
    "signature": None,
}
_imu_alignment_correction = {
    "active": False,
    "alt_offset": 0.0,
    "az_offset": 0.0,
    "set_at": 0.0,
    "target_coordinates": None,
}
_GUIDE_DIRECTIONS = {
    "Mn": "north",
    "Ms": "south",
    "Me": "east",
    "Mw": "west",
}
_guide_motion_lock = threading.Lock()
_guide_motion_state = {
    "direction": None,
    "timer": None,
    "token": 0,
    "started_at": 0.0,
    "next_restart_at": 0.0,
}
_coordinate_service = PointingCoordinateService()
_POINTING_STATUS_FILE = utils.data_dir / "pointing_coordinate_status.json"
# Cross-process "Reset Pointing" request. The web server (server.py) and the
# LCD UI (main.py) run in separate processes and share no queue with the
# pointing coordinate service, so they drop this request file (mirroring the
# backlash stop-request pattern) and the coordinate-service loop below polls
# for it and calls clear_state(). See mf_coordinate_helper_plan.
_POINTING_RESET_REQUEST_FILE = utils.data_dir / "pointing_reset_request.json"
_pointing_reset_last_at = 0.0

def _get_config_option(option: str, default):
    global _config_last_loaded
    if pos_server_config is None:
        return default
    now = time.monotonic()
    if now - _config_last_loaded > _CONFIG_RELOAD_SECONDS:
        try:
            pos_server_config.load_config()
        except Exception:
            logger.warning("Could not reload SkySafari server config", exc_info=True)
        _config_last_loaded = now
    return pos_server_config.get_option(option, default)


def _log_fallback_skip(reason: str) -> None:
    global _fallback_log_last
    now = time.monotonic()
    if now - _fallback_log_last < 5.0:
        return
    _fallback_log_last = now
    logger.info("SkySafari IMU fallback unavailable: %s", reason)


def _format_pointing_pair(sample) -> str:
    radec = sample.radec()
    if radec is None:
        return "-"
    return f"{radec[0]:.3f},{radec[1]:.3f}"


def _sample_status_payload(sample) -> dict:
    radec = sample.radec()
    return {
        "valid": bool(sample.valid),
        "source": sample.source,
        "quality": sample.quality,
        "reason": sample.reason,
        "ra": radec[0] if radec is not None else None,
        "dec": radec[1] if radec is not None else None,
        "alt": sample.alt_deg,
        "az": sample.az_deg,
        "timestamp": sample.timestamp,
        "aligned": bool(sample.aligned),
        "metadata": sample.metadata,
    }


def _write_pointing_status(state) -> None:
    try:
        utils.create_path(utils.data_dir)
        payload = {
            "updated": time.time(),
            "last_reset_at": _pointing_reset_last_at,
            "selected_source": state.current.source,
            "mode": state.mode,
            "weights": state.weights,
            "current": _sample_status_payload(state.current),
            "solved": _sample_status_payload(state.solved),
            "imu": _sample_status_payload(state.imu),
            "mount": _sample_status_payload(state.mount),
            "health": {
                "warnings": list(state.health.warnings),
                "mount_pre_alignment_only": bool(
                    state.health.mount_pre_alignment_only
                ),
                "mount_separation_degrees": state.health.mount_separation_degrees,
                "imu_mount_separation_degrees": (
                    state.health.imu_mount_separation_degrees
                ),
                "mount_delta_degrees": state.health.mount_delta_degrees,
                "imu_altaz_delta_degrees": state.health.imu_altaz_delta_degrees,
            },
        }
        tmp_status = _POINTING_STATUS_FILE.with_name(
            f"{_POINTING_STATUS_FILE.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        )
        with open(tmp_status, "w", encoding="utf-8") as status_out:
            json.dump(payload, status_out, indent=2, sort_keys=True)
            status_out.flush()
            os.fsync(status_out.fileno())
        tmp_status.replace(_POINTING_STATUS_FILE)
    except Exception:
        logger.exception("Could not write SkySafari pointing status")


def _log_pointing_state(state) -> None:
    global _pointing_debug_last
    if not all(
        hasattr(state, attr) for attr in ("current", "solved", "imu", "mount", "health")
    ):
        return
    current = state.current
    solved = state.solved
    imu = state.imu
    mount = state.mount
    health = state.health
    current_radec = current.radec()
    signature = (
        current.source,
        current.reason,
        round(current_radec[0], 3) if current_radec is not None else None,
        round(current_radec[1], 3) if current_radec is not None else None,
        round(imu.alt_deg, 2) if imu.alt_deg is not None else None,
        round(imu.az_deg, 2) if imu.az_deg is not None else None,
        imu.metadata.get("filter_state"),
        solved.valid,
        solved.reason,
        imu.valid,
        imu.reason,
        mount.valid,
        mount.aligned,
        mount.reason,
        tuple(health.warnings),
    )
    now = time.monotonic()
    if signature == _pointing_debug_last["signature"] and (
        now - _pointing_debug_last["time"] < 2.0
    ):
        return
    _write_pointing_status(state)
    _pointing_debug_last = {
        "time": now,
        "signature": signature,
    }
    logger.info(
        "SkySafari pointing source=%s coord=%s solved=%s/%s/%s imu=%s/%s "
        "imu_altaz=%s,%s mount=%s/aligned=%s/%s mount_coord=%s warnings=%s",
        current.source,
        _format_pointing_pair(current),
        solved.valid,
        solved.source,
        solved.reason or solved.metadata.get("solve_source", ""),
        imu.valid,
        imu.reason or imu.quality,
        f"{imu.alt_deg:.2f}" if imu.alt_deg is not None else "-",
        f"{imu.az_deg:.2f}" if imu.az_deg is not None else "-",
        mount.valid,
        mount.aligned,
        mount.reason or mount.quality,
        _format_pointing_pair(mount),
        "; ".join(health.warnings) or "-",
    )


def _invalidate_pointing_cache() -> None:
    _pointing_debug_last["time"] = 0.0
    _pointing_debug_last["signature"] = None


def _wrap_angle_delta_degrees(delta: float) -> float:
    return ((delta + 180.0) % 360.0) - 180.0


def _clamp_altitude_degrees(altitude: float) -> float:
    return max(-90.0, min(90.0, altitude))


def _reset_imu_alignment_correction(
    reason: str, *, clear_coordinate_state: bool = True
) -> None:
    if not _imu_alignment_correction["active"]:
        return
    logger.info("SkySafari IMU alignment correction reset: %s", reason)
    _imu_alignment_correction.update(
        {
            "active": False,
            "alt_offset": 0.0,
            "az_offset": 0.0,
            "set_at": 0.0,
            "target_coordinates": None,
        }
    )
    _invalidate_pointing_cache()
    if clear_coordinate_state:
        _coordinate_service.clear_state()


def _apply_imu_alignment_correction(alt: float, az: float) -> Tuple[float, float]:
    if not _imu_alignment_correction["active"]:
        return alt, az

    corrected_alt = _clamp_altitude_degrees(
        alt + float(_imu_alignment_correction["alt_offset"])
    )
    corrected_az = (az + float(_imu_alignment_correction["az_offset"])) % 360.0
    return corrected_alt, corrected_az


def _format_ra_degrees(ra_degrees: float) -> str:
    total_seconds = round(((ra_degrees % 360.0) / 15.0) * 3600.0)
    total_seconds %= 24 * 3600
    hh = total_seconds // 3600
    mm = (total_seconds % 3600) // 60
    ss = total_seconds % 60
    return f"{hh:02.0f}:{mm:02.0f}:{ss:02.0f}"


def _format_dec_degrees(dec_degrees: float) -> str:
    sign = "-" if dec_degrees < 0 else "+"
    total_seconds = min(round(abs(dec_degrees) * 3600.0), 90 * 3600)
    d = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{sign}{d:02d}*{m:02d}'{s:02d}"


def _imu_altaz_degrees(
    imu_sample, screen_direction: str
) -> Optional[Tuple[float, float]]:
    return _coordinate_service.imu_altaz_degrees(imu_sample, screen_direction)


def _configured_default_location() -> Optional[StateLocation]:
    configured = _get_config_option("locations.default", None)
    if configured is None:
        return None
    return StateLocation(
        lat=float(configured.latitude),
        lon=float(configured.longitude),
        altitude=float(configured.height),
        source=f"CONFIG: {configured.name}",
        lock=True,
        lock_type=2,
        error_in_m=float(configured.error_in_m),
    )


def _observer_location(shared_state) -> Optional[StateLocation]:
    location = shared_state.location()
    if location and location.lock:
        return location
    configured = _configured_default_location()
    if configured:
        return configured
    return location


def _requested_coordinates_to_altaz(
    ra_deg: float, dec_deg: float, location: StateLocation, dt
) -> Tuple[float, Optional[float]]:
    # SkySafari/LX200 coordinates are treated as the requested mount
    # coordinates for the current session.  Do not precess or otherwise
    # reinterpret them here; only convert the same coordinate frame to Alt/Az
    # for IMU alignment.
    return FastAltAz(location.lat, location.lon, dt).radec_to_altaz(
        ra_deg,
        dec_deg,
        alt_only=False,
    )


def _imu_fallback_pointing(
    shared_state, dt, apply_alignment: bool = True
) -> Optional[Tuple[float, float]]:
    sample = _coordinate_service.imu_sample(
        shared_state,
        dt,
        config_get=_get_config_option,
        default_location_provider=_configured_default_location,
        imu_alignment_correction=_imu_alignment_correction,
        apply_alignment=apply_alignment,
    )
    if not sample.valid:
        _log_fallback_skip(sample.reason or "unavailable")
        return None
    return sample.radec()


def _current_datetime(shared_state):
    dt = shared_state.datetime()
    if dt is not None:
        return dt
    return datetime.datetime.now(datetime.timezone.utc)


def _update_coordinate_service_state(shared_state):
    dt = _current_datetime(shared_state)
    state = _coordinate_service.update_state(
        shared_state,
        dt,
        config_get=_get_config_option,
        default_location_provider=_configured_default_location,
        mount_status_provider=_mount_control_status,
        imu_alignment_correction=_imu_alignment_correction,
    )
    _log_pointing_state(state)
    if state.solved.valid:
        _reset_imu_alignment_correction(
            "plate solve available",
            clear_coordinate_state=False,
        )
    return state


def _align_mount_to_imu_on_reset(shared_state) -> Optional[Tuple[float, float]]:
    """Re-align the mount to the current IMU coordinate (no-solve reset).

    When there is no valid plate solve, a bare ``clear_state()`` is not enough:
    the mount is still "aligned" (previously synced), so the selection priority
    keeps returning the mount coordinate and the display stays on the (possibly
    diverged) mount value instead of the IMU. To make the coordinate reflect the
    IMU, sync the mount to the current IMU RA/Dec. Sync only redefines the
    mount's coordinate system; it does not slew the scope. Returns the IMU
    RA/Dec that was applied, or None if no sync was issued.
    """
    state = _coordinate_service.get_state()
    if state is None:
        return None
    if state.solved is not None and state.solved.valid:
        # A solve is available; let it drive the coordinate. No IMU alignment.
        return None
    if not _mount_control_enabled():
        # No mount to align; clear_state alone will fall through to the IMU.
        return None
    # Use the raw IMU coordinate (apply_alignment=False), not the cached
    # state.imu sample: that sample was computed with any SkySafari alignment
    # offset applied, and reset must discard that offset, not re-bake it into
    # the mount's coordinate system.
    dt = _current_datetime(shared_state)
    imu_radec = _imu_fallback_pointing(shared_state, dt, apply_alignment=False)
    if imu_radec is None:
        logger.info("Pointing reset: no valid IMU coordinate to align the mount to")
        return None
    ra_deg, dec_deg = imu_radec
    mountcontrol_queue.put({"type": "sync", "ra": ra_deg, "dec": dec_deg})
    logger.info(
        "Pointing reset: synced mount to IMU coordinate RA %.4f Dec %.4f",
        ra_deg,
        dec_deg,
    )
    return ra_deg, dec_deg


def _handle_pointing_reset_request(shared_state) -> None:
    """Poll for a "Reset Pointing" request file and reinitialize the service.

    Reset discards the accumulated fusion anchor / IMU-delta and any SkySafari
    IMU alignment offset so the coordinate re-baselines on the next tick from
    the best available source. When there is no plate solve it additionally
    syncs the mount to the current raw IMU coordinate
    (``_align_mount_to_imu_on_reset``) so the display follows the IMU rather
    than staying on the previously-synced mount value. This is the operator's
    escape hatch when the fused coordinate has diverged from the sky
    (bad/absent solves, a sync to the wrong target, or indoor IMU drift
    accumulating on the fused source).
    """
    global _pointing_reset_last_at
    try:
        if not _POINTING_RESET_REQUEST_FILE.exists():
            return
        source = "unknown"
        try:
            with open(_POINTING_RESET_REQUEST_FILE, encoding="utf-8") as request_in:
                request = json.load(request_in)
            source = str(request.get("source", "unknown"))
        except (json.JSONDecodeError, OSError):
            pass
        # Consume the request first so a failure below cannot spin-loop on it.
        try:
            _POINTING_RESET_REQUEST_FILE.unlink()
        except FileNotFoundError:
            pass
        # Discard any SkySafari IMU alignment offset first: reset must return
        # to the raw IMU coordinate, not carry a stale (possibly wrong-target)
        # alignment forward. clear_state runs below either way.
        _reset_imu_alignment_correction(
            "pointing reset request", clear_coordinate_state=False
        )
        # Capture the IMU coordinate and align the mount to it (no-solve case)
        # BEFORE clearing state, since clear_state() drops the cached samples.
        aligned = _align_mount_to_imu_on_reset(shared_state)
        _coordinate_service.clear_state()
        _invalidate_pointing_cache()
        # A reset re-establishes the coordinate frame; a tracking target from
        # before the reset (possibly hours stale or below the horizon) must
        # not drive a recovery slew against the new frame.
        if goto_guide_queue is not None:
            goto_guide_queue.put({"type": "clear_tracking_target"})
        _pointing_reset_last_at = time.time()
        logger.info(
            "Pointing coordinate service reset (source=%s, imu_align=%s)",
            source,
            "yes" if aligned is not None else "no",
        )
    except Exception:
        logger.exception("Could not handle pointing reset request")


def _coordinate_service_loop(shared_state, stop_event: threading.Event) -> None:
    logger.info("Pointing coordinate service loop started")
    while not stop_event.is_set():
        try:
            _handle_pointing_reset_request(shared_state)
            _update_coordinate_service_state(shared_state)
        except Exception:
            logger.exception("Pointing coordinate service update failed")
        stop_event.wait(_POINTING_UPDATE_SECONDS)
    logger.info("Pointing coordinate service loop stopped")


def _start_coordinate_service_loop(shared_state) -> None:
    global _coordinate_service_thread, _coordinate_service_stop
    if (
        _coordinate_service_thread is not None
        and _coordinate_service_thread.is_alive()
    ):
        return
    _coordinate_service_stop = threading.Event()
    _coordinate_service_thread = threading.Thread(
        target=_coordinate_service_loop,
        args=(shared_state, _coordinate_service_stop),
        name="PointingCoordinateService",
        daemon=True,
    )
    _coordinate_service_thread.start()


def _current_pointing(_shared_state) -> Optional[Tuple[float, float]]:
    state = _coordinate_service.get_state()
    if state is None:
        logger.debug("No published pointing coordinate state yet")
        return None
    return state.radec()



def get_telescope_ra(shared_state, _):
    """
    Extract RA from current solution
    format for LX200 protocol
    RA = HH:MM:SS
    """
    pointing = _current_pointing(shared_state)
    if pointing is None:
        return "00:00:00"

    ra_result = _format_ra_degrees(pointing[0])
    logger.debug("get_telescope_ra: RA result: %s", ra_result)
    return ra_result


def get_telescope_dec(shared_state, _):
    """
    Extract DEC from current solution
    format for LX200 protocol
    DEC = +/- DD*MM'SS
    """
    pointing = _current_pointing(shared_state)
    if pointing is None:
        return "+00*00'01"

    dec_result = _format_dec_degrees(pointing[1])
    logger.debug("get_telescope_dec: Dec result: %s", dec_result)
    return dec_result


def get_distance_bars(_shared_state, _input_str):
    global _skysafari_slew_started_at, _skysafari_saw_mount_slew
    status = _mount_control_status()
    state = str((status or {}).get("state", ""))
    now = time.monotonic()

    if state in _SKYSAFARI_SLEW_STATES:
        _skysafari_saw_mount_slew = True
        return "\x7f"

    if (
        _skysafari_slew_started_at
        and not _skysafari_saw_mount_slew
        and now - _skysafari_slew_started_at < _SKYSAFARI_SLEW_GRACE_SECONDS
    ):
        return "\x7f"

    _skysafari_slew_started_at = 0.0
    _skysafari_saw_mount_slew = False
    return ""


def _mark_skysafari_slew_started() -> None:
    global _skysafari_slew_started_at, _skysafari_saw_mount_slew
    _skysafari_slew_started_at = time.monotonic()
    _skysafari_saw_mount_slew = False


def _mount_control_status() -> dict:
    now = time.monotonic()
    if now - _mount_status_cache["time"] <= _MOUNT_STATUS_CACHE_SECONDS:
        return _mount_status_cache["value"] or {}

    status_file = utils.data_dir / "mount_control_status.json"
    try:
        with open(status_file, encoding="utf-8") as status_in:
            status = json.load(status_in)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        status = {}

    _mount_status_cache["time"] = now
    _mount_status_cache["value"] = status
    return status


def get_firmware_date(_shared_state, _input_str):
    return "Jan 28 2026"


def get_firmware_version(_shared_state, _input_str):
    return "01.0"


def get_product(_shared_state, _input_str):
    return "PiFinder"


def get_firmware_time(_shared_state, _input_str):
    return "17:25:00"


def _skysafari_lx200_mount_code() -> str:
    override = str(_get_config_option("skysafari_lx200_mount_code", "auto")).strip()
    override = override.upper()
    if override in {"A", "P", "G"}:
        return override

    mount_type = str(_get_config_option("mount_type", "Alt/Az")).strip().lower()
    if "alt" in mount_type and "az" in mount_type:
        return "A"
    return "P"


def get_status(_shared_state, _input_str):
    # LX200-style mount status: geometry, tracking, alignment.
    # A=Alt/Az, P=polar/equatorial, G=German equatorial override.
    return f"{_skysafari_lx200_mount_code()}T1"


def respond_none(shared_state, input_str):
    return None


def respond_zero(shared_state, input_str):
    return "0"


def respond_one(shared_state, input_str):
    return "1"


def _mount_control_enabled() -> bool:
    enabled = bool(_get_config_option("mount_control", False))
    has_queue = mountcontrol_queue is not None
    if not enabled or not has_queue:
        logger.info(
            "SkySafari mount-control unavailable: mount_control=%s queue=%s",
            enabled,
            has_queue,
        )
    return bool(enabled and has_queue)


def _goto_guide_enabled() -> bool:
    enabled = bool(_get_config_option("mount_control", False))
    has_queue = goto_guide_queue is not None
    if not enabled or not has_queue:
        logger.info(
            "SkySafari GoTo/Guide service unavailable: mount_control=%s queue=%s",
            enabled,
            has_queue,
        )
    return bool(enabled and has_queue)


def _has_solved_pointing(shared_state) -> bool:
    try:
        solution = shared_state.solution()
    except Exception:
        logger.debug("Could not read PiFinder solution state", exc_info=True)
        return False
    return bool(solution and solution.has_pointing())


def _queue_indi_goto_if_enabled(shared_state, ra_deg: float, dec_deg: float) -> bool:
    multipoint_active = _multipoint_align_active()
    if multipoint_active:
        if not _mount_control_enabled():
            return False
    elif not _goto_guide_enabled():
        return False

    if not _get_config_option("skysafari_indi_goto", False) and not multipoint_active:
        logger.info("SkySafari INDI GoTo skipped; skysafari_indi_goto is off")
        return False

    has_solved_pointing = _has_solved_pointing(shared_state)
    refine_after_goto = bool(_get_config_option("indi_goto_refine_once", False))
    if multipoint_active:
        refine_after_goto = False
    if refine_after_goto and not has_solved_pointing:
        logger.info("SkySafari INDI GoTo queued without refine; PiFinder is not solved")
        refine_after_goto = False

    if multipoint_active:
        command = {
            "type": "multipoint_align_goto_target",
            "ra": ra_deg,
            "dec": dec_deg,
            "name": "SkySafari Target",
        }
        mountcontrol_queue.put(command)
        logger.info(
            "SkySafari multi-point align GoTo queued: RA %.4f Dec %.4f",
            ra_deg,
            dec_deg,
        )
    else:
        command = {
            "type": "goto_target",
            "ra": ra_deg,
            "dec": dec_deg,
            "refine_after_goto": refine_after_goto,
            "refine_accuracy_arcmin": float(
                _get_config_option("indi_goto_refine_accuracy_arcmin", 6.0)
            ),
        }
        goto_guide_queue.put(command)
        logger.info(
            "SkySafari INDI GoTo routed to GoTo/Guide service: RA %.4f Dec %.4f",
            ra_deg,
            dec_deg,
        )
    return True


def _queue_indi_sync_if_enabled(ra_deg: float, dec_deg: float) -> bool:
    if not _mount_control_enabled():
        return False
    sync_enabled = bool(_get_config_option("skysafari_indi_sync", False))
    goto_forwarding_enabled = bool(_get_config_option("skysafari_indi_goto", False))
    if not (sync_enabled or goto_forwarding_enabled):
        logger.info("SkySafari INDI sync skipped; SkySafari mount forwarding is off")
        return False

    mountcontrol_queue.put(
        {
            "type": "sync",
            "ra": ra_deg,
            "dec": dec_deg,
        }
    )
    logger.info("SkySafari INDI sync queued: RA %.4f Dec %.4f", ra_deg, dec_deg)
    return True


def _multipoint_align_active() -> bool:
    session = _mount_control_status().get("multipoint_align")
    if not isinstance(session, dict):
        return False
    return bool(session.get("active"))


def _queue_multipoint_align_confirm_if_active(ra_deg: float, dec_deg: float) -> bool:
    if not _multipoint_align_active():
        return False
    if not _mount_control_enabled():
        return False

    mountcontrol_queue.put(
        {
            "type": "multipoint_align_confirm",
            "ra": ra_deg,
            "dec": dec_deg,
            "source": "skysafari",
        }
    )
    logger.info(
        "SkySafari sync routed to INDI multi-point alignment: RA %.4f Dec %.4f",
        ra_deg,
        dec_deg,
    )
    if console_queue is not None:
        console_queue.put("INDI Multi Align Point")
    return True


def _set_imu_alignment_from_target_if_no_solve(
    shared_state, ra_deg: float, dec_deg: float
) -> bool:
    if not _get_config_option("skysafari_imu_align_without_solve", True):
        return False
    if _has_solved_pointing(shared_state):
        return False

    location = _observer_location(shared_state)
    if not location or not location.lock:
        logger.warning("SkySafari IMU align skipped; location is not locked")
        return False

    dt = _current_datetime(shared_state)
    if not dt:
        logger.warning("SkySafari IMU align skipped; no PiFinder time")
        return False

    screen_direction = _get_config_option("screen_direction", "right")
    imu_altaz = _imu_altaz_degrees(shared_state.imu(), screen_direction)
    if imu_altaz is None:
        logger.warning("SkySafari IMU align skipped; no calibrated IMU orientation")
        return False

    try:
        target_alt, target_az = _requested_coordinates_to_altaz(
            ra_deg,
            dec_deg,
            location,
            dt,
        )
    except Exception:
        logger.warning(
            "SkySafari IMU align skipped; target alt/az failed", exc_info=True
        )
        return False
    if target_az is None:
        logger.warning("SkySafari IMU align skipped; target azimuth missing")
        return False

    imu_alt, imu_az = imu_altaz
    alt_offset = target_alt - imu_alt
    az_offset = _wrap_angle_delta_degrees(target_az - imu_az)
    _imu_alignment_correction.update(
        {
            "active": True,
            "alt_offset": alt_offset,
            "az_offset": az_offset,
            "set_at": time.monotonic(),
            "target_coordinates": (ra_deg % 360.0, dec_deg),
        }
    )
    _invalidate_pointing_cache()
    _coordinate_service.clear_state()

    if console_queue is not None:
        console_queue.put("SkySafari IMU Alignment Set")
    logger.info(
        "SkySafari IMU alignment set without solve: "
        "target_alt=%.3f target_az=%.3f imu_alt=%.3f imu_az=%.3f "
        "alt_offset=%.3f az_offset=%.3f",
        target_alt,
        target_az,
        imu_alt,
        imu_az,
        alt_offset,
        az_offset,
    )
    return True


def _align_pifinder_if_enabled(shared_state, ra_deg: float, dec_deg: float) -> bool:
    if not _get_config_option("skysafari_pifinder_align", True):
        return False
    if align_command_queue is None or align_response_queue is None:
        return False

    while True:
        try:
            align_response_queue.get(block=False)
        except queue_module.Empty:
            break

    align_command_queue.put(AlignOnRaDec(ra=ra_deg, dec=dec_deg))

    response = None
    start = time.time()
    while response is None:
        if time.time() - start > _ALIGN_TIMEOUT_SECONDS:
            align_command_queue.put(AlignCancel())
            if console_queue is not None:
                console_queue.put("SkySafari Align Timeout")
            logger.warning("SkySafari PiFinder align timed out")
            return False
        try:
            response = align_response_queue.get(block=False)
        except queue_module.Empty:
            time.sleep(0.05)

    target_pixel = response.as_target_pixel()
    if target_pixel[0] == -1:
        logger.warning("SkySafari PiFinder align failed")
        return False

    shared_state.set_target_pixel(target_pixel)
    if pos_server_config is not None:
        pos_server_config.set_option("target_pixel", target_pixel)
    if console_queue is not None:
        console_queue.put("SkySafari Alignment Set")
    ui_queue.put("reload_config")
    logger.info("SkySafari PiFinder align set target pixel: %s", target_pixel)
    return True


def _target_from_parsed_coordinates() -> Optional[Tuple[float, float]]:
    if not sr_result or not sd_result:
        return None

    ra = ra_to_deg(*sr_result)
    dec = dec_to_deg(*sd_result)
    return ra % 360.0, dec


def _queue_mountcontrol_command(command: dict) -> bool:
    if mountcontrol_queue is None:
        return False
    mountcontrol_queue.put(command)
    return True


def _schedule_skysafari_guide_keepalive_locked(token: int) -> None:
    timer = threading.Timer(
        _GUIDE_KEEPALIVE_SECONDS,
        _skysafari_guide_keepalive_if_current,
        args=(token,),
    )
    timer.daemon = True
    _guide_motion_state["timer"] = timer
    timer.start()


def _skysafari_guide_keepalive_if_current(token: int) -> None:
    command_type = "manual_movement_keepalive"
    with _guide_motion_lock:
        if token != _guide_motion_state["token"]:
            return
        direction = _guide_motion_state["direction"]
        if not direction:
            return

        now = time.monotonic()
        started_at = float(_guide_motion_state.get("started_at") or now)
        if now - started_at >= _GUIDE_MAX_HOLD_SECONDS:
            _guide_motion_state["token"] = int(_guide_motion_state["token"]) + 1
            _guide_motion_state["direction"] = None
            _guide_motion_state["timer"] = None
            _guide_motion_state["started_at"] = 0.0
            _guide_motion_state["next_restart_at"] = 0.0
            direction = None
        else:
            next_restart_at = float(_guide_motion_state.get("next_restart_at") or 0.0)
            if next_restart_at and now >= next_restart_at:
                command_type = "manual_movement"
                _guide_motion_state["next_restart_at"] = now + _GUIDE_RESTART_SECONDS
            _schedule_skysafari_guide_keepalive_locked(token)

    if direction is None:
        _queue_mountcontrol_command({"type": "stop_movement"})
        logger.warning("SkySafari guide maximum hold time exceeded; stop queued")
        return

    if token != _guide_motion_state["token"]:
        return
    if not direction:
        return

    _queue_mountcontrol_command(
        {
            "type": command_type,
            "direction": direction,
            "lease_seconds": _GUIDE_LEASE_SECONDS,
        }
    )
    logger.debug("SkySafari guide %s queued: %s", command_type, direction)


def _start_skysafari_guide_keepalive(direction: str) -> Optional[str]:
    with _guide_motion_lock:
        previous_direction = _guide_motion_state["direction"]
        timer = _guide_motion_state.get("timer")
        if timer is not None:
            timer.cancel()

        _guide_motion_state["token"] = int(_guide_motion_state["token"]) + 1
        token = int(_guide_motion_state["token"])
        _guide_motion_state["direction"] = direction
        _guide_motion_state["started_at"] = time.monotonic()
        _guide_motion_state["next_restart_at"] = (
            _guide_motion_state["started_at"] + _GUIDE_RESTART_SECONDS
        )
        _schedule_skysafari_guide_keepalive_locked(token)
        return previous_direction if isinstance(previous_direction, str) else None


def _stop_skysafari_guide_keepalive() -> bool:
    with _guide_motion_lock:
        had_direction = _guide_motion_state["direction"] is not None
        timer = _guide_motion_state.get("timer")
        _guide_motion_state["token"] = int(_guide_motion_state["token"]) + 1
        _guide_motion_state["direction"] = None
        _guide_motion_state["timer"] = None
        _guide_motion_state["started_at"] = 0.0
        _guide_motion_state["next_restart_at"] = 0.0

    if timer is not None:
        timer.cancel()
    return had_direction


def handle_guide_move(_shared_state, input_str: str):
    command = extract_command(input_str)
    direction = _GUIDE_DIRECTIONS.get(command)
    if not direction:
        return None

    if _mount_control_enabled() and mountcontrol_queue is not None:
        previous_direction = _start_skysafari_guide_keepalive(direction)
        if previous_direction and previous_direction != direction:
            _queue_mountcontrol_command({"type": "stop_movement"})
        _queue_mountcontrol_command(
            {
                "type": "manual_movement",
                "direction": direction,
                "lease_seconds": _GUIDE_LEASE_SECONDS,
            }
        )
        logger.debug("SkySafari guide move queued: %s", direction)
    else:
        logger.debug("SkySafari guide move ignored; INDI mount control is disabled")
    return None


def handle_guide_stop(_shared_state, _input_str: str):
    had_active_motion = _stop_skysafari_guide_keepalive()
    if goto_guide_queue is not None:
        goto_guide_queue.put({"type": "stop_movement"})
    if (
        _mount_control_enabled() or had_active_motion
    ) and mountcontrol_queue is not None:
        _queue_mountcontrol_command({"type": "stop_movement"})
        logger.debug("SkySafari guide stop queued")
    else:
        logger.debug("SkySafari guide stop ignored; INDI mount control is disabled")
    return None


def not_implemented(shared_state, input_str):
    # return "not implemented"
    return respond_none(shared_state, input_str)


def _match_to_hms(pattern: str, input_str: str) -> Union[Tuple[int, int, int], None]:
    match = re.match(pattern, input_str)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        return hours, minutes, seconds
    else:
        return None


def parse_sr_command(_, input_str: str):
    global sr_result
    pattern = r":Sr([-+]?\d{2}):(\d{2}):(\d{2})#"
    match = _match_to_hms(pattern, input_str)
    logger.debug("Parsing sr command, match: %s", match)
    if match:
        sr_result = match
        return "1"
    else:
        return "0"


def parse_sd_command(shared_state, input_str: str):
    global sd_result
    pattern = r":Sd([-+]?\d{2})\*(\d{2}):(\d{2})#"
    match = _match_to_hms(pattern, input_str)
    logger.debug("Parsing sd command, match: %s, sr_result: %s", match, sr_result)
    if match and sr_result:
        sd_result = match
        return "1"
    else:
        return "0"


def handle_slew_command(shared_state, _input_str: str):
    if sr_result and sd_result:
        logger.info("SkySafari :MS# received; queuing GoTo for stored target")
        handle_goto_command(shared_state, sr_result, sd_result)
        _mark_skysafari_slew_started()
        # LX200 :MS# returns 0 when the slew starts. 1 means "object below
        # horizon", so do not forward the target-coordinate ACK here.
        return "0"
    logger.warning("SkySafari GoTo ignored; target coordinates are incomplete")
    return "1"


def handle_sync_command(shared_state, _input_str: str):
    parsed_target = _target_from_parsed_coordinates()
    target_source = "parsed_coordinates" if parsed_target is not None else "last_goto"
    target = parsed_target or last_target_coordinates
    if target is None:
        logger.warning("SkySafari sync ignored; no target coordinates")
        return "No target."

    ra_deg, dec_deg = target
    if _queue_multipoint_align_confirm_if_active(ra_deg, dec_deg):
        return "Coordinates matched."

    has_solved_pointing = _has_solved_pointing(shared_state)
    pifinder_aligned = False
    imu_aligned = False
    if has_solved_pointing:
        _reset_imu_alignment_correction("SkySafari sync with solved pointing")
        pifinder_aligned = _align_pifinder_if_enabled(shared_state, ra_deg, dec_deg)
    else:
        imu_aligned = _set_imu_alignment_from_target_if_no_solve(
            shared_state, ra_deg, dec_deg
        )
    indi_synced = _queue_indi_sync_if_enabled(ra_deg, dec_deg)
    logger.info(
        "SkySafari sync handled: target_source=%s pifinder_aligned=%s "
        "imu_aligned=%s indi_synced=%s target=%.4f,%.4f",
        target_source,
        pifinder_aligned,
        imu_aligned,
        indi_synced,
        ra_deg,
        dec_deg,
    )
    return "Coordinates matched."


def handle_goto_command(shared_state, ra_parsed, dec_parsed):
    global sequence, ui_queue, is_stellarium, last_target_coordinates
    ra = ra_to_deg(*ra_parsed)
    dec = dec_to_deg(*dec_parsed)
    target_ra, target_dec = ra % 360.0, dec
    sequence += 1
    last_target_coordinates = (target_ra, target_dec)
    logger.debug("Goto target coordinates: %s, %s", target_ra, target_dec)
    if _multipoint_align_active():
        logger.info(
            "SkySafari GoTo routed to INDI multi-point alignment without "
            "push-to UI transition: RA %.4f Dec %.4f",
            target_ra,
            target_dec,
        )
        _queue_indi_goto_if_enabled(shared_state, target_ra, target_dec)
        return "1"

    constellation = sf_utils.radec_to_constellation(target_ra, target_dec)
    obj = CompositeObject.from_dict(
        {
            "id": -1,
            "object_id": sys.maxsize - sequence,
            "obj_type": "",
            "ra": target_ra,
            "dec": target_dec,
            "const": constellation,
            "size": SizeObject([]),
            "mag": MagnitudeObject([]),
            "catalog_code": "PUSH",
            "sequence": sequence,
            "description": f"Skysafari object nr {sequence}",
        }
    )
    logger.debug("handle_goto_command: Pushing object: %s", obj)
    shared_state.ui_state().add_recent(obj)
    shared_state.ui_state().set_new_pushto(True)
    ui_queue.put("push_object")
    _queue_indi_goto_if_enabled(shared_state, target_ra, target_dec)
    return "1"


# Function to extract command
def extract_command(s):
    match = re.search(r":([A-Za-z]+)", s)
    return match.group(1) if match else None


def _pop_lx200_message(buffer: str) -> Tuple[Optional[str], str]:
    ack_index = buffer.find("\x06")
    command_index = buffer.find(":")

    if ack_index != -1 and (command_index == -1 or ack_index < command_index):
        return "\x06", buffer[ack_index + 1 :]

    if command_index == -1:
        return None, buffer[-1:] if buffer.endswith(":") else ""

    if command_index > 0:
        buffer = buffer[command_index:]

    end_index = buffer.find("#")
    if end_index == -1:
        return None, buffer

    return buffer[: end_index + 1], buffer[end_index + 1 :]


def _format_lx200_response(out_data: str) -> bytes:
    is_bare_status = bool(re.fullmatch(r"[APG]T\d", out_data))
    response = out_data if out_data in ("0", "1") or is_bare_status else out_data + "#"
    return response.encode()


lx_command_dict = {
    "D": get_distance_bars,
    "GD": get_telescope_dec,
    "GR": get_telescope_ra,
    "GVD": get_firmware_date,
    "GVN": get_firmware_version,
    "GVP": get_product,
    "GVT": get_firmware_time,
    "GW": get_status,
    "CM": handle_sync_command,
    "Mn": handle_guide_move,
    "Ms": handle_guide_move,
    "Me": handle_guide_move,
    "Mw": handle_guide_move,
    "Qn": handle_guide_stop,
    "Qs": handle_guide_stop,
    "Qe": handle_guide_stop,
    "Qw": handle_guide_stop,
    "RS": respond_none,  # Set slew rate to max
    "RM": respond_none,  # Set slew rate to find
    "RC": respond_none,  # Set slew rate to center
    "RG": respond_none,  # Set slew rate to guide
    "MS": handle_slew_command,  # Slew to object
    "Q": handle_guide_stop,  # Abort
    "U": respond_none,  # Precision toggle
    "Sd": parse_sd_command,  # Set declination
    "Sr": parse_sr_command,  # Set RA
}


def setup_server_socket():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("", 4030))
    server_socket.listen(1)
    return server_socket


def handle_client(client_socket, shared_state):
    global is_stellarium
    client_socket.settimeout(60)
    client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    is_stellarium = False
    input_buffer = ""

    while True:
        try:
            in_data = client_socket.recv(1024).decode()
            if not in_data:
                break

            input_buffer += in_data
            logging.debug("Received from skysafari: %s", in_data)
            while input_buffer:
                message, input_buffer = _pop_lx200_message(input_buffer)
                if message is None:
                    break

                # Special case for the ACK command in the LX200 protocol sent by Stellarium.
                if message == "\x06":
                    is_stellarium = True
                    # A indicates alt-az mode.
                    client_socket.send("A".encode())
                    continue

                command = extract_command(message)
                if not command:
                    continue
                command_handler = lx_command_dict.get(command, not_implemented)
                out_data = command_handler(shared_state, message)
                if out_data is not None:
                    client_socket.send(_format_lx200_response(out_data))
        except socket.timeout:
            logging.warning("Connection timed out.")
            break
        except ConnectionResetError:
            logging.warning("Client disconnected unexpectedly.")
            break

    client_socket.close()


def run_server(
    shared_state,
    p_ui_queue,
    log_queue,
    p_mountcontrol_queue=None,
    p_goto_guide_queue=None,
    p_align_command_queue=None,
    p_align_response_queue=None,
    p_console_queue=None,
):
    MultiprocLogging.configurer(log_queue)
    global ui_queue, mountcontrol_queue, goto_guide_queue, pos_server_config
    global _config_last_loaded
    global align_command_queue, align_response_queue, console_queue
    ui_queue = p_ui_queue
    mountcontrol_queue = p_mountcontrol_queue
    goto_guide_queue = p_goto_guide_queue
    align_command_queue = p_align_command_queue
    align_response_queue = p_align_response_queue
    console_queue = p_console_queue
    pos_server_config = config.Config()
    _config_last_loaded = time.monotonic()
    logger = logging.getLogger(__name__)
    _start_coordinate_service_loop(shared_state)

    while True:
        try:
            with setup_server_socket() as server_socket:
                logger.info("SkySafari server started and listening")
                while True:
                    client_socket, address = server_socket.accept()
                    logger.debug("New connection from %s", address)
                    handle_client(client_socket, shared_state)
        except Exception:
            logger.exception("Unexpected server error")
            logger.info("Attempting to restart server in 5 seconds...")
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Server shutting down...")
            break
