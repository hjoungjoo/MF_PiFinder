# MF PiFinder IMU Compass Calibration

## 목적

기본 IMU 모드는 기존과 같은 IMUPLUS입니다. 이 모드는 자력계를 쓰지 않으므로 주변 자기장 영향이 적지만, 절대 방위각은 plate solve 이후의 IMU dead-reckoning에 의존합니다.

`Settings > IMU Compass > On`을 선택하면 BNO055 NDOF 모드를 사용합니다. 이 모드는 자력계를 포함해 절대 방위각 안정성을 개선할 수 있지만, 주변 금속/전류/자석 영향과 캘리브레이션 상태에 민감합니다.

## 자동 캘리브레이션

1. `Settings > IMU Compass > On`으로 변경합니다.
2. PiFinder를 재시작합니다.
3. `Tools > Status`에서 `IMU CAL`을 확인합니다.
   - 형식: `NDO Sx Gy Az Mw`
   - `S/G/A/M`은 각각 system, gyro, accel, magnetometer calibration입니다.
   - 각 값이 `3`에 가까울수록 좋고, `S3 G3 A3 M3`이면 완전 캘리브레이션입니다.
4. 완전 캘리브레이션이 되면 PiFinder가 BNO055 offsets/radius 값을 자동 저장합니다.
5. 다음 시작부터 저장된 캘리브레이션이 자동 로드됩니다.

## 수동 캘리브레이션 메뉴

`Settings > IMU Calib.`에서 사용할 수 있습니다.

- `Save`: 현재 BNO055 calibration offsets/radius를 저장합니다.
- `Load`: 저장된 calibration 값을 센서에 다시 적용합니다.
- `Clear`: 저장된 calibration 파일을 삭제합니다.

## 주의사항

- NDOF는 주변 자기장 환경에 민감합니다. 배터리, 모터, 스피커, 강한 전류선, 철제 구조물 근처에서는 방위가 흔들릴 수 있습니다.
- 실내 테스트에서는 magnetometer 값이 늦게 올라가거나 안정되지 않을 수 있습니다.
- NDOF가 불안정하면 `Settings > IMU Compass > Off`로 되돌리면 기존 IMUPLUS 동작을 사용합니다.
