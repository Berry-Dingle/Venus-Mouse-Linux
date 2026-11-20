"""GUI side of live mouse events: launcher, follow-the-mouse, banners (fakes only)."""

import json
from pathlib import Path
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib  # noqa: E402

from venus_profile.app_store import AppStore  # noqa: E402
from venus_profile.read_snapshot import memory_ranges  # noqa: E402
from venus_profile.workspace import VenusWorkspace  # noqa: E402

from gui_display import requires_gtk_display  # noqa: E402
from gui_workspace import shared_application  # noqa: E402
from evidence_paths import EVIDENCE  # noqa: E402

PROJECT = EVIDENCE
STAGE_BYTE = {1: 0x0042, 2: 0x0102, 3: 0x01B2, 4: 0x0262, 5: 0x0312}   # DPI base + 2


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


_KEEP = object()


def _document(stages: dict[int, int] | None = None, active=_KEEP) -> dict:
    """Factory memory; ``active`` sets 0x002C (zero-based), None removes it."""
    values = _factory_values()
    for profile, stage in (stages or {}).items():
        values[STAGE_BYTE[profile]] = stage
    if active is None:
        values.pop(0x002C, None)
    elif active is not _KEEP:
        values[0x002C] = active
    return {"version": 1, "status": "ok", "memory": memory_ranges(values)}


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
        self.started = 0
        self.stopped = 0
        self.on_event = self.on_end = None

    def __call__(self, on_event, on_end):
        self.started += 1
        self.on_event, self.on_end = on_event, on_end
        return self

    def stop(self):
        self.stopped += 1


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("VENUS_PROFILE_NO_DEVICE_READ", raising=False)
    from venus_profile.gui.application import VenusWindow
    workspace = VenusWorkspace.open(AppStore(tmp_path / "store", macro_library_dir=tmp_path / "Macros"))
    reader, watcher = FakeReader(), FakeWatcher()
    window = VenusWindow(shared_application(), workspace, snapshot_runner=reader, event_watcher=watcher)
    window.present()
    _pump()
    yield window, reader, watcher
    window._allow_close = True
    window.close()
    _pump()


pytestmark = requires_gtk_display


def _read_ok(window, reader, stages=None, active=_KEEP):
    window.read_mouse()
    reader.finish(_document(stages, active))


def test_listening_starts_only_after_a_successful_read(setup):
    window, reader, watcher = setup
    window.read_mouse()
    reader.finish({"status": "no-device"})
    assert watcher.started == 0
    _read_ok(window, reader)
    assert watcher.started == 1
    _read_ok(window, reader)                  # Read again: keeps the running helper
    assert watcher.started == 1


def test_profile_button_switches_the_app_to_that_profile(setup):
    window, reader, watcher = setup
    _read_ok(window, reader)
    watcher.on_event({"event": "profile", "profile": 2})
    assert window.workspace.selected_slot == 3
    assert window.active_mouse_profile == 3
    assert window._profile_pills.selected_profile == 3


def test_dpi_press_updates_the_active_profile_without_an_edit(setup):
    window, reader, watcher = setup
    _read_ok(window, reader)
    watcher.on_event({"event": "profile", "profile": 1})
    watcher.on_event({"event": "dpi-stage", "stage": 4})
    assert window.workspace.profile(2).selected_dpi_stage_raw == 4
    assert window.workspace.edited_slots() == ()
    assert reader.started == 1                # no extra read needed


def test_first_dpi_press_is_tracked_immediately_on_profile_1(setup):
    """No re-read: the mouse's memory lags DPI± presses by ~20-30 s (user report)."""
    window, reader, watcher = setup
    window.workspace.select_slot(2)
    _read_ok(window, reader, stages={1: 0, 2: 0, 3: 0, 4: 0, 5: 0}, active=None)   # ambiguous
    window.workspace.select_slot(4)           # browsing profiles in the app doesn't move the mouse
    watcher.on_event({"event": "dpi-stage", "stage": 1})
    assert reader.started == 1                # nothing re-read
    assert window.active_mouse_profile == 1
    assert window.workspace.profile(1).selected_dpi_stage_raw == 1
    assert window.workspace.profile(2).selected_dpi_stage_raw == 0
    assert window.workspace.profile(4).selected_dpi_stage_raw == 0
    watcher.on_event({"event": "dpi-stage", "stage": 2})
    assert window.workspace.profile(1).selected_dpi_stage_raw == 2
    assert window.workspace.edited_slots() == ()


