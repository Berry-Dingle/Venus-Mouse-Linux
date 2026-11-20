"""GUI side of reading the mouse: overlay, banners, menu, access panel (fakes only)."""

import json
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

from venus_profile.app_store import AppStore  # noqa: E402
from venus_profile.read_snapshot import memory_ranges  # noqa: E402
from venus_profile.workspace import VenusWorkspace  # noqa: E402

from gui_display import requires_gtk_display  # noqa: E402
from gui_workspace import shared_application  # noqa: E402
from evidence_paths import EVIDENCE, REPO  # noqa: E402

PROJECT = EVIDENCE


def _pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def _factory_document() -> dict:
    analysis = PROJECT / "usb-captures" / "analysis"
    memory_map = json.loads((analysis / "factory_memory_map.json").read_text())
    base = memory_map["base_address"]
    data = (analysis / "factory_memory.bin").read_bytes()
    present = (analysis / "factory_memory_present.bin").read_bytes()
    values = {base + i: data[i] for i in range(len(data)) if present[i]}
    return {"version": 1, "status": "ok", "memory": memory_ranges(values)}


class FakeReader:
    """Stands in for the helper launch; the test decides the outcome."""

    def __init__(self):
        self.started = 0
        self.cancelled = 0
        self._on_done = None

    def __call__(self, on_done):
        self.started += 1
        self._on_done = on_done
        return self

    def cancel(self):
        self.cancelled += 1
        self.finish({"status": "cancelled", "message": "cancelled"})

    def finish(self, document):
        on_done, self._on_done = self._on_done, None
        if on_done:
            on_done(document)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("VENUS_PROFILE_NO_DEVICE_READ", raising=False)
    from venus_profile.gui.application import VenusWindow
    workspace = VenusWorkspace.open(AppStore(tmp_path / "store", macro_library_dir=tmp_path / "Macros"))
    reader = FakeReader()
    window = VenusWindow(shared_application(), workspace, snapshot_runner=reader)
    window.present()
    _pump()
    yield window, reader
    window._allow_close = True
    window.close()
    _pump()


pytestmark = requires_gtk_display


def test_window_does_not_read_unless_asked(setup):
    window, reader = setup
    assert reader.started == 0  # only the real application start-up reads
    assert window._reading_card.get_visible() is False


def test_reading_shows_card_then_applies_the_mouse(setup):
    window, reader = setup
    assert window.read_mouse() is True
    assert reader.started == 1
    assert window._reading_card.get_visible() is True
    assert window._body_overlay.get_child().get_sensitive() is False
    assert window.read_mouse() is False  # one read at a time
    reader.finish(_factory_document())
    assert window._reading_card.get_visible() is False
    assert window._body_overlay.get_child().get_sensitive() is True
    assert window.workspace.profile(1).selected_dpi_stage_raw == 0  # mouse wins
    assert window._banner.get_revealed() is False


@pytest.mark.parametrize(("status", "title", "button"), (
    ("no-device", "No mouse found — showing saved settings", "Read again"),
    ("permission-denied", "The mouse can't be accessed — showing saved settings", "How to fix"),
    ("multiple-devices", "More than one Venus mouse is connected — showing saved settings", "Read again"),
    ("timeout", "The mouse couldn't be read (slow) — showing saved settings", "Read again"),
))
def test_failures_keep_saved_settings_and_explain(setup, status, title, button):
    window, reader = setup
    before = window.workspace.profile(1).to_bytes()
    window.read_mouse()
    reader.finish({"status": status, "message": "slow"})
    assert window.workspace.profile(1).to_bytes() == before
    assert window._banner.get_revealed() is True
    assert window._banner.get_title() == title
    assert window._banner.get_button_label() == button


def test_read_again_and_how_to_fix_buttons(setup):
    window, reader = setup
    window.read_mouse()
    reader.finish({"status": "no-device"})
    window._banner.emit("button-clicked")
    assert reader.started == 2
    reader.finish({"status": "permission-denied"})
    window._banner.emit("button-clicked")
    assert window._stack.get_visible_child_name() == "settings"
    assert window.pages["settings"]._access_revealer.get_reveal_child() is True


