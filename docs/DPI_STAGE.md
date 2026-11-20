# Selected DPI stage (read-only)

The saved controlled live-read campaign confirms that device-memory address
`0x0042` stores the selected DPI-stage index as a zero-based byte.

| Observable indicator state | `0x0042` |
|---:|---:|
| 0 lights | `00` |
| 1 light | `01` |
| 2 lights | `02` |
| 3 lights | `03` |
| 4 lights | `04` |

The five reads were taken from:

```text
usb-captures/live-reads/venus_read_20260728T092859.409571Z
usb-captures/live-reads/venus_read_20260728T092914.377530Z
usb-captures/live-reads/venus_read_20260728T092928.246118Z
usb-captures/live-reads/venus_read_20260728T092944.934143Z
usb-captures/live-reads/venus_read_20260728T093002.332739Z
```

Only `0x0042` changed between adjacent saved maps. The sequence was exactly
`00, 01, 02, 03, 04`, with one DPI+ operation between reads.

## Correspondence with `.pfd`

Existing `.pfd` evidence defines the selected `DPI` field as a zero-based
value from 0 through 4, exposed by the profile API as one-based stages 1
through 5. The live byte at `0x0042` has the same confirmed zero-based domain.
This documents a correspondence between the live-memory byte and the existing
`.pfd` selected-stage representation; it does not change `.pfd` parsing or
serialization semantics.

## Implementation boundary

`venus_profile.dpi_stage` validates and decodes supplied saved bytes only. It
does not discover devices, open HID nodes, send reports, write memory, build
reports, or claim persistence. The mapping is confirmed for the tested Venus
device/protocol. No claim is made for other firmware, hardware variants, or
power-cycle behavior.

User observation (2026-11-19, physical mouse): the selected DPI stage
survives a power cycle, and DPI± stops at stages 1 and 5 without wrapping.
The stage byte in memory appears to lag a DPI press by roughly 20–30 s
(estimated, not measured). See `investigation/LIVE_EVENT_FINDINGS.md` §9.
