"""Decoding reads of the mouse and applying them to the working set (offline)."""

import json
import tempfile
from pathlib import Path

import pytest

from venus_profile import button_functions as bf
from venus_profile.actions import ActionRecord
from venus_profile.apply_plan import build_apply_preview
from venus_profile.app_store import AppStore
from venus_profile.device_decode import decode_memory, decode_snapshot
from venus_profile.macroset import report4_address
from venus_profile.pfd import LedMode
from venus_profile.read_snapshot import memory_ranges
from venus_profile.workspace import VenusWorkspace
from evidence_paths import EVIDENCE  # noqa: E402

PROJECT = EVIDENCE
ANALYSIS = PROJECT / "usb-captures" / "analysis"
LIVE_READS = sorted((PROJECT / "usb-captures" / "live-reads").glob("venus_read_*"))


def _image(directory: Path, prefix: str) -> dict[int, int]:
    memory_map = json.loads((directory / f"{prefix}_memory_map.json").read_text())
    base = memory_map["base_address"]
    data = (directory / f"{prefix}_memory.bin").read_bytes()
    present = (directory / f"{prefix}_memory_present.bin").read_bytes()
    return {base + i: data[i] for i in range(len(data)) if present[i]}


def factory_values() -> dict[int, int]:
    return _image(ANALYSIS, "factory")


def _workspace() -> VenusWorkspace:
    root = Path(tempfile.mkdtemp())
    return VenusWorkspace.open(AppStore(root / "store", macro_library_dir=root / "Macros"))


def test_factory_read_matches_the_factory_profiles():
    """Everything decodable agrees with the shipped factory PFDs."""
    workspace = _workspace()
    before = [workspace.profile(n).to_bytes() for n in range(1, 6)]
    report = workspace.apply_device_read(decode_memory(factory_values()))
    assert report.notices == []
    # Known provenance gap: the factory image shows stage 1 selected while
    # RSProfile1.pfd says stage 2 (handover assessment §4.4).
    assert [c for c in report.changes if c.startswith("Profile")] == ["Profile 1: DPI stage"]
    assert workspace.profile(1).selected_dpi_stage_raw == 0
    for number in range(2, 6):
        assert workspace.profile(number).to_bytes() == before[number - 1]


def test_factory_lighting_decodes_per_profile():
    decoded = decode_memory(factory_values())
    colours = [item.lighting.rgb for item in decoded.profiles]
    assert colours == [(0xFF, 0, 0), (0, 0, 0xFF), (0, 0xFF, 0), (0xFF, 0, 0xFF), (0xFF, 0xFF, 0)]
    assert all(item.lighting.mode == LedMode.BREATHING and item.lighting.companion == 5
               and item.lighting.brightness == 2 for item in decoded.profiles)


def _workspace_as_memory(workspace) -> dict[int, int]:
    """The memory a full Apply of this working set would produce."""
    values = {}
    for slot in range(15):                    # empty slots read back as zeros
        for offset in range(200):
            values[report4_address(slot) + offset] = 0
    for write in build_apply_preview(workspace).writes:
        if write.address is None or not write.payload:
            continue
        payload = write.payload[8:208] if write.area == "Macros" else write.payload
        for offset, value in enumerate(payload):
            values[write.address + offset] = value
    return values


def test_round_trip_of_the_working_set_changes_nothing():
    workspace = _workspace()
    workspace.profile(2).set_led_color_rgb(0x12, 0x34, 0x56)
    workspace.profile(3).set_dpi_stage_dpi(2, 3200)
    workspace.profile(4).set_polling_rate_hz(1000)
    bf.set_single_key(workspace.profile(5), 9, 0x14)
    workspace.edited()
    report = workspace.apply_device_read(decode_memory(_workspace_as_memory(workspace)))
    assert report.notices == []
    assert report.profiles_changed == set()


