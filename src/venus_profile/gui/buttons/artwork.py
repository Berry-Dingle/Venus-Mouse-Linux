"""Top-view artwork and accessible callout overlay."""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gtk

from ..accessibility import apply_accessible_label, set_accessible_selected
from ..assets import AssetError, TOP_ARTWORK, artwork_bytes
from ..models import PhysicalControl
from .callouts import TOP_CALLOUT_CANVAS_WIDTH, callout_layouts
from .hotspots import transform_hotspot


@dataclass(frozen=True, slots=True)
class ArtworkPresentationPolicy:
    css_class: str
    max_width: int
    max_height: int


TOP_POLICY = ArtworkPresentationPolicy("artwork-top", 320, 520)
TOP_ARTWORK_MAX_WIDTH = TOP_POLICY.max_width
TOP_ARTWORK_MAX_HEIGHT = TOP_POLICY.max_height


class TextureViewport(Gtk.Widget):
    """A bounded texture viewport with an explicit painted rectangle."""

    def __init__(self, texture: Gdk.Texture, policy: ArtworkPresentationPolicy) -> None:
        super().__init__()
        self.texture = texture
        self.policy = policy
        self.painted_rectangle: tuple[float, float, float, float] = (
            0.0, 0.0, float(policy.max_width), float(policy.max_height)
        )
        self.set_size_request(policy.max_width, policy.max_height)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def do_measure(self, orientation: Gtk.Orientation, for_size: int):
        size = self.policy.max_width if orientation is Gtk.Orientation.HORIZONTAL else self.policy.max_height
        return size, size, -1, -1

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width = min(self.get_width(), self.policy.max_width)
        height = min(self.get_height(), self.policy.max_height)
        scale = min(width / self.texture.get_width(), height / self.texture.get_height())
        painted_width = self.texture.get_width() * scale
        painted_height = self.texture.get_height() * scale
        x = (self.get_width() - painted_width) / 2.0
        y = (self.get_height() - painted_height) / 2.0
        self.painted_rectangle = (x, y, painted_width, painted_height)
        rectangle = Graphene.Rect()
        rectangle.init(x, y, painted_width, painted_height)
        snapshot.append_texture(self.texture, rectangle)


class CalloutLayer(Gtk.DrawingArea):
    """Decorative connector and endpoint layer for Top callouts."""

    def __init__(self) -> None:
        super().__init__()
        self._layouts = ()
        self._selected_id: str | None = None
        self.set_can_target(False)
        self.set_focusable(False)
        self.set_draw_func(self._draw)

    def set_layouts(self, layouts, selected_id: str | None) -> None:
        self._layouts = layouts
        self._selected_id = selected_id
        self.queue_draw()

    def _draw(self, _area, context, _width, _height) -> None:
        for layout in self._layouts:
            selected = layout.spec.presentation_id == self._selected_id
            context.set_source_rgb(.18, .59, 1.0)
            context.set_line_width(2.0 if selected else 1.0)
            context.move_to(*layout.connector[0])
            for point in layout.connector[1:]:
                context.line_to(*point)
            context.stroke()
            context.arc(layout.anchor[0], layout.anchor[1], 4.5 if selected else 3.0, 0, 6.2832)
            context.fill()


def texture_from_artwork(name: str) -> Gdk.Texture:
    """Decode the packaged Top PNG without a temporary file."""
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(artwork_bytes(name)))
    except AssetError:
        raise
    except Exception as exc:
        raise AssetError(f"packaged artwork resource {name!r} failed to decode") from exc


