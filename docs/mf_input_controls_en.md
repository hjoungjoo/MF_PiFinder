# MF PiFinder Input Controls (Keypad & Keyboard)

Baseline: `mf_pifinder` branch, 2026-07-11.

This document is the accurate, source-derived reference for how keypad and
keyboard input is handled across the PiFinder UI: the global behavior shared by
every screen, and the screens that behave specially. It also lists the known
inconsistencies and a proposed target model, so input handling can be revised
against a single agreed spec.

Companion doc: `docs/mf_keyboard_mapping_en.md` describes the physical key ->
PiFinder-input mapping (which key produces which event). This doc describes what
the UI does with those events.

## 1. Input sources and event encoding

There are three input sources, and they do **not** all emit the same events.

| Source | Module | Numbers | Letters | Notes |
| --- | --- | --- | --- | --- |
| GPIO keypad (device) | `keyboard_pi.py` | **press/release** (`NUMBER_PRESS_BASE=3000`, `RELEASE=3100`) | n/a | `SQUARE` held + key = `ALT_*` |
| USB/Bluetooth HID keyboard (device) | `keyboard_pi.py` (libinput) | **press/release** | **press/release** (`TEXT_PRESS/RELEASE`) | Alt/Ctrl/Shift combos = `ALT_*` / `LNG_*` |
| Dev host keyboard | `keyboard_local.py` (`--keyboard local`) | **plain 0-9** (`key_number`) | mapped keys only | pyhotkey; for development |

**Key consequence (the root of most divergences):** on real hardware (keypad +
USB/BT) number and letter keys arrive as **press/release** events, while the dev
keyboard emits **plain single numbers**. How a screen reacts therefore depends on
which of `key_number` vs `key_number_press`/`key_number_release` it implements.

Key-event codes (`keyboard_interface.py`): base keys `LEFT=20 UP=21 DOWN=22
RIGHT=24 PLUS=11 MINUS=12 SQUARE=13`; `ALT_*=101..110`; `LNG_*=200..204`;
numbers/letters via press/release bases above; a plain number is `keycode < 10`.

## 2. Global controls (menu_manager — apply on every screen)

`main.py` reads the queue and calls `menu_manager.key_*`, which either handles
the key itself (help overlay, marking menu, back/jump) or forwards to the active
screen `self.stack[-1].key_*`.

