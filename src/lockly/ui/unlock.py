"""Lock screen: re-authenticate with the master password."""

from __future__ import annotations

import logging
from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from .. import APP_ID
from ..core.database import DatabaseError
from ..core.migrations import VaultFormatError
from ..core.vault import Vault, VaultError, VaultLockedError, WrongPasswordError
from .common import run_async, wrap_label

log = logging.getLogger(__name__)


class UnlockView(Adw.Bin):
    def __init__(self, vault: Vault, on_unlocked: Callable[[], None]):
        super().__init__()
        self.vault = vault
        self.on_unlocked = on_unlocked
        self._busy = False
        self._failures = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        icon = Gtk.Image(icon_name=APP_ID, pixel_size=64, halign=Gtk.Align.START, margin_bottom=10)
        icon.update_property([Gtk.AccessibleProperty.LABEL], ["Lockly logo"])
        box.append(icon)
        heading = Gtk.Label(label="Vault locked", xalign=0, accessible_role=Gtk.AccessibleRole.HEADING)
        heading.add_css_class("auth-heading")
        box.append(heading)
        box.append(wrap_label("Enter your master password to unlock Lockly.", "dim-label"))

        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_top=18)
        group.add_css_class("boxed-list")
        self.password_row = Adw.PasswordEntryRow(title="Master password")
        self.password_row.connect("entry-activated", lambda *_: self.unlock())
        self.password_row.connect("changed", self._on_changed)
        group.append(self.password_row)
        box.append(group)

        self.error_label = wrap_label("", "error-text")
        self.error_label.set_visible(False)
        box.append(self.error_label)

        self.button = Gtk.Button(halign=Gtk.Align.START, margin_top=10, sensitive=False)
        content = Gtk.Box(spacing=8)
        self.spinner = Adw.Spinner(visible=False)
        content.append(self.spinner)
        self.button_label = Gtk.Label(label="Unlock")
        content.append(self.button_label)
        self.button.set_child(content)
        self.button.add_css_class("suggested-action")
        self.button.add_css_class("pill")
        self.button.connect("clicked", lambda *_: self.unlock())
        box.append(self.button)

        location = wrap_label(str(vault.path), "caption", "dim-label")
        location.set_margin_top(28)
        location.set_selectable(True)
        box.append(location)

        clamp = Adw.Clamp(maximum_size=400, child=box, valign=Gtk.Align.CENTER)
        clamp.set_margin_start(18)
        clamp.set_margin_end(18)
        clamp.set_margin_bottom(48)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp, vexpand=True)

        header = Adw.HeaderBar(show_title=False)
        header.add_css_class("flat")
        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        self.set_child(view)

    def focus(self) -> None:
        GLib.idle_add(lambda: self.password_row.grab_focus() and False)

    def reset(self) -> None:
        self.password_row.set_text("")
        self.error_label.set_visible(False)
        self.password_row.remove_css_class("error")
        self._set_busy(False)

    def _on_changed(self, *_args) -> None:
        self.button.set_sensitive(bool(self.password_row.get_text()) and not self._busy)
        self.password_row.remove_css_class("error")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.spinner.set_visible(busy)
        self.button_label.set_label("Unlocking…" if busy else "Unlock")
        self.password_row.set_sensitive(not busy)
        self.button.set_sensitive(not busy and bool(self.password_row.get_text()))

    def _show_error(self, message: str) -> None:
        self.error_label.set_label(message)
        self.error_label.set_visible(True)
        if hasattr(self.error_label, "announce"):  # GTK >= 4.14
            self.error_label.announce(message, Gtk.AccessibleAnnouncementPriority.HIGH)

    def unlock(self) -> None:
        password = self.password_row.get_text()
        if self._busy or not password:
            return
        self._set_busy(True)
        self.error_label.set_visible(False)

        def done(_result, error) -> None:
            self._set_busy(False)
            if error is None:
                self._failures = 0
                self.password_row.set_text("")
                self.on_unlocked()
                return
            if isinstance(error, WrongPasswordError):
                self._failures += 1
                self.password_row.add_css_class("error")
                self._show_error("Incorrect master password. Try again.")
                self.password_row.grab_focus()
                self.password_row.select_region(0, -1)
            elif isinstance(error, VaultLockedError):
                pass
            elif isinstance(error, (VaultFormatError, VaultError, DatabaseError)):
                self._show_error(str(error))
            else:
                log.error("Unexpected error while unlocking: %s", type(error).__name__)
                self._show_error("The vault could not be opened.")

        run_async(lambda: self.vault.unlock(password), done)
