# Design: reading the mouse from the GUI (proposal, not implemented)

Status: **approved and implemented** (2026-11-19). Decisions: helper process
and both audit changes approved; macro slots with no library match are shown
("On mouse only"), not added to the library; the mouse's current DPI stage
becomes the profile's start-up stage; AppImage/source users get a copy-paste
udev block (the app never runs privileged commands).

Implementation: `read_snapshot.py` (helper, CLI `venus-profile-read-snapshot`),
`snapshot_audit.py` (helper audit), `data/read_plan/factory_read_plan.json`,
`device_decode.py`, `workspace.apply_device_read`, `gui/device_read.py`,
`gui/safety.py` (launch allowance), the window's read flow and banner, and the
Settings "Set up mouse access…" block. Set `VENUS_PROFILE_NO_DEVICE_READ=1` to
start without reading the mouse.

Goal: when the app starts it shows "Reading from mouse…", reads the mouse, and
the mouse's settings become the working set (the mouse wins), as the Windows
utility does. Writing to the mouse stays disabled; this design adds **reading
only**.

Evidence basis: `investigation/READ_COVERAGE_AUDIT.md`.

## 1. Current rules this must respect

- The GUI's static audit (`gui/safety.py`) proves that no module reachable from
  the GUI can talk to the device: it forbids `readonly_hidraw`,
  `device_session`, `read_device_memory`, `guarded_*`, and
  `fcntl/ctypes/socket/subprocess/usb/hid` imports.
- The only live device path is `DeviceSession` + `ReadOnlyHidrawTransport`:
  exact identity (04D9:FC55, interface 02, usage page FFA0/1), F2 read requests
  only, no write API, response validation, and the captured 193-read plan.
- Project rules: no generic sender, no weakened guards, dry-run/audit modes
  never touch the device, live operations are run by the user.

## 2. Proposed architecture: a separate read-only helper process

```
GUI process  (never opens a device)
  │  launches, with a fixed argument list, no user input:
  ▼
venus-profile-read-snapshot  (helper process, read-only)
  - DeviceSession.discover() → identity checks (unchanged)
  - read_full_known_plan()   → the fixed 193 F2 reads (unchanged)
  - prints one JSON document to stdout and exits
  │
  ▼
GUI: device_decode (pure, offline) → working set → autosave
```

Why a helper process rather than letting the GUI import the read session:

- The GUI process still never opens a device node; the existing audit keeps
  proving that, with one narrow, explicit exception (§4).
- The helper has its own audit: its import closure may contain the read
  transport but **no** `guarded_*` writer, F3/F1/F5 builder or generic sender,
  so a write is impossible in either process.
- A hung or crashing read cannot freeze the GUI; the GUI applies a timeout.
- It is the same read the user already runs from the command line
  (`read_device_memory --live-read`), packaged for the GUI.

Alternative (not recommended): allow the GUI to import a thin wrapper around
`DeviceSession`. Simpler, but it puts `fcntl`/hidraw code in the GUI process
and requires carving holes in every audit rule above.

## 3. Components

### 3.1 Helper: `venus_profile.read_snapshot` (new, CLI `venus-profile-read-snapshot`)

- No options. Runs discovery and the fixed 193-read plan, nothing else.
- Output on stdout, one JSON object, versioned:
  `{"version":1, "status":"ok", "device":{...}, "memory":[{"address":…,"hex":…}, …]}`.
  Error statuses: `no-device`, `multiple-devices`, `permission-denied`,
  `identity-mismatch`, `read-failed` (with a plain message).
