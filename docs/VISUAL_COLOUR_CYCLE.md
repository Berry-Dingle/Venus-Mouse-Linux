# Finite 20-colour visual verification

`venus_profile.visual_colour_cycle` is a deliberately finite, user-paced
diagnostic. It contains one immutable list of 20 semantic `#RRGGBB` values
and writes direct native RGB bytes (`RR GG BB`) at `0x0449–0x044E` only.

It is never automatic: the operator must type `RUN 20 COLOUR VISUAL TEST`,
press Enter before every write, inspect the mouse, answer `Y`, `N`, or `SKIP`,
and press Enter before advancing. `ABORT` and Ctrl+C stop without retry or
restoration. The only delays are the protocol's two 10 ms waits.

Command responses `Y`, `N`, `SKIP`, and `ABORT` are case-insensitive. Empty
input is accepted only at the pre-write and post-observation Enter gates;
invalid answers are re-prompted rather than treated as an abort.

Dry run (no discovery or hidraw access):

```bash
PYTHONPATH=src python3 -m venus_profile.visual_colour_cycle --dry-run
```

The guarded live invocation is intentionally explicit and requires the
existing write acknowledgement and the typed confirmation. It must be run by
the user, not by automated tooling:

```bash
PYTHONPATH=src python3 -m venus_profile.visual_colour_cycle \
  --live-visual-test --acknowledge-device-write
```

Each run creates a timestamped directory below
`usb-captures/visual-colour-cycles/`. A run records transport/read-back and
visual observations, but does not append twenty normal colour transitions.
It records one diagnostic event in `known_custom_colour_state.json`; the last
exactly confirmed colour becomes the current diagnostic state and remains
power-cycle-pending.

This is a diagnostic capability, not persistence validation and not a colour
picker. No caller-supplied list, raw bytes, arbitrary address, F5, Report 3/4/5
write, brightness write, or generic writer is exposed.
