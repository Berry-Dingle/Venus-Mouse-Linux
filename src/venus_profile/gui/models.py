"""Physical-control registry for the Venus GUI (presentation geometry only)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ButtonView(str, Enum):
    TOP = "top"
    SIDE = "side"


@dataclass(frozen=True, slots=True)
class NormalizedRect:
    """A hotspot in image coordinates, independent of widget allocation."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not all(0.0 <= value <= 1.0 for value in (
            self.x, self.y, self.width, self.height
        )) or self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("hotspot must fit within normalized image bounds")


@dataclass(frozen=True, slots=True)
class PhysicalControl:
    presentation_id: str
    view: ButtonView
    display_label: str
    description: str
    protocol_button: int
    hotspot: NormalizedRect
    grid_column: int | None = None
    grid_row: int | None = None


def _top_controls() -> tuple[PhysicalControl, ...]:
    # Geometry is deliberately approximate presentation data. Identity is the
    # explicit protocol_button field, never derived from these rectangles.
    specs = (
        ("top.left-click", "Left Click", 1, NormalizedRect(.20, .16, .25, .33)),
        ("top.right-click", "Right Click", 2, NormalizedRect(.55, .16, .25, .33)),
        ("top.middle-click", "Wheel / Middle Click", 3, NormalizedRect(.43, .16, .14, .17)),
        ("top.fire", "Fire Button", 4, NormalizedRect(.10, .17, .09, .14)),
        ("top.dpi-plus", "DPI+", 5, NormalizedRect(.43, .34, .14, .08)),
        ("top.dpi-minus", "DPI−", 6, NormalizedRect(.43, .43, .14, .08)),
    )
    return tuple(
        PhysicalControl(
            presentation_id=identifier,
            view=ButtonView.TOP,
            display_label=label,
            description=f"Top · {label}",
            protocol_button=button,
            hotspot=hotspot,
        )
        for identifier, label, button, hotspot in specs
    )


def _side_controls() -> tuple[PhysicalControl, ...]:
    # The physical image is four columns by three rows. Button 1 starts at
    # the bottom-left and numbering advances upward, then across columns.
    controls = []
    # The side texture is 846x640.  Keep the hotspot width at 17% of the
    # painted image and compensate for that aspect ratio so the rendered
    # controls are square.  The centre spacing is then set for a 2px-smaller
    # gap than the previous geometry at the fixed side viewport size.
    # At the fixed 336x233 Side viewport, the 846x640 texture paints at
    # 308.15625px wide; this normalized offset is exactly 3 rendered pixels.
    side_x_shift = 3 / 308.15625
    horizontal_gap_adjustment = 2 / 308.15625
    column_shift = 2 / 308.15625
    columns_3_4_shift = 3 / 308.15625
    column_4_shift = 3 / 308.15625
    column_centres = (
        .230 + side_x_shift + 1.5 * horizontal_gap_adjustment,
        .4234 + side_x_shift + .5 * horizontal_gap_adjustment - column_shift,
        .6166 + side_x_shift - .5 * horizontal_gap_adjustment - column_shift - columns_3_4_shift,
        .810 + side_x_shift - 1.5 * horizontal_gap_adjustment - column_shift - columns_3_4_shift - column_4_shift,
    )
    # Tighten each rendered row gap by 3px while keeping the group centred.
    row_step = .256 - (5 / 233)
    row_shift = 10 / 233
    row_centres = (
        .500 - row_step - row_shift,
        .500 - row_shift,
        .500 + row_step - row_shift,
    )
    side_painted_width = 308.15625
    hotspot_width = .15 - (1 / side_painted_width)
    hotspot_height = hotspot_width * 846 / 640
    for number in range(1, 13):
        column = (number - 1) // 3
        row = 2 - ((number - 1) % 3)
        hotspot = NormalizedRect(
            column_centres[column] - hotspot_width / 2,
            row_centres[row] - hotspot_height / 2,
            hotspot_width,
            hotspot_height,
        )
        controls.append(PhysicalControl(
            presentation_id=f"side.thumb-{number}",
            view=ButtonView.SIDE,
            display_label=f"Thumb Button {number}",
            description=f"Side · Thumb Button {number}",
            protocol_button=number + 6,
            hotspot=hotspot,
            grid_column=column,
            grid_row=row,
        ))
    return tuple(controls)


def build_physical_control_registry() -> tuple[PhysicalControl, ...]:
    controls = _top_controls() + _side_controls()
    assert tuple(item.protocol_button for item in controls) == tuple(range(1, 19))
    return controls
