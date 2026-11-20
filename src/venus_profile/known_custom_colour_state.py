"""Offline append-only state for guarded arbitrary custom LED colours."""

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
CUSTOM_COLOUR_STATE_PATH = (
    PROJECT_ROOT / "usb-captures" / "device-state"
    / "known_custom_colour_state.json"
)
PREFIX_ADDRESS = 0x0449
PREFIX_LENGTH = 6
COLOUR_LENGTH = 3
LIGHTING_SUFFIX = bytes.fromhex("01 00 01")
INITIAL_SEMANTIC_HEX = "#0000FF"
INITIAL_LIVE_READ = (
    PROJECT_ROOT / "usb-captures" / "live-reads"
    / "venus_read_20260727T130711.618278Z"
)
_HEX_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


class KnownCustomColourStateError(RuntimeError):
    """Custom-colour state or persistence evidence is malformed."""


def normalise_semantic_hex(value: str) -> str:
    if not isinstance(value, str) or _HEX_PATTERN.fullmatch(value) is None:
        raise KnownCustomColourStateError(
            "custom colour must be exactly #RRGGBB"
        )
    return value.upper()


def semantic_hex_to_rgb(value: str) -> tuple[int, int, int]:
    normalised = normalise_semantic_hex(value)
    return tuple(
        int(normalised[index:index + 2], 16) for index in (1, 3, 5)
    )


def semantic_hex_to_device_rgb(value: str) -> bytes:
    red, green, blue = semantic_hex_to_rgb(value)
    return bytes((red, green, blue))


def semantic_hex_to_prefix(value: str) -> bytes:
    return semantic_hex_to_device_rgb(value) + LIGHTING_SUFFIX


def _hex(value: bytes) -> str:
    return value.hex(" ")


def initial_state(timestamp: str) -> dict[str, object]:
    prefix = semantic_hex_to_prefix(INITIAL_SEMANTIC_HEX)
    return {
        "schema_version": 2,
        "namespace": "profile-1-guarded-custom-led-colour",
        "device": {
            "vendor_id": "04d9", "product_id": "fc55",
            "interface_number": "02", "usage_page": "0xFFA0", "usage": 1,
        },
        "colour_address_start": PREFIX_ADDRESS,
        "colour_address_end": PREFIX_ADDRESS + COLOUR_LENGTH - 1,
        "prefix_address_start": PREFIX_ADDRESS,
        "prefix_address_end": PREFIX_ADDRESS + PREFIX_LENGTH - 1,
        "prefix_length": PREFIX_LENGTH,
        "conversion_rule": "native semantic #RRGGBB -> device RR GG BB",
        "current_semantic_hex": INITIAL_SEMANTIC_HEX,
        "current_semantic_rgb": list(
            semantic_hex_to_rgb(INITIAL_SEMANTIC_HEX)
        ),
        "current_device_rgb": _hex(prefix[:3]),
        "current_prefix": _hex(prefix),
        "initial_evidence": {
            "source_of_authority": "corrected-live-visible-colour-evidence",
            "live_read_directory": str(INITIAL_LIVE_READ),
            "semantic_hex": INITIAL_SEMANTIC_HEX,
            "device_rgb": _hex(prefix[:3]),
            "prefix": _hex(prefix),
            "persistence_status": "confirmed-observed-state",
        },
        "semantic_corrections": [],
        "history": [],
        "diagnostics": [],
        "updated_at": timestamp,
    }


def _validate_semantic_fields(
    semantic_hex: object, rgb: object, bgr: object, prefix: object,
    *, label: str,
) -> tuple[str, bytes]:
    try:
        normalised = normalise_semantic_hex(str(semantic_hex))
        parsed_bgr = bytes.fromhex(str(bgr))
        parsed_prefix = bytes.fromhex(str(prefix))
        parsed_rgb = list(rgb)
    except (TypeError, ValueError) as exc:
        raise KnownCustomColourStateError(
            f"malformed {label} custom-colour fields"
        ) from exc
    expected_rgb = semantic_hex_to_device_rgb(normalised)
    if (
        parsed_rgb != list(semantic_hex_to_rgb(normalised))
        or parsed_bgr != expected_rgb
        or parsed_prefix != expected_rgb + LIGHTING_SUFFIX
    ):
        raise KnownCustomColourStateError(
            f"conflicting {label} semantic/RGB/device-RGB/prefix fields"
        )
    return normalised, parsed_prefix


