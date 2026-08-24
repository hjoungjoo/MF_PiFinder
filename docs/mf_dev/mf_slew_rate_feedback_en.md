# Confirmed slew-rate feedback

PiFinder uses one OnStep/INDI slew-rate selection for the INDI web page,
keypad, and joystick. The native selection order is:

`Off`, `1/2`, `1`, `2`, `4`, `8`, `20`, `48`, `1/2 MAX`, `MAX`.

Keypad `9` increases the rate and `3` decreases it. Joystick speed controls
use the same path. A brief on-device popup is shown only after the INDI update
has completed successfully; it therefore reports the confirmed new rate rather
than the previous rate. If the driver update fails, no success popup is shown.

The motion direction controls remain immediate press/hold commands and are not
delayed by this confirmation step.
