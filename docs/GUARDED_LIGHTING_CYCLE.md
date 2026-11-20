# Guarded lighting cycle

The prototype includes one explicit opt-in guarded lighting writer for the
validated Venus configuration interface. It is a finite verification cycle,
not a general-purpose setting-write API.

## Offline default

From `linux-suite-prototype/`:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_lighting_cycle
PYTHONPATH=src python3 -m venus_profile.guarded_lighting_cycle --dry-run
PYTHONPATH=src python3 -m venus_profile.guarded_lighting_cycle --safety-audit
```

These modes do not discover, open, or communicate with a hidraw device. The
default command prints an offline-mode message and exits.

## Fixed guarded operation

The user-run live command requires both explicit flags:

```bash
PYTHONPATH=src python3 -m venus_profile.guarded_lighting_cycle \
  --live-lighting-cycle --acknowledge-device-writes
```

The command then performs its own exact identity and read/write permission
checks before discovery output and execution. It is intentionally left for the
user to run manually; automated tooling must not run it.

The immutable seven-stage plan is:

1. Wave — `7b 2d d2 03 00 01`
2. Flash — `7b 2d d2 04 05 01`
3. Breathing Low — `7b 2d d2 02 08 01`
4. Breathing Medium — `7b 2d d2 02 05 01`
5. Breathing High — `7b 2d d2 02 02 01`
6. Breathing UI Full Brightness — `7b 2d d2 02 00 01`
7. Restore Full Brightness — `7b 2d d2 01 00 01`

Execution requires the starting prefix `7b 2d d2 01 00 01`. Each stage uses
an F2 pre-read of `0x0449–0x044E`, one exact Report-2 F3 write, the fixed
lighting F1 finalisation report, 10 ms delays between commands, and an exact
six-byte immediate read-back. Only the fixed `0x0449–0x044E` prefix is written;
brightness `0x044F`, F5, and Reports 3–5 writes are excluded.

Every stage also requires user pacing and a visual result. Aborts, incorrect
visual results, exceptions, and Ctrl+C trigger one guarded restoration attempt
and produce timestamped TSV, JSON, and Markdown audit files.

Immediate read-back confirms the command result only. It does not establish
power-cycle persistence; generated records explicitly retain persistence as
`not-established`.

