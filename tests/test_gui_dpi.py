"""DPI page bound to real profile data."""

from pathlib import Path

import pytest

from venus_profile.app_store import AppStore
from venus_profile.gui.dpi.model import DpiRuleError, ProfileDpiState, clamp_dpi, confirmed_dpi_values
from venus_profile.workspace import VenusWorkspace

from gui_display import requires_gtk_display


@pytest.fixture
def workspace(tmp_path):
    return VenusWorkspace.open(AppStore(tmp_path / "store"))


def test_state_shows_real_factory_stages(workspace):
    state = ProfileDpiState(workspace)
    assert [stage.dpi for stage in state.stages] == [1000, 2000, 4000, 8200, 16400]
    assert all(stage.enabled for stage in state.stages)
    assert state.default_stage == 2  # factory PFDs store DPI=1 (zero-based)


@pytest.mark.parametrize("dpi", [200, 6200, 6400, 12400, 12600, 16200, 16400])
def test_confirmed_dpi_domain_is_accepted(dpi):
    assert dpi in confirmed_dpi_values()
    assert clamp_dpi(dpi) == dpi


@pytest.mark.parametrize("dpi,expected", [(199, 200), (201, 200), (6300, 6200), (12500, 12400), (16300, 16200), (16401, 16400)])
def test_dpi_values_clamp_to_confirmed_steps(dpi, expected):
    assert clamp_dpi(dpi) == expected


def test_edits_write_to_the_selected_profile_only(workspace):
    state = ProfileDpiState(workspace)
    workspace.select_slot(2)
    state.set_dpi(1, 1600)
    state.set_default_stage(4)
    assert workspace.profile(2).dpi_stage_dpi(1) == 1600
    assert workspace.profile(2).selected_dpi_stage_raw == 3
    assert workspace.profile(1).dpi_stage_dpi(1) == 1000
    assert workspace.unsaved_profile_slots() == (2,)


def test_no_op_edits_do_not_dirty_the_profile(workspace):
    state = ProfileDpiState(workspace)
    state.set_dpi(1, 1000)
    state.set_enabled(1, True)
    state.set_default_stage(2)
    assert not workspace.dirty


def test_rules_protect_default_and_last_enabled_stage(workspace):
    state = ProfileDpiState(workspace)
    with pytest.raises(DpiRuleError):
        state.set_enabled(2, False)
    for stage in (1, 3, 4):
        state.set_enabled(stage, False)
    with pytest.raises(DpiRuleError):
        state.set_default_stage(1)  # stage 1 is disabled
    state.set_enabled(5, False)
    with pytest.raises(DpiRuleError):
        state.set_default_stage(5)
    assert [stage.enabled for stage in state.stages] == [False, True, False, False, False]
    with pytest.raises(DpiRuleError):
        state.set_enabled(2, False)


def test_asymmetric_and_unknown_values_are_displayed_not_rewritten(workspace):
    profile = workspace.profile(1)
    import struct
    blob = bytearray(profile.dpi_stage_struct.data)
    struct.pack_into("<I", blob, 8, 15)  # stage 1 Y = 1600, X stays 1000
    struct.pack_into("<I", blob, 20, 200)  # stage 2 X = unmapped raw step
    from venus_profile.checksum import ProfileStruct
    profile.document.set(profile.group_section, "DPIStageValue",
                         ProfileStruct.from_data(bytes(blob)).encode(preserve_text=False))
    workspace.save_all()
    stages = ProfileDpiState(workspace).stages
    assert stages[0].display == "X 1,000 · Y 1,600"
    assert stages[1].display.startswith("X raw 200") or stages[1].display.startswith("Unknown")
    assert not workspace.dirty


@requires_gtk_display
def test_page_refresh_never_creates_edits(workspace):
    from venus_profile.gui.dpi.page import DpiPage
    profile3 = workspace.profile(3)
    profile3.set_dpi_stage_enabled(5, False)
    workspace.save_all()
    page = DpiPage(workspace)
    for slot in (3, 1, 3, 5, 1):
        workspace.select_slot(slot)
    assert not workspace.dirty
    assert page._stage_checks[5].get_active() is True
    workspace.select_slot(3)
    assert page._stage_checks[5].get_active() is False
    assert not workspace.dirty


@requires_gtk_display
def test_page_controls_edit_the_profile(workspace):
    from venus_profile.gui.dpi.page import DpiPage
    page = DpiPage(workspace)
    page._stage_scales[1].set_value(15)  # raw step 15 = 1600 DPI
    assert workspace.profile(1).dpi_stage_dpi(1) == 1600
    assert page._stage_value_labels[1].get_text() == "1,600"
    page._stage_buttons[4].emit("clicked")
    assert workspace.profile(1).selected_dpi_stage_raw == 3
    page._stage_checks[4].set_active(False)  # default stage: refused
    assert page._stage_checks[4].get_active() is True
    assert "cannot be disabled" in page._message.get_text()


def test_dpi_page_has_no_device_surface_or_dead_apply():
    root = Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "dpi"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert all(token not in source for token in ("DeviceSession", "hidraw", "discover_venus", "/dev/"))
    # No dead Apply button on the page (the window's Apply sends DPI; the
    # Defaults tooltip may mention it).
    assert 'label="Apply"' not in source and "label='Apply'" not in source
    assert "fixture" not in source.lower()
