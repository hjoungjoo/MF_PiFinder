from __future__ import annotations

import pytest

from PiFinder.auto_exposure_framewise import (
    AutoStarFrameController,
    ExposureGainAllocator,
    FrameExposureSample,
    RegionExposureStats,
    SolveExposureQuality,
    collect_spatial_frame_sample,
    matched_star_exposure_quality,
)

pytestmark = pytest.mark.unit


def _sample(
    sequence: int,
    *,
    exposure: float = 200_000,
    gain: float = 30.0,
    p50: float = 400.0,
    p99: float = 700.0,
    p999: float = 800.0,
    sat: float = 0.0,
    mad: float = 10.0,
    center_contaminated: bool = False,
    motion: float = 0.0,
) -> FrameExposureSample:
    region = RegionExposureStats(
        background_p50_adu=p50,
        background_mad_adu=mad,
        p90_adu=(p50 + p99) / 2,
        p99_adu=p99,
        p999_adu=p999,
        saturated_fraction=sat,
        background_gradient_adu=3.0,
    )
    regions = {
        name: region for name in ("UL", "U", "UR", "L", "C", "R", "DL", "D", "DR")
    }
    return FrameExposureSample(
        frame_id=sequence,
        frame_sequence=sequence,
        captured_at=float(sequence),
        actual_exposure_us=exposure,
        actual_gain=gain,
        white_level=4095.0,
        pedestal_adu=238.0,
        regions=regions,
        center_contaminated=center_contaminated,
        motion_degrees=motion,
    )


def _quality(
    frame_id: int,
    *,
    matches: int,
    candidates: int,
    success: bool,
    snr: float | None = 8.0,
) -> SolveExposureQuality:
    return SolveExposureQuality(
        frame_id=frame_id,
        source="peripheral_full",
        region_ids=("U", "L", "R", "D"),
        matched_stars=matches,
        candidate_stars=candidates,
        snr_p25=snr,
        snr_median=snr,
        rmse=0.5 if success else None,
        solve_success=success,
    )


def _controller(**kwargs) -> AutoStarFrameController:
    kwargs.setdefault("highlight_raise_confirm_frames", 1)
    return AutoStarFrameController(
        ExposureGainAllocator(max_gain=30.0),
        gain_min_dwell_frames=0,
        **kwargs,
    )


def test_low_headroom_requires_three_consecutive_frames_before_raise():
    controller = AutoStarFrameController(ExposureGainAllocator(max_gain=30.0))
    low = _sample(1, exposure=400_000, p50=2000, p99=2900, p999=3100)

    assert controller.on_frame(low) is None
    assert controller.status()["highlight_low_streak"] == 1
    assert (
        controller.on_frame(_sample(2, exposure=400_000, p50=2000, p99=2900, p999=3100))
        is None
    )
    target = controller.on_frame(
        _sample(3, exposure=400_000, p50=2000, p99=2900, p999=3100)
    )

    assert target is not None
    assert target.reason == "acquisition_highlight_exposure_up"
    assert controller.status()["highlight_low_streak"] == 0


def _settled_sample(sequence: int, **kwargs) -> FrameExposureSample:
    kwargs.setdefault("p50", 1200)
    kwargs.setdefault("p99", 3000)
    kwargs.setdefault("p999", 3478)
    return _sample(sequence, **kwargs)


def _with_peripheral_regions(
    sample: FrameExposureSample,
    values: dict[str, tuple[float, float, float, float]],
) -> FrameExposureSample:
    regions = dict(sample.regions)
    for name, (p50, p99, p999, saturated_fraction) in values.items():
        regions[name] = RegionExposureStats(
            background_p50_adu=p50,
            background_mad_adu=10.0,
            p90_adu=(p50 + p99) / 2,
            p99_adu=p99,
            p999_adu=p999,
            saturated_fraction=saturated_fraction,
            background_gradient_adu=3.0,
        )
    return FrameExposureSample(
        frame_id=sample.frame_id,
        frame_sequence=sample.frame_sequence,
        captured_at=sample.captured_at,
        actual_exposure_us=sample.actual_exposure_us,
        actual_gain=sample.actual_gain,
        white_level=sample.white_level,
        pedestal_adu=sample.pedestal_adu,
        regions=regions,
        center_contaminated=sample.center_contaminated,
        motion_degrees=sample.motion_degrees,
    )