def test_a_press_impossible_for_profile_1_picks_the_only_possible_one(setup):
    window, reader, watcher = setup
    window.workspace.select_slot(5)
    _read_ok(window, reader, stages={1: 0, 2: 0, 3: 2, 4: 0, 5: 0}, active=None)
    watcher.on_event({"event": "dpi-stage", "stage": 3})    # one step only from profile 3's stage 2
    assert window.active_mouse_profile == 3
    assert window.workspace.profile(3).selected_dpi_stage_raw == 3
    assert window.workspace.profile(1).selected_dpi_stage_raw == 0


def test_ambiguous_press_defaults_to_profile_1(setup):
    window, reader, watcher = setup
    window.workspace.select_slot(4)
    _read_ok(window, reader, stages={1: 0, 2: 2, 3: 2, 4: 0, 5: 0}, active=None)
    watcher.on_event({"event": "dpi-stage", "stage": 3})    # possible for profiles 2 and 3
    assert window.active_mouse_profile == 1


def test_dpi_steps_stop_at_the_ends_and_skip_disabled_stages(setup):
    window, reader, watcher = setup
    _read_ok(window, reader, stages={1: 0, 2: 4, 3: 2})
    profile = window.workspace.profile(1)
    assert window._dpi_steps_from(1) == {1}                  # stage 1: only DPI+ to 2, no wrap to 5
    assert window._dpi_steps_from(2) == {3}                  # stage 5: only DPI- to 4, no wrap to 1
    assert window._dpi_steps_from(3) == {1, 3}               # stage 3: 2 or 4
    profile.set_dpi_stage_enabled(2, False)
    assert window._dpi_steps_from(1) == {2}                  # stage 2 disabled: skipped


def test_stage_5_press_from_stage_1_is_not_treated_as_a_wrap(setup):
    window, reader, watcher = setup
    _read_ok(window, reader, stages={1: 0, 2: 3, 3: 2, 4: 2, 5: 2}, active=None)
    watcher.on_event({"event": "dpi-stage", "stage": 4})    # only profile 2 (stage 4) can reach 5
    assert window.active_mouse_profile == 2


def test_profile_button_overrides_the_assumption(setup):
    window, reader, watcher = setup
    window.workspace.select_slot(1)
    _read_ok(window, reader)                  # factory: profile 1 on stage 1, others on stage 2
    watcher.on_event({"event": "dpi-stage", "stage": 1})
    assert window.active_mouse_profile == 1
    watcher.on_event({"event": "profile", "profile": 4})
    watcher.on_event({"event": "dpi-stage", "stage": 4})
    assert window.active_mouse_profile == 5
    assert window.workspace.profile(5).selected_dpi_stage_raw == 4


def test_unplugging_shows_read_again(setup):
    window, reader, watcher = setup
    _read_ok(window, reader)
    watcher.on_event({"event": "profile", "profile": 0})
    watcher.on_end({"status": "disconnected", "message": "No such device"})
    assert window._banner.get_title() == "The mouse was disconnected — showing saved settings"
    assert window.active_mouse_profile is None
    window._banner.emit("button-clicked")
    reader.finish(_document())
    assert watcher.started == 2


def test_closing_stops_listening(setup):
    window, reader, watcher = setup
    _read_ok(window, reader)
    window._allow_close = True
    window.close()
    _pump()
    assert watcher.stopped == 1


def test_disabled_by_environment(setup, monkeypatch):
    window, reader, watcher = setup
    _read_ok(window, reader)
    assert watcher.started == 1
    watcher.on_end({"status": "disconnected"})
    monkeypatch.setenv("VENUS_PROFILE_NO_DEVICE_READ", "1")
    window._start_event_watch()
    assert watcher.started == 1


# --------------------------------------------------------------- launcher

def test_parse_event_line():
    from venus_profile.gui.device_events import parse_event_line
    assert parse_event_line('{"event": "profile", "profile": 1}\n') == {"event": "profile", "profile": 1}
    assert parse_event_line("") is None and parse_event_line("junk") is None and parse_event_line("[1]") is None


class _FakeProcess:
    instances = []

    def __init__(self, argv, flags, lines):
        self.argv, self.flags = argv, flags
        self.killed = False
        self._stdout = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new("".join(lines).encode()))
        _FakeProcess.instances.append(self)

    def get_stdout_pipe(self):
        return self._stdout

    def force_exit(self):
        self.killed = True


