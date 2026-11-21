"""Offline, non-executable Apply preview for the five profiles and macro bank.

This module *describes* the device-memory writes a Windows-style Apply would
need, each tagged with its evidence level.  It contains no transport, no
report sender and no execution method; it never grants write permission.
Live operations remain limited to the separately guarded CLI tools.

Evidence used (see ``docs/WINDOWS_WORKFLOW.md``):

* DPI stage destination ``base[p] + 4 + 6*stage`` and selected stage at
  ``base[p] + 2`` (hid.exe ``0x40FB00``/``0x40FC60``; saved Apply capture and
  factory readback).  The lookup byte/selector come from the hid.exe tables
  at ``0x47E3F4..0x47E5B8`` read from the shipped file.
* Polling bytes at ``0x0032``/``0x0038`` (``0x410230``).
* Button records at the confirmed profile bases (``0x412F10`` +
  ``0x47D470`` function table).
* Lighting for all five profiles: a 6-byte RGB+mode record at
  ``0x0449 + 8*(profile-1)`` and brightness 6 bytes later (saved Windows Apply
  captures write all five; the factory read matches the factory PFDs; profile
  1 also has guarded Linux writes). See ``investigation/READ_COVERAGE_AUDIT.md``.
* Macro Report 4 slot body ``0x0478 + slot*0xC8`` (``0x412B50``; slot-0
  capture).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import button_functions as bf
from .button_assignments import BUTTON_OFFSETS, PROFILE_BASES
from .macroset import MACRO_SLOT_COUNT, report4_address

DPI_PROFILE_BASES = (0x0040, 0x0100, 0x01B0, 0x0260, 0x0310)
POLLING_ADDRESSES = (0x0032, 0x0038)
LIGHTING_P1_ADDRESS = 0x0449
LIGHTING_PROFILE_STRIDE = 8      # one 7-byte record (+1 unused byte) per profile
LIGHTING_BRIGHTNESS_OFFSET = 6
REPORT4_LENGTH = 256
REPORT4_BODY_LENGTH = 200

# hid.exe 0x40FB59..0x40FBC2: byte read at a four-byte stride from one of
# three table bases selected by the raw sensor step, plus a selector byte.
# Values for raw steps 0..106 were read from the shipped file and checked
# against the five REA-confirmed lookups (16 2D 5A 5C 7C).
_DPI_LOOKUP = bytes((
    0, 4, 6, 9, 11, 13, 15, 18, 20, 22, 24, 27, 29, 31, 33, 36, 38, 40, 43, 45,
    47, 49, 52, 54, 56, 58, 61, 63, 65, 67, 70, 72, 74, 77, 79, 81, 83, 86, 88,
    90, 92, 95, 97, 99, 102, 104, 106, 108, 111, 113, 115, 117, 120, 122, 124,
    127, 129, 131, 133, 135, 138, 140, 72, 74, 77, 79, 81, 83, 86, 88, 90, 92,
    95, 97, 99, 102, 104, 106, 108, 111, 113, 115, 117, 120, 122, 124, 127, 129,
    131, 133, 135, 138, 140, 95, 97, 99, 102, 104, 106, 108, 111, 113, 115, 117,
    120, 122, 124,
))

PFD_MODE_TO_DEVICE_PREFIX = {
    1: lambda profile: bytes((0x01, 0x00, 0x01)),            # Full Brightness
    0: lambda profile: bytes((0x02, profile.breathing_speed_raw & 0xFF, 0x01)),  # Breathing
    2: lambda profile: bytes((0x02, 0x00, 0x03)),            # Rainbow
    3: lambda profile: bytes((0x03, 0x00, 0x01)),            # Wave
    4: lambda profile: bytes((0x04, profile.flash_state_raw & 0xFF, 0x01)),  # Flash
    5: lambda profile: bytes((0x00, 0x00, 0x00)),            # Off
}

BLOCKERS = (
    "Which F1/F5 control reports are required, and when, has not been isolated "
    "by a controlled capture; Windows sends a long fixed sequence without "
    "checking results.",
    "DPI and polling writes have no controlled single-setting capture with "
    "before/after readback and reconnect.",
    "Macro uploads are confirmed for slot 1 only; replacing a populated slot "
    "and clearing a slot are unverified.",
    "Lighting for profiles 2-5 is known from Windows Apply captures only; no "
    "Linux write has been tested on those profiles.",
    "Button records were live-tested only at profile 1, button 7.",
)


class Evidence(str, Enum):
    VERIFIED = "verified"           # static code + saved capture/readback agree
    STATIC = "static-only"          # established from hid.exe, not from a capture
    INFERRED = "inferred"           # derived; a stated assumption is involved
    UNRESOLVED = "unresolved"       # cannot be planned from current evidence


@dataclass(frozen=True, slots=True)
class PlannedWrite:
    area: str
    profile: int | None
    address: int | None
    payload: bytes
    evidence: Evidence
    note: str = ""

    @property
    def address_text(self) -> str:
        return f"0x{self.address:04X}" if self.address is not None else "—"


@dataclass(frozen=True, slots=True)
class ApplyPreview:
    writes: tuple[PlannedWrite, ...]
    blockers: tuple[str, ...] = BLOCKERS
    executable: bool = False

    def by_area(self, area: str) -> tuple[PlannedWrite, ...]:
        return tuple(item for item in self.writes if item.area == area)


def dpi_lookup(raw: int) -> tuple[int, int]:
    """Return ``(lookup byte, selector)`` for a raw sensor step 0..106."""
    if not 0 <= raw <= 106:
        raise ValueError("DPI raw step must be 0..106")
    selector = 0 if raw <= 0x3D else (1 if raw <= 0x5C else 4)
    return _DPI_LOOKUP[raw], selector


def _dpi_writes(profile, number: int) -> list[PlannedWrite]:
    base = DPI_PROFILE_BASES[number - 1]
    writes = [PlannedWrite(
        "DPI", number, base + 2, bytes((profile.selected_dpi_stage_raw & 0xFF,)),
        Evidence.VERIFIED, "start-up stage (zero-based)",
    )]
    for stage in range(1, 6):
        value = profile.dpi_stage_value(stage)
        if not 0 <= value.x_raw <= 106:
            writes.append(PlannedWrite("DPI", number, None, b"", Evidence.UNRESOLVED,
                                       f"stage {stage} raw step {value.x_raw} is outside the lookup table"))
            continue
        lookup, selector = dpi_lookup(value.x_raw)
        evidence = Evidence.INFERRED
        note = f"stage {stage}: byte 0 assumed to be the enable flag (observed 01 for enabled stages)"
        if value.x_raw != value.y_raw:
            note += "; X/Y differ but the record carries one lookup (X used)"
        writes.append(PlannedWrite(
            "DPI", number, base + 4 + 6 * (stage - 1),
            bytes((value.enabled & 0xFF, lookup, selector, 0, 0)), evidence, note,
        ))
    return writes


#: Count Windows sends for a toggle macro (hid.exe 0x413184-0x41318D).
TOGGLE_COUNT = 0xFFFF


def _macro_loop(entry) -> tuple[int, int]:
    """Return (flags, execution count) as compiled by hid.exe 0x412F10.

    LoopType 1 (while held) sets 0x80, LoopType 3 (toggle) sets 0x40. The count
    is LoopTime (0 -> 1), run once forces 1, and toggle overwrites it with
    0xFFFF (0x41317F-0x41318D: ``cmp eax,3`` then ``mov al,0xff`` into both
    count bytes). Live-confirmed: toggle with count 10 plays 10 times and stops.
    """
    macro = entry.macro
    loop_type = macro.loop_type
    flags = 0x80 if loop_type == 1 else (0x40 if loop_type == 3 else 0)
    count = macro.loop_time or 1
    if loop_type == 2:
        count = 1
    if loop_type == 3:
        count = TOGGLE_COUNT
    return flags, count


def _button_writes(workspace, number: int, profile) -> list[PlannedWrite]:
    writes = []
    for button in range(1, bf.BUTTON_COUNT + 1):
        view = bf.read_assignment(profile, button)
        address = PROFILE_BASES[number] + BUTTON_OFFSETS[button - 1]
        record = None
        note = view.label
        evidence = Evidence.STATIC
        if view.function_code == bf.FUNCTION_MACRO:
            entry = workspace.library.slot_macro(view.key_number) if 0 <= view.key_number < 15 else None
            if entry is not None:
                flags, count = _macro_loop(entry)
                record = bf.derive_device_record(view, macro_flags=flags, macro_count=count)
            elif 0 <= view.key_number < 15 and view.key_number in workspace.library.mouse_only:
                # A macro only on the mouse: its loop settings are unknown, so play it
                # once (flags 0, count 1; hid.exe 0x412F10 default). Buttons that already
                # point at this slot keep the mouse's own flags (compared by slot).
                record = bf.derive_device_record(view, macro_flags=0, macro_count=1)
                note += " — macro only on the mouse; plays once"
            else:
                note += " — slot is empty in the bank"
        else:
            record = view.device_record
            if view.function_code == bf.FUNCTION_FIRE_KEY and view.fire and view.fire.trigger \
                    and view.fire.count is None:
                evidence = Evidence.INFERRED
                note += " — fire count not stored in this profile; Windows default 3 assumed"
        if record is None:
            writes.append(PlannedWrite("Buttons", number, address, b"", Evidence.UNRESOLVED,
                                       note + " — record cannot be derived"))
        else:
            writes.append(PlannedWrite("Buttons", number, address, record, evidence, note))
    return writes


def lighting_address(number: int) -> int:
    """Start of a profile's lighting record (RGB, mode, speed, trailing byte)."""
    if not 1 <= number <= 5:
        raise ValueError("profile must be 1..5")
    return LIGHTING_P1_ADDRESS + LIGHTING_PROFILE_STRIDE * (number - 1)


