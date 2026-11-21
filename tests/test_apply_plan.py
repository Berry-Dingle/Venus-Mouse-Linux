"""The offline Apply preview reproduces saved factory device memory."""

import json

import pytest

from venus_profile import apply_plan
from venus_profile.apply_plan import Evidence, build_apply_preview, dpi_lookup, report4_slot_report
from venus_profile.app_store import AppStore
from venus_profile.workspace import VenusWorkspace


@pytest.fixture
def factory_memory(project_root):
    analysis = project_root / "usb-captures" / "analysis"
    meta = json.loads((analysis / "factory_memory_map.json").read_text())
    base = meta["base_address"]
    data = (analysis / "factory_memory.bin").read_bytes()
    present = (analysis / "factory_memory_present.bin").read_bytes()

    def read(address, length):
        offset = address - base
        assert all(present[offset:offset + length]), f"0x{address:04X} not in saved read"
        return data[offset:offset + length]
    return read


@pytest.fixture
def workspace(tmp_path):
    return VenusWorkspace.open(AppStore(tmp_path / "store"))


def test_lookup_table_matches_rea_confirmed_bytes():
    assert [dpi_lookup(raw) for raw in (9, 19, 39, 71, 106)] == [
        (0x16, 0), (0x2D, 0), (0x5A, 0), (0x5C, 1), (0x7C, 4),
    ]
    with pytest.raises(ValueError):
        dpi_lookup(107)


def test_factory_dpi_buttons_polling_and_lighting_match_saved_memory(workspace, factory_memory):
    preview = build_apply_preview(workspace)
    assert preview.executable is False
    for item in preview.by_area("DPI"):
        if item.profile == 1 and item.address != 0x0042:
            assert item.payload == factory_memory(item.address, len(item.payload)), item
    buttons = preview.by_area("Buttons")
    assert len(buttons) == 100
    for item in buttons:
        assert item.payload == factory_memory(item.address, 4), item
    polling = preview.by_area("Polling")
    assert polling[0].payload == factory_memory(0x0032, 5) + b"\0"
    lighting = preview.by_area("Lighting")
    # All five profiles' lighting records match the saved factory memory
    # (record at 0x0449 + 8*(profile-1), brightness 6 bytes later).
    assert len(lighting) == 10
    for profile in range(1, 6):
        record, brightness = [item for item in lighting if item.profile == profile]
        address = 0x0449 + 8 * (profile - 1)
        assert (record.address, brightness.address) == (address, address + 6)
        assert record.payload == factory_memory(address, 6), profile
        assert brightness.payload == factory_memory(address + 6, 1), profile
    assert not any(item.evidence is Evidence.UNRESOLVED for item in lighting)


def test_factory_selected_stage_differs_from_saved_memory(workspace, factory_memory):
    # Documented provenance gap: factory PFDs store DPI=1, the saved device
    # image has stage 0 selected for profile 1.
    stage = next(item for item in build_apply_preview(workspace).by_area("DPI") if item.address == 0x0042)
    assert stage.payload == b"\x01" and factory_memory(0x0042, 1) == b"\x00"


def test_macro_report_matches_saved_slot_capture(workspace, project_root):
    entry = workspace.library.import_file(project_root / "ProfilesMacros" / "POE-Flasks.MSMACRO")
    workspace.load_macro_into_slot(entry.macro_id, 0)
    report = report4_slot_report(entry, 0)
    assert len(report) == 256 and report[:8] == bytes.fromhex("04 F3 78 04 C8 00 00 00")
    assert report[8 + entry.macro.count * 3:] == bytes(256 - 8 - entry.macro.count * 3)
    macros = build_apply_preview(workspace).by_area("Macros")
    assert [item.address for item in macros] == [0x0478]
    with pytest.raises(ValueError):
        report4_slot_report(entry, 15)


def test_captured_slot_one_body_bytes(project_root):
    captured = (project_root / "usb-captures" / "analysis" / "macro_slot1_a_press_release"
                / "macro_slot1.bin").read_bytes()
    assert captured[:9] == bytes.fromhex("84 04 00 06 01 00 04 04 00")


def test_macro_button_records_use_bank_loop_settings(workspace, project_root):
    entry = workspace.library.import_file(project_root / "ProfilesMacros" / "POE-Flasks.MSMACRO")
    workspace.load_macro_into_slot(entry.macro_id, 0)
    workspace.assign_macro_to_button(0, 1, 7)
    record = next(item for item in build_apply_preview(workspace).by_area("Buttons")
                  if item.profile == 1 and item.address == 0x009A)
    flags, count = apply_plan._macro_loop(entry)
    assert record.payload == bytes((0x91, flags, count & 0xFF, count >> 8))
    workspace.unload_slot(0)
    assert workspace.library.slot_macro(0) is None


@pytest.mark.parametrize("loop_type, loop_time, expected", [
    (0, 3, (0x00, 3)),            # fixed: count = LoopTime
    (0, 255, (0x00, 255)),
    (1, 4, (0x80, 4)),            # while held: 0x80, count = LoopTime
    (2, 9, (0x00, 1)),            # run once: count forced to 1
    (3, 1, (0x40, 0xFFFF)),       # toggle: 0x40, count forced to 0xFFFF (hid.exe 0x41317F-0x41318D)
    (3, 10, (0x40, 0xFFFF)),      # live: toggle with count 10 played 10 times instead of toggling
])
def test_macro_loop_matches_the_windows_compiler(workspace, loop_type, loop_time, expected):
    from venus_profile.macro_library import set_loop
    entry = workspace.library.create("Loop probe")
    set_loop(entry.macro, loop_type, loop_time)
    assert apply_plan._macro_loop(entry) == expected


def test_toggle_record_matches_the_one_read_from_the_mouse(workspace, project_root):
    """Saved read 20261119T214209: P1 button 7 holds 91 44 ff ff (toggle, slot 5)."""
    from venus_profile.lighting_preview_plan import button_record_address
    read = project_root / "usb-captures" / "live-reads" / "venus_read_20261119T214209.289848Z" / "live_memory.bin"
    address = button_record_address(1, 7)
    assert read.read_bytes()[address:address + 4] == bytes.fromhex("9144ffff")
    from venus_profile.macro_library import set_loop
    entry = workspace.library.create("Toggle probe")
    set_loop(entry.macro, 3, 1)
    flags, count = apply_plan._macro_loop(entry)
    assert bytes((0x91, 4 | flags, count & 0xFF, count >> 8)) == bytes.fromhex("9144ffff")


def test_preview_module_has_no_transport_surface():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(apply_plan))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"fcntl", "os", "subprocess", "socket", "usb", "hid"})
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert names.isdisjoint({"send", "execute", "write_report", "apply", "main"})
