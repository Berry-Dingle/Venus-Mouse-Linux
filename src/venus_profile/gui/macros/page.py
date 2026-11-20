"""Macro manager: persistent library, 15 device slots, and the action editor."""

from __future__ import annotations

from dataclasses import dataclass
import os

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk

from ..accessibility import apply_accessible_label, set_accessible_selected
from ...actions import (
    ACTION_DELAY_MAX_MS,
    ACTION_DELAY_QUANTUM_MS,
    ActionRecord,
    ActionType,
    MOUSE_MASK_NAMES,
)
from ...hid_usage import usage_from_gdk_keycode
from ...macro import ACTION_RECORD_CAPACITY
from ...macro_library import (
    DELAY_TYPE_DEFAULT,
    DELAY_TYPE_NONE,
    DELAY_TYPE_RECORDED,
    effective_default_delay_ms,
)
from .model import (
    LOOP_COUNT_MAX,
    LOOP_LABELS,
    DeviceSlotPresentation,
    LocalMacroPresentation,
    MacroPresentationState,
    format_action_display_value,
    library_macro_id,
    format_action_editor_value,
    parse_keyboard_editor,
)

MACRO_LIBRARY_COLUMN_WIDTH = 176
DEVICE_SLOTS_COLUMN_WIDTH = 176
MACRO_LEFT_CARD_WIDTH = 420
ACTION_VALUE_ZONE_WIDTH = 118
# Explicit insert buttons (key, label, accessible description), in display order.
# Mouse records use the confirmed button codes 1 (left) and 2 (right); the
# delay inserts a 10 ms explicit delay, matching the former dropdown entries.
INSERT_ACTIONS = (
    ("lmb-down", "LMB Down", "Add left mouse button press"),
    ("lmb-up", "LMB Up", "Add left mouse button release"),
    ("rmb-down", "RMB Down", "Add right mouse button press"),
    ("rmb-up", "RMB Up", "Add right mouse button release"),
)
# Default delay shown and stored when the field is empty (the Windows macro
# manager's default; it also inserts 10 ms for any typed value under 10).
DEFAULT_INSERT_DELAY_MS = 10


@dataclass(slots=True)
class InlineActionEditorState:
    macro_id: str
    action_id: str
    row: Gtk.Box
    editor: Gtk.Widget
    action_type: ActionType
    original_value: str
    validation_message: str | None = None
    closing: bool = False


@dataclass(slots=True)
class ActionDragState:
    """Provisional, widget-only reorder state kept during a live action drag."""

    action_id: str
    original_index: int
    original_order: tuple[str, ...]
    row: Gtk.Box
    pointer_viewport_y: float | None = None
    pointer_content_y: float | None = None
    grab_offset_x: float = 0.0
    grab_offset_y: float = 0.0
    grab_captured: bool = False
    lifted: Gtk.Box | None = None


