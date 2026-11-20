# Guarded brightness write milestone

This milestone permits one semantic device change: Profile 1 brightness at
address `0x044F`, between Medium (`02`) and High (`03`). It is not a general
write transport.

The guarded transaction now binds to the shared `DeviceSession` through a
private named brightness capability. The session returns device identity only;
it never exposes a raw writer. Exact F3/F1 validation remains inside the
guarded brightness boundary.

## Evidence-backed sequence

The controlled Windows live-preview phase used eleven lighting F3 writes and
then the exact `02 F1 02 04 ...` lighting finalisation. It did not use F5.
The full Apply sequence later used other category finalisers and F5, but those
belong to the broader Apply operation. This milestone therefore uses the
smallest sequence supported by the lighting preview:

1. validated F2 pre-read of `0x044F`;
2. one exact 16-byte F3 write of `0x044F`;
3. wait 10 ms;
4. exact `02 F1 02 04 ...` lighting finalisation;
5. wait 10 ms;
6. validated F2 read-back of `0x044F`.

The capture's successive F3 submissions were 8.095–22.450 ms apart, with a
9.023 ms median. Ten milliseconds is the conservative rounded delay used here.
The post-finalisation F2 verification is an added fail-closed safety check; the
Windows capture did not perform a read-back.

F5 is deliberately absent. The first guarded High→Medium operation was
immediately verified and remained Medium after a full USB unplug/replug cycle.
This confirms that the F3 + lighting F1 sequence persists without F5.

## Offline commands

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_brightness
PYTHONPATH=src python3 -m venus_profile.guarded_brightness --dry-run
PYTHONPATH=src python3 -m venus_profile.guarded_brightness --safety-audit
```

These modes do not discover or open hidraw.

## First live command

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_brightness \
  --live-brightness-write --high-to-medium \
  --acknowledge-device-write
```

The selected hidraw node must already be read/write accessible. The program
then requires the exact typed confirmation `WRITE 044F 03 TO 02`. There is no
noninteractive bypass, retry, or automatic restoration.

The separately explicit restoration command is:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_brightness \
  --live-brightness-write --medium-to-high \
  --acknowledge-device-write
```

It requires `WRITE 044F 02 TO 03`. The two directions are mutually exclusive;
there is no chained toggle operation.

## Known-device state

Confirmed Linux transitions are stored separately from immutable Windows
capture evidence in:

`usb-captures/device-state/known_device_state.json`

An exact immediate read-back adds a history entry and active override with
power-cycle persistence marked `pending`. Persistence is confirmed only by an
explicit offline command against a later complete live-read directory:

```bash
PYTHONPATH=src python3 -m venus_profile.known_device_state \
  confirm-persistence /absolute/path/to/venus_read_DIRECTORY
```

Other offline state commands are:

```bash
PYTHONPATH=src python3 -m venus_profile.known_device_state show
PYTHONPATH=src python3 -m venus_profile.known_device_state validate
PYTHONPATH=src python3 -m venus_profile.known_device_state \
  compare-live-read /absolute/path/to/venus_read_DIRECTORY
```

These commands read JSON artefacts only and contain no hidraw discovery or
device-access code.
