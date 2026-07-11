# MF_PiFinder Keyboard Mapping

This document summarizes the USB/Bluetooth keyboard and GPIO keypad mappings in
the `mf_pifinder` branch.

## USB/Bluetooth Keyboard

| Key | PiFinder input |
| --- | --- |
| Arrow keys | `LEFT`, `UP`, `DOWN`, `RIGHT` |
| Enter / Keypad Enter | `SQUARE` |
| Esc | `LEFT` |
| Backspace | `MINUS` |
| `=` / Keypad `+` | `PLUS` |
| `-` / Keypad `-` | `MINUS` |
| Number `0-9` / Keypad numbers | Number `0-9` |
| Space | Space character |
| `a-z` | Lowercase text input |
| `Shift + a-z` | Uppercase text input |

## Alt Combinations

| Key | PiFinder input |
| --- | --- |
| `Alt + Arrow key` | `ALT_LEFT`, `ALT_UP`, `ALT_DOWN`, `ALT_RIGHT` |
| `Alt + =` / `Alt + Keypad +` | `ALT_PLUS` |
| `Alt + -` / `Alt + Keypad -` | `ALT_MINUS` |
| `Alt + 0` / `Alt + Keypad 0` | `ALT_0` |
| `Alt + Enter` / `Alt + Keypad Enter` | `ALT_SQUARE` |

## Long Press

Holding a key for at least 1 second sends a long-key input.

| Key | PiFinder input |
| --- | --- |
| Hold `Left` | `LNG_LEFT` |
| Hold `Right` | `LNG_RIGHT` |
| Hold `Enter` / `Keypad Enter` | `LNG_SQUARE` |
| Hold `Up` | Repeated `UP` |
| Hold `Down` | Repeated `DOWN` |

For compatibility, pressing `Shift` or `Ctrl` with `Left`, `Up`, `Down`,
`Right`, or `Enter` sends `LNG_LEFT`, `LNG_UP`, `LNG_DOWN`, `LNG_RIGHT`, or
`LNG_SQUARE`.

## GPIO Keypad

| Keypad key | PiFinder input |
| --- | --- |
| Number keys | Number `0-9` |
| `+` | `PLUS` |
| `-` | `MINUS` |
| Square/confirm key | `SQUARE` |
| Direction keys | `LEFT`, `UP`, `DOWN`, `RIGHT` |

On the GPIO keypad, holding `SQUARE` while pressing a direction key, `+`, `-`,
or `0` sends the matching `ALT_*` input.

## INDI Mount Control

INDI mount control is optional. It is available only after installing INDI
support with `scripts/install_indi_mount.sh` and enabling this PiFinder setting:

```text
Settings > Experimental > Mount Control > On
```

When Mount Control is enabled, the number keys send these mount-control actions on
the Object Details screen, on ordinary menus, and on the status screens (the one
shared map — see `docs/mf_input_keymap_en.md`). USB/Bluetooth number keys, keypad
number keys, and GPIO number keys behave the same way. Continuous directional jog
is also on the keyboard letters, and the dedicated INDI Guide screen keeps its own
jog scheme. On the object list the number keys instead type a catalog sequence to
jump to, and a letter opens the Name Search.

| Key | INDI mount action |
| --- | --- |
| `0` | Stop mount |
| `2` | Move south — while the key is held |
| `4` | Move west — while the key is held |
| `5` | GoTo — only on Object Details (a selected object) |
| `6` | Move east — while the key is held |
| `7` | Sync mount to the current PiFinder solved position |
| `8` | Move north — while the key is held |
| `1`, `3`, `9` | unused |

The cardinal keys move the mount for as long as they are held (press starts the
motion, release stops it), so you move exactly as much as you press. `5` (GoTo)
only works on the Object Details screen, where an object is selected; on ordinary
menus and status screens there is no target, so `5` does nothing. There is no
step-size setting, and `1` no longer initializes/syncs — the mount inits and syncs
automatically at startup. Movement speed is set by the slew rate (`+`/`-`).

If the INDI server or mount connection has a problem, the normal PiFinder
features continue running. Mount connection status is written here:

```text
~/PiFinder_data/mount_control_status.json
```
