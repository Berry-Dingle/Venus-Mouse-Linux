# Hardware tests toward full Apply (2026-11-19)

Goal: enable the app's **Apply** for DPI, polling, buttons, macros and scroll
speed, one category at a time, each only after a user-run live test passes.
Lighting and the active profile are already live (`LIVE_LIGHTING_PREVIEW_DESIGN.md`).

All tests are run by the user. Before each test that writes: close the app,
make sure a recent verified backup exists, and stop at
the first unexpected result.

## 1. What Windows sends, per setting (static, `hid.exe` message map)

`ldcfg.exe` (config window) posts a window message to `hid.exe` (device
process); each message has its own handler (`investigation/LIVE_EVENT_FINDINGS.md`
§10.1 for the map).

| Setting | Message → handler | Device reports | Finaliser | Status |
|---|---|---|---|---|
| Lighting | `0x4E3` → `0x410940` | lighting F3s | `F1 02 04` | **live, confirmed** |
| Active profile | `0x4E2` → `0x410cf0` | F3 `0x002C` | `F1 02 01, 04, 02, 08` | **live, confirmed** |
| Polling | `0x4DF` → `0x410230(1)` | F3 `0x0032` (6), F3 `0x0038` (4) | `F1 02 08` | static only |
| Buttons | `0x1516` → `0x4142f0` → `0x412f10` | 4-byte record F3s | **none** | Linux F3-only write read back (July); persistence unknown |
| Macros | `0x1514` → `0x4142b0` | `F5 00`, Report-4 upload (`0x412b50`), `F5 01` | — | slot 1 captured |
| DPI stage values | Apply only: `0x40fb00(stage, f)` | 5-byte F3 at `base+4+6·stage`, all 5 profiles | `F1 02 02` if f≠0; Apply uses f=0 then one `F1 02 10` | static + Apply capture |
| DPI start-up stage | Apply only: `0x40fc60(0, 1)` | 2-byte F3 at `base+2`, all 5 profiles | `F1 02 02` + `F1 02 10` | static + Apply capture |
| Scroll lines | Apply only (`0x410070`) | **Report 3** F3 `0x0020` (10) | (Apply tail) | Apply capture; never read |

Full Apply (`0x413430`) = `F5 00`, all of the above in order, tail
`F1 02 04, 02 01, 02 02, 02 08`, `F5 01` (`REA_FINDINGS.md`).

## 2. Tests, in order

### T1 — Do F1-less button writes persist? (no new code)

Windows' own button update sends no F1, like the July Linux write, which was
restored 3 minutes later without a replug, so persistence is still unknown.
Uses the existing guarded tool (`GUARDED_BUTTON_ASSIGNMENT_WRITE.md`):
profile 1, button 7, allow-listed records only, typed `CONTINUE`, read-back.

1. Close the app. From `linux-suite-prototype/`:
   ```bash
   PYTHONPATH=src python3 -m venus_profile.guarded_button_assignment \
     --write-assignment --profile 1 --button 7 \
     --assignment-id record-00000000 --acknowledge-device-write
   ```
   It shows the current record first. If it is already `00 00 00 00`, abort
   and use `--assignment-id record-90000400` instead. Type `CONTINUE`.
   Note the evidence directory it prints.
2. Unplug and replug the mouse (or power-cycle).
3. Open the app: on the Buttons page, profile 1's button 7 should show
   **Disabled**; pressing that button should do nothing.
4. Close the app and restore:
   ```bash
   PYTHONPATH=src python3 -m venus_profile.guarded_button_assignment \
     --restore-from-evidence <evidence directory from step 1> \
     --acknowledge-device-write
   ```
5. Open the app: button 7 is back to its original assignment.

Answers: whether button records need any finaliser (decides the button part
of Apply).

### T2 — Read the bytes the read plan skips (new read-only probe)

