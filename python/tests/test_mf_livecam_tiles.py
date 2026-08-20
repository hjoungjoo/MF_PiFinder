"""Unit coverage for the optional MF wide-angle LiveCam overlay."""

import pytest

from PiFinder.mf_livecam_tiles import (
    active_focal_length_mm,
    excluded_tile_ids,
    optics_key,
    overlay_payload,
    wide_tiles_enabled,
)


pytestmark = pytest.mark.unit


def test_only_lenses_at_or_below_ten_mm_enable_livecam_tiles():
    assert wide_tiles_enabled("10mm")
    assert wide_tiles_enabled("16mm", 8.5)
    assert not wide_tiles_enabled("12mm")
    assert not wide_tiles_enabled("12mm", 10.1)
    assert not wide_tiles_enabled("")
    assert active_focal_length_mm("16mm", 7.64) == 7.6


def test_overlay_payload_is_normalized_and_keeps_exclusions():
    payload = overlay_payload(
        camera_type="imx296",
        lens_key="8mm",
        manual_focal_length_mm=None,
        frame_hw=(1080, 1920),
        excluded_ids=["C", "NE", 3],
    )

    assert payload["enabled"]
    assert payload["focal_length_mm"] == 8.0
    assert payload["tiles"]
    assert any(tile["id"] == "C" and tile["excluded"] for tile in payload["tiles"])
    assert all(0 <= tile["x"] <= 1 for tile in payload["tiles"])
    assert all(0 < tile["width"] <= 1 for tile in payload["tiles"])
    assert all(
        tile["crop_width"] == pytest.approx(512 / 1920) for tile in payload["tiles"]
    )
    assert all(
        tile["crop_height"] == pytest.approx(512 / 1080) for tile in payload["tiles"]
    )
    # Display cells are deliberately non-overlapping hit targets, unlike the
    # real crops used by the eventual solver.
    top = min(tile["y"] for tile in payload["tiles"])
    left = min(tile["x"] for tile in payload["tiles"])
    assert sum(
        tile["width"] for tile in payload["tiles"] if tile["y"] == top
    ) == pytest.approx(1.0)
    assert sum(
        tile["height"] for tile in payload["tiles"] if tile["x"] == left
    ) == pytest.approx(1.0)
    centre = next(tile for tile in payload["tiles"] if tile["id"] == "C")
    assert centre["x"] <= 0.5 <= centre["x"] + centre["width"]
    assert centre["y"] <= 0.5 <= centre["y"] + centre["height"]
    assert payload["overlaps"]
    assert all(0 < overlap["width"] <= 1 for overlap in payload["overlaps"])


def test_nonwide_or_missing_frame_never_emits_tiles():
    assert (
        overlay_payload(
            camera_type="imx296",
            lens_key="12mm",
            manual_focal_length_mm=None,
            frame_hw=(1080, 1920),
        )["tiles"]
        == []
    )
    assert (
        overlay_payload(
            camera_type="imx296",
            lens_key="8mm",
            manual_focal_length_mm=None,
            frame_hw=None,
        )["tiles"]
        == []
    )


def test_persistent_exclusions_are_scoped_to_optical_train():
    assert optics_key("imx296", "8mm") != optics_key("imx296", "10mm")
    assert optics_key("imx296", "16mm", 8.0) != optics_key("imx296", "8mm")
    assert excluded_tile_ids(["C", "NE", "", 4]) == {"C", "NE"}
