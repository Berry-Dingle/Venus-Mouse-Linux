"""Decode a read of the mouse's memory and apply it to the working set.

Pure and offline: input is a memory image (from ``read_snapshot`` or a saved
read), output is semantic values and the edits made to the workspace. Only
mappings confirmed in ``investigation/READ_COVERAGE_AUDIT.md`` are used:

* lighting, all five profiles: 6 bytes at ``0x0449 + 8*(p-1)`` (RGB, mode,
  companion/speed, trailing byte) and brightness 6 bytes later;
* DPI stage records ``base + 4 + 6*stage`` (enable, lookup, selector) and the
  stage byte ``base + 2`` (the mouse's current stage; it becomes the
  profile's start-up stage, as Windows Apply writes it back there);
* polling enums at ``0x0032``/``0x0038``;
* the 100 button records;
* the 15 macro slot bodies at ``0x0478 + slot*0xC8`` (bodies only: names are
  not stored on the mouse).

Also decoded (inference from ``hid.exe``, not yet live-confirmed): the
mouse's active profile at ``0x002C`` (zero-based). ``hid.exe`` reads it there
(``0x40eef0``, called from its read routine ``0x413830``) and writes the
current profile there before ``F1 02 01`` (``0x410140``). It is not a
workspace setting; the window uses it to pick the profile it shows.

Not read, so the working file keeps its values: scroll speed, the PC-only
pointer settings, macro names and loop settings, and the unexplained fields
``0x003E/0x0446``. Anything that cannot be decoded
keeps its saved value and is reported as a notice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The mouse's active profile, zero-based (hid.exe 0x40eef0 / 0x410140).
ACTIVE_PROFILE_ADDRESS = 0x002C
#: Wheel scroll lines, one byte + 00 per profile (hid.exe 0x410070 writes it with
#: Report 3; Windows never applies it to the PC, so the mouse uses it).
SCROLL_LINES_ADDRESS = 0x0020

from . import button_functions as bf
from .apply_plan import (
    DPI_PROFILE_BASES,
    LIGHTING_BRIGHTNESS_OFFSET,
    dpi_lookup,
    lighting_address,
    report4_slot_report,
)
from .button_assignments import BUTTON_OFFSETS, PROFILE_BASES
from .lighting_preview_plan import macro_slot_is_free
from .macroset import MACRO_SLOT_COUNT, report4_address
from .pfd import POLLING_ENUM_TO_HZ, BreathingSpeed, LedMode, dpi_raw_to_dpi

MACRO_BODY_LENGTH = 200
POLLING_ADDRESSES = {1: 0x0032, 2: 0x0034, 3: 0x0036, 4: 0x0038, 5: 0x003A}

# Inverse of apply_plan.PFD_MODE_TO_DEVICE_PREFIX: (mode, companion, trailing).
_FIXED_PREFIX_TO_MODE = {
    (0x01, 0x00, 0x01): LedMode.FULL_BRIGHTNESS,
    (0x02, 0x00, 0x03): LedMode.RAINBOW,
    (0x03, 0x00, 0x01): LedMode.WAVE,
    (0x00, 0x00, 0x00): LedMode.OFF,
}
_DPI_INVERSE = {dpi_lookup(raw): raw for raw in range(107)}
_FIRE_TRIGGER_FROM_DEVICE = {0x81: bf.FIRE_TRIGGER_LEFT, 0x82: bf.FIRE_TRIGGER_RIGHT,
                             0x84: bf.FIRE_TRIGGER_MIDDLE}
_PARAMETRIC_CODES = {bf.FUNCTION_SINGLE_KEY, bf.FUNCTION_COMBO_KEY, bf.FUNCTION_FIRE_KEY, bf.FUNCTION_MACRO}
_NAMED_TEMPLATES = {item.device_template: item.code for item in bf.FUNCTIONS
                    if item.code not in _PARAMETRIC_CODES}
_MODIFIER_FROM_BIT = {bit: usage for usage, bit in bf.MODIFIER_BITS.items()}


# ----------------------------------------------------------------- decoded values

@dataclass(frozen=True, slots=True)
class DecodedLighting:
    rgb: tuple[int, int, int]
    mode: LedMode | None          # None: unknown prefix
    companion: int
    trailing: int
    brightness: int
    mode_byte: int = 0


@dataclass(frozen=True, slots=True)
class DecodedStage:
    enabled: int
    raw_step: int | None          # None: (lookup, selector) not in the table
    record: bytes


@dataclass(slots=True)
class DecodedProfile:
    number: int
    lighting: DecodedLighting | None = None
    current_stage: int | None = None      # zero-based
    stages: list[DecodedStage | None] = field(default_factory=list)
    polling_enum: int | None = None
    buttons: list[bytes | None] = field(default_factory=list)


@dataclass(slots=True)
class DecodedDevice:
    profiles: list[DecodedProfile]
    macro_bodies: list[bytes | None]
    active_profile: int | None = None     # 1-5 from 0x002C; None if absent/out of range
    scroll_lines: tuple[int, ...] | None = None   # 0x0020: one byte + 00 per profile; None if unknown


def _read(values: dict[int, int], address: int, length: int) -> bytes | None:
    try:
        return bytes(values[address + offset] for offset in range(length))
    except KeyError:
        return None


def decode_lighting(record: bytes | None, brightness: bytes | None) -> DecodedLighting | None:
    if record is None or brightness is None:
        return None
    mode_byte, companion, trailing = record[3], record[4], record[5]
    if (mode_byte, trailing) == (0x02, 0x01):
        mode = LedMode.BREATHING
    elif (mode_byte, trailing) == (0x04, 0x01):
        mode = LedMode.FLASH
    else:
        mode = _FIXED_PREFIX_TO_MODE.get((mode_byte, companion, trailing))
    return DecodedLighting(tuple(record[:3]), mode, companion, trailing, brightness[0], mode_byte)


def decode_memory(values: dict[int, int]) -> DecodedDevice:
    """Decode every confirmed field present in a memory image."""
    profiles = []
    for number in range(1, 6):
        decoded = DecodedProfile(number)
        address = lighting_address(number)
        decoded.lighting = decode_lighting(_read(values, address, 6),
                                           _read(values, address + LIGHTING_BRIGHTNESS_OFFSET, 1))
        base = DPI_PROFILE_BASES[number - 1]
        stage = _read(values, base + 2, 1)
        decoded.current_stage = stage[0] if stage else None
        for index in range(5):
            record = _read(values, base + 4 + 6 * index, 5)
            if record is None:
                decoded.stages.append(None)
            else:
                decoded.stages.append(DecodedStage(record[0], _DPI_INVERSE.get((record[1], record[2])), record))
        polling = _read(values, POLLING_ADDRESSES[number], 1)
        decoded.polling_enum = polling[0] if polling else None
        decoded.buttons = [_read(values, PROFILE_BASES[number] + offset, 4) for offset in BUTTON_OFFSETS]
        profiles.append(decoded)
    bodies = [_read(values, report4_address(slot), MACRO_BODY_LENGTH) for slot in range(MACRO_SLOT_COUNT)]
    active = _read(values, ACTIVE_PROFILE_ADDRESS, 1)
    active_profile = active[0] + 1 if active is not None and active[0] < 5 else None
    scroll = _read(values, SCROLL_LINES_ADDRESS, 10)
    scroll_lines = None
    if scroll is not None and not any(scroll[1::2]) and all(1 <= value <= 63 for value in scroll[0::2]):
        scroll_lines = tuple(scroll[0::2])
    return DecodedDevice(profiles, bodies, active_profile, scroll_lines)


def decode_snapshot(document: dict) -> DecodedDevice:
    """Decode a ``read_snapshot`` JSON document with status ``ok``."""
    if document.get("status") != "ok":
        raise ValueError(f"snapshot status is {document.get('status')!r}, not 'ok'")
    values: dict[int, int] = {}
    for run in document.get("memory", []):
        start = int(run["address"])
        for offset, value in enumerate(bytes.fromhex(str(run["hex"]))):
            values[start + offset] = value
    return decode_memory(values)


# ----------------------------------------------------------------- applying

@dataclass(slots=True)
class ApplyReport:
    changes: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    profiles_changed: set[int] = field(default_factory=set)
    macros_changed: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.profiles_changed) or self.macros_changed


def _device_body(entry) -> bytes | None:
    try:
        return report4_slot_report(entry, 0)[8:8 + MACRO_BODY_LENGTH]
    except (ValueError, AttributeError):
        return None


def _apply_macros(workspace, decoded: DecodedDevice, report: ApplyReport) -> dict[int, str]:
    """Bank follows the mouse; returns slot -> library name for matched slots."""
    library = workspace.library
    by_body: dict[bytes, list] = {}
    for entry in library.entries:
        body = _device_body(entry)
        if body is not None:
            by_body.setdefault(body, []).append(entry)
    names: dict[int, str] = {}
    used: set[str] = set()
    for slot, body in enumerate(decoded.macro_bodies):
        if body is None:
            report.notices.append(f"Device slot {slot + 1} was not read; kept as saved")
            continue
        before = (library.bank.slots[slot], library.mouse_only.get(slot))
        if macro_slot_is_free(body):                   # zeros, or the factory ff ff filler
            library.bank.slots[slot] = None
            library.mouse_only.pop(slot, None)
        else:
            match = next((entry for entry in by_body.get(body, []) if entry.macro_id not in used), None)
            if match is not None:
                current = library.bank.slot_of(match.macro_id)
                if current is not None and current != slot:
                    library.bank.slots[current] = None
                library.bank.slots[slot] = match.macro_id
                library.mouse_only.pop(slot, None)
                used.add(match.macro_id)
                names[slot] = match.name
            else:
                library.bank.slots[slot] = None
                library.mouse_only[slot] = body
        if (library.bank.slots[slot], library.mouse_only.get(slot)) != before:
            report.macros_changed = True
            report.changes.append(f"Device slot {slot + 1}")
    return names


def _apply_lighting(profile, number: int, lighting: DecodedLighting | None, report: ApplyReport) -> list[str]:
    changed = []
    if lighting is None:
        report.notices.append(f"Profile {number} lighting was not read; kept as saved")
        return changed
    if lighting.mode is None:
        report.notices.append(
            f"Profile {number} lighting mode bytes {lighting.mode_byte:02X} {lighting.companion:02X} "
            f"{lighting.trailing:02X} are not recognised; lighting kept as saved")
        return changed
    if tuple(profile.led_color_rgb) != lighting.rgb:
        profile.set_led_color_rgb(*lighting.rgb)
        changed.append("colour")
    if profile.led_mode_raw != int(lighting.mode):
        profile.set_led_mode(lighting.mode)
        changed.append("mode")
    if lighting.mode == LedMode.BREATHING and profile.breathing_speed_raw != lighting.companion:
        if lighting.companion in {int(item) for item in BreathingSpeed}:
            profile.set_breathing_speed(lighting.companion)
            changed.append("breathing speed")
        else:
            report.notices.append(f"Profile {number} breathing speed {lighting.companion} is not recognised")
    if lighting.mode == LedMode.FLASH and profile.flash_state_raw != lighting.companion:
        report.notices.append(f"Profile {number} flash value {lighting.companion} differs from the profile file")
    if profile.brightness_raw != lighting.brightness:
        if 0 <= lighting.brightness <= 3:
            profile.set_brightness(lighting.brightness)
            changed.append("brightness")
        else:
            report.notices.append(f"Profile {number} brightness {lighting.brightness} is not recognised")
    return changed


def _apply_dpi(profile, number: int, decoded: DecodedProfile, report: ApplyReport) -> list[str]:
    changed = []
    stage = decoded.current_stage
    if stage is None:
        report.notices.append(f"Profile {number} DPI stage was not read")
    elif 0 <= stage <= 4:
        if profile.selected_dpi_stage_raw != stage:
            profile.set_selected_dpi_stage(stage + 1)
            changed.append("DPI stage")
    else:
        report.notices.append(f"Profile {number} DPI stage value {stage} is not recognised")
    for index, item in enumerate(decoded.stages, start=1):
        if item is None:
            report.notices.append(f"Profile {number} DPI stage {index} was not read")
            continue
        if item.enabled in (0, 1):
            if profile.dpi_stage_enabled_raw(index) != item.enabled:
                profile.set_dpi_stage_enabled(index, bool(item.enabled))
                changed.append(f"DPI {index} enabled")
        else:
            report.notices.append(f"Profile {number} DPI stage {index} enable value {item.enabled} is not recognised")
        if item.raw_step is None or dpi_raw_to_dpi(item.raw_step) is None:
            report.notices.append(
                f"Profile {number} DPI stage {index} bytes {item.record.hex(' ').upper()} are not recognised")
            continue
        x_raw, y_raw = profile.dpi_stage_value_raw(index)
        if x_raw == item.raw_step:
            continue  # same X; the mouse stores one value, so a differing Y is preserved
        profile.set_dpi_stage_dpi(index, dpi_raw_to_dpi(item.raw_step))
        changed.append(f"DPI {index}")
    return changed


def _apply_button(profile, number: int, button: int, record: bytes, slot_names: dict[int, str],
                  report: ApplyReport) -> bool:
    """Make the profile's button match the mouse's record; True if changed."""
    view = bf.read_assignment(profile, button)
    code = record[0]
    if code == 0x91:
        slot = record[1] & 0x3F
        if view.function_code == bf.FUNCTION_MACRO and view.key_number == slot:
            return False  # loop flags/count belong to the macro, not the profile file
        if slot >= MACRO_SLOT_COUNT:
            report.notices.append(f"Profile {number} {_button_name(button)} uses slot {slot + 1}, outside 1-15")
            return False
        bf.set_macro(profile, button, slot, slot_names.get(slot) or f"Slot {slot + 1}")
        return True
    derived = bf.derive_device_record(view)
    if derived == record:
        return False
    if record in _NAMED_TEMPLATES:
        bf.set_simple_function(profile, button, _NAMED_TEMPLATES[record])
        return True
    if code == 0x90 and record[1] == 0 and record[3] == 0 and record[2]:
        bf.set_single_key(profile, button, record[2])
        return True
    if code == 0x8F and record[3] == 0 and not record[1] & ~0x0F:
        modifiers = tuple(_MODIFIER_FROM_BIT[bit] for bit in (0x01, 0x02, 0x04, 0x08) if record[1] & bit)
        try:
            encoded = bf.ComboSetting(modifiers, record[2] or None)
            bf.set_combo(profile, button, encoded, allow_lone_modifier=True)   # 8F 02 00 00 = Shift
        except ValueError:
            pass
        else:
            return True
    if code == 0x99:
        trigger = _FIRE_TRIGGER_FROM_DEVICE.get(record[1], record[1])
        try:
            bf.set_fire_key(profile, button, trigger, record[3] * 10, record[2])   # count kept by the app
        except ValueError:
            pass
        else:
            return True
    report.notices.append(
        f"Profile {number} {_button_name(button)} record {record.hex(' ').upper()} is not recognised; kept as saved")
    return False


def _button_name(button: int) -> str:
    from .workspace import button_display_name
    return button_display_name(button)


def apply_to_workspace(workspace, decoded: DecodedDevice) -> ApplyReport:
    """The mouse wins: make the working set match everything decodable."""
    report = ApplyReport()
    slot_names = _apply_macros(workspace, decoded, report)
    for item in decoded.profiles:
        profile = workspace.profile(item.number)
        changed = _apply_lighting(profile, item.number, item.lighting, report)
        changed += _apply_dpi(profile, item.number, item, report)
        if item.polling_enum is None:
            report.notices.append(f"Profile {item.number} polling rate was not read")
        elif item.polling_enum in POLLING_ENUM_TO_HZ:
            if profile.polling_rate_raw != item.polling_enum:
                profile.set_polling_rate_hz(POLLING_ENUM_TO_HZ[item.polling_enum])
                changed.append("polling rate")
        else:
            report.notices.append(f"Profile {item.number} polling value {item.polling_enum} is not recognised")
        if decoded.scroll_lines is not None:
            lines = decoded.scroll_lines[item.number - 1]
            if profile.scroll_speed_raw != lines:
                profile.set_scroll_speed(lines)
                changed.append("scroll speed")
        for button, record in enumerate(item.buttons, start=1):
            if record is None:
                report.notices.append(f"Profile {item.number} {_button_name(button)} was not read")
            elif _apply_button(profile, item.number, button, record, slot_names, report):
                changed.append(_button_name(button))
        if changed:
            report.profiles_changed.add(item.number)
            report.changes.append(f"Profile {item.number}: " + ", ".join(changed))
    return report
