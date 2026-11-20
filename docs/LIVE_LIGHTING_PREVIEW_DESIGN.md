# Live lighting — guarded design and implementation

Status (2026-11-19): **implemented; profile 1 live-confirmed by the user**
(§10). Offline: unit tests, static audits, Xvfb visual check with fakes. All decisions in §7 are answered. This is the first
feature that lets the GUI change the mouse; it changes lighting only.

Implementation: `lighting_preview_plan.py` (pure encoding), `preview_lighting.py`
(one-shot helper), `snapshot_audit.preview_lighting_safety_audit()`,
`gui/lighting_preview.py` (launcher), `gui/live_lighting.py` (diff +
coalescing), wiring in `gui/application.py`, note in `gui/lighting/page.py`.
Tests: `test_lighting_preview_plan.py`, `test_preview_lighting.py`,
`test_gui_live_lighting.py`.

## 1. Goal

When the user changes colour, mode, speed or brightness on the Lighting page,
the mouse lights up the same way within about 0.2 s, as the Windows utility
does. Apply stays disabled for everything else.

## 2. What the evidence says (corrects the earlier premise)

The earlier plan (HANDOFF §5 / §9.1, `DEVICE_READ_BOUNDARY_DESIGN.md` §7)
said Windows previews with F3 writes **without** the `F1 02 04` finalise, so
the change would only last until power-off. **The saved captures contradict
this.**

| Capture (analysis dir under `usb-captures/analysis/`) | Preview cycle | Ends with `02 F1 02 04` | F5 |
|---|---|---|---|
| `led_colour_red_to_blue` | frames 8206–8229 | yes, frame 8228 | none |
| `led_colour_blue_to_green` (two cycles) | 2920–2943, 2944–2967 | yes, frames 2942, 2966 | none |
| `led_mode_breathing_to_full` | 5895–5918 | yes, frame 5917 | none |
| `led_mode_full_to_rainbow` (`investigation/REPORT.md`) | narrow F3 at 3289 | yes, frame 3309 | none |

**Confirmed (capture):** a Windows preview cycle is 11 Report-2 F3 writes
(`0x0446` = `00 00`, then for each profile the 6-byte record at
`0x0449 + 8(p−1)` and the 1-byte brightness 6 bytes later), roughly 8 ms
apart, then `02 F1 02 04`. There is no F2 read and no F5. Apply is different:
`F5 00`, the full 146-block plan, several `F1 02 xx`, then `F5 01`.

