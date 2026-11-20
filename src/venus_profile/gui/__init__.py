"""GTK4/libadwaita application for configuring the UtechSmart Venus mouse.

The GUI edits profiles and macros offline.  It contains no device discovery,
HID transport, reader, writer, or device-session integration: to read the
mouse it launches the separate read-only helper ``venus_profile.read_snapshot``
(``gui/device_read.py``) and decodes the result with
``venus_profile.device_decode``; it then follows DPI/profile button presses
through the listen-only helper ``venus_profile.watch_events``
(``gui/device_events.py``).  Lighting changes are sent through the one-shot
helper ``venus_profile.preview_lighting`` (``gui/lighting_preview.py``,
``gui/live_lighting.py``), which writes only the fixed lighting fields with
read-back.  These are the GUI's only three process launches; nothing else can
be written to the mouse (``docs/LIVE_LIGHTING_PREVIEW_DESIGN.md``).
"""

from .models import (
    ButtonView,
    NormalizedRect,
    PhysicalControl,
    build_physical_control_registry,
)

__all__ = [
    "ButtonView",
    "NormalizedRect",
    "PhysicalControl",
    "build_physical_control_registry",
]
