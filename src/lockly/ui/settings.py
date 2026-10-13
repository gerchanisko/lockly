"""Preferences dialog."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import Adw, Gtk

from .. import APP_NAME, __version__
from ..core import crypto, migrations
from ..core.generator import MAX_LENGTH, MAX_WORDS, MIN_WORDS
from ..core.settings import AUTO_LOCK_CHOICES, CLIPBOARD_CLEAR_CHOICES, COLOR_SCHEMES, SettingsStore
from ..core.vault import Vault
from .common import wrap_label


@dataclass
class VaultActions:
    change_password: Callable[[], None]
    export_backup: Callable[[], None]
    import_backup: Callable[[], None]
    export_csv: Callable[[], None]
    add_samples: Callable[[], None]
    open_folder: Callable[[], None]


def _minutes_label(minutes: int) -> str:
    if minutes == 0:
        return "Never"
    if minutes == 60:
        return "After 1 hour"
    return f"After {minutes} minute{'s' if minutes != 1 else ''}"


def _seconds_label(seconds: int) -> str:
    if seconds == 0:
        return "Never"
    if seconds >= 60:
        minutes = seconds / 60
        return f"After {minutes:g} minute{'s' if minutes != 1 else ''}"
    return f"After {seconds} seconds"


def _combo(title: str, subtitle: str, labels: list[str], selected: int, on_change: Callable[[int], None]) -> Adw.ComboRow:
    row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new(labels))
    row.set_selected(selected)
    row.connect("notify::selected", lambda r, _p: on_change(r.get_selected()))
    return row


def _button_row(title: str, subtitle: str, callback: Callable[[], None], destructive: bool = False) -> Adw.ButtonRow | Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
    row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
    if destructive:
        row.add_css_class("error")
    row.connect("activated", lambda *_: callback())
    return row


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, store: SettingsStore, vault: Vault, actions: VaultActions, apply_appearance: Callable[[], None], on_changed: Callable[[], None]):
        super().__init__(title="Settings", search_enabled=False)
        self.store = store
        self.settings = store.settings
        self._apply_appearance = apply_appearance
        self._on_changed = on_changed
        self.add(self._general_page())
        self.add(self._generator_page())
        self.add(self._vault_page(vault, actions))
        self.add(self._security_page(vault))

    def _save(self) -> None:
        self.store.save()
        self._on_changed()

    # General ----------------------------------------------------------------
    def _general_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        s = self.settings

        appearance = Adw.PreferencesGroup(title="Appearance")
        def set_scheme(i: int) -> None:
            s.color_scheme = COLOR_SCHEMES[i]
            self._apply_appearance()
            self._save()
        appearance.add(_combo("Style", "", ["Follow System", "Light", "Dark"], COLOR_SCHEMES.index(s.color_scheme), set_scheme))
        page.add(appearance)

        locking = Adw.PreferencesGroup(title="Locking")
        def set_lock(i: int) -> None:
            s.auto_lock_minutes = AUTO_LOCK_CHOICES[i]
            self._save()
        locking.add(_combo("Lock After Inactivity", "Lock the vault when Lockly has not been used",
                           [_minutes_label(m) for m in AUTO_LOCK_CHOICES], AUTO_LOCK_CHOICES.index(s.auto_lock_minutes), set_lock))
        screen = Adw.SwitchRow(title="Lock With the Screen", subtitle="Also lock when the screen locks or the computer suspends",
                               active=s.lock_on_screen_lock)
        def set_screen(row, _p) -> None:
            s.lock_on_screen_lock = row.get_active()
            self._save()
        screen.connect("notify::active", set_screen)
        locking.add(screen)
        page.add(locking)

        clipboard = Adw.PreferencesGroup(
            title="Clipboard",
            description="While a password is on the clipboard, any application on your desktop can read it. "
            "Clipboard history tools may keep their own copy; Lockly marks copied secrets so that tools "
            "honouring the hint (such as KDE Klipper) skip them.",
        )
        def set_clear(i: int) -> None:
            s.clipboard_clear_seconds = CLIPBOARD_CLEAR_CHOICES[i]
            self._save()
        clipboard.add(_combo("Clear Copied Secrets", "Only cleared if the clipboard still holds what Lockly copied",
                             [_seconds_label(x) for x in CLIPBOARD_CLEAR_CHOICES],
                             CLIPBOARD_CLEAR_CHOICES.index(s.clipboard_clear_seconds), set_clear))
        page.add(clipboard)
        return page

    # Generator ----------------------------------------------------------------
    def _generator_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Generator", icon_name="media-playlist-shuffle-symbolic")
        s = self.settings
        mode = Adw.PreferencesGroup(title="Default Mode")
        def set_mode(i: int) -> None:
            s.generator_mode = ("password", "passphrase")[i]
            self._save()
        mode.add(_combo("Generate", "", ["Password", "Passphrase"], 0 if s.generator_mode == "password" else 1, set_mode))
        page.add(mode)

        pw = Adw.PreferencesGroup(title="Password Defaults")
        length = Adw.SpinRow.new_with_range(8, MAX_LENGTH, 1)
        length.set_title("Length")
        length.set_value(s.password.length)
        def set_length(row, _p) -> None:
            s.password.length = int(row.get_value())
            self._save()
        length.connect("notify::value", set_length)
        pw.add(length)
        for key, title in (("uppercase", "Uppercase Letters"), ("lowercase", "Lowercase Letters"), ("digits", "Numbers"),
                           ("symbols", "Symbols"), ("exclude_ambiguous", "Avoid Ambiguous Characters")):
            row = Adw.SwitchRow(title=title, active=getattr(s.password, key))
            def toggle(r, _p, k=key) -> None:
                setattr(s.password, k, r.get_active())
                self._save()
            row.connect("notify::active", toggle)
            pw.add(row)
        page.add(pw)

        pp = Adw.PreferencesGroup(title="Passphrase Defaults")
        words = Adw.SpinRow.new_with_range(MIN_WORDS, MAX_WORDS, 1)
        words.set_title("Words")
        words.set_value(s.passphrase.words)
        def set_words(row, _p) -> None:
            s.passphrase.words = int(row.get_value())
            self._save()
        words.connect("notify::value", set_words)
        pp.add(words)
        for key, title in (("capitalize", "Capitalize Words"), ("include_number", "Include a Number")):
            row = Adw.SwitchRow(title=title, active=getattr(s.passphrase, key))
            def toggle_pp(r, _p, k=key) -> None:
                setattr(s.passphrase, k, r.get_active())
                self._save()
            row.connect("notify::active", toggle_pp)
            pp.add(row)
        page.add(pp)
        return page

    # Vault -------------------------------------------------------------------
    def _vault_page(self, vault: Vault, actions: VaultActions) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Vault", icon_name="drive-harddisk-symbolic")

        info = Adw.PreferencesGroup(title="Database")
        location = Adw.ActionRow(title="Location", subtitle=str(vault.path), subtitle_selectable=True, use_markup=False)
        location.add_css_class("property")
        open_button = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Open Folder")
        open_button.add_css_class("flat")
        open_button.update_property([Gtk.AccessibleProperty.LABEL], ["Open vault folder"])
        open_button.connect("clicked", lambda *_: actions.open_folder())
        location.add_suffix(open_button)
        info.add(location)
        try:
            count = len(vault.entries())
        except Exception:
            count = 0
        rows = [
            ("Entries", f"{count}"),
            ("Vault Format", f"Version {migrations.CURRENT_FORMAT_VERSION}"),
        ]
        try:
            size = vault.path.stat().st_size
            rows.append(("File Size", f"{size / 1024:.0f} KiB"))
        except OSError:
            pass
        for title, value in rows:
            row = Adw.ActionRow(title=title, subtitle=value)
            row.add_css_class("property")
            info.add(row)
        page.add(info)

        backup = Adw.PreferencesGroup(
            title="Backup",
            description="Encrypted backups are protected by a password you choose and can be restored on "
            "any computer running Lockly.",
        )
        backup.add(_button_row("Export Encrypted Backup…", "Save all entries to an encrypted file", actions.export_backup))
        backup.add(_button_row("Import Backup…", "Merge entries from a Lockly backup into this vault", actions.import_backup))
        page.add(backup)

        danger = Adw.PreferencesGroup(title="Unencrypted Export")
        danger.add(_button_row("Export as CSV…", "Writes every password in plain text. Requires your master password.",
                               actions.export_csv, destructive=True))
        page.add(danger)

        extras = Adw.PreferencesGroup(title="Sample Data")
        extras.add(_button_row("Add Sample Entries", "Add a few fictional entries to explore Lockly", actions.add_samples))
        page.add(extras)
        return page

    # Security ----------------------------------------------------------------
    def _security_page(self, vault: Vault) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Security", icon_name="security-high-symbolic")
        kdf = vault.kdf_params

        master = Adw.PreferencesGroup(title="Master Password")
        master.add(_button_row("Change Master Password…", "Also lets you choose a stronger key derivation setting",
                               lambda: self.activate_action("win.change-password", None)))
        page.add(master)

        details = Adw.PreferencesGroup(title="Encryption")
        profile = "custom"
        for name, params in crypto.KDF_PROFILES.items():
            if params == kdf:
                profile = name
        for title, value in (
            ("Key Derivation", f"Argon2id · {kdf.opslimit} passes · {kdf.memory_mib} MiB ({profile})"),
            ("Encryption", "XChaCha20-Poly1305 (libsodium)"),
            ("Encrypted", "Every entry field, including titles, types, tags and timestamps"),
            ("Not Encrypted", "Number of entries, approximate entry sizes, vault id, file timestamps"),
        ):
            row = Adw.ActionRow(title=title, subtitle=value)
            row.add_css_class("property")
            details.add(row)
        if vault.needs_kdf_upgrade():
            warn = Adw.ActionRow(title="Weaker Than Recommended",
                                 subtitle="This vault uses lighter key derivation than the current default. "
                                 "Change the master password (you may reuse it) to upgrade.")
            warn.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
            details.add(warn)
        page.add(details)

        limits = Adw.PreferencesGroup(title="Limitations")
        limits.add(wrap_label(
            "Lockly protects the vault file at rest. It cannot protect you from malware running as your user "
            "while the vault is unlocked, from keyloggers, or from someone who learns your master password. "
            "Decrypted entries live in memory while unlocked; Python cannot guarantee that every copy is "
            "wiped when the vault locks. Lockly has not had an independent security audit.",
            "dim-label",
        ))
        page.add(limits)

        about = Adw.PreferencesGroup(title=f"About {APP_NAME}")
        version = Adw.ActionRow(title="Version", subtitle=__version__)
        version.add_css_class("property")
        about.add(version)
        about.add(_button_row(f"About {APP_NAME}", "Credits and license", lambda: self.activate_action("app.about", None)))
        page.add(about)
        return page
