#!/bin/sh
# Install the Venus Mouse launcher and icons for the current user only.
# Copies into ~/.local/share (no root needed); safe to run again.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
data=${XDG_DATA_HOME:-$HOME/.local/share}
mkdir -p "$data/applications" "$data/icons"
cp -r "$here/icons/hicolor" "$data/icons/"
cp "$here/io.github.berry_dingle.VenusMouse.desktop" "$data/applications/"
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$data/icons/hicolor" || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$data/applications" || true
echo "Installed Venus Mouse launcher to $data/applications"
