"""Three-byte Venus macro action records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ActionType(IntEnum):
    ZERO = 0
    MOUSE_BUTTON = 1
    KEYBOARD = 4
    DELAY = 6
    UNKNOWN = 255


MOUSE_MASK_NAMES = {0x01: "left", 0x02: "right", 0x04: "middle"}
ACTION_DELAY_QUANTUM_MS = 10
ACTION_DELAY_MAX_MS = 0xFFFF * ACTION_DELAY_QUANTUM_MS


@dataclass(frozen=True, slots=True)
class ActionRecord:
    raw: bytes

    def __post_init__(self) -> None:
        if len(self.raw) != 3:
            raise ValueError("an action record must be exactly 3 bytes")

    @property
    def base_type(self) -> int:
        return self.raw[0] & 0x7F

    @property
    def state_down(self) -> bool:
        return bool(self.raw[0] & 0x80)

    @property
    def action_type(self) -> ActionType:
        if self.raw == b"\0\0\0":
            return ActionType.ZERO
        if self.base_type == 0:
            return ActionType.UNKNOWN
        try:
            return ActionType(self.base_type)
        except ValueError:
            return ActionType.UNKNOWN

    @property
    def usage(self) -> int:
        return self.raw[1]

    @property
    def delay_raw(self) -> int:
        return self.raw[1] | (self.raw[2] << 8)

    @property
    def delay_ms(self) -> int:
        return self.delay_raw * 10

    @classmethod
    def keyboard(cls, usage: int, down: bool) -> "ActionRecord":
        _byte(usage, "keyboard usage")
        return cls(bytes([0x84 if down else 0x04, usage, 0]))

    @classmethod
    def action_delay_ms(cls, milliseconds: int) -> "ActionRecord":
        """Create an explicit action-list delay record.

        This is independent from MacroFile.DefDelayTime/default inserted delay.
        """
        if not isinstance(milliseconds, int) or isinstance(milliseconds, bool):
            raise TypeError("action delay must be an integer number of milliseconds")
        if milliseconds % ACTION_DELAY_QUANTUM_MS:
            raise ValueError("action delay must be a multiple of 10 ms")
        raw = milliseconds // ACTION_DELAY_QUANTUM_MS
        if not 0 <= raw <= 0xFFFF:
            raise ValueError(f"action delay must be between 0 and {ACTION_DELAY_MAX_MS} ms")
        return cls(bytes([0x06, raw & 0xFF, raw >> 8]))

    @classmethod
    def delay(cls, milliseconds: int) -> "ActionRecord":
        """Compatibility alias for action_delay_ms()."""
        return cls.action_delay_ms(milliseconds)

    @classmethod
    def mouse_button(cls, mask: int, down: bool) -> "ActionRecord":
        if mask not in MOUSE_MASK_NAMES:
            raise ValueError("mouse mask must be 0x01, 0x02, or 0x04")
        return cls(bytes([0x81 if down else 0x01, mask, 0]))

    @classmethod
    def zero(cls) -> "ActionRecord":
        return cls(b"\0\0\0")

    def describe(self) -> str:
        if self.action_type == ActionType.ZERO:
            return "zero/padding"
        if self.action_type == ActionType.KEYBOARD:
            return f"keyboard {'down' if self.state_down else 'up'} usage=0x{self.usage:02X}"
        if self.action_type == ActionType.DELAY:
            return f"action delay {self.delay_ms} ms (raw={self.delay_raw})"
        if self.action_type == ActionType.MOUSE_BUTTON:
            name = MOUSE_MASK_NAMES.get(self.usage, f"mask-0x{self.usage:02X}")
            return f"mouse {name} {'down' if self.state_down else 'up'}"
        return f"unknown {self.raw.hex(' ').upper()}"


def _byte(value: int, label: str) -> None:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{label} must fit in one byte")
