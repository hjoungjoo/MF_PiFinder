"""Tests for MF star-preserving RAW preprocessing."""

import numpy as np
import pytest

from PiFinder.mf_star_only_preprocess import (
    MFStarOnlyAccumulator,
    MFStarOnlyConfig,
    _single_frame_component_mask,
    preprocess_star_evidence,
    robust_cell_maps,
)


pytestmark = pytest.mark.unit


def _gaussian_star(frame, y, x, amplitude=500.0, sigma=1.0):
    yy, xx = np.indices(frame.shape)
    frame += amplitude * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * sigma**2))


def test_robust_cell_background_tracks_gradient_without_star_peak():
    yy, xx = np.indices((120, 160))
    frame = (500.0 + yy * 1.5 + xx * 0.5).astype(np.float32)
    _gaussian_star(frame, 60, 80, amplitude=1200)

    background, rms = robust_cell_maps(frame, 10)

    assert background.shape == frame.shape
    assert rms.shape == frame.shape
    assert background[60, 80] < frame[60, 80] - 500
    assert np.all(np.isfinite(background))


def test_large_saturated_light_is_masked_but_faint_cloud_star_survives():
    yy, xx = np.indices((160, 200))
    frame = 600.0 + 900.0 * (xx / 199.0) + 100.0 * np.sin(yy / 15.0)
    frame = frame.astype(np.float32)
    frame[90:130, 70:130] = 4095.0
    _gaussian_star(frame, 45, 155, amplitude=350, sigma=1.0)

    signal, evidence, hard, diagnostics = preprocess_star_evidence(
        frame, saturation_level=4095
    )

    assert hard[105, 100]
    assert signal[105, 100] == 0
    assert evidence[45, 155] > 2.5
    assert signal[45, 155] > 0
    assert diagnostics.hard_mask_fraction > 0


def test_bayer_phase_offsets_are_not_mistaken_for_point_sources():
    frame = np.empty((160, 200), dtype=np.float32)
    for row, column, value in (
        (0, 0, 2400.0),
        (0, 1, 1700.0),
        (1, 0, 1950.0),
        (1, 1, 2420.0),
    ):
        frame[row::2, column::2] = value

    signal, evidence, _hard, diagnostics = preprocess_star_evidence(
        frame, saturation_level=4095
    )

    assert np.max(signal) == 0
    assert np.max(evidence) == 0
    assert diagnostics.evidence_pixels == 0


def test_temporal_accumulator_prefers_repeated_faint_star_over_one_frame_glint():
    config = MFStarOnlyConfig(temporal_frames=5)
    accumulator = MFStarOnlyAccumulator(config)
    final = None
    for index in range(5):
        frame = np.full((160, 200), 800.0, dtype=np.float32)
        _gaussian_star(frame, 50, 60, amplitude=260, sigma=1.0)
        if index == 2:
            _gaussian_star(frame, 110, 150, amplitude=1800, sigma=1.0)
        final = accumulator.add(frame, saturation_level=4095, fingerprint="same")

    assert final is not None
    assert final.frame[50, 60] > final.frame[110, 150]
    assert final.diagnostics.frame_count == 5
    assert final.diagnostics.persistent_pixels > 0


def test_repeated_isolated_hot_pixel_is_rejected_by_spatial_psf_gate():
    accumulator = MFStarOnlyAccumulator(MFStarOnlyConfig(temporal_frames=5))
    final = None
    for _index in range(5):
        frame = np.full((160, 200), 800.0, dtype=np.float32)
        frame[100, 140] += 1800
        _gaussian_star(frame, 50, 60, amplitude=260, sigma=1.0)
        final = accumulator.add(frame, saturation_level=4095, fingerprint="same")

    assert final is not None
    assert final.frame[50, 60] > 100
    assert final.frame[100, 140] < 100


def test_compact_star_visible_in_one_cloud_gap_is_retained_but_capped():
    accumulator = MFStarOnlyAccumulator(MFStarOnlyConfig(temporal_frames=5))
    final = None
    for index in range(5):
        frame = np.full((160, 200), 800.0, dtype=np.float32)
        if index == 4:
            _gaussian_star(frame, 70, 80, amplitude=900, sigma=1.0)
        final = accumulator.add(frame, saturation_level=4095, fingerprint="same")

    assert final is not None
    assert final.frame[70, 80] > 100
    assert final.frame[70, 80] < 900


def test_weak_pixels_from_different_frames_cannot_form_one_fake_psf():
    config = MFStarOnlyConfig(minimum_psf_pixels=3)
    evidences = np.zeros((5, 20, 20), dtype=np.float32)
    evidences[0, 10, 9] = 4.0
    evidences[1, 10, 10] = 4.0
    evidences[2, 10, 11] = 4.0

    accepted = _single_frame_component_mask(evidences, config)

    assert not accepted.any()


def test_compact_psf_formed_inside_one_frame_is_retained():
    config = MFStarOnlyConfig(minimum_psf_pixels=3)
    evidences = np.zeros((5, 20, 20), dtype=np.float32)
    evidences[2, 10, 9:12] = 4.0

    accepted = _single_frame_component_mask(evidences, config)

    assert accepted[10, 10]
    assert np.count_nonzero(accepted) == 3


def test_fingerprint_change_resets_temporal_window():
    accumulator = MFStarOnlyAccumulator(MFStarOnlyConfig(temporal_frames=5))
    frame = np.full((120, 160), 500, dtype=np.uint16)
    accumulator.add(frame, saturation_level=4095, fingerprint=("6mm", 200000))
    result = accumulator.add(frame, saturation_level=4095, fingerprint=("8mm", 200000))

    assert accumulator.frame_count == 1
    assert result.diagnostics.reset_reason == "fingerprint_changed"
