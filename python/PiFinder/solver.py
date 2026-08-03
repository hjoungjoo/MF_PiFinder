#!/usr/bin/python
# -*- coding:utf-8 -*-
"""
This module is the solver
* runs loop looking for new images
* tries to solve them
* If solved, emits solution into queue

"""

from PiFinder.multiproclogging import MultiprocLogging
import queue
import numpy as np
import time
import logging
import sys
from time import perf_counter as precision_timestamp
import os
import platform
import shutil
import socket
import subprocess
import threading
from multiprocessing import shared_memory
import grpc

from PiFinder import config as config_mod
from PiFinder import state_utils
from PiFinder import utils
from PiFinder import timez
from PiFinder import horizon_mask, sep_detect
from PiFinder import solver_frame_map as sfm
from PiFinder.sep_shadow import MAX_FRAME_AGE_S, WARM_MAP_PATH, SepShadowRunner
from PiFinder.sqm import SQM as SQMCalculator
from PiFinder.sqm.camera_profiles import get_camera_profile
from PiFinder.sqm.black_level import BlackLevelTracker
from PiFinder.sqm.clouds import CloudEstimator
from PiFinder.sqm.radiometer import RadiometerAccumulator, extract_photometry_image
from PiFinder.sqm.wings import WingEstimator
from PiFinder.state import SQM as SQMState
from PiFinder.types.positioning import (
    AlignCancel,
    AlignOnRaDec,
    AlignedResult,
    AlignmentResult,
    FailedSolve,
    Pointing,
    ReloadSqmCalibration,
    SolveDiagnostics,
    SuccessfulSolve,
)

sys.path.append(str(utils.tetra3_dir))
import tetra3
from tetra3 import cedar_detect_client

logger = logging.getLogger("Solver")

# SQM publication interval - the radiometric value publishes at most every
# N seconds (samples are collected on every frame regardless)
SQM_CALCULATION_INTERVAL_SECONDS = 1.0

# Stellar photometry is a transmission diagnostic in the radiometer-first
# path; it is expensive, so it runs at most every N seconds on solves.
SQM_STELLAR_DIAGNOSTIC_INTERVAL_SECONDS = 10.0

# Full-frame cedar attempts fail fast: successful FF solves measure 9-26 ms,
# but on unsolvable frames (bright-gradient junk detections) the default 1 s
# timeout dropped the attempt rate to 0.4 Hz and starved the SEP rescue tier
# (LP ascent test 2026-08-03, plan doc section 9-1).
CEDAR_FF_SOLVE_TIMEOUT_MS = 300


def create_sqm_calculator(shared_state):
    """Create a new SQM calculator instance with current calibration.

    Photometry always runs on the raw linear frame (green channel for Bayer
    sensors); the 8-bit processed image is for solving/display only.
    """
    camera_type = shared_state.camera_type()
    logger.info(f"Creating raw-green SQM calculator for camera: {camera_type}")
    return SQMCalculator(camera_type=camera_type)


def _extract_raw_photometry_image(raw, profile):
    """Build the linear photometry image from the stored raw frame.

    For Bayer sensors (SRGGB*) returns the averaged green channel (half-res);
    for mono sensors returns the raw frame as-is. Returns None on any shape/
    dtype problem so the caller can skip the SQM cycle.
    """
    return extract_photometry_image(raw, profile)


def _scaled_photometry_radii(
    scale, aperture_radius=5, inner_radius=10, outer_radius=18
):
    """Convert photometry radii from solve-image (512px) pixels to the
    photometry image's own pitch.

    The radii were tuned on the ~1.0-scale Bayer-green images (imx462: 490px).
    On the full-res mono imx296 (scale 2.125) the unscaled r=5 aperture holds
    only ~85% of a star's flux and the annuli land on the PSF itself, biasing
    every local sky estimate. The floors keep the geometry ordered
    (aperture < inner < outer) at any scale.
    """
    aperture = max(1, round(aperture_radius * scale))
    inner = max(aperture + 1, round(inner_radius * scale))
    outer = max(inner + 2, round(outer_radius * scale))
    return aperture, inner, outer


def _scale_solution_centroids(solution, scale):
    """Return a shallow copy of solution with matched_centroids scaled.

    The solve runs on the 512x512 processed image; the raw photometry image has a
    different pixel pitch, so the matched star positions must be rescaled to it.
    """
    scaled = dict(solution)
    mc = np.asarray(solution["matched_centroids"], dtype=np.float64) * scale
    scaled["matched_centroids"] = mc
    return scaled


def _derotate_centroids(points, rotation_deg, size):
    """Map (y, x) centroids from the display-rotated solve image back onto
    the unrotated raw frame's pixel grid.

    The camera process rotates the solve/display image by ``rotation_deg``
    (PIL CCW) relative to the raw it stores in shared state; photometry runs
    on the raw, so star positions must be counter-rotated or every aperture
    lands on the wrong sky (SQM then reads magnitudes too bright).

    Args:
        points: (N, 2) array of (y, x) positions in the rotated image.
        rotation_deg: degrees the solve image was rotated (PIL CCW).
        size: side length of the (square) pixel grid the points live on.
    """
    pts = np.asarray(points, dtype=np.float64)
    k = int(rotation_deg) % 360
    y, x = pts[:, 0], pts[:, 1]
    m = size - 1
    if k == 0:
        return pts
    if k == 90:
        # solve = raw rotated 90 CCW: raw_y = x, raw_x = m - y
        return np.stack([x, m - y], axis=1)
    if k == 180:
        return np.stack([m - y, m - x], axis=1)
    if k == 270:
        # solve = raw rotated 270 CCW: raw_y = m - x, raw_x = y
        return np.stack([m - x, y], axis=1)
    # Arbitrary angle: rotate about the image centre. PIL's rotate(a) fills
    # dest(x2, y2) from src at c + R(a)·(p2 − c) in (x, y) with y down.
    c = m / 2.0
    a = np.radians(k)
    dx, dy = x - c, y - c
    rx = c + np.cos(a) * dx - np.sin(a) * dy
    ry = c + np.sin(a) * dx + np.cos(a) * dy
    return np.stack([ry, rx], axis=1)


