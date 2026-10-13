#!/bin/sh
# Install the desktop launcher and icons for the current user (no root).
# Run after `pip install --user .` or from a development checkout.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
ICONS="$HERE/src/lockly/resources/icons/hicolor"

install -Dm644 "$HERE/data/io.github.gerchanisko.Lockly.desktop" "$DATA/applications/io.github.gerchanisko.Lockly.desktop"
install -Dm644 "$ICONS/scalable/apps/io.github.gerchanisko.Lockly.svg" "$DATA/icons/hicolor/scalable/apps/io.github.gerchanisko.Lockly.svg"
install -Dm644 "$ICONS/symbolic/apps/io.github.gerchanisko.Lockly-symbolic.svg" "$DATA/icons/hicolor/symbolic/apps/io.github.gerchanisko.Lockly-symbolic.svg"

if ! command -v lockly >/dev/null 2>&1; then
    # Development checkout: point the launcher at this tree.
    sed -i "s|^Exec=.*|Exec=env PYTHONPATH=$HERE/src python3 -m lockly|" "$DATA/applications/io.github.gerchanisko.Lockly.desktop"
fi
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" || true
echo "Installed Lockly launcher to $DATA/applications"
