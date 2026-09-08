"""Web controls must share the controller's speed ownership and motion lease."""

from queue import Queue

import pytest

from PiFinder import server as module


pytestmark = pytest.mark.unit


@pytest.fixture
def motion_client(monkeypatch, tmp_path):
    monkeypatch.setattr(module.config.utils, "data_dir", tmp_path)
    monkeypatch.setattr(module.config.utils, "runtime_dir", tmp_path)
    monkeypatch.setattr(module.sys_utils, "get_indi_profile_drivers", lambda: {})
    monkeypatch.setattr(
        module.sys_utils, "get_indi_profile_device_name", lambda: "LX200 OnStepX"
    )

    def unexpected_write(*args, **kwargs):
        pytest.fail("Web route bypassed the motion controller")

    monkeypatch.setattr(
        module.sys_utils, "apply_indi_onstep_properties", unexpected_write
    )
    queue = Queue()
    server = module.Server(mountcontrol_queue=queue)
    server.app.testing = True
    client = server.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client, queue, server


@pytest.mark.parametrize(
    "url,form,expected",
    [
        ("/indi/slew_rate", {"slew_rate": "6"}, {"type": "set_slew_rate", "rate": 6}),
        (
            "/indi/guide_rate",
            {"guide_rate": "0.5"},
            {"type": "set_guide_rate", "rate": 0.5},
        ),
        (
            "/indi/motion",
            {"direction": "east"},
            {
                "type": "manual_movement",
                "direction": "east",
                "lease_seconds": module.WEB_MOTION_LEASE_SECONDS,
            },
        ),
        (
            "/indi/motion",
            {"direction": "east", "keepalive": "1"},
            {
                "type": "manual_movement_keepalive",
                "direction": "east",
                "lease_seconds": module.WEB_MOTION_LEASE_SECONDS,
            },
        ),
        ("/indi/motion", {"direction": "stop"}, {"type": "stop_movement"}),
    ],
)
def test_speed_and_motion_share_controller(motion_client, url, form, expected):
    client, queue, _ = motion_client
    response = client.post(
        url, data=form, headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert response.status_code == 200
    assert queue.get_nowait() == expected
    assert queue.empty()


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1", "241", "bad"])
def test_invalid_guide_rate_never_queued(motion_client, value):
    client, queue, _ = motion_client
    response = client.post("/indi/guide_rate", data={"guide_rate": value})
    assert response.status_code == 400
    assert queue.empty()


def test_no_controller_does_not_fall_back_to_unmanaged_motion(motion_client):
    client, queue, server = motion_client
    server.mountcontrol_queue = None
    response = client.post(
        "/indi/motion",
        data={"direction": "north"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 400
    assert queue.empty()
