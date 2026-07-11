# MF PiFinder Keypad-First Input Scheme (Grouped Key Map)

Baseline: `mf_pifinder` branch, 2026-07-11.

This is the **target** input design. The goal is a keypad-first scheme that
**blends smoothly with the existing behavior** — it preserves today's keypad
conventions and only resolves the places where inputs collide. It groups the UI
pages and defines the key mapping per group.

For the *current* behavior and the conflicts this resolves, see
`docs/mf_input_controls_en.md`.

## Design principles

1. **Keypad-first.** Every action is reachable with the physical keypad alone
   (arrows, `SQUARE`, `+`, `-`, `0-9`, long-press). A USB/Bluetooth keyboard is an
   accessory that adds letter keys but never unlocks a keypad-impossible action.
2. **Preserve, then reconcile.** Existing behaviors stay as they are wherever they
   do not collide. We change a key's meaning only on the groups that genuinely
   need it for their own purpose (object selection, text, numeric input). This
   keeps the change small and the feel continuous.
3. **INDI jog stays the default** for screens that have no other use for
   numbers/letters — plain menus (mount on), INDI screens, Object Details, status
   screens. This is the existing `GuideKeyMixin` behavior and it is retained, so
   **the alphabet keeps driving INDI manual jog** where the mount is controlled.
4. **Input groups override that default** so numbers/letters do their own job:
   object selection (catalog jump / name search), text search (T9 + English),
   numeric entry (digits). So **English input works where names are typed**, with
   no conflict.
5. **Global keys are identical everywhere** (below).

## Global keys (all groups — unchanged)

| Key | Action |
| --- | --- |
| `LEFT` | Back (pop current screen) |
| `LNG_LEFT` | Home (menu root) |
| `LNG_RIGHT` | Jump to most-recent object's Object Details |
| `LNG_SQUARE` | Open/close the screen's marking menu |
| `ALT_+` / `ALT_-` | Brightness up/down |
| `ALT_0` | Screenshot |
| `ALT_LEFT` / `ALT_RIGHT` | Save image / debug dump |

`SQUARE` closes one marking-menu level when a marking menu is open; otherwise it
is a per-group content key.

## Number keys that drive the mount — one shared map

Wherever the **number keys** control the INDI mount (Object Details, plain menus
with mount control on, status screens), they use one shared mapping:

| Key | Mount action |
| --- | --- |
| `0` | Stop (also stops the GoTo/Guide auto-correction) |
| `2` | Move South — **while held** |
| `4` | Move West — **while held** |
| `5` | GoTo — **only where an object is selected** (Object Details); re-arms auto-correction |
| `6` | Move East — **while held** |
| `7` | Sync to current solve |
| `8` | Move North — **while held** |
| `1` `3` `9` | unused |

The cardinal keys (`2`/`4`/`6`/`8`) **move the mount for as long as they are
held** (press starts motion, release stops it) — you move exactly as much as you
press. `0` (Stop) and `7` (Sync) are discrete one-shot commands. `5` (GoTo) only
works on a screen that has a selected object (Object Details); on plain menus and
status screens there is no target, so `5` is unused there. There is **no
step-size setting** (removed) and **no Init/Sync on `1`** (removed — the mount
auto-inits and syncs at startup, and a stray `1` would needlessly restart the
connection). `0` (Stop) also halts the GoTo/Guide **auto-correction** (tracking
guide) and clears its target so it will not immediately re-correct; a later `5`
(GoTo) or a new tracking start re-arms it. Continuous jog is also on the keyboard
letters (`q w e / a s d / z x c`, `s` = stop), and `+`/`-` set the slew rate
(speed).

## Page groups

| Group | Purpose | Screens |
| --- | --- | --- |
| **G1 Menus & Navigation** | menu trees, settings, equipment, obs/location lists | `UITextMenu`, `UIObsList`, `UILocationList`, `UIEquipment` |
| **G2 Objects** (selection + details) | find/select an object and act on it | `UIObjectList`, `UIObjectDetails` |
| **G3 Text / Name Search** | on-device text and catalog-name search | `UITextEntry` |
| **G4 Numeric / Coordinate Input** | digit/coordinate/value entry | `UIDateEntry`, `UILocationEntry`, `UIIndiBacklash` |
| **G5 INDI Manual Control** | dedicated manual mount driving | `UIIndiGuide`, `UIIndiInit`, `UIIndiMultiPointAlign` (ADJUST) |
| **G6 Alignment Wizards** | plate/daytime/polar alignment | `UIAlign`, `UIAlignDaytime`, `UIPolarAlign` |
| **G7 Info / Passive** | chart, preview, status, console, log | `UIChart`, `UIPreview`, `UIConsole`, `UILog`, `UIGPSStatus`, `UIGPSTimeSyncStatus`, `UIIndiStatus` |

