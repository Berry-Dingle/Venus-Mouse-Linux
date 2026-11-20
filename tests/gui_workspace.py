"""Shared GUI test helpers: temporary workspaces and one registered app."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from venus_profile.app_store import AppStore
from venus_profile.workspace import VenusWorkspace

_APPLICATION = None


def temp_workspace() -> VenusWorkspace:
    base = os.environ.get("VENUS_PROFILE_DATA_DIR")
    parent = Path(base).parent if base else None
    root = Path(tempfile.mkdtemp(dir=parent if parent and parent.exists() else None))
    return VenusWorkspace.open(AppStore(root / "store"))


def shared_application():
    """GTK exports a registered application on D-Bus; register only once."""
    global _APPLICATION
    if _APPLICATION is None:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        from venus_profile.gui.application import VenusApplication
        _APPLICATION = VenusApplication(flags=Gio.ApplicationFlags.NON_UNIQUE, application_id=None)
        _APPLICATION.register(None)
    return _APPLICATION


def new_window():
    from venus_profile.gui.application import VenusWindow
    return VenusWindow(shared_application(), temp_workspace())
