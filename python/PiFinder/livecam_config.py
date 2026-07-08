#!/usr/bin/python
# -*- coding:utf-8 -*-
"""Lightweight LiveCam settings helpers.

This module intentionally avoids importing numpy/Pillow so disabled LiveCam
status/control paths do not load the heavier RAW processing stack.
"""

from __future__ import annotations

from typing import Any


CONFIG_PREFIX = "livecam_"
SOURCE_ORIGINAL = "original_raw"
SOURCE_CROPPED = "cropped_raw"
OUTPUT_LATEST = "latest_selected_raw"
OUTPUT_STACK = "stack"
VALID_SOURCES = {SOURCE_ORIGINAL, SOURCE_CROPPED}
VALID_OUTPUTS = {OUTPUT_LATEST, OUTPUT_STACK}
VALID_STACK_MODES = {"mean", "sum", "max"}
VALID_PREVIEW_MODES = {"raw_display", "stretched", "bayer_2x2_average"}
VALID_IMAGE_FORMATS = {"png", "jpeg", "webp"}
COLOR_MODE_THEME = "theme"
COLOR_MODE_COLOR = "color"
VALID_COLOR_MODES = {COLOR_MODE_THEME, COLOR_MODE_COLOR}


DEFAULT_SETTINGS: dict[str, Any] = {
    "processing_enabled": False,
    "input_frame_source": SOURCE_ORIGINAL,
    "output_source": OUTPUT_LATEST,
    "stack_enabled": False,
    "stack_mode": "mean",
    "stack_frame_limit": 10,
    "preview_mode": "raw_display",
    "color_mode": COLOR_MODE_THEME,
    "low_percentile": 1.0,
    "high_percentile": 99.5,
    "display_size": 768,
    "web_image_format": "jpeg",
}


def normalize_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(DEFAULT_SETTINGS)
    if settings:
        merged.update(settings)

    merged["processing_enabled"] = _coerce_bool(merged.get("processing_enabled"))
    merged["stack_enabled"] = _coerce_bool(merged.get("stack_enabled"))

    if merged.get("input_frame_source") not in VALID_SOURCES:
        merged["input_frame_source"] = SOURCE_ORIGINAL
    if merged.get("output_source") not in VALID_OUTPUTS:
        merged["output_source"] = OUTPUT_LATEST
    if merged.get("stack_mode") not in VALID_STACK_MODES:
        merged["stack_mode"] = "mean"
    if merged.get("preview_mode") not in VALID_PREVIEW_MODES:
        merged["preview_mode"] = "raw_display"
    if str(merged.get("color_mode")).lower() == "thema":
        merged["color_mode"] = COLOR_MODE_THEME
    if merged.get("color_mode") not in VALID_COLOR_MODES:
        merged["color_mode"] = COLOR_MODE_THEME
    if merged.get("web_image_format") not in VALID_IMAGE_FORMATS:
        merged["web_image_format"] = "jpeg"

    merged["low_percentile"] = _coerce_float(merged.get("low_percentile"), 1.0)
    merged["high_percentile"] = _coerce_float(merged.get("high_percentile"), 99.5)
    merged["stack_frame_limit"] = max(
        1, min(60, _coerce_int(merged.get("stack_frame_limit"), 10))
    )
    merged["display_size"] = max(
        128, min(2048, _coerce_int(merged.get("display_size"), 768))
    )
    return merged


def settings_from_config(cfg) -> dict[str, Any]:
    values = {}
    for key, default in DEFAULT_SETTINGS.items():
        values[key] = cfg.get_option(f"{CONFIG_PREFIX}{key}", default)
    values["display_rotation_degrees"] = display_rotation_degrees(cfg)
    return normalize_settings(values)


def save_settings_to_config(cfg, settings: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_settings(settings)
    for key in DEFAULT_SETTINGS:
        value = normalized[key]
        cfg.set_option(f"{CONFIG_PREFIX}{key}", value)
    return normalized


def display_rotation_degrees(cfg) -> int:
    camera_rotation = cfg.get_option("camera_rotation")
    if camera_rotation is not None:
        return (int(camera_rotation) * -1) % 360

    screen_direction = cfg.get_option("screen_direction")
    if screen_direction in ["right", "straight", "flat3", "as_bloom"]:
        return 90
    return 270


def processing_enabled(settings: dict[str, Any] | None = None) -> bool:
    """Return whether the LiveCam pipeline should touch RAW frames."""

    return bool(normalize_settings(settings)["processing_enabled"])


def disabled_status(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_settings(settings)
    return {
        "settings": normalized,
        "frame": None,
        "stack": {
            "processing_enabled": normalized["processing_enabled"],
            "input_frame_source": normalized["input_frame_source"],
            "stack_enabled": normalized["stack_enabled"],
            "output_source": normalized["output_source"],
            "mode": normalized["stack_mode"],
            "frame_limit": normalized["stack_frame_limit"],
            "frame_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "raw_shape": None,
            "display_shape": None,
            "web_image_format": normalized["web_image_format"],
            "last_error": None,
            "last_reject_reason": "disabled",
        },
        "enabled": False,
        "has_frame": False,
    }


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
