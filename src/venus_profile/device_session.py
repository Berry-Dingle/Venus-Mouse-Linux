"""Reusable, F2-read-only Venus device session foundation.

The public session API has no SET_REPORT or memory-write operation. The sole
internal capability name exists only so the separately guarded brightness
module can bind its already restricted writer to the same validated device.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import grp
import json
import os
from pathlib import Path
import pwd
import stat
from typing import Callable

from .known_device_state import KNOWN_STATE_PATH
from .offline_usb_capture import MemoryImage
from .readonly_hidraw import (
    HidrawDevice,
    HidrawPermissionError,
    HidrawProtocolError,
    ReadOnlyHidrawTransport,
    build_f2_request,
    discover_venus_hidraw,
    response_report_for_length,
    validate_f2_response,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FACTORY_TRANSACTIONS = (
    PROJECT_ROOT / "usb-captures" / "analysis" / "factory_transactions.json"
)
_BRIGHTNESS_CAPABILITY = "venus-internal-brightness-044f-v1"
_LED_MODE_CAPABILITY = "venus-internal-led-mode-0449-v1"
_LED_COLOUR_CAPABILITY = "venus-internal-led-colour-0449-v1"
_CUSTOM_COLOUR_CAPABILITY = "venus-internal-custom-colour-0449-v1"
_LIGHTING_PREVIEW_CAPABILITY = "venus-internal-live-lighting-0449-0470-v1"
_BUTTON_ASSIGNMENT_EXPERIMENT_CAPABILITY = (
    "venus-internal-button-assignment-experiment-v1"
)


class DeviceSessionError(RuntimeError):
    """The session lifecycle or validated-device contract was violated."""


@dataclass(frozen=True)
class AccessDescription:
    path: Path
    owner: str
    group: str
    mode: int
    readable: bool
    writable: bool

    @property
    def read_write_access(self) -> bool:
        return self.readable and self.writable

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "owner": self.owner,
            "group": self.group,
            "mode": f"{self.mode:04o}",
            "readable": self.readable,
            "writable": self.writable,
            "read_write_access": self.read_write_access,
        }


@dataclass(frozen=True)
class FullReadResult:
    transactions: tuple[dict[str, object], ...]
    memory: MemoryImage


def load_known_read_plan(
    path: Path = FACTORY_TRANSACTIONS,
) -> list[dict[str, object]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    records = document.get("feature_reads")
    if not isinstance(records, list) or len(records) != 193:
        raise DeviceSessionError(
            "known read plan must contain exactly 193 F2 transactions"
        )
    plan: list[dict[str, object]] = []
    for index, record in enumerate(records):
        try:
            address = int(record["address"])
            length = int(record["requested_length"])
            captured_response = bytes.fromhex(str(record["response"]))
            captured_data = bytes.fromhex(str(record["returned_data"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise DeviceSessionError(
                f"malformed known read-plan entry {index}"
            ) from exc
        request = build_f2_request(address, length)
        if validate_f2_response(
            captured_response, address=address, requested_length=length
        ) != captured_data:
            raise DeviceSessionError(
                f"captured response mismatch in read-plan entry {index}"
            )
        plan.append({
            "index": index,
            "address": address,
            "length": length,
            "request": request,
            "captured_response": captured_response,
            "captured_data": captured_data,
        })
    return plan


def create_timestamped_directory(
    root: Path,
    prefix: str,
    now: datetime | None = None,
) -> Path:
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = root / f"{prefix}_{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    return output


def ownership_warnings(
    paths: list[Path],
    *,
    current_uid: int | None = None,
    effective_uid: int | None = None,
    sudo_user: str | None = None,
) -> list[str]:
    current_uid = os.getuid() if current_uid is None else current_uid
    effective_uid = os.geteuid() if effective_uid is None else effective_uid
    warnings: list[str] = []
    for path in paths:
        target = path if path.exists() else path.parent
        if not target.exists():
            continue
        owner_uid = target.stat().st_uid
        if effective_uid != 0 and owner_uid == 0:
            warnings.append(
                f"{target} is root-owned; normal-user updates may fail"
            )
    if effective_uid == 0 and sudo_user:
        warnings.append(
            f"running through sudo as root for user {sudo_user}; outputs may "
            "become root-owned even though udev should permit normal-user access"
        )
    return warnings


def session_safety_audit() -> list[str]:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    numeric_constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    }
    forbidden_commands = {
        int("F1", 16), int("F3", 16), int("F5", 16)
    }
    if numeric_constants & forbidden_commands:
        raise DeviceSessionError(
            "write/finalisation command literal found in session layer"
        )
    public_methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    forbidden_public = {
        "write_memory", "set_feature_report", "send_report", "send_f3",
        "send_f1", "send_f5",
    }
    if public_methods & forbidden_public:
        raise DeviceSessionError("public raw writer found in session layer")
    return [
        "session layer contains no F1/F3/F5 command literals",
        "session exposes no generic report or memory writer",
        "ordinary reads delegate only to validated F2 transport",
        "report-4/report-5 operations are absent",
        "internal semantic capabilities return identity, not a sender",
    ]


class DeviceSession:
    """Validated, lazy, context-managed F2 read session."""

    def __init__(
        self,
        device: HidrawDevice,
        *,
        transport_factory: Callable[[HidrawDevice], ReadOnlyHidrawTransport] = (
            ReadOnlyHidrawTransport
        ),
        stat_fn: Callable[[Path], os.stat_result] = Path.stat,
        access_fn: Callable[[Path, int], bool] = os.access,
    ) -> None:
        if (
            device.vendor_id != 0x04D9
            or device.product_id != 0xFC55
            or device.interface_number != 0x02
        ):
            raise DeviceSessionError(
                "session requires 04D9:FC55 USB interface 02"
            )
        if (device.usage_page, device.usage) != (0xFFA0, 1):
            raise DeviceSessionError(
                "session requires confirmed usage page FFA0, usage 1"
            )
        self.device = device
        self._transport_factory = transport_factory
        self._transport: ReadOnlyHidrawTransport | None = None
        self._stat_fn = stat_fn
        self._access_fn = access_fn
        self._closed = False

    @classmethod
    def discover(
        cls,
        *,
        discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
        **kwargs,
    ) -> "DeviceSession":
        return cls(discovery(), **kwargs)

    def _require_active(self) -> None:
        if self._closed:
            raise DeviceSessionError("device session is closed")

    def describe(self) -> dict[str, object]:
        self._require_active()
        return self.device.to_dict()

    def check_access(self) -> AccessDescription:
        self._require_active()
        try:
            metadata = self._stat_fn(self.device.path)
        except OSError as exc:
            raise HidrawPermissionError(
                f"could not stat selected hidraw node {self.device.path}: {exc}"
            ) from exc
        mode = stat.S_IMODE(metadata.st_mode)
        try:
            owner = pwd.getpwuid(metadata.st_uid).pw_name
        except KeyError:
            owner = str(metadata.st_uid)
        try:
            group = grp.getgrgid(metadata.st_gid).gr_name
        except KeyError:
            group = str(metadata.st_gid)
        return AccessDescription(
            path=self.device.path,
            owner=owner,
            group=group,
            mode=mode,
            readable=self._access_fn(self.device.path, os.R_OK),
            writable=self._access_fn(self.device.path, os.W_OK),
        )

    def open_read_session(self) -> "DeviceSession":
        """Validate lifecycle/access without opening until the first F2 read."""

        self._require_active()
        access = self.check_access()
        if not access.read_write_access:
            raise HidrawPermissionError(
                f"read/write access is required for F2 feature reports on "
                f"{self.device.path}"
            )
        return self

    def _reader(self) -> ReadOnlyHidrawTransport:
        self._require_active()
        if self._transport is None:
            self._transport = self._transport_factory(self.device)
        return self._transport

    def read_memory(self, address: int, length: int) -> bytes:
        self._require_active()
        return self._reader().read_memory(address, length)

    def read_full_known_plan(
        self,
        *,
        plan_path: Path = FACTORY_TRANSACTIONS,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> FullReadResult:
        self._require_active()
        plan = load_known_read_plan(plan_path)
        values: dict[int, int] = {}
        overlaps: list[dict[str, int]] = []
        transactions: list[dict[str, object]] = []
        for record in plan:
            address = int(record["address"])
            length = int(record["length"])
            data = self.read_memory(address, length)
            response = self._reader().last_response
            if response is None:
                raise HidrawProtocolError("F2 transport retained no response")
            report_id, _ = response_report_for_length(length)
            for offset, value in enumerate(data):
                target = address + offset
                if target in values:
                    overlaps.append({
                        "address": target,
                        "previous": values[target],
                        "new": value,
                        "agree": int(values[target] == value),
                    })
                values[target] = value
            transactions.append({
                "index": record["index"],
                "timestamp": clock().isoformat(),
                "address": address,
                "address_hex": f"0x{address:04X}",
                "length": length,
                "request": bytes(record["request"]).hex(" "),
                "command": "F2",
                "response_report_id": report_id,
                "response": response.hex(" "),
                "returned_data": data.hex(" "),
            })
        return FullReadResult(
            tuple(transactions), MemoryImage(values, overlaps)
        )

    def _request_internal_capability(self, capability: str) -> HidrawDevice:
        """Internal identity gate; it does not return a sender."""

        self._require_active()
        if capability not in {
            _BRIGHTNESS_CAPABILITY,
            _LED_MODE_CAPABILITY,
            _LED_COLOUR_CAPABILITY,
            _CUSTOM_COLOUR_CAPABILITY,
            _LIGHTING_PREVIEW_CAPABILITY,
            _BUTTON_ASSIGNMENT_EXPERIMENT_CAPABILITY,
        }:
            raise DeviceSessionError("unknown internal device capability")
        return self.device

    def close(self) -> None:
        if not self._closed:
            if self._transport is not None:
                self._transport.close()
                self._transport = None
            self._closed = True

    def __enter__(self) -> "DeviceSession":
        self._require_active()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
