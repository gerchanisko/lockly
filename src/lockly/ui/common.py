"""Small UI helpers shared by several views."""

from __future__ import annotations

import datetime as _dt
import hashlib
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from gi.repository import Adw, GLib, Gtk

from ..core.generator import estimate_strength
from ..core.models import Entry, EntryType


def run_async(
    func: Callable[[], Any],
    on_done: Callable[[Any, BaseException | None], None],
) -> None:
    """Run ``func`` on a worker thread and deliver its result on the GTK
    main loop as ``on_done(result, error)``."""

    def worker() -> None:
        try:
            result, error = func(), None
        except BaseException as exc:  # noqa: BLE001 - forwarded to the UI
            result, error = None, exc

        def deliver() -> bool:
            on_done(result, error)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(deliver)

    threading.Thread(target=worker, daemon=True, name="lockly-worker").start()


def format_time(timestamp: float | None) -> str:
    if not timestamp:
        return "—"
    moment = _dt.datetime.fromtimestamp(timestamp)
    today = _dt.date.today()
    if moment.date() == today:
        return f"Today at {moment:%H:%M}"
    if moment.date() == today - _dt.timedelta(days=1):
        return f"Yesterday at {moment:%H:%M}"
    if moment.year == today.year:
        return f"{moment:%d %b} at {moment:%H:%M}".lstrip("0")
    return f"{moment:%d %b %Y}".lstrip("0")


def domain_of(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def safe_web_url(url: str) -> str | None:
    """Return a URL safe to hand to the desktop's browser, or None.

    Only http(s) URLs are opened. Lockly never executes commands or opens
    arbitrary URI schemes taken from vault contents.
    """
    url = url.strip()
    if not url:
        return None
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return url


def tile_for(entry: Entry, large: bool = False) -> Gtk.Widget:
    """A small rounded tile showing the service initial or the type icon.

    Lockly never downloads favicons: fetching icons would leak the list of
    services in the vault to the network.
    """
    seed = domain_of(entry.get("url")) or entry.title.casefold() or entry.id
    tint = int(hashlib.sha256(seed.encode()).hexdigest()[:4], 16) % 8
    box = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
    # Children expand inside the tile; the tile itself must not grow.
    box.set_hexpand(False)
    box.set_vexpand(False)
    box.add_css_class("entry-tile")
    box.add_css_class(f"tile-{tint}")
    if large:
        box.add_css_class("large")
    if entry.type is EntryType.LOGIN and entry.title.strip():
        child = Gtk.Label(label=entry.title.strip()[0].upper(), hexpand=True, halign=Gtk.Align.CENTER)
    else:
        child = Gtk.Image(icon_name=entry.type.icon_name, hexpand=True, halign=Gtk.Align.CENTER)
        child.set_pixel_size(24 if large else 16)
    child.set_valign(Gtk.Align.CENTER)
    child.set_vexpand(True)
    box.append(child)
    box.update_property([Gtk.AccessibleProperty.LABEL], [entry.type.label])
    return box


class StrengthIndicator(Gtk.Box):
    """Level bar + label showing a rough password strength estimate."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=6)
        self.bar = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True, valign=Gtk.Align.CENTER)
        self.bar.add_css_class("strength-bar")
        self.bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_LOW)
        self.bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_HIGH)
        self.bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_FULL)
        self.bar.add_offset_value("low", 0.36)
        self.bar.add_offset_value("high", 0.6)
        self.bar.add_offset_value("full", 0.8)
        self.label = Gtk.Label(xalign=1, width_chars=7)
        self.label.add_css_class("dim-label")
        self.label.add_css_class("caption")
        self.append(self.bar)
        self.append(self.label)
        self.update("")

    def update(self, password: str) -> None:
        score, label = estimate_strength(password)
        self.bar.set_value(score)
        self.label.set_label(label)
        self.bar.update_property([Gtk.AccessibleProperty.LABEL], [f"Password strength: {label or 'none'}"])


def alert(parent: Gtk.Widget, heading: str, body: str = "") -> None:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("ok", "OK")
    dialog.present(parent)


def confirm(
    parent: Gtk.Widget,
    heading: str,
    body: str,
    action_label: str,
    on_confirm: Callable[[], None],
    destructive: bool = True,
) -> Adw.AlertDialog:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("confirm", action_label)
    dialog.set_response_appearance(
        "confirm", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED
    )
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
        if response == "confirm":
            on_confirm()

    dialog.connect("response", on_response)
    dialog.present(parent)
    return dialog


def wrap_label(text: str, *classes: str, xalign: float = 0, selectable: bool = False) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign, wrap=True, selectable=selectable)
    label.set_wrap_mode(2)  # Pango.WrapMode.WORD_CHAR
    for css in classes:
        label.add_css_class(css)
    return label
