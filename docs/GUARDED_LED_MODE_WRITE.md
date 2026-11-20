# Guarded Profile 1 LED-mode experiment

The guarded experiment supports the exact Profile 1 Full Brightness ↔
Breathing transitions and an offline-tested True Off → Full Brightness recovery
direction. True Off is the captured prefix with the preserved current RGB
`7B 2D D2 00 00 00`; no Full Brightness → Off writer is enabled.

## Fixed semantic states

| Mode | Exact bytes at `0x0449–0x044E` |
|---|---|
| Full Brightness | `FF 00 00 01 00 01` |
| Breathing | `FF 00 00 02 05 01` |
| True Off (current colour) | `7B 2D D2 00 00 00` |
| Full Brightness (current colour) | `7B 2D D2 01 00 01` |

The byte at `0x044C` is the mode (`01` Full Brightness, `02` Breathing).
`0x044D` is the correlated companion (`00` and `05`, respectively).
Brightness at `0x044F` is outside the write range.

## Exact transaction

```text
F2 pre-read:
02 F2 49 04 06 00 00 00 00 00 00 00 00 00 00 00

F3 Full Brightness → Breathing:
02 F3 49 04 06 00 00 00 FF 00 00 02 05 01 00 00

F3 Breathing → Full Brightness:
02 F3 49 04 06 00 00 00 FF 00 00 01 00 01 00 00

F3 True Off → Full Brightness:
02 F3 49 04 06 00 00 00 7B 2D D2 01 00 01 00 00

wait 10 ms

lighting F1:
02 F1 02 04 00 00 00 00 00 00 00 00 00 00 00 00

wait 10 ms

F2 read-back of six bytes
```

Success requires exact read-back `FF 00 00 02 05 01`. There is no F5,
retry, restoration, generic report sender, generic memory writer, arbitrary
address, or arbitrary payload.

The initial F2 uses the ordinary read-only session. The private write-capable
fd and internal LED-mode capability are not opened until the complete source
prefix matches.

## Commands

Offline dry run:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_mode \
  --dry-run --full-to-breathing
```

Full Brightness → Breathing:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_mode \
  --live-led-mode-write \
  --full-to-breathing \
  --acknowledge-device-write
```

Required typed confirmation:

```text
WRITE 0449 FULL TO BREATHING
```

Breathing → Full Brightness dry run:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_mode \
  --dry-run --breathing-to-full
```

Breathing → Full Brightness live command:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_mode \
  --live-led-mode-write \
  --breathing-to-full \
  --acknowledge-device-write
```

Its required typed confirmation is:

```text
WRITE 0449 BREATHING TO FULL
```

True Off → Full Brightness live command (first live experiment; user-run):

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_mode \
  --live-led-mode-write --off-to-full \
  --acknowledge-device-write
```

Required confirmation:

```text
WRITE 0449 OFF TO FULL
```

The three direction flags are mutually exclusive. Full Brightness → Off is
not exposed. The Off recovery preserves RGB and excludes brightness `0x044F`.

## Separate known state

LED mode uses
`usb-captures/device-state/known_led_mode_state.json`. It stores the complete
six-byte prefix and append-only transitions separately from the existing
single-byte brightness history. Immediate read-back creates a pending
transition. A second mode write is blocked until a later complete live read is
confirmed offline:

```bash
PYTHONPATH=src python3 -m venus_profile.known_led_mode_state \
  confirm-persistence /path/to/completed/live-read
```

This command reads saved JSON only and never discovers or opens hidraw.
Immediate read-back does not establish power-cycle persistence.

If a transition was mistakenly confirmed using a pre-power-cycle read, the
normal command intentionally refuses to consume it again. Use the explicit
offline correction command with both the superseded and genuine complete
read directories; it preserves the first path as audit evidence and replaces
only the named transition's authoritative confirmation:

```bash
PYTHONPATH=src python3 -m venus_profile.known_led_mode_state \
  correct-persistence '<transition-id>' \
  /path/to/superseded-post-write-read \
  /path/to/genuine-post-power-cycle-read
```

Rainbow → Full Brightness recovery is also an exact-prefix, offline-tested
direction. Rainbow is identified by the complete `0x044C–0x044E` triplet
`02 00 03`, not by raw mode byte `02` alone. The source prefix is
`7B 2D D2 02 00 03`, target is `7B 2D D2 01 00 01`, and the required
confirmation is `WRITE 0449 RAINBOW TO FULL`. Full Brightness → Rainbow remains
disabled; no generic mode writer is exposed.
