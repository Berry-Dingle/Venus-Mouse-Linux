"""First exact-source guarded arbitrary custom-colour write experiment."""

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

from .device_session import (
    DeviceSession, DeviceSessionError, _CUSTOM_COLOUR_CAPABILITY,
    create_timestamped_directory, ownership_warnings,
)
from .known_custom_colour_state import (
    CUSTOM_COLOUR_STATE_PATH, INITIAL_SEMANTIC_HEX, LIGHTING_SUFFIX,
    load_custom_colour_state, normalise_semantic_hex,
    record_custom_colour_transition, semantic_hex_to_device_rgb,
    semantic_hex_to_prefix, semantic_hex_to_rgb,
)
from .readonly_hidraw import (
    HidrawDevice, HidrawPermissionError, REPORT2_LENGTH,
    ReadOnlyHidrawTransport, _HidrawFeatureIo, _hid_iocsfeature,
    build_f2_request, discover_venus_hidraw,
)


PREFIX_ADDRESS = 0x0449
PREFIX_LENGTH = 6
LIVE_TARGET_HEX = "#FF0000"
INTER_COMMAND_DELAY_SECONDS = 0.010
LIGHTING_FINALISATION_REPORT = bytes.fromhex(
    "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESULT_ROOT = (
    PROJECT_ROOT / "usb-captures" / "live-custom-colour-writes"
)


class GuardedCustomColourError(RuntimeError):
    """A custom-colour validation, precondition, or read-back failed."""


@dataclass(frozen=True)
class CustomColour:
    semantic_hex: str
    rgb: tuple[int, int, int]
    device_rgb: bytes
    prefix: bytes

    @classmethod
    def parse(cls, value: str) -> "CustomColour":
        try:
            semantic_hex = normalise_semantic_hex(value)
        except Exception as exc:
            raise GuardedCustomColourError(
                "custom colour must be exactly #RRGGBB"
            ) from exc
        return cls(
            semantic_hex=semantic_hex,
            rgb=semantic_hex_to_rgb(semantic_hex),
            device_rgb=semantic_hex_to_device_rgb(semantic_hex),
            prefix=semantic_hex_to_prefix(semantic_hex),
        )


@dataclass(frozen=True)
class CustomColourPlan:
    device: HidrawDevice
    source: CustomColour
    target: CustomColour
    prewrite_request: bytes
    custom_report: bytes
    finalisation_report: bytes
    postwrite_request: bytes


@dataclass(frozen=True)
class CustomColourResult:
    plan: CustomColourPlan
    read_back: bytes
    reports_sent: tuple[tuple[str, bytes], ...]


def build_custom_colour_report(value: str) -> bytes:
    colour = CustomColour.parse(value)
    report = (
        bytes.fromhex("02 f3 49 04 06 00 00 00")
        + colour.prefix + b"\x00\x00"
    )
    if len(report) != REPORT2_LENGTH:
        raise GuardedCustomColourError("custom F3 must be exactly 16 bytes")
    return report


def validate_live_custom_report(report: bytes) -> str:
    if len(report) != REPORT2_LENGTH:
        raise GuardedCustomColourError("outgoing report must be 16 bytes")
    if report == LIGHTING_FINALISATION_REPORT:
        return "lighting-finalisation"
    if report == build_custom_colour_report(LIVE_TARGET_HEX):
        if report[11:14] != LIGHTING_SUFFIX:
            raise GuardedCustomColourError("lighting suffix changed")
        return "profile-1-custom-colour-boundary-red"
    raise GuardedCustomColourError(
        "report is not the exact authorised #FF0000 F3 or lighting F1"
    )


class _CustomIo(Protocol):
    def send_custom_target(self) -> bytes: ...
    def finalise_lighting(self) -> bytes: ...
    def close(self) -> None: ...


class _GuardedCustomIo(_HidrawFeatureIo):
    def _submit_exact(self, report: bytes) -> None:
        validate_live_custom_report(report)
        buffer = bytearray(report)
        result = self._ioctl(
            self._fd, _hid_iocsfeature(REPORT2_LENGTH), buffer, True
        )
        if result != REPORT2_LENGTH:
            raise GuardedCustomColourError(
                f"guarded SET_FEATURE returned {result}, expected 16"
            )

    def send_custom_target(self) -> bytes:
        report = build_custom_colour_report(LIVE_TARGET_HEX)
        self._submit_exact(report)
        return report

    def finalise_lighting(self) -> bytes:
        self._submit_exact(LIGHTING_FINALISATION_REPORT)
        return LIGHTING_FINALISATION_REPORT