def test_spatial_sample_excludes_central_moon_from_peripheral_statistics():
    raw = pytest.importorskip("numpy").full((300, 300), 400, dtype="uint16")
    raw[110:190, 110:190] = 4095

    sample = collect_spatial_frame_sample(
        raw,
        frame_id=7,
        frame_sequence=7,
        actual_exposure_us=200_000,
        actual_gain=30,
        bit_depth=12,
        pedestal_adu=238,
    )

    assert sample is not None
    assert sample.center_contaminated is True
    assert sample.regions["C"].saturated_fraction > 0
    assert max(region.p999_adu for region in sample.peripheral()) == 400


def test_spatial_sample_headroom_is_independent_of_bayer_phase_after_rotation():
    np = pytest.importorskip("numpy")
    yy, xx = np.indices((240, 360))
    raw = (350 + yy * 4 + xx * 2).astype("uint16")
    raw[0::2, 0::2] += 600
    raw[0::2, 1::2] += 300
    raw[1::2, 0::2] += 100

    original = collect_spatial_frame_sample(
        raw,
        frame_id=1,
        frame_sequence=1,
        actual_exposure_us=400_000,
        actual_gain=30,
        bit_depth=12,
        pedestal_adu=238,
    )
    rotated = collect_spatial_frame_sample(
        np.rot90(raw),
        frame_id=2,
        frame_sequence=2,
        actual_exposure_us=400_000,
        actual_gain=30,
        bit_depth=12,
        pedestal_adu=238,
    )

    assert original is not None and rotated is not None
    original_summary = AutoStarFrameController._peripheral_summary(original)
    rotated_summary = AutoStarFrameController._peripheral_summary(rotated)
    assert rotated_summary["p50"] == pytest.approx(original_summary["p50"], abs=8)
    assert rotated_summary["p99"] == pytest.approx(original_summary["p99"], abs=8)
    assert rotated_summary["p999"] == pytest.approx(original_summary["p999"], abs=8)


def test_matched_star_snr_uses_only_peripheral_raw_stars_around_central_moon():
    np = pytest.importorskip("numpy")
    raw = np.full((300, 300), 400, dtype="uint16")
    raw[115:185, 115:185] = 4095
    yy, xx = np.ogrid[:300, :300]
    for y, x in ((50, 60), (245, 230)):
        raw[(yy - y) ** 2 + (xx - x) ** 2 <= 3**2] = 1800

    quality = matched_star_exposure_quality(
        raw,
        [(50, 60), (150, 150), (245, 230)],
        frame_id=99,
        candidate_stars=20,
        bit_depth=12,
    )

    assert quality["center_contaminated"] is True
    assert quality["matched_stars"] == 2
    assert quality["snr_p25"] > 0
    assert "C" not in quality["region_ids"]


def test_dark_clear_sky_keeps_profile_gain_and_steady_scene_deadband():
    controller = _controller()
    frame = _sample(1)
    acquisition = controller.on_frame(frame)
    assert acquisition is not None
    assert acquisition.reason == "acquisition_highlight_exposure_up"
    controller.update_quality(_quality(1, matches=12, candidates=18, success=True))

    for sequence in range(2, 10):
        assert (
            controller.on_frame(_sample(sequence, p50=405, p99=3000, p999=3478)) is None
        )

    assert controller.status()["reason"] == "highlight_headroom_deadband"


def test_bright_cloud_submits_down_on_first_frame_and_pending_prevents_windup():
    controller = _controller()
    bright = _sample(1, exposure=400_000, p50=2800, p99=3000, p999=3300)

    target = controller.on_frame(bright)

    assert target is not None
    assert target.exposure_us < 400_000
    assert target.gain == 30
    controller.mark_submitted(target, 1)
    assert controller.on_frame(_sample(2, exposure=400_000)) is None
    assert controller.status()["pending"] is True


