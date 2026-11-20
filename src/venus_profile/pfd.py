"""PFD profile model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
import re
import struct

from .checksum import ProfileStruct
from .ini import IniDocument


class PollingRateHz(IntEnum):
    HZ_125 = 125
    HZ_250 = 250
    HZ_500 = 500
    HZ_1000 = 1000


POLLING_ENUM_TO_HZ = {
    8: PollingRateHz.HZ_125,
    4: PollingRateHz.HZ_250,
    2: PollingRateHz.HZ_500,
    1: PollingRateHz.HZ_1000,
}
POLLING_HZ_TO_ENUM = {rate: stored for stored, rate in POLLING_ENUM_TO_HZ.items()}

ACCELERATION_MIN = 1
ACCELERATION_MAX = 4
POINTER_SPEED_MIN = 1
POINTER_SPEED_MAX = 20
SCROLL_SPEED_MIN = 1
SCROLL_SPEED_MAX = 63
DOUBLE_CLICK_SPEED_MIN = 1
DOUBLE_CLICK_SPEED_MAX = 10


class BrightnessLevel(IntEnum):
    """Brightness values exposed by the official Venus profile editor."""

    OFF = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class BreathingSpeed(IntEnum):
    """Breathing-speed values exposed by the official profile editor."""

    LOW = 8
    MEDIUM = 5
    HIGH = 2
    FULL_BRIGHTNESS = 0


class LedMode(IntEnum):
    """LED animation modes exposed by the official profile editor."""

    BREATHING = 0
    FULL_BRIGHTNESS = 1
    RAINBOW = 2
    WAVE = 3
    FLASH = 4
    OFF = 5


class DpiStage(IntEnum):
    """One-based selected DPI stage exposed to callers."""

    STAGE_1 = 1
    STAGE_2 = 2
    STAGE_3 = 3
    STAGE_4 = 4
    STAGE_5 = 5


def dpi_raw_to_dpi(raw: int) -> int | None:
    """Convert a confirmed Venus DPI sensor step to its displayed DPI."""
    if 1 <= raw <= 61:
        return (raw + 1) * 100
    if 62 <= raw <= 92:
        return (raw - 30) * 200
    if 93 <= raw <= 105:
        return (raw - 51) * 300
    if raw == 106:
        return 16400
    return None


def dpi_to_raw(dpi: int) -> int:
    """Convert a legal official-editor DPI value to its packed sensor step."""
    if isinstance(dpi, bool) or not isinstance(dpi, int):
        raise ValueError("DPI must be an integer")
    if 200 <= dpi <= 6200 and dpi % 100 == 0:
        return dpi // 100 - 1
    if 6400 <= dpi <= 12400 and dpi % 200 == 0:
        return dpi // 200 + 30
    if 12600 <= dpi <= 16200 and dpi % 300 == 0:
        return dpi // 300 + 51
    if dpi == 16400:
        return 106
    raise ValueError("unsupported DPI value or increment")


@dataclass(frozen=True, slots=True)
class DpiStageValue:
    """One losslessly decoded 16-byte DPIStageValue tuple."""

    enabled: int
    x_raw: int
    y_raw: int
    flag: int

    @property
    def x_dpi(self) -> int | None:
        return dpi_raw_to_dpi(self.x_raw)

    @property
    def y_dpi(self) -> int | None:
        return dpi_raw_to_dpi(self.y_raw)

    @property
    def dpi(self) -> int | None:
        """Return one DPI value only when X/Y are equal and understood."""
        return self.x_dpi if self.x_raw == self.y_raw else None


class PaletteColor(IntEnum):
    """The 33-entry colour grid embedded in ldcfg.exe, as COLORREF values."""

    PALETTE_00_RED = 0x000100B9
    PALETTE_01 = 0x000000FE
    PALETTE_02 = 0x000047FF
    PALETTE_03 = 0x00016BFE
    PALETTE_04 = 0x00459DFF
    PALETTE_05 = 0x0058BAFF
    PALETTE_06 = 0x0001E8F8
    PALETTE_07 = 0x004EFFF6
    PALETTE_08 = 0x004FFFC9
    PALETTE_09 = 0x004FFFA6
    PALETTE_10 = 0x004EFF84
    PALETTE_11 = 0x0001FF00
    PALETTE_12_GREEN = 0x0034B502
    PALETTE_13 = 0x0036A500
    PALETTE_14 = 0x007BCF00
    PALETTE_15 = 0x009ED000
    PALETTE_16 = 0x00CFCB00
    PALETTE_17 = 0x00E3E16B
    PALETTE_18 = 0x00FEC529
    PALETTE_19 = 0x00EAA226
    PALETTE_20 = 0x00F09E64
    PALETTE_21 = 0x00EA8424
    PALETTE_22 = 0x00FE4800
    PALETTE_23_BLUE = 0x00FF0F0E
    PALETTE_24 = 0x00BD0F0F
    PALETTE_25 = 0x00FF0759
    PALETTE_26 = 0x00FF0C79
    PALETTE_27 = 0x00FE0CA1
    PALETTE_28 = 0x00E76CAA
    PALETTE_29 = 0x00D70AB3
    PALETTE_30 = 0x00FF0BC9
    PALETTE_31 = 0x00DA0ACE
    PALETTE_32 = 0x007E0AD9

    RED = PALETTE_00_RED
    GREEN = PALETTE_12_GREEN
    BLUE = PALETTE_23_BLUE

    @property
    def rgb(self) -> tuple[int, int, int]:
        return colorref_to_rgb(self.value)


def colorref_to_rgb(value: int) -> tuple[int, int, int]:
    """Decode Windows COLORREF 0x00BBGGRR to an (R, G, B) tuple."""
    if not 0 <= value <= 0xFFFFFF:
        raise ValueError("COLORREF must fit unsigned 24-bit range")
    return value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF


def rgb_to_colorref(red: int, green: int, blue: int) -> int:
    """Encode RGB channels as Windows COLORREF 0x00BBGGRR."""
    channels = (red, green, blue)
    if any(isinstance(channel, bool) or not isinstance(channel, int)
           or not 0 <= channel <= 255 for channel in channels):
        raise ValueError("RGB channels must be integers from 0 through 255")
    return red | (green << 8) | (blue << 16)


def parse_rgb_hex(value: str) -> tuple[int, int, int]:
    """Parse exactly #RRGGBB or RRGGBB without accepting ambiguous forms."""
    if not isinstance(value, str) or re.fullmatch(r"#?[0-9A-Fa-f]{6}", value) is None:
        raise ValueError("RGB hex must be exactly #RRGGBB or RRGGBB")
    digits = value.removeprefix("#")
    return tuple(int(digits[offset:offset + 2], 16) for offset in (0, 2, 4))


