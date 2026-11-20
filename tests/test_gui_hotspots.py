from venus_profile.gui.buttons.hotspots import content_rectangle, scale_hotspot, transform_hotspot
from venus_profile.gui.models import NormalizedRect


def test_hotspot_scaling_is_independent_of_protocol_identity():
    rect = NormalizedRect(.25, .10, .50, .20)
    assert scale_hotspot(rect, 800, 400) == (200.0, 40.0, 400.0, 80.0)


def test_letterboxed_content_rectangle_and_hotspot_transform():
    rect = content_rectangle(1000, 600, 500, 1000)
    assert rect == (350.0, 0.0, 300.0, 600.0)
    hotspot = transform_hotspot(NormalizedRect(.25, .25, .5, .5), rect)
    assert hotspot == (425.0, 150.0, 150.0, 300.0)


def test_content_rectangle_reacts_to_resize_without_changing_image_space():
    first = transform_hotspot(NormalizedRect(.1, .2, .2, .2), content_rectangle(800, 600, 846, 640))
    second = transform_hotspot(NormalizedRect(.1, .2, .2, .2), content_rectangle(1200, 700, 846, 640))
    assert first != second
    assert all(value >= 0 for value in first + second)


def test_transformed_hotspot_stays_inside_painted_image():
    painted = content_rectangle(700, 350, 756, 1482)
    transformed = transform_hotspot(NormalizedRect(.1, .1, .2, .2), painted)
    x, y, width, height = transformed
    px, py, pw, ph = painted
    assert px <= x <= x + width <= px + pw
    assert py <= y <= y + height <= py + ph
