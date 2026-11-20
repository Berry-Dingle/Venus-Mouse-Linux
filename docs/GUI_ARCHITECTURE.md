# GTK application architecture

The GUI is a GTK4/libadwaita application that edits the five Venus profiles
and the macro library on this computer. It performs no device discovery,
HID access, reading or writing. `gui/safety.py` proves this statically by
walking the whole import closure of `gui.application` (see §8).

Windows behaviour and the evidence behind each workflow are recorded in
`WINDOWS_WORKFLOW.md`. Steps required before enabling device Apply are in
`HARDWARE_VALIDATION.md`.

## 1. Layers

```
VenusWindow (header: profile selector · status · Save · main menu)
 ├─ ButtonsPage ─ AssignmentPanel ──┐
 ├─ DpiPage (ProfileDpiState) ──────┤
 ├─ LightingPage (ProfileLightingState)
 ├─ MacrosPage (MacroPresentationState)
 └─ SettingsPage ───────────────────┘
          │  all pages read/write through
          ▼
VenusWorkspace  (workspace.py)
 ├─ ProfileSetSession → five ProfileDocumentSession → PfdProfile (lossless INI)
 ├─ MacroLibrary (macro_library.py): unlimited library + 15-slot DeviceBank
 └─ AppStore (app_store.py): atomic, root-confined application files
```

Every page reads the selected profile on demand
(`workspace.profile()`). After an edit a page calls
`workspace.edited(reason)`. The workspace notifies all subscribers, so the
header status, profile pills and other pages stay in sync. Each page
refreshes its widgets under a `_syncing` guard, so programmatic refreshes
never create edits. Tests cover this for DPI, Lighting, Settings and Buttons.

## 2. Persistence and Save semantics

The application store lives at `$VENUS_PROFILE_DATA_DIR`, or
`$XDG_DATA_HOME/venus-profile` (default `~/.local/share/venus-profile`):

```
profiles/profile1.pfd … profile5.pfd   working profiles (Windows: RSProfile1..5.pfd)
macros/library/<id>.MSMACRO            library macros
macros/library.json                    library index (names, order, source)
macros/bank.json                       the 15 device-slot references
macros/deleted/                        archived copies of deleted macros
state.json                             selected slot, import origins
```

- **First launch** seeds the five profiles from the shipped factory PFDs
  (`venus_profile/data/factory/`) and saves them immediately.
- **Startup** restores the five profiles, library, bank and selected slot.
  A corrupt stored profile falls back to factory settings, and the user is
  told.
- **Save** (Ctrl+S) atomically replaces only application-owned files. It
  saves every unsaved profile plus the library and bank. `AppStore` refuses
  any path outside its root.
- **Dirty state** is per profile (byte comparison against the last saved
  bytes) and for the library/bank as a whole. The header status, pill dots,
  Save button and close guard all use it.
- **Close** with unsaved changes asks Save / Discard / Cancel.
- **Revert Profile** and **Discard All Unsaved Changes** return to the
  saved state.

## 3. Profiles

- **Import Profile…** loads any compatible PFD into the selected slot:
  numbered factory files, legacy unnumbered Windows files such as
  `DayZ.pfd`, or portable exports. Structural identities are remapped to the
  slot; `GroupID`/`Group` are kept as data. The import is an unsaved change
  of that slot, and the source file is never written. A dirty slot asks
  before being replaced. If the imported profile references macro slots
  whose names do not match the bank, the user is told how to import the
  Windows MacroDB folder.
- **Export Profile…** offers *Portable* (`[GROUP]`, importable into any
  slot; the default) or *Slot-specific* (`[GROUPn]`). Exports are
  no-clobber copies and never change the working session.
- **Restore Factory Profile…** and **Reset All Profiles…** load the shipped
  factory files as unsaved changes.

## 4. Buttons

`button_functions.py` holds the complete `ButtonFunc` catalogue (from the
`hid.exe` table at `0x47D470`) and lossless setters. The editor offers every
Windows category, and setters change only the fields the chosen function
uses:

- Mouse, single key (captured from a key press or typed by name), key
  combination (Ctrl/Shift/Alt/Super plus one key);
- editing shortcuts, system/browser, media;
- DPI, profile, report rate, scroll/lighting cycle;
- macro (bank slot), fire key (trigger and delay), disable.

