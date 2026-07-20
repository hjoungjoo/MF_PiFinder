#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tracking-frequency policy for GoTo / push targets
=================================================

Shared decision logic for "what tracking frequency fits this target":

- Solar-system targets (obj_type ``Pla``) get an ephemeris feed-forward
  rate (finite-difference dRA/dt over 10 minutes -> :ST frequency).
- Static targets track at sidereal: if a non-sidereal frequency is
  currently active on the mount it is reset, otherwise nothing is sent.

Used by the LCD GoTo key (ui/base.py, keypad 5) so it behaves exactly like
the web catalog's "Push to PiFinder". Comets are not special-cased yet;
they fall into the reset path (their rates are far smaller than the Moon's).
"""

import json
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from PiFinder import nonsidereal, utils

logger = logging.getLogger("TrackFreqPolicy")

PLANET_RATE_SAMPLE_SECONDS = 600.0


def _mount_status() -> Dict[str, Any]:
    try:
        with open(
            utils.runtime_dir / "mount_control_status.json", "r", encoding="utf-8"
        ) as status_in:
            return json.load(status_in)
    except (OSError, ValueError):
        return {}


def _observation_time(shared_state):
    dt = None
    if shared_state is not None:
        try:
            dt = shared_state.datetime()
        except Exception:
            dt = None
    if dt is None:
        from datetime import datetime, timezone

        dt = datetime.now(timezone.utc)
    return dt


def planet_dra_dt(name: str, shared_state) -> Optional[float]:
    """Finite-difference dRA/dt (RA-coordinate arcsec/s) for a solar-system
    body by name, or None if it cannot be computed."""
    try:
        from PiFinder.calc_utils import sf_utils
    except Exception:
        return None

    key = name.strip().upper()
    if not key:
        return None
    dt = _observation_time(shared_state)

    if sf_utils.observer_loc is None and shared_state is not None:
        try:
            location = shared_state.location()
            if location is not None:
                sf_utils.set_location(
                    location.lat, location.lon,
                    getattr(location, "altitude", 0.0) or 0.0,
                )
        except Exception:
            return None

    try:
        first = sf_utils.calc_planets(dt).get(key)
        second = sf_utils.calc_planets(
            dt + timedelta(seconds=PLANET_RATE_SAMPLE_SECONDS)
        ).get(key)
    except Exception:
        logger.exception("Planet rate computation failed for %s", key)
        return None
    if first is None or second is None:
        return None
    return nonsidereal.ra_rate_from_positions(
        first["radec"][0], second["radec"][0], PLANET_RATE_SAMPLE_SECONDS
    )


def track_freq_command_for_target(
    target_object, shared_state
) -> Optional[Dict[str, Any]]:
    """The mount-control command that sets the right tracking frequency for
    a target about to be GoTo'd, or None when nothing needs to change.

    Same policy as the web catalog push path.
    """
    if target_object is not None and getattr(target_object, "obj_type", "") == "Pla":
        names = getattr(target_object, "names", None) or []
        name = str(names[0]) if names else ""
        rate = planet_dra_dt(name, shared_state)
        if rate is not None:
            hz, was_clamped = nonsidereal.clamp_hz(nonsidereal.hz_from_offset(rate))
            if was_clamped:
                logger.warning(
                    "Track frequency for %s clamped to %.5f Hz", name, hz
                )
            return {"type": "set_track_freq", "hz": hz, "label": name.capitalize()}
        # Rate unavailable: leave the mount's current frequency untouched.
        return None

    if _mount_status().get("track_freq_hz") is not None:
        return {"type": "reset_track_freq"}
    return None
