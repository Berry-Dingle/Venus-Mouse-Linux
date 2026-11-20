"""Offline-default CLI for the first read-only Venus hidraw milestone."""

from __future__ import annotations

import argparse
import ast
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Callable

from .device_session import (
    DeviceSession,
    create_timestamped_directory,
    load_known_read_plan,
    ownership_warnings,
)
from .known_device_state import (
    KNOWN_STATE_PATH,
    EffectiveExpectedState,
    effective_expected_state,
)
from .offline_usb_capture import MemoryImage
from .readonly_hidraw import (
    F2_COMMAND,
    HidrawDevice,
    HidrawProtocolError,
    ReadOnlyHidrawTransport,
    build_f2_request,
    discover_venus_hidraw,
    response_report_for_length,
    validate_f2_response,
    validate_f2_request,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FACTORY_TRANSACTIONS = (
    PROJECT_ROOT / "usb-captures" / "analysis" / "factory_transactions.json"
)
FACTORY_MEMORY_MAP = (
    PROJECT_ROOT / "usb-captures" / "analysis" / "factory_memory_map.json"
)
LATEST_MACRO_SLOT = (
    PROJECT_ROOT / "usb-captures" / "analysis"
    / "macro_slot1_a_press_release" / "macro_slot1.bin"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "usb-captures" / "live-reads"


class FixtureValidationError(RuntimeError):
    """An authoritative offline fixture is missing or internally inconsistent."""


def load_factory_read_plan(
    transactions_path: Path = FACTORY_TRANSACTIONS,
) -> list[dict[str, object]]:
    return load_known_read_plan(transactions_path)


def reconstruct_plan_memory(plan: list[dict[str, object]]) -> MemoryImage:
    values: dict[int, int] = {}
    overlaps: list[dict[str, int]] = []
    for record in plan:
        address = int(record["address"])
        data = bytes(record["captured_data"])
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
    return MemoryImage(values, overlaps)


def validate_offline_fixtures(
    transactions_path: Path = FACTORY_TRANSACTIONS,
    memory_map_path: Path = FACTORY_MEMORY_MAP,
) -> dict[str, object]:
    plan = load_factory_read_plan(transactions_path)
    memory = reconstruct_plan_memory(plan)
    expected_document = json.loads(memory_map_path.read_text(encoding="utf-8"))
    expected = {
        int(address, 16): value
        for address, value in expected_document["bytes"].items()
    }
    if memory.values != expected:
        raise FixtureValidationError(
            "transaction reconstruction differs from factory_memory_map.json"
        )
    if memory.overlaps:
        raise FixtureValidationError("factory plan unexpectedly overlaps")
    return {
        "read_count": len(plan),
        "returned_byte_count": len(memory.values),
        "minimum_address": memory.minimum_address,
        "maximum_address": memory.maximum_address,
        "range_count": len(memory.ranges()),
        "gap_count": len(memory.gaps()),
    }


def expected_latest_memory(
    *,
    state_path: Path = KNOWN_STATE_PATH,
) -> dict[int, int]:
    """Latest Windows-captured baseline plus validated Linux-state overlay."""

    return effective_expected_state(
        state_path=state_path, allow_missing=True
    ).values


def _new_output_directory(root: Path) -> Path:
    return create_timestamped_directory(root, "venus_read")


def _write_live_outputs(
    output: Path,
    *,
    device: HidrawDevice,
    transactions: list[dict[str, object]],
    memory: MemoryImage,
    expected: EffectiveExpectedState,
) -> None:
    unexpected = [
        (address, expected.values.get(address), value)
        for address, value in sorted(memory.values.items())
        if expected.values.get(address) != value
    ]
    expected_known = sum(address in expected.values for address in memory.values)
    windows_expectations = sum(
        expected.sources.get(address) == "windows-captured-baseline"
        for address in memory.values
    )
    linux_expectations = sum(
        expected.sources.get(address) == "confirmed-linux-state-overlay"
        for address in memory.values
    )
    transaction_document = {
        "mode": "live-read-only",
        "device": device.to_dict(),
        "read_count": len(transactions),
        "transactions": transactions,
    }
    (output / "live_transactions.json").write_text(
        json.dumps(transaction_document, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "live_reads.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "index", "timestamp", "address", "length", "request",
            "response_report_id", "response", "returned_data",
        ])
        for record in transactions:
            writer.writerow([
                record["index"], record["timestamp"], record["address_hex"],
                record["length"], record["request"],
                record["response_report_id"], record["response"],
                record["returned_data"],
            ])
    (output / "live_memory.bin").write_bytes(memory.flat_bytes())
    (output / "live_memory_present.bin").write_bytes(bytes(
        int(address in memory.values)
        for address in range(memory.minimum_address, memory.maximum_address + 1)
    ))
    memory_map = {
        "base_address": memory.minimum_address,
        "maximum_address": memory.maximum_address,
        "flat_binary_size": memory.maximum_address - memory.minimum_address + 1,
        "gap_fill_byte": 0,
        "returned_byte_count": len(memory.values),
        "ranges": [
            {"start": start, "end": end, "length": end - start + 1}
            for start, end in memory.ranges()
        ],
        "gaps": [
            {"start": start, "end": end, "length": end - start + 1}
            for start, end in memory.gaps()
        ],
        "overlaps": memory.overlaps,
        "bytes": {
            f"0x{address:04X}": value
            for address, value in sorted(memory.values.items())
        },
        "comparison": {
            "expected_known_addresses": expected_known,
            "windows_captured_baseline_expectations": windows_expectations,
            "confirmed_linux_overlay_expectations": linux_expectations,
            "confirmed_linux_overrides": list(expected.overlay_entries),
            "unexpected_difference_count": len(unexpected),
            "unexpected_differences": [
                {
                    "address": address,
                    "address_hex": f"0x{address:04X}",
                    "expected": old,
                    "actual": new,
                    "expectation_source": expected.sources.get(address),
                }
                for address, old, new in unexpected
            ],
        },
    }
    (output / "live_memory_map.json").write_text(
        json.dumps(memory_map, indent=2) + "\n", encoding="utf-8"
    )
    gap_lines = ["# start\tend\tlength"]
    gap_lines.extend(
        f"0x{start:04X}\t0x{end:04X}\t{end - start + 1}"
        for start, end in memory.gaps()
    )
    (output / "live_gaps.txt").write_text(
        "\n".join(gap_lines) + "\n", encoding="utf-8"
    )
    report = [
        "# Venus live read-only memory report",
        "",
        f"- Device: `{device.path}`",
        f"- Interface: `{device.interface_number:02X}`",
        f"- VID:PID: `{device.vendor_id:04X}:{device.product_id:04X}`",
        f"- Usage verified: `{device.usage_verified}`",
        f"- F2 reads: {len(transactions)}",
        f"- Unique returned bytes: {len(memory.values)}",
        f"- Address span: `0x{memory.minimum_address:04X}–"
        f"0x{memory.maximum_address:04X}`",
        f"- Ranges/gaps: {len(memory.ranges())}/{len(memory.gaps())}",
        f"- Windows captured-baseline expectations: {windows_expectations}",
        f"- Confirmed Linux-overlay expectations: {linux_expectations}",
        f"- Unexpected differences after overlay: {len(unexpected)}",
        "",
        "The immutable Windows-captured baseline is applied first. Validated "
        "intentional Linux transitions are then overlaid by address.",
        "",
    ]
    if expected.overlay_entries:
        report.append("## Confirmed Linux state overrides")
        report.append("")
        for entry in expected.overlay_entries:
            report.append(
                f"- `{entry['address_hex']}` = `{entry['value_hex']}` "
                f"({entry['source_of_authority']}; persistence "
                f"{entry['power_cycle_persistence']})"
            )
        report.append("")
    if unexpected:
        report.append("## Unexpected differences")
        report.append("")
        for address, old, new in unexpected:
            expected_text = f"`{old:02X}`" if old is not None else "<unknown>"
            report.append(
                f"- `0x{address:04X}`: expected {expected_text}, "
                f"got `{new:02X}`"
            )
    else:
        report.append(
            "All read addresses match the Windows baseline plus confirmed "
            "Linux state overlay."
        )
    (output / "live_read_report.md").write_text(
        "\n".join(report) + "\n", encoding="utf-8"
    )


def run_live_read(
    device: HidrawDevice,
    *,
    output_root: Path,
    transport_factory: Callable[[HidrawDevice], ReadOnlyHidrawTransport] = (
        ReadOnlyHidrawTransport
    ),
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    known_state_path: Path = KNOWN_STATE_PATH,
) -> Path:
    expected = effective_expected_state(
        state_path=known_state_path, allow_missing=True
    )
    session = DeviceSession(
        device, transport_factory=transport_factory
    )
    with session:
        result = session.read_full_known_plan(clock=clock)
    output = _new_output_directory(output_root)
    _write_live_outputs(
        output,
        device=device,
        transactions=list(result.transactions),
        memory=result.memory,
        expected=expected,
    )
    return output


def safety_audit() -> list[str]:
    """Static audit of the live module's deliberately narrow surface."""

    transport_path = Path(__file__).with_name("readonly_hidraw.py")
    source = transport_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    numeric_constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    }
    forbidden_commands = {0xF1, 0xF3, 0xF5}
    found_commands = forbidden_commands & numeric_constants
    if found_commands:
        raise RuntimeError(
            "forbidden live command values found: "
            + ", ".join(f"0x{value:02X}" for value in sorted(found_commands))
        )
    forbidden_public = {"set_feature_report", "send_report", "write_memory"}
    public_methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    found = forbidden_public & public_methods
    if found:
        raise RuntimeError(f"forbidden public sender APIs found: {sorted(found)}")
    if "F2_COMMAND = 0xF2" not in source:
        raise RuntimeError("F2-only command constant is missing")
    return [
        "live module contains no F3/F1/F5 command literals",
        "no public generic feature-report or memory-write API exists",
        "only report 2/3 GET_FEATURE operations are accepted",
        "outgoing requests pass validate_f2_request before lazy device opening",
        "CLI defaults to offline fixture validation",
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.read_device_memory"
    )
    parser.add_argument(
        "--live-read", action="store_true",
        help="explicitly enable the F2-only hidraw read plan",
    )
    parser.add_argument(
        "--yes-read-only", action="store_true",
        help="skip typed READ confirmation; valid only with --live-read",
    )
    parser.add_argument(
        "--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT,
        help="parent directory for a newly created, never-overwritten read",
    )
    parser.add_argument(
        "--safety-audit", action="store_true",
        help="run the static live-transport safety audit offline",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    discovery: Callable[[], HidrawDevice] = discover_venus_hidraw,
    transport_factory: Callable[[HidrawDevice], ReadOnlyHidrawTransport] = (
        ReadOnlyHidrawTransport
    ),
    input_fn: Callable[[str], str] = input,
) -> int:
    args = _parser().parse_args(argv)
    if args.yes_read_only and not args.live_read:
        print("error: --yes-read-only requires --live-read", file=sys.stderr)
        return 2
    if args.safety_audit:
        for result in safety_audit():
            print(f"PASS: {result}")
        if not args.live_read:
            return 0
    if not args.live_read:
        summary = validate_offline_fixtures()
        print("offline validation passed; no hidraw node was discovered or opened")
        print(
            f"{summary['read_count']} reads, "
            f"{summary['returned_byte_count']} bytes, "
            f"0x{summary['minimum_address']:04X}.."
            f"0x{summary['maximum_address']:04X}, "
            f"{summary['range_count']} ranges, {summary['gap_count']} gaps"
        )
        return 0

    device = discovery()
    usage = (
        f"0x{device.usage_page:04X}/0x{device.usage:04X}"
        if device.usage_verified else "not readable from sysfs"
    )
    print(f"selected hidraw node: {device.path}")
    print(
        f"VID:PID={device.vendor_id:04X}:{device.product_id:04X} "
        f"interface={device.interface_number:02X} usage={usage}"
    )
    for warning in ownership_warnings(
        [KNOWN_STATE_PATH, args.output_root],
        sudo_user=os.environ.get("SUDO_USER"),
    ):
        print(f"WARNING: {warning}", file=sys.stderr)
    print(
        "permission check: "
        + ("read/write available" if os.access(device.path, os.R_OK | os.W_OK)
           else "read/write unavailable; root or an existing ACL/group may be required")
    )
    if not args.yes_read_only:
        confirmation = input_fn(
            "Type READ to perform the fixed 193-request F2-only read plan: "
        )
        if confirmation != "READ":
            print("confirmation did not match READ; no hidraw node was opened")
            return 2
    output = run_live_read(
        device,
        output_root=args.output_root,
        transport_factory=transport_factory,
    )
    print(f"live read complete: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