def test_mouse_wins_for_each_decoded_area():
    workspace = _workspace()
    other = _workspace()
    other.profile(1).set_led_color_rgb(1, 2, 3)
    other.profile(1).set_led_mode(LedMode.WAVE)
    other.profile(1).set_brightness(3)
    other.profile(2).set_dpi_stage_dpi(1, 1600)
    other.profile(2).set_dpi_stage_enabled(5, False)
    other.profile(3).set_selected_dpi_stage(4)
    other.profile(4).set_polling_rate_hz(125)
    bf.set_simple_function(other.profile(5), 3, 43)  # Volume Up
    report = workspace.apply_device_read(decode_memory(_workspace_as_memory(other)))
    assert report.profiles_changed == {1, 2, 3, 4, 5}
    p = workspace.profile
    assert p(1).led_color_rgb == (1, 2, 3) and p(1).led_mode_raw == int(LedMode.WAVE) and p(1).brightness_raw == 3
    assert p(2).dpi_stage_dpi(1) == 1600 and p(2).dpi_stage_enabled(5) is False
    assert p(3).selected_dpi_stage_raw == 3
    assert p(4).polling_rate_raw == 8
    assert bf.read_assignment(p(5), 3).function_code == 43
    # Changed slots are labelled and Cancel now returns to the mouse's state.
    assert workspace.loaded_name(1) == "From mouse"
    assert workspace.edited_slots() == ()


def test_unrecognised_values_keep_saved_settings_and_are_reported():
    values = factory_values()
    values[0x044C] = 0x09                     # unknown lighting mode byte
    for offset, byte in enumerate(bytes.fromhex("7E 01 02 03")):
        values[0x0082 + offset] = byte        # unknown button record, profile 1 button 1
    workspace = _workspace()
    saved_colour = workspace.profile(1).led_color_rgb
    report = workspace.apply_device_read(decode_memory(values))
    assert any("lighting mode bytes 09" in note for note in report.notices)
    assert any("7E 01 02 03" in note for note in report.notices)
    assert workspace.profile(1).led_color_rgb == saved_colour
    assert bf.read_assignment(workspace.profile(1), 1).function_code == bf.FUNCTION_LEFT_CLICK


def test_missing_bytes_are_reported_not_guessed():
    values = factory_values()
    for address in range(0x0451, 0x0458):    # profile 2 lighting not read
        values.pop(address, None)
    report = _workspace().apply_device_read(decode_memory(values))
    assert "Profile 2 lighting was not read; kept as saved" in report.notices


def _macro_values(workspace, slot: int, records):
    entry = workspace.library.create("Probe")
    entry.macro.set_actions(records)
    from venus_profile.apply_plan import report4_slot_report
    data = report4_slot_report(entry, slot)[8:208]
    workspace.library.delete(entry.macro_id)
    values = factory_values()
    for offset, value in enumerate(data):
        values[report4_address(slot) + offset] = value
    return values


def test_macro_slot_matching_a_library_macro_links_to_it():
    workspace = _workspace()
    records = [ActionRecord.keyboard(0x04, True), ActionRecord.action_delay_ms(10), ActionRecord.keyboard(0x04, False)]
    values = _macro_values(workspace, 2, records)
    entry = workspace.library.create("Opener")
    entry.macro.set_actions(records)
    for offset, byte in enumerate(bytes((0x91, 0x02, 0x01, 0x00))):
        values[0x009A + offset] = byte        # profile 1 button 7 -> slot 3
    workspace.apply_device_read(decode_memory(values))
    assert workspace.library.slot_macro(2).name == "Opener"
    view = bf.read_assignment(workspace.profile(1), 7)
    assert (view.function_code, view.key_number, view.macro_name) == (bf.FUNCTION_MACRO, 2, "Opener")


def test_macro_slot_not_in_library_is_shown_not_added():
    workspace = _workspace()
    records = [ActionRecord.mouse_button(1, True), ActionRecord.mouse_button(1, False)]
    values = _macro_values(workspace, 4, records)
    report = workspace.apply_device_read(decode_memory(values))
    assert workspace.library.slot_macro(4) is None
    assert 4 in workspace.library.mouse_only
    assert [entry.name for entry in workspace.library.entries] == []    # nothing added
    assert "Device slot 5" in report.changes
    workspace.save_all()
    assert not list(workspace.library.folder.glob("*.MSMACRO"))          # nothing written
    # Loading a library macro into the slot replaces the mouse-only body.
    entry = workspace.library.create("Replacement")
    workspace.load_macro_into_slot(entry.macro_id, 4)
    assert 4 not in workspace.library.mouse_only


def test_fire_count_on_the_mouse_is_adopted_without_a_notice():
    values = factory_values()
    address = 0x0082 + 4 * 3                                              # profile 1 button 4
    for i, v in enumerate(bytes.fromhex("99 81 04 01")):
        values[address + i] = v
    workspace = _workspace()
    report = workspace.apply_device_read(decode_memory(values))
    fire = bf.read_assignment(workspace.profile(1), 4).fire
    assert (fire.trigger, fire.delay_ms, fire.count) == (bf.FIRE_TRIGGER_LEFT, 10, 4)
    assert not [n for n in report.notices if "fires" in n]
    again = workspace.apply_device_read(decode_memory(values))           # same mouse: nothing changes
    assert not again.changed