**Object selection is its own group (G2), separated from generic navigation
(G1).** Finding a star is a distinct interaction (browse / jump / search / open
details / drive the mount) and gets its own rules.

---

## G1 — Menus & Navigation

Pure navigation. Preserves current behavior, including INDI jog as the default
for the number/letter keys.

| Key | Action |
| --- | --- |
| `UP` / `DOWN` | Move highlight |
| `RIGHT` | Select / enter submenu / set config value |
| `LEFT` | Back |
| `SQUARE` | Cycle display mode (usually a no-op on plain menus) |
| `0-9` | **Shared mount map** (2/4/6/8 move while held; 0=Stop, 7=Sync; 5/GoTo unused here — no selected object) when mount control is on; otherwise no-op |
| letters / `+` `-` | INDI directional jog / slew rate when mount control is on; otherwise no-op |

Menus keep working as today, except the number keys now use the shared mount map
(move-while-held cardinals + 0/5/7) instead of the 8-way directional guide.

## G2 — Objects (selection + details)

Object selection is separated from G1. Two screens, two rôles.

### Object list (selection)

| Key | Action |
| --- | --- |
| `UP` / `DOWN` | Scroll the list |
| `RIGHT` | Open Object Details for the highlighted object |
| `LEFT` | Back |
| `SQUARE` | Cycle list view LOCATE -> NAME -> INFO |
| `0-9` | **Catalog-sequence jump** (e.g. `4` `5` -> M45) — works from the keypad |
| letters (kbd) | **Start a name search** (hand off to G3, seeded with the letter) |

Reconciliation: the number/letter keys here mean *find an object*, not mount jog,
so the object-selection list overrides the INDI-jog default. This restores the
intended keypad jump (today it is shadowed) and gives English name search.

### Object details (view + mount)

| Key | Action |
| --- | --- |
| `UP` / `DOWN` | Previous / next object |
| `RIGHT` | Open the Log screen |
| `LEFT` | Back |
| `SQUARE` | Cycle view: DESC / LOCATE / POSS / SDSS / Contrast |
| `0-9` (keypad) | **Shared mount map**: 2/4/6/8 = move S/W/E/N **while held**, 5=**GoTo** this object, 7=**Sync**, 0=Stop (1/3/9 unused) |
| letters (kbd) | **Continuous INDI jog** (`q w e / a s d / z x c`, `s` = stop) |
| `+` / `-` | Mount on: slew rate; mount off: eyepiece FOV / description scroll |

Reconciliation: on the keypad the number keys run the **documented discrete
commands** (so GoTo/Sync/step are actually usable on the device — today they are
shadowed by jog). Continuous hold-to-move jog stays available through the
keyboard letters and the dedicated INDI Manual screen (G5). This keeps the mount
controllable from Object Details without breaking the alphabet jog.

> Optional enhancement (only if hold-jog on the keypad number keys is wanted):
> add a tap-vs-hold layer so a quick tap = discrete command and a long hold =
> continuous jog on the same digit. Not required for the keypad-first baseline.

## G3 — Text / Name Search

The "English input where needed" group. Keypad and keyboard both produce text;
neither drives the mount.

| Key | Action |
| --- | --- |
| `0-9` | Keypad text entry (T9 / multi-tap); literal digits in numeric-search mode |
| letters (kbd) | Direct English input |
| `SQUARE` | Toggle letter / symbol set |
| `+` / `-` | Insert space / delete character; `LNG_-` clears |
| `RIGHT` | Show search results |
| `LEFT` | Confirm / back |

## G4 — Numeric / Coordinate Input

| Key | Action |
| --- | --- |
| `0-9` | Digits into the current field (auto-advance) |
| `-` | Delete / previous field |
| `+` | Sign toggle (N/S, E/W) or next field |
| `RIGHT` / `LEFT` | Next-confirm / previous-cancel |
| letters (kbd) | Ignored |

`UIIndiBacklash` enters values here; its `+`/`-` axis-toggle collision is fixed
separately (see §Reconciliation).

## G5 — INDI Manual Control

The dedicated manual-driving screens. **These are the exception to the unified
number map**: `UIIndiGuide` and `UIIndiMultiPointAlign` (ADJUST) are purpose-built
jog controllers with a visual keypad overlay and keepalive motion, so they keep
their existing directional-jog scheme unchanged (implemented 2026-07-11):

