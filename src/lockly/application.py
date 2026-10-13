"""Adw.Application subclass: resources, styling, accelerators, about."""

from __future__ import annotations

import logging
from importlib import resources

from gi.repository import Adw, Gdk, Gio, Gtk

from . import APP_ID, APP_NAME, TAGLINE, __version__
from .core.settings import SettingsStore, default_vault_path

log = logging.getLogger(__name__)

ACCELS = {
    "win.new-entry": ["<Control>n"],
    "win.search": ["<Control>f"],
    "win.lock": ["<Control>l"],
    "win.edit-entry": ["<Control>e"],
    "win.copy-username": ["<Control>b"],
    "win.copy-password": ["<Control><Shift>c"],
    "win.generator": ["<Control>g"],
    "win.preferences": ["<Control>comma"],
    "win.show-shortcuts": ["<Control>question"],
    "app.quit": ["<Control>q"],
}


class LocklyApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.store: SettingsStore | None = None
        self.window = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)
        self.store = SettingsStore()
        self._load_resources()
        self.apply_color_scheme()

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit_safely())
        self.add_action(quit_action)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *_: self.show_about())
        self.add_action(about)
        for action, accels in ACCELS.items():
            self.set_accels_for_action(action, accels)

    def _load_resources(self) -> None:
        base = resources.files("lockly.resources")
        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_string(base.joinpath("style.css").read_text("utf-8"))
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        theme = Gtk.IconTheme.get_for_display(display)
        theme.add_search_path(str(base.joinpath("icons")))

    def apply_color_scheme(self) -> None:
        scheme = {
            "system": Adw.ColorScheme.DEFAULT,
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }[self.store.settings.color_scheme]
        Adw.StyleManager.get_default().set_color_scheme(scheme)

    def do_activate(self) -> None:
        from .window import LocklyWindow

        if self.window is None:
            self.window = LocklyWindow(self, default_vault_path(), self.store)
        self.window.present()

    def quit_safely(self) -> None:
        if self.window is not None:
            self.window.close()  # locks the vault and clears the clipboard
        self.quit()

    def show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=__version__,
            developer_name="Lockly contributors",
            comments=f"{TAGLINE}\n\nA local-first password manager for the Linux desktop.",
            license_type=Gtk.License.GPL_3_0,
        )
        about.add_legal_section(
            "EFF Large Wordlist", "© Electronic Frontier Foundation", Gtk.License.CUSTOM,
            "Used for passphrase generation under the Creative Commons Attribution 3.0 United States license.",
        )
        about.add_legal_section("libsodium / PyNaCl", None, Gtk.License.CUSTOM, "ISC License / Apache License 2.0")
        about.present(self.get_active_window())