**Confirmed (Linux, live):** the same F3 + `F1 02 04` sequence persists
across unplug/replug: Blue→Red (`GUARDED_LED_COLOUR_WRITE.md`) and
brightness (`GUARDED_BRIGHTNESS_WRITE.md`: "F3 + lighting F1 persists without
F5").

**Inference:** a Windows preview is therefore stored on the mouse, not
temporary. The user saw the colour change "without saving", but the mouse may
keep it anyway, or Windows may write the old colours back when it closes.
Neither has been tested or captured.

**User-confirmed (2026-11-19, Windows utility on the physical mouse):** a
Windows lighting preview is a permanent change and survives a power cycle.
The inference above is therefore confirmed by observation.

**Consequence:** no evidenced command sequence gives a preview that is lost
at power-off. A Linux preview must be treated as a **real, persistent
lighting write**, and the app must take responsibility for putting things
back (§5).

## 3. Proposed architecture: a third, one-shot helper

Same pattern as the two existing helpers (`gui/safety.py`, `HELPER_LAUNCHES`):

| GUI launcher | Helper | Audit |
|---|---|---|
| `gui/lighting_preview.py` (new) | `venus_profile.preview_lighting` (new) | `snapshot_audit.preview_lighting_safety_audit()` (new) |

- One literal call site `argv = [sys.executable, "-m", HELPER_MODULE]`, no
  options. The GUI keeps its ban on hidraw, ioctl and `guarded_*` imports.
- **One transaction per launch.** The helper exits when done; it never holds a
  write-capable fd between previews.
- **Input:** one small JSON document on stdin (≤ 4 KiB). It holds semantic
  values only, never addresses, lengths, raw bytes or report bytes:

  ```json
  {"version": 1, "profiles": [
    {"profile": 1, "rgb": [255, 0, 0], "mode": "breathing", "speed": 5, "brightness": 3}
  ]}
  ```

- **Output:** one JSON document on stdout:
  `{"status": "ok" | "unchanged" | "refused" | "mismatch" | "error", ...}`
  with the before/after bytes of each record.

## 4. Fixed plan and guards inside the helper

Encoding is a pure function with an explicit allow-list (new module,
`lighting_preview_plan.py`), cross-checked by tests against
`apply_plan.PFD_MODE_TO_DEVICE_PREFIX` and `lighting_state.DEVICE_LED_MODE_PREFIX_NAMES`:

| Mode | Bytes 4–6 of the record | Evidence |
|---|---|---|
| Full Brightness | `01 00 01` | Linux live, persisted |
| Breathing | `02 ss 01`, ss ∈ {08, 05, 02, 00} | Linux live cycle (visual), Windows |
| Wave | `03 00 01` | Linux live cycle (visual) |
| Flash | `04 05 01` | Linux live cycle (visual) |
| Rainbow | `02 00 03` | Windows capture + saved reads; no Linux write yet |
| Off | `00 00 00` (RGB kept) | Windows capture; Linux Off→Full recovery tested offline only |

RGB is 0–255 per channel, written directly as `RR GG BB`. This is supported
by the 20-colour Linux visual cycle and by Windows previewing intermediate
picker values (`02 B5 34`). Brightness ∈ {0, 1, 2, 3}.

Addresses come from one fixed table: records `0x0449/0x0451/0x0459/0x0461/0x0469`
(6 bytes) and brightness `+6` (1 byte). The helper does **not** write `0x0446`.
Its meaning is unknown, and the proven Linux sequence never wrote it.

Transaction (the proven Linux sequence, extended to the records that changed):

1. Discover and check identity exactly as `read_snapshot` does (04D9:FC55,
   iface 02, usage FFA0/1). Refuse on any doubt.
2. **F2 pre-read**: the 5 existing read-plan requests `0x0449+8k`, length 7.
   Refuse unless every record to be written decodes to a known mode.
3. Compare with the request. If nothing differs, exit `unchanged` (no write).
4. Open the write-capable fd only now. For each changed record, send one
   exact F3 (6 or 1 bytes), then wait 10 ms. Each outgoing report is validated
   against the fixed table before it is sent.
5. Send one `02 F1 02 04`, then wait 10 ms.
6. **F2 read-back** of the same 5 requests. Written bytes must match exactly,
   and all other bytes must be byte-identical to the pre-read. Otherwise the
   result is `mismatch`.
7. No retry, no F5, no other report IDs, no automatic restore inside the
   helper.

Immediate read-back only confirms the command result; it is never reported as
persistence.

## 5. GUI behaviour

- **Coalescing:** at most one helper runs at a time. While one runs, only the
  newest pending change is kept, and it is sent when the running one exits.
  A colour-picker drag therefore produces a few transactions, not hundreds.
  A transaction (discovery + 5 reads + ≤10 writes + F1 + 5 reads) is expected
  to take ~0.1–0.3 s (inference).
- **Not during a read:** no preview while the start-up or "Read from Mouse"
  snapshot read is running, or before a successful read. Without a read there
  is no known starting point to restore to.
- **The event listener keeps running.** It reads input reports, and the
  helper uses feature reports on a separate fd.
- **Baseline and restore:** the lighting decoded at the last successful read
  is the *mouse baseline*. Cancel / Restore / Load / Reset in the working set
  simply produce new lighting values, which are previewed like any edit. What
  happens on **close** is a user decision (§7, D2).
- **No switch (D3):** always live after a successful read, like Windows. The
  Lighting page's note says either "Changes are sent to the mouse straight
  away and stay on it." or "Changes are kept in the app only until the mouse
  is read."
- **Close:** nothing is written on close (D2). If a write is running, or a
  change is queued behind it, the window waits for it to finish before
  closing (the helper times out after 10 s at most).
- **Unplug:** the event listener's "disconnected" stops live lighting until
  the mouse is read again.
- **Failure:** any status other than `ok`/`unchanged` stops live lighting and
  shows a banner ("Lighting couldn't be sent to the mouse (…)", **Read
  again**). The app never retries on its own.
- **Kill switches:** `VENUS_PROFILE_NO_DEVICE_READ=1` or
  `VENUS_PROFILE_NO_DEVICE_WRITE=1` stop the launcher before any process
  starts. `tests/conftest.py` sets both for every test; headless (Xvfb) visual
  checks use a fake writer and block `Gio.Subprocess.new`.

## 6. Safety audits and tests (as planned; implemented)

- `gui/safety.py`: add the third helper to `HELPER_LAUNCHES`. The audit still
  requires exactly one literal launch per launcher.
- `preview_lighting_safety_audit()`: only Report-2 F3 to the 10 fixed
  addresses with lengths 6/1, only F1 `02 04`, F2 only from the 5 read-plan
  requests, no F5, no Report 3/4/5, no CLI options, no file output, write fd
  opened only after the pre-read guard.
- Unit tests: encoding table (all modes, invalid speed/brightness/RGB/profile
  rejected, unknown keys rejected, oversize input rejected), report
  validation, pre-read refusal on unknown modes, `unchanged` path, read-back
  mismatch (written and neighbour bytes), fake-transport transaction order and
  delays, GUI coalescing, toggle, failure banner, no preview before a read.
- Visual check on Xvfb with fakes (the Lighting-page note and failure banner).

## 7. Decisions (user answers 2026-11-19)

- D1 — **Yes**: previews are persistent lighting writes.
- D2 — **(a)**: on close, leave the mouse as previewed. No write on close.
- D3 — **No switch**: always live after a successful read (the read means the
  app already matches the mouse, so start-up writes nothing).
- D4 — **Profile 1** for the first live test.

Original wording of the questions:


- **D1 — Accept persistent preview.** Previews are real lighting writes
  that survive power-off (§2). Proceed on that basis? (The alternative is to
  first test F3 *without* F1 on profile 1, an unobserved sequence with an
  unknown outcome. Not recommended: Windows never does it.)
- **D2 — On closing the app** with previewed lighting that differs from the
  mouse baseline: (a) leave the mouse as previewed (simplest; it then matches
  the app's saved working set), or (b) write the baseline back on close, like
  an un-applied Windows change. *Recommendation: (a)*, plus a one-line note on
  the Lighting page that preview changes stay on the mouse.
- **D3 — Default of the switch:** off until the first successful live test,
  then on. *Recommendation.*
- **D4 — First live scope:** profile 1 only for the first user-run test (the
  only profile with Linux lighting writes), then profiles 2–5 after a passing
  read-back and replug read. *Recommendation.*

## 8. First live test (user-run, profile 1)

No separate dry-run CLI was added (the helper must take no options). The exact
reports are pinned by `tests/test_preview_lighting.py`, e.g. profile 1 →
`02 F3 49 04 06 00 00 00 RR GG BB 01 00 01 00 00`, then `02 F1 02 04 …`.

1. Start the app (`PYTHONPATH=src python3 -m venus_profile.gui`) and let it
   read the mouse. The Lighting note should say changes are sent to the mouse.
2. With **profile 1** selected, pick one palette colour. Don't touch other
   profiles or use Load File / Restore / Reset All / Cancel during this test:
   they change lighting too and would be sent as well.
3. Check the mouse shows the colour (if profile 1 is the active profile on
   the mouse). No banner should appear.
4. **Read from Mouse**: profile 1 should show the new colour.
5. Unplug/replug (or power-cycle), start the app again: the start-up read
   should show the new colour (persistence check by the user).

A failure banner means nothing further is sent; report its text.

## 9. Open questions

- ~~Does the Windows utility write the old lighting back when closed without
  Apply?~~ Answered 2026-11-19 (user): no. The preview is permanent and
  survives a power cycle.
- `0x0446` (always `00 00` in captures) is still unexplained.
- Rainbow and Off have no Linux live write yet. Off→Full recovery is
  offline-tested only.
- `08 09` lighting-mode events are unobserved. The listener ignores unknown
  events.

## 10. Live result (2026-11-19, user-reported)

User ran the §8 procedure on **profile 1**: "everything worked perfectly,
power/app restart/changed a few time everything working".

- Confirmed by user observation: profile 1 lighting changes from the app show
  on the mouse; several successive changes worked; the change survived a
  power cycle and an app restart (start-up read showed it).
- No failure banner reported.
- Not yet tested live: profiles 2–5 (code allows them; Windows captures show
  the same layout), Rainbow and Off written from Linux, Load / Restore /
  Reset All / Cancel sending several profiles in one transaction.

### 10.1 All modes; intermittent false refusals (2026-11-19, user-reported)

- Confirmed by user: all six modes work from the app (Full Brightness,
  Breathing, Wave, Flash, Rainbow, Off).
- Problem: brightness and breathing-speed changes intermittently (after 0–4
  changes, not timing-related) showed "profile 1 lighting on the mouse
  (80 e4 0a 63 02 02 01) is not a known mode"; **Read again** showed the change
  had in fact been made.
- Analysis (inference): the reported bytes are the expected record
  `e4 0a 63 02 02 01 ..` (Rose, breathing speed 02) shifted by one byte, with an
  unknown leading `80`, i.e. the reply held the data from `0x0448` instead of
  `0x0449`. The mouse's F2 reply header echoes `address & ~1` (captured
  responses: `48 64` for `0x0449`), so this shift cannot be detected from the
  header. None of the 355 lighting reads in saved live reads shows it.
  **Confirmed 2026-11-19:** the probe (`APPLY_HARDWARE_TESTS.md` T2) read
  `0x0448 = 80`, so the shifted reply really held the data from `0x0448`.
- Fix (offline-tested): every helper lighting read (pre-read and read-back) is
  repeated until two consecutive replies agree, at most 4 reads; otherwise the
  helper stops with `write-failed` ("unstable read…") before writing anything.
  Writes are still never retried. Enforced by the safety audit; tests replay
  the exact shifted reply. **User re-test: no more errors on brightness or
  speed** (2026-11-19).
- Not addressed: the start-up 193-request read could in principle hit the same
  glitch (no evidence it has).
- UI fix: the Lighting note now puts each sentence on its own line; in Rainbow
  and Off the joined line was long enough to widen the window (user-reported).
- User-confirmed (2026-11-19): the note fix; lighting on profiles 2–5; Load
  File and Reset All (multi-profile transactions) all work live.

## 11. Switch the mouse's profile from the app (approved, implemented)

Status (2026-11-19): read side live-confirmed (the app opens on the mouse's
profile and follows the button). Switching **approved by the user and
implemented; offline-tested only, awaiting live test.** As built: request
`{"version": 1, "active_profile": N}` via `lighting_preview_plan.profile_request_document`;
helper `write_active_profile`; GUI `LiveLighting.wanted_profile` (switches
only when the mouse's active profile is known; a switch runs before lighting;
a matching mouse profile event within 2 s of a switch is treated as its echo
and does not move the selection). Failure banner wording is now "Changes
couldn't be sent to the mouse…".

Original proposal:

Evidence: `investigation/LIVE_EVENT_FINDINGS.md` §10 (static only, `hid.exe`).
Proposal, reusing the same one-shot helper (still three launches):

- New request kind `{"version": 1, "active_profile": 1..5}`; nothing else.
- Transaction: identity checks → stable F2 read of `0x002C` (the read-plan
  request, length 1) → if already equal, `unchanged` → F3
  `02 F3 2C 00 02 00 00 00 pp 00 00 00 00 00 00 00` (pp = 0..4, byte `0x002D`
  written as `00` exactly as `hid.exe` and every captured Apply do) → 10 ms →
  `02 F1 02 01 …` → 10 ms → stable read-back of `0x002C` must equal pp.
- Audit additions: exactly these two templates, pp ≤ 4, no other address.
- GUI: when live, clicking a profile button switches the mouse to it (Windows
  behaviour); the mouse's own `08 01` event keeps the app in step; failure →
  banner, live writes stop.
- Unknowns: `0x002D` has never been read; whether the switch is persistent
  across power-off (the user reports the mouse resumes its last profile, so
  probably yes); whether the mouse emits `08 01` after a software switch.

### 11.1 First live test failed; sequence corrected (2026-11-19)

User: app profile buttons did not change the mouse's profile (no banner).
`0x002C` + `F1 02 01` is only part of what Windows sends; the profile-tab
handler `hid.exe` 0x410cf0 also sends `F1 02 04`, `F1 02 02`, `F1 02 08`
(`investigation/LIVE_EVENT_FINDINGS.md` §10.1). The helper now sends all four
(the undefined-buffer send is not reproduced); audit and tests updated. The
GUI now prints one line per mouse write to stderr (`venus-mouse: profile -> ok
(active 3)`) so live tests show the helper's result.

**Live-confirmed by the user (2026-11-19):** with the full sequence the app's
profile buttons switch the mouse's active profile; the physical button is
still followed. Terminal log: `profile -> ok (active 3)`, `(active 5)`,
`(active 1)`, `(active 1)`. Persistence of an app-made switch across power-off
not yet reported.

**Follow-up (2026-11-19):** user-confirmed that after a power cycle the mouse
starts on the last profile selected in the app (app-made switches persist).
Change: the helper now always sends the switch when the GUI asks (the
`0x002C` pre-read is a range guard only), because the mouse's memory may lag a
physical button press while the GUI's profile events are authoritative;
an unneeded switch is harmless. Offline-tested; no live re-test needed beyond
normal use.
