#!/usr/bin/python
# -*- coding:utf-8 -*-
"""RAW LiveCam preview and lightweight live-stack helpers.

This module keeps the new RAW preview/stack behavior away from the existing
camera and solver paths. Camera backends publish one selected RAW frame here;
the web API then turns that frame into a display-sized PNG/JPEG/WebP image.
"""

from __future__ import annotations

import io
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from PIL import Image

from PiFinder.livecam_config import (
    COLOR_MODE_COLOR,
    COLOR_MODE_THEME,
    OUTPUT_LATEST,
    SOURCE_CROPPED,
    SOURCE_ORIGINAL,
    VALID_IMAGE_FORMATS,
    normalize_settings,
)

RESAMPLE_BILINEAR = getattr(getattr(Image, "Resampling", Image), "BILINEAR")


@dataclass
class RawFrameInfo:
    source: str
    shape: tuple[int, int]
    dtype: str
    raw_format: str | None
    rotation_90: int
    display_rotation_degrees: int
    min_value: float
    max_value: float
    p01: float
    p50: float
    p995: float
    camera_type: str | None = None
    exposure_us: float | None = None
    gain: float | None = None
    timestamp: float | None = None
    frame_id: int | None = None

    @classmethod
    def from_array(
        cls,
        frame: np.ndarray,
        *,
        source: str,
        rotation_90: int,
        display_rotation_degrees: int = 0,
        raw_format: str | None = None,
        camera_type: str | None = None,
        exposure_us: float | None = None,
        gain: float | None = None,
        timestamp: float | None = None,
        frame_id: int | None = None,
    ) -> "RawFrameInfo":
        arr = np.asarray(frame)
        if arr.size == 0:
            min_value = max_value = p01 = p50 = p995 = 0.0
        else:
            min_value = float(np.min(arr))
            max_value = float(np.max(arr))
            p01, p50, p995 = [float(v) for v in np.percentile(arr, [1.0, 50.0, 99.5])]
        return cls(
            source=source,
            shape=tuple(int(v) for v in arr.shape[:2]),
            dtype=str(arr.dtype),
            raw_format=raw_format,
            rotation_90=rotation_90,
            display_rotation_degrees=display_rotation_degrees,
            min_value=min_value,
            max_value=max_value,
            p01=p01,
            p50=p50,
            p995=p995,
            camera_type=camera_type,
            exposure_us=exposure_us,
            gain=gain,
            timestamp=timestamp,
            frame_id=frame_id,
        )


@dataclass
class StackState:
    processing_enabled: bool
    input_frame_source: str
    stack_enabled: bool
    output_source: str
    mode: str
    frame_limit: int
    frame_count: int
    accepted_count: int
    rejected_count: int
    raw_shape: tuple[int, int] | None
    display_shape: tuple[int, int] | None
    web_image_format: str
    last_error: str | None
    last_reject_reason: str | None


