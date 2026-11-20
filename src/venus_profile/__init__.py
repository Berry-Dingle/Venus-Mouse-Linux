"""File-only UtechSmart Venus profile parser/compiler.

This package deliberately contains no USB, HID, or device-access code.
"""

from .actions import ActionRecord, ActionType
from .button_assignments import (
    BUTTON_OFFSETS,
    CONFIRMED_ACTION_LABELS,
    PROFILE_BASES,
    ButtonAssignmentRecord,
    MacroAssignment,
    ProfileAssignmentRegion,
    button_assignment_address,
    decode_profile_assignment_region,
    decode_saved_assignments,
)
from .macro import MacroFile
from .macroset import MacroSet
from .dpi_stage import (
    SELECTED_DPI_STAGE_ADDRESS,
    SELECTED_DPI_STAGE_MAX,
    SELECTED_DPI_STAGE_MIN,
    SelectedDpiStage,
    decode_selected_dpi_stage,
    decode_selected_dpi_stage_byte,
)
from .pfd import (
    ACCELERATION_MAX,
    ACCELERATION_MIN,
    DOUBLE_CLICK_SPEED_MAX,
    DOUBLE_CLICK_SPEED_MIN,
    POINTER_SPEED_MAX,
    POINTER_SPEED_MIN,
    SCROLL_SPEED_MAX,
    SCROLL_SPEED_MIN,
    BrightnessLevel,
    BreathingSpeed,
    DpiStage,
    DpiStageValue,
    LedMode,
    PaletteColor,
    PfdProfile,
    PollingRateHz,
    colorref_to_rgb,
    dpi_raw_to_dpi,
    dpi_to_raw,
    parse_rgb_hex,
    rgb_to_colorref,
)
from .profile_session import (
    AmbiguousProfileError,
    DirtyProfileReplacementError,
    IncompatibleProfileError,
    MissingProfileError,
    ProfileDocumentSession,
    ProfileLoadError,
    ProfileSaveError,
    ProfileSetSession,
)

__all__ = [
    "ActionRecord", "ActionType", "MacroFile", "MacroSet", "PfdProfile",
    "ProfileDocumentSession", "ProfileSetSession", "ProfileLoadError",
    "ProfileSaveError", "MissingProfileError", "IncompatibleProfileError",
    "AmbiguousProfileError", "DirtyProfileReplacementError",
    "BUTTON_OFFSETS", "PROFILE_BASES", "ButtonAssignmentRecord",
    "MacroAssignment", "ProfileAssignmentRegion", "button_assignment_address",
    "decode_profile_assignment_region", "decode_saved_assignments",
    "CONFIRMED_ACTION_LABELS", "SELECTED_DPI_STAGE_ADDRESS",
    "SELECTED_DPI_STAGE_MIN", "SELECTED_DPI_STAGE_MAX", "SelectedDpiStage",
    "decode_selected_dpi_stage", "decode_selected_dpi_stage_byte",
    "ACCELERATION_MAX", "ACCELERATION_MIN",
    "DOUBLE_CLICK_SPEED_MAX", "DOUBLE_CLICK_SPEED_MIN",
    "POINTER_SPEED_MAX", "POINTER_SPEED_MIN",
    "SCROLL_SPEED_MAX", "SCROLL_SPEED_MIN",
    "BrightnessLevel", "BreathingSpeed", "DpiStage", "DpiStageValue", "LedMode",
    "PaletteColor", "PollingRateHz", "colorref_to_rgb", "dpi_raw_to_dpi",
    "dpi_to_raw", "parse_rgb_hex", "rgb_to_colorref",
]

