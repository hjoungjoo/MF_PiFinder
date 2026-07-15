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
import re
import time
from datetime import datetime, timezone
from multiprocessing import Queue
from typing import Any, Optional

from PiFinder import calc_utils, config
from PiFinder import sys_utils, utils
from PiFinder.indi_align import (
    ALIGN_STAR_MAX_ALTITUDE_DEG,
    ALIGN_STAR_MIN_ALTITUDE_DEG,
    BRIGHT_ALIGN_STARS,
    clamp_align_points,
    get_align_star,
    nearest_align_star,
    next_align_star,
)
from PiFinder.indi_backlash_calibration import (
    BACKLASH_AUTO_GOTO_TIMEOUT_SECONDS,
    BACKLASH_AUTO_MODE_COMPASS_GOTO,
    BACKLASH_AUTO_MODES,
    BACKLASH_COMPASS_GOTO_REPEATS,
    BACKLASH_COMPASS_GOTO_TIMEOUT_SECONDS,
    BACKLASH_GOTO_MAX_RETRIES,
    BACKLASH_SOLVED_WAIT_SECONDS,
    BacklashCalibrationMixin,
)
from PiFinder.indi_multipoint_align import (
    ALIGN_MODE_AUTO,
    ALIGN_MODE_MANUAL,
    ALIGN_MODES,
    MultiPointAlignController,
    STATE_ADJUST,
    STATE_CANCELLED,
    STATE_FAILED,
    STATE_IDLE,
    STATE_MOVING,
    STATE_PREPARING,
    STATE_WAITING,
)
from PiFinder.multiproclogging import MultiprocLogging

try:
    import PyIndi  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised only on INDI installs
    PyIndi = None


logger = logging.getLogger("MountControl.Indi")
clientlogger = logging.getLogger("MountControl.Indi.Client")

STATUS_FILE = utils.data_dir / "mount_control_status.json"
STOP_REQUEST_FILE = utils.data_dir / "mount_control_stop_request.json"
POSITION_STATUS_MIN_INTERVAL = 0.5
STATUS_HEARTBEAT_INTERVAL = 5.0
AUTO_CONNECT_START_DELAY = 5.0
AUTO_CONNECT_RETRY_INTERVAL = 10.0
AUTO_CONNECT_DEVICE_WAIT_SECONDS = 20.0
AUTO_CONNECT_PROPERTY_WAIT_SECONDS = 20.0
AUTO_CONNECT_DEVICE_CONNECT_WAIT_SECONDS = 15.0
AUTO_CONNECT_POSITION_WAIT_SECONDS = 10.0
MANUAL_MOTION_LEASE_SECONDS = 1.2
MANUAL_MOTION_MIN_LEASE_SECONDS = 0.3
MANUAL_MOTION_MAX_LEASE_SECONDS = 5.0
MANUAL_MOTION_MAX_CONTINUOUS_SECONDS = 10.0
MANUAL_MOTION_POLL_SECONDS = 0.1
MANUAL_MOTION_STOP_RETRY_SECONDS = 0.5
GOTO_REFINE_DELAY_SECONDS = 8.0
GOTO_REFINE_SOLVE_TIMEOUT_SECONDS = 45.0
# Solve-based fine-correction target accuracy (arcmin). 6' = 0.1 deg, matching
# the documented PiFinder GoTo final-alignment accuracy; used as the fallback
# when a caller does not pass an explicit accuracy.
DEFAULT_GOTO_REFINE_ACCURACY_ARCMIN = 6.0
GUIDE_CORRECTION_INTERVAL_SECONDS = 10.0
# Manual-move fallback lease used only when the driver does not expose the INDI
# timed guide-pulse interface.
GUIDE_CORRECTION_PULSE_SECONDS = 0.4
# Real INDI timed guide pulse (TELESCOPE_TIMED_GUIDE_*): the pulse duration is
# computed from the axis error and the mount's guide rate, so the mount moves
# exactly the time proportional to the correction angle.
SIDEREAL_ARCSEC_PER_SEC = 15.041
# Fraction of sidereal used when the driver does not report GUIDE_RATE.
DEFAULT_GUIDE_RATE_X = 0.5
# Recovery-speed guide-rate switching. While the remaining guide-correction
# error is above accuracy * GUIDE_RATE_FAST_MIN_ERROR_MULTIPLE, the guide rate
# is raised to the fast value so recovery pulses cover ground quickly (needs a
# driver that accepts GUIDE_RATE writes up to 1.0, e.g. the modified OnStepX).
# It drops back to the fine value for the final corrections and is restored
# whenever guide correction converges or is disabled, so precision guiding
# always finishes at the conservative rate.
GUIDE_RATE_FAST_X = 1.0
GUIDE_RATE_FINE_X = 0.5
GUIDE_RATE_FAST_MIN_ERROR_MULTIPLE = 2.0
# Close this fraction of the error per pulse; the periodic loop converges the
# rest. Kept conservative because an OnStepX bench test measured the actual
# pulse motion at ~1.6x the nominal GUIDE_RATE (0.5x sidereal), so a higher
# value would overshoot.
GUIDE_PULSE_AGGRESSIVENESS = 0.5
GUIDE_PULSE_MIN_MS = 20
GUIDE_PULSE_MAX_MS = 2500
# INDI timed-guide (property, element) per computed direction. If a mount guides
# the wrong way in RA, swap the "east"/"west" elements here (hardware-validate
# once, like the manual-motion mapping's E/W handling).
GUIDE_PULSE_ELEMENTS: dict[str, tuple[str, str]] = {
    "north": ("TELESCOPE_TIMED_GUIDE_NS", "TIMED_GUIDE_N"),
    "south": ("TELESCOPE_TIMED_GUIDE_NS", "TIMED_GUIDE_S"),
    "east": ("TELESCOPE_TIMED_GUIDE_WE", "TIMED_GUIDE_E"),
    "west": ("TELESCOPE_TIMED_GUIDE_WE", "TIMED_GUIDE_W"),
}
GOTO_COMPLETE_MIN_SECONDS = 1.0
GOTO_COMPLETE_STABLE_SECONDS = 4.0
GOTO_COMPLETE_POSITION_STABLE_DEG = 0.02
GOTO_COMPLETE_TARGET_TOLERANCE_DEG = 0.5
GOTO_ONSTEP_ACTIVE_OBSERVE_GRACE_SECONDS = 3.0
GOTO_COMPLETE_FALLBACK_SECONDS = 180.0
GOTO_TARGET_COMMAND_ATTEMPTS = 2
GOTO_TARGET_ACCEPT_TIMEOUT_SECONDS = 2.0
GOTO_TARGET_ACCEPT_POLL_SECONDS = 0.1
GOTO_TARGET_ACCEPT_TOLERANCE_ARCMIN = 3.0
MULTIPOINT_ALIGN_SYNC_THRESHOLD_ARCMIN = 30.0
MULTIPOINT_ALIGN_SYNC_VERIFY_TIMEOUT_SECONDS = 5.0
MULTIPOINT_ALIGN_SYNC_VERIFY_TOLERANCE_ARCMIN = 5.0


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


