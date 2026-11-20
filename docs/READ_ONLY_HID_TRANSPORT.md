# Read-only hidraw transport

This milestone implements only the validated Venus F2 memory-read exchange.
It does not implement profile selection, Apply, preview, finalisation, macro
upload, setting writes, F1, F3, or F5.

The low-level transport is now lifecycle-managed by
`venus_profile.device_session.DeviceSession`, which also centralises exact
device identity, access metadata, and the captured 193-read plan. The original
CLI and its opt-in behaviour remain unchanged.

## Mechanism

The transport uses the Linux hidraw feature-report ioctls directly through
Python's standard-library `fcntl.ioctl`:

- `HIDIOCSFEATURE(16)` sends the one permitted 16-byte report-2 F2 request;
- `HIDIOCGFEATURE(16)` retrieves short report-2 responses;
- `HIDIOCGFEATURE(64)` retrieves report-3 responses.

The report ID remains byte zero. No USB library is used, so the implementation
does not claim interfaces, detach kernel drivers, reset the device, or
re-enumerate it.

## Discovery

`/sys/class/hidraw` is enumerated by name. A candidate must resolve to:

- `idVendor=04d9`;
- `idProduct=fc55`;
- `bInterfaceNumber=02`.

Interfaces 00 and 01 are rejected rather than used as fallbacks. If a sysfs
`report_descriptor` is readable, its application collection must be usage
page `0xFFA0`, usage `1`. Missing descriptor access is reported as unverified;
a readable mismatching descriptor rejects the candidate. Zero or multiple
matches are errors.

## Safety boundary

The live class exposes only these feature operations:

- `get_feature_report(report_id, length)`;
- `read_memory(address, length)`.

Lifecycle methods and the last-response logging property do not send reports.
There is no public generic SET_REPORT or memory-write API.

Before lazy device opening, every outgoing request must satisfy:

- report ID 2;
- command F2;
- total size 16;
- bytes 6–15 all zero;
- positive data length no larger than one report-3 response;
- address and complete request inside the 16-bit address space.

Only report 2/16-byte and report 3/64-byte GET_FEATURE calls are accepted.
Reports 4 and 5 are rejected. Responses are rejected on an unexpected ID,
status, transformed address, length, marker, or insufficient returned bytes.

## Commands

From `linux-suite-prototype/`, offline validation is the default:

```bash
PYTHONPATH=src python3 -m venus_profile.read_device_memory
```

Static safety audit:

```bash
PYTHONPATH=src python3 -m venus_profile.read_device_memory --safety-audit
```

Explicit interactive live read:

```bash
PYTHONPATH=src python3 -m venus_profile.read_device_memory --live-read
```

The program discovers and prints the selected node and interface before asking
the user to type exactly `READ`. For a previously reviewed scripted run,
`--yes-read-only` skips only that prompt; it does not broaden the protocol.

Live output is placed in a new timestamped subdirectory of
`usb-captures/live-reads/`. Existing directories or files are never replaced.

Live comparisons retain the Windows-captured memory as an immutable baseline
and then apply the validated
`usb-captures/device-state/known_device_state.json` overlay. Reports list the
number of expectations from each authority. A malformed, conflicting, or
wrong-device overlay is a hard error; a missing overlay is allowed for a new
workspace. Offline factory-fixture validation does not use the overlay.

Feature SET ioctls require opening the hidraw node read/write even though the
protocol operation is logically a read request. Whether root is needed depends
on the current node ACL/group permissions. No udev rule is installed by this
prototype.