def _run_until(predicate, limit=200):
    context = GLib.MainContext.default()
    for _ in range(limit):
        if predicate():
            return
        context.iteration(True)


def test_event_watch_launches_only_the_helper_and_streams_events(monkeypatch):
    from venus_profile.gui import device_events
    lines = ['{"status": "listening"}\n', '{"event": "dpi-stage", "stage": 2}\n', "junk\n",
             '{"event": "profile", "profile": 4}\n', '{"status": "disconnected", "message": "gone"}\n']
    monkeypatch.setattr(device_events.Gio.Subprocess, "new",
                        lambda argv, flags: _FakeProcess(argv, flags, lines))
    events, ends = [], []
    watch = device_events.start_event_watch(events.append, ends.append)
    process = _FakeProcess.instances[-1]
    assert process.argv == [sys.executable, "-m", "venus_profile.watch_events"]
    assert process.flags & Gio.SubprocessFlags.STDIN_PIPE        # helper exits when we go away
    _run_until(lambda: ends)
    assert events == [{"event": "dpi-stage", "stage": 2}, {"event": "profile", "profile": 4}]
    assert ends == [{"status": "disconnected", "message": "gone"}]
    assert watch.running is False


def test_stopping_the_watch_kills_the_helper_without_an_end_callback(monkeypatch):
    from venus_profile.gui import device_events
    monkeypatch.setattr(device_events.Gio.Subprocess, "new",
                        lambda argv, flags: _FakeProcess(argv, flags, ['{"status": "listening"}\n']))
    ends = []
    watch = device_events.start_event_watch(lambda _e: None, ends.append)
    watch.stop()
    _pump()
    assert _FakeProcess.instances[-1].killed and ends == []


@pytest.mark.parametrize(("original", "tampered"), (
    ('HELPER_MODULE = "venus_profile.watch_events"', 'HELPER_MODULE = "venus_profile.read_device_memory"'),
    ('argv = [sys.executable, "-m", HELPER_MODULE]', 'argv = [sys.executable, "-m", HELPER_MODULE, "-x"]'),
    ("Gio.Subprocess.new(\n                argv,", "Gio.Subprocess.new(\n                [sys.executable],"),
))
def test_gui_audit_rejects_a_changed_event_helper_launch(tmp_path, original, tampered):
    from venus_profile.gui import safety
    source = (Path(safety.__file__).parent / "device_events.py").read_text(encoding="utf-8")
    assert original in source
    fake = tmp_path / "device_events.py"
    fake.write_text(source.replace(original, tampered), encoding="utf-8")
    with pytest.raises(RuntimeError):
        safety._audit_helper_launch(fake)


def test_gui_audit_does_not_let_one_launcher_start_the_other_helper(tmp_path):
    from venus_profile.gui import safety
    source = (Path(safety.__file__).parent / "device_events.py").read_text(encoding="utf-8")
    fake = tmp_path / "device_read.py"        # read launcher file naming the event helper
    fake.write_text(source, encoding="utf-8")
    with pytest.raises(RuntimeError):
        safety._audit_helper_launch(fake)


# --------------------------------------------------------------- active profile (0x002C)

def test_read_selects_the_mouse_active_profile(setup):
    window, reader, watcher = setup
    window.workspace.select_slot(1)
    _read_ok(window, reader, active=2)                       # 0x002C = 2: profile 3
    assert window.active_mouse_profile == 3 and window.workspace.selected_slot == 3
    watcher.on_event({"event": "dpi-stage", "stage": 4})    # DPI goes to profile 3 without guessing
    assert window.workspace.profile(3).selected_dpi_stage_raw == 4
    assert window.workspace.profile(1).selected_dpi_stage_raw == 0


def test_factory_read_means_profile_1(setup):
    window, reader, watcher = setup
    window.workspace.select_slot(4)
    _read_ok(window, reader)                                 # factory 0x002C = 0
    assert window.active_mouse_profile == 1 and window.workspace.selected_slot == 1


def test_out_of_range_active_byte_falls_back_to_the_old_rule(setup):
    window, reader, watcher = setup
    window.workspace.select_slot(4)
    _read_ok(window, reader, active=7)
    assert window.active_mouse_profile is None and window.workspace.selected_slot == 4

