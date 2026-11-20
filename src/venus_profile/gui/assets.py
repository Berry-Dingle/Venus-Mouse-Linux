"""Packaged runtime assets for the offline GUI."""

from __future__ import annotations

from importlib.resources import files


ASSET_PACKAGE = "venus_profile.gui.data"
TOP_ARTWORK = "venus_top_view_crop.png"
# Header/app logo: the neon mouse (same artwork as the app icon).
VM_ICON = "venus_neon_icon.png"


class AssetError(RuntimeError):
    """A packaged GUI artwork resource is missing or unusable."""


def _asset_bytes(name: str, allowed: set[str]) -> bytes:
    if name not in allowed:
        raise AssetError(f"unknown packaged GUI asset {name!r}")
    try:
        resource = files(ASSET_PACKAGE) / name
        data = resource.read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, TypeError) as exc:
        raise AssetError(f"missing packaged artwork resource {name!r}") from exc
    if not data:
        raise AssetError(f"packaged artwork resource {name!r} is empty")
    return data


def artwork_bytes(name: str) -> bytes:
    return _asset_bytes(name, {TOP_ARTWORK})


def icon_bytes() -> bytes:
    return _asset_bytes(VM_ICON, {VM_ICON})
