"""Presentation-only Top-view callout geometry."""

from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class TopCalloutSpec:
    presentation_id: str
    label: str
    anchor: tuple[float, float]
    side: str
    row: int
    label_box: tuple[float, float, float, float]
    elbow: tuple[float, float] | None = None
    horizontal_length: float = 0.0
    diagonal_endpoint: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class CalloutLayout:
    spec: TopCalloutSpec
    anchor: tuple[float, float]
    box: tuple[float, float, float, float]
    connector: tuple[tuple[float, float], ...]


TOP_CALLOUTS = (
    # Right-side geometry is explicit: the boxes share x/size, the horizontal
    # leaders are all 70px, and each diagonal endpoint is independently set.
    TopCalloutSpec("top.left-click", "Left Click", (.2894339622, .1961538462), "right", 0, (520, 3, 170, 34), horizontal_length=70, diagonal_endpoint=(450, 20)),
    TopCalloutSpec("top.middle-click", "Wheel / Middle Click", (.50, .2423076923), "right", 1, (520, 109, 170, 34), horizontal_length=70, diagonal_endpoint=(450, 126)),
    TopCalloutSpec("top.right-click", "Right Click", (.7433962264, .32), "right", 2, (520, 215, 170, 34), horizontal_length=70, diagonal_endpoint=(450, 232)),
    TopCalloutSpec("top.dpi-plus", "DPI+", (.50, .3626923077), "right", 3, (520, 321, 170, 34), horizontal_length=70, diagonal_endpoint=(450, 338)),
    TopCalloutSpec("top.dpi-minus", "DPI−", (.50, .4084615384), "right", 4, (520, 389, 170, 34), horizontal_length=70, diagonal_endpoint=(450, 406)),
    # Fire retains its existing (.14, .20) anchor; its box is 40px lower and
    # aligned to the Top selector's left edge at canvas x=0.
    TopCalloutSpec("top.fire", "Fire Button", (.14, .2307692307), "left", 0, (18, 122, 170, 34)),
)

TOP_CALLOUT_CANVAS_WIDTH = 700
TOP_CALLOUT_LABEL_X = 500
TOP_CALLOUT_LABEL_WIDTH = 180
TOP_CALLOUT_LABEL_HEIGHT = 34


def callout_layouts(
    painted_rectangle: tuple[float, float, float, float],
) -> tuple[CalloutLayout, ...]:
    image_x, image_y, image_width, image_height = painted_rectangle
    layouts = []
    for spec in TOP_CALLOUTS:
        anchor = (
            image_x + spec.anchor[0] * image_width,
            image_y + spec.anchor[1] * image_height,
        )
        box = spec.label_box
        box_edge = box[0] if spec.side == "right" else box[0] + box[2]
        center_y = box[1] + box[3] / 2
        # A direct leader is intentionally preferred. It is short, local to
        # the control, and cannot create a shared rail or a crossed trunk.
        target = (box_edge, center_y)
        if spec.side == "right":
            diagonal_endpoint = spec.diagonal_endpoint or (
                box[0] - spec.horizontal_length, center_y
            )
            connector = (anchor, diagonal_endpoint, target)
        else:
            connector = (anchor, target)
        layouts.append(CalloutLayout(spec, anchor, box, connector))
    return tuple(layouts)


def rects_overlap(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> bool:
    return not (
        first[0] + first[2] <= second[0]
        or second[0] + second[2] <= first[0]
        or first[1] + first[3] <= second[1]
        or second[1] + second[3] <= first[1]
    )
