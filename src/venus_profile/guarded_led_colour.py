"""Exact-prefix guarded official-blue/official-red LED-colour experiment."""

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
    DeviceSession, DeviceSessionError, _LED_COLOUR_CAPABILITY,
    create_timestamped_directory, ownership_warnings,
)
from .known_led_colour_state import (
    LED_COLOUR_STATE_PATH, OFFICIAL_BLUE_PREFIX, OFFICIAL_GREEN_PREFIX,
    OFFICIAL_RED_PREFIX,
    PREFIX_TO_NAME, record_colour_transition, load_colour_state,
)
from .readonly_hidraw import (
    HidrawDevice, HidrawPermissionError, ReadOnlyHidrawTransport,
    REPORT2_LENGTH, _HidrawFeatureIo, _hid_iocsfeature,
    build_f2_request, discover_venus_hidraw,
)


PREFIX_ADDRESS = 0x0449
PREFIX_LENGTH = 6
LIGHTING_SUFFIX = bytes.fromhex("01 00 01")
INTER_COMMAND_DELAY_SECONDS = 0.010
LIGHTING_FINALISATION_REPORT = bytes.fromhex(
    "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESULT_ROOT = PROJECT_ROOT / "usb-captures" / "live-led-colour-writes"


class LedColour(Enum):
    OFFICIAL_RED = OFFICIAL_RED_PREFIX
    OFFICIAL_BLUE = OFFICIAL_BLUE_PREFIX
    OFFICIAL_GREEN = OFFICIAL_GREEN_PREFIX

    @property
    def label(self) -> str:
        return PREFIX_TO_NAME[self.value]

    @property
    def rgb(self) -> tuple[int, int, int]:
        return tuple(self.value[:3])


class GuardedLedColourError(RuntimeError):
    """An exact colour-prefix precondition, operation, or read-back failed."""


@dataclass(frozen=True)
class ColourPlan:
    device: HidrawDevice
    expected: LedColour
    target: LedColour
    original_prefix: bytes
    prewrite_request: bytes
    colour_report: bytes
    finalisation_report: bytes
    postwrite_request: bytes


@dataclass(frozen=True)
class ColourResult:
    plan: ColourPlan
    read_back: bytes
    reports_sent: tuple[tuple[str, bytes], ...]


def _coerce_colour(value: LedColour) -> LedColour:
    if not isinstance(value, LedColour):
        raise GuardedLedColourError(
            "colour must be OFFICIAL_RED, OFFICIAL_BLUE, or OFFICIAL_GREEN"
        )
    return value


def build_led_colour_report(colour: LedColour) -> bytes:
    selected = _coerce_colour(colour)
    if selected.value[3:] != LIGHTING_SUFFIX:
        raise GuardedLedColourError("lighting suffix must remain 01 00 01")
    report = (
        bytes.fromhex("02 f3 49 04 06 00 00 00")
        + selected.value + b"\x00\x00"
    )
    validate_guarded_colour_report(report)
    return report


def validate_guarded_colour_report(report: bytes) -> str:
    if len(report) != REPORT2_LENGTH:
        raise GuardedLedColourError("guarded outgoing report must be 16 bytes")
    if report == LIGHTING_FINALISATION_REPORT:
        return "lighting-finalisation"
    if (
        report[:8] == bytes.fromhex("02 f3 49 04 06 00 00 00")
        and report[8:14] in {
            OFFICIAL_RED_PREFIX, OFFICIAL_BLUE_PREFIX, OFFICIAL_GREEN_PREFIX
        }
        and report[11:14] == LIGHTING_SUFFIX
        and report[14:] == b"\x00\x00"
    ):
        return "profile-1-official-colour-prefix"
    raise GuardedLedColourError(
        "outgoing report is not an exact guarded colour/F1 template"
    )


class _ColourIo(Protocol):
    def send_f2(self, request: bytes) -> None: ...
    def get_feature(self, report_id: int, length: int) -> bytes: ...
    def send_colour(self, colour: LedColour) -> bytes: ...
    def finalise_lighting(self) -> bytes: ...
    def close(self) -> None: ...


class _GuardedColourIo(_HidrawFeatureIo):
    def _submit_exact(self, report: bytes) -> None:
        validate_guarded_colour_report(report)
        buffer = bytearray(report)
        result = self._ioctl(
            self._fd, _hid_iocsfeature(REPORT2_LENGTH), buffer, True
        )
        if result != REPORT2_LENGTH:
            raise GuardedLedColourError(
                f"guarded SET_FEATURE returned {result}, expected 16"
            )

    def send_colour(self, colour: LedColour) -> bytes:
        report = build_led_colour_report(colour)
        self._submit_exact(report)
        return report

    def finalise_lighting(self) -> bytes:
        self._submit_exact(LIGHTING_FINALISATION_REPORT)
        return LIGHTING_FINALISATION_REPORT


class GuardedLedColourTransport:
    """One exact official-colour transition with mandatory F2 checks."""

    def __init__(
        self, device: HidrawDevice | DeviceSession, *,
        backend_factory: Callable[[Path], _ColourIo] = _GuardedColourIo,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._backend_factory = backend_factory
        self._backend: _ColourIo | None = None
        try:
            self._session = (
                device if isinstance(device, DeviceSession)
                else DeviceSession(device)
            )
        except DeviceSessionError as exc:
            raise GuardedLedColourError(str(exc)) from exc
        self.device = self._session.device
        self._sleeper = sleeper
        self._reports_sent: list[tuple[str, bytes]] = []

    @property
    def reports_sent(self) -> tuple[tuple[str, bytes], ...]:
        return tuple(self._reports_sent)

    def read_current_prefix(self) -> bytes:
        value = self._session.read_memory(PREFIX_ADDRESS, PREFIX_LENGTH)
        if value not in PREFIX_TO_NAME:
            raise GuardedLedColourError(
                f"unexpected lighting prefix {value.hex(' ')}; refusing write"
            )
        return value

    def change_colour(
        self, expected: LedColour, target: LedColour, *,
        before_write: Callable[[ColourPlan], None],
    ) -> ColourResult:
        expected, target = _coerce_colour(expected), _coerce_colour(target)
        if expected is target:
            raise GuardedLedColourError("source and target colours are equal")
        if (expected, target) not in {
            (LedColour.OFFICIAL_BLUE, LedColour.OFFICIAL_RED),
            (LedColour.OFFICIAL_RED, LedColour.OFFICIAL_BLUE),
            (LedColour.OFFICIAL_GREEN, LedColour.OFFICIAL_BLUE),
            (LedColour.OFFICIAL_BLUE, LedColour.OFFICIAL_GREEN),
        }:
            raise GuardedLedColourError(
                "requested colour direction is not live-authorised"
            )
        request = build_f2_request(PREFIX_ADDRESS, PREFIX_LENGTH)
        self._reports_sent.append(("prewrite-f2", request))
        current = self.read_current_prefix()
        if current != expected.value:
            if current == target.value:
                raise GuardedLedColourError(
                    f"colour is already {target.label}; refusing write"
                )
            raise GuardedLedColourError(
                f"expected {expected.value.hex(' ')}, found "
                f"{current.hex(' ')}; refusing write"
            )
        plan = ColourPlan(
            self.device, expected, target, current, request,
            build_led_colour_report(target), LIGHTING_FINALISATION_REPORT,
            request,
        )
        before_write(plan)
        report = self._writer().send_colour(target)
        self._reports_sent.append(("led-colour-f3", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)
        report = self._writer().finalise_lighting()
        self._reports_sent.append(("lighting-f1", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)
        self._reports_sent.append(("postwrite-f2", request))
        read_back = self.read_current_prefix()
        if read_back != target.value:
            raise GuardedLedColourError(
                f"read-back mismatch: expected {target.value.hex(' ')}, got "
                f"{read_back.hex(' ')}; no retry or restoration attempted"
            )
        return ColourResult(plan, read_back, self.reports_sent)

    def blue_to_red(
        self, *, before_write: Callable[[ColourPlan], None]
    ) -> ColourResult:
        return self.change_colour(
            LedColour.OFFICIAL_BLUE, LedColour.OFFICIAL_RED,
            before_write=before_write,
        )

    def red_to_blue(
        self, *, before_write: Callable[[ColourPlan], None]
    ) -> ColourResult:
        return self.change_colour(
            LedColour.OFFICIAL_RED, LedColour.OFFICIAL_BLUE,
            before_write=before_write,
        )

    def green_to_blue(
        self, *, before_write: Callable[[ColourPlan], None]
    ) -> ColourResult:
        return self.change_colour(
            LedColour.OFFICIAL_GREEN, LedColour.OFFICIAL_BLUE,
            before_write=before_write,
        )

    def blue_to_green(
        self, *, before_write: Callable[[ColourPlan], None]
    ) -> ColourResult:
        return self.change_colour(
            LedColour.OFFICIAL_BLUE, LedColour.OFFICIAL_GREEN,
            before_write=before_write,
        )

    def _writer(self) -> _ColourIo:
        if self._backend is None:
            self._session._request_internal_capability(_LED_COLOUR_CAPABILITY)
            self._backend = self._backend_factory(self.device.path)
        return self._backend

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._session.close()

    def __enter__(self) -> "GuardedLedColourTransport":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _write_reports(output: Path, reports: tuple[tuple[str, bytes], ...]) -> None:
    with (output / "reports_sent.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(["sequence", "operation", "report_id", "payload"])
        for index, (name, report) in enumerate(reports, 1):
            writer.writerow([index, name, report[0], report.hex(" ")])


def execute_colour_change(
    device: HidrawDevice, *, expected: LedColour, target: LedColour,
    output_root: Path = DEFAULT_RESULT_ROOT,
    state_path: Path = LED_COLOUR_STATE_PATH,
    transport_factory: Callable[[HidrawDevice], GuardedLedColourTransport] = (
        GuardedLedColourTransport
    ),
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Path:
    expected, target = _coerce_colour(expected), _coerce_colour(target)
    state = load_colour_state(state_path)
    if any(
        item["power_cycle_persistence"] == "pending"
        for item in state["history"]
    ):
        raise GuardedLedColourError(
            "a prior colour transition is pending persistence confirmation"
        )
    if bytes.fromhex(state["current_prefix"]) != expected.value:
        raise GuardedLedColourError(
            "known colour state does not match requested source"
        )
    output: Path | None = None
    plan: ColourPlan | None = None
    operation: dict[str, object] | None = None
    transport_succeeded = False
    transport = transport_factory(device)
    try:
        def before_write(candidate: ColourPlan) -> None:
            nonlocal output, plan, operation
            plan = candidate
            now = clock()
            output = create_timestamped_directory(
                output_root, "led_colour_0449", now
            )
            timestamp = now.astimezone(timezone.utc).isoformat()
            operation = {
                "status": "prewrite-safety-record",
                "timestamp": timestamp,
                "device": candidate.device.to_dict(),
                "colour_address_range": "0x0449-0x044B",
                "prefix_address_range": "0x0449-0x044E",
                "previous_prefix": candidate.original_prefix.hex(" "),
                "target_prefix": candidate.target.value.hex(" "),
                "previous_semantic_colour": candidate.expected.label,
                "target_semantic_colour": candidate.target.label,
                "previous_rgb": list(candidate.expected.rgb),
                "target_rgb": list(candidate.target.rgb),
                "lighting_suffix_preserved": "01 00 01",
                "brightness_address_excluded": "0x044F",
                "reports_intended": [
                    {"operation": "prewrite-f2",
                     "payload": candidate.prewrite_request.hex(" ")},
                    {"operation": "led-colour-f3",
                     "payload": candidate.colour_report.hex(" ")},
                    {"operation": "lighting-f1",
                     "payload": candidate.finalisation_report.hex(" ")},
                    {"operation": "postwrite-f2",
                     "payload": candidate.postwrite_request.hex(" ")},
                ],
                "inter_command_delay_ms": 10,
                "automatic_retry": False,
                "automatic_restoration": False,
                "power_cycle_persistence": "not-established",
            }
            (output / "operation.json").write_text(
                json.dumps(operation, indent=2) + "\n", encoding="utf-8"
            )
            (output / "prewrite_read.json").write_text(
                json.dumps({
                    "timestamp": timestamp, "address": "0x0449",
                    "length": 6, "prefix": candidate.original_prefix.hex(" "),
                    "semantic_colour": candidate.expected.label,
                    "rgb": list(candidate.expected.rgb),
                    "request": candidate.prewrite_request.hex(" "),
                    "matched_expected_source": True,
                }, indent=2) + "\n", encoding="utf-8"
            )
            print(
                f"verified source: {candidate.original_prefix.hex(' ')}; "
                f"target: {candidate.target.value.hex(' ')}"
            )

        result = transport.change_colour(
            expected, target, before_write=before_write
        )
        transport_succeeded = True
        if output is None or plan is None or operation is None:
            raise GuardedLedColourError("prewrite safety record was not created")
        completed = clock().astimezone(timezone.utc).isoformat()
        (output / "postwrite_read.json").write_text(
            json.dumps({
                "timestamp": completed, "address": "0x0449", "length": 6,
                "prefix": result.read_back.hex(" "),
                "semantic_colour": target.label, "rgb": list(target.rgb),
                "request": plan.postwrite_request.hex(" "),
                "matched_target": True,
            }, indent=2) + "\n", encoding="utf-8"
        )
        _write_reports(output, result.reports_sent)
        final = dict(operation)
        final.update({
            "status": "transport-success",
            "completion_timestamp": completed,
            "read_back_prefix": result.read_back.hex(" "),
            "immediate_read_back_confirmed": True,
            "power_cycle_persistence": "pending",
        })
        (output / "operation.json").write_text(
            json.dumps(final, indent=2) + "\n", encoding="utf-8"
        )
        (output / "result.md").write_text(
            "# Guarded Venus LED-colour result\n\n"
            "- Transport status: success\n"
            f"- Source: `{expected.value.hex(' ')}` ({expected.label})\n"
            f"- Target/read-back: `{target.value.hex(' ')}` ({target.label})\n"
            "- Immediate read-back: confirmed\n"
            "- Persistent across power cycle: pending\n"
            "- F5 sent: no\n"
            "- Automatic retry/restoration: no\n",
            encoding="utf-8",
        )
        saved = record_colour_transition(
            state_path=state_path, previous_prefix=expected.value,
            new_prefix=target.value, operation_timestamp=operation["timestamp"],
            result_directory=output, updated_at=completed,
        )
        final["status"] = "success"
        final["known_colour_state_updated"] = True
        final["known_state_transition_id"] = saved["history"][-1][
            "transition_id"
        ]
        (output / "operation.json").write_text(
            json.dumps(final, indent=2) + "\n", encoding="utf-8"
        )
        return output
    except Exception as exc:
        if output is not None:
            _write_reports(output, transport.reports_sent)
            if operation is not None:
                failed = dict(operation)
                failed.update({
                    "status": (
                        "transport-success-state-update-failed"
                        if transport_succeeded else "failed-closed"
                    ),
                    "immediate_read_back_confirmed": transport_succeeded,
                    "power_cycle_persistence": (
                        "pending" if transport_succeeded else "not-established"
                    ),
                    "error": f"{type(exc).__name__}: {exc}",
                })
                (output / "operation.json").write_text(
                    json.dumps(failed, indent=2) + "\n", encoding="utf-8"
                )
            (output / "result.md").write_text(
                "# Guarded Venus LED-colour result\n\n"
                "- Transport status: failed closed\n"
                f"- Error: `{type(exc).__name__}: {exc}`\n"
                "- Automatic retry/restoration: no\n",
                encoding="utf-8",
            )
        raise
    finally:
        transport.close()


def dry_run_lines(
    expected: LedColour = LedColour.OFFICIAL_BLUE,
    target: LedColour = LedColour.OFFICIAL_RED,
) -> list[str]:
    expected, target = _coerce_colour(expected), _coerce_colour(target)
    request = build_f2_request(PREFIX_ADDRESS, PREFIX_LENGTH)
    return [
        "DRY RUN ONLY — no discovery and no hidraw open",
        f"source: {expected.label}: {expected.value.hex(' ')}",
        f"target: {target.label}: {target.value.hex(' ')}",
        f"1 pre-read F2: {request.hex(' ')}",
        f"2 write F3:    {build_led_colour_report(target).hex(' ')}",
        "3 wait:        10 ms",
        f"4 lighting F1: {LIGHTING_FINALISATION_REPORT.hex(' ')}",
        "5 wait:        10 ms",
        f"6 read-back F2:{request.hex(' ')}",
        "F5 absent; suffix 01 00 01 fixed; 0x044F excluded; no retry/restore",
    ]


def safety_audit() -> list[str]:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    public = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    if public & {
        "write_memory", "set_feature_report", "send_report",
        "send_f3", "send_f1", "send_f5",
    }:
        raise GuardedLedColourError("forbidden public sender found")
    options = {
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument" and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    if any(item.startswith("--yes") or item == "--noninteractive"
           for item in options):
        raise GuardedLedColourError("noninteractive bypass found")
    if "read_current_prefix()" not in source:
        raise GuardedLedColourError("mandatory pre-read/read-back absent")
    return [
        "only Red↔Blue and persistent Green↔Blue directions are authorised",
        "only address range 0x0449-0x044E can be submitted",
        "suffix 01 00 01 is fixed and brightness 0x044F is excluded",
        "only exact Report-2 F3 and lighting F1 are accepted",
        "no F5 or Report-3/4/5 write path exists",
        "transient 02 B5 34 and arbitrary RGB are absent and rejected",
        "source match and exact six-byte read-back are mandatory",
        "no bypass, retry, restoration, chained toggle, or live default",
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.guarded_led_colour"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--safety-audit", action="store_true")
    parser.add_argument("--live-led-colour-write", action="store_true")
    directions = parser.add_mutually_exclusive_group()
    directions.add_argument("--blue-to-red", action="store_true")
    directions.add_argument("--red-to-blue", action="store_true")
    directions.add_argument("--green-to-blue", action="store_true")
    directions.add_argument("--blue-to-green", action="store_true")
    parser.add_argument("--acknowledge-device-write", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--state-file", type=Path, default=LED_COLOUR_STATE_PATH)
    return parser


def main(
    argv: list[str] | None = None, *,
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    executor: Callable[..., Path] = execute_colour_change,
    input_fn: Callable[[str], str] = input,
    permission_check: Callable[[Path], bool] = (
        lambda path: os.access(path, os.R_OK | os.W_OK)
    ),
) -> int:
    args = _parser().parse_args(argv)
    if args.blue_to_green:
        expected, target = (
            LedColour.OFFICIAL_BLUE, LedColour.OFFICIAL_GREEN
        )
    elif args.green_to_blue:
        expected, target = (
            LedColour.OFFICIAL_GREEN, LedColour.OFFICIAL_BLUE
        )
    elif args.red_to_blue:
        expected, target = LedColour.OFFICIAL_RED, LedColour.OFFICIAL_BLUE
    else:
        expected, target = LedColour.OFFICIAL_BLUE, LedColour.OFFICIAL_RED
    if args.safety_audit:
        for line in safety_audit():
            print(f"PASS: {line}")
        if not args.live_led_colour_write and not args.dry_run:
            return 0
    if args.dry_run:
        print("\n".join(dry_run_lines(expected, target)))
        if not args.live_led_colour_write:
            return 0
    if not args.live_led_colour_write:
        print("offline mode; no hidraw discovery or open occurred")
        return 0
    if (
        not (
            args.blue_to_red or args.red_to_blue or args.green_to_blue
            or args.blue_to_green
        )
        or not args.acknowledge_device_write
    ):
        print(
            "error: live operation requires exactly one direction and "
            "--acknowledge-device-write", file=sys.stderr,
        )
        return 2
    device = discovery()
    if not permission_check(device.path):
        raise HidrawPermissionError(
            f"read/write access unavailable for {device.path}"
        )
    print(f"selected hidraw node: {device.path}")
    for warning in ownership_warnings(
        [args.state_file, args.output_root],
        sudo_user=os.environ.get("SUDO_USER"),
    ):
        print(f"WARNING: {warning}", file=sys.stderr)
    required = (
        "WRITE 0449 BLUE TO GREEN"
        if args.blue_to_green
        else (
            "WRITE 0449 GREEN TO BLUE"
            if args.green_to_blue
            else (
                "WRITE 0449 RED TO BLUE"
                if args.red_to_blue else "WRITE 0449 BLUE TO RED"
            )
        )
    )
    print(f"source prefix must be: {expected.value.hex(' ')}")
    print(f"target prefix will be: {target.value.hex(' ')}")
    if input_fn(f"Type exactly {required}: ") != required:
        print("confirmation mismatch; no hidraw node was opened", file=sys.stderr)
        return 2
    output = executor(
        device, expected=expected, target=target,
        output_root=args.output_root, state_path=args.state_file,
    )
    print(f"verified LED-colour change completed: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
