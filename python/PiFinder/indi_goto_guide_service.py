#!/usr/bin/python
# -*- coding:utf-8 -*-
"""INDI GoTo/Guide orchestration service.

This service is intentionally separate from ``mountcontrol_indi``.  The mount
control process remains the low-level INDI command executor, while this process
will grow into the higher-level GoTo/Guide policy and state machine.

Stage 3 routes accepted GoTo/abort requests to the existing mount-control
executor when the selected method is ``indi_mount``.  PiFinder-driven GoTo is
left as an explicit later-stage state.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import time
from multiprocessing import Queue
from typing import Any, Optional

from PiFinder import config, utils
from PiFinder.multiproclogging import MultiprocLogging


logger = logging.getLogger("IndiGotoGuideService")

STATUS_FILE = utils.data_dir / "indi_goto_guide_status.json"
MOUNT_STATUS_FILE = utils.data_dir / "mount_control_status.json"
POINTING_STATUS_FILE = utils.data_dir / "pointing_coordinate_status.json"

HEARTBEAT_SECONDS = 1.0
CONFIG_RELOAD_SECONDS = 5.0
POINTING_STATUS_MAX_AGE_SECONDS = 5.0


class IndiGotoGuideService:
    """State-machine host for future INDI GoTo/Guide behavior."""

    def __init__(
        self,
        service_queue: Queue,
        mountcontrol_queue: Optional[Queue],
        shared_state: Any,
    ):
        self.service_queue = service_queue
        self.mountcontrol_queue = mountcontrol_queue
        self.shared_state = shared_state
        self.started_at = time.time()
        self.updated_at = 0.0
        self.last_config_load = 0.0
        self.config_values: dict[str, Any] = {}
        self.last_command: Optional[str] = None
        self.service_state = "starting"
        self.phase = "idle"
        self.wait_reason = ""
        self.active_target_ra: Optional[float] = None
        self.active_target_dec: Optional[float] = None
        self.last_action = "startup"
        self.pointing_status: dict[str, Any] = {"available": False}

    def run(self) -> None:
        logger.info("INDI GoTo/Guide service started")
        self.service_state = "idle"
        running = True
        while running:
            self._reload_config_if_needed()
            self._write_status()
            try:
                command = self.service_queue.get(timeout=HEARTBEAT_SECONDS)
            except queue.Empty:
                continue

            try:
                self._refresh_pointing_status()
                running = self.handle_command(command)
            except Exception:
                logger.exception("INDI GoTo/Guide command failed: %r", command)
                self.service_state = "error"
                self.phase = "error"
                self.wait_reason = "command failed"

        self.service_state = "stopped"
        self.phase = "stopped"
        self._write_status(force=True)
        logger.info("INDI GoTo/Guide service stopped")

    def handle_command(self, command: Any) -> bool:
        if not isinstance(command, dict):
            logger.warning("Ignoring INDI GoTo/Guide command: %r", command)
            return True

        command_type = str(command.get("type", "")).strip()
        self.last_command = command_type or "unknown"

        if command_type == "shutdown":
            return False
        if command_type == "ping":
            self.service_state = "idle"
            self.phase = "idle"
            self.wait_reason = ""
            self.last_action = "ping"
            return True
        if command_type == "goto_target":
            self._handle_goto_target(command)
            return True
        if command_type == "stop_movement":
            self._forward_to_mountcontrol({"type": "stop_movement"})
            self.active_target_ra = None
            self.active_target_dec = None
            self.service_state = "idle"
            self.phase = "idle"
            self.wait_reason = ""
            self.last_action = "stop_movement"
            return True

        logger.info(
            "Queued INDI GoTo/Guide command is not implemented yet: %s",
            command_type,
        )
        self.service_state = "idle"
        self.phase = "idle"
        self.wait_reason = f"command not implemented: {command_type or 'unknown'}"
        return True

    def _handle_goto_target(self, command: dict[str, Any]) -> None:
        try:
            target_ra = float(command["ra"])
            target_dec = float(command["dec"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Invalid INDI GoTo target command: %r", command)
            self.service_state = "error"
            self.phase = "error"
            self.wait_reason = "invalid goto target"
            self.last_action = "goto rejected"
            return

        self.active_target_ra = target_ra
        self.active_target_dec = target_dec
        goto_method = self.config_values.get("indi_goto_method", "indi_mount")

        if goto_method == "indi_mount":
            forwarded = self._forward_to_mountcontrol(
                {
                    "type": "goto_target",
                    "ra": target_ra,
                    "dec": target_dec,
                    "refine_after_goto": bool(
                        command.get("refine_after_goto", False)
                    ),
                    "refine_accuracy_arcmin": command.get(
                        "refine_accuracy_arcmin"
                    ),
                }
            )
            if not forwarded:
                return

            self.service_state = "running"
            self.phase = "indi_mount_goto"
            self.wait_reason = ""
            self.last_action = "forwarded goto_target"
            logger.info(
                "Forwarded INDI Mount GoTo target: RA %.4f Dec %.4f",
                target_ra,
                target_dec,
            )
            return

        pointing = self._refresh_pointing_status()
        if not pointing.get("usable_for_goto"):
            self.service_state = "waiting"
            self.phase = "pifinder_goto_waiting_for_coordinate"
            self.wait_reason = str(
                pointing.get("reason") or "pointing coordinate unavailable"
            )
            self.last_action = "pifinder goto waiting for coordinate"
            logger.info(
                "PiFinder GoTo deferred; coordinate unavailable: %s",
                self.wait_reason,
            )
            return

        self.service_state = "idle"
        self.phase = "pifinder_goto_pending"
        self.wait_reason = "PiFinder GoTo is not implemented yet"
        self.last_action = "pifinder goto deferred"
        logger.info(
            "PiFinder GoTo target deferred until later stage: RA %.4f Dec %.4f",
            target_ra,
            target_dec,
        )

    def _forward_to_mountcontrol(self, command: dict[str, Any]) -> bool:
        if self.mountcontrol_queue is None:
            logger.warning("Cannot forward INDI GoTo/Guide command; queue unavailable")
            self.service_state = "error"
            self.phase = "error"
            self.wait_reason = "mountcontrol queue unavailable"
            self.last_action = "forward failed"
            return False

        self.mountcontrol_queue.put(command)
        return True

    def _refresh_pointing_status(self) -> dict[str, Any]:
        self.pointing_status = self._load_pointing_status()
        return self.pointing_status

    def _load_pointing_status(self) -> dict[str, Any]:
        try:
            with open(POINTING_STATUS_FILE, encoding="utf-8") as status_in:
                raw_status = json.load(status_in)
        except FileNotFoundError:
            return {
                "available": False,
                "fresh": False,
                "usable_for_goto": False,
                "reason": "pointing coordinate status file not found",
            }
        except (json.JSONDecodeError, OSError):
            logger.debug("Could not read pointing coordinate status", exc_info=True)
            return {
                "available": False,
                "fresh": False,
                "usable_for_goto": False,
                "reason": "pointing coordinate status unreadable",
            }

        updated = self._finite_float(raw_status.get("updated"))
        age_seconds = time.time() - updated if updated is not None else None
        fresh = (
            age_seconds is not None
            and age_seconds >= 0.0
            and age_seconds <= POINTING_STATUS_MAX_AGE_SECONDS
        )

        current = self._coordinate_sample_summary(raw_status.get("current"))
        solved = self._coordinate_sample_summary(raw_status.get("solved"))
        imu = self._coordinate_sample_summary(raw_status.get("imu"))
        mount = self._coordinate_sample_summary(raw_status.get("mount"))
        usable_for_goto = bool(
            fresh
            and current.get("valid")
            and current.get("ra") is not None
            and current.get("dec") is not None
            and not self._sample_reports_parked(current)
        )

        reason = ""
        if not fresh:
            reason = "pointing coordinate status is stale"
        elif not current.get("valid"):
            reason = str(current.get("reason") or "current coordinate invalid")
        elif self._sample_reports_parked(current):
            reason = "current coordinate comes from parked mount"

        return {
            "available": True,
            "fresh": fresh,
            "age_seconds": age_seconds,
            "usable_for_goto": usable_for_goto,
            "reason": reason,
            "selected_source": raw_status.get("selected_source"),
            "mode": raw_status.get("mode"),
            "weights": raw_status.get("weights") or {},
            "current": current,
            "solved": solved,
            "imu": imu,
            "mount": mount,
            "health": raw_status.get("health") or {},
            "updated": updated,
        }

    def _coordinate_sample_summary(self, sample: Any) -> dict[str, Any]:
        if not isinstance(sample, dict):
            return {"valid": False, "reason": "sample unavailable"}

        return {
            "valid": bool(sample.get("valid")),
            "source": sample.get("source"),
            "quality": sample.get("quality"),
            "ra": self._finite_float(sample.get("ra")),
            "dec": self._finite_float(sample.get("dec")),
            "alt": self._finite_float(sample.get("alt")),
            "az": self._finite_float(sample.get("az")),
            "reason": sample.get("reason") or "",
            "aligned": bool(sample.get("aligned")),
            "timestamp": self._finite_float(sample.get("timestamp")),
            "metadata": sample.get("metadata") or {},
        }

    def _sample_reports_parked(self, sample: dict[str, Any]) -> bool:
        metadata = sample.get("metadata")
        if not isinstance(metadata, dict):
            return False
        for key in ("park_state", "driver_mount_status"):
            raw = str(metadata.get(key, "")).strip().lower()
            if raw and "park" in raw and "unpark" not in raw:
                return True
        raw_mount_status = str(metadata.get("raw_mount_status", ""))
        return raw_mount_status.startswith("P")

    def _finite_float(self, value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number == number and abs(number) != float("inf") else None

    def _reload_config_if_needed(self) -> None:
        now = time.monotonic()
        if now - self.last_config_load < CONFIG_RELOAD_SECONDS:
            return

        cfg = config.Config()
        cfg.load_config()
        self.config_values = {
            "mount_control": bool(cfg.get_option("mount_control", False)),
            "indi_goto_method": str(
                cfg.get_option("indi_goto_method", "indi_mount")
            ),
            "indi_tracking_guide_enabled": bool(
                cfg.get_option("indi_tracking_guide_enabled", False)
            ),
            "indi_pifinder_goto_near_threshold_deg": float(
                cfg.get_option("indi_pifinder_goto_near_threshold_deg", 1.0)
            ),
            "indi_tracking_guide_threshold_arcmin": float(
                cfg.get_option("indi_tracking_guide_threshold_arcmin", 10.0)
            ),
        }
        self.last_config_load = now

    def _mount_status_summary(self) -> dict[str, Any]:
        try:
            with open(MOUNT_STATUS_FILE, encoding="utf-8") as status_in:
                status = json.load(status_in)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"available": False}

        return {
            "available": True,
            "state": status.get("state"),
            "message": status.get("message"),
            "updated": status.get("updated"),
            "mount_motion_active": status.get("mount_motion_active"),
            "goto_motion_active": status.get("goto_motion_active"),
            "manual_motion_direction": status.get("manual_motion_direction"),
            "target_ra": status.get("target_ra"),
            "target_dec": status.get("target_dec"),
        }

    def _status_payload(self) -> dict[str, Any]:
        return {
            "service_state": self.service_state,
            "phase": self.phase,
            "wait_reason": self.wait_reason,
            "last_command": self.last_command,
            "active_target_ra": self.active_target_ra,
            "active_target_dec": self.active_target_dec,
            "goto_method": self.config_values.get("indi_goto_method", "indi_mount"),
            "tracking_guide_enabled": self.config_values.get(
                "indi_tracking_guide_enabled", False
            ),
            "last_error_arcmin": None,
            "last_action": self.last_action,
            "mountcontrol_queue_available": self.mountcontrol_queue is not None,
            "pointing": self._refresh_pointing_status(),
            "mount_status": self._mount_status_summary(),
            "config": self.config_values,
            "started": self.started_at,
            "updated": time.time(),
        }

    def _write_status(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.updated_at < HEARTBEAT_SECONDS:
            return

        utils.create_path(utils.data_dir)
        payload = self._status_payload()
        tmp_path = STATUS_FILE.with_name(f"{STATUS_FILE.name}.{os.getpid()}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as status_out:
            json.dump(payload, status_out, indent=2, sort_keys=True)
            status_out.flush()
            os.fsync(status_out.fileno())
        tmp_path.replace(STATUS_FILE)
        self.updated_at = now


def run(
    service_queue: Queue,
    mountcontrol_queue: Optional[Queue],
    shared_state: Any,
    log_queue: Queue,
) -> None:
    MultiprocLogging.configurer(log_queue)
    service = IndiGotoGuideService(service_queue, mountcontrol_queue, shared_state)
    service.run()