def test_cancel_stops_the_read(setup):
    window, reader = setup
    window.read_mouse()
    window._reading_cancel.emit("clicked")
    assert reader.cancelled == 1
    assert window._reading_card.get_visible() is False
    assert "cancelled" in window._banner.get_title()


def test_menu_read_asks_before_replacing_edits(setup, monkeypatch):
    window, reader = setup
    asked = []
    monkeypatch.setattr(window, "_confirm", lambda heading, body, label, callback, **_k: asked.append(heading))
    window._confirm_read_mouse()
    assert reader.started == 1 and asked == []   # nothing edited: reads straight away
    reader.finish({"status": "no-device"})
    window.workspace.profile(2).set_pointer_speed(4)
    window.workspace.edited()
    window._confirm_read_mouse()
    assert asked == ["Read settings from the mouse?"] and reader.started == 1


def test_disabled_by_environment(setup, monkeypatch):
    window, reader = setup
    monkeypatch.setenv("VENUS_PROFILE_NO_DEVICE_READ", "1")
    assert window.read_mouse() is False
    assert reader.started == 0


def test_settings_access_block_is_copy_paste_only():
    from venus_profile.gui.settings import MOUSE_ACCESS_COMMAND, MOUSE_ACCESS_TEXT
    assert 'TAG+="uaccess"' in MOUSE_ACCESS_COMMAND
    assert 'ATTRS{bInterfaceNumber}=="02"' in MOUSE_ACCESS_COMMAND
    assert "GROUP=" not in MOUSE_ACCESS_COMMAND        # no group to create
    assert "udevadm control --reload-rules" in MOUSE_ACCESS_COMMAND
    assert "udevadm trigger" not in MOUSE_ACCESS_COMMAND
    assert "Nothing here is run by the app" in MOUSE_ACCESS_TEXT
    assert "systemd" in MOUSE_ACCESS_TEXT
    settings_source = (REPO / "src/venus_profile/gui/settings.py").read_text()
    assert "pkexec" not in settings_source and "Subprocess" not in settings_source


@pytest.mark.parametrize("shell", ("bash", "dash"))
def test_access_command_writes_exactly_the_rule(shell, tmp_path):
    """Run the copy block for real (sudo tee -> a temp file; no reload)."""
    import shutil
    import subprocess
    from venus_profile.gui.settings import MOUSE_ACCESS_COMMAND, MOUSE_ACCESS_RULE_LINES, MOUSE_ACCESS_RULE_PATH
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} not installed")
    out = tmp_path / "rule"
    script = MOUSE_ACCESS_COMMAND.replace(f"sudo tee {MOUSE_ACCESS_RULE_PATH}", f"tee {out}")
    script = script.replace("sudo udevadm control --reload-rules", "true")
    assert "sudo" not in script
    subprocess.run([shell, "-c", script], check=True, timeout=10)
    assert out.read_text() == "\n".join(MOUSE_ACCESS_RULE_LINES) + "\n"


def test_access_command_is_shell_neutral_and_matches_the_packaged_rule():
    from venus_profile.gui.settings import MOUSE_ACCESS_COMMAND, MOUSE_ACCESS_RULE_LINES
    assert "<<" not in MOUSE_ACCESS_COMMAND                      # no heredoc: fish has none
    assert not any("'" in line for line in MOUSE_ACCESS_RULE_LINES)
    packaged = (REPO / "packaging/udev/70-utechsmart-venus.rules").read_text()
    rules = [line for line in packaged.splitlines() if line and not line.startswith("#")]
    rules = [line.replace(' GROUP="venus-mouse",', "") for line in rules]
    mine = [line for line in MOUSE_ACCESS_RULE_LINES if not line.startswith("#")]
    norm = lambda line: sorted(part.strip() for part in line.split(","))  # noqa: E731
    assert [norm(line) for line in mine] == [norm(line) for line in rules]   # same matcher, minus the group


