import json
from pathlib import Path


VIEWS_DIR = Path(__file__).resolve().parents[1] / "views"
SERVER_PATH = Path(__file__).resolve().parents[1] / "PiFinder" / "server.py"


def test_base_template_exposes_theme_selector():
    base_html = (VIEWS_DIR / "base.html").read_text()

    assert "data-theme=\"grey\"" in base_html
    assert "pifinderWebTheme" in base_html
    assert "rel=\"manifest\"" in base_html
    assert "apple-mobile-web-app-capable" in base_html
    assert "apple-touch-icon" in base_html
    assert "pf-theme-select" in base_html
    assert "pf-fullscreen-button" in base_html
    assert "pf-fullscreen-restore" in base_html
    assert "value=\"grey\"" in base_html
    assert "value=\"red\"" in base_html


def test_red_theme_is_defined_without_overriding_log_viewer_colors():
    style_css = (VIEWS_DIR / "css" / "style.css").read_text()
    logs_html = (VIEWS_DIR / "logs.html").read_text()

    assert "html[data-theme=\"red\"]" in style_css
    assert "--pf-bg: #080000" in style_css
    assert "html[data-theme=\"red\"] .log-container" not in style_css
    assert ".pf-fullscreen-menu" in style_css
    assert ".log-container" in logs_html
    assert "color: #d4d4d4" in logs_html
    assert "logLine.style.color = '#ff6b6b'" in logs_html


def test_pwa_manifest_and_assets_are_present():
    manifest = json.loads((VIEWS_DIR / "manifest.webmanifest").read_text())
    server_py = SERVER_PATH.read_text()

    assert manifest["name"] == "PiFinder"
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "fullscreen"
    assert "standalone" in manifest["display_override"]
    assert "/manifest.webmanifest" in server_py
    assert "/service-worker.js" in server_py

    icon_sources = {icon["src"] for icon in manifest["icons"]}
    assert "/images/pwa-icon-192.png" in icon_sources
    assert "/images/pwa-icon-512.png" in icon_sources
    assert (VIEWS_DIR / "images" / "pwa-icon-192.png").exists()
    assert (VIEWS_DIR / "images" / "pwa-icon-512.png").exists()


def test_fullscreen_script_is_present():
    init_js = (VIEWS_DIR / "js" / "init.js").read_text()

    assert "requestFullscreen" in init_js
    assert "pifinderWantFullscreen" in init_js
    assert "sessionStorage" in init_js
    assert "webkitRequestFullscreen" in init_js
    assert "exitFullscreen" in init_js
    assert "fullscreenchange" in init_js