def _lighting_writes(number: int, profile) -> list[PlannedWrite]:
    prefix = PFD_MODE_TO_DEVICE_PREFIX.get(profile.led_mode_raw)
    if prefix is None:
        return [PlannedWrite("Lighting", number, None, b"", Evidence.UNRESOLVED,
                             f"unknown LedMode1 {profile.led_mode_raw}")]
    address = lighting_address(number)
    rgb = bytes(profile.led_color_rgb)
    palette = profile.palette_color is not None
    note = f"RGB + mode triplet; brightness at 0x{address + LIGHTING_BRIGHTNESS_OFFSET:04X}"
    if number != 1:
        note += "; address from Windows Apply captures, no Linux write tested yet"
    evidence = Evidence.VERIFIED
    if palette:
        evidence = Evidence.INFERRED
        note += "; palette entries may be translated by Windows (only three samples exist)"
    return [
        PlannedWrite("Lighting", number, address, rgb + prefix(profile), evidence, note),
        PlannedWrite("Lighting", number, address + LIGHTING_BRIGHTNESS_OFFSET,
                     bytes((profile.brightness_raw & 0xFF,)), Evidence.VERIFIED, "brightness"),
    ]


def button_records(workspace) -> dict[tuple[int, int], bytes | None]:
    """{(profile, button): device record or None if it cannot be derived} for all 100 buttons."""
    records: dict[tuple[int, int], bytes | None] = {}
    for number in range(1, 6):
        for button, write in enumerate(_button_writes(workspace, number, workspace.profile(number)), start=1):
            records[(number, button)] = bytes(write.payload) if write.payload else None
    return records