def update_radiometric_sqm(
    shared_state,
    sqm_calculator,
    accumulator,
    sample,
    calculation_interval_seconds=1.0,
    now=None,
    black_level_tracker=None,
):
    """Collect every frame and publish a solve-independent value at cadence."""
    from datetime import datetime

    fresh_sample = accumulator.add(sample)
    current_time = time.time() if now is None else float(now)

    # Every fresh radiometer sample carries (exposure, background) — feed the
    # black-level tracker here rather than only from the 10-second stellar
    # diagnostics: this cadence conditions its fit in minutes and keeps working
    # through failed solves. Withheld while the last transmission diagnostic
    # said cloud (a moving sky breaks the intercept's single-line model; the
    # tracker's own stderr gate catches drift the flag misses).
    if black_level_tracker is not None and fresh_sample:
        cloudy_now = shared_state.sqm_details().get("cloud_flag") is True
        black_level_tracker.add_sample(
            float(sample["exposure_sec"]),
            float(sample["background_per_pixel"]),
            stable=not cloudy_now,
        )

    current_sqm = shared_state.sqm()
    if current_sqm.last_update is not None:
        try:
            last_update = datetime.fromisoformat(current_sqm.last_update).timestamp()
            if current_time - last_update < calculation_interval_seconds:
                return False
        except (ValueError, AttributeError):
            logger.warning("Failed to parse SQM timestamp, recalculating")

    noise = sqm_calculator.noise_floor_estimator

    def tracked_or_static_bias():
        # The in-session intercept supersedes any static bias, wizard-measured
        # or profile: the OB clamp level moves with sensor state, so a stored
        # constant goes stale while the tracker measures the running session's
        # own frames — bounded by its stderr, deviation-band, and lease gates.
        # The wander is negligible over a city background but worth
        # 0.2–0.4 mag (and dead short-exposure frames) at a dark site.
        if black_level_tracker is not None:
            tracked = black_level_tracker.pedestal()
            if tracked is not None:
                return tracked
        return sqm_calculator.profile.bias_offset

    def pedestal_for_exposure(exposure_sec):
        bias = tracked_or_static_bias()
        if not noise.dark_current_calibrated:
            return bias
        # Dark current stays the wizard's: the intercept fit cannot separate
        # dark from sky (both are linear in exposure).
        return bias + sqm_calculator.profile.dark_current_rate * exposure_sec

    sqm_value, details = accumulator.estimate(
        sqm_calculator.profile,
        current_time,
        pedestal_for_exposure=pedestal_for_exposure,
    )
    if sqm_value is None:
        previous = shared_state.sqm_details()
        shared_state.set_sqm_details({**previous, **details})
        return False

    previous = shared_state.sqm_details()
    diagnostic_at = previous.get("transmission_diagnostic_at")
    diagnostic_age = current_time - diagnostic_at if diagnostic_at is not None else None
    if (
        previous.get("optics_attenuation_candidate")
        and diagnostic_age is not None
        and 0 <= diagnostic_age <= 15.0
    ):
        deficit = previous.get("transmission_deficit")
        if deficit is not None and 0.0 < deficit <= 2.0:
            details["sqm_radiometric_uncorrected"] = sqm_value
            details["optics_attenuation_correction"] = -float(deficit)
            sqm_value -= float(deficit)

    if black_level_tracker is not None:
        tracked, tracked_stderr, _ = black_level_tracker.state()
        # pedestal() applies the lease; the flag must reflect what the
        # publication actually used, not the raw last fit.
        details["black_level_tracked"] = black_level_tracker.pedestal() is not None
        details["black_level_pedestal"] = tracked
        details["black_level_stderr"] = tracked_stderr
        details["window_black_level"] = black_level_tracker.dump()
    details["window_radiometer"] = accumulator.dump()
    details["measurement_role"] = "primary_radiometer"
    shared_state.set_sqm_details({**previous, **details})
    shared_state.set_sqm(
        SQMState(
            value=sqm_value,
            source="Radiometer",
            last_update=timez.local_now().isoformat(),
        )
    )
    # Publishes at 1 Hz in steady state; DEBUG per the MF logging policy
    # (c0ca4dcc) so a night's log isn't 30k identical lines.
    logger.debug("Radiometric SQM updated: %.2f mag/arcsec²", sqm_value)
    return True


