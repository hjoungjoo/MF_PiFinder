from PiFinder import imu_calibration


class FakeBno055:
    def __init__(self):
        self.offsets_accelerometer = (1, 2, 3)
        self.offsets_magnetometer = (4, 5, 6)
        self.offsets_gyroscope = (7, 8, 9)
        self.radius_accelerometer = (10,)
        self.radius_magnetometer = (11,)


def test_snapshot_round_trip_applies_all_bno055_fields():
    source = FakeBno055()
    target = FakeBno055()
    target.offsets_accelerometer = (0, 0, 0)
    target.offsets_magnetometer = (0, 0, 0)
    target.offsets_gyroscope = (0, 0, 0)
    target.radius_accelerometer = (0,)
    target.radius_magnetometer = (0,)

    snapshot = imu_calibration.snapshot_from_sensor(source)
    imu_calibration.apply_snapshot_to_sensor(target, snapshot)

    assert target.offsets_accelerometer == source.offsets_accelerometer
    assert target.offsets_magnetometer == source.offsets_magnetometer
    assert target.offsets_gyroscope == source.offsets_gyroscope
    assert target.radius_accelerometer == source.radius_accelerometer
    assert target.radius_magnetometer == source.radius_magnetometer


def test_snapshot_file_helpers(tmp_path):
    path = tmp_path / "imu_calibration.json"
    snapshot = imu_calibration.snapshot_from_sensor(FakeBno055())

    assert imu_calibration.load_snapshot(path) is None
    imu_calibration.save_snapshot(snapshot, path)
    assert imu_calibration.load_snapshot(path) == snapshot
    assert imu_calibration.clear_snapshot(path) is True
    assert imu_calibration.clear_snapshot(path) is False
