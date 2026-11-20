"""Read-only decoding of the confirmed Venus button-assignment records.

This module consumes saved bytes only.  It intentionally contains no report
builder, writer, transport, device enumeration, or semantic guesses for
unmapped function codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


BUTTON_RECORD_LENGTH = 4
PROFILE_REGION_LENGTH = 0x60
PROFILE_BASES = {
    1: 0x0082,
    2: 0x0142,
    3: 0x01F2,
    4: 0x02A2,
    5: 0x0352,
}
BUTTON_OFFSETS = tuple(range(0, 18 * BUTTON_RECORD_LENGTH, BUTTON_RECORD_LENGTH)) + (
    0x58,
    0x5C,
)

# These labels apply only to the complete raw records observed in the saved
# campaign.  They are deliberately not keyed by function code alone: other
# records beginning with 0x90 remain unknown, and arbitrary 0x8F records are
# not treated as combo assignments.
CONFIRMED_ACTION_LABELS = {
    bytes.fromhex("00 00 00 00"): "Disable",
    bytes.fromhex("81 00 00 00"): "Left Click",
    bytes.fromhex("82 00 00 00"): "Right Click",
    bytes.fromhex("83 00 00 00"): "Middle Click",
    bytes.fromhex("84 00 00 00"): "Backward",
    bytes.fromhex("85 00 00 00"): "Forward",
    bytes.fromhex("8F 0A 00 00"): "Combo Shift+Win",
    bytes.fromhex("8F 02 04 00"): "Combo Shift+A",
    bytes.fromhex("88 00 00 00"): "DPI Switch Cycle",
    bytes.fromhex("89 00 00 00"): "DPI-",
    bytes.fromhex("8A 00 00 00"): "DPI+",
    bytes.fromhex("8D 00 00 00"): "Profile Switch",
    bytes.fromhex("90 00 04 00"): "Single Key A",
    # The factory/default ButtonAssigned_7 record carries HID keyboard usage
    # 0x1E, the "1" key.  It was historically labelled "Single Key A"; saved
    # factory memory (thumb buttons = usages 0x1E..0x27, 0x57, 0x56) and the
    # hid.exe single-key compiler (0x412F10 copies the key usage into byte 2)
    # establish the corrected label.  Matching remains exact four-byte only.
    bytes.fromhex("90 00 1E 00"): "Single Key 1",
    bytes.fromhex("94 00 00 00"): "Profile+",
    bytes.fromhex("95 00 00 00"): "Profile-",
    bytes.fromhex("97 00 00 00"): "Report Rate+",
    bytes.fromhex("98 00 00 00"): "Report Rate-",
    bytes.fromhex("99 81 03 00"): "Fire Key: Left, 3 fires, 0 ms",
    bytes.fromhex("99 81 04 01"): "Fire Key: Left, 4 fires, 10 ms",
    bytes.fromhex("99 81 03 02"): "Fire Key: Left, 3 fires, 20 ms",
    bytes.fromhex("99 82 03 01"): "Fire Key: Right, 3 fires, 10 ms",
    bytes.fromhex("99 84 03 01"): "Fire Key: Middle, 3 fires, 10 ms",
    bytes.fromhex("99 04 03 01"): "Fire Key: Keyboard A, 3 fires, 10 ms",
}


# Labels recorded in historical evidence before a label correction.  They are
# accepted only when the exact raw record and canonical ID also match; they
# never select or construct a record.
HISTORICAL_ACTION_LABELS = {
    bytes.fromhex("90 00 1E 00"): ("Single Key A",),
}


def label_matches_record(raw: bytes, label: object) -> bool:
    """Return True for the current or a recorded historical label of ``raw``."""
    value = bytes(raw)
    current = CONFIRMED_ACTION_LABELS.get(value)
    if current is None:
        return False
    return label == current or label in HISTORICAL_ACTION_LABELS.get(value, ())


@dataclass(frozen=True, slots=True)
class MacroAssignment:
    """Semantics confirmed for function code ``0x91`` only."""

    slot_index: int
    while_held: bool
    toggle: bool
    execution_count: int


@dataclass(frozen=True, slots=True)
class ButtonAssignmentRecord:
    """One lossless four-byte device assignment record."""

    profile: int
    button: int
    address: int
    raw: bytes

    def __post_init__(self) -> None:
        if len(self.raw) != BUTTON_RECORD_LENGTH:
            raise ValueError("button assignment record must be exactly four bytes")

    @property
    def function_code(self) -> int:
        return self.raw[0]

    @property
    def confirmed_action(self) -> str | None:
        """Return a label only for an evidence-confirmed complete record."""
        return CONFIRMED_ACTION_LABELS.get(self.raw)

    @property
    def macro_assignment(self) -> MacroAssignment | None:
        if self.function_code != 0x91:
            return None
        flags = self.raw[1]
        return MacroAssignment(
            slot_index=flags & 0x3F,
            while_held=bool(flags & 0x80),
            toggle=bool(flags & 0x40),
            execution_count=int.from_bytes(self.raw[2:4], "little"),
        )


@dataclass(frozen=True, slots=True)
class ProfileAssignmentRegion:
    """The 0x60-byte address span containing one profile's 20 records."""

    profile: int
    raw_region: bytes
    records: tuple[ButtonAssignmentRecord, ...]

    def __post_init__(self) -> None:
        if len(self.raw_region) != PROFILE_REGION_LENGTH:
            raise ValueError("profile assignment region must be exactly 0x60 bytes")


