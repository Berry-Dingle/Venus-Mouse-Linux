from __future__ import annotations

import json
from pathlib import Path

from venus_profile.inspect_lighting_state import main
from venus_profile.known_device_state import KNOWN_STATE_PATH, load_known_state
from venus_profile.lighting_state import (
    BRIGHTNESS_ADDRESS_PROFILE1,
    LED_COLOUR_ADDRESS_PROFILE1,
    LED_MODE_ADDRESS_PROFILE1,
    inspect_known_lighting_state,
    inspect_live_read_lighting_state,
)
from evidence_paths import EVIDENCE  # noqa: E402


def test_brightness_has_confirmed_address_provenance():
    state = inspect_known_lighting_state()
    field = state.brightness
    assert field.device_memory_address == BRIGHTNESS_ADDRESS_PROFILE1
    assert field.device_memory_address_hex == "0x044F"
    assert field.confidence == "confirmed"
    assert field.live_read_supported
    assert field.live_write_supported_values == (2, 3)
    assert field.known_value_mapping == {
        0: "Off", 1: "Low", 2: "Medium", 3: "High"
    }


def test_mode_has_confirmed_read_mapping_but_no_write_support():
    state = inspect_known_lighting_state()
    field = state.led_mode
    assert field.raw_value == 0
    assert field.semantic_value == "Off"
    assert field.device_memory_address == LED_MODE_ADDRESS_PROFILE1
    assert field.device_memory_address_hex == "0x044C"
    assert field.live_read_supported
    assert field.live_write_supported_values == ()
    assert field.known_value_mapping[0] == "Off"
    assert field.known_value_mapping[1] == "Full Brightness"
    assert "prefix-dependent" in field.known_value_mapping[2]
    assert field.pfd_value_mapping[0] == "Breathing"
    assert field.pfd_value_mapping[1] == "Full Brightness"


def test_true_off_is_distinct_from_rgb_black_and_not_live_writable():
    field = inspect_known_lighting_state().led_mode
    assert field.raw_value == 0
    assert field.semantic_value == "Off"
    assert field.live_write_supported_values == ()
    colour = inspect_known_lighting_state().led_color
    assert colour.raw_bytes_hex == "7b 2d d2"
    assert colour.semantic_value != "Black"


def test_rainbow_mode_requires_companion_and_trailing_bytes():
    directory = (
        EVIDENCE / "usb-captures" / "live-reads"
        / "venus_read_20260727T144118.608223Z"
    )
    state = inspect_live_read_lighting_state(directory)
    assert state.led_mode.raw_value == 2
    assert state.led_mode.semantic_value == "Rainbow"


def test_remaining_modes_and_breathing_speed_values_decode_from_triplets(project_root):
    root = project_root / "usb-captures" / "live-reads"
    expected = {
        "venus_read_20260727T152833.025314Z": "Wave",
        "venus_read_20260727T153200.408240Z": "Flash",
        "venus_read_20260727T153420.879863Z": "Medium",
        "venus_read_20260727T153629.000844Z": "Low",
        "venus_read_20260727T153822.515462Z": "High",
        "venus_read_20260727T154117.272912Z": "Full Brightness (UI label)",
    }
    from venus_profile.lighting_state import inspect_live_read_lighting_state
    for name, semantic in expected.items():
        state = inspect_live_read_lighting_state(root / name)
        if semantic in {"Wave", "Flash"}:
            assert state.led_mode.semantic_value == semantic
        else:
            assert state.breathing_speed.semantic_value == semantic


def test_still_unresolved_fields_remain_explicitly_unresolved():
    state = inspect_known_lighting_state()
    for field in (state.breathing_speed,):
        assert field.raw_value is None
        assert field.semantic_value is None
        assert field.device_memory_address is None
        assert field.device_memory_address_hex is None
        assert field.live_read_supported is False
        assert field.live_write_supported_values == ()
        assert "unresolved" in field.confidence


def test_colour_mapping_decodes_custom_bgr_but_exposes_only_guarded_palette_writes():
    field = inspect_known_lighting_state().led_color
    assert field.device_memory_address == LED_COLOUR_ADDRESS_PROFILE1
    assert field.device_memory_address_hex == "0x0449"
    assert field.device_memory_length == 3
    assert field.raw_bytes_hex == "7b 2d d2"
    assert field.semantic_rgb == (123, 45, 210)
    assert field.semantic_value == "Device RGB #7B2DD2"
    assert field.live_read_supported
    assert field.live_write_supported_values == (
        0xFF0000, 0x0E0FFF, 0x00FF01
    )
    assert 0x0000FF not in field.live_write_supported_values
    assert field.arbitrary_values_supported is True


def test_no_accidental_device_address_for_breathing_speed():
    encoded = inspect_known_lighting_state().to_dict()
    assert encoded["led_mode"]["device_memory_address"] == 0x044C
    assert encoded["breathing_speed"]["device_memory_address"] is None
    assert encoded["led_color"]["device_memory_address"] == 0x0449
    assert "arbitrary RGB" in encoded["led_color"]["notes"]


def test_current_known_brightness_tracks_latest_overlay_history():
    known = load_known_state(KNOWN_STATE_PATH)
    expected = known["history"][-1]["confirmed_new_value"]
    state = inspect_known_lighting_state()
    assert state.brightness.raw_value == expected


def test_offline_inspection_cli_is_json_and_does_not_open_hidraw(
    capsys,
):
    assert main([]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["brightness"]["device_memory_address_hex"] == "0x044F"
    assert document["led_mode"]["device_memory_address_hex"] == "0x044C"
    assert document["led_mode"]["semantic_value"] == "Off"
    assert document["led_mode"]["live_write_supported_values"] == []
    assert document["led_color"]["raw_bytes_hex"] == "7b 2d d2"
    assert document["led_color"]["semantic_rgb"] == [123, 45, 210]
    assert document["led_color"]["live_write_supported_values"] == [
        0xFF0000, 0x0E0FFF, 0x00FF01
    ]
    source = (
        Path(__file__).parents[1] / "src" / "venus_profile"
        / "inspect_lighting_state.py"
    ).read_text()
    assert "DeviceSession" not in source
    assert "discover_venus_hidraw" not in source
    assert "ReadOnlyHidrawTransport" not in source


def test_completed_live_read_inspection_is_deterministic():
    known = load_known_state(KNOWN_STATE_PATH)
    directory = Path(
        known["history"][-1]["latest_confirming_live_read_directory"]
    )
    first = inspect_live_read_lighting_state(directory).to_dict()
    second = inspect_live_read_lighting_state(directory).to_dict()
    assert first == second
    assert first["brightness"]["raw_value"] == known["history"][-1][
        "confirmed_new_value"
    ]
    assert first["led_mode"]["raw_value"] == 2
    assert first["led_mode"]["semantic_value"] == "Breathing"
    assert first["led_mode"]["live_write_supported_values"] == ()
    assert "completed-live-read" in first["source"]
