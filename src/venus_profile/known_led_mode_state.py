"""Offline, append-only known state for the six-byte Profile 1 lighting prefix."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable

from .known_device_state import _load_live_read


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LED_MODE_STATE_PATH = (
    PROJECT_ROOT / "usb-captures" / "device-state"
    / "known_led_mode_state.json"
)
PREFIX_ADDRESS = 0x0449
PREFIX_LENGTH = 6
FULL_BRIGHTNESS_PREFIX = bytes.fromhex("ff 00 00 01 00 01")
BREATHING_PREFIX = bytes.fromhex("ff 00 00 02 05 01")
TRUE_OFF_PREFIX = bytes.fromhex("7b 2d d2 00 00 00")
CURRENT_FULL_BRIGHTNESS_PREFIX = bytes.fromhex("7b 2d d2 01 00 01")
RAINBOW_PREFIX = bytes.fromhex("7b 2d d2 02 00 03")
PREFIX_TO_NAME = {
    FULL_BRIGHTNESS_PREFIX: "Full Brightness",
    BREATHING_PREFIX: "Breathing",
    TRUE_OFF_PREFIX: "True Off",
    CURRENT_FULL_BRIGHTNESS_PREFIX: "Full Brightness",
    RAINBOW_PREFIX: "Rainbow",
}


class KnownLedModeStateError(RuntimeError):
    """The offline LED-mode state or confirming evidence is invalid."""


def _prefix_hex(prefix: bytes) -> str:
    return prefix.hex(" ")


def initial_state(timestamp: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "device": {
            "vendor_id": "04d9",
            "product_id": "fc55",
            "interface_number": "02",
            "usage_page": "0xFFA0",
            "usage": 1,
        },
        "namespace": "profile-1-six-byte-lighting-prefix",
        "address_start": PREFIX_ADDRESS,
        "address_start_hex": "0x0449",
        "address_end": PREFIX_ADDRESS + PREFIX_LENGTH - 1,
        "address_end_hex": "0x044E",
        "length": PREFIX_LENGTH,
        "current_prefix": _prefix_hex(FULL_BRIGHTNESS_PREFIX),
        "current_semantic_mode": "Full Brightness",
        "initial_evidence": {
            "source_of_authority": (
                "controlled-windows-transition-and-native-post-read"
            ),
            "live_read_directory": str(
                PROJECT_ROOT / "usb-captures" / "live-reads"
                / "venus_read_20260727T101703.627495Z"
            ),
            "prefix": _prefix_hex(FULL_BRIGHTNESS_PREFIX),
            "semantic_mode": "Full Brightness",
            "persistence_status": "confirmed-observed-state",
        },
        "history": [],
        "updated_at": timestamp,
    }


def validate_led_mode_state(document: object) -> dict[str, object]:
    if not isinstance(document, dict):
        raise KnownLedModeStateError("LED-mode state must be a JSON object")
    try:
        device = document["device"]
        identity = (
            str(device["vendor_id"]).lower(),
            str(device["product_id"]).lower(),
            str(device["interface_number"]).lower(),
            device["usage_page"],
            device["usage"],
        )
        start = int(document["address_start"])
        end = int(document["address_end"])
        length = int(document["length"])
        current = bytes.fromhex(str(document["current_prefix"]))
        current_name = str(document["current_semantic_mode"])
        history = document["history"]
    except (KeyError, TypeError, ValueError) as exc:
        raise KnownLedModeStateError("malformed LED-mode state") from exc
    if identity != ("04d9", "fc55", "02", "0xFFA0", 1):
        raise KnownLedModeStateError(
            "LED-mode state must identify 04d9:fc55 interface 02 FFA0:0001"
        )
    if (start, end, length) != (0x0449, 0x044E, 6):
        raise KnownLedModeStateError("LED-mode state has wrong address range")
    if current not in PREFIX_TO_NAME or PREFIX_TO_NAME[current] != current_name:
        raise KnownLedModeStateError("current LED-mode prefix/name conflict")
    if not isinstance(history, list):
        raise KnownLedModeStateError("LED-mode history must be a list")
    previous = None
    pending = 0
    for index, item in enumerate(history):
        try:
            old = bytes.fromhex(str(item["previous_prefix"]))
            new = bytes.fromhex(str(item["confirmed_new_prefix"]))
            old_name = str(item["previous_semantic_mode"])
            new_name = str(item["new_semantic_mode"])
            status = item["power_cycle_persistence"]
            read_back = item["immediate_read_back_confirmed"]
        except (KeyError, TypeError, ValueError) as exc:
            raise KnownLedModeStateError(
                f"malformed LED-mode history entry {index}"
            ) from exc
        if (
            old not in PREFIX_TO_NAME
            or new not in PREFIX_TO_NAME
            or old_name != PREFIX_TO_NAME[old]
            or new_name != PREFIX_TO_NAME[new]
            or old == new
        ):
            raise KnownLedModeStateError(
                f"invalid LED-mode transition at history entry {index}"
            )
        if previous is not None and old != previous and not item.get(
            "external_state_discontinuity"
        ):
            raise KnownLedModeStateError("conflicting LED-mode history chain")
        if status not in {"pending", "confirmed"} or read_back is not True:
            raise KnownLedModeStateError(
                f"invalid persistence/read-back state at history entry {index}"
            )
        pending += status == "pending"
        previous = new
    if pending > 1:
        raise KnownLedModeStateError("multiple pending LED-mode transitions")
    if history and bytes.fromhex(history[-1]["confirmed_new_prefix"]) != current and not document.get(
        "current_observation"
    ):
        raise KnownLedModeStateError("current prefix conflicts with history")
    return document


def load_led_mode_state(
    path: Path = LED_MODE_STATE_PATH,
    *,
    allow_missing: bool = False,
) -> dict[str, object] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if allow_missing:
            return None
        raise KnownLedModeStateError(f"LED-mode state file is missing: {path}")
    except (OSError, json.JSONDecodeError) as exc:
        raise KnownLedModeStateError(
            f"could not parse LED-mode state file: {path}"
        ) from exc
    return validate_led_mode_state(document)


def atomic_write_led_mode_state(path: Path, document: dict[str, object]) -> None:
    validate_led_mode_state(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(document, indent=2) + "\n").encode()
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def record_led_mode_transition(
    *,
    state_path: Path,
    previous_prefix: bytes,
    new_prefix: bytes,
    operation_timestamp: str,
    result_directory: Path,
    updated_at: str,
) -> dict[str, object]:
    state = load_led_mode_state(state_path, allow_missing=True)
    if state is None:
        state = initial_state(updated_at)
    state = json.loads(json.dumps(state))
    if any(
        item["power_cycle_persistence"] == "pending"
        for item in state["history"]
    ):
        raise KnownLedModeStateError(
            "a prior LED-mode transition is pending persistence confirmation"
        )
    current = bytes.fromhex(state["current_prefix"])
    if current != previous_prefix:
        raise KnownLedModeStateError(
            f"known LED-mode prefix is {_prefix_hex(current)}, not expected "
            f"{_prefix_hex(previous_prefix)}"
        )
    if previous_prefix not in PREFIX_TO_NAME or new_prefix not in PREFIX_TO_NAME:
        raise KnownLedModeStateError("unsupported LED-mode prefix")
    discontinuity = bool(state.get("history")) and bytes.fromhex(
        state["history"][-1]["confirmed_new_prefix"]
    ) != previous_prefix
    transition = {
        "transition_id": (
            f"{operation_timestamp}|0x0449-0x044E|"
            f"{previous_prefix.hex()}-to-{new_prefix.hex()}"
        ),
        "address_start": PREFIX_ADDRESS,
        "address_start_hex": "0x0449",
        "address_end": PREFIX_ADDRESS + PREFIX_LENGTH - 1,
        "address_end_hex": "0x044E",
        "length": PREFIX_LENGTH,
        "previous_prefix": _prefix_hex(previous_prefix),
        "confirmed_new_prefix": _prefix_hex(new_prefix),
        "previous_semantic_mode": PREFIX_TO_NAME[previous_prefix],
        "new_semantic_mode": PREFIX_TO_NAME[new_prefix],
        "operation_timestamp": operation_timestamp,
        "result_directory": str(result_directory),
        "immediate_read_back_confirmed": True,
        "power_cycle_persistence": "pending",
        "persistence_confirmed_at": None,
        "source_of_authority": "guarded-linux-led-mode-write",
        "latest_confirming_live_read_directory": None,
        "external_state_discontinuity": discontinuity,
    }
    state["history"].append(transition)
    state["current_prefix"] = _prefix_hex(new_prefix)
    state["current_semantic_mode"] = PREFIX_TO_NAME[new_prefix]
    state.pop("current_observation", None)
    state["updated_at"] = updated_at
    atomic_write_led_mode_state(state_path, state)
    return state


def confirm_led_mode_persistence(
    live_read_directory: Path,
    *,
    state_path: Path = LED_MODE_STATE_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    state = load_led_mode_state(state_path)
    pending = [
        item for item in state["history"]
        if item["power_cycle_persistence"] == "pending"
    ]
    if len(pending) != 1:
        raise KnownLedModeStateError(
            "persistence confirmation requires exactly one pending LED-mode "
            f"transition; found {len(pending)}"
        )
    _, values = _load_live_read(live_read_directory)
    if any(
        PREFIX_ADDRESS + offset not in values
        for offset in range(PREFIX_LENGTH)
    ):
        raise KnownLedModeStateError(
            "live read does not cover the complete 0x0449-0x044E prefix"
        )
    actual = bytes(
        values[PREFIX_ADDRESS + offset]
        for offset in range(PREFIX_LENGTH)
    )
    expected = bytes.fromhex(pending[0]["confirmed_new_prefix"])
    if actual != expected:
        raise KnownLedModeStateError(
            f"live read prefix {_prefix_hex(actual)} does not confirm "
            f"{_prefix_hex(expected)}"
        )
    now = clock().astimezone(timezone.utc).isoformat()
    state = json.loads(json.dumps(state))
    for item in state["history"]:
        if item["transition_id"] == pending[0]["transition_id"]:
            item["power_cycle_persistence"] = "confirmed"
            item["persistence_confirmed_at"] = now
            item["latest_confirming_live_read_directory"] = str(
                live_read_directory
            )
    state["updated_at"] = now
    atomic_write_led_mode_state(state_path, state)
    return state


def correct_led_mode_persistence(
    *,
    transition_id: str,
    superseded_live_read_directory: Path,
    genuine_live_read_directory: Path,
    state_path: Path = LED_MODE_STATE_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    """Repair a mistaken persistence confirmation using later offline evidence.

    Normal confirmation intentionally refuses already-confirmed transitions.
    This explicit correction path preserves the superseded evidence and only
    changes the named transition after validating a complete post-power-cycle
    read with the exact device identity and target prefix.
    """
    state = load_led_mode_state(state_path)
    matches = [item for item in state["history"]
               if item.get("transition_id") == transition_id]
    if len(matches) != 1:
        raise KnownLedModeStateError(
            "persistence correction requires exactly one matching transition"
        )
    transition = matches[0]
    if transition.get("power_cycle_persistence") != "confirmed":
        raise KnownLedModeStateError(
            "persistence correction expects an already-confirmed transition"
        )
    old_directory = Path(
        transition.get("latest_confirming_live_read_directory") or ""
    )
    if str(old_directory) != str(superseded_live_read_directory):
        raise KnownLedModeStateError(
            "superseded evidence path does not match the recorded confirmation"
        )
    old_transactions, old_values = _load_live_read(old_directory)
    new_transactions, new_values = _load_live_read(genuine_live_read_directory)
    for label, transactions in (("superseded", old_transactions),
                                ("genuine", new_transactions)):
        device = transactions.get("device", {})
        if (str(device.get("vendor_id", "")).lower() != "04d9"
                or str(device.get("product_id", "")).lower() != "fc55"
                or str(device.get("interface_number", "")).lower() != "02"
                or device.get("usage_page") != "0xFFA0"
                or device.get("usage") != 1):
            raise KnownLedModeStateError(
                f"{label} persistence evidence has wrong device/interface/usage"
            )
    expected = bytes.fromhex(str(transition["confirmed_new_prefix"]))
    actual_old = bytes(old_values[PREFIX_ADDRESS + offset]
                       for offset in range(PREFIX_LENGTH))
    actual_new = bytes(new_values[PREFIX_ADDRESS + offset]
                       for offset in range(PREFIX_LENGTH))
    if actual_old != expected or actual_new != expected:
        raise KnownLedModeStateError(
            "persistence correction evidence does not contain the exact target prefix"
        )
    now = clock().astimezone(timezone.utc).isoformat()
    evidence_timestamp = now
    match = re.search(
        r"venus_read_(\d{8}T\d{6}\.\d+)Z$",
        genuine_live_read_directory.name,
    )
    if match:
        raw = match.group(1)
        evidence_timestamp = (
            datetime.strptime(raw, "%Y%m%dT%H%M%S.%f")
            .replace(tzinfo=timezone.utc)
            .isoformat()
        )
    repaired = json.loads(json.dumps(state))
    repaired_transition = next(item for item in repaired["history"]
                               if item["transition_id"] == transition_id)
    previous_confirmation = {
        "directory": str(old_directory),
        "status": "post-write-full-read-not-power-cycle",
        "superseded_at": now,
        "note": "Recorded before a genuine unplug/replug; retained as audit evidence only.",
    }
    repaired_transition["superseded_persistence_evidence"] = previous_confirmation
    repaired_transition["persistence_correction"] = {
        "corrected_at": now,
        "reason": "initial confirmation occurred without unplug/replug",
        "genuine_power_cycle_read_directory": str(genuine_live_read_directory),
        "source_of_authority": "offline-evidence-correction",
    }
    repaired_transition["power_cycle_persistence"] = "confirmed"
    repaired_transition["persistence_confirmed_at"] = evidence_timestamp
    repaired_transition["latest_confirming_live_read_directory"] = str(
        genuine_live_read_directory
    )
    audit = repaired.setdefault("audit_corrections", [])
    audit.append({
        "correction_type": "rainbow-to-full-persistence-evidence",
        "transition_id": transition_id,
        "superseded_read": str(old_directory),
        "genuine_power_cycle_read": str(genuine_live_read_directory),
        "timestamp": now,
        "note": "The first read was post-write but pre-power-cycle; the later read supersedes it.",
    })
    repaired["updated_at"] = now
    atomic_write_led_mode_state(state_path, repaired)
    return repaired


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.known_led_mode_state"
    )
    parser.add_argument("--state-file", type=Path, default=LED_MODE_STATE_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show")
    commands.add_parser("validate")
    confirm = commands.add_parser("confirm-persistence")
    confirm.add_argument("live_read_directory", type=Path)
    correct = commands.add_parser("correct-persistence")
    correct.add_argument("transition_id")
    correct.add_argument("superseded_live_read_directory", type=Path)
    correct.add_argument("genuine_live_read_directory", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "show":
        print(json.dumps(load_led_mode_state(args.state_file), indent=2))
    elif args.command == "validate":
        state = load_led_mode_state(args.state_file)
        print(
            f"valid LED-mode state: {len(state['history'])} transition(s); "
            f"current={state['current_semantic_mode']}"
        )
    elif args.command == "confirm-persistence":
        print(json.dumps(confirm_led_mode_persistence(
            args.live_read_directory, state_path=args.state_file
        ), indent=2))
    elif args.command == "correct-persistence":
        print(json.dumps(correct_led_mode_persistence(
            transition_id=args.transition_id,
            superseded_live_read_directory=args.superseded_live_read_directory,
            genuine_live_read_directory=args.genuine_live_read_directory,
            state_path=args.state_file,
        ), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
