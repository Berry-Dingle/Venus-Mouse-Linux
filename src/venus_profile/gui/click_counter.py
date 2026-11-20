"""Click counter: measure what a fire key really sends (diagnostic, user-run).

    PYTHONPATH=src python -m venus_profile.gui.click_counter

A plain GTK window. It never opens the mouse: it only counts the button-press
and key-press events the desktop delivers to the window. Presses close
together form one *burst*; a burst ends after ``BURST_GAP_MS`` without a
press. For each burst it shows the number of presses, the presses after the
first (your own click), and the gaps between presses, which also checks the
fire-key delay.

Raw press events are counted (``Gtk.EventControllerLegacy``): the usual click
gesture merges quick clicks into double/triple clicks and would undercount.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import statistics
import sys

#: A burst ends after this long without a press.
BURST_GAP_MS = 600


@dataclass(slots=True)
class Burst:
    kind: str                          # "Left", "Right", "Middle", "Button 8", "Key A", ...
    times: list[int] = field(default_factory=list)   # event timestamps (ms)

    @property
    def presses(self) -> int:
        return len(self.times)

    @property
    def extra(self) -> int:
        """Presses after the first one (the first is your own click)."""
        return max(0, self.presses - 1)

    @property
    def gaps(self) -> list[int]:
        return [later - earlier for earlier, later in zip(self.times, self.times[1:])]

    def summary(self) -> str:
        text = f"{self.kind}: {self.presses} presses ({self.extra} after the first)"
        gaps = self.gaps
        if gaps:
            text += (f" · gap median {statistics.median(gaps):.0f} ms"
                     f" (min {min(gaps)}, max {max(gaps)})")
        return text


class BurstCounter:
    """Groups press timestamps into bursts (pure logic; no GTK)."""

    def __init__(self, gap_ms: int = BURST_GAP_MS) -> None:
        self.gap_ms = gap_ms
        self.current: Burst | None = None
        self.finished: list[Burst] = []

    def press(self, kind: str, time_ms: int) -> Burst:
        current = self.current
        if current is not None and (current.kind != kind or time_ms - current.times[-1] > self.gap_ms):
            self.finished.append(current)
            current = None
        if current is None:
            current = self.current = Burst(kind)
        current.times.append(time_ms)
        return current

    def close_if_idle(self, now_ms: int) -> Burst | None:
        """Finish the current burst if nothing came for ``gap_ms``; returns it."""
        current = self.current
        if current is not None and now_ms - current.times[-1] > self.gap_ms:
            self.finished.append(current)
            self.current = None
            return current
        return None

    def reset(self) -> None:
        self.current = None
        self.finished.clear()


BUTTON_NAMES = {1: "Left", 2: "Middle", 3: "Right"}


def button_name(number: int) -> str:
    return BUTTON_NAMES.get(number, f"Button {number}")


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - interactive
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk

    counter = BurstCounter()
    app = Gtk.Application(application_id=None)

    def activate(application):
        window = Gtk.ApplicationWindow(application=application, title="Venus Click Counter")
        window.set_default_size(640, 480)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        hint = Gtk.Label(label=("Hold the pointer over the box and press the fire button once.\n"
                                f"A burst ends after {BURST_GAP_MS} ms without a press. "
                                "Keyboard fire keys count too (keep this window focused)."),
                         wrap=True, xalign=0)
        box.append(hint)
        target = Gtk.Frame(label="Click target")
        target.set_size_request(-1, 160)
        live = Gtk.Label(label="Waiting for a press…")
        live.set_vexpand(True)
        target.set_child(live)
        box.append(target)
        history = Gtk.Label(label="", xalign=0, yalign=0, selectable=True, wrap=True)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(history)
        box.append(scroller)
        reset = Gtk.Button(label="Clear")
        box.append(reset)
        window.set_child(box)

        def show():
            lines = [burst.summary() for burst in reversed(counter.finished[-30:])]
            history.set_text("\n".join(lines))
            current = counter.current
            live.set_text(current.summary() if current else "Waiting for a press…")

        last_press = {"wall_ms": 0.0}

        def counted(kind, event):
            counter.press(kind, event.get_time())
            last_press["wall_ms"] = GLib.get_monotonic_time() / 1000
            show()

        def on_button(controller, event):
            event = event or controller.get_current_event()   # PyGObject may pass None
            if event is None:
                return False
            # Only presses over the target box (so clicking Clear is not counted).
            if event.get_event_type() == Gdk.EventType.BUTTON_PRESS:
                counted(button_name(event.get_button()), event)
            return False

        def on_key(controller, event):
            event = event or controller.get_current_event()   # PyGObject may pass None
            if event is None:
                return False
            if event.get_event_type() == Gdk.EventType.KEY_PRESS:
                counted(f"Key {Gdk.keyval_name(event.get_keyval()) or '?'}", event)
            return False

        buttons = Gtk.EventControllerLegacy()
        buttons.connect("event", on_button)
        target.add_controller(buttons)
        keys = Gtk.EventControllerLegacy()
        keys.connect("event", on_key)
        window.add_controller(keys)

        def tick():
            current = counter.current
            if current is not None:
                # Event timestamps use their own clock: advance the last press by wall time since it.
                idle = GLib.get_monotonic_time() / 1000 - last_press["wall_ms"]
                if counter.close_if_idle(current.times[-1] + int(idle)):
                    show()
            return GLib.SOURCE_CONTINUE

        GLib.timeout_add(100, tick)
        reset.connect("clicked", lambda _b: (counter.reset(), show()))
        window.present()

    app.connect("activate", activate)
    return app.run(argv if argv is not None else sys.argv[:1])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
