# Guarded Profile 1 LED-colour experiment

The guard supports both exact directions between official Blue and official
Red. Blue → Red is confirmed persistent after unplug/replug. Red → Blue is
implemented and tested offline only.

## Exact states

| Colour | RGB | Six-byte Profile 1 prefix |
|---|---|---|
| Official Blue | `0E 0F FF` | `0E 0F FF 01 00 01` |
| Official Red | `FF 00 00` | `FF 00 00 01 00 01` |
| Official Green | `00 FF 01` | `00 FF 01 01 00 01` |

These are device values from controlled captures, not values derived from PFD
COLORREF. The mode/companion/trailing suffix `01 00 01` is immutable.
Brightness at `0x044F` is outside the write range.

## Exact sequence

```text
F2 pre-read:
02 F2 49 04 06 00 00 00 00 00 00 00 00 00 00 00

F3 Blue → Red:
02 F3 49 04 06 00 00 00 FF 00 00 01 00 01 00 00

wait 10 ms

lighting F1:
02 F1 02 04 00 00 00 00 00 00 00 00 00 00 00 00

wait 10 ms

F2 read-back of the complete six-byte prefix
```

Success requires exact read-back `FF 00 00 01 00 01`. There is no F5,
arbitrary RGB, generic sender, retry, or restoration.

## Commands

Dry run:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_colour \
  --dry-run --blue-to-red
```

First live experiment, to be run by the user:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_colour \
  --live-led-colour-write \
  --blue-to-red \
  --acknowledge-device-write
```

Required confirmation:

```text
WRITE 0449 BLUE TO RED
```

Red → Blue dry run:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_colour \
  --dry-run --red-to-blue
```

Red → Blue live command:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_colour \
  --live-led-colour-write \
  --red-to-blue \
  --acknowledge-device-write
```

Required reverse confirmation:

```text
WRITE 0449 RED TO BLUE
```

The direction flags remain mutually exclusive and cannot chain transitions.

## Official Green ↔ Official Blue

Only persistent Green `00 FF 01` is supported. Transient preview `02 B5 34`
is neither an enum value nor an accepted report payload.

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_colour \
  --dry-run --green-to-blue
```

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_colour \
  --live-led-colour-write \
  --green-to-blue \
  --acknowledge-device-write
```

Required confirmation:

```text
WRITE 0449 GREEN TO BLUE
```

Exact F3:

```text
02 F3 49 04 06 00 00 00 0E 0F FF 01 00 01 00 00
```

Blue → Green dry run and live command:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_colour \
  --dry-run --blue-to-green
```

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_led_colour \
  --live-led-colour-write \
  --blue-to-green \
  --acknowledge-device-write
```

Required confirmation:

```text
WRITE 0449 BLUE TO GREEN
```

Exact Blue → Green F3:

```text
02 F3 49 04 06 00 00 00 00 FF 01 01 00 01 00 00
```

The operation uses the same 10 ms delays, exact lighting F1, and mandatory
six-byte read-back. Persistence remains pending after immediate read-back.

## Separate known state

`usb-captures/device-state/known_led_colour_state.json` stores the colour range
`0x0449–0x044B`, complete six-byte prefixes, RGB tuples, semantic names, and
append-only transitions. It does not modify brightness or LED-mode history.

Immediate read-back creates a pending transition. Confirm persistence later
from a complete saved live read, without accessing hidraw:

```bash
PYTHONPATH=src python3 -m venus_profile.known_led_colour_state \
  confirm-persistence /path/to/completed/live-read
```
