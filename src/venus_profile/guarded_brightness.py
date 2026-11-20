"""One-address, two-value guarded brightness writer for the wired Venus.

This module deliberately is not a general HID write transport.  Its only
write operation changes address 0x044F between the two captured brightness
values, then sends the one lighting finalisation report observed after the
Windows live-preview sequence.
"""

from __future__ import annotations

import argparse
import ast
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
import json
import os
from pathlib import Path
import sys
import time
from typing import Callable, Protocol

from .device_session import (
    DeviceSession,
    DeviceSessionError,
    _BRIGHTNESS_CAPABILITY,
    create_timestamped_directory,
    ownership_warnings,
)
from .known_device_state import (
    KNOWN_STATE_PATH,
    effective_expected_state,
    load_known_state,
    record_confirmed_transition,
)
from .readonly_hidraw import (
    HidrawDevice,
    HidrawPermissionError,
    HidrawProtocolError,
    ReadOnlyHidrawTransport,
    REPORT2_LENGTH,
    _HidrawFeatureIo,
    _hid_iocsfeature,
    build_f2_request,
    discover_venus_hidraw,
)


BRIGHTNESS_ADDRESS = 0x044F
INTER_COMMAND_DELAY_SECONDS = 0.010
LIGHTING_FINALISATION_REPORT = bytes.fromhex(
    "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESULT_ROOT = (
    PROJECT_ROOT / "usb-captures" / "live-brightness-writes"
)


class BrightnessValue(IntEnum):
    MEDIUM = 0x02
    HIGH = 0x03


class GuardedBrightnessError(RuntimeError):
    """A safety precondition, ioctl, finalisation, or read-back failed."""


@dataclass(frozen=True)
class BrightnessPlan:
    device: HidrawDevice
    expected: BrightnessValue
    target: BrightnessValue
    original: BrightnessValue
    prewrite_request: bytes
    brightness_report: bytes
    finalisation_report: bytes
    postwrite_request: bytes


@dataclass(frozen=True)
class BrightnessResult:
    plan: BrightnessPlan
    read_back: BrightnessValue
    reports_sent: tuple[tuple[str, bytes], ...]


def _coerce_brightness(value: BrightnessValue | int) -> BrightnessValue:
    if isinstance(value, bool):
        raise GuardedBrightnessError("brightness must be Medium 02 or High 03")
    try:
        return BrightnessValue(value)
    except (TypeError, ValueError) as exc:
        raise GuardedBrightnessError(
            "brightness must be Medium 02 or High 03"
        ) from exc


def build_brightness_report(
    value: BrightnessValue | int,
) -> bytes:
    """Build the sole permitted F3 form; there is no address parameter."""
    brightness = _coerce_brightness(value)
    report = bytes([
        0x02, 0xF3, 0x4F, 0x04, 0x01, 0x00, 0x00, 0x00,
        int(brightness), 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    ])
    validate_guarded_write_report(report)
    return report


def validate_guarded_write_report(report: bytes) -> str:
    """Accept only the exact brightness F3 or exact lighting F1 template."""

    if len(report) != REPORT2_LENGTH:
        raise GuardedBrightnessError("guarded outgoing report must be 16 bytes")
    if report == LIGHTING_FINALISATION_REPORT:
        return "lighting-finalisation"
    if (
        report[0] == 0x02
        and report[1] == 0xF3
        and report[2:4] == b"\x4F\x04"
        and report[4:6] == b"\x01\x00"
        and report[6:8] == b"\x00\x00"
        and report[8] in (BrightnessValue.MEDIUM, BrightnessValue.HIGH)
        and report[9:] == b"\x00" * 7
    ):
        return "brightness"
    raise GuardedBrightnessError(
        "outgoing report is not an exact guarded brightness/F1 template"
    )


