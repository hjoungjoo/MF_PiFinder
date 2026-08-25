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


def test_solver_centroids_scale_to_native_focus_coordinates():
    assert mf_wide_focus.scale_solver_centroids(
        [(256.0, 128.0)], native_hw=(980, 980)
    ) == pytest.approx([(490.0, 245.0)])
