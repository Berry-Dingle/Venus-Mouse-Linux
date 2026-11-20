"""Lighting state bound to the selected profile's PFD fields.

PFD fields (controlled-fixture evidence, ``FORMAT_SPEC.md``):
``LedMode1`` 0 Breathing, 1 Full Brightness, 2 Rainbow, 3 Wave, 4 Flash,
5 Off; ``BreathState1`` 8 Low, 5 Medium, 2 High; ``LedState1`` brightness
0..3; ``LedColor1`` Windows COLORREF.  ``FlashState1`` has no controlled
variation and is preserved untouched.

This module edits only the in-memory profile; it has no device access.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...pfd import BreathingSpeed, BrightnessLevel, LedMode, PaletteColor, colorref_to_rgb, rgb_to_colorref


@dataclass(frozen=True, slots=True)
class LightingModeOption:
    stable_id: str
    label: str
    pfd_value: int
    uses_colour: bool
    supports_breathing_speed: bool = False


MODES: tuple[LightingModeOption, ...] = (
    LightingModeOption("full-brightness", "Full Brightness", LedMode.FULL_BRIGHTNESS, True),
    LightingModeOption("breathing", "Breathing", LedMode.BREATHING, True, True),
    LightingModeOption("wave", "Wave", LedMode.WAVE, True),
    LightingModeOption("flash", "Flash", LedMode.FLASH, True),
    LightingModeOption("rainbow", "Rainbow", LedMode.RAINBOW, False),
    LightingModeOption("off", "Off", LedMode.OFF, False),
)
MODES_BY_VALUE = {int(item.pfd_value): item for item in MODES}

BRIGHTNESS_LEVELS: tuple[tuple[int, str], ...] = (
    (BrightnessLevel.OFF, "Off"), (BrightnessLevel.LOW, "Low"),
    (BrightnessLevel.MEDIUM, "Medium"), (BrightnessLevel.HIGH, "High"),
)
BREATHING_SPEEDS: tuple[tuple[int, str], ...] = (
    (BreathingSpeed.LOW, "Slow"), (BreathingSpeed.MEDIUM, "Medium"), (BreathingSpeed.HIGH, "Fast"),
)
_PREVIEW_SCALE = {0: 0.0, 1: .45, 2: .72, 3: 1.0}


@dataclass(frozen=True, slots=True)
class LightingSwatch:
    stable_id: str
    name: str
    colorref: int
    # False for app-added swatches outside the Windows palette; those are
    # stored as custom colours (exact RGB) rather than palette entries.
    official: bool = True

    @property
    def rgb(self) -> tuple[int, int, int]:
        return colorref_to_rgb(self.colorref)

    @property
    def hex_value(self) -> str:
        return "#" + "".join(f"{channel:02X}" for channel in self.rgb)


def official_swatches() -> tuple[LightingSwatch, ...]:
    """The 33-entry Windows editor palette (``ldcfg.exe`` VA 0x005B6098)."""
    names = {0: "Red", 12: "Green", 23: "Blue"}
    entries = [item for item in PaletteColor if item.name.startswith("PALETTE_")]
    return tuple(
        LightingSwatch(f"palette-{index:02d}", names.get(index, f"Palette {index + 1}"), int(item))
        for index, item in enumerate(entries)
    )


# App-added swatches completing the 6x6 grid: they continue the palette's last
# row from magenta-pink (#D90A7E) towards red. Not part of the Windows palette.
EXTRA_SWATCH_COLOURS = (
    ("Rose", (0xE4, 0x0A, 0x63)),
    ("Raspberry", (0xEE, 0x07, 0x48)),
    ("Crimson", (0xF6, 0x04, 0x2C)),
)


def palette_swatches() -> tuple[LightingSwatch, ...]:
    """The Windows palette plus the three app-added pink-red swatches (6x6)."""
    extras = tuple(
        LightingSwatch(f"extra-{index:02d}", name, rgb_to_colorref(*rgb), official=False)
        for index, (name, rgb) in enumerate(EXTRA_SWATCH_COLOURS)
    )
    return official_swatches() + extras


class ProfileLightingState:
    """Read/write lighting view of the workspace's selected profile."""

    def __init__(self, workspace) -> None:
        self.workspace = workspace
        self._listeners = []

    # ---------------------------------------------------------- reading
    @property
    def profile(self):
        return self.workspace.profile()

    @property
    def mode(self) -> LightingModeOption | None:
        return MODES_BY_VALUE.get(self.profile.led_mode_raw)

    @property
    def mode_raw(self) -> int:
        return self.profile.led_mode_raw

    @property
    def stored_colour(self) -> tuple[int, int, int]:
        return self.profile.led_color_rgb

    @property
    def colour_hex(self) -> str:
        return self.profile.led_color_hex

    @property
    def palette_colour(self) -> PaletteColor | None:
        return self.profile.palette_color

    @property
    def brightness_level(self) -> int:
        return self.profile.brightness_raw

    @property
    def brightness_known(self) -> bool:
        return self.profile.brightness is not None

    @property
    def breathing_speed(self) -> int:
        return self.profile.breathing_speed_raw

    @property
    def breathing_relevant(self) -> bool:
        mode = self.mode
        return bool(mode and mode.supports_breathing_speed)

    @property
    def colour_relevant(self) -> bool:
        mode = self.mode
        return mode is None or mode.uses_colour

    @property
    def colour(self) -> tuple[int, int, int]:
        """Approximate on-mouse appearance used by the preview artwork."""
        mode = self.mode
        if mode is not None and mode.stable_id == "off":
            return (0, 0, 0)
        scale = _PREVIEW_SCALE.get(self.brightness_level, 1.0)
        return tuple(round(channel * scale) for channel in self.stored_colour)

    # ---------------------------------------------------------- events
    def subscribe(self, listener) -> None:
        self._listeners.append(listener)

    def changed(self) -> None:
        for listener in tuple(self._listeners):
            listener(self)

    def _edited(self) -> None:
        self.workspace.edited("lighting")
        self.changed()

    # ---------------------------------------------------------- editing
    def select_mode(self, mode_id: str) -> None:
        option = next((item for item in MODES if item.stable_id == mode_id), None)
        if option is None:
            raise ValueError("unknown lighting mode")
        if self.profile.led_mode_raw == int(option.pfd_value):
            return
        self.profile.set_led_mode(option.pfd_value)
        self._edited()

    def select_swatch(self, stable_id: str) -> None:
        swatch = next((item for item in palette_swatches() if item.stable_id == stable_id), None)
        if swatch is None:
            raise ValueError("unknown palette swatch")
        if self.profile.led_color_raw == swatch.colorref:
            return
        if swatch.official:
            self.profile.set_palette_color(swatch.colorref)
        else:
            self.profile.set_led_color_rgb(*swatch.rgb)
        self._edited()

    def select_colour(self, rgb: tuple[int, int, int]) -> None:
        if len(rgb) != 3 or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255 for v in rgb):
            raise ValueError("colour must be three byte-sized integers")
        if tuple(self.stored_colour) == tuple(rgb):
            return
        self.profile.set_led_color_rgb(*rgb)
        self._edited()

    def set_brightness(self, level: int) -> None:
        if self.profile.brightness_raw == level:
            return
        self.profile.set_brightness(level)
        self._edited()

    def set_breathing_speed(self, raw_value: int) -> None:
        if raw_value not in dict(BREATHING_SPEEDS):
            raise ValueError("unsupported breathing speed")
        if self.profile.breathing_speed_raw == raw_value:
            return
        self.profile.set_breathing_speed(raw_value)
        self._edited()