def button_assignment_address(profile: int, button: int) -> int:
    """Return the confirmed address of button 1..20 in profile 1..5."""
    if profile not in PROFILE_BASES:
        raise ValueError("profile must be between 1 and 5")
    if not 1 <= button <= 20:
        raise ValueError("button must be between 1 and 20")
    return PROFILE_BASES[profile] + BUTTON_OFFSETS[button - 1]


def _record(profile: int, button: int, raw: bytes) -> ButtonAssignmentRecord:
    return ButtonAssignmentRecord(
        profile=profile,
        button=button,
        address=button_assignment_address(profile, button),
        raw=bytes(raw),
    )


def decode_profile_assignment_region(
    profile: int,
    data: bytes,
) -> ProfileAssignmentRegion:
    """Decode one complete saved 0x60-byte profile region.

    The bytes at deliberate holes are preserved in ``raw_region`` but are not
    interpreted as button records.
    """
    if profile not in PROFILE_BASES:
        raise ValueError("profile must be between 1 and 5")
    if len(data) != PROFILE_REGION_LENGTH:
        raise ValueError("profile assignment region must be exactly 0x60 bytes")
    records = tuple(
        _record(profile, button, data[offset:offset + BUTTON_RECORD_LENGTH])
        for button, offset in enumerate(BUTTON_OFFSETS, start=1)
    )
    return ProfileAssignmentRegion(profile, bytes(data), records)


def decode_saved_assignments(
    memory: Mapping[int, int],
) -> tuple[ProfileAssignmentRegion, ...]:
    """Decode all five regions from a sparse saved address-to-byte map.

    Deliberate unread holes are zero-filled in ``raw_region``; every actual
    four-byte assignment record must be present and is preserved exactly.
    """
    for address, value in memory.items():
        if not isinstance(address, int) or not isinstance(value, int) or not 0 <= value <= 0xFF:
            raise ValueError("saved memory must map integer addresses to byte values")
    regions = []
    for profile in PROFILE_BASES:
        region = bytearray(PROFILE_REGION_LENGTH)
        for button, offset in enumerate(BUTTON_OFFSETS, start=1):
            for byte_offset in range(BUTTON_RECORD_LENGTH):
                address = button_assignment_address(profile, button) + byte_offset
                if address not in memory:
                    raise ValueError(f"saved memory is missing address 0x{address:04X}")
                region[offset + byte_offset] = memory[address]
        regions.append(decode_profile_assignment_region(profile, bytes(region)))
    return tuple(regions)
