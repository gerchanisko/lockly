"""GDK clipboard backend and GLib timer scheduler for ClipboardGuard."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gdk, GLib, GObject

# Clipboard managers that understand this hint (e.g. KDE Klipper) do not
# record the copied value in their history.
PASSWORD_HINT_MIME = "x-kde-passwordManagerHint"


class GtkClipboardBackend:
    def __init__(self, display: Gdk.Display):
        self._clipboard = display.get_clipboard()

    def set_text(self, text: str, sensitive: bool) -> object:
        providers = [
            Gdk.ContentProvider.new_for_value(GObject.Value(GObject.TYPE_STRING, text)),
            Gdk.ContentProvider.new_for_bytes("text/plain;charset=utf-8", GLib.Bytes.new(text.encode("utf-8"))),
        ]
        if sensitive:
            providers.append(Gdk.ContentProvider.new_for_bytes(PASSWORD_HINT_MIME, GLib.Bytes.new(b"secret")))
        provider = Gdk.ContentProvider.new_union(providers)
        self._clipboard.set_content(provider)
        return provider

    def owns(self, token: object) -> bool:
        return self._clipboard.is_local() and self._clipboard.get_content() is token

    def clear(self) -> bool:
        if not self._clipboard.set_content(None):
            return False
        return self._clipboard.get_content() is None


def glib_scheduler(delay: float, callback: Callable[[], None]) -> Callable[[], None]:
    state = {"id": 0}

    def fire() -> bool:
        state["id"] = 0
        callback()
        return GLib.SOURCE_REMOVE

    state["id"] = GLib.timeout_add(int(delay * 1000), fire)

    def cancel() -> None:
        if state["id"]:
            GLib.source_remove(state["id"])
            state["id"] = 0

    return cancel
