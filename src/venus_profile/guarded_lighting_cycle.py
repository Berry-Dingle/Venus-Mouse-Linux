"""Guarded, user-paced Venus lighting verification cycle."""

from __future__ import annotations

import argparse
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
    DeviceSession,
    DeviceSessionError,
    _LED_MODE_CAPABILITY,
    create_timestamped_directory,
    ownership_warnings,
)
from .readonly_hidraw import (
    HidrawDevice,
    HidrawPermissionError,
    REPORT2_LENGTH,
    _HidrawFeatureIo,
    _hid_iocsfeature,
    build_f2_request,
    discover_venus_hidraw,
)

PREFIX_ADDRESS = 0x0449
PREFIX_LENGTH = 6
INTER_COMMAND_DELAY_SECONDS = 0.010
LIGHTING_FINALISATION_REPORT = bytes.fromhex(
    "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"
)
FULL_BRIGHTNESS_PREFIX = bytes.fromhex("7b 2d d2 01 00 01")
INITIAL_CONFIRMATION = "WRITE LIGHTING CYCLE"

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESULT_ROOT = PROJECT_ROOT / "usb-captures" / "live-lighting-cycles"


@dataclass(frozen=True)
class LightingStage:
    number: int
    name: str
    prefix: bytes


STAGES: tuple[LightingStage, ...] = (
    LightingStage(1, "Wave", bytes.fromhex("7b 2d d2 03 00 01")),
    LightingStage(2, "Flash", bytes.fromhex("7b 2d d2 04 05 01")),
    LightingStage(3, "Breathing Low", bytes.fromhex("7b 2d d2 02 08 01")),
    LightingStage(4, "Breathing Medium", bytes.fromhex("7b 2d d2 02 05 01")),
    LightingStage(5, "Breathing High", bytes.fromhex("7b 2d d2 02 02 01")),
    LightingStage(
        6,
        "Breathing UI Full Brightness",
        bytes.fromhex("7b 2d d2 02 00 01"),
    ),
    LightingStage(7, "Restore Full Brightness", FULL_BRIGHTNESS_PREFIX),
)
ALLOWED_PREFIXES = frozenset(stage.prefix for stage in STAGES)


class GuardedLightingCycleError(RuntimeError):
    pass


def _read_response(input_fn: Callable[[str], str], prompt: str) -> str:
    try:
        return input_fn(prompt)
    except EOFError as exc:
        raise GuardedLightingCycleError("input ended before completion") from exc


def _confirm(input_fn: Callable[[str], str]) -> None:
    value = _read_response(input_fn, f"Type exactly {INITIAL_CONFIRMATION}: ")
    if value != INITIAL_CONFIRMATION:
        raise GuardedLightingCycleError("confirmation mismatch")


def _enter_or_abort(input_fn: Callable[[str], str]) -> None:
    while True:
        value = _read_response(
            input_fn,
            "Press Enter to write, or type Q to abort and restore: ",
        )
        if value == "":
            return
        if value.upper() == "Q":
            raise GuardedLightingCycleError("user aborted")
        print("Invalid response; press Enter or type Q.")


def _visual(input_fn: Callable[[str], str]) -> tuple[str, str]:
    while True:
        value = _read_response(
            input_fn,
            "Visual result — Y expected, N wrong, U uncertain, Q abort: ",
        ).upper()
        if value in {"Y", "U"}:
            return value, ""
        if value == "N":
            return value, _read_response(input_fn, "Brief observation: ")
        if value == "Q":
            raise GuardedLightingCycleError("user aborted")
        print("Invalid response; type Y, N, U, or Q.")


def build_stage_report(stage: LightingStage) -> bytes:
    if stage not in STAGES:
        raise GuardedLightingCycleError("unknown stage")
    report = bytes.fromhex("02 f3 49 04 06 00 00 00") + stage.prefix + b"\x00\x00"
    validate_outgoing_report(report)
    return report


