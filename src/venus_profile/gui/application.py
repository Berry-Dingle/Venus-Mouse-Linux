"""GTK4/libadwaita application for the UtechSmart Venus mouse (offline editor)."""

from __future__ import annotations

from pathlib import Path

import os
import sys

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .accessibility import apply_accessible_label, apply_pointer_cursor_tree, set_accessible_selected
from .assets import icon_bytes
from .buttons.page import ButtonsPage
from .device_events import start_event_watch
from .device_read import device_read_disabled, start_snapshot_read
from .dpi.page import DpiPage
from .lighting.page import LightingPage
from .lighting_preview import start_lighting_write
from .live_lighting import LiveLighting, buttons_from_decoded, dpi_from_decoded, lighting_from_decoded
from .macros.page import MacrosPage
from .navigation import PAGE_NAMES, NavigationState
from .profile import ProfilePills
from .settings import SettingsPage
from ..macro_library import MacroLibraryError
from ..profile_session import DirtyProfileReplacementError, ProfileLoadError, ProfileSaveError
from ..app_store import default_profile_library_dir
from ..device_decode import decode_snapshot
from ..workspace import VenusWorkspace

APPLICATION_ID = "io.github.berry_dingle.VenusMouse"
WINDOW_WIDTH = 1100
WINDOW_HEIGHT = 780
OUTER_MARGIN = 13
# Working-set edits are persisted this long after the last change.
AUTOSAVE_DELAY_MS = 400

PAGE_LABELS = {
    "buttons": "Buttons",
    "dpi": "DPI",
    "lighting": "Lighting",
    "macros": "Macros",
    "settings": "Settings",
}
NAV_ICON_NAMES = {
    "buttons": "input-mouse-symbolic",
    "dpi": "find-location-symbolic",
    "lighting": "display-brightness-symbolic",
    "macros": "view-grid-symbolic",
    "settings": "preferences-system-symbolic",
}


TITLE_FONT_FILE = "AlexBrush-Regular.ttf"  # SIL OFL 1.1, see fonts/AlexBrush-OFL.txt


def load_app_fonts() -> bool:
    """Make the bundled title font available to this process only.

    Pango loads the font file into the app's font map; nothing is installed
    on the system. Returns False if the font could not be added (the title
    then falls back to the normal UI font).
    """
    from importlib.resources import files
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import PangoCairo
    path = files("venus_profile.gui") / "data" / "fonts" / TITLE_FONT_FILE
    font_map = PangoCairo.FontMap.get_default()
    try:
        return bool(font_map.add_font_file(str(path)))
    except (AttributeError, GLib.Error):
        return False


def apply_css() -> None:
    display = Gdk.Display.get_default()
    if display is None:
        return
    load_app_fonts()
    provider = Gtk.CssProvider()
    provider.load_from_string(_css())
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _centre_window_controls(header: Gtk.Widget) -> None:
    for controls in _descendants(header, Gtk.WindowControls):
        controls.set_valign(Gtk.Align.CENTER)


def _descendants(widget: Gtk.Widget, kind: type):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, kind):
            yield child
        yield from _descendants(child, kind)
        child = child.get_next_sibling()


def _without_icon_button(layout: str | None) -> str:
    """A GTK decoration layout with the "icon" and "menu" buttons removed."""
    start, _sep, end = (layout or ":minimize,maximize,close").partition(":")
    clean = lambda side: ",".join(item for item in side.split(",") if item and item not in ("icon", "menu"))
    return f"{clean(start)}:{clean(end)}"


def install_app_icon() -> None:
    """Make the packaged app icon available by its ID (window, taskbar, About).

    The icons ship inside the package (``gui/data/icons/hicolor``), so the app
    shows its icon even when not installed system-wide; an installed copy in
    the user's icon theme works the same way.
    """
    display = Gdk.Display.get_default()
    if display is None:
        return
    from importlib.resources import files
    icons = files("venus_profile.gui") / "data" / "icons"
    Gtk.IconTheme.get_for_display(display).add_search_path(str(icons))
    Gtk.Window.set_default_icon_name(APPLICATION_ID)


def _css() -> str:
    from importlib.resources import files
    return (files("venus_profile.gui") / "styles.css").read_text(encoding="utf-8")


def _pfd_filter() -> Gio.ListStore:
    filters = Gio.ListStore.new(Gtk.FileFilter)
    profile_filter = Gtk.FileFilter()
    profile_filter.set_name("Venus profiles (*.pfd)")
    profile_filter.add_pattern("*.pfd")
    profile_filter.add_pattern("*.PFD")
    filters.append(profile_filter)
    return filters


def _macro_filter() -> Gio.ListStore:
    filters = Gio.ListStore.new(Gtk.FileFilter)
    macro_filter = Gtk.FileFilter()
    macro_filter.set_name("Venus macros (*.MSMACRO, *.MSDB)")
    for pattern in ("*.MSMACRO", "*.msmacro", "*.MSDB", "*.msdb"):
        macro_filter.add_pattern(pattern)
    filters.append(macro_filter)
    return filters


#: The mouse's profile is assumed to be this one until its profile button is pressed.
DEFAULT_ACTIVE_PROFILE = 1


#: Set to "1" to print one ``venus-mouse:`` line per write to the mouse (stderr).
DIAGNOSTICS_ENV = "VENUS_PROFILE_DIAGNOSTICS"


def diagnostics_enabled() -> bool:
    return os.environ.get(DIAGNOSTICS_ENV) == "1"


