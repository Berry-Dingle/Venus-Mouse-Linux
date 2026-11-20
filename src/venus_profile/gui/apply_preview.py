"""Read-only dialog listing the offline Apply preview (nothing is sent)."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk

from ..apply_plan import build_apply_preview


def preview_text(workspace) -> str:
    preview = build_apply_preview(workspace)
    lines = ["Nothing in this list is sent to the mouse.", ""]
    for area in ("DPI", "Buttons", "Lighting", "Polling", "Macros"):
        items = preview.by_area(area)
        if not items:
            continue
        lines.append(f"{area}")
        for item in items:
            profile = f"P{item.profile} " if item.profile else ""
            payload = item.payload.hex(" ").upper()
            if len(item.payload) > 12:
                payload = payload[:35] + f" … ({len(item.payload)} bytes)"
            lines.append(f"  {profile}{item.address_text}  {payload or '—'}  [{item.evidence.value}]  {item.note}")
        lines.append("")
    lines.append("Before device Apply can be enabled:")
    lines.extend(f"  • {blocker}" for blocker in preview.blockers)
    return "\n".join(lines)


def present_apply_preview(parent, workspace) -> Adw.Window:
    window = Adw.Window(transient_for=parent, modal=True)
    window.set_title("Apply Preview")
    window.set_default_size(820, 600)
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    toolbar.add_top_bar(header)
    text = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
    text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    text.set_left_margin(12)
    text.set_right_margin(12)
    text.set_top_margin(10)
    text.get_buffer().set_text(preview_text(workspace))
    scroller = Gtk.ScrolledWindow()
    scroller.set_child(text)
    toolbar.set_content(scroller)
    window.set_content(toolbar)
    window.present()
    return window
