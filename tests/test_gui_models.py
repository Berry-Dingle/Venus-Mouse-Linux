from __future__ import annotations

from venus_profile.gui.models import ButtonView, build_physical_control_registry


def test_registry_has_exactly_eighteen_controls_and_excludes_19_20():
    controls = build_physical_control_registry()
    assert len(controls) == 18
    assert [item.protocol_button for item in controls] == list(range(1, 19))
    assert all(item.protocol_button not in (19, 20) for item in controls)


def test_top_mapping_is_explicit_and_complete():
    controls = [item for item in build_physical_control_registry() if item.view is ButtonView.TOP]
    assert [(item.display_label, item.protocol_button) for item in controls] == [
        ("Left Click", 1),
        ("Right Click", 2),
        ("Wheel / Middle Click", 3),
        ("Fire Button", 4),
        ("DPI+", 5),
        ("DPI−", 6),
    ]
    assert controls[4].protocol_button == 5
    assert controls[5].protocol_button == 6


def test_side_mapping_and_four_by_three_geometry():
    controls = [item for item in build_physical_control_registry() if item.view is ButtonView.SIDE]
    by_position = {(item.grid_row, item.grid_column): item.protocol_button for item in controls}
    assert by_position == {
        (0, 0): 9, (0, 1): 12, (0, 2): 15, (0, 3): 18,
        (1, 0): 8, (1, 1): 11, (1, 2): 14, (1, 3): 17,
        (2, 0): 7, (2, 1): 10, (2, 2): 13, (2, 3): 16,
    }
    assert len({item.grid_column for item in controls}) == 4
    assert len({item.grid_row for item in controls}) == 3
    assert [(item.grid_row, item.grid_column, item.display_label) for item in controls] == [
        (2, 0, "Thumb Button 1"), (1, 0, "Thumb Button 2"),
        (0, 0, "Thumb Button 3"), (2, 1, "Thumb Button 4"),
        (1, 1, "Thumb Button 5"), (0, 1, "Thumb Button 6"),
        (2, 2, "Thumb Button 7"), (1, 2, "Thumb Button 8"),
        (0, 2, "Thumb Button 9"), (2, 3, "Thumb Button 10"),
        (1, 3, "Thumb Button 11"), (0, 3, "Thumb Button 12"),
    ]
    expected_width = .15 - (1 / 308.15625)
    assert {item.hotspot.width for item in controls} == {expected_width}
    assert {item.hotspot.height for item in controls} == {expected_width * 846 / 640}
    rows = {
        row: [item for item in controls if item.grid_row == row]
        for row in range(3)
    }
    assert all(
        upper.hotspot.y + upper.hotspot.height < lower.hotspot.y
        for upper, lower in zip(rows[0], rows[1])
    )
    assert all(
        upper.hotspot.y + upper.hotspot.height < lower.hotspot.y
        for upper, lower in zip(rows[1], rows[2])
    )


def test_side_controls_map_to_protocol_buttons_7_to_18():
    controls = [item for item in build_physical_control_registry() if item.view is ButtonView.SIDE]
    assert [item.protocol_button for item in controls] == list(range(7, 19))
    assert controls[0].description == "Side · Thumb Button 1"
