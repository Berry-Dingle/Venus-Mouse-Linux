"""Offline CLI for provenance-rich lighting-state inspection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .known_device_state import KNOWN_STATE_PATH
from .lighting_state import (
    inspect_known_lighting_state,
    inspect_live_read_lighting_state,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m venus_profile.inspect_lighting_state"
    )
    parser.add_argument("--live-read-directory", type=Path)
    parser.add_argument("--state-file", type=Path, default=KNOWN_STATE_PATH)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    state = (
        inspect_live_read_lighting_state(
            args.live_read_directory, state_path=args.state_file
        )
        if args.live_read_directory
        else inspect_known_lighting_state(state_path=args.state_file)
    )
    encoded = json.dumps(state.to_dict(), indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
