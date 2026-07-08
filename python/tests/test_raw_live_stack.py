import io

import numpy as np
from PIL import Image

from PiFinder.raw_live_stack import (
    DisplayFrameBuilder,
    SOURCE_CROPPED,
    SOURCE_ORIGINAL,
    RawLiveStackProcessor,
    download_color_mode,
    download_image_format,
    normalize_settings,
    publish_selected_frame,
)
from PiFinder.livecam_config import processing_enabled
from PiFinder.livecam_config import DEFAULT_SETTINGS, default_settings_for_config
from PiFinder.sqm.camera_profiles import CameraProfile


class DummySharedState:
    def __init__(self):
        self._frame = None
        self.set_calls = 0

    def raw_live_frame(self):
        return self._frame

    def set_raw_live_frame(self, value):
        self.set_calls += 1
        self._frame = value


class DummyConfig:
    def __init__(self, options=None):
        self.options = options or {}

    def get_option(self, key, default=None):
        return self.options.get(key, default)


def _profile(rotation_90=0):
    return CameraProfile(
        format="R10",
        raw_size=(4, 3),
        analog_gain=1.0,
        crop_y=(1, 1),
        crop_x=(1, 1),
        rotation_90=rotation_90,
    )


def _bayer_profile():
    return CameraProfile(
        format="SRGGB12",
        raw_size=(4, 4),
        analog_gain=1.0,
        crop_y=(0, 0),
        crop_x=(0, 0),
        rotation_90=0,
    )


def test_publish_disabled_does_not_touch_shared_frame():
    shared = DummySharedState()
    shared.set_raw_live_frame({"frame": np.ones((2, 2), dtype=np.uint16)})
    set_calls = shared.set_calls

    publish_selected_frame(
        shared,
        {"processing_enabled": False},
        _profile(),
        "test",
        np.ones((3, 4), dtype=np.uint16),
        np.ones((1, 2), dtype=np.uint16),
    )

    assert shared.raw_live_frame() is not None
    assert shared.set_calls == set_calls


def test_processing_enabled_coerces_string_values():
    assert processing_enabled({"processing_enabled": "true"})
    assert not processing_enabled({"processing_enabled": "false"})


def test_default_settings_for_config_restores_livecam_defaults():
    defaults = default_settings_for_config(DummyConfig({"camera_rotation": 90}))

    for key, value in DEFAULT_SETTINGS.items():
        assert defaults[key] == value
    assert defaults["display_rotation_degrees"] == 270


def test_publish_original_rotates_without_crop():
    shared = DummySharedState()
    original = np.arange(12, dtype=np.uint16).reshape(3, 4)
    cropped = np.array([[99, 100]], dtype=np.uint16)

    publish_selected_frame(
        shared,
        {"processing_enabled": True, "input_frame_source": SOURCE_ORIGINAL},
        _profile(rotation_90=1),
        "test",
        original,
        cropped,
    )

    entry = shared.raw_live_frame()
    assert entry["info"]["source"] == SOURCE_ORIGINAL
    assert entry["frame"].shape == (4, 3)
    np.testing.assert_array_equal(entry["frame"], np.rot90(original, 1))


def test_publish_cropped_uses_cropped_frame():
    shared = DummySharedState()
    original = np.arange(12, dtype=np.uint16).reshape(3, 4)
    cropped = np.array([[99, 100]], dtype=np.uint16)

    publish_selected_frame(
        shared,
        {"processing_enabled": True, "input_frame_source": SOURCE_CROPPED},
        _profile(rotation_90=1),
        "test",
        original,
        cropped,
    )

    entry = shared.raw_live_frame()
    assert entry["info"]["source"] == SOURCE_CROPPED
    np.testing.assert_array_equal(entry["frame"], cropped)


