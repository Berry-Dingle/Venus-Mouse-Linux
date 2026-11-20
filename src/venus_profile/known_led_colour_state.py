"""Offline append-only state for the guarded Profile 1 LED colour prefixes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Callable

from .known_device_state import _load_live_read


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LED_COLOUR_STATE_PATH = (
    PROJECT_ROOT / "usb-captures" / "device-state"
    / "known_led_colour_state.json"
)
COLOUR_ADDRESS = 0x0449
COLOUR_LENGTH = 3
PREFIX_LENGTH = 6
LIGHTING_SUFFIX = bytes.fromhex("01 00 01")
OFFICIAL_RED_RGB = bytes.fromhex("ff 00 00")
OFFICIAL_BLUE_RGB = bytes.fromhex("0e 0f ff")
OFFICIAL_GREEN_RGB = bytes.fromhex("00 ff 01")
OFFICIAL_RED_PREFIX = OFFICIAL_RED_RGB + LIGHTING_SUFFIX
OFFICIAL_BLUE_PREFIX = OFFICIAL_BLUE_RGB + LIGHTING_SUFFIX
OFFICIAL_GREEN_PREFIX = OFFICIAL_GREEN_RGB + LIGHTING_SUFFIX
PREFIX_TO_NAME = {
    OFFICIAL_RED_PREFIX: "Official Red",
    OFFICIAL_BLUE_PREFIX: "Official Blue",
    OFFICIAL_GREEN_PREFIX: "Official Green",
}


class KnownLedColourStateError(RuntimeError):
    """The saved colour state or offline persistence evidence is invalid."""


def _hex(value: bytes) -> str:
    return value.hex(" ")


def initial_state(timestamp: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "namespace": "profile-1-guarded-led-colour",
        "device": {
            "vendor_id": "04d9", "product_id": "fc55",
            "interface_number": "02", "usage_page": "0xFFA0", "usage": 1,
        },
        "colour_address_start": COLOUR_ADDRESS,
        "colour_address_start_hex": "0x0449",
        "colour_address_end": COLOUR_ADDRESS + COLOUR_LENGTH - 1,
        "colour_address_end_hex": "0x044B",
        "colour_length": COLOUR_LENGTH,
        "prefix_address_start": COLOUR_ADDRESS,
        "prefix_address_end": COLOUR_ADDRESS + PREFIX_LENGTH - 1,
        "prefix_length": PREFIX_LENGTH,
        "current_prefix": _hex(OFFICIAL_BLUE_PREFIX),
        "current_semantic_colour": "Official Blue",
        "current_rgb": [14, 15, 255],
        "initial_evidence": {
            "source_of_authority": (
                "controlled-windows-colour-transition-and-native-post-read"
            ),
            "live_read_directory": str(
                PROJECT_ROOT / "usb-captures" / "live-reads"
                / "venus_read_20260727T112130.097148Z"
            ),
            "prefix": _hex(OFFICIAL_BLUE_PREFIX),
            "semantic_colour": "Official Blue",
        },
        "history": [],
        "updated_at": timestamp,
    }


def validate_colour_state(document: object) -> dict[str, object]:
    if not isinstance(document, dict):
        raise KnownLedColourStateError("colour state must be a JSON object")
    try:
        device = document["device"]
        identity = (
            str(device["vendor_id"]).lower(),
            str(device["product_id"]).lower(),
            str(device["interface_number"]).lower(),
            device["usage_page"], device["usage"],
        )
        colour_range = (
            int(document["colour_address_start"]),
            int(document["colour_address_end"]),
            int(document["colour_length"]),
        )
        prefix_range = (
            int(document["prefix_address_start"]),
            int(document["prefix_address_end"]),
            int(document["prefix_length"]),
        )
        current = bytes.fromhex(str(document["current_prefix"]))
        current_name = str(document["current_semantic_colour"])
        current_rgb = list(document["current_rgb"])
        history = document["history"]
    except (KeyError, TypeError, ValueError) as exc:
        raise KnownLedColourStateError("malformed colour state") from exc
    if identity != ("04d9", "fc55", "02", "0xFFA0", 1):
        raise KnownLedColourStateError(
            "colour state must identify 04d9:fc55 interface 02 FFA0:0001"
        )
    if colour_range != (0x0449, 0x044B, 3):
        raise KnownLedColourStateError("wrong colour address range")
    if prefix_range != (0x0449, 0x044E, 6):
        raise KnownLedColourStateError("wrong lighting-prefix address range")
    if (
        current not in PREFIX_TO_NAME
        or current_name != PREFIX_TO_NAME[current]
        or current_rgb != list(current[:3])
        or current[3:] != LIGHTING_SUFFIX
    ):
        raise KnownLedColourStateError("current colour prefix/name/RGB conflict")
    if not isinstance(history, list):
        raise KnownLedColourStateError("colour history must be a list")
    chain = None
    pending = 0
    for index, item in enumerate(history):
        try:
            old = bytes.fromhex(item["previous_prefix"])
            new = bytes.fromhex(item["confirmed_new_prefix"])
            old_rgb = list(item["previous_rgb"])
            new_rgb = list(item["new_rgb"])
            status = item["power_cycle_persistence"]
        except (KeyError, TypeError, ValueError) as exc:
            raise KnownLedColourStateError(
                f"malformed colour history entry {index}"
            ) from exc
        if (
            old not in PREFIX_TO_NAME or new not in PREFIX_TO_NAME
            or old == new or old[3:] != LIGHTING_SUFFIX
            or new[3:] != LIGHTING_SUFFIX
            or old_rgb != list(old[:3]) or new_rgb != list(new[:3])
            or item["previous_semantic_colour"] != PREFIX_TO_NAME[old]
            or item["new_semantic_colour"] != PREFIX_TO_NAME[new]
            or status not in {"pending", "confirmed"}
        ):
            raise KnownLedColourStateError(
                f"invalid colour transition at history entry {index}"
            )
        immediate = item["immediate_read_back_confirmed"]
        authority = item.get("source_of_authority")
        if immediate is not True and not (
            immediate is False
            and authority == "controlled-windows-colour-transition"
            and status == "confirmed"
        ):
            raise KnownLedColourStateError(
                f"invalid read-back authority at history entry {index}"
            )
        if chain is not None and old != chain:
            raise KnownLedColourStateError("conflicting colour history chain")
        chain = new
        pending += status == "pending"
    if pending > 1:
        raise KnownLedColourStateError("multiple pending colour transitions")
    if history and bytes.fromhex(history[-1]["confirmed_new_prefix"]) != current:
        raise KnownLedColourStateError("current colour conflicts with history")
    return document


def load_colour_state(
    path: Path = LED_COLOUR_STATE_PATH, *, allow_missing: bool = False
) -> dict[str, object] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if allow_missing:
            return None
        raise KnownLedColourStateError(f"colour state file is missing: {path}")
    except (OSError, json.JSONDecodeError) as exc:
        raise KnownLedColourStateError(
            f"could not parse colour state file: {path}"
        ) from exc
    return validate_colour_state(document)


def atomic_write_colour_state(path: Path, document: dict[str, object]) -> None:
    validate_colour_state(document)
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


def record_colour_transition(
    *, state_path: Path, previous_prefix: bytes, new_prefix: bytes,
    operation_timestamp: str, result_directory: Path, updated_at: str,
) -> dict[str, object]:
    state = load_colour_state(state_path, allow_missing=True)
    if state is None:
        state = initial_state(updated_at)
    state = json.loads(json.dumps(state))
    if any(
        item["power_cycle_persistence"] == "pending"
        for item in state["history"]
    ):
        raise KnownLedColourStateError(
            "a prior colour transition is pending persistence confirmation"
        )
    current = bytes.fromhex(state["current_prefix"])
    if current != previous_prefix:
        raise KnownLedColourStateError(
            f"known colour prefix is {_hex(current)}, not {_hex(previous_prefix)}"
        )
    if previous_prefix not in PREFIX_TO_NAME or new_prefix not in PREFIX_TO_NAME:
        raise KnownLedColourStateError("unsupported guarded colour prefix")
    transition = {
        "transition_id": (
            f"{operation_timestamp}|0x0449-0x044E|"
            f"{previous_prefix.hex()}-to-{new_prefix.hex()}"
        ),
        "colour_address_start": COLOUR_ADDRESS,
        "colour_address_end": COLOUR_ADDRESS + COLOUR_LENGTH - 1,
        "full_prefix_address_start": COLOUR_ADDRESS,
        "full_prefix_address_end": COLOUR_ADDRESS + PREFIX_LENGTH - 1,
        "previous_prefix": _hex(previous_prefix),
        "confirmed_new_prefix": _hex(new_prefix),
        "previous_semantic_colour": PREFIX_TO_NAME[previous_prefix],
        "new_semantic_colour": PREFIX_TO_NAME[new_prefix],
        "previous_rgb": list(previous_prefix[:3]),
        "new_rgb": list(new_prefix[:3]),
        "operation_timestamp": operation_timestamp,
        "result_directory": str(result_directory),
        "immediate_read_back_confirmed": True,
        "power_cycle_persistence": "pending",
        "persistence_confirmed_at": None,
        "source_of_authority": "guarded-linux-led-colour-write",
        "latest_confirming_live_read_directory": None,
    }
    state["history"].append(transition)
    state["current_prefix"] = _hex(new_prefix)
    state["current_semantic_colour"] = PREFIX_TO_NAME[new_prefix]
    state["current_rgb"] = list(new_prefix[:3])
    state["updated_at"] = updated_at
    atomic_write_colour_state(state_path, state)
    return state


def confirm_colour_persistence(
    live_read_directory: Path, *, state_path: Path = LED_COLOUR_STATE_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    state = load_colour_state(state_path)
    pending = [
        item for item in state["history"]
        if item["power_cycle_persistence"] == "pending"
    ]
    if len(pending) != 1:
        raise KnownLedColourStateError(
            "confirmation requires exactly one pending colour transition; "
            f"found {len(pending)}"
        )
    _, memory = _load_live_read(live_read_directory)
    if any(COLOUR_ADDRESS + offset not in memory for offset in range(6)):
        raise KnownLedColourStateError("live read lacks complete colour prefix")
    actual = bytes(memory[COLOUR_ADDRESS + offset] for offset in range(6))
    expected = bytes.fromhex(pending[0]["confirmed_new_prefix"])
    if actual != expected:
        raise KnownLedColourStateError(
            f"live read prefix {_hex(actual)} does not confirm {_hex(expected)}"
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
    atomic_write_colour_state(state_path, state)
    return state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.known_led_colour_state"
    )
    parser.add_argument("--state-file", type=Path, default=LED_COLOUR_STATE_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show")
    commands.add_parser("validate")
    confirm = commands.add_parser("confirm-persistence")
    confirm.add_argument("live_read_directory", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "show":
        print(json.dumps(load_colour_state(args.state_file), indent=2))
    elif args.command == "validate":
        state = load_colour_state(args.state_file)
        print(
            f"valid colour state: {len(state['history'])} transition(s); "
            f"current={state['current_semantic_colour']}"
        )
    elif args.command == "confirm-persistence":
        print(json.dumps(confirm_colour_persistence(
            args.live_read_directory, state_path=args.state_file
        ), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