def publish_selected_frame(
    shared_state,
    settings: dict[str, Any],
    profile,
    camera_type: str,
    original_raw: np.ndarray,
    cropped_raw: np.ndarray,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Publish the currently selected RAW frame for LiveCam consumers.

    Disabled processing returns before touching shared state. The control API
    clears any previous shared frame once when the top-level switch is turned
    off, so the camera loop does not pay a per-frame manager write cost.
    """

    normalized = normalize_settings(settings)
    if not normalized["processing_enabled"]:
        return

    metadata = metadata or {}
    source = normalized["input_frame_source"]
    rotation_90 = int(getattr(profile, "rotation_90", 0) or 0)
    display_rotation = int(normalized.get("display_rotation_degrees", 0) or 0) % 360

    if source == SOURCE_CROPPED:
        selected = cropped_raw
    else:
        selected = original_raw
        if rotation_90:
            selected = np.rot90(selected, rotation_90)

    selected = _rotate_display(selected, display_rotation)
    selected = np.ascontiguousarray(selected).copy()
    timestamp = float(metadata.get("timestamp") or time.time())
    frame_id = int(metadata.get("frame_id") or time.time_ns())
    info = RawFrameInfo.from_array(
        selected,
        source=source,
        rotation_90=rotation_90 if source == SOURCE_ORIGINAL else 0,
        display_rotation_degrees=display_rotation,
        raw_format=getattr(profile, "format", None),
        camera_type=camera_type,
        exposure_us=_optional_float(metadata.get("ExposureTime")),
        gain=_optional_float(metadata.get("AnalogueGain")),
        timestamp=timestamp,
        frame_id=frame_id,
    )
    shared_state.set_raw_live_frame({"frame": selected, "info": asdict(info)})


class DisplayFrameBuilder:
    def __init__(
        self,
        low_percentile: float = 1.0,
        high_percentile: float = 99.5,
        display_size: int = 768,
        preview_mode: str = "raw_display",
        color_mode: str = COLOR_MODE_THEME,
        web_theme: str = "grey",
        raw_format: str | None = None,
    ) -> None:
        self.low_percentile = low_percentile
        self.high_percentile = high_percentile
        self.display_size = display_size
        self.preview_mode = preview_mode
        self.color_mode = color_mode
        self.web_theme = web_theme
        self.raw_format = raw_format

    def build(self, frame: np.ndarray) -> Image.Image:
        arr = _prepare_display_frame(np.asarray(frame), self.preview_mode, self.raw_format)

        scaled = _percentile_stretch(
            arr,
            low_percentile=self.low_percentile,
            high_percentile=self.high_percentile,
        )
        if self.color_mode == COLOR_MODE_THEME:
            image = Image.fromarray(_theme_tint(_luminance(scaled), self.web_theme), mode="RGB")
        elif scaled.ndim == 3:
            image = Image.fromarray(scaled, mode="RGB")
        else:
            image = Image.fromarray(scaled, mode="L")
        image.thumbnail((self.display_size, self.display_size), RESAMPLE_BILINEAR)
        return image


class RawLiveStackProcessor:
    def __init__(self) -> None:
        self._frames: deque[np.ndarray] = deque()
        self._accumulator: np.ndarray | None = None
        self._max_frame: np.ndarray | None = None
        self._frame_count = 0
        self._accepted_count = 0
        self._rejected_count = 0
        self._last_frame_id: int | None = None
        self._raw_shape: tuple[int, int] | None = None
        self._input_source: str | None = None
        self._mode = "mean"
        self._frame_limit = 10
        self._last_error: str | None = None
        self._last_reject_reason: str | None = None
        self._last_display_shape: tuple[int, int] | None = None

    def reset(self) -> None:
        self._frames.clear()
        self._accumulator = None
        self._max_frame = None
        self._frame_count = 0
        self._accepted_count = 0
        self._rejected_count = 0
        self._last_frame_id = None
        self._raw_shape = None
        self._input_source = None
        self._frame_limit = 10
        self._last_error = None
        self._last_reject_reason = None
        self._last_display_shape = None

    def status(self, shared_state, settings: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_settings(settings)
        entry = _shared_entry(shared_state) if normalized["processing_enabled"] else None
        info = entry.get("info") if entry else None
        return {
            "settings": normalized,
            "frame": info,
            "stack": asdict(
                StackState(
                    processing_enabled=normalized["processing_enabled"],
                    input_frame_source=normalized["input_frame_source"],
                    stack_enabled=normalized["stack_enabled"],
                    output_source=normalized["output_source"],
                    mode=normalized["stack_mode"],
                    frame_limit=normalized["stack_frame_limit"],
                    frame_count=self._frame_count,
                    accepted_count=self._accepted_count,
                    rejected_count=self._rejected_count,
                    raw_shape=self._raw_shape,
                    display_shape=self._last_display_shape,
                    web_image_format=normalized["web_image_format"],
                    last_error=self._last_error,
                    last_reject_reason=self._last_reject_reason,
                )
            ),
            "enabled": normalized["processing_enabled"],
            "has_frame": bool(entry),
        }

    def render_image(
        self,
        shared_state,
        settings: dict[str, Any],
        *,
        image_format: str | None = None,
        web_theme: str = "grey",
        color_mode: str | None = None,
        accept_new_frame: bool = True,
    ) -> tuple[bytes, str] | None:
        normalized = normalize_settings(settings)
        if not normalized["processing_enabled"]:
            return None
        if color_mode is not None:
            normalized["color_mode"] = color_mode

        entry = _shared_entry(shared_state)
        if not entry:
            self._last_reject_reason = "no-frame"
            return None

        frame = np.asarray(entry["frame"])
        info = entry.get("info") or {}
        frame_id = info.get("frame_id")

        if normalized["stack_enabled"] and accept_new_frame:
            self._accept_frame(frame, info, normalized)
            display_source = self._stack_display_frame(normalized)
            if display_source is None:
                display_source = frame
        elif normalized["stack_enabled"]:
            display_source = self._stack_display_frame(normalized)
            if display_source is None:
                display_source = frame
        else:
            display_source = frame
            self._last_frame_id = frame_id

        if normalized["output_source"] == OUTPUT_LATEST or not normalized["stack_enabled"]:
            display_source = frame

        builder = DisplayFrameBuilder(
            low_percentile=normalized["low_percentile"],
            high_percentile=normalized["high_percentile"],
            display_size=normalized["display_size"],
            preview_mode=normalized["preview_mode"],
            color_mode=normalized["color_mode"],
            web_theme=web_theme,
            raw_format=info.get("raw_format"),
        )
        image = builder.build(display_source)
        self._last_display_shape = tuple(int(v) for v in image.size[::-1])
        return encode_image(image, image_format or normalized["web_image_format"])

    def _accept_frame(
        self, frame: np.ndarray, info: dict[str, Any], settings: dict[str, Any]
    ) -> None:
        frame_id = info.get("frame_id")
        source = info.get("source")
        shape = tuple(int(v) for v in frame.shape[:2])
        mode = settings["stack_mode"]
        frame_limit = int(settings["stack_frame_limit"])

        if frame_id is not None and frame_id == self._last_frame_id:
            return

        reset_needed = (
            self._raw_shape != shape
            or self._input_source != source
            or self._mode != mode
            or self._frame_limit != frame_limit
        )
        if reset_needed:
            self.reset()
            self._raw_shape = shape
            self._input_source = source
            self._mode = mode
            self._frame_limit = frame_limit

        self._last_frame_id = frame_id

        frame_sample = np.asarray(frame).copy()
        frame_f = frame_sample.astype(np.float32, copy=False)

        if mode == "max":
            self._frames.append(frame_sample)
            self._max_frame = _max_from_frames(self._frames)
        else:
            if self._accumulator is None:
                self._accumulator = np.zeros(shape, dtype=np.float32)
            self._frames.append(frame_sample)
            self._accumulator += frame_f

        while len(self._frames) > frame_limit:
            old = self._frames.popleft()
            if mode == "max":
                self._max_frame = _max_from_frames(self._frames)
            else:
                self._accumulator -= old.astype(np.float32, copy=False)

        self._frame_count = len(self._frames)
        self._accepted_count += 1
        self._last_reject_reason = None

    def _stack_display_frame(self, settings: dict[str, Any]) -> np.ndarray | None:
        if settings["stack_mode"] == "max":
            return self._max_frame
        if self._accumulator is None:
            return None
        if settings["stack_mode"] == "sum":
            return self._accumulator
        return self._accumulator / max(1, len(self._frames))


def encode_image(image: Image.Image, image_format: str) -> tuple[bytes, str]:
    fmt = image_format if image_format in VALID_IMAGE_FORMATS else "jpeg"
    pil_format = {"jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}[fmt]
    mimetype = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[fmt]
    buf = io.BytesIO()
    if fmt == "jpeg":
        if image.mode not in {"L", "RGB"}:
            image = image.convert("RGB")
        image.save(buf, format=pil_format, quality=85, optimize=True)
    else:
        image.save(buf, format=pil_format)
    return buf.getvalue(), mimetype


def download_image_format(settings: dict[str, Any]) -> str:
    normalized = normalize_settings(settings)
    fmt = normalized["web_image_format"]
    return "png" if fmt == "webp" else fmt


def download_color_mode() -> str:
    return COLOR_MODE_COLOR


def _theme_tint(luminance: np.ndarray, web_theme: str) -> np.ndarray:
    theme_rgb = {
        "red": (232.0, 75.0, 63.0),
        "grey": (224.0, 224.0, 224.0),
    }.get(web_theme, (224.0, 224.0, 224.0))
    alpha = np.asarray(luminance, dtype=np.float32) / 255.0
    tinted = np.empty((*alpha.shape, 3), dtype=np.uint8)
    for channel, color in enumerate(theme_rgb):
        tinted[..., channel] = np.clip(alpha * color, 0, 255).astype(np.uint8)
    return tinted


def _prepare_display_frame(
    frame: np.ndarray, preview_mode: str, raw_format: str | None
) -> np.ndarray:
    arr = np.asarray(frame)
    if arr.ndim == 3:
        return arr
    if _is_bayer_format(raw_format):
        return _bayer_2x2_rgb(arr, raw_format)
    if preview_mode == "bayer_2x2_average":
        return _bayer_2x2_average(arr)
    return arr


def _is_bayer_format(raw_format: str | None) -> bool:
    return _bayer_pattern(raw_format) is not None


def _bayer_pattern(raw_format: str | None) -> str | None:
    if not raw_format:
        return None
    normalized = str(raw_format).upper()
    for pattern in ("RGGB", "BGGR", "GRBG", "GBRG"):
        if pattern in normalized:
            return pattern
    return None


def _bayer_2x2_rgb(frame: np.ndarray, raw_format: str | None) -> np.ndarray:
    """Convert a Bayer mosaic into a half-resolution RGB image."""

    arr = np.asarray(frame, dtype=np.float32)
    h = arr.shape[0] - (arr.shape[0] % 2)
    w = arr.shape[1] - (arr.shape[1] % 2)
    if h <= 0 or w <= 0:
        return np.zeros((1, 1, 3), dtype=np.float32)
    arr = arr[:h, :w]
    pattern = _bayer_pattern(raw_format) or "RGGB"
    if pattern == "BGGR":
        red = arr[1::2, 1::2]
        green = (arr[0::2, 1::2] + arr[1::2, 0::2]) / 2.0
        blue = arr[0::2, 0::2]
    elif pattern == "GRBG":
        red = arr[0::2, 1::2]
        green = (arr[0::2, 0::2] + arr[1::2, 1::2]) / 2.0
        blue = arr[1::2, 0::2]
    elif pattern == "GBRG":
        red = arr[1::2, 0::2]
        green = (arr[0::2, 0::2] + arr[1::2, 1::2]) / 2.0
        blue = arr[0::2, 1::2]
    else:
        red = arr[0::2, 0::2]
        green = (arr[0::2, 1::2] + arr[1::2, 0::2]) / 2.0
        blue = arr[1::2, 1::2]
    return np.dstack((red, green, blue))


def _max_from_frames(frames: deque[np.ndarray]) -> np.ndarray | None:
    if not frames:
        return None
    max_frame = np.asarray(frames[0], dtype=np.float32).copy()
    for frame in list(frames)[1:]:
        np.maximum(max_frame, np.asarray(frame, dtype=np.float32), out=max_frame)
    return max_frame


def _rotate_display(frame: np.ndarray, degrees: int) -> np.ndarray:
    if degrees == 90:
        return np.rot90(frame, 1)
    if degrees == 180:
        return np.rot90(frame, 2)
    if degrees == 270:
        return np.rot90(frame, 3)
    return frame


def _shared_entry(shared_state) -> dict[str, Any] | None:
    if not hasattr(shared_state, "raw_live_frame"):
        return None
    entry = shared_state.raw_live_frame()
    if not entry or not isinstance(entry, dict) or "frame" not in entry:
        return None
    return entry


def _percentile_stretch(
    frame: np.ndarray, *, low_percentile: float, high_percentile: float
) -> np.ndarray:
    arr = np.asarray(frame, dtype=np.float32)
    if arr.size == 0:
        return np.zeros((1, 1), dtype=np.uint8)
    low, high = [float(v) for v in np.percentile(arr, [low_percentile, high_percentile])]
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        low = float(np.min(arr))
        high = float(np.max(arr))
    if high <= low:
        high = low + 1.0
    scaled = (arr - low) * (255.0 / (high - low))
    return np.clip(scaled, 0, 255).astype(np.uint8)


def _luminance(frame: np.ndarray) -> np.ndarray:
    arr = np.asarray(frame)
    if arr.ndim != 3:
        return arr
    return (
        arr[..., 0].astype(np.float32) * 0.299
        + arr[..., 1].astype(np.float32) * 0.587
        + arr[..., 2].astype(np.float32) * 0.114
    ).astype(np.uint8)


def _bayer_2x2_average(frame: np.ndarray) -> np.ndarray:
    arr = np.asarray(frame, dtype=np.float32)
    h = arr.shape[0] - (arr.shape[0] % 2)
    w = arr.shape[1] - (arr.shape[1] % 2)
    if h <= 0 or w <= 0:
        return arr
    arr = arr[:h, :w]
    return (
        arr[0::2, 0::2]
        + arr[1::2, 0::2]
        + arr[0::2, 1::2]
        + arr[1::2, 1::2]
    ) / 4.0


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
