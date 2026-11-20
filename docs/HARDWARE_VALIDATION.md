# Hardware validation plan for enabling device Apply

The GUI never communicates with the mouse. Before any GUI Apply can be
enabled, the operator must collect the evidence below **manually**. Automated
tooling must not run these steps. Each step requires a fresh verified backup, a
known starting state, and stopping on the first unexpected byte.

The evidence levels shown by *Apply Preview…* (`verified`, `static-only`,
`inferred`, `unresolved`) identify what each step must prove.

## 0. Preconditions

1. Confirm the interface descriptor and identity with the existing read-only
   tooling (`python3 -m venus_profile.read_device_memory`, user-run), saving
   a full 193-request baseline read.
2. Keep the Windows utility available on a separate machine for comparison
   captures.

## 1. Command-sequence isolation (blocks everything else)

In Windows, change exactly one setting and capture Apply with before/after
full reads, a reconnect, and a third full read. Repeat for:

| Setting | Expected changed bytes (from Apply Preview) |
|---|---|
| Profile 1 polling 500 → 1000 Hz | `0x0032` only |
| Profile 1 stage 1 DPI 1000 → 1600 | `0x0044..0x0048` only |
| Profile 1 start-up stage 2 → 3 | `0x0042` only |
| Profile 1 Thumb 2 key 2 → F | `0x009E..0x00A1` only |

Record every F1/F5 report and its timing. Success means the unchanged
neighbours stay byte-identical and the value survives reconnect.

## 2. Linux single-write experiments (only after step 1)

For each setting above, build a guarded module following
`guarded_brightness.py`:

- exact identity check;
- one fixed address;
- an exact source value from a pre-read;
- typed confirmation;
- one F3 write, plus only the F1 shown necessary in step 1;
- exact read-back;
- separate reconnect evidence.

Never add a generic sender.

## 3. Macro slots

1. Upload to empty slot 2 (`0x0540`), verify the 200-byte body, reconnect.
2. Replace a populated slot and confirm stale bytes are cleared.
3. Only if needed: a zero-body write to an unused slot. Never touch regions
   15–19 (`0x1030..0x1417`).

## 4. Remaining profile mappings

- Lighting for profiles 2–5: the addresses are already known. Saved Windows
  Apply captures write all five records (`0x0449 + 8×(profile−1)`, brightness
  at +6) and the factory read matches the factory profiles
  (`investigation/READ_COVERAGE_AUDIT.md`, 2026-11-18). What remains is one
  guarded Linux write to a profile 2–5 record with `F1 02 04`, then a
  reconnect read.
- Active profile: a read before and after one profile-button press (read-only)
  to find the byte that holds the active profile.
- Scroll lines: confirm `0x0020` (Report 3) is readable, and capture a Windows
  Apply that changes only profile 1's scroll speed.
- Palette translation: apply two palette entries other than red, green and
  blue.
- Fire-key count: capture Apply of a fire key after changing only the count.

## 5. Enabling GUI Apply

After the steps above, wire a GUI boundary that reuses the guarded modules.
It must show transport submission, read-back acceptance and durability
separately, and update `gui/safety.py` deliberately. Until then the GUI's
static audit forbids every device module.