def test_preanchor_twilight_fading_raises_exposure_without_manual_reset():
    controller = _controller()
    # Reproduce the live 2026-09-03 hold: 36.2 ms / gain 29.51, p50 1093,
    # pedestal 238.  The old controller stayed at this point indefinitely
    # with reason=awaiting_peripheral_solve_anchor.
    frame = _sample(
        1,
        exposure=36_222,
        gain=29.5121,
        p50=1093.5,
        p99=2100,
        p999=2258.3,
    )

    target = controller.on_frame(frame)

    assert target is not None
    assert target.reason == "acquisition_highlight_exposure_up"
    assert target.exposure_us > frame.actual_exposure_us
    assert target.exposure_us <= round(frame.actual_exposure_us * 2**0.5)
    assert target.gain == pytest.approx(frame.actual_gain)


def test_preanchor_acquisition_tracks_multiple_fading_frames():
    controller = _controller()
    first = _sample(1, exposure=50_000, p50=650, p99=900, p999=1050)
    target1 = controller.on_frame(first)
    assert target1 is not None
    controller.mark_submitted(target1, first.frame_sequence)

    # Metadata confirms application, but twilight has faded again. The same
    # frame can request the next bounded increase; no mode toggle is needed.
    applied = _sample(
        4,
        exposure=target1.exposure_us,
        p50=650,
        p99=900,
        p999=1050,
    )
    target2 = controller.on_frame(applied)
    assert target2 is not None
    assert target2.reason == "acquisition_highlight_exposure_up"
    assert target2.exposure_us > target1.exposure_us
    assert controller.status()["applied_after_frames"] == 3


def test_preanchor_acquisition_holds_at_calibrated_highlight_target():
    controller = _controller()
    target_p999 = 238.0 + 0.84 * (4095.0 - 238.0)

    assert controller.on_frame(_sample(1, p50=1200, p99=3000, p999=target_p999)) is None
    assert controller.status()["reason"] == "awaiting_peripheral_solve_anchor"


def test_isolated_peripheral_highlight_does_not_fight_acquisition_servo():
    controller = _controller()
    # A star-sized highlight may raise p99.9 and clip roughly 0.1% of a
    # region. It must not lower exposure while p99 shows broad headroom.
    assert (
        controller.on_frame(
            _sample(
                1,
                p50=1200,
                p99=1800,
                p999=4095,
                sat=0.001,
            )
        )
        is None
    )
    assert controller.status()["reason"] == "awaiting_peripheral_solve_anchor"


def test_broad_near_white_peripheral_highlight_remains_immediate_safety_event():
    controller = _controller()

    target = controller.on_frame(
        _sample(1, exposure=400_000, p99=3800, p999=4095, sat=0.001)
    )

    assert target is not None
    assert target.safety is True
    assert target.reason == "peripheral_saturation_exposure_down"
    assert target.exposure_us < 400_000


def test_single_broad_peripheral_highlight_does_not_control_whole_sky():
    controller = _controller()
    sample = _sample(1)
    regions = dict(sample.regions)
    regions["UL"] = RegionExposureStats(
        background_p50_adu=400,
        background_mad_adu=10,
        p90_adu=2000,
        p99_adu=3900,
        p999_adu=4095,
        saturated_fraction=0.02,
        background_gradient_adu=3,
    )
    sample = FrameExposureSample(
        frame_id=sample.frame_id,
        frame_sequence=sample.frame_sequence,
        captured_at=sample.captured_at,
        actual_exposure_us=sample.actual_exposure_us,
        actual_gain=sample.actual_gain,
        white_level=sample.white_level,
        pedestal_adu=sample.pedestal_adu,
        regions=regions,
    )

    target = controller.on_frame(sample)

    assert target is not None
    assert target.reason == "acquisition_highlight_exposure_up"
    assert target.exposure_us > sample.actual_exposure_us