@dataclass(frozen=True, slots=True)
class ButtonAssignment:
    section: str
    button_id: int
    function: int
    key_number: int
    macro_name: str
    loop_number: int | None
    blob: ProfileStruct | None


@dataclass(slots=True)
class PfdProfile:
    document: IniDocument
    source_path: Path | None = None

    @classmethod
    def from_path(cls, path: str | Path) -> "PfdProfile":
        source = Path(path)
        return cls(IniDocument.from_path(source), source)

    @classmethod
    def from_bytes(cls, content: bytes) -> "PfdProfile":
        return cls(IniDocument.parse_bytes(content))

    @property
    def group_section(self) -> str | None:
        for section in self.document.sections:
            if section == "GROUP" or section.startswith("GROUP"):
                return section
        return None

    @property
    def buttons(self) -> list[ButtonAssignment]:
        group = self.group_section
        result: list[ButtonAssignment] = []
        for section in self.document.sections:
            if not section.startswith("ButtonAssigned"):
                continue
            function = self.document.get(section, "ButtonFunc")
            if function is None:
                continue
            blob = None
            if group is not None:
                text = self.document.get(group, section)
                if text:
                    blob = ProfileStruct.decode(text)
            loop = self.document.get(section, "LoopNumber")
            result.append(ButtonAssignment(
                section=section,
                button_id=int(self.document.get(section, "ButtonID", "0") or 0),
                function=int(function),
                key_number=int(self.document.get(section, "KeyNumber", "0") or 0),
                macro_name=self.document.get(section, "MacroName", "") or "",
                loop_number=int(loop) if loop is not None else None,
                blob=blob,
            ))
        return result

    @property
    def macro_assignments(self) -> list[ButtonAssignment]:
        return [button for button in self.buttons if button.function == 100]

    @property
    def dpi_stage_struct(self) -> ProfileStruct | None:
        group = self.group_section
        if group is None:
            return None
        text = self.document.get(group, "DPIStageValue")
        return ProfileStruct.decode(text) if text else None

    @property
    def polling_rate_raw(self) -> int:
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        return int(self.document.require(group, "PollingRate"), 10)

    @property
    def polling_rate_hz(self) -> PollingRateHz | None:
        """Decode a known rate, returning None for a preserved unknown enum."""
        return POLLING_ENUM_TO_HZ.get(self.polling_rate_raw)

    def set_polling_rate_hz(self, rate: PollingRateHz | int) -> None:
        """Set one of the four rates supported by the official editor."""
        try:
            known_rate = PollingRateHz(rate)
        except (TypeError, ValueError) as exc:
            supported = ", ".join(str(value.value) for value in PollingRateHz)
            raise ValueError(f"unsupported polling rate; expected one of {supported} Hz") from exc
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        self.document.set(group, "PollingRate", str(POLLING_HZ_TO_ENUM[known_rate]))

    @property
    def acceleration_raw(self) -> int:
        """Return the decimal integer stored in GROUP Acceleration."""
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        return int(self.document.require(group, "Acceleration"), 10)

    @property
    def acceleration(self) -> int | None:
        """Return a known editor level, or None for a preserved unknown."""
        raw = self.acceleration_raw
        return raw if ACCELERATION_MIN <= raw <= ACCELERATION_MAX else None

    def set_acceleration(self, value: int) -> None:
        """Set an official acceleration level from 1 through 4."""
        if (isinstance(value, bool) or not isinstance(value, int)
                or not ACCELERATION_MIN <= value <= ACCELERATION_MAX):
            raise ValueError("unsupported acceleration; expected integer 1..4")
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        self.document.set(group, "Acceleration", str(value))

    @property
    def pointer_speed_raw(self) -> int:
        """Return the decimal integer stored in GROUP MouseSensitivity."""
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        return int(self.document.require(group, "MouseSensitivity"), 10)

    @property
    def pointer_speed(self) -> int | None:
        """Return a known editor level, or None for a preserved unknown."""
        raw = self.pointer_speed_raw
        return raw if POINTER_SPEED_MIN <= raw <= POINTER_SPEED_MAX else None

    def set_pointer_speed(self, value: int) -> None:
        """Set an official pointer-speed level from 1 through 20."""
        if (isinstance(value, bool) or not isinstance(value, int)
                or not POINTER_SPEED_MIN <= value <= POINTER_SPEED_MAX):
            raise ValueError("unsupported pointer speed; expected integer 1..20")
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        self.document.set(group, "MouseSensitivity", str(value))

    @property
    def scroll_speed_raw(self) -> int:
        """Return the decimal integer stored in GROUP WheelScrollLines."""
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        return int(self.document.require(group, "WheelScrollLines"), 10)

    @property
    def scroll_speed(self) -> int | None:
        """Return a known editor line count, or None for an unknown raw value."""
        raw = self.scroll_speed_raw
        return raw if SCROLL_SPEED_MIN <= raw <= SCROLL_SPEED_MAX else None

    def set_scroll_speed(self, value: int) -> None:
        """Set an official wheel-scroll-lines value from 1 through 63."""
        if (isinstance(value, bool) or not isinstance(value, int)
                or not SCROLL_SPEED_MIN <= value <= SCROLL_SPEED_MAX):
            raise ValueError("unsupported scroll speed; expected integer 1..63")
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        self.document.set(group, "WheelScrollLines", str(value))

    @property
    def double_click_speed_raw(self) -> int:
        """Return the decimal integer stored in GROUP DoubleClickSpeed."""
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        return int(self.document.require(group, "DoubleClickSpeed"), 10)

    @property
    def double_click_speed(self) -> int | None:
        """Return a known editor speed level, or None for an unknown raw."""
        raw = self.double_click_speed_raw
        return raw if DOUBLE_CLICK_SPEED_MIN <= raw <= DOUBLE_CLICK_SPEED_MAX else None

    @property
    def double_click_time_ms(self) -> int | None:
        """Return the Windows threshold generated by a known speed level."""
        level = self.double_click_speed
        return 900 - level * 70 if level is not None else None

    def set_double_click_speed(self, value: int) -> None:
        """Set an official double-click speed level from 1 through 10."""
        if (isinstance(value, bool) or not isinstance(value, int)
                or not DOUBLE_CLICK_SPEED_MIN <= value <= DOUBLE_CLICK_SPEED_MAX):
            raise ValueError(
                "unsupported double-click speed; expected integer 1..10"
            )
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        self.document.set(group, "DoubleClickSpeed", str(value))

    @property
    def brightness_raw(self) -> int:
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        return int(self.document.require(group, "LedState1"), 10)

    @property
    def brightness(self) -> BrightnessLevel | None:
        """Decode a known level, returning None for a preserved unknown enum."""
        try:
            return BrightnessLevel(self.brightness_raw)
        except ValueError:
            return None

    def set_brightness(self, level: BrightnessLevel | int) -> None:
        """Set one of the four brightness levels supported by the editor."""
        try:
            known_level = BrightnessLevel(level)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "unsupported brightness; expected Off, Low, Medium, or High"
            ) from exc
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        self.document.set(group, "LedState1", str(known_level.value))

    @property
    def breathing_speed_raw(self) -> int:
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        return int(self.document.require(group, "BreathState1"), 10)

    @property
    def breathing_speed(self) -> BreathingSpeed | None:
        """Decode a known speed, returning None for a preserved unknown enum."""
        try:
            return BreathingSpeed(self.breathing_speed_raw)
        except ValueError:
            return None

    def set_breathing_speed(self, speed: BreathingSpeed | int) -> None:
        """Set one of the four breathing values supported by the editor."""
        try:
            known_speed = BreathingSpeed(speed)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "unsupported breathing speed; expected Low, Medium, High, "
                "or Full Brightness"
            ) from exc
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        self.document.set(group, "BreathState1", str(known_speed.value))

    @property
    def led_mode_raw(self) -> int:
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        return int(self.document.require(group, "LedMode1"), 10)

    @property
    def led_mode(self) -> LedMode | None:
        """Decode a known mode, returning None for a preserved unknown enum."""
        try:
            return LedMode(self.led_mode_raw)
        except ValueError:
            return None

    def set_led_mode(self, mode: LedMode | int) -> None:
        """Set one of the six mutually exclusive modes supported by the editor."""
        try:
            known_mode = LedMode(mode)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "unsupported LED mode; expected Breathing, Full Brightness, "
                "Rainbow, Wave, Flash, or Off"
            ) from exc
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        self.document.set(group, "LedMode1", str(known_mode.value))

    @property
    def flash_state_raw(self) -> int:
        """Expose the preserved FlashState1 field; it is not the Flash selector."""
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        return int(self.document.require(group, "FlashState1"), 10)

    @property
    def selected_dpi_stage_raw(self) -> int:
        """Return the zero-based value stored in the PFD's DPI field."""
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        return int(self.document.require(group, "DPI"), 10)

    @property
    def selected_dpi_stage(self) -> DpiStage | None:
        """Return the one-based stage, or None for a preserved unknown value."""
        try:
            return DpiStage(self.selected_dpi_stage_raw + 1)
        except ValueError:
            return None

    def set_selected_dpi_stage(self, stage: DpiStage | int) -> None:
        """Select one of the five official stages without changing DPI values."""
        try:
            known_stage = DpiStage(stage)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid DPI stage; expected a stage from 1 to 5") from exc
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        self.document.set(group, "DPI", str(known_stage.value - 1))

    def dpi_stage_value(self, stage: DpiStage | int) -> DpiStageValue:
        """Return the complete raw tuple for one of the five DPI stages."""
        try:
            known_stage = DpiStage(stage)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid DPI stage; expected a stage from 1 to 5") from exc
        blob = self.dpi_stage_struct
        if blob is None or len(blob.data) < known_stage.value * 16:
            raise ValueError("DPIStageValue is absent or truncated")
        return DpiStageValue(
            *struct.unpack_from("<4I", blob.data, (known_stage.value - 1) * 16)
        )

    def dpi_stage_value_raw(self, stage: DpiStage | int) -> tuple[int, int]:
        """Return the packed (X, Y) sensor steps without normalization."""
        value = self.dpi_stage_value(stage)
        return value.x_raw, value.y_raw

    def dpi_stage_enabled_raw(self, stage: DpiStage | int) -> int:
        """Return a stage's preserved uint32 enable field."""
        return self.dpi_stage_value(stage).enabled

    def dpi_stage_enabled(self, stage: DpiStage | int) -> bool | None:
        """Return a known enable state, or None for an unknown raw value."""
        raw = self.dpi_stage_enabled_raw(stage)
        return bool(raw) if raw in (0, 1) else None

    def dpi_stage_dpi(self, stage: DpiStage | int) -> int | None:
        """Return displayed DPI, or None for unknown/asymmetric raw values."""
        return self.dpi_stage_value(stage).dpi

    def set_dpi_stage_enabled(self, stage: DpiStage | int, enabled: bool) -> None:
        """Change only one packed enable dword, without selection policy."""
        try:
            known_stage = DpiStage(stage)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid DPI stage; expected a stage from 1 to 5") from exc
        if not isinstance(enabled, bool):
            raise ValueError("DPI stage enabled state must be boolean")
        blob = self.dpi_stage_struct
        if blob is None or len(blob.data) < known_stage.value * 16:
            raise ValueError("DPIStageValue is absent or truncated")
        data = bytearray(blob.data)
        base = (known_stage.value - 1) * 16
        struct.pack_into("<I", data, base, int(enabled))
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        encoded = ProfileStruct.from_data(bytes(data)).encode(preserve_text=False)
        self.document.set(group, "DPIStageValue", encoded)

    def set_dpi_stage_dpi(self, stage: DpiStage | int, dpi: int) -> None:
        """Set one stage's X/Y sensor steps to a legal official-editor DPI."""
        try:
            known_stage = DpiStage(stage)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid DPI stage; expected a stage from 1 to 5") from exc
        raw = dpi_to_raw(dpi)
        blob = self.dpi_stage_struct
        if blob is None or len(blob.data) < known_stage.value * 16:
            raise ValueError("DPIStageValue is absent or truncated")
        data = bytearray(blob.data)
        base = (known_stage.value - 1) * 16
        struct.pack_into("<I", data, base + 4, raw)
        struct.pack_into("<I", data, base + 8, raw)
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        encoded = ProfileStruct.from_data(bytes(data)).encode(preserve_text=False)
        self.document.set(group, "DPIStageValue", encoded)

    @property
    def led_color_raw(self) -> int:
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        return int(self.document.require(group, "LedColor1"), 10)

    @property
    def led_color_rgb(self) -> tuple[int, int, int]:
        return colorref_to_rgb(self.led_color_raw)

    @property
    def led_color_hex(self) -> str:
        """Return the semantic COLORREF colour as canonical #RRGGBB."""
        red, green, blue = self.led_color_rgb
        return f"#{red:02X}{green:02X}{blue:02X}"

    @property
    def led_color_raw_hex(self) -> str:
        """Return the stored integer digits as #HHHHHH (not semantic RGB)."""
        return f"#{self.led_color_raw:06X}"

    @property
    def palette_color(self) -> PaletteColor | None:
        """Return a grid entry, or None for a preserved non-grid COLORREF."""
        try:
            return PaletteColor(self.led_color_raw)
        except ValueError:
            return None

    def set_palette_color(self, color: PaletteColor | int) -> None:
        """Set one of the 33 colours in the official editor's palette grid."""
        try:
            known_color = PaletteColor(color)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "unsupported palette colour; expected an official PaletteColor"
            ) from exc
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        self.document.set(group, "LedColor1", str(known_color.value))

    def set_led_color_rgb(self, red: int, green: int, blue: int) -> None:
        """Set an arbitrary RGB colour using exact COLORREF channel order."""
        colorref = rgb_to_colorref(red, green, blue)
        group = self.group_section
        if group is None:
            raise ValueError("profile has no GROUP section")
        self.document.set(group, "LedColor1", str(colorref))

    def set_led_color_hex(self, value: str) -> None:
        """Set an arbitrary colour from canonical #RRGGBB or RRGGBB."""
        self.set_led_color_rgb(*parse_rgb_hex(value))

    def assign_macro(self, section: str, *, slot: int, name: str) -> None:
        self.document.set(section, "ButtonFunc", "100")
        self.document.set(section, "KeyNumber", str(slot))
        self.document.set(section, "MacroName", name)

    def to_bytes(self) -> bytes:
        return self.document.to_bytes()
