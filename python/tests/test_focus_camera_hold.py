"""MF regression: a temporary Focus visit must not reselect camera regimes."""

import pytest

from PiFinder.camera_interface import CameraInterface

pytestmark = pytest.mark.unit


class RecordingCamera(CameraInterface):
    def __init__(self, regime, manual_gain):
        self.exposure_time = 437_000
        self.gain = 17.5
        self._gain_mode = "manual" if manual_gain else "profile"
        self._auto_exposure_enabled = regime in ("auto", "auto_star", "snr")
        self._native_ae_enabled = regime == "native"
        self._auto_exposure_mode = "snr" if regime == "snr" else "pid"
        self._ae_controller_choice = (
            "star_count" if regime == "auto_star" else "match_count"
        )
        self._auto_exposure_star = object()
        self.controls = []
        self.resets = []

    def set_camera_config(self, exposure_time, gain):
        self.controls.append((exposure_time, gain))
        return exposure_time, gain

    def set_native_ae(self, enabled):
        self.controls.append(("native", enabled))
        return True

    def reset_framewise_auto_star(self, *, gain_locked=False):
        self.resets.append(gain_locked)


@pytest.mark.parametrize("regime", ("manual", "auto", "auto_star", "native", "snr"))
@pytest.mark.parametrize("manual_gain", (False, True))
def test_hold_restores_actual_runtime_state_and_manual_gain(regime, manual_gain):
    camera = RecordingCamera(regime, manual_gain)
    original = {
        name: value
        for name, value in vars(camera).items()
        if name not in ("controls", "resets")
    }
    assert camera._handle_focus_exposure_command("focus_begin:one:437000")
    assert not camera._auto_exposure_enabled
    assert not camera._native_ae_enabled
    assert camera._handle_focus_exposure_command("focus_set:one:800000")
    assert camera.exposure_time == 800_000
    assert camera.gain == 17.5
    assert camera._handle_focus_exposure_command("focus_end:one")
    for name, value in original.items():
        assert getattr(camera, name) == value
    assert camera.resets == [manual_gain]
    if regime == "native":
        assert camera.controls[-1] == ("native", True)
    calls = camera.controls.copy()
    camera._handle_focus_exposure_command("focus_end:one")
    assert camera.controls == calls


def test_old_token_cannot_release_or_nudge_a_new_focus_visit():
    camera = RecordingCamera("auto_star", True)
    camera._handle_focus_exposure_command("focus_begin:one:400000")
    camera._handle_focus_exposure_command("focus_set:one:800000")
    camera._handle_focus_exposure_command("focus_begin:two:200000")
    camera._handle_focus_exposure_command("focus_end:one")
    camera._handle_focus_exposure_command("focus_set:one:1000000")
    assert camera.exposure_time == 200_000
    assert camera._focus_exposure_token == "two"
    camera._handle_focus_exposure_command("focus_end:two")
    assert camera.exposure_time == 437_000
    assert camera._auto_exposure_enabled
    assert camera._gain_mode == "manual"


@pytest.mark.parametrize(
    "command",
    (
        "set_gain:9",
        "set_exp:200000",
        "set_exp_transient:auto_star",
        "set_ae_mode:snr",
        "exp_up",
        "exp_dn",
        "exp_save",
        "stop",
        "capture_exp_sweep:1",
    ),
)
def test_explicit_control_releases_hold_before_existing_handler_takes_over(command):
    camera = RecordingCamera("auto_star", True)
    camera._handle_focus_exposure_command("focus_begin:one:800000")
    assert not camera._handle_focus_exposure_command(command)
    assert camera.exposure_time == 437_000
    assert camera._auto_exposure_enabled
    # Emulate the ordinary handler's new choice. A queued old UI event is inert.
    camera.exposure_time = 200_000
    camera._handle_focus_exposure_command("focus_set:one:1000000")
    camera._handle_focus_exposure_command("focus_end:one")
    assert camera.exposure_time == 200_000


def test_duplicate_begin_does_not_replace_the_saved_state():
    camera = RecordingCamera("auto_star", True)
    camera._handle_focus_exposure_command("focus_begin:one:800000")
    camera._handle_focus_exposure_command("focus_begin:one:200000")
    assert camera.exposure_time == 800_000
    camera._handle_focus_exposure_command("focus_end:one")
    assert camera.exposure_time == 437_000


def test_unrelated_commands_do_not_release_hold():
    camera = RecordingCamera("auto_star", True)
    camera._handle_focus_exposure_command("focus_begin:one:800000")
    assert not camera._handle_focus_exposure_command("save_stages")
    assert not camera._handle_focus_exposure_command("")
    assert camera._focus_exposure_token == "one"


@pytest.mark.parametrize("fail_on_begin", (False, True))
def test_failed_hold_change_restores_previous_runtime_state(monkeypatch, fail_on_begin):
    camera = RecordingCamera("auto_star", True)
    if not fail_on_begin:
        camera._handle_focus_exposure_command("focus_begin:one:400000")
    original_set = camera.set_camera_config

    def fail_only_for_hold(exposure_time, gain):
        if exposure_time == 800_000:
            raise RuntimeError("camera rejected control")
        return original_set(exposure_time, gain)

    monkeypatch.setattr(camera, "set_camera_config", fail_only_for_hold)
    command = "focus_begin:one:800000" if fail_on_begin else "focus_set:one:800000"
    with pytest.raises(RuntimeError, match="camera rejected control"):
        camera._handle_focus_exposure_command(command)
    assert camera.exposure_time == 437_000
    assert camera.gain == 17.5
    assert camera._auto_exposure_enabled
    assert camera._gain_mode == "manual"
    assert camera._focus_exposure_token is None
