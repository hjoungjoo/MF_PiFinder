#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
Small INDI mount-control bridge for PiFinder.

The feature is intentionally optional: PyIndi is imported defensively and this
module is only started when ``mount_control`` is enabled in the PiFinder config.
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import statistics
import time
from datetime import datetime, timezone
from multiprocessing import Queue
from typing import Any, Optional

import quaternion

from PiFinder import calc_utils, config
from PiFinder import sys_utils, utils
from PiFinder.indi_align import (
    BRIGHT_ALIGN_STARS,
    clamp_align_points,
    get_align_star,
    next_align_star,
)
from PiFinder.multiproclogging import MultiprocLogging
from PiFinder.pointing_model.imu_dead_reckoning import ImuDeadReckoning

try:
    import PyIndi  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised only on INDI installs
    PyIndi = None


logger = logging.getLogger("MountControl.Indi")
clientlogger = logging.getLogger("MountControl.Indi.Client")

STATUS_FILE = utils.data_dir / "mount_control_status.json"
STOP_REQUEST_FILE = utils.data_dir / "mount_control_stop_request.json"
DEFAULT_STEP_DEGREES = 1.0
MIN_STEP_DEGREES = 0.05
MAX_STEP_DEGREES = 10.0
POSITION_STATUS_MIN_INTERVAL = 2.0
STATUS_HEARTBEAT_INTERVAL = 5.0
AUTO_CONNECT_START_DELAY = 5.0
AUTO_CONNECT_RETRY_INTERVAL = 10.0
MANUAL_MOTION_LEASE_SECONDS = 1.2
MANUAL_MOTION_MIN_LEASE_SECONDS = 0.3
MANUAL_MOTION_MAX_LEASE_SECONDS = 5.0
MANUAL_MOTION_MAX_CONTINUOUS_SECONDS = 10.0
MANUAL_MOTION_POLL_SECONDS = 0.1
MANUAL_MOTION_STOP_RETRY_SECONDS = 0.5
GOTO_REFINE_DELAY_SECONDS = 8.0
GOTO_REFINE_SOLVE_TIMEOUT_SECONDS = 45.0
DEFAULT_GOTO_REFINE_ACCURACY_ARCMIN = 10.0
GUIDE_CORRECTION_INTERVAL_SECONDS = 10.0
GUIDE_CORRECTION_PULSE_SECONDS = 0.4
GOTO_COMPLETE_MIN_SECONDS = 1.0
GOTO_COMPLETE_STABLE_SECONDS = 4.0
GOTO_COMPLETE_POSITION_STABLE_DEG = 0.02
GOTO_COMPLETE_TARGET_TOLERANCE_DEG = 0.5
GOTO_ONSTEP_ACTIVE_OBSERVE_GRACE_SECONDS = 3.0
GOTO_COMPLETE_FALLBACK_SECONDS = 180.0
BACKLASH_MIN_VALUE = 0
BACKLASH_MAX_VALUE = 3600
BACKLASH_AUTO_IMU_STALE_SECONDS = 5.0
BACKLASH_AUTO_GOTO_TIMEOUT_SECONDS = 90.0
BACKLASH_AUTO_GOTO_POLL_SECONDS = 0.25
BACKLASH_AUTO_GOTO_TARGET_TOLERANCE_DEG = 0.5
BACKLASH_AUTO_SAFE_MIN_ALT_DEG = 15.0
BACKLASH_AUTO_SAFE_MAX_ALT_DEG = 75.0
BACKLASH_AUTO_MODE_COMPASS_GOTO = "compass_goto_loop"
BACKLASH_AUTO_MODES = {
    BACKLASH_AUTO_MODE_COMPASS_GOTO,
}
BACKLASH_COMPASS_DIFF_REJECT_ARCSEC = 3600
BACKLASH_COMPASS_TRIM_FRACTION = 0.30
BACKLASH_COMPASS_GOTO_OFFSET_DEG = 2.0
BACKLASH_COMPASS_GOTO_REPEATS = 10
BACKLASH_COMPASS_GOTO_SETTLE_SECONDS = 0.5
BACKLASH_COMPASS_GOTO_BETWEEN_SECONDS = 1.0
BACKLASH_COMPASS_GOTO_TIMEOUT_SECONDS = 180.0
BACKLASH_GOTO_RETRY_AFTER_SECONDS = 8.0
BACKLASH_GOTO_MAX_RETRIES = 3


def radec_separation_arcmin(
    ra_a_deg: float, dec_a_deg: float, ra_b_deg: float, dec_b_deg: float
) -> float:
    ra_a = math.radians(ra_a_deg)
    dec_a = math.radians(dec_a_deg)
    ra_b = math.radians(ra_b_deg)
    dec_b = math.radians(dec_b_deg)
    cos_sep = math.sin(dec_a) * math.sin(dec_b) + math.cos(dec_a) * math.cos(
        dec_b
    ) * math.cos(ra_a - ra_b)
    sep_rad = math.acos(max(-1.0, min(1.0, cos_sep)))
    return math.degrees(sep_rad) * 60.0


def shortest_ra_delta_deg(target_ra_deg: float, current_ra_deg: float) -> float:
    return (target_ra_deg - current_ra_deg + 180.0) % 360.0 - 180.0


