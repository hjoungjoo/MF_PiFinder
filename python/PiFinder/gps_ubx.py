#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
This module is for GPS related functions
"""

import asyncio
from PiFinder.multiproclogging import MultiprocLogging
from PiFinder.gps_ubx_parser import UBXParser
import logging

logger = logging.getLogger("GPS.parser")
# Latest satellite telemetry: seen (signal-locked), used (in fix),
# in_view (all listed by the receiver), top_cno (strongest C/N0 values).
sats = [0, 0, 0, ()]


def _top_cno(satellites):
    values = sorted(
        (int(s["signal"]) for s in satellites if s.get("signal")),
        reverse=True,
    )
    return tuple(values[:4])


MAX_GPS_ERROR = 50000  # 50 km


def _time_accuracy_ns(msg):
    if "tAcc_ns" in msg:
        try:
            return int(msg["tAcc_ns"])
        except (TypeError, ValueError):
            return -1

    tacc = msg.get("tAcc", -1)
    try:
        tacc_value = float(tacc)
    except (TypeError, ValueError):
        return -1

    if tacc_value < 0:
        return -1
    return int(round(tacc_value * 1_000_000_000))


def _gps_time_message(msg, info=None):
    if not msg.get("time"):
        return None

    valid = bool(msg.get("valid", True))
    return (
        "time" if valid else "time_sample",
        {
            "time": msg["time"],
            "tAcc": _time_accuracy_ns(msg),
            "source": "GPS" if not info else info,
            "message_class": msg.get("class", "unknown"),
            "lock_type": msg.get("mode"),
            "valid": valid,
        },
    )


async def process_messages(
    parser_iterator, gps_queue, console_queue, error_info, wait=0, info=None
):
    gps_locked = False
    got_sat_update = False  # Track if we got a NAV-SAT message this cycle

    async for msg in parser_iterator():
        msg_class = msg.get("class", "")
        logger.debug("GPS: %s: %s", msg_class, msg)

        if msg_class == "NAV-DOP":
            error_info["error_2d"] = msg["hdop"]
            error_info["error_3d"] = msg["pdop"]

        elif msg_class == "NAV-SVINFO" and not got_sat_update:
            # Fallback satellite info if NAV-SAT not available
            if "nSat" in msg:
                sats[0] = msg["nSat"]  # seen (code-locked)
                sats[1] = msg["uSat"]  # used
                sats[2] = msg.get("in_view", msg["nSat"])  # all listed
                sats[3] = _top_cno(msg.get("satellites", []))
                gps_queue.put(("satellites", tuple(sats)))
                logger.debug(
                    "Number of sats (SVINFO) seen: %i, used: %i, in-view: %i",
                    sats[0],
                    sats[1],
                    sats[2],
                )

        elif msg_class == "NAV-SAT":
            # Preferred satellite info source - not seen in the current pifinder gps versions
            got_sat_update = True
            sats[0] = msg["nSat"]  # seen (code-locked)
            sats[1] = sum(
                1 for sat in msg.get("satellites", []) if sat.get("used", False)
            )
            sats[2] = msg.get("in_view", msg["nSat"])  # all listed
            sats[3] = _top_cno(msg.get("satellites", []))
            gps_queue.put(("satellites", tuple(sats)))
            logger.debug(
                "Number of sats (NAV-SAT) seen: %i, used: %i, in-view: %i",
                sats[0],
                sats[1],
                sats[2],
            )

        elif msg_class == "NAV-SOL":
            # only source of truth for satellites used in a FIX
            if "satellites" in msg:
                sats_used = msg["satellites"]
                sats[1] = sats_used
                gps_queue.put(("satellites", tuple(sats)))

            if all(k in msg for k in ["lat", "lon", "altHAE", "ecefpAcc", "mode"]):
                if not gps_locked and msg["ecefpAcc"] < MAX_GPS_ERROR:
                    gps_locked = True
                    console_queue.put("GPS: Locked")
                    logger.debug("GPS locked")
                gps_queue.put(
                    (
                        "fix",
                        {
                            "lat": msg["lat"],
                            "lon": msg["lon"],
                            "altitude": msg["altHAE"],
                            "source": "GPS" if not info else info,
                            "lock": gps_locked,
                            "lock_type": msg["mode"],
                            "error_in_m": msg["ecefpAcc"],
                        },
                    )
                )
                logger.debug("GPS fix: %s", msg)

        elif msg_class == "NAV-TIMEGPS":
            time_msg = _gps_time_message(msg, info=info)
            if time_msg is not None:
                gps_queue.put(time_msg)
            else:
                logger.debug("TIMEGPS message has no time: %s", msg)

        elif msg_class == "NAV-PVT":
            # Upstream #524: on protVer>=15 receivers gpsd sends NAV-PVT
            # instead of NAV-SOL, so surface numSV as the used count here.
            if "numSV" in msg:
                sats[1] = msg["numSV"]
                gps_queue.put(("satellites", tuple(sats)))
            # MF: NAV-PVT also carries the time we forward via the helper.
            time_msg = _gps_time_message(msg, info=info)
            if time_msg is not None:
                gps_queue.put(time_msg)

            if all(k in msg for k in ["lat", "lon", "altHAE", "hAcc", "vAcc"]):
                if not gps_locked and msg["hAcc"] < MAX_GPS_ERROR:
                    gps_locked = True
                    console_queue.put("GPS: Locked")
                    logger.info("GPS locked")
                gps_queue.put(
                    (
                        "fix",
                        {
                            "lat": msg["lat"],
                            "lon": msg["lon"],
                            "altitude": msg["altHAE"],
                            "source": "GPS",
                            "lock": gps_locked,
                            "lock_type": msg["mode"],
                            "error_in_m": msg["hAcc"],
                        },
                    )
                )
                logger.debug("GPS fix: %s", msg)

        # Wait a bit more on processing, if messages pile up in the queue
        if gps_queue.qsize() > 50:
            await asyncio.sleep(0.7)
        elif gps_queue.qsize() > 10:
            await asyncio.sleep(0.1)
        await asyncio.sleep(wait)


async def gps_main(gps_queue, console_queue, log_queue, inject_parser=None):
    MultiprocLogging.configurer(log_queue)
    logger.info("Using UBX GPS code")
    error_info = {"error_2d": 123_456, "error_3d": 123_456}

    while True:
        try:
            if inject_parser:  # dependency injection for testing, see gps_fake.py
                parser = inject_parser
            else:
                parser = await UBXParser.connect(log_queue, host="127.0.0.1", port=2947)
            await process_messages(
                parser.parse_messages, gps_queue, console_queue, error_info
            )
        except Exception as e:
            logger.error(f"Error in GPS monitor: {e}")
            await asyncio.sleep(5)


def gps_monitor(gps_queue, console_queue, log_queue):
    asyncio.run(gps_main(gps_queue, console_queue, log_queue))
