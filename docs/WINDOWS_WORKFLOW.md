# Windows application workflow and its Linux equivalent

Status date: 2026-11-18. Sources: static analysis of the shipped
`investigation/extracted/app/hid.exe` (SHA-256 `7fae6f5b…de88d`) and
`ldcfg.exe`, the editor string tables under
`investigation/extracted/app/images/*/strings/`, saved PFD/MSMACRO/MSDB
fixtures, saved USB captures and the saved factory device-memory image. No
Windows binary was executed and no device was accessed.

Labels: **Confirmed** = directly visible in shipped code/data and, where
stated, corroborated by saved captures or readback. **Operator-reported** =
stated by the project owner. **Inference** = reasoning from confirmed facts.
**Unresolved** = not established.

## 1. New reverse-engineering results (this milestone)

### 1.1 `ButtonFunc` → device record table (Confirmed)

`hid.exe` `0x408480` searches a 52-entry, 6-byte table at `0x47D470`
(`ButtonFunc`, four record bytes, pad) and returns the device record template
used by the button compiler `0x412F10`. REA evidence
`ev_99a898ac5bf107635c6dfc1b9f6401ad8030c43c5b39431141ae5112cacd198f`
(bytes) and `ev_013e8c83a06e604f025767a86f9047be92f10a43ea673ffa7dc10c2169637a06`
(lookup routine). The table is reproduced in
`src/venus_profile/button_functions.py` (`FUNCTIONS`).

Cross-checks:

- Every template that is also a capture-confirmed record carries the same
  meaning (Left/Right/Middle `81/82/83`, Forward `85`, Backward `84`, DPI
  cycle/+/− `88/8A/89`, Profile cycle/+/− `8D/94/95`, Report Rate ± `97/98`,
  Disable `00` = `ButtonFunc 16`, Fire `99` = 12, Single key `90` = 6, Macro
  `91` = 100).
- The offline Apply preview built from the factory PFDs reproduces **all 100
  button records** in the saved factory memory image
  (`tests/test_apply_plan.py`).

Labels come from the editor string tables (`FUNCTION_STRING`, `BASIC`,
`ADVANCED`, `MEDIA`, `DPI`, `RATE`, `SCROLL`). The `0409` directory contains
Korean text because of the known extraction collision; the
`Italy`/`Spanish`/`French` files carry the English strings. Pairing labels
with codes is an **inference** from order plus template semantics; every
BASIC entry matches its shortcut (Cut = `8F 01 1B` = Ctrl+X … Save = Ctrl+S),
ADVANCED matches Alt+Tab, Alt+F4, Win+E/R/D/L and browser consumer codes, and
MEDIA matches standard consumer usages (`CD` Play/Pause, `B7` Stop, `B6`/`B5`
tracks, `E9`/`EA` volume, `E2` mute). Codes 52/53 are Scroll Up/Down: the
factory Buttons 19/20 (the wheel directions). Code 54 is Lighting Mode Cycle.
Code 13 (`30 00 00 00`) has no label and is never offered.

### 1.2 Single-key compiler and the "Single Key A" mislabel (Confirmed)

For `ButtonFunc 6`, `0x412F10` (`0x4131C1–0x4131F0`, `0x413380`) builds
`90 00 <runtime key byte> 00`. Saved factory memory holds thumb buttons
1–12 as usages `1E..27` (keys 1–0), `57` (Keypad +) and `56` (Keypad −), and
DayZ stores usages such as F, G, Q, E, Space and Left Ctrl. Record
`90 00 1E 00` is therefore the **"1" key**; it had been labelled
"Single Key A" (`90 00 04 00` is "A"). The label is corrected in
`button_assignments.py`; saved evidence that recorded the old label still
restores, because restore accepts it only with the exact raw record and ID.

### 1.3 What a PFD stores per function (Confirmed from fixtures)

| Function | Fields Windows writes |
|---|---|
| Single key (6) | blob byte 0 = HID usage, rest zero |
| Combo key (7) | blob = `(usage, 05, 80)` press triples then `(usage, 05, 00)` releases in reverse; `KeyNumber` = record count. Compiles to `8F <modifier bits> <key> 00` (Ctrl 01, Shift 02, Alt 04, Win 08) |
| Fire key (12) | blob byte 0 = trigger (`00`/`F0` left, `F1` right, `F2` middle, otherwise a key usage); `KeyNumber` = delay in ms. **The fire count is not stored in the PFD** (`01_Fire_Count4.pfd` differs from default only in trigger and delay) |
| Macro (100) | `KeyNumber` = device slot 0–14, `MacroName` = macro name |
| All other functions | only `ButtonFunc` changes; the previous blob and `KeyNumber` are left as stale data |

The Linux editor follows the same rule: it writes only the fields used by the
chosen function and preserves everything else.

### 1.4 Complete DPI lookup table (Confirmed)