- Writes no files (unlike the CLI tool's evidence directories) unless a later
  option is approved; it does not use the known-state overlay.
- Exit within a bounded time; any exception → an error status, never a partial
  "ok".
- The read plan is loaded from **package data** (a copy of the 193 requests
  from `usb-captures/analysis/factory_transactions.json`, verified identical by
  a test), because an installed app cannot reach the repository's capture
  files.

### 3.2 Decoder: `venus_profile.device_decode` (new, pure, no I/O)

Turns a memory image into semantic values per profile, each tagged with its
evidence level, using only mappings confirmed in the audit:

| Setting | Source | Decode |
|---|---|---|
| Lighting P1–P5 | `0x0449 + 8×(p−1)` (6 bytes) + brightness at +6 | RGB → `LedColor1`; mode triplet → `LedMode1` (inverse of `PFD_MODE_TO_DEVICE_PREFIX`); companion → `BreathState1` when Breathing; brightness → `LedState1` |
| DPI stages | `base + 4 + 6×stage` | (lookup, selector) → raw step (unique inverse of `dpi_lookup`); enable byte |
| DPI stage | `base + 2` | zero-based stage → `DPI` |
| Polling | `0x0032/0x0038` | enum `8/4/2/1` |
| Buttons | 100 records | confirmed record families → `ButtonFunc`/`KeyNumber`/blob (reverse of `derive_device_record`); unknown records are kept raw and flagged |
| Macro slots | `0x0478 + slot×0xC8` | action records (bodies only) |

Not read (the working file keeps its values): scroll speed (`0x0020` is
outside the read plan), active profile (unknown), PC-only pointer settings,
macro names, and the unexplained fields `0x002C/0x003E/0x0446`. Adding
`0x0020` or an active-profile byte to the plan is a separate, evidence-backed
change later.

### 3.3 GUI: start-up read flow

1. Window opens with a "Reading from mouse…" overlay (spinner, Cancel).
2. Launch the helper with `Gio.Subprocess` (fixed argv), 10 s timeout.
3. Outcome:
   - **ok** → decode → apply every decodable value to the five working slots
     → autosave → toast "Settings read from the mouse". Values that could not
     be decoded keep the saved value and are listed in a notice.
   - **no-device** → open with the saved working set; banner "No mouse found —
     showing saved settings" with a "Read again" button.
   - **permission-denied** → same, with a short explanation and the udev-rule
     instructions.
   - **error/timeout/cancel** → same as no-device, with the message.
4. A menu item **Read from Mouse** repeats the read on demand (asks first if it
   would replace edits made since the last read).

Macros: a slot body that matches a library macro is linked to it by name; a
body with no match becomes a new library macro named "Mouse slot N" (saved to
the Macros folder). Button records that reference slots then resolve to those
names.

## 4. Safety rule changes (the part needing approval)

1. **GUI audit:** still forbids every device module and low-level I/O import.
   New narrow allowance: exactly one call site may launch a subprocess, via
   `Gio.Subprocess`, with a constant argument list naming the helper; the audit
   checks the call site and that the argument list is a literal.
2. **New helper audit:** the helper's import closure must contain
   `readonly_hidraw`/`device_session` and must **not** contain any
   `guarded_*` module, write/report builders, `subprocess`, `socket` or
   `ctypes`; it must expose no command-line options.
3. **Unchanged:** identity checks, F2-only validation, the fixed read plan,
   response validation, and the rule that writing stays disabled.

## 5. Testing (offline; live testing is the user's)

- Decoder against the saved factory read: decoded values equal the factory
  PFDs (lighting, DPI, buttons, polling) — the same data the Apply-preview test
  already matches.
- Decoder against all 71 saved Linux reads: every known change (lighting,
  stage, button 4/7 records) decodes to its documented value.
- Helper with a fake transport: every status path; no partial "ok".
- GUI with a fake helper: overlay, apply, all error banners, cancel, timeout,
  "mouse wins" autosave, macro matching.
- Both audits, plus a test that the packaged read plan equals the capture.
- Live (user-run, after implementation): one start-up read with the mouse
  connected; compare the app's values with what the Windows utility shows.

## 6. Decisions requested

1. Approve the helper-process architecture (§2) and the two audit changes (§4).
2. Macro slots with no library match: create "Mouse slot N" library macros
   (proposed), or show them without adding to the library?
3. DPI stage: `0x0042` is the mouse's *current* stage (it changes with DPI±).
   Proposed: it sets the profile's start-up stage, as Windows Apply writes it
   back there.
4. Permissions: replace the group-based udev rule with a `TAG+="uaccess"` rule
   so the logged-in user can read without joining a group (separate step).

## 7. Live result (user-reported, 2026-11-19)

- `read_snapshot` first returned `permission-denied`; after
  `sudo tools/install_udev_rule.sh --install --replace` and a replug it returned
  `ok`.
- The app read the mouse at start-up and displayed the mouse's current
  settings correctly (user comparison).
- After pressing DPI+ on the mouse, **Read from Mouse** advanced the DPI stage
  by one in the app, confirming `0x0042` tracks the live stage.
- User observations for future work (not implemented): the Windows utility
  appears to update live when DPI± is pressed; and changing the colour in the
  Windows utility changes the mouse's lighting temporarily without saving it
  (consistent with the `live_preview` phase in the saved captures: F3 lighting
  writes with no finalising F1; Apply adds `F1 02 04`, which persisted in the
  Linux guarded writes).
  *Correction (2026-11-19):* re-checking the saved captures shows every
  `live_preview` cycle **does** end with `F1 02 04` (e.g. red→blue frame 8228,
  blue→green frames 2942/2966, breathing→full frame 5917). What Apply adds is
  the `F5 00`…`F5 01` bracket and the full 146-block plan. See
  `docs/LIVE_LIGHTING_PREVIEW_DESIGN.md` §2.
