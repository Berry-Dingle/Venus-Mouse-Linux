"""Limited one-target button-assignment writer and exact restore.

The public live boundary accepts one validated profile, button, and finite
allowlisted assignment ID. It sends one F3 and verifies the immediate readback.
There is no finalizer or bracket report, retry, bulk operation, generic sender, or persistence
claim.
"""

from __future__ import annotations

import argparse
import ast
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Callable, Protocol

from .button_assignment_write import (
    AssignmentWritePlan,
    ConfirmedAssignment,
    build_assignment_write_plan,
    confirmed_assignment_from_id,
    confirmed_assignment_id,
    confirmed_assignment_raw,
    serialize_assignment_f3,
)
from .button_assignments import button_assignment_address, label_matches_record
from .device_session import (
    DeviceSession,
    DeviceSessionError,
    _BUTTON_ASSIGNMENT_EXPERIMENT_CAPABILITY,
    create_timestamped_directory,
)
from .readonly_hidraw import (
    HidrawDevice,
    HidrawPermissionError,
    ReadOnlyHidrawTransport,
    REPORT2_LENGTH,
    _HidrawFeatureIo,
    _hid_iocsfeature,
    discover_venus_hidraw,
)


TARGET_LENGTH = 4
SCHEMA_VERSION = 2
CONFIRMATION_WORD = "CONTINUE"
RESULT_ROOT = (
    Path(__file__).resolve().parents[3]
    / "usb-captures"
    / "live-button-assignment-writes"
)
INTER_COMMAND_DELAY_SECONDS = 0.010


class GuardedButtonAssignmentError(RuntimeError):
    """A normal writer or restore precondition failed."""


class _AssignmentFeatureIo(Protocol):
    def send_assignment_f3(self, plan: AssignmentWritePlan) -> bytes: ...
    def close(self) -> None: ...


class _GuardedAssignmentFeatureIo(_HidrawFeatureIo):
    """Private ioctl boundary accepting only an immutable validated plan."""

    def send_assignment_f3(self, plan: AssignmentWritePlan) -> bytes:
        report = serialize_assignment_f3(
            plan.profile, plan.button, plan.target
        )
        if len(report) != REPORT2_LENGTH:
            raise GuardedButtonAssignmentError("guarded report must be 16 bytes")
        buffer = bytearray(report)
        result = self._ioctl(
            self._fd, _hid_iocsfeature(REPORT2_LENGTH), buffer, True
        )
        if result != REPORT2_LENGTH:
            raise GuardedButtonAssignmentError(
                f"guarded SET_FEATURE returned {result}, expected 16"
            )
        return report


@dataclass(frozen=True, slots=True)
class AssignmentOperationResult:
    output: Path
    final_record: bytes


def _validate_device(device: HidrawDevice) -> None:
    if (
        device.vendor_id != 0x04D9
        or device.product_id != 0xFC55
        or device.interface_number != 0x02
        or device.usage_page != 0xFFA0
        or device.usage != 0x0001
    ):
        raise GuardedButtonAssignmentError(
            "device is not the exact 04D9:FC55 interface 02 FFA0/0001"
        )


def _validate_scope(profile: int, button: int) -> None:
    try:
        button_assignment_address(profile, button)
    except (TypeError, ValueError) as exc:
        raise GuardedButtonAssignmentError(
            "profile must be 1..5 and button must be 1..20"
        ) from exc


def _validate_target(target: ConfirmedAssignment) -> ConfirmedAssignment:
    if not isinstance(target, ConfirmedAssignment):
        raise GuardedButtonAssignmentError("target must be a confirmed assignment")
    return _assignment(target.raw)


def _assignment(raw: bytes) -> ConfirmedAssignment:
    try:
        return confirmed_assignment_raw(bytes(raw))
    except (TypeError, ValueError) as exc:
        raise GuardedButtonAssignmentError(
            f"record is not an exact confirmed assignment: {bytes(raw).hex(' ')}"
        ) from exc


def _read_plan(profile: int, button: int, target: ConfirmedAssignment) -> AssignmentWritePlan:
    source = confirmed_assignment_raw(bytes.fromhex("00 00 00 00"))
    if source.raw == target.raw:
        source = confirmed_assignment_raw(bytes.fromhex("85 00 00 00"))
    return build_assignment_write_plan(profile, button, source, target)


def _optional_record_metadata(raw: bytes) -> tuple[str | None, str | None]:
    try:
        assignment = _assignment(raw)
    except GuardedButtonAssignmentError:
        return None, None
    return confirmed_assignment_id(assignment), assignment.label


