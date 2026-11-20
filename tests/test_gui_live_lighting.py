"""Always-live lighting: GUI controller, window wiring, launcher (fakes only).

No test launches the real helper: the window gets a fake writer, and the
launcher tests replace ``Gio.Subprocess.new``.
"""

import json
from pathlib import Path
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from venus_profile.app_store import AppStore  # noqa: E402
from venus_profile.lighting_preview_plan import RECORD_ADDRESSES, parse_any_request  # noqa: E402
from venus_profile.read_snapshot import memory_ranges  # noqa: E402
from venus_profile.workspace import VenusWorkspace  # noqa: E402

from gui_display import requires_gtk_display  # noqa: E402
from gui_workspace import shared_application  # noqa: E402
from evidence_paths import EVIDENCE  # noqa: E402

PROJECT = EVIDENCE


def _pump():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def _factory_values() -> dict[int, int]:
    analysis = PROJECT / "usb-captures" / "analysis"
    memory_map = json.loads((analysis / "factory_memory_map.json").read_text())
    base = memory_map["base_address"]
    data = (analysis / "factory_memory.bin").read_bytes()
    present = (analysis / "factory_memory_present.bin").read_bytes()
    return {base + i: data[i] for i in range(len(data)) if present[i]}


def _document(lighting: dict[int, str] | None = None) -> dict:
    values = _factory_values()
    for profile, text in (lighting or {}).items():
        for offset, value in enumerate(bytes.fromhex(text)):
            values[RECORD_ADDRESSES[profile - 1] + offset] = value
    return {"version": 1, "status": "ok", "memory": memory_ranges(values)}


def _records(window) -> dict[str, str]:
    """What the mouse would hold if it matched the app now (all five profiles)."""
    from venus_profile.lighting_preview_plan import target_from_profile
    return {str(p): target_from_profile(p, window.workspace.profile(p)).lighting_bytes().hex()
            for p in range(1, 6)}


class FakeReader:
    def __init__(self):
        self.started = 0
        self._pending = []

    def __call__(self, on_done):
        self.started += 1
        self._pending.append(on_done)
        return self

    def cancel(self):
        self.finish({"status": "cancelled"})

    def finish(self, document):
        if self._pending:
            self._pending.pop(0)(document)


class FakeWatcher:
    def __init__(self):
        self.on_event = self.on_end = None
        self.stopped = 0

    def __call__(self, on_event, on_end):
        self.on_event, self.on_end = on_event, on_end
        return self

    def stop(self):
        self.stopped += 1


class FakeWriter:
    """Records helper requests: ``requests`` = lighting targets, ``switches`` = profile numbers."""

    def __init__(self):
        self.calls = []
        self._pending = []

    def __call__(self, request, on_done):
        self.calls.append(parse_any_request(request))
        self._pending.append(on_done)
        return self

    @property
    def requests(self):
        return [payload for kind, payload in self.calls if kind == "lighting"]

    @property
    def switches(self):
        return [payload for kind, payload in self.calls if kind == "profile"]

    @property
    def polling(self):
        return [payload for kind, payload in self.calls if kind == "polling"]

    @property
    def dpi(self):
        return [payload for kind, payload in self.calls if kind == "dpi"]

    @property
    def macros(self):
        return [payload for kind, payload in self.calls if kind == "macros"]

    @property
    def scroll(self):
        return [payload for kind, payload in self.calls if kind == "scroll"]

    @property
    def running(self):
        return bool(self._pending)

    def finish(self, document):
        self._pending.pop(0)(document)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("VENUS_PROFILE_NO_DEVICE_READ", raising=False)
    from venus_profile.gui.application import VenusWindow
    workspace = VenusWorkspace.open(AppStore(tmp_path / "store", macro_library_dir=tmp_path / "Macros"))
    reader, watcher, writer = FakeReader(), FakeWatcher(), FakeWriter()
    window = VenusWindow(shared_application(), workspace, snapshot_runner=reader, event_watcher=watcher,
                         lighting_writer=writer)
    window.present()
    _pump()
    yield window, reader, watcher, writer
    window._allow_close = True
    window._live_lighting._running = None
    window.close()
    _pump()


pytestmark = requires_gtk_display


def _read_ok(window, reader, lighting=None):
    window.read_mouse()
    reader.finish(_document(lighting))


def _lighting(window):
    return window.pages["lighting"].presentation


def _select(window, writer, slot):
    """Select a profile in the app and let the resulting mouse switch succeed."""
    window.workspace.select_slot(slot)
    if writer.running and writer.calls[-1] == ("profile", slot):
        writer.finish({"status": "ok", "active_profile": slot})


# --------------------------------------------------------------- window

def test_nothing_is_sent_before_the_mouse_is_read(setup):
    window, reader, _watcher, writer = setup
    _lighting(window).select_colour((1, 2, 3))
    assert writer.requests == []
    assert window.pages["lighting"]._colour_note.get_text().endswith("kept in the app only until the mouse is read.")