`venus_profile.read_probe` reads exactly `0x0020` (10), `0x002C` (2), `0x003E`
(2) and `0x0446` (4), F2-only, each until two replies agree; no option takes an
address. Audit: `snapshot_audit.read_probe_safety_audit()`.

```bash
PYTHONPATH=src python3 -m venus_profile.read_probe              # offline: prints the plan
PYTHONPATH=src python3 -m venus_profile.read_probe --live-read  # type READ
```

Paste the output. Answers: scroll lines are readable and their current
values; `0x002D`, `0x003E`, `0x0446–0x0448` values (and whether `0x0448` is the
`80` seen in the shifted lighting reply).

### T3 — Polling (approved; implemented live; **live-confirmed**)

Proposed, as `hid.exe` 0x410230: stable pre-read of `0x0032–0x003B` (read
plan), F3 `0x0032` `[p1 00 p2 00 p3 00]`, F3 `0x0038` `[p4 00 p5 00]`, `F1 02 08`,
read-back. Values only from the evidenced enum `8/4/2/1` (125/250/500/1000 Hz).
Live test: change profile 1's polling in the app, Apply, read back, replug,
read. Optional physical check with a Linux event-rate tool.

### T4 — DPI stage values (approved; implemented live; **live-confirmed**)

Proposed: 5-byte stage record F3 (enable, lookup, selector, 00, 00 from the
existing `apply_plan.dpi_lookup` table) for the changed records, then
`F1 02 10` (Apply's choice) — or `F1 02 02` if T4a shows it is needed.
Live test: profile 1 stage 1 DPI change; cursor speed should change; read
back, replug, read.

### T5 — DPI start-up stage (approved 2026-11-19; implemented on Apply; **live-confirmed 2026-11-20**)

`base+2` 2-byte F3 + `F1 02 02` + `F1 02 10`. Note this byte also changes when
DPI± is pressed (memory lags ~20–30 s); the app already writes the read value
back to the working set.

### T6 — Scroll lines (approved; implemented on Apply; **live-confirmed**)

Report-3 F3 at `0x0020`, 10 bytes. New report type for writes; design after
T2 shows the current bytes.

### T7 — Macros (needs approval)

`F5 00`, one Report-4 upload to an **empty** slot (slot 2, `0x0540`), `F5 01`,
read back the 200-byte body, replug. Then replace a populated slot. Never touch
regions 15–19 (`0x1030–0x1417`).

### T8 — Enable Apply

Proposed architecture: the same one-shot helper gains one fixed transaction
per category; the Apply button sends only categories that differ from the
mouse and have passed their test; lighting and profile stay live. Full Windows
Apply (`F5` bracket + everything) is not needed if per-category sequences
persist.

## 3. Results

### T2 — probe (2026-11-19, user-run; `usb-captures/probe-reads/probe_20261119T123028.146793Z`)

| Address | Bytes | Reading |
|---|---|---|
| `0x0020` (10, Report-3 read) | `01 00 01 00 01 00 01 00 01 00` | scroll lines readable; `01 00` for all five profiles = what every captured Apply writes |
| `0x002C` (2) | `02 00` | active profile 3 (matched the mouse); **`0x002D = 00`**, as the profile switch writes it |
| `0x003E` (2) | `00 00` | as Apply writes |
| `0x0446` (4) | `00 00 80 ee` | `0x0446–0x0447 = 00 00` as Apply writes; **`0x0448 = 80`**; `0x0449 = ee` (P1 red) |

- **Confirmed:** the stray leading `80` in the intermittent "not a known mode"
  reply (`LIVE_LIGHTING_PREVIEW_DESIGN.md` §10.1) is the real byte at `0x0448`,
  i.e. the mouse occasionally answered a read of `0x0449` with data from
  `0x0448`. The two-agreeing-replies fix addresses exactly this.
- `0x0448`'s meaning is unknown (not written by any captured Apply); preserve.
- Scroll-lines meaning (`01` = ? lines) still needs a change made in Windows or
  a guarded write (T6).

### T1 — button persistence without F1 (2026-11-19, user-run)

- Profile 1 button 7 could not be used: it holds macro record `91 00 01 00`,
  outside the tool's allow-list; the tool refused before writing (no evidence
  directory created). A full read (`usb-captures/live-reads/venus_read_20261119T123427.513785Z`)
  showed the only allow-listed profile 1 buttons are B1–B3 (clicks), B5 DPI+,
  B6 DPI−.