def test_polluted_lower_band_is_excluded_at_on_sky_400ms_optimum():
    controller = _controller()
    sample = _with_peripheral_regions(
        _sample(1, exposure=400_000),
        {
            "UL": (1404, 2274, 3511, 0.00007),
            "U": (1497, 2048, 2245, 0.0),
            "UR": (1509, 2076, 2218, 0.00014),
            "L": (2133, 2823, 3014, 0.0),
            "R": (2383, 3197, 3379, 0.0),
            "DL": (3042, 4065, 4095, 0.0073),
            "D": (3811, 4095, 4095, 0.324),
            "DR": (4058, 4095, 4095, 0.487),
        },
    )

    assert controller.on_frame(sample) is None
    status = controller.status()
    assert status["reason"] == "awaiting_peripheral_solve_anchor"
    assert status["usable_peripheral_regions"] == 5
    assert status["contaminated_peripheral_regions"] == 3
    assert 0.78 * 4095 < status["peripheral_p999_adu"] < 0.90 * 4095


def test_on_sky_560ms_saturation_spread_forces_immediate_reduction():
    controller = _controller()
    sample = _with_peripheral_regions(
        _sample(1, exposure=560_000),
        {
            "UL": (1876, 3104, 4095, 0.0031),
            "U": (2009, 2738, 2953, 0.0002),
            "UR": (2021, 2793, 3001, 0.00007),
            "L": (2898, 3857, 4095, 0.0013),
            "R": (3290, 4095, 4095, 0.052),
            "DL": (4095, 4095, 4095, 0.566),
            "D": (4095, 4095, 4095, 0.907),
            "DR": (4095, 4095, 4095, 0.955),
        },
    )

    target = controller.on_frame(sample)

    assert target is not None
    assert target.safety is True
    assert target.reason == "peripheral_saturation_exposure_down"
    assert target.exposure_us < sample.actual_exposure_us


def test_successful_low_exposure_anchor_still_climbs_toward_highlight_target():
    controller = _controller()
    low = _sample(1, exposure=160_000, p50=763, p99=1984, p999=2405)
    assert controller.on_frame(low) is not None
    controller.update_quality(_quality(1, matches=14, candidates=47, success=True))

    target = controller.on_frame(
        _sample(2, exposure=160_000, p50=763, p99=1984, p999=2405)
    )

    assert target is not None
    assert target.reason == "highlight_headroom_exposure_up"
    assert target.exposure_us > low.actual_exposure_us
    assert target.exposure_us <= round(low.actual_exposure_us * 2**0.5)


def test_hard_peripheral_saturation_overrides_only_with_safer_pending_target():
    controller = _controller()
    saturated = _sample(1, exposure=400_000, p999=4095, sat=0.02)
    target = controller.on_frame(saturated)
    assert target is not None and target.safety
    controller.mark_submitted(target, 1)

    # Same stale sensor frame must not enqueue an identical second correction.
    assert (
        controller.on_frame(_sample(2, exposure=400_000, p999=4095, sat=0.02)) is None
    )
    # A more severe observation may replace the pending mailbox with a safer pair.
    override = controller.on_frame(_sample(3, exposure=300_000, p999=4095, sat=0.10))
    assert override is not None
    assert override.exposure_us * override.gain < target.exposure_us * target.gain


def test_saturation_ceiling_prevents_immediate_acquisition_rebound_then_probes():
    controller = _controller()
    saturated = _sample(
        1,
        exposure=100_000,
        p50=400,
        p99=3900,
        p999=4095,
        sat=0.02,
    )
    target = controller.on_frame(saturated)
    assert target is not None
    controller.mark_submitted(target, saturated.frame_sequence)

    applied = _sample(2, exposure=target.exposure_us, p50=400)
    assert controller.on_frame(applied) is None
    status = controller.status()
    assert status["reason"] == "saturation_ceiling_hold"
    assert status["saturation_probe_after_s"] > 0

    probe = controller.on_frame(_sample(32, exposure=target.exposure_us, p50=400))
    assert probe is not None
    assert probe.reason == "acquisition_highlight_exposure_up"
    assert probe.exposure_us > target.exposure_us


def test_pending_is_cleared_by_quantized_driver_metadata():
    controller = _controller()
    target = controller.on_frame(_sample(1, p999=4095, sat=0.02))
    assert target is not None
    controller.mark_submitted(target, 1)

    controller.on_frame(_sample(4, exposure=target.exposure_us * 1.01, gain=29.51))

    assert controller.status()["pending"] is False
    assert controller.status()["applied_after_frames"] == 3


