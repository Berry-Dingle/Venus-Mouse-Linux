"""Strict file validators with warnings for preserved editor anomalies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .actions import ActionType
from .checksum import ProfileStruct
from .macro import (
    ACTION_DATA_LENGTH,
    ACTION_RECORD_CAPACITY,
    DEFAULT_INSERTED_DELAY_MAX_MS,
    DEFAULT_INSERTED_DELAY_MIN_MS,
    MacroFile,
)
from .macroset import MACRO_SLOT_COUNT, MacroSet
from .pfd import (
    ACCELERATION_MAX,
    ACCELERATION_MIN,
    DOUBLE_CLICK_SPEED_MAX,
    DOUBLE_CLICK_SPEED_MIN,
    BreathingSpeed,
    BrightnessLevel,
    LedMode,
    POLLING_ENUM_TO_HZ,
    POINTER_SPEED_MAX,
    POINTER_SPEED_MIN,
    SCROLL_SPEED_MAX,
    SCROLL_SPEED_MIN,
    PfdProfile,
    dpi_raw_to_dpi,
)


@dataclass(frozen=True, slots=True)
class Issue:
    severity: str
    code: str
    message: str


def validate_macro(macro: MacroFile) -> list[Issue]:
    issues: list[Issue] = []
    required = ("MacroFilePath", "LoopTime", "DefDelayTime", "LoopType",
                "DelayType", "Count", "MacroSetting_0")
    for key in required:
        if macro.document.get("Setting", key) is None:
            issues.append(Issue("error", "missing-field", f"missing [Setting] {key}"))
    if issues:
        return issues
    if not 0 <= macro.count <= ACTION_RECORD_CAPACITY:
        issues.append(Issue("error", "count-range", f"Count={macro.count}, expected 0..63"))
    blob = macro.action_struct
    if len(blob.data) != ACTION_DATA_LENGTH:
        issues.append(Issue("error", "blob-length",
                            f"macro payload is {len(blob.data)} bytes, expected 189"))
        return issues
    if not blob.valid:
        issues.append(Issue("error", "checksum", "MacroSetting_0 checksum is invalid"))
    records = macro.all_records
    used = records[:min(max(macro.count, 0), ACTION_RECORD_CAPACITY)]
    for index, record in enumerate(used):
        if record.action_type == ActionType.UNKNOWN:
            issues.append(Issue("warning", "unknown-record",
                                f"record {index}: {record.raw.hex().upper()}"))
        elif record.action_type == ActionType.ZERO:
            issues.append(Issue("warning", "zero-within-count",
                                f"record {index} is zero/padding inside Count"))
    if 0 <= macro.count <= ACTION_RECORD_CAPACITY and any(
            record.raw != b"\0\0\0" for record in records[macro.count:]):
        issues.append(Issue("warning", "data-after-count",
                            "non-zero action data exists after Count"))
    held_keys: set[int] = set()
    held_mouse: set[int] = set()
    for index, record in enumerate(used):
        target = None
        if record.action_type == ActionType.KEYBOARD:
            target = held_keys
        elif record.action_type == ActionType.MOUSE_BUTTON:
            target = held_mouse
        if target is None:
            continue
        if record.state_down:
            if record.usage in target:
                issues.append(Issue("warning", "duplicate-down",
                                    f"record {index}: usage 0x{record.usage:02X} already held"))
            target.add(record.usage)
        elif record.usage not in target:
            issues.append(Issue("warning", "release-without-down",
                                f"record {index}: usage 0x{record.usage:02X} not held"))
        else:
            target.remove(record.usage)
    for usage in sorted(held_keys):
        issues.append(Issue("error", "missing-key-release",
                            f"keyboard usage 0x{usage:02X} remains held"))
    for mask in sorted(held_mouse):
        issues.append(Issue("error", "missing-mouse-release",
                            f"mouse mask 0x{mask:02X} remains held"))
    if macro.loop_type not in (0, 1, 2, 3):
        issues.append(Issue("warning", "unknown-loop-type",
                            f"LoopType={macro.loop_type}"))
    if macro.delay_type not in (0, 1, 2):
        issues.append(Issue("warning", "unknown-delay-type",
                            f"DelayType={macro.delay_type}"))
    try:
        default_inserted_delay = macro.default_inserted_delay_ms
    except ValueError:
        issues.append(Issue("error", "default-inserted-delay-integer",
                            "DefDelayTime must be an integer"))
    else:
        if not DEFAULT_INSERTED_DELAY_MIN_MS <= default_inserted_delay <= (
                DEFAULT_INSERTED_DELAY_MAX_MS):
            issues.append(Issue(
                "error", "default-inserted-delay-range",
                f"DefDelayTime={default_inserted_delay}, expected 0..999 ms",
            ))
    return issues


def validate_macroset(macro_set: MacroSet) -> list[Issue]:
    issues: list[Issue] = []
    if macro_set.document.sections[:1] != ["MACRO_LIST"]:
        issues.append(Issue("error", "ini-structure", "missing leading [MACRO_LIST]"))
    for slot in range(MACRO_SLOT_COUNT):
        if macro_set.document.get("MACRO_LIST", str(slot)) is None:
            issues.append(Issue("error", "missing-slot", f"slot {slot} is absent"))
    names = [name for name in macro_set.names if name]
    for name in sorted(set(names)):
        if names.count(name) > 1:
            issues.append(Issue("error", "duplicate-name", f"macro name {name!r} is duplicated"))
    return issues


def validate_pfd(profile: PfdProfile, *, macro_set: MacroSet | None = None,
                 macro_dir: Path | None = None) -> list[Issue]:
    issues: list[Issue] = []
    group = profile.group_section
    if group is None:
        return [Issue("error", "ini-structure", "missing GROUP section")]
    group_match = re.fullmatch(r"GROUP(\d*)", group)
    if group_match is None:
        return [Issue("error", "ini-structure",
                      f"unsupported group section name [{group}]")]
    button_prefix = f"ButtonAssigned{group_match.group(1)}_"
    acceleration_text = profile.document.get(group, "Acceleration")
    if acceleration_text is None:
        issues.append(Issue(
            "error", "missing-acceleration", f"[{group}] Acceleration is absent",
        ))
    else:
        try:
            acceleration_raw = int(acceleration_text, 10)
        except ValueError:
            issues.append(Issue(
                "error", "acceleration-integer",
                f"Acceleration={acceleration_text!r} is not an integer",
            ))
        else:
            if not ACCELERATION_MIN <= acceleration_raw <= ACCELERATION_MAX:
                issues.append(Issue(
                    "warning", "unknown-acceleration",
                    f"Acceleration={acceleration_raw} is preserved but not mapped",
                ))
    pointer_speed_text = profile.document.get(group, "MouseSensitivity")
    if pointer_speed_text is None:
        issues.append(Issue(
            "error", "missing-pointer-speed",
            f"[{group}] MouseSensitivity is absent",
        ))
    else:
        try:
            pointer_speed_raw = int(pointer_speed_text, 10)
        except ValueError:
            issues.append(Issue(
                "error", "pointer-speed-integer",
                f"MouseSensitivity={pointer_speed_text!r} is not an integer",
            ))
        else:
            if not POINTER_SPEED_MIN <= pointer_speed_raw <= POINTER_SPEED_MAX:
                issues.append(Issue(
                    "warning", "unknown-pointer-speed",
                    f"MouseSensitivity={pointer_speed_raw} is preserved but not mapped",
                ))
    scroll_speed_text = profile.document.get(group, "WheelScrollLines")
    if scroll_speed_text is None:
        issues.append(Issue(
            "error", "missing-scroll-speed",
            f"[{group}] WheelScrollLines is absent",
        ))
    else:
        try:
            scroll_speed_raw = int(scroll_speed_text, 10)
        except ValueError:
            issues.append(Issue(
                "error", "scroll-speed-integer",
                f"WheelScrollLines={scroll_speed_text!r} is not an integer",
            ))
        else:
            if not SCROLL_SPEED_MIN <= scroll_speed_raw <= SCROLL_SPEED_MAX:
                issues.append(Issue(
                    "warning", "unknown-scroll-speed",
                    f"WheelScrollLines={scroll_speed_raw} is preserved but not mapped",
                ))
    double_click_text = profile.document.get(group, "DoubleClickSpeed")
    if double_click_text is None:
        issues.append(Issue(
            "error", "missing-double-click-speed",
            f"[{group}] DoubleClickSpeed is absent",
        ))
    else:
        try:
            double_click_raw = int(double_click_text, 10)
        except ValueError:
            issues.append(Issue(
                "error", "double-click-speed-integer",
                f"DoubleClickSpeed={double_click_text!r} is not an integer",
            ))
        else:
            if not DOUBLE_CLICK_SPEED_MIN <= double_click_raw <= (
                    DOUBLE_CLICK_SPEED_MAX):
                issues.append(Issue(
                    "warning", "unknown-double-click-speed",
                    f"DoubleClickSpeed={double_click_raw} is preserved but not mapped",
                ))
    polling_text = profile.document.get(group, "PollingRate")
    if polling_text is None:
        issues.append(Issue("error", "missing-polling-rate",
                            f"[{group}] PollingRate is absent"))
    else:
        try:
            polling_raw = int(polling_text, 10)
        except ValueError:
            issues.append(Issue("error", "polling-rate-integer",
                                f"PollingRate={polling_text!r} is not an integer"))
        else:
            if polling_raw not in POLLING_ENUM_TO_HZ:
                issues.append(Issue(
                    "warning", "unknown-polling-rate",
                    f"PollingRate={polling_raw} is preserved but not mapped",
                ))
    brightness_text = profile.document.get(group, "LedState1")
    if brightness_text is None:
        issues.append(Issue("error", "missing-brightness",
                            f"[{group}] LedState1 is absent"))
    else:
        try:
            brightness_raw = int(brightness_text, 10)
        except ValueError:
            issues.append(Issue("error", "brightness-integer",
                                f"LedState1={brightness_text!r} is not an integer"))
        else:
            if brightness_raw not in set(BrightnessLevel):
                issues.append(Issue(
                    "warning", "unknown-brightness",
                    f"LedState1={brightness_raw} is preserved but not mapped",
                ))
    color_text = profile.document.get(group, "LedColor1")
    if color_text is None:
        issues.append(Issue("error", "missing-led-colour",
                            f"[{group}] LedColor1 is absent"))
    else:
        try:
            color_raw = int(color_text, 10)
        except ValueError:
            issues.append(Issue("error", "led-colour-integer",
                                f"LedColor1={color_text!r} is not an integer"))
        else:
            if not 0 <= color_raw <= 0xFFFFFF:
                issues.append(Issue(
                    "error", "led-colour-range",
                    f"LedColor1={color_raw}, expected unsigned 24-bit COLORREF",
                ))
    breathing_text = profile.document.get(group, "BreathState1")
    if breathing_text is None:
        issues.append(Issue("error", "missing-breathing-speed",
                            f"[{group}] BreathState1 is absent"))
    else:
        try:
            breathing_raw = int(breathing_text, 10)
        except ValueError:
            issues.append(Issue(
                "error", "breathing-speed-integer",
                f"BreathState1={breathing_text!r} is not an integer",
            ))
        else:
            if breathing_raw not in set(BreathingSpeed):
                issues.append(Issue(
                    "warning", "unknown-breathing-speed",
                    f"BreathState1={breathing_raw} is preserved but not mapped",
                ))
    mode_text = profile.document.get(group, "LedMode1")
    if mode_text is None:
        issues.append(Issue("error", "missing-led-mode",
                            f"[{group}] LedMode1 is absent"))
    else:
        try:
            mode_raw = int(mode_text, 10)
        except ValueError:
            issues.append(Issue("error", "led-mode-integer",
                                f"LedMode1={mode_text!r} is not an integer"))
        else:
            if mode_raw not in set(LedMode):
                issues.append(Issue(
                    "warning", "unknown-led-mode",
                    f"LedMode1={mode_raw} is preserved but not mapped",
                ))
    flash_text = profile.document.get(group, "FlashState1")
    if flash_text is None:
        issues.append(Issue("error", "missing-flash-state",
                            f"[{group}] FlashState1 is absent"))
    else:
        try:
            int(flash_text, 10)
        except ValueError:
            issues.append(Issue("error", "flash-state-integer",
                                f"FlashState1={flash_text!r} is not an integer"))
    selected_dpi_text = profile.document.get(group, "DPI")
    if selected_dpi_text is None:
        issues.append(Issue("error", "missing-selected-dpi-stage",
                            f"[{group}] DPI is absent"))
    else:
        try:
            selected_dpi_raw = int(selected_dpi_text, 10)
        except ValueError:
            issues.append(Issue(
                "error", "selected-dpi-stage-integer",
                f"DPI={selected_dpi_text!r} is not an integer",
            ))
        else:
            if selected_dpi_raw not in range(5):
                issues.append(Issue(
                    "warning", "unknown-selected-dpi-stage",
                    f"DPI={selected_dpi_raw} is preserved but not mapped",
                ))
    required_blobs = ["DPIStageValue"] + [
        f"{button_prefix}{number}" for number in range(1, 21)
    ]
    for key in required_blobs:
        if profile.document.get(group, key) is None:
            issues.append(Issue("error", "missing-blob",
                                f"[{group}] {key} is absent"))
    for key, text in profile.document.items(group):
        if key != "DPIStageValue" and not key.startswith("ButtonAssigned"):
            continue
        try:
            blob = ProfileStruct.decode(text)
        except ValueError as exc:
            issues.append(Issue("error", "blob-decode", f"{key}: {exc}"))
            continue
        expected = 408 if key == "DPIStageValue" else 189
        if len(blob.data) != expected:
            issues.append(Issue("error", "blob-length",
                                f"{key}: {len(blob.data)} data bytes, expected {expected}"))
        if not blob.valid:
            issues.append(Issue("error", "checksum", f"{key}: invalid checksum"))
        if key == "DPIStageValue" and len(blob.data) >= 80:
            stage_values = [
                profile.dpi_stage_value(stage) for stage in range(1, 6)
            ]
            for stage in range(1, 6):
                value = stage_values[stage - 1]
                if value.enabled not in (0, 1):
                    issues.append(Issue(
                        "warning", "unknown-dpi-enable",
                        f"stage {stage}: enable={value.enabled} is preserved",
                    ))
                if value.x_raw != value.y_raw:
                    issues.append(Issue(
                        "warning", "asymmetric-dpi-stage",
                        f"stage {stage}: X={value.x_raw}, Y={value.y_raw} preserved",
                    ))
                for axis, raw in (("X", value.x_raw), ("Y", value.y_raw)):
                    if dpi_raw_to_dpi(raw) is None:
                        issues.append(Issue(
                            "warning", "unknown-dpi-raw",
                            f"stage {stage} {axis}={raw} is preserved but not mapped",
                        ))
            if all(value.enabled == 0 for value in stage_values):
                issues.append(Issue(
                    "warning", "all-dpi-stages-disabled",
                    "all five packed DPI stage enable fields are zero",
                ))
            try:
                selected = profile.selected_dpi_stage_raw
            except ValueError:
                pass
            else:
                if (selected in range(5)
                        and stage_values[selected].enabled == 0):
                    issues.append(Issue(
                        "warning", "selected-dpi-stage-disabled",
                        f"stored selected stage {selected + 1} is disabled",
                    ))
    if len(profile.buttons) != 20:
        issues.append(Issue("error", "button-count",
                            f"found {len(profile.buttons)} semantic button sections, expected 20"))
    required_button_fields = ("ButtonID", "ButtonFunc", "KeyNumber", "MacroName")
    for number in range(1, 21):
        section = f"{button_prefix}{number}"
        if section not in profile.document.sections:
            issues.append(Issue("error", "missing-button-section",
                                f"[{section}] is absent"))
            continue
        for field in required_button_fields:
            if profile.document.get(section, field) is None:
                issues.append(Issue("error", "missing-button-field",
                                    f"[{section}] {field} is absent"))
    for button in profile.macro_assignments:
        if not 0 <= button.key_number < MACRO_SLOT_COUNT:
            issues.append(Issue("error", "slot-range",
                                f"{button.section}: KeyNumber={button.key_number}"))
            continue
        if not button.macro_name:
            issues.append(Issue("error", "macro-name",
                                f"{button.section}: macro assignment has empty MacroName"))
        if macro_set is not None:
            listed = macro_set.names[button.key_number]
            if listed != button.macro_name:
                issues.append(Issue("error", "slot-name-mismatch",
                                    f"{button.section}: slot {button.key_number} is "
                                    f"{listed!r}, expected {button.macro_name!r}"))
        if macro_dir is not None and button.macro_name:
            expected = macro_dir / f"{button.macro_name}.MSMACRO"
            if not expected.is_file():
                issues.append(Issue("error", "missing-macro-file",
                                    f"{button.section}: missing {expected.name}"))
    return issues


def detect_kind(path: str | Path):
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".pfd":
        return PfdProfile.from_path(source)
    document_head = source.read_bytes()
    if b"[MACRO_LIST]" in document_head:
        return MacroSet.from_bytes(document_head)
    if suffix in (".msmacro", ".msdb"):
        return MacroFile.from_bytes(document_head)
    raise ValueError(f"unsupported file type: {source}")
