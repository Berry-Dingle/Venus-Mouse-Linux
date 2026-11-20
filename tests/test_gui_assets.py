import pytest

from venus_profile.gui.assets import (
    AssetError, TOP_ARTWORK, VM_ICON, artwork_bytes, icon_bytes,
)
from venus_profile.gui.buttons.artwork import texture_from_artwork


def test_artwork_loads_from_package_resources():
    assert artwork_bytes(TOP_ARTWORK).startswith(b"\x89PNG")


def test_vm_icon_loads_from_package_resources():
    assert VM_ICON == "venus_neon_icon.png"
    assert icon_bytes().startswith(b"\x89PNG")


def test_packaged_artwork_decodes_to_gdk_textures():
    gi = pytest.importorskip("gi")
    gi.require_version("Gdk", "4.0")
    texture = texture_from_artwork(TOP_ARTWORK)
    assert (texture.get_width(), texture.get_height()) == (756, 1482)


def test_corrupt_artwork_fails_with_resource_name(monkeypatch):
    from venus_profile.gui import assets
    from venus_profile.gui.buttons import artwork

    monkeypatch.setattr(artwork, "artwork_bytes", lambda _name: b"not-png")
    with pytest.raises(AssetError, match="venus_top_view_crop.png.*failed to decode"):
        artwork.texture_from_artwork(TOP_ARTWORK)


def test_missing_artwork_fails_with_resource_name(monkeypatch):
    from venus_profile.gui import assets

    class Missing:
        def read_bytes(self):
            raise FileNotFoundError

    monkeypatch.setattr(assets, "files", lambda _package: Missing())
    with pytest.raises(AssetError, match="missing packaged artwork resource.*venus_top_view_crop.png"):
        assets.artwork_bytes(TOP_ARTWORK)


def test_asset_loader_has_no_absolute_development_path():
    from pathlib import Path
    source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "assets.py"
    ).read_text(encoding="utf-8")
    assert "/home/" not in source
    artwork_source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "buttons", "artwork.py"
    ).read_text(encoding="utf-8")
    assert "new_from_stream" not in artwork_source


def test_header_branding_uses_packaged_icon_and_shared_text_block():
    from pathlib import Path

    source = Path(__file__).parents[1].joinpath(
        "src", "venus_profile", "gui", "application.py"
    ).read_text(encoding="utf-8")
    assert "from .assets import icon_bytes" in source
    assert "Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)" in source
    assert "Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)" in source
    assert "Gtk.Image.new_from_paintable" in source
    assert "set_pixel_size(46)" in source
    assert "Venus Mouse — Wired MMO Mouse Utility" in source
    assert "/home/" not in source and "vm.icon.png" not in source


def test_unused_side_artwork_is_not_shipped():
    from pathlib import Path
    from venus_profile.gui import assets

    data = Path(__file__).parents[1] / "src" / "venus_profile" / "gui" / "data"
    assert not (data / "venus_side_view_crop.png").exists()
    assert not hasattr(assets, "SIDE_ARTWORK")
    with pytest.raises(AssetError, match="unknown packaged GUI asset"):
        assets.artwork_bytes("venus_side_view_crop.png")