| Key | Action |
| --- | --- |
| `2` `4` `6` `8` | Cardinal jog (press-hold; release stops) |
| `1` `3` `7` `9` | Diagonal jog |
| `0` / `5` | Screen-specific (e.g. toggle guide correction / refine) |
| letters (kbd) | INDI directional jog (`q w e / a s d / z x c`, `s` = stop) |
| `+` / `-` | Slew rate up / down |
| `SQUARE` | Sync / align to current solve |

`UIIndiInit` keeps its own discrete INDI panel (Init/Park/Home on digits) for
connection management. The shared mount map (§Number keys that drive the mount) is
applied to *incidental* mount access on Object Details, mount-on menus, and status
screens — not to these dedicated controllers, whose whole purpose is hands-on
jogging.

## G6 — Alignment Wizards

Keypad-driven, per-screen (kept as-is):

- **`UIAlign`**: `SQUARE` align-mode toggle + save; arrows move star pick; `+`/`-`
  zoom; `1` reset reticle, `0` cancel.
- **`UIAlignDaytime`**: `SQUARE` start/save; `7 9 1 3` quadrant pick; arrows fine
  nudge; `0` cancel; `+`/`-` exposure.
- **`UIPolarAlign`**: `SQUARE` advance; `-` cancel/back; `0` compute.

Letters ignored; numbers are wizard-specific.

## G7 — Info / Passive

- **`UIChart`**: `+`/`-` zoom, `SQUARE` reset FOV.
- **`UIPreview`**: `+`/`-` zoom, `SQUARE` focus overlay.
- **`UIConsole`**: `UP`/`DOWN` scroll.
- **`UILog`**: `RIGHT` acts on field, `UP`/`DOWN` change field, digits set rating.
- **Status screens** (`UIGPSStatus`, `UIGPSTimeSyncStatus`, `UIIndiStatus`): allow
  mount control (no text here) — number keys use the **shared mount map**
  (2/4/6/8 move while held; 0=Stop, 7=Sync; 5/GoTo unused — no selected object),
  letters do continuous jog — a convenient place to drive the mount while
  watching status.

## Per-group summary

| Group | Numbers `0-9` | Letters (kbd) | INDI jog |
| --- | --- | --- | --- |
| G1 Menus/Nav | shared mount map (mount on) / none | directional jog / none | default |
| G2 Object list | catalog-sequence jump | start name search | no |
| G2 Object details | shared mount map (hold-to-move + 0/5/7) | continuous jog | yes |
| G3 Text/Search | T9 / multi-tap text | English text input | no |
| G4 Numeric input | digits | ignored | no |
| G5 INDI Manual | directional jog + screen-specific (dedicated controllers) | directional jog | yes |
| G6 Alignment | wizard-specific | ignored | no |
| G7 Info/Passive | ratings / dev / none (status: shared mount map) | ignored (status: jog) | status only |

Incidental mount access (G1 menus mount-on, G2 Object Details, G7 status) uses the
shared mount map (2/4/6/8 move S/W/E/N while held, 0=Stop 7=Sync; 5=GoTo only on
Object Details where an object is selected; 1/3/9 unused); continuous directional
jog is also on the keyboard letters. The dedicated INDI jog controllers (G5) keep
their own directional-jog scheme.

## Reconciliation steps (small, additive — to implement)

These are scoped changes that blend with existing behavior; nothing is torn out
wholesale.

1. **Object list**: make `0-9` do catalog-sequence jump from the keypad (today it
   is shadowed by INDI jog), and make a letter open the G3 name search. Scope: the
   object-selection list only.
2. **Shared mount number keys** (done): for *incidental* mount access — Object
   Details, mount-on menus, status screens — the cardinal keys 2/4/6/8 move the
   mount **while held** (press = start, release = stop), and 0=Stop / 5=GoTo /
   7=Sync are discrete. Step-size (3/9) and Init/Sync (1) were **removed** — you
   move as much as you press, and the mount already inits/syncs at startup.
   Implemented as shared `_mount_key` / `_mount_key_press` / `_mount_key_release`
   on `UIModule`, used by `GuideKeyMixin` and `UIObjectDetails`. The dedicated
   INDI jog controllers (G5) keep their own directional-jog scheme.
3. **Keep continuous jog on the keyboard letters** (`q w e / a s d / z x c`) on the
   mount screens, and keep `+`/`-` as slew-rate — unchanged.
4. **Fix** `UIIndiBacklash` `+`/`-` (both currently toggle axis) into distinct
   actions.
5. **Optional**: tap-vs-hold on the number keys, only if hold-jog on the keypad is
   desired on G2 Object Details.
6. **Re-sync** `docs/mf_keyboard_mapping_*` and the user guide once implemented.

Open decisions for review: whether plain menus should keep INDI jog at all (or
require an explicit "mount jog" screen); the exact letter that triggers name
search on the object list; and whether to add the optional tap-vs-hold layer.
