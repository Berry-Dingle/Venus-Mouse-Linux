# Button-assignment protocol (read-only)

This document describes only the button-assignment facts supported by saved
repository evidence. The implementation is a decoder for saved bytes. It has
no writer, report builder, live transport, device discovery, or generic HID
sender.

## Confirmed profile layout

The Windows factory-open capture contains five groups of twenty four-byte
button-assignment records. The profile bases are:

| Profile | Region | Base |
|---|---|---:|
| 1 | `0x0082–0x00E1` | `0x0082` |
| 2 | `0x0142–0x01A1` | `0x0142` |
| 3 | `0x01F2–0x0251` | `0x01F2` |
| 4 | `0x02A2–0x0301` | `0x02A2` |
| 5 | `0x0352–0x03B1` | `0x0352` |

The assignment records are four bytes each. For buttons 1–18, the offset is
`4 × (button − 1)`. Buttons 19 and 20 use offsets `0x58` and `0x5C`.
Therefore:

```text
address(profile, button) = profile_base[profile] + button_offset[button]
```

For example, profile 1 button 7 (`ButtonAssigned_7`) is at `0x009A`.
Deliberate gaps in each region are not interpreted as assignments.

## Confirmed record decoding

Every record is preserved as its exact four raw bytes. The first byte is
exposed numerically as `function_code`.

Only function code `0x91` has confirmed field-level semantics:

```text
byte 0       0x91                 macro assignment
byte 1       bits 0..5            device macro slot index
byte 1       bit 6                toggle flag
byte 1       bit 7                while-held flag
bytes 2..3   little-endian uint16 execution count
```

The saved controlled capture contains:

```text
factory/default: 90 00 1E 00
macro assignment: 91 00 01 00
```

The latter decodes as device slot 0, while-held false, toggle false, and
execution count 1.

The non-macro campaign also confirms these complete four-byte records. The
labels below apply to the complete raw record, not to the function code alone:

| Label | Complete record |
|---|---|
| Disable | `00 00 00 00` |
| Left Click | `81 00 00 00` |
| Right Click | `82 00 00 00` |
| Middle Click | `83 00 00 00` |
| Backward | `84 00 00 00` |
| Forward | `85 00 00 00` |
| Combo Shift+Win | `8F 0A 00 00` |
| Combo Shift+A | `8F 02 04 00` |
| DPI Switch Cycle | `88 00 00 00` |
| DPI- | `89 00 00 00` |
| DPI+ | `8A 00 00 00` |
| Profile Switch | `8D 00 00 00` |
| Single Key A | `90 00 04 00` |
| Single Key 1 (factory/default; formerly mislabelled "Single Key A") | `90 00 1E 00` |
| Profile+ | `94 00 00 00` |
| Profile- | `95 00 00 00` |
| Report Rate+ | `97 00 00 00` |
| Report Rate- | `98 00 00 00` |
| Fire Key: Left, 3 fires, 0 ms | `99 81 03 00` |
| Fire Key: Left, 4 fires, 10 ms | `99 81 04 01` |
| Fire Key: Left, 3 fires, 20 ms | `99 81 03 02` |
| Fire Key: Right, 3 fires, 10 ms | `99 82 03 01` |
| Fire Key: Middle, 3 fires, 10 ms | `99 84 03 01` |
| Fire Key: Keyboard A, 3 fires, 10 ms | `99 04 03 01` |

Both `0x90` labels apply only to their exact complete records. The
factory/default `90 00 1E 00` (HID usage 0x1E, the "1" key; see the 2026-11-18
correction below) is accepted as the confirmed source
record for the guarded experiment; other `0x90` records remain unknown. Bytes
1–3 remain semantically unknown except for the separately confirmed `0x91`
macro fields. No unsupported keyboard, media, DPI, profile, report-rate,
advanced Windows, mouse, or disable generalization is made.

The controlled Combo/Report Rate campaign additionally confirms:

```text
Single Key A:     90 00 04 00
Combo Shift+A:    8F 02 04 00
Combo Shift+Win:  8F 0A 00 00
Report Rate+:     97 00 00 00
Report Rate-:     98 00 00 00
```

`0x8F` is common to the two captured combo records. The third byte `04` is
shared by Single Key A and Combo Shift+A. These observations do not isolate
modifier, ordinary-key, Win-key, length, or other byte semantics, and the
decoder does not implement generalized combo construction or interpretation.

Report Rate+ and Report Rate- are button-assignment labels only. They are not
associated with the separate polling-rate values `08=125 Hz`, `04=250 Hz`,
`02=500 Hz`, and `01=1000 Hz`. In the controlled campaign, assigning either
action changed only Profile 1 ButtonAssigned_7; active polling rate, selected
DPI stage, lighting, and all memory outside `0x009A–0x009D` were unchanged.

