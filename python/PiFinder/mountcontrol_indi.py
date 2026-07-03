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
from PiFinder.pointing_model import quaternion_transforms as qt

try:
    import PyIndi  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised only on INDI installs
    PyIndi = None


logger = logging.getLogger("MountControl.Indi")
clientlogger = logging.getLogger("MountControl.Indi.Client")

STATUS_FILE = utils.data_dir / "mount_control_status.json"
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
GOTO_COMPLETE_FALLBACK_SECONDS = 180.0
BACKLASH_MIN_VALUE = 0
BACKLASH_MAX_VALUE = 3600
BACKLASH_AXES = {"ra", "de"}
BACKLASH_AUTO_SETTLE_SECONDS = 3.0
BACKLASH_AUTO_SAMPLE_SECONDS = 0.15
BACKLASH_AUTO_SETTLE_AFTER_PULSE_SECONDS = 0.45
BACKLASH_AUTO_TRACKING_SETTLE_SECONDS = 5.0
BACKLASH_AUTO_PULSE_SECONDS = 0.35
BACKLASH_AUTO_SLEW_RATE = 5
BACKLASH_AUTO_IMU_STALE_SECONDS = 5.0
BACKLASH_AUTO_MIN_DETECT_DEG = 0.012
BACKLASH_AUTO_MAX_STABLE_NOISE_DEG = 0.05
BACKLASH_AUTO_STABILITY_RETRIES = 3
BACKLASH_AUTO_MAX_PRIME_PULSES = 20
BACKLASH_AUTO_MAX_REVERSE_PULSES = 40
BACKLASH_AUTO_GOTO_DEGREES = 5.0
BACKLASH_AUTO_GOTO_MAX_DEGREES = 20.0
BACKLASH_AUTO_GOTO_REPEATS = 3
BACKLASH_AUTO_VERIFY_REPEATS = 3
BACKLASH_AUTO_GOTO_TIMEOUT_SECONDS = 90.0
BACKLASH_AUTO_GOTO_POLL_SECONDS = 0.25
BACKLASH_AUTO_MIN_GOTO_MOTION_FRACTION = 0.5
BACKLASH_AUTO_GOTO_TARGET_TOLERANCE_DEG = 0.5
BACKLASH_AUTO_VERIFY_WARN_PERCENT = 25.0
BACKLASH_AUTO_VERIFY_WARN_ARCSEC = 120
BACKLASH_AUTO_SAFE_MIN_ALT_DEG = 20.0
BACKLASH_AUTO_SAFE_MAX_ALT_DEG = 75.0
BACKLASH_AUTO_SAFE_TARGET_ALT_DEG = 45.0
BACKLASH_AUTO_SAFE_GOTO_TIMEOUT_SECONDS = 120.0
SIDEREAL_ARCSEC_PER_SECOND = 15.041067
BACKLASH_AUTO_RATE_MULTIPLIERS = {
    1: 0.5,
    2: 1.0,
    3: 2.0,
    4: 4.0,
    5: 8.0,
    6: 20.0,
    7: 48.0,
}


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
        tmp_status = STATUS_FILE.with_name(f"{STATUS_FILE.name}.tmp")
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
        indi_host: str = "localhost",
        indi_port: int = 7624,
    ):
        self.mount_queue = mount_queue
        self.console_queue = console_queue
        self.shared_state = shared_state
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

    def _shared_location_time_values(self):
        latitude = longitude = elevation = None
        try:
            location = self.shared_state.location()
        except Exception:
            location = None

        if location and location.lock:
            latitude = float(location.lat)
            longitude = float(location.lon)
            elevation = None if location.altitude is None else float(location.altitude)

        try:
            dt = self.shared_state.datetime()
        except Exception:
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
        if is_busy is True:
            return
        if is_busy is False:
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

    def _read_tracking_enabled(self) -> Optional[bool]:
        for attempt in range(3):
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

            tracking_text = self._device_text_value("OnStep Status", "Tracking")
            tracking_lower = tracking_text.strip().lower()
            if tracking_lower in {"on", "tracking", "active"}:
                return True
            if tracking_lower in {"off", "not tracking", "inactive"}:
                return False

            if attempt < 2:
                time.sleep(0.2)
        return None

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
            self._console("INDI tracking\nfailed")
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

    def _backlash_axis_config(self, axis: str) -> dict[str, str]:
        if axis == "ra":
            return {
                "axis": "RA",
                "value_key": "backlash_ra",
                "forward": "east",
                "reverse": "west",
                "coord": "ra",
            }
        return {
            "axis": "DE",
            "value_key": "backlash_de",
            "forward": "north",
            "reverse": "south",
            "coord": "dec",
        }

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

    def _wait_for_imu_sample(self, timeout: float = 3.0) -> Any:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            imu = self._current_imu_sample()
            if imu is not None:
                return self._copy_imu_quat(getattr(imu, "quat", None))
            time.sleep(BACKLASH_AUTO_SAMPLE_SECONDS)
        return None

    def _imu_angle_diff_deg(self, reference_quat: Any) -> Optional[float]:
        imu = self._current_imu_sample()
        if imu is None:
            return None
        try:
            current_quat = self._copy_imu_quat(imu.quat)
            return math.degrees(qt.get_quat_angular_diff(reference_quat, current_quat))
        except Exception:
            logger.exception("Could not compare IMU quaternions")
            return None

    def _screen_direction(self) -> str:
        try:
            return str(config.Config().get_option("screen_direction", "right"))
        except Exception:
            logger.warning("Could not read screen direction; using right", exc_info=True)
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

            boresight = (
                q_x2cam * quaternion.quaternion(0, 0, 0, 1) * q_x2cam.conj()
            )
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

    def _backlash_altitude_is_safe(self, altitude_deg: float) -> bool:
        return (
            BACKLASH_AUTO_SAFE_MIN_ALT_DEG
            <= altitude_deg
            <= BACKLASH_AUTO_SAFE_MAX_ALT_DEG
        )

    def _safe_backlash_target_radec(
        self, current_az_deg: float
    ) -> Optional[tuple[float, float]]:
        latitude, longitude, elevation, dt = self._shared_location_time_values()
        if latitude is None or longitude is None or dt is None:
            self._backlash_auto_status(
                "failed",
                (
                    "A locked PiFinder location and current time are required "
                    "before moving to a safe backlash test position"
                ),
                phase="safe_position",
            )
            return None

        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except ValueError:
                self._backlash_auto_status(
                    "failed",
                    "Could not parse PiFinder time for safe backlash positioning",
                    phase="safe_position",
                )
                return None
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)

        try:
            calc_utils.sf_utils.set_location(latitude, longitude, elevation or 0.0)
            return calc_utils.sf_utils.altaz_to_radec(
                BACKLASH_AUTO_SAFE_TARGET_ALT_DEG,
                current_az_deg % 360.0,
                dt,
            )
        except Exception:
            logger.exception("Could not calculate safe backlash target")
            self._backlash_auto_status(
                "failed",
                "Could not calculate a safe backlash test target from IMU/location/time",
                phase="safe_position",
            )
            return None

    def _ensure_backlash_safe_position(self) -> bool:
        altaz = self._current_imu_altaz()
        if altaz is None:
            self._backlash_auto_status(
                "failed",
                (
                    "Could not read a calibrated IMU altitude for safe backlash "
                    "positioning"
                ),
                phase="safe_position",
            )
            return False

        altitude_deg, azimuth_deg = altaz
        if self._backlash_altitude_is_safe(altitude_deg):
            self._backlash_auto_status(
                "running",
                f"IMU altitude {altitude_deg:.1f} deg is safe for backlash test",
                phase="safe_position",
                imu_altitude_deg=altitude_deg,
                imu_azimuth_deg=azimuth_deg,
                safe_min_alt_deg=BACKLASH_AUTO_SAFE_MIN_ALT_DEG,
                safe_max_alt_deg=BACKLASH_AUTO_SAFE_MAX_ALT_DEG,
            )
            return True

        self._backlash_auto_status(
            "running",
            (
                f"IMU altitude {altitude_deg:.1f} deg is outside the safe "
                "backlash range; moving to a safe test altitude"
            ),
            phase="safe_position",
            imu_altitude_deg=altitude_deg,
            imu_azimuth_deg=azimuth_deg,
            safe_min_alt_deg=BACKLASH_AUTO_SAFE_MIN_ALT_DEG,
            safe_max_alt_deg=BACKLASH_AUTO_SAFE_MAX_ALT_DEG,
            safe_target_alt_deg=BACKLASH_AUTO_SAFE_TARGET_ALT_DEG,
        )

        target = self._safe_backlash_target_radec(azimuth_deg)
        if target is None:
            return False

        target_ra, target_dec = target
        if not self._goto_target_and_wait(
            target_ra,
            target_dec,
            "Safe backlash test position",
            timeout=BACKLASH_AUTO_SAFE_GOTO_TIMEOUT_SECONDS,
        ):
            self.stop_mount()
            self._backlash_auto_status(
                "failed",
                (
                    "Could not move to a safe backlash test position. "
                    "OnStep may have blocked the GoTo because of mount limits."
                ),
                phase="safe_position",
                target_ra=target_ra % 360.0,
                target_dec=target_dec,
            )
            return False

        settle = self._wait_for_backlash_baseline("safe position")
        if settle is None:
            return False

        altaz = self._current_imu_altaz()
        if altaz is None:
            self._backlash_auto_status(
                "failed",
                "Safe GoTo completed, but IMU altitude could not be re-read",
                phase="safe_position",
            )
            return False

        altitude_deg, azimuth_deg = altaz
        if not self._backlash_altitude_is_safe(altitude_deg):
            self.stop_mount()
            self._backlash_auto_status(
                "failed",
                (
                    f"Safe GoTo completed, but IMU altitude is still "
                    f"{altitude_deg:.1f} deg. Stop and check mount limits/"
                    "orientation before retrying."
                ),
                phase="safe_position",
                imu_altitude_deg=altitude_deg,
                imu_azimuth_deg=azimuth_deg,
                target_ra=target_ra % 360.0,
                target_dec=target_dec,
            )
            return False

        self._backlash_auto_status(
            "running",
            f"Safe backlash test altitude confirmed at {altitude_deg:.1f} deg",
            phase="safe_position",
            imu_altitude_deg=altitude_deg,
            imu_azimuth_deg=azimuth_deg,
            target_ra=target_ra % 360.0,
            target_dec=target_dec,
        )
        return True

    def _wait_for_imu_stable(
        self, seconds: float = BACKLASH_AUTO_SETTLE_SECONDS
    ) -> Optional[dict[str, Any]]:
        baseline = self._wait_for_imu_sample(timeout=seconds)
        if baseline is None:
            return None

        readings: list[float] = [0.0]
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            angle = self._imu_angle_diff_deg(baseline)
            if angle is not None:
                readings.append(angle)
            time.sleep(BACKLASH_AUTO_SAMPLE_SECONDS)

        spread = max(readings) - min(readings)
        threshold = max(BACKLASH_AUTO_MIN_DETECT_DEG, spread * 4.0)
        return {
            "quat": baseline,
            "spread_deg": spread,
            "threshold_deg": threshold,
            "samples": len(readings),
        }

    def _wait_for_backlash_baseline(self, phase_label: str) -> Optional[dict[str, Any]]:
        last_settle = None
        for attempt in range(1, BACKLASH_AUTO_STABILITY_RETRIES + 1):
            self._backlash_auto_status(
                "running",
                (
                    f"{phase_label}: waiting for IMU stability "
                    f"{attempt}/{BACKLASH_AUTO_STABILITY_RETRIES}"
                ),
                phase=phase_label,
                stability_attempt=attempt,
            )
            settle = self._wait_for_imu_stable()
            if settle is None:
                return None
            last_settle = settle
            if settle["spread_deg"] <= BACKLASH_AUTO_MAX_STABLE_NOISE_DEG:
                return settle
            self._backlash_auto_status(
                "running",
                (
                    f"{phase_label}: IMU still settling, noise "
                    f"{settle['spread_deg']:.3f} deg"
                ),
                phase=phase_label,
                stability_attempt=attempt,
                imu_noise_deg=settle["spread_deg"],
                max_stable_noise_deg=BACKLASH_AUTO_MAX_STABLE_NOISE_DEG,
            )
            time.sleep(BACKLASH_AUTO_TRACKING_SETTLE_SECONDS)

        if last_settle is not None:
            self._backlash_auto_status(
                "failed",
                (
                    f"{phase_label}: IMU baseline noise "
                    f"{last_settle['spread_deg']:.3f} deg is too high; "
                    "wait for the mount/IMU to settle and retry."
                ),
                phase=phase_label,
                imu_noise_deg=last_settle["spread_deg"],
                max_stable_noise_deg=BACKLASH_AUTO_MAX_STABLE_NOISE_DEG,
            )
        return None

    def _backlash_pulse(self, direction: str, pulse_seconds: float) -> bool:
        if not self.manual_move(direction, lease_seconds=pulse_seconds):
            return False
        try:
            time.sleep(pulse_seconds)
        finally:
            self.stop_mount()
            time.sleep(BACKLASH_AUTO_SETTLE_AFTER_PULSE_SECONDS)
        return True

    def _pulse_until_imu_motion(
        self,
        direction: str,
        baseline_quat: Any,
        max_pulses: int,
        threshold_deg: float,
        phase_label: str,
    ) -> tuple[Optional[int], float]:
        last_angle = 0.0
        for pulse_count in range(1, max_pulses + 1):
            self._backlash_auto_status(
                "running",
                f"{phase_label}: pulse {pulse_count}/{max_pulses}",
                pulse_count=pulse_count,
                phase=phase_label,
            )
            if not self._backlash_pulse(direction, BACKLASH_AUTO_PULSE_SECONDS):
                return None, last_angle
            angle = self._imu_angle_diff_deg(baseline_quat)
            if angle is not None:
                last_angle = angle
            self._backlash_auto_status(
                "running",
                (
                    f"{phase_label}: pulse {pulse_count}/{max_pulses}, "
                    f"IMU delta {last_angle:.3f} deg"
                ),
                pulse_count=pulse_count,
                phase=phase_label,
                angle_delta_deg=last_angle,
                threshold_deg=threshold_deg,
            )
            if last_angle >= threshold_deg:
                return pulse_count, last_angle
        return None, last_angle

    def _estimate_backlash_arcsec(self, pulse_count: int, slew_rate: int) -> int:
        multiplier = BACKLASH_AUTO_RATE_MULTIPLIERS.get(slew_rate)
        if multiplier is None:
            return BACKLASH_MIN_VALUE
        estimated = math.ceil(
            pulse_count
            * BACKLASH_AUTO_PULSE_SECONDS
            * SIDEREAL_ARCSEC_PER_SECOND
            * multiplier
        )
        return max(BACKLASH_MIN_VALUE, min(BACKLASH_MAX_VALUE, estimated))

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

    def _axis_goto_target(
        self, axis: str, start_ra: float, start_dec: float, move_degrees: float
    ) -> tuple[float, float]:
        if axis == "ra":
            dec_cos = max(0.25, abs(math.cos(math.radians(start_dec))))
            return (start_ra + move_degrees / dec_cos) % 360.0, start_dec

        direction = 1.0 if move_degrees >= 0 else -1.0
        if start_dec + move_degrees > 85.0:
            direction = -1.0
        elif start_dec + move_degrees < -85.0:
            direction = 1.0
        signed_degrees = abs(move_degrees) * direction
        return start_ra % 360.0, max(
            -85.0, min(85.0, start_dec + signed_degrees)
        )

    def _axis_goto_plan(
        self,
        axis: str,
        start_ra: float,
        start_dec: float,
        move_degrees: float,
        mount_model: str,
    ) -> dict[str, float]:
        if mount_model == "eq":
            if axis == "ra":
                target_ra = (start_ra + move_degrees) % 360.0
                signed_move = shortest_ra_delta_deg(target_ra, start_ra)
                return {
                    "target_ra": target_ra,
                    "target_dec": start_dec,
                    "commanded_degrees": abs(signed_move),
                    "signed_move_degrees": signed_move,
                }

            target_ra, target_dec = self._axis_goto_target(
                "dec", start_ra, start_dec, move_degrees
            )
            return {
                "target_ra": target_ra,
                "target_dec": target_dec,
                "commanded_degrees": abs(target_dec - start_dec),
                "signed_move_degrees": target_dec - start_dec,
            }

        target_ra, target_dec = self._axis_goto_target(
            axis, start_ra, start_dec, move_degrees
        )
        commanded_degrees = (
            radec_separation_arcmin(start_ra, start_dec, target_ra, target_dec) / 60.0
        )
        if axis == "ra":
            signed_move = shortest_ra_delta_deg(target_ra, start_ra) * max(
                0.25, abs(math.cos(math.radians(start_dec)))
            )
        else:
            signed_move = target_dec - start_dec
        return {
            "target_ra": target_ra,
            "target_dec": target_dec,
            "commanded_degrees": commanded_degrees,
            "signed_move_degrees": signed_move,
        }

    def _axis_position_delta_degrees(
        self,
        axis: str,
        start_ra: float,
        start_dec: float,
        end_ra: float,
        end_dec: float,
        mount_model: str,
    ) -> float:
        if mount_model == "eq":
            if axis == "ra":
                return abs(shortest_ra_delta_deg(end_ra, start_ra))
            return abs(end_dec - start_dec)
        return radec_separation_arcmin(start_ra, start_dec, end_ra, end_dec) / 60.0

    def _goto_target_and_wait(
        self,
        ra_deg: float,
        dec_deg: float,
        phase_label: str,
        timeout: float = BACKLASH_AUTO_GOTO_TIMEOUT_SECONDS,
    ) -> bool:
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
        while time.monotonic() - start < timeout:
            elapsed = time.monotonic() - start
            if elapsed >= GOTO_COMPLETE_MIN_SECONDS:
                busy = self._indi_mount_is_busy()
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

    def _measure_backlash_goto_roundtrip(
        self,
        cfg: dict[str, str],
        repeat_index: int,
        move_degrees: float,
        mount_model: Optional[str] = None,
        phase_prefix: str = "",
    ) -> Optional[dict[str, Any]]:
        if mount_model is None:
            mount_model = self._backlash_mount_model()
        current_position = self._read_current_position()
        if current_position is None:
            self._backlash_auto_status(
                "failed",
                "Could not read current mount position before GoTo backlash test",
            )
            return None

        start_ra, start_dec = current_position
        forward_plan = self._axis_goto_plan(
            cfg["coord"], start_ra, start_dec, move_degrees, mount_model
        )
        target_ra = forward_plan["target_ra"]
        target_dec = forward_plan["target_dec"]
        commanded_degrees = forward_plan["commanded_degrees"]
        if commanded_degrees <= 0:
            self._backlash_auto_status(
                "failed", "Computed GoTo movement is too small for backlash test"
            )
            return None

        phase_name = f"{phase_prefix}{cfg['axis']} repeat {repeat_index}"
        start_settle = self._wait_for_backlash_baseline(
            f"{phase_name} start"
        )
        if start_settle is None:
            return None

        if not self._goto_target_and_wait(
            target_ra,
            target_dec,
            f"{phase_name} outward",
        ):
            return None

        outward_settle = self._wait_for_backlash_baseline(
            f"{phase_name} outward baseline"
        )
        if outward_settle is None:
            return None

        outward_angle = self._imu_angle_diff_deg(start_settle["quat"])
        if outward_angle is None:
            self._backlash_auto_status(
                "failed",
                f"{phase_name}: could not read outward IMU motion",
            )
            return None

        outward_position = self._read_current_position()
        if outward_position is None:
            self._backlash_auto_status(
                "failed",
                f"{phase_name}: could not read current position after outward GoTo",
            )
            return None
        outward_ra, outward_dec = outward_position
        actual_forward_degrees = self._axis_position_delta_degrees(
            cfg["coord"],
            start_ra,
            start_dec,
            outward_ra,
            outward_dec,
            mount_model,
        )
        if actual_forward_degrees <= 0:
            self._backlash_auto_status(
                "failed",
                f"{phase_name}: actual outward coordinate motion is too small",
            )
            return None
        min_motion = actual_forward_degrees * BACKLASH_AUTO_MIN_GOTO_MOTION_FRACTION
        if outward_angle < min_motion:
            measurement_noise = max(
                float(start_settle["spread_deg"]), float(outward_settle["spread_deg"])
            )
            self._backlash_auto_status(
                "running",
                (
                    f"{phase_name}: outward IMU motion "
                    f"{outward_angle:.3f} deg is below actual coordinate motion "
                    f"{min_motion:.3f} deg"
                ),
                phase=phase_name,
                commanded_degrees=actual_forward_degrees,
                planned_commanded_degrees=commanded_degrees,
                outward_angle_deg=outward_angle,
                min_motion_deg=min_motion,
                imu_noise_deg=measurement_noise,
            )
            return {
                "valid": False,
                "reason": "outward_motion_too_small",
                "mount_model": mount_model,
                "move_degrees": move_degrees,
                "signed_move_degrees": forward_plan["signed_move_degrees"],
                "commanded_degrees": actual_forward_degrees,
                "planned_commanded_degrees": commanded_degrees,
                "forward_commanded_degrees": actual_forward_degrees,
                "planned_forward_commanded_degrees": commanded_degrees,
                "outward_angle_deg": outward_angle,
                "start_ra": start_ra,
                "start_dec": start_dec,
                "outward_ra": outward_ra,
                "outward_dec": outward_dec,
                "start_noise_deg": start_settle["spread_deg"],
                "outward_noise_deg": outward_settle["spread_deg"],
                "imu_noise_deg": measurement_noise,
            }

        reverse_plan = self._axis_goto_plan(
            cfg["coord"],
            outward_ra,
            outward_dec,
            -float(forward_plan["signed_move_degrees"]),
            mount_model,
        )
        reverse_commanded_degrees = reverse_plan["commanded_degrees"]
        if reverse_commanded_degrees <= 0:
            self._backlash_auto_status(
                "failed", "Computed reverse GoTo movement is too small for backlash test"
            )
            return None

        if not self._goto_target_and_wait(
            reverse_plan["target_ra"],
            reverse_plan["target_dec"],
            f"{phase_name} return",
        ):
            return None

        return_settle = self._wait_for_backlash_baseline(
            f"{phase_name} return baseline"
        )
        if return_settle is None:
            return None

        reverse_angle = self._imu_angle_diff_deg(outward_settle["quat"])
        if reverse_angle is None:
            self._backlash_auto_status(
                "failed",
                f"{phase_name}: could not read return IMU motion",
            )
            return None

        return_position = self._read_current_position()
        if return_position is None:
            self._backlash_auto_status(
                "failed",
                f"{phase_name}: could not read current position after return GoTo",
            )
            return None
        return_ra, return_dec = return_position
        actual_reverse_degrees = self._axis_position_delta_degrees(
            cfg["coord"],
            outward_ra,
            outward_dec,
            return_ra,
            return_dec,
            mount_model,
        )
        if actual_reverse_degrees <= 0:
            self._backlash_auto_status(
                "failed",
                f"{phase_name}: actual return coordinate motion is too small",
            )
            return None

        reverse_min_motion = actual_reverse_degrees * BACKLASH_AUTO_MIN_GOTO_MOTION_FRACTION
        if reverse_angle < reverse_min_motion:
            measurement_noise = max(
                float(start_settle["spread_deg"]),
                float(outward_settle["spread_deg"]),
                float(return_settle["spread_deg"]),
            )
            self._backlash_auto_status(
                "running",
                (
                    f"{phase_name}: return IMU motion "
                    f"{reverse_angle:.3f} deg is below expected "
                    f"{reverse_min_motion:.3f} deg"
                ),
                phase=phase_name,
                commanded_degrees=actual_reverse_degrees,
                planned_commanded_degrees=reverse_commanded_degrees,
                forward_commanded_degrees=actual_forward_degrees,
                planned_forward_commanded_degrees=commanded_degrees,
                reverse_commanded_degrees=actual_reverse_degrees,
                planned_reverse_commanded_degrees=reverse_commanded_degrees,
                reverse_angle_deg=reverse_angle,
                min_motion_deg=reverse_min_motion,
                imu_noise_deg=measurement_noise,
            )
            return {
                "valid": False,
                "reason": "return_motion_too_small",
                "mount_model": mount_model,
                "move_degrees": move_degrees,
                "signed_move_degrees": forward_plan["signed_move_degrees"],
                "commanded_degrees": actual_reverse_degrees,
                "planned_commanded_degrees": reverse_commanded_degrees,
                "forward_commanded_degrees": actual_forward_degrees,
                "planned_forward_commanded_degrees": commanded_degrees,
                "reverse_commanded_degrees": actual_reverse_degrees,
                "planned_reverse_commanded_degrees": reverse_commanded_degrees,
                "outward_angle_deg": outward_angle,
                "reverse_angle_deg": reverse_angle,
                "start_ra": start_ra,
                "start_dec": start_dec,
                "outward_ra": outward_ra,
                "outward_dec": outward_dec,
                "return_ra": return_ra,
                "return_dec": return_dec,
                "start_noise_deg": start_settle["spread_deg"],
                "outward_noise_deg": outward_settle["spread_deg"],
                "return_noise_deg": return_settle["spread_deg"],
                "imu_noise_deg": measurement_noise,
            }

        measurement_noise = max(
            float(start_settle["spread_deg"]),
            float(outward_settle["spread_deg"]),
            float(return_settle["spread_deg"]),
        )
        error_degrees = max(0.0, actual_reverse_degrees - reverse_angle)
        estimated_arcsec = max(
            BACKLASH_MIN_VALUE,
            min(BACKLASH_MAX_VALUE, int(round(error_degrees * 3600.0))),
        )
        if (
            estimated_arcsec >= BACKLASH_MAX_VALUE
            and move_degrees < BACKLASH_AUTO_GOTO_MAX_DEGREES
        ):
            self._backlash_auto_status(
                "running",
                (
                    f"{phase_name}: estimate hit "
                    f"{BACKLASH_MAX_VALUE} arc-sec at {move_degrees:.1f} deg, "
                    "retrying with a larger GoTo angle"
                ),
                phase=phase_name,
                commanded_degrees=actual_reverse_degrees,
                planned_commanded_degrees=reverse_commanded_degrees,
                forward_commanded_degrees=actual_forward_degrees,
                planned_forward_commanded_degrees=commanded_degrees,
                reverse_commanded_degrees=actual_reverse_degrees,
                planned_reverse_commanded_degrees=reverse_commanded_degrees,
                outward_angle_deg=outward_angle,
                reverse_angle_deg=reverse_angle,
                estimated_arcsec=estimated_arcsec,
                move_degrees=move_degrees,
                imu_noise_deg=measurement_noise,
            )
            return {
                "valid": False,
                "reason": "estimate_saturated",
                "mount_model": mount_model,
                "move_degrees": move_degrees,
                "signed_move_degrees": forward_plan["signed_move_degrees"],
                "commanded_degrees": actual_reverse_degrees,
                "planned_commanded_degrees": reverse_commanded_degrees,
                "forward_commanded_degrees": actual_forward_degrees,
                "planned_forward_commanded_degrees": commanded_degrees,
                "reverse_commanded_degrees": actual_reverse_degrees,
                "planned_reverse_commanded_degrees": reverse_commanded_degrees,
                "outward_angle_deg": outward_angle,
                "reverse_angle_deg": reverse_angle,
                "estimated_arcsec": estimated_arcsec,
                "start_ra": start_ra,
                "start_dec": start_dec,
                "outward_ra": outward_ra,
                "outward_dec": outward_dec,
                "return_ra": return_ra,
                "return_dec": return_dec,
                "start_noise_deg": start_settle["spread_deg"],
                "outward_noise_deg": outward_settle["spread_deg"],
                "return_noise_deg": return_settle["spread_deg"],
                "imu_noise_deg": measurement_noise,
            }
        return {
            "valid": True,
            "mount_model": mount_model,
            "move_degrees": move_degrees,
            "signed_move_degrees": forward_plan["signed_move_degrees"],
            "commanded_degrees": actual_reverse_degrees,
            "planned_commanded_degrees": reverse_commanded_degrees,
            "forward_commanded_degrees": actual_forward_degrees,
            "planned_forward_commanded_degrees": commanded_degrees,
            "reverse_commanded_degrees": actual_reverse_degrees,
            "planned_reverse_commanded_degrees": reverse_commanded_degrees,
            "outward_angle_deg": outward_angle,
            "reverse_angle_deg": reverse_angle,
            "error_degrees": error_degrees,
            "estimated_arcsec": estimated_arcsec,
            "start_ra": start_ra,
            "start_dec": start_dec,
            "outward_ra": outward_ra,
            "outward_dec": outward_dec,
            "return_ra": return_ra,
            "return_dec": return_dec,
            "start_noise_deg": start_settle["spread_deg"],
            "outward_noise_deg": outward_settle["spread_deg"],
            "return_noise_deg": return_settle["spread_deg"],
            "imu_noise_deg": measurement_noise,
        }

    def _verification_error_rate_percent(
        self, residual_arcsec: int, estimated_value: int
    ) -> Optional[float]:
        if estimated_value <= 0:
            return 0.0 if residual_arcsec <= 0 else None
        return round((residual_arcsec / estimated_value) * 100.0, 1)

    def _run_backlash_verification(
        self,
        cfg: dict[str, str],
        estimated_value: int,
        original_ra: int,
        original_de: int,
        move_degrees: float,
        mount_model: str,
    ) -> dict[str, Any]:
        verify_ra = original_ra
        verify_de = original_de
        if cfg["value_key"] == "backlash_ra":
            verify_ra = estimated_value
        else:
            verify_de = estimated_value

        self._backlash_auto_status(
            "running",
            (
                f"Applying estimated {cfg['axis']} backlash temporarily "
                "for verification"
            ),
            phase=f"{cfg['axis']} verification",
            verification_ra=verify_ra,
            verification_de=verify_de,
            verification_repeats=BACKLASH_AUTO_VERIFY_REPEATS,
        )
        if not self._apply_backlash_values(verify_ra, verify_de):
            return {
                "ok": False,
                "message": "Could not apply estimated backlash for verification",
                "measurements": [],
                "invalid_measurements": [],
            }

        verification_measurements: list[dict[str, Any]] = []
        invalid_measurements: list[dict[str, Any]] = []
        for repeat_index in range(1, BACKLASH_AUTO_VERIFY_REPEATS + 1):
            self._backlash_auto_status(
                "running",
                (
                    f"{cfg['axis']} verification "
                    f"{repeat_index}/{BACKLASH_AUTO_VERIFY_REPEATS} "
                    f"using {move_degrees:.1f} deg"
                ),
                phase=f"{cfg['axis']} verification",
                repeat_index=repeat_index,
                move_degrees=move_degrees,
                valid_measurements=len(verification_measurements),
            )
            measurement = self._measure_backlash_goto_roundtrip(
                cfg,
                repeat_index,
                move_degrees,
                mount_model=mount_model,
                phase_prefix="verification ",
            )
            if measurement is None:
                return {
                    "ok": False,
                    "message": "Verification stopped before completing a round-trip",
                    "measurements": verification_measurements,
                    "invalid_measurements": invalid_measurements,
                }
            if not measurement.get("valid"):
                invalid_measurements.append(measurement)
                continue
            verification_measurements.append(measurement)

        if len(verification_measurements) < BACKLASH_AUTO_VERIFY_REPEATS:
            return {
                "ok": False,
                "message": (
                    f"Verification produced only {len(verification_measurements)}/"
                    f"{BACKLASH_AUTO_VERIFY_REPEATS} reliable round-trips"
                ),
                "measurements": verification_measurements,
                "invalid_measurements": invalid_measurements,
            }

        average_residual_arcsec = int(
            round(
                sum(m["estimated_arcsec"] for m in verification_measurements)
                / len(verification_measurements)
            )
        )
        error_rate = self._verification_error_rate_percent(
            average_residual_arcsec, estimated_value
        )
        warn = average_residual_arcsec > BACKLASH_AUTO_VERIFY_WARN_ARCSEC
        if error_rate is not None:
            warn = warn or error_rate > BACKLASH_AUTO_VERIFY_WARN_PERCENT
        return {
            "ok": True,
            "message": (
                f"Verification residual average {average_residual_arcsec} arc-sec"
            ),
            "measurements": verification_measurements,
            "invalid_measurements": invalid_measurements,
            "average_residual_arcsec": average_residual_arcsec,
            "error_rate_percent": error_rate,
            "warning": warn,
        }

    def auto_calculate_backlash(self, axis: str) -> bool:
        axis = axis.lower()
        if axis not in BACKLASH_AXES:
            logger.warning("Unknown backlash axis: %s", axis)
            return False

        cfg = self._backlash_axis_config(axis)
        mount_model = self._backlash_mount_model()
        self._backlash_auto = {
            "axis": cfg["axis"],
            "state": "running",
            "message": f"Auto backlash calculation started for {cfg['axis']}",
            "steps": [
                "Disable tracking during measurement",
                "Confirm the IMU altitude is safe; move to a safe test position if needed",
                "Reset selected backlash to 0",
                "Move far enough with coordinate GoTo to load the axis",
                "Return with coordinate GoTo and compare commanded vs IMU motion",
                "Repeat and average stable measurements",
                "Temporarily apply the measured backlash and verify with 3 more round-trips",
                "Restore original Backlash, slew rate, and tracking",
                "Copy calculated value into the input field only",
            ],
            "started_at": time.time(),
            "method": "coordinate_goto_roundtrip",
            "mount_model": mount_model,
            "method_note": (
                "Each reverse GoTo starts from the actual completed position; "
                "coordinate GoTo may move both physical axes during DEC or RA tests."
            ),
            "move_degrees": BACKLASH_AUTO_GOTO_DEGREES,
            "max_move_degrees": BACKLASH_AUTO_GOTO_MAX_DEGREES,
            "repeats": BACKLASH_AUTO_GOTO_REPEATS,
            "verification_repeats": BACKLASH_AUTO_VERIFY_REPEATS,
            "slew_rate": BACKLASH_AUTO_SLEW_RATE,
        }
        self._write_controller_status(
            "backlash_auto_running",
            f"Backlash auto calculation started for {cfg['axis']}",
        )
        self._console(f"Backlash {cfg['axis']}\nauto start")

        original_ra: Optional[int] = None
        original_de: Optional[int] = None
        original_slew: Optional[int] = None
        original_tracking: Optional[bool] = None
        restored = False

        try:
            imu_sample = self._current_imu_sample()
            if imu_sample is None:
                self._backlash_auto_status(
                    "failed",
                    "No fresh IMU sample is available for auto backlash calculation",
                )
                self._console("Backlash auto\nno IMU")
                return False
            if getattr(imu_sample, "uses_magnetometer", False):
                self._backlash_auto_status(
                    "failed",
                    (
                        "IMU compass/NDOF mode is active. Set Settings > IMU "
                        "Settings > Compass to Off, restart PiFinder, then retry "
                        "auto backlash."
                    ),
                    fusion_mode=getattr(imu_sample, "fusion_mode", "unknown"),
                    calibration_status=getattr(imu_sample, "calibration_status", None),
                )
                self._console("Backlash auto\nCompass off")
                return False
            if self._wait_for_imu_sample(timeout=3.0) is None:
                self._backlash_auto_status(
                    "failed",
                    "No fresh IMU orientation is available for auto backlash calculation",
                )
                self._console("Backlash auto\nno IMU")
                return False

            current_ra, current_de = self.refresh_backlash()
            original_ra = (
                current_ra if current_ra is not None else self.backlash_ra or 0
            )
            original_de = (
                current_de if current_de is not None else self.backlash_de or 0
            )
            original_slew = self._read_driver_slew_rate()
            if original_slew is None:
                self._backlash_auto_status(
                    "failed",
                    "Could not read the current driver slew rate; auto test was not started",
                )
                self._console("Backlash auto\nno speed")
                return False
            self.slew_rate = original_slew

            original_tracking = self._read_tracking_enabled()
            if original_tracking is None:
                self._backlash_auto_status(
                    "failed",
                    "Could not read the current tracking state; auto test was not started",
                )
                self._console("Backlash auto\nno tracking")
                return False
            if original_tracking and not self.set_tracking(False):
                self._backlash_auto_status(
                    "failed", "Could not disable tracking before backlash test"
                )
                return False
            if original_tracking:
                self._backlash_auto_status(
                    "running",
                    (
                        "Tracking disabled; waiting for mount and IMU to settle "
                        f"for {BACKLASH_AUTO_TRACKING_SETTLE_SECONDS:.0f} seconds"
                    ),
                )
                time.sleep(BACKLASH_AUTO_TRACKING_SETTLE_SECONDS)

            if not self.set_slew_rate(BACKLASH_AUTO_SLEW_RATE):
                self._backlash_auto_status(
                    "failed", "Could not set slow slew rate for backlash test"
                )
                return False

            if not self._ensure_backlash_safe_position():
                self._console("Backlash auto\nunsafe")
                return False

            test_ra = original_ra
            test_de = original_de
            if axis == "ra":
                test_ra = 0
            else:
                test_de = 0
            if not self._apply_backlash_values(test_ra, test_de):
                self._backlash_auto_status(
                    "failed", "Could not reset selected backlash to 0"
                )
                self._console("Backlash auto\nreset failed")
                return False

            measurements: list[dict[str, Any]] = []
            invalid_measurements: list[dict[str, Any]] = []
            move_degrees = BACKLASH_AUTO_GOTO_DEGREES
            repeat_index = 1
            while (
                repeat_index <= BACKLASH_AUTO_GOTO_REPEATS
                and move_degrees <= BACKLASH_AUTO_GOTO_MAX_DEGREES
            ):
                self._backlash_auto_status(
                    "running",
                    (
                        f"{cfg['axis']} GoTo round-trip "
                        f"{repeat_index}/{BACKLASH_AUTO_GOTO_REPEATS} "
                        f"using {move_degrees:.1f} deg"
                    ),
                    phase=f"{cfg['axis']} GoTo round-trip",
                    repeat_index=repeat_index,
                    move_degrees=move_degrees,
                    valid_measurements=len(measurements),
                )
                measurement = self._measure_backlash_goto_roundtrip(
                    cfg, repeat_index, move_degrees, mount_model=mount_model
                )
                if measurement is None:
                    return False
                if not measurement.get("valid"):
                    invalid_measurements.append(measurement)
                    move_degrees *= 2.0
                    continue
                measurements.append(measurement)
                self._backlash_auto_status(
                    "running",
                    (
                        f"{cfg['axis']} GoTo round-trip "
                        f"{repeat_index}/{BACKLASH_AUTO_GOTO_REPEATS} measured"
                    ),
                    phase=f"{cfg['axis']} GoTo round-trip",
                    repeat_index=repeat_index,
                    move_degrees=move_degrees,
                    valid_measurements=len(measurements),
                    last_measurement=measurement,
                    imu_noise_deg=measurement.get("imu_noise_deg"),
                )
                repeat_index += 1

            if len(measurements) < BACKLASH_AUTO_GOTO_REPEATS:
                self._backlash_auto_status(
                    "failed",
                    (
                        f"{cfg['axis']} GoTo round-trip did not produce reliable "
                        f"IMU motion for all repeats "
                        f"({len(measurements)}/{BACKLASH_AUTO_GOTO_REPEATS}). "
                        "Increase movement distance or inspect IMU mounting/"
                        "calibration before applying backlash."
                    ),
                    measurements=measurements,
                    invalid_measurements=invalid_measurements,
                )
                return False

            estimated_value = int(
                round(
                    sum(m["estimated_arcsec"] for m in measurements) / len(measurements)
                )
            )
            estimated_value = max(
                BACKLASH_MIN_VALUE, min(BACKLASH_MAX_VALUE, estimated_value)
            )
            saturated = any(
                int(m.get("estimated_arcsec", 0)) >= BACKLASH_MAX_VALUE
                for m in measurements
            )
            verification_move_degrees = max(
                float(m.get("move_degrees", BACKLASH_AUTO_GOTO_DEGREES))
                for m in measurements
            )
            verification = self._run_backlash_verification(
                cfg,
                estimated_value,
                original_ra,
                original_de,
                verification_move_degrees,
                mount_model,
            )
            if not self._apply_backlash_values(original_ra, original_de):
                self._backlash_auto_status(
                    "failed",
                    (
                        "Backlash was measured and verified, but restoring the "
                        "original driver values failed"
                    ),
                )
                return False
            restored = True
            if original_slew is not None:
                self.set_slew_rate(original_slew)

            verification_residual = verification.get("average_residual_arcsec")
            verification_error_rate = verification.get("error_rate_percent")
            completion_message = (
                f"{cfg['axis']} backlash estimated as {estimated_value} arc-sec. "
                "Original driver value restored; press Save Backlash to apply."
            )
            if verification.get("ok") and verification_residual is not None:
                completion_message = (
                    f"{cfg['axis']} backlash estimated as {estimated_value} arc-sec; "
                    f"verification residual average {verification_residual} arc-sec"
                )
                if verification_error_rate is not None:
                    completion_message += (
                        f" ({verification_error_rate:.1f}% of estimate)"
                    )
                completion_message += (
                    ". Original driver value restored; press Save Backlash to apply."
                )
            elif not verification.get("ok"):
                completion_message = (
                    f"{cfg['axis']} backlash estimated as {estimated_value} arc-sec, "
                    f"but verification was incomplete: {verification.get('message')}. "
                    "Original driver value restored; inspect repeat data before applying."
                )
            if saturated:
                completion_message = (
                    f"{cfg['axis']} backlash reached the {BACKLASH_MAX_VALUE} "
                    "arc-sec limit during measurement. Original driver value "
                    "restored; inspect measurement and verification data before applying."
                )

            confidence = "low" if saturated else "normal"
            if verification.get("warning") or not verification.get("ok"):
                confidence = "low"
            self._backlash_auto_status(
                "complete",
                completion_message,
                estimated_value=estimated_value,
                value_key=cfg["value_key"],
                measurements=measurements,
                invalid_measurements=invalid_measurements,
                verification=verification,
                verification_average_residual_arcsec=verification_residual,
                verification_error_rate_percent=verification_error_rate,
                restored_ra=original_ra,
                restored_de=original_de,
                valid_measurements=len(measurements),
                saturated=saturated,
                confidence=confidence,
            )
            self._console(f"Backlash {cfg['axis']}\n{estimated_value} arcsec")
            return True
        except Exception as exc:
            logger.exception("Auto backlash calculation failed")
            self._backlash_auto_status("failed", f"Auto backlash failed: {exc}")
            self._console("Backlash auto\nfailed")
            return False
        finally:
            self.stop_mount()
            if original_ra is not None and original_de is not None and not restored:
                if self._apply_backlash_values(original_ra, original_de):
                    self._backlash_auto_status(
                        self._backlash_auto.get("state", "failed")
                        if self._backlash_auto
                        else "failed",
                        (
                            self._backlash_auto.get("message", "")
                            if self._backlash_auto
                            else "Auto backlash calculation stopped"
                        ),
                        restored_ra=original_ra,
                        restored_de=original_de,
                    )
            if original_slew is not None and self.slew_rate != original_slew:
                self.set_slew_rate(original_slew)
            if original_tracking is not None:
                current_tracking = self._read_tracking_enabled()
                if current_tracking is None or current_tracking != original_tracking:
                    self.set_tracking(original_tracking)

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
            return self.select_multipoint_align_star(star_name)

        self._align_session_status(
            "waiting", f"Manual alignment 0/{total_points}: select a star"
        )
        self._console("Multi align\nselect star")
        return True

    def select_multipoint_align_star(self, star_name: str) -> bool:
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
        return self._align_goto_current_star(session)

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
            self.auto_calculate_backlash(str(command.get("axis", "")))
        elif command_type == "multipoint_align_start":
            self.start_multipoint_align(
                str(command.get("mode", "manual")),
                command.get("points"),
                command.get("star_name"),
            )
        elif command_type == "multipoint_align_select_star":
            self.select_multipoint_align_star(str(command.get("star_name", "")))
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
    indi_host: str = "localhost",
    indi_port: int = 7624,
) -> None:
    """Process entry point used by ``main.py``."""
    MultiprocLogging.configurer(log_queue)
    controller = MountControlIndi(
        mount_queue,
        console_queue,
        shared_state,
        indi_host=indi_host,
        indi_port=indi_port,
    )
    controller.run()
