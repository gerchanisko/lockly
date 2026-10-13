#!/bin/sh
# Lockly installer for the current user (no root needed).
#
#   curl -fsSL https://raw.githubusercontent.com/gerchanisko/lockly/main/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- --uninstall
#
# Installs into ~/.local: a private Python environment, the `lockly` command
# and the desktop launcher. Your vault (~/.local/share/lockly) is never touched,
# not even by --uninstall.
set -eu

REPO="${LOCKLY_REPO:-https://github.com/gerchanisko/lockly}"
REF="${LOCKLY_REF:-main}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
VENV="$DATA/lockly-venv"
BIN="$HOME/.local/bin"
APP_ID=io.github.gerchanisko.Lockly

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

uninstall() {
    say "Removing Lockly (your vault is kept)"
    rm -rf "$VENV"
    [ -L "$BIN/lockly" ] && rm -f "$BIN/lockly"
    rm -f "$DATA/applications/$APP_ID.desktop" \
          "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg" \
          "$DATA/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" || true
    say "Done. Your vault is still in $DATA/lockly"
    exit 0
}

[ "${1:-}" = "--uninstall" ] && uninstall
[ "$(uname -s)" = Linux ] || die "Lockly currently runs on Linux only."

deps_hint() {
    . /etc/os-release 2>/dev/null || true
    case "${ID:-} ${ID_LIKE:-}" in
        *arch*)          echo "sudo pacman -S --needed python python-gobject gtk4 libadwaita python-pynacl" ;;
        *fedora*|*rhel*) echo "sudo dnf install python3 python3-gobject gtk4 libadwaita python3-pynacl" ;;
        *debian*|*ubuntu*) echo "sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-nacl python3-venv" ;;
        *suse*)          echo "sudo zypper install python3 python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 python3-PyNaCl" ;;
        *)               echo "install Python 3.11+, PyGObject, GTK 4.14+ and libadwaita 1.6+ from your distribution" ;;
    esac
}

say "Checking system libraries"
command -v python3 >/dev/null 2>&1 || die "python3 not found. Run: $(deps_hint)"
python3 - <<'EOF' || die "Missing or too old system libraries. Run: $(deps_hint)"
import sys
if sys.version_info < (3, 11):
    sys.exit("Python 3.11+ is required")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
if (Gtk.get_major_version(), Gtk.get_minor_version()) < (4, 14):
    sys.exit("GTK 4.14+ is required")
if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 6):
    sys.exit("libadwaita 1.6+ is required")
EOF
python3 -c "import venv, ensurepip" 2>/dev/null || die "Python venv support missing. Run: $(deps_hint)"

# Use this checkout if the script is run from one, otherwise download.
SRC=""
case "$0" in
    */install.sh|install.sh)
        HERE=$(cd "$(dirname "$0")" && pwd)
        [ -f "$HERE/pyproject.toml" ] && [ -d "$HERE/src/lockly" ] && SRC="$HERE" ;;
esac
if [ -z "$SRC" ]; then
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    say "Downloading Lockly ($REF)"
    if command -v git >/dev/null 2>&1; then
        git clone -q --depth 1 --branch "$REF" "$REPO.git" "$TMP/lockly"
    else
        command -v curl >/dev/null 2>&1 || die "Need git or curl to download Lockly."
        curl -fsSL "$REPO/archive/$REF.tar.gz" | tar -xz -C "$TMP"
        mv "$TMP"/lockly-* "$TMP/lockly"
    fi
    SRC="$TMP/lockly"
fi

say "Installing into $VENV"
rm -rf "$VENV"
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install -q --disable-pip-version-check "$SRC"

mkdir -p "$BIN"
ln -sf "$VENV/bin/lockly" "$BIN/lockly"

ICONS="$SRC/src/lockly/resources/icons/hicolor"
install -Dm644 "$SRC/data/$APP_ID.desktop" "$DATA/applications/$APP_ID.desktop"
sed -i "s|^Exec=.*|Exec=$VENV/bin/lockly|" "$DATA/applications/$APP_ID.desktop"
install -Dm644 "$ICONS/scalable/apps/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
install -Dm644 "$ICONS/symbolic/apps/$APP_ID-symbolic.svg" "$DATA/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" || true

say "Lockly is installed. Open it from your app menu, or run: lockly"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "   (Note: $BIN is not on your PATH; the app menu launcher still works.)" ;; esac