The Fire Key campaign confirms only the six complete records listed above.
`0x99` is common to all six records. In the observed samples, byte 1 is `81`
for Left, `82` for Right, `84` for Middle, and `04` for the complete
Keyboard-A sample. In the controlled Left comparison, fire count 3 versus 4
correlates with byte 2 changing `03` to `04`. The observed delay-byte values
are `00` = 0 ms, `01` = 10 ms, and `02` = 20 ms; this mapping is confirmed
only for those tested values. The Keyboard-A record is confirmed as a complete
record, but keyboard-mode and key-code fields are not independently isolated.
No universal formula, supported range, or generalized Fire Key constructor is
implemented.

The factory/reset physical Button 4 record `99 81 03 00` is therefore labeled
only as the exact observed `Fire Key: Left, 3 fires, 0 ms` record. Two reset
readbacks were byte-for-byte identical across 4,604 captured bytes. Immediately
after Reset to Default, the Windows utility could temporarily display 10 ms;
after utility restart or mouse reconnect it displayed 0 ms for the same record.
The refreshed UI and device-memory evidence support `99 81 03 00` as the
observed 0 ms record; the immediate display is treated as stale UI state.

Each controlled sample changed only Profile 1 Button 4 at `0x008E–0x0091`.
The other 99 assignment records and all memory outside that four-byte record
remained unchanged. The `.pfd` evidence retains `ButtonFunc=12`; `KeyNumber`
varied and the 190-byte `ButtonAssigned_4` blob boundary values changed. No
checksum semantics are established for those PFD differences. The Windows utility's Fire
Settings dialog shows the parameterised L/R/M, optional keyboard key, `Fire(times)`, and
`Delay(ms)` controls.

## Evidence provenance

- Profile ranges and the factory/default records: saved Windows factory-open
  capture and its offline reconstructed memory map at
  `usb-captures/analysis/factory_memory_map.json`.
- The `0x90` to `0x91` transition and macro-field interpretation: saved
  Windows capture `macro_slot1_a_press_release_20260727_090056.pcapng` and its
  offline analysis report.
- The later `91 00 01 00` value: saved read-back fixture only. It is not used
  to claim persistence.
- The thirteen complete non-macro labels: the original Windows Apply campaign
  and the three-action recapture read-backs. The original pcap path was reused
  by the short recapture campaign; its duplicate Forward Apply and final
  recapture bookkeeping mismatch are retained as evidence notes, not treated
  as additional mappings.
- The four Combo/Report Rate labels: continuous pcap
  `usb-captures/button-assignment-campaign/button_assignments_combo_fire_report_rate_20260728_113553.pcapng`,
  four matching saved readbacks, and four matching `.pfd` files. The pcap has
  four isolated Apply sequences in the stated order; all 100 assignment records
  were compared for each readback.
- `.pfd` field presence and existing numeric function values: preserved
  Windows profile samples under `ProfilesMacros/`.
- The six Fire Key labels: continuous pcap
  `usb-captures/button-assignment-campaign/fire_key_campaign_20260728_123751.pcapng`,
  six matching readbacks, the repeated reset confirmation readback, and six
  matching `.pfd` files. The complete readbacks changed only P1 Button 4.

## Inference and unknowns

The correspondence between the five memory regions and the five Windows
profiles is confirmed by capture/project correlation. Fire Key semantics are
limited to the six exact records and the tested byte observations above. The
meaning of every other function code, untested Fire Key values, generalized
keyboard encoding, and non-macro record bytes remain unknown. No persistence
claim is made.

## Safety boundary

Imports, tests, and the CLI path for this decoder operate on supplied bytes or
saved files only. They must not discover devices, open `/dev/hidraw*`, access
USB device nodes, read or write live HID reports, or construct HID reports.


## Correction (2026-11-18): `90 00 1E 00` is "Single Key 1"

The factory record `90 00 1E 00` was historically labelled "Single Key A".
Saved factory memory shows thumb buttons 1–12 as `90 00 1E..27 00`,
`90 00 57 00` and `90 00 56 00` (HID keys 1–0, Keypad +, Keypad −). The
hid.exe single-key compiler (`0x412F10`, `ButtonFunc 6`) copies the key
usage into byte 2. The record is therefore the "1" key; `90 00 04 00`
remains "Single Key A". Matching is still exact four-byte only. Restore
accepts evidence that recorded the old label, but only when the exact raw
record and canonical ID also match (`HISTORICAL_ACTION_LABELS`).

The complete PFD `ButtonFunc` → device-template table recovered from
`hid.exe` `0x47D470` is documented in `WINDOWS_WORKFLOW.md`. It is used for
offline editing and the Apply preview only; it does not extend the guarded
writer's allowlist.
