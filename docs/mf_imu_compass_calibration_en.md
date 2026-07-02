# MF PiFinder IMU Compass Calibration

## Purpose

The default IMU mode remains IMUPLUS. It does not use the magnetometer, so it is less sensitive to local magnetic interference, but absolute heading still depends on plate solving plus IMU dead-reckoning.

Set `Settings > IMU Settings > Compass > On` to use BNO055 NDOF mode. This includes the magnetometer and can improve absolute heading stability, but it is sensitive to nearby metal, current, magnets, and calibration quality.

## Automatic Calibration

1. Set `Settings > IMU Settings > Compass > On`.
2. Restart PiFinder.
3. Check `Tools > Status` and watch `IMU CAL`.
   - Format: `NDO Sx Gy Az Mw`
   - `S/G/A/M` mean system, gyro, accelerometer, and magnetometer calibration.
   - Each value ranges from `0` to `3`; `S3 G3 A3 M3` means fully calibrated.
   - IMU tracking continues based on gyro readiness; the full `S/G/A/M` tuple is used for compass quality and automatic calibration save.
4. When fully calibrated, PiFinder automatically saves the BNO055 offsets/radii.
5. On the next startup, the saved calibration is loaded automatically.

## Manual Calibration Menu

Use `Settings > IMU Settings > Calibration`.

- `Save`: save the current BNO055 calibration offsets/radii.
- `Load`: apply the saved calibration to the sensor.
- `Clear`: remove the saved calibration file.

## Notes

- NDOF is sensitive to magnetic environment. Batteries, motors, speakers, high-current wires, and steel structures can disturb heading.
- During indoor tests, magnetometer calibration may be slow or unstable.
- If NDOF is unstable, set `Settings > IMU Settings > Compass > Off` to return to the legacy IMUPLUS behavior.