def test_reading_the_mouse_sends_nothing(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    assert writer.requests == [] and window._live_lighting.live
    assert window.pages["lighting"]._colour_note.get_text().endswith("straight away and stay on it.")


def test_a_colour_change_sends_only_that_profile(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    _select(window, writer, 3)
    _lighting(window).select_colour((0x12, 0x34, 0x56))
    assert len(writer.requests) == 1
    (target,) = writer.requests[0]
    assert target.profile == 3 and target.rgb == (0x12, 0x34, 0x56)
    assert target.mode == "breathing" and target.speed == 5                    # factory profile 3


def test_changes_during_a_write_are_coalesced_to_the_latest(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    _select(window, writer, 1)
    _lighting(window).select_colour((10, 0, 0))
    for value in range(11, 40):
        _lighting(window).select_colour((value, 0, 0))
    assert len(writer.requests) == 1
    writer.finish({"status": "ok", "records": {"1": "0a000002050102"}})
    assert len(writer.requests) == 2 and writer.requests[1][0].rgb == (39, 0, 0)
    writer.finish({"status": "ok", "records": _records(window)})
    assert not writer.running
    _lighting(window).select_colour((39, 0, 0))                                # same again: nothing
    assert len(writer.requests) == 2


def test_mode_brightness_and_speed_changes_are_sent(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    _select(window, writer, 2)
    _lighting(window).set_brightness(3)
    writer.finish({"status": "ok", "records": _records(window)})
    _lighting(window).set_breathing_speed(8)
    writer.finish({"status": "ok", "records": _records(window)})
    _lighting(window).select_mode("wave")
    writer.finish({"status": "ok", "records": _records(window)})
    assert [(r[0].brightness, r[0].mode, r[0].speed) for r in writer.requests] == [
        (3, "breathing", 5), (3, "breathing", 8), (3, "wave", None)]


def test_selecting_a_profile_switches_the_mouse_and_nothing_else(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)                                   # factory: mouse on profile 1
    window.workspace.select_slot(3)
    assert writer.switches == [3] and writer.requests == []
    writer.finish({"status": "ok", "active_profile": 3})
    assert window.active_mouse_profile == 3 and not writer.running


def test_rapid_profile_clicks_coalesce_to_the_last(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    for slot in (2, 5, 4):
        window.workspace.select_slot(slot)
    assert writer.switches == [2]
    writer.finish({"status": "ok", "active_profile": 2})
    assert writer.switches == [2, 4]
    writer.finish({"status": "ok", "active_profile": 4})
    assert not writer.running and window.active_mouse_profile == 4


def test_the_mouse_echo_after_a_switch_does_not_move_the_selection(setup):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    window.workspace.select_slot(2)
    writer.finish({"status": "ok", "active_profile": 2})
    window.workspace.select_slot(5)                            # user clicks on before the echo arrives
    watcher.on_event({"event": "profile", "profile": 1})       # echo of the switch to 2 (zero-based)
    assert window.workspace.selected_slot == 5
    writer.finish({"status": "ok", "active_profile": 5})
    assert writer.switches == [2, 5]


def test_the_physical_button_is_still_followed_and_not_switched_back(setup):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    watcher.on_event({"event": "profile", "profile": 3})       # button: profile 4
    assert window.workspace.selected_slot == 4 and writer.calls == []


def test_unknown_active_profile_never_switches(setup):
    window, reader, _watcher, writer = setup
    window.read_mouse()
    document = _document()
    from venus_profile.read_snapshot import memory_ranges
    values = {}
    for run in document["memory"]:
        for offset, value in enumerate(bytes.fromhex(run["hex"])):
            values[run["address"] + offset] = value
    values[0x002C] = 9
    reader.finish({"version": 1, "status": "ok", "memory": memory_ranges(values)})
    window.workspace.select_slot(3)
    assert writer.switches == []


def test_failed_switch_shows_the_banner_and_stops(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    window.workspace.select_slot(2)
    writer.finish({"status": "mismatch", "message": "active profile reads back as 1, expected 2"})
    assert "couldn't be sent" in window._banner.get_title() and not window._live_lighting.live
    window.workspace.select_slot(3)
    assert writer.switches == [2]


def test_unknown_lighting_on_the_mouse_is_never_written(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader, {4: "ff00ff990000" "02"})                       # unrecognised mode
    _select(window, writer, 4)
    _lighting(window).select_colour((1, 1, 1))
    assert writer.requests == []


def test_failure_stops_live_lighting_until_read_again(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    _select(window, writer, 1)
    _lighting(window).select_colour((5, 5, 5))
    writer.finish({"status": "mismatch", "message": "read-back differs for profile(s) 1"})
    assert window._banner.get_revealed() and "Changes couldn't be sent" in window._banner.get_title()
    assert not window._live_lighting.live
    assert window.pages["lighting"]._colour_note.get_text().endswith("kept in the app only until the mouse is read.")
    _lighting(window).select_colour((6, 6, 6))
    assert len(writer.requests) == 1
    window._banner.emit("button-clicked")                                      # Read again
    reader.finish(_document({1: "060606020501" "02"}))
    assert window._live_lighting.live and len(writer.requests) == 1          # mouse now matches


def test_a_read_waits_for_a_running_write(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    _lighting(window).select_colour((7, 7, 7))
    assert window.read_mouse() is True and reader.started == 1               # deferred
    writer.finish({"status": "ok", "records": _records(window)})
    assert reader.started == 2


def test_disconnect_stops_sending(setup):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    watcher.on_end({"status": "disconnected"})
    _lighting(window).select_colour((8, 8, 8))
    assert writer.requests == []


def test_closing_waits_for_a_running_write_and_sends_nothing_on_close(setup):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    _lighting(window).select_colour((9, 9, 9))
    window.close()
    _pump()
    assert watcher.stopped == 0 and writer.running                             # close held back
    writer.finish({"status": "ok", "records": _records(window)})
    _pump()
    assert watcher.stopped == 1 and len(writer.requests) == 1                 # closed; nothing sent on close


def test_disabled_writer_reports_a_banner_not_a_hang(setup, monkeypatch):
    from venus_profile.gui import application
    window, reader, _watcher, _writer = setup
    window._live_lighting._runner = application.start_lighting_write          # real launcher, disabled
    monkeypatch.setenv("VENUS_PROFILE_NO_DEVICE_WRITE", "1")
    _read_ok(window, reader)
    _lighting(window).select_colour((3, 3, 3))
    assert not window._live_lighting.busy and not window._live_lighting.live
    assert "disabled" in window._banner.get_title()


# --------------------------------------------------------------- controller

def test_lighting_from_decoded_needs_a_known_mode():
    from venus_profile.device_decode import decode_lighting
    from venus_profile.gui.live_lighting import lighting_from_decoded
    known = decode_lighting(bytes.fromhex("ff0000020501"), b"\x02")
    assert lighting_from_decoded(known) == bytes.fromhex("ff000002050102")
    assert lighting_from_decoded(decode_lighting(bytes.fromhex("ff0000020701"), b"\x02")) is None
    assert lighting_from_decoded(None) is None


# --------------------------------------------------------------- launcher

class _FakeProcess:
    instances = []

    def __init__(self, argv, flags):
        self.argv, self.flags = argv, flags
        self.stdin = self.callback = None
        self.killed = False
        _FakeProcess.instances.append(self)

    def communicate_utf8_async(self, stdin, _cancellable, callback):
        self.stdin, self.callback = stdin, callback

    def communicate_utf8_finish(self, result):
        return True, result, ""

    def force_exit(self):
        self.killed = True


def test_launcher_runs_only_the_helper_with_the_request_on_stdin(monkeypatch):
    from venus_profile.gui import lighting_preview
    from gi.repository import Gio
    monkeypatch.delenv("VENUS_PROFILE_NO_DEVICE_WRITE", raising=False)
    monkeypatch.delenv("VENUS_PROFILE_NO_DEVICE_READ", raising=False)
    monkeypatch.setattr(lighting_preview.Gio.Subprocess, "new", _FakeProcess)
    results = []
    lighting_preview.start_lighting_write('{"request": 1}', results.append)
    process = _FakeProcess.instances[-1]
    assert process.argv == [sys.executable, "-m", "venus_profile.preview_lighting"]
    assert process.flags & Gio.SubprocessFlags.STDIN_PIPE and process.stdin == '{"request": 1}'
    process.callback(process, 'noise\n{"status": "ok", "records": {}}\n')
    assert results == [{"status": "ok", "records": {}}]
    lighting_preview.start_lighting_write("{}", results.append)._timed_out()
    assert results[-1]["status"] == "timeout" and _FakeProcess.instances[-1].killed
    lighting_preview.start_lighting_write("{}", results.append)
    _FakeProcess.instances[-1].callback(_FakeProcess.instances[-1], "")
    assert results[-1]["status"] == "bad-output"


@pytest.mark.parametrize("variable", ("VENUS_PROFILE_NO_DEVICE_WRITE", "VENUS_PROFILE_NO_DEVICE_READ"))
def test_launcher_never_starts_when_disabled(monkeypatch, variable):
    from venus_profile.gui import lighting_preview
    monkeypatch.delenv("VENUS_PROFILE_NO_DEVICE_WRITE", raising=False)
    monkeypatch.delenv("VENUS_PROFILE_NO_DEVICE_READ", raising=False)
    monkeypatch.setenv(variable, "1")

    def forbidden(*_args):
        raise AssertionError("must not launch")
    monkeypatch.setattr(lighting_preview.Gio.Subprocess, "new", forbidden)
    results = []
    lighting_preview.start_lighting_write("{}", results.append)
    assert results == [{"status": "disabled", "message": "sending to the mouse is disabled"}]


@pytest.mark.parametrize(("original", "tampered"), (
    ('HELPER_MODULE = "venus_profile.preview_lighting"', 'HELPER_MODULE = "venus_profile.guarded_led_mode"'),
    ('argv = [sys.executable, "-m", HELPER_MODULE]', 'argv = [sys.executable, "-m", HELPER_MODULE, "--raw"]'),
    ("Gio.Subprocess.new(\n                argv,", "Gio.Subprocess.new(\n                [sys.executable],"),
))
def test_gui_audit_rejects_a_changed_lighting_helper_launch(tmp_path, original, tampered):
    from venus_profile.gui import safety
    source = (Path(safety.__file__).parent / "lighting_preview.py").read_text(encoding="utf-8")
    assert original in source
    fake = tmp_path / "lighting_preview.py"
    fake.write_text(source.replace(original, tampered), encoding="utf-8")
    with pytest.raises(RuntimeError):
        safety._audit_helper_launch(fake)


def test_gui_cannot_reach_the_helper_module():
    from venus_profile.gui.safety import FORBIDDEN_MODULES, import_closure
    assert "venus_profile.preview_lighting" in FORBIDDEN_MODULES
    closure = import_closure()
    assert "venus_profile.gui.live_lighting" in closure and "venus_profile.preview_lighting" not in closure


def test_mode_notes_are_one_sentence_per_line_and_never_widen_the_window(setup):
    import time
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    window._select_page(None, "lighting")

    def settle(seconds=0.3):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
    settle(0.5)
    width = window.get_width()
    note = window.pages["lighting"]._colour_note
    for mode in ("rainbow", "off", "wave", "flash", "breathing", "full-brightness", "rainbow"):
        _lighting(window).select_mode(mode)
        while writer.running:
            writer.finish({"status": "ok", "records": _records(window)})
        settle()
        assert window.get_width() == width, mode
        if mode in ("rainbow", "off"):
            assert note.get_text().split("\n") == [
                f"{'Rainbow' if mode == 'rainbow' else 'Off'} does not use the colour setting.",
                "Changes are sent to the mouse straight away and stay on it."]


def test_mouse_writes_are_silent_without_the_diagnostics_flag(setup, capsys, monkeypatch):
    monkeypatch.delenv("VENUS_PROFILE_DIAGNOSTICS", raising=False)
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    window.workspace.select_slot(3)
    writer.finish({"status": "ok", "active_profile": 3})
    assert "venus-mouse" not in capsys.readouterr().err


def test_each_mouse_write_is_logged_with_the_diagnostics_flag(setup, capsys, monkeypatch):
    monkeypatch.setenv("VENUS_PROFILE_DIAGNOSTICS", "1")
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    window.workspace.select_slot(3)
    writer.finish({"status": "ok", "active_profile": 3})
    _lighting(window).select_colour((1, 2, 3))
    writer.finish({"status": "mismatch", "message": "read-back differs for profile(s) 3"})
    err = capsys.readouterr().err
    assert "venus-mouse: profile -> ok (active 3)" in err
    assert "venus-mouse: lighting -> mismatch (read-back differs for profile(s) 3)" in err


# --------------------------------------------------------------- Apply categories (polling, DPI, buttons)

def _apply_enabled(window):
    return window._actions["apply-to-mouse"].get_enabled()


def test_apply_is_disabled_until_the_mouse_is_read_and_something_differs(setup):
    window, reader, _watcher, writer = setup
    window.workspace.profile(1).set_polling_rate_hz(1000)
    window.workspace.edited("settings")
    assert not _apply_enabled(window)                          # not read yet
    _read_ok(window, reader)                                   # mouse wins: back to 500 Hz
    assert not _apply_enabled(window) and writer.calls == []
    window.workspace.profile(1).set_polling_rate_hz(1000)
    window.workspace.edited("settings")
    assert _apply_enabled(window) and writer.calls == []       # pending, not sent


def test_polling_waits_for_apply_then_sends_all_five_profiles(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)                                   # factory: all 500 Hz (enum 2)
    window.workspace.profile(1).set_polling_rate_hz(1000)
    window.workspace.edited("settings")
    assert writer.calls == []
    window.activate_action("win.apply-to-mouse")
    assert writer.polling == [(1, 2, 2, 2, 2)] and not _apply_enabled(window)
    writer.finish({"status": "ok", "polling": [1, 2, 2, 2, 2]})
    assert not writer.running and not _apply_enabled(window)


def test_apply_sends_polling_then_dpi_then_buttons_and_toasts(setup, monkeypatch):
    window, reader, _watcher, writer = setup
    toasts = []
    monkeypatch.setattr(window, "toast", toasts.append)
    _read_ok(window, reader)
    profile = window.workspace.profile(2)                      # edit profile 2 without selecting it
    profile.set_polling_rate_hz(125)
    profile.set_dpi_stage_dpi(1, 1600)
    from venus_profile import button_functions as bf
    bf.set_single_key(profile, 9, 0x04)
    window.workspace.edited("settings")
    window.activate_action("win.apply-to-mouse")
    assert [kind for kind, _ in writer.calls] == ["polling"]
    writer.finish({"status": "ok", "polling": [2, 8, 2, 2, 2]})
    assert [kind for kind, _ in writer.calls] == ["polling", "dpi"]
    raw = profile.dpi_stage_value(1).x_raw
    writer.finish({"status": "ok", "dpi": [{"profile": 2, "stage": 1, "raw": raw}]})
    assert [kind for kind, _ in writer.calls] == ["polling", "dpi", "buttons"]
    assert writer.calls[-1][1] == ((2, 9, bytes.fromhex("90000400")),)
    writer.finish({"status": "ok", "buttons": [{"profile": 2, "button": 9, "record": "90000400"}]})
    assert toasts[-1] == "Applied to the mouse" and not _apply_enabled(window) and not writer.running


def test_lighting_and_profile_stay_live_while_dpi_waits(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    window.workspace.select_slot(2)
    window.workspace.profile(2).set_polling_rate_hz(125)
    window.workspace.edited("settings")                        # as the Settings page does
    _lighting(window).select_colour((4, 5, 6))
    assert [kind for kind, _ in writer.calls] == ["profile"]
    writer.finish({"status": "ok", "active_profile": 2})
    assert [kind for kind, _ in writer.calls] == ["profile", "lighting"]      # polling waits for Apply
    writer.finish({"status": "ok", "records": _records(window)})
    assert not writer.running and _apply_enabled(window)


def test_fire_count_on_the_mouse_is_never_overwritten(setup):
    window, reader, _watcher, writer = setup
    window.read_mouse()
    document = _document()
    from venus_profile.read_snapshot import memory_ranges
    values = {}
    for run in document["memory"]:
        for offset, value in enumerate(bytes.fromhex(run["hex"])):
            values[run["address"] + offset] = value
    for i, v in enumerate(bytes.fromhex("99 81 04 01")):       # P1 button 4: left fire, 4 shots, 10 ms
        values[0x008E + i] = v
    reader.finish({"version": 1, "status": "ok", "memory": memory_ranges(values)})
    assert window._live_lighting.wanted_buttons() == [] and window.workspace.edited_slots() == (), window.workspace.edited_slots()
    assert not _apply_enabled(window)


def test_unknown_button_record_on_the_mouse_is_never_written(setup):
    window, reader, _watcher, writer = setup
    window.read_mouse()
    document = _document()
    from venus_profile.read_snapshot import memory_ranges
    values = {}
    for run in document["memory"]:
        for offset, value in enumerate(bytes.fromhex(run["hex"])):
            values[run["address"] + offset] = value
    for i, v in enumerate(bytes.fromhex("7f 01 02 03")):       # not a known shape
        values[0x0092 + i] = v
    reader.finish({"version": 1, "status": "ok", "memory": memory_ranges(values)})
    from venus_profile import button_functions as bf
    bf.set_single_key(window.workspace.profile(1), 5, 0x04)
    window.workspace.edited("buttons")
    assert all(key != (1, 5) for key in [(p, b) for p, b, _ in window._live_lighting.wanted_buttons()])


def test_unknown_polling_on_the_mouse_is_never_written(setup):
    window, reader, _watcher, writer = setup
    window.read_mouse()
    document = _document()
    from venus_profile.read_snapshot import memory_ranges
    values = {}
    for run in document["memory"]:
        for offset, value in enumerate(bytes.fromhex(run["hex"])):
            values[run["address"] + offset] = value
    values[0x0034] = 3                                        # not an enum
    reader.finish({"version": 1, "status": "ok", "memory": memory_ranges(values)})
    window.workspace.profile(1).set_polling_rate_hz(1000)
    window.workspace.edited("settings")
    window.activate_action("win.apply-to-mouse")
    assert writer.polling == []


# --------------------------------------------------------------- DPI values (T4, on Apply)

def test_dpi_value_change_waits_for_apply_and_sends_only_that_stage(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    profile = window.workspace.profile(1)
    profile.set_dpi_stage_dpi(1, 1600)
    window.workspace.edited("dpi")
    assert writer.calls == [] and _apply_enabled(window)
    window.activate_action("win.apply-to-mouse")
    raw = profile.dpi_stage_value(1).x_raw
    assert writer.dpi == [((1, 1, raw),)] and writer.requests == [] and writer.polling == []
    writer.finish({"status": "ok", "dpi": [{"profile": 1, "stage": 1, "raw": raw}]})
    assert len(writer.calls) == 1 and not _apply_enabled(window)


def test_disabled_stage_is_never_sent(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    profile = window.workspace.profile(1)
    profile.set_dpi_stage_enabled(2, False)
    profile.set_dpi_stage_dpi(2, 1600)
    window.workspace.edited("dpi")
    assert window._live_lighting.wanted_dpi() == []


def test_dpi_button_presses_do_not_write_dpi_records(setup):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    watcher.on_event({"event": "dpi-stage", "stage": 1})
    assert writer.calls == []



# --------------------------------------------------------------- Stage 2: defaults, commit, close overlay

def test_dpi_defaults_button_sets_factory_values_all_on_and_waits_for_apply(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    profile = window.workspace.profile(1)
    profile.set_dpi_stage_dpi(3, 600)
    profile.set_dpi_stage_enabled(5, False)
    start = profile.selected_dpi_stage_raw
    window.workspace.edited("dpi")
    window.pages["dpi"]._defaults_button.emit("clicked")
    assert [profile.dpi_stage_dpi(s) for s in range(1, 6)] == [1000, 2000, 4000, 8200, 16400]
    assert all(profile.dpi_stage_enabled(s) for s in range(1, 6))
    assert profile.selected_dpi_stage_raw == start and writer.calls == []
    assert window.pages["dpi"].presentation.restore_defaults() is False      # already defaults


def test_apply_clears_the_edited_markers(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    window.workspace.profile(2).set_polling_rate_hz(125)
    window.workspace.edited("settings")
    assert 2 in window.workspace.edited_slots()
    window.activate_action("win.apply-to-mouse")
    writer.finish({"status": "ok", "polling": [2, 8, 2, 2, 2]})
    assert window.workspace.edited_slots() == () and not _apply_enabled(window)


def test_live_lighting_edits_are_committed_at_once_and_never_enable_apply(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    _lighting(window).select_colour((1, 2, 3))
    writer.finish({"status": "ok", "records": _records(window)})
    assert window.workspace.edited_slots() == () and not _apply_enabled(window)
    assert not window._actions["cancel-edits"].get_enabled()            # nothing to cancel: it's on the mouse


def test_lighting_commit_keeps_other_edits_marked(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    window.workspace.profile(1).set_polling_rate_hz(1000)
    window.workspace.edited("settings")
    _lighting(window).select_colour((9, 9, 9))
    writer.finish({"status": "ok", "records": _records(window)})
    assert window.workspace.edited_slots() == (1,) and _apply_enabled(window)
    window.workspace.cancel_edits(1)                                    # Cancel reverts polling, not lighting
    assert window.workspace.profile(1).polling_rate_raw == 2
    assert tuple(window.workspace.profile(1).led_color_rgb) == (9, 9, 9)


def test_lighting_before_a_read_is_an_ordinary_edit(setup):
    window, reader, _watcher, writer = setup
    _lighting(window).select_colour((5, 5, 5))
    assert window.workspace.edited_slots() == (1,)


def _pending_polling(window):
    window.workspace.profile(1).set_polling_rate_hz(1000)
    window.workspace.edited("settings")


def test_close_with_unapplied_changes_asks_and_cancel_keeps_the_app_open(setup):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    _pending_polling(window)
    window.close()
    _pump()
    assert window._unapplied_dialog is not None and watcher.stopped == 0
    window._unapplied_response(window._unapplied_dialog, "cancel")
    _pump()
    assert watcher.stopped == 0 and writer.calls == []
    window.close()                                             # asks again
    _pump()
    assert window._unapplied_dialog is not None


def test_close_overlay_apply_sends_then_closes(setup):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    _pending_polling(window)
    window.close()
    _pump()
    window._unapplied_response(window._unapplied_dialog, "apply")
    assert writer.polling == [(1, 2, 2, 2, 2)] and watcher.stopped == 0
    writer.finish({"status": "ok", "polling": [1, 2, 2, 2, 2]})
    _pump()
    assert watcher.stopped == 1                                # closed after Apply


def test_close_overlay_apply_failure_stays_open(setup):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    _pending_polling(window)
    window.close()
    _pump()
    window._unapplied_response(window._unapplied_dialog, "apply")
    writer.finish({"status": "mismatch", "message": "polling reads back differently"})
    _pump()
    assert watcher.stopped == 0 and "couldn't be sent" in window._banner.get_title()


def test_close_overlay_save_saves_then_closes(setup, tmp_path, monkeypatch):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    _pending_polling(window)
    target = tmp_path / "Pending.pfd"
    monkeypatch.setattr(window, "_choose_save_as",
                        lambda: window.save_profile_as(target, window.workspace.selected_slot))
    window.close()
    _pump()
    window._unapplied_response(window._unapplied_dialog, "save")
    _pump()
    assert target.exists() and watcher.stopped == 1 and writer.calls == []


def test_close_overlay_offers_apply_save_discard_cancel_in_that_order(setup):
    window, reader, _watcher, _writer = setup
    _read_ok(window, reader)
    _pending_polling(window)
    window.close()
    _pump()
    dialog = window._unapplied_dialog
    ids = ("apply", "save", "discard", "cancel")
    assert all(dialog.has_response(response) for response in ids)
    assert [dialog.get_response_label(response) for response in ids] == ["Apply", "Save", "Discard", "Cancel"]
    assert dialog.get_response_appearance("discard") == Adw.ResponseAppearance.DESTRUCTIVE
    assert dialog.get_close_response() == "cancel"
    window._unapplied_response(dialog, "cancel")


def test_close_overlay_discard_reverts_edits_sends_nothing_and_closes(setup):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    slot = 1                                                                 # _pending_polling edits profile 1
    before = window.workspace.profile(slot).polling_rate_hz
    _pending_polling(window)
    assert window.workspace.profile(slot).polling_rate_hz != before
    window.close()
    _pump()
    window._unapplied_response(window._unapplied_dialog, "discard")
    _pump()
    assert writer.calls == [] and watcher.stopped == 1                        # nothing sent, closed
    assert window.workspace.profile(slot).polling_rate_hz == before         # back to the mouse's value
    assert window.workspace.edited_slots() == ()


def test_no_overlay_without_unapplied_device_changes(setup):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    _lighting(window).select_colour((7, 8, 9))                # live, already on the mouse
    writer.finish({"status": "ok", "records": _records(window)})
    window.close()
    _pump()
    assert getattr(window, "_unapplied_dialog", None) is None and watcher.stopped == 1


# --------------------------------------------------------------- Stage 3: macro slots (live)

def _library_macro(window, name="Tap A", usage=0x04):
    from venus_profile.actions import ActionRecord
    entry = window.workspace.library.create(name)
    entry.macro.set_actions([ActionRecord.keyboard(usage, True), ActionRecord.action_delay_ms(10),
                             ActionRecord.keyboard(usage, False)])
    return entry


def _with_slot_bodies(window, reader, bodies: dict[int, bytes]):
    window.read_mouse()
    document = _document()
    from venus_profile.read_snapshot import memory_ranges
    from venus_profile.lighting_preview_plan import macro_slot_address
    values = {}
    for run in document["memory"]:
        for offset, value in enumerate(bytes.fromhex(run["hex"])):
            values[run["address"] + offset] = value
    for slot, body in bodies.items():
        for i, v in enumerate(body):
            values[macro_slot_address(slot) + i] = v
    reader.finish({"version": 1, "status": "ok", "memory": memory_ranges(values)})


def test_reading_the_mouse_leaves_no_macro_upload_pending(setup):
    window, reader, _watcher, writer = setup
    _with_slot_bodies(window, reader, {0: bytes.fromhex("841e00060100041e00").ljust(200, b"\0"),
                                       7: bytes.fromhex("ffff00").ljust(200, b"\0")})
    assert window._live_lighting.wanted_macros() == [] and writer.calls == []


def test_putting_a_macro_in_a_slot_uploads_it_live(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    entry = _library_macro(window)
    window.workspace.load_macro_into_slot(entry.macro_id, 2)
    assert len(writer.macros) == 1
    ((slot, body),) = writer.macros[0]
    assert slot == 2 and body[:9] == bytes.fromhex("840400060100040400") and not any(body[9:])
    writer.finish({"status": "ok", "macros": [{"slot": 2, "body": body.hex()}]})
    assert not writer.running and not _apply_enabled(window)                   # live, not an Apply change


def test_removing_a_macro_uploads_the_empty_body(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    entry = _library_macro(window)
    window.workspace.load_macro_into_slot(entry.macro_id, 4)
    ((slot, body),) = writer.macros[-1]
    writer.finish({"status": "ok", "macros": [{"slot": 4, "body": body.hex()}]})
    window.workspace.unload_slot(4)
    assert writer.macros[-1] == ((4, bytes(200)),)


def test_editing_an_assigned_macro_reuploads_its_slot(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    entry = _library_macro(window)
    window.workspace.load_macro_into_slot(entry.macro_id, 1)
    ((_slot, body),) = writer.macros[-1]
    writer.finish({"status": "ok", "macros": [{"slot": 1, "body": body.hex()}]})
    from venus_profile.actions import ActionRecord
    entry.macro.set_actions([ActionRecord.keyboard(0x05, True), ActionRecord.keyboard(0x05, False)])
    window.workspace.notify("macro-edit")
    assert writer.macros[-1][0][0] == 1 and writer.macros[-1][0][1][:6] == bytes.fromhex("840500040500")


def test_unrecognised_slot_on_the_mouse_is_never_written(setup):
    window, reader, _watcher, writer = setup
    _with_slot_bodies(window, reader, {7: bytes.fromhex("ffff01").ljust(200, b"\0")})
    entry = _library_macro(window)
    window.workspace.load_macro_into_slot(entry.macro_id, 7)
    assert writer.macros == []


def test_factory_filler_slot_is_free_and_takes_a_macro(setup):
    window, reader, _watcher, writer = setup
    _with_slot_bodies(window, reader, {7: bytes.fromhex("ffff00").ljust(200, b"\0")})
    library = window.workspace.library
    assert library.bank.slots[7] is None and 7 not in library.mouse_only     # shown as an empty slot
    assert writer.macros == []                                                # never "emptied" on its own
    entry = _library_macro(window)
    window.workspace.load_macro_into_slot(entry.macro_id, 7)
    ((slot, body),) = writer.macros[-1]
    assert slot == 7 and body[:9] == bytes.fromhex("840400060100040400")



# --------------------------------------------------------------- option b: buttons follow slot moves live

def _finish_all(writer, window):
    """Answer every queued helper run as the mouse would (records follow the request)."""
    from venus_profile.lighting_preview_plan import parse_any_request  # noqa: F401
    for _ in range(10):
        if not writer.running:
            return
        kind, payload = writer.calls[-1]
        if kind == "macros":
            writer.finish({"status": "ok", "macros": [{"slot": s, "body": b.hex()} for s, b in payload]})
        elif kind in ("buttons", "live-buttons"):
            writer.finish({"status": "ok", "buttons": [{"profile": p, "button": b, "record": r.hex()}
                                                       for p, b, r in payload]})
        else:
            writer.finish({"status": "ok", "records": _records(window)})


def _two_macros_on_buttons(window, writer):
    from venus_profile import button_functions as bf
    a, b = _library_macro(window, "Mac A", 0x04), _library_macro(window, "Mac B", 0x05)
    window.workspace.load_macro_into_slot(a.macro_id, 9)
    _finish_all(writer, window)
    window.workspace.load_macro_into_slot(b.macro_id, 10)
    _finish_all(writer, window)
    bf.set_macro(window.workspace.profile(1), 15, 9, "Mac A")
    window.workspace.edited("buttons")
    window.activate_action("win.apply-to-mouse")
    _finish_all(writer, window)
    assert window.workspace.edited_slots() == () and not _apply_enabled(window)
    return a, b


def test_swapping_slots_sends_the_repointed_buttons_live(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    _two_macros_on_buttons(window, writer)
    calls_before = len(writer.calls)
    window.workspace.swap_slots(9, 10)                           # no Apply clicked
    _finish_all(writer, window)
    kinds = [kind for kind, _ in writer.calls[calls_before:]]
    assert kinds == ["macros", "buttons"] and not window._live_lighting.applying
    assert writer.calls[-1][1] == ((1, 15, bytes.fromhex("910a0100")),)       # button follows Mac A to slot 11
    assert window.workspace.edited_slots() == () and not _apply_enabled(window)   # no leftover Apply


def test_removing_a_slot_sends_disable_live(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    _two_macros_on_buttons(window, writer)
    calls_before = len(writer.calls)
    window.workspace.unload_slot(9)
    _finish_all(writer, window)
    assert [kind for kind, _ in writer.calls[calls_before:]] == ["macros", "buttons"]
    assert writer.calls[-1][1] == ((1, 15, bytes.fromhex("00000000")),)
    assert window.workspace.edited_slots() == ()


def test_unrelated_button_edits_still_wait_for_apply(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    _two_macros_on_buttons(window, writer)
    from venus_profile import button_functions as bf
    bf.set_single_key(window.workspace.profile(1), 12, 0x05)                  # an ordinary edit
    window.workspace.edited("buttons")
    calls_before = len(writer.calls)
    window.workspace.swap_slots(9, 10)
    _finish_all(writer, window)
    sent = [payload for kind, payload in writer.calls[calls_before:] if kind == "buttons"]
    assert sent and all(button != 12 for entries in sent for _p, button, _r in entries)
    assert _apply_enabled(window) and window.workspace.edited_slots() == (1,)


def _edit_loop(window, entry, loop_type, loop_time=None):
    from venus_profile.gui.macros.model import library_stable_id
    presentation = window.pages["macros"].presentation
    presentation.select_macro(library_stable_id(entry.macro_id))
    presentation.edit_loop(loop_type, loop_time)


@pytest.mark.parametrize("loop_type, loop_time, record", [
    (3, None, "914a0100"),        # toggle: flag 0x40 | slot 10, count = LoopTime (1)
    (1, None, "918a0100"),        # while held: flag 0x80
    (0, 5, "910a0500"),           # fixed: count 5
])
def test_changing_a_macro_loop_mode_sends_its_buttons_live(setup, loop_type, loop_time, record):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    a, _b = _two_macros_on_buttons(window, writer)
    window.workspace.swap_slots(9, 10)                            # Mac A now in slot 11 (index 10)
    _finish_all(writer, window)
    calls_before = len(writer.calls)
    _edit_loop(window, a, loop_type, loop_time)
    _finish_all(writer, window)
    assert [kind for kind, _ in writer.calls[calls_before:]] == ["buttons"]
    assert writer.calls[-1][1] == ((1, 15, bytes.fromhex(record)),)
    assert window._live_lighting.wanted_buttons() == [] and window.workspace.edited_slots() == (), window.workspace.edited_slots()
    assert not _apply_enabled(window)


def test_diagnostics_name_a_loop_change_and_a_slot_change(setup, capsys, monkeypatch):
    monkeypatch.setenv("VENUS_PROFILE_DIAGNOSTICS", "1")
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    a, _b = _two_macros_on_buttons(window, writer)
    capsys.readouterr()
    _edit_loop(window, a, 3)
    _finish_all(writer, window)
    err = capsys.readouterr().err
    assert "venus-mouse: loop change updates P1B15" in err and "slot change" not in err
    window.workspace.swap_slots(9, 10)
    _finish_all(writer, window)
    err = capsys.readouterr().err
    assert "venus-mouse: slot change re-points P1B15" in err and "loop change" not in err


def test_loop_change_back_to_run_once_is_sent(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    a, _b = _two_macros_on_buttons(window, writer)
    _edit_loop(window, a, 3)
    _finish_all(writer, window)
    _edit_loop(window, a, 2)
    _finish_all(writer, window)
    assert writer.calls[-1] == ("buttons", ((1, 15, bytes.fromhex("91090100")),))


def test_loop_change_of_a_macro_not_in_a_slot_sends_nothing(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    _two_macros_on_buttons(window, writer)
    loose = _library_macro(window, "Loose", 0x06)
    calls_before = len(writer.calls)
    _edit_loop(window, loose, 3)
    _finish_all(writer, window)
    assert writer.calls[calls_before:] == [] and window._live_lighting.wanted_buttons() == []


def test_loop_flags_of_a_mouse_only_slot_are_left_alone(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    live = window._live_lighting
    live._workspace.library.mouse_only[6] = bytes(200)
    assert not live._macro_loop_differs(bytes.fromhex("91860100"), bytes.fromhex("91060100"))
    assert not live._macro_loop_differs(bytes.fromhex("91060100"), bytes.fromhex("91070100"))  # slot change: elsewhere


def test_before_a_read_repointed_buttons_are_ordinary_edits(setup):
    window, reader, _watcher, writer = setup
    a = _library_macro(window, "Mac A")
    window.workspace.load_macro_into_slot(a.macro_id, 9)
    from venus_profile import button_functions as bf
    bf.set_macro(window.workspace.profile(1), 15, 9, "Mac A")
    window.workspace.swap_slots(9, 10)
    assert writer.calls == [] and window._live_lighting._live_buttons == set()


# --------------------------------------------------------------- T6: scroll lines (Apply)

def _read_with_scroll(window, reader, scroll=bytes.fromhex("01000100010001000100")):
    window.read_mouse()
    document = _document()
    from venus_profile.read_snapshot import memory_ranges
    values = {}
    for run in document["memory"]:
        for offset, value in enumerate(bytes.fromhex(run["hex"])):
            values[run["address"] + offset] = value
    for i, v in enumerate(scroll):
        values[0x0020 + i] = v
    reader.finish({"version": 1, "status": "ok", "memory": memory_ranges(values)})


def test_scroll_change_waits_for_apply_and_sends_all_five(setup):
    window, reader, _watcher, writer = setup
    _read_with_scroll(window, reader)
    assert not _apply_enabled(window)
    window.workspace.profile(1).set_scroll_speed(3)
    window.workspace.edited("settings")
    assert writer.calls == [] and _apply_enabled(window)
    window.activate_action("win.apply-to-mouse")
    assert writer.scroll == [(3, 1, 1, 1, 1)]
    writer.finish({"status": "ok", "scroll": [3, 1, 1, 1, 1]})
    assert not writer.running and not _apply_enabled(window)


def test_mouse_scroll_wins_on_read(setup):
    window, reader, _watcher, writer = setup
    _read_with_scroll(window, reader, bytes.fromhex("05000100010001000100"))
    assert window.workspace.profile(1).scroll_speed_raw == 5 and not _apply_enabled(window)


def test_unread_scroll_is_never_sent(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)                                     # no 0x0020 in the document
    window.workspace.profile(1).set_scroll_speed(9)
    window.workspace.edited("settings")
    assert window._live_lighting.wanted_scroll() is None



# --------------------------------------------------------------- T5: DPI start-up stage on Apply

def test_read_gives_the_mouse_start_up_stage_and_nothing_is_pending(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    controller = window._live_lighting
    assert set(controller._mouse_dpi_stage) == {1, 2, 3, 4, 5}
    assert controller.wanted_dpi_stage() == [] and writer.calls == [] and not _apply_enabled(window)


def test_start_up_stage_waits_for_apply_and_follows_the_dpi_values(setup):
    window, reader, _watcher, writer = setup
    _read_ok(window, reader)
    page = window.pages["dpi"]
    assert window.workspace.selected_slot == 1                            # factory read: mouse on profile 1
    current = window._live_lighting._mouse_dpi_stage[1]
    target = 3 if current != 3 else 2                                      # zero-based
    profile = window.workspace.profile(1)
    profile.set_dpi_stage_dpi(1, 1600)                                    # a DPI value too
    page.presentation.set_default_stage(target + 1)                       # one-based, as the page does
    assert _apply_enabled(window) and writer.calls == []                  # nothing sent before Apply
    window.activate_action("win.apply-to-mouse")
    assert [kind for kind, _ in writer.calls] == ["dpi"]
    raw = profile.dpi_stage_value(1).x_raw
    writer.finish({"status": "ok", "dpi": [{"profile": 1, "stage": 1, "raw": raw}]})
    assert writer.calls[-1] == ("dpi_stage", ((1, target),))             # after the stage values
    writer.finish({"status": "ok", "dpi_stage": [{"profile": 1, "stage": target}]})
    assert not writer.running and not _apply_enabled(window)
    assert window._live_lighting._mouse_dpi_stage[1] == target


def test_dpi_button_press_moves_the_known_mouse_stage_so_apply_sends_nothing(setup):
    window, reader, watcher, writer = setup
    _read_ok(window, reader)
    profile = window.workspace.selected_slot
    before = window._live_lighting._mouse_dpi_stage[profile]
    pressed = 1 if before != 1 else 2
    watcher.on_event({"event": "dpi-stage", "stage": pressed})
    assert window.workspace.profile(profile).selected_dpi_stage_raw == pressed        # the mouse wins
    assert window._live_lighting._mouse_dpi_stage[profile] == pressed                 # the mouse is there
    assert window._live_lighting.wanted_dpi_stage() == [] and writer.calls == [] and not _apply_enabled(window)


def test_unknown_start_up_stage_on_the_mouse_is_never_sent(setup):
    window, reader, _watcher, writer = setup
    document = _document()
    from venus_profile.read_snapshot import memory_ranges
    values = {}
    for run in document["memory"]:
        for offset, value in enumerate(bytes.fromhex(run["hex"])):
            values[run["address"] + offset] = value
    values[0x0102] = 0x07                                                  # profile 2: not a stage
    window.read_mouse()
    reader.finish({"version": 1, "status": "ok", "memory": memory_ranges(values)})
    assert 2 not in window._live_lighting._mouse_dpi_stage
    window.workspace.profile(2).set_selected_dpi_stage(2)
    window.workspace.edited("dpi")
    assert all(profile != 2 for profile, _stage in window._live_lighting.wanted_dpi_stage())


# --------------------------------------------------------------- fire-key count (2026-11-20)

def _fire_read(window, reader, record="99 81 04 01"):
    document = _document()
    from venus_profile.read_snapshot import memory_ranges
    values = {}
    for run in document["memory"]:
        for offset, value in enumerate(bytes.fromhex(run["hex"])):
            values[run["address"] + offset] = value
    for i, v in enumerate(bytes.fromhex(record)):
        values[0x0082 + 4 * 3 + i] = v                                     # profile 1 button 4
    window.read_mouse()
    reader.finish({"version": 1, "status": "ok", "memory": memory_ranges(values)})


def test_fire_count_read_from_the_mouse_leaves_nothing_pending(setup):
    window, reader, _watcher, writer = setup
    _fire_read(window, reader)
    assert bf_read(window, 1, 4).fire.count == 4
    assert window._live_lighting.wanted_buttons() == [] and window.workspace.edited_slots() == (), window.workspace.edited_slots()
    assert not _apply_enabled(window)


def test_changing_only_the_fire_count_waits_for_apply_and_sends_it(setup):
    window, reader, _watcher, writer = setup
    _fire_read(window, reader)
    from venus_profile import button_functions as bf
    bf.set_fire_key(window.workspace.profile(1), 4, bf.FIRE_TRIGGER_LEFT, 10, 9)
    window.workspace.edited("buttons")
    assert _apply_enabled(window) and writer.calls == []
    assert window._live_lighting.wanted_buttons() == [(1, 4, bytes.fromhex("99 81 09 01"))]


def test_profile_without_a_stored_count_keeps_the_mouse_count(setup):
    window, reader, _watcher, writer = setup
    _fire_read(window, reader)
    from venus_profile import button_functions as bf
    bf.set_fire_key(window.workspace.profile(1), 4, bf.FIRE_TRIGGER_LEFT, 10)    # e.g. a Windows .pfd
    window.workspace.edited("buttons")
    assert window._live_lighting.wanted_buttons() == []                          # mouse keeps 4


def bf_read(window, profile, button):
    from venus_profile import button_functions as bf
    return bf.read_assignment(window.workspace.profile(profile), button)


def test_typing_a_fire_count_enables_apply_and_cancel(setup):
    window, reader, _watcher, writer = setup
    _fire_read(window, reader)
    window._select_page(None, "buttons")
    page = window.pages["buttons"]
    page.select_control(next(item for item in page._controls if item.protocol_button == 4))
    _pump()
    assert not _apply_enabled(window) and not window._actions["cancel-edits"].get_enabled()
    page._assignment._fire_count.set_text("7")                  # typed, no Enter
    _pump()
    assert _apply_enabled(window) and window._actions["cancel-edits"].get_enabled() and writer.calls == []