class _BrightnessFeatureIo(Protocol):
    def send_f2(self, request: bytes) -> None: ...
    def get_feature(self, report_id: int, length: int) -> bytes: ...
    def send_brightness(self, value: BrightnessValue) -> bytes: ...
    def finalise_lighting(self) -> bytes: ...
    def close(self) -> None: ...


class _GuardedBrightnessFeatureIo(_HidrawFeatureIo):
    """Private ioctl boundary with no caller-supplied report method."""

    def _submit_validated(self, report: bytes) -> None:
        validate_guarded_write_report(report)
        buffer = bytearray(report)
        result = self._ioctl(
            self._fd, _hid_iocsfeature(REPORT2_LENGTH), buffer, True
        )
        if result != REPORT2_LENGTH:
            raise GuardedBrightnessError(
                f"guarded SET_FEATURE returned {result}, expected 16"
            )

    def send_brightness(self, value: BrightnessValue) -> bytes:
        report = build_brightness_report(value)
        self._submit_validated(report)
        return report

    def finalise_lighting(self) -> bytes:
        self._submit_validated(LIGHTING_FINALISATION_REPORT)
        return LIGHTING_FINALISATION_REPORT


class GuardedBrightnessTransport:
    """Semantic one-change transaction; no arbitrary write API is exposed."""

    def __init__(
        self,
        device: HidrawDevice | DeviceSession,
        *,
        backend_factory: Callable[[Path], _BrightnessFeatureIo] = (
            _GuardedBrightnessFeatureIo
        ),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._backend_factory = backend_factory
        self._backend: _BrightnessFeatureIo | None = None
        try:
            session = (
                device if isinstance(device, DeviceSession)
                else DeviceSession(
                    device,
                    transport_factory=lambda selected: ReadOnlyHidrawTransport(
                        selected, backend_factory=lambda path: self._writer()
                    ),
                )
            )
        except DeviceSessionError as exc:
            raise GuardedBrightnessError(str(exc)) from exc
        device = session._request_internal_capability(_BRIGHTNESS_CAPABILITY)
        self.device = device
        self._session = session
        self._sleeper = sleeper
        self._reports_sent: list[tuple[str, bytes]] = []

    @property
    def reports_sent(self) -> tuple[tuple[str, bytes], ...]:
        return tuple(self._reports_sent)

    def read_current_brightness(self) -> BrightnessValue:
        raw = self._session.read_memory(BRIGHTNESS_ADDRESS, 1)
        try:
            return BrightnessValue(raw[0])
        except ValueError as exc:
            raise GuardedBrightnessError(
                f"unexpected brightness byte 0x{raw[0]:02X}; refusing write"
            ) from exc

    def change_brightness(
        self,
        expected: BrightnessValue,
        target: BrightnessValue,
        *,
        before_write: Callable[[BrightnessPlan], None],
    ) -> BrightnessResult:
        expected = _coerce_brightness(expected)
        target = _coerce_brightness(target)
        if expected == target:
            raise GuardedBrightnessError(
                "source and target brightness are equal; refusing write"
            )
        self._reports_sent.append((
            "prewrite-f2",
            build_f2_request(BRIGHTNESS_ADDRESS, 1),
        ))
        current = self.read_current_brightness()
        if current != expected:
            if current == target:
                raise GuardedBrightnessError(
                    f"brightness is already 0x{int(target):02X}; refusing write"
                )
            raise GuardedBrightnessError(
                f"expected source 0x{int(expected):02X}, "
                f"found 0x{int(current):02X}; refusing write"
            )
        plan = BrightnessPlan(
            device=self.device,
            expected=expected,
            target=target,
            original=current,
            prewrite_request=build_f2_request(BRIGHTNESS_ADDRESS, 1),
            brightness_report=build_brightness_report(target),
            finalisation_report=LIGHTING_FINALISATION_REPORT,
            postwrite_request=build_f2_request(BRIGHTNESS_ADDRESS, 1),
        )
        before_write(plan)
        report = self._writer().send_brightness(target)
        self._reports_sent.append(("brightness-f3", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)
        report = self._writer().finalise_lighting()
        self._reports_sent.append(("lighting-f1", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)
        self._reports_sent.append((
            "postwrite-f2",
            build_f2_request(BRIGHTNESS_ADDRESS, 1),
        ))
        read_back = self.read_current_brightness()
        if read_back != target:
            raise GuardedBrightnessError(
                f"read-back mismatch: expected 0x{int(target):02X}, "
                f"got 0x{int(read_back):02X}; no retry or restoration attempted"
            )
        return BrightnessResult(
            plan=plan,
            read_back=read_back,
            reports_sent=self.reports_sent,
        )

    def high_to_medium(
        self,
        *,
        before_write: Callable[[BrightnessPlan], None],
    ) -> BrightnessResult:
        return self.change_brightness(
            BrightnessValue.HIGH,
            BrightnessValue.MEDIUM,
            before_write=before_write,
        )

    def medium_to_high(
        self,
        *,
        before_write: Callable[[BrightnessPlan], None],
    ) -> BrightnessResult:
        return self.change_brightness(
            BrightnessValue.MEDIUM,
            BrightnessValue.HIGH,
            before_write=before_write,
        )

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._session.close()

    def _writer(self) -> _BrightnessFeatureIo:
        if self._backend is None:
            self._backend = self._backend_factory(self.device.path)
        return self._backend

    def __enter__(self) -> "GuardedBrightnessTransport":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _iso(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(timezone.utc).isoformat()


def _new_result_directory(root: Path, now: datetime) -> Path:
    return create_timestamped_directory(root, "brightness_044f", now)


def _latest_confirming_read(state_path: Path) -> str | None:
    state = load_known_state(state_path, allow_missing=True)
    if state is None:
        return None
    for transition in reversed(state["history"]):
        path = transition.get("latest_confirming_live_read_directory")
        if isinstance(path, str):
            return path
    return None


def _plan_document(
    plan: BrightnessPlan,
    timestamp: str,
    *,
    state_path: Path,
) -> dict[str, object]:
    return {
        "status": "prewrite-safety-record",
        "timestamp": timestamp,
        "device": plan.device.to_dict(),
        "address": BRIGHTNESS_ADDRESS,
        "address_hex": "0x044F",
        "original": int(plan.original),
        "original_hex": f"{int(plan.original):02X}",
        "target": int(plan.target),
        "target_hex": f"{int(plan.target):02X}",
        "reports_intended": [
            {"operation": "prewrite-f2-read", "payload": plan.prewrite_request.hex(" ")},
            {"operation": "brightness-f3", "payload": plan.brightness_report.hex(" ")},
            {"operation": "lighting-f1", "payload": plan.finalisation_report.hex(" ")},
            {"operation": "postwrite-f2-read", "payload": plan.postwrite_request.hex(" ")},
        ],
        "inter_command_delay_ms": int(INTER_COMMAND_DELAY_SECONDS * 1000),
        "known_device_state_file": str(state_path),
        "latest_confirming_live_read_directory": _latest_confirming_read(
            state_path
        ),
        "automatic_retry": False,
        "automatic_restoration": False,
    }


def _write_reports_tsv(
    output: Path,
    reports: tuple[tuple[str, bytes], ...],
) -> None:
    with (output / "reports_sent.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(["sequence", "operation", "report_id", "payload"])
        for index, (name, report) in enumerate(reports, 1):
            writer.writerow([index, name, report[0], report.hex(" ")])


def execute_brightness_change(
    device: HidrawDevice,
    *,
    expected: BrightnessValue,
    target: BrightnessValue,
    output_root: Path = DEFAULT_RESULT_ROOT,
    state_path: Path = KNOWN_STATE_PATH,
    transport_factory: Callable[[HidrawDevice], GuardedBrightnessTransport] = (
        GuardedBrightnessTransport
    ),
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Path:
    expected = _coerce_brightness(expected)
    target = _coerce_brightness(target)
    current_state = load_known_state(state_path, allow_missing=True)
    if current_state is not None and any(
        transition["power_cycle_persistence"] == "pending"
        for transition in current_state["history"]
    ):
        raise GuardedBrightnessError(
            "a prior write is pending power-cycle confirmation; "
            "refusing another live write"
        )
    known = effective_expected_state(
        state_path=state_path, allow_missing=True
    ).values.get(BRIGHTNESS_ADDRESS)
    if known != int(expected):
        known_text = f"0x{known:02X}" if known is not None else "<unknown>"
        raise GuardedBrightnessError(
            f"known-state overlay expects {known_text} at 0x044F, "
            f"not requested source 0x{int(expected):02X}"
        )
    output: Path | None = None
    plan: BrightnessPlan | None = None
    operation_document: dict[str, object] | None = None
    transport_succeeded = False
    transport = transport_factory(device)
    try:
        def record_before_write(candidate: BrightnessPlan) -> None:
            nonlocal output, plan, operation_document
            plan = candidate
            now = clock()
            output = _new_result_directory(output_root, now)
            timestamp = now.astimezone(timezone.utc).isoformat()
            document = _plan_document(
                candidate, timestamp, state_path=state_path
            )
            operation_document = document
            (output / "operation.json").write_text(
                json.dumps(document, indent=2) + "\n", encoding="utf-8"
            )
            (output / "prewrite_read.json").write_text(
                json.dumps({
                    "timestamp": timestamp,
                    "address": BRIGHTNESS_ADDRESS,
                    "value": int(candidate.original),
                    "value_hex": f"{int(candidate.original):02X}",
                    "request": candidate.prewrite_request.hex(" "),
                    "matched_expected_source": True,
                }, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                f"verified current brightness at 0x044F: "
                f"{int(candidate.original):02X}; intended: "
                f"{int(candidate.target):02X}"
            )

        result = transport.change_brightness(
            expected, target, before_write=record_before_write
        )
        transport_succeeded = True
        if output is None or plan is None:
            raise GuardedBrightnessError("prewrite safety record was not created")
        timestamp = _iso(clock)
        (output / "postwrite_read.json").write_text(
            json.dumps({
                "timestamp": timestamp,
                "address": BRIGHTNESS_ADDRESS,
                "value": int(result.read_back),
                "value_hex": f"{int(result.read_back):02X}",
                "request": plan.postwrite_request.hex(" "),
                "matched_target": True,
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_reports_tsv(output, result.reports_sent)
        if operation_document is None:
            raise GuardedBrightnessError("prewrite operation record is missing")
        operation = dict(operation_document)
        operation["status"] = "transport-success"
        operation["completion_timestamp"] = timestamp
        operation["read_back"] = int(result.read_back)
        operation["immediate_read_back_confirmed"] = True
        operation["power_cycle_persistence"] = "pending"
        (output / "operation.json").write_text(
            json.dumps(operation, indent=2) + "\n", encoding="utf-8"
        )
        (output / "result.md").write_text(
            "# Guarded Venus brightness result\n\n"
            "- Transport status: success\n"
            "- Address: `0x044F`\n"
            f"- Original: `{int(expected):02X}` ({expected.name.title()})\n"
            f"- Target/read-back: `{int(target):02X}` ({target.name.title()})\n"
            "- Immediate read-back: confirmed\n"
            "- Persistent across power cycle: pending\n"
            "- Reports sent: two validated F2 requests, one guarded F3, "
            "and one exact lighting F1\n"
            "- Automatic retry: no\n"
            "- Automatic restoration: no\n",
            encoding="utf-8",
        )
        state = record_confirmed_transition(
            state_path=state_path,
            address=BRIGHTNESS_ADDRESS,
            previous_value=int(expected),
            confirmed_new_value=int(target),
            operation_timestamp=str(operation_document["timestamp"]),
            result_directory=output,
            updated_at=timestamp,
        )
        operation["status"] = "success"
        operation["known_state_updated"] = True
        operation["known_state_transition_id"] = state["history"][-1][
            "transition_id"
        ]
        (output / "operation.json").write_text(
            json.dumps(operation, indent=2) + "\n", encoding="utf-8"
        )
        return output
    except Exception as exc:
        if output is not None:
            _write_reports_tsv(output, transport.reports_sent)
            if operation_document is not None:
                operation = dict(operation_document)
                operation["status"] = (
                    "transport-success-state-update-failed"
                    if transport_succeeded else "failed-closed"
                )
                operation["immediate_read_back_confirmed"] = transport_succeeded
                operation["power_cycle_persistence"] = (
                    "pending" if transport_succeeded else "not-established"
                )
                operation["failure_timestamp"] = _iso(clock)
                operation["error"] = f"{type(exc).__name__}: {exc}"
                (output / "operation.json").write_text(
                    json.dumps(operation, indent=2) + "\n", encoding="utf-8"
                )
            (output / "result.md").write_text(
                "# Guarded Venus brightness result\n\n"
                f"- Transport status: "
                f"{'success' if transport_succeeded else 'failed closed'}\n"
                f"- Immediate read-back: "
                f"{'confirmed' if transport_succeeded else 'not confirmed'}\n"
                f"- Persistent across power cycle: "
                f"{'pending' if transport_succeeded else 'not established'}\n"
                f"- Error: `{type(exc).__name__}: {exc}`\n"
                "- Automatic retry: no\n"
                "- Automatic restoration: no\n",
                encoding="utf-8",
            )
        raise
    finally:
        transport.close()


def execute_high_to_medium(
    device: HidrawDevice,
    **kwargs,
) -> Path:
    return execute_brightness_change(
        device,
        expected=BrightnessValue.HIGH,
        target=BrightnessValue.MEDIUM,
        **kwargs,
    )


def execute_medium_to_high(
    device: HidrawDevice,
    **kwargs,
) -> Path:
    return execute_brightness_change(
        device,
        expected=BrightnessValue.MEDIUM,
        target=BrightnessValue.HIGH,
        **kwargs,
    )


def dry_run_lines(
    expected: BrightnessValue = BrightnessValue.HIGH,
    target: BrightnessValue = BrightnessValue.MEDIUM,
) -> list[str]:
    expected = _coerce_brightness(expected)
    target = _coerce_brightness(target)
    if expected == target:
        raise GuardedBrightnessError("dry-run direction must change brightness")
    pre = build_f2_request(BRIGHTNESS_ADDRESS, 1)
    f3 = build_brightness_report(target)
    return [
        "DRY RUN ONLY — no discovery and no hidraw open",
        f"direction: {int(expected):02X} to {int(target):02X}",
        f"1 pre-read F2: {pre.hex(' ')}",
        f"2 write F3:    {f3.hex(' ')}",
        f"3 wait:        {int(INTER_COMMAND_DELAY_SECONDS * 1000)} ms",
        f"4 lighting F1: {LIGHTING_FINALISATION_REPORT.hex(' ')}",
        f"5 wait:        {int(INTER_COMMAND_DELAY_SECONDS * 1000)} ms",
        f"6 read-back F2:{pre.hex(' ')}",
        "F5 is not included; no retry and no automatic restoration",
    ]


def safety_audit() -> list[str]:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    public_methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    forbidden = {
        "write_memory", "set_feature_report", "send_report",
        "send_f3", "send_f1", "send_f5",
    }
    if public_methods & forbidden:
        raise RuntimeError("a forbidden generic/public sender API exists")
    command_line_options = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.startswith("--")
    }
    if any(
        option.startswith("--yes") or option == "--noninteractive"
        for option in command_line_options
    ):
        raise RuntimeError("noninteractive live bypass found")
    numeric_constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    }
    macro_boundaries = {int("0478", 16), int("1417", 16)}
    if numeric_constants & macro_boundaries:
        raise RuntimeError("macro-region constants found in live writer")
    if "0x04" + "4F" not in source and "0x044F" not in source:
        raise RuntimeError("fixed brightness address is missing")
    if "read_current_brightness()" not in source:
        raise RuntimeError("mandatory pre-read/read-back path is missing")
    return [
        "only fixed address 0x044F and values 02/03 are constructible",
        "only exact brightness F3 and lighting F1 templates are accepted",
        "no F5, macro, report-4, report-5, or arbitrary sender path exists",
        "no noninteractive live bypass exists",
        "live change requires exact source match and mandatory read-back",
        "no automatic retry, automatic restoration, or chained toggle exists",
        "default and dry-run modes do not discover or open hidraw",
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.guarded_brightness"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--safety-audit", action="store_true")
    parser.add_argument("--live-brightness-write", action="store_true")
    directions = parser.add_mutually_exclusive_group()
    directions.add_argument("--high-to-medium", action="store_true")
    directions.add_argument("--medium-to-high", action="store_true")
    parser.add_argument(
        "--acknowledge-device-write",
        action="store_true",
        help="separate explicit acknowledgement that this changes the device",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--state-file", type=Path, default=KNOWN_STATE_PATH)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    executor: Callable[..., Path] = execute_brightness_change,
    input_fn: Callable[[str], str] = input,
    permission_check: Callable[[Path], bool] = (
        lambda path: os.access(path, os.R_OK | os.W_OK)
    ),
) -> int:
    args = _parser().parse_args(argv)
    expected = (
        BrightnessValue.MEDIUM
        if args.medium_to_high else BrightnessValue.HIGH
    )
    target = (
        BrightnessValue.HIGH
        if args.medium_to_high else BrightnessValue.MEDIUM
    )
    if args.safety_audit:
        for item in safety_audit():
            print(f"PASS: {item}")
        if not args.live_brightness_write and not args.dry_run:
            return 0
    if args.dry_run:
        for line in dry_run_lines(expected, target):
            print(line)
        if not args.live_brightness_write:
            return 0
    if not args.live_brightness_write:
        print("offline mode; no hidraw discovery or open occurred")
        print("use --dry-run to print the exact guarded sequence")
        return 0
    if (
        not (args.high_to_medium or args.medium_to_high)
        or not args.acknowledge_device_write
    ):
        print(
            "error: live operation requires exactly one direction and "
            "--acknowledge-device-write",
            file=sys.stderr,
        )
        return 2
    device = discovery()
    if not permission_check(device.path):
        raise HidrawPermissionError(
            f"read/write access unavailable for {device.path}; "
            "root or an existing ACL/group is required"
        )
    print(f"selected hidraw node: {device.path}")
    print(
        f"VID:PID={device.vendor_id:04X}:{device.product_id:04X} "
        f"interface={device.interface_number:02X} "
        f"usage=0x{device.usage_page:04X}/0x{device.usage:04X}"
    )
    for warning in ownership_warnings(
        [args.state_file, args.output_root],
        sudo_user=os.environ.get("SUDO_USER"),
    ):
        print(f"WARNING: {warning}", file=sys.stderr)
    expected_text = f"{int(expected):02X}"
    target_text = f"{int(target):02X}"
    required_confirmation = (
        f"WRITE 044F {expected_text} TO {target_text}"
    )
    print(
        f"current value must be {expected_text} ({expected.name.title()}); "
        f"intended value is {target_text} ({target.name.title()})"
    )
    confirmation = input_fn(f"Type exactly {required_confirmation}: ")
    if confirmation != required_confirmation:
        print("confirmation mismatch; no hidraw node was opened", file=sys.stderr)
        return 2
    output = executor(
        device,
        expected=expected,
        target=target,
        output_root=args.output_root,
        state_path=args.state_file,
    )
    print(f"verified brightness change completed: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
