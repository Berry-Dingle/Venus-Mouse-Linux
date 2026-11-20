# Limited guarded button-assignment writer

This boundary supports one profile, one button, and one exact allowlisted
assignment per operation. It sends one F3 and verifies the immediate four-byte
readback. It has no finalizer report, bracket report, retry, bulk write,
automatic restore, generic sender, or persistence claim.

## Stable assignment IDs

`button_assignment_write.py` derives IDs from complete raw records:

```text
record-00000000  = 00 00 00 00
record-90000400  = 90 00 04 00
record-90001E00  = 90 00 1E 00
```

`confirmed_assignment_id()` and `confirmed_assignment_from_id()` use the
decoder allowlist as their only source of truth. IDs are canonical uppercase
hexadecimal, exactly eight hex digits after `record-`. Labels are display text
only; duplicate labels do not identify records.

## Exact F3 report

The existing offline serializer produces the only write report:

```text
02 F3 <address little-endian> 04 00 00 00 <four raw bytes> 00 00 00 00
```

The address is calculated from the validated profile `1..5` and button `1..20`.
No caller-supplied address, raw record, payload, or report is accepted.

## Write CLI and flow

```text
python3 -m venus_profile.guarded_button_assignment \
  --write-assignment --profile 1 --button 7 \
  --assignment-id record-90000400 --acknowledge-device-write
```

Live modes require the exact device identity, read/write permission, and an
interactive terminal. The writer reads and allowlist-validates the current
four-byte record, requires it to differ from the selected target, creates a
new evidence directory, prints the complete source/target summary, and requires
exact case-sensitive `CONTINUE`. It then sends one serializer-produced F3 and
immediately reads the calculated address again. Any mismatch or exception
fails closed without retry.

## Restore CLI and flow

```text
python3 -m venus_profile.guarded_button_assignment \
  --restore-from-evidence /absolute/write-evidence-directory \
  --acknowledge-device-write
```

Restore validates schema, device identity, profile, button, calculated address,
source and target IDs, raw bytes, labels, and allowlist membership. It requires
the current record to equal the exact saved target, then sends one F3 restoring
the exact saved source and verifies the immediate readback. Duplicate-label
records are preserved through `confirmed_assignment_raw()` and never rebuilt
from labels.

Neither operation sends F1 or F5, retries, or restores automatically. Immediate
readback is not persistence confirmation across reconnect, application restart,
profile switching, or power loss.

## Evidence

New evidence is written under:

```text
usb-captures/live-button-assignment-writes/
  button_assignment_p<profile>_b<button>_<UTC timestamp>/
```

Each directory contains `operation.json`, `prewrite_read.json`,
`postwrite_read.json`, `reports_sent.tsv`, and `result.md`. It records exact
raw bytes, stable IDs, labels, profile/button/address, identity, summary,
confirmation, report, readback, final result, and the fixed safety flags:
`automatic_retry=false`, `automatic_restoration=false`, `f1_sent=false`,
`f5_sent=false`, and `persistence=not-established`. Existing directories are
never overwritten.

## Evidence boundary

The one-F3/immediate-read sequence is directly live-tested at Profile 1,
Button 7, address `0x009A`. All other profile/button addresses are supported
by the confirmed address model, offline tests, and the same serializer, but
have not individually undergone live validation. This is guarded rollout
support, not equivalent per-address live evidence.

Historical fixed-experiment and duplicate-label incident evidence is retained
as evidence only. The temporary incident repair path was removed after it
successfully restored `90 00 1E 00`; the permanent protection is exact raw
record restoration.
