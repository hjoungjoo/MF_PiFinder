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
import math
import queue as queue_module
import re
import datetime
import json
from multiprocessing import Queue
from typing import Optional, Tuple, Union
import numpy as np
import quaternion
from PiFinder import config, utils
from PiFinder.calc_utils import ra_to_deg, dec_to_deg, sf_utils
from PiFinder.composite_object import CompositeObject, MagnitudeObject, SizeObject
from PiFinder.multiproclogging import MultiprocLogging
from PiFinder.pointing_model.imu_dead_reckoning import ImuDeadReckoning
from PiFinder.state import Location as StateLocation
from PiFinder.types.positioning import AlignCancel, AlignOnRaDec
from skyfield.positionlib import position_of_radec
import sys
import time

logger = logging.getLogger("PosServer")

sr_result = None
sd_result = None
last_target_j2000: Optional[Tuple[float, float]] = None
sequence = 0
ui_queue: Queue
mountcontrol_queue: Optional[Queue] = None
align_command_queue: Optional[Queue] = None
align_response_queue: Optional[Queue] = None
console_queue: Optional[Queue] = None
is_stellarium = False
pos_server_config: Optional[config.Config] = None

_POINTING_CACHE_SECONDS = 0.2
_CONFIG_RELOAD_SECONDS = 1.0
_ALIGN_TIMEOUT_SECONDS = 2.0
_GUIDE_LEASE_SECONDS = 1.2
_MOUNT_STATUS_CACHE_SECONDS = 0.2
_SKYSAFARI_SLEW_GRACE_SECONDS = 2.0
_SKYSAFARI_SLEW_STATES = {"slewing", "refine_wait", "refine_sent"}
_pointing_cache = {
    "time": 0.0,
    "value": None,
}
_mount_status_cache = {
    "time": 0.0,
    "value": None,
}
_skysafari_slew_started_at = 0.0
_skysafari_saw_mount_slew = False
_config_last_loaded = 0.0
_fallback_log_last = 0.0
_GUIDE_DIRECTIONS = {
    "Mn": "north",
    "Ms": "south",
    "Me": "east",
    "Mw": "west",
}

# shortcut for skyfield timescale
ts = sf_utils.ts


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


def _solved_pointing_jnow(shared_state, dt) -> Optional[Tuple[float, float]]:
    solution = shared_state.solution()
    if not solution or not dt or not solution.has_pointing():
        return None

    aligned = solution.pointing.aligned.estimate
    try:
        ra_deg = float(aligned.RA)
        dec_deg = float(aligned.Dec)
    except TypeError:
        logger.warning("solved_pointing_jnow: Type error in solved coords")
        return None

    point = position_of_radec(
        ra_hours=ra_deg / 15.0,
        dec_degrees=dec_deg,
        epoch=ts.J2000,
    )
    ra_h, dec, _dist = point.radec(epoch=ts.from_datetime(dt))
    return float(ra_h._degrees), float(dec.degrees)


def _imu_altaz_degrees(
    imu_sample, screen_direction: str
) -> Optional[Tuple[float, float]]:
    if not imu_sample or not imu_sample.is_calibrated():
        return None
    try:
        q_x2cam = (
            imu_sample.quat * ImuDeadReckoning._q_imu2cam(screen_direction)
        ).normalized()
    except (AttributeError, ValueError, ZeroDivisionError):
        logger.debug("imu_altaz_degrees: invalid IMU sample", exc_info=True)
        return None

    if not np.isfinite(quaternion.as_float_array(q_x2cam)).all():
        return None

    # BNO055 IMUPLUS mode does not use the magnetometer, so yaw is relative to
    # the sensor-fusion reference and may drift. Plate-solved pointing always
    # overrides this fallback when available.
    boresight = q_x2cam * quaternion.quaternion(0, 0, 0, 1) * q_x2cam.conj()
    east, north, up = boresight.x, boresight.y, boresight.z
    norm = math.sqrt(east * east + north * north + up * up)
    if norm <= 0:
        return None

    east, north, up = east / norm, north / norm, up / norm
    alt = math.degrees(math.asin(max(-1.0, min(1.0, up))))
    az = math.degrees(math.atan2(east, north)) % 360.0
    return alt, az


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


def _imu_fallback_pointing_jnow(shared_state, dt) -> Optional[Tuple[float, float]]:
    if not _get_config_option("skysafari_imu_fallback", True):
        _log_fallback_skip("disabled")
        return None

    location = _observer_location(shared_state)
    if not location:
        _log_fallback_skip("no location")
        return None
    if not location.lock:
        _log_fallback_skip("location unlocked")
        return None
    if not dt:
        _log_fallback_skip("no datetime")
        return None

    screen_direction = _get_config_option("screen_direction", "right")
    altaz = _imu_altaz_degrees(shared_state.imu(), screen_direction)
    if altaz is None:
        _log_fallback_skip("no calibrated IMU orientation")
        return None

    alt, az = altaz
    sf_utils.set_location(location.lat, location.lon, location.altitude)
    return sf_utils.altaz_to_radec(alt, az, dt)


def _current_datetime(shared_state):
    dt = shared_state.datetime()
    if dt is not None:
        return dt
    return datetime.datetime.now(datetime.timezone.utc)


