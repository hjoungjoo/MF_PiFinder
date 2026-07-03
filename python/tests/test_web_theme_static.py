import json
from pathlib import Path


VIEWS_DIR = Path(__file__).resolve().parents[1] / "views"
SERVER_PATH = Path(__file__).resolve().parents[1] / "PiFinder" / "server.py"


def test_base_template_exposes_theme_selector():
    base_html = (VIEWS_DIR / "base.html").read_text()

    assert 'data-theme="grey"' in base_html
    assert "pifinderWebTheme" in base_html
    assert 'rel="manifest"' in base_html
    assert "apple-mobile-web-app-capable" in base_html
    assert "apple-touch-icon" in base_html
    assert "pf-theme-select" in base_html
    assert "pf-fullscreen-button" in base_html
    assert "pf-fullscreen-restore" in base_html
    assert 'value="grey"' in base_html
    assert 'value="red"' in base_html


def test_base_template_exposes_language_selector():
    base_html = (VIEWS_DIR / "base.html").read_text()
    init_js = (VIEWS_DIR / "js" / "init.js").read_text()
    server_py = SERVER_PATH.read_text()

    assert 'action="/language"' in base_html
    assert "pf-language-select" in base_html
    assert "current_web_language" in base_html
    assert "web_language_options" in base_html
    assert 'value="{{ code }}"' in base_html
    assert 'name="next"' in base_html

    assert "pf-language-select" in init_js
    assert "this.form.submit()" in init_js

    assert "WEB_LANGUAGE_COOKIE" in server_py
    assert "SUPPORTED_WEB_LANGUAGES" in server_py
    assert "BABEL_TRANSLATION_DIRECTORIES" in server_py
    assert '@app.route("/language", methods=["POST"])' in server_py


def test_korean_web_translations_cover_recent_pages():
    ko_po = (
        Path(__file__).resolve().parents[1]
        / "locale"
        / "ko"
        / "LC_MESSAGES"
        / "messages.po"
    ).read_text()
    remote_html = (VIEWS_DIR / "remote.html").read_text()
    location_form_html = (VIEWS_DIR / "location_form.html").read_text()

    assert "{{ _('Long') }}" in remote_html
    assert "{{ _('Manual Entry') }}" in location_form_html

    expected_pairs = {
        "PiFinder Logs": "PiFinder 로그",
        "Download All Logs": "모든 로그 다운로드",
        "Current INDI Driver State": "현재 INDI 드라이버 상태",
        "Send Location and Time": "위치와 시간 전송",
        "Location Management": "위치 관리",
        "Network Settings": "네트워크 설정",
        "AP Connected Devices": "AP 접속 장치",
        "User Data and Settings": "사용자 데이터와 설정",
        "Long": "길게",
        "Manual Entry": "수동 입력",
    }
    for msgid, msgstr in expected_pairs.items():
        assert f'msgid "{msgid}"' in ko_po
        assert f'msgstr "{msgstr}"' in ko_po


def test_desktop_nav_controls_share_one_alignment_context():
    style_css = (VIEWS_DIR / "css" / "style.css").read_text()
    base_html = (VIEWS_DIR / "base.html").read_text()

    assert "pf-nav-top-row" in base_html
    assert "pf-nav-controls" in base_html
    assert "pf-nav-links hide-on-med-and-down" in base_html
    assert "pf-main-nav" in style_css
    assert ".pf-nav-top-row" in style_css
    assert ".pf-nav-controls" in style_css
    assert ".pf-nav-links" in style_css
    assert "display: flex" in style_css
    assert "align-items: center" in style_css
    assert ".pf-nav-icon-button .material-icons" in style_css
    assert ".pf-language-form" in style_css
    assert "height: 2.5rem" in style_css
    assert "line-height: 1" in style_css


def test_indi_backlash_controls_are_present():
    indi_html = (VIEWS_DIR / "indi_mount.html").read_text()
    server_py = SERVER_PATH.read_text()

    assert 'id="indi_backlash_form"' in indi_html
    assert 'id="backlash_current_value"' in indi_html
    assert 'id="backlash_auto_status"' in indi_html
    assert 'name="backlash_ra"' in indi_html
    assert 'name="backlash_de"' in indi_html
    assert 'data-axis="ra"' in indi_html
    assert 'data-axis="de"' in indi_html
    assert "/indi/backlash/auto" in indi_html

    assert '@app.route("/indi/backlash", methods=["POST"])' in server_py
    assert '@app.route("/indi/backlash/auto", methods=["POST"])' in server_py


def test_red_theme_is_defined_without_overriding_log_viewer_colors():
    style_css = (VIEWS_DIR / "css" / "style.css").read_text()
    logs_html = (VIEWS_DIR / "logs.html").read_text()

    assert 'html[data-theme="red"]' in style_css
    assert "--pf-bg: #080000" in style_css
    assert 'html[data-theme="red"] .log-container' not in style_css
    assert ".pf-fullscreen-menu" in style_css
    assert ".log-container" in logs_html
    assert "color: #d4d4d4" in logs_html
    assert "logLine.style.color = '#ff6b6b'" in logs_html


def test_log_toolbar_controls_wrap_and_center_button_content():
    logs_html = (VIEWS_DIR / "logs.html").read_text()

    assert "flex-wrap: wrap" in logs_html
    assert ".controls .btn" in logs_html
    assert "display: inline-flex" in logs_html
    assert "align-items: center" in logs_html
    assert ".controls .btn .material-icons.left" in logs_html
    assert "float: none" in logs_html
    assert "flex: 1 1 16rem" in logs_html


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
