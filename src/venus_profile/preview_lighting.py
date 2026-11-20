"""One-shot live lighting write, for the GUI (helper process).

The GUI never opens the device. When the user changes lighting, it launches
this module (``python -m venus_profile.preview_lighting``) and writes one JSON
request to its stdin: semantic values only (profile, RGB, mode, speed,
brightness), parsed strictly by ``lighting_preview_plan.parse_request``. The
caller cannot supply an address, length, raw bytes or report.

One transaction, then exit (``docs/LIVE_LIGHTING_PREVIEW_DESIGN.md`` §4):

1. discover the one Venus configuration interface with the existing exact
   identity checks (``DeviceSession``: 04D9:FC55, interface 02, FFA0/1);
2. F2 pre-read of the five lighting reads from the captured read plan
   (``0x0449 + 8k``, 7 bytes), each repeated until two consecutive replies
   agree (at most 4; otherwise ``write-failed``, nothing written);
3. refuse unless every requested profile's current lighting has an evidenced
   mode suffix and brightness; if nothing differs, exit ``unchanged``;
4. only then open the write-capable fd; one exact Report-2 F3 per changed
   record (6 bytes) or brightness (1 byte), each followed by 10 ms;
5. the exact lighting finalisation ``02 F1 02 04``, then 10 ms;
6. F2 read-back of the same five reads (same agreement rule): written bytes
   must match and every other byte must equal the pre-read.

A request ``{"version": 1, "active_profile": N}`` instead switches the
mouse's active profile (``investigation/LIVE_EVENT_FINDINGS.md`` §10): stable
read of ``0x002C`` (range guard only) → F3 ``02 F3 2C 00 02 00 00 00 (N-1) 00 …`` →
10 ms → ``02 F1 02 01``, ``02 F1 02 04``, ``02 F1 02 02``, ``02 F1 02 08`` (10 ms
after each; ``hid.exe`` 0x410cf0) → stable read-back must equal ``N-1``.

A request ``{"version": 1, "polling": [p1..p5]}`` sets the polling rates
(``docs/APPLY_HARDWARE_TESTS.md`` T3, ``hid.exe`` 0x410230): stable reads of
the read-plan requests ``0x0032``/5 and ``0x0038``/3 (refused unless every
value is 8/4/2/1 and every read companion is 00) → F3 ``0x0032``
``[p1 00 p2 00 p3 00]`` → F3 ``0x0038`` ``[p4 00 p5 00]`` → ``02 F1 02 08``
(10 ms after each) → stable read-back must equal the request.

A request ``{"version": 1, "dpi": [{"profile", "stage", "raw"}, ...]}`` sets
DPI stage values (``APPLY_HARDWARE_TESTS.md`` T4): stable read of each
involved profile's read-plan block (base+2, 32 bytes) → refused unless each
requested stage holds a known enabled record → one F3 per changed 5-byte
record ``01 lookup selector 00 00`` → ``02 F1 02 10`` → ``02 F1 02 02`` (10 ms
after each) → stable read-back of the blocks must equal the expected bytes
(other stages and the start-up stage byte unchanged).

A request ``{"version": 1, "dpi_stage": [{"profile", "stage"}, ...]}`` sets the
DPI start-up stage (``APPLY_HARDWARE_TESTS.md`` T5; zero-based 0-4): stable read
of ``base+2``/2 per profile (refused unless ``[0-4, 00]``) → F3 ``base+2``
``[stage 00]`` per changed profile → ``02 F1 02 02`` → ``02 F1 02 10`` (10 ms
after each; hid.exe 0x40FC60) → stable read-back must equal the request.

A request ``{"version": 1, "buttons": [{"profile", "button", "record"}, ...]}``
sets button records (``APPLY_HARDWARE_TESTS.md`` T1/Stage 1): every record must
have a shape the decoder recognises (``button_record_is_known``) and goes to a
fixed address (5 profiles × 20 buttons); stable read of each record → refused
unless each current record is recognised → one 4-byte F3 per changed record
(10 ms after each), **no finaliser** (T1: persists without; Windows' button
path sends none) → stable read-back of every requested record.

A request ``{"version": 1, "macros": [{"slot", "body"}, ...]}`` uploads device
macro slots (Stage 3): each body must be a known action list
(``macro_body_is_known``); stable read of each slot (read plan: 4 x 50 bytes)
→ refused unless each current body is recognised → per changed slot
``02 F5 00``, 100 ms, Report 4 ``04 F3 <addr> C8 00 00 00`` + body + 48 x 00,
10 ms, ``02 F5 01``, 100 ms (as ``hid.exe`` 0x4142b0) → stable read-back.

A request ``{"version": 1, "scroll": [p1..p5]}`` sets wheel scroll lines
(T6): stable read of ``0x0020``/10 → refused unless every value is 1-63 with
00 companions → Report 3 ``03 F3 20 00 0A 00 00 00`` + ``p 00`` x5 + zeros →
``02 F1 02 04``, ``02 01``, ``02 02``, ``02 08`` (10 ms after each) → read-back.

F5 only as ``02 F5 00`` / ``02 F5 01`` around a macro upload; no other report
IDs, no retry, no automatic restoration, no files, no options. Every outgoing write report passes ``validate_preview_report``
immediately before the ioctl. Immediate read-back confirms the command
result only; it is not a persistence claim. ``snapshot_audit.
preview_lighting_safety_audit`` checks these rules statically.

Run only through the GUI by the user (live device access); tests inject fakes.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Callable, Protocol

from .device_session import DeviceSession, DeviceSessionError, _LIGHTING_PREVIEW_CAPABILITY
from .lighting_preview_plan import (
    ACTIVE_PROFILE_ADDRESS, SCROLL_ADDRESS, SCROLL_LENGTH, SCROLL_MAX, SCROLL_MIN, SCROLL_REPORT_LENGTH,
    scroll_from_read, MACRO_BODY_LENGTH, MACRO_READ_LENGTH, MACRO_READS_PER_SLOT, MACRO_REPORT_LENGTH,
    FACTORY_SLOT_FILLER, MACRO_SLOT_COUNT, macro_body_is_known, macro_slot_address, BUTTON_COUNT, BUTTON_OFFSETS, BUTTON_PROFILE_BASES, BUTTON_RECORD_LENGTH,
    button_record_address, button_record_is_known, BRIGHTNESS_ADDRESSES, BRIGHTNESS_LEVELS, DPI_PROFILE_BASES, DPI_READ_LENGTH,
    DPI_READ_OFFSET, DPI_RECORD_LENGTH, LIGHTING_READ_LENGTH, MAX_REQUEST_BYTES, dpi_raw_from_record, dpi_record,
    dpi_record_address, DPI_STAGE_COUNT, DPI_STAGE_SELECT_LENGTH, dpi_stage_select_address,
    dpi_stage_select_from_read,
    POLLING_ENUMS, POLLING_READS, POLLING_WRITE_ADDRESSES, PROFILE_COUNT, RECORD_ADDRESSES, RECORD_LENGTH,
    LightingRequestError, LightingTarget, lighting_is_known, parse_any_request, polling_from_reads,
    suffix_is_known,
)
from .readonly_hidraw import (
    REPORT2_LENGTH, HidrawDiscoveryError, HidrawPermissionError, HidrawProtocolError, _HidrawFeatureIo,
    _hid_iocsfeature,
)

RESULT_VERSION = 1
INTER_COMMAND_DELAY_SECONDS = 0.010
#: Each lighting read is repeated until two consecutive replies agree. Live use
#: (2026-11-19) showed the mouse occasionally answering a read of an odd
#: address with the data one byte earlier; its reply header echoes the even
#: address either way (``address & ~1``), so the shift cannot be seen there.
MAX_READS_PER_RECORD = 4
LIGHTING_FINALISATION_REPORT = bytes.fromhex("02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00")
#: hid.exe 0x410140: after writing 0x002C it sends F1 02 01.
PROFILE_FINALISATION_REPORT = bytes.fromhex("02 f1 02 01 00 00 00 00 00 00 00 00 00 00 00 00")
#: hid.exe 0x410cf0 (the handler for ldcfg's profile-tab message 0x4E2) then
#: sends F1 02 04, F1 02 02, F1 02 08 (the same tail as every captured Apply).
#: Live test 2026-11-19: 0x002C + F1 02 01 alone did not switch the profile.
PROFILE_APPLY_REPORT_02 = bytes.fromhex("02 f1 02 02 00 00 00 00 00 00 00 00 00 00 00 00")
PROFILE_APPLY_REPORT_08 = bytes.fromhex("02 f1 02 08 00 00 00 00 00 00 00 00 00 00 00 00")
PROFILE_SWITCH_SEQUENCE = (PROFILE_FINALISATION_REPORT, LIGHTING_FINALISATION_REPORT,
                           PROFILE_APPLY_REPORT_02, PROFILE_APPLY_REPORT_08)
#: Windows Apply sends F1 02 10 right after its DPI stage writes (hid.exe
#: 0x413646); 0x40fb00 sends F1 02 02 when used on its own. T4 sends both.
DPI_FINALISATION_REPORT_10 = bytes.fromhex("02 f1 02 10 00 00 00 00 00 00 00 00 00 00 00 00")
DPI_SEQUENCE = (DPI_FINALISATION_REPORT_10, PROFILE_APPLY_REPORT_02)
#: T5 start-up stage: hid.exe 0x40FC60 sends F1 02 02 then F1 02 10 after its
#: per-profile base+2 writes (same order in the saved Apply capture).
DPI_STAGE_SEQUENCE = (PROFILE_APPLY_REPORT_02, DPI_FINALISATION_REPORT_10)
_DPI_STAGE_SELECT_ADDRESSES = frozenset(dpi_stage_select_address(profile) for profile in range(1, PROFILE_COUNT + 1))
_DPI_RECORD_ADDRESSES = frozenset(base + 4 + 6 * index for base in DPI_PROFILE_BASES for index in range(5))
_BUTTON_ADDRESSES = frozenset(base + offset for base in BUTTON_PROFILE_BASES for offset in BUTTON_OFFSETS)
#: Macro uploads (Stage 3): Windows brackets each Report-4 slot upload with F5 00
#: and F5 01 and sleeps 100 ms after each (hid.exe 0x4142b0 / 0x410420).
MACRO_BEGIN_REPORT = bytes.fromhex("02 f5 00 00 00 00 00 00 00 00 00 00 00 00 00 00")
MACRO_END_REPORT = bytes.fromhex("02 f5 01 00 00 00 00 00 00 00 00 00 00 00 00 00")
MACRO_BRACKET_DELAY_SECONDS = 0.100
_MACRO_ADDRESSES = frozenset(macro_slot_address(slot) for slot in range(MACRO_SLOT_COUNT))
#: Scroll lines (T6): Windows writes 0x0020 only inside Apply, before the tail
#: F1 02 04, 02 01, 02 02, 02 08; T6 sends that tail (all in every Apply).
SCROLL_SEQUENCE = (LIGHTING_FINALISATION_REPORT, PROFILE_FINALISATION_REPORT,
                   PROFILE_APPLY_REPORT_02, PROFILE_APPLY_REPORT_08)
_F3_REPORT2 = bytes.fromhex("02 f3")

STATUS_OK = "ok"
STATUS_UNCHANGED = "unchanged"
STATUS_REFUSED = "refused"
STATUS_MISMATCH = "mismatch"
STATUS_NO_DEVICE = "no-device"
STATUS_MULTIPLE = "multiple-devices"
STATUS_PERMISSION = "permission-denied"
STATUS_IDENTITY = "identity-mismatch"
STATUS_FAILED = "write-failed"
STATUSES = (STATUS_OK, STATUS_UNCHANGED, STATUS_REFUSED, STATUS_MISMATCH, STATUS_NO_DEVICE,
            STATUS_MULTIPLE, STATUS_PERMISSION, STATUS_IDENTITY, STATUS_FAILED)


class LightingWriteError(RuntimeError):
    """An outgoing report, pre-read guard or read-back check failed."""


# ----------------------------------------------------------------- exact reports

def build_record_report(profile: int, record: bytes) -> bytes:
    address = RECORD_ADDRESSES[profile - 1]
    report = (_F3_REPORT2 + bytes((address & 0xFF, address >> 8, RECORD_LENGTH, 0, 0, 0))
              + bytes(record) + bytes(2))
    validate_preview_report(report)
    return report


def build_brightness_report(profile: int, brightness: int) -> bytes:
    address = BRIGHTNESS_ADDRESSES[profile - 1]
    report = _F3_REPORT2 + bytes((address & 0xFF, address >> 8, 1, 0, 0, 0, brightness)) + bytes(7)
    validate_preview_report(report)
    return report


def build_profile_report(profile: int) -> bytes:
    """``02 F3 2C 00 02 00 00 00 pp 00 …``: 0x002C = profile-1, 0x002D = 00 (as hid.exe)."""
    report = (_F3_REPORT2 + bytes((ACTIVE_PROFILE_ADDRESS & 0xFF, ACTIVE_PROFILE_ADDRESS >> 8, 2, 0, 0, 0,
                                   profile - 1, 0)) + bytes(6))
    validate_preview_report(report)
    return report


def _validate_scroll_report(report: bytes) -> str:
    """Report 3: 03 F3 20 00 0A 00 00 00, five (lines 1-63, 00) pairs, then zeros."""
    if report[:8] != bytes((3, 0xF3, SCROLL_ADDRESS, 0, SCROLL_LENGTH, 0, 0, 0)):
        raise LightingWriteError("Report 3 header is not the scroll-lines write")
    values, companions = report[8:18:2], report[9:18:2]
    if any(not SCROLL_MIN <= v <= SCROLL_MAX for v in values) or any(companions) or any(report[18:]):
        raise LightingWriteError("scroll lines, companions or padding are not allowed")
    return "scroll-lines"


def build_scroll_report(values: tuple[int, ...]) -> bytes:
    data = b"".join(bytes((value, 0)) for value in values)
    report = (bytes((3, 0xF3, SCROLL_ADDRESS, 0, SCROLL_LENGTH, 0, 0, 0)) + data
              + bytes(SCROLL_REPORT_LENGTH - 8 - SCROLL_LENGTH))
    validate_preview_report(report)
    return report


def _validate_macro_report(report: bytes) -> str:
    """Report 4: 04 F3 <slot address> C8 00 00 00, a known 200-byte body, 48 zero bytes."""
    address = report[2] | (report[3] << 8)
    if report[0] != 4 or report[1] != 0xF3 or address not in _MACRO_ADDRESSES \
            or report[4:8] != bytes((MACRO_BODY_LENGTH, 0, 0, 0)):
        raise LightingWriteError("macro upload header is not a device slot write")
    if not macro_body_is_known(report[8:8 + MACRO_BODY_LENGTH]) or any(report[8 + MACRO_BODY_LENGTH:]):
        raise LightingWriteError("macro body or padding is not allowed")
    return f"macro-slot-{(address - _MIN_MACRO_ADDRESS) // 0xC8 + 1}"


_MIN_MACRO_ADDRESS = min(_MACRO_ADDRESSES)


def build_macro_report(slot: int, body: bytes) -> bytes:
    address = macro_slot_address(slot)
    report = (bytes((4, 0xF3, address & 0xFF, address >> 8, MACRO_BODY_LENGTH, 0, 0, 0)) + bytes(body)
              + bytes(MACRO_REPORT_LENGTH - 8 - MACRO_BODY_LENGTH))
    validate_preview_report(report)
    return report


def build_button_report(profile: int, button: int, record: bytes) -> bytes:
    address = button_record_address(profile, button)
    report = (_F3_REPORT2 + bytes((address & 0xFF, address >> 8, BUTTON_RECORD_LENGTH, 0, 0, 0))
              + bytes(record) + bytes(4))
    validate_preview_report(report)
    return report


def build_dpi_report(profile: int, stage: int, raw: int) -> bytes:
    address = dpi_record_address(profile, stage)
    report = (_F3_REPORT2 + bytes((address & 0xFF, address >> 8, DPI_RECORD_LENGTH, 0, 0, 0))
              + dpi_record(raw) + bytes(3))
    validate_preview_report(report)
    return report


def build_dpi_stage_report(profile: int, stage: int) -> bytes:
    """hid.exe 0x40FC60: 02 F3 <base+2> 02 00 00 00 <stage> 00 + padding."""
    address = dpi_stage_select_address(profile)
    report = (_F3_REPORT2 + bytes((address & 0xFF, address >> 8, DPI_STAGE_SELECT_LENGTH, 0, 0, 0, stage, 0))
              + bytes(6))
    validate_preview_report(report)
    return report


def build_polling_reports(values: tuple[int, ...]) -> tuple[bytes, bytes]:
    """hid.exe 0x410230: 0x0032 = p1 00 p2 00 p3 00, 0x0038 = p4 00 p5 00."""
    first_address, second_address = POLLING_WRITE_ADDRESSES
    first = (_F3_REPORT2 + bytes((first_address, 0, 6, 0, 0, 0, values[0], 0, values[1], 0, values[2], 0))
             + bytes(2))
    second = _F3_REPORT2 + bytes((second_address, 0, 4, 0, 0, 0, values[3], 0, values[4], 0)) + bytes(4)
    validate_preview_report(first)
    validate_preview_report(second)
    return first, second


def validate_preview_report(report: bytes) -> str:
    """Accept only the fixed templates; raise for anything else."""
    report = bytes(report)
    if len(report) == MACRO_REPORT_LENGTH:
        return _validate_macro_report(report)
    if len(report) == SCROLL_REPORT_LENGTH:
        return _validate_scroll_report(report)
    if len(report) != REPORT2_LENGTH:
        raise LightingWriteError("outgoing report must be 16 bytes (or a 64-byte scroll / 256-byte macro write)")
    if report == MACRO_BEGIN_REPORT:
        return "macro-begin"
    if report == MACRO_END_REPORT:
        return "macro-end"
    if report == LIGHTING_FINALISATION_REPORT:
        return "lighting-finalisation"
    if report == PROFILE_FINALISATION_REPORT:
        return "profile-finalisation"
    if report == PROFILE_APPLY_REPORT_02:
        return "profile-apply-02"
    if report == PROFILE_APPLY_REPORT_08:
        return "finalise-02-08"           # profile switch tail and polling (hid.exe 0x410230)
    if report == DPI_FINALISATION_REPORT_10:
        return "finalise-02-10"
    if report[:2] != _F3_REPORT2 or report[5:8] != bytes(3):
        raise LightingWriteError("outgoing report is not a Report-2 lighting F3")
    address, length = report[2] | (report[3] << 8), report[4]
    if address in _BUTTON_ADDRESSES and length == BUTTON_RECORD_LENGTH:
        if not button_record_is_known(report[8:12]) or report[12:] != bytes(4):
            raise LightingWriteError("button record shape or padding is not allowed")
        return "button-record"
    if address in _DPI_RECORD_ADDRESSES and length == DPI_RECORD_LENGTH:
        if dpi_raw_from_record(report[8:13]) is None or report[13:] != bytes(3):
            raise LightingWriteError("DPI stage record or padding is not allowed")
        return "dpi-stage-record"
    if address in _DPI_STAGE_SELECT_ADDRESSES and length == DPI_STAGE_SELECT_LENGTH:
        if report[8] >= DPI_STAGE_COUNT or report[9:] != bytes(7):
            raise LightingWriteError("DPI start-up stage value or padding is not allowed")
        return "dpi-start-up-stage"
    if address == POLLING_WRITE_ADDRESSES[0] and length == 6:
        values, companions = report[8:14:2], report[9:14:2]
        if any(v not in POLLING_ENUMS for v in values) or any(companions) or report[14:] != bytes(2):
            raise LightingWriteError("polling values, companions or padding are not allowed")
        return "polling-p1-p3"
    if address == POLLING_WRITE_ADDRESSES[1] and length == 4:
        values, companions = report[8:12:2], report[9:12:2]
        if any(v not in POLLING_ENUMS for v in values) or any(companions) or report[12:] != bytes(4):
            raise LightingWriteError("polling values, companions or padding are not allowed")
        return "polling-p4-p5"
    if address == ACTIVE_PROFILE_ADDRESS and length == 2:
        if report[8] >= PROFILE_COUNT or report[9:] != bytes(7):
            raise LightingWriteError("active profile value or padding is not allowed")
        return f"active-profile-p{report[8] + 1}"
    if address in RECORD_ADDRESSES and length == RECORD_LENGTH:
        if not suffix_is_known(report[11:14]) or report[14:] != bytes(2):
            raise LightingWriteError("lighting record has an unknown mode suffix or padding")
        return f"lighting-record-p{RECORD_ADDRESSES.index(address) + 1}"
    if address in BRIGHTNESS_ADDRESSES and length == 1:
        if report[8] not in BRIGHTNESS_LEVELS or report[9:] != bytes(7):
            raise LightingWriteError("brightness value or padding is not allowed")
        return f"lighting-brightness-p{BRIGHTNESS_ADDRESSES.index(address) + 1}"
    raise LightingWriteError(f"address 0x{address:04X}/length {length} is not a lighting field")


def plan_reports(current: dict[int, bytes], targets: tuple[LightingTarget, ...]) -> list[bytes]:
    """F3 reports for the fields that differ, in profile order (no F1)."""
    reports = []
    for target in sorted(targets, key=lambda item: item.profile):
        before, after = current[target.profile], target.lighting_bytes()
        if before[:RECORD_LENGTH] != after[:RECORD_LENGTH]:
            reports.append(build_record_report(target.profile, after[:RECORD_LENGTH]))
        if before[RECORD_LENGTH] != after[RECORD_LENGTH]:
            reports.append(build_brightness_report(target.profile, after[RECORD_LENGTH]))
    return reports


# ----------------------------------------------------------------- device I/O

class _WriteIo(Protocol):
    def submit(self, report: bytes) -> None: ...
    def close(self) -> None: ...


class _LightingWriteIo(_HidrawFeatureIo):
    """Write-capable fd that only ever sends a validated lighting template."""

    def submit(self, report: bytes) -> None:
        validate_preview_report(report)
        buffer = bytearray(report)                # 16 bytes, or 64 (scroll) / 256 (macro), validated
        result = self._ioctl(self._fd, _hid_iocsfeature(len(buffer)), buffer, True)
        if result != len(buffer):
            raise LightingWriteError(f"SET_FEATURE returned {result}, expected {len(buffer)}")


def _stable_read(session: DeviceSession, address: int, length: int = LIGHTING_READ_LENGTH) -> bytes:
    """One read, accepted only when two consecutive replies agree."""
    replies: list[bytes] = []
    for _ in range(MAX_READS_PER_RECORD):
        replies.append(session.read_memory(address, length))
        if len(replies) >= 2 and replies[-1] == replies[-2]:
            return replies[-1]
    raise LightingWriteError(
        f"unstable read at 0x{address:04X}: " + " / ".join(reply.hex(" ") for reply in replies))


def _read_lighting(session: DeviceSession) -> dict[int, bytes]:
    return {profile: _stable_read(session, address)
            for profile, address in enumerate(RECORD_ADDRESSES, 1)}


def _records_hex(values: dict[int, bytes]) -> dict[str, str]:
    return {str(profile): value.hex() for profile, value in values.items()}


def _result(status: str, message: str = "", **extra) -> dict[str, object]:
    return {"version": RESULT_VERSION, "status": status, "message": message, **extra}


def write_lighting(
    targets: tuple[LightingTarget, ...],
    session: DeviceSession,
    *,
    backend_factory: Callable = _LightingWriteIo,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """The guarded transaction on an already discovered session."""
    session.open_read_session()
    current = _read_lighting(session)
    for target in targets:
        if not lighting_is_known(current[target.profile]):
            return _result(STATUS_REFUSED,
                           f"profile {target.profile} lighting on the mouse "
                           f"({current[target.profile].hex(' ')}) is not a known mode; nothing written",
                           records=_records_hex(current))
    reports = plan_reports(current, targets)
    if not reports:
        return _result(STATUS_UNCHANGED, records=_records_hex(current))
    expected = dict(current)
    for target in targets:
        expected[target.profile] = target.lighting_bytes()
    session._request_internal_capability(_LIGHTING_PREVIEW_CAPABILITY)
    writer = backend_factory(session.device.path)
    try:
        for report in reports:
            writer.submit(report)
            sleeper(INTER_COMMAND_DELAY_SECONDS)
        writer.submit(LIGHTING_FINALISATION_REPORT)
        sleeper(INTER_COMMAND_DELAY_SECONDS)
    finally:
        writer.close()
    read_back = _read_lighting(session)
    written = [{"profile": t.profile, "before": current[t.profile].hex(), "after": read_back[t.profile].hex()}
               for t in sorted(targets, key=lambda item: item.profile)
               if current[t.profile] != expected[t.profile]]
    if read_back != expected:
        wrong = [str(p) for p in sorted(expected) if read_back[p] != expected[p]]
        return _result(STATUS_MISMATCH, f"read-back differs for profile(s) {', '.join(wrong)}; "
                       "no retry or restoration attempted",
                       records=_records_hex(read_back), expected=_records_hex(expected), written=written)
    return _result(STATUS_OK, records=_records_hex(read_back), written=written,
                   reports_sent=len(reports) + 1)


def write_active_profile(
    profile: int,
    session: DeviceSession,
    *,
    backend_factory: Callable = _LightingWriteIo,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Make ``profile`` (1-5) the mouse's active profile (0x002C, then the F1 sequence).

    Always sends the switch, even if ``0x002C`` already holds the target: the
    mouse's memory may lag a physical button press (as seen for the DPI stage
    byte), while the GUI only asks after the mouse's own events say the profile
    differs. The pre-read is a range guard only.
    """
    session.open_read_session()
    current = _stable_read(session, ACTIVE_PROFILE_ADDRESS, 1)[0]
    if current >= PROFILE_COUNT:
        return _result(STATUS_REFUSED, f"active-profile byte on the mouse is {current:#04x}; nothing written")
    report = build_profile_report(profile)
    session._request_internal_capability(_LIGHTING_PREVIEW_CAPABILITY)
    writer = backend_factory(session.device.path)
    try:
        writer.submit(report)
        sleeper(INTER_COMMAND_DELAY_SECONDS)
        for finalisation in PROFILE_SWITCH_SEQUENCE:
            writer.submit(finalisation)
            sleeper(INTER_COMMAND_DELAY_SECONDS)
    finally:
        writer.close()
    read_back = _stable_read(session, ACTIVE_PROFILE_ADDRESS, 1)[0]
    if read_back != profile - 1:
        return _result(STATUS_MISMATCH, f"active profile reads back as {read_back + 1}, expected {profile}; "
                       "no retry", active_profile=read_back + 1 if read_back < PROFILE_COUNT else None)
    return _result(STATUS_OK, active_profile=profile, previous_profile=current + 1)


