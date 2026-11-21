"""Button assignment editor bound to the selected profile's PFD."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

from ... import button_functions as bf
from ...hid_usage import MODIFIER_USAGES, key_name, parse_key, usage_from_gdk_keycode
from ..accessibility import apply_accessible_label
from ..models import PhysicalControl

CATEGORIES = tuple(bf.CATEGORY_ORDER)
FIRE_TRIGGERS = (
    ("Left button", bf.FIRE_TRIGGER_LEFT),
    ("Right button", bf.FIRE_TRIGGER_RIGHT),
    ("Middle button", bf.FIRE_TRIGGER_MIDDLE),
    ("Keyboard key", None),
)


SLOT_PLACEHOLDER = "Choose a slot…"
TAB_USAGE = 0x2B
#: Right-hand modifiers are stored as their left-hand twin: the Windows editor
#: only uses the left ones and the device mask has bits for Ctrl/Shift/Alt/Super.
LEFT_MODIFIER = {0xE0: 0xE0, 0xE1: 0xE1, 0xE2: 0xE2, 0xE3: 0xE3,
                 0xE4: 0xE0, 0xE5: 0xE1, 0xE6: 0xE2, 0xE7: 0xE3}


def _typed_value(spin: Gtk.SpinButton) -> int:
    """The number being typed (before Enter/focus-out commits it), clamped to the range.

    A GTK spin button only updates its value on Enter or focus-out, so edits
    typed into it would otherwise not reach the profile (or enable Apply).
    """
    digits = "".join(ch for ch in spin.get_text() if ch.isdigit())
    if not digits:
        return int(spin.get_value())
    low, high = spin.get_range()
    return int(min(max(int(digits), low), high))


class IncompleteChoice(ValueError):
    """The editor needs more input (e.g. a key) before it can apply."""


class KeyEntry(Gtk.Entry):
    """Entry that captures one pressed key as a HID usage, or parses a name."""

    def __init__(self, placeholder: str, *, allow_modifiers: bool = False) -> None:
        super().__init__(placeholder_text=placeholder)
        self.usage: int | None = None
        self._allow_modifiers = allow_modifiers
        self._pending_modifier: int | None = None    # a modifier pressed with nothing else (yet)
        self._on_change = None
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key_pressed)
        keys.connect("key-released", self._key_released)
        self.add_controller(keys)
        self.connect("changed", self._text_changed)
        # A typed key name applies on Enter or when focus leaves the field.
        self.connect("activate", lambda _entry: self._commit_typed())
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda _controller: self._commit_typed())
        self.add_controller(focus)
        tip = "Press the key to assign, or type its name (for example F, Space, Tab, F5, Keypad +)"
        if allow_modifiers:
            tip += ". Shift, Ctrl, Alt and Super assign when pressed and released on their own"
        self.set_tooltip_text(tip + ". Ctrl+Tab moves to the next field.")

    def connect_change(self, callback) -> None:
        self._on_change = callback

    def set_usage(self, usage: int | None) -> None:
        self.usage = usage
        self.set_text(key_name(usage) if usage else "")
        self.remove_css_class("error")

    def _key_pressed(self, _controller, keyval, keycode, state) -> bool:
        usage = usage_from_gdk_keycode(keycode)
        if usage in MODIFIER_USAGES:
            # Wait for the release: Ctrl+Tab (move focus) must not assign Ctrl.
            self._pending_modifier = usage if self._allow_modifiers else None
            return True
        self._pending_modifier = None
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) and state & Gdk.ModifierType.CONTROL_MASK:
            return False                                 # Ctrl+Tab: let GTK move the focus on
        if usage is None:
            return False
        self._capture(usage)
        return True

    def _key_released(self, _controller, _keyval, keycode, _state) -> None:
        usage = usage_from_gdk_keycode(keycode)
        pending, self._pending_modifier = self._pending_modifier, None
        if pending is not None and usage == pending:
            self._capture(pending)

    def _capture(self, usage: int) -> None:
        self.set_usage(usage)
        self.set_position(-1)
        if self._on_change:
            self._on_change()

    def _commit_typed(self) -> None:
        if self.usage is not None and self._on_change:
            self._on_change()

    def _text_changed(self, _entry) -> None:
        text = self.get_text().strip()
        if not text:
            self.usage = None
            return
        try:
            usage = parse_key(text)
        except ValueError:
            self.usage = None
            self.add_css_class("error")
            return
        if usage in MODIFIER_USAGES and not self._allow_modifiers:
            self.usage = None
            self.add_css_class("error")
            return
        self.usage = usage
        self.remove_css_class("error")


class AssignmentPanel(Gtk.Box):
    def __init__(self, workspace, control: PhysicalControl) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add_css_class("assignment-panel")
        self.workspace = workspace
        self._control = control
        self._syncing = False

        selected_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        selected_card.add_css_class("selected-control-card")
        self._title = Gtk.Label(xalign=0)
        self._title.add_css_class("panel-title")
        self._subtitle = Gtk.Label(xalign=0)
        self._subtitle.add_css_class("dim-label")
        selected_card.append(self._title)
        selected_card.append(self._subtitle)
        self.append(selected_card)

        current_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        current_card.add_css_class("section-card")
        current_label = Gtk.Label(label="Current assignment", xalign=0)
        current_label.add_css_class("section-label")
        current_card.append(current_label)
        self._current = Gtk.Label(xalign=0, wrap=True)
        self._current.add_css_class("assignment-value")
        current_card.append(self._current)
        self._current_note = Gtk.Label(xalign=0, wrap=True)
        self._current_note.add_css_class("dim-label")
        current_card.append(self._current_note)
        self.append(current_card)

        change_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        change_card.add_css_class("section-card")
        change_label = Gtk.Label(label="Change assignment", xalign=0)
        change_label.add_css_class("section-label")
        change_card.append(change_label)
        self._category = Gtk.DropDown.new_from_strings([item.value for item in CATEGORIES])
        apply_accessible_label(self._category, "Assignment category")
        self._category.connect("notify::selected", self._category_changed)
        change_card.append(self._category)

        self._function = Gtk.DropDown.new_from_strings(["—"])
        apply_accessible_label(self._function, "Assignment")
        self._function.connect("notify::selected", self._function_changed)
        change_card.append(self._function)

        self._key_entry = KeyEntry("Press a key…", allow_modifiers=True)
        apply_accessible_label(self._key_entry, "Key to assign")
        self._key_entry.connect_change(self._apply_choice)
        change_card.append(self._key_entry)

        self._combo_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        modifiers = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        modifiers.set_homogeneous(True)
        self._modifier_buttons: dict[int, Gtk.ToggleButton] = {}
        for usage in (0xE0, 0xE1, 0xE2, 0xE3):
            toggle = Gtk.ToggleButton(label=bf.MODIFIER_LABELS[usage])
            toggle.add_css_class("modifier-toggle")
            toggle.add_css_class("action-button")
            apply_accessible_label(toggle, f"{bf.MODIFIER_LABELS[usage]} modifier")
            toggle.connect("toggled", self._editor_changed)
            self._modifier_buttons[usage] = toggle
            modifiers.append(toggle)
        self._combo_box.append(modifiers)
        self._combo_key = KeyEntry("Plus key (optional)")
        apply_accessible_label(self._combo_key, "Combination key")
        self._combo_key.connect_change(self._apply_choice)
        self._combo_box.append(self._combo_key)
        change_card.append(self._combo_box)

        self._fire_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._fire_trigger = Gtk.DropDown.new_from_strings([label for label, _ in FIRE_TRIGGERS])
        apply_accessible_label(self._fire_trigger, "Fire key trigger")
        self._fire_trigger.connect("notify::selected", self._fire_trigger_changed)
        self._fire_box.append(self._fire_trigger)
        self._fire_key = KeyEntry("Key to repeat")
        apply_accessible_label(self._fire_key, "Fire key keyboard key")
        self._fire_key.connect_change(self._apply_choice)
        self._fire_box.append(self._fire_key)
        delay_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        delay_row.append(Gtk.Label(label="Delay between fires (ms)", xalign=0, hexpand=True))
        self._fire_delay = Gtk.SpinButton.new_with_range(bf.FIRE_DELAY_UI_MIN_MS, bf.FIRE_DELAY_UI_MAX_MS,
                                                         bf.FIRE_DELAY_STEP_MS)
        apply_accessible_label(self._fire_delay, "Delay between fires in milliseconds")
        # Measured 2026-11-20 (click counter): shot-to-shot gap = delay + ~104 ms (50→154, 60→164, 80→184).
        self._fire_delay.set_tooltip_text("Extra time between shots (20–250 ms; below 20 ms the mouse sends "
                                          "only the first click). The mouse adds about 100 ms of its own per "
                                          "shot, so 20 ms gives a shot every ~120 ms.")
        self._fire_delay.connect("value-changed", self._editor_changed)
        self._fire_delay.connect("changed", self._spin_typed)          # typing counts at once, not only on Enter
        delay_row.append(self._fire_delay)
        self._fire_box.append(delay_row)
        count_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        count_row.append(Gtk.Label(label="Shots per press", xalign=0, hexpand=True))
        self._fire_count = Gtk.SpinButton.new_with_range(1, bf.FIRE_COUNT_UI_MAX, 1)
        self._fire_count.set_value(bf.FIRE_DEFAULT_COUNT)
        apply_accessible_label(self._fire_count, "Shots fired per press")
        # Live-confirmed 2026-11-20 with the click counter: the total equals this value (5…99).
        self._fire_count.set_tooltip_text("Total shots per press, counting the first one (1–255)")
        self._fire_count.connect("value-changed", self._editor_changed)
        self._fire_count.connect("changed", self._spin_typed)
        count_row.append(self._fire_count)
        self._fire_box.append(count_row)
        change_card.append(self._fire_box)

        # Entry 0 is a "Choose a slot…" placeholder; device slot s is entry s + 1.
        self._macro_slot = Gtk.DropDown.new_from_strings(
            [SLOT_PLACEHOLDER] + [f"Slot {index + 1}" for index in range(15)])
        apply_accessible_label(self._macro_slot, "Device macro slot")
        self._macro_slot.connect("notify::selected", self._macro_slot_changed)
        change_card.append(self._macro_slot)

        # Changes apply immediately (the working profile saves itself); the
        # profile bar's Cancel undoes edits since the profile was loaded.
        self._message = Gtk.Label(xalign=0, wrap=True)
        self._message.add_css_class("assignment-message")
        change_card.append(self._message)
        self.append(change_card)
        self.update_control(control)

    # ------------------------------------------------------------ helpers
    @property
    def profile(self):
        return self.workspace.profile()

    @property
    def control(self) -> PhysicalControl:
        return self._control

    def _view(self) -> bf.ButtonAssignmentView:
        return bf.read_assignment(self.profile, self._control.protocol_button)

    def _selected_category(self) -> bf.Category:
        index = self._category.get_selected()
        return CATEGORIES[index] if 0 <= index < len(CATEGORIES) else bf.Category.MOUSE

    def _category_functions(self) -> tuple[bf.ButtonFunction, ...]:
        return bf.functions_in(self._selected_category())

    def _set_function_choices(self, category: bf.Category, selected_code: int | None) -> None:
        functions = bf.functions_in(category)
        if getattr(self, "_function_category", None) is not category:
            # Rebuilding the model makes GTK emit a late selection notify;
            # _expected_function filters it so refreshes never apply.
            self._function.set_model(Gtk.StringList.new([item.label for item in functions]))
            self._function_category = category
        index = next((i for i, item in enumerate(functions) if item.code == selected_code), 0)
        self._expected_function = index
        if self._function.get_selected() != index:
            self._function.set_selected(index)

    def _update_editor_visibility(self) -> None:
        category = self._selected_category()
        functions = bf.functions_in(category)
        self._function.set_visible(len(functions) > 1)
        self._key_entry.set_visible(category is bf.Category.KEYBOARD)
        self._combo_box.set_visible(category is bf.Category.COMBO)
        self._fire_box.set_visible(category is bf.Category.FIRE)
        fire_index = self._fire_trigger.get_selected()
        self._fire_key.set_visible(fire_index == len(FIRE_TRIGGERS) - 1)
        self._macro_slot.set_visible(category is bf.Category.MACRO)

    def _slot_description(self, slot: int) -> str | None:
        """What a device slot holds: a library macro's name, "On mouse", or None if empty."""
        library = self.workspace.library
        macro = library.slot_macro(slot)
        if macro is not None:
            return macro.name
        if slot in library.mouse_only:
            return "On mouse"
        return None

    def _refresh_macro_slots(self) -> None:
        labels = [SLOT_PLACEHOLDER]
        for slot in range(15):
            labels.append(f"Slot {slot + 1} · {self._slot_description(slot) or 'Empty'}")
        if labels == getattr(self, "_macro_slot_labels", None):
            # Never swap the model needlessly: this runs from the dropdown's
            # own selection notify (live apply), where a model swap crashes GTK.
            return
        self._macro_slot_labels = labels
        selected = self._macro_slot.get_selected()
        selected = selected if 0 <= selected <= 15 else 0
        self._expected_macro_slot = selected
        self._macro_slot.set_model(Gtk.StringList.new(labels))
        self._macro_slot.set_selected(selected)

    def _say(self, text: str, *, warning: bool = False) -> None:
        self._message.set_text(text)
        if warning:
            self._message.add_css_class("warning-text")
        else:
            self._message.remove_css_class("warning-text")

    # ------------------------------------------------------------ handlers
    def _category_changed(self, _dropdown, _param) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self._set_function_choices(self._selected_category(), None)
            entering_macro = (self._selected_category() is bf.Category.MACRO
                              and self._view().function_code != bf.FUNCTION_MACRO)
            if entering_macro:
                # Don't assign a slot the user hasn't picked: clear the selection so
                # any slot they choose (including Slot 1) is applied.
                self._macro_slot.set_selected(0)       # the placeholder
                self._expected_macro_slot = 0
        finally:
            self._syncing = False
        self._update_editor_visibility()
        if entering_macro:
            self._say("Choose the device slot to play.")
            return
        self._apply_choice()

    def _fire_trigger_changed(self, _dropdown, _param) -> None:
        self._update_editor_visibility()
        if not self._syncing:
            self._apply_choice()

    def _spin_typed(self, spin: Gtk.SpinButton) -> None:
        """A key typed into a fire field: apply it now, without rewriting the field mid-typing."""
        if self._syncing or not any(ch.isdigit() for ch in spin.get_text()):
            return                                   # an emptied field is mid-edit: wait for digits
        self._typing_spin = spin
        try:
            self._apply_choice()
        finally:
            self._typing_spin = None

    def _editor_changed(self, *_args) -> None:
        if not self._syncing:
            self._apply_choice()

    def _function_changed(self, *_args) -> None:
        # Ignore late notifies that merely restate what refresh displayed.
        if self._syncing or self._function.get_selected() == getattr(self, "_expected_function", None):
            return
        self._expected_function = self._function.get_selected()
        self._apply_choice()

    def _macro_slot_changed(self, *_args) -> None:
        if self._syncing or self._macro_slot.get_selected() == getattr(self, "_expected_macro_slot", None):
            return
        self._expected_macro_slot = self._macro_slot.get_selected()
        self._apply_choice()

    def _apply_choice(self) -> None:
        """Apply the editor's current choice to the button immediately."""
        if self._syncing or getattr(self, "_applying", False):
            return
        self._applying = True
        try:
            self._apply_choice_now()
        finally:
            self._applying = False

    def _apply_choice_now(self) -> None:
        button = self._control.protocol_button
        profile = self.profile
        before = profile.to_bytes()
        try:
            apply = self._pending_assignment(button)
            trial = type(profile).from_bytes(before)
            apply(trial)
            if not bf.left_click_retained(trial):
                # Windows: "At least one click function to be retained."
                raise ValueError("At least one button must keep Left Click.")
        except IncompleteChoice as exc:
            self._say(str(exc))
            return
        except ValueError as exc:
            self._say(str(exc), warning=True)
            self.refresh()  # show what the button still does
            return
        apply(profile)
        if profile.to_bytes() != before:
            self._say("")
            self.workspace.edited("buttons")
        self.refresh()

    def _pending_assignment(self, button: int):
        """Return a function applying the editor's choice to a profile."""
        category = self._selected_category()
        if category is bf.Category.KEYBOARD:
            usage = self._key_entry.usage
            if usage is None:
                raise IncompleteChoice("Press or type the key to assign.")
            if usage in MODIFIER_USAGES:                 # Shift/Ctrl/Alt/Super on their own
                modifier = LEFT_MODIFIER[usage]
                return lambda target: bf.set_modifier_key(target, button, modifier)
            return lambda target: bf.set_single_key(target, button, usage)
        if category is bf.Category.COMBO:
            modifiers = tuple(usage for usage, toggle in self._modifier_buttons.items() if toggle.get_active())
            setting = bf.ComboSetting(modifiers, self._combo_key.usage)
            try:
                bf.encode_combo(setting)
            except ValueError as exc:
                raise IncompleteChoice(f"Choose modifiers and/or a key ({exc}).") from exc
            return lambda target: bf.set_combo(target, button, setting)
        if category is bf.Category.FIRE:
            index = self._fire_trigger.get_selected()
            trigger = FIRE_TRIGGERS[index][1] if 0 <= index < len(FIRE_TRIGGERS) else bf.FIRE_TRIGGER_LEFT
            if trigger is None:
                trigger = self._fire_key.usage
                if trigger is None:
                    raise IncompleteChoice("Choose the keyboard key to repeat.")
            delay = round(_typed_value(self._fire_delay) / bf.FIRE_DELAY_STEP_MS) * bf.FIRE_DELAY_STEP_MS
            count = _typed_value(self._fire_count)
            return lambda target: bf.set_fire_key(target, button, trigger, delay, count)
        if category is bf.Category.MACRO:
            slot = self._macro_slot.get_selected() - 1          # entry 0 is the placeholder
            if not 0 <= slot < 15:
                raise IncompleteChoice("Choose the device slot to play.")
            description = self._slot_description(slot)
            if description is None:
                raise IncompleteChoice(f"Device slot {slot + 1} is empty. Load a macro into it on the Macros page "
                                       "first, or choose another slot.")
            name = description if description != "On mouse" else f"Slot {slot + 1} (on mouse)"
            return lambda target: bf.set_macro(target, button, slot, name)
        functions = self._category_functions()
        index = self._function.get_selected()
        function = functions[index] if 0 <= index < len(functions) else functions[0]
        return lambda target: bf.set_simple_function(target, button, function.code)

    # ------------------------------------------------------------ refresh
    def update_control(self, control: PhysicalControl) -> None:
        self._control = control
        self._say("")
        self.refresh()

    def refresh(self) -> None:
        control = self._control
        view = self._view()
        self._title.set_text(control.display_label)
        self._subtitle.set_text(f"Profile {self.workspace.selected_slot} · {control.view.value.title()} · Button {control.protocol_button}")
        self._current.set_text(view.label)
        notes = []
        if not view.known:
            notes.append("This function is not recognised. It is kept exactly as stored until you assign something else.")
        if view.combo_error:
            notes.append(f"The stored key combination could not be read ({view.combo_error}); it is preserved.")
        if view.function_code == bf.FUNCTION_MACRO:
            slot_macro = self.workspace.library.slot_macro(view.key_number) if 0 <= view.key_number < 15 else None
            on_mouse = 0 <= view.key_number < 15 and view.key_number in self.workspace.library.mouse_only
            if slot_macro is None and on_mouse:
                notes.append(f"Slot {view.key_number + 1} holds a macro that is only on the mouse.")
            elif slot_macro is None:
                notes.append(f"Device slot {view.key_number + 1} is empty, so this button plays nothing.")
            elif slot_macro.name != view.macro_name:
                notes.append(f"Slot {view.key_number + 1} currently holds {slot_macro.name!r}.")
        self._current_note.set_text(" ".join(notes))
        self._current_note.set_visible(bool(notes))

        self._syncing = True
        try:
            self._refresh_macro_slots()
            category = view.category or bf.Category.MOUSE
            lone_modifier = view.combo.lone_modifier if view.combo else None
            if lone_modifier is not None:
                category = bf.Category.KEYBOARD          # a bare Shift etc. is shown as a keyboard key
            self._category.set_selected(CATEGORIES.index(category))
            self._set_function_choices(category, None if lone_modifier else view.function_code)
            if lone_modifier is not None:
                self._key_entry.set_usage(lone_modifier)
            else:
                self._key_entry.set_usage(view.single_key if view.function_code == bf.FUNCTION_SINGLE_KEY else None)
            combo = None if lone_modifier is not None else view.combo
            for usage, toggle in self._modifier_buttons.items():
                toggle.set_active(bool(combo and usage in combo.modifiers))
            self._combo_key.set_usage(combo.key if combo else None)
            fire = view.fire
            if fire is not None:
                trigger_index = {0: 0, bf.FIRE_TRIGGER_LEFT: 0, bf.FIRE_TRIGGER_RIGHT: 1,
                                 bf.FIRE_TRIGGER_MIDDLE: 2}.get(fire.trigger, 3)
                self._fire_trigger.set_selected(trigger_index)
                self._fire_key.set_usage(fire.trigger if trigger_index == 3 else None)
                typing = getattr(self, "_typing_spin", None)    # never rewrite the field being typed in
                if typing is not self._fire_delay:
                    self._fire_delay.set_value(min(max(fire.delay_ms, bf.FIRE_DELAY_UI_MIN_MS),
                                                   bf.FIRE_DELAY_UI_MAX_MS))
                if typing is not self._fire_count:
                    self._fire_count.set_value(min(fire.count or bf.FIRE_DEFAULT_COUNT, bf.FIRE_COUNT_UI_MAX))
            else:
                self._fire_trigger.set_selected(0)
                self._fire_key.set_usage(None)
                self._fire_delay.set_value(bf.FIRE_DELAY_UI_MIN_MS)
                self._fire_count.set_value(bf.FIRE_DEFAULT_COUNT)
            if view.function_code == bf.FUNCTION_MACRO and 0 <= view.key_number < 15:
                self._expected_macro_slot = view.key_number + 1
                self._macro_slot.set_selected(view.key_number + 1)
        finally:
            self._syncing = False
        self._update_editor_visibility()
