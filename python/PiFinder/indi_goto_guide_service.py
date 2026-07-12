#!/usr/bin/python
# -*- coding:utf-8 -*-
"""INDI GoTo/Guide orchestration service.

This service is intentionally separate from ``mountcontrol_indi``.  The mount
control process remains the low-level INDI command executor, while this process
owns the higher-level GoTo/Guide policy and state machine.

Responsibilities:

- ``indi_goto_method = indi_mount``: forward accepted GoTo/abort requests
  straight to the mount-control executor.
- ``indi_goto_method = pifinder``: run the manual-approach loop against
  ``PointingCoordinateService`` coordinates, then sync + final INDI GoTo with a
  bounded correction pass.
- Tracking Guide: when enabled, hold a target with pulse-guide correction, and
  recover from an external disturbance by settling first, then either
  pulse-guiding (small error) or sync + GoTo re-acquisition (large error, gated
  by ``indi_tracking_guide_goto_recovery_enabled``).

All mount motion is issued as small primitive commands on the mount-control
queue; Stop/Abort takes priority in every phase.
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
from typing import Any, Optional, Tuple

from PiFinder import config, utils
from PiFinder.calc_utils import FastAltAz
from PiFinder.multiproclogging import MultiprocLogging


logger = logging.getLogger("IndiGotoGuideService")

STATUS_FILE = utils.data_dir / "indi_goto_guide_status.json"
MOUNT_STATUS_FILE = utils.data_dir / "mount_control_status.json"
POINTING_STATUS_FILE = utils.data_dir / "pointing_coordinate_status.json"

HEARTBEAT_SECONDS = 1.0
# While a PiFinder manual approach / final GoTo is active, tick the service loop
# faster than the idle heartbeat so manual-motion keepalives keep the mount moving
# (the motion lease must not expire between commands). See the "manual-approach
# motion dies between ticks" finding in mf_indi_goto_guide_plan.
PIFINDER_ACTIVE_LOOP_SECONDS = 0.25
CONFIG_RELOAD_SECONDS = 5.0
POINTING_STATUS_MAX_AGE_SECONDS = 5.0
# Motion lease must be comfortably larger than the command interval so the mount
# keeps moving (mount-control stays in state=manual_motion) between keepalives.
PIFINDER_MANUAL_LEASE_SECONDS = 2.5
PIFINDER_MANUAL_TICK_SECONDS = 0.25
# Re-send a fresh manual_movement before mount-control's 10 s continuous-hold
# guard starts refusing keepalives (same idea as the UI hold-to-move restart).
PIFINDER_MANUAL_RESTART_SECONDS = 8.0
PIFINDER_MANUAL_STALL_SECONDS = 5.0
PIFINDER_MANUAL_MIN_COORDINATE_DELTA_DEGREES = 0.01
PIFINDER_FINAL_GOTO_SETTLE_SECONDS = 2.0
PIFINDER_MAX_CORRECTION_GOTOS = 2
PIFINDER_MIN_ERROR_IMPROVEMENT_ARCMIN = 1.0
TRACKING_GUIDE_MAX_RECOVERY_GOTOS = 2

# Alt/Az manual approach: jog directions that move altitude and azimuth. On an
# Alt/Az mount TELESCOPE_MOTION_NS/WE move altitude/azimuth, so the approach jogs
# in Alt/Az (not RA/Dec). Confirmed on hardware: "north" raises altitude, and
# "east" increases azimuth. Flip the azimuth pair if a different mount turns the
# wrong way in azimuth.
ALTAZ_ALT_UP_DIRECTION = "north"
ALTAZ_ALT_DOWN_DIRECTION = "south"
ALTAZ_AZ_INCREASE_DIRECTION = "east"
ALTAZ_AZ_DECREASE_DIRECTION = "west"


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
        self.current_ra: Optional[float] = None
        self.current_dec: Optional[float] = None
        self.last_error_arcmin: Optional[float] = None
        self.goto_plan: Optional[dict[str, Any]] = None
        self.manual_direction: Optional[str] = None
        self.manual_slew_rate: Optional[int] = None
        self.last_manual_command_at = 0.0
        self.last_fresh_motion_at = 0.0
        self.last_coordinate_change_at = 0.0
        self.last_coordinate_ra: Optional[float] = None
        self.last_coordinate_dec: Optional[float] = None
        self.final_goto_sent_at = 0.0
        self.final_goto_idle_since = 0.0
        self.correction_count = 0
        self.previous_goto_error_arcmin: Optional[float] = None
        self.final_sync_sent = False
        self.tracking_target_ra: Optional[float] = None
        self.tracking_target_dec: Optional[float] = None
        self.tracking_guide_active_sent = False
        self.tracking_guide_state = "off"
        self.tracking_guide_last_action = ""
        self.tracking_guide_error_arcmin: Optional[float] = None
        self.tracking_guide_accuracy_arcmin: Optional[float] = None
        self.tracking_guide_recovery_mode = "none"
        self.tracking_guide_recovery_count = 0
        self.tracking_guide_settle_remaining: Optional[float] = None
        self.tracking_motion_ra: Optional[float] = None
        self.tracking_motion_dec: Optional[float] = None
        self.tracking_last_motion_at = 0.0
        self.tracking_recovery_state = "idle"
        self.tracking_recovery_goto_sent_at = 0.0
        self.tracking_recovery_goto_idle_since = 0.0
        self.tracking_recovery_attempts = 0
        self.last_action = "startup"
        self._altaz_debug = ""
        self.pointing_status: dict[str, Any] = {"available": False}

    def run(self) -> None:
        logger.info("INDI GoTo/Guide service started")
        self.service_state = "idle"
        running = True
        while running:
            self._reload_config_if_needed()
            self._tick_state_machine()
            self._tick_tracking_guide()
            self._write_status()
            try:
                command = self.service_queue.get(timeout=self._loop_timeout())
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

    def _loop_timeout(self) -> float:
        # Tick fast while actively driving the mount so manual-motion keepalives
        # keep the lease alive; idle otherwise.
        if self.phase in {
            "manual_approach",
            "final_indi_goto",
            "corrective_indi_goto",
        }:
            return PIFINDER_ACTIVE_LOOP_SECONDS
        return HEARTBEAT_SECONDS

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
        if command_type == "set_tracking_target":
            # Re-arm the tracking-guide target for a GoTo the UI sent straight to
            # mount control (e.g. Object Details key 5). Does not move the mount;
            # it just lets the tracking guide resume auto-correction.
            try:
                self.tracking_target_ra = float(command["ra"]) % 360.0
                self.tracking_target_dec = float(command["dec"])
                self.last_action = "tracking target set"
            except (KeyError, TypeError, ValueError):
                logger.warning("Invalid set_tracking_target command: %r", command)
            return True
        if command_type == "stop_movement":
            self._forward_to_mountcontrol({"type": "stop_movement"})
            self.active_target_ra = None
            self.active_target_dec = None
            self.current_ra = None
            self.current_dec = None
            self.last_error_arcmin = None
            self.goto_plan = None
            self.manual_direction = None
            self.manual_slew_rate = None
            self.last_manual_command_at = 0.0
            self.last_fresh_motion_at = 0.0
            self.final_goto_sent_at = 0.0
            self.final_goto_idle_since = 0.0
            self.correction_count = 0
            self.previous_goto_error_arcmin = None
            self.final_sync_sent = False
            self._disable_tracking_guide("stop command")
            self._reset_tracking_recovery()
            # Clear the tracking target so auto-correction stays off until the
            # next GoTo / tracking start re-arms it.
            self.tracking_target_ra = None
            self.tracking_target_dec = None
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

        # Reset per-GoTo approach state. Without this a second GoTo in the same
        # session (e.g. a fresh SkySafari GoTo after one already completed, or a
        # disturbance recovery) inherits a stale final_sync_sent=True, so
        # _send_final_sync_once() no-ops and the pifinder state machine hangs in
        # final_indi_goto forever (mount control finishes in ~7s but the guide
        # never advances to "complete"). Stale correction_count /
        # previous_goto_error_arcmin would likewise mis-fire the correction
        # limit and the "error did not improve" guard.
        self.correction_count = 0
        self.previous_goto_error_arcmin = None
        self.final_sync_sent = False
        self.final_goto_idle_since = 0.0
        self.final_goto_sent_at = 0.0

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

            self.tracking_target_ra = target_ra
            self.tracking_target_dec = target_dec
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

        block_reason = self._pifinder_goto_block_reason()
        if block_reason:
            self.service_state = "waiting"
            self.phase = "pifinder_goto_blocked"
            self.wait_reason = block_reason
            self.last_action = "pifinder goto blocked"
            logger.info("PiFinder GoTo blocked: %s", self.wait_reason)
            return

        current = self.pointing_status.get("current") or {}
        self.current_ra = self._finite_float(current.get("ra"))
        self.current_dec = self._finite_float(current.get("dec"))
        self.last_error_arcmin = self._angular_error_arcmin(
            self.current_ra,
            self.current_dec,
            target_ra,
            target_dec,
        )
        self.goto_plan = {
            "method": "pifinder",
            "target_ra": target_ra,
            "target_dec": target_dec,
            "current_ra": self.current_ra,
            "current_dec": self.current_dec,
            "error_arcmin": self.last_error_arcmin,
            "current_source": current.get("source"),
            "current_quality": current.get("quality"),
            "near_threshold_degrees": self.config_values.get(
                "indi_pifinder_goto_near_threshold_deg", 1.0
            ),
            "movement_enabled": True,
            "stage": "manual_approach",
            "manual_direction": None,
            "manual_slew_rate": None,
            "lease_seconds": PIFINDER_MANUAL_LEASE_SECONDS,
        }

        # Auto-align at GoTo start: sync the mount to the current PiFinder
        # coordinate so the manual approach navigates with reliable mount readback
        # (PointingCoordinateService current.source = mount) instead of the raw
        # IMU fallback. Without this the mount stays unaligned (source =
        # imu_fallback) and the approach cannot converge.
        if self.current_ra is not None and self.current_dec is not None:
            self._forward_to_mountcontrol(
                {"type": "sync", "ra": self.current_ra, "dec": self.current_dec}
            )
            if self.goto_plan is not None:
                self.goto_plan["start_sync_ra"] = self.current_ra
                self.goto_plan["start_sync_dec"] = self.current_dec

        self.service_state = "running"
        self.phase = "manual_approach"
        self.wait_reason = ""
        self.last_action = "pifinder manual approach planned"
        self._reset_coordinate_progress_tracking()
        logger.info(
            "PiFinder manual approach planned: target RA %.4f Dec %.4f current RA %s "
            "Dec %s error %.2f arcmin",
            target_ra,
            target_dec,
            f"{self.current_ra:.4f}" if self.current_ra is not None else "-",
            f"{self.current_dec:.4f}" if self.current_dec is not None else "-",
            self.last_error_arcmin if self.last_error_arcmin is not None else -1.0,
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

    def _tick_state_machine(self) -> None:
        if self.phase == "manual_approach":
            self._tick_manual_approach()
            return
        if self.phase in {"final_indi_goto", "corrective_indi_goto"}:
            self._tick_final_goto()
            return

    def _tick_manual_approach(self) -> None:
        if self.active_target_ra is None or self.active_target_dec is None:
            self._stop_with_error("manual approach target unavailable")
            return

        block_reason = self._pifinder_goto_block_reason()
        if block_reason:
            self._stop_with_error(block_reason)
            return

        current = self.pointing_status.get("current") or {}
        self.current_ra = self._finite_float(current.get("ra"))
        self.current_dec = self._finite_float(current.get("dec"))
        self.last_error_arcmin = self._angular_error_arcmin(
            self.current_ra,
            self.current_dec,
            self.active_target_ra,
            self.active_target_dec,
        )
        if self.last_error_arcmin is None:
            self._stop_with_error("manual approach error unavailable")
            return

        near_threshold_arcmin = (
            float(self.config_values.get("indi_pifinder_goto_near_threshold_deg", 1.0))
            * 60.0
        )
        self._update_goto_plan()
        if self.last_error_arcmin <= near_threshold_arcmin:
            # Already within the hand-off threshold (includes a GoTo issued while
            # on target): go straight to the final INDI GoTo. The stall guard
            # must NOT run first here -- a stationary on-target coordinate
            # legitimately stops changing and would otherwise be misread as a
            # stalled approach and error out.
            self._forward_to_mountcontrol({"type": "stop_movement"})
            self.manual_direction = None
            self._begin_final_indi_goto()
            return

        # Actively jogging toward the target: from here the coordinate must keep
        # updating, otherwise the manual approach has stalled.
        self._update_coordinate_progress_tracking()
        now = time.monotonic()
        if (
            self.last_coordinate_change_at > 0.0
            and now - self.last_coordinate_change_at > PIFINDER_MANUAL_STALL_SECONDS
        ):
            self._stop_with_error("pointing coordinate stopped updating")
            return

        if now - self.last_manual_command_at < PIFINDER_MANUAL_TICK_SECONDS:
            return

        direction = self._manual_direction_to_target(
            self.current_ra,
            self.current_dec,
            self.active_target_ra,
            self.active_target_dec,
        )
        if not direction:
            self._stop_with_error("manual approach direction unavailable")
            return
        slew_rate = self._manual_slew_rate_for_error(self.last_error_arcmin)

        rate_changed = self.manual_slew_rate != slew_rate
        if rate_changed:
            self._forward_to_mountcontrol({"type": "set_slew_rate", "rate": slew_rate})
            self.manual_slew_rate = slew_rate

        if self.manual_direction and self.manual_direction != direction:
            self._forward_to_mountcontrol({"type": "stop_movement"})

        # A fresh manual_movement (not a keepalive) is needed when:
        # - the direction changed (motion was stopped above),
        # - the slew rate changed — OnStepX halts motion on a rate change, and a
        #   keepalive alone never restarts it (observed on hardware: readback
        #   froze right at the 9->8 transition, tripping the stall guard),
        # - or periodically, because mount-control refuses keepalives after 10 s
        #   of continuous hold (MANUAL_MOTION_MAX_CONTINUOUS_SECONDS); re-send
        #   before that window closes, like the UI hold-to-move restart.
        needs_fresh = (
            self.manual_direction != direction
            or rate_changed
            or now - self.last_fresh_motion_at >= PIFINDER_MANUAL_RESTART_SECONDS
        )
        self._forward_to_mountcontrol(
            {
                "type": "manual_movement" if needs_fresh else "manual_movement_keepalive",
                "direction": direction,
                "lease_seconds": PIFINDER_MANUAL_LEASE_SECONDS,
            }
        )
        if needs_fresh:
            self.last_fresh_motion_at = now
        self.manual_direction = direction
        self.last_manual_command_at = now
        self.last_action = f"pifinder manual approach {direction}"
        self._update_goto_plan()
        logger.debug(
            "PiFinder manual approach %s rate=%s error=%.2f arcmin",
            direction,
            slew_rate,
            self.last_error_arcmin,
        )

    def _stop_with_error(self, reason: str) -> None:
        self._forward_to_mountcontrol({"type": "stop_movement"})
        self.manual_direction = None
        self.service_state = "error"
        self.phase = "error"
        self.wait_reason = reason
        self.last_action = "pifinder manual approach stopped"
        self._update_goto_plan()
        logger.warning("PiFinder manual approach stopped: %s", reason)

    def _begin_final_indi_goto(self) -> None:
        if (
            self.current_ra is None
            or self.current_dec is None
            or self.active_target_ra is None
            or self.active_target_dec is None
        ):
            self._stop_with_error("final INDI GoTo coordinates unavailable")
            return

        self._forward_to_mountcontrol(
            {"type": "sync", "ra": self.current_ra, "dec": self.current_dec}
        )
        self._forward_to_mountcontrol(
            {
                "type": "goto_target",
                "ra": self.active_target_ra,
                "dec": self.active_target_dec,
                "refine_after_goto": False,
            }
        )
        self.service_state = "running"
        self.phase = "final_indi_goto"
        self.wait_reason = ""
        self.last_action = "pifinder final indi goto sent"
        self._update_goto_plan()
        if self.goto_plan is not None:
            self.goto_plan.update(
                {
                    "stage": "final_indi_goto",
                    "sync_ra": self.current_ra,
                    "sync_dec": self.current_dec,
                    "final_goto_ra": self.active_target_ra,
                    "final_goto_dec": self.active_target_dec,
                    "correction_count": self.correction_count,
                    "final_sync_sent": self.final_sync_sent,
                }
            )
        self.final_goto_sent_at = time.monotonic()
        self.final_goto_idle_since = 0.0
        self.previous_goto_error_arcmin = self.last_error_arcmin
        logger.info(
            "PiFinder manual approach reached %.2f arcmin; sync RA %.4f Dec %.4f "
            "then final GoTo RA %.4f Dec %.4f",
            self.last_error_arcmin if self.last_error_arcmin is not None else -1.0,
            self.current_ra,
            self.current_dec,
            self.active_target_ra,
            self.active_target_dec,
        )

    def _tick_final_goto(self) -> None:
        if self.active_target_ra is None or self.active_target_dec is None:
            self._stop_with_error("final GoTo target unavailable")
            return

        mount_status = self._mount_status_summary()
        if not mount_status.get("available"):
            self._stop_with_error("mount status unavailable during final GoTo")
            return
        if self._mount_summary_reports_parked(mount_status):
            self._stop_with_error("mount parked during final GoTo")
            return

        now = time.monotonic()
        if now - self.final_goto_sent_at < PIFINDER_FINAL_GOTO_SETTLE_SECONDS:
            return

        if self._mount_summary_reports_motion(mount_status):
            self.final_goto_idle_since = 0.0
            self.last_action = "waiting for final INDI GoTo"
            return
        if self.final_goto_idle_since == 0.0:
            self.final_goto_idle_since = now
            return
        if now - self.final_goto_idle_since < PIFINDER_FINAL_GOTO_SETTLE_SECONDS:
            return

        pointing = self._refresh_pointing_status()
        if not pointing.get("usable_for_goto"):
            self._stop_with_error(
                str(pointing.get("reason") or "pointing unavailable after final GoTo")
            )
            return

        current = pointing.get("current") or {}
        self.current_ra = self._finite_float(current.get("ra"))
        self.current_dec = self._finite_float(current.get("dec"))
        self.last_error_arcmin = self._angular_error_arcmin(
            self.current_ra,
            self.current_dec,
            self.active_target_ra,
            self.active_target_dec,
        )
        if self.last_error_arcmin is None:
            self._stop_with_error("final GoTo error unavailable")
            return

        near_threshold_arcmin = (
            float(self.config_values.get("indi_pifinder_goto_near_threshold_deg", 1.0))
            * 60.0
        )
        self._update_goto_plan()
        if self.last_error_arcmin <= near_threshold_arcmin:
            self._send_final_sync_once()
            return

        if self.correction_count >= PIFINDER_MAX_CORRECTION_GOTOS:
            self._stop_with_error("final GoTo correction limit reached")
            return
        if (
            self.previous_goto_error_arcmin is not None
            and self.correction_count > 0
            and self.last_error_arcmin
            >= self.previous_goto_error_arcmin - PIFINDER_MIN_ERROR_IMPROVEMENT_ARCMIN
        ):
            self._stop_with_error("final GoTo error did not improve")
            return

        self.previous_goto_error_arcmin = self.last_error_arcmin
        self.correction_count += 1
        self._forward_to_mountcontrol(
            {
                "type": "goto_target",
                "ra": self.active_target_ra,
                "dec": self.active_target_dec,
                "refine_after_goto": False,
            }
        )
        self.phase = "corrective_indi_goto"
        self.service_state = "running"
        self.final_goto_sent_at = now
        self.final_goto_idle_since = 0.0
        self.last_action = "pifinder corrective indi goto sent"
        self._update_goto_plan()
        logger.info(
            "PiFinder corrective GoTo %s/%s sent; error %.2f arcmin",
            self.correction_count,
            PIFINDER_MAX_CORRECTION_GOTOS,
            self.last_error_arcmin,
        )

    def _send_final_sync_once(self) -> None:
        if self.final_sync_sent:
            return
        self._forward_to_mountcontrol(
            {
                "type": "sync",
                "ra": self.active_target_ra,
                "dec": self.active_target_dec,
            }
        )
        self.final_sync_sent = True
        self.tracking_target_ra = self.active_target_ra
        self.tracking_target_dec = self.active_target_dec
        self.service_state = "idle"
        self.phase = "complete"
        self.wait_reason = ""
        self.last_action = "pifinder final sync complete"
        self._update_goto_plan()
        if self.goto_plan is not None:
            self.goto_plan.update(
                {
                    "stage": "complete",
                    "final_sync_ra": self.active_target_ra,
                    "final_sync_dec": self.active_target_dec,
                    "final_sync_sent": True,
                    "tracking_target_ra": self.tracking_target_ra,
                    "tracking_target_dec": self.tracking_target_dec,
                }
            )
        logger.info(
            "PiFinder GoTo complete; final sync target RA %.4f Dec %.4f "
            "error %.2f arcmin",
            self.active_target_ra,
            self.active_target_dec,
            self.last_error_arcmin if self.last_error_arcmin is not None else -1.0,
        )

    def _tick_tracking_guide(self) -> None:
        enabled = bool(self.config_values.get("indi_tracking_guide_enabled", False))
        if not enabled:
            self._disable_tracking_guide("disabled in config")
            self._reset_tracking_recovery()
            self.tracking_guide_state = "off"
            return

        if self.tracking_target_ra is None or self.tracking_target_dec is None:
            self._disable_tracking_guide("no tracking target")
            self._reset_tracking_recovery()
            self.tracking_guide_state = "waiting_target"
            self.tracking_guide_last_action = "waiting for tracking target"
            return

        if self.phase in {
            "manual_approach",
            "final_indi_goto",
            "corrective_indi_goto",
        }:
            self._disable_tracking_guide(f"paused during {self.phase}")
            self._reset_tracking_recovery()
            self.tracking_guide_state = "paused"
            self.tracking_guide_last_action = f"paused during {self.phase}"
            return

        mount_status = self._mount_status_summary()
        if not mount_status.get("available"):
            self.tracking_guide_state = "waiting_mount"
            self.tracking_guide_last_action = "mount status unavailable"
            return
        if self._mount_summary_reports_parked(mount_status):
            self._disable_tracking_guide("mount parked")
            self._reset_tracking_recovery()
            self.tracking_guide_state = "paused"
            self.tracking_guide_last_action = "paused because mount is parked"
            return

        # Drive an in-progress sync + GoTo recovery to completion first, even
        # though the mount reports motion during its own recovery slew.
        if self.tracking_recovery_state == "goto_wait":
            self._tick_tracking_recovery_goto(mount_status)
            return

        # Any other slew/manual motion (not our recovery) suspends correction.
        if self._mount_summary_reports_motion(mount_status):
            self._disable_tracking_guide("mount motion")
            self.tracking_motion_ra = None
            self.tracking_motion_dec = None
            self.tracking_last_motion_at = time.monotonic()
            self.tracking_guide_state = "paused"
            self.tracking_guide_last_action = "paused during mount motion"
            return

        # Coordinate and its usability are decided by PointingCoordinateService;
        # Tracking Guide trusts usable_for_goto and makes no solve/IMU judgment.
        pointing = self._refresh_pointing_status()
        current = pointing.get("current") or {}
        current_ra = self._finite_float(current.get("ra"))
        current_dec = self._finite_float(current.get("dec"))
        if (
            not pointing.get("usable_for_goto")
            or current_ra is None
            or current_dec is None
        ):
            self._disable_tracking_guide("pointing coordinate unavailable")
            self.tracking_guide_error_arcmin = None
            self.tracking_guide_state = "waiting_coordinate"
            self.tracking_guide_last_action = str(
                pointing.get("reason") or "pointing coordinate unavailable"
            )
            return

        self.tracking_guide_error_arcmin = self._angular_error_arcmin(
            current_ra,
            current_dec,
            self.tracking_target_ra,
            self.tracking_target_dec,
        )

        # Disturbance detection: while the scope is being moved, suspend all
        # correction and wait for it to stop.
        if self._tracking_coordinate_moving(current_ra, current_dec):
            self._disable_tracking_guide("scope moving")
            self.tracking_recovery_attempts = 0
            self.tracking_guide_recovery_mode = "none"
            self.tracking_guide_state = "disturbed"
            self.tracking_guide_last_action = "suspended: coordinate moving"
            return

        # Settle wait: coordinate must stay stable before we correct again.
        settle_seconds = float(
            self.config_values.get("indi_tracking_guide_settle_seconds", 2.0)
        )
        now = time.monotonic()
        stable_for = (
            now - self.tracking_last_motion_at
            if self.tracking_last_motion_at
            else settle_seconds
        )
        self.tracking_guide_settle_remaining = max(0.0, settle_seconds - stable_for)
        if stable_for < settle_seconds:
            self.tracking_guide_state = "settling"
            self.tracking_guide_last_action = (
                f"settling {self.tracking_guide_settle_remaining:.1f}s"
            )
            return

        if self.tracking_guide_error_arcmin is None:
            self.tracking_guide_state = "waiting_coordinate"
            self.tracking_guide_last_action = "tracking error unavailable"
            return

        goto_threshold_arcmin = (
            float(self.config_values.get("indi_tracking_guide_goto_threshold_deg", 3.0))
            * 60.0
        )
        goto_recovery_enabled = bool(
            self.config_values.get("indi_tracking_guide_goto_recovery_enabled", False)
        )

        # Large error with recovery enabled: sync mount to current, GoTo target.
        if (
            self.tracking_guide_error_arcmin > goto_threshold_arcmin
            and goto_recovery_enabled
        ):
            self._begin_tracking_recovery_goto(current_ra, current_dec)
            return

        # Otherwise pulse-guide fine correction. Within the envelope this closes
        # the error; with recovery Off it is the only tool and pulses slowly
        # toward the target without any mount slew.
        accuracy = float(
            self.config_values.get("indi_tracking_guide_threshold_arcmin", 10.0)
        )
        self._enable_pulse_correction(accuracy)
        self.tracking_recovery_attempts = 0
        self.tracking_guide_recovery_mode = "pulse"

        mount_state = str(mount_status.get("state", "")).strip().lower()
        if mount_state == "guide_correction_failed":
            self.tracking_guide_state = "failed"
            self.tracking_guide_last_action = str(
                mount_status.get("message") or "guide correction failed"
            )
        else:
            self.tracking_guide_state = "enabled"
            if self.tracking_guide_error_arcmin > goto_threshold_arcmin:
                self.tracking_guide_last_action = (
                    "large error; goto recovery off, pulse only"
                )

    def _enable_pulse_correction(self, accuracy: float) -> None:
        target_changed = not self.tracking_guide_active_sent
        accuracy_changed = self.tracking_guide_accuracy_arcmin != accuracy
        if target_changed or accuracy_changed:
            self._forward_to_mountcontrol(
                {
                    "type": "toggle_guide_correction",
                    "enabled": True,
                    "target_ra": self.tracking_target_ra,
                    "target_dec": self.tracking_target_dec,
                    "accuracy_arcmin": accuracy,
                }
            )
            self.tracking_guide_active_sent = True
            self.tracking_guide_accuracy_arcmin = accuracy
            self.tracking_guide_last_action = "guide correction enabled"

    def _tracking_coordinate_moving(
        self, current_ra: float, current_dec: float
    ) -> bool:
        motion_arcmin = float(
            self.config_values.get("indi_tracking_guide_motion_arcmin", 15.0)
        )
        previous_ra = self.tracking_motion_ra
        previous_dec = self.tracking_motion_dec
        self.tracking_motion_ra = current_ra
        self.tracking_motion_dec = current_dec
        if previous_ra is None or previous_dec is None:
            # First sample after acquisition; require a fresh settle window.
            self.tracking_last_motion_at = time.monotonic()
            return False
        delta = self._angular_error_arcmin(
            previous_ra, previous_dec, current_ra, current_dec
        )
        if delta is not None and delta >= motion_arcmin:
            self.tracking_last_motion_at = time.monotonic()
            return True
        return False

    def _begin_tracking_recovery_goto(
        self, current_ra: float, current_dec: float
    ) -> None:
        if self.tracking_recovery_attempts >= TRACKING_GUIDE_MAX_RECOVERY_GOTOS:
            self._disable_tracking_guide("goto recovery limit reached")
            self.tracking_recovery_state = "idle"
            self.tracking_guide_recovery_mode = "goto"
            self.tracking_guide_state = "failed"
            self.tracking_guide_last_action = "goto recovery limit reached"
            return

        self._disable_tracking_guide("starting goto recovery")
        self._forward_to_mountcontrol(
            {"type": "sync", "ra": current_ra, "dec": current_dec}
        )
        self._forward_to_mountcontrol(
            {
                "type": "goto_target",
                "ra": self.tracking_target_ra,
                "dec": self.tracking_target_dec,
                "refine_after_goto": False,
            }
        )
        self.tracking_recovery_attempts += 1
        self.tracking_guide_recovery_count += 1
        self.tracking_recovery_state = "goto_wait"
        self.tracking_recovery_goto_sent_at = time.monotonic()
        self.tracking_recovery_goto_idle_since = 0.0
        self.tracking_guide_recovery_mode = "goto"
        self.tracking_guide_state = "recovering_goto"
        self.tracking_guide_last_action = (
            f"goto recovery {self.tracking_recovery_attempts}"
            f"/{TRACKING_GUIDE_MAX_RECOVERY_GOTOS}: sync+goto sent"
        )
        logger.info(
            "Tracking guide recovery %s/%s: sync RA %.4f Dec %.4f then GoTo "
            "RA %.4f Dec %.4f (error %.2f arcmin)",
            self.tracking_recovery_attempts,
            TRACKING_GUIDE_MAX_RECOVERY_GOTOS,
            current_ra,
            current_dec,
            self.tracking_target_ra,
            self.tracking_target_dec,
            self.tracking_guide_error_arcmin
            if self.tracking_guide_error_arcmin is not None
            else -1.0,
        )

    def _tick_tracking_recovery_goto(self, mount_status: dict[str, Any]) -> None:
        self.tracking_guide_state = "recovering_goto"
        now = time.monotonic()
        if (
            now - self.tracking_recovery_goto_sent_at
            < PIFINDER_FINAL_GOTO_SETTLE_SECONDS
        ):
            self.tracking_guide_last_action = "recovery goto settling"
            return
        if self._mount_summary_reports_motion(mount_status):
            self.tracking_recovery_goto_idle_since = 0.0
            self.tracking_guide_last_action = "waiting for recovery goto"
            return
        if self.tracking_recovery_goto_idle_since == 0.0:
            self.tracking_recovery_goto_idle_since = now
            return
        if (
            now - self.tracking_recovery_goto_idle_since
            < PIFINDER_FINAL_GOTO_SETTLE_SECONDS
        ):
            return
        # Recovery GoTo finished; re-baseline and re-measure on the next tick.
        self.tracking_recovery_state = "idle"
        self.tracking_motion_ra = None
        self.tracking_motion_dec = None
        self.tracking_last_motion_at = now
        self.tracking_guide_last_action = "recovery goto complete"

    def _reset_tracking_recovery(self) -> None:
        self.tracking_motion_ra = None
        self.tracking_motion_dec = None
        self.tracking_last_motion_at = 0.0
        self.tracking_recovery_state = "idle"
        self.tracking_recovery_goto_sent_at = 0.0
        self.tracking_recovery_goto_idle_since = 0.0
        self.tracking_recovery_attempts = 0
        self.tracking_guide_recovery_mode = "none"
        self.tracking_guide_recovery_count = 0
        self.tracking_guide_settle_remaining = None
        self.tracking_guide_error_arcmin = None

    def _disable_tracking_guide(self, reason: str) -> None:
        if self.tracking_guide_active_sent:
            self._forward_to_mountcontrol(
                {"type": "toggle_guide_correction", "enabled": False}
            )
        self.tracking_guide_active_sent = False
        self.tracking_guide_accuracy_arcmin = None
        self.tracking_guide_last_action = reason

    def _reset_coordinate_progress_tracking(self) -> None:
        self.last_coordinate_ra = self.current_ra
        self.last_coordinate_dec = self.current_dec
        self.last_coordinate_change_at = time.monotonic()

    def _update_coordinate_progress_tracking(self) -> None:
        if self.current_ra is None or self.current_dec is None:
            return
        if self.last_coordinate_ra is None or self.last_coordinate_dec is None:
            self._reset_coordinate_progress_tracking()
            return
        delta = self._angular_error_arcmin(
            self.last_coordinate_ra,
            self.last_coordinate_dec,
            self.current_ra,
            self.current_dec,
        )
        if (
            delta is not None
            and delta / 60.0 >= PIFINDER_MANUAL_MIN_COORDINATE_DELTA_DEGREES
        ):
            self.last_coordinate_ra = self.current_ra
            self.last_coordinate_dec = self.current_dec
            self.last_coordinate_change_at = time.monotonic()

    def _update_goto_plan(self) -> None:
        if self.goto_plan is None:
            return
        self.goto_plan.update(
            {
                "current_ra": self.current_ra,
                "current_dec": self.current_dec,
                "error_arcmin": self.last_error_arcmin,
                "manual_direction": self.manual_direction,
                "manual_slew_rate": self.manual_slew_rate,
                "phase": self.phase,
                "last_coordinate_change_age_seconds": (
                    time.monotonic() - self.last_coordinate_change_at
                    if self.last_coordinate_change_at
                    else None
                ),
            }
        )

    def _manual_direction_to_target(
        self,
        current_ra: Optional[float],
        current_dec: Optional[float],
        target_ra: Optional[float],
        target_dec: Optional[float],
    ) -> Optional[str]:
        if (
            current_ra is None
            or current_dec is None
            or target_ra is None
            or target_dec is None
        ):
            return None

        component_threshold = 0.15

        # On an Alt/Az mount the manual-motion buttons move altitude/azimuth, so
        # jog in Alt/Az. Fall back to RA/Dec if the conversion is unavailable
        # (no location/time) or for EQ mounts.
        if self._is_altaz_mount():
            errors = self._altaz_errors(
                current_ra, current_dec, target_ra, target_dec
            )
            if errors is not None:
                alt_error, az_error = errors
                ns = ""
                ew = ""
                if alt_error > component_threshold:
                    ns = ALTAZ_ALT_UP_DIRECTION
                elif alt_error < -component_threshold:
                    ns = ALTAZ_ALT_DOWN_DIRECTION
                if az_error > component_threshold:
                    ew = ALTAZ_AZ_INCREASE_DIRECTION
                elif az_error < -component_threshold:
                    ew = ALTAZ_AZ_DECREASE_DIRECTION
                if ns and ew:
                    return ns + ew
                return ns or ew or None

        ra_delta = self._wrap_angle_delta_degrees(target_ra - current_ra)
        east_west_error = ra_delta * math.cos(math.radians(current_dec))
        north_south_error = target_dec - current_dec

        ns = ""
        ew = ""
        if north_south_error > component_threshold:
            ns = "north"
        elif north_south_error < -component_threshold:
            ns = "south"
        if east_west_error > component_threshold:
            ew = "east"
        elif east_west_error < -component_threshold:
            ew = "west"

        if ns and ew:
            return ns + ew
        return ns or ew or None

    def _is_altaz_mount(self) -> bool:
        mount_type = str(self.config_values.get("mount_type", "Alt/Az")).strip().lower()
        return "alt" in mount_type and "az" in mount_type

    def _altaz_converter(self) -> Optional[FastAltAz]:
        try:
            location = self.shared_state.location()
            dt = self.shared_state.datetime()
        except Exception as exc:
            self._altaz_debug = f"shared_state error: {exc!r}"
            return None
        dt_source = "shared"
        if dt is None:
            # No GPS/manual time in shared state yet. The device keeps its
            # system clock synced (gps_time_sync helper / NTP), so fall back to
            # system UTC rather than dropping to the RA/Dec jog logic, which is
            # plain wrong on an Alt/Az mount.
            dt = datetime.now(timezone.utc)
            dt_source = "system_utc"

        # A shared-state Location without a lock is the zeroed default
        # (lat=lon=0), which would flip the computed jog directions. Prefer a
        # locked shared location; otherwise fall back to the saved default
        # location from config.
        lat = lon = None
        loc_source = "shared"
        if location is not None and bool(getattr(location, "lock", False)):
            lat = getattr(location, "lat", None)
            lon = getattr(location, "lon", None)
        if lat is None or lon is None:
            default_loc = self.config_values.get("default_location")
            if isinstance(default_loc, dict):
                lat = self._finite_float(default_loc.get("latitude"))
                lon = self._finite_float(default_loc.get("longitude"))
                loc_source = "config_default"
        if lat is None or lon is None:
            self._altaz_debug = "no usable location (no lock, no default)"
            return None
        try:
            conv = FastAltAz(lat, lon, dt)
            self._altaz_debug = (
                f"ok lat={lat:.3f} lon={lon:.3f} ({loc_source}) "
                f"dt={dt.isoformat()} ({dt_source})"
            )
            return conv
        except Exception as exc:
            self._altaz_debug = f"FastAltAz error: {exc!r}"
            return None

    def _altaz_errors(
        self,
        current_ra: float,
        current_dec: float,
        target_ra: float,
        target_dec: float,
    ) -> Optional[Tuple[float, float]]:
        """Altitude/azimuth error (target - current) for Alt/Az jogging.

        Both points use the same location/time, so the relative alt/az direction
        is robust even if the absolute location is only approximate. Returns None
        if the conversion is unavailable (caller falls back to RA/Dec).
        """
        converter = self._altaz_converter()
        if converter is None:
            return None
        try:
            cur_alt, cur_az = converter.radec_to_altaz(
                current_ra, current_dec, alt_only=False
            )
            tgt_alt, tgt_az = converter.radec_to_altaz(
                target_ra, target_dec, alt_only=False
            )
        except Exception:
            logger.debug("Alt/Az conversion failed", exc_info=True)
            return None
        if None in (cur_alt, cur_az, tgt_alt, tgt_az):
            return None
        alt_error = tgt_alt - cur_alt
        az_error = self._wrap_angle_delta_degrees(tgt_az - cur_az)
        return alt_error, az_error

    def _manual_slew_rate_for_error(self, error_arcmin: float) -> int:
        # The approach only needs to get inside the ~1 deg near threshold; the
        # final INDI GoTo handles precision. Keep the last leg fast (48x) — the
        # earlier 20x leg made the final degree crawl (~5'/s).
        error_degrees = error_arcmin / 60.0
        if error_degrees >= 10.0:
            return 9
        if error_degrees >= 3.0:
            return 8
        if error_degrees >= 1.0:
            return 7
        return 6

    def _refresh_pointing_status(self) -> dict[str, Any]:
        self.pointing_status = self._load_pointing_status()
        return self.pointing_status

    def _pifinder_goto_block_reason(self) -> str:
        pointing = self._refresh_pointing_status()
        if not pointing.get("usable_for_goto"):
            return str(pointing.get("reason") or "pointing coordinate unavailable")

        mount_status = self._mount_status_summary()
        if not mount_status.get("available"):
            return "mount status unavailable"
        if self._mount_summary_reports_parked(mount_status):
            return "mount is parked"

        current = pointing.get("current") or {}
        if self._finite_float(current.get("ra")) is None:
            return "current RA unavailable"
        if self._finite_float(current.get("dec")) is None:
            return "current Dec unavailable"

        return ""

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

    def _wrap_angle_delta_degrees(self, delta: float) -> float:
        return ((delta + 180.0) % 360.0) - 180.0

    def _mount_summary_reports_parked(self, status: dict[str, Any]) -> bool:
        for key in ("park_state", "driver_mount_status"):
            raw = str(status.get(key, "")).strip().lower()
            if raw and "park" in raw and "unpark" not in raw:
                return True
        raw_mount_status = str(status.get("raw_mount_status", ""))
        return raw_mount_status.startswith("P")

    def _mount_summary_reports_motion(self, status: dict[str, Any]) -> bool:
        if bool(status.get("mount_motion_active")):
            return True
        if bool(status.get("goto_motion_active")):
            return True
        if status.get("manual_motion_direction"):
            return True
        state = str(status.get("state", "")).strip().lower()
        return any(token in state for token in ("slew", "goto", "moving", "motion"))

    def _angular_error_arcmin(
        self,
        current_ra: Optional[float],
        current_dec: Optional[float],
        target_ra: Optional[float],
        target_dec: Optional[float],
    ) -> Optional[float]:
        if (
            current_ra is None
            or current_dec is None
            or target_ra is None
            or target_dec is None
        ):
            return None

        ra_a = math.radians(current_ra)
        dec_a = math.radians(current_dec)
        ra_b = math.radians(target_ra)
        dec_b = math.radians(target_dec)
        cos_sep = math.sin(dec_a) * math.sin(dec_b) + math.cos(dec_a) * math.cos(
            dec_b
        ) * math.cos(ra_a - ra_b)
        sep_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))
        return sep_deg * 60.0

    def _finite_float(self, value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

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
            "indi_tracking_guide_settle_seconds": float(
                cfg.get_option("indi_tracking_guide_settle_seconds", 2.0)
            ),
            "indi_tracking_guide_motion_arcmin": float(
                cfg.get_option("indi_tracking_guide_motion_arcmin", 15.0)
            ),
            "indi_tracking_guide_goto_recovery_enabled": bool(
                cfg.get_option("indi_tracking_guide_goto_recovery_enabled", False)
            ),
            "indi_tracking_guide_goto_threshold_deg": float(
                cfg.get_option("indi_tracking_guide_goto_threshold_deg", 3.0)
            ),
            "mount_type": str(cfg.get_option("mount_type", "Alt/Az")),
        }
        locations = cfg.get_option("locations", {}) or {}
        loc_list = (
            locations.get("locations", []) if isinstance(locations, dict) else []
        )
        self.config_values["default_location"] = next(
            (loc for loc in loc_list if loc.get("is_default")),
            loc_list[0] if loc_list else None,
        )
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
            "device": status.get("device"),
            "park_state": status.get("park_state"),
            "driver_mount_status": status.get("driver_mount_status"),
            "raw_mount_status": status.get("raw_mount_status"),
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
            "current_ra": self.current_ra,
            "current_dec": self.current_dec,
            "manual_direction": self.manual_direction,
            "manual_slew_rate": self.manual_slew_rate,
            "correction_count": self.correction_count,
            "final_sync_sent": self.final_sync_sent,
            "tracking_target_ra": self.tracking_target_ra,
            "tracking_target_dec": self.tracking_target_dec,
            "tracking_guide_state": self.tracking_guide_state,
            "tracking_guide_active_sent": self.tracking_guide_active_sent,
            "tracking_guide_last_action": self.tracking_guide_last_action,
            "tracking_guide_error_arcmin": self.tracking_guide_error_arcmin,
            "tracking_guide_accuracy_arcmin": self.tracking_guide_accuracy_arcmin,
            "tracking_guide_recovery_mode": self.tracking_guide_recovery_mode,
            "tracking_guide_recovery_count": self.tracking_guide_recovery_count,
            "tracking_guide_settle_remaining": self.tracking_guide_settle_remaining,
            "goto_method": self.config_values.get("indi_goto_method", "indi_mount"),
            "tracking_guide_enabled": self.config_values.get(
                "indi_tracking_guide_enabled", False
            ),
            "last_error_arcmin": self.last_error_arcmin,
            "goto_plan": self.goto_plan,
            "last_action": self.last_action,
            "altaz_debug": self._altaz_debug,
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
