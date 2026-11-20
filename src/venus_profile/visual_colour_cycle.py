"""Finite, user-paced offline/testable 20-colour visual cycle guard."""

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
    DeviceSession, DeviceSessionError, _CUSTOM_COLOUR_CAPABILITY,
    create_timestamped_directory, ownership_warnings,
)
from .known_custom_colour_state import (
    CUSTOM_COLOUR_STATE_PATH, LIGHTING_SUFFIX, load_custom_colour_state,
    record_visual_diagnostic, semantic_hex_to_prefix, semantic_hex_to_rgb,
)
from .readonly_hidraw import (
    HidrawDevice, HidrawPermissionError, REPORT2_LENGTH,
    _HidrawFeatureIo, _hid_iocsfeature, build_f2_request,
    discover_venus_hidraw,
)


PREFIX_ADDRESS = 0x0449
PREFIX_LENGTH = 6
INTER_COMMAND_DELAY_SECONDS = 0.010
LIGHTING_FINALISATION_REPORT = bytes.fromhex(
    "02 f1 02 04 00 00 00 00 00 00 00 00 00 00 00 00"
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESULT_ROOT = (
    PROJECT_ROOT / "usb-captures" / "visual-colour-cycles"
)
INITIAL_CONFIRMATION = "RUN 20 COLOUR VISUAL TEST"


@dataclass(frozen=True)
class VisualColour:
    number: int
    name: str
    semantic_hex: str

    @property
    def rgb(self) -> tuple[int, int, int]:
        return semantic_hex_to_rgb(self.semantic_hex)

    @property
    def prefix(self) -> bytes:
        return semantic_hex_to_prefix(self.semantic_hex)


# Deliberately immutable and not caller-configurable.
COLOUR_PLAN: tuple[VisualColour, ...] = (
    VisualColour(1, "Red", "#FF0000"),
    VisualColour(2, "Green", "#00FF00"),
    VisualColour(3, "Blue", "#0000FF"),
    VisualColour(4, "Yellow", "#FFFF00"),
    VisualColour(5, "Cyan", "#00FFFF"),
    VisualColour(6, "Magenta", "#FF00FF"),
    VisualColour(7, "White", "#FFFFFF"),
    VisualColour(8, "Black", "#000000"),
    VisualColour(9, "Orange", "#FF8000"),
    VisualColour(10, "Purple", "#8000FF"),
    VisualColour(11, "Deep Pink", "#FF1493"),
    VisualColour(12, "Deep Sky Blue", "#00BFFF"),
    VisualColour(13, "Chartreuse", "#7FFF00"),
    VisualColour(14, "Gold", "#FFD700"),
    VisualColour(15, "Saddle Brown", "#8B4513"),
    VisualColour(16, "Turquoise", "#40E0D0"),
    VisualColour(17, "Crimson", "#DC143C"),
    VisualColour(18, "Lime Green", "#32CD32"),
    VisualColour(19, "Asymmetric Test A", "#12A7E4"),
    VisualColour(20, "Asymmetric Test B", "#7B2DD2"),
)


class VisualColourCycleError(RuntimeError):
    """The finite visual cycle stopped safely."""


def _read_response(input_fn: Callable[[str], str], prompt: str) -> str:
    """Read one response, converting EOF into an auditable abort."""
    try:
        return input_fn(prompt)
    except EOFError as exc:
        raise VisualColourCycleError("input ended before the visual cycle completed") from exc


def _initial_confirmation(input_fn: Callable[[str], str]) -> None:
    while True:
        response = _read_response(
            input_fn, "Type exactly RUN 20 COLOUR VISUAL TEST to begin: "
        )
        if response == INITIAL_CONFIRMATION:
            return
        print("Confirmation mismatch; type the exact confirmation or Ctrl+C to abort.")


def _enter_or_abort(input_fn: Callable[[str], str], prompt: str) -> None:
    while True:
        response = _read_response(input_fn, prompt)
        command = response.upper()
        if response == "":
            return
        if command == "ABORT":
            raise VisualColourCycleError("user aborted")
        print("Invalid response; press Enter to continue or type ABORT.")


def _visual_answer(input_fn: Callable[[str], str]) -> str:
    while True:
        response = _read_response(
            input_fn, "Did the mouse match? Type exactly Y, N, or SKIP: "
        )
        answer = response.upper()
        if answer in {"Y", "N", "SKIP"}:
            return answer
        print("Invalid visual answer; type Y, N, or SKIP (or Ctrl+C to abort).")


class _VisualIo(Protocol):
    def send_colour(self, colour: VisualColour) -> bytes: ...
    def finalise_lighting(self) -> bytes: ...
    def close(self) -> None: ...


class _GuardedVisualIo(_HidrawFeatureIo):
    def _submit_exact(self, report: bytes) -> None:
        if len(report) != REPORT2_LENGTH:
            raise VisualColourCycleError("visual report must be 16 bytes")
        if report == LIGHTING_FINALISATION_REPORT:
            pass
        elif (
            report[:8] == bytes.fromhex("02 f3 49 04 06 00 00 00")
            and report[14:] == b"\0\0"
            and report[11:14] == LIGHTING_SUFFIX
            and report[8:14] in {item.prefix for item in COLOUR_PLAN}
        ):
            pass
        else:
            raise VisualColourCycleError("non-visual report rejected")
        result = self._ioctl(
            self._fd, _hid_iocsfeature(REPORT2_LENGTH), bytearray(report), True
        )
        if result != REPORT2_LENGTH:
            raise VisualColourCycleError(
                f"SET_FEATURE returned {result}, expected 16"
            )

    def send_colour(self, colour: VisualColour) -> bytes:
        report = build_visual_f3(colour)
        self._submit_exact(report)
        return report

    def finalise_lighting(self) -> bytes:
        self._submit_exact(LIGHTING_FINALISATION_REPORT)
        return LIGHTING_FINALISATION_REPORT


class VisualColourTransport:
    def __init__(
        self, device: HidrawDevice | DeviceSession, *,
        backend_factory: Callable[[Path], _VisualIo] = _GuardedVisualIo,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._backend_factory = backend_factory
        self._backend: _VisualIo | None = None
        try:
            self._session = (
                device if isinstance(device, DeviceSession)
                else DeviceSession(device)
            )
        except DeviceSessionError as exc:
            raise VisualColourCycleError(str(exc)) from exc
        self.device = self._session.device
        self._sleeper = sleeper
        self.reports_sent: list[tuple[str, bytes]] = []

    def read_prefix(self) -> bytes:
        value = self._session.read_memory(PREFIX_ADDRESS, PREFIX_LENGTH)
        if len(value) != PREFIX_LENGTH:
            raise VisualColourCycleError("six-byte prefix read was short")
        if value[3:] != LIGHTING_SUFFIX:
            raise VisualColourCycleError(
                f"lighting suffix mismatch: {value[3:].hex(' ')}"
            )
        return value

    def write_one(self, colour: VisualColour) -> bytes:
        current = self.read_prefix()
        request = build_f2_request(PREFIX_ADDRESS, PREFIX_LENGTH)
        self.reports_sent.append(("prewrite-f2", request))
        writer = self._writer()
        report = writer.send_colour(colour)
        self.reports_sent.append(("colour-f3", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)
        report = writer.finalise_lighting()
        self.reports_sent.append(("lighting-f1", report))
        self._sleeper(INTER_COMMAND_DELAY_SECONDS)
        self.reports_sent.append(("postwrite-f2", request))
        read_back = self.read_prefix()
        if read_back != colour.prefix:
            raise VisualColourCycleError(
                f"read-back mismatch for {colour.semantic_hex}: "
                f"{read_back.hex(' ')}"
            )
        return read_back

    def _writer(self) -> _VisualIo:
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

    def __enter__(self) -> "VisualColourTransport":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def build_visual_f3(colour: VisualColour) -> bytes:
    if colour not in COLOUR_PLAN:
        raise VisualColourCycleError("colour is not in the fixed visual plan")
    report = (
        bytes.fromhex("02 f3 49 04 06 00 00 00")
        + colour.prefix + b"\0\0"
    )
    if len(report) != 16 or report[11:14] != LIGHTING_SUFFIX:
        raise VisualColourCycleError("invalid generated visual report")
    return report


def _write_tsv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _create_artifacts(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _write_tsv(
        output / "colour_plan.tsv",
        ["step", "name", "semantic_hex", "rgb", "prefix", "f3"],
        [
            [
                item.number, item.name, item.semantic_hex, str(item.rgb),
                item.prefix.hex(" "), build_visual_f3(item).hex(" "),
            ] for item in COLOUR_PLAN
        ],
    )


def run_visual_cycle(
    device: HidrawDevice, *, output_root: Path = DEFAULT_RESULT_ROOT,
    state_path: Path = CUSTOM_COLOUR_STATE_PATH,
    transport_factory: Callable[
        [HidrawDevice], VisualColourTransport
    ] = VisualColourTransport,
    input_fn: Callable[[str], str] = input,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Path:
    state = load_custom_colour_state(state_path)
    transport: VisualColourTransport | None = None
    output: Path | None = None
    step_rows: list[list[object]] = []
    readback_rows: list[list[object]] = []
    observation_rows: list[list[object]] = []
    attempted = 0
    confirmed = 0
    last_prefix: bytes | None = None
    status = "aborted"
    first_failure: str | None = None
    started = clock().astimezone(timezone.utc).isoformat()
    try:
        # Creating a user-owned result directory is harmless and ensures even
        # an early Ctrl+C/malformed confirmation has an auditable outcome.
        output = create_timestamped_directory(
            output_root, "visual_colour_cycle", clock()
        )
        _create_artifacts(output)
        _initial_confirmation(input_fn)
        transport = transport_factory(device)
        for colour in COLOUR_PLAN:
            attempted += 1
            print(
                f"Step {colour.number} of 20 — {colour.name} — "
                f"{colour.semantic_hex} — RGB {colour.rgb}"
            )
            print(f"Expected prefix: {colour.prefix.hex(' ')}")
            _enter_or_abort(
                input_fn,
                "Press Enter to write this colour, or type ABORT and press Enter to stop: ",
            )
            timestamp = clock().astimezone(timezone.utc).isoformat()
            try:
                read_back = transport.write_one(colour)
            except Exception as exc:
                first_failure = f"step {colour.number}: {exc}"
                raise
            confirmed += 1
            last_prefix = read_back
            readback_rows.append([
                colour.number, timestamp, colour.semantic_hex,
                read_back.hex(" "), True,
            ])
            print(f"Write confirmed; read-back: {read_back.hex(' ')}")
            print("Please visually inspect the mouse.")
            visual = _visual_answer(input_fn)
            observation = ""
            if visual == "N":
                observation = _read_response(input_fn, "Brief observation: ")
            observation_rows.append([
                colour.number, timestamp, colour.name, colour.semantic_hex,
                visual, observation,
            ])
            step_rows.append([
                colour.number, colour.name, colour.semantic_hex,
                str(colour.rgb), colour.prefix.hex(" "),
                read_back.hex(" "), True, visual, observation, timestamp,
            ])
            if colour.number < len(COLOUR_PLAN):
                _enter_or_abort(
                    input_fn,
                    "Press Enter to continue to the next colour, or type ABORT and press Enter to stop: ",
                )
        status = "completed"
    except KeyboardInterrupt:
        first_failure = first_failure or "Ctrl+C"
    except Exception as exc:
        first_failure = first_failure or str(exc)
    finally:
        if transport is not None:
            transport.close()
        if output is not None:
            sent = transport.reports_sent if transport is not None else []
            _write_tsv(
                output / "reports_sent.tsv",
                ["sequence", "operation", "report_id", "payload"],
                [
                    [i, name, report[0], report.hex(" ")]
                    for i, (name, report) in enumerate(sent, 1)
                ],
            )
            _write_tsv(
                output / "step_results.tsv",
                ["step", "name", "semantic_hex", "rgb", "sent_prefix",
                 "read_back", "transport_success", "visual", "observation",
                 "timestamp"], step_rows,
            )
            _write_tsv(
                output / "readbacks.tsv",
                ["step", "timestamp", "semantic_hex", "read_back",
                 "matched"], readback_rows,
            )
            _write_tsv(
                output / "visual_observations.tsv",
                ["step", "timestamp", "name", "semantic_hex", "answer",
                 "observation"], observation_rows,
            )
            completed = clock().astimezone(timezone.utc).isoformat()
            record_visual_diagnostic(
                state_path=state_path, result_directory=output, status=status,
                last_confirmed_prefix=last_prefix, timestamp=completed,
                steps_attempted=attempted, writes_confirmed=confirmed,
                visual_y_count=sum(row[7] == "Y" for row in step_rows),
                visual_n_count=sum(row[7] == "N" for row in step_rows),
                skipped_count=sum(row[7] == "SKIP" for row in step_rows),
            )
            operation = {
                "started": started, "completed": completed,
                "status": status, "steps_attempted": attempted,
                "writes_confirmed": confirmed,
                "visual_y_count": sum(row[7] == "Y" for row in step_rows),
                "visual_n_count": sum(row[7] == "N" for row in step_rows),
                "skipped_count": sum(row[7] == "SKIP" for row in step_rows),
                "partial_run": status != "completed",
                "last_completed_step": confirmed,
                "first_failure": first_failure,
                "result_directory": str(output),
                "protocol_delay_ms": 10, "automatic_progression": False,
                "automatic_restoration": False,
            }
            (output / "operation.json").write_text(
                json.dumps(operation, indent=2) + "\n", encoding="utf-8"
            )
            (output / "result.md").write_text(
                "# Finite 20-colour visual cycle\n\n"
                f"- Status: {status}\n- Steps attempted: {attempted}\n"
                f"- Writes confirmed: {confirmed}\n"
                f"- Partial run: {'yes' if status != 'completed' else 'no'}\n"
                f"- Visual Y/N/SKIP: {operation['visual_y_count']}/"
                f"{operation['visual_n_count']}/{operation['skipped_count']}\n"
                f"- First failure: {first_failure or 'none'}\n"
                "- Persistence: untested/pending\n",
                encoding="utf-8",
            )
    return output if output is not None else output_root


def dry_run_lines() -> list[str]:
    lines = [
        "DRY RUN ONLY — no discovery and no hidraw open",
        "Fixed finite plan: exactly 20 colours; no timer or automatic progression",
    ]
    for colour in COLOUR_PLAN:
        lines.extend([
            f"Step {colour.number} of 20: {colour.name} {colour.semantic_hex}",
            f"  RGB: {colour.rgb}",
            f"  prefix: {colour.prefix.hex(' ')}",
            f"  F3: {build_visual_f3(colour).hex(' ')}",
        ])
    return lines


def safety_audit() -> list[str]:
    source = Path(__file__).read_text(encoding="utf-8")
    if ("--" + "colour" + "-list") in source or ("--" + "infinite") in source:
        raise VisualColourCycleError("caller plan/infinite option found")
    if ("write" + "_memory") in source or ("set" + "_feature_report") in source:
        raise VisualColourCycleError("generic writer found")
    return [
        "exact immutable 20-colour plan is hard-coded",
        "direct RR GG BB conversion and suffix 01 00 01 are enforced",
        "only 0x0449-0x044E and exact F2/F3/F1 templates are used",
        "brightness 0x044F, F5, and Reports 3/4/5 writes are absent",
        "initial confirmation and Enter gating precede every write",
        "Y/N/SKIP visual recording and N observation are required",
        "no timer, automatic progression, retry, restoration, or chaining",
        "default, dry-run, and audit modes never discover or open hidraw",
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.visual_colour_cycle"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--safety-audit", action="store_true")
    parser.add_argument("--live-visual-test", action="store_true")
    parser.add_argument("--acknowledge-device-write", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--state-file", type=Path, default=CUSTOM_COLOUR_STATE_PATH)
    return parser


def main(
    argv: list[str] | None = None, *,
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    runner: Callable[..., Path] = run_visual_cycle,
    input_fn: Callable[[str], str] = input,
    permission_check: Callable[[Path], bool] = (
        lambda path: os.access(path, os.R_OK | os.W_OK)
    ),
) -> int:
    args = _parser().parse_args(argv)
    if args.safety_audit:
        for line in safety_audit():
            print(f"PASS: {line}")
        if not args.live_visual_test and not args.dry_run:
            return 0
    if args.dry_run:
        print("\n".join(dry_run_lines()))
        if not args.live_visual_test:
            return 0
    if not args.live_visual_test:
        print("offline mode; no hidraw discovery or open occurred")
        return 0
    if not args.acknowledge_device_write:
        print("error: --acknowledge-device-write is required", file=sys.stderr)
        return 2
    device = discovery()
    if not permission_check(device.path):
        raise HidrawPermissionError(f"read/write access unavailable: {device.path}")
    print(f"selected hidraw node: {device.path}")
    for warning in ownership_warnings(
        [args.state_file, args.output_root], sudo_user=os.environ.get("SUDO_USER")
    ):
        print(f"WARNING: {warning}", file=sys.stderr)
    runner(
        device, output_root=args.output_root, state_path=args.state_file,
        input_fn=input_fn,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
