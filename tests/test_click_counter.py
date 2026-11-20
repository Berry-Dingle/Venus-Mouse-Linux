"""Click counter diagnostic: burst grouping (pure logic) and no device access."""

import ast
from pathlib import Path

from venus_profile.gui import click_counter as cc


def test_presses_close_together_form_one_burst_with_gaps():
    counter = cc.BurstCounter(gap_ms=600)
    for t in (1000, 1050, 1101, 1150, 1200):           # own click + 4 fire shots, ~50 ms apart
        counter.press("Left", t)
    burst = counter.current
    assert (burst.presses, burst.extra, burst.gaps) == (5, 4, [50, 51, 49, 50])
    assert "5 presses (4 after the first)" in burst.summary() and "median 50 ms" in burst.summary()


def test_a_pause_or_another_button_starts_a_new_burst():
    counter = cc.BurstCounter(gap_ms=600)
    counter.press("Left", 0)
    counter.press("Left", 100)
    counter.press("Left", 1000)                        # 900 ms later: new burst
    counter.press("Right", 1010)                       # other button: new burst
    assert [b.presses for b in counter.finished] == [2, 1]
    assert counter.current.kind == "Right"


def test_idle_closes_the_current_burst_once():
    counter = cc.BurstCounter(gap_ms=600)
    counter.press("Key a", 0)
    assert counter.close_if_idle(500) is None
    closed = counter.close_if_idle(601)
    assert closed is not None and closed.presses == 1 and counter.current is None
    assert counter.close_if_idle(5000) is None and len(counter.finished) == 1
    counter.reset()
    assert counter.finished == [] and counter.current is None


def test_button_names():
    assert [cc.button_name(n) for n in (1, 2, 3, 8)] == ["Left", "Middle", "Right", "Button 8"]


def test_click_counter_never_touches_the_device():
    tree = ast.parse(Path(cc.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    assert imported <= {"__future__", "dataclasses", "statistics", "sys", "gi", "gi.repository"}
    source = Path(cc.__file__).read_text(encoding="utf-8")
    assert "hidraw" not in source and "Subprocess" not in source and "open(" not in source