def _write_status(state: str, message: str = "", **extra: Any) -> None:
    """Persist a compact mount-control status snapshot for logs/web/debug."""
    try:
        utils.create_path(utils.data_dir)
        payload = {
            "state": state,
            "message": message,
            "updated": time.time(),
        }
        payload.update(extra)
        tmp_status = STATUS_FILE.with_name(
            f"{STATUS_FILE.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        )
        with open(tmp_status, "w", encoding="utf-8") as status_out:
            json.dump(payload, status_out, indent=2, sort_keys=True)
            status_out.flush()
            os.fsync(status_out.fileno())
        tmp_status.replace(STATUS_FILE)
    except Exception:
        logger.exception("Could not write mount-control status")


if PyIndi is not None:

    class PiFinderIndiClient(PyIndi.BaseClient):  # type: ignore[misc]
        """Minimal INDI client that finds a telescope-like device."""

        def __init__(self, mount_control=None):
            super().__init__()
            self.telescope_device = None
            self.mount_control = mount_control
            self.preferred_device_name = sys_utils.get_indi_profile_device_name()

        def get_telescope_device(self):
            return self.telescope_device

        def _wait_for_property(self, device, property_name: str, timeout: float = 5.0):
            start_time = time.time()
            while time.time() - start_time < timeout:
                prop = device.getProperty(property_name)
                if prop:
                    return prop
                time.sleep(0.1)
            clientlogger.warning(
                "Timeout waiting for property %s on %s",
                property_name,
                device.getDeviceName(),
            )
            return None

        def set_switch(
            self, device, property_name: str, element_name: str, timeout: float = 5.0
        ) -> bool:
            if not self._wait_for_property(device, property_name, timeout):
                return False

            switch_prop = device.getSwitch(property_name)
            if not switch_prop:
                clientlogger.error("Could not get switch property %s", property_name)
                return False

            found = False
            for i in range(len(switch_prop)):
                switch = switch_prop[i]
                if switch.name == element_name:
                    switch.s = PyIndi.ISS_ON
                    found = True
                else:
                    switch.s = PyIndi.ISS_OFF

            if not found:
                clientlogger.error(
                    "Switch element %s.%s not found", property_name, element_name
                )
                return False

            self.sendNewSwitch(switch_prop)
            return True

        def set_number(
            self, device, property_name: str, values: dict[str, float], timeout=5.0
        ) -> bool:
            if not self._wait_for_property(device, property_name, timeout):
                return False

            number_prop = device.getNumber(property_name)
            if not number_prop:
                clientlogger.error("Could not get number property %s", property_name)
                return False

            found = False
            for i in range(len(number_prop)):
                number = number_prop[i]
                if number.name in values:
                    number.value = values[number.name]
                    found = True

            if not found:
                clientlogger.error("No matching elements in %s", property_name)
                return False

            self.sendNewNumber(number_prop)
            return True

        def set_text(
            self, device, property_name: str, values: dict[str, str], timeout=5.0
        ) -> bool:
            if not self._wait_for_property(device, property_name, timeout):
                return False

            text_prop = device.getText(property_name)
            if not text_prop:
                clientlogger.error("Could not get text property %s", property_name)
                return False

            found = False
            for i in range(len(text_prop)):
                text = text_prop[i]
                if text.name in values:
                    text.text = values[text.name]
                    found = True

            if not found:
                clientlogger.error("No matching elements in %s", property_name)
                return False

            self.sendNewText(text_prop)
            return True

        def unpark_mount(self, device) -> bool:
            if not self._wait_for_property(device, "TELESCOPE_PARK", timeout=2.0):
                return True

            park_switch = device.getSwitch("TELESCOPE_PARK")
            if not park_switch:
                return True

            is_parked = False
            for i in range(len(park_switch)):
                if park_switch[i].name == "PARK" and park_switch[i].s == PyIndi.ISS_ON:
                    is_parked = True
                    break

            return not is_parked or self.set_switch(device, "TELESCOPE_PARK", "UNPARK")

        def enable_tracking(self, device) -> bool:
            if self._wait_for_property(device, "TELESCOPE_TRACK_MODE", timeout=2.0):
                self.set_switch(device, "TELESCOPE_TRACK_MODE", "TRACK_SIDEREAL")

            if self._wait_for_property(device, "TELESCOPE_TRACK_STATE", timeout=2.0):
                return self.set_switch(device, "TELESCOPE_TRACK_STATE", "TRACK_ON")
            return True

        def newDevice(self, device):
            device_name = device.getDeviceName().lower()
            preferred = (self.preferred_device_name or "").lower()
            is_preferred = preferred and device_name == preferred
            is_telescope_like = (
                any(
                    word in device_name
                    for word in ("telescope", "mount", "eqmod", "lx200", "celestron")
                )
                or device_name == "telescope simulator"
            )
            if is_preferred or (self.telescope_device is None and is_telescope_like):
                self.telescope_device = device
                clientlogger.info(
                    "Telescope device detected: %s", device.getDeviceName()
                )

        def removeDevice(self, device):
            if (
                self.telescope_device
                and device.getDeviceName() == self.telescope_device.getDeviceName()
            ):
                clientlogger.warning(
                    "Telescope device removed: %s", device.getDeviceName()
                )
                self.telescope_device = None

        def newNumber(self, nvp):
            if nvp.name != "EQUATORIAL_EOD_COORD":
                return

            ra_hours = None
            dec_deg = None
            for widget in nvp:
                if widget.name == "RA":
                    ra_hours = widget.value
                elif widget.name == "DEC":
                    dec_deg = widget.value

            if (
                self.mount_control is not None
                and ra_hours is not None
                and dec_deg is not None
            ):
                self.mount_control.set_current_position(ra_hours * 15.0, dec_deg)

        def newMessage(self, device, message):
            clientlogger.info(
                "INDI message from %s: %s",
                device.getDeviceName(),
                device.messageQueue(message),
            )

        def serverConnected(self):
            clientlogger.info("Connected to INDI server")

        def serverDisconnected(self, code):
            clientlogger.warning("Disconnected from INDI server: %s", code)
            if self.mount_control is not None:
                self.mount_control.mark_disconnected(
                    f"INDI server disconnected: {code}"
                )

else:

    class PiFinderIndiClient:  # type: ignore[no-redef]
        pass


class MountControlIndi:
    """Translate PiFinder queue commands into INDI telescope commands."""

    def __init__(
        self,
        mount_queue: Queue,
        console_queue: Queue,
        shared_state,
        imu_command_queue: Optional[Queue] = None,
        indi_host: str = "localhost",
        indi_port: int = 7624,
    ):
        self.mount_queue = mount_queue
        self.console_queue = console_queue
        self.shared_state = shared_state
        self.imu_command_queue = imu_command_queue
        self.indi_host = indi_host
        self.indi_port = indi_port
        self.client: Optional[PiFinderIndiClient] = None
        self.device = None
        self.step_degrees = DEFAULT_STEP_DEGREES
        self.slew_rate = 5
        self.current_ra: Optional[float] = None
        self.current_dec: Optional[float] = None
        self.connected = False
        self._last_position_status_at = 0.0
        self._last_status_heartbeat_at = 0.0
        self._manual_motion_direction: Optional[str] = None
        self._manual_motion_deadline: Optional[float] = None
        self._manual_motion_started_at: Optional[float] = None
        self._manual_motion_stop_retry_at = 0.0
        self._pending_goto_refine: Optional[dict[str, Any]] = None
        self._last_goto_target: Optional[tuple[float, float]] = None
        self._goto_motion: Optional[dict[str, Any]] = None
        self._guide_correction_enabled = False
        self._guide_correction_target: Optional[tuple[float, float]] = None
        self._guide_correction_accuracy_arcmin = DEFAULT_GOTO_REFINE_ACCURACY_ARCMIN
        self._guide_correction_next_at = 0.0
        self._guide_correction_last_solve_time = 0.0
        self.backlash_ra: Optional[int] = None
        self.backlash_de: Optional[int] = None
        self._backlash_auto: Optional[dict[str, Any]] = None
        self._backlash_stop_seen_at: float = 0.0
        self._multipoint_align: Optional[dict[str, Any]] = None

    def _console(self, message: str) -> None:
        self.console_queue.put(message)

    def _status_fields(self, **extra: Any) -> dict[str, Any]:
        payload = {
            "step_degrees": self.step_degrees,
            "slew_rate": self.slew_rate,
            "ra": self.current_ra,
            "dec": self.current_dec,
        }
        payload.update(self._home_park_status_fields())
        if self._manual_motion_direction is not None:
            payload["manual_motion_direction"] = self._manual_motion_direction
            if self._manual_motion_deadline is not None:
                payload["manual_motion_lease_remaining"] = max(
                    0.0, self._manual_motion_deadline - time.monotonic()
                )
        if self._pending_goto_refine is not None:
            payload["goto_refine_pending"] = True
            payload["goto_refine_accuracy_arcmin"] = self._pending_goto_refine.get(
                "accuracy_arcmin"
            )
        if self._goto_motion is not None:
            payload["goto_motion_active"] = True
            payload["target_ra"] = self._goto_motion.get("target_ra")
            payload["target_dec"] = self._goto_motion.get("target_dec")
        payload["guide_correction_enabled"] = self._guide_correction_enabled
        if self._guide_correction_target is not None:
            payload["guide_correction_target_ra"] = self._guide_correction_target[0]
            payload["guide_correction_target_dec"] = self._guide_correction_target[1]
            payload["guide_correction_accuracy_arcmin"] = (
                self._guide_correction_accuracy_arcmin
            )
        if self.backlash_ra is not None:
            payload["backlash_ra"] = self.backlash_ra
        if self.backlash_de is not None:
            payload["backlash_de"] = self.backlash_de
        if self._backlash_auto is not None:
            payload["backlash_auto"] = self._backlash_auto
        if self._multipoint_align is not None:
            payload["multipoint_align"] = self._multipoint_align
        if self.device is not None:
            try:
                payload["device"] = self.device.getDeviceName()
            except Exception:
                pass
        payload.update(extra)
        return payload

    def _device_switch_on(
        self, property_name: str, element_name: str
    ) -> Optional[bool]:
        if self.device is None:
            return None
        try:
            switch_prop = self.device.getSwitch(property_name)
        except Exception:
            return None
        if not switch_prop:
            return None
        for i in range(len(switch_prop)):
            switch = switch_prop[i]
            if getattr(switch, "name", "") != element_name:
                continue
            state = getattr(switch, "s", None)
            if PyIndi is not None:
                try:
                    return state == PyIndi.ISS_ON
                except Exception:
                    pass
            return str(state).lower() in {"on", "iss_on", "1", "true"}
        return None

    def _device_text_value(self, property_name: str, element_name: str) -> str:
        if self.device is None:
            return ""
        try:
            text_prop = self.device.getText(property_name)
        except Exception:
            return ""
        if not text_prop:
            return ""
        for i in range(len(text_prop)):
            text = text_prop[i]
            if getattr(text, "name", "") == element_name:
                return str(getattr(text, "text", "") or "")
        return ""

    def _home_park_status_fields(self) -> dict[str, str]:
        status_text = self._device_text_value("OnStep Status", "Park")
        raw_status = self._device_text_value("OnStep Status", ":GU# return")
        park_switch = self._device_switch_on("TELESCOPE_PARK", "PARK")
        unpark_switch = self._device_switch_on("TELESCOPE_PARK", "UNPARK")
        state = sys_utils.parse_onstep_home_park_state(
            status_text=status_text,
            park_switch="On" if park_switch else "",
            unpark_switch="On" if unpark_switch else "",
            raw_status=raw_status,
        )
        return {
            "home_state": state["home_state"],
            "park_state": state["park_state"],
            "driver_mount_status": state["driver_status"],
            "raw_mount_status": state["raw_status"],
        }

    def _write_controller_status(
        self, state: str, message: str = "", **extra: Any
    ) -> None:
        _write_status(state, message, **self._status_fields(**extra))

    def _indi_device_name(self) -> str:
        if self.device is not None:
            try:
                return self.device.getDeviceName()
            except Exception:
                pass
        return sys_utils.get_indi_profile_device_name()

    def _indi_property_name(self, property_name: str) -> str:
        return f"{self._indi_device_name()}.{property_name}"

    def _indi_property_on(self, property_name: str) -> str:
        return f"{self._indi_property_name(property_name)}=On"

    def _indi_property_state(self, property_name: str) -> Any:
        if self.device is None:
            return None

        for getter_name in ("getProperty", "getNumber", "getSwitch", "getText"):
            getter = getattr(self.device, getter_name, None)
            if getter is None:
                continue
            try:
                prop = getter(property_name)
            except Exception:
                continue
            if not prop:
                continue

            state = getattr(prop, "s", None)
            if state is None:
                get_state = getattr(prop, "getState", None)
                if callable(get_state):
                    try:
                        state = get_state()
                    except Exception:
                        state = None
            if state is not None:
                return state
        return None

    def _indi_state_is_busy(self, state: Any) -> bool:
        if state is None:
            return False
        if PyIndi is not None:
            try:
                if state == PyIndi.IPS_BUSY:
                    return True
            except Exception:
                pass
        return str(state).lower() == "busy"

    def _indi_mount_is_busy(self) -> Optional[bool]:
        saw_state = False
        for property_name in (
            "EQUATORIAL_EOD_COORD",
            "ON_COORD_SET",
            "TELESCOPE_MOTION_NS",
            "TELESCOPE_MOTION_WE",
        ):
            state = self._indi_property_state(property_name)
            if state is None:
                continue
            saw_state = True
            if self._indi_state_is_busy(state):
                return True
        return False if saw_state else None

    def _raw_onstep_status(self) -> str:
        return self._device_text_value("OnStep Status", ":GU# return").strip()

    def _onstep_goto_complete(self) -> Optional[bool]:
        raw_status = self._raw_onstep_status()
        if not raw_status:
            return None
        return "N" in raw_status

    def _goto_completion_ready(
        self,
        motion: dict[str, Any],
        is_busy: Optional[bool],
        now: float,
        current_position: Optional[tuple[float, float]] = None,
    ) -> bool:
        elapsed = now - float(motion.get("started_at", now))
        onstep_complete = self._onstep_goto_complete()

        if is_busy is True:
            motion["indi_seen_busy"] = True
            motion["complete_ready_since"] = None
            return False

        if onstep_complete is False:
            motion["onstep_seen_goto_active"] = True
            motion["complete_ready_since"] = None
            return False

        if onstep_complete is True:
            saw_active = bool(
                motion.get("indi_seen_busy") or motion.get("onstep_seen_goto_active")
            )
            if not saw_active and elapsed < GOTO_ONSTEP_ACTIVE_OBSERVE_GRACE_SECONDS:
                motion["complete_ready_since"] = None
                return False

        if is_busy is not False:
            motion["complete_ready_since"] = None
            return False

        if current_position is not None:
            target_ra = motion.get("target_ra")
            target_dec = motion.get("target_dec")
            if target_ra is not None and target_dec is not None:
                target_error_deg = (
                    radec_separation_arcmin(
                        current_position[0],
                        current_position[1],
                        float(target_ra),
                        float(target_dec),
                    )
                    / 60.0
                )
                motion["target_error_deg"] = target_error_deg
                if target_error_deg > GOTO_COMPLETE_TARGET_TOLERANCE_DEG:
                    motion["complete_ready_since"] = None
                    motion["last_complete_position"] = current_position
                    return False

            last_position = motion.get("last_complete_position")
            if last_position is not None:
                position_change_deg = (
                    radec_separation_arcmin(
                        current_position[0],
                        current_position[1],
                        float(last_position[0]),
                        float(last_position[1]),
                    )
                    / 60.0
                )
                motion["position_change_deg"] = position_change_deg
                if position_change_deg > GOTO_COMPLETE_POSITION_STABLE_DEG:
                    motion["complete_ready_since"] = None
                    motion["last_complete_position"] = current_position
                    return False
            motion["last_complete_position"] = current_position

        ready_since = motion.get("complete_ready_since")
        if ready_since is None:
            motion["complete_ready_since"] = now
            return False
        return now - float(ready_since) >= GOTO_COMPLETE_STABLE_SECONDS

    def _write_status_heartbeat(self) -> None:
        now = time.monotonic()
        if now - self._last_status_heartbeat_at < STATUS_HEARTBEAT_INTERVAL:
            return
        self._last_status_heartbeat_at = now

        if (
            self.connected
            and self.client is not None
            and self.device is not None
            and self.client.isServerConnected()
        ):
            if (
                self._goto_motion is not None
                or self._manual_motion_direction is not None
            ):
                return
            self._read_cached_current_position()
            driver_slew = self._read_driver_slew_rate()
            if driver_slew is not None:
                self.slew_rate = driver_slew
            self._write_controller_status("connected", "INDI mount connected")
        elif not self.connected:
            self._write_controller_status("idle", "INDI mount waiting")

    def _apply_indi_properties(
        self,
        properties: list[str],
        success_state: str,
        success_message: str,
        failure_state: str,
    ) -> bool:
        try:
            result = sys_utils.apply_indi_onstep_properties(
                properties,
                server_host=self.indi_host,
                server_port=self.indi_port,
            )
        except Exception as exc:
            logger.exception("INDI setprop command failed")
            self._write_controller_status(failure_state, str(exc))
            return False

        if not result.get("ok"):
            error = (
                result.get("stderr") or result.get("stdout") or "INDI command failed"
            )
            logger.warning("INDI setprop returned failure: %s", error)
            self._write_controller_status(failure_state, error)
            return False

        self._write_controller_status(success_state, success_message)
        return True

    def _apply_indi_backlash(self, backlash_ra: int, backlash_de: int):
        return sys_utils.apply_indi_onstep_backlash(
            backlash_ra,
            backlash_de,
            server_host=self.indi_host,
            server_port=self.indi_port,
            device_name=self._indi_device_name(),
        )

    def set_current_position(self, ra_deg: float, dec_deg: float) -> None:
        self.current_ra = ra_deg % 360.0
        self.current_dec = dec_deg
        self._write_position_status()

    def _write_position_status(self, force: bool = False) -> None:
        now = time.monotonic()
        if (
            not force
            and now - self._last_position_status_at < POSITION_STATUS_MIN_INTERVAL
        ):
            return
        self._last_position_status_at = now
        self._write_controller_status(
            "connected",
            "Mount position updated",
        )

    def _manual_motion_lease(self, requested: Any = None) -> float:
        try:
            lease_seconds = float(requested)
        except (TypeError, ValueError):
            lease_seconds = MANUAL_MOTION_LEASE_SECONDS
        return max(
            MANUAL_MOTION_MIN_LEASE_SECONDS,
            min(MANUAL_MOTION_MAX_LEASE_SECONDS, lease_seconds),
        )

    def _clear_manual_motion_deadline(self) -> None:
        self._manual_motion_direction = None
        self._manual_motion_deadline = None
        self._manual_motion_started_at = None
        self._manual_motion_stop_retry_at = 0.0

    def _arm_manual_motion_deadline(
        self, direction: str, lease_seconds: Any = None
    ) -> None:
        now = time.monotonic()
        self._manual_motion_direction = direction
        self._manual_motion_deadline = now + self._manual_motion_lease(lease_seconds)
        self._manual_motion_started_at = now
        self._manual_motion_stop_retry_at = 0.0

    def manual_motion_keepalive(
        self, direction: str, lease_seconds: Any = None
    ) -> bool:
        direction = direction.lower()
        if (
            self._manual_motion_direction is None
            or direction != self._manual_motion_direction
        ):
            return False

        now = time.monotonic()
        if (
            self._manual_motion_started_at is not None
            and now - self._manual_motion_started_at
            > MANUAL_MOTION_MAX_CONTINUOUS_SECONDS
        ):
            logger.warning("Manual mount motion maximum hold time exceeded")
            return False

        self._manual_motion_deadline = now + self._manual_motion_lease(lease_seconds)
        return True

    def _manual_motion_queue_timeout(self) -> float:
        if self._manual_motion_deadline is None:
            return 1.0
        return max(
            MANUAL_MOTION_POLL_SECONDS,
            min(1.0, self._manual_motion_deadline - time.monotonic()),
        )

    def _check_manual_motion_deadline(self) -> None:
        if self._manual_motion_deadline is None:
            return

        now = time.monotonic()
        if (
            now < self._manual_motion_deadline
            or now < self._manual_motion_stop_retry_at
        ):
            return

        logger.warning("Manual mount motion lease expired; sending stop")
        if self.stop_mount():
            return

        self._manual_motion_stop_retry_at = now + MANUAL_MOTION_STOP_RETRY_SECONDS

    def _wait_for_device(self, timeout: float = 10.0) -> bool:
        assert self.client is not None
        start = time.time()
        while time.time() - start < timeout:
            self.device = self.client.get_telescope_device()
            if self.device is not None:
                return True
            time.sleep(0.25)
        return False

    def connect(self, announce: bool = True, sync_on_connect: bool = True) -> bool:
        if (
            self.connected
            and self.device is not None
            and self.client is not None
            and self.client.isServerConnected()
        ):
            return True

        if self.connected:
            self.mark_disconnected("INDI server connection is not active")

        if PyIndi is None:
            self._write_controller_status("missing_pyindi", "PyIndi is not installed")
            if announce:
                self._console("INDI mount\nPyIndi missing")
            return False

        direct_sync_for_onstep = self._use_direct_onstep_location_time_sync()
        if sync_on_connect and direct_sync_for_onstep:
            self.sync_location_time(reconnect_after=False)

        self.client = PiFinderIndiClient(self)
        self.client.setServer(self.indi_host, self.indi_port)
        self._write_controller_status(
            "connecting",
            f"Connecting to INDI server {self.indi_host}:{self.indi_port}",
        )
        logger.info(
            "Connecting to INDI server at %s:%s", self.indi_host, self.indi_port
        )

        if not self.client.connectServer():
            self._write_controller_status(
                "server_unavailable",
                f"Could not connect to INDI server {self.indi_host}:{self.indi_port}",
            )
            if announce:
                self._console("INDI server\nnot found")
            return False

        if not self._wait_for_device():
            self._write_controller_status(
                "no_telescope",
                "No telescope/mount device detected",
            )
            if announce:
                self._console("INDI mount\nnot found")
            return False

        assert self.device is not None
        device_name = self.device.getDeviceName()
        logger.info("Using INDI telescope device: %s", device_name)

        if self.client._wait_for_property(self.device, "CONNECTION", timeout=2.0):
            if not self.device.isConnected():
                if not self.client.set_switch(self.device, "CONNECTION", "CONNECT"):
                    self._write_controller_status(
                        "device_connect_failed",
                        f"Could not connect {device_name}",
                    )
                    if announce:
                        self._console("INDI mount\nconnect failed")
                    return False
                time.sleep(1.0)

        if sync_on_connect and not direct_sync_for_onstep:
            self.sync_location_time()
        self.client.unpark_mount(self.device)
        self.client.enable_tracking(self.device)
        self._read_current_position()
        self.connected = True
        self._last_position_status_at = time.monotonic()
        self._write_controller_status(
            "connected",
            f"Connected to {device_name}",
            device=device_name,
        )
        if announce:
            self._console("INDI mount\nconnected")
        return True

    def mark_disconnected(self, message: str) -> None:
        self.connected = False
        self.device = None
        self._write_controller_status("disconnected", message)

    def restart_driver(self) -> bool:
        logger.info("Restarting INDI Web Manager, server, and mount driver")
        self._write_controller_status("restarting", "Restarting INDI server/driver")
        self._console("INDI server\nrestarting")
        self.disconnect()
        self.client = None
        self.device = None
        try:
            result = sys_utils.restart_indi_web_manager(timeout=30)
            if not result["ok"]:
                error = (
                    result.get("stderr")
                    or result.get("stdout")
                    or "Could not restart INDI Web Manager"
                )
                logger.error("Could not restart INDI Web Manager: %s", error)
                self._write_controller_status("restart_failed", error)
                self._console("INDI restart\nfailed")
                return False
        except RuntimeError as exc:
            logger.error("Could not restart INDI Web Manager: %s", exc)
            self._write_controller_status("restart_failed", str(exc))
            self._console("INDI restart\nfailed")
            return False

        time.sleep(3.0)
        connect_result = sys_utils.connect_indi_onstep_driver(
            server_host=self.indi_host,
            server_port=self.indi_port,
            device_name=sys_utils.get_indi_profile_device_name(),
            wait_timeout=15,
        )
        if not connect_result["ok"]:
            error = (
                connect_result.get("stderr")
                or connect_result.get("stdout")
                or "Could not connect INDI OnStep driver"
            )
            logger.error(
                "Could not connect INDI OnStep driver after restart: %s", error
            )
            self._write_controller_status("device_connect_failed", error)
            self._console("INDI connect\nfailed")
            return False
        return self.connect()

    def disconnect(self) -> None:
        if self.client is not None:
            try:
                self.client.disconnectServer()
            except Exception:
                logger.exception("Could not disconnect from INDI server")
        self.connected = False
        self._write_controller_status("stopped", "Mount-control process stopped")

    def _onstep_connection_config(self) -> dict[str, Any]:
        cfg = config.Config()
        cfg.load_config()
        direct_sync = cfg.get_option("onstep_direct_lx200_location_time_sync", False)
        if isinstance(direct_sync, str):
            direct_sync = direct_sync.strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
        return {
            "connection_type": cfg.get_option("onstep_connection_type", "network"),
            "network_host": cfg.get_option("onstep_network_host", ""),
            "network_port": int(cfg.get_option("onstep_network_port", 9999)),
            "serial_port": cfg.get_option("onstep_serial_port", ""),
            "direct_location_time_sync": bool(direct_sync),
        }

    def _use_direct_onstep_location_time_sync(self) -> bool:
        try:
            if self.device is not None:
                device_name = self.device.getDeviceName()
                if not sys_utils.is_onstep_family_device_name(device_name):
                    return False
            return bool(self._onstep_connection_config()["direct_location_time_sync"])
        except Exception:
            logger.exception("Could not read OnStep direct-sync configuration")
            return False

    def _shared_location_time_values(
        self, include_default_location: bool = False
    ) -> tuple[Optional[float], Optional[float], Optional[float], Any]:
        latitude = longitude = elevation = None
        try:
            location = self.shared_state.location()
        except Exception:
            location = None

        if location and location.lock:
            latitude = float(location.lat)
            longitude = float(location.lon)
            elevation = None if location.altitude is None else float(location.altitude)
        elif include_default_location:
            try:
                cfg = config.Config()
                cfg.load_config()
                default_location = cfg.locations.default_location
                if default_location:
                    latitude = float(default_location.latitude)
                    longitude = float(default_location.longitude)
                    elevation = float(default_location.height)
            except Exception:
                logger.debug("Could not load default location fallback", exc_info=True)

        try:
            dt = self.shared_state.datetime()
        except Exception:
            dt = None
        if dt is None:
            dt = datetime.now(timezone.utc)

        return latitude, longitude, elevation, dt

    def _sync_location_time_direct_onstep(
        self,
        latitude: float,
        longitude: float,
        elevation: float | None,
        dt: Any,
        reconnect_after: bool,
    ) -> bool:
        onstep_cfg = self._onstep_connection_config()
        was_connected = self.connected or (
            self.client is not None and self.client.isServerConnected()
        )

        if self.client is not None:
            self.disconnect()
            self.client = None
            self.device = None

        self._write_controller_status(
            "syncing",
            "Sending location/time via direct LX200 OnStep commands",
        )
        try:
            result = sys_utils.sync_onstep_location_time_exclusive(
                connection_type=onstep_cfg["connection_type"],
                latitude=latitude,
                longitude=longitude,
                elevation=elevation,
                utc_datetime=dt,
                network_host=onstep_cfg["network_host"],
                network_port=onstep_cfg["network_port"],
                serial_port=onstep_cfg["serial_port"],
                server_host=self.indi_host,
                server_port=self.indi_port,
            )
        except Exception as exc:
            logger.exception("Direct LX200 OnStep location/time sync failed")
            self._write_controller_status("sync_failed", str(exc))
            return False

        if not result.get("ok"):
            error = (
                result.get("stderr")
                or result.get("stdout")
                or "Direct LX200 OnStep sync failed"
            )
            logger.warning("Direct LX200 OnStep location/time sync failed: %s", error)
            self._write_controller_status("sync_failed", error)
            return False

        sys_utils.write_onstep_location_cache(latitude, longitude, elevation, dt)
        self._write_controller_status(
            "connected" if was_connected else "idle",
            "Location/time sent via direct LX200 OnStep commands",
        )

        if reconnect_after and was_connected:
            return self.connect(announce=False, sync_on_connect=False)
        return True

    def sync_location_time(self, reconnect_after: bool = True) -> bool:
        try:
            latitude, longitude, elevation, dt = self._shared_location_time_values()
            if self._use_direct_onstep_location_time_sync():
                if latitude is None or longitude is None:
                    self._write_controller_status(
                        "connected" if self.connected else "idle",
                        "No locked location available for direct OnStep sync",
                    )
                    return False
                return self._sync_location_time_direct_onstep(
                    latitude,
                    longitude,
                    elevation,
                    dt,
                    reconnect_after=reconnect_after,
                )

            if latitude is None and longitude is None and dt is None:
                self._write_controller_status(
                    "connected" if self.connected else "idle",
                    "No locked location/time available",
                )
                return False

            try:
                result = sys_utils.apply_indi_onstep_location_time(
                    latitude=latitude,
                    longitude=longitude,
                    elevation=elevation,
                    utc_datetime=dt,
                    server_host=self.indi_host,
                    server_port=self.indi_port,
                    device_name=self._indi_device_name(),
                )
            except Exception as exc:
                logger.exception("INDI location/time sync failed")
                self._write_controller_status("sync_failed", str(exc))
                return False

            if not result.get("ok"):
                error = (
                    result.get("stderr")
                    or result.get("stdout")
                    or "INDI location/time sync failed"
                )
                logger.warning("INDI location/time sync failed: %s", error)
                self._write_controller_status("sync_failed", error)
                return False

            self._write_controller_status(
                "connected" if self.connected else "idle",
                "Location/time sent via INDI",
            )
            if latitude is not None and longitude is not None:
                sys_utils.write_onstep_location_cache(
                    latitude,
                    longitude,
                    elevation,
                    dt,
                )
            return True
        except Exception:
            logger.exception("Could not sync INDI location/time")
            return False

    def _read_cached_current_position(self) -> Optional[tuple[float, float]]:
        if self.device is None:
            return None

        coord_prop = self.device.getNumber("EQUATORIAL_EOD_COORD")
        if not coord_prop:
            return None

        ra_hours = None
        dec_deg = None
        for i in range(len(coord_prop)):
            number = coord_prop[i]
            if number.name == "RA":
                ra_hours = number.value
            elif number.name == "DEC":
                dec_deg = number.value

        if ra_hours is None or dec_deg is None:
            return None

        self.set_current_position(ra_hours * 15.0, dec_deg)
        return self.current_ra, self.current_dec

    def _read_current_position(self) -> Optional[tuple[float, float]]:
        if self.client is None or self.device is None:
            return None

        if not self.client._wait_for_property(
            self.device, "EQUATORIAL_EOD_COORD", timeout=2.0
        ):
            return None

        return self._read_cached_current_position()

    def _current_plate_solve(self) -> Optional[tuple[float, float, Optional[float]]]:
        try:
            solution = self.shared_state.solution()
        except Exception:
            logger.debug("Could not read PiFinder solve for GoTo refine", exc_info=True)
            return None

        if not solution or solution.last_solve_success is None:
            return None

        try:
            pointing = solution.pointing.aligned.solve
            if pointing is None:
                return None
            return float(pointing.RA), float(pointing.Dec), solution.last_solve_success
        except (AttributeError, TypeError, ValueError):
            logger.debug("Invalid PiFinder solve for GoTo refine", exc_info=True)
            return None

    def _arm_goto_refine(
        self, target_ra: float, target_dec: float, accuracy_arcmin: Any = None
    ) -> None:
        try:
            accuracy = float(accuracy_arcmin)
        except (TypeError, ValueError):
            accuracy = DEFAULT_GOTO_REFINE_ACCURACY_ARCMIN
        accuracy = max(0.1, accuracy)
        now = time.monotonic()
        self._pending_goto_refine = {
            "target_ra": target_ra % 360.0,
            "target_dec": target_dec,
            "accuracy_arcmin": accuracy,
            "requested_wall": time.time(),
            "ready_at": now + GOTO_REFINE_DELAY_SECONDS,
            "timeout_at": now + GOTO_REFINE_SOLVE_TIMEOUT_SECONDS,
        }
        self._write_controller_status(
            "refine_wait",
            "Waiting for GoTo settle before solve refine",
            target_ra=target_ra % 360.0,
            target_dec=target_dec,
        )

    def _check_pending_goto_refine(self) -> None:
        pending = self._pending_goto_refine
        if pending is None:
            return

        now = time.monotonic()
        if now < pending["ready_at"]:
            return

        if now >= pending["timeout_at"]:
            self._pending_goto_refine = None
            self._write_controller_status("refine_timeout", "No fresh solve for refine")
            self._console("INDI refine\nno solve")
            return

        solved = self._current_plate_solve()
        if solved is None:
            return

        current_ra, current_dec, solve_time = solved
        if solve_time is None or solve_time < pending["requested_wall"]:
            return

        target_ra = float(pending["target_ra"])
        target_dec = float(pending["target_dec"])
        separation = radec_separation_arcmin(
            current_ra,
            current_dec,
            target_ra,
            target_dec,
        )
        if separation <= float(pending["accuracy_arcmin"]):
            self._pending_goto_refine = None
            self._write_controller_status(
                "refine_complete",
                f"GoTo refine within {separation:.1f} arcmin",
                target_ra=target_ra,
                target_dec=target_dec,
                refine_error_arcmin=separation,
            )
            self._console("INDI refine\nwithin target")
            return

        self._pending_goto_refine = None
        if not self.sync_mount(current_ra, current_dec):
            self._write_controller_status(
                "refine_failed", "Could not sync current solve"
            )
            return
        self.goto_target(target_ra, target_dec, refine_after_goto=False)
        self._write_controller_status(
            "refine_sent",
            f"Refine GoTo sent; error {separation:.1f} arcmin",
            target_ra=target_ra,
            target_dec=target_dec,
            refine_error_arcmin=separation,
        )
        self._console("INDI refine\nGoTo sent")

    def _arm_goto_motion(self, ra_deg: float, dec_deg: float) -> None:
        self._goto_motion = {
            "target_ra": ra_deg % 360.0,
            "target_dec": dec_deg,
            "started_at": time.monotonic(),
            "complete_ready_since": None,
            "indi_seen_busy": False,
            "onstep_seen_goto_active": False,
        }

    def _complete_goto_motion(self, message: str = "GoTo complete") -> None:
        if self._goto_motion is None:
            return

        target_ra = self._goto_motion.get("target_ra")
        target_dec = self._goto_motion.get("target_dec")
        self._goto_motion = None
        self._read_current_position()
        self._write_controller_status(
            "connected",
            message,
            target_ra=target_ra,
            target_dec=target_dec,
        )
        logger.info("%s: RA %s Dec %s", message, target_ra, target_dec)

    def _check_goto_motion(self) -> None:
        if self._goto_motion is None:
            return
        if self._pending_goto_refine is not None:
            return
        if self._manual_motion_direction is not None:
            return

        started_at = float(self._goto_motion.get("started_at", 0.0))
        elapsed = time.monotonic() - started_at
        if elapsed < GOTO_COMPLETE_MIN_SECONDS:
            return

        is_busy = self._indi_mount_is_busy()
        current_position = None if is_busy is True else self._read_current_position()
        if self._goto_completion_ready(
            self._goto_motion,
            is_busy,
            time.monotonic(),
            current_position=current_position,
        ):
            self._complete_goto_motion()
            return

        if elapsed > GOTO_COMPLETE_FALLBACK_SECONDS:
            logger.warning(
                "Could not read INDI GoTo busy state after %.1fs; assuming complete",
                elapsed,
            )
            self._complete_goto_motion("GoTo status timeout; assuming complete")

    def _guide_direction_for_error(
        self,
        current_ra: float,
        current_dec: float,
        target_ra: float,
        target_dec: float,
        accuracy_arcmin: float,
    ) -> Optional[str]:
        ra_delta = shortest_ra_delta_deg(target_ra, current_ra)
        dec_delta = target_dec - current_dec
        ra_arcmin = ra_delta * 60.0 * math.cos(math.radians(current_dec))
        dec_arcmin = dec_delta * 60.0
        component_threshold = max(0.5, accuracy_arcmin / 2.0)

        ns = None
        if dec_arcmin > component_threshold:
            ns = "north"
        elif dec_arcmin < -component_threshold:
            ns = "south"

        we = None
        if ra_arcmin > component_threshold:
            we = "east"
        elif ra_arcmin < -component_threshold:
            we = "west"

        if ns and we:
            return ns + we
        return ns or we

    def toggle_guide_correction(
        self,
        enabled: Optional[bool] = None,
        target_ra: Any = None,
        target_dec: Any = None,
        accuracy_arcmin: Any = None,
    ) -> bool:
        if enabled is None:
            enabled = not self._guide_correction_enabled

        if not enabled:
            self._guide_correction_enabled = False
            self._write_controller_status("connected", "Guide correction disabled")
            self._console("Guide corr\nOff")
            return True

        try:
            target = (
                float(target_ra) % 360.0,
                float(target_dec),
            )
        except (TypeError, ValueError):
            target = self._last_goto_target

        if target is None:
            self._write_controller_status(
                "guide_correction_failed",
                "No GoTo target for guide correction",
            )
            self._console("Guide corr\nno target")
            return False

        try:
            accuracy = float(accuracy_arcmin)
        except (TypeError, ValueError):
            accuracy = self._guide_correction_accuracy_arcmin

        self._guide_correction_enabled = True
        self._guide_correction_target = target
        self._guide_correction_accuracy_arcmin = max(0.1, accuracy)
        self._guide_correction_next_at = time.monotonic()
        self._guide_correction_last_solve_time = 0.0
        self._write_controller_status(
            "guide_correction",
            "Guide correction enabled",
            target_ra=target[0],
            target_dec=target[1],
        )
        self._console("Guide corr\nOn")
        return True

    def _check_guide_correction(self) -> None:
        if not self._guide_correction_enabled or self._guide_correction_target is None:
            return
        if self._manual_motion_direction is not None:
            return

        now = time.monotonic()
        if now < self._guide_correction_next_at:
            return

        solved = self._current_plate_solve()
        if solved is None:
            return

        current_ra, current_dec, solve_time = solved
        if solve_time is None or solve_time <= self._guide_correction_last_solve_time:
            return

        target_ra, target_dec = self._guide_correction_target
        separation = radec_separation_arcmin(
            current_ra,
            current_dec,
            target_ra,
            target_dec,
        )
        self._guide_correction_last_solve_time = solve_time
        self._guide_correction_next_at = now + GUIDE_CORRECTION_INTERVAL_SECONDS

        if separation <= self._guide_correction_accuracy_arcmin:
            self._write_controller_status(
                "guide_correction",
                f"Guide correction within {separation:.1f} arcmin",
                guide_error_arcmin=separation,
            )
            return

        direction = self._guide_direction_for_error(
            current_ra,
            current_dec,
            target_ra,
            target_dec,
            self._guide_correction_accuracy_arcmin,
        )
        if not direction:
            return

        if self.manual_move(direction, lease_seconds=GUIDE_CORRECTION_PULSE_SECONDS):
            self._write_controller_status(
                "guide_correction",
                f"Guide correction pulse {direction}; error {separation:.1f} arcmin",
                guide_error_arcmin=separation,
                guide_direction=direction,
            )

    def sync_mount(self, ra_deg: float, dec_deg: float) -> bool:
        if not self.connect() or self.client is None or self.device is None:
            return False

        if not self.client.set_switch(self.device, "ON_COORD_SET", "SYNC"):
            self._write_controller_status("sync_failed", "Could not set INDI SYNC mode")
            return False

        if not self.client.set_number(
            self.device,
            "EQUATORIAL_EOD_COORD",
            {"RA": (ra_deg % 360.0) / 15.0, "DEC": dec_deg},
        ):
            self._write_controller_status(
                "sync_failed", "Could not set sync coordinates"
            )
            return False

        self.client.set_switch(self.device, "ON_COORD_SET", "TRACK")
        self.client.set_switch(self.device, "TELESCOPE_TRACK_STATE", "TRACK_ON")
        self.set_current_position(ra_deg, dec_deg)
        logger.info("Mount synced to RA %.4f Dec %.4f", ra_deg, dec_deg)
        self._console("INDI mount\nsynced")
        return True

    def goto_target(
        self,
        ra_deg: float,
        dec_deg: float,
        refine_after_goto: bool = False,
        refine_accuracy_arcmin: Any = None,
    ) -> bool:
        self._last_goto_target = (ra_deg % 360.0, dec_deg)
        if not self.connect() or self.client is None or self.device is None:
            return False

        if not self.client.set_switch(self.device, "ON_COORD_SET", "SLEW"):
            self._write_controller_status("goto_failed", "Could not set INDI SLEW mode")
            return False

        if not self.client.set_number(
            self.device,
            "EQUATORIAL_EOD_COORD",
            {"RA": (ra_deg % 360.0) / 15.0, "DEC": dec_deg},
        ):
            self._write_controller_status(
                "goto_failed", "Could not set target coordinates"
            )
            return False

        self._arm_goto_motion(ra_deg, dec_deg)
        self._write_controller_status(
            "slewing",
            "GoTo target command sent",
            target_ra=ra_deg % 360.0,
            target_dec=dec_deg,
        )
        logger.info("Mount GoTo RA %.4f Dec %.4f", ra_deg, dec_deg)
        self._console("INDI mount\nGoTo sent")
        if refine_after_goto:
            self._arm_goto_refine(ra_deg, dec_deg, refine_accuracy_arcmin)
        return True

    def stop_mount(self) -> bool:
        if not self._apply_indi_properties(
            [self._indi_property_on("TELESCOPE_ABORT_MOTION.ABORT")],
            "stopped",
            "Mount stop command sent",
            "stop_failed",
        ):
            self._console("INDI stop\nfailed")
            return False

        self._clear_manual_motion_deadline()
        self._goto_motion = None
        logger.info("Mount stop command sent")
        self._console("INDI mount\nstopped")
        return True

    def manual_move(self, direction: str, lease_seconds: Any = None) -> bool:
        direction = direction.lower()
        motion_map = {
            "north": [self._indi_property_on("TELESCOPE_MOTION_NS.MOTION_NORTH")],
            "south": [self._indi_property_on("TELESCOPE_MOTION_NS.MOTION_SOUTH")],
            "east": [self._indi_property_on("TELESCOPE_MOTION_WE.MOTION_WEST")],
            "west": [self._indi_property_on("TELESCOPE_MOTION_WE.MOTION_EAST")],
            "northeast": [
                self._indi_property_on("TELESCOPE_MOTION_NS.MOTION_NORTH"),
                self._indi_property_on("TELESCOPE_MOTION_WE.MOTION_WEST"),
            ],
            "northwest": [
                self._indi_property_on("TELESCOPE_MOTION_NS.MOTION_NORTH"),
                self._indi_property_on("TELESCOPE_MOTION_WE.MOTION_EAST"),
            ],
            "southeast": [
                self._indi_property_on("TELESCOPE_MOTION_NS.MOTION_SOUTH"),
                self._indi_property_on("TELESCOPE_MOTION_WE.MOTION_WEST"),
            ],
            "southwest": [
                self._indi_property_on("TELESCOPE_MOTION_NS.MOTION_SOUTH"),
                self._indi_property_on("TELESCOPE_MOTION_WE.MOTION_EAST"),
            ],
        }
        if direction not in motion_map:
            logger.warning("Unknown manual mount direction: %s", direction)
            return False

        if not self._apply_indi_properties(
            motion_map[direction],
            "moving",
            f"Manual {direction} motion sent",
            "manual_failed",
        ):
            self._console("INDI motion\nfailed")
            return False

        self._arm_manual_motion_deadline(direction, lease_seconds)
        logger.info("Manual %s motion sent", direction)
        self._console(f"INDI move\n{direction}")
        return True

    def park_action(self, action: str) -> bool:
        action_map = {
            "park": ("TELESCOPE_PARK.PARK", "Mount parked"),
            "unpark": ("TELESCOPE_PARK.UNPARK", "Mount unparked"),
            "set_home": ("TELESCOPE_HOME.SET", "Home position set"),
            "return_home": ("TELESCOPE_HOME.GO", "Return home command sent"),
            "set_park": ("TELESCOPE_PARK_OPTION.PARK_CURRENT", "Park position set"),
        }
        if action not in action_map:
            logger.warning("Unknown park action: %s", action)
            return False

        property_name, message = action_map[action]
        if not self._apply_indi_properties(
            [self._indi_property_on(property_name)],
            "connected",
            message,
            "park_failed",
        ):
            self._console("INDI park\nfailed")
            return False

        self._console("INDI\n" + message)
        return True

    def change_step(self, multiplier: float) -> None:
        self.step_degrees = max(
            MIN_STEP_DEGREES,
            min(MAX_STEP_DEGREES, self.step_degrees * multiplier),
        )
        self._write_controller_status(
            "connected" if self.connected else "idle",
            f"Step size {self.step_degrees:.2f} deg",
        )
        self._console(f"INDI step\n{self.step_degrees:.2f} deg")

    def _read_driver_slew_rate(self) -> Optional[int]:
        properties = sys_utils.get_indi_onstep_properties(
            server_host=self.indi_host,
            server_port=self.indi_port,
            device_name=self._indi_device_name(),
        )
        for rate in range(10):
            if (
                properties.get(self._indi_property_name(f"TELESCOPE_SLEW_RATE.{rate}"))
                == "On"
            ):
                return rate
        return None

    def refresh_slew_rate(self) -> int:
        driver_rate = self._read_driver_slew_rate()
        if driver_rate is not None:
            self.slew_rate = driver_rate
            self._write_controller_status(
                "connected" if self.connected else "idle",
                f"Slew rate {self.slew_rate}",
            )
        return self.slew_rate

    def set_slew_rate(self, rate: int) -> bool:
        self.slew_rate = max(0, min(9, int(rate)))
        if not self._apply_indi_properties(
            [self._indi_property_on(f"TELESCOPE_SLEW_RATE.{self.slew_rate}")],
            "connected" if self.connected else "idle",
            f"Slew rate {self.slew_rate}",
            "slew_rate_failed",
        ):
            self._console("INDI speed\nfailed")
            return False
        self._console(f"INDI speed\n{self.slew_rate}")
        return True

    def _read_driver_guide_rate(self) -> Optional[tuple[float, float]]:
        properties = sys_utils.get_indi_onstep_properties(
            server_host=self.indi_host,
            server_port=self.indi_port,
            device_name=self._indi_device_name(),
        )
        try:
            guide_we = float(
                properties[self._indi_property_name("GUIDE_RATE.GUIDE_RATE_WE")]
            )
            guide_ns = float(
                properties[self._indi_property_name("GUIDE_RATE.GUIDE_RATE_NS")]
            )
        except (KeyError, TypeError, ValueError):
            return None
        return guide_we, guide_ns

    def set_guide_rate(self, rate: Any) -> bool:
        try:
            guide_we, guide_ns = rate
        except (TypeError, ValueError):
            guide_we = guide_ns = rate

        try:
            guide_we = float(guide_we)
            guide_ns = float(guide_ns)
        except (TypeError, ValueError):
            self._write_controller_status(
                "guide_rate_failed",
                f"Invalid guide rate {rate!r}",
            )
            return False

        guide_we = max(0.0, min(240.0, guide_we))
        guide_ns = max(0.0, min(240.0, guide_ns))
        if not self._apply_indi_properties(
            [
                f"{self._indi_property_name('GUIDE_RATE.GUIDE_RATE_WE')}={guide_we:g}",
                f"{self._indi_property_name('GUIDE_RATE.GUIDE_RATE_NS')}={guide_ns:g}",
            ],
            "connected" if self.connected else "idle",
            f"Guide rate WE {guide_we:g} / NS {guide_ns:g}",
            "guide_rate_failed",
        ):
            self._console("INDI guide\nfailed")
            return False
        deadline = time.monotonic() + 1.5
        observed: Optional[tuple[float, float]] = None
        while time.monotonic() < deadline:
            observed = self._read_driver_guide_rate()
            if observed is not None:
                observed_we, observed_ns = observed
                tolerance_we = max(0.05, abs(guide_we) * 0.02)
                tolerance_ns = max(0.05, abs(guide_ns) * 0.02)
                if (
                    abs(observed_we - guide_we) <= tolerance_we
                    and abs(observed_ns - guide_ns) <= tolerance_ns
                ):
                    self._console(f"INDI guide\n{guide_we:g}/{guide_ns:g}")
                    return True
            time.sleep(0.2)

        observed_text = "not readable"
        if observed is not None:
            observed_text = f"WE {observed[0]:g} / NS {observed[1]:g}"
        self._write_controller_status(
            "guide_rate_failed",
            (
                f"Requested GUIDE_RATE WE {guide_we:g} / NS {guide_ns:g}, "
                f"but driver reports {observed_text}"
            ),
        )
        self._console("INDI guide\nmismatch")
        return False

    def _read_tracking_enabled_from_properties(self) -> Optional[bool]:
        properties = sys_utils.get_indi_onstep_properties(
            server_host=self.indi_host,
            server_port=self.indi_port,
            device_name=self._indi_device_name(),
        )
        track_on = properties.get(
            self._indi_property_name("TELESCOPE_TRACK_STATE.TRACK_ON")
        )
        track_off = properties.get(
            self._indi_property_name("TELESCOPE_TRACK_STATE.TRACK_OFF")
        )
        if track_on in {"On", "Off"}:
            return track_on == "On"
        if track_off in {"On", "Off"}:
            return track_off != "On"
        return None

    def _read_tracking_enabled(self) -> Optional[bool]:
        for attempt in range(3):
            property_state = self._read_tracking_enabled_from_properties()
            if property_state is not None:
                return property_state

            track_on_switch = self._device_switch_on(
                "TELESCOPE_TRACK_STATE", "TRACK_ON"
            )
            track_off_switch = self._device_switch_on(
                "TELESCOPE_TRACK_STATE", "TRACK_OFF"
            )
            if track_on_switch is not None:
                return track_on_switch
            if track_off_switch is not None:
                return not track_off_switch

            tracking_text = self._device_text_value("OnStep Status", "Tracking")
            tracking_lower = tracking_text.strip().lower()
            if tracking_lower in {"on", "tracking", "active"}:
                return True
            if tracking_lower in {"off", "idle", "not tracking", "inactive"}:
                return False

            if attempt < 2:
                time.sleep(0.2)
        return None

    def _confirm_tracking_state(self, enabled: bool, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            property_state = self._read_tracking_enabled_from_properties()
            if property_state is enabled:
                return True
            if property_state is None:
                cached_state = self._read_tracking_enabled()
                if cached_state is enabled:
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)

    def set_tracking(self, enabled: bool) -> bool:
        property_name = (
            "TELESCOPE_TRACK_STATE.TRACK_ON"
            if enabled
            else "TELESCOPE_TRACK_STATE.TRACK_OFF"
        )
        if not self._apply_indi_properties(
            [self._indi_property_on(property_name)],
            "connected" if self.connected else "idle",
            "Tracking enabled" if enabled else "Tracking disabled",
            "tracking_failed",
        ):
            if self._confirm_tracking_state(enabled):
                self._write_controller_status(
                    "connected" if self.connected else "idle",
                    "Tracking enabled" if enabled else "Tracking disabled",
                )
                self._console("Tracking\n" + ("on" if enabled else "off"))
                return True
            self._console("INDI tracking\nfailed")
            return False
        if not self._confirm_tracking_state(enabled):
            self._write_controller_status(
                "tracking_failed",
                (
                    "Tracking command was sent but driver readback did not "
                    f"confirm {'On' if enabled else 'Off'}"
                ),
            )
            self._console("INDI tracking\nmismatch")
            return False
        self._console("Tracking\n" + ("on" if enabled else "off"))
        return True

    def _validate_backlash_value(self, value: Any) -> int:
        try:
            backlash = int(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("Backlash must be a number") from exc
        if not BACKLASH_MIN_VALUE <= backlash <= BACKLASH_MAX_VALUE:
            raise ValueError(
                f"Backlash must be between {BACKLASH_MIN_VALUE} and {BACKLASH_MAX_VALUE}"
            )
        return backlash

    def _read_float_property(self, properties: dict[str, str], property_names):
        if isinstance(property_names, str):
            property_names = (property_names,)
        for property_name in property_names:
            try:
                value = properties.get(self._indi_property_name(property_name))
                if value in (None, ""):
                    continue
                return int(float(value))
            except (TypeError, ValueError):
                continue
        return None

    def refresh_backlash(self) -> tuple[Optional[int], Optional[int]]:
        properties = sys_utils.get_indi_onstep_properties(
            server_host=self.indi_host,
            server_port=self.indi_port,
            device_name=self._indi_device_name(),
        )
        self.backlash_de = self._read_float_property(
            properties, sys_utils.ONSTEP_BACKLASH_DE_FALLBACK_PROPERTIES
        )
        self.backlash_ra = self._read_float_property(
            properties, sys_utils.ONSTEP_BACKLASH_RA_FALLBACK_PROPERTIES
        )
        self._write_controller_status(
            "connected" if self.connected else "idle",
            "Backlash values refreshed",
        )
        return self.backlash_ra, self.backlash_de

    def _apply_backlash_values(self, backlash_ra: int, backlash_de: int) -> bool:
        try:
            result = self._apply_indi_backlash(backlash_ra, backlash_de)
        except Exception as exc:
            logger.exception("INDI backlash vector update failed")
            self._write_controller_status("backlash_failed", str(exc))
            return False

        if not result.get("ok"):
            error = result.get("stderr") or result.get("stdout") or "Backlash failed"
            logger.warning("INDI backlash returned failure: %s", error)
            self._write_controller_status("backlash_failed", error)
            return False

        self.backlash_ra = backlash_ra
        self.backlash_de = backlash_de
        return True

    def set_backlash(self, ra_value: Any, de_value: Any) -> bool:
        backlash_ra = self._validate_backlash_value(ra_value)
        backlash_de = self._validate_backlash_value(de_value)
        if not self._apply_backlash_values(backlash_ra, backlash_de):
            self._console("Backlash\nsave failed")
            return False

        self._backlash_auto = None
        self._write_controller_status(
            "connected" if self.connected else "idle",
            f"Backlash saved RA {backlash_ra} DE {backlash_de}",
        )
        self._console("Backlash\nsaved")
        return True

    def _backlash_auto_status(self, state: str, message: str, **extra: Any) -> None:
        if self._backlash_auto is None:
            self._backlash_auto = {}
        self._backlash_auto.update(
            {
                "state": state,
                "message": message,
                "updated": time.time(),
            }
        )
        self._backlash_auto.update(extra)
        self._write_controller_status(f"backlash_auto_{state}", message)

    def _clear_backlash_stop_request(self) -> None:
        try:
            STOP_REQUEST_FILE.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not clear backlash stop request", exc_info=True)

    def _backlash_stop_requested(self) -> bool:
        try:
            with open(STOP_REQUEST_FILE, encoding="utf-8") as stop_in:
                payload = json.load(stop_in)
        except FileNotFoundError:
            return False
        except (json.JSONDecodeError, OSError):
            logger.debug("Could not read backlash stop request", exc_info=True)
            return True

        requested_at = payload.get("requested_at")
        try:
            requested_at = float(requested_at)
        except (TypeError, ValueError):
            requested_at = time.time()
        if requested_at <= self._backlash_stop_seen_at:
            return False
        self._backlash_stop_seen_at = requested_at
        return True

    def stop_backlash_auto(self, message: str = "Backlash motion test stopped") -> bool:
        self.stop_mount()
        self.set_tracking(False)
        if (
            not self._backlash_auto
            or self._backlash_auto.get("auto_mode") != BACKLASH_AUTO_MODE_COMPASS_GOTO
        ):
            self._backlash_auto = {
                "axis": "Mount frame",
                "state": "stopped",
                "message": message,
                "auto_mode": BACKLASH_AUTO_MODE_COMPASS_GOTO,
                "mode": "motion_test",
                "stopped_at": time.time(),
            }
            self._write_controller_status("backlash_auto_stopped", message)
        else:
            self._backlash_auto_status(
                "stopped",
                message,
                phase="stopped",
                stopped_at=time.time(),
            )
        self._clear_backlash_stop_request()
        self._console("Backlash test\nstopped")
        return True

    def _abort_backlash_if_requested(self, phase_label: str) -> bool:
        if not self._backlash_stop_requested():
            return False
        self.stop_backlash_auto(f"{phase_label}: backlash motion test stopped")
        return True

    def _backlash_cancelable_sleep(self, seconds: float, phase_label: str) -> bool:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            if self._abort_backlash_if_requested(phase_label):
                return False
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        return not self._abort_backlash_if_requested(phase_label)

    def _current_imu_sample(self) -> Any:
        if self.shared_state is None or not hasattr(self.shared_state, "imu"):
            return None
        try:
            imu = self.shared_state.imu()
        except Exception:
            logger.exception("Could not read IMU sample for backlash calculation")
            return None
        quat = getattr(imu, "quat", None)
        if quat is None:
            return None
        timestamp = getattr(imu, "timestamp", None)
        if timestamp:
            try:
                if time.time() - float(timestamp) > BACKLASH_AUTO_IMU_STALE_SECONDS:
                    return None
            except (TypeError, ValueError):
                return None
        return imu

    def _copy_imu_quat(self, quat_value: Any) -> Any:
        if quat_value is None:
            return None
        return quaternion.quaternion(
            float(quat_value.w),
            float(quat_value.x),
            float(quat_value.y),
            float(quat_value.z),
        )

    def _percentile_value(self, sorted_values: list[int], fraction: float) -> int:
        if not sorted_values:
            return 0
        index = min(
            len(sorted_values) - 1,
            max(0, math.ceil((len(sorted_values) - 1) * fraction)),
        )
        return int(sorted_values[index])

    def _spread_percent(self, min_value: int, max_value: int, base_value: int) -> float:
        if base_value <= 0:
            return 0.0
        return round(((max_value - min_value) / base_value) * 100.0, 1)

    def _filter_compass_backlash_values(self, values: list[int]) -> dict[str, Any]:
        if not values:
            return {
                "values": [],
                "excluded_values": [],
                "raw_values": [],
                "raw_min": 0,
                "raw_max": 0,
                "min": 0,
                "max": 0,
                "mean": 0.0,
                "median": 0.0,
                "p75": 0,
                "p90": 0,
                "trimmed_mean": 0.0,
                "trim_low_count": 0,
                "trim_high_count": 0,
                "trim_fraction": BACKLASH_COMPASS_TRIM_FRACTION,
                "spread_percent": 0.0,
                "raw_spread_percent": 0.0,
                "sample_count": 0,
                "filtered_count": 0,
                "excluded_count": 0,
                "filter_strategy": "reject_1deg_then_middle_40_mean",
            }

        sorted_all_values = sorted(values)
        raw_min_value = min(sorted_all_values)
        raw_max_value = max(sorted_all_values)
        trim_count = int(len(sorted_all_values) * BACKLASH_COMPASS_TRIM_FRACTION)
        if trim_count * 2 >= len(sorted_all_values):
            trim_count = 0
        trimmed_values = sorted_all_values[
            trim_count : len(sorted_all_values) - trim_count
            if trim_count
            else len(sorted_all_values)
        ]
        if not trimmed_values:
            trimmed_values = sorted_all_values
            trim_count = 0
        mean_value = statistics.mean(trimmed_values)
        median_value = statistics.median(trimmed_values)
        max_value = max(trimmed_values)
        min_value = min(trimmed_values)
        p75_value = self._percentile_value(trimmed_values, 0.75)
        p90_value = self._percentile_value(trimmed_values, 0.90)
        excluded_values = list(sorted_all_values)
        for value in trimmed_values:
            if value in excluded_values:
                excluded_values.remove(value)
        return {
            "values": trimmed_values,
            "excluded_values": excluded_values,
            "raw_values": sorted_all_values,
            "raw_min": raw_min_value,
            "raw_max": raw_max_value,
            "min": min_value,
            "max": max_value,
            "mean": round(mean_value, 1),
            "median": round(float(median_value), 1),
            "p75": p75_value,
            "p90": p90_value,
            "trimmed_mean": round(mean_value, 1),
            "trim_low_count": trim_count,
            "trim_high_count": trim_count,
            "trim_fraction": BACKLASH_COMPASS_TRIM_FRACTION,
            "spread_percent": self._spread_percent(min_value, max_value, mean_value),
            "raw_spread_percent": self._spread_percent(
                raw_min_value, raw_max_value, mean_value
            ),
            "sample_count": len(sorted_all_values),
            "filtered_count": len(trimmed_values),
            "excluded_count": len(excluded_values),
            "filter_strategy": "reject_1deg_then_middle_40_mean",
        }

    def _imu_status_payload(self, imu_sample=None) -> dict[str, Any]:
        if imu_sample is None:
            imu_sample = self._current_imu_sample()
        if imu_sample is None:
            return {
                "available": False,
                "uses_magnetometer": False,
                "fusion_mode": "unknown",
                "calibration_status": None,
                "fully_calibrated": False,
                "heading_ready": False,
            }
        calibration_status = getattr(imu_sample, "calibration_status", None)
        mag_level = None
        gyro_level = None
        if calibration_status is not None and len(calibration_status) >= 4:
            gyro_level = int(calibration_status[1])
            mag_level = int(calibration_status[3])
        full_calibration = (
            bool(getattr(imu_sample, "uses_magnetometer", False))
            and calibration_status is not None
            and min(int(value) for value in calibration_status) >= 3
        )
        heading_ready = (
            bool(getattr(imu_sample, "uses_magnetometer", False))
            and mag_level is not None
            and mag_level >= 3
            and (gyro_level is None or gyro_level > 0)
        )
        return {
            "available": True,
            "uses_magnetometer": bool(getattr(imu_sample, "uses_magnetometer", False)),
            "fusion_mode": getattr(imu_sample, "fusion_mode", "unknown"),
            "calibration_status": calibration_status,
            "fully_calibrated": full_calibration,
            "heading_ready": heading_ready,
            "mag_calibration": mag_level,
            "gyro_calibration": gyro_level,
        }

    def start_backlash_compass_goto_loop(
        self,
        repeats: int = BACKLASH_COMPASS_GOTO_REPEATS,
        offset_deg: float = BACKLASH_COMPASS_GOTO_OFFSET_DEG,
    ) -> bool:
        self._clear_backlash_stop_request()
        repeats = max(1, int(repeats))
        offset_deg = max(0.1, float(offset_deg))
        self._backlash_auto = {
            "axis": "Mount frame",
            "state": "starting",
            "message": "Compass GoTo loop setup started",
            "started_at": time.time(),
            "mode": "motion_test",
            "auto_mode": BACKLASH_AUTO_MODE_COMPASS_GOTO,
            "method": "compass_goto_loop_motion_record",
            "repeats": repeats,
            "offset_deg": offset_deg,
            "coordinate_records": [],
            "steps": [
                "Enable IMU compass/NDOF mode and restart if required",
                "Wait until IMU MAG calibration reaches 3",
                "Press Continue after MAG calibration is ready",
                "Verify location/time, Unparked state, tracking Off, and current mount coordinates",
                "Sync mount coordinates to the current IMU Alt/Az",
                "Run one-axis tests in mount order: AZ/ALT for Alt/Az or RA/DEC for EQ",
                "For each axis, move to the initial anti-offset point",
                "Record initial mount and IMU coordinates for the active axis",
                "GoTo the active-axis offset point while holding the inactive-axis coordinate fixed",
                "Repeat return-to-start and offset GoTo cycles for each active axis",
            ],
        }

        cfg = config.Config()
        if not bool(cfg.get_option("imu_use_magnetometer", False)):
            cfg.set_option("imu_use_magnetometer", True)
            self._backlash_auto_status(
                "restart_required",
                (
                    "IMU Compass was Off. It has been changed to On; restart "
                    "PiFinder, then start this test again."
                ),
                phase="compass_enable",
                imu_compass_enabled=True,
            )
            self._console("IMU compass\nrestart needed")
            return False

        imu_sample = self._current_imu_sample()
        imu_status = self._imu_status_payload(imu_sample)
        if not imu_status["uses_magnetometer"]:
            self._backlash_auto_status(
                "restart_required",
                (
                    "IMU Compass is enabled in config, but the running IMU is not "
                    "using NDOF mode. Restart PiFinder, then start this test again."
                ),
                phase="compass_enable",
                imu_status=imu_status,
            )
            self._console("IMU compass\nrestart needed")
            return False

        if not self.connect():
            self._backlash_auto_status(
                "failed",
                "Could not connect to INDI mount before compass calibration",
                phase="device_state",
                imu_status=imu_status,
            )
            return False

        if not self.stop_mount():
            self._backlash_auto_status(
                "failed",
                "Could not send mount stop before compass calibration",
                phase="device_state",
                imu_status=imu_status,
            )
            return False
        original_tracking = self._read_tracking_enabled()
        if original_tracking is None:
            self._backlash_auto_status(
                "failed",
                "Could not read tracking state before compass calibration",
                phase="device_state",
                imu_status=imu_status,
            )
            return False
        if original_tracking and not self.set_tracking(False):
            self._backlash_auto_status(
                "failed",
                "Could not disable tracking before compass calibration",
                phase="device_state",
                imu_status=imu_status,
                original_tracking=original_tracking,
            )
            return False

        if imu_status["heading_ready"]:
            calibration_message = (
                "IMU MAG calibration is ready; press Continue Motion Test to "
                "start the backlash movement."
            )
        else:
            calibration_message = (
                "Move/rotate the PiFinder by hand until IMU MAG calibration is 3, "
                "then press Continue Motion Test."
            )

        self._backlash_auto_status(
            "waiting_for_calibration",
            calibration_message,
            phase="compass_calibration",
            imu_status=imu_status,
            original_tracking=original_tracking,
            tracking_disabled_for_calibration=bool(original_tracking),
        )
        self._console("Compass cal\nthen continue")
        return True

    def _record_compass_goto_point(
        self,
        label: str,
        sequence: int,
        *,
        command_start_ra: Optional[float] = None,
        command_start_dec: Optional[float] = None,
        command_start_altitude: Optional[float] = None,
        command_start_azimuth: Optional[float] = None,
        target_ra: Optional[float] = None,
        target_dec: Optional[float] = None,
        target_altitude: Optional[float] = None,
        target_azimuth: Optional[float] = None,
        movement_frame: Optional[str] = None,
        active_axis: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        mount_position = self._read_current_position()
        imu_altaz = self._current_imu_altaz()
        imu_status = self._imu_status_payload()
        if mount_position is None or imu_altaz is None:
            self._backlash_auto_status(
                "failed",
                f"{label}: could not record both mount and calibrated IMU coordinates",
                phase=label,
                imu_status=imu_status,
            )
            return None

        imu_radec = self._imu_altaz_to_radec(imu_altaz[0], imu_altaz[1])
        mount_altaz = self._radec_to_altaz(mount_position[0], mount_position[1])
        record = {
            "sequence": sequence,
            "label": label,
            "recorded_at": time.time(),
            "mount_ra": mount_position[0] % 360.0,
            "mount_dec": mount_position[1],
            "mount_altitude": None if mount_altaz is None else mount_altaz[0],
            "mount_azimuth": None if mount_altaz is None else mount_altaz[1],
            "imu_altitude": imu_altaz[0],
            "imu_azimuth": imu_altaz[1],
            "imu_ra": None if imu_radec is None else imu_radec[0] % 360.0,
            "imu_dec": None if imu_radec is None else imu_radec[1],
            "command_start_ra": None
            if command_start_ra is None
            else command_start_ra % 360.0,
            "command_start_dec": command_start_dec,
            "command_start_altitude": command_start_altitude,
            "command_start_azimuth": None
            if command_start_azimuth is None
            else command_start_azimuth % 360.0,
            "target_ra": None if target_ra is None else target_ra % 360.0,
            "target_dec": target_dec,
            "target_altitude": target_altitude,
            "target_azimuth": None
            if target_azimuth is None
            else target_azimuth % 360.0,
            "movement_frame": movement_frame,
            "active_axis": active_axis,
            "imu_status": imu_status,
        }
        return record

    def _append_compass_goto_record(
        self,
        records: list[dict[str, Any]],
        label: str,
        *,
        command_start_ra: Optional[float] = None,
        command_start_dec: Optional[float] = None,
        command_start_altitude: Optional[float] = None,
        command_start_azimuth: Optional[float] = None,
        target_ra: Optional[float] = None,
        target_dec: Optional[float] = None,
        target_altitude: Optional[float] = None,
        target_azimuth: Optional[float] = None,
        movement_frame: Optional[str] = None,
        active_axis: Optional[str] = None,
    ) -> bool:
        record = self._record_compass_goto_point(
            label,
            len(records) + 1,
            command_start_ra=command_start_ra,
            command_start_dec=command_start_dec,
            command_start_altitude=command_start_altitude,
            command_start_azimuth=command_start_azimuth,
            target_ra=target_ra,
            target_dec=target_dec,
            target_altitude=target_altitude,
            target_azimuth=target_azimuth,
            movement_frame=movement_frame,
            active_axis=active_axis,
        )
        if record is None:
            return False
        records.append(record)
        self._backlash_auto_status(
            "running",
            (
                f"{label}: mount RA {record['mount_ra']:.4f}, "
                f"DEC {record['mount_dec']:.4f}; IMU Alt "
                f"{record['imu_altitude']:.2f}, Az {record['imu_azimuth']:.2f}"
            ),
            phase=label,
            coordinate_records=records,
            imu_status=record["imu_status"],
        )
        return True

    def _backlash_active_axes(self, mount_model: str) -> tuple[str, ...]:
        return ("ra", "dec") if mount_model == "eq" else ("az", "alt")

    def _backlash_goto_command_and_wait(
        self,
        command: dict[str, Any],
        phase_label: str,
        active_axis: Optional[str] = None,
    ) -> bool:
        target_ra = float(command["target_ra"]) % 360.0
        target_dec = float(command["target_dec"])
        self._backlash_auto_status(
            "running",
            (f"{phase_label}: GoTo RA {target_ra:.4f}, " f"DEC {target_dec:.4f}"),
            phase=phase_label,
            active_axis=active_axis,
            target_ra=target_ra,
            target_dec=target_dec,
            target_altitude=command.get("target_altitude"),
            target_azimuth=command.get("target_azimuth"),
            movement_frame=command.get("movement_frame"),
        )
        if not self._goto_target_and_wait(
            target_ra,
            target_dec,
            phase_label,
            timeout=BACKLASH_COMPASS_GOTO_TIMEOUT_SECONDS,
        ):
            return False

        tracking_enabled = self._read_tracking_enabled()
        if tracking_enabled and not self.set_tracking(False):
            self._backlash_auto_status(
                "failed",
                f"{phase_label}: could not disable tracking after GoTo",
                phase=phase_label,
                active_axis=active_axis,
            )
            return False
        if tracking_enabled is None:
            self._backlash_auto_status(
                "running",
                f"{phase_label}: GoTo complete, tracking state unreadable",
                phase=phase_label,
                active_axis=active_axis,
            )
        return self._backlash_cancelable_sleep(
            BACKLASH_COMPASS_GOTO_SETTLE_SECONDS,
            f"{phase_label} settle",
        )

    def _compass_goto_loop_plan(
        self,
        start_ra: float,
        start_dec: float,
        offset_deg: float,
        active_axis: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        mount_model = self._backlash_mount_model()
        if mount_model == "eq":
            active_axis = (active_axis or "ra").lower()
            if active_axis not in {"ra", "dec"}:
                self._backlash_auto_status(
                    "failed",
                    f"Unsupported EQ backlash test axis: {active_axis}",
                    phase="target_calculation",
                    mount_model=mount_model,
                    movement_frame="radec",
                    active_axis=active_axis,
                )
                return None
            target_ra = (
                (start_ra + offset_deg) % 360.0
                if active_axis == "ra"
                else start_ra % 360.0
            )
            target_dec = start_dec + offset_deg if active_axis == "dec" else start_dec
            if target_dec > 89.0 or target_dec < -89.0:
                self._backlash_auto_status(
                    "failed",
                    (
                        f"Current DEC {start_dec:.2f} is too close to the pole for a "
                        f"{offset_deg:.1f} degree DEC test move"
                    ),
                    phase="target_calculation",
                    mount_model=mount_model,
                    movement_frame="radec",
                    start_ra=start_ra % 360.0,
                    start_dec=start_dec,
                    offset_deg=offset_deg,
                )
                return None
            start_altaz = self._radec_to_altaz(start_ra, start_dec)
            target_altaz = self._radec_to_altaz(target_ra, target_dec)
            return {
                "mount_model": mount_model,
                "movement_frame": "radec",
                "active_axis": active_axis,
                "start_ra": start_ra % 360.0,
                "start_dec": start_dec,
                "target_ra": target_ra,
                "target_dec": target_dec,
                "start_altitude": None if start_altaz is None else start_altaz[0],
                "start_azimuth": None if start_altaz is None else start_altaz[1],
                "target_altitude": None if target_altaz is None else target_altaz[0],
                "target_azimuth": None if target_altaz is None else target_altaz[1],
            }

        start_altaz = self._radec_to_altaz(start_ra, start_dec)
        if start_altaz is None:
            self._backlash_auto_status(
                "failed",
                (
                    "A usable PiFinder location/time is required to convert the "
                    "Alt/Az backlash test target to RA/DEC"
                ),
                phase="target_calculation",
                mount_model=mount_model,
                movement_frame="altaz",
                start_ra=start_ra % 360.0,
                start_dec=start_dec,
                offset_deg=offset_deg,
            )
            return None

        active_axis = (active_axis or "az").lower()
        if active_axis not in {"az", "alt"}:
            self._backlash_auto_status(
                "failed",
                f"Unsupported Alt/Az backlash test axis: {active_axis}",
                phase="target_calculation",
                mount_model=mount_model,
                movement_frame="altaz",
                active_axis=active_axis,
            )
            return None

        start_altitude, start_azimuth = start_altaz
        target_altitude = (
            start_altitude + offset_deg if active_axis == "alt" else start_altitude
        )
        target_azimuth = (
            (start_azimuth + offset_deg) % 360.0
            if active_axis == "az"
            else start_azimuth
        )
        if (
            target_altitude < BACKLASH_AUTO_SAFE_MIN_ALT_DEG
            or target_altitude > BACKLASH_AUTO_SAFE_MAX_ALT_DEG
        ):
            self._backlash_auto_status(
                "failed",
                (
                    f"Alt/Az target altitude {target_altitude:.1f} deg is outside "
                    f"the safe backlash range "
                    f"{BACKLASH_AUTO_SAFE_MIN_ALT_DEG:.0f}-"
                    f"{BACKLASH_AUTO_SAFE_MAX_ALT_DEG:.0f} deg"
                ),
                phase="target_calculation",
                mount_model=mount_model,
                movement_frame="altaz",
                start_ra=start_ra % 360.0,
                start_dec=start_dec,
                start_altitude=start_altitude,
                start_azimuth=start_azimuth,
                target_altitude=target_altitude,
                target_azimuth=target_azimuth,
                offset_deg=offset_deg,
            )
            return None

        target_radec = self._altaz_to_radec(target_altitude, target_azimuth)
        if target_radec is None:
            self._backlash_auto_status(
                "failed",
                "Could not convert Alt/Az backlash target to RA/DEC",
                phase="target_calculation",
                mount_model=mount_model,
                movement_frame="altaz",
                start_altitude=start_altitude,
                start_azimuth=start_azimuth,
                target_altitude=target_altitude,
                target_azimuth=target_azimuth,
                offset_deg=offset_deg,
            )
            return None

        return {
            "mount_model": mount_model,
            "movement_frame": "altaz",
            "active_axis": active_axis,
            "start_ra": start_ra % 360.0,
            "start_dec": start_dec,
            "start_altitude": start_altitude,
            "start_azimuth": start_azimuth,
            "target_altitude": target_altitude,
            "target_azimuth": target_azimuth,
            "target_ra": target_radec[0] % 360.0,
            "target_dec": target_radec[1],
        }

    def _compass_goto_init_command(
        self,
        current_ra: float,
        current_dec: float,
        offset_deg: float,
        active_axis: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        mount_model = self._backlash_mount_model()
        if mount_model == "eq":
            active_axis = (active_axis or "ra").lower()
            if active_axis not in {"ra", "dec"}:
                self._backlash_auto_status(
                    "failed",
                    f"Unsupported EQ backlash init axis: {active_axis}",
                    phase="init_target_calculation",
                    mount_model=mount_model,
                    movement_frame="radec",
                    active_axis=active_axis,
                )
                return None
            init_ra = (
                (current_ra - offset_deg) % 360.0
                if active_axis == "ra"
                else current_ra % 360.0
            )
            init_dec = current_dec - offset_deg if active_axis == "dec" else current_dec
            if init_dec > 89.0 or init_dec < -89.0:
                self._backlash_auto_status(
                    "failed",
                    (
                        f"Current DEC {current_dec:.2f} is too close to the pole for a "
                        f"{offset_deg:.1f} degree initial DEC test move"
                    ),
                    phase="init_target_calculation",
                    mount_model=mount_model,
                    movement_frame="radec",
                    current_ra=current_ra % 360.0,
                    current_dec=current_dec,
                    offset_deg=offset_deg,
                )
                return None
            init_altaz = self._radec_to_altaz(init_ra, init_dec)
            return {
                "target_ra": init_ra,
                "target_dec": init_dec,
                "target_altitude": None if init_altaz is None else init_altaz[0],
                "target_azimuth": None if init_altaz is None else init_altaz[1],
                "movement_frame": "radec",
                "mount_model": mount_model,
                "active_axis": active_axis,
            }

        current_altaz = self._radec_to_altaz(current_ra, current_dec)
        if current_altaz is None:
            self._backlash_auto_status(
                "failed",
                (
                    "A usable PiFinder location/time is required to convert the "
                    "Alt/Az backlash init target to RA/DEC"
                ),
                phase="init_target_calculation",
                mount_model=mount_model,
                movement_frame="altaz",
                current_ra=current_ra % 360.0,
                current_dec=current_dec,
                offset_deg=offset_deg,
            )
            return None

        active_axis = (active_axis or "az").lower()
        if active_axis not in {"az", "alt"}:
            self._backlash_auto_status(
                "failed",
                f"Unsupported Alt/Az backlash init axis: {active_axis}",
                phase="init_target_calculation",
                mount_model=mount_model,
                movement_frame="altaz",
                active_axis=active_axis,
            )
            return None

        init_altitude = (
            current_altaz[0] - offset_deg if active_axis == "alt" else current_altaz[0]
        )
        init_azimuth = (
            (current_altaz[1] - offset_deg) % 360.0
            if active_axis == "az"
            else current_altaz[1]
        )
        if (
            init_altitude < BACKLASH_AUTO_SAFE_MIN_ALT_DEG
            or init_altitude > BACKLASH_AUTO_SAFE_MAX_ALT_DEG
        ):
            self._backlash_auto_status(
                "failed",
                (
                    f"Alt/Az initial target altitude {init_altitude:.1f} deg is "
                    f"outside the safe backlash range "
                    f"{BACKLASH_AUTO_SAFE_MIN_ALT_DEG:.0f}-"
                    f"{BACKLASH_AUTO_SAFE_MAX_ALT_DEG:.0f} deg"
                ),
                phase="init_target_calculation",
                mount_model=mount_model,
                movement_frame="altaz",
                current_ra=current_ra % 360.0,
                current_dec=current_dec,
                current_altitude=current_altaz[0],
                current_azimuth=current_altaz[1],
                target_altitude=init_altitude,
                target_azimuth=init_azimuth,
                offset_deg=offset_deg,
            )
            return None

        init_radec = self._altaz_to_radec(init_altitude, init_azimuth)
        if init_radec is None:
            self._backlash_auto_status(
                "failed",
                "Could not convert Alt/Az backlash init target to RA/DEC",
                phase="init_target_calculation",
                mount_model=mount_model,
                movement_frame="altaz",
                current_altitude=current_altaz[0],
                current_azimuth=current_altaz[1],
                target_altitude=init_altitude,
                target_azimuth=init_azimuth,
                offset_deg=offset_deg,
            )
            return None

        return {
            "target_ra": init_radec[0] % 360.0,
            "target_dec": init_radec[1],
            "target_altitude": init_altitude,
            "target_azimuth": init_azimuth,
            "movement_frame": "altaz",
            "mount_model": mount_model,
            "active_axis": active_axis,
        }

    def _sync_backlash_mount_to_imu(self) -> bool:
        imu_altaz = self._current_imu_altaz()
        imu_status = self._imu_status_payload()
        if imu_altaz is None:
            self._backlash_auto_status(
                "failed",
                "Could not read calibrated IMU Alt/Az before backlash test",
                phase="imu_mount_sync",
                imu_status=imu_status,
            )
            return False

        imu_radec = self._imu_altaz_to_radec(imu_altaz[0], imu_altaz[1])
        if imu_radec is None:
            self._backlash_auto_status(
                "failed",
                "Could not convert current IMU Alt/Az to RA/DEC for mount sync",
                phase="imu_mount_sync",
                imu_altitude=imu_altaz[0],
                imu_azimuth=imu_altaz[1],
                imu_status=imu_status,
            )
            return False

        sync_ra, sync_dec = imu_radec
        self._backlash_auto_status(
            "running",
            ("Syncing mount coordinates to current IMU Alt/Az before " "backlash test"),
            phase="imu_mount_sync",
            imu_altitude=imu_altaz[0],
            imu_azimuth=imu_altaz[1],
            sync_ra=sync_ra % 360.0,
            sync_dec=sync_dec,
            imu_status=imu_status,
        )
        if not self.sync_mount(sync_ra, sync_dec):
            self._backlash_auto_status(
                "failed",
                "Could not sync mount coordinates to the current IMU position",
                phase="imu_mount_sync",
                imu_altitude=imu_altaz[0],
                imu_azimuth=imu_altaz[1],
                sync_ra=sync_ra % 360.0,
                sync_dec=sync_dec,
                imu_status=imu_status,
            )
            return False

        # sync_mount() enables tracking for normal operation; this test needs
        # tracking off so drift is not counted as backlash travel.
        if not self.set_tracking(False):
            self._backlash_auto_status(
                "failed",
                "Could not turn tracking back off after IMU mount sync",
                phase="imu_mount_sync",
                imu_altitude=imu_altaz[0],
                imu_azimuth=imu_altaz[1],
                sync_ra=sync_ra % 360.0,
                sync_dec=sync_dec,
                imu_status=imu_status,
            )
            return False

        return True

    def _compass_goto_loop_command(
        self, plan: dict[str, Any], point: str
    ) -> Optional[dict[str, Any]]:
        movement_frame = str(plan.get("movement_frame") or "radec")
        if movement_frame == "altaz":
            altitude = plan.get(f"{point}_altitude")
            azimuth = plan.get(f"{point}_azimuth")
            if altitude is None or azimuth is None:
                return None
            radec = self._altaz_to_radec(float(altitude), float(azimuth))
            if radec is None:
                return None
            return {
                "target_ra": radec[0] % 360.0,
                "target_dec": radec[1],
                "target_altitude": float(altitude),
                "target_azimuth": float(azimuth) % 360.0,
                "movement_frame": movement_frame,
                "active_axis": plan.get("active_axis"),
            }

        return {
            "target_ra": float(plan[f"{point}_ra"]) % 360.0,
            "target_dec": float(plan[f"{point}_dec"]),
            "target_altitude": plan.get(f"{point}_altitude"),
            "target_azimuth": plan.get(f"{point}_azimuth"),
            "movement_frame": movement_frame,
            "active_axis": plan.get("active_axis"),
        }

    def _altaz_separation_deg(
        self, alt1_deg: float, az1_deg: float, alt2_deg: float, az2_deg: float
    ) -> float:
        alt1 = math.radians(alt1_deg)
        alt2 = math.radians(alt2_deg)
        az_delta = math.radians((az2_deg - az1_deg + 180.0) % 360.0 - 180.0)
        cos_sep = math.sin(alt1) * math.sin(alt2) + math.cos(alt1) * math.cos(
            alt2
        ) * math.cos(az_delta)
        return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))

    def _compass_goto_loop_directional_analysis(
        self, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        legs: list[dict[str, Any]] = []
        grouped_values: dict[str, list[int]] = {"offset": [], "return": []}
        axis_grouped_values: dict[str, dict[str, list[int]]] = {
            "offset": {"ra": [], "dec": [], "alt": [], "az": []},
            "return": {"ra": [], "dec": [], "alt": [], "az": []},
        }
        axis_direction_grouped_values: dict[str, list[int]] = {}
        imu_skipped_values: dict[str, list[int]] = {"offset": [], "return": []}
        imu_skipped_leg_indices: dict[str, list[int]] = {"offset": [], "return": []}
        threshold_skipped_values: dict[str, list[int]] = {"offset": [], "return": []}
        threshold_skipped_leg_indices: dict[str, list[int]] = {
            "offset": [],
            "return": [],
        }

        def signed_arcsec(value: Optional[float]) -> Optional[int]:
            if value is None:
                return None
            return int(round(value * 3600.0))

        def backlash_arcsec_from_signed(value: Optional[float]) -> Optional[int]:
            if value is None:
                return None
            return max(
                BACKLASH_MIN_VALUE,
                min(BACKLASH_MAX_VALUE, int(round(abs(value) * 3600.0))),
            )

        def direction_sign_from_delta(value: Optional[float]) -> Optional[str]:
            if value is None or abs(value) < 1e-9:
                return None
            return "positive" if value > 0 else "negative"

        def axis_direction_key(axis: str, delta: Optional[float]) -> Optional[str]:
            sign = direction_sign_from_delta(delta)
            if sign is None:
                return None
            return f"{axis}_{sign}"

        def axis_direction_label(axis: str, sign: str) -> str:
            axis_labels = {
                "ra": "RA",
                "dec": "DEC",
                "alt": "ALT",
                "az": "AZ",
            }
            suffix = "+" if sign == "positive" else "-"
            return f"{axis_labels.get(axis, axis.upper())}{suffix}"

        for index in range(1, len(records)):
            previous = records[index - 1]
            current = records[index]
            label = str(current.get("label", ""))
            if label.startswith("offset"):
                direction = "offset"
            elif label.startswith("return"):
                direction = "return"
            else:
                continue

            movement_frame = str(current.get("movement_frame") or "radec")
            active_axis = current.get("active_axis")
            mount_record_start_ra = float(previous["mount_ra"]) % 360.0
            mount_record_start_dec = float(previous["mount_dec"])
            mount_record_start_alt = previous.get("mount_altitude")
            mount_record_start_az = previous.get("mount_azimuth")
            command_start_ra = current.get("command_start_ra")
            command_start_dec = current.get("command_start_dec")
            command_start_alt = current.get("command_start_altitude")
            command_start_az = current.get("command_start_azimuth")
            fixed_command_start_ra = (
                float(command_start_ra) % 360.0
                if command_start_ra is not None
                else None
            )
            fixed_command_start_dec = (
                float(command_start_dec) if command_start_dec is not None else None
            )
            fixed_command_start_alt = (
                float(command_start_alt) if command_start_alt is not None else None
            )
            fixed_command_start_az = (
                float(command_start_az) % 360.0
                if command_start_az is not None
                else None
            )
            mount_start_ra = mount_record_start_ra
            mount_start_dec = mount_record_start_dec
            mount_start_alt = mount_record_start_alt
            mount_start_az = mount_record_start_az
            mount_end_ra = float(current["mount_ra"]) % 360.0
            mount_end_dec = float(current["mount_dec"])
            mount_end_alt = current.get("mount_altitude")
            mount_end_az = current.get("mount_azimuth")
            target_ra = current.get("target_ra")
            target_dec = current.get("target_dec")
            target_alt = current.get("target_altitude")
            target_az = current.get("target_azimuth")

            mount_delta_ra = shortest_ra_delta_deg(mount_end_ra, mount_start_ra)
            mount_delta_dec = mount_end_dec - mount_start_dec
            mount_delta_alt = (
                float(mount_end_alt) - float(mount_start_alt)
                if mount_start_alt is not None and mount_end_alt is not None
                else None
            )
            mount_delta_az = (
                shortest_ra_delta_deg(float(mount_end_az), float(mount_start_az))
                if mount_start_az is not None and mount_end_az is not None
                else None
            )
            mount_record_delta_ra = shortest_ra_delta_deg(
                mount_end_ra, mount_record_start_ra
            )
            mount_record_delta_dec = mount_end_dec - mount_record_start_dec
            mount_record_delta_alt = (
                float(mount_end_alt) - float(mount_record_start_alt)
                if mount_record_start_alt is not None and mount_end_alt is not None
                else None
            )
            mount_record_delta_az = (
                shortest_ra_delta_deg(float(mount_end_az), float(mount_record_start_az))
                if mount_record_start_az is not None and mount_end_az is not None
                else None
            )

            if (
                movement_frame == "altaz"
                and mount_start_alt is not None
                and mount_start_az is not None
                and mount_end_alt is not None
                and mount_end_az is not None
            ):
                mount_sep_deg = self._altaz_separation_deg(
                    float(mount_start_alt),
                    float(mount_start_az),
                    float(mount_end_alt),
                    float(mount_end_az),
                )
            else:
                mount_sep_deg = (
                    radec_separation_arcmin(
                        mount_start_ra,
                        mount_start_dec,
                        mount_end_ra,
                        mount_end_dec,
                    )
                    / 60.0
                )
            imu_sep_deg = self._altaz_separation_deg(
                float(previous["imu_altitude"]),
                float(previous["imu_azimuth"]),
                float(current["imu_altitude"]),
                float(current["imu_azimuth"]),
            )
            target_delta_ra = (
                shortest_ra_delta_deg(float(target_ra), mount_start_ra)
                if target_ra is not None
                else None
            )
            target_delta_dec = (
                float(target_dec) - mount_start_dec if target_dec is not None else None
            )
            target_delta_alt = (
                float(target_alt) - float(mount_start_alt)
                if target_alt is not None and mount_start_alt is not None
                else None
            )
            target_delta_az = (
                shortest_ra_delta_deg(float(target_az), float(mount_start_az))
                if target_az is not None and mount_start_az is not None
                else None
            )
            coordinate_error_ra_arcsec = (
                int(
                    round(
                        shortest_ra_delta_deg(mount_end_ra, float(target_ra)) * 3600.0
                    )
                )
                if target_ra is not None
                else None
            )
            coordinate_error_dec_arcsec = (
                int(round((mount_end_dec - float(target_dec)) * 3600.0))
                if target_dec is not None
                else None
            )
            coordinate_error_alt_arcsec = (
                int(round((float(mount_end_alt) - float(target_alt)) * 3600.0))
                if target_alt is not None and mount_end_alt is not None
                else None
            )
            coordinate_error_az_arcsec = (
                int(
                    round(
                        shortest_ra_delta_deg(float(mount_end_az), float(target_az))
                        * 3600.0
                    )
                )
                if target_az is not None and mount_end_az is not None
                else None
            )
            coordinate_backlash_ra_arcsec = (
                max(
                    BACKLASH_MIN_VALUE,
                    min(BACKLASH_MAX_VALUE, abs(coordinate_error_ra_arcsec)),
                )
                if coordinate_error_ra_arcsec is not None
                else None
            )
            coordinate_backlash_dec_arcsec = (
                max(
                    BACKLASH_MIN_VALUE,
                    min(BACKLASH_MAX_VALUE, abs(coordinate_error_dec_arcsec)),
                )
                if coordinate_error_dec_arcsec is not None
                else None
            )
            coordinate_backlash_alt_arcsec = (
                max(
                    BACKLASH_MIN_VALUE,
                    min(BACKLASH_MAX_VALUE, abs(coordinate_error_alt_arcsec)),
                )
                if coordinate_error_alt_arcsec is not None
                else None
            )
            coordinate_backlash_az_arcsec = (
                max(
                    BACKLASH_MIN_VALUE,
                    min(BACKLASH_MAX_VALUE, abs(coordinate_error_az_arcsec)),
                )
                if coordinate_error_az_arcsec is not None
                else None
            )
            imu_start_ra = previous.get("imu_ra")
            imu_start_dec = previous.get("imu_dec")
            imu_end_ra = current.get("imu_ra")
            imu_end_dec = current.get("imu_dec")
            imu_delta_ra = None
            imu_delta_dec = None
            imu_delta_alt = float(current["imu_altitude"]) - float(
                previous["imu_altitude"]
            )
            imu_delta_az = (
                float(current["imu_azimuth"]) - float(previous["imu_azimuth"]) + 180.0
            ) % 360.0 - 180.0
            motion_difference_ra_deg = None
            motion_difference_dec_deg = None
            motion_difference_alt_deg = None
            motion_difference_az_deg = None
            motion_backlash_ra_arcsec = None
            motion_backlash_dec_arcsec = None
            motion_backlash_alt_arcsec = None
            motion_backlash_az_arcsec = None
            if (
                imu_start_ra is not None
                and imu_start_dec is not None
                and imu_end_ra is not None
                and imu_end_dec is not None
            ):
                imu_delta_ra = shortest_ra_delta_deg(
                    float(imu_end_ra), float(imu_start_ra)
                )
                imu_delta_dec = float(imu_end_dec) - float(imu_start_dec)
                motion_difference_ra_deg = mount_delta_ra - imu_delta_ra
                motion_difference_dec_deg = mount_delta_dec - imu_delta_dec
                motion_backlash_ra_arcsec = backlash_arcsec_from_signed(
                    motion_difference_ra_deg
                )
                motion_backlash_dec_arcsec = backlash_arcsec_from_signed(
                    motion_difference_dec_deg
                )
            if mount_delta_alt is not None and mount_delta_az is not None:
                motion_difference_alt_deg = mount_delta_alt - imu_delta_alt
                motion_difference_az_deg = mount_delta_az - imu_delta_az
                motion_backlash_alt_arcsec = backlash_arcsec_from_signed(
                    motion_difference_alt_deg
                )
                motion_backlash_az_arcsec = backlash_arcsec_from_signed(
                    motion_difference_az_deg
                )
            if (
                movement_frame == "altaz"
                and motion_difference_alt_deg is not None
                and motion_difference_az_deg is not None
            ):
                raw_estimated_arcsec = int(
                    round(
                        math.hypot(
                            motion_difference_alt_deg,
                            motion_difference_az_deg,
                        )
                        * 3600.0
                    )
                )
            elif (
                motion_difference_ra_deg is not None
                and motion_difference_dec_deg is not None
            ):
                raw_estimated_arcsec = int(
                    round(
                        math.hypot(
                            motion_difference_ra_deg,
                            motion_difference_dec_deg,
                        )
                        * 3600.0
                    )
                )
            else:
                raw_estimated_arcsec = int(
                    round(max(0.0, mount_sep_deg - imu_sep_deg) * 3600.0)
                )
            estimated_arcsec = max(
                BACKLASH_MIN_VALUE, min(BACKLASH_MAX_VALUE, raw_estimated_arcsec)
            )
            threshold_rejected_axes: list[str] = []
            if movement_frame == "altaz":
                if (
                    motion_difference_alt_deg is not None
                    and abs(motion_difference_alt_deg) * 3600.0
                    >= BACKLASH_COMPASS_DIFF_REJECT_ARCSEC
                ):
                    threshold_rejected_axes.append("alt")
                if (
                    motion_difference_az_deg is not None
                    and abs(motion_difference_az_deg) * 3600.0
                    >= BACKLASH_COMPASS_DIFF_REJECT_ARCSEC
                ):
                    threshold_rejected_axes.append("az")
            else:
                if (
                    motion_difference_ra_deg is not None
                    and abs(motion_difference_ra_deg) * 3600.0
                    >= BACKLASH_COMPASS_DIFF_REJECT_ARCSEC
                ):
                    threshold_rejected_axes.append("ra")
                if (
                    motion_difference_dec_deg is not None
                    and abs(motion_difference_dec_deg) * 3600.0
                    >= BACKLASH_COMPASS_DIFF_REJECT_ARCSEC
                ):
                    threshold_rejected_axes.append("dec")
            threshold_rejected = (
                raw_estimated_arcsec >= BACKLASH_COMPASS_DIFF_REJECT_ARCSEC
                or bool(threshold_rejected_axes)
            )
            previous_imu_status = previous.get("imu_status") or {}
            current_imu_status = current.get("imu_status") or {}
            imu_heading_ready = bool(
                previous_imu_status.get("heading_ready")
                and current_imu_status.get("heading_ready")
            )
            if movement_frame == "altaz":
                movement_direction_deltas = {
                    "alt": target_delta_alt
                    if target_delta_alt is not None
                    else mount_delta_alt,
                    "az": target_delta_az
                    if target_delta_az is not None
                    else mount_delta_az,
                }
            else:
                movement_direction_deltas = {
                    "ra": target_delta_ra
                    if target_delta_ra is not None
                    else mount_delta_ra,
                    "dec": target_delta_dec
                    if target_delta_dec is not None
                    else mount_delta_dec,
                }
            axis_movement_directions = {
                axis: direction_sign_from_delta(delta)
                for axis, delta in movement_direction_deltas.items()
                if direction_sign_from_delta(delta) is not None
            }
            warmup = label.startswith("offset initial")
            leg = {
                "index": index,
                "direction": direction,
                "from_label": previous.get("label", ""),
                "to_label": label,
                "warmup": warmup,
                "active_axis": active_axis,
                "imu_heading_ready": imu_heading_ready,
                "mount_start_ra": mount_start_ra,
                "mount_start_dec": mount_start_dec,
                "mount_start_altitude": mount_start_alt,
                "mount_start_azimuth": mount_start_az,
                "mount_record_start_ra": mount_record_start_ra,
                "mount_record_start_dec": mount_record_start_dec,
                "mount_record_start_altitude": mount_record_start_alt,
                "mount_record_start_azimuth": mount_record_start_az,
                "mount_end_ra": mount_end_ra,
                "mount_end_dec": mount_end_dec,
                "mount_end_altitude": mount_end_alt,
                "mount_end_azimuth": mount_end_az,
                "command_start_ra": fixed_command_start_ra,
                "command_start_dec": fixed_command_start_dec,
                "command_start_altitude": fixed_command_start_alt,
                "command_start_azimuth": fixed_command_start_az,
                "target_ra": None if target_ra is None else float(target_ra) % 360.0,
                "target_dec": None if target_dec is None else float(target_dec),
                "target_altitude": None if target_alt is None else float(target_alt),
                "target_azimuth": None
                if target_az is None
                else float(target_az) % 360.0,
                "movement_frame": movement_frame,
                "axis_movement_directions": axis_movement_directions,
                "target_delta_ra": target_delta_ra,
                "target_delta_dec": target_delta_dec,
                "target_delta_altitude": target_delta_alt,
                "target_delta_azimuth": target_delta_az,
                "mount_delta_ra": mount_delta_ra,
                "mount_delta_dec": mount_delta_dec,
                "mount_delta_altitude": mount_delta_alt,
                "mount_delta_azimuth": mount_delta_az,
                "mount_record_delta_ra": mount_record_delta_ra,
                "mount_record_delta_dec": mount_record_delta_dec,
                "mount_record_delta_altitude": mount_record_delta_alt,
                "mount_record_delta_azimuth": mount_record_delta_az,
                "coordinate_error_ra_arcsec": coordinate_error_ra_arcsec,
                "coordinate_error_dec_arcsec": coordinate_error_dec_arcsec,
                "coordinate_error_alt_arcsec": coordinate_error_alt_arcsec,
                "coordinate_error_az_arcsec": coordinate_error_az_arcsec,
                "coordinate_backlash_ra_arcsec": coordinate_backlash_ra_arcsec,
                "coordinate_backlash_dec_arcsec": coordinate_backlash_dec_arcsec,
                "coordinate_backlash_alt_arcsec": coordinate_backlash_alt_arcsec,
                "coordinate_backlash_az_arcsec": coordinate_backlash_az_arcsec,
                "imu_start_ra": imu_start_ra,
                "imu_start_dec": imu_start_dec,
                "imu_end_ra": imu_end_ra,
                "imu_end_dec": imu_end_dec,
                "imu_delta_ra": imu_delta_ra,
                "imu_delta_dec": imu_delta_dec,
                "motion_difference_ra_arcsec": signed_arcsec(motion_difference_ra_deg),
                "motion_difference_dec_arcsec": signed_arcsec(
                    motion_difference_dec_deg
                ),
                "motion_difference_alt_arcsec": signed_arcsec(
                    motion_difference_alt_deg
                ),
                "motion_difference_az_arcsec": signed_arcsec(motion_difference_az_deg),
                "mount_sep_deg": mount_sep_deg,
                "imu_sep_deg": imu_sep_deg,
                "raw_estimated_arcsec": raw_estimated_arcsec,
                "estimated_arcsec": estimated_arcsec,
                "motion_difference_threshold_arcsec": BACKLASH_COMPASS_DIFF_REJECT_ARCSEC,
                "motion_difference_threshold_rejected": threshold_rejected,
                "motion_difference_threshold_rejected_axes": threshold_rejected_axes,
                "motion_backlash_ra_arcsec": motion_backlash_ra_arcsec,
                "motion_backlash_dec_arcsec": motion_backlash_dec_arcsec,
                "motion_backlash_alt_arcsec": motion_backlash_alt_arcsec,
                "motion_backlash_az_arcsec": motion_backlash_az_arcsec,
                "motion_backlash_combined_arcsec": estimated_arcsec,
                "imu_start_altitude": previous.get("imu_altitude"),
                "imu_start_azimuth": previous.get("imu_azimuth"),
                "imu_end_altitude": current.get("imu_altitude"),
                "imu_end_azimuth": current.get("imu_azimuth"),
                "imu_delta_alt": imu_delta_alt,
                "imu_delta_az": imu_delta_az,
            }
            legs.append(leg)
            if not leg["warmup"] and imu_heading_ready:
                if threshold_rejected:
                    threshold_skipped_values[direction].append(raw_estimated_arcsec)
                    threshold_skipped_leg_indices[direction].append(index)
                else:
                    grouped_values[direction].append(raw_estimated_arcsec)
                    if (
                        motion_backlash_ra_arcsec is not None
                        and leg["motion_difference_ra_arcsec"] is not None
                    ):
                        axis_grouped_values[direction]["ra"].append(
                            motion_backlash_ra_arcsec
                        )
                    if (
                        motion_backlash_dec_arcsec is not None
                        and leg["motion_difference_dec_arcsec"] is not None
                    ):
                        axis_grouped_values[direction]["dec"].append(
                            motion_backlash_dec_arcsec
                        )
                    if (
                        motion_backlash_alt_arcsec is not None
                        and leg["motion_difference_alt_arcsec"] is not None
                    ):
                        axis_grouped_values[direction]["alt"].append(
                            motion_backlash_alt_arcsec
                        )
                    if (
                        motion_backlash_az_arcsec is not None
                        and leg["motion_difference_az_arcsec"] is not None
                    ):
                        axis_grouped_values[direction]["az"].append(
                            motion_backlash_az_arcsec
                        )
                    if movement_frame == "altaz":
                        axis_direction_candidates = (
                            ("alt", target_delta_alt, motion_backlash_alt_arcsec),
                            ("az", target_delta_az, motion_backlash_az_arcsec),
                        )
                    else:
                        axis_direction_candidates = (
                            ("ra", target_delta_ra, motion_backlash_ra_arcsec),
                            ("dec", target_delta_dec, motion_backlash_dec_arcsec),
                        )
                    for axis, delta, value in axis_direction_candidates:
                        key = axis_direction_key(axis, delta)
                        if key is not None and value is not None:
                            axis_direction_grouped_values.setdefault(key, []).append(
                                value
                            )
            elif not leg["warmup"]:
                imu_skipped_values[direction].append(estimated_arcsec)
                imu_skipped_leg_indices[direction].append(index)

        direction_stats: dict[str, Any] = {}
        for direction, values in grouped_values.items():
            stats = self._filter_compass_backlash_values(values)
            normal_values = stats.get("values", [])
            normal_min = min(normal_values) if normal_values else None
            normal_max = max(normal_values) if normal_values else None
            normal_leg_indices = [
                leg["index"]
                for leg in legs
                if leg["direction"] == direction
                and not leg.get("warmup")
                and leg.get("imu_heading_ready")
                and not leg.get("motion_difference_threshold_rejected")
                and normal_min is not None
                and normal_min <= int(leg["raw_estimated_arcsec"]) <= normal_max
            ]
            direction_stats[direction] = {
                **stats,
                "normal_min": normal_min,
                "normal_max": normal_max,
                "normal_leg_indices": normal_leg_indices,
                "imu_skipped_count": len(imu_skipped_values[direction]),
                "imu_skipped_values": imu_skipped_values[direction],
                "imu_skipped_leg_indices": imu_skipped_leg_indices[direction],
                "threshold_reject_arcsec": BACKLASH_COMPASS_DIFF_REJECT_ARCSEC,
                "threshold_skipped_count": len(threshold_skipped_values[direction]),
                "threshold_skipped_values": threshold_skipped_values[direction],
                "threshold_skipped_leg_indices": threshold_skipped_leg_indices[
                    direction
                ],
                "total_leg_count": (
                    len(values)
                    + len(imu_skipped_values[direction])
                    + len(threshold_skipped_values[direction])
                ),
                "recommended_trimmed_mean": int(round(float(stats["trimmed_mean"])))
                if stats.get("filtered_count", 0)
                else 0,
                "recommended_median": int(round(float(stats["median"])))
                if stats.get("filtered_count", 0)
                else 0,
                "recommended_p75": int(stats["p75"])
                if stats.get("filtered_count", 0)
                else 0,
            }
            for axis in ("ra", "dec", "alt", "az"):
                axis_stats = self._filter_compass_backlash_values(
                    axis_grouped_values[direction][axis]
                )
                direction_stats[direction][f"{axis}_motion_backlash"] = {
                    **axis_stats,
                    "threshold_reject_arcsec": BACKLASH_COMPASS_DIFF_REJECT_ARCSEC,
                    "recommended_trimmed_mean": int(
                        round(float(axis_stats["trimmed_mean"]))
                    )
                    if axis_stats.get("filtered_count", 0)
                    else 0,
                    "recommended_median": int(round(float(axis_stats["median"])))
                    if axis_stats.get("filtered_count", 0)
                    else 0,
                    "recommended_p75": int(axis_stats["p75"])
                    if axis_stats.get("filtered_count", 0)
                    else 0,
                }

        axis_direction_stats: dict[str, Any] = {}
        for direction_key, values in axis_direction_grouped_values.items():
            axis, sign = direction_key.rsplit("_", 1)
            stats = self._filter_compass_backlash_values(values)
            axis_direction_stats[direction_key] = {
                **stats,
                "axis": axis,
                "direction": sign,
                "display_label": axis_direction_label(axis, sign),
                "threshold_reject_arcsec": BACKLASH_COMPASS_DIFF_REJECT_ARCSEC,
                "recommended_trimmed_mean": int(round(float(stats["trimmed_mean"])))
                if stats.get("filtered_count", 0)
                else 0,
                "recommended_median": int(round(float(stats["median"])))
                if stats.get("filtered_count", 0)
                else 0,
                "recommended_p75": int(stats["p75"])
                if stats.get("filtered_count", 0)
                else 0,
            }

        return {
            "method": "mount_minus_imu_angular_travel_filtered_by_direction",
            "note": (
                "Indoor directional estimate: each GoTo leg compares mount "
                "travel from the previous settled mount readback to the actual "
                "mount readback after the GoTo, then compares that with IMU "
                "travel recorded across the same leg. Alt/Az and EQ fixed S/T "
                "points are reused for record analysis; actual motion uses "
                "GoTo commands with only the active-axis coordinate offset. "
                "The offset-initial warm-up leg, legs with degraded IMU heading "
                "calibration, and legs where mount-vs-IMU travel differs by at "
                "least 1 degree are excluded. Remaining values are sorted, the "
                "lowest 30% and highest 30% are discarded, and the middle 40% "
                "mean is used as the recommendation."
            ),
            "legs": legs,
            "direction_stats": direction_stats,
            "axis_direction_stats": axis_direction_stats,
        }

    def continue_backlash_compass_goto_loop(self) -> bool:
        if (
            not self._backlash_auto
            or self._backlash_auto.get("auto_mode") != BACKLASH_AUTO_MODE_COMPASS_GOTO
        ):
            return self.start_backlash_compass_goto_loop()

        repeats = int(self._backlash_auto.get("repeats", BACKLASH_COMPASS_GOTO_REPEATS))
        offset_deg = float(
            self._backlash_auto.get("offset_deg", BACKLASH_COMPASS_GOTO_OFFSET_DEG)
        )
        if self._abort_backlash_if_requested("continue"):
            return False

        imu_status = self._imu_status_payload()
        if not imu_status["heading_ready"]:
            self._backlash_auto_status(
                "waiting_for_calibration",
                (
                    "IMU MAG calibration is not ready yet. Move/rotate the device "
                    "by hand until MAG is 3, then press Continue Motion Test again."
                ),
                phase="compass_calibration",
                imu_status=imu_status,
            )
            self._console("Compass cal\nnot ready")
            return False

        records: list[dict[str, Any]] = []
        original_tracking: Optional[bool] = self._backlash_auto.get("original_tracking")
        try:
            if not self.connect():
                self._backlash_auto_status("failed", "Could not connect to INDI mount")
                return False

            latitude, longitude, _elevation, dt = self._shared_location_time_values()
            location_time_available = (
                latitude is not None and longitude is not None and dt is not None
            )
            if not location_time_available:
                self._backlash_auto_status(
                    "running",
                    (
                        "PiFinder shared location/time is not locked in the "
                        "mount-control process; continuing because this motion "
                        "test records mount/IMU coordinates from the active "
                        "OnStep session."
                    ),
                    phase="device_state",
                    location_time_available=False,
                )

            park_state = self._home_park_status_fields().get("park_state", "Unknown")
            if park_state != "Unparked":
                self._backlash_auto_status(
                    "failed",
                    f"Mount must be Unparked before the compass GoTo loop ({park_state})",
                    phase="device_state",
                    park_state=park_state,
                )
                return False

            current_tracking = self._read_tracking_enabled()
            if current_tracking is None:
                self._backlash_auto_status(
                    "failed",
                    "Could not read tracking state before compass GoTo loop",
                    phase="device_state",
                )
                return False
            if original_tracking is None:
                original_tracking = current_tracking
            if current_tracking and not self.set_tracking(False):
                self._backlash_auto_status(
                    "failed",
                    "Could not disable tracking before compass GoTo loop",
                    phase="device_state",
                )
                return False

            if not self._sync_backlash_mount_to_imu():
                return False

            mount_model = self._backlash_mount_model()
            active_axes = self._backlash_active_axes(mount_model)
            plan: Optional[dict[str, Any]] = None
            start_ra = start_dec = target_ra = target_dec = 0.0
            movement_frame = "radec" if mount_model == "eq" else "altaz"

            for active_axis in active_axes:
                axis_label = active_axis.upper()
                start_position = self._read_current_position()
                if start_position is None:
                    self._backlash_auto_status(
                        "failed",
                        f"Could not read current mount coordinates before {axis_label}",
                        phase="pre_init_position",
                        active_axis=active_axis,
                    )
                    return False
                pre_init_ra, pre_init_dec = start_position
                init_command = self._compass_goto_init_command(
                    pre_init_ra,
                    pre_init_dec,
                    offset_deg,
                    active_axis=active_axis,
                )
                if init_command is None:
                    return False
                init_label = f"init {axis_label}"
                self._backlash_auto_status(
                    "running",
                    (
                        f"Moving {axis_label} to initial anti-offset point "
                        "before calculating fixed backlash command points"
                    ),
                    phase=init_label,
                    mount_model=init_command.get("mount_model"),
                    movement_frame=init_command.get("movement_frame"),
                    active_axis=active_axis,
                    active_axes=list(active_axes),
                    pre_init_ra=pre_init_ra % 360.0,
                    pre_init_dec=pre_init_dec,
                    target_ra=init_command.get("target_ra"),
                    target_dec=init_command.get("target_dec"),
                    target_altitude=init_command.get("target_altitude"),
                    target_azimuth=init_command.get("target_azimuth"),
                    offset_deg=offset_deg,
                    imu_status=imu_status,
                )
                if not self._backlash_goto_command_and_wait(
                    init_command,
                    init_label,
                    active_axis,
                ):
                    return False

                start_position = self._read_current_position()
                if start_position is None:
                    self._backlash_auto_status(
                        "failed",
                        (
                            "Could not read current mount coordinates after "
                            f"{axis_label} init move"
                        ),
                        phase="initial_position",
                        active_axis=active_axis,
                    )
                    return False
                start_ra, start_dec = start_position
                plan = self._compass_goto_loop_plan(
                    start_ra,
                    start_dec,
                    offset_deg,
                    active_axis=active_axis,
                )
                if plan is None:
                    return False
                mount_model = str(plan.get("mount_model") or "altaz")
                movement_frame = str(plan.get("movement_frame") or "radec")
                start_command = self._compass_goto_loop_command(plan, "start")
                offset_command = self._compass_goto_loop_command(plan, "target")
                if start_command is None or offset_command is None:
                    self._backlash_auto_status(
                        "failed",
                        (
                            f"{axis_label}: could not calculate compass GoTo loop "
                            "start/offset commands"
                        ),
                        phase="target_calculation",
                        mount_model=mount_model,
                        movement_frame=movement_frame,
                        active_axis=active_axis,
                        offset_deg=offset_deg,
                    )
                    return False
                target_ra = float(offset_command["target_ra"])
                target_dec = float(offset_command["target_dec"])

                self._backlash_auto_status(
                    "running",
                    (
                        f"Compass GoTo loop {axis_label} using "
                        f"{movement_frame.upper()} frame: start RA "
                        f"{start_ra:.4f}, DEC {start_dec:.4f}; target RA "
                        f"{target_ra:.4f}, DEC {target_dec:.4f}"
                    ),
                    phase="initial_position",
                    mount_model=mount_model,
                    movement_frame=movement_frame,
                    active_axis=active_axis,
                    active_axes=list(active_axes),
                    start_ra=start_ra % 360.0,
                    start_dec=start_dec,
                    start_altitude=plan.get("start_altitude"),
                    start_azimuth=plan.get("start_azimuth"),
                    target_ra=target_ra,
                    target_dec=target_dec,
                    target_altitude=plan.get("target_altitude"),
                    target_azimuth=plan.get("target_azimuth"),
                    offset_deg=offset_deg,
                    repeats=repeats,
                    imu_status=imu_status,
                )

                initial_label = f"initial {axis_label}"
                if not self._append_compass_goto_record(
                    records,
                    initial_label,
                    active_axis=active_axis,
                ):
                    return False

                offset_initial_label = f"offset initial {axis_label}"
                if not self._backlash_goto_command_and_wait(
                    offset_command,
                    offset_initial_label,
                    active_axis,
                ):
                    return False
                if not self._append_compass_goto_record(
                    records,
                    offset_initial_label,
                    command_start_ra=float(start_command["target_ra"]),
                    command_start_dec=float(start_command["target_dec"]),
                    command_start_altitude=start_command.get("target_altitude"),
                    command_start_azimuth=start_command.get("target_azimuth"),
                    target_ra=float(offset_command["target_ra"]),
                    target_dec=float(offset_command["target_dec"]),
                    target_altitude=offset_command.get("target_altitude"),
                    target_azimuth=offset_command.get("target_azimuth"),
                    movement_frame=movement_frame,
                    active_axis=active_axis,
                ):
                    return False

                for repeat_index in range(1, repeats + 1):
                    if not self._backlash_cancelable_sleep(
                        BACKLASH_COMPASS_GOTO_BETWEEN_SECONDS,
                        f"{axis_label} repeat {repeat_index} pause",
                    ):
                        return False
                    return_label = f"return {repeat_index} {axis_label}"
                    start_command = self._compass_goto_loop_command(plan, "start")
                    if start_command is None:
                        self._backlash_auto_status(
                            "failed",
                            f"{return_label}: could not calculate start command",
                            phase=return_label,
                            mount_model=mount_model,
                            movement_frame=movement_frame,
                            active_axis=active_axis,
                        )
                        return False
                    if not self._backlash_goto_command_and_wait(
                        start_command,
                        return_label,
                        active_axis,
                    ):
                        return False
                    if not self._append_compass_goto_record(
                        records,
                        return_label,
                        command_start_ra=float(offset_command["target_ra"]),
                        command_start_dec=float(offset_command["target_dec"]),
                        command_start_altitude=offset_command.get("target_altitude"),
                        command_start_azimuth=offset_command.get("target_azimuth"),
                        target_ra=float(start_command["target_ra"]),
                        target_dec=float(start_command["target_dec"]),
                        target_altitude=start_command.get("target_altitude"),
                        target_azimuth=start_command.get("target_azimuth"),
                        movement_frame=movement_frame,
                        active_axis=active_axis,
                    ):
                        return False

                    offset_label = f"offset {repeat_index} {axis_label}"
                    offset_command = self._compass_goto_loop_command(plan, "target")
                    if offset_command is None:
                        self._backlash_auto_status(
                            "failed",
                            f"{offset_label}: could not calculate offset command",
                            phase=offset_label,
                            mount_model=mount_model,
                            movement_frame=movement_frame,
                            active_axis=active_axis,
                        )
                        return False
                    if not self._backlash_goto_command_and_wait(
                        offset_command,
                        offset_label,
                        active_axis,
                    ):
                        return False
                    if not self._append_compass_goto_record(
                        records,
                        offset_label,
                        command_start_ra=float(start_command["target_ra"]),
                        command_start_dec=float(start_command["target_dec"]),
                        command_start_altitude=start_command.get("target_altitude"),
                        command_start_azimuth=start_command.get("target_azimuth"),
                        target_ra=float(offset_command["target_ra"]),
                        target_dec=float(offset_command["target_dec"]),
                        target_altitude=offset_command.get("target_altitude"),
                        target_azimuth=offset_command.get("target_azimuth"),
                        movement_frame=movement_frame,
                        active_axis=active_axis,
                    ):
                        return False

            directional_analysis = self._compass_goto_loop_directional_analysis(records)
            self._backlash_auto_status(
                "complete",
                (
                    f"Compass GoTo loop complete: {len(records)} coordinate "
                    "records captured"
                ),
                phase="complete",
                coordinate_records=records,
                mount_model=mount_model,
                movement_frame=movement_frame,
                active_axes=list(active_axes),
                start_ra=start_ra % 360.0,
                start_dec=start_dec,
                start_altitude=plan.get("start_altitude"),
                start_azimuth=plan.get("start_azimuth"),
                target_ra=plan.get("target_ra"),
                target_dec=plan.get("target_dec"),
                target_altitude=plan.get("target_altitude"),
                target_azimuth=plan.get("target_azimuth"),
                offset_deg=offset_deg,
                repeats=repeats,
                imu_status=self._imu_status_payload(),
                directional_analysis=directional_analysis,
                estimate_type="motion_record_only",
                valid=True,
            )
            self._console("Compass loop\ncomplete")
            return True
        except Exception as exc:
            logger.exception("Compass GoTo loop failed")
            self._backlash_auto_status(
                "failed",
                f"Compass GoTo loop failed: {exc}",
                coordinate_records=records,
            )
            self._console("Compass loop\nfailed")
            return False
        finally:
            self.stop_mount()
            final_state = (
                self._backlash_auto.get("state") if self._backlash_auto else None
            )
            if original_tracking and final_state == "complete":
                self.set_tracking(True)

    def _screen_direction(self) -> str:
        try:
            return str(config.Config().get_option("screen_direction", "right"))
        except Exception:
            logger.warning(
                "Could not read screen direction; using right", exc_info=True
            )
            return "right"

    def _current_imu_altaz(self) -> Optional[tuple[float, float]]:
        imu = self._current_imu_sample()
        if imu is None:
            return None

        is_calibrated = getattr(imu, "is_calibrated", None)
        if callable(is_calibrated) and not is_calibrated():
            return None

        try:
            q_x2cam = (
                self._copy_imu_quat(imu.quat)
                * ImuDeadReckoning._q_imu2cam(self._screen_direction())
            ).normalized()
            if not all(
                math.isfinite(float(component))
                for component in (q_x2cam.w, q_x2cam.x, q_x2cam.y, q_x2cam.z)
            ):
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
        except Exception:
            logger.exception("Could not derive IMU Alt/Az for backlash safety")
            return None

    def _imu_altaz_to_radec(
        self, altitude_deg: float, azimuth_deg: float
    ) -> Optional[tuple[float, float]]:
        return self._altaz_to_radec(altitude_deg, azimuth_deg)

    def _altaz_to_radec(
        self, altitude_deg: float, azimuth_deg: float
    ) -> Optional[tuple[float, float]]:
        latitude, longitude, elevation, dt = self._shared_location_time_values(
            include_default_location=True
        )
        if latitude is None or longitude is None or dt is None:
            return None

        try:
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            if getattr(dt, "tzinfo", None) is None:
                dt = dt.replace(tzinfo=timezone.utc)
            calc_utils.sf_utils.set_location(latitude, longitude, elevation or 0.0)
            ra_deg, dec_deg = calc_utils.sf_utils.altaz_to_radec(
                altitude_deg, azimuth_deg, dt
            )
            return ra_deg % 360.0, dec_deg
        except Exception:
            logger.debug("Could not convert IMU Alt/Az to RA/DEC", exc_info=True)
            return None

    def _radec_to_altaz(
        self, ra_deg: float, dec_deg: float
    ) -> Optional[tuple[float, float]]:
        latitude, longitude, elevation, dt = self._shared_location_time_values(
            include_default_location=True
        )
        if latitude is None or longitude is None or dt is None:
            return None

        try:
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            if getattr(dt, "tzinfo", None) is None:
                dt = dt.replace(tzinfo=timezone.utc)
            calc_utils.sf_utils.set_location(latitude, longitude, elevation or 0.0)
            alt_deg, az_deg = calc_utils.sf_utils.radec_to_altaz(ra_deg, dec_deg, dt)
            return alt_deg, az_deg % 360.0
        except Exception:
            logger.debug("Could not convert RA/DEC to Alt/Az", exc_info=True)
            return None

    def _backlash_mount_model(self) -> str:
        try:
            mount_type = str(config.Config().get_option("mount_type", "Alt/Az"))
        except Exception:
            logger.warning("Could not read mount type; using Alt/Az", exc_info=True)
            return "altaz"
        mount_type = mount_type.strip().lower()
        if "eq" in mount_type or "equatorial" in mount_type:
            return "eq"
        return "altaz"

    def _goto_target_and_wait(
        self,
        ra_deg: float,
        dec_deg: float,
        phase_label: str,
        timeout: float = BACKLASH_AUTO_GOTO_TIMEOUT_SECONDS,
    ) -> bool:
        if self._abort_backlash_if_requested(phase_label):
            return False
        self._backlash_auto_status(
            "running",
            f"{phase_label}: GoTo sent",
            phase=phase_label,
            target_ra=ra_deg % 360.0,
            target_dec=dec_deg,
        )
        if not self.goto_target(ra_deg, dec_deg, refine_after_goto=False):
            self._backlash_auto_status(
                "failed",
                f"{phase_label}: could not send GoTo",
                phase=phase_label,
            )
            return False

        start = time.monotonic()
        motion = self._goto_motion
        if motion is None:
            motion = {
                "started_at": start,
                "complete_ready_since": None,
                "indi_seen_busy": False,
                "onstep_seen_goto_active": False,
            }
        last_retry_at = start
        retry_count = 0
        while time.monotonic() - start < timeout:
            if self._abort_backlash_if_requested(phase_label):
                return False
            elapsed = time.monotonic() - start
            if elapsed >= GOTO_COMPLETE_MIN_SECONDS:
                busy = self._indi_mount_is_busy()
                if busy is True:
                    self._goto_completion_ready(motion, busy, time.monotonic())
                if busy is False:
                    current_position = self._read_current_position()
                    if current_position is not None:
                        separation_deg = (
                            radec_separation_arcmin(
                                current_position[0],
                                current_position[1],
                                ra_deg,
                                dec_deg,
                            )
                            / 60.0
                        )
                        if separation_deg > BACKLASH_AUTO_GOTO_TARGET_TOLERANCE_DEG:
                            now = time.monotonic()
                            if (
                                retry_count < BACKLASH_GOTO_MAX_RETRIES
                                and now - last_retry_at
                                >= BACKLASH_GOTO_RETRY_AFTER_SECONDS
                            ):
                                retry_count += 1
                                last_retry_at = now
                                self._backlash_auto_status(
                                    "running",
                                    (
                                        f"{phase_label}: target is still "
                                        f"{separation_deg:.2f} deg away; "
                                        f"resending GoTo ({retry_count}/"
                                        f"{BACKLASH_GOTO_MAX_RETRIES})"
                                    ),
                                    phase=phase_label,
                                    target_ra=ra_deg % 360.0,
                                    target_dec=dec_deg,
                                    current_ra=current_position[0],
                                    current_dec=current_position[1],
                                    target_error_deg=separation_deg,
                                    retry=retry_count,
                                )
                                self.goto_target(
                                    ra_deg, dec_deg, refine_after_goto=False
                                )
                                motion = self._goto_motion or motion
                                time.sleep(BACKLASH_AUTO_GOTO_POLL_SECONDS)
                                continue
                            motion["complete_ready_since"] = None
                            self._backlash_auto_status(
                                "running",
                                (
                                    f"{phase_label}: INDI is idle but target is "
                                    f"still {separation_deg:.2f} deg away"
                                ),
                                phase=phase_label,
                                target_ra=ra_deg % 360.0,
                                target_dec=dec_deg,
                                current_ra=current_position[0],
                                current_dec=current_position[1],
                                target_error_deg=separation_deg,
                                target_tolerance_deg=(
                                    BACKLASH_AUTO_GOTO_TARGET_TOLERANCE_DEG
                                ),
                            )
                            time.sleep(BACKLASH_AUTO_GOTO_POLL_SECONDS)
                            continue
                    if self._goto_completion_ready(
                        motion,
                        busy,
                        time.monotonic(),
                        current_position=current_position,
                    ):
                        self._complete_goto_motion(f"{phase_label}: GoTo complete")
                        return True
                if busy is None:
                    self._read_current_position()
            self._backlash_auto_status(
                "running",
                f"{phase_label}: waiting for GoTo completion",
                phase=phase_label,
                goto_wait_seconds=round(elapsed, 1),
            )
            time.sleep(BACKLASH_AUTO_GOTO_POLL_SECONDS)

        self.stop_mount()
        self._backlash_auto_status(
            "failed",
            (
                f"{phase_label}: GoTo did not complete or target was not reached "
                f"within {timeout:.0f} seconds; check OnStep mount limits"
            ),
            phase=phase_label,
        )
        return False

    def _normalize_backlash_auto_mode(self, mode: Any) -> str:
        mode_key = str(mode or BACKLASH_AUTO_MODE_COMPASS_GOTO).strip().lower()
        aliases = {
            "compass": BACKLASH_AUTO_MODE_COMPASS_GOTO,
            "goto_loop": BACKLASH_AUTO_MODE_COMPASS_GOTO,
            "compass_goto": BACKLASH_AUTO_MODE_COMPASS_GOTO,
        }
        mode_key = aliases.get(mode_key, mode_key)
        if mode_key not in BACKLASH_AUTO_MODES:
            return BACKLASH_AUTO_MODE_COMPASS_GOTO
        return mode_key

    def auto_calculate_backlash(
        self,
        _axis: str = "",
        mode: str = BACKLASH_AUTO_MODE_COMPASS_GOTO,
        repeats: Any = None,
    ) -> bool:
        mode = self._normalize_backlash_auto_mode(mode)
        if mode != BACKLASH_AUTO_MODE_COMPASS_GOTO:
            logger.warning("Unknown backlash auto mode: %s", mode)
            return False
        repeat_count = BACKLASH_COMPASS_GOTO_REPEATS
        if repeats not in (None, ""):
            repeat_count = int(repeats)
        return self.start_backlash_compass_goto_loop(repeats=repeat_count)

    def _align_session_status(self, state: str, message: str) -> None:
        if self._multipoint_align is not None:
            self._multipoint_align["state"] = state
            self._multipoint_align["message"] = message
            self._multipoint_align["updated"] = time.time()
        self._write_controller_status(
            "align_" + state,
            message,
        )

    def _current_align_session(self) -> Optional[dict[str, Any]]:
        session = self._multipoint_align
        if not session or not session.get("active"):
            return None
        return session

    def _set_current_align_star(
        self, session: dict[str, Any], star: dict[str, Any]
    ) -> dict[str, Any]:
        current_star = {
            "name": str(star.get("name") or "SkySafari Target"),
            "ra": float(star["ra"]) % 360.0,
            "dec": float(star["dec"]),
            "mag": star.get("mag"),
        }
        session["current_star"] = current_star
        session["updated"] = time.time()
        return current_star

    def _align_auto_star(self, session: dict[str, Any]) -> dict[str, Any]:
        completed = session.get("completed", [])
        used_names = {str(star.get("name", "")).casefold() for star in completed}
        try:
            latitude, longitude, elevation, dt = self._shared_location_time_values()
            if latitude is None or longitude is None or dt is None:
                raise ValueError("No locked location/time for altitude filtering")
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            if getattr(dt, "tzinfo", None) is None:
                dt = dt.replace(tzinfo=timezone.utc)
            calc_utils.sf_utils.set_location(latitude, longitude, elevation or 0.0)
            visible_candidates = []
            for star in BRIGHT_ALIGN_STARS:
                if star["name"].casefold() in used_names:
                    continue
                alt, _az = calc_utils.sf_utils.radec_to_altaz(
                    float(star["ra"]), float(star["dec"]), dt
                )
                if alt >= 20.0:
                    visible_candidates.append((alt, star))
            if visible_candidates:
                return dict(max(visible_candidates, key=lambda item: item[0])[1])
        except Exception:
            logger.debug(
                "Could not altitude-filter auto alignment stars", exc_info=True
            )
        return next_align_star(completed)

    def _align_goto_current_star(self, session: dict[str, Any]) -> bool:
        current_star = session.get("current_star")
        if not current_star:
            self._align_session_status("waiting", "Select an alignment star")
            return False

        session["state"] = "moving"
        session["message"] = f"GoTo {current_star['name']} sent"
        session["updated"] = time.time()
        if not self.goto_target(
            float(current_star["ra"]),
            float(current_star["dec"]),
            refine_after_goto=False,
        ):
            self._align_session_status(
                "failed", f"Could not GoTo {current_star['name']}"
            )
            return False

        self._align_session_status(
            "adjust",
            (
                f"Center {current_star['name']} manually, "
                "then confirm the alignment point"
            ),
        )
        self._console(
            f"Align {session['completed_points'] + 1}\n{current_star['name']}"
        )
        return True

    def start_multipoint_align(
        self,
        mode: str = "manual",
        points: Any = None,
        star_name: str | None = None,
    ) -> bool:
        mode = (mode or "manual").strip().lower()
        if mode not in {"manual", "auto"}:
            mode = "manual"
        total_points = clamp_align_points(points)
        self._multipoint_align = {
            "active": True,
            "mode": mode,
            "total_points": total_points,
            "completed_points": 0,
            "completed": [],
            "current_star": None,
            "available_stars": [star["name"] for star in BRIGHT_ALIGN_STARS],
            "state": "waiting",
            "message": "Select an alignment star",
            "started_at": time.time(),
            "updated": time.time(),
        }
        session = self._multipoint_align

        if mode == "auto":
            star = self._align_auto_star(session)
            self._set_current_align_star(session, star)
            self._align_session_status(
                "moving", f"Auto alignment point 1/{total_points}: {star['name']}"
            )
            return self._align_goto_current_star(session)

        if star_name:
            return self.select_multipoint_align_star(star_name, goto=False)

        self._align_session_status(
            "waiting", f"Manual alignment 0/{total_points}: select a star"
        )
        self._console("Multi align\nselect star")
        return True

    def select_multipoint_align_star(self, star_name: str, goto: bool = False) -> bool:
        session = self._current_align_session()
        if session is None:
            session_started = self.start_multipoint_align(
                mode="manual", points=None, star_name=None
            )
            if not session_started:
                return False
            session = self._current_align_session()
        if session is None:
            return False

        star = get_align_star(star_name)
        if star is None:
            self._align_session_status("failed", f"Unknown alignment star: {star_name}")
            return False

        session["mode"] = "manual"
        self._set_current_align_star(session, star)
        if goto:
            return self._align_goto_current_star(session)

        self._align_session_status(
            "adjust",
            (
                f"Selected {star['name']}; center the target manually, "
                "then confirm the alignment point"
            ),
        )
        self._console(f"Align star\n{star['name']}")
        return True

    def confirm_multipoint_align(
        self,
        ra_deg: Any = None,
        dec_deg: Any = None,
        source: str = "ui",
    ) -> bool:
        session = self._current_align_session()
        if session is None:
            self._align_session_status("idle", "No active multi-point alignment")
            return False

        current_star = session.get("current_star")
        if ra_deg is not None and dec_deg is not None:
            try:
                ra_deg = float(ra_deg) % 360.0
                dec_deg = float(dec_deg)
            except (TypeError, ValueError):
                self._align_session_status("failed", "Invalid alignment coordinates")
                return False
            if not current_star:
                current_star = self._set_current_align_star(
                    session,
                    {
                        "name": f"SkySafari Target {session['completed_points'] + 1}",
                        "ra": ra_deg,
                        "dec": dec_deg,
                        "mag": None,
                    },
                )
            else:
                current_star = dict(current_star)
                current_star["ra"] = ra_deg
                current_star["dec"] = dec_deg
                session["current_star"] = current_star

        if not current_star:
            self._align_session_status("waiting", "Select an alignment star first")
            return False

        ra = float(current_star["ra"]) % 360.0
        dec = float(current_star["dec"])
        if not self.sync_mount(ra, dec):
            self._align_session_status(
                "failed", f"Could not confirm {current_star['name']}"
            )
            return False

        completed = list(session.get("completed", []))
        completed.append(
            {
                "name": current_star["name"],
                "ra": ra,
                "dec": dec,
                "source": source,
                "confirmed_at": time.time(),
            }
        )
        session["completed"] = completed
        session["completed_points"] = len(completed)
        session["current_star"] = None

        if len(completed) >= int(session["total_points"]):
            session["active"] = False
            self._align_session_status(
                "complete",
                f"Multi-point alignment complete: {len(completed)} points",
            )
            self._console("Multi align\ncomplete")
            return True

        if session.get("mode") == "auto":
            star = self._align_auto_star(session)
            self._set_current_align_star(session, star)
            self._align_session_status(
                "moving",
                (
                    f"Auto alignment point {len(completed) + 1}/"
                    f"{session['total_points']}: {star['name']}"
                ),
            )
            return self._align_goto_current_star(session)

        self._align_session_status(
            "waiting",
            (
                f"Alignment point {len(completed)}/{session['total_points']} saved; "
                "select the next star"
            ),
        )
        self._console(f"Align point\n{len(completed)} saved")
        return True

    def cancel_multipoint_align(self) -> bool:
        if self._multipoint_align is not None:
            self._multipoint_align["active"] = False
            self._multipoint_align["state"] = "cancelled"
            self._multipoint_align["message"] = "Multi-point alignment cancelled"
            self._multipoint_align["updated"] = time.time()
        self._write_controller_status(
            "align_cancelled", "Multi-point alignment cancelled"
        )
        self._console("Multi align\ncancelled")
        return True

    def change_slew_rate(self, delta: int) -> None:
        self.refresh_slew_rate()
        self.set_slew_rate(self.slew_rate + delta)

    def handle_command(self, command: Any) -> bool:
        if not isinstance(command, dict):
            logger.warning("Ignoring mount-control command: %r", command)
            return True

        command_type = command.get("type")
        if command_type == "shutdown":
            return False
        if command_type == "init":
            self.connect()
        elif command_type == "restart_driver":
            self.restart_driver()
        elif command_type == "sync":
            self.sync_mount(float(command["ra"]), float(command["dec"]))
        elif command_type == "goto_target":
            self.goto_target(
                float(command["ra"]),
                float(command["dec"]),
                bool(command.get("refine_after_goto", False)),
                command.get("refine_accuracy_arcmin"),
            )
        elif command_type == "toggle_guide_correction":
            self.toggle_guide_correction(
                command.get("enabled"),
                command.get("target_ra"),
                command.get("target_dec"),
                command.get("accuracy_arcmin"),
            )
        elif command_type == "stop_movement":
            self.stop_mount()
        elif command_type == "manual_movement":
            self.manual_move(
                str(command.get("direction", "")),
                command.get("lease_seconds"),
            )
        elif command_type == "manual_movement_keepalive":
            self.manual_motion_keepalive(
                str(command.get("direction", "")),
                command.get("lease_seconds"),
            )
        elif command_type == "increase_step_size":
            self.change_step(2.0)
        elif command_type == "reduce_step_size":
            self.change_step(0.5)
        elif command_type == "increase_slew_rate":
            self.change_slew_rate(1)
        elif command_type == "reduce_slew_rate":
            self.change_slew_rate(-1)
        elif command_type == "set_slew_rate":
            self.set_slew_rate(int(command.get("rate", self.slew_rate)))
        elif command_type == "refresh_slew_rate":
            self.refresh_slew_rate()
        elif command_type == "refresh_backlash":
            self.refresh_backlash()
        elif command_type == "set_backlash":
            self.set_backlash(command.get("ra"), command.get("de"))
        elif command_type == "auto_backlash":
            self.auto_calculate_backlash(
                str(command.get("axis", "")),
                str(command.get("mode", BACKLASH_AUTO_MODE_COMPASS_GOTO)),
                command.get("repeats"),
            )
        elif command_type == "backlash_compass_continue":
            self.continue_backlash_compass_goto_loop()
        elif command_type == "backlash_compass_stop":
            self.stop_backlash_auto()
        elif command_type == "multipoint_align_start":
            self.start_multipoint_align(
                str(command.get("mode", "manual")),
                command.get("points"),
                command.get("star_name"),
            )
        elif command_type == "multipoint_align_select_star":
            self.select_multipoint_align_star(
                str(command.get("star_name", "")),
                bool(command.get("goto", False)),
            )
        elif command_type == "multipoint_align_confirm":
            self.confirm_multipoint_align(
                command.get("ra"),
                command.get("dec"),
                str(command.get("source", "ui")),
            )
        elif command_type == "multipoint_align_cancel":
            self.cancel_multipoint_align()
        elif command_type == "sync_location_time":
            self.sync_location_time()
        elif command_type == "park_action":
            self.park_action(str(command.get("action", "")))
        else:
            logger.warning("Unknown mount-control command: %s", command_type)
        return True

    def run(self) -> None:
        self._write_controller_status(
            "idle",
            f"Mount-control process ready for {self.indi_host}:{self.indi_port}",
        )

        running = True
        next_auto_connect_at = time.monotonic() + AUTO_CONNECT_START_DELAY
        while running:
            self._check_manual_motion_deadline()
            self._check_goto_motion()
            self._check_pending_goto_refine()
            self._check_guide_correction()
            try:
                command = self.mount_queue.get(
                    timeout=self._manual_motion_queue_timeout()
                )
                running = self.handle_command(command)
            except queue.Empty:
                self._check_manual_motion_deadline()
                self._check_goto_motion()
                self._check_pending_goto_refine()
                self._check_guide_correction()
                now = time.monotonic()
                if not self.connected and now >= next_auto_connect_at:
                    logger.info("Attempting automatic INDI mount connection")
                    if self.connect(announce=False):
                        self._console("INDI mount\nconnected")
                    next_auto_connect_at = now + AUTO_CONNECT_RETRY_INTERVAL
                self._write_status_heartbeat()
                continue
            except Exception as exc:
                logger.exception("Mount-control command failed")
                self._write_controller_status("error", str(exc))
                self._console("INDI mount\ncommand failed")

        self.disconnect()


def run(
    mount_queue: Queue,
    console_queue: Queue,
    shared_state,
    log_queue: Queue,
    imu_command_queue: Optional[Queue] = None,
    indi_host: str = "localhost",
    indi_port: int = 7624,
) -> None:
    """Process entry point used by ``main.py``."""
    MultiprocLogging.configurer(log_queue)
    controller = MountControlIndi(
        mount_queue,
        console_queue,
        shared_state,
        imu_command_queue=imu_command_queue,
        indi_host=indi_host,
        indi_port=indi_port,
    )
    controller.run()