class MountControlIndi(BacklashCalibrationMixin):
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
        # Cached result of INDI timed-guide-pulse capability detection (None =
        # not yet probed). When False, guide correction uses the manual-move
        # fallback.
        self._pulse_guide_supported: Optional[bool] = None
        # Guide-rate switching state: boosted is True while the fast recovery
        # rate is applied (must be restored to the fine rate on finish);
        # writable is None until the first GUIDE_RATE write attempt, False when
        # the driver rejected it (no further attempts).
        self._guide_rate_boosted = False
        self._guide_rate_writable: Optional[bool] = None
        self._last_goto_progress_status_at = 0.0
        self._last_manual_motion_status_at = 0.0
        self.backlash_ra: Optional[int] = None
        self.backlash_de: Optional[int] = None
        self._backlash_auto: Optional[dict[str, Any]] = None
        self._backlash_stop_seen_at: float = 0.0
        self._multipoint_align_controller = MultiPointAlignController()
        self._multipoint_align: Optional[dict[str, Any]] = None
        self._coordinate_sync: Optional[dict[str, Any]] = None

    def _console(self, message: str) -> None:
        self.console_queue.put(message)

    def _mount_common_status_fields(
        self, state: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        state_lower = state.strip().lower()
        motion_type: Optional[str] = None
        motion_active = False
        readback_priority = False

        if self._manual_motion_direction is not None or payload.get(
            "manual_motion_direction"
        ):
            motion_type = "manual"
            motion_active = True
            readback_priority = True
        elif self._goto_motion is not None or payload.get("goto_motion_active"):
            motion_type = "goto"
            motion_active = True
            readback_priority = True
        elif self._pending_goto_refine is not None or payload.get(
            "goto_refine_pending"
        ):
            motion_type = "goto_refine_settle"
            readback_priority = True

        if state_lower in {"slewing", "align_goto"}:
            motion_type = motion_type or "goto"
            motion_active = True
            readback_priority = True
        elif state_lower == "manual_motion":
            motion_type = motion_type or "manual"
            motion_active = True
            readback_priority = True
        elif state_lower in {"refine_wait", "refine_sent"}:
            motion_type = motion_type or "goto_refine_settle"
            readback_priority = True
        elif state_lower == "guide_correction":
            # A tracking-guide correction is a sub-arcminute pulse (~0.001 deg at
            # guide rate) -- it barely moves the mount readback, and the IMU
            # delta is already rate-gated so the pulse itself is discarded. Do
            # NOT claim readback priority for it: otherwise, while the guide is
            # nulling residual error right after a GoTo/recovery, the fused
            # coordinate falls back to raw mount for several seconds and a real
            # hand-push (external disturbance) is ignored during that window.
            motion_type = motion_type or "guide_correction"
        elif state_lower.startswith("backlash_auto_") and state_lower not in {
            "backlash_auto_complete",
            "backlash_auto_failed",
            "backlash_auto_stopped",
        }:
            motion_type = motion_type or "backlash_auto"
            motion_active = True
            readback_priority = True
        elif "slew" in state_lower or "goto" in state_lower:
            motion_type = motion_type or "goto"
            motion_active = True
            readback_priority = True
        elif "moving" in state_lower or "motion" in state_lower:
            motion_type = motion_type or "manual"
            motion_active = True
            readback_priority = True

        multipoint = payload.get("multipoint_align")
        if isinstance(multipoint, dict):
            align_state = str(multipoint.get("state", "")).strip().lower()
            if "goto" in align_state or "slew" in align_state:
                motion_type = motion_type or "align_goto"
                motion_active = True
                readback_priority = True

        backlash = payload.get("backlash_auto")
        if isinstance(backlash, dict):
            backlash_state = str(backlash.get("state", "")).strip().lower()
            if backlash_state in {"starting", "running"}:
                motion_type = motion_type or "backlash_auto"
                motion_active = True
                readback_priority = True

        return {
            "mount_motion_active": motion_active,
            "mount_motion_type": motion_type,
            "mount_readback_priority": readback_priority,
        }

    def _status_fields(self, state: str = "", **extra: Any) -> dict[str, Any]:
        payload = {
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
        if self._coordinate_sync is not None:
            payload["coordinate_sync"] = self._coordinate_sync
        if self.device is not None:
            try:
                payload["device"] = self.device.getDeviceName()
            except Exception:
                pass
        payload.update(extra)
        payload.update(self._mount_common_status_fields(state, payload))
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
        _write_status(state, message, **self._status_fields(state, **extra))

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
            if not self._device_is_connected():
                self.mark_disconnected("INDI mount device is disconnected")
                return
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

    def set_current_position(
        self, ra_deg: float, dec_deg: float, write_status: bool = True
    ) -> None:
        self.current_ra = ra_deg % 360.0
        self.current_dec = dec_deg
        if write_status:
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
        self._last_manual_motion_status_at = 0.0

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

    def _read_manual_motion_progress_position(
        self,
    ) -> Optional[tuple[float, float]]:
        try:
            return self._read_cached_current_position(write_status=False)
        except TypeError:
            # Some tests monkeypatch this method with a no-argument callable.
            return self._read_cached_current_position()

    def _write_manual_motion_progress_status(
        self,
        current_position: Optional[tuple[float, float]],
        force: bool = False,
    ) -> None:
        if self._manual_motion_direction is None:
            return

        now = time.monotonic()
        if (
            not force
            and now - self._last_manual_motion_status_at
            < POSITION_STATUS_MIN_INTERVAL
        ):
            return
        self._last_manual_motion_status_at = now

        extra: dict[str, Any] = {
            "manual_motion_direction": self._manual_motion_direction,
        }
        if self._manual_motion_started_at is not None:
            extra["manual_motion_elapsed"] = round(
                now - self._manual_motion_started_at, 1
            )
        if current_position is not None:
            extra["ra"] = current_position[0] % 360.0
            extra["dec"] = current_position[1]

        self._write_controller_status(
            "manual_motion",
            f"Manual {self._manual_motion_direction} motion in progress",
            **extra,
        )

    def _publish_manual_motion_progress(self, force: bool = False) -> None:
        if self._manual_motion_direction is None:
            return
        current_position = self._read_manual_motion_progress_position()
        self._write_manual_motion_progress_status(current_position, force=force)

    def _wait_for_device(
        self, timeout: float = AUTO_CONNECT_DEVICE_WAIT_SECONDS
    ) -> bool:
        assert self.client is not None
        start = time.time()
        while time.time() - start < timeout:
            self.device = self.client.get_telescope_device()
            if self.device is not None:
                return True
            time.sleep(0.25)
        return False

    def _device_is_connected(self) -> bool:
        if self.device is None:
            return False
        try:
            if self.device.isConnected():
                return True
        except Exception:
            pass
        return self._device_switch_on("CONNECTION", "CONNECT") is True

    def _wait_for_device_connected(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._device_is_connected():
                return True
            time.sleep(0.25)
        return False

    def _wait_for_current_position(
        self, timeout: float
    ) -> Optional[tuple[float, float]]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            position = self._read_current_position()
            if position is not None:
                return position
            time.sleep(0.25)
        return None

    def connect(self, announce: bool = True, sync_on_connect: bool = True) -> bool:
        if (
            self.connected
            and self.device is not None
            and self.client is not None
            and self.client.isServerConnected()
        ):
            if self._device_is_connected():
                return True
            self.mark_disconnected("INDI mount device is disconnected")

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

        if self.client is not None:
            try:
                self.client.disconnectServer()
            except Exception:
                logger.debug("Could not close previous INDI client", exc_info=True)
        self.client = None
        self.device = None

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

        if not self.client._wait_for_property(
            self.device,
            "CONNECTION",
            timeout=AUTO_CONNECT_PROPERTY_WAIT_SECONDS,
        ):
            self._write_controller_status(
                "device_connect_failed",
                f"{device_name} CONNECTION property was not available",
            )
            if announce:
                self._console("INDI mount\nconnect failed")
            return False

        if not self._device_is_connected():
            if not self.client.set_switch(
                self.device,
                "CONNECTION",
                "CONNECT",
                timeout=5.0,
            ):
                self._write_controller_status(
                    "device_connect_failed",
                    f"Could not connect {device_name}",
                )
                if announce:
                    self._console("INDI mount\nconnect failed")
                return False
            if not self._wait_for_device_connected(
                AUTO_CONNECT_DEVICE_CONNECT_WAIT_SECONDS
            ):
                self._write_controller_status(
                    "device_connect_failed",
                    f"{device_name} did not enter connected state",
                )
                if announce:
                    self._console("INDI mount\nconnect failed")
                return False

        if sync_on_connect and not direct_sync_for_onstep:
            self.sync_location_time()
        self.client.unpark_mount(self.device)
        self.client.enable_tracking(self.device)
        if self._wait_for_current_position(AUTO_CONNECT_POSITION_WAIT_SECONDS) is None:
            self._write_controller_status(
                "device_connect_failed",
                f"{device_name} did not publish telescope coordinates",
            )
            if announce:
                self._console("INDI mount\nconnect failed")
            return False
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
        self._coordinate_sync = None
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

    def sync_location_time(
        self,
        reconnect_after: bool = True,
        include_default_location: bool = False,
    ) -> bool:
        try:
            latitude, longitude, elevation, dt = self._shared_location_time_values(
                include_default_location=include_default_location
            )
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

    def _read_cached_number_position(
        self, property_name: str
    ) -> Optional[tuple[float, float]]:
        if self.device is None:
            return None
        get_number = getattr(self.device, "getNumber", None)
        if get_number is None:
            return None

        coord_prop = get_number(property_name)
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

        return ra_hours * 15.0, dec_deg

    def _read_cached_current_position(
        self, write_status: bool = True
    ) -> Optional[tuple[float, float]]:
        position = self._read_cached_number_position("EQUATORIAL_EOD_COORD")
        if position is None:
            return None

        self.set_current_position(position[0], position[1], write_status=write_status)
        return self.current_ra, self.current_dec

    def _read_current_position(
        self, write_status: bool = True
    ) -> Optional[tuple[float, float]]:
        if self.client is None or self.device is None:
            return None

        if not self.client._wait_for_property(
            self.device, "EQUATORIAL_EOD_COORD", timeout=2.0
        ):
            return None

        return self._read_cached_current_position(write_status=write_status)

    def _read_target_position(self) -> Optional[tuple[float, float]]:
        if self.client is None or self.device is None:
            return None

        if not self.client._wait_for_property(
            self.device, "TARGET_EOD_COORD", timeout=0.25
        ):
            return None

        return self._read_cached_number_position("TARGET_EOD_COORD")

    def _goto_target_accepted(self, ra_deg: float, dec_deg: float) -> bool:
        target_ra = ra_deg % 360.0
        started_at = time.monotonic()
        while True:
            target_position = self._read_target_position()
            if (
                target_position is not None
                and radec_separation_arcmin(
                    target_position[0],
                    target_position[1],
                    target_ra,
                    dec_deg,
                )
                <= GOTO_TARGET_ACCEPT_TOLERANCE_ARCMIN
            ):
                return True

            if self._indi_mount_is_busy() is True:
                return True

            onstep_complete = self._onstep_goto_complete()
            if onstep_complete is False:
                return True

            current_position = self._read_cached_current_position()
            if (
                current_position is not None
                and radec_separation_arcmin(
                    current_position[0],
                    current_position[1],
                    target_ra,
                    dec_deg,
                )
                <= GOTO_TARGET_ACCEPT_TOLERANCE_ARCMIN
            ):
                return True

            if (
                time.monotonic() - started_at
                >= GOTO_TARGET_ACCEPT_TIMEOUT_SECONDS
            ):
                return False

            time.sleep(GOTO_TARGET_ACCEPT_POLL_SECONDS)

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
        self._last_goto_progress_status_at = 0.0

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

    def _read_goto_progress_position(
        self, cached_only: bool
    ) -> Optional[tuple[float, float]]:
        try:
            if cached_only:
                return self._read_cached_current_position(write_status=False)
            return self._read_current_position(write_status=False)
        except TypeError:
            # Some tests monkeypatch these methods with no-argument callables.
            if cached_only:
                return self._read_cached_current_position()
            return self._read_current_position()

    def _write_goto_progress_status(
        self,
        motion: dict[str, Any],
        elapsed: float,
        is_busy: Optional[bool],
        current_position: Optional[tuple[float, float]],
    ) -> None:
        now = time.monotonic()
        if now - self._last_goto_progress_status_at < POSITION_STATUS_MIN_INTERVAL:
            return
        self._last_goto_progress_status_at = now

        target_ra = motion.get("target_ra")
        target_dec = motion.get("target_dec")
        extra: dict[str, Any] = {
            "target_ra": target_ra,
            "target_dec": target_dec,
            "goto_wait_seconds": round(elapsed, 1),
            "indi_busy": is_busy,
        }
        if current_position is not None:
            extra["ra"] = current_position[0] % 360.0
            extra["dec"] = current_position[1]
            if target_ra is not None and target_dec is not None:
                extra["target_error_deg"] = (
                    radec_separation_arcmin(
                        current_position[0],
                        current_position[1],
                        float(target_ra),
                        float(target_dec),
                    )
                    / 60.0
                )

        self._write_controller_status(
            "slewing",
            "GoTo in progress",
            **extra,
        )

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
        current_position = self._read_goto_progress_position(
            cached_only=is_busy is True
        )
        self._write_goto_progress_status(
            self._goto_motion,
            elapsed,
            is_busy,
            current_position,
        )
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
            self._restore_fine_guide_rate()
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
            self._restore_fine_guide_rate()
            self._write_controller_status(
                "guide_correction",
                f"Guide correction within {separation:.1f} arcmin",
                guide_error_arcmin=separation,
            )
            return

        # Preferred: real INDI timed guide pulses (moves the mount for a time
        # computed from the axis error and guide rate; NOT a manual move, so it
        # does not show up as manual_motion in status).
        if self._guide_pulse_supported():
            self._select_guide_rate_for_error(separation)
            self._apply_guide_pulse(
                current_ra, current_dec, target_ra, target_dec, separation
            )
            return

        # Fallback: short manual-movement nudge for drivers without a timed
        # guide-pulse interface.
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

    def _guide_pulse_supported(self) -> bool:
        if self._pulse_guide_supported is not None:
            return self._pulse_guide_supported
        if self.device is None:
            # Do not cache a negative result before the device is available;
            # re-probe once it connects.
            return False
        try:
            ns = self.device.getNumber("TELESCOPE_TIMED_GUIDE_NS")
            we = self.device.getNumber("TELESCOPE_TIMED_GUIDE_WE")
            supported = bool(ns) and bool(we)
        except Exception:
            logger.debug("Timed guide capability probe failed", exc_info=True)
            supported = False
        self._pulse_guide_supported = supported
        logger.info("INDI timed guide pulse supported: %s", supported)
        return supported

    def _current_guide_rate_x(self) -> tuple[float, float]:
        """Guide rate (WE, NS) as a fraction of sidereal, with a safe fallback."""
        if self.device is not None:
            try:
                guide_rate = self.device.getNumber("GUIDE_RATE")
                if guide_rate:
                    we = ns = None
                    for i in range(len(guide_rate)):
                        if guide_rate[i].name == "GUIDE_RATE_WE":
                            we = float(guide_rate[i].value)
                        elif guide_rate[i].name == "GUIDE_RATE_NS":
                            ns = float(guide_rate[i].value)
                    if we and ns and we > 0.0 and ns > 0.0:
                        return we, ns
            except Exception:
                logger.debug("GUIDE_RATE read failed", exc_info=True)
        driver = self._read_driver_guide_rate()
        if driver is not None and driver[0] > 0.0 and driver[1] > 0.0:
            return driver
        return DEFAULT_GUIDE_RATE_X, DEFAULT_GUIDE_RATE_X

    def _set_guide_rate(self, rate_x: float) -> bool:
        if self.client is None or self.device is None:
            return False
        return self.client.set_number(
            self.device,
            "GUIDE_RATE",
            {"GUIDE_RATE_WE": rate_x, "GUIDE_RATE_NS": rate_x},
        )

    def _select_guide_rate_for_error(self, separation_arcmin: float) -> None:
        """Pick the guide rate for the coming pulse from the remaining error.

        Large recovery errors run at GUIDE_RATE_FAST_X so each capped pulse
        covers twice the ground; once the error is inside the fine band the
        rate drops back to GUIDE_RATE_FINE_X for precise final corrections.
        A driver that rejects the GUIDE_RATE write keeps its own rate (the
        pulse-duration math always reads the actual rate back), and no further
        writes are attempted.
        """
        if self._guide_rate_writable is False:
            return
        fast_band_arcmin = (
            self._guide_correction_accuracy_arcmin
            * GUIDE_RATE_FAST_MIN_ERROR_MULTIPLE
        )
        desired = (
            GUIDE_RATE_FAST_X
            if separation_arcmin > fast_band_arcmin
            else GUIDE_RATE_FINE_X
        )
        current_we, current_ns = self._current_guide_rate_x()
        if abs(current_we - desired) <= 0.01 and abs(current_ns - desired) <= 0.01:
            self._guide_rate_boosted = desired == GUIDE_RATE_FAST_X
            return
        if self._set_guide_rate(desired):
            self._guide_rate_writable = True
            self._guide_rate_boosted = desired == GUIDE_RATE_FAST_X
            logger.info(
                "Guide rate set to %.2fx sidereal (error %.1f arcmin, "
                "fast band > %.1f arcmin)",
                desired,
                separation_arcmin,
                fast_band_arcmin,
            )
        else:
            if self._guide_rate_writable is None:
                logger.warning(
                    "Driver rejected GUIDE_RATE write; keeping current guide rate"
                )
            self._guide_rate_writable = False

    def _restore_fine_guide_rate(self) -> None:
        """Drop back to the fine guide rate after a fast-rate recovery."""
        if not self._guide_rate_boosted:
            return
        self._guide_rate_boosted = False
        if self._guide_rate_writable is False:
            return
        if self._set_guide_rate(GUIDE_RATE_FINE_X):
            logger.info(
                "Guide rate restored to %.2fx sidereal", GUIDE_RATE_FINE_X
            )

    def _guide_pulse_ms(self, error_arcsec: float, guide_rate_x: float) -> int:
        rate_arcsec_per_sec = max(0.01, guide_rate_x) * SIDEREAL_ARCSEC_PER_SEC
        ms = (
            abs(error_arcsec)
            / rate_arcsec_per_sec
            * 1000.0
            * GUIDE_PULSE_AGGRESSIVENESS
        )
        return int(max(GUIDE_PULSE_MIN_MS, min(GUIDE_PULSE_MAX_MS, ms)))

    def _send_guide_pulse(self, direction: str, duration_ms: int) -> bool:
        if self.client is None or self.device is None:
            return False
        prop_name, element = GUIDE_PULSE_ELEMENTS[direction]
        return self.client.set_number(
            self.device, prop_name, {element: float(duration_ms)}
        )

    def _guide_pulse_inversions(self) -> tuple[bool, bool]:
        """Per-axis guide-pulse direction inversion (NS, WE) from config.

        Read fresh each cycle so a UI toggle takes effect without extra plumbing;
        the guide loop only runs every GUIDE_CORRECTION_INTERVAL_SECONDS. Applies
        to the timed guide pulse only, not the manual-move fallback (whose
        direction mapping is already hardware-validated).
        """
        try:
            cfg = config.Config()
            cfg.load_config()
            invert_ns = bool(cfg.get_option("indi_guide_pulse_invert_ns", False))
            invert_we = bool(cfg.get_option("indi_guide_pulse_invert_we", False))
        except Exception:
            logger.debug("Guide-pulse inversion config read failed", exc_info=True)
            return False, False
        return invert_ns, invert_we

    def _apply_guide_pulse(
        self,
        current_ra: float,
        current_dec: float,
        target_ra: float,
        target_dec: float,
        separation: float,
    ) -> None:
        ra_delta_deg = shortest_ra_delta_deg(target_ra, current_ra)
        dec_delta_deg = target_dec - current_dec
        ra_arcsec = ra_delta_deg * 3600.0 * math.cos(math.radians(current_dec))
        dec_arcsec = dec_delta_deg * 3600.0
        guide_we_x, guide_ns_x = self._current_guide_rate_x()
        invert_ns, invert_we = self._guide_pulse_inversions()
        threshold_arcsec = (
            max(0.5, self._guide_correction_accuracy_arcmin / 2.0) * 60.0
        )

        pulses: list[str] = []
        if abs(dec_arcsec) > threshold_arcsec:
            ns_dir = "north" if dec_arcsec > 0 else "south"
            if invert_ns:
                ns_dir = "south" if ns_dir == "north" else "north"
            ns_ms = self._guide_pulse_ms(dec_arcsec, guide_ns_x)
            if self._send_guide_pulse(ns_dir, ns_ms):
                pulses.append(f"{ns_dir} {ns_ms}ms")
        if abs(ra_arcsec) > threshold_arcsec:
            we_dir = "east" if ra_arcsec > 0 else "west"
            if invert_we:
                we_dir = "west" if we_dir == "east" else "east"
            we_ms = self._guide_pulse_ms(ra_arcsec, guide_we_x)
            if self._send_guide_pulse(we_dir, we_ms):
                pulses.append(f"{we_dir} {we_ms}ms")

        if pulses:
            self._write_controller_status(
                "guide_correction",
                f"Guide pulse {', '.join(pulses)}; error {separation:.1f} arcmin",
                guide_error_arcmin=separation,
                guide_direction=",".join(p.split()[0] for p in pulses),
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
        self._coordinate_sync = {
            "active": True,
            "synced": True,
            "ra": ra_deg % 360.0,
            "dec": dec_deg,
            "synced_at": time.time(),
            "source": "sync_mount",
        }
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
        target_ra = ra_deg % 360.0
        if not self.connect() or self.client is None or self.device is None:
            return False

        for attempt in range(1, GOTO_TARGET_COMMAND_ATTEMPTS + 1):
            if not self.client.set_switch(self.device, "ON_COORD_SET", "SLEW"):
                self._write_controller_status(
                    "goto_failed", "Could not set INDI SLEW mode"
                )
                return False

            if not self.client.set_number(
                self.device,
                "EQUATORIAL_EOD_COORD",
                {"RA": target_ra / 15.0, "DEC": dec_deg},
            ):
                self._write_controller_status(
                    "goto_failed", "Could not set target coordinates"
                )
                return False

            if self._goto_target_accepted(target_ra, dec_deg):
                break

            logger.warning(
                "INDI GoTo target RA %.4f Dec %.4f was not accepted "
                "after attempt %d/%d",
                target_ra,
                dec_deg,
                attempt,
                GOTO_TARGET_COMMAND_ATTEMPTS,
            )
        else:
            self._write_controller_status(
                "goto_failed",
                "INDI GoTo target was not accepted",
                target_ra=target_ra,
                target_dec=dec_deg,
            )
            self._console("INDI mount\nGoTo failed")
            return False

        self._last_goto_target = (target_ra, dec_deg)
        self._arm_goto_motion(target_ra, dec_deg)
        self._write_controller_status(
            "slewing",
            "GoTo target command sent",
            target_ra=target_ra,
            target_dec=dec_deg,
        )
        logger.info("Mount GoTo RA %.4f Dec %.4f", target_ra, dec_deg)
        self._console("INDI mount\nGoTo sent")
        if refine_after_goto:
            self._arm_goto_refine(target_ra, dec_deg, refine_accuracy_arcmin)
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
        self._publish_manual_motion_progress(force=True)
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

    def _align_session_status(self, state: str, message: str) -> None:
        self._multipoint_align_controller.set_status(state, message)
        self._multipoint_align = self._multipoint_align_controller.status()
        self._write_controller_status(
            "align_" + state,
            message,
        )

    def _current_align_session(self) -> Optional[dict[str, Any]]:
        self._multipoint_align = self._multipoint_align_controller.status()
        return self._multipoint_align_controller.active_session()

    def _current_pifinder_pointing_for_align(
        self,
    ) -> Optional[tuple[str, float, float]]:
        try:
            solution = self.shared_state.solution()
        except Exception:
            solution = None
        if solution is not None and solution.has_pointing():
            try:
                pointing = solution.pointing.aligned.estimate
                if pointing is None:
                    pointing = solution.pointing.aligned.solve
                if pointing is not None:
                    return "solve", float(pointing.RA) % 360.0, float(pointing.Dec)
            except (AttributeError, TypeError, ValueError):
                logger.debug(
                    "Could not read PiFinder solved pointing for multi-align",
                    exc_info=True,
                )

        imu_altaz = self._current_imu_altaz()
        if imu_altaz is None:
            return None
        imu_radec = self._imu_altaz_to_radec(imu_altaz[0], imu_altaz[1])
        if imu_radec is None:
            return None
        return "imu", imu_radec[0] % 360.0, imu_radec[1]

    def _sync_multipoint_location_time(self, session: dict[str, Any]) -> bool:
        if not self.sync_location_time(
            reconnect_after=True,
            include_default_location=True,
        ):
            self._align_session_status(
                STATE_FAILED,
                "Could not sync location/time before multi-point alignment",
            )
            return False
        self._multipoint_align_controller.set_location_time_synced()
        return True

    def _onstep_native_alignment_active(self) -> bool:
        if not sys_utils.is_onstepx_device_name(self._indi_device_name()):
            return False

        for element_name in ("6", "7"):
            text_value = self._device_text_value("Align Process", element_name).strip()
            try:
                if int(text_value) > 0:
                    return True
            except (TypeError, ValueError):
                pass

        status_text = self._device_text_value("Align Process", "4")
        match = re.search(r"(\d)(\d)(\d)\s+Alignment", status_text or "")
        if not match:
            return False
        current_star = int(match.group(2))
        last_star = int(match.group(3))
        return current_star > 0 or last_star > 0

    def _reset_stale_onstep_alignment_for_multipoint(
        self, session: dict[str, Any]
    ) -> bool:
        if not self._onstep_native_alignment_active():
            session["onstep_native_align_reset"] = False
            return True

        onstep_cfg = self._onstep_connection_config()
        self._align_session_status(
            STATE_PREPARING,
            "Resetting stale OnStep native alignment before multi-point alignment",
        )
        logger.info("Resetting stale OnStep native alignment before multi-align")
        result = sys_utils.reset_onstep_alignment_exclusive(
            connection_type=onstep_cfg["connection_type"],
            network_host=onstep_cfg["network_host"],
            network_port=onstep_cfg["network_port"],
            serial_port=onstep_cfg["serial_port"],
            server_host=self.indi_host,
            server_port=self.indi_port,
        )
        session["onstep_native_align_reset"] = bool(result.get("ok"))
        session["onstep_native_align_reset_result"] = result
        if not result.get("ok"):
            error = result.get("stderr") or "Could not reset OnStep native alignment"
            logger.warning("Could not reset stale OnStep native alignment: %s", error)
            self._align_session_status(STATE_FAILED, error)
            return False

        self.disconnect()
        if not self.connect():
            self._align_session_status(
                STATE_FAILED,
                "Could not reconnect INDI after resetting OnStep native alignment",
            )
            return False
        return True

    def _sync_multipoint_mount_to_pifinder(self, session: dict[str, Any]) -> bool:
        pointing = self._current_pifinder_pointing_for_align()
        if pointing is None:
            self._align_session_status(
                STATE_FAILED,
                "No PiFinder solve or IMU pointing available for alignment sync",
            )
            return False

        source, pifinder_ra, pifinder_dec = pointing
        mount_position = self._read_current_position()
        separation_arcmin = None
        if mount_position is not None:
            separation_arcmin = radec_separation_arcmin(
                mount_position[0],
                mount_position[1],
                pifinder_ra,
                pifinder_dec,
            )
        self._multipoint_align_controller.record_pifinder_sync(
            source,
            pifinder_ra,
            pifinder_dec,
            separation_arcmin,
        )

        if not self.sync_mount(pifinder_ra, pifinder_dec):
            self._align_session_status(
                STATE_FAILED,
                "Could not sync mount coordinates to PiFinder before alignment",
            )
            return False

        if not self._verify_multipoint_mount_sync(
            session, "before native alignment start"
        ):
            return False

        self._multipoint_align_controller.mark_mount_synced()
        self._align_session_status(
            STATE_WAITING,
            f"Mount synced to PiFinder {source} before alignment",
        )

        return True

    def _verify_multipoint_mount_sync(
        self,
        session: dict[str, Any],
        phase: str,
        timeout: float = MULTIPOINT_ALIGN_SYNC_VERIFY_TIMEOUT_SECONDS,
        tolerance_arcmin: float = MULTIPOINT_ALIGN_SYNC_VERIFY_TOLERANCE_ARCMIN,
    ) -> bool:
        pifinder_ra = session.get("pifinder_sync_ra")
        pifinder_dec = session.get("pifinder_sync_dec")
        if pifinder_ra is None or pifinder_dec is None:
            self._align_session_status(
                STATE_FAILED,
                "No PiFinder pointing was recorded for alignment sync verification",
            )
            return False

        deadline = time.monotonic() + timeout
        last_position = None
        last_separation = None
        while time.monotonic() <= deadline:
            mount_position = self._read_current_position()
            if mount_position is not None:
                last_position = mount_position
                last_separation = radec_separation_arcmin(
                    mount_position[0],
                    mount_position[1],
                    float(pifinder_ra),
                    float(pifinder_dec),
                )
                if last_separation <= tolerance_arcmin:
                    self._multipoint_align_controller.record_mount_sync_verified(
                        True,
                        phase,
                        tolerance_arcmin,
                        mount_position,
                        last_separation,
                    )
                    return True
            time.sleep(0.25)

        self._multipoint_align_controller.record_mount_sync_verified(
            False,
            phase,
            tolerance_arcmin,
            last_position,
            last_separation,
        )
        if last_position is None:
            self._align_session_status(
                STATE_FAILED,
                f"Could not read mount coordinates {phase}",
            )
        else:
            self._align_session_status(
                STATE_FAILED,
                (
                    f"Mount coordinates no longer match PiFinder {phase}; "
                    f"separation {last_separation:.1f} arcmin"
                ),
            )
        return False

    def start_mount_alignment_session(self, points: Any) -> bool:
        total_points = clamp_align_points(points)
        if not self.connect() or self.client is None or self.device is None:
            logger.warning("Could not connect before starting mount alignment session")
            return False

        def set_switch(property_name: str, element_name: str) -> bool:
            try:
                return bool(
                    self.client.set_switch(
                        self.device,
                        property_name,
                        element_name,
                        timeout=1.0,
                    )
                )
            except TypeError:
                return bool(
                    self.client.set_switch(
                        self.device,
                        property_name,
                        element_name,
                    )
                )

        if not set_switch("AlignStars", str(total_points)):
            logger.info(
                "INDI mount does not expose AlignStars; "
                "continuing PiFinder multi-point alignment without mount session"
            )
            return False

        if not set_switch("NewAlignStar", "0"):
            logger.info(
                "INDI mount does not expose NewAlignStar start; "
                "continuing PiFinder multi-point alignment without mount session"
            )
            return False

        logger.info(
            "Started INDI mount native alignment session for %d point(s)",
            total_points,
        )
        return True

    def accept_mount_alignment_point(self) -> bool:
        if not self.connect() or self.client is None or self.device is None:
            logger.warning("Could not connect before accepting mount alignment point")
            return False

        try:
            accepted = bool(
                self.client.set_switch(
                    self.device,
                    "NewAlignStar",
                    "1",
                    timeout=2.0,
                )
            )
        except TypeError:
            accepted = bool(
                self.client.set_switch(
                    self.device,
                    "NewAlignStar",
                    "1",
                )
            )

        if not accepted:
            logger.warning("INDI mount rejected native alignment point accept")
            return False

        logger.info("Accepted current INDI mount native alignment point")
        return True

    def _set_current_align_star(
        self, session: dict[str, Any], star: dict[str, Any]
    ) -> dict[str, Any]:
        return self._multipoint_align_controller.set_current_target(star)

    def _align_auto_star(self, session: dict[str, Any]) -> dict[str, Any]:
        completed = session.get("completed", [])
        used_names = {str(star.get("name", "")).casefold() for star in completed}
        reference = session.get("auto_reference")
        try:
            latitude, longitude, elevation, dt = self._shared_location_time_values(
                include_default_location=True
            )
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
                if (
                    ALIGN_STAR_MIN_ALTITUDE_DEG
                    <= alt
                    <= ALIGN_STAR_MAX_ALTITUDE_DEG
                ):
                    visible_candidates.append((alt, star))
            if visible_candidates:
                visible_stars = [star for _alt, star in visible_candidates]
                if reference:
                    return nearest_align_star(
                        float(reference["ra"]),
                        float(reference["dec"]),
                        completed,
                        visible_stars,
                    )
                return dict(max(visible_candidates, key=lambda item: item[0])[1])
        except Exception:
            logger.debug(
                "Could not altitude-filter auto alignment stars", exc_info=True
            )
        if reference:
            return nearest_align_star(
                float(reference["ra"]),
                float(reference["dec"]),
                completed,
            )
        return next_align_star(completed)

    def _align_goto_current_star(self, session: dict[str, Any]) -> bool:
        current_star = session.get("current_star")
        if not current_star:
            self._align_session_status(STATE_WAITING, "Select an alignment star")
            return False

        altaz = self._radec_to_altaz(
            float(current_star["ra"]),
            float(current_star["dec"]),
        )
        if altaz is not None and altaz[0] < ALIGN_STAR_MIN_ALTITUDE_DEG:
            self._multipoint_align_controller.clear_current_target()
            self._align_session_status(
                STATE_WAITING,
                (
                    f"{current_star['name']} is below the "
                    f"{ALIGN_STAR_MIN_ALTITUDE_DEG:.0f} deg alignment limit; "
                    "select another star"
                ),
            )
            self._console("Align star\nbelow horizon")
            return False
        if altaz is not None and altaz[0] > ALIGN_STAR_MAX_ALTITUDE_DEG:
            self._multipoint_align_controller.clear_current_target()
            self._align_session_status(
                STATE_WAITING,
                (
                    f"{current_star['name']} is above the "
                    f"{ALIGN_STAR_MAX_ALTITUDE_DEG:.0f} deg alignment limit; "
                    "select another star"
                ),
            )
            self._console("Align star\nnear zenith")
            return False

        self._align_session_status(STATE_MOVING, f"GoTo {current_star['name']} sent")
        logger.info(
            "Multi-align GoTo requested for %s: RA %.6f Dec %.6f",
            current_star["name"],
            float(current_star["ra"]) % 360.0,
            float(current_star["dec"]),
        )
        if not self.goto_target(
            float(current_star["ra"]),
            float(current_star["dec"]),
            refine_after_goto=False,
        ):
            logger.warning(
                "Multi-align GoTo failed for %s: RA %.6f Dec %.6f",
                current_star["name"],
                float(current_star["ra"]) % 360.0,
                float(current_star["dec"]),
            )
            self._multipoint_align_controller.clear_current_target()
            self._align_session_status(
                STATE_WAITING,
                f"Could not GoTo {current_star['name']}; select another star",
            )
            self._write_controller_status(
                "align_goto_failed",
                f"Could not GoTo {current_star['name']}; select another star",
            )
            self._console("Align GoTo\nfailed")
            return False

        self._multipoint_align_controller.mark_current_target_sent()
        self._align_session_status(
            STATE_ADJUST,
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
        target_ra: Any = None,
        target_dec: Any = None,
    ) -> bool:
        mode = (mode or "manual").strip().lower()
        if mode not in ALIGN_MODES:
            mode = ALIGN_MODE_MANUAL
        total_points = clamp_align_points(points)
        session = self._multipoint_align_controller.start(mode, total_points)
        self._multipoint_align = session
        self._align_session_status(STATE_PREPARING, "Preparing multi-point alignment")

        if not self._sync_multipoint_location_time(session):
            self._multipoint_align_controller.fail(session["message"])
            return False

        if not self._reset_stale_onstep_alignment_for_multipoint(session):
            self._multipoint_align_controller.fail(session["message"])
            return False

        if not self._sync_multipoint_mount_to_pifinder(session):
            self._multipoint_align_controller.fail(session["message"])
            return False

        # OnStep's native :A<n># alignment start resets the mount home frame.
        # Keep it deferred so the star-selection/GoTo phase starts with the
        # mount coordinates synchronized to PiFinder.
        self._multipoint_align_controller.record_native_alignment_started(False)
        session["mount_align_deferred"] = True
        session["mount_align_message"] = (
            "Mount native alignment start deferred to preserve PiFinder sync"
        )

        if target_ra is not None and target_dec is not None:
            try:
                self._multipoint_align_controller.set_auto_reference(
                    float(target_ra), float(target_dec)
                )
            except (TypeError, ValueError):
                session["auto_reference"] = None
        elif session.get("pifinder_sync_ra") is not None:
            self._multipoint_align_controller.set_auto_reference(
                float(session["pifinder_sync_ra"]),
                float(session["pifinder_sync_dec"]),
            )

        if mode == ALIGN_MODE_AUTO:
            star = self._align_auto_star(session)
            self._set_current_align_star(session, star)
            self._align_session_status(
                STATE_MOVING, f"Auto alignment point 1/{total_points}: {star['name']}"
            )
            return self._align_goto_current_star(session)

        if star_name:
            return self.select_multipoint_align_star(star_name, goto=False)

        self._align_session_status(
            STATE_WAITING, f"Manual alignment 0/{total_points}: select a star"
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
            self._align_session_status(STATE_FAILED, f"Unknown alignment star: {star_name}")
            return False

        session["mode"] = ALIGN_MODE_MANUAL
        self._set_current_align_star(session, star)
        logger.info(
            "Multi-align selected star %s, goto=%s, point %d/%d",
            star["name"],
            goto,
            int(session.get("completed_points", 0)) + 1,
            int(session.get("total_points", 0)),
        )
        if goto:
            return self._align_goto_current_star(session)

        self._align_session_status(
            STATE_ADJUST,
            (
                f"Selected {star['name']}; center the target manually, "
                "then confirm the alignment point"
            ),
        )
        self._console(f"Align star\n{star['name']}")
        return True

    def select_multipoint_align_target(
        self,
        ra_deg: Any,
        dec_deg: Any,
        name: str | None = None,
        goto: bool = True,
    ) -> bool:
        session = self._current_align_session()
        if session is None:
            return False
        try:
            ra = float(ra_deg) % 360.0
            dec = float(dec_deg)
        except (TypeError, ValueError):
            self._align_session_status(STATE_FAILED, "Invalid alignment target")
            return False

        current_star = self._set_current_align_star(
            session,
            {
                "name": name or f"SkySafari Target {session['completed_points'] + 1}",
                "ra": ra,
                "dec": dec,
                "mag": None,
            },
        )
        session["mode"] = session.get("mode") or ALIGN_MODE_MANUAL
        if goto:
            return self._align_goto_current_star(session)

        self._align_session_status(
            STATE_ADJUST,
            (
                f"Selected {current_star['name']}; center the target manually, "
                "then confirm the alignment point"
            ),
        )
        self._console(f"Align target\n{current_star['name']}")
        return True

    def confirm_multipoint_align(
        self,
        ra_deg: Any = None,
        dec_deg: Any = None,
        source: str = "ui",
    ) -> bool:
        session = self._current_align_session()
        if session is None:
            self._align_session_status(STATE_IDLE, "No active multi-point alignment")
            return False

        current_star = session.get("current_star")
        if ra_deg is not None and dec_deg is not None:
            try:
                ra_deg = float(ra_deg) % 360.0
                dec_deg = float(dec_deg)
            except (TypeError, ValueError):
                self._align_session_status(STATE_FAILED, "Invalid alignment coordinates")
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

        if not current_star:
            self._align_session_status(STATE_WAITING, "Select an alignment star first")
            return False

        ra = float(current_star["ra"]) % 360.0
        dec = float(current_star["dec"])
        mount_align_started = bool(session.get("mount_align_started"))
        if not current_star.get("target_sent"):
            self._align_session_status(
                STATE_FAILED,
                (
                    f"{current_star['name']} target was not sent to the mount; "
                    "GoTo the alignment target before confirming"
                ),
            )
            return False
        if mount_align_started:
            if not self.accept_mount_alignment_point():
                self._align_session_status(
                    STATE_FAILED, f"Could not accept {current_star['name']}"
                )
                return False
        elif not self.sync_mount(ra, dec):
            self._align_session_status(
                STATE_FAILED, f"Could not confirm {current_star['name']}"
            )
            return False

        self._multipoint_align_controller.record_current_point(
            source,
            "A+" if mount_align_started else "sync",
        )
        completed = list(session.get("completed", []))

        if len(completed) >= int(session["total_points"]):
            self._multipoint_align_controller.complete(
                f"Multi-point alignment complete: {len(completed)} points"
            )
            self._console("Multi align\ncomplete")
            return True

        if session.get("mode") == ALIGN_MODE_AUTO:
            star = self._align_auto_star(session)
            self._set_current_align_star(session, star)
            self._align_session_status(
                STATE_MOVING,
                (
                    f"Auto alignment point {len(completed) + 1}/"
                    f"{session['total_points']}: {star['name']}"
                ),
            )
            return self._align_goto_current_star(session)

        self._align_session_status(
            STATE_WAITING,
            (
                f"Alignment point {len(completed)}/{session['total_points']} saved; "
                "select the next star"
            ),
        )
        self._console(f"Align point\n{len(completed)} saved")
        return True

    def cancel_multipoint_align(self) -> bool:
        self._multipoint_align_controller.cancel()
        self._multipoint_align = self._multipoint_align_controller.status()
        self._write_controller_status(
            "align_" + STATE_CANCELLED, "Multi-point alignment cancelled"
        )
        self._console("Multi align\ncancelled")
        return True

    def clear_multipoint_align_target(self) -> bool:
        session = self._multipoint_align_controller.active_session()
        if not session:
            return False
        self._multipoint_align_controller.clear_current_target()
        self._align_session_status(STATE_WAITING, "Select an alignment star")
        self._write_controller_status("align_target_cleared", "Select another star")
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
                command.get("target_ra"),
                command.get("target_dec"),
            )
        elif command_type == "multipoint_align_select_star":
            self.select_multipoint_align_star(
                str(command.get("star_name", "")),
                bool(command.get("goto", False)),
            )
        elif command_type == "multipoint_align_goto_target":
            self.select_multipoint_align_target(
                command.get("ra"),
                command.get("dec"),
                command.get("name"),
                True,
            )
        elif command_type == "multipoint_align_confirm":
            self.confirm_multipoint_align(
                command.get("ra"),
                command.get("dec"),
                str(command.get("source", "ui")),
            )
        elif command_type == "multipoint_align_cancel":
            self.cancel_multipoint_align()
        elif command_type == "multipoint_align_clear_target":
            self.clear_multipoint_align_target()
        elif command_type == "sync_location_time":
            self.sync_location_time(
                include_default_location=bool(
                    command.get("include_default_location", False)
                )
            )
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
            self._publish_manual_motion_progress()
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
                self._publish_manual_motion_progress()
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
