"""Exact-prefix, Profile-1-only guarded LED-mode write experiment.

This is deliberately not a reusable write transport. It can construct and
submit only the finite captured six-byte prefixes at 0x0449 and the exact
lighting F1 finalisation report. Rainbow/Breathing identity is validated by
the complete 0x044C–0x044E triplet.
"""

from __future__ import annotations

import argparse
import ast
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import sys
import time
from typing import Callable, Protocol

from .device_session import (
    DeviceSession,
    DeviceSessionError,
    _LED_MODE_CAPABILITY,
    create_timestamped_directory,
    ownership_warnings,
)
from .known_led_mode_state import (
    BREATHING_PREFIX,
    FULL_BRIGHTNESS_PREFIX,
    LED_MODE_STATE_PATH,
    PREFIX_ADDRESS,
    PREFIX_LENGTH,
    PREFIX_TO_NAME,
    KnownLedModeStateError,
    load_led_mode_state,
    record_led_mode_transition,
)
from .readonly_hidraw import (
    HidrawDevice,
    HidrawPermissionError,
    ReadOnlyHidrawTransport,
    REPORT2_LENGTH,
    _HidrawFeatureIo,
    _hid_iocsfeature,
    build_f2_request,
    discover_venus_hidraw,
)


INTER_COMMAND_DELAY_SECONDS = 0.010
LIGHTING_FINALISATION_REPORT = bytes.fromhex(
    "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESULT_ROOT = (
    PROJECT_ROOT / "usb-captures" / "live-led-mode-writes"
)


class LedModeState(Enum):
    BREATHING = BREATHING_PREFIX
    FULL_BRIGHTNESS = FULL_BRIGHTNESS_PREFIX
    TRUE_OFF = bytes.fromhex("7b 2d d2 00 00 00")
    CURRENT_FULL_BRIGHTNESS = bytes.fromhex("7b 2d d2 01 00 01")
    RAINBOW = bytes.fromhex("7b 2d d2 02 00 03")

    @property
    def label(self) -> str:
        return PREFIX_TO_NAME[self.value]


class GuardedLedModeError(RuntimeError):
    """An exact-prefix safety precondition or operation failed."""


@dataclass(frozen=True)
class LedModePlan:
    device: HidrawDevice
    expected: LedModeState
    target: LedModeState
    original_prefix: bytes
    prewrite_request: bytes
    mode_report: bytes
    finalisation_report: bytes
    postwrite_request: bytes


@dataclass(frozen=True)
class LedModeResult:
    plan: LedModePlan
    read_back: bytes
    reports_sent: tuple[tuple[str, bytes], ...]


def _coerce_state(value: LedModeState) -> LedModeState:
    if not isinstance(value, LedModeState):
        raise GuardedLedModeError(
            "LED mode is not an exact supported guarded state"
        )
    return value


def build_led_mode_report(state: LedModeState) -> bytes:
    """Build only the captured Profile 1 six-byte F3 report."""
    selected = _coerce_state(state)
    report = (
        bytes.fromhex("02 f3 49 04 06 00 00 00")
        + selected.value
        + b"\x00\x00"
    )
    validate_guarded_led_mode_report(report)
    return report


def validate_guarded_led_mode_report(report: bytes) -> str:
    """Accept only either exact mode-prefix F3 or the exact lighting F1."""
    if len(report) != REPORT2_LENGTH:
        raise GuardedLedModeError("guarded outgoing report must be 16 bytes")
    if report == LIGHTING_FINALISATION_REPORT:
        return "lighting-finalisation"
    fixed_header = bytes.fromhex("02 f3 49 04 06 00 00 00")
    if (
        report[:8] == fixed_header
        and report[8:14] in {
            BREATHING_PREFIX, FULL_BRIGHTNESS_PREFIX,
            LedModeState.TRUE_OFF.value,
            LedModeState.CURRENT_FULL_BRIGHTNESS.value,
            LedModeState.RAINBOW.value,
        }
        and report[14:] == b"\x00\x00"
    ):
        return "profile-1-led-mode-prefix"
    raise GuardedLedModeError(
        "outgoing report is not an exact guarded LED-mode/F1 template"
    )