def validate_outgoing_report(report: bytes) -> str:
    if len(report) != REPORT2_LENGTH:
        raise GuardedLightingCycleError("outgoing report must be 16 bytes")
    if report == LIGHTING_FINALISATION_REPORT:
        return "lighting-finalisation"
    if (
        report[:8] == bytes.fromhex("02 f3 49 04 06 00 00 00")
        and report[8:14] in ALLOWED_PREFIXES
        and report[14:] == b"\x00\x00"
    ):
        return "lighting-prefix"
    raise GuardedLightingCycleError("outgoing report rejected")


class _CycleIo(Protocol):
    def send_stage(self, stage: LightingStage) -> bytes: ...
    def finalise_lighting(self) -> bytes: ...
    def close(self) -> None: ...


class _GuardedCycleIo(_HidrawFeatureIo):
    def _submit_exact(self, report: bytes) -> None:
        validate_outgoing_report(report)
        result = self._ioctl(
            self._fd,
            _hid_iocsfeature(REPORT2_LENGTH),
            bytearray(report),
            True,
        )
        if result != REPORT2_LENGTH:
            raise GuardedLightingCycleError(
                f"SET_FEATURE returned {result}, expected {REPORT2_LENGTH}"
            )

    def send_stage(self, stage: LightingStage) -> bytes:
        report = build_stage_report(stage)
        self._submit_exact(report)
        return report

    def finalise_lighting(self) -> bytes:
        self._submit_exact(LIGHTING_FINALISATION_REPORT)
        return LIGHTING_FINALISATION_REPORT


