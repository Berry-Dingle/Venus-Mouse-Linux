"""PFD button-function catalogue and lossless assignment editing.

Evidence (see ``docs/WINDOWS_WORKFLOW.md``):

* ``hid.exe`` table at ``0x47D470`` (52 six-byte entries read by ``0x408480``)
  maps every PFD ``ButtonFunc`` to the four-byte device record template used
  by the button compiler ``0x412F10``.
* The editor's localized ``[FUNCTION_STRING]``/``[BASIC]``/``[ADVANCED]``/
  ``[MEDIA]``/``[DPI]``/``[RATE]``/``[SCROLL]`` string tables supply the user
  labels.  Their order matches the device templates (for example BASIC "CUT"
  is ``8F 01 1B 00`` = Ctrl+X).
* Saved PFD fixtures show what the Windows editor stores per function:
  single key keeps the HID usage in blob byte 0; combo key stores
  ``(usage, 05, 80|00)`` press/release triples with ``KeyNumber`` = record
  count; fire key stores its trigger in blob byte 0 (``00``/``F0`` left,
  ``F1`` right, ``F2`` middle, otherwise a keyboard usage) and its delay in
  ``KeyNumber`` (milliseconds).  The fire count is *not* stored in a PFD.
* When a function that does not use the blob is selected, Windows leaves the
  previous blob and ``KeyNumber`` unchanged (macro, disable and report-rate
  fixtures retain stale key data).  This module therefore changes only the
  fields used by the newly selected function.

Device records are derived for display and offline planning only.  This
module has no transport and grants no device-write permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .checksum import ProfileStruct
from .hid_usage import MODIFIER_USAGES, key_name
from .pfd import PfdProfile

BUTTON_COUNT = 20
VISUAL_BUTTON_COUNT = 18
BUTTON_STRUCT_LENGTH = 189

FUNCTION_MACRO = 100
FUNCTION_SINGLE_KEY = 6
FUNCTION_COMBO_KEY = 7
FUNCTION_FIRE_KEY = 12
FUNCTION_DISABLE = 16
FUNCTION_LEFT_CLICK = 1

FIRE_TRIGGER_LEFT = 0xF0
FIRE_TRIGGER_RIGHT = 0xF1
FIRE_TRIGGER_MIDDLE = 0xF2
FIRE_DEFAULT_COUNT = 3
FIRE_DELAY_STEP_MS = 10
FIRE_DELAY_MAX_MS = 2550       # what the record's delay byte can hold (mouse reads may carry it)
#: Offered in the editor (user decisions 2026-11-20). Windows can only send up to
#: 250 ms (hid.exe keeps the delay in ms in one byte, then divides by 10) and
#: accepts any typed count but keeps one byte, so 255 is the most it can send;
#: the editor offers that full range (user: "freedom to choose"). Counts 5-99
#: live-confirmed with the click counter.
FIRE_DELAY_UI_MAX_MS = 250
#: Live test 2026-11-20 (click counter): with a delay of 0 or 10 ms the mouse sends
#: only the first click and stays busy for the whole burst; 20 ms works (255 shots,
#: 122 ms apart). The factory record 99 81 03 00 (0 ms) is read and kept as it is;
#: the editor only offers 20 ms and up.
FIRE_DELAY_UI_MIN_MS = 20
FIRE_COUNT_MAX = 255           # one byte in the device record
FIRE_COUNT_UI_MAX = FIRE_COUNT_MAX
#: PFDs have no fire-count field. This app keeps the count in the button
#: structure after the trigger byte, behind a marker: ``trigger 'V' 'C' count``.
#: Windows reads only byte 0 for a fire key (hid.exe) and already leaves stale
#: bytes after it, so the extra bytes are ignored there; the marker keeps such
#: stale bytes from being read as a count.
FIRE_COUNT_MARKER = b"VC"

COMBO_STATE_DOWN = 0x80
COMBO_STATE_UP = 0x00
COMBO_RECORD_MARKER = 0x05
COMBO_MAX_RECORDS = 10

# HID modifier usage -> device combo modifier bit (8F <mask> <key> 00).
# Confirmed by Shift+Win = 8F 0A 00 00 and Shift+A = 8F 02 04 00, plus the
# static Ctrl (01), Alt (04) and Win (08) shortcut templates.
MODIFIER_BITS = {0xE0: 0x01, 0xE1: 0x02, 0xE2: 0x04, 0xE3: 0x08}
MODIFIER_LABELS = {0xE0: "Ctrl", 0xE1: "Shift", 0xE2: "Alt", 0xE3: "Super"}


class Category(str, Enum):
    MOUSE = "Mouse"
    KEYBOARD = "Keyboard key"
    COMBO = "Key combination"
    BASIC = "Editing shortcuts"
    ADVANCED = "System and browser"
    MEDIA = "Media"
    DPI = "DPI"
    PROFILE = "Profile"
    REPORT_RATE = "Report rate"
    SCROLL = "Scroll and lighting"
    MACRO = "Macro"
    FIRE = "Fire key"
    DISABLE = "Disable"


@dataclass(frozen=True, slots=True)
class ButtonFunction:
    code: int
    label: str
    category: Category
    device_template: bytes

    @property
    def stable_id(self) -> str:
        return f"button-function:{self.code}"


def _f(code: int, label: str, category: Category, template: str) -> ButtonFunction:
    return ButtonFunction(code, label, category, bytes.fromhex(template))


# Order follows the Windows editor menus.
FUNCTIONS: tuple[ButtonFunction, ...] = (
    _f(1, "Left Click", Category.MOUSE, "81 00 00 00"),
    _f(2, "Right Click", Category.MOUSE, "82 00 00 00"),
    _f(3, "Middle Click", Category.MOUSE, "83 00 00 00"),
    _f(4, "Forward", Category.MOUSE, "85 00 00 00"),
    _f(5, "Backward", Category.MOUSE, "84 00 00 00"),
    _f(6, "Single Key", Category.KEYBOARD, "90 00 00 00"),
    _f(7, "Key Combination", Category.COMBO, "8F 00 00 00"),
    _f(17, "Cut (Ctrl+X)", Category.BASIC, "8F 01 1B 00"),
    _f(18, "Copy (Ctrl+C)", Category.BASIC, "8F 01 06 00"),
    _f(19, "Paste (Ctrl+V)", Category.BASIC, "8F 01 19 00"),
    _f(20, "Select All (Ctrl+A)", Category.BASIC, "8F 01 04 00"),
    _f(21, "Find (Ctrl+F)", Category.BASIC, "8F 01 09 00"),
    _f(22, "New (Ctrl+N)", Category.BASIC, "8F 01 11 00"),
    _f(23, "Print (Ctrl+P)", Category.BASIC, "8F 01 13 00"),
    _f(24, "Save (Ctrl+S)", Category.BASIC, "8F 01 16 00"),
    _f(25, "Switch Window (Alt+Tab)", Category.ADVANCED, "8F 04 2B 00"),
    _f(26, "Close Window (Alt+F4)", Category.ADVANCED, "8F 04 3D 00"),
    _f(27, "File Manager (Super+E)", Category.ADVANCED, "8F 08 08 00"),
    _f(28, "Run (Super+R)", Category.ADVANCED, "8F 08 15 00"),
    _f(29, "Show Desktop (Super+D)", Category.ADVANCED, "8F 08 07 00"),
    _f(30, "Lock Screen (Super+L)", Category.ADVANCED, "8F 08 0F 00"),
    _f(31, "Browser Home", Category.ADVANCED, "8E 01 FF 1F"),
    _f(32, "Browser Back", Category.ADVANCED, "8E 01 FF 20"),
    _f(33, "Browser Forward", Category.ADVANCED, "8E 01 FF 21"),
    _f(34, "Browser Stop", Category.ADVANCED, "8E 01 FF 22"),
    _f(35, "Browser Refresh", Category.ADVANCED, "8E 01 FF 23"),
    _f(36, "Browser Search", Category.ADVANCED, "8E 01 FF 24"),
    _f(37, "Browser Favorites", Category.ADVANCED, "8E 01 FF 25"),
    _f(38, "Mail", Category.ADVANCED, "8E 01 FF 26"),
    _f(39, "Play/Pause", Category.MEDIA, "8E 01 CD 00"),
    _f(40, "Stop", Category.MEDIA, "8E 01 B7 00"),
    _f(41, "Previous Track", Category.MEDIA, "8E 01 B6 00"),
    _f(42, "Next Track", Category.MEDIA, "8E 01 B5 00"),
    _f(43, "Volume Up", Category.MEDIA, "8E 01 E9 00"),
    _f(44, "Volume Down", Category.MEDIA, "8E 01 EA 00"),
    _f(45, "Mute", Category.MEDIA, "8E 01 E2 00"),
    _f(58, "Media Player", Category.MEDIA, "8E 01 FF 3A"),
    _f(47, "DPI Cycle", Category.DPI, "88 00 00 00"),
    _f(48, "DPI +", Category.DPI, "8A 00 00 00"),
    _f(49, "DPI −", Category.DPI, "89 00 00 00"),
    _f(55, "Profile Cycle", Category.PROFILE, "8D 00 00 00"),
    _f(56, "Profile +", Category.PROFILE, "94 00 00 00"),
    _f(57, "Profile −", Category.PROFILE, "95 00 00 00"),
    _f(50, "Report Rate +", Category.REPORT_RATE, "97 00 00 00"),
    _f(51, "Report Rate −", Category.REPORT_RATE, "98 00 00 00"),
    _f(52, "Scroll Up", Category.SCROLL, "8B 00 00 00"),
    _f(53, "Scroll Down", Category.SCROLL, "8C 00 00 00"),
    _f(54, "Lighting Mode Cycle", Category.SCROLL, "9B 04 00 00"),
    _f(100, "Macro", Category.MACRO, "91 00 00 00"),
    _f(12, "Fire Key", Category.FIRE, "99 00 00 00"),
    _f(16, "Disable", Category.DISABLE, "00 00 00 00"),
)

FUNCTIONS_BY_CODE = {item.code: item for item in FUNCTIONS}
CATEGORY_ORDER = tuple(Category)
# Code 13 exists in the hid.exe table (template 30 00 00 00) but has no
# editor label; it is preserved when found and never offered.
UNOFFERED_TEMPLATES = {13: bytes.fromhex("30 00 00 00")}


def functions_in(category: Category) -> tuple[ButtonFunction, ...]:
    return tuple(item for item in FUNCTIONS if item.category is category)


@dataclass(frozen=True, slots=True)
class FireSetting:
    trigger: int
    delay_ms: int
    count: int | None = None      # None: not stored (Windows profile); the mouse's own count is kept

    @property
    def trigger_label(self) -> str:
        return fire_trigger_label(self.trigger)


def fire_trigger_label(trigger: int) -> str:
    if trigger in (0, FIRE_TRIGGER_LEFT):
        return "Left button"
    if trigger == FIRE_TRIGGER_RIGHT:
        return "Right button"
    if trigger == FIRE_TRIGGER_MIDDLE:
        return "Middle button"
    return f"Key {key_name(trigger)}"


@dataclass(frozen=True, slots=True)
class ComboSetting:
    modifiers: tuple[int, ...]
    key: int | None

    @property
    def label(self) -> str:
        parts = [MODIFIER_LABELS.get(usage, key_name(usage)) for usage in self.modifiers]
        if self.key is not None:
            parts.append(key_name(self.key))
        return "+".join(parts) if parts else "Empty combination"


@dataclass(frozen=True, slots=True)
class ButtonAssignmentView:
    """Decoded, display-ready view of one PFD button section."""

    button: int
    section: str
    function_code: int
    function: ButtonFunction | None
    key_number: int
    macro_name: str
    blob: bytes
    blob_valid: bool
    single_key: int | None = None
    combo: ComboSetting | None = None
    fire: FireSetting | None = None
    combo_error: str | None = None

    @property
    def known(self) -> bool:
        return self.function is not None

    @property
    def category(self) -> Category | None:
        return self.function.category if self.function else None

    @property
    def label(self) -> str:
        if self.function is None:
            return f"Unknown function {self.function_code} (preserved)"
        code = self.function.code
        if code == FUNCTION_SINGLE_KEY:
            return f"Key {key_name(self.single_key)}" if self.single_key else "Single Key (unset)"
        if code == FUNCTION_COMBO_KEY:
            if self.combo is None:
                return "Key Combination (unreadable, preserved)"
            return f"Combo {self.combo.label}"
        if code == FUNCTION_FIRE_KEY and self.fire is not None:
            shots = self.fire.count if self.fire.count is not None else FIRE_DEFAULT_COUNT
            return f"Fire Key · {self.fire.trigger_label} · {self.fire.delay_ms} ms · {shots} shots"
        if code == FUNCTION_MACRO:
            name = self.macro_name or "unnamed"
            return f"Macro · Slot {self.key_number + 1} · {name}"
        return self.function.label

    @property
    def device_record(self) -> bytes | None:
        """Offline-derived four-byte device record, when fully determined."""
        return derive_device_record(self)


def _button_struct(profile: PfdProfile, button: int) -> ProfileStruct | None:
    group = profile.group_section
    if group is None:
        return None
    text = profile.document.get(group, button_key(profile, button))
    if not text:
        return None
    return ProfileStruct.decode(text)


def button_section(profile: PfdProfile, button: int) -> str:
    _validate_button(button)
    group = profile.group_section
    if group is None:
        raise ValueError("profile has no GROUP section")
    suffix = group.removeprefix("GROUP")
    return f"ButtonAssigned{suffix}_{button}"


def button_key(profile: PfdProfile, button: int) -> str:
    return button_section(profile, button)


def _validate_button(button: int) -> None:
    if isinstance(button, bool) or not isinstance(button, int) or not 1 <= button <= BUTTON_COUNT:
        raise ValueError("button must be an integer from 1 to 20")


def decode_combo(blob: bytes, record_count: int) -> ComboSetting:
    """Decode Windows combo press/release triples into modifiers and one key."""
    if not 0 < record_count <= COMBO_MAX_RECORDS:
        raise ValueError(f"combo record count {record_count} is outside 1..{COMBO_MAX_RECORDS}")
    modifiers: list[int] = []
    keys: list[int] = []
    for index in range(record_count):
        usage, marker, state = blob[index * 3:index * 3 + 3]
        if usage == 0 or marker != COMBO_RECORD_MARKER or state not in (COMBO_STATE_DOWN, COMBO_STATE_UP):
            raise ValueError(f"combo record {index} is not a recognised press/release record")
        if state != COMBO_STATE_DOWN:
            continue
        if usage in MODIFIER_USAGES:
            if usage not in MODIFIER_BITS:
                raise ValueError("right-hand modifiers are not used by the Windows editor")
            if usage not in modifiers:
                modifiers.append(usage)
        elif usage not in keys:
            keys.append(usage)
    if len(keys) > 1:
        raise ValueError("combo contains more than one non-modifier key")
    return ComboSetting(tuple(modifiers), keys[0] if keys else None)


def encode_combo(setting: ComboSetting) -> tuple[bytes, int]:
    """Encode modifiers and an optional key as Windows-style triples."""
    if any(usage not in MODIFIER_BITS for usage in setting.modifiers):
        raise ValueError("combo modifiers must be Left Ctrl, Shift, Alt, or Super")
    if len(set(setting.modifiers)) != len(setting.modifiers):
        raise ValueError("combo modifiers must not repeat")
    if setting.key is not None and (setting.key in MODIFIER_USAGES or not 0 < setting.key <= 0xFF):
        raise ValueError("combo key must be a non-modifier keyboard usage")
    order = list(setting.modifiers) + ([setting.key] if setting.key is not None else [])
    if not order:
        raise ValueError("a key combination needs at least one key")
    if len(order) < 2:
        raise ValueError("a key combination needs at least two keys; use Single Key instead")
    records = [(usage, COMBO_RECORD_MARKER, COMBO_STATE_DOWN) for usage in order]
    records += [(usage, COMBO_RECORD_MARKER, COMBO_STATE_UP) for usage in reversed(order)]
    data = b"".join(bytes(record) for record in records)
    return data, len(records)


def read_assignment(profile: PfdProfile, button: int) -> ButtonAssignmentView:
    section = button_section(profile, button)
    document = profile.document
    func_text = document.get(section, "ButtonFunc")
    if func_text is None:
        raise ValueError(f"[{section}] ButtonFunc is absent")
    function_code = int(func_text, 10)
    key_number = int(document.get(section, "KeyNumber", "0") or 0, 10)
    macro_name = document.get(section, "MacroName", "") or ""
    struct = _button_struct(profile, button)
    blob = struct.data if struct is not None else b""
    valid = bool(struct and struct.valid and len(struct.data) == BUTTON_STRUCT_LENGTH)
    function = FUNCTIONS_BY_CODE.get(function_code)
    single = combo = fire = None
    combo_error = None
    if function_code == FUNCTION_SINGLE_KEY and blob:
        single = blob[0] or None
    elif function_code == FUNCTION_COMBO_KEY and blob:
        try:
            combo = decode_combo(blob, key_number)
        except ValueError as exc:
            combo_error = str(exc)
    elif function_code == FUNCTION_FIRE_KEY:
        count = None
        if len(blob) >= 4 and blob[1:3] == FIRE_COUNT_MARKER and 1 <= blob[3] <= FIRE_COUNT_MAX:
            count = blob[3]
        fire = FireSetting(blob[0] if blob else 0, key_number, count)
    return ButtonAssignmentView(
        button=button, section=section, function_code=function_code,
        function=function, key_number=key_number, macro_name=macro_name,
        blob=blob, blob_valid=valid, single_key=single, combo=combo,
        fire=fire, combo_error=combo_error,
    )


def read_all_assignments(profile: PfdProfile) -> tuple[ButtonAssignmentView, ...]:
    return tuple(read_assignment(profile, button) for button in range(1, BUTTON_COUNT + 1))


def derive_device_record(view: ButtonAssignmentView, *, macro_flags: int | None = None,
                         macro_count: int | None = None) -> bytes | None:
    """Derive the record ``0x412F10`` would compile, or None if undetermined.

    Macro records need the macro's loop settings and are derived elsewhere
    unless ``macro_flags``/``macro_count`` are supplied.  Fire records use the
    stored count (``FIRE_COUNT_MARKER``) or Windows' runtime default of 3.
    """
    code = view.function_code
    template = (view.function.device_template if view.function
                else UNOFFERED_TEMPLATES.get(code))
    if template is None:
        return None
    if code == FUNCTION_SINGLE_KEY:
        if not view.single_key:
            return None
        return bytes((0x90, 0x00, view.single_key, 0x00))
    if code == FUNCTION_COMBO_KEY:
        if view.combo is None:
            return None
        mask = 0
        for usage in view.combo.modifiers:
            mask |= MODIFIER_BITS[usage]
        return bytes((0x8F, mask, view.combo.key or 0, 0x00))
    if code == FUNCTION_FIRE_KEY:
        fire = view.fire or FireSetting(0, 0)
        trigger = {0: 0x81, FIRE_TRIGGER_LEFT: 0x81, FIRE_TRIGGER_RIGHT: 0x82,
                   FIRE_TRIGGER_MIDDLE: 0x84}.get(fire.trigger, fire.trigger)
        count = fire.count if fire.count is not None else FIRE_DEFAULT_COUNT
        if fire.trigger == 0:
            return bytes((0x99, 0x81, count, 0x00))
        return bytes((0x99, trigger, count, (fire.delay_ms // 10) & 0xFF))
    if code == FUNCTION_MACRO:
        if macro_flags is None or macro_count is None or not 0 <= view.key_number < 15:
            return None
        return bytes((0x91, (view.key_number & 0x3F) | (macro_flags & 0xC0),
                      macro_count & 0xFF, (macro_count >> 8) & 0xFF))
    return template


# ----------------------------------------------------------------- editing


def _set_struct(profile: PfdProfile, button: int, data: bytes) -> None:
    if len(data) > BUTTON_STRUCT_LENGTH:
        raise ValueError("button structure is limited to 189 bytes")
    padded = data.ljust(BUTTON_STRUCT_LENGTH, b"\0")
    group = profile.group_section
    encoded = ProfileStruct.from_data(padded).encode(preserve_text=False)
    profile.document.set(group, button_key(profile, button), encoded)


def _set_field(profile: PfdProfile, button: int, key: str, value: str) -> None:
    section = button_section(profile, button)
    if profile.document.get(section, key) == value:
        return
    profile.document.set(section, key, value)


def set_simple_function(profile: PfdProfile, button: int, code: int) -> None:
    """Assign a function that uses neither the blob nor KeyNumber."""
    function = FUNCTIONS_BY_CODE.get(code)
    if function is None or code in (FUNCTION_SINGLE_KEY, FUNCTION_COMBO_KEY,
                                    FUNCTION_FIRE_KEY, FUNCTION_MACRO):
        raise ValueError("function requires its dedicated setter or is not offered")
    _set_field(profile, button, "ButtonFunc", str(code))
    if function.code != FUNCTION_MACRO:
        _clear_macro_name(profile, button)


def _clear_macro_name(profile: PfdProfile, button: int) -> None:
    section = button_section(profile, button)
    if profile.document.get(section, "MacroName"):
        profile.document.set(section, "MacroName", "")


def set_single_key(profile: PfdProfile, button: int, usage: int) -> None:
    if isinstance(usage, bool) or not isinstance(usage, int) or not 0 < usage <= 0xFF:
        raise ValueError("key usage must be from 1 to 255")
    _set_field(profile, button, "ButtonFunc", str(FUNCTION_SINGLE_KEY))
    current = _button_struct(profile, button)
    desired = bytes([usage]).ljust(BUTTON_STRUCT_LENGTH, b"\0")
    if current is None or current.data != desired:
        _set_struct(profile, button, bytes([usage]))
    _clear_macro_name(profile, button)


def set_combo(profile: PfdProfile, button: int, setting: ComboSetting) -> None:
    data, count = encode_combo(setting)
    _set_field(profile, button, "ButtonFunc", str(FUNCTION_COMBO_KEY))
    _set_field(profile, button, "KeyNumber", str(count))
    current = _button_struct(profile, button)
    if current is None or current.data != data.ljust(BUTTON_STRUCT_LENGTH, b"\0"):
        _set_struct(profile, button, data)
    _clear_macro_name(profile, button)


def set_fire_key(profile: PfdProfile, button: int, trigger: int, delay_ms: int,
                 count: int | None = None) -> None:
    if trigger not in (FIRE_TRIGGER_LEFT, FIRE_TRIGGER_RIGHT, FIRE_TRIGGER_MIDDLE) and (
            isinstance(trigger, bool) or not isinstance(trigger, int)
            or not 0 < trigger <= 0xFF or trigger in MODIFIER_USAGES):
        raise ValueError("fire trigger must be left, right, middle, or a keyboard key")
    if (isinstance(delay_ms, bool) or not isinstance(delay_ms, int)
            or not 0 <= delay_ms <= FIRE_DELAY_MAX_MS or delay_ms % FIRE_DELAY_STEP_MS):
        raise ValueError("fire delay must be 0-2550 ms in 10 ms steps")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int)
                              or not 1 <= count <= FIRE_COUNT_MAX):
        raise ValueError("fire count must be 1-255")
    _set_field(profile, button, "ButtonFunc", str(FUNCTION_FIRE_KEY))
    _set_field(profile, button, "KeyNumber", str(delay_ms))
    current = _button_struct(profile, button)
    data = bytes([trigger]) + (FIRE_COUNT_MARKER + bytes([count]) if count is not None else b"")
    desired = data.ljust(BUTTON_STRUCT_LENGTH, b"\0")
    if current is None or current.data != desired:
        _set_struct(profile, button, data)
    _clear_macro_name(profile, button)


def set_macro(profile: PfdProfile, button: int, slot: int, name: str) -> None:
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < 15:
        raise ValueError("macro slot must be 0..14")
    if not name:
        raise ValueError("macro assignment requires a macro name")
    _set_field(profile, button, "ButtonFunc", str(FUNCTION_MACRO))
    _set_field(profile, button, "KeyNumber", str(slot))
    _set_field(profile, button, "MacroName", name)


def left_click_retained(profile: PfdProfile) -> bool:
    """Windows warns: at least one Left Click function must be retained."""
    return any(read_assignment(profile, button).function_code == FUNCTION_LEFT_CLICK
               for button in range(1, BUTTON_COUNT + 1))
