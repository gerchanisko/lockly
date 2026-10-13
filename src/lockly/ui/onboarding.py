"""First-run setup: create a new vault, or restore one from a backup."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .. import APP_ID, TAGLINE
from ..core import backup
from ..core.backup import BackupContents, BackupError
from ..core.demo import demo_entries
from ..core.vault import MIN_PASSWORD_LENGTH, Vault
from .common import StrengthIndicator, run_async, wrap_label

log = logging.getLogger(__name__)

SECURITY_POINTS = [
    ("dialog-warning-symbolic",
     "Your master password cannot be recovered. Lockly never stores it and has no reset "
     "mechanism. If you forget it, the contents of this vault are permanently lost."),
    ("security-high-symbolic",
     "Entries are encrypted on this device with XChaCha20-Poly1305. The key is derived from "
     "your master password with Argon2id."),
    ("network-offline-symbolic",
     "Lockly works offline. It does not sync, upload or contact any server."),
    ("document-save-symbolic",
     "Make encrypted backups regularly from Settings → Vault. A damaged disk or a lost "
     "laptop takes the vault with it."),
]


class OnboardingView(Adw.Bin):
    def __init__(self, vault_path: Path, on_finished: Callable[[Vault, str], None]):
        super().__init__()
        self.vault_path = vault_path
        self.on_finished = on_finished
        self._restore: BackupContents | None = None
        self._restore_file: Gio.File | None = None
        self._history: list[str] = []

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vhomogeneous=False)
        self.stack.add_named(self._build_welcome(), "welcome")
        self.stack.add_named(self._build_restore(), "restore")
        self.stack.add_named(self._build_password(), "password")
        self.stack.add_named(self._build_security(), "security")

        header = Adw.HeaderBar(show_title=False)
        header.add_css_class("flat")
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back", visible=False)
        self.back_button.connect("clicked", lambda *_: self._go_back())
        header.pack_start(self.back_button)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=460, tightening_threshold=360, child=self.stack, valign=Gtk.Align.CENTER)
        clamp.set_margin_start(18)
        clamp.set_margin_end(18)
        clamp.set_margin_bottom(36)
        scroller.set_child(clamp)

        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        self.set_child(view)

        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_key)
        self.add_controller(controller)

    # Navigation ------------------------------------------------------------
    def _show(self, name: str) -> None:
        current = self.stack.get_visible_child_name()
        if current and current != name:
            self._history.append(current)
        self.stack.set_visible_child_name(name)
        self.back_button.set_visible(bool(self._history))
        GLib.idle_add(self._focus_first, name)

    def _focus_first(self, name: str) -> bool:
        target = {"password": self.password_row, "restore": self.restore_password_row}.get(name)
        if target is not None:
            target.grab_focus()
        return GLib.SOURCE_REMOVE

    def _go_back(self) -> None:
        if self._busy or not self._history:
            return
        self.stack.set_visible_child_name(self._history.pop())
        self.back_button.set_visible(bool(self._history))

    def _on_key(self, _ctrl, keyval, _code, _state) -> bool:
        from gi.repository import Gdk

        if keyval == Gdk.KEY_Escape and self._history:
            self._go_back()
            return True
        return False

    _busy = False

    # Pages -----------------------------------------------------------------
    def _page(self, title: str, subtitle: str = "", step: str = "") -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        if step:
            label = Gtk.Label(label=step, xalign=0)
            label.add_css_class("dim-label")
            label.add_css_class("step-indicator")
            box.append(label)
        heading = Gtk.Label(label=title, xalign=0, wrap=True, accessible_role=Gtk.AccessibleRole.HEADING)
        heading.add_css_class("auth-heading")
        box.append(heading)
        if subtitle:
            box.append(wrap_label(subtitle, "dim-label"))
        return box

    def _build_welcome(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        icon = Gtk.Image(icon_name=APP_ID, pixel_size=72, halign=Gtk.Align.START, margin_bottom=12)
        icon.update_property([Gtk.AccessibleProperty.LABEL], ["Lockly logo"])
        box.append(icon)
        box.append(wrap_label("Welcome to Lockly", "auth-heading"))
        box.append(wrap_label(TAGLINE, "brand-tagline", "dim-label"))
        box.append(
            wrap_label(
                "Lockly keeps your logins, notes, identities and cards in an encrypted vault "
                "on this computer. Nothing leaves your device.",
                "body",
            )
        )
        box.get_last_child().set_margin_top(10)

        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=24)
        create = Gtk.Button(label="Create New Vault")
        create.add_css_class("suggested-action")
        create.add_css_class("pill")
        create.connect("clicked", lambda *_: self._start_create())
        restore = Gtk.Button(label="Restore from Backup…")
        restore.add_css_class("pill")
        restore.connect("clicked", lambda *_: self._choose_backup())
        buttons.append(create)
        buttons.append(restore)
        box.append(buttons)

        location = wrap_label(f"The vault will be stored at {self.vault_path}", "caption", "dim-label")
        location.set_margin_top(18)
        box.append(location)
        self.welcome_create = create
        return box

    def _build_restore(self) -> Gtk.Widget:
        box = self._page("Unlock the backup", "Enter the password that was used when this backup was exported.")
        self.restore_file_label = wrap_label("", "caption", "dim-label")
        box.append(self.restore_file_label)
        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        group.add_css_class("boxed-list")
        self.restore_password_row = Adw.PasswordEntryRow(title="Backup password")
        self.restore_password_row.connect("entry-activated", lambda *_: self._decrypt_backup())
        self.restore_password_row.connect("changed", lambda *_: self.restore_error.set_visible(False))
        group.append(self.restore_password_row)
        box.append(group)
        self.restore_error = wrap_label("", "error-text")
        self.restore_error.set_visible(False)
        box.append(self.restore_error)
        self.restore_button = self._action_button("Open Backup", self._decrypt_backup)
        box.append(self.restore_button)
        return box

    def _build_password(self) -> Gtk.Widget:
        box = self._page(
            "Choose a master password",
            "This one password unlocks everything in Lockly. Make it long and unique — a "
            "phrase of four or more unrelated words works well.",
            step="Step 1 of 2",
        )
        self.password_step_label = box.get_first_child()
        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        group.add_css_class("boxed-list")
        self.password_row = Adw.PasswordEntryRow(title="Master password")
        self.confirm_row = Adw.PasswordEntryRow(title="Confirm master password")
        for row in (self.password_row, self.confirm_row):
            row.connect("changed", lambda *_: self._validate_password())
            group.append(row)
        self.confirm_row.connect("entry-activated", lambda *_: self._password_continue())
        self.password_row.connect("entry-activated", lambda *_: self.confirm_row.grab_focus())
        box.append(group)

        self.strength = StrengthIndicator()
        box.append(self.strength)
        self.password_hint = wrap_label(
            f"At least {MIN_PASSWORD_LENGTH} characters.", "caption", "dim-label"
        )
        box.append(self.password_hint)
        self.password_continue = self._action_button("Continue", self._password_continue)
        self.password_continue.set_sensitive(False)
        box.append(self.password_continue)
        return box

    def _build_security(self) -> Gtk.Widget:
        box = self._page("Before you start", step="Step 2 of 2")
        points = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        points.add_css_class("info-list")
        for icon_name, text in SECURITY_POINTS:
            row = Gtk.Box(spacing=12)
            icon = Gtk.Image(icon_name=icon_name, valign=Gtk.Align.START, margin_top=2)
            icon.add_css_class("dim-label")
            row.append(icon)
            text_label = wrap_label(text)
            text_label.set_hexpand(True)
            row.append(text_label)
            points.append(row)
        box.append(points)

        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_top=6)
        group.add_css_class("boxed-list")
        self.understand_row = Adw.SwitchRow(
            title="I understand my master password cannot be recovered",
        )
        self.understand_row.connect("notify::active", lambda *_: self._update_create_sensitivity())
        self.demo_row = Adw.SwitchRow(
            title="Add sample entries",
            subtitle="Fictional data to explore Lockly. You can delete it later.",
        )
        group.append(self.understand_row)
        group.append(self.demo_row)
        box.append(group)

        self.create_error = wrap_label("", "error-text")
        self.create_error.set_visible(False)
        box.append(self.create_error)
        self.create_button = self._action_button("Create Vault", self._create_vault)
        self.create_button.set_sensitive(False)
        box.append(self.create_button)
        return box

    def _action_button(self, label: str, callback: Callable[[], None]) -> Gtk.Button:
        button = Gtk.Button(halign=Gtk.Align.CENTER, margin_top=8)
        content = Gtk.Box(spacing=8)
        spinner = Adw.Spinner(visible=False)
        content.append(spinner)
        content.append(Gtk.Label(label=label))
        button.set_child(content)
        button.add_css_class("suggested-action")
        button.add_css_class("pill")
        button.connect("clicked", lambda *_: callback())
        button._spinner = spinner  # type: ignore[attr-defined]
        return button

    def _set_busy(self, button: Gtk.Button, busy: bool) -> None:
        self._busy = busy
        button._spinner.set_visible(busy)  # type: ignore[attr-defined]
        button.set_sensitive(not busy)
        self.back_button.set_sensitive(not busy)

    # Create flow ------------------------------------------------------------
    def _start_create(self) -> None:
        self._restore = None
        self.password_step_label.set_label("Step 1 of 2")
        self._show("password")

    def _validate_password(self) -> bool:
        password = self.password_row.get_text()
        confirm = self.confirm_row.get_text()
        self.strength.update(password)
        ok = len(password) >= MIN_PASSWORD_LENGTH and password == confirm
        if len(password) < MIN_PASSWORD_LENGTH:
            hint = f"At least {MIN_PASSWORD_LENGTH} characters."
        elif confirm and password != confirm:
            hint = "The passwords do not match."
        else:
            hint = "Passwords match." if confirm else "Now repeat it to confirm."
        self.password_hint.set_label(hint)
        if confirm and password != confirm:
            self.confirm_row.add_css_class("error")
        else:
            self.confirm_row.remove_css_class("error")
        self.password_continue.set_sensitive(ok)
        return ok

    def _password_continue(self) -> None:
        if self._validate_password():
            self._show("security")

    def _update_create_sensitivity(self) -> None:
        self.create_button.set_sensitive(self.understand_row.get_active() and not self._busy)

    def _create_vault(self) -> None:
        if not self.understand_row.get_active() or self._busy:
            return
        password = self.password_row.get_text()
        restore = self._restore
        add_demo = self.demo_row.get_active()
        path = self.vault_path
        self.create_error.set_visible(False)
        self._set_busy(self.create_button, True)

        def work() -> tuple[Vault, str]:
            vault = Vault.create(path, password)
            message = "Vault created"
            if restore is not None:
                added, _updated, _skipped = vault.import_entries(restore.entries)
                message = f"Vault restored with {added} {'entry' if added == 1 else 'entries'}"
            elif add_demo:
                vault.import_entries(demo_entries())
            return vault, message

        def done(result, error) -> None:
            self._set_busy(self.create_button, False)
            self._update_create_sensitivity()
            if error is not None:
                log.warning("Vault creation failed: %s", type(error).__name__)
                self.create_error.set_label(f"Could not create the vault: {error}")
                self.create_error.set_visible(True)
                return
            self._clear_sensitive()
            vault, message = result
            self.on_finished(vault, message)

        run_async(work, done)

    def _clear_sensitive(self) -> None:
        self.password_row.set_text("")
        self.confirm_row.set_text("")
        self.restore_password_row.set_text("")
        self._restore = None

    # Restore flow -----------------------------------------------------------
    def _choose_backup(self) -> None:
        dialog = Gtk.FileDialog(title="Choose a Lockly Backup", modal=True)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        lockly_filter = Gtk.FileFilter(name="Lockly backups")
        lockly_filter.add_pattern(f"*{backup.BACKUP_SUFFIX}")
        any_filter = Gtk.FileFilter(name="All files")
        any_filter.add_pattern("*")
        filters.append(lockly_filter)
        filters.append(any_filter)
        dialog.set_filters(filters)

        def opened(dlg, result) -> None:
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return
            self._restore_file = file
            self.restore_file_label.set_label(file.get_basename() or "")
            self.restore_error.set_visible(False)
            self.restore_password_row.set_text("")
            self._show("restore")

        dialog.open(self.get_root(), None, opened)

    def _decrypt_backup(self) -> None:
        if self._busy or self._restore_file is None:
            return
        password = self.restore_password_row.get_text()
        if not password:
            return
        path = self._restore_file.get_path()
        self._set_busy(self.restore_button, True)

        def work() -> BackupContents:
            if path is None:
                raise BackupError("The selected file is not available locally")
            with open(path, "rb") as fh:
                data = fh.read(backup.MAX_BACKUP_SIZE + 1)
            return backup.read_backup(data, password)

        def done(result, error) -> None:
            self._set_busy(self.restore_button, False)
            if error is not None:
                message = str(error) if isinstance(error, (BackupError, OSError)) else "The backup could not be read"
                self.restore_error.set_label(message)
                self.restore_error.set_visible(True)
                return
            self._restore = result
            self.restore_password_row.set_text("")
            count = len(result.entries)
            self.password_step_label.set_label(
                f"Backup contains {count} {'entry' if count == 1 else 'entries'} · Step 1 of 2"
            )
            self.demo_row.set_visible(False)
            self._show("password")

        run_async(work, done)