class GuardedCustomColourTransport:
    """The corrected #0000FF-to-#FF0000 transaction with exact F2 checks."""

    def __init__(
        self, device: HidrawDevice | DeviceSession, *,
        backend_factory: Callable[[Path], _CustomIo] = _GuardedCustomIo,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._backend_factory = backend_factory
        self._backend: _CustomIo | None = None
        try:
            self._session = (
                device if isinstance(device, DeviceSession)
                else DeviceSession(device)
            )
        except DeviceSessionError as exc:
            raise GuardedCustomColourError(str(exc)) from exc
        self.device = self._session.device
        self._sleeper = sleeper
        self._reports_sent: list[tuple[str, bytes]] = []

    @property
    def reports_sent(self) -> tuple[tuple[str, bytes], ...]:
        return tuple(self._reports_sent)

    def _read_prefix(self) -> bytes:
        value = self._session.read_memory(PREFIX_ADDRESS, PREFIX_LENGTH)
        if len(value) != PREFIX_LENGTH:
            raise GuardedCustomColourError("short six-byte lighting prefix")
        return value

    def source_to_boundary_red(
        self, *, before_write: Callable[[CustomColourPlan], None]
    ) -> CustomColourResult:
        source = CustomColour.parse(INITIAL_SEMANTIC_HEX)
        target = CustomColour.parse(LIVE_TARGET_HEX)
        request = build_f2_request(PREFIX_ADDRESS, PREFIX_LENGTH)
        self._reports_sent.append(("prewrite-f2", request))
        current = self._read_prefix()
        if current != source.prefix:
            if current == target.prefix:
                raise GuardedCustomColourError(
                    "custom colour is already visible red #FF0000; refusing write"
                )
            raise GuardedCustomColourError(
                f"expected {source.prefix.hex(' ')}, found "
                f"{current.hex(' ')}; refusing write"
            )
        plan = CustomColourPlan(
            self.device, source, target, request,
            build_custom_colour_report(target.semantic_hex),
            LIGHTING_FINALISATION_REPORT, request,
        )
        before_write(plan)
        report = self._writer().send_custom_target()
        self._reports_sent.append(("custom-colour-f3", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)
        report = self._writer().finalise_lighting()
        self._reports_sent.append(("lighting-f1", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)
        self._reports_sent.append(("postwrite-f2", request))
        read_back = self._read_prefix()
        if read_back != target.prefix:
            raise GuardedCustomColourError(
                f"read-back mismatch: expected {target.prefix.hex(' ')}, got "
                f"{read_back.hex(' ')}; no retry or restoration attempted"
            )
        return CustomColourResult(plan, read_back, self.reports_sent)

    def _writer(self) -> _CustomIo:
        if self._backend is None:
            self._session._request_internal_capability(
                _CUSTOM_COLOUR_CAPABILITY
            )
            self._backend = self._backend_factory(self.device.path)
        return self._backend

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._session.close()

    def __enter__(self) -> "GuardedCustomColourTransport":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _write_reports(
    output: Path, reports: tuple[tuple[str, bytes], ...]
) -> None:
    with (output / "reports_sent.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(["sequence", "operation", "report_id", "payload"])
        for index, (name, report) in enumerate(reports, 1):
            writer.writerow([index, name, report[0], report.hex(" ")])


def execute_custom_colour_change(
    device: HidrawDevice, *, target_hex: str,
    output_root: Path = DEFAULT_RESULT_ROOT,
    state_path: Path = CUSTOM_COLOUR_STATE_PATH,
    transport_factory: Callable[
        [HidrawDevice], GuardedCustomColourTransport
    ] = GuardedCustomColourTransport,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Path:
    target = CustomColour.parse(target_hex)
    if target.semantic_hex != LIVE_TARGET_HEX:
        raise GuardedCustomColourError(
            "live custom-colour milestone permits only #FF0000"
        )
    state = load_custom_colour_state(state_path)
    if any(
        item["power_cycle_persistence"] == "pending"
        for item in state["history"]
    ):
        raise GuardedCustomColourError(
            "a prior custom-colour transition is pending persistence"
        )
    if state["current_semantic_hex"] != INITIAL_SEMANTIC_HEX:
        raise GuardedCustomColourError(
            "known custom-colour state does not match visible #0000FF source"
        )
    output: Path | None = None
    operation: dict[str, object] | None = None
    transport = transport_factory(device)
    try:
        def before_write(plan: CustomColourPlan) -> None:
            nonlocal output, operation
            now = clock()
            output = create_timestamped_directory(
                output_root, "custom_colour_0449", now
            )
            timestamp = now.astimezone(timezone.utc).isoformat()
            operation = {
                "status": "prewrite-safety-record",
                "timestamp": timestamp,
                "device": plan.device.to_dict(),
                "address_range": "0x0449-0x044E",
                "brightness_address_excluded": "0x044F",
                "conversion_rule": (
                    "native semantic #RRGGBB -> device RR GG BB"
                ),
                "source_semantic_hex": plan.source.semantic_hex,
                "source_semantic_rgb": list(plan.source.rgb),
                "source_device_rgb": plan.source.device_rgb.hex(" "),
                "source_prefix": plan.source.prefix.hex(" "),
                "target_semantic_hex": plan.target.semantic_hex,
                "target_semantic_rgb": list(plan.target.rgb),
                "target_device_rgb": plan.target.device_rgb.hex(" "),
                "target_prefix": plan.target.prefix.hex(" "),
                "reports_intended": [
                    {"operation": "prewrite-f2",
                     "payload": plan.prewrite_request.hex(" ")},
                    {"operation": "custom-colour-f3",
                     "payload": plan.custom_report.hex(" ")},
                    {"operation": "lighting-f1",
                     "payload": plan.finalisation_report.hex(" ")},
                    {"operation": "postwrite-f2",
                     "payload": plan.postwrite_request.hex(" ")},
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
                    "timestamp": timestamp, "address": "0x0449", "length": 6,
                    "semantic_hex": plan.source.semantic_hex,
                    "semantic_rgb": list(plan.source.rgb),
                    "device_rgb": plan.source.device_rgb.hex(" "),
                    "prefix": plan.source.prefix.hex(" "),
                    "matched_expected_source": True,
                }, indent=2) + "\n", encoding="utf-8"
            )

        result = transport.source_to_boundary_red(before_write=before_write)
        if output is None or operation is None:
            raise GuardedCustomColourError("safety record was not created")
        completed = clock().astimezone(timezone.utc).isoformat()
        (output / "postwrite_read.json").write_text(
            json.dumps({
                "timestamp": completed, "address": "0x0449", "length": 6,
                "semantic_hex": target.semantic_hex,
                "semantic_rgb": list(target.rgb),
                "device_rgb": target.device_rgb.hex(" "),
                "prefix": result.read_back.hex(" "),
                "matched_target": True,
            }, indent=2) + "\n", encoding="utf-8"
        )
        _write_reports(output, result.reports_sent)
        final = dict(operation)
        final.update({
            "status": "success", "completion_timestamp": completed,
            "read_back_prefix": result.read_back.hex(" "),
            "immediate_read_back_confirmed": True,
            "power_cycle_persistence": "pending",
        })
        saved = record_custom_colour_transition(
            state_path=state_path, previous_hex=INITIAL_SEMANTIC_HEX,
            new_hex=target.semantic_hex,
            operation_timestamp=str(operation["timestamp"]),
            result_directory=output, updated_at=completed,
        )
        final["known_state_transition_id"] = saved["history"][-1][
            "transition_id"
        ]
        (output / "operation.json").write_text(
            json.dumps(final, indent=2) + "\n", encoding="utf-8"
        )
        (output / "result.md").write_text(
            "# Guarded custom LED-colour result\n\n"
            "- Transport and immediate read-back: success\n"
            f"- Semantic transition: `{INITIAL_SEMANTIC_HEX}` → "
            f"`{target.semantic_hex}`\n"
            f"- Device RGB/read-back: `{target.device_rgb.hex(' ')}`\n"
            "- Persistent across power cycle: pending\n"
            "- F5/retry/restoration: none\n",
            encoding="utf-8",
        )
        return output
    except Exception as exc:
        if output is not None:
            _write_reports(output, transport.reports_sent)
            if operation is not None:
                failed = dict(operation)
                failed.update({
                    "status": "failed-closed",
                    "immediate_read_back_confirmed": False,
                    "power_cycle_persistence": "not-established",
                    "error": f"{type(exc).__name__}: {exc}",
                })
                (output / "operation.json").write_text(
                    json.dumps(failed, indent=2) + "\n", encoding="utf-8"
                )
            (output / "result.md").write_text(
                "# Guarded custom LED-colour result\n\n"
                f"- Failed closed: `{type(exc).__name__}: {exc}`\n"
                "- Automatic retry/restoration: no\n",
                encoding="utf-8",
            )
        raise
    finally:
        transport.close()


def dry_run_lines(value: str) -> list[str]:
    target = CustomColour.parse(value)
    source = CustomColour.parse(INITIAL_SEMANTIC_HEX)
    request = build_f2_request(PREFIX_ADDRESS, PREFIX_LENGTH)
    live_status = (
        "live-authorised first target"
        if target.semantic_hex == LIVE_TARGET_HEX
        else "offline parse only; live mode rejects this target"
    )
    return [
        "DRY RUN ONLY — no discovery and no hidraw open",
        f"source: {source.semantic_hex}: {source.prefix.hex(' ')}",
        f"target: {target.semantic_hex}: RGB {target.rgb}; "
        f"device RGB {target.device_rgb.hex(' ')}",
        f"target status: {live_status}",
        f"1 pre-read F2: {request.hex(' ')}",
        f"2 write F3:    {build_custom_colour_report(target.semantic_hex).hex(' ')}",
        "3 wait:        10 ms",
        f"4 lighting F1: {LIGHTING_FINALISATION_REPORT.hex(' ')}",
        "5 wait:        10 ms",
        f"6 read-back F2:{request.hex(' ')}",
        "F5 absent; suffix fixed; 0x044F excluded; no retry/restore",
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
        raise GuardedCustomColourError("forbidden public sender found")
    options = {
        node.args[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument" and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    if any(
        option.startswith("--yes") or option == "--noninteractive"
        for option in options
    ):
        raise GuardedCustomColourError("noninteractive bypass found")
    return [
        "only strict #RRGGBB semantic input is accepted",
        "live target is restricted to exact #FF0000",
        "only 0x0449-0x044E is written; suffix fixed; 0x044F excluded",
        "only exact Report-2 #FF0000 F3 and lighting F1 can be submitted",
        "no F5 or Report-3/4/5 write path exists",
        "source pre-read and exact six-byte read-back are mandatory",
        "no generic sender/address/payload/raw-RGB API exists",
        "no bypass, retry, restoration, loop, animation, or live default",
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.guarded_custom_colour"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--safety-audit", action="store_true")
    parser.add_argument("--live-custom-colour-write", action="store_true")
    parser.add_argument("--hex", dest="semantic_hex")
    parser.add_argument("--acknowledge-device-write", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument(
        "--state-file", type=Path, default=CUSTOM_COLOUR_STATE_PATH
    )
    return parser


def main(
    argv: list[str] | None = None, *,
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    executor: Callable[..., Path] = execute_custom_colour_change,
    input_fn: Callable[[str], str] = input,
    permission_check: Callable[[Path], bool] = (
        lambda path: os.access(path, os.R_OK | os.W_OK)
    ),
) -> int:
    args = _parser().parse_args(argv)
    if args.semantic_hex is None:
        if args.live_custom_colour_write or args.dry_run:
            print("error: --hex '#RRGGBB' is required", file=sys.stderr)
            return 2
        target = None
    else:
        try:
            target = CustomColour.parse(args.semantic_hex)
        except GuardedCustomColourError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.safety_audit:
        for line in safety_audit():
            print(f"PASS: {line}")
        if not args.live_custom_colour_write and not args.dry_run:
            return 0
    if args.dry_run:
        print("\n".join(dry_run_lines(target.semantic_hex)))
        if not args.live_custom_colour_write:
            return 0
    if not args.live_custom_colour_write:
        print("offline mode; no hidraw discovery or open occurred")
        return 0
    if (
        target is None or target.semantic_hex != LIVE_TARGET_HEX
        or not args.acknowledge_device_write
    ):
        print(
            "error: live milestone requires --hex '#FF0000' and "
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
    required = "WRITE 0449 #0000FF TO #FF0000"
    if input_fn(f"Type exactly {required}: ") != required:
        print("confirmation mismatch; private writer was not opened",
              file=sys.stderr)
        return 2
    output = executor(
        device, target_hex=target.semantic_hex,
        output_root=args.output_root, state_path=args.state_file,
    )
    print(f"verified custom-colour change completed: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
