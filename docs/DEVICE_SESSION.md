# Venus device-session architecture

`venus_profile.device_session.DeviceSession` is the shared foundation for
device discovery, identity validation, permission metadata, F2 reads, complete
known-plan reads, lifecycle management, timestamped output directories, and
ownership warnings.

## Public session API

- `DeviceSession.discover()`
- `describe()`
- `check_access()`
- `open_read_session()`
- `read_memory(address, length)`
- `read_full_known_plan()`
- `close()`
- context-manager entry/exit

Opening remains lazy: discovery, description, permission checks, and
`open_read_session()` do not open hidraw. The descriptor is first opened by an
actual validated F2 read. Closing is idempotent, occurs on context-manager
failure paths, and permanently rejects reuse.

The session accepts only `04D9:FC55`, interface 02, usage page `FFA0`, usage 1.
It never assumes a hidraw number and contains no libusb claim, detach, reset,
or re-enumeration path.

## Privilege separation

The ordinary session delegates reads to the existing F2-only hidraw transport:

- outgoing Report 2 is exactly the validated F2 request;
- responses are Report 2 or Report 3 only;
- there is no public report sender or `write_memory`.

The guarded brightness and guarded LED-mode modules request distinct private
identity capabilities. Those capabilities return no sender. Brightness still
constructs and submits reports inside its separate private boundary, which
accepts only:

- exact `0x044F` F3 with value Medium (`02`) or High (`03`);
- exact lighting `F1 02 04`;
- validated F2 pre-read and read-back.

There is no generic public writer object. The GUI must call semantic operations
such as “set Profile 1 brightness Medium”, never construct addresses, command
bytes, or reports.

The LED-mode guard uses the ordinary F2 session for its exact six-byte
pre-read. It opens its separate private write-capable fd only after the source
prefix matches. That boundary accepts only the captured `0x0449–0x044E`
prefixes and exact lighting `F1 02 04`. Both directions are exposed as
mutually exclusive semantic operations.

The separate LED-colour experiment follows the same privilege boundary but
has its own internal identity capability and state namespace. Its private
writer opens only after exact source prefix `0E 0F FF 01 00 01` is read. It
accepts only the captured official Red/Blue and persistent Green prefixes,
preserves suffix `01 00 01`, and exposes Red↔Blue and persistent Green↔Blue
as mutually exclusive operations. Transient `02 B5 34` remains unsupported.

## Lighting support matrix

| Semantic control | Read support | Write support | Device-memory mapping |
|---|---|---|---|
| Profile 1 brightness | Proven | Medium/High proven persistent | `0x044F` confirmed |
| Brightness Low/Off | Meaning known | Not authorised | Same byte meaning known; live write unproven |
| Profile 1 LED mode | Proven for Breathing, Full Brightness, and true Off | Breathing↔Full persistent; Off→Full recovery guard implemented offline; Full→Off disabled | Six-byte prefix `0x0449–0x044E`; mode at `0x044C` |
| Breathing speed | PFD semantics only | None | Unresolved |
| Profile 1 colour | Native direct RGB proven visibly and after power cycle | Exact palette guards proven; corrected `#0000FF`→`#FF0000` recovery guard implemented offline | Three bytes at `0x0449–0x044B` are `RR GG BB` |
| Finite 20-colour visual diagnostic | Per-step F2/read-back proven by the guarded transport design | Diagnostic only; not a persistence writer and no normal transitions | Fixed direct-RGB plan at `0x0449–0x044E` |

`lighting_state.py` reports Profile 1 LED mode at `0x044C`. Controlled native
pre/post reads prove device raw `02` means Breathing, raw `01` means Full
Brightness, and raw `00` means true Off. This device byte is not a direct copy
of the PFD enumeration (`0=Breathing`, `1=Full Brightness`, `5=Off`). In true
Off the companion `0x044D` and trailing `0x044E` are both `00`; the colour is
preserved. The adjacent byte `0x044D` correlates with PFD `BreathState1`, but
its independent speed semantics remain unresolved.
Breathing speed retains a null device address. Colour reads now expose three
direct `R G B` bytes at `0x0449–0x044B`: controlled red `FF 00 00` and blue
`0E 0F FF`, plus controlled green `00 FF 01`. The field is consistently direct
RGB in device memory. The red device value differs from the known PFD red
COLORREF, so arbitrary PFD-to-device conversion remains unresolved. Only exact
persistent Green and proven Blue are available for Green↔Blue guarded
operations; arbitrary RGB remains disabled.

The controlled Windows custom-picker experiment adds an important distinction:
the entered RGB `#7B2DD2` persisted as device bytes `D2 2D 7B`. The custom
picker therefore uses a BGR channel transform rather than universally storing
semantic RGB byte order. This confirms arbitrary 24-bit colour-field storage
but does not enable a native arbitrary-colour writer.

A second asymmetric custom sample confirms the general conversion:
`#12A7E4` persisted as `E4 A7 12`. Custom values therefore use semantic
`#RRGGBB` → device `BB GG RR`. Captured palette values remain explicit
exceptions and are not reinterpreted by this rule. The evidence is sufficient
to design a future guarded arbitrary-colour capability, but no such live
capability exists yet.

The custom-colour guard has a separate internal session capability and state
namespace. Its corrected private live boundary accepts only recovery target
`#FF0000`, generated internally as direct device RGB `FF 00 00`, from exact
current blue prefix `00 00 FF 01 00 01`.
The semantic inspection model advertises no general arbitrary mode values;
only the captured guarded prefixes are constructible. True Off → Full
Brightness is the additional recovery guard; Full Brightness → Off remains
disabled.

The finite visual diagnostic is intentionally separate from that support
matrix: it uses a hard-coded 20-colour plan, requires explicit operator input
before and after each step, and records one diagnostic event. It does not
claim power-cycle persistence for each colour.

Offline inspection:

```bash
PYTHONPATH=src python3 -m venus_profile.inspect_lighting_state
PYTHONPATH=src python3 -m venus_profile.inspect_lighting_state \
  --live-read-directory /path/to/completed/venus_read_DIRECTORY
```

Both forms produce JSON and never discover or open hidraw.

## Diagnostics and ownership

Metadata-only diagnostics:

```bash
PYTHONPATH=src python3 -m venus_profile.device_diagnostics
```

This discovers the matching sysfs/hidraw metadata, checks owner/group/mode and
effective access, reports whether `venus-mouse` is active, and compares the
readable installed/packaged udev rules. It performs no HID operation.

With the installed udev rule, live tools should be run as the normal user.
Running through sudo can create root-owned state and result directories.
The live CLIs warn about root-owned paths and sudo invocation; they never
silently change ownership. Existing root-owned paths should be reviewed and
corrected administratively before normal-user operation.

Future live settings must be introduced as individual semantic capabilities,
each backed by controlled capture evidence, exact validators, preconditions,
read-back, mocks, and an explicit user-facing operation.

Finite visual-cycle diagnostics are separate from ordinary guarded-write
history. A completed run, or an aborted run with an exact confirmed last
colour, may establish the current diagnostic colour without fabricating a
normal transition. Diagnostic counts, RGB/prefix fields, and read-back status
are validated strictly; malformed or contradictory events fail closed.

Rainbow is currently read-only: raw mode `02` is prefix-dependent. Breathing
uses `02 05 01`, while the controlled Rainbow capture uses `02 00 03` at
`0x044C–0x044E`; RGB and brightness remain preserved. The guarded
Rainbow → Full Brightness recovery is a separate exact-prefix capability; it
does not expose a generic mode writer.
