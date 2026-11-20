# Guarded arbitrary custom-colour experiment

The first experiment successfully wrote and persisted raw prefix
`00 00 FF 01 00 01`, but the mouse visibly turned blue. That proves device
storage is direct RGB and the original semantic conversion was wrong.

The corrected recovery milestone permits one live transition:

```text
semantic #0000FF -> #FF0000
device   00 00 FF -> FF 00 00
prefix   00 00 FF 01 00 01 -> FF 00 00 01 00 01
```

Mouse memory is native `RR GG BB`. Windows custom-picker captures separately
show that Windows reversed its entered hex channels before writing; native
Linux must not reproduce that Windows UI behavior. Callers cannot provide an address, raw bytes, suffix,
report payload, report ID, or finalisation command.

## Offline dry run

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_custom_colour \
  --dry-run --hex '#FF0000'
```

Other strict `#RRGGBB` values can be parsed and shown offline, but live mode
rejects every target except `#FF0000` in this milestone.

## First live experiment

Run only after reviewing the dry run:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_custom_colour \
  --live-custom-colour-write \
  --hex '#FF0000' \
  --acknowledge-device-write
```

Required typed confirmation:

```text
WRITE 0449 #0000FF TO #FF0000
```

Exact write report:

```text
02 F3 49 04 06 00 00 00 FF 00 00 01 00 01 00 00
```

The operation requires exact source pre-read, waits 10 ms after F3, sends
only exact lighting `F1 02 04`, waits 10 ms, and requires exact six-byte
read-back. It sends no F5 and has no retry or automatic restoration.

## State and persistence

The independent state file is:

```text
usb-captures/device-state/known_custom_colour_state.json
```

It records semantic hex, native device RGB, complete prefixes, conversion
rule, operation evidence, immediate read-back, and append-only persistence
history. Schema v2 includes an explicit semantic-correction record preserving
the original erroneous request, raw write/read-back, timestamps, result path,
and power-cycle evidence. It does not modify brightness, LED-mode, or
palette-colour state.

Confirm persistence later from a complete saved live-read directory:

```bash
PYTHONPATH=src python3 -m venus_profile.known_custom_colour_state \
  confirm-persistence /path/to/completed/live-read
```

State inspection and validation are offline:

```bash
PYTHONPATH=src python3 -m venus_profile.known_custom_colour_state show
PYTHONPATH=src python3 -m venus_profile.known_custom_colour_state validate
```