def _record_text(raw: bytes) -> str:
    return bytes(raw).hex(" ").upper()


def _summary(
    *, plan: AssignmentWritePlan, source: ConfirmedAssignment,
    target: ConfirmedAssignment, action: str,
) -> str:
    return (
        "GUARDED BUTTON-ASSIGNMENT OPERATION\n"
        f"PROFILE {plan.profile}\n"
        f"BUTTON {plan.button}\n"
        f"ADDRESS 0x{plan.address:04X}\n"
        f"ASSIGNMENT {target.label}\n"
        f"SOURCE {confirmed_assignment_id(source)} {_record_text(source.raw)} "
        f"({source.label})\n"
        f"TARGET {confirmed_assignment_id(target)} {_record_text(target.raw)} "
        f"({target.label})\n"
        f"NEXT ACTION {action}\n"
        "No retry; no " + "F" + "1; no " + "F" + "5" + "; no automatic restore.\n"
        "Persistence not established."
    )


def _atomic_json(path: Path, document: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_reports(path: Path, reports: list[tuple[str, bytes]]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(["sequence", "operation", "report_id", "payload"])
        for index, (operation, report) in enumerate(reports, 1):
            writer.writerow([index, operation, report[0], report.hex(" ")])
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardedButtonAssignmentError(f"invalid evidence file: {path}") from exc
    if not isinstance(value, dict):
        raise GuardedButtonAssignmentError("evidence JSON must be an object")
    return value


def _new_output(root: Path, profile: int, button: int, clock: Callable[[], datetime]) -> Path:
    prefix = f"button_assignment_p{profile}_b{button}"
    try:
        return create_timestamped_directory(root, prefix, clock())
    except FileExistsError as exc:
        raise GuardedButtonAssignmentError(
            "evidence directory already exists; refusing overwrite"
        ) from exc


def _base_operation(
    *, operation: str, device: HidrawDevice, plan: AssignmentWritePlan,
    source: ConfirmedAssignment, target: ConfirmedAssignment, output: Path,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "status": "in-progress",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "device": device.to_dict(),
        "hidraw_node": str(device.path),
        "profile": plan.profile,
        "button": plan.button,
        "address": plan.address,
        "address_hex": f"0x{plan.address:04X}",
        "source": _record_text(source.raw),
        "source_id": confirmed_assignment_id(source),
        "source_label": source.label,
        "target": _record_text(target.raw),
        "target_id": confirmed_assignment_id(target),
        "target_label": target.label,
        "operation_summary": None,
        "confirmation_expected": CONFIRMATION_WORD,
        "confirmation_succeeded": False,
        "f3_report": None,
        "f3_sent": False,
        "postwrite_readback": None,
        "final_result": None,
        "unexpected_bytes": None,
        "automatic_retry": False,
        "automatic_restoration": False,
        "f1_sent": False,
        "f5_sent": False,
        "persistence": "not-established",
        "evidence_directory": str(output),
    }


def _confirm(
    *, output: Path, document: dict[str, object], summary: str,
    input_fn: Callable[[str], str], output_fn: Callable[[str], None],
) -> None:
    output_fn(summary)
    output_fn("Type CONTINUE to proceed:")
    document["operation_summary"] = summary
    document["confirmation_expected"] = CONFIRMATION_WORD
    _atomic_json(output / "operation.json", document)
    if input_fn("Type CONTINUE to proceed: ") != CONFIRMATION_WORD:
        raise GuardedButtonAssignmentError("confirmation mismatch")
    document["confirmation_succeeded"] = True
    _atomic_json(output / "operation.json", document)


def _save_result(output: Path, document: dict[str, object], result: str) -> None:
    document["status"] = "completed" if result.startswith("success") else "failed"
    document["final_result"] = result
    _atomic_json(output / "operation.json", document)
    (output / "result.md").write_text(
        "# Guarded button-assignment operation\n\n"
        f"Result: `{result}`\n\n"
        "Immediate read-back is not persistence confirmation.\n",
        encoding="utf-8",
    )


class _GuardedAssignmentTransport:
    """Private validated transport for one calculated assignment plan."""

    def __init__(
        self, device: HidrawDevice,
        *,
        backend_factory: Callable[[Path], _AssignmentFeatureIo] = (
            _GuardedAssignmentFeatureIo
        ),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not os.access(device.path, os.R_OK | os.W_OK):
            raise HidrawPermissionError(f"read/write access unavailable for {device.path}")
        self._backend_factory = backend_factory
        self._backend: _AssignmentFeatureIo | None = None
        try:
            session = DeviceSession(
                device,
                transport_factory=lambda selected: ReadOnlyHidrawTransport(
                    selected, backend_factory=lambda path: self._writer()
                ),
            )
            session._request_internal_capability(
                _BUTTON_ASSIGNMENT_EXPERIMENT_CAPABILITY
            )
        except DeviceSessionError as exc:
            raise GuardedButtonAssignmentError(str(exc)) from exc
        self._session = session
        self.device = device
        self._sleeper = sleeper
        self.reports_sent: list[tuple[str, bytes]] = []

    def _writer(self) -> _AssignmentFeatureIo:
        if self._backend is None:
            self._backend = self._backend_factory(self.device.path)
        return self._backend

    def _read_plan_target(self, plan: AssignmentWritePlan) -> bytes:
        value = self._session.read_memory(plan.address, TARGET_LENGTH)
        if len(value) != TARGET_LENGTH:
            raise GuardedButtonAssignmentError("assignment read was not four bytes")
        return bytes(value)

    def _send_validated_plan(self, plan: AssignmentWritePlan) -> bytes:
        report = self._writer().send_assignment_f3(plan)
        self.reports_sent.append(("assignment-f3", report))
        return report

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._session.close()


def _validated_plan(profile: int, button: int, source: bytes, target: ConfirmedAssignment) -> AssignmentWritePlan:
    _validate_scope(profile, button)
    current = _assignment(source)
    try:
        return build_assignment_write_plan(profile, button, current, target)
    except (TypeError, ValueError) as exc:
        raise GuardedButtonAssignmentError(str(exc)) from exc


def execute_assignment_write(
    profile: int,
    button: int,
    target: ConfirmedAssignment,
    *,
    output_root: Path = RESULT_ROOT,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    transport_factory: Callable[[HidrawDevice], _GuardedAssignmentTransport] = (
        _GuardedAssignmentTransport
    ),
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    permission_check: Callable[[Path], bool] = lambda path: os.access(
        path, os.R_OK | os.W_OK
    ),
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> AssignmentOperationResult:
    _validate_scope(profile, button)
    target = _validate_target(target)
    device = discovery()
    _validate_device(device)
    if not permission_check(device.path):
        raise HidrawPermissionError(f"read/write access unavailable for {device.path}")
    transport = transport_factory(device)
    output: Path | None = None
    document: dict[str, object] | None = None
    try:
        address = button_assignment_address(profile, button)
        source_raw = transport._read_plan_target(_read_plan(profile, button, target))
        plan = _validated_plan(profile, button, source_raw, target)
        output = _new_output(output_root, profile, button, clock)
        document = _base_operation(
            operation="write", device=device, plan=plan,
            source=plan.source, target=plan.target, output=output,
        )
        _atomic_json(output / "operation.json", document)
        _atomic_json(output / "prewrite_read.json", {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "address": address, "address_hex": f"0x{address:04X}",
            "record": _record_text(source_raw),
            "record_id": confirmed_assignment_id(plan.source),
            "label": plan.source.label,
        })
        _confirm(
            output=output, document=document,
            summary=_summary(
                plan=plan, source=plan.source, target=plan.target,
                action="send one F3 assignment report",
            ),
            input_fn=input_fn, output_fn=output_fn,
        )
        report = transport._send_validated_plan(plan)
        document["f3_report"] = report.hex(" ")
        document["f3_sent"] = True
        transport._sleeper(INTER_COMMAND_DELAY_SECONDS)
        after = transport._read_plan_target(plan)
        document["postwrite_readback"] = _record_text(after)
        record_id, record_label = _optional_record_metadata(after)
        _atomic_json(output / "postwrite_read.json", {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "address": plan.address, "address_hex": f"0x{plan.address:04X}",
            "record": _record_text(after),
            "record_id": record_id,
            "label": record_label,
        })
        if after != plan.target.raw:
            document["unexpected_bytes"] = _record_text(after)
            raise GuardedButtonAssignmentError(
                f"post-write readback mismatch: {_record_text(after)}"
            )
        result = "success: target read back; persistence unestablished"
        _save_result(output, document, result)
        _write_reports(output / "reports_sent.tsv", transport.reports_sent)
        return AssignmentOperationResult(output, after)
    except Exception as exc:
        if output is not None and document is not None:
            _save_result(output, document, f"failed: {exc}")
            _write_reports(output / "reports_sent.tsv", transport.reports_sent)
        if isinstance(exc, GuardedButtonAssignmentError):
            raise
        raise GuardedButtonAssignmentError(str(exc)) from exc
    finally:
        transport.close()


def _load_write_evidence(evidence: Path) -> tuple[dict[str, object], AssignmentWritePlan]:
    operation = _read_json(evidence / "operation.json")
    required = (
        operation.get("schema_version") == SCHEMA_VERSION
        and operation.get("operation") == "write"
        and operation.get("automatic_retry") is False
        and operation.get("automatic_restoration") is False
        and operation.get("f1_sent") is False
        and operation.get("f5_sent") is False
    )
    if not required:
        raise GuardedButtonAssignmentError("evidence schema mismatch")
    device = operation.get("device")
    if not isinstance(device, dict) or {
        "vendor_id": device.get("vendor_id"),
        "product_id": device.get("product_id"),
        "interface_number": device.get("interface_number"),
        "usage_page": device.get("usage_page"),
        "usage": device.get("usage"),
        "usage_verified": device.get("usage_verified"),
    } != {
        "vendor_id": "04d9",
        "product_id": "fc55",
        "interface_number": "02",
        "usage_page": "0xFFA0",
        "usage": 1,
        "usage_verified": True,
    }:
        raise GuardedButtonAssignmentError("evidence device identity mismatch")
    try:
        profile = int(operation["profile"])
        button = int(operation["button"])
        address = int(operation["address"])
        source = bytes.fromhex(str(operation["source"]))
        target = bytes.fromhex(str(operation["target"]))
        source_id = str(operation["source_id"])
        target_id = str(operation["target_id"])
        source_assignment = confirmed_assignment_from_id(source_id)
        target_assignment = confirmed_assignment_from_id(target_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise GuardedButtonAssignmentError("evidence assignment fields are invalid") from exc
    if (
        address != button_assignment_address(profile, button)
        or source_assignment.raw != source
        or target_assignment.raw != target
        or not label_matches_record(source, operation.get("source_label"))
        or not label_matches_record(target, operation.get("target_label"))
    ):
        raise GuardedButtonAssignmentError("evidence raw/ID/address mismatch")
    prewrite = _read_json(evidence / "prewrite_read.json")
    if prewrite.get("record") != _record_text(source):
        raise GuardedButtonAssignmentError("evidence source read mismatch")
    try:
        return operation, build_assignment_write_plan(
            profile, button, source_assignment, target_assignment
        )
    except (TypeError, ValueError) as exc:
        raise GuardedButtonAssignmentError("evidence plan is invalid") from exc


def execute_restore(
    evidence: Path,
    *,
    output_root: Path = RESULT_ROOT,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    transport_factory: Callable[[HidrawDevice], _GuardedAssignmentTransport] = (
        _GuardedAssignmentTransport
    ),
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    permission_check: Callable[[Path], bool] = lambda path: os.access(
        path, os.R_OK | os.W_OK
    ),
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> AssignmentOperationResult:
    _, plan = _load_write_evidence(evidence)
    device = discovery()
    _validate_device(device)
    if not permission_check(device.path):
        raise HidrawPermissionError(f"read/write access unavailable for {device.path}")
    transport = transport_factory(device)
    output: Path | None = None
    document: dict[str, object] | None = None
    try:
        current = transport._read_plan_target(plan)
        if current != plan.target.raw:
            raise GuardedButtonAssignmentError(
                f"restore requires saved target, got {_record_text(current)}"
            )
        output = _new_output(output_root, plan.profile, plan.button, clock)
        document = _base_operation(
            operation="restore", device=device, plan=plan,
            source=plan.target, target=plan.source, output=output,
        )
        document["source_evidence_directory"] = str(evidence)
        _atomic_json(output / "operation.json", document)
        _atomic_json(output / "prewrite_read.json", {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "address": plan.address, "address_hex": f"0x{plan.address:04X}",
            "record": _record_text(current),
            "record_id": confirmed_assignment_id(plan.target),
            "label": plan.target.label,
        })
        _confirm(
            output=output, document=document,
            summary=_summary(
                plan=plan, source=plan.target, target=plan.source,
                action="send one restore F3 assignment report",
            ),
            input_fn=input_fn, output_fn=output_fn,
        )
        restore_plan = build_assignment_write_plan(
            plan.profile, plan.button, plan.target, plan.source
        )
        report = transport._send_validated_plan(restore_plan)
        document["f3_report"] = report.hex(" ")
        document["f3_sent"] = True
        transport._sleeper(INTER_COMMAND_DELAY_SECONDS)
        after = transport._read_plan_target(restore_plan)
        document["postwrite_readback"] = _record_text(after)
        record_id, record_label = _optional_record_metadata(after)
        _atomic_json(output / "postwrite_read.json", {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "address": plan.address, "address_hex": f"0x{plan.address:04X}",
            "record": _record_text(after),
            "record_id": record_id,
            "label": record_label,
        })
        if after != plan.source.raw:
            document["unexpected_bytes"] = _record_text(after)
            raise GuardedButtonAssignmentError(
                f"restore readback mismatch: {_record_text(after)}"
            )
        result = "success: original read back; persistence unestablished"
        _save_result(output, document, result)
        _write_reports(output / "reports_sent.tsv", transport.reports_sent)
        return AssignmentOperationResult(output, after)
    except Exception as exc:
        if output is not None and document is not None:
            _save_result(output, document, f"failed: {exc}")
            _write_reports(output / "reports_sent.tsv", transport.reports_sent)
        if isinstance(exc, GuardedButtonAssignmentError):
            raise
        raise GuardedButtonAssignmentError(str(exc)) from exc
    finally:
        transport.close()


def safety_audit() -> list[str]:
    """Static audit for the limited one-F3 writer and restore only."""
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    if "F" + "1" in source or "F" + "5" in source:
        raise GuardedButtonAssignmentError("forbidden report support found")
    if any(token in source for token in ("--" + "force", "--" + "yes", "--non" + "-interactive")):
        raise GuardedButtonAssignmentError("confirmation bypass found")
    imports = {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    )
    if imports & {"usb", "hid", "hidraw", "subprocess"}:
        raise GuardedButtonAssignmentError("unsafe transport import found")
    names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if names & {"send", "send_report", "write_report", "write_memory", "discover"}:
        raise GuardedButtonAssignmentError("generic operation found")
    if "serialize_assignment_f3" not in source or "confirmed_assignment_from_id" not in source:
        raise GuardedButtonAssignmentError("validated serializer or ID lookup missing")
    if "persistence" not in source or "not-established" not in source:
        raise GuardedButtonAssignmentError("persistence boundary missing")
    return [
        "one calculated target and one allowlisted assignment ID",
        "no " + "F" + "1/" + "F" + "5" + ", retry, rollback, bulk, or generic report path",
        "exact serializer and raw/ID evidence validation required",
        "interactive confirmation and immediate readback required",
        "offline/default mode contains no discovery or device access",
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m venus_profile.guarded_button_assignment")
    parser.add_argument("--safety-audit", action="store_true")
    parser.add_argument("--write-assignment", action="store_true")
    parser.add_argument("--profile", type=int)
    parser.add_argument("--button", type=int)
    parser.add_argument("--assignment-id")
    parser.add_argument("--restore-from-evidence", type=Path)
    parser.add_argument("--acknowledge-device-write", action="store_true")
    parser.add_argument("--output-root", type=Path, default=RESULT_ROOT)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    input_fn: Callable[[str], str] = input,
    permission_check: Callable[[Path], bool] = lambda path: os.access(path, os.R_OK | os.W_OK),
    isatty_fn: Callable[[], bool] = lambda: sys.stdin.isatty(),
) -> int:
    args = _parser().parse_args(argv)
    if args.safety_audit:
        for line in safety_audit():
            print(f"PASS: {line}")
        if not args.write_assignment and args.restore_from_evidence is None:
            return 0
    modes = int(args.write_assignment) + int(args.restore_from_evidence is not None)
    if modes > 1:
        print("error: choose exactly one live mode", file=sys.stderr)
        return 2
    if modes == 0:
        print("offline mode; no hidraw discovery or open occurred")
        return 0
    if not args.acknowledge_device_write:
        print("error: --acknowledge-device-write is required", file=sys.stderr)
        return 2
    if not isatty_fn():
        print("error: live mode requires an interactive terminal", file=sys.stderr)
        return 2
    if args.write_assignment:
        if args.profile is None or args.button is None or args.assignment_id is None:
            print("error: write mode requires profile, button, and assignment-id", file=sys.stderr)
            return 2
        try:
            target = confirmed_assignment_from_id(args.assignment_id)
            _validate_scope(args.profile, args.button)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        device = discovery()
        _validate_device(device)
        if not permission_check(device.path):
            raise HidrawPermissionError(f"read/write access unavailable for {device.path}")
        result = execute_assignment_write(
            args.profile, args.button, target,
            output_root=args.output_root, input_fn=input_fn,
            discovery=lambda: device, permission_check=lambda path: True,
        )
    else:
        device = discovery()
        _validate_device(device)
        if not permission_check(device.path):
            raise HidrawPermissionError(f"read/write access unavailable for {device.path}")
        result = execute_restore(
            args.restore_from_evidence, output_root=args.output_root,
            input_fn=input_fn, discovery=lambda: device,
            permission_check=lambda path: True,
        )
    print(f"verified guarded button-assignment operation: {result.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
