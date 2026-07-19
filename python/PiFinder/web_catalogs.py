#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PiFinder Web Catalogs
=====================
Registers the /catalogs pages and JSON APIs on PiFinder's Flask web server.

Usage (mirrors PiFinder.api_extensions):
    Add the following at the end of Server.__init__ in
    PiFinder/python/PiFinder/server.py, before run() is called:

        from PiFinder.web_catalogs import register_catalog_routes
        register_catalog_routes(app, self)

Design notes (docs/mf_web_catalogs_dev_ko.md):
- All functionality lives in this module + views/catalogs/* templates; the
  original sources only gain the registration hook and two nav links.
- The objects database (astro_data/pifinder_objects.db) is opened read-only;
  it is a repo-tracked build artifact and must never be written to.
- Altitude math uses calc_utils.FastAltAz (no skyfield in this process).
- "Push to PiFinder" reuses the SkySafari GoTo mechanism from pos_server
  (ui_state.add_recent + set_new_pushto + ui_queue "push_object") but with a
  fully populated CompositeObject from the DB, and sets the mount tracking
  frequency appropriate for the target (sidereal for static catalog objects,
  optional non-sidereal offset for future ephemeris targets).
"""

import json
import logging
import math
import os
import sqlite3
import threading
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from flask import Response, redirect, render_template, request, send_file, session

from PiFinder import nonsidereal, utils
from PiFinder.calc_utils import FastAltAz, dec_to_dms, ra_to_hms
from PiFinder.composite_object import CompositeObject, MagnitudeObject, SizeObject
from PiFinder.obj_types import OBJ_TYPES

logger = logging.getLogger("WebCatalogs")

# Catalog grouping for the home page. Codes not listed land in "Other".
CATALOG_GROUPS: List[Tuple[str, List[str]]] = [
    ("Deep Sky", ["M", "NGC", "IC", "C", "H", "Col", "Har", "Abl", "Arp", "B", "Sh2", "EGC", "Lyn"]),
    ("Double & Variable Stars", ["WDS", "SaM", "RDS", "SaR", "TLK", "Str"]),
    ("Observing Lists", ["Ta2", "SaA"]),
]

# Preferred "home catalog" order when an object appears in several catalogs
# (drives the detail page title and the designation used for push).
CATALOG_PRIORITY = [
    "M", "C", "H", "NGC", "IC", "Str", "Col", "Har", "Abl", "Arp", "B",
    "Sh2", "EGC", "Lyn", "Ta2", "SaA", "RDS", "TLK", "SaM", "SaR", "WDS",
]

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# Above this many filtered rows, per-row altitude work (up-now filter and
# altitude sort) is disabled to keep requests fast on the Pi (WDS: 131k rows).
ALT_COMPUTE_LIMIT = 12000

# Altitude curve sampling: -6h .. +18h from now, every 10 minutes.
CURVE_HOURS_BACK = 6
CURVE_HOURS_FORWARD = 18
CURVE_STEP_MINUTES = 10

_db_conn: Optional[sqlite3.Connection] = None
_db_lock = threading.Lock()

# calc_utils.sf_utils is a module-level singleton already loaded by this
# module's FastAltAz import, so using it for live planet positions costs no
# extra memory. It is not thread-safe (set_location mutates state) -> lock.
_sf_lock = threading.Lock()

PLANET_SEQUENCE = [
    "MERCURY", "VENUS", "MOON", "MARS", "JUPITER", "SATURN", "URANUS",
    "NEPTUNE", "PLUTO",
]


def _db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        conn = sqlite3.connect(
            f"file:{utils.pifinder_db}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        _db_conn = conn
    return _db_conn


def _query(sql: str, params: Tuple = ()) -> List[sqlite3.Row]:
    with _db_lock:
        return _db().execute(sql, params).fetchall()


def _json_response(data: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(data, default=str, ensure_ascii=False),
        status=status,
        content_type="application/json; charset=utf-8",
    )


def _observed_set() -> set:
    """(catalog_code, sequence) pairs with logged observations."""
    try:
        from PiFinder.db.observations_db import ObservationsDatabase

        obs_db = ObservationsDatabase()
        return {(row["catalog"], row["sequence"]) for row in obs_db.get_observed_objects()}
    except Exception:
        logger.exception("Could not load observed objects")
        return set()


def _altaz_calculator(shared_state) -> Optional[FastAltAz]:
    try:
        location = _observer_location(shared_state)
        if location is None:
            return None
        dt = _planet_observation_time(shared_state)
        return FastAltAz(location.lat, location.lon, dt)
    except Exception:
        return None


def _mag_str(mag_json: Optional[str]) -> str:
    try:
        return MagnitudeObject.from_json(mag_json or "").calc_two_mag_representation()
    except Exception:
        return "-"


def _size_str(size_json: Optional[str]) -> str:
    try:
        text = str(SizeObject.from_json(size_json or ""))
        return text if text.strip() else "-"
    except Exception:
        return "-"


def _display_name(catalog_code: str, sequence: int) -> str:
    return f"{catalog_code} {sequence}"


_location_cache: Dict[str, Any] = {"time": 0.0, "value": None}


def _observer_location(shared_state):
    """The GPS-locked location, else the saved default location from config.

    Without this fallback the shared-state default (lat 0, lon 0) silently
    produces wrong Alt/Az (e.g. the Moon near +85 while actually set)."""
    try:
        location = shared_state.location()
    except Exception:
        location = None
    if location is not None and getattr(location, "lock", False):
        return location

    import time as _time
    from types import SimpleNamespace

    now = _time.monotonic()
    if now - _location_cache["time"] > 60.0:
        configured = None
        try:
            from PiFinder import config as pf_config

            configured = pf_config.Config().get_option("locations.default", None)
        except Exception:
            logger.debug("Could not load default location", exc_info=True)
        _location_cache["value"] = (
            SimpleNamespace(
                lat=float(configured.latitude),
                lon=float(configured.longitude),
                altitude=float(configured.height),
                lock=True,
                source=f"CONFIG: {configured.name}",
            )
            if configured is not None
            else None
        )
        _location_cache["time"] = now
    return _location_cache["value"]


def _mount_status() -> Dict[str, Any]:
    try:
        with open(
            utils.runtime_dir / "mount_control_status.json", "r", encoding="utf-8"
        ) as status_in:
            return json.load(status_in)
    except (OSError, ValueError):
        return {}


def _planet_observation_time(shared_state):
    try:
        dt = shared_state.datetime()
    except Exception:
        dt = None
    if dt is None:
        from datetime import datetime, timezone

        dt = datetime.now(timezone.utc)
    return dt


def _calc_planets(shared_state, dt=None) -> Dict[str, Dict[str, Any]]:
    """Live planet positions via the sf_utils singleton (SUN excluded,
    matching the LCD's PL catalog). Uses the last known location; positions
    are topocentric so they are approximate until a location is set."""
    from PiFinder.calc_utils import sf_utils

    location = _observer_location(shared_state)
    if location is None:
        return {}
    if dt is None:
        dt = _planet_observation_time(shared_state)
    with _sf_lock:
        sf_utils.set_location(
            location.lat, location.lon, getattr(location, "altitude", 0.0) or 0.0
        )
        planets = sf_utils.calc_planets(dt)
    ordered: Dict[str, Dict[str, Any]] = {}
    for name in PLANET_SEQUENCE:
        if name in planets:
            ordered[name] = planets[name]
    return ordered


def register_catalog_routes(app, server_instance):
    """Register /catalogs pages and APIs on the existing Flask app."""

    def _auth_ok() -> bool:
        return bool(session.get("authenticated"))

    def _page_login_redirect():
        """Same behavior as server.auth_required: send to /login with ?next=."""
        return redirect(f"/login?next={quote(request.url, safe='')}")

    # ──────────────────────────────────────────────────────────────
    # Pages
    # ──────────────────────────────────────────────────────────────

    @app.route("/catalogs")
    def catalogs_home():
        if not _auth_ok():
            return _page_login_redirect()
        rows = _query(
            """
            SELECT c.catalog_code, c.desc,
                   (SELECT COUNT(*) FROM catalog_objects co
                     WHERE co.catalog_code = c.catalog_code) AS obj_count
              FROM catalogs c
            """
        )
        by_code = {row["catalog_code"]: row for row in rows}
        groups = []
        seen = set()
        for group_name, codes in CATALOG_GROUPS:
            entries = []
            for code in codes:
                row = by_code.get(code)
                if row is None:
                    continue
                seen.add(code)
                entries.append(
                    {
                        "code": code,
                        "desc": (row["desc"] or "").strip().splitlines()[0][:80]
                        if row["desc"]
                        else "",
                        "count": row["obj_count"],
                    }
                )
            if entries:
                groups.append({"name": group_name, "entries": entries})
        other = [
            {
                "code": row["catalog_code"],
                "desc": (row["desc"] or "").strip().splitlines()[0][:80]
                if row["desc"]
                else "",
                "count": row["obj_count"],
            }
            for code, row in sorted(by_code.items())
            if code not in seen
        ]
        if other:
            groups.append({"name": "Other", "entries": other})

        groups.append(
            {
                "name": "Solar System",
                "entries": [
                    {
                        "code": "PL",
                        "desc": "Planets & Moon — live positions computed on device",
                        "count": len(PLANET_SEQUENCE),
                    }
                ],
            }
        )

        total_objects = sum(
            entry["count"] for group in groups for entry in group["entries"]
        )
        return render_template(
            "catalogs/index.html",
            title="Catalogs",
            groups=groups,
            catalog_count=len(rows),
            total_objects=total_objects,
        )

    @app.route("/catalogs/<catalog_code>")
    def catalogs_catalog(catalog_code):
        if not _auth_ok():
            return _page_login_redirect()
        if catalog_code == "PL":
            return render_template(
                "catalogs/catalog.html",
                title="Catalogs - PL",
                catalog_code="PL",
                catalog_desc="Planets & Moon — live positions",
                object_count=len(PLANET_SEQUENCE),
                obj_types=[],
                constellations=[],
                alt_enabled=True,
            )
        rows = _query(
            "SELECT catalog_code, desc, max_sequence FROM catalogs WHERE catalog_code = ?",
            (catalog_code,),
        )
        if not rows:
            return Response("Catalog not found", status=404)
        catalog = rows[0]
        count = _query(
            "SELECT COUNT(*) AS n FROM catalog_objects WHERE catalog_code = ?",
            (catalog_code,),
        )[0]["n"]
        obj_types = _query(
            """
            SELECT DISTINCT o.obj_type FROM catalog_objects co
              JOIN objects o ON o.id = co.object_id
             WHERE co.catalog_code = ? ORDER BY o.obj_type
            """,
            (catalog_code,),
        )
        consts = _query(
            """
            SELECT DISTINCT o.const FROM catalog_objects co
              JOIN objects o ON o.id = co.object_id
             WHERE co.catalog_code = ? AND o.const != '' ORDER BY o.const
            """,
            (catalog_code,),
        )
        return render_template(
            "catalogs/catalog.html",
            title=f"Catalogs - {catalog_code}",
            catalog_code=catalog_code,
            catalog_desc=(catalog["desc"] or "").strip().splitlines()[0]
            if catalog["desc"]
            else "",
            object_count=count,
            obj_types=[
                {"code": row["obj_type"], "label": OBJ_TYPES.get(row["obj_type"], row["obj_type"])}
                for row in obj_types
            ],
            constellations=[row["const"] for row in consts],
            alt_enabled=count <= ALT_COMPUTE_LIMIT,
        )

    @app.route("/catalogs/object/<int:object_id>")
    def catalogs_object(object_id):
        if not _auth_ok():
            return _page_login_redirect()
        obj = _load_object_bundle(object_id)
        if obj is None:
            return Response("Object not found", status=404)
        return render_template(
            "catalogs/object.html",
            title=obj["display"],
            obj=obj,
        )

    # ──────────────────────────────────────────────────────────────
    # JSON APIs
    # ──────────────────────────────────────────────────────────────

    @app.route("/catalogs/api/objects")
    def catalogs_api_objects():
        if not _auth_ok():
            return _json_response({"error": "Unauthorized"}, 401)
        catalog_code = request.args.get("catalog", "")
        if not catalog_code:
            return _json_response({"error": "catalog parameter required"}, 400)
        if catalog_code == "PL":
            return _planet_objects_response()

        where = ["co.catalog_code = ?"]
        params: List[Any] = [catalog_code]

        q = request.args.get("q", "").strip()
        if q:
            where.append(
                "(EXISTS (SELECT 1 FROM names n WHERE n.object_id = o.id"
                " AND n.common_name LIKE ?)"
                " OR (co.catalog_code || ' ' || co.sequence) LIKE ?)"
            )
            params.extend([f"%{q}%", f"%{q}%"])

        types = [t for t in request.args.get("types", "").split(",") if t]
        if types:
            where.append(
                "o.obj_type IN ({})".format(",".join("?" * len(types)))
            )
            params.extend(types)

        const = request.args.get("const", "")
        if const:
            where.append("o.const = ?")
            params.append(const)

        mag_max = request.args.get("mag_max", "")
        if mag_max:
            where.append("CAST(json_extract(o.mag, '$.filter_mag') AS REAL) <= ?")
            params.append(float(mag_max))

        base_sql = (
            " FROM catalog_objects co JOIN objects o ON o.id = co.object_id"
            " WHERE " + " AND ".join(where)
        )

        observed = _observed_set()
        observed_filter = request.args.get("observed", "")  # "", "yes", "no"

        page = max(1, int(request.args.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(request.args.get("page_size", DEFAULT_PAGE_SIZE))))
        sort = request.args.get("sort", "seq")
        up_now = request.args.get("up_now", "") == "1"

        calculator = _altaz_calculator(server_instance.shared_state)
        total_filtered = _query("SELECT COUNT(*) AS n" + base_sql, tuple(params))[0]["n"]
        alt_allowed = total_filtered <= ALT_COMPUTE_LIMIT and calculator is not None

        select_cols = (
            "SELECT co.id AS co_id, co.catalog_code, co.sequence, o.id AS object_id,"
            " o.obj_type, o.const, o.ra, o.dec, o.mag, o.size,"
            " (SELECT n.common_name FROM names n WHERE n.object_id = o.id"
            "   AND n.common_name NOT LIKE co.catalog_code || '%'"
            "   ORDER BY (n.common_name GLOB '*[a-z]*') DESC, n.id LIMIT 1)"
            " AS common_name"
        )

        def row_dict(row, alt: Optional[float], az: Optional[float]) -> Dict[str, Any]:
            key = (row["catalog_code"], row["sequence"])
            return {
                "object_id": row["object_id"],
                "display": _display_name(row["catalog_code"], row["sequence"]),
                "common_name": row["common_name"] or "",
                "obj_type": row["obj_type"],
                "type_label": OBJ_TYPES.get(row["obj_type"], row["obj_type"]),
                "const": row["const"],
                "mag": _mag_str(row["mag"]),
                "size": _size_str(row["size"]),
                "alt": round(alt, 1) if alt is not None else None,
                "rising": (az is not None and az < 180.0) or None,
                "observed": key in observed,
            }

        needs_python_pass = (
            (up_now and alt_allowed)
            or (sort == "alt" and alt_allowed)
            or observed_filter in ("yes", "no")
        )

        if needs_python_pass:
            rows = _query(select_cols + base_sql + " ORDER BY co.sequence", tuple(params))
            enriched = []
            for row in rows:
                alt = az = None
                if alt_allowed:
                    alt, az = calculator.radec_to_altaz(row["ra"], row["dec"])
                key = (row["catalog_code"], row["sequence"])
                if up_now and alt_allowed and (alt is None or alt <= 0.0):
                    continue
                if observed_filter == "yes" and key not in observed:
                    continue
                if observed_filter == "no" and key in observed:
                    continue
                enriched.append((row, alt, az))
            if sort == "alt" and alt_allowed:
                enriched.sort(key=lambda item: item[1] if item[1] is not None else -99.0, reverse=True)
            elif sort == "mag":
                def mag_key(item):
                    try:
                        return float(json.loads(item[0]["mag"] or "{}").get("filter_mag"))
                    except (TypeError, ValueError):
                        return 99.0
                enriched.sort(key=mag_key)
            total = len(enriched)
            page_rows = enriched[(page - 1) * page_size : page * page_size]
            objects = [row_dict(row, alt, az) for row, alt, az in page_rows]
        else:
            order = "co.sequence"
            if sort == "mag":
                order = "CAST(json_extract(o.mag, '$.filter_mag') AS REAL) IS NULL, CAST(json_extract(o.mag, '$.filter_mag') AS REAL)"
            total = total_filtered
            rows = _query(
                select_cols + base_sql + f" ORDER BY {order} LIMIT ? OFFSET ?",
                tuple(params) + (page_size, (page - 1) * page_size),
            )
            objects = []
            for row in rows:
                alt = az = None
                if calculator is not None:
                    alt, az = calculator.radec_to_altaz(row["ra"], row["dec"])
                objects.append(row_dict(row, alt, az))

        return _json_response(
            {
                "catalog": catalog_code,
                "total": total,
                "page": page,
                "page_size": page_size,
                "pages": max(1, math.ceil(total / page_size)),
                "sort": sort,
                "alt_available": calculator is not None,
                "alt_enabled": alt_allowed,
                "objects": objects,
            }
        )

    @app.route("/catalogs/api/search")
    def catalogs_api_search():
        if not _auth_ok():
            return _json_response({"error": "Unauthorized"}, 401)
        q = request.args.get("q", "").strip()
        if len(q) < 2:
            return _json_response({"results": []})
        rows = _query(
            """
            SELECT n.object_id, n.common_name, co.catalog_code, co.sequence,
                   o.obj_type, o.const
              FROM names n
              JOIN objects o ON o.id = n.object_id
              JOIN catalog_objects co ON co.object_id = n.object_id
             WHERE n.common_name LIKE ?
             LIMIT 120
            """,
            (f"%{q}%",),
        )
        results = []
        seen = set()
        for row in rows:
            if row["object_id"] in seen:
                continue
            seen.add(row["object_id"])
            results.append(
                {
                    "object_id": row["object_id"],
                    "display": _display_name(row["catalog_code"], row["sequence"]),
                    "matched_name": row["common_name"],
                    "type_label": OBJ_TYPES.get(row["obj_type"], row["obj_type"]),
                    "const": row["const"],
                }
            )
            if len(results) >= 50:
                break
        return _json_response({"results": results})

    @app.route("/catalogs/api/altitude/<int:object_id>")
    def catalogs_api_altitude(object_id):
        if not _auth_ok():
            return _json_response({"error": "Unauthorized"}, 401)
        rows = _query("SELECT ra, dec FROM objects WHERE id = ?", (object_id,))
        if not rows:
            return _json_response({"error": "Object not found"}, 404)
        ra, dec = rows[0]["ra"], rows[0]["dec"]

        location = _observer_location(server_instance.shared_state)
        if location is None:
            return _json_response({"available": False, "reason": "no location"})
        now = _planet_observation_time(server_instance.shared_state)

        start = now - timedelta(hours=CURVE_HOURS_BACK)
        steps = (CURVE_HOURS_BACK + CURVE_HOURS_FORWARD) * 60 // CURVE_STEP_MINUTES
        samples = []
        best = (-91.0, None)
        for i in range(steps + 1):
            dt = start + timedelta(minutes=i * CURVE_STEP_MINUTES)
            alt, _az = FastAltAz(location.lat, location.lon, dt).radec_to_altaz(
                ra, dec
            )
            samples.append({"t": dt.isoformat(), "alt": round(alt, 2)})
            if alt > best[0]:
                best = (alt, dt)

        calculator = FastAltAz(location.lat, location.lon, now)
        alt_now, az_now = calculator.radec_to_altaz(ra, dec)
        return _json_response(
            {
                "available": True,
                "now": now.isoformat(),
                "alt_now": round(alt_now, 1),
                "az_now": round(az_now, 1),
                "transit_time": best[1].isoformat() if best[1] else None,
                "transit_alt": round(best[0], 1),
                "samples": samples,
            }
        )

    @app.route("/catalogs/image/<int:object_id>")
    def catalogs_image(object_id):
        if not _auth_ok():
            return Response("Unauthorized", status=401)
        bundle = _load_object_bundle(object_id)
        if bundle is None:
            return Response("Object not found", status=404)

        image_path = _resolve_or_fetch_image(bundle)
        if not image_path:
            return Response("No image", status=404)
        try:
            return send_file(image_path, mimetype="image/jpeg")
        except (OSError, ValueError):
            return Response("No image", status=404)

    def _resolve_or_fetch_image(bundle: Dict[str, Any]) -> Optional[str]:
        """Find the POSS thumbnail locally, or fetch it once from the
        PiFinder image CDN into the standard catalog_images cache (same
        layout as get_images.py, so a later full download coexists)."""
        from PiFinder import cat_images

        candidates = [f"{bundle['catalog_code']}{bundle['sequence']}"] + [
            "".join(name.split()) for name in bundle["names"]
        ]
        for image_name in candidates:
            if not image_name:
                continue
            local = (
                f"{cat_images.BASE_IMAGE_PATH}/{image_name[-1]}/"
                f"{image_name}_POSS.jpg"
            )
            if os.path.exists(local):
                return local

        import requests

        for image_name in candidates:
            if not image_name:
                continue
            local = (
                f"{cat_images.BASE_IMAGE_PATH}/{image_name[-1]}/"
                f"{image_name}_POSS.jpg"
            )
            url = (
                "https://ddbeeedxfpnp0.cloudfront.net/catalog_images/"
                f"{image_name[-1]}/{image_name}_POSS.jpg"
            )
            try:
                response = requests.get(url, timeout=10)
                if response.status_code != 200 or not response.content:
                    continue
                os.makedirs(os.path.dirname(local), exist_ok=True)
                tmp_path = f"{local}.tmp"
                with open(tmp_path, "wb") as image_out:
                    image_out.write(response.content)
                os.replace(tmp_path, local)
                logger.info("Fetched catalog image from CDN: %s", image_name)
                return local
            except requests.RequestException:
                logger.debug("CDN image fetch failed for %s", image_name)
        return None

    @app.route("/catalogs/api/push/<int:object_id>", methods=["POST"])
    def catalogs_api_push(object_id):
        if not _auth_ok():
            return _json_response({"error": "Unauthorized"}, 401)

        bundle = _load_object_bundle(object_id)
        if bundle is None:
            return _json_response({"error": "Object not found"}, 404)

        obj = CompositeObject.from_dict(
            {
                "id": bundle["co_id"],
                "object_id": object_id,
                "obj_type": bundle["obj_type"],
                "ra": bundle["ra"],
                "dec": bundle["dec"],
                "const": bundle["const"],
                "catalog_code": bundle["catalog_code"],
                "sequence": bundle["sequence"],
                "description": bundle["description"],
                "surface_brightness": bundle["surface_brightness"],
            }
        )
        obj.names = bundle["names"]
        try:
            obj.mag = MagnitudeObject.from_json(bundle["mag_json"] or "")
            obj.mag_str = obj.mag.calc_two_mag_representation()
        except Exception:
            obj.mag = MagnitudeObject([])
            obj.mag_str = "-"
        try:
            obj.size = SizeObject.from_json(bundle["size_json"] or "")
        except Exception:
            obj.size = SizeObject([])
        obj.logged = bundle["observed"]
        obj._details_loaded = True

        shared_state = server_instance.shared_state
        try:
            shared_state.ui_state().add_recent(obj)
            shared_state.ui_state().set_new_pushto(True)
            server_instance.ui_queue.put("push_object")
        except Exception as exc:
            logger.exception("Push to PiFinder failed")
            return _json_response({"error": f"Push failed: {exc}"}, 500)

        goto = _queue_mount_goto(
            server_instance, bundle["ra"], bundle["dec"], bundle["display"]
        )
        track_freq = _apply_push_track_freq(
            server_instance, bundle["display"], request.get_json(silent=True) or {}
        )

        logger.info("Web catalog push: %s", bundle["display"])
        return _json_response(
            {
                "success": True,
                "pushed": bundle["display"],
                "goto": goto,
                "track_freq": track_freq,
            }
        )

    # ──────────────────────────────────────────────────────────────
    # Live planet catalog (PL)
    # ──────────────────────────────────────────────────────────────

    def _planet_objects_response():
        from PiFinder.calc_utils import sf_utils

        planets = _calc_planets(server_instance.shared_state)
        q = request.args.get("q", "").strip().upper()
        sort = request.args.get("sort", "seq")
        up_now = request.args.get("up_now", "") == "1"
        rows = []
        for name, planet in planets.items():
            if q and q not in name:
                continue
            alt, az = planet.get("altaz", (None, None))
            if up_now and (alt is None or alt <= 0.0):
                continue
            ra, dec = planet["radec"]
            rows.append(
                {
                    "object_id": None,
                    "href": f"/catalogs/planet/{name.lower()}",
                    "display": name.capitalize(),
                    "common_name": "",
                    "obj_type": "Pla",
                    "type_label": OBJ_TYPES.get("Pla", "Planet"),
                    "const": sf_utils.radec_to_constellation(ra, dec) or "",
                    "mag": str(planet.get("mag", "-")),
                    "size": "-",
                    "alt": round(alt, 1) if alt is not None else None,
                    "rising": (az is not None and az < 180.0) or None,
                    "observed": False,
                }
            )
        if sort == "alt":
            rows.sort(key=lambda r: r["alt"] if r["alt"] is not None else -99.0,
                      reverse=True)
        elif sort == "mag":
            def planet_mag(row):
                try:
                    return float(row["mag"])
                except ValueError:
                    return 99.0
            rows.sort(key=planet_mag)
        return _json_response(
            {
                "catalog": "PL",
                "total": len(rows),
                "page": 1,
                "page_size": max(1, len(rows)),
                "pages": 1,
                "sort": sort,
                "alt_available": bool(rows),
                "alt_enabled": True,
                "objects": rows,
            }
        )

    def _planet_bundle(name: str) -> Optional[Dict[str, Any]]:
        planets = _calc_planets(server_instance.shared_state)
        key = name.upper()
        if key not in planets:
            return None
        planet = planets[key]
        ra, dec = planet["radec"]
        (ra_h, ra_m, ra_s), (dec_d, dec_m, dec_s) = planet["radec_pretty"]
        alt, az = planet.get("altaz", (None, None))
        # Feed-forward rate: finite difference of the ephemeris over 10 min.
        dt = _planet_observation_time(server_instance.shared_state)
        later = _calc_planets(server_instance.shared_state, dt + timedelta(minutes=10))
        track = None
        if key in later:
            result = nonsidereal.track_freq_for_target(
                ra, later[key]["radec"][0], 600.0
            )
            if result is not None:
                hz, dra_dt, was_clamped = result
                track = {
                    "offset_arcsec_per_s": round(dra_dt, 4),
                    "hz": round(hz, 5),
                    "clamped": was_clamped,
                }
        from PiFinder.calc_utils import sf_utils

        return {
            "planet": key,
            "display": key.capitalize(),
            "catalog_code": "PL",
            "sequence": PLANET_SEQUENCE.index(key) + 1,
            "obj_type": "Pla",
            "type_label": OBJ_TYPES.get("Pla", "Planet"),
            "const": sf_utils.radec_to_constellation(ra, dec) or "",
            "ra": ra,
            "dec": dec,
            "ra_str": f"{ra_h:02.0f}h {ra_m:02.0f}m {ra_s:02.0f}s",
            "dec_str": f"{dec_d:+03.0f}° {dec_m:02.0f}′ {dec_s:02.0f}″",
            "mag_str": str(planet.get("mag", "-")),
            "size_str": "-",
            "surface_brightness": None,
            "names": [],
            "other_entries": [],
            "description": "Live position computed on device.",
            "observed": False,
            "alt": round(alt, 1) if alt is not None else None,
            "az": round(az, 1) if az is not None else None,
            "track": track,
        }

    @app.route("/catalogs/planet/<name>")
    def catalogs_planet(name):
        if not _auth_ok():
            return _page_login_redirect()
        bundle = _planet_bundle(name)
        if bundle is None:
            return Response("Planet not found", status=404)
        return render_template(
            "catalogs/object.html", title=bundle["display"], obj=bundle
        )

    @app.route("/catalogs/api/altitude_planet/<name>")
    def catalogs_api_altitude_planet(name):
        if not _auth_ok():
            return _json_response({"error": "Unauthorized"}, 401)
        shared_state = server_instance.shared_state
        planets = _calc_planets(shared_state)
        if name.upper() not in planets:
            return _json_response({"error": "Planet not found"}, 404)
        if _observer_location(shared_state) is None:
            return _json_response({"available": False, "reason": "no location"})
        now = _planet_observation_time(shared_state)
        start = now - timedelta(hours=CURVE_HOURS_BACK)
        step = 30  # minutes; planet sampling recomputes the ephemeris
        steps = (CURVE_HOURS_BACK + CURVE_HOURS_FORWARD) * 60 // step
        samples = []
        best = (-91.0, None)
        for i in range(steps + 1):
            sample_dt = start + timedelta(minutes=i * step)
            sample = _calc_planets(shared_state, sample_dt).get(name.upper())
            if sample is None:
                continue
            alt = sample["altaz"][0]
            samples.append({"t": sample_dt.isoformat(), "alt": round(alt, 2)})
            if alt > best[0]:
                best = (alt, sample_dt)
        current = planets[name.upper()]
        return _json_response(
            {
                "available": True,
                "now": now.isoformat(),
                "alt_now": round(current["altaz"][0], 1),
                "az_now": round(current["altaz"][1], 1),
                "transit_time": best[1].isoformat() if best[1] else None,
                "transit_alt": round(best[0], 1),
                "samples": samples,
            }
        )

    @app.route("/catalogs/api/push_planet/<name>", methods=["POST"])
    def catalogs_api_push_planet(name):
        if not _auth_ok():
            return _json_response({"error": "Unauthorized"}, 401)
        bundle = _planet_bundle(name)
        if bundle is None:
            return _json_response({"error": "Planet not found"}, 404)

        try:
            mag = MagnitudeObject([float(bundle["mag_str"])])
        except ValueError:
            mag = MagnitudeObject([])
        obj = CompositeObject.from_dict(
            {
                "id": -1,
                "object_id": -(bundle["sequence"]),
                "obj_type": "Pla",
                "ra": bundle["ra"],
                "dec": bundle["dec"],
                "const": bundle["const"],
                "catalog_code": "PL",
                "sequence": bundle["sequence"],
                "description": bundle["description"],
            }
        )
        obj.names = [bundle["display"]]
        obj.mag = mag
        obj.mag_str = mag.calc_two_mag_representation()
        obj.size = SizeObject([])
        obj._details_loaded = True

        shared_state = server_instance.shared_state
        try:
            shared_state.ui_state().add_recent(obj)
            shared_state.ui_state().set_new_pushto(True)
            server_instance.ui_queue.put("push_object")
        except Exception as exc:
            logger.exception("Planet push failed")
            return _json_response({"error": f"Push failed: {exc}"}, 500)

        goto = _queue_mount_goto(
            server_instance, bundle["ra"], bundle["dec"], bundle["display"]
        )
        # Planets are non-sidereal: apply the ephemeris feed-forward rate.
        body = {"offset_arcsec_per_s": bundle["track"]["offset_arcsec_per_s"]} \
            if bundle.get("track") else {}
        track_freq = _apply_push_track_freq(
            server_instance, bundle["display"], body
        )
        logger.info("Web catalog planet push: %s", bundle["display"])
        return _json_response(
            {
                "success": True,
                "pushed": bundle["display"],
                "goto": goto,
                "track_freq": track_freq,
            }
        )

    # ──────────────────────────────────────────────────────────────
    # Helpers bound to server_instance
    # ──────────────────────────────────────────────────────────────

    def _load_object_bundle(object_id: int) -> Optional[Dict[str, Any]]:
        rows = _query(
            """
            SELECT o.id, o.obj_type, o.ra, o.dec, o.const, o.size, o.mag,
                   o.surface_brightness
              FROM objects o WHERE o.id = ?
            """,
            (object_id,),
        )
        if not rows:
            return None
        obj_row = rows[0]

        entries = _query(
            """
            SELECT id AS co_id, catalog_code, sequence, description
              FROM catalog_objects WHERE object_id = ? ORDER BY id
            """,
            (object_id,),
        )
        if not entries:
            return None

        def entry_priority(entry) -> Tuple[int, int]:
            try:
                rank = CATALOG_PRIORITY.index(entry["catalog_code"])
            except ValueError:
                rank = len(CATALOG_PRIORITY)
            return (rank, entry["co_id"])

        entries = sorted(entries, key=entry_priority)
        home = entries[0]

        names = [
            row["common_name"]
            for row in _query(
                "SELECT common_name FROM names WHERE object_id = ?", (object_id,)
            )
        ]

        observed = _observed_set()
        is_observed = any(
            (entry["catalog_code"], entry["sequence"]) in observed
            for entry in entries
        )

        calculator = _altaz_calculator(server_instance.shared_state)
        alt = az = None
        if calculator is not None:
            alt, az = calculator.radec_to_altaz(obj_row["ra"], obj_row["dec"])

        ra_h, ra_m, ra_s = ra_to_hms(obj_row["ra"])
        dec_d, dec_m, dec_s = dec_to_dms(obj_row["dec"])

        return {
            "object_id": object_id,
            "co_id": home["co_id"],
            "catalog_code": home["catalog_code"],
            "sequence": home["sequence"],
            "display": _display_name(home["catalog_code"], home["sequence"]),
            "description": home["description"] or "",
            "obj_type": obj_row["obj_type"],
            "type_label": OBJ_TYPES.get(obj_row["obj_type"], obj_row["obj_type"]),
            "const": obj_row["const"],
            "ra": obj_row["ra"],
            "dec": obj_row["dec"],
            "ra_str": f"{ra_h:02.0f}h {ra_m:02.0f}m {ra_s:02.0f}s",
            "dec_str": f"{dec_d:+03.0f}° {dec_m:02.0f}′ {dec_s:02.0f}″",
            "mag_json": obj_row["mag"],
            "size_json": obj_row["size"],
            "mag_str": _mag_str(obj_row["mag"]),
            "size_str": _size_str(obj_row["size"]),
            "surface_brightness": obj_row["surface_brightness"],
            "names": names,
            "other_entries": [
                {
                    "display": _display_name(entry["catalog_code"], entry["sequence"]),
                    "description": entry["description"] or "",
                }
                for entry in entries[1:]
            ],
            "observed": is_observed,
            "alt": round(alt, 1) if alt is not None else None,
            "az": round(az, 1) if az is not None else None,
        }

    logger.info("PiFinder web catalog routes registered")


def _apply_push_track_freq(
    server_instance, label: str, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Set the mount tracking frequency appropriate for a pushed target.

    Static catalog objects track at sidereal rate: if a non-sidereal
    frequency is currently active it is reset. A caller that knows the
    target's own motion (future ephemeris targets) may send
    {"offset_arcsec_per_s": <dRA/dt>} to request a non-sidereal frequency.
    """
    mount_queue = getattr(server_instance, "mountcontrol_queue", None)
    if mount_queue is None:
        return {"action": "none", "reason": "mount control not available"}

    offset = body.get("offset_arcsec_per_s")
    if offset is not None:
        hz, was_clamped = nonsidereal.clamp_hz(
            nonsidereal.hz_from_offset(float(offset))
        )
        mount_queue.put({"type": "set_track_freq", "hz": hz, "label": label})
        return {
            "action": "set",
            "hz": hz,
            "offset_arcsec_per_s": float(offset),
            "clamped": was_clamped,
        }

    # Sidereal target: only reset when a non-sidereal frequency is active.
    status = _mount_status()
    if status.get("track_freq_hz") is not None:
        mount_queue.put({"type": "reset_track_freq"})
        return {"action": "reset", "was_hz": status.get("track_freq_hz")}
    return {"action": "none", "reason": "already sidereal"}


def _queue_mount_goto(
    server_instance, ra_deg: float, dec_deg: float, name: str
) -> Dict[str, Any]:
    """Queue an INDI mount GoTo for a pushed target.

    Mirrors pos_server's SkySafari routing: an active multipoint-align
    session GoTos through mount control; otherwise the GoTo/Guide service
    handles slew + solve refinement. Honors mount_control/indi_goto_method
    config exactly like the SkySafari path."""
    try:
        from PiFinder import config as pf_config

        cfg = pf_config.Config()
        mount_control = bool(cfg.get_option("mount_control", False))
        goto_method = str(cfg.get_option("indi_goto_method", "indi_mount"))
    except Exception:
        logger.exception("Could not load mount config for goto")
        return {"action": "none", "reason": "config unavailable"}

    multipoint = _mount_status().get("multipoint_align")
    multipoint_active = isinstance(multipoint, dict) and bool(
        multipoint.get("active")
    )

    mountcontrol_queue = getattr(server_instance, "mountcontrol_queue", None)
    goto_guide_queue = getattr(server_instance, "goto_guide_queue", None)

    if multipoint_active:
        if not mount_control or mountcontrol_queue is None:
            return {"action": "none", "reason": "mount control unavailable"}
        mountcontrol_queue.put(
            {
                "type": "multipoint_align_goto_target",
                "ra": ra_deg,
                "dec": dec_deg,
                "name": name,
            }
        )
        logger.info("Web push multipoint GoTo queued: %s", name)
        return {"action": "multipoint_align_goto"}

    if not mount_control or goto_guide_queue is None:
        return {"action": "none", "reason": "mount control off"}
    if goto_method == "off":
        return {"action": "none", "reason": "GoTo type off"}
    goto_guide_queue.put({"type": "goto_target", "ra": ra_deg, "dec": dec_deg})
    logger.info("Web push GoTo queued via GoTo/Guide service: %s", name)
    return {"action": "goto_queued"}