def update_sqm(
    shared_state,
    sqm_calculator,
    centroids,
    solution,
    exposure_sec,
    altitude_deg,
    calculation_interval_seconds=5.0,
    aperture_radius=5,
    annulus_inner_radius=10,
    annulus_outer_radius=18,
    wing_estimator=None,
    cloud_estimator=None,
    black_level_tracker=None,
    publish=True,
):
    """
    Calculate SQM from image.

    Args:
        shared_state: SharedStateObj instance
        sqm_calculator: SQM calculator instance
        centroids: List of detected star centroids
        solution: Tetra3 solve solution with matched stars
        exposure_sec: Exposure time in seconds
        altitude_deg: Altitude in degrees for extinction correction
        calculation_interval_seconds: Minimum time between calculations (default: 5.0)
        aperture_radius: Aperture radius for photometry, in solve-image
            (512px) pixels; rescaled to the photometry image (default: 5)
        annulus_inner_radius: Inner annulus radius, solve-image pixels (default: 10)
        annulus_outer_radius: Outer annulus radius, solve-image pixels (default: 18)
        wing_estimator: WingEstimator that supplies the rolling aperture
            (wing-loss) mzero correction and is fed each frame's photometry
            image + matched centroids.

    Returns:
        bool: True if SQM was calculated and updated, False otherwise
    """
    from datetime import datetime

    # Get current SQM state from shared state
    current_sqm = shared_state.sqm()
    current_time = time.time()

    # Check if we should calculate SQM
    should_calculate = not publish or current_sqm.last_update is None

    if publish and current_sqm.last_update is not None:
        try:
            last_update_time = datetime.fromisoformat(
                current_sqm.last_update
            ).timestamp()
            should_calculate = (
                current_time - last_update_time
            ) >= calculation_interval_seconds
        except (ValueError, AttributeError):
            logger.warning("Failed to parse SQM timestamp, recalculating")
            should_calculate = True

    if not should_calculate:
        return False

    profile = sqm_calculator.profile

    try:
        raw = shared_state.cam_raw()
    except (BrokenPipeError, ConnectionResetError):
        raw = None
    green = _extract_raw_photometry_image(raw, profile)
    if green is None or green.shape[0] < 256:
        # cam_raw() is None until the first real capture (test mode never
        # fills it), and a malformed frame comes through far smaller than a
        # real one. A genuine green frame is several hundred px per side —
        # e.g. ~490 for the imx462/imx290 crop, larger for the imx296 — so
        # the floor only rejects missing/garbage frames, not valid sensors.
        # Photometry runs at the green frame's own scale, so a side shorter
        # than the 512px solve image is fine.
        logger.debug("Raw frame unavailable/invalid for SQM; skipping this cycle")
        return False
    scale = green.shape[0] / 512.0
    aperture_radius, annulus_inner_radius, annulus_outer_radius = (
        _scaled_photometry_radii(
            scale, aperture_radius, annulus_inner_radius, annulus_outer_radius
        )
    )
    calc_image = green
    calc_solution = _scale_solution_centroids(solution, scale)
    # All detected centroids, scaled to the photometry image: sqm masks them
    # out of background annuli (neighbour-star contamination in dense fields).
    calc_centroids = (
        np.asarray(centroids, dtype=np.float64) * scale
        if centroids is not None and len(centroids) > 0
        else None
    )

    # The solve image is display-rotated relative to the raw; counter-rotate
    # all star positions onto the raw's grid before photometry.
    try:
        solve_rotation = shared_state.solve_image_rotation()
    except (BrokenPipeError, ConnectionResetError, AttributeError):
        solve_rotation = None
    if solve_rotation:
        side = green.shape[0]
        calc_solution["matched_centroids"] = _derotate_centroids(
            calc_solution["matched_centroids"], solve_rotation, side
        )
        if calc_centroids is not None:
            calc_centroids = _derotate_centroids(calc_centroids, solve_rotation, side)
    image_pixels_per_side = int(green.shape[0])
    # 0.70 of full scale, not ~1.0: CMOS response bends well before hard
    # clip, and stars peaking at 75-90% already read systematically low.
    saturation_threshold = int(0.70 * (2**profile.bit_depth - 1))

    mzero_correction = 0.0
    if wing_estimator is not None:
        # Match the estimator's patch geometry to this photometry image
        # (no-op after the first frame; the scale is a per-camera constant).
        wing_estimator.set_scale(scale)
        mzero_correction = wing_estimator.correction()

    # Pedestal from the sky-vs-exposure intercept (see sqm.black_level): the
    # in-session tracked bias supersedes any static constant, wizard-measured
    # or profile — the OB clamp level moves with sensor state, so a stored
    # value goes stale. The wizard's dark-current rate remains authoritative
    # (the intercept fit cannot separate dark from sky) and is added on top,
    # matching the calculator's own bias + dark composition.
    pedestal_override = None
    if black_level_tracker is not None:
        tracked = black_level_tracker.pedestal()
        if tracked is not None:
            pedestal_override = tracked
            if sqm_calculator.noise_floor_estimator.dark_current_calibrated:
                pedestal_override += (
                    sqm_calculator.profile.dark_current_rate * exposure_sec
                )

    try:
        # Calculate SQM from image
        sqm_value, details = sqm_calculator.calculate(
            centroids=calc_centroids,
            solution=calc_solution,
            image=calc_image,
            exposure_sec=exposure_sec,
            altitude_deg=altitude_deg,
            aperture_radius=aperture_radius,
            annulus_inner_radius=annulus_inner_radius,
            annulus_outer_radius=annulus_outer_radius,
            saturation_threshold=saturation_threshold,
            image_pixels_per_side=image_pixels_per_side,
            mzero_correction=mzero_correction,
            pedestal_override=pedestal_override,
        )

        # Feed this frame's stars into the rolling wing (aperture-loss) fit.
        if (
            wing_estimator is not None
            and calc_solution.get("matched_centroids") is not None
        ):
            wing_estimator.add_frame(
                calc_image,
                calc_solution["matched_centroids"],
                saturation_threshold,
            )

        # Stellar photometry is now a live transmission diagnostic. The primary
        # sky value is the fixed-calibration radiometer and remains meaningful
        # through cloud; stars classify cloud versus instrument attenuation.
        # Feed the estimator and report the deficit. Only a recent non-cloud
        # deficit against a conditioned session baseline may compensate the
        # next radiometric publication for instrument-side attenuation.
        cloud_flag = None
        if cloud_estimator is not None and details.get("mzero") is not None:
            try:
                pointing = shared_state.solution()
                pointing_alt = getattr(pointing, "Alt", None)
            except (BrokenPipeError, ConnectionResetError, AttributeError):
                pointing_alt = None
            # sky_brightness is the independent radiometric measurement,
            # UNCORRECTED: the guard asks whether the raw sky is anomalously
            # bright vs the device's learned clear-sky level (cloud brightens
            # the sky; dew/optics dim stars and sky together). Feeding the
            # optics-compensated published value back would let a transient
            # correction overshoot masquerade as sky excess and mislabel dew
            # onset as cloud.
            previous_details = shared_state.sqm_details()
            radiometric_sky = previous_details.get("sqm_radiometric")
            if radiometric_sky is None:
                radiometric_sky = shared_state.sqm().value
            cloud_deficit = cloud_estimator.add_sample(
                details["mzero"],
                exposure_sec,
                sky_brightness=radiometric_sky,
                # details['mzero'] already includes the wing correction.
                wing_correction=0.0,
                altitude_deg=pointing_alt,
            )
            cloud_flag = cloud_estimator.is_cloudy()
            details["cloud_extinction"] = cloud_deficit
            details["cloud_flag"] = cloud_flag
            details["transmission_deficit"] = cloud_deficit
            details["optics_attenuation_candidate"] = bool(
                cloud_deficit is not None
                and cloud_deficit > cloud_estimator.cloud_threshold
                and cloud_flag is False
                and cloud_estimator.conditioned()
            )
            details["transmission_diagnostic_at"] = time.time()
            primary_value = shared_state.sqm().value
            if details["optics_attenuation_candidate"]:
                details["sqm_optics_compensated"] = primary_value - cloud_deficit

        # The tracker is fed from the radiometer samples (denser cadence, and
        # the same background estimator its pedestal is applied to); here it is
        # only consumed, so stellar diagnostics report the pedestal actually
        # used for their photometry.
        if black_level_tracker is not None:
            details["black_level_tracked"] = pedestal_override is not None

        details["sqm_star_calibrated"] = sqm_value
        details["measurement_role"] = "stellar_transmission_diagnostic"

        # Full rolling-window state of every tracker, so diagnostics dumps
        # (exposure sweeps in particular) carry the samples behind each
        # published number, not just the summary.
        if wing_estimator is not None:
            details["window_wings"] = wing_estimator.dump()
        if cloud_estimator is not None:
            details["window_clouds"] = cloud_estimator.dump()
        if black_level_tracker is not None:
            details["window_black_level"] = black_level_tracker.dump()

        # Store SQM details (filter out large per-star arrays)
        filtered_details = {
            k: v
            for k, v in details.items()
            if k
            not in (
                "star_centroids",
                "star_mags",
                "star_fluxes",
                "star_local_backgrounds",
                "star_mzeros",
            )
        }
        previous = shared_state.sqm_details()
        shared_state.set_sqm_details({**previous, **filtered_details})

        # Update shared state
        if publish and sqm_value is not None:
            new_sqm_state = SQMState(
                value=sqm_value,
                source="Calculated",
                last_update=timez.local_now().isoformat(),
            )
            shared_state.set_sqm(new_sqm_state)
            logger.info(f"SQM updated: {sqm_value:.2f} mag/arcsec²")
            return True
        if sqm_value is not None:
            return True

    except Exception as e:
        logger.error(f"Error calculating SQM: {e}")
        return False

    return False


class CedarConnectionError(Exception):
    """Raised when Cedar gRPC connection fails."""

    pass


# Must match the hard-coded segment name in
# tetra3/cedar_detect_client.py:_alloc_shmem(). The segment is unlinked on a
# clean close(), but a solver process that is killed (or crashes) leaves it in
# /dev/shm, so the next run's create=True fails with FileExistsError.
_CEDAR_DETECT_SHMEM_NAME = "/cedar_detect_image"


