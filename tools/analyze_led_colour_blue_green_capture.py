#!/usr/bin/env python3
"""Generate deterministic offline Blue-to-Green analysis artefacts."""

from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "linux-suite-prototype" / "src"))

from venus_profile.led_colour_blue_green_analysis import (  # noqa: E402
    analyse_blue_to_green_capture, write_analysis_outputs,
)


if __name__ == "__main__":
    write_analysis_outputs(analysis=analyse_blue_to_green_capture())