def test_pending_timeout_records_fault_and_blocks_upward_hunting():
    controller = _controller(pending_timeout_frames=2)
    target = controller.on_frame(_sample(1, p50=1800, p99=2500, p999=3000))
    assert target is not None
    controller.mark_submitted(target, 1)
    controller.on_frame(_sample(2))
    controller.on_frame(_sample(3))
    assert controller.on_frame(_sample(4)) is None

    status = controller.status()
    assert status["control_fault"] is True
    assert status["pending"] is True
    assert status["reason"] == "control_apply_timeout"


def test_dark_cloud_holds_recent_anchor_instead_of_raising_exposure():
    controller = _controller()
    clear = _sample(1, p50=500, p99=900, mad=10)
    controller.on_frame(clear)
    controller.update_quality(_quality(1, matches=12, candidates=18, success=True))

    cloudy = _sample(2, p50=265, p99=270, mad=10)
    assert controller.on_frame(cloudy) is None
    assert controller.status()["reason"] == "dark_cloud_anchor_hold"


def test_failed_peripheral_solve_blocks_upward_change_despite_high_contrast():
    controller = _controller()
    clear = _sample(1, p50=600, p99=1000)
    controller.on_frame(clear)
    controller.update_quality(_quality(1, matches=12, candidates=18, success=True))
    failed = _sample(2, p50=350, p99=750)
    controller.on_frame(failed)
    controller.update_quality(
        _quality(2, matches=0, candidates=15, success=False, snr=None)
    )

    assert controller.on_frame(_sample(3, p50=350, p99=750)) is None
    assert controller.status()["reason"] == "dark_cloud_anchor_hold"


def test_center_moon_without_peripheral_solve_never_raises_gain_or_exposure():
    controller = _controller()
    clear = _sample(1, p50=500, p99=900)
    controller.on_frame(clear)
    controller.update_quality(_quality(1, matches=10, candidates=15, success=True))

    moon = _sample(2, p50=280, p99=400, center_contaminated=True)
    assert controller.on_frame(moon) is None
    assert controller.status()["reason"] == "dark_cloud_anchor_hold"


def test_candidate_pressure_low_gain_trial_is_kept_when_matches_improve():
    controller = _controller(false_candidate_repeats=3)
    for sequence in range(1, 4):
        controller.on_frame(_settled_sample(sequence, gain=30))
        controller.update_quality(
            _quality(sequence, matches=0, candidates=100, success=False, snr=None)
        )

    trial = controller.on_frame(_settled_sample(4, gain=30))
    assert trial is not None
    assert trial.gain == 15
    assert trial.exposure_us == 400_000
    controller.mark_submitted(trial, 4)
    controller.on_frame(_settled_sample(7, exposure=400_000, gain=15))
    controller.update_quality(_quality(7, matches=9, candidates=25, success=True))

    assert controller.status()["reason"] == "gain_trial_kept"
    assert (
        controller.on_frame(_sample(8, exposure=400_000, gain=15, p99=3000, p999=3478))
        is None
    )


def test_gain_trial_waits_until_exposure_headroom_is_settled():
    controller = _controller(false_candidate_repeats=1)
    low = _sample(1, exposure=250_000, gain=30, p50=1558, p99=2186, p999=2312)
    assert controller.on_frame(low) is not None
    controller.update_quality(
        _quality(1, matches=0, candidates=100, success=False, snr=None)
    )

    target = controller.on_frame(
        _sample(2, exposure=250_000, gain=30, p50=1558, p99=2186, p999=2312)
    )

    assert target is not None
    assert target.gain == 30
    assert target.reason == "acquisition_highlight_exposure_up"
    assert controller.status()["gain_trial"] is None