def report4_slot_report(entry, slot: int) -> bytes:
    """The 256-byte Report 4 feature report a slot upload would use."""
    if not 0 <= slot < MACRO_SLOT_COUNT:
        raise ValueError("macro slot must be 0..14")
    count = entry.macro.count
    if not 0 <= count <= 63:
        raise ValueError("macro Count must be 0..63")
    body = entry.macro.action_struct.data[: count * 3]
    address = report4_address(slot)
    header = bytes((0x04, 0xF3, address & 0xFF, address >> 8, 0xC8, 0x00, 0x00, 0x00))
    report = header + body.ljust(REPORT4_BODY_LENGTH, b"\0")
    return report.ljust(REPORT4_LENGTH, b"\0")


def build_apply_preview(workspace) -> ApplyPreview:
    writes: list[PlannedWrite] = []
    polling = []
    for number in range(1, 6):
        profile = workspace.profile(number)
        polling.append(profile.polling_rate_raw & 0xFF)
        writes.extend(_dpi_writes(profile, number))
        writes.extend(_button_writes(workspace, number, profile))
        writes.extend(_lighting_writes(number, profile))
    writes.append(PlannedWrite("Polling", None, POLLING_ADDRESSES[0],
                               bytes((polling[0], 0, polling[1], 0, polling[2], 0)), Evidence.STATIC,
                               "profiles 1-3 polling enum"))
    writes.append(PlannedWrite("Polling", None, POLLING_ADDRESSES[1],
                               bytes((polling[3], 0, polling[4], 0)), Evidence.STATIC,
                               "profiles 4-5 polling enum"))
    for slot in range(MACRO_SLOT_COUNT):
        entry = workspace.library.slot_macro(slot)
        if entry is None:
            continue
        evidence = Evidence.VERIFIED if slot == 0 else Evidence.STATIC
        writes.append(PlannedWrite("Macros", None, report4_address(slot),
                                   report4_slot_report(entry, slot), evidence,
                                   f"slot {slot + 1}: {entry.name} ({entry.macro.count} actions)"))
    return ApplyPreview(tuple(writes))