- Write: profile 1 button 5 (`0x0092`) `8A 00 00 00` (DPI+) → `89 00 00 00`
  (DPI−), one F3, no F1/F5, read back
  (`usb-captures/live-button-assignment-writes/button_assignment_p1_b5_20261119T123724.215242Z`).
- **After unplug/replug (user):** the app's start-up read shows button 5 = DPI−,
  and pressing it no longer raises the DPI (already at the lowest stage, so a
  decrease could not be seen). **Confirmed: a single F3 button-record write
  persists across reconnect without any F1**, matching Windows' button path
  (`hid.exe` 0x412f10 sends no F1).
- Restore: pending (user).
- Restore (user): done; DPI+ works again.

### T3 — polling (implemented 2026-11-19, offline-tested)

Built **live, like lighting** (the Apply-vs-live choice is left to T8):
changing the rate on the Settings page sends `{"version": 1, "polling": [p1..p5]}`
to the one-shot helper (`write_polling`): stable reads of `0x0032`/5 and
`0x0038`/3 (refused unless all values are 8/4/2/1 and read companions are
00) → F3 `0x0032` `[p1 00 p2 00 p3 00]` → F3 `0x0038` `[p4 00 p5 00]` →
`F1 02 08` (10 ms after each) → stable read-back. `0x0037` and `0x003B` are
written as `00` exactly as `hid.exe` does (never read). Order in the GUI:
profile switch, then polling, then lighting. Settings-page texts, the Apply
tooltip and the About text updated (visual check on Xvfb).
- **Live-confirmed by the user (2026-11-19):** profile 1 set to 1000 Hz
  (`venus-mouse: polling -> ok (values 1 2 2 2 2)`); after unplug/replug the
  start-up read showed 1000 Hz; set back to 500 Hz (`ok (values 2 2 2 2 2)`).

### T4 — DPI stage values (implemented 2026-11-19, offline-tested)

Live, like polling. Request `{"version": 1, "dpi": [{"profile", "stage", "raw"}]}`
→ helper `write_dpi`: stable read of each involved profile's read-plan block
(base+2, 32 bytes) → refused unless every requested stage holds a known
**enabled** record → one F3 per changed record `01 lookup selector 00 00`
(lookup table copied from `apply_plan`, tested identical) → `F1 02 10` →
`F1 02 02` → stable read-back of the whole block (other stages and the
start-up stage byte must be unchanged). Not handled: disabling/enabling a
stage, X≠Y stages, the start-up stage byte (T5). GUI order: profile, polling,
DPI, lighting. Settings texts, tooltip and About updated (Xvfb check).
- **Live-confirmed by the user (2026-11-19):** 23 successive profile 1 stage 1
  changes, raw 1–106 (`venus-mouse: dpi -> ok` each time); cursor speed changed
  immediately; the value survived a replug. `F1 02 10` + `F1 02 02` is
  sufficient.

## 4. Live vs Apply (user decisions, 2026-11-19)

Matches Windows (`ldcfg.exe` only ever posts `0x4E3` lighting preview, `0x4E2`
profile tab, `0x4DE` Apply, `0x1514` macro upload; the polling-only and
buttons-only handlers exist in `hid.exe` but are never triggered by the UI):

- **Live:** lighting; active profile (1–5 buttons switch the mouse).
- **On Apply:** DPI values, polling, buttons (and scroll speed later). Apply
  sends every pending change for **all five slots**; edits stay pending when
  switching slots; Apply is enabled only when something differs.
