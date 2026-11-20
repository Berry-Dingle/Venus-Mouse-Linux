"""Read-only snapshot of the mouse's memory, for the GUI (helper process).

The GUI never opens the device. At start-up it launches this module as a
separate process (``python -m venus_profile.read_snapshot``). The helper:

* takes **no options**;
* discovers the one Venus configuration interface with the existing exact
  identity checks (``DeviceSession``: 04D9:FC55, interface 02, usage page
  FFA0/1);
* performs the fixed, captured 193-request F2 read plan, packaged with the
  app (``data/read_plan/factory_read_plan.json``), plus the fixed
  ``EXTRA_READS`` (scroll lines), through the existing read-only transport
  (F2 requests only; there is no write API);
* prints one JSON document on stdout and exits.

It writes no files and sends nothing but validated F2 read requests.
``venus_profile.snapshot_audit`` statically proves its import closure contains
the read path and no writer, report builder or other low-level I/O (see
``docs/DEVICE_READ_BOUNDARY_DESIGN.md``).

Run only by the user (live device access); tests inject a fake session.
"""

from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path
import sys
from typing import Callable

from .device_session import DeviceSession, DeviceSessionError
from .readonly_hidraw import HidrawDiscoveryError, HidrawPermissionError, HidrawProtocolError

SNAPSHOT_VERSION = 1
READ_PLAN_FILE = "factory_read_plan.json"
#: Fixed F2 reads after the captured plan: scroll lines for the five profiles
#: (0x0020, 10 bytes; outside the captured plan; read live by the T2 probe,
#: docs/APPLY_HARDWARE_TESTS.md).
EXTRA_READS = ((0x0020, 10),)

STATUS_OK = "ok"
STATUS_NO_DEVICE = "no-device"
STATUS_MULTIPLE = "multiple-devices"
STATUS_PERMISSION = "permission-denied"
STATUS_IDENTITY = "identity-mismatch"
STATUS_READ_FAILED = "read-failed"
STATUSES = (STATUS_OK, STATUS_NO_DEVICE, STATUS_MULTIPLE, STATUS_PERMISSION,
            STATUS_IDENTITY, STATUS_READ_FAILED)


def packaged_read_plan_path() -> Path:
    """The read plan shipped inside the package (works when installed)."""
    return Path(str(files("venus_profile.data") / "read_plan" / READ_PLAN_FILE))


def _error(status: str, message: str) -> dict[str, object]:
    return {"version": SNAPSHOT_VERSION, "status": status, "message": message}


def memory_ranges(values: dict[int, int]) -> list[dict[str, object]]:
    """Contiguous ``{"address", "hex"}`` runs of a sparse memory image."""
    runs: list[dict[str, object]] = []
    start = previous = None
    chunk = bytearray()
    for address in sorted(values):
        if previous is not None and address == previous + 1:
            chunk.append(values[address])
        else:
            if start is not None:
                runs.append({"address": start, "hex": chunk.hex()})
            start, chunk = address, bytearray((values[address],))
        previous = address
    if start is not None:
        runs.append({"address": start, "hex": chunk.hex()})
    return runs


def take_snapshot(
    session_factory: Callable[[], DeviceSession] = DeviceSession.discover,
    plan_path: Path | None = None,
) -> dict[str, object]:
    """Read the mouse once; always returns a document (never raises)."""
    try:
        session = session_factory()
    except HidrawDiscoveryError as exc:
        text = str(exc)
        status = STATUS_MULTIPLE if text.startswith("multiple") else STATUS_NO_DEVICE
        return _error(status, text)
    except DeviceSessionError as exc:
        return _error(STATUS_IDENTITY, str(exc))
    except (OSError, RuntimeError) as exc:
        return _error(STATUS_READ_FAILED, str(exc))
    try:
        with session:
            device = session.describe()
            session.open_read_session()
            result = session.read_full_known_plan(plan_path=plan_path or packaged_read_plan_path())
            values = dict(result.memory.values)
            for address, length in EXTRA_READS:
                for offset, value in enumerate(session.read_memory(address, length)):
                    values[address + offset] = value
    except HidrawPermissionError as exc:
        return _error(STATUS_PERMISSION, str(exc))
    except DeviceSessionError as exc:
        return _error(STATUS_READ_FAILED, str(exc))
    except (HidrawProtocolError, OSError, RuntimeError, ValueError) as exc:
        return _error(STATUS_READ_FAILED, str(exc))
    return {
        "version": SNAPSHOT_VERSION,
        "status": STATUS_OK,
        "device": {key: str(value) for key, value in device.items()},
        "read_count": len(result.transactions) + len(EXTRA_READS),
        "memory": memory_ranges(values),
    }


def main(argv: list[str] | None = None,
         session_factory: Callable[[], DeviceSession] = DeviceSession.discover) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(json.dumps(_error(STATUS_READ_FAILED, "this helper takes no options")))
        return 2
    document = take_snapshot(session_factory)
    print(json.dumps(document))
    return 0 if document["status"] == STATUS_OK else 1


if __name__ == "__main__":  # pragma: no cover - live use is user-run
    raise SystemExit(main())
