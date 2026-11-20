"""USB HID Keyboard/Keypad usage names (HID Usage Tables, page 0x07).

Evidence basis: the Venus single-key device record carries one keyboard usage
byte (``90 00 <usage> 00``).  Saved factory memory shows thumb buttons 1-12 as
usages 0x1E..0x27 ("1".."0"), 0x57 (Keypad +) and 0x56 (Keypad -), and the
genuine DayZ profile stores usages such as 0x09 (F), 0x2C (Space) and 0xE0
(Left Ctrl).  Macro keyboard action records use the same usage byte.

This module is pure data; it performs no device or file access.
"""

from __future__ import annotations

_LETTERS = {0x04 + index: chr(ord("A") + index) for index in range(26)}
_DIGITS = {0x1E + index: str((index + 1) % 10) for index in range(10)}
_FUNCTION_KEYS = {0x3A + index: f"F{index + 1}" for index in range(12)}
_FUNCTION_KEYS.update({0x68 + index: f"F{index + 13}" for index in range(12)})

KEY_NAMES: dict[int, str] = {
    **_LETTERS,
    **_DIGITS,
    0x28: "Enter",
    0x29: "Escape",
    0x2A: "Backspace",
    0x2B: "Tab",
    0x2C: "Space",
    0x2D: "-",
    0x2E: "=",
    0x2F: "[",
    0x30: "]",
    0x31: "\\",
    0x32: "Non-US #",
    0x33: ";",
    0x34: "'",
    0x35: "`",
    0x36: ",",
    0x37: ".",
    0x38: "/",
    0x39: "Caps Lock",
    **_FUNCTION_KEYS,
    0x46: "Print Screen",
    0x47: "Scroll Lock",
    0x48: "Pause",
    0x49: "Insert",
    0x4A: "Home",
    0x4B: "Page Up",
    0x4C: "Delete",
    0x4D: "End",
    0x4E: "Page Down",
    0x4F: "Right Arrow",
    0x50: "Left Arrow",
    0x51: "Down Arrow",
    0x52: "Up Arrow",
    0x53: "Num Lock",
    0x54: "Keypad /",
    0x55: "Keypad *",
    0x56: "Keypad -",
    0x57: "Keypad +",
    0x58: "Keypad Enter",
    **{0x59 + index: f"Keypad {(index + 1) % 10}" for index in range(10)},
    0x63: "Keypad .",
    0x64: "Non-US \\",
    0x65: "Menu",
    0xE0: "Left Ctrl",
    0xE1: "Left Shift",
    0xE2: "Left Alt",
    0xE3: "Left Super",
    0xE4: "Right Ctrl",
    0xE5: "Right Shift",
    0xE6: "Right Alt",
    0xE7: "Right Super",
}

MODIFIER_USAGES = frozenset(range(0xE0, 0xE8))

_ALIASES = {
    "esc": 0x29,
    "return": 0x28,
    "spacebar": 0x2C,
    "ctrl": 0xE0,
    "control": 0xE0,
    "left control": 0xE0,
    "right control": 0xE4,
    "shift": 0xE1,
    "alt": 0xE2,
    "super": 0xE3,
    "win": 0xE3,
    "windows": 0xE3,
    "meta": 0xE3,
    "left meta": 0xE3,
    "right meta": 0xE7,
    "left win": 0xE3,
    "right win": 0xE7,
    "del": 0x4C,
    "ins": 0x49,
    "pgup": 0x4B,
    "pgdn": 0x4E,
    "up": 0x52,
    "down": 0x51,
    "left": 0x50,
    "right": 0x4F,
    "minus": 0x2D,
    "equals": 0x2E,
    "keypad plus": 0x57,
    "keypad minus": 0x56,
}

_BY_NAME = {name.casefold(): usage for usage, name in KEY_NAMES.items()}
_BY_NAME.update(_ALIASES)