class GuardedLightingCycleTransport:
    def __init__(
        self,
        device: HidrawDevice | DeviceSession,
        *,
        backend_factory: Callable[[Path], _CycleIo] = _GuardedCycleIo,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        try:
            self._session = (
                device if isinstance(device, DeviceSession) else DeviceSession(device)
            )
        except DeviceSessionError as exc:
            raise GuardedLightingCycleError(str(exc)) from exc
        self.device = self._session.device
        self._backend_factory = backend_factory
        self._backend: _CycleIo | None = None
        self._sleeper = sleeper
        self.reports_sent: list[tuple[str, bytes]] = []

    def read_prefix(self) -> bytes:
        value = self._session.read_memory(PREFIX_ADDRESS, PREFIX_LENGTH)
        if len(value) != PREFIX_LENGTH:
            raise GuardedLightingCycleError("six-byte read was short")
        if value not in ALLOWED_PREFIXES:
            raise GuardedLightingCycleError(
                f"unexpected lighting prefix {value.hex(' ')}"
            )
        return value

    def write_stage(self, stage: LightingStage) -> bytes:
        request = build_f2_request(PREFIX_ADDRESS, PREFIX_LENGTH)
        self.reports_sent.append(("prewrite-f2", request))
        self.read_prefix()

        writer = self._writer()
        report = writer.send_stage(stage)
        self.reports_sent.append((f"stage-{stage.number}-f3", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)

        report = writer.finalise_lighting()
        self.reports_sent.append((f"stage-{stage.number}-f1", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)

        self.reports_sent.append(("postwrite-f2", request))
        read_back = self.read_prefix()
        if read_back != stage.prefix:
            raise GuardedLightingCycleError(
                f"read-back mismatch: expected {stage.prefix.hex(' ')}, "
                f"got {read_back.hex(' ')}"
            )
        return read_back

    def restore(self) -> tuple[bool, str]:
        try:
            current = self.read_prefix()
            if current == FULL_BRIGHTNESS_PREFIX:
                return True, "already Full Brightness"
            read_back = self.write_stage(STAGES[-1])
            return read_back == FULL_BRIGHTNESS_PREFIX, read_back.hex(" ")
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _writer(self) -> _CycleIo:
        if self._backend is None:
            self._session._request_internal_capability(_LED_MODE_CAPABILITY)
            self._backend = self._backend_factory(self.device.path)
        return self._backend

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._session.close()


def _write_tsv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def run_cycle(
    device: HidrawDevice,
    *,
    output_root: Path = DEFAULT_RESULT_ROOT,
    input_fn: Callable[[str], str] = input,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    transport_factory: Callable[
        [HidrawDevice], GuardedLightingCycleTransport
    ] = GuardedLightingCycleTransport,
) -> Path:
    output = create_timestamped_directory(
        output_root, "guarded_lighting_cycle", clock()
    )
    started = clock().astimezone(timezone.utc).isoformat()
    rows: list[list[object]] = []
    status = "aborted"
    error: str | None = None
    restored = False
    restore_detail = "not attempted"
    transport: GuardedLightingCycleTransport | None = None

    try:
        _confirm(input_fn)
        transport = transport_factory(device)
        initial = transport.read_prefix()
        if initial != FULL_BRIGHTNESS_PREFIX:
            raise GuardedLightingCycleError(
                f"starting prefix must be {FULL_BRIGHTNESS_PREFIX.hex(' ')}, "
                f"found {initial.hex(' ')}"
            )

        for stage in STAGES:
            print()
            print(f"Stage {stage.number} of {len(STAGES)} — {stage.name}")
            print(f"Current: {transport.read_prefix().hex(' ')}")
            print(f"Target:  {stage.prefix.hex(' ')}")
            _enter_or_abort(input_fn)
            timestamp = clock().astimezone(timezone.utc).isoformat()
            read_back = transport.write_stage(stage)
            print(f"Read-back confirmed: {read_back.hex(' ')}")
            visual, note = _visual(input_fn)
            rows.append([
                stage.number,
                stage.name,
                stage.prefix.hex(" "),
                read_back.hex(" "),
                visual,
                note,
                timestamp,
            ])
            if visual == "N":
                raise GuardedLightingCycleError(
                    f"visual result incorrect at stage {stage.number}"
                )

        status = "completed"
        restored = True
        restore_detail = "final stage confirmed Full Brightness"

    except KeyboardInterrupt:
        error = "KeyboardInterrupt: Ctrl+C"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if transport is not None and not restored:
            restored, restore_detail = transport.restore()

        completed = clock().astimezone(timezone.utc).isoformat()
        reports = transport.reports_sent if transport is not None else []

        _write_tsv(
            output / "reports_sent.tsv",
            ["sequence", "operation", "report_id", "payload"],
            [
                [i, name, report[0], report.hex(" ")]
                for i, (name, report) in enumerate(reports, 1)
            ],
        )
        _write_tsv(
            output / "stage_results.tsv",
            [
                "stage",
                "name",
                "target_prefix",
                "read_back",
                "visual",
                "observation",
                "timestamp",
            ],
            rows,
        )

        operation = {
            "started": started,
            "completed": completed,
            "status": status,
            "error": error,
            "device": device.to_dict(),
            "address_start_hex": "0x0449",
            "address_end_hex": "0x044E",
            "brightness_address_excluded": "0x044F",
            "stages_completed": len(rows),
            "stage_count": len(STAGES),
            "restoration_succeeded": restored,
            "restoration_detail": restore_detail,
            "power_cycle_persistence": "not-established",
        }
        (output / "operation.json").write_text(
            json.dumps(operation, indent=2) + "\n",
            encoding="utf-8",
        )
        (output / "result.md").write_text(
            "# Guarded lighting-cycle result\n\n"
            f"- Status: {status}\n"
            f"- Stages completed: {len(rows)}/{len(STAGES)}\n"
            f"- Error: {error or 'none'}\n"
            f"- Restored Full Brightness: {restored}\n"
            f"- Restoration detail: {restore_detail}\n"
            "- Address written: `0x0449-0x044E`\n"
            "- Brightness `0x044F` written: no\n"
            "- Persistence: not established\n",
            encoding="utf-8",
        )

        if transport is not None:
            transport.close()

    print()
    for row in rows:
        print(f"{row[0]}. {row[1]}: read-back PASS; visual {row[4]}")
    print(f"Restored Full Brightness: {'YES' if restored else 'NO'}")
    print(f"Result directory: {output}")

    if status != "completed":
        raise GuardedLightingCycleError(error or "cycle did not complete")
    return output


def dry_run_lines() -> list[str]:
    request = build_f2_request(PREFIX_ADDRESS, PREFIX_LENGTH)
    lines = [
        "DRY RUN ONLY — no discovery and no hidraw open",
        f"Start: {FULL_BRIGHTNESS_PREFIX.hex(' ')}",
        f"Confirmation: {INITIAL_CONFIRMATION}",
    ]
    for stage in STAGES:
        lines.extend([
            f"{stage.number}. {stage.name}: {stage.prefix.hex(' ')}",
            f"   F2: {request.hex(' ')}",
            f"   F3: {build_stage_report(stage).hex(' ')}",
            f"   F1: {LIGHTING_FINALISATION_REPORT.hex(' ')}",
        ])
    lines.append("0x044F excluded; no F5; restores Full Brightness")
    return lines


def safety_audit() -> list[str]:
    source = Path(__file__).read_text(encoding="utf-8")
    forbidden = (
        "write" + "_memory",
        "set_feature" + "_report",
        "--non" + "interactive",
        "--" + "yes",
        "--pre" + "fix",
        "--mode" + "-list",
    )
    for token in forbidden:
        if token in source:
            raise GuardedLightingCycleError(
                f"forbidden generic/bypass token found: {token}"
            )
    return [
        "fixed immutable seven-stage plan",
        "only exact captured six-byte prefixes are accepted",
        "only 0x0449-0x044E Report-2 F3 and exact lighting F1 are sent",
        "brightness 0x044F, F5, and Reports 3/4/5 writes are absent",
        "starting Full Brightness prefix is mandatory",
        "exact immediate read-back is mandatory after every stage",
        "N, Q, exception, and Ctrl+C trigger one guarded restoration attempt",
        "dry-run and audit modes do not discover or open hidraw",
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.guarded_lighting_cycle"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--safety-audit", action="store_true")
    parser.add_argument("--live-lighting-cycle", action="store_true")
    parser.add_argument("--acknowledge-device-writes", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_RESULT_ROOT)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    runner: Callable[..., Path] = run_cycle,
    input_fn: Callable[[str], str] = input,
    permission_check: Callable[[Path], bool] = (
        lambda path: os.access(path, os.R_OK | os.W_OK)
    ),
) -> int:
    args = _parser().parse_args(argv)

    if args.safety_audit:
        for line in safety_audit():
            print(f"PASS: {line}")
        if not args.live_lighting_cycle and not args.dry_run:
            return 0

    if args.dry_run:
        print("\n".join(dry_run_lines()))
        if not args.live_lighting_cycle:
            return 0

    if not args.live_lighting_cycle:
        print("offline mode; no hidraw discovery or open occurred")
        return 0

    if not args.acknowledge_device_writes:
        print(
            "error: --acknowledge-device-writes is required",
            file=sys.stderr,
        )
        return 2

    device = discovery()
    if not permission_check(device.path):
        raise HidrawPermissionError(
            f"read/write access unavailable: {device.path}"
        )

    print(f"selected hidraw node: {device.path}")
    print(
        f"VID:PID={device.vendor_id:04X}:{device.product_id:04X} "
        f"interface={device.interface_number:02X} "
        f"usage=0x{device.usage_page:04X}/0x{device.usage:04X}"
    )

    for warning in ownership_warnings(
        [args.output_root],
        sudo_user=os.environ.get("SUDO_USER"),
    ):
        print(f"WARNING: {warning}", file=sys.stderr)

    runner(device, output_root=args.output_root, input_fn=input_fn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
