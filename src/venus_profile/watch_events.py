"""Listen-only helper that streams the mouse's DPI and profile events to the GUI.

Confirmed on hardware (``investigation/LIVE_EVENT_FINDINGS.md`` §5): the mouse
sends 8-byte input reports, report ID 8, on its interface-2 hidraw node, on its
own, when its buttons change state:

* ``08 02 vv``: DPI stage changed to ``vv`` (zero-based, 0–4);
* ``08 01 vv``: active profile changed to ``vv`` (zero-based, 0–4).

The GUI launches this module (``python -m venus_profile.watch_events``) after
reading the mouse. The helper:

* takes **no options**;
* finds the node with the existing exact discovery (04D9:FC55, interface 02)
  and requires the descriptor to confirm usage page FFA0, usage 1;
* opens it **read-only** and only calls ``read``: no ioctl, no write, no
  feature report, nothing is ever sent to the mouse; it writes no files;
* prints one JSON object per line on stdout: ``{"status": "listening"}``, then
  ``{"event": "dpi-stage", "stage": n}`` / ``{"event": "profile", "profile":
  n}``; a final ``{"status": ...}`` line if it stops because of the device;
* exits when its stdin closes (the GUI went away) or the mouse is unplugged.

``snapshot_audit.watch_events_safety_audit`` statically proves the above.
Run live only through the GUI or by the user; tests inject fakes.
"""

from __future__ import annotations

import json
import os
import select
import sys
from typing import Callable

from .readonly_hidraw import (
    VENUS_INTERFACE,
    VENUS_PRODUCT_ID,
    VENUS_USAGE,
    VENUS_USAGE_PAGE,
    VENUS_VENDOR_ID,
    HidrawDevice,
    HidrawDiscoveryError,
    discover_venus_hidraw,
)

READ_SIZE = 64
EVENT_REPORT_ID = 0x08
EVENT_PROFILE = 0x01
EVENT_DPI_STAGE = 0x02
STAGE_COUNT = 5
PROFILE_COUNT = 5

STATUS_LISTENING = "listening"
STATUS_NO_DEVICE = "no-device"
STATUS_MULTIPLE = "multiple-devices"
STATUS_PERMISSION = "permission-denied"
STATUS_IDENTITY = "identity-mismatch"
STATUS_DISCONNECTED = "disconnected"


def decode_event(report: bytes) -> dict[str, object] | None:
    """The GUI-facing event for one input report, or None to ignore it."""
    if len(report) < 3 or report[0] != EVENT_REPORT_ID:
        return None
    kind, value = report[1], report[2]
    if kind == EVENT_DPI_STAGE and value < STAGE_COUNT:
        return {"event": "dpi-stage", "stage": value}
    if kind == EVENT_PROFILE and value < PROFILE_COUNT:
        return {"event": "profile", "profile": value}
    return None


def _identity_problem(device: HidrawDevice) -> str | None:
    if (device.vendor_id, device.product_id, device.interface_number) != (
        VENUS_VENDOR_ID, VENUS_PRODUCT_ID, VENUS_INTERFACE
    ):
        return "event helper requires 04D9:FC55 USB interface 02"
    if (device.usage_page, device.usage) != (VENUS_USAGE_PAGE, VENUS_USAGE):
        return "event helper requires a confirmed usage page FFA0, usage 1"
    return None


def watch(
    *,
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    opener: Callable[[str, int], int] = os.open,
    control_fd: int | None = None,
    emit: Callable[[dict[str, object]], None] | None = None,
) -> int:
    """Stream events until ``control_fd`` closes or the device goes away."""
    if emit is None:
        def emit(document: dict[str, object]) -> None:
            print(json.dumps(document), flush=True)
    if control_fd is None:
        control_fd = sys.stdin.fileno()
    try:
        device = discovery()
    except HidrawDiscoveryError as exc:
        text = str(exc)
        emit({"status": STATUS_MULTIPLE if text.startswith("multiple") else STATUS_NO_DEVICE,
              "message": text})
        return 1
    except OSError as exc:
        emit({"status": STATUS_NO_DEVICE, "message": str(exc)})
        return 1
    problem = _identity_problem(device)
    if problem:
        emit({"status": STATUS_IDENTITY, "message": problem})
        return 1
    try:
        fd = opener(str(device.path), os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except PermissionError as exc:
        emit({"status": STATUS_PERMISSION, "message": str(exc)})
        return 1
    except OSError as exc:
        emit({"status": STATUS_DISCONNECTED, "message": str(exc)})
        return 1
    emit({"status": STATUS_LISTENING})
    try:
        while True:
            ready, _w, _x = select.select([fd, control_fd], [], [])
            if control_fd in ready and not os.read(control_fd, 4096):
                return 0                      # the GUI closed our stdin
            if fd not in ready:
                continue
            try:
                report = os.read(fd, READ_SIZE)
            except BlockingIOError:
                continue
            except OSError as exc:            # unplugged: ENODEV / EIO
                emit({"status": STATUS_DISCONNECTED, "message": str(exc)})
                return 1
            if not report:
                emit({"status": STATUS_DISCONNECTED, "message": "the mouse stopped sending"})
                return 1
            event = decode_event(report)
            if event is not None:
                emit(event)
    finally:
        os.close(fd)


def main(argv: list[str] | None = None, **watch_kwargs) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(json.dumps({"status": STATUS_IDENTITY, "message": "this helper takes no options"}))
        return 2
    try:
        return watch(**watch_kwargs)
    except BrokenPipeError:
        return 0                              # the GUI went away mid-write


if __name__ == "__main__":  # pragma: no cover - live use is GUI/user-run
    raise SystemExit(main())
