"""Provenance-rich semantic lighting state with no live-write operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .known_device_state import (
    KNOWN_STATE_PATH,
    compare_live_read,
    effective_expected_state,
)


BRIGHTNESS_ADDRESS_PROFILE1 = 0x044F
LED_MODE_ADDRESS_PROFILE1 = 0x044C
LED_COLOUR_ADDRESS_PROFILE1 = 0x0449
LED_COLOUR_LENGTH_PROFILE1 = 3
BRIGHTNESS_NAMES = {0: "Off", 1: "Low", 2: "Medium", 3: "High"}
PFD_LED_MODE_NAMES = {
    0: "Breathing", 1: "Full Brightness", 2: "Rainbow",
    3: "Wave", 4: "Flash", 5: "Off",
}
# Controlled device-memory evidence currently covers only these two raw values.
# It proves that the device byte is not a direct copy of the PFD LedMode1 enum.
DEVICE_LED_MODE_NAMES = {
    0: "Off", 1: "Full Brightness", 2: "Breathing or Rainbow (prefix-dependent)",
}
DEVICE_LED_MODE_PREFIX_NAMES = {
    bytes.fromhex("01 00 01"): "Full Brightness",
    bytes.fromhex("02 05 01"): "Breathing",
    bytes.fromhex("00 00 00"): "Off",
    bytes.fromhex("02 00 03"): "Rainbow",
    bytes.fromhex("03 00 01"): "Wave",
    bytes.fromhex("04 05 01"): "Flash",
}
LED_MODE_NAMES = PFD_LED_MODE_NAMES
BREATHING_SPEED_NAMES = {
    8: "Low", 5: "Medium", 2: "High", 0: "Full Brightness",
}
DEVICE_BREATHING_SPEED_NAMES = {
    8: "Low", 5: "Medium", 2: "High", 0: "Full Brightness (UI label)",
}
DEVICE_LED_COLOURS = {
    0xFF0000: "Official solid red",
    0x0E0FFF: "Official solid blue",
    0x00FF01: "Official solid green",
    0xD22D7B: "Device RGB #D22D7B",
    0xE4A712: "Device RGB #E4A712",
    0x0000FF: "Device RGB #0000FF",
}
DEVICE_PALETTE_COLOUR_VALUES = {0xFF0000, 0x0E0FFF, 0x00FF01}
PFD_PALETTE_COLOURS = {
    65721: "Official red palette COLORREF",
    16715534: "Official blue palette COLORREF",
}
LATEST_LIGHTING_READ = (
    Path(__file__).resolve().parents[3] / "usb-captures" / "live-reads"
    / "venus_read_20260727T141157.470945Z"
)


class LightingStateError(RuntimeError):
    """Offline lighting input was incomplete or inconsistent."""


@dataclass(frozen=True)
class LightingField:
    name: str
    raw_value: int | None
    semantic_value: str | None
    device_memory_address: int | None
    device_memory_address_hex: str | None
    device_memory_length: int | None
    raw_bytes_hex: str | None
    semantic_rgb: tuple[int, int, int] | None
    provenance: str
    confidence: str
    live_read_supported: bool
    live_write_supported_values: tuple[int, ...]
    known_value_mapping: dict[int, str] | None
    pfd_value_mapping: dict[int, str] | None
    arbitrary_values_supported: bool | None
    notes: str


@dataclass(frozen=True)
class LightingState:
    profile: int
    source: str
    brightness: LightingField
    led_mode: LightingField
    breathing_speed: LightingField
    led_color: LightingField

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _unresolved_field(
    name: str,
    mapping: dict[int, str] | None,
    notes: str,
) -> LightingField:
    return LightingField(
        name=name,
        raw_value=None,
        semantic_value=None,
        device_memory_address=None,
        device_memory_address_hex=None,
        device_memory_length=None,
        raw_bytes_hex=None,
        semantic_rgb=None,
        provenance="known-from-pfd-semantics-only",
        confidence="semantic-enumeration-confirmed-device-mapping-unresolved",
        live_read_supported=False,
        live_write_supported_values=(),
        known_value_mapping=mapping,
        pfd_value_mapping=mapping,
        arbitrary_values_supported=None,
        notes=notes,
    )


def lighting_state_from_brightness(
    brightness: int,
    *,
    source: str,
    led_mode_raw: int | None = None,
    led_mode_source: str | None = None,
    led_colour_bytes: bytes | None = None,
    led_colour_source: str | None = None,
    led_mode_prefix: bytes | None = None,
) -> LightingState:
    if brightness not in BRIGHTNESS_NAMES:
        semantic = f"Unknown raw value {brightness}"
    else:
        semantic = BRIGHTNESS_NAMES[brightness]
    return LightingState(
        profile=1,
        source=source,
        brightness=LightingField(
            name="brightness",
            raw_value=brightness,
            semantic_value=semantic,
            device_memory_address=BRIGHTNESS_ADDRESS_PROFILE1,
            device_memory_address_hex="0x044F",
            device_memory_length=1,
            raw_bytes_hex=f"{brightness:02x}",
            semantic_rgb=None,
            provenance=(
                "confirmed-device-memory-address-controlled-linux-read-write"
            ),
            confidence="confirmed",
            live_read_supported=True,
            live_write_supported_values=(2, 3),
            known_value_mapping=BRIGHTNESS_NAMES,
            pfd_value_mapping=BRIGHTNESS_NAMES,
            arbitrary_values_supported=False,
            notes=(
                "Bytes 0..3 have known meanings. Only Medium (2) and High (3) "
                "are authorised and proven persistent live writes."
            ),
        ),
        led_mode=(
            LightingField(
                name="led_mode",
                raw_value=led_mode_raw,
                semantic_value=(
                    DEVICE_LED_MODE_PREFIX_NAMES.get(
                        led_mode_prefix if led_mode_prefix is not None else b"",
                        DEVICE_LED_MODE_NAMES.get(
                            led_mode_raw, f"Unknown device raw value {led_mode_raw}"
                        ),
                    )
                ),
                device_memory_address=LED_MODE_ADDRESS_PROFILE1,
                device_memory_address_hex="0x044C",
                device_memory_length=1,
                raw_bytes_hex=f"{led_mode_raw:02x}",
                semantic_rgb=None,
                provenance=led_mode_source or (
                    "controlled-windows-transition-and-native-pre-post-reads"
                ),
                confidence="confirmed-for-off-breathing-and-full-brightness",
                live_read_supported=True,
                live_write_supported_values=(),
                known_value_mapping=DEVICE_LED_MODE_NAMES,
                pfd_value_mapping=PFD_LED_MODE_NAMES,
                arbitrary_values_supported=False,
                notes=(
                    "Device raw 02 was Breathing and raw 01 is Full "
                    "Brightness. This is not the PFD LedMode1 enumeration. "
                    "Device raw 00 is true Mode Off and is distinct from RGB "
                    "black (00 00 00). Raw mode 02 is ambiguous: Breathing "
                    "uses suffix 05 01 while Rainbow uses 00 03; the full "
                    "three-byte mode suffix is required for decoding."
                ),
            )
            if led_mode_raw is not None
            else _unresolved_field(
                "led_mode",
                PFD_LED_MODE_NAMES,
                "Profile 1 address 0x044C is confirmed, but this source did "
                "not provide that byte.",
            )
        ),
        breathing_speed=_breathing_speed_field(
            led_mode_prefix,
            source,
        ),
        led_color=(
            _device_colour_field(
                led_colour_bytes,
                led_colour_source or (
                    "controlled-red-to-blue-transition-and-native-pre-post-reads"
                ),
            )
            if led_colour_bytes is not None
            else _unresolved_field(
                "led_color",
                PFD_PALETTE_COLOURS,
                "Profile 1 device colour is at 0x0449, but this source did "
                "not provide its complete three-byte value.",
            )
        ),
    )


def _breathing_speed_field(prefix: bytes | None, source: str) -> LightingField:
    # The controlled speed-only snapshots prove that 0x044D is the speed
    # selector while the mode triplet remains 02 ?? 01. Other modes use the
    # same byte as a mode companion and are intentionally not relabelled.
    if prefix is None or len(prefix) != 3 or prefix[0] != 2 or prefix[2] != 1:
        return _unresolved_field(
            "breathing_speed", BREATHING_SPEED_NAMES,
            "BreathState1 is known from PFD files; device speed decoding is "
            "available only for a Breathing triplet 02 ?? 01.",
        )
    raw = prefix[1]
    semantic = DEVICE_BREATHING_SPEED_NAMES.get(
        raw, f"Unknown device breathing-speed value {raw}"
    )
    return LightingField(
        name="breathing_speed", raw_value=raw, semantic_value=semantic,
        device_memory_address=0x044D, device_memory_address_hex="0x044D",
        device_memory_length=1, raw_bytes_hex=f"{raw:02x}", semantic_rgb=None,
        provenance=source,
        confidence="confirmed-by-controlled-breathing-speed-only-snapshots",
        live_read_supported=True, live_write_supported_values=(),
        known_value_mapping=DEVICE_BREATHING_SPEED_NAMES,
        pfd_value_mapping=BREATHING_SPEED_NAMES,
        arbitrary_values_supported=False,
        notes=("Only Breathing triplets are decoded as speed. The UI label "
               "Full Brightness at raw 00 is kept verbatim and is distinct "
               "from LED mode Full Brightness."),
    )


def _device_colour_field(raw: bytes, source: str) -> LightingField:
    if len(raw) != LED_COLOUR_LENGTH_PROFILE1:
        raise LightingStateError("device LED colour must contain three bytes")
    value = int.from_bytes(raw, "big")
    semantic_rgb = (raw[0], raw[1], raw[2])
    semantic_hex = "".join(f"{channel:02X}" for channel in semantic_rgb)
    semantic = DEVICE_LED_COLOURS.get(value, f"Device RGB #{semantic_hex}")
    return LightingField(
        name="led_color",
        raw_value=value,
        semantic_value=semantic,
        device_memory_address=LED_COLOUR_ADDRESS_PROFILE1,
        device_memory_address_hex="0x0449",
        device_memory_length=LED_COLOUR_LENGTH_PROFILE1,
        raw_bytes_hex=raw.hex(" "),
        semantic_rgb=semantic_rgb,
        provenance=source,
        confidence=(
            "confirmed-native-device-rgb-by-visible-live-experiment"
        ),
        live_read_supported=True,
        live_write_supported_values=(0xFF0000, 0x0E0FFF, 0x00FF01),
        known_value_mapping=DEVICE_LED_COLOURS,
        pfd_value_mapping=PFD_PALETTE_COLOURS,
        arbitrary_values_supported=True,
        notes=(
            "Mouse memory bytes are native R G B. The live write of 00 00 FF "
            "visibly produced blue and persisted, proving native decoding. "
            "Windows custom-picker entries #7B2DD2 and #12A7E4 were reversed "
            "by Windows before it wrote D2 2D 7B and E4 A7 12; that Windows "
            "UI conversion is not the mouse encoding. Exact palette mappings "
            "remain captured facts. Native arbitrary RGB live writing remains "
            "restricted to the separate recovery guard."
        ),
    )


def inspect_known_lighting_state(
    *,
    state_path: Path = KNOWN_STATE_PATH,
) -> LightingState:
    expected = effective_expected_state(
        state_path=state_path, allow_missing=True
    )
    brightness = expected.values.get(BRIGHTNESS_ADDRESS_PROFILE1)
    if brightness is None:
        raise LightingStateError("known state contains no byte at 0x044F")
    led_mode = _read_live_memory_byte(
        LATEST_LIGHTING_READ, LED_MODE_ADDRESS_PROFILE1
    )
    led_mode_prefix = _read_live_memory_bytes(
        LATEST_LIGHTING_READ, LED_MODE_ADDRESS_PROFILE1, 3
    )
    led_colour = _read_live_memory_bytes(
        LATEST_LIGHTING_READ, LED_COLOUR_ADDRESS_PROFILE1,
        LED_COLOUR_LENGTH_PROFILE1,
    )
    return lighting_state_from_brightness(
        brightness,
        source=(
            "windows-baseline-plus-confirmed-linux-overlay; "
            "lighting-fields-from-latest-controlled-post-read"
        ),
        led_mode_raw=led_mode,
        led_mode_source=(
            f"controlled-windows-transition; completed-native-read:"
            f"{LATEST_LIGHTING_READ}"
        ),
        led_mode_prefix=led_mode_prefix,
        led_colour_bytes=led_colour,
        led_colour_source=(
            f"controlled-red-to-blue-transition; completed-native-read:"
            f"{LATEST_LIGHTING_READ}"
        ),
    )


def _read_live_memory_byte(directory: Path, address: int) -> int:
    memory_path = directory / "live_memory_map.json"
    try:
        document = json.loads(memory_path.read_text(encoding="utf-8"))
        return int(document["bytes"][f"0x{address:04X}"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LightingStateError(
            f"live-read directory does not contain address 0x{address:04X}"
        ) from exc


def _read_live_memory_bytes(directory: Path, address: int, length: int) -> bytes:
    return bytes(
        _read_live_memory_byte(directory, address + offset)
        for offset in range(length)
    )


def inspect_live_read_lighting_state(
    directory: Path,
    *,
    state_path: Path = KNOWN_STATE_PATH,
) -> LightingState:
    comparison = compare_live_read(directory, state_path=state_path)
    brightness = _read_live_memory_byte(directory, BRIGHTNESS_ADDRESS_PROFILE1)
    led_mode = _read_live_memory_byte(directory, LED_MODE_ADDRESS_PROFILE1)
    led_mode_prefix = _read_live_memory_bytes(directory, LED_MODE_ADDRESS_PROFILE1, 3)
    led_colour = _read_live_memory_bytes(
        directory, LED_COLOUR_ADDRESS_PROFILE1, LED_COLOUR_LENGTH_PROFILE1
    )
    return lighting_state_from_brightness(
        brightness,
        source=(
            f"completed-live-read:{directory}; "
            f"unexpected-differences="
            f"{comparison['unexpected_difference_count']}"
        ),
        led_mode_raw=led_mode,
        led_mode_source=(
            f"completed-native-live-read:{directory}; address confirmed by "
            "controlled Breathing-to-Full-Brightness experiment"
        ),
        led_mode_prefix=led_mode_prefix,
        led_colour_bytes=led_colour,
        led_colour_source=(
            f"completed-native-live-read:{directory}; address confirmed by "
            "controlled official-red-to-official-blue experiment"
        ),
    )