- **Macros (Stage 3):** library → device-slot drag uploads live (Windows also
  uploads macros immediately); button assignments to slots go through Apply.
- No "send live" switch; no "Copy to profile".
- Stage 2: DPI **Defaults** button (selected profile → 1000/2000/4000/8200/16400,
  all stages on, pending until Apply); close overlay "You have unsaved changes,
  do you want to apply or save them now? [Apply] [Save] [Cancel]" (Cancel keeps
  the app open). No start-up handling (only a crash could leave unapplied
  edits; the mouse then wins).

### Stage 1 (implemented 2026-11-19, offline-tested)

- Polling and DPI moved from live to Apply; Apply enabled (`win.apply-to-mouse`).
- Buttons added as an Apply category: helper `write_buttons` (T1-proven: one
  4-byte F3 per changed record, no finaliser); records must match a shape the
  decoder recognises (`button_record_is_known`, named set kept identical to
  `device_decode`), at the 100 fixed addresses; pre-read guard + read-back.
- Comparison is semantic: a fire key with the same trigger and delay is not
  rewritten (the mouse keeps its own count, which profile files cannot store);
  a macro button pointing at the same slot is not rewritten (loop flags belong
  to the macro, Stage 3); macro assignments to slots without a library macro
  are not sent.
- Checked against the real read `venus_read_20261119T123427.513785Z`: no
  notices, 25/25 DPI stages and 100/100 buttons recognised, **nothing pending**
  after a read.
- Apply order: (live) profile, lighting; then polling, DPI, buttons; toast
  "Applied to the mouse". Settings texts, Apply tooltip, About updated (Xvfb).
- **User re-test (2026-11-19): Stage 1 all worked.** Feedback: the profile
  buttons kept their amber "edited" marker after Apply.

### Stage 2 (implemented 2026-11-19, offline-tested + Xvfb)

- **Apply commits:** after a successful Apply every slot's Cancel baseline
  becomes its current state (`VenusWorkspace.mark_applied`), so the amber
  markers clear and Cancel returns to what was applied (as Windows). Apply is
  enabled when something would be sent **or** any slot shows edits; with only
  lighting edits (already live) it commits without writing.
- **DPI Defaults button** (DPI page, bottom right): selected profile's five
  stages → 1000/2000/4000/8200/16400, all enabled, start-up stage kept;
  pending until Apply.
- **Close overlay** (only when DPI/polling/button changes differ from the
  mouse): "Unsaved changes — You have unsaved changes, do you want to apply or
  save them now?" [Apply] sends then closes (a failure keeps the app open with
  the banner); [Save] opens Save As for the selected profile, closes once saved
  (cancelling the file dialog keeps the app open); [Cancel] keeps the app open.
- **User test (2026-11-19): Stage 2 items 1–4 work.** Feedback: a lighting
  change enabled Apply (lighting is live, so nothing is pending); the Defaults
  button was smaller than the bottom-bar buttons.
- Fixes: while the mouse is live, lighting edits are committed into the slot's
  Cancel baseline at once (`VenusWorkspace.commit_lighting`, lighting fields
  only), so they never mark the slot as edited or enable Apply, and Cancel no
  longer reverts lighting that is already on the mouse; other edits in the slot
  keep their marker. Before a read, lighting is an ordinary edit. The Defaults
  button uses the `profile-action-button` class (measured on Xvfb: 78×28, same
  as Restore/Apply).

### Stage 3 — macro slots, live (implemented 2026-11-19, offline-tested)

Evidence: captured Windows macro save (`usb-captures/analysis/macro_slot1_a_press_release`,
frame 4260): `02 F5 00`, one 256-byte Report 4 `04 F3 78 04 C8 00 00 00` +
200-byte slot body + 48 zero bytes, `02 F5 01`; no F1. `hid.exe` 0x4142b0:
F5 00 (+100 ms), `0x412b50` uploads **one** slot (`0x478 + slot*0xC8`), F5 01
(+100 ms). Factory empty slot = 200 zero bytes.

