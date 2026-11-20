"""Launch the listen-only event helper and pass its events on (GUI side).

After a successful read of the mouse the window starts the separate helper
``venus_profile.watch_events`` with a fixed argument list (no user input
reaches it). The helper only reads the mouse's own DPI/profile event reports
and prints one JSON object per line; it has no way to send anything to the
mouse (``venus_profile.snapshot_audit.watch_events_safety_audit``).

The helper's stdin is a pipe held open by this process: when the GUI exits,
the pipe closes and the helper exits too. ``gui/safety.py`` checks that the
call site and its argument list stay exactly as written here.
"""

from __future__ import annotations

import json
import sys
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

HELPER_MODULE = "venus_profile.watch_events"

STATUS_LISTENING = "listening"
STATUS_ENDED = "ended"
STATUS_LAUNCH_FAILED = "launch-failed"


def parse_event_line(line: str | None) -> dict | None:
    """One helper line as a dict, or None for blank/unreadable lines."""
    if not line or not line.strip():
        return None
    try:
        document = json.loads(line)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


class EventWatch:
    """A running event helper.

    ``on_event(document)`` is called for each ``{"event": ...}`` line;
    ``on_end(document)`` once, with the helper's last status, when it stops by
    itself. ``stop()`` ends it without calling ``on_end``.
    """

    def __init__(self, on_event: Callable[[dict], None], on_end: Callable[[dict], None]) -> None:
        self._on_event = on_event
        self._on_end = on_end
        self._process: Gio.Subprocess | None = None
        self._stream: Gio.DataInputStream | None = None
        self._cancellable = Gio.Cancellable()
        self._last_status: dict = {"status": STATUS_ENDED, "message": "the mouse event helper stopped"}
        self._finished = False

    def start(self) -> "EventWatch":
        argv = [sys.executable, "-m", HELPER_MODULE]
        try:
            self._process = Gio.Subprocess.new(
                argv,
                Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE
                | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as exc:
            self._finish({"status": STATUS_LAUNCH_FAILED, "message": exc.message})
            return self
        self._stream = Gio.DataInputStream.new(self._process.get_stdout_pipe())
        self._read_next()
        return self

    @property
    def running(self) -> bool:
        return not self._finished

    def stop(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._cancellable.cancel()
        if self._process is not None:
            self._process.force_exit()

    def _read_next(self) -> None:
        self._stream.read_line_async(GLib.PRIORITY_DEFAULT, self._cancellable, self._line_ready)

    def _line_ready(self, stream, result) -> None:
        if self._finished:
            return
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error as exc:
            self._finish({"status": STATUS_ENDED, "message": exc.message})
            return
        if line is None:                      # helper exited
            self._finish(self._last_status)
            return
        document = parse_event_line(line)
        if document is not None:
            if "event" in document:
                self._on_event(document)
            elif document.get("status") != STATUS_LISTENING:
                self._last_status = document
        self._read_next()

    def _finish(self, document: dict) -> None:
        if self._finished:
            return
        self._finished = True
        self._on_end(document)


def start_event_watch(on_event: Callable[[dict], None], on_end: Callable[[dict], None]) -> EventWatch:
    return EventWatch(on_event, on_end).start()