def test_copy_button_reports_copied():
    from venus_profile.gui.settings import SettingsPage
    from gui_workspace import temp_workspace
    page = SettingsPage(temp_workspace())
    page.show_mouse_access()
    page._copy_access.emit("clicked")
    assert page._copy_access.get_label() == "Copied"


def test_opening_mouse_access_keeps_the_layout():
    from venus_profile.gui.settings import RIGHT_COLUMN_WIDTH, SettingsPage
    from gui_workspace import temp_workspace
    page = SettingsPage(temp_workspace())
    page._access_revealer.set_transition_duration(0)
    left = page.get_first_child()
    right = left.get_next_sibling()
    command = next(label for label in _labels(right) if label.has_css_class("access-command-text"))
    explanation = next(label for label in _labels(right) if label.get_label().startswith("When the app starts"))
    scroller = command.get_ancestor(Gtk.ScrolledWindow)
    # Commands: unwrapped, scrolling sideways only.
    assert not command.get_wrap() and scroller is not None
    assert scroller.get_policy() == (Gtk.PolicyType.ALWAYS, Gtk.PolicyType.NEVER)
    assert not scroller.get_overlay_scrolling()                  # own row under the text, not on top
    frame = scroller.get_parent()
    assert frame.has_css_class("access-command") and not command.has_css_class("access-command")  # frame fixed
    assert explanation.get_visible()
    # Explanation sits under the access button, above the commands: buttons never move.
    assert explanation.get_prev_sibling() is page._access_toggle
    assert explanation.get_next_sibling() is page._access_revealer
    assert page._copy_access.get_parent() is frame.get_parent()           # under the commands, right-aligned
    assert page._copy_access.get_halign() == Gtk.Align.END and frame.get_next_sibling() is page._copy_access
    closed = right.measure(Gtk.Orientation.HORIZONTAL, -1)
    page.show_mouse_access()
    _pump()
    assert page._access_revealer.get_child_revealed()
    assert not explanation.get_visible()
    # The column asks for the same fixed width; long lines never widen it.
    assert right.measure(Gtk.Orientation.HORIZONTAL, -1)[:2] == closed[:2] == (RIGHT_COLUMN_WIDTH, RIGHT_COLUMN_WIDTH)
    page._access_toggle.set_active(False)
    assert explanation.get_visible()


@requires_gtk_display
def test_explanation_returns_only_after_the_close_animation():
    import time
    from venus_profile.gui.settings import SettingsPage
    from gui_workspace import temp_workspace
    Gtk.Settings.get_default().set_property("gtk-enable-animations", True)
    page = SettingsPage(temp_workspace())
    window = Gtk.Window()
    window.set_child(page)
    window.present()
    try:
        explanation = page._explanation
        page._access_revealer.set_transition_duration(0)
        page.show_mouse_access()
        _pump()
        assert not explanation.get_visible()
        page._access_revealer.set_transition_duration(300)
        page._access_toggle.set_active(False)
        _pump()
        assert page._access_revealer.get_child_revealed()      # still collapsing
        assert not explanation.get_visible()                  # would make the card too tall mid-close
        deadline = time.monotonic() + 3
        while page._access_revealer.get_child_revealed() and time.monotonic() < deadline:
            _pump()
            time.sleep(0.01)
        assert not page._access_revealer.get_child_revealed() and explanation.get_visible()
    finally:
        window.destroy()
        _pump()


def _labels(widget):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            yield child
        yield from _labels(child)
        child = child.get_next_sibling()


