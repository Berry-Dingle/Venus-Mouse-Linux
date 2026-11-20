"""Launch the one-shot lighting helper and collect its result (GUI side).

The GUI process never opens the mouse. To send a lighting change it launches
``venus_profile.preview_lighting`` with a fixed argument list, writes one JSON
request (semantic lighting values built by ``lighting_preview_plan``) to its
stdin, and waits for its single JSON result on stdout. The helper performs one
guarded lighting transaction and exits (``snapshot_audit.
preview_lighting_safety_audit``).

``gui/safety.py`` checks that the call site and its argument list stay exactly
as written here.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from .device_read import device_read_disabled, parse_helper_output

HELPER_MODULE = "venus_profile.preview_lighting"
WRITE_TIMEOUT_SECONDS = 10

#: Set to "1" to never send anything to the mouse (tests and automated checks);
#: ``VENUS_PROFILE_NO_DEVICE_READ=1`` disables writes too.
DISABLE_WRITE_ENV = "VENUS_PROFILE_NO_DEVICE_WRITE"

STATUS_DISABLED = "disabled"
STATUS_TIMEOUT = "timeout"
STATUS_LAUNCH_FAILED = "launch-failed"
STATUS_BAD_OUTPUT = "bad-output"


def device_write_disabled() -> bool:
    return os.environ.get(DISABLE_WRITE_ENV) == "1" or device_read_disabled()


class LightingWrite:
    """One asynchronous run of the helper; calls ``on_done(document)`` once."""

    def __init__(self, request: str, on_done: Callable[[dict], None]) -> None:
        self._request = request
        self._on_done = on_done
        self._process: Gio.Subprocess | None = None
        self._cancellable = Gio.Cancellable()
        self._timeout_id: int | None = None
        self._finished = False

    def start(self) -> "LightingWrite":
        if device_write_disabled():
            self._finish({"status": STATUS_DISABLED, "message": "sending to the mouse is disabled"})
            return self
        argv = [sys.executable, "-m", HELPER_MODULE]
        try:
            self._process = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE
                | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as exc:
            self._finish({"status": STATUS_LAUNCH_FAILED, "message": exc.message})
            return self
        self._process.communicate_utf8_async(self._request, self._cancellable, self._communicated)
        self._timeout_id = GLib.timeout_add_seconds(WRITE_TIMEOUT_SECONDS, self._timed_out)
        return self

    def _timed_out(self) -> bool:
        self._timeout_id = None
        if not self._finished:
            self._cancellable.cancel()
            if self._process is not None:
                self._process.force_exit()
            self._finish({"status": STATUS_TIMEOUT,
                          "message": f"the mouse did not answer within {WRITE_TIMEOUT_SECONDS} seconds"})
        return GLib.SOURCE_REMOVE

    def _communicated(self, process, result) -> None:
        if self._finished:
            return
        try:
            _ok, stdout, _stderr = process.communicate_utf8_finish(result)
        except GLib.Error as exc:
            self._finish({"status": STATUS_BAD_OUTPUT, "message": exc.message})
            return
        document = parse_helper_output(stdout)
        if document.get("status") == "bad-output":
            document["message"] = "the lighting writer produced no usable output"
        self._finish(document)

    def _finish(self, document: dict) -> None:
        if self._finished:
            return
        self._finished = True
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None
        self._on_done(document)


def start_lighting_write(request: str, on_done: Callable[[dict], None]) -> LightingWrite:
    return LightingWrite(request, on_done).start()
