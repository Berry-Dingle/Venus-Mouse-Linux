"""Read-only command-line interface."""

from __future__ import annotations

import argparse
from pathlib import Path

from .actions import ActionType
from .macro import MacroFile
from .macroset import MacroSet, report4_address
from .pfd import PfdProfile
from .validation import detect_kind, validate_macro, validate_macroset, validate_pfd


def inspect_file(path: Path) -> int:
    model = detect_kind(path)
    print(f"file: {path}")
    if isinstance(model, PfdProfile):
        print("type: PFD")
        print(f"group: {model.group_section}")
        print(f"buttons: {len(model.buttons)}")
        rate = model.polling_rate_hz
        print(f"polling-rate: {rate.value if rate is not None else '<unknown>'} Hz "
              f"(stored={model.polling_rate_raw})")
        brightness = model.brightness
        print(f"brightness: {brightness.name.title() if brightness is not None else '<unknown>'} "
              f"(stored={model.brightness_raw})")
        breathing = model.breathing_speed
        breathing_label = (
            breathing.name.replace("_", " ").title()
            if breathing is not None else "<unknown>"
        )
        print(f"breathing-speed: {breathing_label} "
              f"(stored={model.breathing_speed_raw})")
        mode = model.led_mode
        mode_label = (
            mode.name.replace("_", " ").title()
            if mode is not None else "<unknown>"
        )
        print(f"led-mode: {mode_label} (stored={model.led_mode_raw})")
        print(f"flash-state-raw: {model.flash_state_raw}")
        selected_stage = model.selected_dpi_stage
        print(f"selected-dpi-stage: "
              f"{selected_stage.value if selected_stage is not None else '<unknown>'} "
              f"(stored={model.selected_dpi_stage_raw})")
        palette = model.palette_color
        red, green, blue = model.led_color_rgb
        print(f"colour: RGB({red},{green},{blue}) stored={model.led_color_raw} "
              f"palette={palette.name if palette is not None else '<non-grid>'}")
        for button in model.macro_assignments:
            print(f"macro: {button.section} name={button.macro_name} "
                  f"slot={button.key_number} address=0x{report4_address(button.key_number):04X}")
    elif isinstance(model, MacroSet):
        print("type: MacroSet.MSDB")
        for slot, name in enumerate(model.names):
            print(f"slot {slot:02d}: {name or '<empty>'} address=0x{report4_address(slot):04X}")
    else:
        print(f"type: {path.suffix[1:].upper()} macro")
        if not model.has_action_blob:
            print("action blob: missing")
            return 0
        print(f"Count: {model.count}")
        print(f"LoopType: {model.loop_type}; LoopTime: {model.loop_time}")
        print(f"DelayType: {model.delay_type}; "
              f"Default inserted delay: {model.default_inserted_delay_ms} ms")
        print(f"checksum-valid: {model.action_struct.valid}")
    return 0


def validate_file(path: Path) -> int:
    model = detect_kind(path)
    if isinstance(model, PfdProfile):
        macro_dir = path.parent / "MacroDB"
        macro_set_path = macro_dir / "MacroSet.MSDB"
        macro_set = MacroSet.from_path(macro_set_path) if macro_set_path.is_file() else None
        issues = validate_pfd(model, macro_set=macro_set,
                              macro_dir=macro_dir if macro_dir.is_dir() else None)
    elif isinstance(model, MacroSet):
        issues = validate_macroset(model)
    else:
        issues = validate_macro(model)
    if not issues:
        print("valid")
        return 0
    for issue in issues:
        print(f"{issue.severity}: {issue.code}: {issue.message}")
    return 1 if any(issue.severity == "error" for issue in issues) else 0


def decode_macro(path: Path) -> int:
    model = detect_kind(path)
    if not isinstance(model, MacroFile):
        print("error: decode-macro requires an MSMACRO or macro-shaped MSDB")
        return 2
    if not model.has_action_blob:
        print("error: MacroSetting_0 is missing")
        return 1
    elapsed = 0
    for index, record in enumerate(model.records):
        if record.action_type == ActionType.DELAY:
            elapsed += record.delay_ms
        print(f"{index:02d} offset={index * 3:03d} t={elapsed:06d}ms "
              f"{record.raw.hex(' ').upper()}  {record.describe()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m venus_profile")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "validate", "decode-macro"):
        command = sub.add_parser(name)
        command.add_argument("file", type=Path)
    args = parser.parse_args(argv)
    if args.command == "inspect":
        return inspect_file(args.file)
    if args.command == "validate":
        return validate_file(args.file)
    return decode_macro(args.file)