class MacrosPage(Gtk.Box):
    """Two-card macro manager: visible library/slots and creator/actions."""

    def __init__(self, workspace, window=None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("page")
        self.workspace = workspace
        self._window = window
        self.presentation = MacroPresentationState(workspace)
        self.presentation.subscribe(self._refresh)
        self._recording: list | None = None
        self._recording_controller: Gtk.EventControllerKey | None = None
        self._recording_last_time: int | None = None
        self._recording_held: set[int] = set()
        self._library_rows: dict[str, Gtk.Button] = {}
        self._slot_rows: dict[str, Gtk.Box] = {}
        self._action_rows: dict[str, Gtk.Box] = {}
        self._rendered_library_key = None
        self._rendered_slots_key = None
        self._rendered_action_macro_id: str | None = None
        self._rendered_action_records = None
        self._rendered_action_ids = None
        self._inline_editor_state: InlineActionEditorState | None = None
        self._action_drag: ActionDragState | None = None
        self._action_autoscroll_id: int | None = None
        self._click_away_root: Gtk.Widget | None = None
        self._click_away_controller: Gtk.EventControllerLegacy | None = None
        self._programmatic_inline_update = False
        self._gesture_debug = os.environ.get("VENUS_MACRO_GESTURE_DEBUG") == "1"
        self._actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._actions_box.add_css_class("macro-action-list")
        self._library_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._slot_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self._editor_title = Gtk.Label(xalign=0)
        self._editor_summary = Gtk.Label(xalign=0)
        self._editor_warning = Gtk.Label(xalign=0, wrap=True)
        self._impact_label = Gtk.Label(xalign=0, wrap=True)
        self._loop_mode = Gtk.DropDown.new_from_strings(tuple(LOOP_LABELS[index] for index in range(4)))
        self._loop_count = Gtk.Entry(text="1", width_chars=1, max_length=3)
        self._loop_count.set_max_width_chars(3)
        self._syncing_widgets = False
        self.connect("notify::root", self._root_changed)
        self._build()
        self._refresh(self.presentation)

    def _root_changed(self, _page: Gtk.Widget, _param) -> None:
        root = self.get_root()
        if root is self._click_away_root:
            return
        if (
            self._click_away_root is not None
            and self._click_away_controller is not None
        ):
            self._click_away_root.remove_controller(self._click_away_controller)
        self._click_away_root = root if isinstance(root, Gtk.Widget) else None
        self._click_away_controller = None
        if self._click_away_root is None:
            return
        controller = Gtk.EventControllerLegacy()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("event", self._application_click_away_event)
        self._click_away_root.add_controller(controller)
        self._click_away_controller = controller

    def _build(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        outer.add_css_class("macros-regions")
        outer.set_vexpand(True)
        left = self._build_left_card()
        self._left_card = left
        # 395 + 25: room for the two classic list scrollbars (12px each, +1 so
        # the columns split evenly), so the library and slot rows keep their width.
        left.set_size_request(MACRO_LEFT_CARD_WIDTH, -1)
        left.set_hexpand(False)
        outer.append(left)
        right = self._build_right_card()
        self._right_card = right
        right.set_hexpand(True)
        outer.append(right)
        self.append(outer)

    @staticmethod
    def _heading(title: str, subtitle: str) -> Gtk.Box:
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.add_css_class("panel-title")
        heading.append(title_label)
        if subtitle:
            subtitle_label = Gtk.Label(label=subtitle, xalign=0, wrap=True)
            subtitle_label.add_css_class("panel-subtitle")
            heading.append(subtitle_label)
        return heading

    @staticmethod
    def _scroll(content: Gtk.Widget, *, minimum: int = 0, reserve_scrollbar: bool = False) -> Gtk.ScrolledWindow:
        scroll = Gtk.ScrolledWindow()
        # reserve_scrollbar: a classic scrollbar that is always present, so
        # its width is reserved and rows never shrink when scrolling starts.
        scroll.set_policy(Gtk.PolicyType.NEVER,
                          Gtk.PolicyType.ALWAYS if reserve_scrollbar else Gtk.PolicyType.AUTOMATIC)
        if reserve_scrollbar:
            scroll.set_overlay_scrolling(False)
            # GTK has no "nothing to scroll" style state, so track it: the
            # bar's space stays reserved but it is drawn invisibly.
            adjustment = scroll.get_vadjustment()
            update = lambda *_args, s=scroll: MacrosPage._sync_scroll_idle(s)
            adjustment.connect("changed", update)
            adjustment.connect("notify::page-size", update)
            adjustment.connect("notify::upper", update)
            MacrosPage._sync_scroll_idle(scroll)
        scroll.set_child(content)
        scroll.set_vexpand(True)
        if minimum:
            scroll.set_min_content_height(minimum)
        return scroll

    @staticmethod
    def _sync_scroll_idle(scroll: Gtk.ScrolledWindow) -> None:
        adjustment = scroll.get_vadjustment()
        if adjustment.get_upper() - adjustment.get_page_size() < 1:
            scroll.add_css_class("scroll-idle")
        else:
            scroll.remove_css_class("scroll-idle")

    def _build_left_card(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        card.add_css_class("card-panel")
        card.add_css_class("macros-left-card")
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        title = Gtk.Label(label="Macros", xalign=0)
        title.add_css_class("panel-title")
        title.set_valign(Gtk.Align.END)
        hint = Gtk.Label(label="• Drag a macro into a device slot", xalign=0)
        hint.add_css_class("macro-note")
        hint.set_valign(Gtk.Align.END)
        hint.set_margin_bottom(3)
        hint.set_hexpand(True)
        heading.append(title)
        heading.append(hint)
        import_button = Gtk.Button(icon_name="document-open-symbolic")
        import_button.add_css_class("flat")
        import_button.set_action_name("win.import-macros")
        import_button.set_tooltip_text("Import macro files (.MSMACRO/.MSDB)")
        apply_accessible_label(import_button, "Import macro files")
        heading.append(import_button)
        card.append(heading)
        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        columns.add_css_class("macro-bank-columns")
        # Library and Device Slots share the card width equally.
        columns.set_homogeneous(True)
        columns.append(self._build_library_column())
        columns.append(self._build_slots_column())
        card.append(columns)
        self._impact_bar = self._build_impact_bar()
        card.append(self._impact_bar)
        card.append(self._build_library_actions())
        return card

    def _build_library_column(self) -> Gtk.Box:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        column.set_size_request(MACRO_LIBRARY_COLUMN_WIDTH, -1)
        column.set_hexpand(True)
        column.add_css_class("macro-bank-column")
        title = Gtk.Label(label="Macro Library", xalign=0)
        title.add_css_class("section-label")
        column.append(title)
        self._library_list.add_css_class("macro-list")
        self._library_list.set_vexpand(True)
        library_scroll = self._scroll(self._library_list, minimum=320, reserve_scrollbar=True)
        self._library_scroll = library_scroll
        column.append(library_scroll)
        target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        target.connect("drop", self._drop_on_library)
        self._library_list.add_controller(target)
        apply_accessible_label(self._library_list, "Macro library saved on this computer; drag a slot here to unload it")
        return column

    def _build_slots_column(self) -> Gtk.Box:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        column.set_size_request(DEVICE_SLOTS_COLUMN_WIDTH, -1)
        column.set_hexpand(True)
        column.add_css_class("macro-bank-column")
        title = Gtk.Label(label="Device Slots", xalign=0)
        title.add_css_class("section-label")
        column.append(title)
        self._slot_list.add_css_class("macro-slot-list")
        self._slot_list.set_vexpand(True)
        slot_scroll = self._scroll(self._slot_list, minimum=320, reserve_scrollbar=True)
        self._slot_scroll = slot_scroll
        column.append(slot_scroll)
        apply_accessible_label(self._slot_list, "Device Slots; exactly 15 shared across all five profiles")
        return column

    def _build_impact_bar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        bar.add_css_class("macro-impact-bar")
        self._impact_label.add_css_class("macro-impact-text")
        self._impact_label.set_hexpand(True)
        bar.append(self._impact_label)
        self._cancel_impact = Gtk.Button(label="Cancel")
        self._cancel_impact.add_css_class("action-button")
        self._cancel_impact.connect("clicked", lambda _button: self.presentation.cancel_pending_impact())
        self._replace_impact = Gtk.Button(label="Replace / Unload")
        self._replace_impact.add_css_class("action-button")
        self._replace_impact.add_css_class("suggested-action")
        self._replace_impact.connect("clicked", lambda _button: self.presentation.confirm_pending_impact())
        apply_accessible_label(self._cancel_impact, "Cancel pending slot operation")
        apply_accessible_label(self._replace_impact, "Confirm pending slot operation and its assignment impact")
        bar.append(self._cancel_impact)
        bar.append(self._replace_impact)
        bar.set_visible(False)
        return bar

    def _build_library_actions(self) -> Gtk.Box:
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        actions.add_css_class("macro-library-actions")
        label = Gtk.Label(label="Library Actions", xalign=0)
        label.add_css_class("section-label")
        actions.append(label)
        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self._name_entry = Gtk.Entry(placeholder_text="Select or create a new macro")
        self._name_entry.set_hexpand(True)
        apply_accessible_label(self._name_entry, "Macro name")
        self._name_entry.connect("activate", self._name_entry_activated)
        name_row.append(self._name_entry)
        actions.append(name_row)
        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        button_row.set_homogeneous(True)
        self._new_button = self._library_button("New", self._new_macro)
        self._new_button.set_tooltip_text("Create a macro named from the name field (or New Macro)")
        self._duplicate_button = self._library_button("Duplicate", self._duplicate_macro)
        self._rename_button = self._library_button("Rename", self._rename_macro)
        self._delete_library_button = self._library_button("Delete", self._delete_macro)
        for button in (self._new_button, self._duplicate_button, self._rename_button, self._delete_library_button):
            button_row.append(button)
        actions.append(button_row)
        return actions

    @staticmethod
    def _library_button(label: str, callback) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.add_css_class("action-button")
        button.add_css_class("macro-library-button")
        if callback:
            button.connect("clicked", callback)
        apply_accessible_label(button, label)
        return button

    def _build_right_card(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("card-panel")
        card.add_css_class("macros-right-card")
        card.append(self._heading("Macro Creator", "Create and Edit a Macro"))
        card.append(self._build_creator_settings())
        creator = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        creator.add_css_class("macro-creator-columns")
        # The creator row absorbs all spare card height so the staging
        # sequence runs from the settings row down to the record row.
        creator.set_vexpand(True)
        staging = self._build_staging_area()
        staging.set_hexpand(True)
        staging.set_vexpand(True)
        creator.append(staging)
        creator.append(self._build_action_column())
        card.append(creator)
        card.append(self._build_record_row())
        card.append(self._build_editor_footer())
        return card

    def _build_creator_settings(self) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        row.add_css_class("macro-settings-row")
        self._editor_title.add_css_class("macro-editor-title")
        self._editor_title.set_hexpand(True)
        apply_accessible_label(self._editor_title, "Selected macro name")
        row.append(self._editor_title)
        self._loop_mode.connect("notify::selected", self._loop_changed)
        self._loop_mode.set_size_request(140, -1)
        apply_accessible_label(self._loop_mode, "Repeat mode")
        row.append(self._loop_mode)
        self._loop_count.set_sensitive(False)
        self._loop_count.set_size_request(62, -1)
        self._loop_count.connect("changed", self._loop_count_changed)
        apply_accessible_label(self._loop_count, f"Repeat count, 1 to {LOOP_COUNT_MAX}")
        self._loop_count.set_tooltip_text(f"Repeat count (1-{LOOP_COUNT_MAX})")
        row.append(self._loop_count)
        return row

    def _build_staging_area(self) -> Gtk.Box:
        area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        area.add_css_class("macro-staging-area")
        sequence_title = Gtk.Label(label="Staging sequence · drag rows to reorder", xalign=0)
        sequence_title.add_css_class("section-label")
        area.append(sequence_title)
        # Reserved classic scrollbar: never covers the right-aligned values and
        # rows keep their width as the list grows past the viewport.
        sequence_scroll = self._scroll(self._actions_box, minimum=210, reserve_scrollbar=True)
        sequence_scroll.set_vexpand(True)
        self._sequence_scroll = sequence_scroll
        action_target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        action_target.connect("motion", self._action_list_drag_motion)
        action_target.connect("drop", self._action_list_drag_drop)
        self._actions_box.add_controller(action_target)
        self._action_list_drop_target = action_target
        # The lifted row is rendered inside this overlay (not as the native,
        # OS-level drag icon) so it stays anchored to the grab point and clamped
        # to the staging viewport.
        sequence_overlay = Gtk.Overlay()
        sequence_overlay.set_vexpand(True)
        sequence_overlay.set_child(sequence_scroll)
        lifted_layer = Gtk.Overlay()
        lifted_layer.set_can_target(False)
        # This nested overlay is the full viewport coordinate plane.  Its lifted
        # child is width-allocated between margins derived from the source row's
        # final allocation; Gtk.Fixed's natural-size child semantics are avoided.
        lifted_layer.set_halign(Gtk.Align.FILL)
        lifted_layer.set_valign(Gtk.Align.FILL)
        lifted_layer.set_hexpand(True)
        lifted_layer.set_vexpand(True)
        sequence_overlay.add_overlay(lifted_layer)
        self._sequence_overlay = sequence_overlay
        self._lifted_layer = lifted_layer
        # The visible frame wraps the overlay rather than styling it, so the
        # overlay, scroller and lifted layer keep identical allocations.
        sequence_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sequence_frame.add_css_class("macro-sequence-frame")
        sequence_frame.set_overflow(Gtk.Overflow.HIDDEN)
        sequence_frame.set_vexpand(True)
        sequence_frame.append(sequence_overlay)
        self._sequence_frame = sequence_frame
        area.append(sequence_frame)
        return area

    def _build_record_row(self) -> Gtk.Box:
        record_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        record_row.add_css_class("macro-recording-row")
        self._record_row = record_row
        self._record_button = Gtk.Button(label="Record")
        self._record_button.add_css_class("action-button")
        self._record_button.add_css_class("macro-record-button")
        self._record_button.set_tooltip_text(
            "Record key presses typed into this window; they are added to the end of the macro"
        )
        self._record_button.connect("clicked", self._start_recording)
        apply_accessible_label(self._record_button, "Record keyboard actions")
        self._stop_button = Gtk.Button(label="Stop")
        self._stop_button.add_css_class("action-button")
        self._stop_button.set_sensitive(False)
        self._stop_button.set_tooltip_text("Stop recording and add the recorded actions")
        self._stop_button.connect("clicked", self._stop_recording)
        apply_accessible_label(self._stop_button, "Stop recording")
        self._record_delays = Gtk.CheckButton(label="Record delays")
        self._record_delays.set_tooltip_text(
            "Insert the measured time between key events as delay actions (turns off Delay). "
            "With neither option on, 10 ms is used."
        )
        self._record_delays.connect("toggled", self._record_delays_toggled)
        self._record_status = Gtk.Label(label="Keyboard only · this window", xalign=0)
        self._record_status.add_css_class("macro-row-meta")
        self._record_status.set_hexpand(True)
        self._editor_summary.add_css_class("macro-record-summary")
        record_row.append(self._record_button)
        record_row.append(self._stop_button)
        record_row.append(self._record_delays)
        record_row.append(self._record_status)
        record_row.append(self._editor_summary)
        return record_row

    def _build_action_column(self) -> Gtk.Box:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        column.add_css_class("macro-actions-column")
        column.set_size_request(116, -1)
        title = Gtk.Label(label="Macro Actions", xalign=0)
        title.add_css_class("section-label")
        column.append(title)
        # One explicit button per confirmed non-keyboard action; keyboard
        # presses/releases come from the Record function.
        self._insert_buttons: dict[str, Gtk.Button] = {}
        for key, label, tooltip in INSERT_ACTIONS:
            button = self._action_button(label, self._insert_action, key)
            button.set_tooltip_text(tooltip)
            apply_accessible_label(button, tooltip)
            self._insert_buttons[key] = button
            column.append(button)
        # [Delay][ ms ]: Windows "INSERT DEFAULT DELAY". An on/off toggle,
        # mutually exclusive with "Record delays"; while on, the default delay
        # is placed between actions (recorded and LMB/RMB-inserted).
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.add_css_class("macro-delay-row")
        # The toggle fills the row, but must not make the whole action column
        # expand (hexpand propagates upwards).
        row.set_hexpand(False)
        self._delay_toggle = Gtk.ToggleButton(label="Delay")
        self._delay_toggle.add_css_class("action-button")
        self._delay_toggle.add_css_class("macro-action-button")
        self._delay_toggle.add_css_class("modifier-toggle")
        self._delay_toggle.set_hexpand(True)
        self._delay_toggle.set_tooltip_text(
            "Insert default delay: put the delay entered beside it between actions "
            "(turns off Record delays). With neither option on, 10 ms is used."
        )
        apply_accessible_label(self._delay_toggle, "Insert default delay between actions")
        self._delay_toggle.connect("toggled", self._delay_toggled)
        row.append(self._delay_toggle)
        self._delay_entry = Gtk.Entry(placeholder_text=str(DEFAULT_INSERT_DELAY_MS),
                                      width_chars=3, max_width_chars=3, max_length=3)
        self._delay_entry.add_css_class("macro-delay-entry")
        self._delay_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self._delay_entry.set_tooltip_text(
            "Default delay in ms (up to 3 digits, like Windows). Under 10 inserts 10 ms; "
            "other values are rounded down to a multiple of 10 ms."
        )
        apply_accessible_label(self._delay_entry, "Default delay, in milliseconds")
        self._delay_entry.connect("changed", self._delay_entry_changed)
        row.append(self._delay_entry)
        column.append(row)
        self._up_button = self._action_button("Move Up", lambda _button: self._move_action(-1))
        self._up_button.add_css_class("macro-action-group-start")
        self._down_button = self._action_button("Move Down", lambda _button: self._move_action(1))
        self._delete_button = self._action_button("Delete", self._delete_action)
        self._cut_button = self._action_button("Cut", self._cut_action)
        self._copy_button = self._action_button("Copy", self._copy_action)
        self._paste_button = self._action_button("Paste", self._paste_action)
        for button in (self._up_button, self._down_button, self._delete_button,
                       self._cut_button, self._copy_button, self._paste_button):
            column.append(button)
        return column

    @staticmethod
    def _action_button(label: str, callback, *args) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.add_css_class("action-button")
        button.add_css_class("macro-action-button")
        button.connect("clicked", callback, *args)
        apply_accessible_label(button, label)
        return button

    def _build_editor_footer(self) -> Gtk.Box:
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.add_css_class("macro-editor-footer")
        self._editor_footer = footer
        self._macro_status = Gtk.Label(label="", xalign=0)
        self._macro_status.add_css_class("offline-status")
        self._macro_status.set_hexpand(True)
        footer.append(self._macro_status)
        self._revert_macro_button = Gtk.Button(label="Revert Macro")
        self._revert_macro_button.add_css_class("action-button")
        self._revert_macro_button.set_tooltip_text("Undo changes made since this macro was selected")
        self._revert_macro_button.connect("clicked", self._revert_macro)
        apply_accessible_label(self._revert_macro_button, "Undo changes made to the selected macro since it was selected")
        footer.append(self._revert_macro_button)
        return footer

    @staticmethod
    def _direction_label(action) -> Gtk.Label:
        """↓ for a press and ↑ for a release (the Windows macro manager's
        STATUS icons); blank for delays so the action names stay aligned."""
        record = action.record
        glyph = ""
        if record.action_type in (ActionType.KEYBOARD, ActionType.MOUSE_BUTTON):
            glyph = "↓" if record.state_down else "↑"
        label = Gtk.Label(label=glyph, xalign=.5)
        label.add_css_class("macro-action-direction")
        label.add_css_class("press" if glyph == "↓" else "release" if glyph == "↑" else "none")
        label.set_size_request(16, -1)
        if glyph:
            label.set_tooltip_text("Press" if glyph == "↓" else "Release")
        return label

    def _library_row(self, item: LocalMacroPresentation) -> Gtk.Button:
        row = Gtk.Button()
        row.add_css_class("macro-library-row")
        row.set_can_focus(True)
        row.connect("clicked", self._select_macro, item.stable_id)
        grip = Gtk.Label(label="⠿", xalign=.5)
        grip.add_css_class("macro-drag-grip")
        grip.set_valign(Gtk.Align.CENTER)
        grip.set_margin_top(5)
        grip.set_margin_end(2)
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        content.append(grip)
        details = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        name = Gtk.Label(label=item.name + (" •" if item.edited else ""), xalign=0, ellipsize=3)
        name.add_css_class("macro-row-title")
        row._macro_name_label = name
        if item.stable_id == self.presentation.selected_macro_id:
            row.add_css_class("selected-macro-row")
        name.set_hexpand(True)
        title.append(name)
        warning = Gtk.Label(label="!", xalign=.5)
        warning.add_css_class("macro-warning")
        warning.set_tooltip_text("This macro has a validation warning")
        warning.set_visible(item.incomplete)
        title.append(warning)
        row._macro_warning_label = warning
        if item.loaded_slot is not None:
            loaded = Gtk.Label(label=f"S{item.loaded_slot + 1}", xalign=.5)
            loaded.add_css_class("macro-slot-loaded")
            loaded.set_tooltip_text("Loaded in this device slot")
            title.append(loaded)
        details.append(title)
        content.append(details)
        row.set_child(content)
        apply_accessible_label(row, f"Local macro {item.name}; drag source; {item.action_count} actions; {item.encoded_delay_total_ms} milliseconds encoded delay")
        source = Gtk.DragSource()
        source.set_actions(Gdk.DragAction.COPY)
        source.connect(
            "prepare",
            lambda _source, _x, _y, identity=item.stable_id:
                Gdk.ContentProvider.new_for_value(identity),
        )
        self._configure_drag_preview(
            source,
            payload=item.stable_id,
            title=item.name,
        )
        row._macro_drag_source = source
        row.add_controller(source)
        return row

    def _slot_row(self, slot: DeviceSlotPresentation) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.add_css_class("macro-slot-row")
        if not slot.is_empty:
            row.add_css_class("occupied-macro-slot-row")
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        grip = Gtk.Label(label="⠿", xalign=.5)
        grip.add_css_class("macro-drag-grip")
        grip.set_valign(Gtk.Align.CENTER)
        grip.set_margin_top(5)
        grip.set_margin_end(2)
        title.append(grip)
        number = Gtk.Label(label=slot.label, xalign=0)
        number.add_css_class("macro-slot-number")
        number.set_visible(slot.loaded_macro_id is None)  # empty or on-mouse-only
        title.append(number)
        if slot.on_mouse_only:
            loaded = Gtk.Label(label="On mouse only", xalign=0, ellipsize=3)
            loaded.add_css_class("macro-slot-mouse-only")
        else:
            loaded = Gtk.Label(label=slot.loaded_macro_name or "Empty", xalign=0, ellipsize=3)
            loaded.add_css_class("macro-slot-loaded-name" if not slot.is_empty else "macro-slot-empty")
        usage = Gtk.Label(label=f"{len(slot.usage)} uses", xalign=0, ellipsize=3)
        usage.add_css_class("macro-row-meta")
        usage.set_visible(bool(slot.usage))
        loaded.set_hexpand(True)
        title.append(loaded)
        title.append(usage)
        row.append(title)
        description = f"{slot.label}; {slot.loaded_macro_name or 'empty'}; shared across all five profiles; {len(slot.usage)} assignment uses"
        apply_accessible_label(row, description)
        target = Gtk.DropTarget.new(str, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        target.connect("drop", self._drop_on_slot, slot.slot_index)
        row.add_controller(target)
        if slot.loaded_macro_id is not None:
            click = Gtk.GestureClick()
            click.connect("released", lambda _gesture, _n, _x, _y, identity=slot.loaded_macro_id:
                          self.presentation.select_macro(identity))
            row.add_controller(click)
            row.set_tooltip_text(
                f"{slot.label}: {slot.loaded_macro_name}. Click to edit; drag to another slot to swap or onto the library to unload."
                + (f"\nUsed by: " + "; ".join(item.description for item in slot.usage) if slot.usage else "")
            )
        elif slot.on_mouse_only:
            row.set_tooltip_text(
                f"{slot.label} holds a macro read from the mouse that isn't in your library. "
                "It stays in this slot; drag a library macro here to replace it."
            )
        else:
            row.set_tooltip_text(f"{slot.label} is empty. Drag a macro here to load it.")
        source = Gtk.DragSource()
        source.set_actions(Gdk.DragAction.MOVE)
        source.connect(
            "prepare",
            lambda _source, _x, _y, identity=slot.stable_id:
                Gdk.ContentProvider.new_for_value(identity),
        )
        self._configure_drag_preview(
            source,
            payload=slot.stable_id,
            title=slot.label,
            value=slot.loaded_macro_name or "Empty",
        )
        row._macro_drag_source = source
        row.add_controller(source)
        return row

    def _action_row(self, action) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.add_css_class("macro-action-row")
        row.set_can_focus(True)
        row._macro_action = action
        row._macro_action_index = action.index
        row._macro_action_id = action.stable_id

        drag_body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        drag_body.add_css_class("macro-action-drag-zone")
        drag_body.set_hexpand(True)
        drag_body.set_can_focus(True)
        drag_body._macro_action_row = row
        drag_body.set_cursor_from_name("grab")

        grip = Gtk.Label(label="⠿", xalign=.5)
        grip.add_css_class("macro-drag-grip")
        grip.set_valign(Gtk.Align.CENTER)
        grip.set_margin_top(5)
        grip.set_margin_end(2)
        grip.set_can_focus(False)
        drag_body.append(grip)

        number = Gtk.Label(label=f"{action.index + 1:02d}", xalign=.5)
        number.add_css_class("macro-action-number")
        number.set_size_request(24, -1)
        drag_body.append(number)
        drag_body.append(self._direction_label(action))

        kind = Gtk.Label(label=action.action, xalign=0)
        kind.add_css_class("macro-action-kind")
        kind.set_ellipsize(3)
        drag_body.append(kind)

        drag_spacer = Gtk.Box()
        drag_spacer.set_hexpand(True)
        drag_body.append(drag_spacer)

        drag_selection = Gtk.EventControllerLegacy()
        drag_selection.connect("event", self._action_drag_zone_event)
        drag_body.add_controller(drag_selection)
        drag_keys = Gtk.EventControllerKey()
        drag_keys.connect("key-pressed", self._action_zone_key_pressed)
        drag_body.add_controller(drag_keys)
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect(
            "prepare",
            self._prepare_action_drag,
            action.stable_id,
        )
        self._configure_drag_preview(
            drag_source,
            payload=f"action-id:{action.stable_id}",
            title=f"{action.index + 1:02d}  {action.action}",
            value=format_action_display_value(action.record),
        )
        drag_source._macro_action_reorder = True
        drag_source._macro_action_id = action.stable_id
        drag_body.add_controller(drag_source)

        value_body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        value_body.add_css_class("macro-action-value-zone")
        value_body.set_size_request(ACTION_VALUE_ZONE_WIDTH, -1)
        value_body.set_hexpand(False)
        value_body.set_can_focus(True)
        value_body._macro_action_row = row
        value_body.set_cursor_from_name(
            "default"
            if action.record.action_type in {ActionType.UNKNOWN, ActionType.ZERO}
            else "pointer"
        )

        value_label = Gtk.Label(
            label=format_action_display_value(action.record),
            xalign=1,
            ellipsize=3,
        )
        value_label.add_css_class("macro-action-value")
        value_label.set_hexpand(True)
        value_body.append(value_label)

        value_gesture = Gtk.GestureClick(button=1)
        value_gesture.connect("pressed", self._action_value_pressed)
        value_body.add_controller(value_gesture)
        value_keys = Gtk.EventControllerKey()
        value_keys.connect("key-pressed", self._action_zone_key_pressed)
        value_body.add_controller(value_keys)

        if action.record.action_type == ActionType.MOUSE_BUTTON:
            mouse_values = ("Left button", "Right button", "Middle button")
            editor = Gtk.DropDown.new_from_strings(mouse_values)
            current_mouse = format_action_editor_value(action.record)
            if current_mouse in mouse_values:
                editor.set_selected(mouse_values.index(current_mouse))
            editor.connect("notify::selected", self._inline_selector_changed)
            row._macro_editor_values = (1, 2, 4)
        else:
            editor = Gtk.Entry()
            editor.set_editable(True)
            if action.record.action_type == ActionType.DELAY:
                editor.set_input_purpose(Gtk.InputPurpose.DIGITS)
            editor.connect("activate", self._commit_inline_action)
            editor.connect("changed", self._inline_editor_changed)
            editor_keys = Gtk.EventControllerKey()
            editor_keys.connect("key-pressed", self._inline_editor_key_pressed)
            editor.add_controller(editor_keys)
        editor.add_css_class("macro-inline-action-editor")
        editor.set_hexpand(False)
        editor.set_size_request(ACTION_VALUE_ZONE_WIDTH, -1)
        editor.set_visible(False)
        editor.set_sensitive(True)
        apply_accessible_label(editor, f"Edit {action.action} value")
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", self._inline_editor_focus_leave)
        editor.add_controller(focus)
        value_body.append(editor)

        row._macro_drag_body = drag_body
        row._macro_value_body = value_body
        row._macro_drag_source = drag_source
        row._macro_drag_selection = drag_selection
        row._macro_value_click_gesture = value_gesture
        row._macro_value_label = value_label
        row._macro_inline_editor = editor
        editor._macro_action_row = row
        row.append(drag_body)
        row.append(value_body)

        if action.warning:
            row.add_css_class("macro-action-warning")
            row.set_tooltip_text(action.warning)
        value = format_action_display_value(action.record)
        apply_accessible_label(
            row,
            f"Action {action.index + 1}: {action.action}; value {value}; "
            "selectable and reorderable",
        )
        apply_accessible_label(
            drag_body,
            f"Action {action.index + 1}: {action.action}; drag to reorder; "
            "press F2 to edit",
        )
        value_accessibility = (
            f"Value {value}; editing unavailable for preserved raw action"
            if action.record.action_type in {ActionType.UNKNOWN, ActionType.ZERO}
            else f"Value {value}; double click or press Enter to edit"
        )
        apply_accessible_label(value_body, value_accessibility)

        # Reordering is driven by the container drop target on _actions_box so the
        # provisional order can be previewed live and committed once at drag end.
        return row

    def _gesture_log(self, message: str) -> None:
        if self._gesture_debug:
            print(f"[macro-gesture] {message}", flush=True)

    @staticmethod
    def _is_widget_within(widget: Gtk.Widget | None, ancestor: Gtk.Widget) -> bool:
        current = widget
        while current is not None:
            if current is ancestor:
                return True
            current = current.get_parent()
        return False

    def _application_click_away_event(
        self,
        controller: Gtk.EventControllerLegacy,
        event: Gdk.Event | None,
    ) -> bool:
        if event is None:
            event = controller.get_current_event()
        if (
            event is None
            or event.get_event_type() != Gdk.EventType.BUTTON_PRESS
            or not isinstance(event, Gdk.ButtonEvent)
            or event.get_button() != 1
        ):
            return False
        has_position, x, y = event.get_position()
        if not has_position:
            return False
        self._application_click_away_at(controller.get_widget(), x, y)
        return False

    def _application_click_away_at(
        self, controller_widget: Gtk.Widget, x: float, y: float
    ) -> None:
        state = self._inline_editor_state
        if state is None or state.closing:
            return
        target = controller_widget.pick(x, y, Gtk.PickFlags.DEFAULT)
        inside_editor = self._is_widget_within(target, state.editor)
        self._gesture_log(
            f"window press x={x:.1f} y={y:.1f} "
            f"target={type(target).__name__ if target else 'none'} "
            f"inside_editor={inside_editor}"
        )
        if not inside_editor:
            self._finish_inline_edit(commit=True)

    def _action_drag_zone_event(
        self,
        controller: Gtk.EventControllerLegacy,
        event: Gdk.Event | None,
    ) -> bool:
        if event is None:
            event = controller.get_current_event()
        if (
            event is None
            or event.get_event_type() != Gdk.EventType.BUTTON_PRESS
            or not isinstance(event, Gdk.ButtonEvent)
            or event.get_button() != 1
        ):
            return False
        row = controller.get_widget()._macro_action_row
        self._select_action_row(row)
        return False

    def _select_action_row(self, row: Gtk.Box) -> bool:
        if (
            self._inline_editor_state is not None
            and not self._finish_inline_edit(commit=True)
        ):
            return False
        resolved = self._resolve_action(row._macro_action_id)
        if resolved is None:
            return False
        self.presentation.select_action(resolved[0])
        return True

    def _prepare_action_drag(
        self,
        _source: Gtk.DragSource,
        x: float,
        y: float,
        action_id: str,
    ) -> Gdk.ContentProvider:
        self._gesture_log(
            f"drag prepare action={action_id} x={x:.1f} y={y:.1f}"
        )
        return Gdk.ContentProvider.new_for_value(f"action-id:{action_id}")

    def _configure_drag_preview(
        self,
        source: Gtk.DragSource,
        *,
        payload: str,
        title: str,
        value: str | None = None,
    ) -> None:
        source._macro_drag_payload = payload
        source._macro_drag_preview_title = title
        source._macro_drag_preview_value = value
        source._macro_drag_preview = None
        source._macro_drag_icon = None
        source.connect_after("drag-begin", self._drag_preview_begin)
        source.connect("drag-end", self._drag_preview_end)
        source.connect("drag-cancel", self._drag_preview_cancel)

    @staticmethod
    def _build_drag_preview(title: str, value: str | None) -> Gtk.Box:
        preview = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        preview.add_css_class("macro-action-drag-preview")
        preview.set_can_target(False)
        preview.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)

        grip = Gtk.Label(label="⠿", xalign=.5)
        grip.add_css_class("macro-drag-preview-grip")
        preview.append(grip)

        title_label = Gtk.Label(label=title, xalign=0, ellipsize=3)
        title_label.add_css_class("macro-drag-preview-title")
        preview.append(title_label)

        if value:
            value_label = Gtk.Label(label=value, xalign=1, ellipsize=3)
            value_label.add_css_class("macro-drag-preview-value")
            preview.append(value_label)
        return preview

    def _drag_preview_begin(
        self, source: Gtk.DragSource, drag: Gdk.Drag
    ) -> None:
        self._clear_drag_preview(source)
        if getattr(source, "_macro_action_reorder", False):
            self._begin_action_reorder_drag(source, drag)
            return
        preview = self._build_drag_preview(
            source._macro_drag_preview_title,
            source._macro_drag_preview_value,
        )
        icon = Gtk.DragIcon.get_for_drag(drag)
        icon.set_child(preview)
        source._macro_drag_preview = preview
        source._macro_drag_icon = icon

    @staticmethod
    def _clear_drag_preview(source: Gtk.DragSource) -> None:
        icon = source._macro_drag_icon
        preview = source._macro_drag_preview
        if icon is not None and icon.get_child() is preview:
            icon.set_child(None)
        source._macro_drag_preview = None
        source._macro_drag_icon = None

    def _drag_preview_end(
        self,
        source: Gtk.DragSource,
        _drag: Gdk.Drag,
        _delete_data: bool,
    ) -> None:
        if getattr(source, "_macro_action_reorder", False):
            self._finish_action_reorder_drag(commit=False)
        self._clear_drag_preview(source)

    def _drag_preview_cancel(
        self,
        source: Gtk.DragSource,
        _drag: Gdk.Drag,
        _reason: Gdk.DragCancelReason,
    ) -> bool:
        if getattr(source, "_macro_action_reorder", False):
            self._finish_action_reorder_drag(commit=False)
        self._clear_drag_preview(source)
        return False

    def _action_value_pressed(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        value_body = gesture.get_widget()
        row = value_body._macro_action_row
        action_id = row._macro_action_id
        self._gesture_log(
            f"value press action={action_id} n_press={n_press} "
            f"x={x:.1f} y={y:.1f}"
        )
        if (
            self._inline_editor_state is not None
            and not self._finish_inline_edit(commit=True)
        ):
            return
        resolved = self._resolve_action(action_id)
        if resolved is None:
            return
        index, _action = resolved
        self.presentation.select_action(index)
        if n_press == 2:
            self._begin_inline_action_edit(action_id)

    def _action_zone_key_pressed(
        self, controller: Gtk.EventControllerKey, keyval, _keycode, _state
    ) -> bool:
        row = controller.get_widget()._macro_action_row
        if (
            self._inline_editor_state is not None
            and self._inline_editor_state.row is row
        ):
            return False
        resolved = self._resolve_action(row._macro_action_id)
        if resolved is None:
            return False
        index, _action = resolved
        if keyval == Gdk.KEY_F2 or (
            controller.get_widget() is row._macro_value_body
            and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
        ):
            if (
                self._inline_editor_state is not None
                and not self._finish_inline_edit(commit=True)
            ):
                return True
            self.presentation.select_action(index)
            self._begin_inline_action_edit(row._macro_action_id)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._finish_inline_edit(commit=True)
            self.presentation.select_action(index)
            return True
        return False

    def _resolve_action(self, action_id: str):
        for index, action in enumerate(self.presentation.selected_macro.current_actions):
            if action.stable_id == action_id:
                return index, action
        return None

    def _begin_inline_action_edit(self, action_id: str) -> None:
        if (
            self._inline_editor_state is not None
            and not self._finish_inline_edit(commit=True)
        ):
            return
        resolved = self._resolve_action(action_id)
        row = self._action_rows.get(action_id)
        if resolved is None or row is None:
            return
        _index, action = resolved
        if action.record.action_type == ActionType.UNKNOWN or action.record.action_type == ActionType.ZERO:
            row.set_tooltip_text("Unknown/raw action is preserved and cannot be edited")
            return
        editor = row._macro_inline_editor
        original = format_action_editor_value(action.record)
        self._programmatic_inline_update = True
        try:
            if isinstance(editor, Gtk.Entry):
                editor.set_text(original)
            else:
                editor.set_selected({1: 0, 2: 1, 4: 2}.get(action.record.usage, 0))
            row._macro_value_label.set_visible(False)
        finally:
            self._programmatic_inline_update = False
        editor.set_visible(True)
        editor.set_sensitive(True)
        editor.set_can_focus(True)
        editor.set_focusable(True)
        row._macro_value_click_gesture.set_propagation_phase(
            Gtk.PropagationPhase.NONE
        )
        self._inline_editor_state = InlineActionEditorState(
            macro_id=self.presentation.selected_macro_id,
            action_id=action_id,
            row=row,
            editor=editor,
            action_type=action.record.action_type,
            original_value=original,
        )
        self._gesture_log(f"open editor action={action_id} value={original!r}")
        focused = editor.grab_focus()
        self._gesture_log(
            f"immediate focus action={action_id} grab={focused} has_focus={editor.has_focus()}"
        )
        GLib.idle_add(self._focus_inline_editor, editor, action_id)

    def _focus_inline_editor(self, editor: Gtk.Widget, action_id: str) -> bool:
        state = self._inline_editor_state
        if state is None or state.action_id != action_id or state.editor is not editor:
            return GLib.SOURCE_REMOVE
        focused = editor.grab_focus()
        if isinstance(editor, Gtk.Entry):
            editor.select_region(0, -1)
        root = editor.get_root()
        root_focus = root.get_focus() if root is not None and hasattr(root, "get_focus") else None
        focus_within = False
        focus_widget = root_focus
        while focus_widget is not None:
            if focus_widget is editor:
                focus_within = True
                break
            focus_widget = focus_widget.get_parent()
        self._gesture_log(
            f"focus action={action_id} grab={focused} focus_within={focus_within} "
            f"root_focus={type(root_focus).__name__ if root_focus else 'none'}"
        )
        return GLib.SOURCE_REMOVE

    def _inline_editor_key_pressed(self, controller, keyval, _keycode, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self._cancel_inline_edit(controller.get_widget())
            return True
        return False

    def _inline_editor_focus_leave(self, controller) -> None:
        self._commit_inline_action(controller.get_widget())

    def _inline_selector_changed(self, dropdown, _param) -> None:
        if self._inline_editor_state is not None and not self._programmatic_inline_update:
            self._commit_inline_action(dropdown)

    def _inline_editor_changed(self, entry: Gtk.Entry) -> None:
        if not self._programmatic_inline_update:
            state = self._inline_editor_state
            if state is not None and state.editor is entry:
                state.validation_message = None
                entry.remove_css_class("error")
                entry.set_tooltip_text(None)
            self._gesture_log(f"changed action={entry._macro_action_row._macro_action_id} text={entry.get_text()!r}")

    def _finish_inline_edit(self, *, commit: bool) -> bool:
        state = self._inline_editor_state
        if state is None or state.closing:
            return False
        state.closing = True
        try:
            if commit and not self._apply_inline_editor_value(state):
                return False
            state.editor.remove_css_class("error")
            state.editor.set_tooltip_text(None)
            state.editor.set_visible(False)
            state.row._macro_value_label.set_visible(True)
            state.row._macro_value_click_gesture.set_propagation_phase(
                Gtk.PropagationPhase.BUBBLE
            )
            self._gesture_log(f"close editor action={state.action_id} commit={commit}")
            self._inline_editor_state = None
            return True
        finally:
            state.closing = False

    def _cancel_inline_edit(self, editor: Gtk.Widget) -> None:
        state = self._inline_editor_state
        if state is not None and state.editor is editor:
            self._finish_inline_edit(commit=False)

    def _commit_inline_action(self, editor: Gtk.Widget) -> None:
        state = self._inline_editor_state
        if state is not None and state.editor is editor:
            self._finish_inline_edit(commit=True)

    def _set_inline_validation(
        self, state: InlineActionEditorState, message: str
    ) -> None:
        state.validation_message = message
        state.editor.add_css_class("error")
        state.editor.set_tooltip_text(message)
        self._gesture_log(
            f"validation action={state.action_id} message={message!r}"
        )

    def _apply_inline_editor_value(self, state: InlineActionEditorState) -> bool:
        if self.presentation.selected_macro_id != state.macro_id:
            return False
        resolved = self._resolve_action(state.action_id)
        if resolved is None:
            return False
        _index, action = resolved
        editor = state.editor
        try:
            if isinstance(editor, Gtk.Entry):
                text = editor.get_text().strip().lower().replace(",", "")
            else:
                text = ("left button", "right button", "middle button")[editor.get_selected()]
            if action.record.action_type == ActionType.DELAY:
                requested = int(text)
                bounded = max(0, min(requested, ACTION_DELAY_MAX_MS))
                quantized = (
                    (bounded + ACTION_DELAY_QUANTUM_MS // 2)
                    // ACTION_DELAY_QUANTUM_MS
                    * ACTION_DELAY_QUANTUM_MS
                )
                quantized = min(quantized, ACTION_DELAY_MAX_MS)
                if quantized != action.record.delay_ms:
                    self.presentation.edit_action_delay(state.action_id, quantized)
            elif action.record.action_type == ActionType.KEYBOARD:
                usage = parse_keyboard_editor(text)
                if usage != action.record.usage:
                    self.presentation.edit_action_usage(state.action_id, usage)
            elif action.record.action_type == ActionType.MOUSE_BUTTON:
                usage = {"left button": 1, "right button": 2, "middle button": 4}[text]
                if usage != action.record.usage:
                    self.presentation.edit_action_usage(state.action_id, usage)
        except (KeyError, TypeError, ValueError):
            message = (
                "Enter a key such as A, 7, Left Ctrl, Space, or Enter"
                if action.record.action_type == ActionType.KEYBOARD
                else "Enter a valid value"
            )
            self._set_inline_validation(state, message)
            return False
        state.validation_message = None
        editor.remove_css_class("error")
        editor.set_tooltip_text(None)
        return True

    def _refresh_action_rows_in_place(
        self, selected: LocalMacroPresentation
    ) -> None:
        """Refresh edited values without destroying an active click sequence."""
        for action in selected.current_actions:
            row = self._action_rows[action.stable_id]
            row._macro_action = action
            row._macro_action_index = action.index
            value = format_action_display_value(action.record)
            row._macro_value_label.set_text(value)
            if action.warning:
                row.add_css_class("macro-action-warning")
                row.set_tooltip_text(action.warning)
            else:
                row.remove_css_class("macro-action-warning")
                row.set_tooltip_text(None)
            apply_accessible_label(
                row,
                f"Action {action.index + 1}: {action.action}; "
                f"value {value}; selectable and reorderable",
            )
        self._rendered_action_records = selected.current_records
        self._rendered_action_ids = selected.current_action_ids

    def _refresh(self, state: MacroPresentationState) -> None:
        library_key = (
            state.selected_macro_id,
            tuple(
                (
                    item.stable_id,
                    item.name,
                    item.loaded_slot,
                )
                for item in state.local_macros
            ),
        )
        if library_key != self._rendered_library_key:
            self._refresh_library(state)
            self._rendered_library_key = library_key
        # Unsaved markers update in place so an inline-edit commit never
        # rebuilds (and swallows a click on) the library rows.
        for item in state.local_macros:
            row = self._library_rows.get(item.stable_id)
            if row is not None:
                text = item.name + (" •" if item.edited else "")
                if row._macro_name_label.get_text() != text:
                    row._macro_name_label.set_text(text)
                row._macro_warning_label.set_visible(item.incomplete)
        slots_key = tuple(
            (
                slot.stable_id,
                slot.loaded_macro_id,
                slot.loaded_macro_name,
                slot.usage,
                slot.mapping_note,
                slot.on_mouse_only,
            )
            for slot in state.device_slots
        )
        if slots_key != self._rendered_slots_key:
            self._refresh_slots(state)
            self._rendered_slots_key = slots_key
        selected = state.selected_macro
        self._set_editor_enabled(selected is not None and selected.editable and self._recording is None)
        if selected is None:
            self._show_empty_editor()
            self._refresh_impact(state)
            return
        self._editor_title.set_text(selected.name)
        self._editor_summary.set_text(f"{selected.current_action_count}/{ACTION_RECORD_CAPACITY} · {selected.current_delay_total_ms:,} ms")
        self._sync_name_entry(selected.stable_id, selected.name)
        action_rows_changed = (
            self._rendered_action_macro_id != selected.stable_id
            or self._rendered_action_records != selected.current_records
            or self._rendered_action_ids != selected.current_action_ids
        )
        # Never rebuild the rows mid-drag: the provisional order lives only in
        # the widget tree and is committed or restored when the drag ends.
        if action_rows_changed and self._action_drag is None:
            inline_commit = (
                self._inline_editor_state is not None
                and self._inline_editor_state.closing
                and self._inline_editor_state.macro_id == selected.stable_id
                and self._rendered_action_macro_id == selected.stable_id
                and self._rendered_action_ids == selected.current_action_ids
            )
            if inline_commit:
                self._refresh_action_rows_in_place(selected)
            else:
                child = self._actions_box.get_first_child()
                while child is not None:
                    next_child = child.get_next_sibling()
                    self._actions_box.remove(child)
                    child = next_child
                self._action_rows.clear()
                for action in selected.current_actions:
                    row = self._action_row(action)
                    self._action_rows[action.stable_id] = row
                    self._actions_box.append(row)
                self._rendered_action_macro_id = selected.stable_id
                self._rendered_action_records = selected.current_records
                self._rendered_action_ids = selected.current_action_ids
        for row in self._action_rows.values():
            active = row._macro_action_index == state.selected_action_index
            row.add_css_class("selected-action-row") if active else row.remove_css_class("selected-action-row")
            set_accessible_selected(row, active)
        self._syncing_widgets = True
        try:
            self._sync_delay_widgets(selected)
            self._loop_mode.set_selected(selected.current_loop_type)
            self._loop_count.set_sensitive(selected.current_loop_type == 0)
            displayed_count = str(selected.current_loop_count or 1)
            if self._loop_count.get_text() != displayed_count:
                self._loop_count.set_text(displayed_count)
        finally:
            self._syncing_widgets = False
        self._delete_button.set_sensitive(state.selected_action_index is not None)
        self._up_button.set_sensitive(state.selected_action_index is not None and state.selected_action_index > 0)
        self._down_button.set_sensitive(state.selected_action_index is not None and state.selected_action_index < selected.current_action_count - 1)
        self._copy_button.set_sensitive(state.selected_action_index is not None)
        self._cut_button.set_sensitive(state.selected_action_index is not None)
        self._paste_button.set_sensitive(bool(state._clipboard) and selected.current_action_count < ACTION_RECORD_CAPACITY)
        recording = self._recording is not None
        warnings = [text for text in selected.warnings
                    if not (recording and text in selected.unfinished)]  # held keys are expected mid-recording
        self._editor_warning.set_text(" · ".join(warnings))
        slot_text = f"Slot {selected.loaded_slot + 1}" if selected.loaded_slot is not None else "not in a device slot"
        # The library saves itself; the status only says whether that has
        # happened. An unfinished macro (press without release) waits, with a
        # plain-language reason, and saves as soon as it is complete.
        if recording:
            text, waiting = f"Recording · {slot_text}", False
        elif selected.unfinished:
            text, waiting = f"Not saved yet · {selected.unfinished[0]}", True
        elif selected.dirty:
            text, waiting = f"Saving… · {slot_text}", False
        elif selected.edited:
            text, waiting = f"Changes saved · {slot_text}", False
        else:
            text, waiting = f"Saved · {slot_text}", False
        self._macro_status.set_text(text)
        if waiting:
            self._macro_status.add_css_class("unsaved-status")
        else:
            self._macro_status.remove_css_class("unsaved-status")
        self._revert_macro_button.set_sensitive(selected.edited)
        self._refresh_impact(state)

    def _refresh_impact(self, state: MacroPresentationState) -> None:
        pending = state.pending_impact
        self._impact_bar.set_visible(pending is not None)
        if pending:
            self._impact_label.set_text(pending.message)
            self._replace_impact.set_label(pending.confirm_label)
            apply_accessible_label(self._replace_impact, f"Confirm {pending.operation}; affected assignments are listed")

    def _refresh_library(self, state: MacroPresentationState) -> None:
        child = self._library_list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._library_list.remove(child)
            child = next_child
        self._library_rows.clear()
        for item in state.local_macros:
            row = self._library_row(item)
            self._library_rows[item.stable_id] = row
            self._library_list.append(row)
        for identity, row in self._library_rows.items():
            set_accessible_selected(row, identity == state.selected_macro_id)
        has_selection = state.selected_macro_id is not None
        for button in (self._duplicate_button, self._rename_button, self._delete_library_button):
            button.set_sensitive(has_selection)
        if not state.local_macros:
            empty = Gtk.Label(label="No macros yet.\nCreate one with New, or import\nWindows macro files.", wrap=True)
            empty.add_css_class("dim-label")
            empty.add_css_class("macro-library-empty")
            empty.set_margin_top(18)
            self._library_list.append(empty)

    def _refresh_slots(self, state: MacroPresentationState) -> None:
        child = self._slot_list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._slot_list.remove(child)
            child = next_child
        self._slot_rows.clear()
        for slot in state.device_slots:
            row = self._slot_row(slot)
            self._slot_rows[slot.stable_id] = row
            self._slot_list.append(row)

    def _select_macro(self, _button: Gtk.Button, stable_id: str) -> None:
        self.presentation.select_macro(stable_id)

    def _new_macro(self, _button: Gtk.Button) -> None:
        # A name typed into the name field becomes the new macro's name. The
        # field otherwise mirrors the selected macro, so an unchanged (or
        # empty) field falls back to the default name rather than cloning it.
        typed = self._name_entry.get_text().strip()
        selected = self.presentation.selected_macro
        if not typed or (selected is not None and typed == selected.name):
            self.presentation.add_local_macro()
            return
        try:
            self.presentation.add_local_macro(typed)
        except ValueError as exc:
            self._report_error(str(exc))

    def _set_editor_enabled(self, enabled: bool) -> None:
        # The name field stays usable with no macro selected: type a name and
        # click New (or press Enter) to create a macro with that name.
        for widget in (self._loop_mode, *self._insert_buttons.values(), self._delay_toggle,
                       self._delay_entry, self._record_delays, self._record_button, self._actions_box):
            widget.set_sensitive(enabled)
        if not enabled:
            for widget in (self._delete_button, self._up_button, self._down_button, self._copy_button,
                           self._cut_button, self._paste_button, self._loop_count):
                widget.set_sensitive(False)

    def _sync_name_entry(self, macro_id: str | None, name: str) -> None:
        """Show the selected macro's name without clobbering typed text.

        Selecting a different macro always shows its name. Otherwise the field
        is only updated if it still shows what was last displayed, so a name
        being typed (e.g. for New) survives unrelated refreshes.
        """
        displayed = getattr(self, "_displayed_name", "")
        selection_changed = macro_id != getattr(self, "_displayed_macro_id", None)
        if selection_changed or self._name_entry.get_text() == displayed:
            if self._name_entry.get_text() != name:
                self._name_entry.set_text(name)
        self._displayed_name = name
        self._displayed_macro_id = macro_id

    def _show_empty_editor(self) -> None:
        self._editor_title.set_text("No macro selected")
        self._editor_summary.set_text("")
        self._sync_name_entry(None, "")
        self._editor_warning.set_text("")
        self._macro_status.set_text("Create or import a macro to begin")
        self._revert_macro_button.set_sensitive(False)
        child = self._actions_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._actions_box.remove(child)
            child = next_child
        self._action_rows.clear()
        self._rendered_action_macro_id = None
        self._rendered_action_records = None
        self._rendered_action_ids = None

    def _report_error(self, message: str) -> None:
        if self._window is not None and hasattr(self._window, "toast"):
            self._window.toast(message)
        else:
            self._editor_warning.set_text(message)

    def _name_entry_activated(self, entry) -> None:
        if self.presentation.selected_macro is None:
            self._new_macro(entry)
        else:
            self._rename_macro(entry)

    def _rename_macro(self, _widget) -> None:
        try:
            self.presentation.rename_selected_macro(self._name_entry.get_text())
        except ValueError as exc:
            self._report_error(str(exc))
            selected = self.presentation.selected_macro
            if selected is not None:
                self._name_entry.set_text(selected.name)

    def _duplicate_macro(self, _button: Gtk.Button) -> None:
        try:
            self.presentation.duplicate_selected_macro()
        except ValueError as exc:
            self._report_error(str(exc))

    def _delete_macro(self, _button: Gtk.Button) -> None:
        # No "are you sure?" dialog (user decision 2026-11-19): the macro file
        # goes to the trash, so a deletion can be undone from there.
        if self.presentation.selected_macro is None:
            return
        self.presentation.delete_selected_macro()

    def _revert_macro(self, _button: Gtk.Button) -> None:
        try:
            self.presentation.revert_selected_macro()
        except ValueError as exc:
            self._report_error(str(exc))

    # ------------------------------------------------------------ recording
    def _start_recording(self, _button: Gtk.Button) -> None:
        root = self.get_root()
        if root is None or self.presentation.selected_macro is None:
            return
        self._recording = []
        self._recording_last_time = None
        self._recording_held = set()
        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._record_key, True)
        controller.connect("key-released", self._record_key_released)
        root.add_controller(controller)
        self._recording_controller = controller
        self._record_button.set_sensitive(False)
        self._stop_button.set_sensitive(True)
        self._record_status.set_text("Recording… press keys, then click Stop")
        self._record_status.add_css_class("recording-active")
        self._set_editor_enabled(False)

    def _record_key_released(self, controller, keyval, keycode, state) -> None:
        self._record_key(controller, keyval, keycode, state, False)

    def _record_key(self, controller, _keyval, keycode, _state, down: bool) -> bool:
        if self._recording is None:
            return False
        usage = usage_from_gdk_keycode(keycode)
        if usage is None:
            return True
        if down and usage in self._recording_held:
            return True  # key auto-repeat
        if not down and usage not in self._recording_held:
            return True
        if down:
            self._recording_held.add(usage)
        else:
            self._recording_held.discard(usage)
        time = controller.get_current_event_time()
        delay_type = self.presentation.selected_macro.macro.delay_type
        quantized = 0
        if self._recording_last_time is not None and time:
            # Like the Windows editor (ldcfg.exe sub_424a30): the first
            # recorded event only starts the timer; delays are rounded down
            # to 10 ms and a zero delay inserts nothing.
            if delay_type == DELAY_TYPE_RECORDED:
                elapsed = max(0, time - self._recording_last_time)
                quantized = min(elapsed - elapsed % 10, ACTION_DELAY_MAX_MS)
            else:
                # Default delay, or the 10 ms minimum when neither is checked.
                quantized = self.presentation.delay_between_actions_ms()
        new: list[ActionRecord] = []
        if quantized >= ACTION_DELAY_QUANTUM_MS:
            new.append(ActionRecord.action_delay_ms(quantized))
        self._recording_last_time = time or self._recording_last_time
        new.append(ActionRecord.keyboard(usage, down))
        self._append_recorded(new)
        return True

    def _append_recorded(self, records: list[ActionRecord]) -> bool:
        """Add recorded actions to the staging list as they happen (like Windows)."""
        try:
            self.presentation.add_actions(records)
        except ValueError as exc:
            self._report_error(str(exc))
            if self._recording is not None:
                self._recording_held = set()  # nothing more fits; stop cleanly
                self._stop_recording()
            return False
        if self._recording is not None:
            self._recording.extend(records)
            self._record_status.set_text(f"Recording… {len(self._recording)} actions")
        GLib.idle_add(self._scroll_sequence_to_end)
        return True

    def _scroll_sequence_to_end(self) -> bool:
        adjustment = self._sequence_scroll.get_vadjustment()
        adjustment.set_value(max(0.0, adjustment.get_upper() - adjustment.get_page_size()))
        return GLib.SOURCE_REMOVE

    def _stop_recording(self, _button=None) -> None:
        held = [ActionRecord.keyboard(usage, False) for usage in sorted(self._recording_held)]
        self._recording_held = set()
        if held and self._recording is not None:
            self._append_recorded(held)  # release keys still held at Stop
        root = self.get_root()
        if self._recording_controller is not None and root is not None:
            root.remove_controller(self._recording_controller)
        self._recording_controller = None
        self._recording = None
        self._recording_held = set()
        self._record_button.set_sensitive(True)
        self._stop_button.set_sensitive(False)
        self._record_status.remove_css_class("recording-active")
        self._record_status.set_text("Keyboard only · this window")
        self._refresh(self.presentation)

    # ------------------------------------------------------------ delay modes
    def _typed_default_delay(self) -> int:
        """The default-delay field as stored (empty = 10, like Windows' default)."""
        digits = "".join(ch for ch in self._delay_entry.get_text() if ch.isdigit())
        return int(digits) if digits else DEFAULT_INSERT_DELAY_MS

    def _record_delays_toggled(self, check) -> None:
        if self._syncing_widgets or self.presentation.selected_macro is None:
            return
        if check.get_active():
            mode = DELAY_TYPE_RECORDED
        elif self._delay_toggle.get_active():
            mode = DELAY_TYPE_DEFAULT
        else:
            mode = DELAY_TYPE_NONE
        self.presentation.edit_delay_settings(mode, self._typed_default_delay())

    def _delay_toggled(self, toggle) -> None:
        if self._syncing_widgets or self.presentation.selected_macro is None:
            return
        if toggle.get_active():
            mode = DELAY_TYPE_DEFAULT
        elif self._record_delays.get_active():
            mode = DELAY_TYPE_RECORDED
        else:
            mode = DELAY_TYPE_NONE
        self.presentation.edit_delay_settings(mode, self._typed_default_delay())

    def _delay_entry_changed(self, entry) -> None:
        text = entry.get_text()
        if any(not ch.isdigit() for ch in text):
            # Digits only, like the Windows field. The text is tidied on the
            # next idle tick (changing it inside "changed" corrupts it).
            digits = "".join(ch for ch in text if ch.isdigit())
            GLib.idle_add(lambda: (entry.get_text() == text and entry.set_text(digits), False)[1])
        if self._syncing_widgets or self.presentation.selected_macro is None:
            return
        selected = self.presentation.selected_macro
        # The refresh this edit triggers must not write back into the field
        # while it is mid-change.
        self._editing_delay_entry = True
        try:
            self.presentation.edit_delay_settings(selected.macro.delay_type, self._typed_default_delay())
        except ValueError as exc:
            self._report_error(str(exc))
        finally:
            self._editing_delay_entry = False

    def _sync_delay_widgets(self, selected) -> None:
        """Reflect the selected macro's DelayType/DefDelayTime (call while syncing)."""
        delay_type = selected.macro.delay_type
        self._record_delays.set_active(delay_type == DELAY_TYPE_RECORDED)
        self._delay_toggle.set_active(delay_type == DELAY_TYPE_DEFAULT)
        if not self._delay_entry.has_focus() and not getattr(self, "_editing_delay_entry", False):
            shown = str(selected.macro.default_inserted_delay_ms)
            if self._delay_entry.get_text() != shown:
                self._delay_entry.set_text(shown)

    def _insert_action(self, _widget, key: str) -> None:
        record = {
            "lmb-down": ActionRecord.mouse_button(1, True),
            "lmb-up": ActionRecord.mouse_button(1, False),
            "rmb-down": ActionRecord.mouse_button(2, True),
            "rmb-up": ActionRecord.mouse_button(2, False),
        }[key]
        try:
            self.presentation.insert_with_default_delay(record)
        except ValueError:
            self._editor_summary.set_text("Maximum capacity reached: 63 records")

    def _delete_action(self, _button: Gtk.Button) -> None:
        try:
            self.presentation.delete_selected_action()
        except ValueError:
            pass

    def _move_action(self, direction: int) -> None:
        self.presentation.move_selected_action(direction)

    def _copy_action(self, _button: Gtk.Button) -> None:
        try:
            self.presentation.copy_selected_action()
        except ValueError:
            pass
        self._refresh(self.presentation)

    def _cut_action(self, _button: Gtk.Button) -> None:
        try:
            self.presentation.cut_selected_action()
        except ValueError:
            pass

    def _paste_action(self, _button: Gtk.Button) -> None:
        try:
            self.presentation.paste_action()
        except ValueError:
            pass

    def _select_action(self, _button: Gtk.Button, index: int) -> None:
        self.presentation.select_action(index)

    def _loop_changed(self, _dropdown, _param) -> None:
        if self._syncing_widgets:
            return
        try:
            self.presentation.edit_loop(int(self._loop_mode.get_selected()), self.presentation.selected_macro.current_loop_time)
        except ValueError:
            pass

    def _loop_count_changed(self, _spin: Gtk.Entry) -> None:
        if self._syncing_widgets:
            return
        if self._loop_count.get_sensitive():
            try:
                self.presentation.edit_loop(0, int(self._loop_count.get_text() or 1))
            except ValueError:
                pass

    def _drop_on_slot(self, _target, value: str, _x: float, _y: float, slot_index: int) -> bool:
        if value.startswith("device-macro-slot:"):
            other = int(value.rsplit(":", 1)[1])
            self.presentation.swap_slots(other, slot_index)
        elif value.startswith("local-macro:"):
            self.presentation.drop_library_on_slot(value, slot_index)
        return True

    def _drop_on_library(self, _target, value: str, _x=0, _y=0) -> bool:
        if value.startswith("device-macro-slot:"):
            self.presentation.unload_slot(int(value.rsplit(":", 1)[1]))
        return True

    def _drop_on_action(
        self,
        _target,
        value: str,
        _x: float,
        _y: float,
        target_action_id: str,
    ) -> bool:
        target = self._resolve_action(target_action_id)
        if target is None:
            return False
        if value.startswith("action-id:"):
            source = self._resolve_action(value.removeprefix("action-id:"))
            if source is None:
                return False
            source_index = source[0]
        elif value.startswith("action:"):
            try:
                source_index = int(value.split(":", 1)[1])
            except ValueError:
                return False
        else:
            return False
        target_index = target[0]
        if source_index != target_index:
            self.presentation.reorder_action(source_index, target_index)
        return True

    # ------------------------------------------------------------------
    # Live drag reordering of the staging sequence.
    #
    # The persistent model is never mutated while the pointer moves. A
    # provisional visual order is kept by reordering the row widgets inside
    # _actions_box, the dragged row is shown as a dimmed landing slot, and a
    # lifted replica follows the pointer inside the staging overlay (anchored to
    # the grab point and clamped to the viewport; the OS drag icon is hidden).
    # The model is committed exactly once on a successful drop; Escape/cancel
    # restores the exact original ordering.
    # ------------------------------------------------------------------

    def _begin_action_reorder_drag(
        self, source: Gtk.DragSource, drag: Gdk.Drag
    ) -> None:
        action_id = getattr(source, "_macro_action_id", None)
        row = self._action_rows.get(action_id) if action_id else None
        if row is None or not self._begin_action_reorder(action_id):
            preview = self._build_drag_preview(
                source._macro_drag_preview_title,
                source._macro_drag_preview_value,
            )
            icon = Gtk.DragIcon.get_for_drag(drag)
            icon.set_child(preview)
            source._macro_drag_preview = preview
            source._macro_drag_icon = icon
            return
        state = self._action_drag
        lifted = self._build_lifted_action_preview(row)
        state.lifted = lifted
        self._lifted_layer.add_overlay(lifted)
        self._place_lifted_over_row(state)
        icon = Gtk.DragIcon.get_for_drag(drag)
        # The lifted row lives in the staging overlay. Give the OS drag icon an
        # empty, zero-sized child: leaving it childless makes GTK fall back to
        # rendering the raw DnD payload (the stable action id) as a label.
        icon.set_child(self._build_hidden_drag_icon())
        source._macro_drag_preview = lifted
        source._macro_drag_icon = icon
        self._start_action_autoscroll()

    @staticmethod
    def _build_hidden_drag_icon() -> Gtk.Widget:
        hidden = Gtk.Box()
        hidden.set_size_request(0, 0)
        hidden.set_can_target(False)
        hidden.add_css_class("macro-drag-icon-hidden")
        return hidden

    def _capture_action_grab_offset(
        self, state: ActionDragState, x: float, y: float
    ) -> None:
        # Measured at the first drag motion (not at press time) so the pointer
        # movement that crossed the drag threshold is absorbed and the lifted
        # row stays pixel-for-pixel on the source row with no jump.
        # Pointer coordinates and row allocations share the list's content
        # frame, so the offset is simply their difference.
        row_allocation = state.row.get_allocation()
        state.grab_offset_x = x - row_allocation.x
        state.grab_offset_y = y - row_allocation.y
        state.grab_captured = True

    def _list_offset_in_layer(self) -> tuple[float, float]:
        """Where the list's content frame starts inside the lifted layer.

        The staging list has CSS padding, so its content frame (in which row
        allocations and pointer coordinates are expressed) is inset from the
        viewport-sized lifted layer. Returned unscrolled (scroll is handled
        separately via the adjustment).
        """
        ok, point = self._actions_box.compute_point(self._lifted_layer, Graphene.Point())
        if not ok:
            return 0.0, 0.0
        scrolled = self._sequence_scroll.get_vadjustment().get_value()
        return point.x, point.y + scrolled

    def _place_lifted_over_row(
        self, state: ActionDragState
    ) -> tuple[float, float] | None:
        adjustment = self._sequence_scroll.get_vadjustment()
        allocation = state.row.get_allocation()
        return self._move_lifted(
            state, allocation.x, allocation.y - adjustment.get_value()
        )

    def _place_lifted_at_pointer(
        self, state: ActionDragState, pointer_x: float, pointer_y: float
    ) -> tuple[float, float] | None:
        return self._move_lifted(
            state,
            pointer_x - state.grab_offset_x,
            pointer_y - state.grab_offset_y,
        )

    def _move_lifted(
        self, state: ActionDragState, _x: float, y: float
    ) -> tuple[float, float] | None:
        lifted = state.lifted
        if lifted is None:
            return None
        # Reordering is vertical-only: the lifted row stays locked to the list
        # row's x and follows the pointer on the y axis, so it reads as an item
        # sliding up and down the column rather than a free-floating card. Its
        # width already matches the row, so no horizontal clamp is needed.
        offset_x, offset_y = self._list_offset_in_layer()
        fixed_x = state.row.get_allocation().x + offset_x
        y = y + offset_y  # list-frame (scrolled) y -> lifted-layer y
        viewport_height = self._lifted_layer.get_height()
        lifted_height = state.row.get_height() or lifted.get_height()
        if viewport_height > 1 and lifted_height > 1:
            y = max(0.0, min(y, max(0.0, viewport_height - lifted_height)))
        self._sync_lifted_width(state)
        lifted.set_margin_top(max(0, int(round(y))))
        if not getattr(lifted, "_width_tick", None):
            # Allocations can still be settling when the row is lifted; keep
            # the horizontal margins in step with the source row each frame.
            lifted._width_tick = lifted.add_tick_callback(
                lambda widget, _clock, drag=state: self._lifted_width_tick(widget, drag)
            )
        return (fixed_x, y)

    def _sync_lifted_width(self, state: ActionDragState) -> None:
        lifted = state.lifted
        if lifted is None:
            return
        # Use allocations throughout: get_width() excludes CSS box extents
        # that allocations include, which would skew the right margin.
        row_allocation = state.row.get_allocation()
        fixed_x = row_allocation.x + self._list_offset_in_layer()[0]
        layer_width = self._lifted_layer.get_allocation().width
        row_width = row_allocation.width
        start = max(0, int(round(fixed_x)))
        end = (max(0, int(round(layer_width - fixed_x - row_width)))
               if layer_width > 1 and row_width > 1 else 0)
        if lifted.get_margin_start() != start:
            lifted.set_margin_start(start)
        if lifted.get_margin_end() != end:
            lifted.set_margin_end(end)

    def _lifted_width_tick(self, widget: Gtk.Widget, state: ActionDragState) -> bool:
        if widget.get_parent() is None or state.lifted is not widget:
            widget._width_tick = None
            return GLib.SOURCE_REMOVE
        self._sync_lifted_width(state)
        return GLib.SOURCE_CONTINUE

    def _begin_action_reorder(self, action_id: str) -> bool:
        row = self._action_rows.get(action_id)
        if row is None:
            return False
        order = self.presentation.selected_macro.current_action_ids
        if action_id not in order:
            return False
        if self._action_drag is not None:
            self._finish_action_reorder_drag(commit=False)
        if self._inline_editor_state is not None:
            self._finish_inline_edit(commit=True)
        self._action_drag = ActionDragState(
            action_id=action_id,
            original_index=order.index(action_id),
            original_order=order,
            row=row,
        )
        row.add_css_class("macro-action-row-dragging")
        self._gesture_log(
            f"drag begin action={action_id} index={order.index(action_id)}"
        )
        return True

    def _build_lifted_action_preview(self, row: Gtk.Box) -> Gtk.Box:
        action = row._macro_action
        lifted = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        lifted.add_css_class("macro-action-row")
        lifted.add_css_class("macro-action-row-lifted")
        lifted.set_can_target(False)
        lifted.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        lifted.set_halign(Gtk.Align.FILL)
        lifted.set_valign(Gtk.Align.START)

        grip = Gtk.Label(label="⠿", xalign=.5)
        grip.add_css_class("macro-drag-grip")
        grip.set_valign(Gtk.Align.CENTER)
        grip.set_margin_top(5)
        grip.set_margin_end(2)
        lifted.append(grip)

        number = Gtk.Label(label=f"{action.index + 1:02d}", xalign=.5)
        number.add_css_class("macro-action-number")
        number.set_size_request(24, -1)
        lifted.append(number)
        lifted.append(self._direction_label(action))

        kind = Gtk.Label(label=action.action, xalign=0)
        kind.add_css_class("macro-action-kind")
        kind.set_ellipsize(3)
        lifted.append(kind)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        lifted.append(spacer)

        value_label = Gtk.Label(
            label=format_action_display_value(action.record),
            xalign=1,
            ellipsize=3,
        )
        value_label.add_css_class("macro-action-value")
        value_label.set_hexpand(True)
        lifted.append(value_label)

        return lifted

    def _action_row_children(self) -> list[Gtk.Box]:
        children: list[Gtk.Box] = []
        child = self._actions_box.get_first_child()
        while child is not None:
            if getattr(child, "_macro_action_id", None):
                children.append(child)
            child = child.get_next_sibling()
        return children

    def _action_list_drag_motion(self, _target, x: float, y: float):
        state = self._action_drag
        if state is None:
            return 0
        self._action_drag_update_pointer(x, y)
        if not state.grab_captured:
            self._capture_action_grab_offset(state, x, y)
        self._move_dragged_row_to(self._compute_target_index(y))
        if state.pointer_viewport_y is not None:
            self._place_lifted_at_pointer(state, x, state.pointer_viewport_y)
        return Gdk.DragAction.MOVE

    def _action_list_drag_drop(self, _target, value, x: float, y: float) -> bool:
        state = self._action_drag
        if state is None or not isinstance(value, str):
            return False
        if value != f"action-id:{state.action_id}":
            return False
        self._action_drag_update_pointer(x, y)
        self._commit_action_reorder()
        return True

    def _action_drag_update_pointer(self, x: float, y: float) -> None:
        state = self._action_drag
        if state is None:
            return
        adjustment = self._sequence_scroll.get_vadjustment()
        state.pointer_content_y = y
        state.pointer_viewport_y = y - adjustment.get_value()

    def _compute_target_index(self, content_y: float) -> int:
        state = self._action_drag
        if state is None:
            return 0
        index = 0
        for row in self._action_row_children():
            if row is state.row:
                continue
            allocation = row.get_allocation()
            if content_y > allocation.y + allocation.height / 2:
                index += 1
        return index

    def _move_dragged_row_to(self, index: int) -> None:
        state = self._action_drag
        if state is None:
            return
        others = [row for row in self._action_row_children() if row is not state.row]
        index = max(0, min(index, len(others)))
        sibling = None if index == 0 else others[index - 1]
        self._actions_box.reorder_child_after(state.row, sibling)

    def _commit_action_reorder(self) -> bool:
        state = self._action_drag
        if state is None:
            return False
        children = self._action_row_children()
        try:
            final_index = children.index(state.row)
        except ValueError:
            final_index = state.original_index
        self._gesture_log(
            f"drag commit action={state.action_id} "
            f"{state.original_index} -> {final_index}"
        )
        self._finish_action_reorder_drag(commit=True, final_index=final_index)
        return True

    def _finish_action_reorder_drag(
        self, *, commit: bool, final_index: int | None = None
    ) -> None:
        state = self._action_drag
        if state is None:
            return
        self._action_drag = None
        self._stop_action_autoscroll()
        if state.lifted is not None and state.lifted.get_parent() is self._lifted_layer:
            self._lifted_layer.remove_overlay(state.lifted)
        state.lifted = None
        state.row.remove_css_class("macro-action-row-dragging")
        if commit and final_index is not None and final_index != state.original_index:
            self.presentation.reorder_action(state.original_index, final_index)
        else:
            self._restore_action_order(state.original_order)

    def _restore_action_order(self, order: tuple[str, ...]) -> None:
        previous: Gtk.Box | None = None
        for action_id in order:
            row = self._action_rows.get(action_id)
            if row is None:
                continue
            self._actions_box.reorder_child_after(row, previous)
            previous = row

    def _start_action_autoscroll(self) -> None:
        if self._action_autoscroll_id is None:
            self._action_autoscroll_id = GLib.timeout_add(
                50, self._action_autoscroll_tick
            )

    def _stop_action_autoscroll(self) -> None:
        if self._action_autoscroll_id is not None:
            GLib.source_remove(self._action_autoscroll_id)
            self._action_autoscroll_id = None

    def _action_autoscroll_tick(self) -> bool:
        state = self._action_drag
        if state is None or state.pointer_viewport_y is None:
            self._action_autoscroll_id = None
            return GLib.SOURCE_REMOVE
        adjustment = self._sequence_scroll.get_vadjustment()
        viewport = adjustment.get_page_size()
        if viewport <= 0:
            return GLib.SOURCE_CONTINUE
        threshold = 36.0
        max_step = 18.0
        pointer_y = state.pointer_viewport_y
        if pointer_y < threshold:
            direction = -1
            proximity = (threshold - pointer_y) / threshold
        elif pointer_y > viewport - threshold:
            direction = 1
            proximity = (pointer_y - (viewport - threshold)) / threshold
        else:
            return GLib.SOURCE_CONTINUE
        step = max(4.0, max_step * min(1.0, proximity + 0.35))
        value = adjustment.get_value()
        upper = adjustment.get_upper()
        lower = adjustment.get_lower()
        new_value = max(lower, min(value + direction * step, upper - viewport))
        if new_value != value:
            adjustment.set_value(new_value)
            content_y = pointer_y + adjustment.get_value()
            self._move_dragged_row_to(self._compute_target_index(content_y))
        return GLib.SOURCE_CONTINUE
