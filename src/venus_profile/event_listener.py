"""Listen-only probe for the mouse's event reports (user-run, live).

Evidence: ``investigation/LIVE_EVENT_FINDINGS.md``. The Windows utility learns
about DPI±, profile and polling changes from 8-byte input reports with report
ID 8 on the vendor collection of interface 2 (``08 02 vv`` = DPI stage, ``08 01
vv`` = profile, ``08 06 vv`` = polling, ``08 09 …`` = lighting mode). Those
reports have never been seen on the wire; this probe exists to confirm them.

Safety boundary:

* takes **no options** and listens for a fixed :data:`LISTEN_SECONDS`;
* finds the node with the existing exact discovery (04D9:FC55, interface 02)
  and additionally requires the report descriptor to confirm usage page
  FFA0, usage 1;
* opens the node **read-only** (``O_RDONLY``) and only calls ``read``: no
  ioctl, no write, no feature reports, nothing is ever sent to the mouse;
* prints each input report and saves one JSON evidence file under
  ``usb-captures/live-events/`` (new timestamped file, never overwritten).

``event_listener_safety_audit`` statically proves the above. Run only by the
user: ``python -m venus_profile.event_listener``. Tests inject fakes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import select
import sys
import time
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

LISTEN_SECONDS = 30
READ_SIZE = 64
EVENT_REPORT_ID = 0x08
PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = PROJECT_ROOT / "usb-captures" / "live-events"

#: Polling code -> rate, the same encoding as memory 0x0032 (and ldcfg.exe's
#: table at 0x414de0).
POLLING_HZ = {0x08: 125, 0x04: 250, 0x02: 500, 0x01: 1000}

STATUS_OK = "ok"
STATUS_NO_DEVICE = "no-device"
STATUS_MULTIPLE = "multiple-devices"
STATUS_PERMISSION = "permission-denied"
STATUS_IDENTITY = "identity-mismatch"
STATUS_READ_FAILED = "read-failed"

INSTRUCTIONS = (
    "Listening for {seconds} s (nothing is sent to the mouse). Now press, a few "
    "seconds apart: DPI+ three times, DPI- three times, then the profile button "
    "(if the mouse has one) and any polling-rate button."
)


def describe_report(report: bytes) -> str:
    """Plain interpretation of one input report (findings, not verified)."""
    if not report:
        return "empty read"
    if report[0] != EVENT_REPORT_ID or len(report) < 3:
        return f"report ID {report[0]:#04x} (not an event report)"
    kind, value = report[1], report[2]
    if kind == 0x02:
        return f"DPI stage -> {value + 1} (expected from DPI+/DPI-)"
    if kind == 0x01:
        return f"profile -> {value + 1}"
    if kind == 0x06:
        rate = POLLING_HZ.get(value)
        return f"polling -> {rate} Hz" if rate else f"polling code {value:#04x} (unknown)"
    if kind == 0x09:
        return "lighting mode changed (raw bytes; mapping not verified)"
    if kind == 0xFF:
        return "0xFF event (handled separately by Windows; not analysed)"
    return f"unknown event kind {kind:#04x}"


def _require_identity(device: HidrawDevice) -> None:
    if (device.vendor_id, device.product_id, device.interface_number) != (
        VENUS_VENDOR_ID, VENUS_PRODUCT_ID, VENUS_INTERFACE
    ):
        raise ValueError("listener requires 04D9:FC55 USB interface 02")
    if (device.usage_page, device.usage) != (VENUS_USAGE_PAGE, VENUS_USAGE):
        raise ValueError("listener requires a confirmed usage page FFA0, usage 1")


def listen(
    *,
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    opener: Callable[[str, int], int] = os.open,
    seconds: float = LISTEN_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    out: Callable[[str], None] = print,
) -> dict[str, object]:
    """Listen for input reports; always returns a document (never raises)."""
    try:
        device = discovery()
    except HidrawDiscoveryError as exc:
        text = str(exc)
        status = STATUS_MULTIPLE if text.startswith("multiple") else STATUS_NO_DEVICE
        return {"status": status, "message": text}
    except OSError as exc:
        return {"status": STATUS_READ_FAILED, "message": str(exc)}
    try:
        _require_identity(device)
    except ValueError as exc:
        return {"status": STATUS_IDENTITY, "message": str(exc)}
    try:
        fd = opener(str(device.path), os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except PermissionError as exc:
        return {"status": STATUS_PERMISSION, "message": str(exc)}
    except OSError as exc:
        return {"status": STATUS_READ_FAILED, "message": str(exc)}

    out(INSTRUCTIONS.format(seconds=int(seconds)))
    reports: list[dict[str, object]] = []
    start = clock()
    try:
        while True:
            remaining = seconds - (clock() - start)
            if remaining <= 0:
                break
            ready, _w, _x = select.select([fd], [], [], remaining)
            if not ready:
                continue
            try:
                report = os.read(fd, READ_SIZE)
            except BlockingIOError:
                continue
            if not report:
                break
            offset = round(clock() - start, 3)
            meaning = describe_report(report)
            reports.append({"t": offset, "hex": report.hex(" "), "meaning": meaning})
            out(f"{offset:7.3f}s  {report.hex(' ')}  {meaning}")
    except OSError as exc:
        return {"status": STATUS_READ_FAILED, "message": str(exc), "reports": reports}
    finally:
        os.close(fd)
    out(f"Done: {len(reports)} report(s).")
    return {
        "status": STATUS_OK,
        "device": {key: str(value) for key, value in device.to_dict().items()},
        "seconds": seconds,
        "reports": reports,
    }


def save_evidence(document: dict[str, object], directory: Path = EVIDENCE_DIR) -> Path | None:
    """Write a new timestamped evidence file; never overwrites; None if no tree."""
    if not directory.parent.is_dir():
        return None
    directory.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = directory / f"venus_events_{stamp}.json"
    record = {"tool": "venus_profile.event_listener", "utc": stamp, **document}
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    return path


def main(argv: list[str] | None = None, evidence_dir: Path = EVIDENCE_DIR, **listen_kwargs) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print("this probe takes no options")
        return 2
    document = listen(**listen_kwargs)
    if document["status"] != STATUS_OK:
        print(f"status: {document['status']} - {document.get('message', '')}")
        return 1
    saved = save_evidence(document, evidence_dir)
    if saved is not None:
        print(f"Saved: {saved}")
    return 0


if __name__ == "__main__":  # pragma: no cover - live use is user-run
    raise SystemExit(main())
