"""Tests for PiFinder.web_catalogs (web catalog pages, APIs, push)."""

import os
import queue
from types import SimpleNamespace

import pytest
from flask import Flask

from PiFinder import utils


class FakeUIState:
    def __init__(self):
        self.recent = []
        self.pushto = False

    def add_recent(self, obj):
        self.recent.append(obj)

    def set_new_pushto(self, value):
        self.pushto = value


class FakeSharedState:
    def __init__(self):
        self._ui_state = FakeUIState()

    def location(self):
        # lock=True so the planet catalog uses THIS location. With lock=False
        # _observer_location falls back to the config's default location,
        # which exists on a developer's device but not in CI -- there the
        # planet list came back empty and the tests failed.
        return SimpleNamespace(lat=37.5, lon=127.1, altitude=0.0, lock=True)

    def datetime(self):
        return None

    def ui_state(self):
        return self._ui_state


class FakeServer:
    def __init__(self):
        self.shared_state = FakeSharedState()
        self.ui_queue = queue.Queue()
        self.mountcontrol_queue = queue.Queue()
        self.goto_guide_queue = queue.Queue()


@pytest.fixture()
def web_app():
    from PiFinder.web_catalogs import register_catalog_routes

    views_path = os.path.abspath(
        os.path.join(os.path.dirname(utils.__file__), "..", "views")
    )
    app = Flask(__name__, template_folder=views_path)
    app.secret_key = "test"
    app.jinja_env.add_extension("jinja2.ext.i18n")
    app.jinja_env.install_null_translations()
    server = FakeServer()
    register_catalog_routes(app, server)
    return app, server


def _login(client):
    with client.session_transaction() as sess:
        sess["authenticated"] = True
    return client


def _m31_object_id():
    import sqlite3

    conn = sqlite3.connect(f"file:{utils.pifinder_db}?mode=ro", uri=True)
    row = conn.execute(
        "SELECT object_id FROM catalog_objects"
        " WHERE catalog_code = 'M' AND sequence = 31"
    ).fetchone()
    conn.close()
    return row[0]


@pytest.mark.unit
def test_pages_and_apis_require_auth(web_app):
    app, _server = web_app
    client = app.test_client()
    for url in ("/catalogs", "/catalogs/M", "/catalogs/object/224"):
        response = client.get(url)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]
    assert client.get("/catalogs/api/objects?catalog=M").status_code == 401
    assert client.get("/catalogs/api/search?q=andromeda").status_code == 401
    assert client.get("/catalogs/api/altitude/224").status_code == 401
    assert client.get("/catalogs/image/224").status_code == 401


@pytest.mark.unit
def test_home_page(web_app):
    app, _server = web_app
    response = _login(app.test_client()).get("/catalogs")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "NGC" in body and "Messier" in body or "M" in body


@pytest.mark.unit
def test_catalog_page_and_404(web_app):
    app, _server = web_app
    client = _login(app.test_client())
    assert client.get("/catalogs/M").status_code == 200
    assert client.get("/catalogs/NOPE").status_code == 404


@pytest.mark.unit
def test_objects_api_basic(web_app):
    app, _server = web_app
    client = _login(app.test_client())
    data = client.get("/catalogs/api/objects?catalog=M").get_json()
    assert data["total"] == 110
    assert data["objects"][0]["display"] == "M 1"
    # alt availability depends on a saved default location in local config
    assert data["alt_available"] in (True, False)

    galaxies = client.get("/catalogs/api/objects?catalog=M&types=Gx").get_json()
    assert 0 < galaxies["total"] < 110
    assert all(o["obj_type"] == "Gx" for o in galaxies["objects"])

    bright = client.get("/catalogs/api/objects?catalog=M&mag_max=5").get_json()
    assert 0 < bright["total"] < 110


@pytest.mark.unit
def test_objects_api_pagination(web_app):
    app, _server = web_app
    client = _login(app.test_client())
    page2 = client.get("/catalogs/api/objects?catalog=M&page=2&page_size=50").get_json()
    assert page2["page"] == 2
    assert page2["pages"] == 3
    assert page2["objects"][0]["display"] == "M 51"


