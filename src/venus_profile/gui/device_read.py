"""Launch the read-only snapshot helper and collect its result (GUI side).

The GUI process never opens the mouse. To read it, the window launches the
separate helper ``venus_profile.read_snapshot`` with a fixed argument list (no
user input reaches it), waits for its single JSON document on stdout, and
hands the parsed result back. The helper performs only the captured F2 read
plan; it has no way to write (``venus_profile.snapshot_audit``).

This module is the GUI's only process launch; ``gui/safety.py`` checks that
the call site and its argument list stay exactly as written here.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

HELPER_MODULE = "venus_profile.read_snapshot"
READ_TIMEOUT_SECONDS = 15
#: Set to "1" to skip reading the mouse (development and automated checks).
DISABLE_ENV = "VENUS_PROFILE_NO_DEVICE_READ"

STATUS_CANCELLED = "cancelled"
STATUS_TIMEOUT = "timeout"
STATUS_LAUNCH_FAILED = "launch-failed"
STATUS_BAD_OUTPUT = "bad-output"


def device_read_disabled() -> bool:
    return os.environ.get(DISABLE_ENV) == "1"


def parse_helper_output(stdout: str | None) -> dict:
    """The helper's JSON document (its last non-empty stdout line)."""
    lines = [line for line in (stdout or "").splitlines() if line.strip()]
    if not lines:
        return {"status": STATUS_BAD_OUTPUT, "message": "the mouse reader produced no output"}
    try:
        document = json.loads(lines[-1])
    except ValueError:
        return {"status": STATUS_BAD_OUTPUT, "message": "the mouse reader produced unreadable output"}
    if not isinstance(document, dict) or "status" not in document:
        return {"status": STATUS_BAD_OUTPUT, "message": "the mouse reader produced unexpected output"}
    return document


class SnapshotRead:
    """One asynchronous run of the helper; calls ``on_done(document)`` once."""

    def __init__(self, on_done: Callable[[dict], None]) -> None:
        self._on_done = on_done
        self._process: Gio.Subprocess | None = None
        self._cancellable = Gio.Cancellable()
        self._timeout_id: int | None = None
        self._finished = False

    def start(self) -> "SnapshotRead":
        argv = [sys.executable, "-m", HELPER_MODULE]
        try:
            self._process = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as exc:
            self._finish({"status": STATUS_LAUNCH_FAILED, "message": exc.message})
            return self
        self._process.communicate_utf8_async(None, self._cancellable, self._communicated)
        self._timeout_id = GLib.timeout_add_seconds(READ_TIMEOUT_SECONDS, self._timed_out)
        return self

    def cancel(self) -> None:
        self._stop({"status": STATUS_CANCELLED, "message": "Reading the mouse was cancelled"})

    def _timed_out(self) -> bool:
        self._timeout_id = None
        self._stop({"status": STATUS_TIMEOUT,
                    "message": f"the mouse did not answer within {READ_TIMEOUT_SECONDS} seconds"})
        return GLib.SOURCE_REMOVE

    def _stop(self, document: dict) -> None:
        if self._finished:
            return
        self._cancellable.cancel()
        if self._process is not None:
            self._process.force_exit()
        self._finish(document)

    def _communicated(self, process, result) -> None:
        if self._finished:
            return
        try:
            _ok, stdout, _stderr = process.communicate_utf8_finish(result)
        except GLib.Error as exc:
            self._finish({"status": STATUS_BAD_OUTPUT, "message": exc.message})
            return
        self._finish(parse_helper_output(stdout))

    def _finish(self, document: dict) -> None:
        if self._finished:
            return
        self._finished = True
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None
        self._on_done(document)


def start_snapshot_read(on_done: Callable[[dict], None]) -> SnapshotRead:
    return SnapshotRead(on_done).start()