def _current_pointing_jnow(shared_state) -> Optional[Tuple[float, float]]:
    now = time.monotonic()
    if now - _pointing_cache["time"] <= _POINTING_CACHE_SECONDS:
        return _pointing_cache["value"]

    dt = _current_datetime(shared_state)
    pointing = _solved_pointing_jnow(shared_state, dt)
    if pointing is None:
        pointing = _imu_fallback_pointing_jnow(shared_state, dt)

    _pointing_cache["time"] = now
    _pointing_cache["value"] = pointing
    return pointing


def get_telescope_ra(shared_state, _):
    """
    Extract RA from current solution
    format for LX200 protocol
    RA = HH:MM:SS
    """
    pointing = _current_pointing_jnow(shared_state)
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
    pointing = _current_pointing_jnow(shared_state)
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


def get_status(_shared_state, _input_str):
    # Indicates alt-az mode, tracking, and 1-star aligned
    return "AT1"


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


def _has_solved_pointing(shared_state) -> bool:
    try:
        solution = shared_state.solution()
    except Exception:
        logger.debug("Could not read PiFinder solution state", exc_info=True)
        return False
    return bool(solution and solution.has_pointing())


def _queue_indi_goto_if_enabled(shared_state, ra_deg: float, dec_deg: float) -> bool:
    if not _mount_control_enabled():
        return False
    if not _get_config_option("skysafari_indi_goto", False):
        logger.info("SkySafari INDI GoTo skipped; skysafari_indi_goto is off")
        return False

    has_solved_pointing = _has_solved_pointing(shared_state)
    refine_after_goto = bool(_get_config_option("indi_goto_refine_once", False))
    if refine_after_goto and not has_solved_pointing:
        logger.info("SkySafari INDI GoTo queued without refine; PiFinder is not solved")
        refine_after_goto = False

    command = {
        "type": "goto_target",
        "ra": ra_deg,
        "dec": dec_deg,
        "refine_after_goto": refine_after_goto,
        "refine_accuracy_arcmin": float(
            _get_config_option("indi_goto_refine_accuracy_arcmin", 10.0)
        ),
    }
    mountcontrol_queue.put(command)
    logger.info("SkySafari INDI GoTo queued: RA %.4f Dec %.4f", ra_deg, dec_deg)
    return True


def _queue_indi_sync_if_enabled(ra_deg: float, dec_deg: float) -> bool:
    if not _mount_control_enabled():
        return False
    if not _get_config_option("skysafari_indi_sync", False):
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
    if is_stellarium:
        return ra, dec

    point = position_of_radec(ra_hours=ra / 15, dec_degrees=dec, epoch=ts.now())
    ra_h, dec_d, _ = point.radec(epoch=ts.J2000)
    return float(ra_h._degrees), float(dec_d.degrees)


def handle_guide_move(_shared_state, input_str: str):
    command = extract_command(input_str)
    direction = _GUIDE_DIRECTIONS.get(command)
    if not direction:
        return None

    if _mount_control_enabled():
        mountcontrol_queue.put(
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
    if _mount_control_enabled():
        mountcontrol_queue.put({"type": "stop_movement"})
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
    target = last_target_j2000 or _target_from_parsed_coordinates()
    if target is None:
        logger.warning("SkySafari sync ignored; no target coordinates")
        return "No target."

    ra_deg, dec_deg = target
    pifinder_aligned = _align_pifinder_if_enabled(shared_state, ra_deg, dec_deg)
    indi_synced = _queue_indi_sync_if_enabled(ra_deg, dec_deg)
    logger.info(
        "SkySafari sync handled: pifinder_aligned=%s indi_synced=%s",
        pifinder_aligned,
        indi_synced,
    )
    return "Coordinates matched."


def handle_goto_command(shared_state, ra_parsed, dec_parsed):
    global sequence, ui_queue, is_stellarium, last_target_j2000
    ra = ra_to_deg(*ra_parsed)
    dec = dec_to_deg(*dec_parsed)
    if is_stellarium:
        comp_ra, comp_dec = ra, dec
    else:
        logger.debug("handle_goto_command: ra,dec in deg, JNOW: %s, %s", ra, dec)
        _p = position_of_radec(ra_hours=ra / 15, dec_degrees=dec, epoch=ts.now())
        ra_h, dec_d, _ = _p.radec(epoch=ts.J2000)
        comp_ra = float(ra_h._degrees)
        comp_dec = float(dec_d.degrees)
    sequence += 1
    last_target_j2000 = (comp_ra, comp_dec)
    logger.debug("Goto ra,dec in deg, J2000: %s, %s", comp_ra, comp_dec)
    constellation = sf_utils.radec_to_constellation(comp_ra, comp_dec)
    obj = CompositeObject.from_dict(
        {
            "id": -1,
            "object_id": sys.maxsize - sequence,
            "obj_type": "",
            "ra": comp_ra,
            "dec": comp_dec,
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
    _queue_indi_goto_if_enabled(shared_state, comp_ra, comp_dec)
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
    response = out_data if out_data in ("0", "1", "AT1") else out_data + "#"
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
    p_align_command_queue=None,
    p_align_response_queue=None,
    p_console_queue=None,
):
    MultiprocLogging.configurer(log_queue)
    global ui_queue, mountcontrol_queue, pos_server_config, _config_last_loaded
    global align_command_queue, align_response_queue, console_queue
    ui_queue = p_ui_queue
    mountcontrol_queue = p_mountcontrol_queue
    align_command_queue = p_align_command_queue
    align_response_queue = p_align_response_queue
    console_queue = p_console_queue
    pos_server_config = config.Config()
    _config_last_loaded = time.monotonic()
    logger = logging.getLogger(__name__)

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