def test_macros_page_shows_mouse_only_slot():
    from venus_profile.gui.macros.page import MacrosPage
    from gui_workspace import temp_workspace
    workspace = temp_workspace()
    workspace.library.mouse_only[6] = bytes((0x81, 0x01, 0x00)) + bytes(197)
    page = MacrosPage(workspace)
    slot = page.presentation.device_slots[6]
    assert slot.on_mouse_only and not slot.is_empty
    labels = []

    def walk(widget):
        if isinstance(widget, Gtk.Label):
            labels.append(widget.get_text())
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(page._slot_rows[slot.stable_id])
    assert "On mouse only" in labels and "Slot 7" in labels


def test_parse_helper_output_takes_the_last_json_line():
    from venus_profile.gui.device_read import parse_helper_output
    assert parse_helper_output('noise\n{"status": "ok", "memory": []}\n')["status"] == "ok"
    assert parse_helper_output("")["status"] == "bad-output"
    assert parse_helper_output("not json")["status"] == "bad-output"
    assert parse_helper_output('["no status"]')["status"] == "bad-output"


class _FakeProcess:
    instances = []

    def __init__(self, argv, flags):
        self.argv, self.flags = argv, flags
        self.callback = None
        self.killed = False
        _FakeProcess.instances.append(self)

    def communicate_utf8_async(self, _stdin, _cancellable, callback):
        self.callback = callback

    def communicate_utf8_finish(self, result):
        return True, result, ""

    def force_exit(self):
        self.killed = True


def test_snapshot_read_launches_only_the_helper_and_handles_outcomes(monkeypatch):
    import sys
    from venus_profile.gui import device_read
    _FakeProcess.instances.clear()
    monkeypatch.setattr(device_read.Gio.Subprocess, "new", _FakeProcess)
    results = []
    reader = device_read.start_snapshot_read(results.append)
    process = _FakeProcess.instances[-1]
    assert process.argv == [sys.executable, "-m", "venus_profile.read_snapshot"]
    process.callback(process, '{"status": "no-device", "message": "none"}')
    assert results == [{"status": "no-device", "message": "none"}]
    reader.cancel()                                   # after completion: no second result
    assert len(results) == 1
    second = device_read.start_snapshot_read(results.append)
    second.cancel()
    assert results[-1]["status"] == "cancelled" and _FakeProcess.instances[-1].killed
    third = device_read.start_snapshot_read(results.append)
    third._timed_out()
    assert results[-1]["status"] == "timeout" and _FakeProcess.instances[-1].killed


@pytest.mark.parametrize(("original", "tampered"), (
    ('HELPER_MODULE = "venus_profile.read_snapshot"', 'HELPER_MODULE = "venus_profile.guarded_led_mode"'),
    ('argv = [sys.executable, "-m", HELPER_MODULE]', 'argv = [sys.executable, "-m", HELPER_MODULE, "--write"]'),
    ("Gio.Subprocess.new(\n                argv,", "Gio.Subprocess.new(\n                [sys.executable],"),
))
def test_gui_audit_rejects_a_changed_helper_launch(tmp_path, original, tampered):
    from venus_profile.gui import safety
    source = (Path(safety.__file__).parent / "device_read.py").read_text(encoding="utf-8")
    assert original in source
    fake = tmp_path / "device_read.py"
    fake.write_text(source.replace(original, tampered), encoding="utf-8")
    with pytest.raises(RuntimeError):
        safety._audit_helper_launch(fake)


def test_gui_audit_rejects_a_second_launch(tmp_path):
    from venus_profile.gui import safety
    source = (Path(safety.__file__).parent / "device_read.py").read_text(encoding="utf-8")
    fake = tmp_path / "device_read.py"
    fake.write_text(source + "\n\ndef extra():\n    return Gio.Subprocess.new(argv, 0)\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        safety._audit_helper_launch(fake)


def test_packaged_udev_rule_also_grants_logged_in_user_access():
    rule = (REPO / "packaging/udev/70-utechsmart-venus.rules").read_text()
    line = next(item for item in rule.splitlines() if 'GROUP="venus-mouse"' in item)
    assert 'TAG+="uaccess"' in line and 'bInterfaceNumber}=="02"' in line