class PFCedarDetectClient(cedar_detect_client.CedarDetectClient):
    def __init__(self, port=50551):
        """Connect to cedar-detect-server.

        On the PiFinder the server runs as a systemd service, so normally we
        just connect to it. In a development checkout no service is running;
        rather than require a manual start, if nothing is listening on the
        port we spawn the bundled ``bin/cedar-detect-server-<arch>`` ourselves
        and tear it down again in ``__del__``.

        Also changes this to a different default port.
        """
        self._port = port
        self._subprocess = None
        # Will initialize on first use.
        self._stub = None
        self._shmem = None
        self._shmem_size = 0
        # Try shared memory, fall back if an error occurs.
        self._use_shmem = True
        # A killed solver leaves its shmem segment behind; clear any stale one
        # so this run can re-create it instead of dying on FileExistsError.
        self._clear_stale_shmem()
        if self._server_reachable():
            # An external server (systemd service) is already running.
            time.sleep(2)
        else:
            self._spawn_server()

    def _server_reachable(self):
        """True if cedar-detect-server is already listening on our port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", self._port)) == 0

    def _spawn_server(self):
        """Spawn the bundled cedar-detect-server (development fallback)."""
        binary = self._find_server_binary()
        if binary is None:
            raise FileNotFoundError(
                f"cedar-detect-server is not listening on port {self._port} "
                "and no bundled binary was found in bin/; start it manually."
            )
        env = os.environ.copy()
        env["RUST_BACKTRACE"] = "1"
        logger.info("Spawning cedar-detect-server: %s", binary)
        self._subprocess = subprocess.Popen(
            [str(binary), "--port", str(self._port)], env=env
        )
        time.sleep(1)

    @staticmethod
    def _find_server_binary():
        """Locate the bin/cedar-detect-server binary matching this arch.

        Falls back to a ``cedar-detect-server`` found on ``PATH``.
        """
        machine = platform.machine().lower()
        if machine in ("aarch64", "arm64"):
            prefer = ("aarch64", "arm64")
        else:
            prefer = ("x86_64", "amd64", "x86")
        candidates = sorted((utils.pifinder_dir / "bin").glob("cedar-detect-server*"))
        for suffix in prefer:
            for candidate in candidates:
                if candidate.name.endswith(suffix) and os.access(candidate, os.X_OK):
                    return candidate
        for candidate in candidates:  # any executable cedar binary
            if os.access(candidate, os.X_OK):
                return candidate
        on_path = shutil.which("cedar-detect-server")
        return on_path if on_path else None

    def _clear_stale_shmem(self):
        """Unlink a leaked cedar_detect_image segment from a prior solver.

        Makes solver restarts self-healing. Safe because PiFinder runs a
        single solver process, so any existing segment is necessarily stale.
        """
        try:
            stale = shared_memory.SharedMemory(_CEDAR_DETECT_SHMEM_NAME)
        except FileNotFoundError:
            return
        stale.close()
        stale.unlink()
        logger.warning(
            "Cleared stale %s shared memory segment from a prior solver",
            _CEDAR_DETECT_SHMEM_NAME,
        )

    def _del_shmem(self):
        """Release the shared-memory segment, tolerating one that has
        already vanished from /dev/shm.

        systemd-logind's ``RemoveIPC=yes`` (the default) deletes every
        POSIX shared-memory segment a user owns the moment that user's
        last login session ends — an SSH logout is enough, because the
        PiFinder service runs as the same user but holds no login
        session of its own. The upstream cleanup then raises
        ``FileNotFoundError`` from ``unlink()``, which escaped before
        ``extract_centroids`` could flip ``_use_shmem`` off — so instead
        of falling back to passing the image over gRPC, every subsequent
        solve repeated the crash until restart. A segment that is
        already gone is this method's goal state: treat it as released.
        """
        try:
            super()._del_shmem()
        except FileNotFoundError:
            self._shmem = None

    def _get_stub(self):
        if self._stub is None:
            channel = grpc.insecure_channel("127.0.0.1:%d" % self._port)
            self._stub = cedar_detect_client.cedar_detect_pb2_grpc.CedarDetectStub(
                channel
            )
        return self._stub

    def extract_centroids(
        self, image, sigma, max_size, use_binned, detect_hot_pixels=True
    ):
        """Override to raise CedarConnectionError on gRPC failure instead of returning empty list."""
        import numpy as np
        from tetra3 import cedar_detect_pb2

        np_image = np.asarray(image, dtype=np.uint8)
        (height, width) = np_image.shape
        centroids_result = None

        # Use shared memory path (same machine)
        if self._use_shmem:
            self._alloc_shmem(size=width * height)
            shimg = np.ndarray(
                np_image.shape, dtype=np_image.dtype, buffer=self._shmem.buf
            )
            shimg[:] = np_image[:]

            im = cedar_detect_pb2.Image(
                width=width, height=height, shmem_name=self._shmem.name
            )
            req = cedar_detect_pb2.CentroidsRequest(
                input_image=im,
                sigma=sigma,
                max_size=max_size,
                return_binned=False,
                use_binned_for_star_candidates=use_binned,
                detect_hot_pixels=detect_hot_pixels,
            )
            try:
                centroids_result = self._get_stub().ExtractCentroids(req)
            except grpc.RpcError as err:
                if err.code() == grpc.StatusCode.INTERNAL:
                    # Shared memory issue, fall back to non-shmem. The flag
                    # latches for the life of the process, so this logs once --
                    # but without it the downgrade is silent and the only
                    # symptom is a slower extract time.
                    logger.warning(
                        "Cedar shared-memory handoff failed (%s); passing the "
                        "image inline over gRPC from now on. If %s was removed "
                        "out from under us, check for the RemoveIPC=no drop-in "
                        "in /etc/systemd/logind.conf.d/.",
                        err.details(),
                        _CEDAR_DETECT_SHMEM_NAME,
                    )
                    self._del_shmem()
                    self._use_shmem = False
                else:
                    raise CedarConnectionError(
                        f"Cedar gRPC failed: {err.details()}"
                    ) from err

        if not self._use_shmem:
            im = cedar_detect_pb2.Image(
                width=width, height=height, image_data=np_image.tobytes()
            )
            req = cedar_detect_pb2.CentroidsRequest(
                input_image=im,
                sigma=sigma,
                max_size=max_size,
                return_binned=False,
                use_binned_for_star_candidates=use_binned,
                # Must be passed here too: detect_hot_pixels is a proto3 bool,
                # so leaving it out sends false and hot pixels start being
                # detected as stars. Losing the shared-memory handoff should
                # cost throughput, not detection quality.
                detect_hot_pixels=detect_hot_pixels,
            )
            try:
                centroids_result = self._get_stub().ExtractCentroids(req)
            except grpc.RpcError as err:
                raise CedarConnectionError(
                    f"Cedar gRPC failed: {err.details()}"
                ) from err

        tetra_centroids = []
        if centroids_result is not None:
            for sc in centroids_result.star_candidates:
                tetra_centroids.append((sc.centroid_position.y, sc.centroid_position.x))
        return tetra_centroids

    def __del__(self):
        # __del__ can run on a partially-constructed instance (e.g. if __init__
        # raised), so attributes may be missing -- access defensively.
        subprocess_handle = getattr(self, "_subprocess", None)
        if subprocess_handle is not None:
            subprocess_handle.kill()
        self._del_shmem()


def _build_successful_solve(
    solution: dict,
    last_image_metadata: dict,
    last_solve_attempt: float,
    last_solve_success: float,
    centroid_count: int = 0,
    solve_path: str = "",
) -> SuccessfulSolve:
    """Fold a successful tetra3 ``solution`` dict into a
    :class:`SuccessfulSolve` message.

    Carries flat per-axis solve-truth (no ``solve``/``estimate`` split);
    the integrator fans ``camera``/``aligned`` into both cells of its
    long-lived :class:`PointingEstimate` and advances only the
    ``estimate`` cells via IMU dead-reckoning between solves.
    """
    camera_value = Pointing(
        RA=solution["RA"],
        Dec=solution["Dec"],
        Roll=solution["Roll"],
    )
    aligned_value = Pointing(
        RA=solution.get("RA_target", solution["RA"]),
        Dec=solution.get("Dec_target", solution["Dec"]),
        Roll=solution["Roll"],
    )

    imu_anchor = None
    if last_image_metadata.get("imu"):
        imu_anchor = last_image_metadata["imu"].quat

    return SuccessfulSolve(
        camera=camera_value,
        aligned=aligned_value,
        imu_anchor=imu_anchor,
        last_solve_attempt=last_solve_attempt,
        last_solve_success=last_solve_success,
        diagnostics=SolveDiagnostics(
            Matches=solution.get("Matches", 0),
            Centroids=centroid_count,
            RMSE=solution.get("RMSE"),
            Prob=solution.get("Prob"),
            FOV=solution.get("FOV"),
            T_solve=solution.get("T_solve"),
            solve_path=solve_path,
            T_extract=solution.get("T_extract"),
        ),
        alignment=AlignmentResult(
            x_target=solution.get("x_target"),
            y_target=solution.get("y_target"),
        ),
        matched_centroids=solution.get("matched_centroids"),
        matched_stars=solution.get("matched_stars"),
        matched_catID=solution.get("matched_catID"),
    )


def _build_failed_solve(
    last_solve_attempt: float,
    last_solve_success,
    t_extract_ms: float,
    centroid_count: int = 0,
    solve_path: str = "",
) -> FailedSolve:
    """Build a :class:`FailedSolve` message for an attempt that produced
    no pointing. The integrator's long-lived estimate preserves the
    previous ``solve`` cells so IMU dead-reckoning continues.

    ``centroid_count`` defaults to 0 for the exception path, where the
    ``centroids`` list may be stale from a previous loop iteration."""
    return FailedSolve(
        last_solve_attempt=last_solve_attempt,
        last_solve_success=last_solve_success,
        diagnostics=SolveDiagnostics(
            Matches=0,
            Centroids=centroid_count,
            T_extract=t_extract_ms,
            solve_path=solve_path,
        ),
    )


def _cedar_fullframe_geometry(cfg, camera_type):
    """Context for the full-frame cedar path, or None until resolvable.

    Same sources as SepShadowRunner.create_if_enabled: the camera profile
    for the production crop width and saturation level, the display config
    for the stage-5 rotation, plus the warm-pixel map and screen direction
    for the detection gates / horizon mask. Camera type is published after
    the camera process boots, so resolution is retried from the loop until
    it succeeds."""
    try:
        if not camera_type:
            return None
        profile = get_camera_profile(camera_type)
        crop_width = int(profile.raw_size[0] - profile.crop_x[0] - profile.crop_x[1])
        rotation = sfm.stage5_rotation_deg(
            cfg.get_option("screen_direction"),
            cfg.get_option("camera_rotation"),
        )
        warm_map = None
        try:
            if WARM_MAP_PATH.exists():
                warm_map = np.asarray(np.load(WARM_MAP_PATH), dtype=np.int32)
        except Exception:
            logger.exception("Warm-pixel map load failed; FF gates run unmasked")
        return {
            "rotation_deg": rotation,
            "crop_width_px": crop_width,
            "saturation_level": float(2**profile.bit_depth - 1),
            "warm_map": warm_map,
            "screen_direction": cfg.get_option("screen_direction"),
        }
    except Exception:
        logger.exception("cedar full-frame geometry unavailable")
        return None


def _center_square_subset(centroids, frame_hw):
    """Centroids inside the largest centred square of the frame.

    The centre-first cascade (user design 2026-08-04) solves this subset
    before the full set: near the frame centre optical distortion is
    lowest (offline A/B: RMSE 24 -> 13 arcsec on the dark-sky corpus)
    and edge junk that survives the gates is excluded. Selection happens
    on coordinates -- the frame itself is never re-processed."""
    pts = np.asarray(centroids, dtype=np.float64)
    if pts.ndim != 2 or len(pts) == 0:
        return pts.reshape(0, 2)
    h, w = float(frame_hw[0]), float(frame_hw[1])
    side = min(h, w)
    y0, x0 = (h - side) / 2.0, (w - side) / 2.0
    keep = (
        (pts[:, 0] >= y0)
        & (pts[:, 0] < y0 + side)
        & (pts[:, 1] >= x0)
        & (pts[:, 1] < x0 + side)
    )
    return pts[keep]


def _count_in_crop(centroids, frame_hw, crop_width_px: int) -> int:
    """Detections inside the (centred) production crop window.

    Published as SolveDiagnostics.Centroids on the full-frame path so
    auto-exposure keeps its 512-crop star-count semantics unchanged."""
    if centroids is None or len(centroids) == 0:
        return 0
    arr = np.asarray(centroids, dtype=np.float64)
    height, width = float(frame_hw[0]), float(frame_hw[1])
    y0 = max(0.0, (height - crop_width_px) / 2.0)
    x0 = max(0.0, (width - crop_width_px) / 2.0)
    inside = (
        (arr[:, 0] >= y0)
        & (arr[:, 0] < y0 + crop_width_px)
        & (arr[:, 1] >= x0)
        & (arr[:, 1] < x0 + crop_width_px)
    )
    return int(np.count_nonzero(inside))


def _solve_cedar_fullframe(
    t3,
    centroids,
    frame_hw,
    rotation_deg: float,
    crop_width_px: int,
    shared_state,
    target_sky_coord=None,
) -> dict:
    """Solve full-frame cedar centroids at native FOV, in 512 semantics.

    Mirrors SepShadowRunner.solve: rotation, canvas, fov and target_pixel
    are mapped through solver_frame_map so RA/Dec/Roll and the aligned
    pointing at target_pixel carry the exact production-512 meaning, and
    y/x_target is mapped back so the alignment chain persists 512-space
    coordinates unchanged."""
    try:
        cents, canvas = sfm.rotate_centroids(
            np.asarray(centroids, dtype=np.float64), frame_hw, rotation_deg
        )
        target_pixel = sfm.map_target_pixel_to_frame(
            shared_state.target_pixel(), canvas, crop_width_px
        )
        fov = sfm.fov_estimate_deg(canvas[1], crop_width_px)
        solution = t3.solve_from_centroids(
            cents,
            canvas,
            fov_estimate=fov,
            fov_max_error=fov / 3.0,
            match_max_error=0.005,
            return_matches=True,
            target_pixel=target_pixel,
            target_sky_coord=target_sky_coord,
            solve_timeout=CEDAR_FF_SOLVE_TIMEOUT_MS,
        )
        if (
            solution
            and solution.get("RA") is not None
            and solution.get("y_target") is not None
            and solution.get("x_target") is not None
        ):
            ty, tx = sfm.map_frame_pixel_to_target(
                (float(solution["y_target"]), float(solution["x_target"])),
                canvas,
                crop_width_px,
            )
            solution["y_target"], solution["x_target"] = ty, tx
        return solution or {}
    except Exception:
        logger.exception("cedar full-frame solve failed")
        return {}


def solver(
    shared_state,
    solver_queue,
    camera_image,
    console_queue,
    log_queue,
    align_command_queue,
    align_result_queue,
    camera_command_queue,
    is_debug=False,
    max_imu_ang_during_exposure=1.0,  # Max allowed turn during exp [degrees]
):
    MultiprocLogging.configurer(log_queue)
    logger.debug("Starting Solver")
    t3 = tetra3.Tetra3(str(utils.tetra3_dir / "data" / "default_database.npz"))
    align_ra = 0
    align_dec = 0
    last_solve_attempt: float = 0.0
    last_solve_success = None  # exposure_end of most recent successful solve

    centroids = []
    log_no_stars_found = True

    # SQM calculator is created lazily on the first radiometer sample (or
    # solve), not here: at solver startup shared_state.camera_type() still
    # holds the pre-camera default, and a calculator built from it would
    # photometer with the wrong sensor profile (pedestal etc.). The camera
    # process records the real type before it captures its first frame, and a
    # solve requires a captured frame, so first real-frame use is guaranteed
    # to see the real camera type.
    sqm_calculator = None
    # Rolling aperture (wing-loss) correction, fed by bright matched stars
    sqm_wing_estimator = WingEstimator()
    # Cloud/dew estimator and black-level tracker are created with the
    # calculator (below) so they get the real sensor's profile seeds; the
    # camera type is not yet known here.
    sqm_cloud_estimator = None
    sqm_black_level = None
    sqm_radiometer = RadiometerAccumulator()
    last_stellar_diagnostic = 0.0

    # SEP shadow/fallback runner (full-frame detection experiment). Needs
    # the camera type for crop geometry, which the camera process publishes
    # after startup -- so creation is retried in the loop until it works.
    _sep_cfg = config_mod.Config()
    sep_shadow = None
    sep_shadow_wanted = bool(
        _sep_cfg.get_option("solver_shadow_detect")
        or _sep_cfg.get_option("solver_sep_fallback")
    )
    # Full-frame cedar primary path (mf_cedar_fullframe_primary_plan_ko.md):
    # feed cedar the uncropped 12-bit raw (>>4, detection is invariant to the
    # affine stretch) and solve at native FOV via solver_frame_map. The SEP
    # fallback below is unchanged; flag off = byte-identical 512 path.
    cedar_fullframe_wanted = bool(_sep_cfg.get_option("solver_cedar_fullframe"))
    cedar_ff_geometry = None  # context dict, resolved lazily
    # Ground-light rejection for the FF path (docs field test 2026-08-04):
    # detection quality gates (edge/saturation/warm/cluster -- the SEP
    # fallback's filters applied to cedar centroids, default on) and the
    # IMU horizon mask (default OFF: the altitude worth masking depends on
    # the observing site's skyline, so it is an opt-in per location).
    cedar_ff_gates_wanted = _sep_cfg.get_option("solver_cedar_ff_gates")
    cedar_ff_gates_wanted = (
        True if cedar_ff_gates_wanted is None else bool(cedar_ff_gates_wanted)
    )
    horizon_mask_wanted = bool(_sep_cfg.get_option("solver_horizon_mask"))
    # Centre-first cascade (user design 2026-08-04): solve the centred-square
    # coordinate subset first, full set on failure, then SEP the same way;
    # SEP detection runs concurrently with the cedar tiers to hide its cost.
    center_first_wanted = bool(_sep_cfg.get_option("solver_center_first"))
    if cedar_fullframe_wanted:
        logger.info(
            "Cedar full-frame primary path enabled "
            "(gates=%s, horizon_mask=%s, center_first=%s)",
            cedar_ff_gates_wanted,
            horizon_mask_wanted,
            center_first_wanted,
        )

    while True:
        logger.info("Starting Solver Loop")
        # Try to start cedar detect server, fall back to tetra3 centroider if unavailable
        cedar_detect = None
        try:
            cedar_detect = PFCedarDetectClient()
        except FileNotFoundError as e:
            logger.warning(
                "Not using cedar_detect, as corresponding file '%s' could not be found",
                e.filename,
            )
        except ValueError:
            logger.exception("Not using cedar_detect")

        try:
            while True:
                # Drain any pending command queue messages.
                while True:
                    try:
                        command = align_command_queue.get(block=False)
                    except queue.Empty:
                        break

                    if isinstance(command, AlignOnRaDec):
                        logger.debug("Align Command Received: %s", command)
                        align_ra = command.ra
                        align_dec = command.dec
                    elif isinstance(command, AlignCancel):
                        align_ra = 0
                        align_dec = 0
                    elif isinstance(command, ReloadSqmCalibration):
                        # Invalidate; the next solve recreates the calculator
                        # with fresh calibration (single creation site).
                        logger.info("Reloading SQM calibration...")
                        sqm_calculator = None
                        sqm_wing_estimator.reset()
                        # Cloud estimator and black-level tracker are recreated
                        # from the fresh profile on the next solve; drop them
                        # here so stale seeds/history cannot carry over.
                        sqm_cloud_estimator = None
                        sqm_black_level = None
                        sqm_radiometer.reset()
                        last_stellar_diagnostic = 0.0
                    else:
                        logger.warning(
                            "Unknown solver command (type=%s): %r",
                            type(command).__name__,
                            command,
                        )

                state_utils.sleep_for_framerate(shared_state)

                # use the time the exposure started here to
                # reject images started before the last solve
                # which might be from the IMU
                try:
                    last_image_metadata = shared_state.last_image_metadata()
                except (BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Lost connection to shared state manager: {e}")
                    continue

                # Check if we should process this image
                is_new_image = last_image_metadata["exposure_end"] > last_solve_attempt

                if not is_new_image:
                    continue

                # Every camera frame already carries a tiny radiometer sample
                # reduced in the camera process. Collect all of them and publish
                # at most once per second for CPU/battery stability.
                try:
                    radiometer_sample = shared_state.sqm_radiometer_sample()
                except (BrokenPipeError, ConnectionResetError, AttributeError):
                    radiometer_sample = None
                if radiometer_sample is not None and sqm_calculator is None:
                    sqm_calculator = create_sqm_calculator(shared_state)
                    sqm_wing_estimator.reset()
                    profile = sqm_calculator.profile
                    sqm_cloud_estimator = CloudEstimator(
                        clear_zero_point=profile.clear_zero_point,
                        clear_sky_brightness=profile.clear_sky_brightness,
                    )
                    sqm_black_level = BlackLevelTracker(profile.bias_offset)
                if sqm_calculator is not None:
                    update_radiometric_sqm(
                        shared_state,
                        sqm_calculator,
                        sqm_radiometer,
                        radiometer_sample,
                        calculation_interval_seconds=SQM_CALCULATION_INTERVAL_SECONDS,
                        black_level_tracker=sqm_black_level,
                    )

                try:
                    img = camera_image.copy()
                    img = img.convert(mode="L")
                    np_image = np.asarray(img, dtype=np.uint8)

                    # Mark that we're attempting a solve - use image exposure_end timestamp.
                    # This is more accurate than wall clock and ties the attempt to the
                    # actual image so the integrator can dedupe.
                    last_solve_attempt = last_image_metadata["exposure_end"]

                    if cedar_fullframe_wanted and cedar_ff_geometry is None:
                        cedar_ff_geometry = _cedar_fullframe_geometry(
                            _sep_cfg, shared_state.camera_type()
                        )

                    t0 = precision_timestamp()
                    used_fullframe = False
                    ff_frame_hw = None
                    ff_center_solved = False
                    sep_thread = None
                    sep_thread_result = {}
                    if cedar_detect is not None:
                        ff_entry = None
                        if cedar_fullframe_wanted and cedar_ff_geometry is not None:
                            ff_entry = shared_state.solver_raw()
                            if (
                                not ff_entry
                                or "frame" not in ff_entry
                                or time.time() - float(ff_entry.get("timestamp") or 0)
                                > MAX_FRAME_AGE_S
                            ):
                                # No fresh raw: fall back to the 512 path for
                                # this attempt rather than skipping it.
                                ff_entry = None
                        # Centre-first: overlap the SEP detection with the
                        # cedar tiers (accepted CPU cost for latency).
                        if (
                            center_first_wanted
                            and ff_entry is not None
                            and sep_shadow is None
                            and sep_shadow_wanted
                        ):
                            sep_shadow = SepShadowRunner.create_if_enabled(
                                _sep_cfg, shared_state.camera_type()
                            )
                        if (
                            center_first_wanted
                            and ff_entry is not None
                            and sep_shadow is not None
                        ):

                            def _sep_detect_bg():
                                try:
                                    sep_thread_result["run"] = sep_shadow.detect(
                                        shared_state
                                    )
                                except Exception:
                                    logger.exception("Parallel SEP detect failed")

                            sep_thread = threading.Thread(
                                target=_sep_detect_bg, daemon=True
                            )
                            sep_thread.start()
                        try:
                            if ff_entry is not None:
                                ff_frame = np.asarray(ff_entry["frame"])
                                centroids = cedar_detect.extract_centroids(
                                    (ff_frame >> 4).astype(np.uint8),
                                    sigma=8,
                                    max_size=10,
                                    use_binned=True,
                                )
                                ff_frame_hw = (
                                    int(ff_frame.shape[0]),
                                    int(ff_frame.shape[1]),
                                )
                                used_fullframe = True
                                ff_raw_count = len(centroids)
                                if cedar_ff_gates_wanted and len(centroids):
                                    centroids = sep_detect.filter_plain_centroids(
                                        centroids,
                                        ff_frame,
                                        saturation_level=cedar_ff_geometry[
                                            "saturation_level"
                                        ],
                                        warm_pixel_map=cedar_ff_geometry["warm_map"],
                                    )
                                if horizon_mask_wanted and len(centroids):
                                    centroids, ground_dropped = (
                                        horizon_mask.filter_ground_centroids(
                                            centroids,
                                            ff_frame_hw,
                                            cedar_ff_geometry["rotation_deg"],
                                            last_image_metadata.get("imu"),
                                            cedar_ff_geometry["screen_direction"],
                                            cedar_ff_geometry["crop_width_px"],
                                        )
                                    )
                                else:
                                    ground_dropped = 0
                                if ff_raw_count != len(centroids):
                                    logger.debug(
                                        "FF gates: %d -> %d centroids "
                                        "(%d below horizon)",
                                        ff_raw_count,
                                        len(centroids),
                                        ground_dropped,
                                    )
                            else:
                                centroids = cedar_detect.extract_centroids(
                                    np_image, sigma=8, max_size=10, use_binned=True
                                )
                        except CedarConnectionError as e:
                            logger.warning(
                                f"Cedar connection failed: {e}, falling back to tetra3"
                            )
                            used_fullframe = False
                            centroids = tetra3.get_centroids_from_image(np_image)
                    else:
                        # Cedar not available, use tetra3
                        centroids = tetra3.get_centroids_from_image(np_image)
                    t_extract = (precision_timestamp() - t0) * 1000

                    logger.debug(
                        "File %s, extracted %d centroids in %.2fms"
                        % ("camera", len(centroids), t_extract)
                    )

                    solution: dict = {}

                    if len(centroids) == 0:
                        if log_no_stars_found:
                            logger.info("No stars found, skipping (Logged only once)")
                            log_no_stars_found = False
                    else:
                        log_no_stars_found = True
                        _solver_args = {}
                        if align_ra != 0 and align_dec != 0:
                            _solver_args["target_sky_coord"] = [[align_ra, align_dec]]

                        if used_fullframe:
                            solution = {}
                            if center_first_wanted:
                                subset = _center_square_subset(centroids, ff_frame_hw)
                                if 4 <= len(subset) < len(centroids):
                                    solution = _solve_cedar_fullframe(
                                        t3,
                                        subset,
                                        ff_frame_hw,
                                        cedar_ff_geometry["rotation_deg"],
                                        cedar_ff_geometry["crop_width_px"],
                                        shared_state,
                                        target_sky_coord=_solver_args.get(
                                            "target_sky_coord"
                                        ),
                                    )
                                    ff_center_solved = bool(
                                        solution and solution.get("RA") is not None
                                    )
                            if not solution or solution.get("RA") is None:
                                solution = _solve_cedar_fullframe(
                                    t3,
                                    centroids,
                                    ff_frame_hw,
                                    cedar_ff_geometry["rotation_deg"],
                                    cedar_ff_geometry["crop_width_px"],
                                    shared_state,
                                    target_sky_coord=_solver_args.get(
                                        "target_sky_coord"
                                    ),
                                )
                        else:
                            solution = t3.solve_from_centroids(
                                centroids,
                                (512, 512),
                                fov_estimate=12.0,
                                fov_max_error=4.0,
                                match_max_error=0.005,
                                return_matches=True,  # Required for SQM calculation
                                target_pixel=shared_state.target_pixel(),
                                solve_timeout=1000,
                                **_solver_args,
                            )

                    ff_matched_for_overlay = None
                    ff_in_crop_count = 0
                    if used_fullframe:
                        ff_in_crop_count = _count_in_crop(
                            centroids, ff_frame_hw, cedar_ff_geometry["crop_width_px"]
                        )
                        if solution and solution.get("RA") is not None:
                            # Same rule as the SEP fallback below: per-centroid
                            # outputs are in full-frame coordinates, so strip
                            # them before SQM photometry (which reads the 512
                            # frame) -- but keep the matched set aside for the
                            # overlay, which knows the canvas space.
                            ff_matched_for_overlay = solution.get("matched_centroids")
                            solution.pop("matched_centroids", None)
                            solution.pop("matched_stars", None)
                            solution.pop("matched_catID", None)

                    solve_path = (
                        ("cedar_ff" if used_fullframe else "cedar_512")
                        if cedar_detect is not None
                        else "tetra3"
                    )
                    if used_fullframe and ff_center_solved:
                        solve_path = "cedar_ff_center"

                    # SEP full-frame experiment: shadow-detect on every
                    # attempt; optionally rescue a failed production solve
                    # from the SEP centroids (sep_shadow module docstring).
                    if sep_shadow is None and sep_shadow_wanted:
                        sep_shadow = SepShadowRunner.create_if_enabled(
                            _sep_cfg, shared_state.camera_type()
                        )
                    sep_run = None
                    sep_fallback_used = False
                    if sep_shadow is not None:
                        if sep_thread is not None:
                            sep_thread.join(timeout=5.0)
                            sep_run = sep_thread_result.get("run")
                        else:
                            sep_run = sep_shadow.detect(shared_state)
                        if (
                            sep_run is not None
                            and sep_shadow.fallback_enabled
                            and (not solution or solution.get("RA") is None)
                            and len(sep_run.detection.centroids)
                            >= sep_shadow.min_fallback_stars
                            # Backoff: persistently unsolvable scenes
                            # (indoors, thick cloud) otherwise burn up to
                            # solve_timeout per attempt, starving the whole
                            # solver loop. Re-arms instantly on a SEP count
                            # jump (cloud gap opening on stars).
                            and sep_shadow.fallback_should_attempt(
                                len(sep_run.detection.centroids)
                            )
                        ):
                            # Hybrid alignment: cedar keeps priority (this
                            # branch only runs when it failed); under the
                            # target sky the SEP solve resolves the alignment
                            # coordinate and hands y/x_target back in 512
                            # space (sep_shadow.solve), so the normal
                            # alignment chain below consumes it unchanged.
                            _sep_target_sky = (
                                [[align_ra, align_dec]]
                                if align_ra != 0 and align_dec != 0
                                else None
                            )
                            fb_solution = None
                            sep_center_used = False
                            if center_first_wanted:
                                sep_subset = _center_square_subset(
                                    sep_run.detection.centroids,
                                    sep_run.frame_hw,
                                )
                                if (
                                    4
                                    <= len(sep_subset)
                                    < len(sep_run.detection.centroids)
                                ):
                                    fb_solution = sep_shadow.solve(
                                        t3,
                                        sep_run,
                                        shared_state,
                                        target_sky_coord=_sep_target_sky,
                                        centroids_override=sep_subset,
                                    )
                                    sep_center_used = bool(
                                        fb_solution
                                        and fb_solution.get("RA") is not None
                                    )
                            if not fb_solution or fb_solution.get("RA") is None:
                                fb_solution = sep_shadow.solve(
                                    t3,
                                    sep_run,
                                    shared_state,
                                    target_sky_coord=_sep_target_sky,
                                )
                            sep_shadow.record_fallback_result(
                                bool(fb_solution and fb_solution.get("RA") is not None),
                                len(sep_run.detection.centroids),
                            )
                            if fb_solution and fb_solution.get("RA") is not None:
                                # Per-centroid outputs are in full-frame
                                # coordinates; strip them so SQM photometry
                                # (which reads the 512 frame) never mixes
                                # coordinate spaces.
                                fb_solution.pop("matched_centroids", None)
                                fb_solution.pop("matched_stars", None)
                                # catID parallels the stripped arrays; keep the
                                # matched-* trio consistent on the message.
                                fb_solution.pop("matched_catID", None)
                                solution = fb_solution
                                sep_fallback_used = True
                                solve_path = "sep_center" if sep_center_used else "sep"
                                logger.debug(
                                    "SEP fallback solve SUCCESS - %d SEP "
                                    "centroids (cedar saw %d), RMSE %.1f",
                                    len(sep_run.detection.centroids),
                                    len(centroids),
                                    solution.get("RMSE") or -1.0,
                                )

                    if "matched_centroids" in solution:
                        if sqm_calculator is None:
                            sqm_calculator = create_sqm_calculator(shared_state)
                            sqm_wing_estimator.reset()
                            profile = sqm_calculator.profile
                            sqm_cloud_estimator = CloudEstimator(
                                clear_zero_point=profile.clear_zero_point,
                                clear_sky_brightness=profile.clear_sky_brightness,
                            )
                            sqm_black_level = BlackLevelTracker(profile.bias_offset)

                        # Expensive stellar photometry is diagnostic-only in the
                        # radiometer-first path and remains limited to 10 seconds.
                        exposure_sec = (
                            last_image_metadata["exposure_time"] / 1_000_000.0
                        )
                        # Topocentric altitude is computed later by the
                        # integrator. Do not mislabel an unavailable value as
                        # zenith; the published SQM remains uncorrected and the
                        # optional comparison diagnostic stays absent.
                        altitude_for_sqm = None

                        diagnostic_now = time.time()
                        if (
                            diagnostic_now - last_stellar_diagnostic
                            >= SQM_STELLAR_DIAGNOSTIC_INTERVAL_SECONDS
                        ):
                            update_sqm(
                                shared_state=shared_state,
                                sqm_calculator=sqm_calculator,
                                centroids=centroids,
                                solution=solution,
                                exposure_sec=exposure_sec,
                                altitude_deg=altitude_for_sqm,
                                calculation_interval_seconds=SQM_CALCULATION_INTERVAL_SECONDS,
                                wing_estimator=sqm_wing_estimator,
                                cloud_estimator=sqm_cloud_estimator,
                                black_level_tracker=sqm_black_level,
                                publish=False,
                            )
                            last_stellar_diagnostic = diagnostic_now

                        # Don't clutter printed solution with these fields (use pop to safely remove)
                        solution.pop("pattern_centroids", None)
                        solution.pop("epoch_equinox", None)
                        solution.pop("epoch_proper_motion", None)
                        solution.pop("cache_hit_fraction", None)

                    if solution and solution.get("RA") is not None:
                        last_solve_success = last_solve_attempt
                        if sep_shadow is not None and not sep_fallback_used:
                            # Production solve: sky is workable, clear the
                            # fallback backoff so the next cedar failure gets
                            # an immediate rescue try.
                            sep_shadow.note_solved()
                        solve_result = _build_successful_solve(
                            solution=solution,
                            last_image_metadata=last_image_metadata,
                            last_solve_attempt=last_solve_attempt,
                            last_solve_success=last_solve_success,
                            # A fallback solve's real star count is SEP's --
                            # publishing it keeps auto-exposure's solve-hold
                            # engaged on the exposure that actually solved.
                            # Full-frame cedar publishes the in-crop count so
                            # auto-exposure keeps its 512-crop semantics.
                            centroid_count=(
                                len(sep_run.detection.centroids)
                                if sep_fallback_used and sep_run is not None
                                else (
                                    ff_in_crop_count
                                    if used_fullframe
                                    else len(centroids)
                                )
                            ),
                            solve_path=solve_path,
                        )
                        # Popped only now: _build_successful_solve above needs
                        # it for the Gaia-G reference band.
                        solution.pop("matched_catID", None)

                        total_tetra_time = t_extract + (solution.get("T_solve") or 0)
                        if total_tetra_time > 1000:
                            console_queue.put(f"SLV: Long: {total_tetra_time}")
                            logger.warning("Long solver time: %i", total_tetra_time)

                        logger.debug(
                            f"Solve SUCCESS - {len(centroids)} centroids → "
                            f"{solve_result.diagnostics.Matches} matches, "
                            f"RMSE: {solve_result.diagnostics.RMSE:.1f}px"
                        )

                        # See if we are waiting for alignment
                        if align_ra != 0 and align_dec != 0:
                            if solve_result.alignment.is_set():
                                align_result_queue.put(
                                    AlignedResult(
                                        y_target=solve_result.alignment.y_target,
                                        x_target=solve_result.alignment.x_target,
                                    )
                                )
                                logger.debug(
                                    "Align target_pixel=(%s, %s)",
                                    solve_result.alignment.y_target,
                                    solve_result.alignment.x_target,
                                )
                            align_ra = 0
                            align_dec = 0
                            # Clear alignment fields from the message now that
                            # the result has been consumed.
                            solve_result.alignment = AlignmentResult()

                        solver_queue.put(solve_result)
                    else:
                        if solution:
                            logger.warning(
                                f"Solve FAILED - {len(centroids)} centroids detected but "
                                f"pattern match failed "
                                f"({'full-frame native FOV' if used_fullframe else 'FOV est: 12.0°, max err: 4.0°'})"
                            )
                        solver_queue.put(
                            _build_failed_solve(
                                last_solve_attempt=last_solve_attempt,
                                last_solve_success=last_solve_success,
                                t_extract_ms=t_extract,
                                centroid_count=(
                                    ff_in_crop_count
                                    if used_fullframe
                                    else len(centroids)
                                ),
                                solve_path=solve_path,
                            )
                        )

                    if sep_shadow is not None:
                        # Overlay ships once per attempt, after the solve
                        # outcome, so the confirmed/candidate split is never
                        # overwritten by the next detect (race fixed).
                        # A production (cedar) solve contributes its matched
                        # stars too -- green means "confirmed by whichever
                        # solver succeeded".
                        if (
                            solution
                            and solution.get("RA") is not None
                            and not sep_fallback_used
                        ):
                            if used_fullframe:
                                # Full-frame cedar matched stars are already
                                # in the rotated canvas (same space as a SEP
                                # solve's); stashed before the SQM strip.
                                if ff_matched_for_overlay is not None:
                                    sep_shadow.attach_canvas_matched(
                                        ff_matched_for_overlay
                                    )
                            else:
                                sep_shadow.attach_production_matched(solution)
                        sep_shadow.publish_overlay(shared_state)
                        sep_shadow.log_attempt(
                            exposure_us=last_image_metadata.get("exposure_time"),
                            gain=last_image_metadata.get("gain"),
                            cedar_count=len(centroids),
                            matches=solution.get("Matches") if solution else None,
                            solved=bool(solution and solution.get("RA") is not None),
                            run=sep_run,
                            fallback_used=sep_fallback_used,
                            fallback_rmse=(
                                solution.get("RMSE")
                                if sep_fallback_used and solution
                                else None
                            ),
                        )
                except Exception as e:
                    logger.error(
                        f"Exception during solve attempt: {e.__class__.__name__}: {str(e)}"
                    )
                    logger.exception(e)
                    last_solve_attempt = last_image_metadata["exposure_end"]
                    solver_queue.put(
                        _build_failed_solve(
                            last_solve_attempt=last_solve_attempt,
                            last_solve_success=last_solve_success,
                            t_extract_ms=0.0,
                        )
                    )
        except EOFError as eof:
            logger.error(f"Main process no longer running for solver: {eof}")
            logger.exception(eof)
            logger.error(
                f"Last solve attempt: {last_solve_attempt}, last success: {last_solve_success}"
            )
        except Exception as e:
            logger.error(f"Exception in Solver: {e.__class__.__name__}: {str(e)}")
            logger.exception(e)
            logger.error(f"Current process ID: {os.getpid()}")
            logger.error(f"Current thread: {threading.current_thread().name}")
            try:
                logger.error(
                    f"Active threads: {[t.name for t in threading.enumerate()]}"
                )
            except Exception:
                pass  # Don't let diagnostic logging fail
