"""Normalized hotspot helpers."""

from ..models import NormalizedRect


def scale_hotspot(rect: NormalizedRect, width: float, height: float) -> tuple[float, float, float, float]:
    return (rect.x * width, rect.y * height, rect.width * width, rect.height * height)


def content_rectangle(
    allocation_width: float,
    allocation_height: float,
    image_width: float,
    image_height: float,
    image_x: float = 0.0,
    image_y: float = 0.0,
) -> tuple[float, float, float, float]:
    """Return the letterboxed painted image rectangle in parent coordinates."""
    if min(allocation_width, allocation_height, image_width, image_height) <= 0:
        return (image_x, image_y, 0.0, 0.0)
    scale = min(allocation_width / image_width, allocation_height / image_height)
    painted_width = image_width * scale
    painted_height = image_height * scale
    return (
        image_x + (allocation_width - painted_width) / 2.0,
        image_y + (allocation_height - painted_height) / 2.0,
        painted_width,
        painted_height,
    )


def transform_hotspot(
    rect: NormalizedRect,
    painted_rectangle: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Transform normalized image coordinates into the painted rectangle."""
    x, y, width, height = painted_rectangle
    return (x + rect.x * width, y + rect.y * height,
            rect.width * width, rect.height * height)