def test_factory_slot_8_filler_decodes_as_an_empty_slot():
    values = factory_values()
    assert values[report4_address(7)] == 0xFF and values[report4_address(7) + 1] == 0xFF   # factory image
    workspace = _workspace()
    workspace.apply_device_read(decode_memory(values))
    assert workspace.library.bank.slots[7] is None and 7 not in workspace.library.mouse_only
    values[report4_address(7) + 2] = 0x01                                    # anything else stays mouse-only
    workspace.apply_device_read(decode_memory(values))
    assert 7 in workspace.library.mouse_only


def test_dpi_stage_from_the_mouse_becomes_the_start_up_stage():
    values = factory_values()
    values[0x0102] = 3                        # profile 2 currently on stage 4
    workspace = _workspace()
    workspace.apply_device_read(decode_memory(values))
    assert workspace.profile(2).selected_dpi_stage_raw == 3


def test_snapshot_document_round_trip():
    document = {"version": 1, "status": "ok", "memory": memory_ranges(factory_values())}
    assert decode_snapshot(document).profiles[2].lighting.rgb == (0, 0xFF, 0)
    with pytest.raises(ValueError):
        decode_snapshot({"status": "no-device"})


@pytest.mark.skipif(not LIVE_READS, reason="saved live reads not present")
def test_every_saved_linux_read_decodes_and_applies():
    assert len(LIVE_READS) >= 71              # 71 from July 2026; new user-run reads are added over time
    for directory in LIVE_READS:
        values = _image(directory, "live")
        report = _workspace().apply_device_read(decode_memory(values))
        unexpected = [note for note in report.notices
                      if "not recognised" in note and "record" not in note]
        assert unexpected == [], (directory.name, unexpected)


def test_saved_dpi_button_reads_decode_the_documented_stages():
    """docs/DPI_STAGE.md: five reads with 0x0042 = 00..04 after DPI+ presses."""
    names = ["20260728T092859.409571Z", "20260728T092914.377530Z", "20260728T092928.246118Z",
             "20260728T092944.934143Z", "20260728T093002.332739Z"]
    stages = []
    for name in names:
        directory = PROJECT / "usb-captures" / "live-reads" / f"venus_read_{name}"
        if not directory.exists():
            pytest.skip("saved live reads not present")
        stages.append(decode_memory(_image(directory, "live")).profiles[0].current_stage)
    assert stages == [0, 1, 2, 3, 4]


def test_active_profile_is_decoded_from_0x002c():
    from venus_profile.device_decode import decode_memory
    assert decode_memory({0x002C: 0}).active_profile == 1
    assert decode_memory({0x002C: 4}).active_profile == 5
    assert decode_memory({0x002C: 5}).active_profile is None
    assert decode_memory({}).active_profile is None


def test_scroll_lines_are_decoded_and_the_mouse_wins():
    from venus_profile.device_decode import decode_memory
    values = {0x0020 + i: v for i, v in enumerate(bytes.fromhex("03 00 01 00 05 00 01 00 3f 00"))}
    decoded = decode_memory(values)
    assert decoded.scroll_lines == (3, 1, 5, 1, 63)
    assert decode_memory({0x0020 + i: v for i, v in enumerate(bytes(10))}).scroll_lines is None    # 0 invalid
    assert decode_memory({0x0020 + i: 1 if i == 1 else 2 for i in range(10)}).scroll_lines is None  # companion
    workspace = _workspace()
    full = factory_values()
    full.update(values)
    workspace.apply_device_read(decode_memory(full))
    assert [workspace.profile(p).scroll_speed_raw for p in range(1, 6)] == [3, 1, 5, 1, 63]



def test_a_lone_modifier_on_the_mouse_is_adopted():
    values = factory_values()
    address = 0x0082 + 4 * 8                                              # profile 1 button 9
    for i, v in enumerate(bytes.fromhex("8F 02 00 00")):                  # Shift on its own
        values[address + i] = v
    workspace = _workspace()
    report = workspace.apply_device_read(decode_memory(values))
    view = bf.read_assignment(workspace.profile(1), 9)
    assert view.combo.lone_modifier == 0xE1 and view.device_record == bytes.fromhex("8F 02 00 00")
    assert not [n for n in report.notices if "8F 02" in n]
