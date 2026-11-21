"""Settings page desktop rows in the window: mirror at start-up, change only on Apply (fake desktop)."""

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from venus_profile.app_store import AppStore  # noqa: E402
from venus_profile.desktop_settings import (  # noqa: E402
    ACCELERATION, DOUBLE_CLICK, POINTER, DesktopSettingsError, DesktopValues,
)
from venus_profile.workspace import VenusWorkspace  # noqa: E402

from gui_display import requires_gtk_display  # noqa: E402
from gui_workspace import shared_application  # noqa: E402

pytestmark = requires_gtk_display


def _pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


class FakeDesktop:
    name = "KDE Plasma"
    scope_note = "Pointer speed and acceleration apply to this mouse only."

    def __init__(self, values=DesktopValues(14, True, 7), refuse=False):
        self.values = values
        self.refuse = refuse
        self.writes = []

    def read(self):
        return self.values

    def write(self, **changes):
        self.writes.append(changes)
        if self.refuse:
            raise DesktopSettingsError("KDE refused the pointer setting (test)")
        v = self.values
        self.values = DesktopValues(changes.get("pointer_level", v.pointer_level),
                                    changes.get("acceleration_on", v.acceleration_on),
                                    changes.get("double_click_level", v.double_click_level))


@pytest.fixture
def make_window(tmp_path):
    from venus_profile.gui.application import VenusWindow
    windows = []

    def make(desktop):
        workspace = VenusWorkspace.open(AppStore(tmp_path / f"store{len(windows)}"))
        win = VenusWindow(shared_application(), workspace, desktop_settings=desktop)
        alerts = []
        win._alert = lambda heading, body: alerts.append((heading, body))
        win.alerts = alerts
        win.present()
        _pump()
        windows.append(win)
        return win

    yield make
    for win in windows:
        win._allow_close = True
        win.close()
    _pump()


def _page(win):
    return win.pages["settings"]


def test_the_desktop_wins_at_start_up_and_is_not_an_edit(make_window):
    win = make_window(FakeDesktop(DesktopValues(14, True, 7)))
    page = _page(win)
    assert all(row.get_visible() for row in (page._pointer, page._acceleration, page._double_click))
    assert page._pointer.value_label.get_text() == "14"
    assert page._acceleration.scale.get_value() == 1 and page._acceleration.value_label.get_text() == "On"
    assert page._double_click.value_label.get_text() == "7 · 410 ms"
    assert all(win.workspace.profile(slot).pointer_speed == 14 for slot in range(1, 6))
    assert win.workspace.edited_slots() == ()
    assert win._actions["apply-to-mouse"].get_enabled() is False
    assert "KDE Plasma" in page._note.get_text()


def test_unavailable_settings_are_hidden(make_window):
    win = make_window(FakeDesktop(DesktopValues(None, None, 7)))     # e.g. KDE with the mouse unplugged
    page = _page(win)
    assert not page._pointer.get_visible() and not page._acceleration.get_visible()
    assert page._double_click.get_visible()
    unsupported = make_window(None)
    assert not any(row.get_visible() for row in (_page(unsupported)._pointer, _page(unsupported)._acceleration,
                                                 _page(unsupported)._double_click))
    assert "hidden" in _page(unsupported)._note.get_text()


def test_edits_wait_for_apply_which_works_without_the_mouse(make_window):
    desktop = FakeDesktop(DesktopValues(14, True, 7))
    win = make_window(desktop)
    page = _page(win)
    page._pointer.scale.set_value(5)
    page._acceleration.scale.set_value(0)
    page._double_click.scale.set_value(3)
    assert desktop.writes == []                                       # nothing changes before Apply
    assert win._actions["apply-to-mouse"].get_enabled() is True       # even though the mouse isn't live
    assert win._has_unapplied_changes()
    win._apply_to_mouse()
    assert desktop.writes == [{"pointer_level": 5, "acceleration_on": False, "double_click_level": 3}]
    assert all(win.workspace.profile(slot).pointer_speed == 5 for slot in range(1, 6))   # mirrored to all
    assert all(win.workspace.profile(slot).acceleration == 1 for slot in range(1, 6))
    assert win._actions["apply-to-mouse"].get_enabled() is False
    assert not win._has_unapplied_changes()


def test_cancel_drops_a_desktop_edit_and_nothing_is_written(make_window):
    desktop = FakeDesktop()
    win = make_window(desktop)
    _page(win)._pointer.scale.set_value(3)
    win.workspace.cancel_edits(1)
    assert win._desktop_changes() == {} and desktop.writes == []
    assert _page(win)._pointer.value_label.get_text() == "14"


def test_an_edit_made_before_switching_profile_is_still_applied(make_window):
    desktop = FakeDesktop()
    win = make_window(desktop)
    _page(win)._double_click.scale.set_value(2)
    win.workspace.select_slot(3)
    assert win._desktop_changes() == {"double_click_level": 2}
    win._apply_to_mouse()
    assert desktop.writes == [{"double_click_level": 2}]


def test_a_refusal_is_shown_and_nothing_is_committed(make_window):
    desktop = FakeDesktop(refuse=True)
    win = make_window(desktop)
    _page(win)._pointer.scale.set_value(18)
    win._apply_to_mouse()
    assert win.alerts and "not changed" in win.alerts[0][0] and "refused" in win.alerts[0][1]
    assert win.workspace.edited_slots() == (1,)                       # the edit still waits
    assert win._actions["apply-to-mouse"].get_enabled() is True


def test_returning_to_the_app_mirrors_desktop_changes_unless_edits_wait(make_window):
    desktop = FakeDesktop(DesktopValues(14, True, 7))
    win = make_window(desktop)
    win.is_active = lambda: True                                      # the user comes back to the app
    desktop.values = DesktopValues(6, False, 9)                       # changed in System Settings
    win._activated(win, None)
    assert win.workspace.profile(1).pointer_speed == 6 and win.workspace.profile(4).acceleration == 1
    assert win.workspace.edited_slots() == ()
    _page(win)._pointer.scale.set_value(12)                           # a pending edit is never overwritten
    desktop.values = DesktopValues(2, False, 9)
    win._activated(win, None)
    assert win.workspace.profile(1).pointer_speed == 12


def test_close_overlay_apply_writes_the_desktop_then_closes(make_window):
    desktop = FakeDesktop()
    win = make_window(desktop)
    closed = []
    win._close_now = lambda: closed.append(True)
    _page(win)._acceleration.scale.set_value(0)
    win.close()
    _pump()
    assert win._unapplied_dialog is not None
    win._unapplied_response(win._unapplied_dialog, "apply")
    assert desktop.writes == [{"acceleration_on": False}] and closed == [True]


def test_available_names_cover_the_three_rows():
    assert DesktopValues(1, False, 1).available == {POINTER, ACCELERATION, DOUBLE_CLICK}


def test_acceleration_is_a_two_stop_slider_in_line_with_the_others(make_window):
    win = make_window(FakeDesktop(DesktopValues(10, False, 5)))
    row = _page(win)._acceleration
    assert row.scale.get_adjustment().get_lower() == 0 and row.scale.get_adjustment().get_upper() == 1
    assert row.pc_tag is not None and row.value_label.get_text() == "Off"
    row.scale.set_value(1)
    assert row.value_label.get_text() == "On" and win.workspace.profile(1).acceleration == 2
