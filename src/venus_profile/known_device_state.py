"""Offline known-device-state overlay for confirmed native Linux changes.

The Windows capture-derived memory remains immutable historical evidence.
This module validates and overlays explicit, history-backed Linux transitions
without accessing sysfs, hidraw, or any USB/HID API.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FACTORY_MEMORY_MAP = (
    PROJECT_ROOT / "usb-captures" / "analysis" / "factory_memory_map.json"
)
LATEST_WINDOWS_MACRO_SLOT = (
    PROJECT_ROOT / "usb-captures" / "analysis"
    / "macro_slot1_a_press_release" / "macro_slot1.bin"
)
STATE_DIRECTORY = PROJECT_ROOT / "usb-captures" / "device-state"
KNOWN_STATE_PATH = STATE_DIRECTORY / "known_device_state.json"

TARGET_VENDOR = "04d9"
TARGET_PRODUCT = "fc55"
TARGET_INTERFACE = "02"


class KnownDeviceStateError(RuntimeError):
    """Known-state data or offline confirming evidence failed validation."""


@dataclass(frozen=True)
class EffectiveExpectedState:
    values: dict[int, int]
    sources: dict[int, str]
    overlay_entries: tuple[dict[str, object], ...]


def windows_captured_baseline(
    *,
    factory_map_path: Path = FACTORY_MEMORY_MAP,
    macro_slot_path: Path = LATEST_WINDOWS_MACRO_SLOT,
) -> dict[int, int]:
    """Build the latest immutable Windows-captured state."""

    document = json.loads(factory_map_path.read_text(encoding="utf-8"))
    try:
        expected = {
            int(address, 16): int(value)
            for address, value in document["bytes"].items()
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise KnownDeviceStateError("malformed Windows factory memory map") from exc
    # Controlled Windows writes captured after the untouched factory read.
    expected[0x044F] = 0x03
    expected.update(dict(zip(
        range(0x009A, 0x009E), bytes.fromhex("91 00 01 00")
    )))
    slot = macro_slot_path.read_bytes()
    if len(slot) != 200:
        raise KnownDeviceStateError(
            "captured Windows macro-slot fixture must be exactly 200 bytes"
        )
    expected.update(dict(zip(range(0x0478, 0x0540), slot)))
    return expected


def _device_document() -> dict[str, str]:
    return {
        "vendor_id": TARGET_VENDOR,
        "product_id": TARGET_PRODUCT,
        "interface_number": TARGET_INTERFACE,
    }


def empty_state(timestamp: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "device": _device_document(),
        "historical_baseline": {
            "authority": "windows-captured-state",
            "factory_memory_map": str(FACTORY_MEMORY_MAP),
            "latest_windows_macro_slot": str(LATEST_WINDOWS_MACRO_SLOT),
            "mutable": False,
        },
        "current_overrides": {},
        "history": [],
        "updated_at": timestamp,
    }


def _normalise_device(device: object) -> tuple[str, str, str]:
    if not isinstance(device, dict):
        raise KnownDeviceStateError("known-state device must be an object")
    try:
        vendor = str(device["vendor_id"]).lower()
        product = str(device["product_id"]).lower()
        interface = str(device["interface_number"]).lower()
    except KeyError as exc:
        raise KnownDeviceStateError("known-state device identity is incomplete") from exc
    if (
        vendor != TARGET_VENDOR
        or product != TARGET_PRODUCT
        or interface != TARGET_INTERFACE
    ):
        raise KnownDeviceStateError(
            "known-state VID:PID/interface is not 04d9:fc55 interface 02"
        )
    return vendor, product, interface


def validate_state_document(
    document: object,
    *,
    baseline: dict[int, int] | None = None,
) -> dict[str, object]:
    if not isinstance(document, dict):
        raise KnownDeviceStateError("known-state root must be an object")
    if document.get("schema_version") != 1:
        raise KnownDeviceStateError("unsupported known-state schema version")
    _normalise_device(document.get("device"))
    historical = document.get("historical_baseline")
    if (
        not isinstance(historical, dict)
        or historical.get("authority") != "windows-captured-state"
        or historical.get("mutable") is not False
    ):
        raise KnownDeviceStateError(
            "known-state historical baseline declaration is malformed"
        )
    history = document.get("history")
    overrides = document.get("current_overrides")
    if not isinstance(history, list) or not isinstance(overrides, dict):
        raise KnownDeviceStateError(
            "known-state history/current_overrides have invalid types"
        )
    baseline = baseline if baseline is not None else windows_captured_baseline()
    derived: dict[int, int] = {}
    identifiers: set[str] = set()
    for index, transition in enumerate(history):
        if not isinstance(transition, dict):
            raise KnownDeviceStateError(f"history entry {index} is not an object")
        try:
            identifier = str(transition["transition_id"])
            address = int(transition["address"])
            previous = int(transition["previous_value"])
            current = int(transition["confirmed_new_value"])
            operation_timestamp = str(transition["operation_timestamp"])
            result_directory = str(transition["result_directory"])
            immediate = transition["immediate_read_back_confirmed"]
            persistence = str(transition["power_cycle_persistence"])
            authority = str(transition["source_of_authority"])
        except (KeyError, TypeError, ValueError) as exc:
            raise KnownDeviceStateError(
                f"history entry {index} is incomplete or malformed"
            ) from exc
        if not identifier or identifier in identifiers:
            raise KnownDeviceStateError("transition IDs must be nonempty and unique")
        identifiers.add(identifier)
        if not 0 <= address <= 0xFFFF or not 0 <= previous <= 0xFF or not 0 <= current <= 0xFF:
            raise KnownDeviceStateError(
                f"history entry {index} contains an out-of-range address/value"
            )
        expected_previous = derived.get(address, baseline.get(address))
        if expected_previous is None or previous != expected_previous:
            raise KnownDeviceStateError(
                f"conflicting history at 0x{address:04X}: previous "
                f"{previous:02X} does not match expected {expected_previous!r}"
            )
        if previous == current:
            raise KnownDeviceStateError("state transitions must change the value")
        if immediate is not True:
            raise KnownDeviceStateError(
                "only exact immediate-read-back-confirmed transitions are valid"
            )
        if persistence not in {"pending", "confirmed"}:
            raise KnownDeviceStateError(
                "power_cycle_persistence must be pending or confirmed"
            )
        confirming = transition.get("latest_confirming_live_read_directory")
        if persistence == "confirmed" and not isinstance(confirming, str):
            raise KnownDeviceStateError(
                "confirmed persistence requires a confirming live-read directory"
            )
        if persistence == "pending" and confirming is not None:
            raise KnownDeviceStateError(
                "pending persistence cannot have confirming live-read evidence"
            )
        if authority != "guarded-linux-write":
            raise KnownDeviceStateError("unsupported state authority")
        if not operation_timestamp or not result_directory:
            raise KnownDeviceStateError("transition paths/timestamps cannot be empty")
        derived[address] = current
    parsed_overrides: dict[int, int] = {}
    for address_text, value in overrides.items():
        try:
            address = int(str(address_text), 16)
            parsed_value = int(value)
        except (TypeError, ValueError) as exc:
            raise KnownDeviceStateError("malformed current override") from exc
        if address in parsed_overrides:
            raise KnownDeviceStateError("duplicate current override address")
        if not 0 <= parsed_value <= 0xFF:
            raise KnownDeviceStateError("current override value is out of range")
        parsed_overrides[address] = parsed_value
    if parsed_overrides != derived:
        raise KnownDeviceStateError(
            "current_overrides conflict with transition history"
        )
    if not isinstance(document.get("updated_at"), str):
        raise KnownDeviceStateError("known-state updated_at is missing")
    return document


def load_known_state(
    path: Path = KNOWN_STATE_PATH,
    *,
    allow_missing: bool = False,
) -> dict[str, object] | None:
    if not path.exists():
        if allow_missing:
            return None
        raise KnownDeviceStateError(f"known-state file does not exist: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnownDeviceStateError(f"could not parse known-state file: {path}") from exc
    return validate_state_document(document)


def atomic_write_state(path: Path, document: dict[str, object]) -> None:
    validate_state_document(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def effective_expected_state(
    *,
    state_path: Path = KNOWN_STATE_PATH,
    allow_missing: bool = True,
) -> EffectiveExpectedState:
    baseline = windows_captured_baseline()
    values = dict(baseline)
    sources = {address: "windows-captured-baseline" for address in values}
    state = load_known_state(state_path, allow_missing=allow_missing)
    entries: list[dict[str, object]] = []
    if state is not None:
        for address_text, value in state["current_overrides"].items():
            address = int(address_text, 16)
            values[address] = int(value)
            sources[address] = "confirmed-linux-state-overlay"
            latest = next(
                item for item in reversed(state["history"])
                if int(item["address"]) == address
            )
            entries.append({
                "address": address,
                "address_hex": f"0x{address:04X}",
                "value": int(value),
                "value_hex": f"{int(value):02X}",
                "transition_id": latest["transition_id"],
                "power_cycle_persistence": latest["power_cycle_persistence"],
                "source_of_authority": latest["source_of_authority"],
            })
    return EffectiveExpectedState(values, sources, tuple(entries))


def record_confirmed_transition(
    *,
    state_path: Path,
    address: int,
    previous_value: int,
    confirmed_new_value: int,
    operation_timestamp: str,
    result_directory: Path,
    updated_at: str,
) -> dict[str, object]:
    state = load_known_state(state_path, allow_missing=True)
    if state is None:
        state = empty_state(updated_at)
    state = json.loads(json.dumps(state))
    if any(
        transition["power_cycle_persistence"] == "pending"
        for transition in state["history"]
    ):
        raise KnownDeviceStateError(
            "a prior transition is still pending power-cycle confirmation"
        )
    current = effective_expected_state(
        state_path=state_path, allow_missing=True
    ).values if state_path.exists() else windows_captured_baseline()
    if current.get(address) != previous_value:
        raise KnownDeviceStateError(
            f"known state at 0x{address:04X} is "
            f"{current.get(address)!r}, not expected {previous_value}"
        )
    transition = {
        "transition_id": (
            f"{operation_timestamp}|0x{address:04X}|"
            f"{previous_value:02X}-to-{confirmed_new_value:02X}"
        ),
        "address": address,
        "address_hex": f"0x{address:04X}",
        "previous_value": previous_value,
        "previous_value_hex": f"{previous_value:02X}",
        "confirmed_new_value": confirmed_new_value,
        "confirmed_new_value_hex": f"{confirmed_new_value:02X}",
        "operation_timestamp": operation_timestamp,
        "result_directory": str(result_directory),
        "immediate_read_back_confirmed": True,
        "power_cycle_persistence": "pending",
        "persistence_confirmed_at": None,
        "source_of_authority": "guarded-linux-write",
        "latest_confirming_live_read_directory": None,
    }
    state["history"].append(transition)
    state["current_overrides"][f"0x{address:04X}"] = confirmed_new_value
    state["updated_at"] = updated_at
    atomic_write_state(state_path, state)
    return state


def _load_live_read(directory: Path) -> tuple[dict[str, object], dict[int, int]]:
    try:
        transactions = json.loads(
            (directory / "live_transactions.json").read_text(encoding="utf-8")
        )
        memory_map = json.loads(
            (directory / "live_memory_map.json").read_text(encoding="utf-8")
        )
        device = transactions["device"]
        values = {
            int(address, 16): int(value)
            for address, value in memory_map["bytes"].items()
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise KnownDeviceStateError(
            f"invalid live-read result directory: {directory}"
        ) from exc
    _normalise_device(device)
    if transactions.get("mode") != "live-read-only":
        raise KnownDeviceStateError("evidence is not a live-read-only result")
    if transactions.get("read_count") != 193:
        raise KnownDeviceStateError(
            "persistence evidence must contain the complete 193-read plan"
        )
    if len(values) != 4604 or memory_map.get("returned_byte_count") != 4604:
        raise KnownDeviceStateError(
            "persistence evidence must contain all 4,604 factory-plan bytes"
        )
    return transactions, values


def compare_live_read(
    directory: Path,
    *,
    state_path: Path = KNOWN_STATE_PATH,
) -> dict[str, object]:
    transactions, actual = _load_live_read(directory)
    expected = effective_expected_state(
        state_path=state_path, allow_missing=False
    )
    unexpected = [
        {
            "address": address,
            "address_hex": f"0x{address:04X}",
            "expected": expected.values.get(address),
            "actual": value,
            "expectation_source": expected.sources.get(address),
        }
        for address, value in sorted(actual.items())
        if expected.values.get(address) != value
    ]
    overrides_read = [
        {
            **entry,
            "actual": actual.get(int(entry["address"])),
            "matched": actual.get(int(entry["address"])) == int(entry["value"]),
        }
        for entry in expected.overlay_entries
        if int(entry["address"]) in actual
    ]
    return {
        "live_read_directory": str(directory),
        "device": transactions["device"],
        "read_count": transactions.get("read_count"),
        "returned_byte_count": len(actual),
        "windows_baseline_expectation_count": sum(
            expected.sources.get(address) == "windows-captured-baseline"
            for address in actual
        ),
        "linux_overlay_expectation_count": sum(
            expected.sources.get(address) == "confirmed-linux-state-overlay"
            for address in actual
        ),
        "confirmed_linux_overrides_read": overrides_read,
        "unexpected_difference_count": len(unexpected),
        "unexpected_differences": unexpected,
    }


def confirm_pending_persistence(
    live_read_directory: Path,
    *,
    state_path: Path = KNOWN_STATE_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    state = load_known_state(state_path, allow_missing=False)
    pending = [
        item for item in state["history"]
        if item["power_cycle_persistence"] == "pending"
    ]
    if len(pending) != 1:
        raise KnownDeviceStateError(
            f"persistence confirmation requires exactly one pending transition; "
            f"found {len(pending)}"
        )
    comparison = compare_live_read(
        live_read_directory, state_path=state_path
    )
    transition = pending[0]
    address = int(transition["address"])
    matching = [
        entry for entry in comparison["confirmed_linux_overrides_read"]
        if int(entry["address"]) == address
    ]
    if not matching or not matching[0]["matched"]:
        raise KnownDeviceStateError(
            f"live read does not confirm 0x{address:04X}="
            f"{int(transition['confirmed_new_value']):02X}"
        )
    if comparison["unexpected_difference_count"] != 0:
        raise KnownDeviceStateError(
            "live read has unexpected differences after applying pending state"
        )
    now = clock().astimezone(timezone.utc).isoformat()
    state = json.loads(json.dumps(state))
    for item in state["history"]:
        if item["transition_id"] == transition["transition_id"]:
            item["power_cycle_persistence"] = "confirmed"
            item["persistence_confirmed_at"] = now
            item["latest_confirming_live_read_directory"] = str(
                live_read_directory
            )
    state["updated_at"] = now
    atomic_write_state(state_path, state)
    return state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.known_device_state"
    )
    parser.add_argument(
        "--state-file", type=Path, default=KNOWN_STATE_PATH
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("show")
    subparsers.add_parser("validate")
    compare = subparsers.add_parser("compare-live-read")
    compare.add_argument("live_read_directory", type=Path)
    confirm = subparsers.add_parser("confirm-persistence")
    confirm.add_argument("live_read_directory", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "show":
        state = load_known_state(args.state_file)
        print(json.dumps(state, indent=2))
    elif args.command == "validate":
        state = load_known_state(args.state_file)
        print(
            f"valid known state: {len(state['history'])} transition(s), "
            f"{len(state['current_overrides'])} active override(s)"
        )
    elif args.command == "compare-live-read":
        comparison = compare_live_read(
            args.live_read_directory, state_path=args.state_file
        )
        print(json.dumps(comparison, indent=2))
        return int(comparison["unexpected_difference_count"] != 0)
    elif args.command == "confirm-persistence":
        state = confirm_pending_persistence(
            args.live_read_directory, state_path=args.state_file
        )
        print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