- Helper `write_macros`: stable read of each slot (read plan 4 x 50 bytes) →
  refused unless the current body is a recognised action list → per changed
  slot `F5 00`, 100 ms, Report 4, 10 ms, `F5 01`, 100 ms → stable read-back.
  Bodies must match `macro_body_is_known` (keyboard 84/04 + usage, mouse 81/01
  + mask 1/2/4, delay 06, zero padding after the actions, 11 trailing zeros).
  The single write method now sends 16- or 256-byte reports (length from the
  validated report). Audit: F5 00/F5 01 only inside `write_macros`, slot
  addresses end at `0x1030`.
- GUI: live category. Desired body per slot: the assigned library macro's
  compiled body; a macro only on the mouse is left alone; empty = 200 zero
  bytes. Putting a macro in a slot, removing, swapping or editing an assigned
  macro uploads the changed slots.
- Real-data check (`venus_read_20261119T123427.513785Z`): 14 slots recognised,
  **slot 8 holds `ff ff 00 …` (unknown record) and is never written**;
  nothing pending after a read.
- Known limitation: moving/swapping macros between slots is live, but the
  buttons that point at those slots are re-pointed as Apply changes, so until
  Apply a button may play the other macro (decision pending).
- Settings status line split into two lines (one long line shifted the column
  split; Xvfb check).
- **User test (2026-11-19):** "Test Macro" dragged to slot 9 uploaded fine.
  Assigning it to side button 9 failed: choosing the Macro category applied
  the dropdown's default Slot 1 at once; slot 1 holds a macro that is only on
  the mouse, which the panel treated as empty, so it raised "Device slot 1 is
  empty…" and reverted the button to its keyboard key.
- **Not a device rule:** button macro records carry the slot directly
  (`91 <flags|slot> …`); the user's mouse already has buttons on slots 1, 5 and
  7 (P1 B7 → 1, P1 B18 → 5, P2 B7/B18 → 7). Slot 7 is empty on the mouse, hence
  "2 uses" on an empty slot (those two buttons play nothing).
- Fixes (Buttons page, `gui/assignment/panel.py`): slot list starts with a
  "Choose a slot…" entry; choosing Macro waits for a slot (any slot, including
  Slot 1); slots show the library macro's name, "On mouse" or "Empty"; macros
  only on the mouse can be assigned (Apply sends them as play-once
  `91 <slot> 01 00`, `apply_plan`); an empty slot prompts instead of reverting.
  Xvfb check of the panel.
- **User re-test (2026-11-19):** assigning slot 9 to a side button worked;
  editing the macro (g → p) re-uploaded live and played the new key; a
  `P down, 6000 ms, P up` macro played as a 6-second key hold. Observed output
  ("p", Plasma accent popup, "p" in KDE apps; `pppp…` auto-repeat in Konsole)
  is the desktop's normal held-key handling, not a device or app issue.
  Still to report: slot 9 + button after a replug; clean-up.
- **User (2026-11-19): replug check passed** (slot 9 macro and the side button
  survived). Decision: **option (b)**.
- Option (b) implemented: slot operations that re-point buttons (moving a
  macro to another slot, swapping slots, removing a slot macro → Disable)
  record the affected buttons (`VenusWorkspace.slot_rewritten_buttons`); while
  the mouse is live the window sends exactly those button records straight
  after the slot uploads (same `buttons` helper transaction), then commits
  them into the Cancel baseline (`commit_buttons`), so no Apply or edited
  marker is left. Other button edits still wait for Apply. Before a read,
  re-pointed buttons are ordinary pending edits.
- **User test of option (b) (2026-11-19): failed.** Side button 9 played
  Test Macro ("p") from a slot; after swapping it with Test Macro ii ("b") the
  button played "b". The swap confirmation popup was called annoying.
