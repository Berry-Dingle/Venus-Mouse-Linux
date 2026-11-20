#!/usr/bin/env python3
"""Generate deterministic offline controlled LED-colour artefacts."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "linux-suite-prototype" / "src"))

from venus_profile.led_colour_analysis import (  # noqa: E402
    CAPTURE, OUTPUT, POST_READ, PRE_READ,
    analyse_led_colour_capture, write_analysis_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, default=CAPTURE)
    parser.add_argument("--pre-read", type=Path, default=PRE_READ)
    parser.add_argument("--post-read", type=Path, default=POST_READ)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    analysis = analyse_led_colour_capture(
        args.capture, pre_directory=args.pre_read,
        post_directory=args.post_read,
    )
    write_analysis_outputs(args.output, analysis=analysis)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