def test_lower_gain_rolls_back_when_it_only_reduces_false_candidates():
    controller = _controller(false_candidate_repeats=1)
    controller.on_frame(_settled_sample(1, gain=30))
    controller.update_quality(
        _quality(1, matches=0, candidates=100, success=False, snr=None)
    )
    trial = controller.on_frame(_settled_sample(2, gain=30))
    assert trial is not None and trial.gain == 15
    controller.mark_submitted(trial, 2)
    controller.on_frame(_settled_sample(5, exposure=400_000, gain=15))
    controller.update_quality(
        _quality(5, matches=0, candidates=20, success=False, snr=None)
    )

    rollback = controller.on_frame(_settled_sample(6, exposure=400_000, gain=15))
    assert rollback is not None
    assert rollback.gain == 30
    controller.mark_submitted(rollback, 6)
    controller.on_frame(_settled_sample(9, exposure=200_000, gain=30))
    for sequence in range(10, 20):
        controller.on_frame(_sample(sequence, exposure=200_000, gain=30))
        controller.update_quality(
            _quality(sequence, matches=0, candidates=100, success=False, snr=None)
        )
    acquisition = controller.on_frame(_sample(20, exposure=200_000, gain=30))
    assert acquisition is not None
    assert acquisition.reason == "acquisition_highlight_exposure_up"
    assert acquisition.gain == 30
    assert controller.status()["gain_retry_after_s"] > 0


def test_low_gain_trial_rolls_back_when_match_quality_falls():
    controller = _controller(false_candidate_repeats=1)
    controller.on_frame(_settled_sample(1, gain=30))
    controller.update_quality(_quality(1, matches=0, candidates=40, success=False))
    trial = controller.on_frame(_settled_sample(2, gain=30))
    assert trial is not None and trial.gain == 15
    controller.mark_submitted(trial, 2)
    controller.on_frame(_settled_sample(5, exposure=400_000, gain=15))

    # Compare against a deliberately strong high-gain baseline: the lower
    # gain's 8 matches do not beat it and must queue rollback.
    controller._trial_baseline = _quality(1, matches=15, candidates=40, success=True)
    controller.update_quality(_quality(5, matches=8, candidates=18, success=True))
    rollback = controller.on_frame(_settled_sample(6, exposure=400_000, gain=15))
    assert rollback is not None
    assert rollback.gain == 30
    assert rollback.exposure_us == 200_000


def test_sustained_good_low_gain_quality_trials_profile_gain_again():
    controller = _controller()
    for sequence in range(1, 6):
        controller.on_frame(_settled_sample(sequence, exposure=400_000, gain=15))
        controller.update_quality(
            _quality(sequence, matches=10, candidates=18, success=True)
        )

    restore = controller.on_frame(_settled_sample(6, exposure=400_000, gain=15))
    assert restore is not None
    assert restore.gain == 30
    assert restore.exposure_us == 200_000
    controller.mark_submitted(restore, 6)
    controller.on_frame(_settled_sample(9, exposure=200_000, gain=30))
    controller.update_quality(_quality(9, matches=10, candidates=18, success=True))
    assert controller.status()["reason"] == "gain_trial_kept"


def test_manual_gain_lock_allows_exposure_safety_but_not_gain_change():
    controller = _controller(gain_locked=True)
    target = controller.on_frame(
        _sample(1, exposure=4_000, gain=8, p999=4095, sat=0.05)
    )
    assert target is not None
    assert target.gain == 8


def test_upward_adjustment_is_limited_to_half_stop_and_motion_cap():
    controller = _controller()
    clear = _sample(1, exposure=200_000, p50=600, p99=1000)
    controller.on_frame(clear)
    controller.update_quality(_quality(1, matches=12, candidates=16, success=True))
    dark = _sample(2, exposure=200_000, p50=350, p99=650, motion=1.0)

    target = controller.on_frame(dark)

    assert target is not None
    assert target.exposure_us == 50_000  # IMU motion cap wins over +0.5 stop.


def test_allocator_clamps_gain_ladder_for_imx296_and_hardware_limits():
    allocator = ExposureGainAllocator(
        min_exposure_us=500,
        max_exposure_us=900_000,
        max_gain=15,
    )

    assert allocator.gain_ladder == (15.0, 8.0, 4.0, 2.0, 1.0)
    assert allocator.clamp_exposure(100) == 500
    assert allocator.clamp_exposure(2_000_000) == 900_000
    assert allocator.preserve_light(200_000, 15, 8) == (375_000, 8.0)