class _LedModeFeatureIo(Protocol):
    def send_f2(self, request: bytes) -> None: ...
    def get_feature(self, report_id: int, length: int) -> bytes: ...
    def send_led_mode(self, state: LedModeState) -> bytes: ...
    def finalise_lighting(self) -> bytes: ...
    def close(self) -> None: ...


class _GuardedLedModeFeatureIo(_HidrawFeatureIo):
    """Private ioctl boundary; callers cannot submit report bytes."""

    def _submit_exact(self, report: bytes) -> None:
        validate_guarded_led_mode_report(report)
        buffer = bytearray(report)
        result = self._ioctl(
            self._fd, _hid_iocsfeature(REPORT2_LENGTH), buffer, True
        )
        if result != REPORT2_LENGTH:
            raise GuardedLedModeError(
                f"guarded SET_FEATURE returned {result}, expected 16"
            )

    def send_led_mode(self, state: LedModeState) -> bytes:
        report = build_led_mode_report(state)
        self._submit_exact(report)
        return report

    def finalise_lighting(self) -> bytes:
        self._submit_exact(LIGHTING_FINALISATION_REPORT)
        return LIGHTING_FINALISATION_REPORT


class GuardedLedModeTransport:
    """One exact semantic transition, with mandatory pre-read and read-back."""

    def __init__(
        self,
        device: HidrawDevice | DeviceSession,
        *,
        backend_factory: Callable[[Path], _LedModeFeatureIo] = (
            _GuardedLedModeFeatureIo
        ),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._backend_factory = backend_factory
        self._backend: _LedModeFeatureIo | None = None
        try:
            session = (
                device if isinstance(device, DeviceSession)
                else DeviceSession(device)
            )
        except DeviceSessionError as exc:
            raise GuardedLedModeError(str(exc)) from exc
        self.device = session.device
        self._session = session
        self._sleeper = sleeper
        self._reports_sent: list[tuple[str, bytes]] = []

    @property
    def reports_sent(self) -> tuple[tuple[str, bytes], ...]:
        return tuple(self._reports_sent)

    def read_current_prefix(self) -> bytes:
        value = self._session.read_memory(PREFIX_ADDRESS, PREFIX_LENGTH)
        if value not in PREFIX_TO_NAME:
            raise GuardedLedModeError(
                f"unexpected lighting prefix {value.hex(' ')}; refusing write"
            )
        return value

    def change_mode(
        self,
        expected: LedModeState,
        target: LedModeState,
        *,
        before_write: Callable[[LedModePlan], None],
    ) -> LedModeResult:
        expected = _coerce_state(expected)
        target = _coerce_state(target)
        if expected is target:
            raise GuardedLedModeError(
                "source and target LED modes are equal; refusing write"
            )
        request = build_f2_request(PREFIX_ADDRESS, PREFIX_LENGTH)
        self._reports_sent.append(("prewrite-f2", request))
        current = self.read_current_prefix()
        if current != expected.value:
            if current == target.value:
                raise GuardedLedModeError(
                    f"LED mode is already {target.label}; refusing write"
                )
            raise GuardedLedModeError(
                f"expected source {expected.value.hex(' ')}, found "
                f"{current.hex(' ')}; refusing write"
            )
        plan = LedModePlan(
            device=self.device,
            expected=expected,
            target=target,
            original_prefix=current,
            prewrite_request=request,
            mode_report=build_led_mode_report(target),
            finalisation_report=LIGHTING_FINALISATION_REPORT,
            postwrite_request=request,
        )
        before_write(plan)
        report = self._writer().send_led_mode(target)
        self._reports_sent.append(("led-mode-f3", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)
        report = self._writer().finalise_lighting()
        self._reports_sent.append(("lighting-f1", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)
        self._reports_sent.append(("postwrite-f2", request))
        read_back = self.read_current_prefix()
        if read_back != target.value:
            raise GuardedLedModeError(
                f"read-back mismatch: expected {target.value.hex(' ')}, got "
                f"{read_back.hex(' ')}; no retry or restoration attempted"
            )
        return LedModeResult(plan, read_back, self.reports_sent)

    def full_to_breathing(
        self, *, before_write: Callable[[LedModePlan], None]
    ) -> LedModeResult:
        return self.change_mode(
            LedModeState.FULL_BRIGHTNESS,
            LedModeState.BREATHING,
            before_write=before_write,
        )

    def breathing_to_full(
        self, *, before_write: Callable[[LedModePlan], None]
    ) -> LedModeResult:
        return self.change_mode(
            LedModeState.BREATHING,
            LedModeState.FULL_BRIGHTNESS,
            before_write=before_write,
        )

    def off_to_full(
        self, *, before_write: Callable[[LedModePlan], None]
    ) -> LedModeResult:
        return self.change_mode(
            LedModeState.TRUE_OFF,
            LedModeState.CURRENT_FULL_BRIGHTNESS,
            before_write=before_write,
        )

    def rainbow_to_full(
        self, *, before_write: Callable[[LedModePlan], None]
    ) -> LedModeResult:
        return self.change_mode(
            LedModeState.RAINBOW,
            LedModeState.CURRENT_FULL_BRIGHTNESS,
            before_write=before_write,
        )

    def _writer(self) -> _LedModeFeatureIo:
        if self._backend is None:
            # This identity gate and write-capable fd are intentionally delayed
            # until after the exact six-byte source prefix has matched.
            self._session._request_internal_capability(_LED_MODE_CAPABILITY)
            self._backend = self._backend_factory(self.device.path)
        return self._backend

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._session.close()

    def __enter__(self) -> "GuardedLedModeTransport":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _write_reports_tsv(
    output: Path, reports: tuple[tuple[str, bytes], ...]
) -> None:
    with (output / "reports_sent.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(["sequence", "operation", "report_id", "payload"])
        for index, (name, report) in enumerate(reports, 1):
            writer.writerow([index, name, report[0], report.hex(" ")])


def execute_led_mode_change(
    device: HidrawDevice,
    *,
    expected: LedModeState,
    target: LedModeState,
    output_root: Path = DEFAULT_RESULT_ROOT,
    state_path: Path = LED_MODE_STATE_PATH,
    transport_factory: Callable[[HidrawDevice], GuardedLedModeTransport] = (
        GuardedLedModeTransport
    ),
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Path:
    expected = _coerce_state(expected)
    target = _coerce_state(target)
    state = load_led_mode_state(state_path)
    if any(
        item["power_cycle_persistence"] == "pending"
        for item in state["history"]
    ):
        raise GuardedLedModeError(
            "a prior LED-mode transition is pending persistence confirmation"
        )
    known = bytes.fromhex(state["current_prefix"])
    if known != expected.value:
        raise GuardedLedModeError(
            f"known LED-mode state expects {known.hex(' ')}, not requested "
            f"source {expected.value.hex(' ')}"
        )
    output: Path | None = None
    plan: LedModePlan | None = None
    operation_document: dict[str, object] | None = None
    transport_succeeded = False
    transport = transport_factory(device)
    try:
        def before_write(candidate: LedModePlan) -> None:
            nonlocal output, plan, operation_document
            plan = candidate
            now = clock()
            output = create_timestamped_directory(
                output_root, "led_mode_0449", now
            )
            timestamp = now.astimezone(timezone.utc).isoformat()
            operation_document = {
                "status": "prewrite-safety-record",
                "timestamp": timestamp,
                "device": candidate.device.to_dict(),
                "address_start": PREFIX_ADDRESS,
                "address_start_hex": "0x0449",
                "address_end": PREFIX_ADDRESS + PREFIX_LENGTH - 1,
                "address_end_hex": "0x044E",
                "length": PREFIX_LENGTH,
                "previous_prefix": candidate.original_prefix.hex(" "),
                "target_prefix": candidate.target.value.hex(" "),
                "previous_semantic_mode": candidate.expected.label,
                "target_semantic_mode": candidate.target.label,
                "reports_intended": [
                    {"operation": "prewrite-f2",
                     "payload": candidate.prewrite_request.hex(" ")},
                    {"operation": "led-mode-f3",
                     "payload": candidate.mode_report.hex(" ")},
                    {"operation": "lighting-f1",
                     "payload": candidate.finalisation_report.hex(" ")},
                    {"operation": "postwrite-f2",
                     "payload": candidate.postwrite_request.hex(" ")},
                ],
                "inter_command_delay_ms": 10,
                "brightness_address_excluded": "0x044F",
                "automatic_retry": False,
                "automatic_restoration": False,
                "power_cycle_persistence": "not-established",
            }
            (output / "operation.json").write_text(
                json.dumps(operation_document, indent=2) + "\n",
                encoding="utf-8",
            )
            (output / "prewrite_read.json").write_text(
                json.dumps({
                    "timestamp": timestamp,
                    "address_start_hex": "0x0449",
                    "length": 6,
                    "prefix": candidate.original_prefix.hex(" "),
                    "semantic_mode": candidate.expected.label,
                    "request": candidate.prewrite_request.hex(" "),
                    "matched_expected_source": True,
                }, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                f"verified current prefix: "
                f"{candidate.original_prefix.hex(' ')}; target: "
                f"{candidate.target.value.hex(' ')}"
            )

        result = transport.change_mode(
            expected, target, before_write=before_write
        )
        transport_succeeded = True
        if output is None or plan is None or operation_document is None:
            raise GuardedLedModeError("prewrite safety record was not created")
        completed = clock().astimezone(timezone.utc).isoformat()
        (output / "postwrite_read.json").write_text(
            json.dumps({
                "timestamp": completed,
                "address_start_hex": "0x0449",
                "length": 6,
                "prefix": result.read_back.hex(" "),
                "semantic_mode": target.label,
                "request": plan.postwrite_request.hex(" "),
                "matched_target": True,
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_reports_tsv(output, result.reports_sent)
        operation = dict(operation_document)
        operation.update({
            "status": "transport-success",
            "completion_timestamp": completed,
            "read_back_prefix": result.read_back.hex(" "),
            "immediate_read_back_confirmed": True,
            "power_cycle_persistence": "pending",
        })
        (output / "operation.json").write_text(
            json.dumps(operation, indent=2) + "\n", encoding="utf-8"
        )
        (output / "result.md").write_text(
            "# Guarded Venus LED-mode result\n\n"
            "- Transport status: success\n"
            "- Address range: `0x0449–0x044E`\n"
            f"- Source: `{expected.value.hex(' ')}` ({expected.label})\n"
            f"- Target/read-back: `{target.value.hex(' ')}` ({target.label})\n"
            "- Immediate read-back: confirmed\n"
            "- Persistent across power cycle: pending\n"
            "- F5 sent: no\n"
            "- Automatic retry: no\n"
            "- Automatic restoration: no\n",
            encoding="utf-8",
        )
        saved = record_led_mode_transition(
            state_path=state_path,
            previous_prefix=expected.value,
            new_prefix=target.value,
            operation_timestamp=operation_document["timestamp"],
            result_directory=output,
            updated_at=completed,
        )
        operation["status"] = "success"
        operation["known_led_mode_state_updated"] = True
        operation["known_state_transition_id"] = saved["history"][-1][
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
                operation.update({
                    "status": (
                        "transport-success-state-update-failed"
                        if transport_succeeded else "failed-closed"
                    ),
                    "immediate_read_back_confirmed": transport_succeeded,
                    "power_cycle_persistence": (
                        "pending" if transport_succeeded
                        else "not-established"
                    ),
                    "error": f"{type(exc).__name__}: {exc}",
                })
                (output / "operation.json").write_text(
                    json.dumps(operation, indent=2) + "\n", encoding="utf-8"
                )
            (output / "result.md").write_text(
                "# Guarded Venus LED-mode result\n\n"
                "- Transport status: failed closed\n"
                f"- Error: `{type(exc).__name__}: {exc}`\n"
                "- Automatic retry: no\n"
                "- Automatic restoration: no\n",
                encoding="utf-8",
            )
        raise
    finally:
        transport.close()


def dry_run_lines(
    expected: LedModeState = LedModeState.FULL_BRIGHTNESS,
    target: LedModeState = LedModeState.BREATHING,
) -> list[str]:
    expected = _coerce_state(expected)
    target = _coerce_state(target)
    if expected is target:
        raise GuardedLedModeError("dry-run direction must change LED mode")
    request = build_f2_request(PREFIX_ADDRESS, PREFIX_LENGTH)
    return [
        "DRY RUN ONLY — no discovery and no hidraw open",
        f"source: {expected.label}: {expected.value.hex(' ')}",
        f"target: {target.label}: {target.value.hex(' ')}",
        f"1 pre-read F2: {request.hex(' ')}",
        f"2 write F3:    {build_led_mode_report(target).hex(' ')}",
        "3 wait:        10 ms",
        f"4 lighting F1: {LIGHTING_FINALISATION_REPORT.hex(' ')}",
        "5 wait:        10 ms",
        f"6 read-back F2:{request.hex(' ')}",
        "F5 is absent; 0x044F is excluded; no retry or restoration",
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
        raise GuardedLedModeError("forbidden public sender API found")
    options = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    if any(item.startswith("--yes") or item == "--noninteractive"
           for item in options):
        raise GuardedLedModeError("noninteractive bypass found")
    if "0x044F" not in source or "brightness_address_excluded" not in source:
        raise GuardedLedModeError("brightness exclusion is not explicit")
    if "read_current_prefix()" not in source:
        raise GuardedLedModeError("mandatory pre-read/read-back is absent")
    return [
        "only exact captured 0x0449-0x044E prefixes are constructible",
        "only exact Report-2 F3 and lighting F1 are accepted",
        "F5 and Report-3/4/5 write paths are absent",
        "brightness address 0x044F is never included",
        "no generic report or memory writer is public",
        "exact source match and exact six-byte read-back are mandatory",
        "no noninteractive bypass, retry, restoration, or chained toggle",
        "default, dry-run, and state commands perform no live access",
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.guarded_led_mode"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--safety-audit", action="store_true")
    parser.add_argument("--live-led-mode-write", action="store_true")
    directions = parser.add_mutually_exclusive_group()
    directions.add_argument("--full-to-breathing", action="store_true")
    directions.add_argument("--breathing-to-full", action="store_true")
    directions.add_argument("--off-to-full", action="store_true")
    directions.add_argument("--rainbow-to-full", action="store_true")
    parser.add_argument("--acknowledge-device-write", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--state-file", type=Path, default=LED_MODE_STATE_PATH)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    executor: Callable[..., Path] = execute_led_mode_change,
    input_fn: Callable[[str], str] = input,
    permission_check: Callable[[Path], bool] = (
        lambda path: os.access(path, os.R_OK | os.W_OK)
    ),
) -> int:
    args = _parser().parse_args(argv)
    if args.rainbow_to_full:
        expected, target = LedModeState.RAINBOW, LedModeState.CURRENT_FULL_BRIGHTNESS
    elif args.off_to_full:
        expected, target = LedModeState.TRUE_OFF, LedModeState.CURRENT_FULL_BRIGHTNESS
    elif args.breathing_to_full:
        expected, target = LedModeState.BREATHING, LedModeState.FULL_BRIGHTNESS
    else:
        expected, target = LedModeState.FULL_BRIGHTNESS, LedModeState.BREATHING
    if args.safety_audit:
        for line in safety_audit():
            print(f"PASS: {line}")
        if not args.live_led_mode_write and not args.dry_run:
            return 0
    if args.dry_run:
        print("\n".join(dry_run_lines(expected, target)))
        if not args.live_led_mode_write:
            return 0
    if not args.live_led_mode_write:
        print("offline mode; no hidraw discovery or open occurred")
        print("use --dry-run to print the exact guarded sequence")
        return 0
    if (
        not (args.full_to_breathing or args.breathing_to_full or args.off_to_full or args.rainbow_to_full)
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
            f"read/write access unavailable for {device.path}"
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
    required = (
        "WRITE 0449 RAINBOW TO FULL" if args.rainbow_to_full else
        "WRITE 0449 OFF TO FULL" if args.off_to_full else
        "WRITE 0449 BREATHING TO FULL" if args.breathing_to_full else
        "WRITE 0449 FULL TO BREATHING"
    )
    print(f"current prefix must be: {expected.value.hex(' ')}")
    print(f"target prefix will be:  {target.value.hex(' ')}")
    confirmation = input_fn(f"Type exactly {required}: ")
    if confirmation != required:
        print("confirmation mismatch; no hidraw node was opened", file=sys.stderr)
        return 2
    output = executor(
        device,
        expected=expected,
        target=target,
        output_root=args.output_root,
        state_path=args.state_file,
    )
    print(f"verified LED-mode change completed: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
