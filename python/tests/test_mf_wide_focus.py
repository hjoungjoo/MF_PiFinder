"""Unit coverage for native-pixel wide-lens Focus helpers."""

import numpy as np
import pytest
from PIL import Image

from PiFinder import focus, mf_wide_focus


pytestmark = pytest.mark.unit


def test_native_focus_is_enabled_only_below_ten_mm():
    assert mf_wide_focus.wide_focus_enabled("6mm")
    assert mf_wide_focus.wide_focus_enabled("16mm", 8.0)
    assert not mf_wide_focus.wide_focus_enabled("10mm")
    assert not mf_wide_focus.wide_focus_enabled("16mm")


def test_native_linear_frame_preserves_compact_wide_stars_for_detection():
    raw = np.full((980, 980), 408, dtype=np.uint16)
    for y, x, peak in ((120, 220, 1300), (460, 520, 1800), (760, 360, 2100)):
        raw[y : y + 2, x : x + 2] = peak

    native = mf_wide_focus.native_focus_frame(raw, bias_offset=238.0, bit_depth=12)
    result = focus.focus_hfd(native, sigma_k=mf_wide_focus.WIDE_FOCUS_SIGMA_K)

    assert native.shape == (980, 980)
    assert len(result.blobs) == 3


def test_star_crop_stretch_is_display_only_and_reaches_full_contrast():
    raw = np.full((9, 9), 20, dtype=np.uint8)
    raw[4, 4] = 80
    image = Image.fromarray(raw)

    stretched = np.asarray(
        mf_wide_focus.stretch_star_crop(image, background=20, peak=80)
    )

    assert np.asarray(image)[4, 4] == 80
    assert stretched[4, 4] == 255
    assert stretched[0, 0] == 0


def test_native_color_stars_survive_bayer_pattern_and_sky_gradient():
    y, x = np.indices((980, 980))
    raw = 1000.0 + x * 0.7 + y * 0.2
    # The native color frame retains the Bayer lattice. Its large per-pixel
    # variation is not the noise of the smoothed image used to detect stars.
    raw += np.where((x + y) % 2, 350.0, -350.0)
    raw += np.random.default_rng(13).normal(0, 12, raw.shape)
    centers = ((120, 220), (460, 520), (760, 360))
    for cy, cx in centers:
        raw += 800 * np.exp(-((y - cy) ** 2 + (x - cx) ** 2) / (2 * 2.0**2))
    native = mf_wide_focus.native_focus_frame(
        raw.astype(np.uint16), bias_offset=238.0, bit_depth=12
    )

    result = focus.focus_hfd(native, sigma_k=mf_wide_focus.WIDE_FOCUS_SIGMA_K)

    assert len(result.blobs) == len(centers)
    assert result.n_used == len(centers)
    assert result.median_hfd is not None
    for cy, cx in centers:
        assert any(np.hypot(blob.y - cy, blob.x - cx) < 2 for blob in result.blobs)


def test_solver_centroids_scale_to_native_focus_coordinates():
    assert mf_wide_focus.scale_solver_centroids(
        [(256.0, 128.0)], native_hw=(980, 980)
    ) == pytest.approx([(490.0, 245.0)])