The three lookup branches of `0x40FB00` index one table region
`0x47E3F4..0x47E5B8`. All values for raw steps 0–106 were read from the PE
file data and checked against the five REA-confirmed bytes
(`16 2D 5A 5C 7C`). They are stored in `apply_plan.py`.

## 2. Windows workflows mapped onto Linux

| # | Windows workflow | Evidence | Linux implementation |
|---|---|---|---|
| 1 | Startup loads `RSProfile1..5.pfd` into five runtime profiles | Confirmed (`0x40EEB0→0x40EA10→0x40BB10`) | `VenusWorkspace.open()` restores five profiles from the application store; the first launch seeds them from the shipped factory PFDs (`data/factory/`) and saves them |
| 2 | Profile tabs PROFILE1–5 select the edited profile | Confirmed (UI strings); runtime selection in memory | One global profile selector in the header; every page follows it. Unsaved slots show a dot |
| 3 | LOAD FILE / SAVE AS | Confirmed strings; dialog wiring generic (not joined to PFD code). User-observed (2026-11-18): Load File copies a named profile into the slot and editing never changes that file; Save As is the only way to write a named file (new name or chosen overwrite) | *Load File* loads any compatible PFD (numbered or legacy, e.g. DayZ) into the selected slot and never touches the source. *Save As* writes the slot as a portable PFD, defaulting to `~/Documents/VenusMouse/Profiles/`; an existing file is replaced only after the save dialog's (or the app's) overwrite confirmation |
| 4 | Profiles persist between sessions | Confirmed writer `0x40A1F0` rewrites the fixed `RSProfile%d.pfd` set; user-observed: edits are kept automatically (no Save button) and survive a restart | The five working profiles, macro library and bank are written to the application store automatically shortly after each change and on close; there is no Save button |
| 5 | RESTORE / RESET ALL / CANCEL | Confirmed strings and warnings ("Reset the current profile back to factory setting"); user-observed: CANCEL drops the edit and keeps the loaded profile (it also hides the Windows app to the tray) | *Restore* and *Reset All* load the shipped factory files into the slot(s); named files are untouched. *Cancel* returns the slot to the profile as it was loaded (no tray behaviour) |
| 6 | Button assignment: Mouse, Single key, Combo, Basic, Advanced, Media, Macro, Fire, DPI, Profile, Report rate, Scroll, Disable | Confirmed table + strings | Buttons page editor with every category; unknown functions are shown and preserved; the Windows rule "at least one click function must be retained" is enforced |
| 7 | DPI: five stages, enable, values, start-up stage | Confirmed formats and writers | DPI page bound to `DPIStageValue`/`DPI`; refuses to disable the start-up stage or the last enabled stage |
| 8 | Lighting: mode, colour grid, custom colour, brightness, breathing speed | Confirmed fixtures; flashing speed unmapped | Lighting page bound to `LedMode1`, `LedColor1`, `LedState1`, `BreathState1`; the 33-entry Windows palette plus a custom picker. `FlashState1` is preserved, not exposed |
| 9 | General: acceleration, pointer, scroll, double-click, polling | Confirmed fixtures | Settings page |
| 10 | Macro manager: record, insert delay, loop modes (fixed 1–255, while held, toggle), edit tools, load/save file, max 15 macros | Confirmed strings; `ldcfg.exe` reader | Macros page: unlimited saved library, editor with action tools, keyboard recording in this window, four loop modes (1–255), import of MSMACRO/MSDB files and Windows MacroDB folders, export back to a Windows MacroDB folder |
| 11 | Macro → button assignment by slot | Confirmed (`0x412F10`, `0x412850`) | Assign "Macro · Slot n" on the Buttons page; the bank keeps every profile's `KeyNumber`/`MacroName` consistent on replace, swap, rename, unload and delete, with explicit confirmation when buttons are affected |
| 12 | APPLY / OK writes everything to the mouse | Confirmed sequence `0x413430`, no acknowledgement checks | **Disabled.** *Apply Preview…* lists the writes, each with its evidence level; nothing is sent |
| 13 | Read current configuration from the device | Confirmed read paths | Existing user-run CLI `read_device_memory` only; the GUI performs no device access |
| 14 | Other | Tray icon (Config/Exit), language files | Not reproduced (no device daemon is needed while Apply is disabled) |

**Operator-reported:** custom PFDs can be loaded into any Windows profile
slot, and importing existing Windows profiles is required. Exports do not
need to be Windows-compatible; portable exports nevertheless remain valid
Windows-format PFDs.

## 3. Unresolved

- Which F1/F5 reports a minimal write needs; device acknowledgement and
  durability semantics.
- Lighting addresses for profiles 2–5; whether palette entries are
  translated by Windows before writing (only three samples exist).
- The fire count stored at runtime (`+0x116`) when a PFD is applied: PFDs do
  not carry it, so a Linux Apply would need a chosen default.
- Behaviour of macro slot replacement and zero-body writes on the device;
  regions 15–19 stay untouched.
- "Go without trace" and "Reactive" lighting modes appear in the string table
  but have no PFD mapping or capture evidence; they are not offered.