- End-to-end reproduction against the real helper code on simulated memory
  (no device): after the swap the helper uploads the swapped slot and then
  writes the button `91 09 01 00` (new slot), read-back ok — i.e. the app
  sends the right records. Open: either the live button write did not run in
  the user's session, or the mouse keeps playing the previous assignment until
  some refresh (no evidence either way; Windows never swaps slots live).
  Diagnostic run requested (terminal `venus-mouse:` lines; then profile
  switch away/back; then replug).
- Swap confirmation removed (`MacroPresentationState.swap_slots` swaps at once;
  buttons follow their macros). Diagnostics now also print
  `venus-mouse: slot change re-points P…B…`.
- **Diagnostic re-run (user, 2026-11-19): option (b) works.** Two swaps of
  slots 10/11, each: `slot change re-points P1B15` → `macros -> ok (slots 10, 11)`
  → `live-buttons -> ok (P1B15=910a0100)` then `…=91090100`; the side button
  kept playing its own macro. **Confirmed: the mouse uses a re-pointed button
  record immediately (no refresh needed).** Cause of the first failure not
  established (inference: the app was started before the option (b) code, or
  the old popup path was taken).

### T6 — scroll lines (implemented 2026-11-19, offline-tested)

Evidence: `hid.exe` 0x410070 builds a 64-byte **Report 3**
`03 F3 20 00 0A 00 00 00` + one byte per profile copied directly from the
settings' WheelScrollLines field (`obj+0x68 + 0x3880·p`) + `00` each, then
zeros; no F1 of its own; called only from Apply (before the tail
`F1 02 04, 02 01, 02 02, 02 08`). Windows' profile routine 0x4109d0 applies only
SPI_SETMOUSE (acceleration), SPI_SETMOUSESPEED (pointer speed) and
SetDoubleClickTime to the PC — **never wheel scroll lines** — so the mouse is
what uses this value (inference, strong).

- Read: `read_snapshot` adds one fixed read after the captured plan
  (`EXTRA_READS = ((0x0020, 10),)`, the T2 probe request); `device_decode`
  decodes `scroll_lines` (1–63 with 00 companions) and the mouse wins.
- Write (Apply category, after polling): helper `write_scroll`: stable read →
  refused unless known → Report 3 → the Apply tail F1s (10 ms each) → read-back.
  Writer accepts 16/64/256-byte reports (length from the validated report).
  Audit: Report 3 only for 0x0020, values 1–63.
- Settings texts updated (two-line status kept; Xvfb check).
- **Live-confirmed by the user (2026-11-19):** scroll speed changed the scroll
  distance and survived a power cycle. The mouse does use the value.

## 5. Slot 8 investigation (2026-11-19, offline)

- Factory image: slots 1–15 empty **except slot 8, which holds exactly
  `ff ff` at `0x0730–0x0731`** followed by 198 zeros.
- All 72 saved Linux reads (July → today): slot 8 unchanged `ff ff 00 …`.
- No captured Windows run writes slot 8 (the only Report-4 upload in all
  captures is the slot-1 macro); Windows Apply never rewrites slot bodies.
- No Windows code refers to device address `0x0730` or special-cases slot
  index 7.
- `ff ff 00` is not a valid action record (no type `0x7F`); it is two bytes,
  not a 3-byte action list. Inference: factory residue or a firmware marker
  sitting in slot 8's range; whether the firmware relies on it is unknown.
- Current behaviour: the app never writes slot 8 (pre-read guard), shows it
  as "On mouse" and buttons may point at it (none do).
- Possible next step (user decision): a controlled experiment that whitelists
  exactly the factory pattern so slot 8 can be written and later restored to
  `ff ff`, with full reads before/after plus replug/power cycle to detect any
  side effect elsewhere in memory.