class ArtworkView(Gtk.Overlay):
    """Top artwork with the six clickable physical-control callouts."""

    def __init__(self, controls: tuple[PhysicalControl, ...], on_selected) -> None:
        super().__init__()
        self._controls = controls
        self._on_selected = on_selected
        self._buttons: dict[str, Gtk.Button] = {}
        texture = texture_from_artwork(TOP_ARTWORK)
        self._texture_width = texture.get_width()
        self._texture_height = texture.get_height()
        self._viewport = TextureViewport(texture, TOP_POLICY)
        self.add_css_class(TOP_POLICY.css_class)
        self.set_size_request(TOP_CALLOUT_CANVAS_WIDTH, TOP_POLICY.max_height)
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.set_child(self._viewport)
        self._callout_layer = CalloutLayer()
        self._callout_layer.set_hexpand(True)
        self._callout_layer.set_vexpand(True)
        self.add_overlay(self._callout_layer)
        self._callout_buttons: dict[str, Gtk.Button] = {}
        for layout in callout_layouts((0, 0, self._texture_width, self._texture_height)):
            button = Gtk.Button(label=layout.spec.label)
            button.add_css_class("top-callout")
            button.set_can_focus(True)
            button.set_halign(Gtk.Align.START)
            button.set_valign(Gtk.Align.START)
            button.set_hexpand(False)
            button.set_vexpand(False)
            button.set_accessible_role(Gtk.AccessibleRole.BUTTON)
            apply_accessible_label(button, layout.spec.label)
            button.set_cursor_from_name("pointer")
            button.set_tooltip_text(layout.spec.label)
            button.set_visible(False)
            button.connect(
                "clicked",
                lambda _button, item=layout.spec.presentation_id: self._select_callout(item),
            )
            self._callout_buttons[layout.spec.presentation_id] = button
            self.add_overlay(button)
        self._layout_tick: int | None = None
        self._viewport.connect("map", self._position_hotspots)
        self.connect("map", self._position_hotspots)
        self.connect("map", self._refresh_after_map)
        # The view is mapped before GTK first allocates it (at launch the first
        # layout can come after every map-time attempt), so also retry on each
        # frame until the viewport has a size, then stop. Gtk.Widget has no
        # "width"/"height" properties to watch instead.
        self.connect("map", self._position_when_allocated)
        self.connect("unmap", self._stop_layout_tick)

    def _refresh_after_map(self, *_args) -> None:
        GLib.idle_add(self._position_hotspots)

    def _position_when_allocated(self, *_args) -> None:
        if self._layout_tick is None:
            self._layout_tick = self.add_tick_callback(self._on_layout_tick)

    def _on_layout_tick(self, _widget, _clock) -> bool:
        if self._painted_rectangle() is None:
            return GLib.SOURCE_CONTINUE
        self._layout_tick = None
        self._position_hotspots()
        return GLib.SOURCE_REMOVE

    def _stop_layout_tick(self, *_args) -> None:
        if self._layout_tick is not None:
            self.remove_tick_callback(self._layout_tick)
            self._layout_tick = None

    def _position_hotspots(self, *_args) -> None:
        painted = self._painted_rectangle()
        if painted is None:
            return
        layouts = callout_layouts(painted)
        self._callout_layer.set_layouts(layouts, getattr(self, "_selected_id", None))
        for layout in layouts:
            button = self._callout_buttons[layout.spec.presentation_id]
            button.set_margin_start(round(layout.box[0]))
            button.set_margin_top(round(layout.box[1]))
            button.set_size_request(round(layout.box[2]), round(layout.box[3]))
            button.set_visible(True)

    def set_assignment_tooltip(self, presentation_id: str, text: str) -> None:
        button = self._callout_buttons.get(presentation_id)
        if button is not None:
            button.set_tooltip_text(text)

    def _select_callout(self, presentation_id: str) -> None:
        control = next(item for item in self._controls if item.presentation_id == presentation_id)
        self._on_selected(control)

    def select(self, selected: PhysicalControl) -> None:
        self._selected_id = selected.presentation_id
        for presentation_id, button in self._callout_buttons.items():
            is_selected = presentation_id == selected.presentation_id
            if is_selected:
                button.add_css_class("selected-callout")
            else:
                button.remove_css_class("selected-callout")
            set_accessible_selected(button, is_selected)
        painted = self._painted_rectangle()
        if painted is not None:
            self._callout_layer.set_layouts(callout_layouts(painted), self._selected_id)

    def _painted_rectangle(self) -> tuple[float, float, float, float] | None:
        viewport_width = self._viewport.get_width()
        viewport_height = self._viewport.get_height()
        if min(viewport_width, viewport_height) <= 0:
            return None
        origin = Graphene.Point()
        origin.init(0.0, 0.0)
        ok, origin = self._viewport.compute_point(self, origin)
        if not ok:
            return None
        width = min(viewport_width, TOP_POLICY.max_width)
        height = min(viewport_height, TOP_POLICY.max_height)
        scale = min(width / self._texture_width, height / self._texture_height)
        painted_width = self._texture_width * scale
        painted_height = self._texture_height * scale
        x = (viewport_width - painted_width) / 2.0
        y = (viewport_height - painted_height) / 2.0
        return origin.x + x, origin.y + y, painted_width, painted_height