| Input | Global action |
| --- | --- |
| `LEFT` | Go back: pop the current screen off the stack (unless the screen's `key_left()` returns `False` to stay) |
| `LNG_LEFT` | Jump to the top of the menu (reset to root) |
| `LNG_RIGHT` | Jump to the most-recent object's Object Details |
| `LNG_SQUARE` | Toggle the current screen's **marking menu** |
| `SQUARE` | If a marking menu is open: go back one marking-menu level / close; else forward to screen |
| `UP`/`DOWN`/`RIGHT`/`PLUS`/`MINUS`/numbers/letters | Forward to the active screen |
| Help overlay open | Any key closes help; `UP`/`DOWN` page through help images |
| `ALT_PLUS`/`ALT_MINUS` | Display brightness up/down (global) |
| `ALT_0` | Screenshot |
| `ALT_LEFT` | Save camera image |
| `ALT_RIGHT` | Save full debug dump (image + solution + state) |

### Marking menu model (`marking_menus.py`)

- Opened by `LNG_SQUARE`; rendered as a 4-slice pie over a frozen screenshot.
- Holds four `MarkingMenuOption`s: `up` (default `HELP`), `down`, `left`,
  `right`. While open, `LEFT`/`UP`/`DOWN`/`RIGHT` select the matching option and
  `SQUARE` backs out one level (or closes when the stack empties).
- An option can: open a nested marking menu; show HELP; `menu_jump` to a labeled
  menu (e.g. `filter_options`, `shutdown`, `camera_gain`); or run a callback.

### Dead / unhandled inputs (currently no effect anywhere)

- `LNG_UP` / `LNG_DOWN` (200/201 emitted) — never dispatched by `main.py`, and
  `menu_manager.key_long_up/down` are `pass`. Makes
  `UIObjectList.key_long_up/key_long_down` unreachable dead code.
- `ALT_UP` / `ALT_DOWN` / `ALT_SQUARE` — emitted by keyboards but not handled by
  `main.py` and not forwarded to screens.

## 3. Base screen defaults (`UIModule`)

Unless a screen overrides them:

| Input | Default |
| --- | --- |
| `LEFT` | return `True` -> screen is popped (go back) |
| `RIGHT` / `UP` / `DOWN` | no-op |
| `SQUARE` | `cycle_display_mode()` (advance the screen's display mode) |
| `PLUS` / `MINUS` / numbers / letters | no-op |
| number **press** | falls through to `key_number()` (so a tap fires the discrete handler); number **release** = no-op |

## 4. Standard menu — `UITextMenu` (and list variants)

`UITextMenu` inherits **`GuideKeyMixin`** (see §6), so on standard menus the
number/letter/`+`/`-` keys are hijacked for INDI mount guiding **when mount
control is on**, and are otherwise no-ops.

| Input | Action |
| --- | --- |
| `UP` / `DOWN` | Scroll the highlight (`menu_scroll`) |
| `RIGHT` | Select: run item `callback`, or enter submenu `class`, or set a `config_option` value (see below), then run menu `post_callback` |
| `LEFT` | Go back (pop) |
| `SQUARE` | `cycle_display_mode` (usually a no-op on plain menus) |
| numbers / letters / `+` / `-` | **GuideKeyMixin** -> mount guide (mount on) or no-op |

Config-option menus (via `RIGHT`):
- `single`: set the one value. For `filter.*` options, changing the value also
  auto-navigates back to the parent menu.
- `multi`: toggle the item in/out of the selection; `Select All` / `Select None`
  handle bulk toggles.

### List variants (all inherit `UITextMenu` + `GuideKeyMixin`)

- **`UIObjectList`**: `RIGHT` opens Object Details for the highlighted object;
  `SQUARE` cycles display mode `LOCATE -> NAME -> INFO`; **numbers type a catalog
  sequence to jump to** (e.g. 45 -> M45) via `key_number`. Sort order and Filter
  are on the marking menu (`Sort` nested MM, `Filter` -> `filter_options`).
- **`UIObsList`**: `RIGHT` descends into a folder or loads a `.skylist` and opens
  it as an object list; `LEFT` back.
- **`UILocationList`**: `RIGHT` opens a per-location action menu (Load / Delete /
  Rename); `UP`/`DOWN` move within it; `LEFT` closes the action menu or backs out.
- **`UIEquipment`** (`GuideKeyMixin` + `UIModule`): `UP`/`DOWN` switch Telescope /
  Eyepiece row; `RIGHT` opens the corresponding select menu.

## 5. Screens with special key behavior

### Object Details — `UIObjectDetails` (custom; NOT GuideKeyMixin)

| Input | Action |
| --- | --- |
| `UP` / `DOWN` | Previous / next object in the list |
| `LEFT` | Back (and add to recents) |
| `RIGHT` | Open the **Log** screen (requires a pointing solution) |
| `SQUARE` | Cycle display mode: DESC / LOCATE / POSS / SDSS / Contrast |
| `PLUS` / `MINUS` | Mount on: slew-rate up/down ("Speed +/-"); mount off: cycle eyepiece FOV / scroll description |
| number **tap** (`key_number`) | Discrete INDI mount command: 0=Stop 1=Init+Sync 2=South-step 3=Step- 4=West 5=**GoTo target** 6=East 7=**Sync** 8=North 9=Step+ |
| number **press/release** (`key_number_press`) | If mount control on: 8-way **hold-to-move** guide (1=SW 2=S 3=SE 4=W 6=E 7=NW 8=N 9=NE; 0/5=stop; release=stop) |
| letters (HID) | 8-way hold-to-move guide (q/w/e/a/s/d/z/x/c, s=stop) |

### Text / number entry

- **`UITextEntry`**: multi-tap or T9 text input. Digit keys cycle letters
  (multi-tap) or enter numbers (T9/search). `SQUARE` toggles letter/symbol set;
  `PLUS` inserts space; `MINUS` deletes; long `MINUS` clears; `LEFT` confirms or
  goes back; `RIGHT` shows search results; HID letters append directly.
- **`UIDateEntry`** (gated on a location/GPS fix): digits fill yyyy/mm/dd boxes
  (auto-advance); `MINUS` deletes / moves to previous box; `RIGHT` advances /
  confirms; `LEFT` previous box or cancel.
- **`UILocationEntry`**: digits fill lat/lon/alt boxes; `MINUS` deletes / previous
  box; `PLUS` toggles sign (N/S, E/W); `RIGHT` advances the lat->lon->alt flow;
  `LEFT` previous box or cancel.

### INDI mount screens (`indi.py`)

- **`UIIndiInit`**: discrete one-shot commands on digits — 1=Init 2=Sync
  loc/time 3=Park 4=Set Home 5=Return Home 6=Unpark 7=Set Park 8=Restart driver;
  `SQUARE` = Init.
- **`UIIndiBacklash`**: digits enter the selected axis's backlash value (0-999);
  `PLUS` **and** `MINUS` both toggle RA<->DE axis; `RIGHT` runs auto-backlash;
  `SQUARE` saves both axes.
- **`UIIndiGuide`**: `0`/`5` are discrete toggles (0=guide correction on/off,
  5=one-shot refine on/off); direction digits `1-4/6-9` and letters are
  **press/hold-to-move** guide (release stops); `PLUS`/`MINUS` slew-rate up/down;
  `SQUARE` syncs the mount to the current solve.
- **`UIIndiMultiPointAlign`** (extends `UIIndiGuide`): staged wizard. In setup
  stages digits are discrete (pick point count / mode); in the ADJUST stage the
  same direction digits/letters become hold-to-move jog. `UP`/`DOWN`, `LEFT`/
  `RIGHT`, `SQUARE` drive the wizard stage transitions.

### Alignment screens

- **`UIAlign`**: `SQUARE` toggles align mode (and saves alignment on exit);
  `UP`/`DOWN`/`RIGHT`/`LEFT` move the star selection in align mode; `PLUS`/`MINUS`
  zoom; `1` reset reticle, `0` cancel (align mode only).
- **`UIAlignDaytime`**: `SQUARE` start / save; quadrant digits `7 9 1 3` pick the
  region; arrows switch to fine mode and nudge 1px; `0` cancel; `PLUS`/`MINUS`
  exposure up/down.
- **`UIPolarAlign`**: `SQUARE` advances the wizard; `MINUS` cancels/backs out;
  `0` computes (in AIM with >=2 solves).

### Info / utility screens

- **`UILog`**: `RIGHT` acts on the current field (log & exit / cycle rating /
  submenu / eyepiece); `UP`/`DOWN` move the field index; digits set the current
  star rating (observability / appeal).
- **`UIChart`**: `PLUS`/`MINUS` zoom; `SQUARE` reset FOV to the camera view.
- **`UIPreview`** (`GuideKeyMixin`): `PLUS`/`MINUS` **zoom** (overrides the mixin
  slew-rate), `SQUARE` toggles the focus/HUD overlay; numbers/letters still guide
  the mount when mount control is on.
- **`UIConsole`**: `UP`/`DOWN` scroll log; digits are a dev shortcut (0 toggles
  camera debug; any digit sets a fixed debug datetime).
- **Passive status screens** (`UIGPSStatus`, `UIGPSTimeSyncStatus`,
  `UIIndiStatus`): plain `GuideKeyMixin` — arrows do their local thing, all
  numbers/letters/`+`/`-` are mount guide when mount control is on.

## 6. GuideKeyMixin — the cross-cutting number/letter hijack

`GuideKeyMixin` (`base.py`) overrides `key_number`, `key_number_press`,
`key_number_release`, `key_text*`, `key_plus`, `key_minus` and routes them to
INDI mount guiding (`_guide_*`). It is inherited by `UITextMenu` (hence every
standard menu and list), `UIEquipment`, `UIPreview`, and the passive status
screens.

- Numbers 1-9 -> compass move (1=SW 2=S 3=SE 4=W 6=E 7=NW 8=N 9=NE), 0/5=stop.
- Letters q/w/e/a/s/d/z/x/c -> compass move, s=stop.
- `+`/`-` -> increase/reduce slew rate.
- Everything funnels through `_guide_mount_queue`; if `mount_control` is off it
  returns `None` and the key is a **no-op**. Arrow and `SQUARE` controls are never
  touched by the mixin.

Because the mixin overrides `key_number_press` to go straight to guide (it does
**not** fall back to `key_number`), a subclass's own `key_number` handler is
**unreachable from the physical keypad / HID** (press/release) and only fires
from the dev keyboard's plain numbers.

## 7. Known inconsistencies (candidates to fix)

1. **Discrete Object Details mount commands are shadowed on real hardware.** On a
   device with mount control on, number press/release routes to hold-to-move
   guide, so `key_number`'s GoTo(5)/Sync(7)/Init(1)/step(3,9) never run — you
   cannot GoTo/Sync a target from the keypad. They only run from the dev keyboard.
2. **Object List catalog-number jump is dead on the keypad.** `UIObjectList`
   implements only `key_number`; the keypad/HID send press/release, which
   `GuideKeyMixin` consumes for guide, so typing a sequence number to jump works
   only from the dev keyboard.
3. **Standard menus become a mount joystick.** With mount control on, every
   `UITextMenu` (all menus/lists) turns its number/letter/`+`/`-` keys into mount
   guiding — easy to trigger unintentionally while navigating.
4. **Dev keyboard vs device diverge fundamentally** (plain number vs
   press/release), so behavior differs between `--keyboard local` testing and real
   hardware.
5. **Dead keys**: `LNG_UP`/`LNG_DOWN`, `ALT_UP`/`ALT_DOWN`/`ALT_SQUARE` are
   emitted but never dispatched.
6. **`UIIndiBacklash`**: `PLUS` and `MINUS` do the identical action (toggle axis).
7. **Docs vs code**: `mf_keyboard_mapping` presents the discrete Object Details
   number commands as the behavior for keypad/HID/GPIO, but on real hardware they
   are shadowed by guide (see #1).

## 8. Proposed target model (for discussion)

Goal: one predictable scheme, identical on keypad and keyboard, that keeps both
discrete actions and hold-to-move guide without each screen re-implementing the
split.

- **Introduce a tap-vs-hold layer in the dispatch** (main.py / keyboard driver):
  on number/letter press, start a timer; a quick release (< ~400 ms) emits a
  single **tap** (`key_number(n)` / `key_text(c)`); crossing the hold threshold
  emits **hold-start** (`key_number_press`) and **hold-stop** (`key_number_release`)
  on release. Both keypad and keyboard then feed the same tap/hold events, and the
  dev-keyboard plain-number path is unified to a tap.
- **Define one convention:** tap = discrete action (GoTo/Sync/select/jump/enter),
  hold = mount guide / auto-repeat. `GuideKeyMixin` maps hold -> guide and leaves
  tap to the screen's discrete handler (so Object List jump and Object Details
  GoTo both work on device).
- **Gate mount guiding** so it does not hijack numbers on ordinary navigation
  menus unless the screen opts in (e.g. only Object Details / Preview / status
  screens guide; plain menus do not).
- **Clean up dead keys** — either wire `LNG_UP/DOWN` and `ALT_UP/DOWN` to real
  actions or drop them from the mapping.
- **Fix `UIIndiBacklash`** `PLUS`/`MINUS` to be distinct (e.g. +/- adjust value,
  or one toggles axis and the other something useful).
- **Re-sync `mf_keyboard_mapping`** with the final behavior.

The exact tap threshold and which screens opt into guiding should be decided,
then implemented against this doc.