def validate_custom_colour_state(document: object) -> dict[str, object]:
    if not isinstance(document, dict):
        raise KnownCustomColourStateError("custom colour state must be object")
    try:
        device = document["device"]
        identity = (
            str(device["vendor_id"]).lower(),
            str(device["product_id"]).lower(),
            str(device["interface_number"]).lower(),
            device["usage_page"], device["usage"],
        )
        ranges = (
            int(document["colour_address_start"]),
            int(document["colour_address_end"]),
            int(document["prefix_address_start"]),
            int(document["prefix_address_end"]),
            int(document["prefix_length"]),
        )
        history = document["history"]
        corrections = document["semantic_corrections"]
        diagnostics = document["diagnostics"]
        schema_version = int(document["schema_version"])
        conversion = document["conversion_rule"]
    except (KeyError, TypeError, ValueError) as exc:
        raise KnownCustomColourStateError(
            "malformed custom colour state"
        ) from exc
    if identity != ("04d9", "fc55", "02", "0xFFA0", 1):
        raise KnownCustomColourStateError("wrong device identity/interface")
    if ranges != (0x0449, 0x044B, 0x0449, 0x044E, 6):
        raise KnownCustomColourStateError("wrong custom colour address range")
    if conversion != "native semantic #RRGGBB -> device RR GG BB":
        raise KnownCustomColourStateError("wrong custom colour conversion")
    if schema_version != 2:
        raise KnownCustomColourStateError("custom colour state must be schema 2")
    if not isinstance(corrections, list):
        raise KnownCustomColourStateError(
            "semantic corrections must be an append-only list"
        )
    if not isinstance(diagnostics, list):
        raise KnownCustomColourStateError(
            "diagnostic events must be an append-only list"
        )
    current_hex, current_prefix = _validate_semantic_fields(
        document.get("current_semantic_hex"),
        document.get("current_semantic_rgb"),
        document.get("current_device_rgb"),
        document.get("current_prefix"),
        label="current",
    )
    if not isinstance(history, list):
        raise KnownCustomColourStateError("history must be a list")
    chain = None
    pending = 0
    for index, item in enumerate(history):
        try:
            old_hex, old_prefix = _validate_semantic_fields(
                item["previous_semantic_hex"], item["previous_semantic_rgb"],
                item["previous_device_rgb"], item["previous_prefix"],
                label=f"history {index} previous",
            )
            new_hex, new_prefix = _validate_semantic_fields(
                item["new_semantic_hex"], item["new_semantic_rgb"],
                item["new_device_rgb"], item["confirmed_new_prefix"],
                label=f"history {index} new",
            )
            status = item["power_cycle_persistence"]
            immediate = item["immediate_read_back_confirmed"]
        except (KeyError, TypeError, ValueError) as exc:
            raise KnownCustomColourStateError(
                f"malformed history entry {index}"
            ) from exc
        if old_hex == new_hex or status not in {"pending", "confirmed"}:
            raise KnownCustomColourStateError(
                f"invalid transition at history entry {index}"
            )
        if immediate is not True:
            raise KnownCustomColourStateError(
                f"read-back not confirmed at history entry {index}"
            )
        if chain is not None and old_prefix != chain:
            raise KnownCustomColourStateError("conflicting history chain")
        chain = new_prefix
        pending += status == "pending"
    if pending > 1:
        raise KnownCustomColourStateError("multiple pending transitions")
    diagnostic_prefix = None
    for index, item in enumerate(diagnostics):
        if not isinstance(item, dict):
            raise KnownCustomColourStateError(
                f"malformed diagnostic event {index}"
            )
        try:
            status = item["status"]
            attempted = int(item["steps_attempted"])
            writes = int(item["writes_confirmed"])
            y_count = int(item["visual_y_count"])
            n_count = int(item["visual_n_count"])
            skipped = int(item["skipped_count"])
            persistence = item["persistence"]
            authority = item["source_of_authority"]
            confirmed = item["immediate_read_back_confirmed"]
            raw_prefix = item.get("last_confirmed_prefix")
        except (KeyError, TypeError, ValueError) as exc:
            raise KnownCustomColourStateError(
                f"malformed diagnostic event {index}"
            ) from exc
        if status not in {"completed", "aborted"}:
            raise KnownCustomColourStateError(
                f"invalid diagnostic status {index}"
            )
        if min(attempted, writes, y_count, n_count, skipped) < 0:
            raise KnownCustomColourStateError(
                f"negative diagnostic count {index}"
            )
        if writes > attempted or y_count + n_count + skipped > writes:
            raise KnownCustomColourStateError(
                f"inconsistent diagnostic counts {index}"
            )
        if persistence != "untested/pending" or authority != "visual-cycle":
            raise KnownCustomColourStateError(
                f"invalid diagnostic provenance {index}"
            )
        if confirmed is not True and writes > 0:
            raise KnownCustomColourStateError(
                f"diagnostic lacks exact read-back confirmation {index}"
            )
        if status == "completed" and (
            attempted != 20 or writes != 20
            or y_count + n_count + skipped != 20
        ):
            raise KnownCustomColourStateError(
                f"completed diagnostic is not a full 20-step run {index}"
            )
        if raw_prefix is not None:
            try:
                parsed = bytes.fromhex(str(raw_prefix))
            except ValueError as exc:
                raise KnownCustomColourStateError(
                    f"malformed diagnostic prefix {index}"
                ) from exc
            if len(parsed) != PREFIX_LENGTH or parsed[3:] != LIGHTING_SUFFIX:
                raise KnownCustomColourStateError(
                    f"invalid diagnostic prefix {index}"
                )
            try:
                diag_hex, diag_prefix = _validate_semantic_fields(
                    item["last_confirmed_semantic_hex"],
                    item["last_confirmed_rgb"],
                    item["last_confirmed_device_rgb"],
                    raw_prefix,
                    label=f"diagnostic {index}",
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise KnownCustomColourStateError(
                    f"malformed diagnostic semantic fields {index}"
                ) from exc
            if diag_prefix != parsed:
                raise KnownCustomColourStateError(
                    f"diagnostic prefix disagreement {index}"
                )
            diagnostic_prefix = parsed
        elif status == "completed" or writes:
            raise KnownCustomColourStateError(
                f"diagnostic has no confirmed final colour {index}"
            )
    authoritative_prefixes = {
        value for value in (chain, diagnostic_prefix) if value is not None
    }
    if authoritative_prefixes and current_prefix not in authoritative_prefixes:
        raise KnownCustomColourStateError("current state conflicts with history and diagnostics")
    if current_hex != normalise_semantic_hex(
        str(document["current_semantic_hex"])
    ):
        raise KnownCustomColourStateError("current semantic hex is not normal")
    return document


def record_visual_diagnostic(
    *, state_path: Path, result_directory: Path, status: str,
    last_confirmed_prefix: bytes | None, timestamp: str,
    steps_attempted: int = 0, writes_confirmed: int = 0,
    visual_y_count: int = 0, visual_n_count: int = 0,
    skipped_count: int = 0,
) -> dict[str, object]:
    """Record one finite visual run without adding colour transitions."""
    if status not in {"completed", "aborted"}:
        raise KnownCustomColourStateError("invalid visual diagnostic status")
    state = load_custom_colour_state(state_path)
    state = json.loads(json.dumps(state))
    if last_confirmed_prefix is not None:
        if len(last_confirmed_prefix) != PREFIX_LENGTH:
            raise KnownCustomColourStateError("diagnostic prefix must be 6 bytes")
        if last_confirmed_prefix[3:] != LIGHTING_SUFFIX:
            raise KnownCustomColourStateError("diagnostic suffix mismatch")
        semantic_hex = "#" + last_confirmed_prefix[:3].hex().upper()
        state["current_semantic_hex"] = semantic_hex
        state["current_semantic_rgb"] = list(last_confirmed_prefix[:3])
        state["current_device_rgb"] = _hex(last_confirmed_prefix[:3])
        state["current_prefix"] = _hex(last_confirmed_prefix)
        state["diagnostic_persistence"] = "pending"
    final_hex = (
        "#" + last_confirmed_prefix[:3].hex().upper()
        if last_confirmed_prefix is not None else None
    )
    event = {
        "diagnostic_id": f"{timestamp}|visual-20-colour-cycle",
        "type": "finite-visual-colour-cycle",
        "status": status,
        "result_directory": str(result_directory),
        "last_confirmed_prefix": (
            _hex(last_confirmed_prefix)
            if last_confirmed_prefix is not None else None
        ),
        "persistence": "untested/pending",
        "steps_attempted": steps_attempted,
        "writes_confirmed": writes_confirmed,
        "visual_y_count": visual_y_count,
        "visual_n_count": visual_n_count,
        "skipped_count": skipped_count,
        "last_confirmed_semantic_hex": final_hex,
        "last_confirmed_rgb": (
            list(last_confirmed_prefix[:3])
            if last_confirmed_prefix is not None else None
        ),
        "last_confirmed_device_rgb": (
            _hex(last_confirmed_prefix[:3])
            if last_confirmed_prefix is not None else None
        ),
        "immediate_read_back_confirmed": last_confirmed_prefix is not None,
        "source_of_authority": "visual-cycle",
        "timestamp": timestamp,
    }
    state["diagnostics"].append(event)
    state["updated_at"] = timestamp
    atomic_write_custom_colour_state(state_path, state)
    return state


def load_custom_colour_state(
    path: Path = CUSTOM_COLOUR_STATE_PATH, *, allow_missing: bool = False
) -> dict[str, object] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if allow_missing:
            return None
        raise KnownCustomColourStateError(f"state file missing: {path}")
    except (OSError, json.JSONDecodeError) as exc:
        raise KnownCustomColourStateError(f"could not parse state: {path}") from exc
    return validate_custom_colour_state(document)


def atomic_write_custom_colour_state(
    path: Path, document: dict[str, object]
) -> None:
    validate_custom_colour_state(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(document, indent=2) + "\n").encode()
    fd, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def record_custom_colour_transition(
    *, state_path: Path, previous_hex: str, new_hex: str,
    operation_timestamp: str, result_directory: Path, updated_at: str,
) -> dict[str, object]:
    previous_hex = normalise_semantic_hex(previous_hex)
    new_hex = normalise_semantic_hex(new_hex)
    state = load_custom_colour_state(state_path, allow_missing=True)
    if state is None:
        state = initial_state(updated_at)
    state = json.loads(json.dumps(state))
    if any(
        item["power_cycle_persistence"] == "pending"
        for item in state["history"]
    ):
        raise KnownCustomColourStateError(
            "a prior custom-colour transition is pending persistence"
        )
    if state["current_semantic_hex"] != previous_hex:
        raise KnownCustomColourStateError(
            "known custom colour does not match requested source"
        )
    if previous_hex == new_hex:
        raise KnownCustomColourStateError("source and target are equal")
    old_prefix = semantic_hex_to_prefix(previous_hex)
    new_prefix = semantic_hex_to_prefix(new_hex)
    transition = {
        "transition_id": (
            f"{operation_timestamp}|0x0449-0x044E|"
            f"{old_prefix.hex()}-to-{new_prefix.hex()}"
        ),
        "address_start": PREFIX_ADDRESS,
        "address_end": PREFIX_ADDRESS + PREFIX_LENGTH - 1,
        "conversion_rule": "native semantic #RRGGBB -> device RR GG BB",
        "previous_semantic_hex": previous_hex,
        "new_semantic_hex": new_hex,
        "previous_semantic_rgb": list(semantic_hex_to_rgb(previous_hex)),
        "new_semantic_rgb": list(semantic_hex_to_rgb(new_hex)),
        "previous_device_rgb": _hex(old_prefix[:3]),
        "new_device_rgb": _hex(new_prefix[:3]),
        "previous_prefix": _hex(old_prefix),
        "confirmed_new_prefix": _hex(new_prefix),
        "operation_timestamp": operation_timestamp,
        "result_directory": str(result_directory),
        "immediate_read_back_confirmed": True,
        "power_cycle_persistence": "pending",
        "persistence_confirmed_at": None,
        "source_of_authority": "guarded-linux-custom-colour-write",
        "latest_confirming_live_read_directory": None,
    }
    state["history"].append(transition)
    state["current_semantic_hex"] = new_hex
    state["current_semantic_rgb"] = list(semantic_hex_to_rgb(new_hex))
    state["current_device_rgb"] = _hex(new_prefix[:3])
    state["current_prefix"] = _hex(new_prefix)
    state["updated_at"] = updated_at
    atomic_write_custom_colour_state(state_path, state)
    return state


def confirm_custom_colour_persistence(
    live_read_directory: Path, *,
    state_path: Path = CUSTOM_COLOUR_STATE_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    state = load_custom_colour_state(state_path)
    pending = [
        item for item in state["history"]
        if item["power_cycle_persistence"] == "pending"
    ]
    if len(pending) != 1:
        raise KnownCustomColourStateError(
            f"expected one pending transition, found {len(pending)}"
        )
    _, memory = _load_live_read(live_read_directory)
    if any(PREFIX_ADDRESS + offset not in memory for offset in range(6)):
        raise KnownCustomColourStateError("live read lacks complete prefix")
    actual = bytes(memory[PREFIX_ADDRESS + offset] for offset in range(6))
    expected = bytes.fromhex(pending[0]["confirmed_new_prefix"])
    if actual != expected:
        raise KnownCustomColourStateError(
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
    atomic_write_custom_colour_state(state_path, state)
    return state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.known_custom_colour_state"
    )
    parser.add_argument(
        "--state-file", type=Path, default=CUSTOM_COLOUR_STATE_PATH
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show")
    commands.add_parser("validate")
    confirm = commands.add_parser("confirm-persistence")
    confirm.add_argument("live_read_directory", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "show":
        print(json.dumps(load_custom_colour_state(args.state_file), indent=2))
    elif args.command == "validate":
        state = load_custom_colour_state(args.state_file)
        print(
            f"valid custom colour state: {len(state['history'])} "
            f"transition(s); current={state['current_semantic_hex']}"
        )
    elif args.command == "confirm-persistence":
        print(json.dumps(confirm_custom_colour_persistence(
            args.live_read_directory, state_path=args.state_file
        ), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