def _read_polling(session: DeviceSession) -> tuple[bytes, bytes]:
    return tuple(_stable_read(session, address, length) for address, length in POLLING_READS)


def write_polling(
    values: tuple[int, ...],
    session: DeviceSession,
    *,
    backend_factory: Callable = _LightingWriteIo,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Set the five polling enums (hid.exe 0x410230: two F3, then F1 02 08)."""
    session.open_read_session()
    before = _read_polling(session)
    current = polling_from_reads(*before)
    if current is None:
        return _result(STATUS_REFUSED, "polling bytes on the mouse (" + " / ".join(b.hex(" ") for b in before)
                       + ") are not known values; nothing written")
    if current == tuple(values):
        return _result(STATUS_UNCHANGED, polling=list(current))
    reports = build_polling_reports(tuple(values))
    session._request_internal_capability(_LIGHTING_PREVIEW_CAPABILITY)
    writer = backend_factory(session.device.path)
    try:
        for report in reports:
            writer.submit(report)
            sleeper(INTER_COMMAND_DELAY_SECONDS)
        writer.submit(PROFILE_APPLY_REPORT_08)
        sleeper(INTER_COMMAND_DELAY_SECONDS)
    finally:
        writer.close()
    read_back = polling_from_reads(*_read_polling(session))
    if read_back != tuple(values):
        return _result(STATUS_MISMATCH, f"polling reads back as {read_back}, expected {tuple(values)}; no retry",
                       polling=list(read_back) if read_back else None)
    return _result(STATUS_OK, polling=list(read_back), previous=list(current))


def _read_dpi_block(session: DeviceSession, profile: int) -> bytes:
    """The read-plan request base+2 / 32 bytes for one profile."""
    return _stable_read(session, DPI_PROFILE_BASES[profile - 1] + DPI_READ_OFFSET, DPI_READ_LENGTH)


def _dpi_record_in_block(block: bytes, stage: int) -> bytes:
    start = 2 + 6 * (stage - 1)                  # block starts at base+2; records at base+4+6*(s-1)
    return block[start:start + DPI_RECORD_LENGTH]


def write_dpi(
    stages: tuple[tuple[int, int, int], ...],
    session: DeviceSession,
    *,
    backend_factory: Callable = _LightingWriteIo,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Set DPI stage values (T4): changed records, then F1 02 10 and F1 02 02."""
    session.open_read_session()
    profiles = sorted({profile for profile, _stage, _raw in stages})
    before = {profile: _read_dpi_block(session, profile) for profile in profiles}
    for profile, stage, _raw in stages:
        record = _dpi_record_in_block(before[profile], stage)
        if dpi_raw_from_record(record) is None:
            return _result(STATUS_REFUSED, f"profile {profile} DPI stage {stage} on the mouse ({record.hex(' ')}) "
                           "is not a known enabled record; nothing written")
    reports = [build_dpi_report(profile, stage, raw) for profile, stage, raw in sorted(stages)
               if dpi_raw_from_record(_dpi_record_in_block(before[profile], stage)) != raw]
    if not reports:
        return _result(STATUS_UNCHANGED, dpi=[{"profile": p, "stage": s, "raw": r} for p, s, r in stages])
    expected = {profile: bytearray(block) for profile, block in before.items()}
    for profile, stage, raw in stages:
        start = 2 + 6 * (stage - 1)
        expected[profile][start:start + DPI_RECORD_LENGTH] = dpi_record(raw)
    session._request_internal_capability(_LIGHTING_PREVIEW_CAPABILITY)
    writer = backend_factory(session.device.path)
    try:
        for report in reports:
            writer.submit(report)
            sleeper(INTER_COMMAND_DELAY_SECONDS)
        for finalisation in DPI_SEQUENCE:
            writer.submit(finalisation)
            sleeper(INTER_COMMAND_DELAY_SECONDS)
    finally:
        writer.close()
    read_back = {profile: _read_dpi_block(session, profile) for profile in profiles}
    wrong = [profile for profile in profiles if read_back[profile] != bytes(expected[profile])]
    if wrong:
        return _result(STATUS_MISMATCH, f"DPI block reads back differently for profile(s) "
                       f"{', '.join(map(str, wrong))}; no retry")
    return _result(STATUS_OK, dpi=[{"profile": p, "stage": s, "raw": r} for p, s, r in stages],
                   reports_sent=len(reports) + len(DPI_SEQUENCE))


def write_dpi_stage(
    entries: tuple[tuple[int, int], ...],
    session: DeviceSession,
    *,
    backend_factory: Callable = _LightingWriteIo,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Set the DPI start-up stage (T5): base+2 = [stage, 00] per changed profile, then F1 02 02, F1 02 10."""
    session.open_read_session()
    profiles = sorted({profile for profile, _stage in entries})
    before = {profile: _stable_read(session, dpi_stage_select_address(profile), DPI_STAGE_SELECT_LENGTH)
              for profile in profiles}
    current = {profile: dpi_stage_select_from_read(data) for profile, data in before.items()}
    for profile in profiles:
        if current[profile] is None:
            return _result(STATUS_REFUSED, f"profile {profile} start-up stage on the mouse "
                           f"({before[profile].hex(' ')}) is not a known value; nothing written")
    wanted = dict(entries)
    reports = [build_dpi_stage_report(profile, wanted[profile]) for profile in profiles
               if current[profile] != wanted[profile]]
    if not reports:
        return _result(STATUS_UNCHANGED, dpi_stage=[{"profile": p, "stage": current[p]} for p in profiles])
    session._request_internal_capability(_LIGHTING_PREVIEW_CAPABILITY)
    writer = backend_factory(session.device.path)
    try:
        for report in reports:
            writer.submit(report)
            sleeper(INTER_COMMAND_DELAY_SECONDS)
        for finalisation in DPI_STAGE_SEQUENCE:
            writer.submit(finalisation)
            sleeper(INTER_COMMAND_DELAY_SECONDS)
    finally:
        writer.close()
    read_back = {profile: _stable_read(session, dpi_stage_select_address(profile), DPI_STAGE_SELECT_LENGTH)
                 for profile in profiles}
    wrong = [str(profile) for profile in profiles if dpi_stage_select_from_read(read_back[profile]) != wanted[profile]]
    if wrong:
        return _result(STATUS_MISMATCH, f"start-up stage reads back differently for profile(s) "
                       f"{', '.join(wrong)}; no retry")
    return _result(STATUS_OK, dpi_stage=[{"profile": p, "stage": wanted[p]} for p in profiles],
                   reports_sent=len(reports) + len(DPI_STAGE_SEQUENCE))


def write_buttons(
    entries: tuple[tuple[int, int, bytes], ...],
    session: DeviceSession,
    *,
    backend_factory: Callable = _LightingWriteIo,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Set button records (T1-proven: one F3 per record, no finaliser; hid.exe 0x412f10)."""
    session.open_read_session()
    keys = sorted({(profile, button) for profile, button, _record in entries})
    before = {key: _stable_read(session, button_record_address(*key), BUTTON_RECORD_LENGTH) for key in keys}
    for key, record in before.items():
        if not button_record_is_known(record):
            return _result(STATUS_REFUSED, f"profile {key[0]} button {key[1]} on the mouse ({record.hex(' ')}) "
                           "is not a known record; nothing written")
    wanted = {(profile, button): bytes(record) for profile, button, record in entries}
    reports = [build_button_report(profile, button, wanted[(profile, button)]) for profile, button in keys
               if before[(profile, button)] != wanted[(profile, button)]]
    if not reports:
        return _result(STATUS_UNCHANGED, buttons=[{"profile": p, "button": b, "record": r.hex()}
                                                  for (p, b), r in sorted(before.items())])
    session._request_internal_capability(_LIGHTING_PREVIEW_CAPABILITY)
    writer = backend_factory(session.device.path)
    try:
        for report in reports:
            writer.submit(report)
            sleeper(INTER_COMMAND_DELAY_SECONDS)
    finally:
        writer.close()
    read_back = {key: _stable_read(session, button_record_address(*key), BUTTON_RECORD_LENGTH) for key in keys}
    wrong = [f"P{p}B{b}" for (p, b) in keys if read_back[(p, b)] != wanted[(p, b)]]
    if wrong:
        return _result(STATUS_MISMATCH, f"button records read back differently: {', '.join(wrong)}; no retry")
    return _result(STATUS_OK, buttons=[{"profile": p, "button": b, "record": r.hex()}
                                       for (p, b), r in sorted(read_back.items())],
                   reports_sent=len(reports))


def _read_macro_slot(session: DeviceSession, slot: int) -> bytes:
    """The four read-plan requests (50 bytes each) of one device macro slot."""
    base = macro_slot_address(slot)
    return b"".join(_stable_read(session, base + MACRO_READ_LENGTH * index, MACRO_READ_LENGTH)
                    for index in range(MACRO_READS_PER_SLOT))


def write_macros(
    entries: tuple[tuple[int, bytes], ...],
    session: DeviceSession,
    *,
    backend_factory: Callable = _LightingWriteIo,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Upload macro slot bodies (Stage 3): per slot F5 00, Report 4, F5 01, as Windows."""
    session.open_read_session()
    slots = sorted({slot for slot, _body in entries})
    before = {slot: _read_macro_slot(session, slot) for slot in slots}
    for slot, body in before.items():
        if not macro_body_is_known(body) and body != FACTORY_SLOT_FILLER:
            return _result(STATUS_REFUSED, f"macro slot {slot + 1} on the mouse holds an unrecognised body "
                           f"({body[:9].hex(' ')} ...); nothing written")
    wanted = {slot: bytes(body) for slot, body in entries}
    reports = [build_macro_report(slot, wanted[slot]) for slot in slots if before[slot] != wanted[slot]]
    if not reports:
        return _result(STATUS_UNCHANGED, macros=[{"slot": s, "body": before[s].hex()} for s in slots])
    session._request_internal_capability(_LIGHTING_PREVIEW_CAPABILITY)
    writer = backend_factory(session.device.path)
    try:
        for report in reports:
            writer.submit(MACRO_BEGIN_REPORT)
            sleeper(MACRO_BRACKET_DELAY_SECONDS)
            writer.submit(report)
            sleeper(INTER_COMMAND_DELAY_SECONDS)
            writer.submit(MACRO_END_REPORT)
            sleeper(MACRO_BRACKET_DELAY_SECONDS)
    finally:
        writer.close()
    read_back = {slot: _read_macro_slot(session, slot) for slot in slots}
    wrong = [str(slot + 1) for slot in slots if read_back[slot] != wanted[slot]]
    if wrong:
        return _result(STATUS_MISMATCH, f"macro slot(s) {', '.join(wrong)} read back differently; no retry")
    return _result(STATUS_OK, macros=[{"slot": s, "body": read_back[s].hex()} for s in slots],
                   reports_sent=len(reports) * 3)


def write_scroll(
    values: tuple[int, ...],
    session: DeviceSession,
    *,
    backend_factory: Callable = _LightingWriteIo,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Set wheel scroll lines (T6): one Report-3 write, then the Apply tail F1s."""
    session.open_read_session()
    before = _stable_read(session, SCROLL_ADDRESS, SCROLL_LENGTH)
    current = scroll_from_read(before)
    if current is None:
        return _result(STATUS_REFUSED, f"scroll bytes on the mouse ({before.hex(' ')}) are not known values; "
                       "nothing written")
    if current == tuple(values):
        return _result(STATUS_UNCHANGED, scroll=list(current))
    report = build_scroll_report(tuple(values))
    session._request_internal_capability(_LIGHTING_PREVIEW_CAPABILITY)
    writer = backend_factory(session.device.path)
    try:
        writer.submit(report)
        sleeper(INTER_COMMAND_DELAY_SECONDS)
        for finalisation in SCROLL_SEQUENCE:
            writer.submit(finalisation)
            sleeper(INTER_COMMAND_DELAY_SECONDS)
    finally:
        writer.close()
    read_back = scroll_from_read(_stable_read(session, SCROLL_ADDRESS, SCROLL_LENGTH))
    if read_back != tuple(values):
        return _result(STATUS_MISMATCH, f"scroll lines read back as {read_back}, expected {tuple(values)}; no retry")
    return _result(STATUS_OK, scroll=list(read_back), previous=list(current))


def run(
    request_text: str,
    session_factory: Callable[[], DeviceSession] = DeviceSession.discover,
    **kwargs,
) -> dict[str, object]:
    """Parse, discover, write; always returns a document (never raises)."""
    try:
        kind, request = parse_any_request(request_text)
    except LightingRequestError as exc:
        return _result(STATUS_REFUSED, f"invalid request: {exc}")
    try:
        session = session_factory()
    except HidrawDiscoveryError as exc:
        text = str(exc)
        return _result(STATUS_MULTIPLE if text.startswith("multiple") else STATUS_NO_DEVICE, text)
    except DeviceSessionError as exc:
        return _result(STATUS_IDENTITY, str(exc))
    except (OSError, RuntimeError) as exc:
        return _result(STATUS_FAILED, str(exc))
    try:
        with session:
            if kind == "profile":
                return write_active_profile(request, session, **kwargs)
            if kind == "polling":
                return write_polling(request, session, **kwargs)
            if kind == "dpi":
                return write_dpi(request, session, **kwargs)
            if kind == "dpi_stage":
                return write_dpi_stage(request, session, **kwargs)
            if kind == "buttons":
                return write_buttons(request, session, **kwargs)
            if kind == "macros":
                return write_macros(request, session, **kwargs)
            if kind == "scroll":
                return write_scroll(request, session, **kwargs)
            return write_lighting(request, session, **kwargs)
    except HidrawPermissionError as exc:
        return _result(STATUS_PERMISSION, str(exc))
    except (LightingWriteError, DeviceSessionError, HidrawProtocolError, OSError, RuntimeError,
            ValueError) as exc:
        return _result(STATUS_FAILED, str(exc))


def main(argv: list[str] | None = None, stdin=None,
         session_factory: Callable[[], DeviceSession] = DeviceSession.discover) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(json.dumps(_result(STATUS_REFUSED, "this helper takes no options")))
        return 2
    stream = sys.stdin if stdin is None else stdin
    document = run(stream.read(MAX_REQUEST_BYTES + 1), session_factory)
    print(json.dumps(document))
    return 0 if document["status"] in (STATUS_OK, STATUS_UNCHANGED) else 1


if __name__ == "__main__":  # pragma: no cover - live use is user-run, via the GUI
    raise SystemExit(main())