def log_mouse_write(kind: str, document: dict) -> None:
    """One terminal line per write to the mouse, only with VENUS_PROFILE_DIAGNOSTICS=1."""
    if not diagnostics_enabled():
        return
    status = document.get("status", "?")
    detail = document.get("message") or ""
    if kind == "profile" and document.get("active_profile") is not None:
        detail = f"active {document['active_profile']}" + (f"; {detail}" if detail else "")
    elif kind == "scroll" and document.get("scroll"):
        detail = "lines " + " ".join(str(value) for value in document["scroll"]) + (f"; {detail}" if detail else "")
    elif kind == "polling" and document.get("polling"):
        detail = "values " + " ".join(str(value) for value in document["polling"]) + (f"; {detail}" if detail else "")
    elif kind == "dpi" and document.get("dpi"):
        detail = "stages " + ", ".join(f"P{e['profile']}S{e['stage']}={e['raw']}" for e in document["dpi"]) \
            + (f"; {detail}" if detail else "")
    elif kind == "dpi_stage" and document.get("dpi_stage"):
        detail = "start-up " + ", ".join(f"P{e['profile']}=stage {int(e['stage']) + 1}" for e in document["dpi_stage"]) \
            + (f"; {detail}" if detail else "")
    elif kind in ("buttons", "live-buttons") and document.get("buttons"):
        detail = "buttons " + ", ".join(f"P{e['profile']}B{e['button']}={e['record']}" for e in document["buttons"]) \
            + (f"; {detail}" if detail else "")
    elif kind == "macros" and document.get("macros"):
        detail = "slots " + ", ".join(str(int(e["slot"]) + 1) for e in document["macros"]) \
            + (f"; {detail}" if detail else "")
    elif kind == "lighting" and document.get("written"):
        detail = "profiles " + ",".join(str(item["profile"]) for item in document["written"])
    print(f"venus-mouse: {kind} -> {status}" + (f" ({detail})" if detail else ""), file=sys.stderr, flush=True)


class VenusWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, workspace: VenusWorkspace | None = None,
                 *, read_mouse_on_start: bool = False, snapshot_runner=None,
                 event_watcher=None, lighting_writer=None) -> None:
        super().__init__(application=application)
        # snapshot_runner(on_done) starts a read of the mouse and returns an
        # object with cancel(); the default launches the read-only helper.
        self._snapshot_runner = snapshot_runner or start_snapshot_read
        # event_watcher(on_event, on_end) starts listening for the mouse's
        # DPI/profile events and returns an object with stop(); the default
        # launches the listen-only event helper.
        self._event_watcher = event_watcher or start_event_watch
        self._mouse_read = None
        self._event_watch = None
        self._mouse_stages: list[int | None] | None = None   # each profile's stage on the mouse
        self.active_mouse_profile: int | None = None         # 1-5: reported, or inferred on first DPI±
        self._banner_action = None
        self.workspace = workspace or VenusWorkspace.open()
        # Lighting changes go to the mouse straight away once it has been read
        # (lighting_writer(request, on_done) defaults to the one-shot helper).
        # Selecting a profile in the app also makes it the mouse's active profile.
        self._live_lighting = LiveLighting(self.workspace, lighting_writer or start_lighting_write,
                                           self._lighting_write_failed, on_profile=self._mouse_profile_switched,
                                           on_result=log_mouse_write, on_applied=self._applied_to_mouse,
                                           on_live_buttons=self._live_buttons_sent)
        self._close_after_lighting = False
        self._close_after_apply = False         # close overlay: Apply, then close
        self._close_after_save = False          # close overlay: Save As, then close
        self._skip_unapplied_check = False      # the overlay was answered for this close
        self.set_default_size(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.set_size_request(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.set_resizable(False)
        self.set_title("Venus Mouse")
        install_app_icon()
        self.set_icon_name(APPLICATION_ID)
        self._allow_close = False
        self._autosave_source: int | None = None
        self._autosave_error: str | None = None
        self._install_actions()

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())
        self._banner = Adw.Banner()
        self._banner.add_css_class("mouse-banner")
        self._banner.connect("button-clicked", self._banner_clicked)
        toolbar.add_top_bar(self._banner)
        self._toasts = Adw.ToastOverlay()
        self._body_overlay = Gtk.Overlay()
        self._body_overlay.set_child(self._build_body())
        self._reading_card = self._build_reading_card()
        self._body_overlay.add_overlay(self._reading_card)
        self._toasts.set_child(self._body_overlay)
        toolbar.set_content(self._toasts)
        self.set_content(toolbar)
        self.connect("close-request", self._close_requested)
        self.workspace.subscribe(self._workspace_changed)
        self._select_page("buttons")
        self.refresh_status()
        GLib.idle_add(self._report_startup)
        if read_mouse_on_start:
            GLib.idle_add(lambda: (self.read_mouse(), GLib.SOURCE_REMOVE)[1])

    # ------------------------------------------------------------ reading the mouse
    def _build_reading_card(self) -> Gtk.Box:
        """Centred "Reading from mouse…" card shown over the pages while reading."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("reading-card")
        card.set_halign(Gtk.Align.CENTER)
        card.set_valign(Gtk.Align.CENTER)
        # Adw.Spinner needs libadwaita 1.6; Gtk.Spinner keeps the app working on
        # 1.5 (e.g. Ubuntu 24.04), which the AppImage uses from the host.
        if hasattr(Adw, "Spinner"):
            spinner = Adw.Spinner()
        else:
            spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(36, 36)
        card.append(spinner)
        label = Gtk.Label(label="Reading from mouse…")
        label.add_css_class("reading-title")
        card.append(label)
        detail = Gtk.Label(label="Your saved settings are shown if the mouse can't be read.")
        detail.add_css_class("dim-label")
        card.append(detail)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("action-button")
        cancel.set_halign(Gtk.Align.CENTER)
        cancel.connect("clicked", lambda _button: self.cancel_mouse_read())
        card.append(cancel)
        self._reading_cancel = cancel
        card.set_visible(False)
        return card

    def read_mouse(self) -> bool:
        """Start reading the mouse (no-op if already reading or disabled)."""
        if self._mouse_read is not None or device_read_disabled():
            return False
        if self._live_lighting.busy:              # never read while a lighting write runs
            self._live_lighting.when_idle(self.read_mouse)
            return True
        self._live_lighting.suspend()
        self._refresh_lighting_note()
        self._refresh_apply()
        self._banner.set_revealed(False)
        self._reading_card.set_visible(True)
        self._body_overlay.get_child().set_sensitive(False)
        self._mouse_read = self._snapshot_runner(self._mouse_read_done)
        return True

    def cancel_mouse_read(self) -> None:
        if self._mouse_read is not None:
            self._mouse_read.cancel()

    def _confirm_read_mouse(self) -> None:
        if self.workspace.edited_slots():
            self._confirm(
                "Read settings from the mouse?",
                "The mouse's settings replace edits made since your profiles were loaded.",
                "Read", self.read_mouse, destructive=False,
            )
        else:
            self.read_mouse()

    def _mouse_read_done(self, document: dict) -> None:
        self._mouse_read = None
        self._reading_card.set_visible(False)
        self._body_overlay.get_child().set_sensitive(True)
        status = document.get("status")
        if status == "ok":
            try:
                decoded = decode_snapshot(document)
                report = self.workspace.apply_device_read(decoded)
            except (ValueError, KeyError, TypeError) as exc:
                self._show_banner(f"The mouse's settings could not be decoded ({exc}); showing saved settings",
                                  "Read again", "retry")
                return
            self._mouse_stages = [profile.current_stage for profile in decoded.profiles]
            if decoded.active_profile is not None:       # the mouse wins (0x002C)
                self.active_mouse_profile = decoded.active_profile
                if self.workspace.selected_slot != decoded.active_profile:
                    self.workspace.select_slot(decoded.active_profile)
                    self.workspace.remember_selection()
            self._live_lighting.mouse_read({profile.number: lighting_from_decoded(profile.lighting)
                                            for profile in decoded.profiles}, decoded.active_profile,
                                           [profile.polling_enum for profile in decoded.profiles],
                                           dpi_from_decoded(decoded.profiles), buttons_from_decoded(decoded.profiles),
                                           decoded.macro_bodies, decoded.scroll_lines,
                                           [profile.current_stage for profile in decoded.profiles])
            self._refresh_apply()
            self._refresh_lighting_note()
            self._start_event_watch()
            if report.changed:
                self.toast("Settings read from the mouse")
            else:
                self.toast("The mouse matches your saved settings")
            if report.notices:
                self._last_read_notices = list(report.notices)
                self._show_banner(f"{len(report.notices)} setting(s) on the mouse couldn't be read; "
                                  "those keep their saved values", "Details", "details")
            return
        message = str(document.get("message") or "").strip()
        if status == "cancelled":
            self._show_banner("Reading the mouse was cancelled — showing saved settings", "Read again", "retry")
        elif status == "no-device":
            self._show_banner("No mouse found — showing saved settings", "Read again", "retry")
        elif status == "permission-denied":
            self._show_banner("The mouse can't be accessed — showing saved settings", "How to fix", "access")
        elif status == "multiple-devices":
            self._show_banner("More than one Venus mouse is connected — showing saved settings",
                              "Read again", "retry")
        else:
            detail = f" ({message})" if message else ""
            self._show_banner(f"The mouse couldn't be read{detail} — showing saved settings", "Read again", "retry")

    # ------------------------------------------------------------ live mouse events
    def _start_event_watch(self) -> None:
        if self._event_watch is not None or device_read_disabled():
            return
        self._event_watch = self._event_watcher(self._mouse_event, self._event_watch_ended)

    def stop_event_watch(self) -> None:
        watch, self._event_watch = self._event_watch, None
        if watch is not None:
            watch.stop()

    def _mouse_event(self, document: dict) -> None:
        """A DPI± or profile-button press on the mouse (the mouse wins)."""
        kind = document.get("event")
        if kind == "profile":
            profile = int(document["profile"]) + 1
            self.active_mouse_profile = profile
            follow = self._live_lighting.mouse_profile_event(profile)   # False: echo of an app switch
            if follow and self.workspace.selected_slot != profile:
                self.workspace.select_slot(profile)
                self.workspace.remember_selection()
        elif kind == "dpi-stage":
            stage = int(document["stage"])
            if self.active_mouse_profile is None:
                self.active_mouse_profile = self._infer_active_profile(stage)
            self._apply_mouse_stage(self.active_mouse_profile, stage)

    def _infer_active_profile(self, stage: int) -> int:
        """Which profile the mouse is on, before its profile button is pressed.

        The mouse only reports its profile when the button is pressed, and its
        memory catches up with DPI± only after a delay, so assume profile 1
        (user decision). If the pressed stage is not one DPI± step from profile
        1's stage but is for exactly one other profile, take that one instead.
        """
        assumed = DEFAULT_ACTIVE_PROFILE
        candidates = [profile for profile in range(1, 6) if stage in self._dpi_steps_from(profile)]
        if assumed in candidates or len(candidates) != 1:
            return assumed
        return candidates[0]

    def _dpi_steps_from(self, profile: int) -> set[int]:
        """Stages one DPI+ or DPI− press away from the profile's current stage (zero-based).

        DPI± stops at the first and last enabled stage; it does not wrap
        (user-confirmed on the mouse, 2026-11-19).
        """
        current = self._mouse_stages[profile - 1] if self._mouse_stages else None
        settings = self.workspace.profile(profile)
        enabled = [index for index in range(5) if settings.dpi_stage_enabled(index + 1)]
        if current is None or current not in enabled:
            return set(enabled)
        position = enabled.index(current)
        return {enabled[index] for index in (position - 1, position + 1) if 0 <= index < len(enabled)}

    def _apply_mouse_stage(self, profile: int, stage: int) -> None:
        if self._mouse_stages is not None:
            self._mouse_stages[profile - 1] = stage
        self._live_lighting.mouse_dpi_stage_event(profile, stage)   # the mouse is there: nothing to Apply
        self.workspace.apply_mouse_dpi_stage(profile, stage)

    def _event_watch_ended(self, document: dict) -> None:
        self._event_watch = None
        self.active_mouse_profile = None
        status = document.get("status")
        if status == "disconnected":
            self._live_lighting.suspend()
            self._refresh_lighting_note()
            self._refresh_apply()
            self._show_banner("The mouse was disconnected — showing saved settings", "Read again", "retry")
        else:
            message = str(document.get("message") or "").strip()
            detail = f" ({message})" if message else ""
            self._show_banner(f"Live updates from the mouse stopped{detail}", "Read again", "retry")

    # ------------------------------------------------------------ Apply
    def _refresh_apply(self) -> None:
        """Apply commits edits: enabled when something would be sent or any profile shows edits."""
        live = self._live_lighting
        enabled = live.live and not live.applying and (live.has_pending_apply() or bool(self.workspace.edited_slots()))
        self._actions["apply-to-mouse"].set_enabled(enabled)

    def _apply_to_mouse(self) -> None:
        if not self._live_lighting.live:
            return
        if self._live_lighting.apply():
            self._refresh_apply()                       # sending; _applied_to_mouse follows
        else:
            self._applied_to_mouse()                    # nothing to send (e.g. PC-only settings): just commit

    def _live_buttons_sent(self, keys) -> None:
        """Buttons sent live (slot move or macro loop change): commit them, then refresh Apply.

        A loop-only change leaves the profile file as it was, so commit_buttons
        has nothing to notify; the Apply state must still drop the sent records.
        """
        self.workspace.commit_buttons(keys)
        self._refresh_apply()

    def _applied_to_mouse(self) -> None:
        self.workspace.mark_applied()                  # edited markers clear; Cancel returns here
        self._refresh_apply()
        self.toast("Applied to the mouse")
        if self._close_after_apply:
            self._close_after_apply = False
            self._close_now()

    def _mouse_profile_switched(self, profile: int) -> None:
        """The app made ``profile`` the mouse's active profile (read back)."""
        self.active_mouse_profile = profile

    def _refresh_lighting_note(self) -> None:
        self.pages["lighting"].set_mouse_live(self._live_lighting.live)

    def _lighting_write_failed(self, document: dict) -> None:
        self._close_after_apply = False                 # the failure banner explains; stay open
        self._refresh_lighting_note()
        self._refresh_apply()
        message = str(document.get("message") or document.get("status") or "").strip()
        detail = f" ({message})" if message else ""
        if document.get("status") == "permission-denied":
            self._show_banner("Changes couldn't be sent: the mouse can't be accessed", "How to fix", "access")
        else:
            self._show_banner(f"Changes couldn't be sent to the mouse{detail}; nothing more is sent until "
                              "the mouse is read again", "Read again", "retry")

    def _show_banner(self, title: str, button: str, action: str) -> None:
        self._banner.set_title(title)
        self._banner.set_button_label(button)
        self._banner_action = action
        self._banner.set_revealed(True)

    def _banner_clicked(self, _banner) -> None:
        action = self._banner_action
        if action == "retry":
            self.read_mouse()
        elif action == "access":
            self._banner.set_revealed(False)
            self._select_page("settings")
            self.pages["settings"].show_mouse_access()
        elif action == "details":
            self._banner.set_revealed(False)
            self._alert("Settings kept from your saved profiles",
                        "\n".join(getattr(self, "_last_read_notices", [])))

    # ------------------------------------------------------------ layout
    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        header.add_css_class("app-header")
        # The branding already shows the logo; drop the desktop layout's
        # window-icon/menu button (it would repeat the app icon) while keeping
        # the user's placement of minimise/maximise/close.
        header.set_decoration_layout(_without_icon_button(
            self.get_settings().get_property("gtk-decoration-layout")))
        branding = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)
        branding.add_css_class("header-branding")
        branding.set_valign(Gtk.Align.CENTER)
        apply_accessible_label(branding, "Venus Mouse — Wired MMO Mouse Utility")
        icon = Gtk.Image.new_from_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(icon_bytes())))
        icon.add_css_class("header-branding-icon")
        icon.set_pixel_size(46)
        icon.set_valign(Gtk.Align.CENTER)
        icon.set_can_target(False)
        icon.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        branding.append(icon)
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title.add_css_class("header-identity")
        title.set_valign(Gtk.Align.CENTER)
        app_title = Gtk.Label(label="Venus Mouse", xalign=0)
        app_title.add_css_class("app-title")
        title.append(app_title)
        subtitle = Gtk.Label(label="Wired MMO Mouse Utility", xalign=0)
        subtitle.add_css_class("dim-label")
        subtitle.add_css_class("app-subtitle")
        title.append(subtitle)
        branding.append(title)
        header.pack_start(branding)

        # Page navigation sits in the header (trial placement); the profile
        # pills live in the profile action bar under the pages.
        nav = self._build_navigation()
        nav.set_valign(Gtk.Align.CENTER)
        header.set_title_widget(nav)

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_menu_model(self._build_menu())
        menu_button.set_tooltip_text("Profiles, macros and more")
        apply_accessible_label(menu_button, "Main menu")
        menu_button.set_valign(Gtk.Align.CENTER)
        header.pack_end(menu_button)
        self._menu_button = menu_button
        # Minimise/close: centred like the menu button. Filling the header
        # gave them a 60 px tall hit area next to the menu button's 34 px.
        # Adwaita creates its window controls once the header is in the
        # window, so centre them when it is mapped (repeat-safe).
        header.connect("map", _centre_window_controls)
        return header

    def _build_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        profile = Gio.Menu()
        profile.append("Save Profile As…", "win.save-as")
        profile.append("Load Profile File…", "win.load-file")
        profile.append("Restore Factory Profile…", "win.restore-profile")
        profile.append("Reset All Profiles…", "win.reset-all")
        menu.append_section("Profile", profile)
        macros = Gio.Menu()
        macros.append("Import Macro Files…", "win.import-macros")
        macros.append("Import Windows MacroDB Folder…", "win.import-macrodb")
        macros.append("Export Macros for Windows…", "win.export-macrodb")
        menu.append_section("Macros", macros)
        other = Gio.Menu()
        other.append("Read from Mouse", "win.read-mouse")
        other.append("Apply Preview…", "win.apply-preview")
        other.append("About Venus Mouse", "win.about")
        menu.append_section(None, other)
        return menu

    def _build_navigation(self) -> Gtk.Box:
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        nav.add_css_class("navigation-row")
        nav.set_halign(Gtk.Align.CENTER)
        self._nav_buttons: dict[str, Gtk.Button] = {}
        self._navigation = NavigationState()
        for name in PAGE_NAMES:
            label = PAGE_LABELS[name]
            tab = Gtk.Button()
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            icon = Gtk.Image.new_from_icon_name(NAV_ICON_NAMES[name])
            icon.set_pixel_size(16)
            content.append(icon)
            content.append(Gtk.Label(label=label))
            tab.set_child(content)
            apply_accessible_label(tab, label)
            tab.set_tooltip_text(f"Show {label}")
            tab.connect("clicked", self._select_page, name)
            self._nav_buttons[name] = tab
            nav.append(tab)
        return nav

    def _build_body(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(OUTER_MARGIN)
        root.set_margin_bottom(OUTER_MARGIN)
        root.set_margin_start(OUTER_MARGIN)
        root.set_margin_end(OUTER_MARGIN)
        stack = Gtk.Stack()
        stack.add_css_class("page-stack")
        stack.set_vexpand(True)
        self.pages = {
            "buttons": ButtonsPage(self.workspace),
            "dpi": DpiPage(self.workspace),
            "lighting": LightingPage(self.workspace),
            "macros": MacrosPage(self.workspace, window=self),
            "settings": SettingsPage(self.workspace),
        }
        for name, child in self.pages.items():
            stack.add_named(child, name)
        root.append(stack)
        self._stack = stack
        root.append(self._build_profile_bar())
        apply_pointer_cursor_tree(root)
        return root

    def _build_profile_bar(self) -> Gtk.Box:
        """Windows-style profile row: what the slot holds, then its actions."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("profile-action-bar")
        self._profile_pills = ProfilePills(self.workspace)
        self._profile_pills.set_valign(Gtk.Align.CENTER)
        self._profile_pills.set_halign(Gtk.Align.START)
        self._profile_pills.set_hexpand(True)
        bar.append(self._profile_pills)
        self._profile_buttons: dict[str, Gtk.Button] = {}
        for key, label, action, tooltip in (
            ("save-as", "Save As", "win.save-as",
             "Save this profile to a named file (Ctrl+Shift+S)"),
            ("load-file", "Load File", "win.load-file",
             "Load a named profile file into this profile slot (Ctrl+O)"),
            ("restore", "Restore", "win.restore-profile",
             "Reset this profile slot to factory settings"),
            ("reset-all", "Reset All", "win.reset-all",
             "Reset all five profile slots to factory settings"),
            ("cancel", "Cancel", "win.cancel-edits",
             "Discard edits made since this profile was loaded"),
            ("apply", "Apply", "win.apply-to-mouse",
             "Send DPI, polling, scroll and button changes to the mouse (all five profiles). "
             "Lighting and the active profile are sent straight away."),
        ):
            button = Gtk.Button(label=label)
            button.add_css_class("action-button")
            button.add_css_class("profile-action-button")
            button.set_valign(Gtk.Align.CENTER)
            if action is not None:
                button.set_action_name(action)
            else:
                button.set_sensitive(False)
            button.set_tooltip_text(tooltip)
            apply_accessible_label(button, tooltip)
            self._profile_buttons[key] = button
            bar.append(button)
        return bar

    # ------------------------------------------------------------ actions
    def _install_actions(self) -> None:
        handlers = {
            "save-as": self._choose_save_as,
            "load-file": self._choose_load_file,
            "restore-profile": self._confirm_restore_profile,
            "reset-all": self._confirm_reset_all,
            "cancel-edits": self._confirm_cancel_edits,
            "import-macros": self._choose_import_macros,
            "import-macrodb": self._choose_import_macrodb,
            "export-macrodb": self._choose_export_macrodb,
            "read-mouse": self._confirm_read_mouse,
            "apply-preview": self._show_apply_preview,
            "apply-to-mouse": self._apply_to_mouse,
            "about": self._show_about,
        }
        self._actions: dict[str, Gio.SimpleAction] = {}
        for name, handler in handlers.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _param, callback=handler: callback())
            self.add_action(action)
            self._actions[name] = action
        self._actions["apply-to-mouse"].set_enabled(False)
        application = self.get_application()
        if application is not None:
            application.set_accels_for_action("win.save-as", ["<Control><Shift>s"])
            application.set_accels_for_action("win.load-file", ["<Control>o"])

    # ------------------------------------------------------------ status
    def _workspace_changed(self, reason: str) -> None:
        self.refresh_status()
        for cause, rewritten in (("slot change re-points", self.workspace.take_slot_rewritten_buttons()),
                                 ("loop change updates", self.workspace.take_loop_rewritten_buttons())):
            if rewritten and self._live_lighting.live:
                self._live_lighting.push_live_buttons(rewritten)   # buttons follow live (option b)
                if diagnostics_enabled():
                    print(f"venus-mouse: {cause} " + ", ".join(f"P{p}B{b}" for p, b in sorted(rewritten)),
                          file=sys.stderr, flush=True)
        self._live_lighting.sync()
        if reason == "lighting":
            # Lighting is sent live, so it is committed at once (never shown as pending, never
            # enables Apply); skipping the Apply refresh keeps colour-picker drags fast.
            if self._live_lighting.live:
                self.workspace.commit_lighting(self.workspace.selected_slot)
        elif reason != "lighting-committed":
            self._refresh_apply()
        if reason not in ("saved", "selection"):
            self._schedule_autosave()

    def refresh_status(self) -> None:
        workspace = self.workspace
        slot = workspace.selected_slot
        edited = workspace.edited_since_load(slot)
        self._actions["cancel-edits"].set_enabled(edited)

    # ------------------------------------------------------------ autosave
    def _schedule_autosave(self) -> None:
        """Persist the working set shortly after the latest change."""
        if self._autosave_source is not None:
            GLib.source_remove(self._autosave_source)
        self._autosave_source = GLib.timeout_add(AUTOSAVE_DELAY_MS, self._autosave_timeout)

    def _autosave_timeout(self) -> bool:
        self._autosave_source = None
        self.autosave_now()
        return GLib.SOURCE_REMOVE

    def autosave_now(self) -> bool:
        """Write pending working-set changes now; returns False on failure."""
        if self._autosave_source is not None:
            GLib.source_remove(self._autosave_source)
            self._autosave_source = None
        if not self.workspace.dirty:
            return True
        try:
            self.workspace.save_all()
        except (ProfileSaveError, OSError, MacroLibraryError) as exc:
            macro_error = isinstance(exc.__cause__, MacroLibraryError) or isinstance(exc, MacroLibraryError)
            if macro_error and self.pages["macros"].presentation.unfinished_macro_problems():
                # An unfinished macro (e.g. a key pressed but not yet
                # released, mid-recording). Profiles were saved; the macro
                # saves itself once complete and the Macros page explains
                # why it is waiting. No dialog: it would steal focus.
                # Other macro errors (e.g. the trash is unavailable) fall
                # through and are shown.
                return False
            message = str(exc)
            if message != self._autosave_error:
                self._autosave_error = message
                self._alert("Changes could not be saved", message)
            return False
        self._autosave_error = None
        return True

    def refresh_profile_status(self) -> None:
        """Compatibility name used by page adapters."""
        self.refresh_status()

    def toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast.new(message))

    def _report_startup(self) -> bool:
        report = self.workspace.startup
        if report.problems:
            self._alert("Some saved data could not be restored", "\n".join(report.problems))
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------ navigation
    def _select_page(self, _button_or_name, name: str | None = None) -> None:
        page = _button_or_name if isinstance(_button_or_name, str) else name
        if page is None:
            return
        page = self._navigation.select(page)
        self._stack.set_visible_child_name(page)
        for item, button in self._nav_buttons.items():
            active = item == page
            if active:
                button.add_css_class("active-tab")
            else:
                button.remove_css_class("active-tab")
            set_accessible_selected(button, active)

    # ------------------------------------------------------------ dialogs
    def _alert(self, heading: str, body: str) -> Adw.AlertDialog:
        dialog = Adw.AlertDialog.new(heading, body)
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.present(self)
        return dialog

    def _confirm(self, heading: str, body: str, label: str, callback, *, destructive: bool = True) -> Adw.AlertDialog:
        dialog = Adw.AlertDialog.new(heading, body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("confirm", label)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance(
            "confirm", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED
        )
        dialog.connect("response", lambda _dialog, response: callback() if response == "confirm" else None)
        dialog.present(self)
        return dialog

    # ------------------------------------------------------------ profiles
    def profile_library_folder(self) -> Path:
        folder = default_profile_library_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return folder

    def _choose_save_as(self) -> None:
        slot = self.workspace.selected_slot
        name = self.workspace.loaded_name(slot)
        if name == "Factory profile":
            name = f"Profile {slot}"
        dialog = Gtk.FileDialog(title=f"Save Profile {slot} As", modal=True, initial_name=f"{name}.pfd")
        dialog.set_filters(_pfd_filter())
        dialog.set_initial_folder(Gio.File.new_for_path(str(self.profile_library_folder())))
        dialog.save(self, None, self._save_as_chosen, slot)

    def _save_as_chosen(self, dialog, result, slot: int) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            self._close_after_save = False              # save dialog cancelled: stay open
            return
        path = file.get_path() if file is not None else None
        if path is None:
            self._alert("Could not save", "The destination has no local path.")
            return
        chosen = Path(path)
        # The save dialog itself confirmed replacing the exact file it returned.
        self.save_profile_as(chosen, slot, dialog_confirmed=chosen.exists())

    def save_profile_as(self, path: Path, slot: int, *, dialog_confirmed: bool = False,
                        confirmed: bool = False) -> bool:
        """Save As to ``path``; an existing file is replaced only once confirmed."""
        if path.suffix.lower() != ".pfd":
            path = path.with_name(path.name + ".pfd")
            dialog_confirmed = False  # the dialog confirmed a different name
        replace = path.exists()
        if replace and not (dialog_confirmed or confirmed):
            self._confirm(
                f"Replace {path.name}?",
                f"A profile named {path.stem} already exists in {path.parent}. Replace it with Profile {slot}?",
                "Replace", lambda: self.save_profile_as(path, slot, confirmed=True),
            )
            return False
        try:
            self.workspace.save_profile_as(path, slot=slot, replace_existing=replace)
        except (ProfileSaveError, ProfileLoadError, OSError) as exc:
            self._alert("Could not save", str(exc))
            return False
        self.toast(f"Saved Profile {slot} as {path.stem}")
        if self._close_after_save:
            self._close_after_save = False
            self._close_now()
        return True

    def _choose_load_file(self) -> None:
        slot = self.workspace.selected_slot
        dialog = Gtk.FileDialog(title=f"Load a Profile into Profile {slot}", modal=True)
        dialog.set_filters(_pfd_filter())
        dialog.set_initial_folder(Gio.File.new_for_path(str(self.profile_library_folder())))
        dialog.open(self, None, self._load_file_chosen, slot)

    def _load_file_chosen(self, dialog, result, slot: int) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        path = file.get_path() if file is not None else None
        if path is None:
            self._alert("Could not load", "The selected file has no local path.")
            return
        self.load_profile(Path(path), slot)

    def load_profile(self, path: Path, slot: int, *, confirmed: bool = False) -> None:
        if not confirmed and self.workspace.edited_since_load(slot):
            self._confirm(
                f"Replace Profile {slot}?",
                f"Profile {slot} has edits since it was loaded. Loading {path.name} will discard them.",
                "Replace", lambda: self.load_profile(path, slot, confirmed=True),
            )
            return
        try:
            self.workspace.load_profile(path, slot=slot)
        except (ProfileLoadError, DirtyProfileReplacementError, OSError) as exc:
            self._alert(f"Could not load into Profile {slot}", str(exc))
            return
        self.toast(f"Loaded {path.stem} into Profile {slot}")
        mismatches = [
            note for note in self.workspace.reference_mismatches() if note.startswith(f"Profile {slot} ")
        ]
        if mismatches:
            self._alert(
                "This profile uses macros that are not in your device slots",
                "\n".join(mismatches)
                + "\n\nProfile files do not contain macro actions. Import the matching Windows "
                "MacroDB folder (main menu → Import Windows MacroDB Folder…) or reassign these buttons.",
            )

    def _confirm_restore_profile(self) -> None:
        slot = self.workspace.selected_slot
        self._confirm(
            f"Restore Profile {slot} to factory settings?",
            "This profile slot is replaced with the shipped factory profile. "
            "Named profile files are not changed.",
            "Restore", lambda: self._do(self.workspace.restore_defaults, slot,
                                        message=f"Profile {slot} restored to factory settings"),
        )

    def _confirm_reset_all(self) -> None:
        self._confirm(
            "Reset all five profiles?",
            "Every profile slot is replaced with its shipped factory profile. "
            "Macros and named profile files are not affected.",
            "Reset All", lambda: self._do(self.workspace.reset_all, message="All profiles reset to factory settings"),
        )

    def _confirm_cancel_edits(self) -> None:
        slot = self.workspace.selected_slot
        if not self.workspace.edited_since_load(slot):
            return
        name = self.workspace.loaded_name(slot)
        self._confirm(
            f"Discard edits to Profile {slot}?",
            f"Profile {slot} returns to {name} as it was loaded.",
            "Discard", lambda: self._do(self.workspace.cancel_edits, slot,
                                        message=f"Edits to Profile {slot} discarded"),
        )

    def _do(self, function, *args, message: str = "") -> None:
        try:
            function(*args)
        except (ProfileLoadError, ProfileSaveError, MacroLibraryError, OSError) as exc:
            self._alert("Operation failed", str(exc))
            return
        if message:
            self.toast(message)

    # ------------------------------------------------------------ macros
    def _choose_import_macros(self) -> None:
        dialog = Gtk.FileDialog(title="Import Macro Files", modal=True)
        dialog.set_filters(_macro_filter())
        folder = self.workspace.library.folder
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))
        dialog.open_multiple(self, None, self._import_macros_chosen)

    def _import_macros_chosen(self, dialog, result) -> None:
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return
        paths = [Path(item.get_path()) for item in files if item.get_path()]
        self.import_macro_files(paths)

    def import_macro_files(self, paths: list[Path]) -> None:
        imported, errors = [], []
        for path in paths:
            try:
                imported.append(self.workspace.library.import_file(path))
            except MacroLibraryError as exc:
                errors.append(str(exc))
        if imported:
            self.workspace.notify("macros")
            self.toast(f"Imported {len(imported)} macro{'s' if len(imported) != 1 else ''}")
        if errors:
            self._alert("Some macros were not imported", "\n".join(errors))

    def _choose_import_macrodb(self) -> None:
        dialog = Gtk.FileDialog(title="Choose a Windows MacroDB Folder", modal=True)
        dialog.select_folder(self, None, self._import_macrodb_chosen)

    def _import_macrodb_chosen(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder is not None and folder.get_path():
            self.import_macrodb(Path(folder.get_path()))

    def import_macrodb(self, folder: Path) -> None:
        try:
            imported, notes = self.workspace.library.import_macrodb(folder, populate_bank=True)
        except MacroLibraryError as exc:
            self._alert("Could not import macros", str(exc))
            return
        self.workspace.notify("macros")
        self.toast(f"Imported {len(imported)} macros from {folder.name}")
        if notes:
            self._alert("Macro import notes", "\n".join(notes))

    def _choose_export_macrodb(self) -> None:
        dialog = Gtk.FileDialog(title="Export Macros as a Windows MacroDB Folder", modal=True,
                                initial_name="MacroDB")
        dialog.save(self, None, self._export_macrodb_chosen)

    def _export_macrodb_chosen(self, dialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        if file is not None and file.get_path():
            self.export_macrodb(Path(file.get_path()))

    def export_macrodb(self, folder: Path) -> None:
        try:
            self.workspace.library.export_windows_macrodb(folder)
        except (MacroLibraryError, OSError) as exc:
            self._alert("Could not export macros", str(exc))
            return
        self.toast(f"Exported the device slots to {folder.name}")

    # ------------------------------------------------------------ misc
    def _show_apply_preview(self) -> None:
        from .apply_preview import present_apply_preview
        present_apply_preview(self, self.workspace)

    def _show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name="Venus Mouse",
            application_icon=APPLICATION_ID,
            version="0.2.0",
            comments=("Linux configuration utility for the UtechSmart Venus wired MMO mouse. "
                      "Profiles and macros are edited and saved on this computer; lighting, "
                      "the active profile and macro slots are sent to the mouse live; DPI, polling, "
                      "scroll speed and buttons on Apply."),
            developer_name="Venus Linux project",
        )
        about.present(self)

    # ------------------------------------------------------------ closing
    def _close_requested(self, _window) -> bool:
        """Closing keeps the working set: pending edits are written first."""
        if self._live_lighting.busy:
            # Let the running lighting write (and any change queued behind it)
            # finish, then close; the helper times out after 10 s at most.
            if not self._close_after_lighting:
                self._close_after_lighting = True
                self._live_lighting.when_idle(self.close)
            return True
        if not self._skip_unapplied_check and not self._allow_close and self._has_unapplied_changes():
            self._ask_about_unapplied_changes()
            return True
        if self._allow_close:
            self.stop_event_watch()
            self.workspace.remember_selection()
            return False
        if self.autosave_now():
            self.stop_event_watch()
            self.workspace.remember_selection()
            return False
        unfinished = self.pages["macros"].presentation.unfinished_macro_problems()
        body = (
            "A macro is not finished, so it could not be saved: " + "; ".join(unfinished)
            + ". Close anyway and lose those macro changes?"
            if unfinished else
            "Your latest edits could not be written to disk. Close anyway and lose them?"
        )
        dialog = Adw.AlertDialog.new("Changes could not be saved", body)
        dialog.add_response("cancel", "Keep Open")
        dialog.add_response("discard", "Close Anyway")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._close_response)
        dialog.present(self)
        self._skip_unapplied_check = False
        return True

    # ------------------------------------------------------------ unapplied changes on close
    def _has_unapplied_changes(self) -> bool:
        """DPI, polling or button edits that differ from the mouse (lighting/profile are already on it)."""
        return self._live_lighting.live and self._live_lighting.has_pending_apply()

    def _ask_about_unapplied_changes(self) -> None:
        dialog = Adw.AlertDialog.new("Unsaved changes",
                                     "You have unsaved changes, do you want to apply or save them now?")
        # Shown top to bottom as the user asked: Apply, Save, Discard, Cancel.
        # Libadwaita stacks responses in reverse order of adding, so add them reversed.
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard")
        dialog.add_response("save", "Save")
        dialog.add_response("apply", "Apply")
        dialog.set_default_response("apply")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._unapplied_response)
        dialog.present(self)
        self._unapplied_dialog = dialog

    def _unapplied_response(self, _dialog, response: str) -> None:
        self._unapplied_dialog = None
        if response == "apply":
            self._close_after_apply = True
            if not self._live_lighting.apply():          # nothing left to send after all
                self._close_after_apply = False
                self._close_now()
        elif response == "save":
            self._close_after_save = True
            self._choose_save_as()
        elif response == "discard":
            self._discard_unapplied_and_close()
        # "cancel": keep the app open

    def _discard_unapplied_and_close(self) -> None:
        """Discard: undo every profile's edits since it was read/applied/loaded, send nothing, close.

        After a mouse read each profile's Cancel baseline is the mouse's state
        (and Apply moves it on), so the saved working set matches the mouse
        again instead of keeping the unapplied edits for the next launch.
        """
        for slot in self.workspace.edited_slots():
            self.workspace.cancel_edits(slot)
        self._close_now()

    def _close_now(self) -> None:
        self._skip_unapplied_check = True
        self.close()

    def _close_response(self, _dialog, response: str) -> None:
        if response != "discard":
            return
        self._allow_close = True
        self.close()


class VenusApplication(Adw.Application):
    def __init__(self, workspace: VenusWorkspace | None = None,
                 flags: Gio.ApplicationFlags = Gio.ApplicationFlags.DEFAULT_FLAGS,
                 application_id: str | None = APPLICATION_ID) -> None:
        super().__init__(application_id=application_id, flags=flags)
        self._workspace = workspace

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        apply_css()

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            window = VenusWindow(self, self._workspace, read_mouse_on_start=True)
        window.present()


def prepare_gtk_environment() -> None:
    """Quiet two harmless start-up console warnings.

    * GTK's Vulkan renderer makes Mesa's RADV driver print "radv is not a
      conformant Vulkan implementation"; the OpenGL renderer avoids it. GTK 4.22
      names it ``gl`` (``ngl`` is deprecated and warns). An explicit
      ``GSK_RENDERER`` set by the user is respected.
    * Desktop GTK settings (e.g. Plasma's ``gtk-4.0/settings.ini``) may turn on
      the legacy ``gtk-application-prefer-dark-theme`` hint, which libadwaita
      warns about. The app owns its theme (styles.css), so the hint is cleared
      for this process only; the user's config file is never touched.
    """
    os.environ.setdefault("GSK_RENDERER", "gl")
    if not Gtk.init_check():
        return
    settings = Gtk.Settings.get_default()
    if settings is not None and settings.get_property("gtk-application-prefer-dark-theme"):
        settings.set_property("gtk-application-prefer-dark-theme", False)


def main(argv: list[str] | None = None) -> int:
    prepare_gtk_environment()
    return VenusApplication().run(None)