> **Correction (2026-11-19, later):** slot 8 (index 7) is at
> `0x0478 + 7×0xC8 = 0x09F0`, not `0x0730`; the `ff ff` bytes are at
> `0x09F0–0x09F1`. The reads themselves were right; only the address quoted
> above was wrong, so the "no Windows code refers to 0x0730" search looked
> for the wrong address. §5.1 settles the question anyway.

### 5.1 Windows writes slot 8 (2026-11-19, user-run)

Method: the VM grabbed the mouse at boot. In the Windows utility the user
recorded about 9 one-key macros (record → stop each time, no Apply).
The mouse flashed on each stop. Mouse returned to Linux for a full read.
Earlier attempt (`…T184033`, mouse not reaching Windows): 0 bytes changed.
Also, Windows does not upload an empty macro; it uploads one only once it
has content.

Reads: before `venus_read_20261119T184033.443223Z`,
after `venus_read_20261119T214209.289848Z` (same 4604-byte coverage).
Only these ranges changed:

| Slot | Before | After |
|---|---|---|
| lighting P1 `0x0449` | `d9 0a 7e` | `00 a5 36` (user's connectivity check) |
| 7 | empty | `84 04 00 06 09 00 04 04` (a) |
| **8** | **`ff ff`** | **`84 05 00 06 07 00 04 05`** (b) |
| 9 | empty | `84 06 …` (c) |
| 10 | Linux test macro | `84 07 …` (d) |
| 11 | Linux test macro | `84 08 …` (e) |
| 12–15 | empty | `84 09` … `84 0c` (f–i) |

Confirmed:

- Windows uploads a macro to the mouse **as soon as it is recorded**
  (no Apply needed).
- Windows **does use slot 8**. It treated `ff ff` as free and overwrote
  it with a normal macro. Slot 8 is an ordinary slot; `ff ff` was factory
  filler.
- Windows fills slots in order from its *own* library's idea of which slots
  are free (7, 8, 9, …). It overwrote the two Linux-made macros in
  slots 10–11, because its library did not know about them.
  Slots 1–6 (Windows' own macros) were untouched.
- A slot upload changes nothing else in memory: no index, count or name
  bytes. Macro names live only on the PC.
- Windows' records use the same format the app writes (`84 kk` key down,
  `00 06 dd` delay, `04 kk` key up).

Inference: if the app treats the exact factory pattern (`ff ff` + 198 zero
bytes) as an empty slot, it matches Windows. This mouse no longer has the
pattern, so it only matters for factory-fresh mice.

### 5.2 App treats the factory filler as free (implemented 2026-11-19, offline-tested)

- `lighting_preview_plan.FACTORY_SLOT_FILLER` is exactly `ff ff` followed
  by 198 zero bytes. `macro_slot_is_free()` returns true for zeros or for
  that exact pattern.
- Helper `write_macros` pre-read guard: it now accepts the exact filler as a
  slot it may overwrite, as well as known bodies. Any other unrecognised
  body is still refused, and nothing is written. The filler can never be
  *sent*: request validation still requires a known action list.
- Decoder: a filler slot becomes an empty bank slot (not "On mouse"), so a
  library macro can be dragged onto it.
- Live controller: a filler slot the app wants empty is left alone. The app
  never sends zeros just to clear the filler.
- Not hardware-tested on a factory-fresh mouse. The user's mouse no longer
  has the filler, because Windows overwrote it in §5.1.

### T5 — DPI start-up stage (implemented 2026-11-19; **live-confirmed by the user 2026-11-20**)

Evidence: hid.exe `0x40FC60` writes each profile's selected stage as
`02 F3 <base+2> 02 00 00 00 <stage> 00 …` and, when its second argument is
set (as in Apply), then sends `F1 02 02` and `F1 02 10`; the saved Apply
capture (`led_mode_breathing_to_full`) shows F1 02 02 then F1 02 10 after
the five `base+2` writes (`investigation/REA_FINDINGS.md`, "Selected-stage
writer"). The byte's domain 0–4 (zero-based) is read-confirmed
(`docs/DPI_STAGE.md`).

- Request `{"version": 1, "dpi_stage": [{"profile", "stage"}]}` (stage
  zero-based 0–4), same helper.
- Helper `write_dpi_stage`: stable read of `base+2`/2 per profile → refused
  unless `[0–4, 00]` → one F3 per changed profile → `F1 02 02` → `F1 02 10`
  (10 ms after each) → stable read-back must equal the request. Validator
  accepts only the 5 `base+2` addresses, length 2, value 0–4, companion 00.
  Audit checks the transaction order and the field table.
- App: on Apply, after the DPI stage values and before buttons. The mouse's
  stage comes from the start-up read and from DPI± presses (a press updates
  both the profile and the known mouse stage, so it never causes a write).
  The DPI page's "click a stage name to make it the start-up stage" is the
  edit that enables Apply.
- Unknown: whether writing the **active** profile's byte changes the
  current DPI at once (Windows does it during Apply), and how it interacts
  with the ~20–30 s memory lag after a DPI± press (an Apply made within that
  window could be overtaken by the mouse's delayed update — untested).

**User test 2026-11-20:** "all dpi checks passed": choosing a start-up stage
in the app and clicking Apply set it on the mouse, it survived replug / power
cycle and an app restart, and DPI± afterwards was followed without enabling
Apply. (Whether the active profile's stage changed at once was not reported
separately.)

## 6. Fire-key count and delay (2026-11-20)

Evidence and design: `investigation/REA_FINDINGS.md`, "Fire-key count and
delay limits". The count is one byte in the record `99 trigger count delay`;
Windows' dialog accepts 3 typed characters with no range check and keeps both
values as bytes (so at most 255 shots and 250 ms are ever sent by Windows).

User decisions: shots 1–255 (first 1–99; raised to the byte maximum, the most
Windows can send, so users have the same freedom), delay 0–250 ms. The count is stored in the PFD
button structure after the trigger byte as `'V' 'C' count` (Windows reads
only byte 0 for a fire key, and leaves stale bytes there itself); read from
the mouse at start-up; Apply compares the full record only when the profile
stores a count (Windows `.pfd` files keep the mouse's count).

**Live results (user, with `python -m venus_profile.gui.click_counter`):**

| Shots set | Presses counted (incl. your own click) |
|---|---|
| 5, 10, 11, 20, 30, 99 | 5, 10, 11, 20, 30, 99 (each twice, exact) |

| Delay set | Median gap between shots |
|---|---|
| 50 ms | 154 ms |
| 60 ms | 164 ms |
| 80 ms | 184 ms |

- **Confirmed:** "shots per press" is the total, counting the first shot.
- **Confirmed:** the delay is applied exactly; the mouse adds ~104 ms of its
  own per shot (inference: click hold + release time), so the fastest rate is
  ~9–10 shots/s. The editor's tooltip says so.
- Typing into the fields now applies at once (GTK spin buttons otherwise
  commit only on Enter/focus-out, which left Apply disabled — user report).
- **Delay below 20 ms is broken on the mouse** (user, 2026-11-20): with 0 or
  10 ms only the first click arrives and the button stays busy for the whole
  burst; **20 ms works** — 255 shots counted, median gap 122 ms (min 122, max
  124). The editor now offers 20–250 ms (new fire keys default to 20 ms).
- **Windows cross-check (user, 2026-11-20):** Windows' fire dialog pre-fills
  20 ms for a new fire key but accepts 0 — and 0 breaks the mouse the same way
  there. So the limit is the mouse firmware, not the Linux app.
- The factory fire button itself is `99 81 03 00` in the saved factory read:
  3 shots at 0 ms, so (inference, not tested) it sends a single click. The app
  reads and keeps it as is until it is edited.
- **255 shots live-confirmed** (at 20 ms). Not tested: whether releasing the
  button stops a long burst; keyboard-key fire triggers; Windows loading a
  PFD that carries the `VC` count bytes.