@pytest.mark.unit
def test_search_api(web_app):
    app, _server = web_app
    client = _login(app.test_client())
    data = client.get("/catalogs/api/search?q=Andromeda").get_json()
    assert any("Andromeda" in r["matched_name"] for r in data["results"])
    assert client.get("/catalogs/api/search?q=a").get_json() == {"results": []}


@pytest.mark.unit
def test_object_page_and_altitude(web_app):
    app, _server = web_app
    client = _login(app.test_client())
    object_id = _m31_object_id()
    page = client.get(f"/catalogs/object/{object_id}")
    assert page.status_code == 200
    assert "M 31" in page.get_data(as_text=True)

    alt = client.get(f"/catalogs/api/altitude/{object_id}").get_json()
    assert "available" in alt
    if alt["available"]:
        assert len(alt["samples"]) > 100 and "transit_time" in alt

    assert client.get("/catalogs/object/99999999").status_code == 404


@pytest.mark.unit
def test_push_requires_auth(web_app):
    app, _server = web_app
    client = app.test_client()
    response = client.post(f"/catalogs/api/push/{_m31_object_id()}", json={})
    assert response.status_code == 401


@pytest.mark.unit
def test_push_authenticated(web_app):
    app, server = web_app
    client = _login(app.test_client())
    response = client.post(f"/catalogs/api/push/{_m31_object_id()}", json={})
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["pushed"] == "M 31"
    # GoTo routing mirrors SkySafari: queued when mount_control is on
    assert "goto" in data
    if data["goto"]["action"] == "goto_queued":
        command = server.goto_guide_queue.get_nowait()
        assert command["type"] == "goto_target"
        assert command["ra"] == pytest.approx(10.68, abs=0.1)
    # LCD push mechanism fired
    assert server.ui_queue.get_nowait() == "push_object"
    ui_state = server.shared_state.ui_state()
    assert ui_state.pushto is True
    assert ui_state.recent and ui_state.recent[0].catalog_code == "M"
    assert ui_state.recent[0].sequence == 31
    assert ui_state.recent[0].ra == pytest.approx(10.68, abs=0.1)


@pytest.mark.unit
def test_push_with_nonsidereal_offset(web_app):
    from PiFinder import nonsidereal

    app, server = web_app
    client = _login(app.test_client())
    response = client.post(
        f"/catalogs/api/push/{_m31_object_id()}",
        json={"offset_arcsec_per_s": 0.55},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["track_freq"]["action"] == "set"
    assert data["track_freq"]["hz"] == pytest.approx(
        nonsidereal.hz_from_offset(0.55), abs=1e-6
    )
    server.ui_queue.get_nowait()
    command = server.mountcontrol_queue.get_nowait()
    assert command["type"] == "set_track_freq"
    assert command["hz"] == pytest.approx(57.964, abs=0.01)
    assert command["label"] == "M 31"


@pytest.mark.unit
def test_planet_catalog_listing(web_app):
    app, _server = web_app
    client = _login(app.test_client())
    assert client.get("/catalogs/PL").status_code == 200
    data = client.get("/catalogs/api/objects?catalog=PL").get_json()
    assert data["total"] >= 8
    names = [o["display"] for o in data["objects"]]
    assert "Moon" in names and "Jupiter" in names and "Sun" not in names
    moon = next(o for o in data["objects"] if o["display"] == "Moon")
    assert moon["href"] == "/catalogs/planet/moon"
    assert moon["const"]  # constellation resolved from live position


@pytest.mark.unit
def test_planet_detail_and_push(web_app):
    app, server = web_app
    client = _login(app.test_client())
    page = client.get("/catalogs/planet/moon")
    assert page.status_code == 200
    assert "Moon" in page.get_data(as_text=True)
    assert client.get("/catalogs/planet/nope").status_code == 404

    response = client.post("/catalogs/api/push_planet/moon", json={})
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True and data["pushed"] == "Moon"
    # Non-sidereal feed-forward applied automatically
    assert data["track_freq"]["action"] == "set"
    assert server.ui_queue.get_nowait() == "push_object"
    command = server.mountcontrol_queue.get_nowait()
    assert command["type"] == "set_track_freq"
    assert command["label"] == "Moon"
    # Moon rate is slower than sidereal -> below 60.164 Hz
    assert command["hz"] < 60.16
