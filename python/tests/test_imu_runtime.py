import time

import pytest
import quaternion

from PiFinder.imu_pi import _update_imu_safely
from PiFinder.types.positioning import ImuSample


pytestmark = pytest.mark.unit


def _sample(**overrides):
    values = {
        "quat": quaternion.one,
        "timestamp": time.time(),
        "status": 3,
        "moving": True,
        "sensor_healthy": True,
    }
    values.update(overrides)
    return ImuSample(**values)


def test_safe_update_contains_runtime_i2c_error_and_invalidates_sample():
    class FailingImu:
        last_io_time = 10.0

        def update(self):
            raise OSError("i2c bus failed")

    sample = _sample()

    failed, errors, recovered = _update_imu_safely(FailingImu(), sample, 1)

    assert failed is True
    assert errors == 2
    assert recovered is False
    assert sample.sensor_healthy is False
    assert sample.consecutive_errors == 2
    assert sample.last_error == "OSError: i2c bus failed"
    assert sample.moving is False
    assert sample.is_usable() is False


def test_safe_update_marks_sensor_healthy_after_new_successful_io():
    class RecoveredImu:
        def __init__(self):
            self.last_io_time = 10.0

        def update(self):
            self.last_io_time = 11.0

    sample = _sample(
        sensor_healthy=False,
        consecutive_errors=3,
        last_error="OSError: old failure",
    )

    failed, errors, recovered = _update_imu_safely(RecoveredImu(), sample, 3)

    assert failed is False
    assert errors == 0
    assert recovered is True
    assert sample.sensor_healthy is True
    assert sample.consecutive_errors == 0
    assert sample.last_error is None
    assert sample.last_success_time == 11.0


def test_throttled_update_does_not_claim_recovery_without_io():
    class ThrottledImu:
        last_io_time = 10.0

        def update(self):
            return None

    sample = _sample(sensor_healthy=False, consecutive_errors=1)

    failed, errors, recovered = _update_imu_safely(ThrottledImu(), sample, 1)

    assert failed is False
    assert errors == 1
    assert recovered is False
    assert sample.sensor_healthy is False