def test_publish_applies_display_rotation_after_source_selection():
    shared = DummySharedState()
    original = np.arange(12, dtype=np.uint16).reshape(3, 4)
    cropped = np.array([[1, 2], [3, 4]], dtype=np.uint16)

    publish_selected_frame(
        shared,
        {
            "processing_enabled": True,
            "input_frame_source": SOURCE_CROPPED,
            "display_rotation_degrees": 90,
        },
        _profile(),
        "test",
        original,
        cropped,
    )

    entry = shared.raw_live_frame()
    np.testing.assert_array_equal(entry["frame"], np.rot90(cropped, 1))
    assert entry["info"]["display_rotation_degrees"] == 90


def test_processor_renders_selected_raw_image():
    shared = DummySharedState()
    frame = np.arange(100, dtype=np.uint16).reshape(10, 10)
    publish_selected_frame(
        shared,
        {"processing_enabled": True},
        _profile(),
        "test",
        frame,
        frame[2:8, 2:8],
    )

    settings = normalize_settings(
        {
            "processing_enabled": True,
            "display_size": 64,
            "web_image_format": "png",
        }
    )
    processor = RawLiveStackProcessor()

    rendered = processor.render_image(shared, settings)

    assert rendered is not None
    image_bytes, mimetype = rendered
    assert mimetype == "image/png"
    assert image_bytes.startswith(b"\x89PNG")


def test_display_size_zero_keeps_original_display_dimensions():
    builder = DisplayFrameBuilder(display_size=0)

    image = builder.build(np.arange(50, dtype=np.uint16).reshape(5, 10))

    assert image.size == (10, 5)


def test_processor_keeps_rolling_stack_frame_limit():
    shared = DummySharedState()
    processor = RawLiveStackProcessor()
    settings = normalize_settings(
        {
            "processing_enabled": True,
            "stack_enabled": True,
            "output_source": "stack",
            "stack_mode": "mean",
            "stack_frame_limit": 2,
            "display_size": 64,
        }
    )

    for frame_id, value in enumerate([10, 20, 30], start=1):
        frame = np.full((4, 4), value, dtype=np.uint16)
        publish_selected_frame(
            shared,
            {"processing_enabled": True},
            _profile(),
            "test",
            frame,
            frame,
            metadata={"timestamp": float(frame_id), "frame_id": frame_id},
        )
        assert processor.render_image(shared, settings) is not None

    status = processor.status(shared, settings)
    assert status["stack"]["frame_count"] == 2
    assert status["stack"]["accepted_count"] == 3
    np.testing.assert_allclose(processor._stack_display_frame(settings), 25.0)


def test_theme_color_mode_tints_luminance_image():
    builder = DisplayFrameBuilder(
        display_size=4,
        color_mode="theme",
        web_theme="red",
    )

    image = builder.build(np.arange(16, dtype=np.uint8).reshape(4, 4))

    assert image.mode == "RGB"
    r, g, b = image.getpixel((3, 3))
    assert r > g > b


def test_download_uses_png_when_preview_format_is_webp():
    assert download_image_format({"web_image_format": "webp"}) == "png"
    assert download_image_format({"web_image_format": "jpeg"}) == "jpeg"


def test_download_color_mode_overrides_theme_tint():
    shared = DummySharedState()
    frame = np.arange(100, dtype=np.uint16).reshape(10, 10)
    publish_selected_frame(
        shared,
        {"processing_enabled": True},
        _bayer_profile(),
        "test",
        frame,
        frame,
        metadata={"timestamp": 1.0, "frame_id": 1},
    )
    settings = normalize_settings(
        {
            "processing_enabled": True,
            "color_mode": "theme",
            "web_image_format": "png",
            "display_size": 64,
        }
    )
    processor = RawLiveStackProcessor()

    themed_bytes, _ = processor.render_image(shared, settings, web_theme="red")
    color_bytes, _ = processor.render_image(
        shared,
        settings,
        image_format="png",
        color_mode=download_color_mode(),
        web_theme="red",
        accept_new_frame=False,
    )

    assert Image.open(io.BytesIO(themed_bytes)).mode == "RGB"
    assert Image.open(io.BytesIO(color_bytes)).mode == "RGB"