Unknown functions are shown as "Unknown function N (preserved)" and are
never rewritten unless the user assigns something else. Assignments are
validated on a copy first; removing the last Left Click is refused (Windows
rule). *Revert Button* restores one button's saved fields. Callout and
side-grid tooltips show the current assignment. The physical registry
(Top 1–6, Side 1–12 → protocol buttons 1–18) is unchanged; Buttons 19/20
(wheel up/down) keep their stored functions.

## 5. DPI, Lighting, Settings

- **DPI**: five stages bound to `DPIStageValue`. Each stage has an Enabled
  toggle and a slider over the 106 legal sensor steps (200–16,400 using the
  official increments). Clicking a stage header makes it the start-up stage
  (`DPI`). The start-up stage and the last enabled stage cannot be disabled.
  Asymmetric or unknown stored values are displayed and preserved.
- **Lighting**: mode (Full Brightness, Breathing, Wave, Flash, Rainbow, Off),
  brightness (Off–High), breathing speed (Slow/Medium/Fast), the 33-entry
  Windows palette and a custom colour picker or `#RRGGBB` entry stored as a
  correct COLORREF. Colour controls are disabled for Rainbow and Off.
  `FlashState1` is preserved, not exposed (no controlled evidence).
- **Settings**: pointer speed 1–20, acceleration 1–4, scroll lines 1–63,
  double-click speed 1–10 and polling rate 125/250/500/1000 Hz. A *Mouse
  Connection* card states that device writing is disabled and opens the
  read-only *Apply Preview*.

## 6. Macros

- **Library**: unlimited macros saved under the store. New, Duplicate,
  Rename (validated, unique, Windows-safe names), Delete (confirmed;
  archived on Save), *Revert Macro*, and import of MSMACRO/MSDB files or a
  whole Windows MacroDB folder, whose `MacroSet.MSDB` fills empty slots.
  All macros stay listed; loaded ones show their slot badge, and unsaved
  ones show a dot.
- **Editor**: action list with drag reordering, inline value edits, add,
  delete, move, copy/cut/paste, the four Windows loop modes (fixed count
  1–255, while held, once, toggle) and *Record*, which captures key presses
  typed into this window, optionally with measured delays.
- **Device slots**: exactly 15. Drag a library macro onto a slot to load
  it, drag a slot onto another to swap, or onto the library to unload. A
  macro occupies at most one slot. Buttons reference slots, so:
  - Replace updates every referencing button's `MacroName`.
  - Swap renumbers buttons so they keep their macro.
  - Rename propagates to every profile.
  - Unload or delete sets referencing buttons to Disable.

  Each operation that changes button references asks for confirmation and
  lists the affected buttons first.
- **Export Macros for Windows…** writes the bank as a new MacroDB folder.
- Device regions 15–19 are never represented.

## 7. Apply boundary

`apply_plan.py` builds an immutable, non-executable preview of the writes a
full Apply would need, each tagged `verified`, `static-only`, `inferred` or
`unresolved`. It reproduces all 100 factory button records, the profile-1
DPI records, polling and profile-1 lighting from the saved factory memory
image. It has no transport and grants no permission. Live operations remain
limited to the separately guarded CLI tools.

## 8. Safety audit

`gui/safety.py` parses, without importing, every module reachable from
`venus_profile.gui.application`, including `from package import module`
forms. It fails if the closure contains:

- `readonly_hidraw`, `device_session`, `read_device_memory`,
  `visual_colour_cycle`, `device_diagnostics`, `verify_udev_access` or any
  `guarded_*` module;
- `fcntl`, `ctypes`, `socket`, `subprocess` or a USB/HID library import.

It also fails if a GUI module uses dynamic import or evaluation, or names a
device path.

## 9. Launch and tests

```bash
PYTHONPATH=src python -m venus_profile.gui
```

Fedora runtime requirements: GTK ≥ 4.12, libadwaita ≥ 1.5 (AlertDialog),
PyGObject.

Run GUI tests under a virtual display so none are skipped:

```bash
env -u WAYLAND_DISPLAY xvfb-run -a -s "-screen 0 1600x1200x24" python3 -m pytest -q
```

Tests always use a temporary store (`tests/conftest.py`), never the user's
real data.

## 10. Fixed desktop window

The window is a 1100×780, non-resizable utility. Navigation icons are
verified Adwaita symbolic names. Artwork, callout and hotspot geometry rules
are unchanged from the approved Buttons milestone: normalized hotspots,
`TextureViewport` painted-rectangle placement, and callouts as focusable
buttons.
