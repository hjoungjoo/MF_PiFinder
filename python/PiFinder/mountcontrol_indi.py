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
import queue
import time
from multiprocessing import Queue
from typing import Any, Optional

from PiFinder import sys_utils, utils
from PiFinder.multiproclogging import MultiprocLogging

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


def radec_separation_arcmin(
    ra_a_deg: float, dec_a_deg: float, ra_b_deg: float, dec_b_deg: float
) -> float:
    ra_a = math.radians(ra_a_deg)
    dec_a = math.radians(dec_a_deg)
    ra_b = math.radians(ra_b_deg)
    dec_b = math.radians(dec_b_deg)
    cos_sep = (
        math.sin(dec_a) * math.sin(dec_b)
        + math.cos(dec_a) * math.cos(dec_b) * math.cos(ra_a - ra_b)
    )
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
        with open(STATUS_FILE, "w", encoding="utf-8") as status_out:
            json.dump(payload, status_out, indent=2, sort_keys=True)
    except Exception:
        logger.exception("Could not write mount-control status")


if PyIndi is not None:

    class PiFinderIndiClient(PyIndi.BaseClient):  # type: ignore[misc]
        """Minimal INDI client that finds a telescope-like device."""

        def __init__(self, mount_control=None):
            super().__init__()
            self.telescope_device = None
            self.mount_control = mount_control

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
                if (
                    park_switch[i].name == "PARK"
                    and park_switch[i].s == PyIndi.ISS_ON
                ):
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
            if self.telescope_device is None and (
                any(
                    word in device_name
                    for word in ("telescope", "mount", "eqmod", "lx200", "celestron")
                )
                or device_name == "telescope simulator"
            ):
                self.telescope_device = device
                clientlogger.info("Telescope device detected: %s", device.getDeviceName())

        def removeDevice(self, device):
            if (
                self.telescope_device
                and device.getDeviceName() == self.telescope_device.getDeviceName()
            ):
                clientlogger.warning("Telescope device removed: %s", device.getDeviceName())
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
                self.mount_control.mark_disconnected(f"INDI server disconnected: {code}")

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
        self._guide_correction_enabled = False
        self._guide_correction_target: Optional[tuple[float, float]] = None
        self._guide_correction_accuracy_arcmin = DEFAULT_GOTO_REFINE_ACCURACY_ARCMIN
        self._guide_correction_next_at = 0.0
        self._guide_correction_last_solve_time = 0.0

    def _console(self, message: str) -> None:
        self.console_queue.put(message)

    def _status_fields(self, **extra: Any) -> dict[str, Any]:
        payload = {
            "step_degrees": self.step_degrees,
            "slew_rate": self.slew_rate,
            "ra": self.current_ra,
            "dec": self.current_dec,
        }
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
        payload["guide_correction_enabled"] = self._guide_correction_enabled
        if self._guide_correction_target is not None:
            payload["guide_correction_target_ra"] = self._guide_correction_target[0]
            payload["guide_correction_target_dec"] = self._guide_correction_target[1]
            payload["guide_correction_accuracy_arcmin"] = (
                self._guide_correction_accuracy_arcmin
            )
        if self.device is not None:
            try:
                payload["device"] = self.device.getDeviceName()
            except Exception:
                pass
        payload.update(extra)
        return payload

    def _write_controller_status(
        self, state: str, message: str = "", **extra: Any
    ) -> None:
        _write_status(state, message, **self._status_fields(**extra))

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
                result.get("stderr")
                or result.get("stdout")
                or "INDI command failed"
            )
            logger.warning("INDI setprop returned failure: %s", error)
            self._write_controller_status(failure_state, error)
            return False

        self._write_controller_status(success_state, success_message)
        return True

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
        if now < self._manual_motion_deadline or now < self._manual_motion_stop_retry_at:
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

    def connect(self, announce: bool = True) -> bool:
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

        self.client = PiFinderIndiClient(self)
        self.client.setServer(self.indi_host, self.indi_port)
        self._write_controller_status(
            "connecting",
            f"Connecting to INDI server {self.indi_host}:{self.indi_port}",
        )
        logger.info("Connecting to INDI server at %s:%s", self.indi_host, self.indi_port)

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
            wait_timeout=15,
        )
        if not connect_result["ok"]:
            error = (
                connect_result.get("stderr")
                or connect_result.get("stdout")
                or "Could not connect INDI OnStep driver"
            )
            logger.error("Could not connect INDI OnStep driver after restart: %s", error)
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

    def sync_location_time(self) -> None:
        try:
            latitude = longitude = elevation = None
            location = self.shared_state.location()
            if location and location.lock:
                latitude = float(location.lat)
                longitude = float(location.lon)
                elevation = (
                    None
                    if location.altitude is None
                    else float(location.altitude)
                )

            dt = self.shared_state.datetime()
            properties = sys_utils.build_indi_location_time_properties(
                latitude=latitude,
                longitude=longitude,
                elevation=elevation,
                utc_datetime=dt,
            )

            if properties:
                if self._apply_indi_properties(
                    properties,
                    "connected" if self.connected else "idle",
                    "Location/time sent",
                    "sync_failed",
                ) and latitude is not None and longitude is not None:
                    sys_utils.write_onstep_location_cache(
                        latitude,
                        longitude,
                        elevation,
                        dt,
                    )
            else:
                self._write_controller_status(
                    "connected" if self.connected else "idle",
                    "No locked location/time available",
                )
        except Exception:
            logger.exception("Could not sync INDI location/time")

    def _read_current_position(self) -> Optional[tuple[float, float]]:
        if self.client is None or self.device is None:
            return None

        if not self.client._wait_for_property(
            self.device, "EQUATORIAL_EOD_COORD", timeout=2.0
        ):
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
            self._write_controller_status("refine_failed", "Could not sync current solve")
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
            self._write_controller_status("sync_failed", "Could not set sync coordinates")
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

        if not self.client.set_switch(self.device, "ON_COORD_SET", "TRACK"):
            self._write_controller_status("goto_failed", "Could not set INDI TRACK mode")
            return False

        if not self.client.set_number(
            self.device,
            "EQUATORIAL_EOD_COORD",
            {"RA": (ra_deg % 360.0) / 15.0, "DEC": dec_deg},
        ):
            self._write_controller_status("goto_failed", "Could not set target coordinates")
            return False

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
            [f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_ABORT_MOTION.ABORT=On"],
            "stopped",
            "Mount stop command sent",
            "stop_failed",
        ):
            self._console("INDI stop\nfailed")
            return False

        self._clear_manual_motion_deadline()
        logger.info("Mount stop command sent")
        self._console("INDI mount\nstopped")
        return True

    def manual_move(self, direction: str, lease_seconds: Any = None) -> bool:
        direction = direction.lower()
        motion_map = {
            "north": [
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_NS.MOTION_NORTH=On"
            ],
            "south": [
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_NS.MOTION_SOUTH=On"
            ],
            "east": [
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_WE.MOTION_WEST=On"
            ],
            "west": [
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_WE.MOTION_EAST=On"
            ],
            "northeast": [
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_NS.MOTION_NORTH=On",
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_WE.MOTION_WEST=On",
            ],
            "northwest": [
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_NS.MOTION_NORTH=On",
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_WE.MOTION_EAST=On",
            ],
            "southeast": [
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_NS.MOTION_SOUTH=On",
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_WE.MOTION_WEST=On",
            ],
            "southwest": [
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_NS.MOTION_SOUTH=On",
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_MOTION_WE.MOTION_EAST=On",
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
            [f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.{property_name}=On"],
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

    def refresh_slew_rate(self) -> int:
        properties = sys_utils.get_indi_onstep_properties(
            server_host=self.indi_host,
            server_port=self.indi_port,
        )
        for rate in range(10):
            if (
                properties.get(
                    f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_SLEW_RATE.{rate}"
                )
                == "On"
            ):
                self.slew_rate = rate
                self._write_controller_status(
                    "connected" if self.connected else "idle",
                    f"Slew rate {self.slew_rate}",
                )
                break
        return self.slew_rate

    def set_slew_rate(self, rate: int) -> bool:
        self.slew_rate = max(0, min(9, int(rate)))
        if not self._apply_indi_properties(
            [
                f"{sys_utils.DEFAULT_ONSTEP_DEVICE_NAME}.TELESCOPE_SLEW_RATE.{self.slew_rate}=On"
            ],
            "connected" if self.connected else "idle",
            f"Slew rate {self.slew_rate}",
            "slew_rate_failed",
        ):
            self._console("INDI speed\nfailed")
            return False
        self._console(f"INDI speed\n{self.slew_rate}")
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
            self._check_pending_goto_refine()
            self._check_guide_correction()
            try:
                command = self.mount_queue.get(
                    timeout=self._manual_motion_queue_timeout()
                )
                running = self.handle_command(command)
            except queue.Empty:
                self._check_manual_motion_deadline()
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