def key_name(usage: int) -> str:
    """Return a readable key name, or an explicit hexadecimal usage."""
    return KEY_NAMES.get(usage, f"Usage 0x{usage:02X}")


def parse_key(text: str) -> int:
    """Resolve a key name or an explicit 8-bit usage (``0x2C`` or decimal).

    Bare single digits resolve to the digit key, not to a numeric usage.
    """
    clean = text.strip()
    if not clean:
        raise ValueError("key cannot be empty")
    known = _BY_NAME.get(clean.casefold())
    if known is not None:
        return known
    lowered = clean.casefold()
    try:
        if lowered.startswith("0x"):
            usage = int(lowered, 16)
        elif lowered.startswith("usage "):
            usage = int(lowered.removeprefix("usage ").strip(), 0)
        else:
            usage = int(clean, 10)
    except ValueError as exc:
        raise ValueError(f"unknown key {clean!r}") from exc
    if not 0 < usage <= 0xFF:
        raise ValueError("key usage must be an 8-bit value from 1 to 255")
    return usage


# Linux evdev key codes (linux/input-event-codes.h) to HID keyboard usages.
# GDK hardware keycodes on X11 and Wayland are evdev codes plus 8.
_EVDEV_TO_HID = {
    1: 0x29, 2: 0x1E, 3: 0x1F, 4: 0x20, 5: 0x21, 6: 0x22, 7: 0x23, 8: 0x24,
    9: 0x25, 10: 0x26, 11: 0x27, 12: 0x2D, 13: 0x2E, 14: 0x2A, 15: 0x2B,
    16: 0x14, 17: 0x1A, 18: 0x08, 19: 0x15, 20: 0x17, 21: 0x1C, 22: 0x18,
    23: 0x0C, 24: 0x12, 25: 0x13, 26: 0x2F, 27: 0x30, 28: 0x28, 29: 0xE0,
    30: 0x04, 31: 0x16, 32: 0x07, 33: 0x09, 34: 0x0A, 35: 0x0B, 36: 0x0D,
    37: 0x0E, 38: 0x0F, 39: 0x33, 40: 0x34, 41: 0x35, 42: 0xE1, 43: 0x31,
    44: 0x1D, 45: 0x1B, 46: 0x06, 47: 0x19, 48: 0x05, 49: 0x11, 50: 0x10,
    51: 0x36, 52: 0x37, 53: 0x38, 54: 0xE5, 55: 0x55, 56: 0xE2, 57: 0x2C,
    58: 0x39, 59: 0x3A, 60: 0x3B, 61: 0x3C, 62: 0x3D, 63: 0x3E, 64: 0x3F,
    65: 0x40, 66: 0x41, 67: 0x42, 68: 0x43, 69: 0x53, 70: 0x47, 71: 0x5F,
    72: 0x60, 73: 0x61, 74: 0x56, 75: 0x5C, 76: 0x5D, 77: 0x5E, 78: 0x57,
    79: 0x59, 80: 0x5A, 81: 0x5B, 82: 0x62, 83: 0x63, 86: 0x64, 87: 0x44,
    88: 0x45, 96: 0x58, 97: 0xE4, 98: 0x54, 99: 0x46, 100: 0xE6, 102: 0x4A,
    103: 0x52, 104: 0x4B, 105: 0x50, 106: 0x4F, 107: 0x4D, 108: 0x51,
    109: 0x4E, 110: 0x49, 111: 0x4C, 119: 0x48, 125: 0xE3, 126: 0xE7,
    127: 0x65,
}


def usage_from_evdev(code: int) -> int | None:
    """Translate a Linux evdev key code to a HID keyboard usage."""
    return _EVDEV_TO_HID.get(code)


def usage_from_gdk_keycode(hardware_keycode: int) -> int | None:
    """Translate a GDK hardware keycode (evdev + 8) to a HID usage."""
    return usage_from_evdev(hardware_keycode - 8)
