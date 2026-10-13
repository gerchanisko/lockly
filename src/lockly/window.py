"""Main application window: owns the vault, locking and all user actions."""

from __future__ import annotations

import datetime as _dt
import logging
import time
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import __version__
from .core import backup, crypto
from .core.backup import BackupContents, BackupError
from .core.clipboard import ClearResult, ClipboardGuard
from .core.database import DatabaseError, atomic_write
from .core.demo import demo_entries
from .core.models import Entry, EntryType, ValidationError
from .core.search import Category, SortOrder
from .core.settings import SettingsStore
from .core.vault import MIN_PASSWORD_LENGTH, Vault, VaultError, WrongPasswordError
from .platform.gtk_clipboard import GtkClipboardBackend, glib_scheduler
from .platform.session import SessionMonitor
from .ui.common import confirm, run_async, safe_web_url
from .ui.dialogs import PasswordField, PasswordFormDialog, ShortcutsDialog, field_error
from .ui.entry_editor import EntryEditor
from .ui.onboarding import OnboardingView
from .ui.password_generator import GeneratorDialog
from .ui.settings import PreferencesDialog, VaultActions
from .ui.unlock import UnlockView
from .ui.vault_view import VaultView

log = logging.getLogger(__name__)

ACTIVITY_CHECK_SECONDS = 5

# Actions that require an unlocked vault.
VAULT_ACTIONS = [
    "new-entry", "new-entry-of-type", "search", "lock", "preferences", "sort-order", "empty-trash",
    "change-password", "show-details",
]
# Actions that additionally require a selected entry.
ENTRY_ACTIONS = [
    "edit-entry", "trash-entry", "restore-entry", "delete-entry", "toggle-favorite",
    "copy-username", "copy-password", "open-website",
]


class LocklyWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, vault_path: Path, store: SettingsStore):
        super().__init__(application=app, title="Lockly")
        self.set_default_size(1180, 760)
        self.set_size_request(360, 420)
        self.store = store
        self.settings = store.settings
        self.vault = Vault(vault_path)
        self.vault.connect(self._on_vault_event_threadsafe)
        self._last_activity = time.monotonic()
        self._activity_source = 0
        self._cancellable = Gio.Cancellable()

        self.clipboard = ClipboardGuard(GtkClipboardBackend(self.get_display()), glib_scheduler, self._on_clipboard_cleared)
        self.session_monitor = SessionMonitor(self._on_session_lock)

        # Views
        self.vault_view = VaultView(self._entries_or_empty, self.copy_text, self.show_preferences)
        self.vault_view.entry_list.connect("entry-selected", self._on_entry_selected)
        self.vault_view.add_breakpoints(self)
        self.unlock_view = UnlockView(self.vault, self._on_unlocked)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        self.stack.add_named(self.unlock_view, "unlock")
        self.stack.add_named(self.vault_view, "vault")
        self.toasts = Adw.ToastOverlay(child=self.stack)
        self.set_content(self.toasts)

        self._install_actions()
        self._install_activity_tracking()
        self.connect("close-request", self._on_close_request)

        if Vault.exists(vault_path):
            self._show_unlock()
        else:
            self.onboarding = OnboardingView(vault_path, self._on_vault_created)
            self.stack.add_named(self.onboarding, "onboarding")
            self.stack.set_visible_child_name("onboarding")
            self._update_action_state()

    # ----------------------------------------------------------------- state
    def _entries_or_empty(self) -> list[Entry]:
        return self.vault.entries() if self.vault.is_unlocked else []

    def _selected_entry(self) -> Entry | None:
        entry_id = self.vault_view.entry_list.selected_id
        if not entry_id or not self.vault.is_unlocked:
            return None
        try:
            return self.vault.get(entry_id)
        except VaultError:
            return None

    def _update_action_state(self) -> None:
        unlocked = self.vault.is_unlocked
        entry = self._selected_entry() if unlocked else None
        for name in VAULT_ACTIONS:
            self.lookup_action(name).set_enabled(unlocked)
        in_trash = bool(entry and entry.in_trash)
        live = bool(entry and not entry.in_trash)
        self.lookup_action("edit-entry").set_enabled(live)
        self.lookup_action("trash-entry").set_enabled(live)
        self.lookup_action("toggle-favorite").set_enabled(live)
        self.lookup_action("restore-entry").set_enabled(in_trash)
        self.lookup_action("delete-entry").set_enabled(in_trash)
        username = entry and (entry.get("username") or entry.get("email"))
        secret = entry and (entry.get("password") or entry.get("number"))
        self.lookup_action("copy-username").set_enabled(bool(username))
        self.lookup_action("copy-password").set_enabled(bool(secret))
        self.lookup_action("open-website").set_enabled(bool(entry and safe_web_url(entry.get("url"))))
        has_trash = unlocked and any(e.in_trash for e in self.vault.entries())
        self.lookup_action("empty-trash").set_enabled(has_trash)

    # --------------------------------------------------------------- actions
    def _install_actions(self) -> None:
        def add(name: str, callback, param: str | None = None) -> None:
            action = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            action.connect("activate", callback)
            self.add_action(action)

        add("new-entry", lambda *_: self.new_entry())
        add("new-entry-of-type", lambda _a, p: self.new_entry(EntryType(p.get_string())), "s")
        add("edit-entry", lambda *_: self.edit_entry())
        add("trash-entry", lambda *_: self.trash_entry())
        add("restore-entry", lambda *_: self.restore_entry())
        add("delete-entry", lambda *_: self.delete_entry())
        add("empty-trash", lambda *_: self.empty_trash())
        add("toggle-favorite", lambda *_: self.toggle_favorite())
        add("copy-username", lambda *_: self.copy_username())
        add("copy-password", lambda *_: self.copy_password())
        add("open-website", lambda *_: self.open_website())
        add("search", lambda *_: self._focus_search())
        add("lock", lambda *_: self.lock_vault("manual"))
        add("generator", lambda *_: self.show_generator())
        add("preferences", lambda *_: self.show_preferences())
        add("show-shortcuts", lambda *_: ShortcutsDialog().present(self))
        add("show-details", lambda *_: self.vault_view.show_details())
        add("change-password", lambda *_: self.change_password())

        sort = Gio.SimpleAction.new_stateful(
            "sort-order", GLib.VariantType.new("s"), GLib.Variant.new_string(self.settings.sort_order)
        )
        sort.connect("activate", self._on_sort)
        self.add_action(sort)
        try:
            self.vault_view.entry_list.sort_order = SortOrder(self.settings.sort_order)
        except ValueError:
            pass

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

    def _on_key_pressed(self, _ctrl, keyval, _code, state) -> bool:
        if self.stack.get_visible_child_name() != "vault" or self.get_visible_dialog() is not None:
            return False
        entry_list = self.vault_view.entry_list
        if keyval == Gdk.KEY_Escape:
            if entry_list.search_entry.get_text():
                entry_list.search_entry.set_text("")
                return True
            if entry_list.selected_id:
                if self.vault_view.inner.get_collapsed() and self.vault_view.inner.get_show_content():
                    self.vault_view.show_list()
                entry_list.unselect()
                return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            focus = self.get_focus()
            if focus is not None and focus.is_ancestor(entry_list.list):
                entry = self._selected_entry()
                if entry is not None:
                    self.delete_entry() if entry.in_trash else self.trash_entry()
                    return True
        return False

    def _on_sort(self, action: Gio.SimpleAction, param: GLib.Variant) -> None:
        action.set_state(param)
        order = SortOrder(param.get_string())
        self.settings.sort_order = order.value
        self.store.save()
        self.vault_view.entry_list.set_sort_order(order)

    def _focus_search(self) -> None:
        self.vault_view.show_list()
        self.vault_view.entry_list.focus_search()

    # -------------------------------------------------------- locking / auth
    def _show_unlock(self) -> None:
        self.unlock_view.reset()
        self.stack.set_visible_child_name("unlock")
        self.set_title("Lockly — Locked")
        self._update_action_state()
        self.unlock_view.focus()

    def _on_vault_created(self, vault: Vault, message: str) -> None:
        # The onboarding view created and unlocked a fresh Vault object.
        self.vault.disconnect(self._on_vault_event_threadsafe)
        self.vault = vault
        self.vault.connect(self._on_vault_event_threadsafe)
        self.unlock_view.vault = vault
        self._on_unlocked()
        self.stack.remove(self.onboarding)
        del self.onboarding
        self.toast(message)

    def _on_unlocked(self) -> None:
        self._last_activity = time.monotonic()
        self.set_title("Lockly")
        self.vault_view.select_category(Category.ALL)
        self.vault_view.entry_list.set_category(Category.ALL)
        self._refresh_all()
        self.stack.set_visible_child_name("vault")
        self._start_activity_timer()
        self._update_action_state()
        self.vault_view.entry_list.search_entry.set_key_capture_widget(self.vault_view)
        GLib.idle_add(lambda: self.vault_view.entry_list.search_entry.grab_focus() and False)
        if self.vault.needs_kdf_upgrade():
            self.toast("This vault uses weaker key derivation than recommended. See Settings → Security.", timeout=6)

    def lock_vault(self, reason: str = "manual") -> None:
        if not self.vault.is_unlocked:
            if self.stack.get_visible_child_name() == "unlock":
                self.vault.lock()  # cancels an unlock in progress
            return
        # Close every dialog (editor, generator, settings, confirmations).
        while (dialog := self.get_visible_dialog()) is not None:
            dialog.force_close()
        self._cancellable.cancel()
        self._cancellable = Gio.Cancellable()
        clip = self.clipboard.clear()
        self.vault_view.entry_list.search_entry.set_key_capture_widget(None)
        self.vault_view.entry_list.clear()
        self.vault_view.details.clear()
        self.vault_view.sidebar.set_counts(None)
        self.vault_view.show_list()
        self.vault.lock()
        self._stop_activity_timer()
        self._show_unlock()
        messages = {
            "manual": "Vault locked",
            "inactivity": "Locked after inactivity",
            "screen-locked": "Locked because the screen was locked",
            "session-locked": "Locked because the session was locked",
            "suspend": "Locked before suspend",
        }
        message = messages.get(reason, "Vault locked")
        if clip is ClearResult.CLEARED:
            message += " · clipboard cleared"
        elif clip is ClearResult.FAILED:
            message += " · the clipboard could not be cleared"
        self.toast(message)
        log.info("Vault locked (%s)", reason)

    def _on_session_lock(self, reason: str) -> None:
        if self.settings.lock_on_screen_lock and self.vault.is_unlocked:
            self.lock_vault(reason)

    def _install_activity_tracking(self) -> None:
        def touch(*_args) -> None:
            self._last_activity = time.monotonic()

        key = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        key.connect("key-pressed", lambda *a: touch() or False)
        click = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE, button=0)
        click.connect("pressed", touch)
        scroll = Gtk.EventControllerScroll(
            propagation_phase=Gtk.PropagationPhase.CAPTURE, flags=Gtk.EventControllerScrollFlags.BOTH_AXES
        )
        scroll.connect("scroll", lambda *a: touch() or False)
        motion = Gtk.EventControllerMotion(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", touch)
        for controller in (key, click, scroll, motion):
            self.add_controller(controller)

    def _start_activity_timer(self) -> None:
        self._stop_activity_timer()
        self._activity_source = GLib.timeout_add_seconds(ACTIVITY_CHECK_SECONDS, self._check_inactivity)

    def _stop_activity_timer(self) -> None:
        if self._activity_source:
            GLib.source_remove(self._activity_source)
            self._activity_source = 0

    def _check_inactivity(self) -> bool:
        minutes = self.settings.auto_lock_minutes
        if not self.vault.is_unlocked:
            self._activity_source = 0
            return GLib.SOURCE_REMOVE
        if minutes and time.monotonic() - self._last_activity >= minutes * 60:
            self._activity_source = 0
            self.lock_vault("inactivity")
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _on_close_request(self, *_args) -> bool:
        while (dialog := self.get_visible_dialog()) is not None:
            dialog.force_close()
        self.clipboard.clear()
        self.vault.lock()
        self._stop_activity_timer()
        self.session_monitor.close()
        return False

    # ------------------------------------------------------------ vault data
    def _on_vault_event_threadsafe(self, event: str, entry_id: str | None) -> None:
        def handle() -> bool:
            self._on_vault_event(event, entry_id)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(handle)

    def _on_vault_event(self, event: str, entry_id: str | None) -> None:
        if event in ("locked", "unlocked", "password-changed") or not self.vault.is_unlocked:
            return
        self.vault_view.entry_list.invalidate(entry_id)
        self._refresh_all()

    def _refresh_all(self, select: str | None = None) -> None:
        if not self.vault.is_unlocked:
            return
        from .core.search import category_counts

        self.vault_view.sidebar.set_counts(category_counts(self.vault.entries()))
        self.vault_view.entry_list.refresh(keep_selection=True, select=select)
        self._show_selected()

    def _on_entry_selected(self, _list, entry_id: str) -> None:
        self._show_selected()

    def _show_selected(self) -> None:
        entry = self._selected_entry()
        current = self.vault_view.details.entry
        if entry is None or current is None or current.id != entry.id or current.modified != entry.modified \
                or current.favorite != entry.favorite or current.deleted_at != entry.deleted_at:
            self.vault_view.details.show_entry(entry)
        if entry is None and self.vault_view.inner.get_collapsed():
            self.vault_view.show_list()
        self._update_action_state()

    # --------------------------------------------------------------- entries
    def new_entry(self, entry_type: EntryType | None = None) -> None:
        if not self.vault.is_unlocked:
            return
        if entry_type is None:
            entry_type = self.vault_view.category.entry_type or EntryType.LOGIN
        self._open_editor(None, entry_type)

    def edit_entry(self) -> None:
        entry = self._selected_entry()
        if entry is not None and not entry.in_trash:
            self._open_editor(entry, entry.type)

    def _open_editor(self, entry: Entry | None, entry_type: EntryType) -> None:
        generation = self.vault.generation

        def save(edited: Entry) -> str | None:
            if generation != self.vault.generation or not self.vault.is_unlocked:
                return "The vault was locked. Changes were not saved."
            try:
                stored = self.vault.update(edited) if entry is not None else self.vault.add(edited)
            except ValidationError as exc:
                return "\n".join(exc.errors.values())
            except (VaultError, DatabaseError) as exc:
                return f"Could not save: {exc}"
            if entry is None:
                self._ensure_visible(stored)
            self._refresh_all(select=stored.id)
            self.toast("Entry saved" if entry is not None else f"{stored.type.label} added")
            return None

        editor = EntryEditor(entry, entry_type, save, lambda use: self.show_generator(on_use=use))
        editor.present(self)

    def _ensure_visible(self, entry: Entry) -> None:
        from .core.search import in_category

        category = self.vault_view.category
        if not in_category(entry, category):
            self.vault_view.select_category(Category.ALL)
        entry_list = self.vault_view.entry_list
        if entry_list.search_entry.get_text():
            entry_list.search_entry.set_text("")

    def trash_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None or entry.in_trash:
            return
        self.vault.trash(entry.id)
        toast = Adw.Toast(title=GLib.markup_escape_text(f"“{entry.title}” moved to Trash"), button_label="Undo", timeout=5)

        def undo(*_args) -> None:
            if self.vault.is_unlocked:
                try:
                    self.vault.restore(entry.id)
                    self._refresh_all(select=entry.id)
                except VaultError:
                    pass

        toast.connect("button-clicked", undo)
        self.toasts.add_toast(toast)

    def restore_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None or not entry.in_trash:
            return
        self.vault.restore(entry.id)
        self.toast(f"“{entry.title}” restored")

    def delete_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None or not entry.in_trash:
            return
        generation = self.vault.generation

        def do_delete() -> None:
            if generation == self.vault.generation and self.vault.is_unlocked:
                self.vault.delete_permanently(entry.id)
                self.toast("Entry deleted permanently")

        confirm(self, "Delete Permanently?", f"“{entry.title}” will be removed from the vault. This cannot be undone.",
                "Delete", do_delete)

    def empty_trash(self) -> None:
        if not self.vault.is_unlocked:
            return
        count = sum(1 for e in self.vault.entries() if e.in_trash)
        if not count:
            return
        generation = self.vault.generation

        def do_empty() -> None:
            if generation == self.vault.generation and self.vault.is_unlocked:
                removed = self.vault.empty_trash()
                self.toast(f"Deleted {removed} {'entry' if removed == 1 else 'entries'} permanently")

        confirm(self, "Empty Trash?", f"{count} {'entry' if count == 1 else 'entries'} will be deleted permanently. "
                "This cannot be undone.", "Empty Trash", do_empty)

    def toggle_favorite(self) -> None:
        entry = self._selected_entry()
        if entry is not None and not entry.in_trash:
            self.vault.set_favorite(entry.id, not entry.favorite)

    # ------------------------------------------------------------- clipboard
    def copy_text(self, text: str, label: str, sensitive: bool) -> None:
        if not text:
            return
        seconds = self.settings.clipboard_clear_seconds if sensitive else 0
        self.clipboard.copy(text, sensitive=sensitive, clear_after=seconds or None)
        message = f"{label} copied"
        if sensitive and seconds:
            message += f" · clears in {seconds} s"
        self.toast(message, timeout=2)

    def copy_username(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if entry.get("username"):
            self.copy_text(entry.get("username"), "Username", False)
        elif entry.get("email"):
            self.copy_text(entry.get("email"), "Email", False)

    def copy_password(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if entry.get("password"):
            self.copy_text(entry.get("password"), "Password", True)
        elif entry.get("number"):
            self.copy_text(entry.get("number").replace(" ", ""), "Card number", True)

    def _on_clipboard_cleared(self, result: ClearResult) -> None:
        if result is ClearResult.CLEARED:
            self.toast("Clipboard cleared", timeout=2)
        elif result is ClearResult.FAILED:
            self.toast("Could not clear the clipboard. It may still contain the copied secret.", timeout=6)
        elif result is ClearResult.REPLACED:
            log.debug("Clipboard content was replaced by another application; not cleared")

    def open_website(self) -> None:
        entry = self._selected_entry()
        url = safe_web_url(entry.get("url")) if entry else None
        if not url:
            return
        launcher = Gtk.UriLauncher(uri=url)

        def done(source, result) -> None:
            try:
                source.launch_finish(result)
            except GLib.Error:
                self.toast("Could not open the website")

        launcher.launch(self, None, done)

    # ------------------------------------------------------------- dialogs
    def toast(self, message: str, timeout: int = 3) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(message), timeout=timeout))

    def show_generator(self, on_use=None) -> None:
        def copy(value: str) -> None:
            self.copy_text(value, "Password", True)

        GeneratorDialog(self.settings, copy, on_use).present(self)

    def show_preferences(self) -> None:
        if not self.vault.is_unlocked:
            return
        actions = VaultActions(
            change_password=self.change_password,
            export_backup=self.export_backup,
            import_backup=self.import_backup,
            export_csv=self.export_csv,
            add_samples=self.add_samples,
            open_folder=self.open_vault_folder,
        )
        app = self.get_application()
        PreferencesDialog(self.store, self.vault, actions, app.apply_color_scheme, lambda: None).present(self)

    def _guarded(self, generation: int):
        return lambda: generation == self.vault.generation and self.vault.is_unlocked

    def change_password(self) -> None:
        if not self.vault.is_unlocked:
            return
        vault = self.vault
        profiles = list(crypto.KDF_PROFILES.items())
        current = vault.kdf_params
        combo = Adw.ComboRow(
            title="Key Derivation Strength",
            subtitle="Stronger settings take longer to unlock",
            model=Gtk.StringList.new([
                f"Standard — Argon2id, {p.memory_mib} MiB" if name == "moderate" else f"High — Argon2id, {p.memory_mib} MiB"
                for name, p in profiles
            ]),
        )
        combo.set_selected(next((i for i, (_n, p) in enumerate(profiles) if p == current), 0))
        extra = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        extra.add_css_class("boxed-list")
        extra.append(combo)

        def validate(values: dict[str, str]) -> str | None:
            if len(values["new"]) < MIN_PASSWORD_LENGTH:
                return f"new|The new password must be at least {MIN_PASSWORD_LENGTH} characters long"
            if values["new"] != values["confirm"]:
                return "confirm|The new passwords do not match"
            return None

        def work(values: dict[str, str]) -> None:
            kdf = profiles[combo.get_selected()][1]
            try:
                vault.change_password(values["current"], values["new"], kdf)
            except WrongPasswordError:
                raise field_error("current", "The current master password is incorrect") from None

        dialog = PasswordFormDialog(
            title="Change Master Password",
            intro="Your entries stay encrypted with the same vault key; only the way it is protected changes. "
            "You can keep the same password and just raise the key derivation strength.",
            fields=[PasswordField("current", "Current master password"), PasswordField("new", "New master password", strength=True),
                    PasswordField("confirm", "Confirm new master password")],
            action_label="Change",
            validate=validate,
            work=work,
            on_success=lambda _r: self.toast("Master password updated"),
            extra=extra,
            is_current=self._guarded(vault.generation),
        )
        dialog.present(self)

    def export_backup(self) -> None:
        if not self.vault.is_unlocked:
            return
        vault = self.vault
        entries = [e.copy() for e in vault.entries()]

        def validate(values: dict[str, str]) -> str | None:
            if len(values["backup"]) < 8:
                return "backup|The backup password must be at least 8 characters long"
            if values["backup"] != values["confirm"]:
                return "confirm|The backup passwords do not match"
            return None

        def work(values: dict[str, str]) -> bytes:
            if not vault.verify_password(values["master"]):
                raise field_error("master", "The master password is incorrect")
            return backup.create_backup(entries, values["backup"], __version__)

        dialog = PasswordFormDialog(
            title="Export Encrypted Backup",
            intro=f"The backup contains all {len(entries)} entries, including the Trash. Choose a password to "
            "protect it — you can reuse your master password. Without this password the backup cannot be restored.",
            fields=[PasswordField("master", "Your master password"), PasswordField("backup", "Backup password", strength=True),
                    PasswordField("confirm", "Confirm backup password")],
            action_label="Continue",
            validate=validate,
            work=work,
            on_success=lambda data: self._save_file(
                data, f"lockly-backup-{_dt.date.today():%Y-%m-%d}{backup.BACKUP_SUFFIX}", "Save Encrypted Backup",
                "Backup saved", "Lockly backups", f"*{backup.BACKUP_SUFFIX}",
            ),
            is_current=self._guarded(vault.generation),
        )
        dialog.present(self)

    def _save_file(self, data: bytes, name: str, title: str, success: str, filter_name: str, pattern: str) -> None:
        generation = self.vault.generation
        dialog = Gtk.FileDialog(title=title, initial_name=name, modal=True)
        file_filter = Gtk.FileFilter(name=filter_name)
        file_filter.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)

        def chosen(dlg, result) -> None:
            try:
                file = dlg.save_finish(result)
            except GLib.Error:
                return  # cancelled (or the vault was locked)
            path = file.get_path() if file else None
            if path is None or generation != self.vault.generation:
                return
            if Path(path).resolve() == self.vault.path.resolve():
                self.toast("Refusing to overwrite the vault file")
                return

            def done(_r, error) -> None:
                if error is not None:
                    self.toast(f"Could not save the file: {error.strerror if isinstance(error, OSError) else error}")
                else:
                    self.toast(f"{success}: {Path(path).name}", timeout=4)

            # The file chooser has already asked about replacing an existing file.
            run_async(lambda: atomic_write(Path(path), data, overwrite=True), done)

        dialog.save(self, self._cancellable, chosen)

    def import_backup(self) -> None:
        if not self.vault.is_unlocked:
            return
        vault = self.vault
        generation = vault.generation
        dialog = Gtk.FileDialog(title="Import Lockly Backup", modal=True)
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
            path = file.get_path()
            if path is None or generation != vault.generation:
                return
            try:
                with open(path, "rb") as fh:
                    data = fh.read(backup.MAX_BACKUP_SIZE + 1)
            except OSError as exc:
                self.toast(f"Could not read the file: {exc.strerror}")
                return
            if not backup.is_backup_file(data):
                confirm(self, "Not a Lockly Backup", "The selected file is not a Lockly backup, or it was made by "
                        "a newer version of Lockly.", "OK", lambda: None, destructive=False)
                return

            def work(values: dict[str, str]) -> BackupContents:
                try:
                    return backup.read_backup(data, values["backup"])
                except BackupError as exc:
                    raise field_error("backup", str(exc)) from None

            PasswordFormDialog(
                title="Import Backup",
                intro=f"Enter the password for “{file.get_basename()}”. Entries are checked before anything is imported.",
                fields=[PasswordField("backup", "Backup password")],
                action_label="Open",
                work=work,
                on_success=lambda contents: self._confirm_import(contents, generation),
                is_current=self._guarded(generation),
            ).present(self)

        dialog.open(self, self._cancellable, opened)

    def _confirm_import(self, contents: BackupContents, generation: int) -> None:
        existing = {e.id: e for e in self.vault.entries()}
        new = sum(1 for e in contents.entries if e.id not in existing)
        newer = sum(1 for e in contents.entries if e.id in existing and e.modified > existing[e.id].modified)
        same = len(contents.entries) - new - newer
        body = (f"{new} new {'entry' if new == 1 else 'entries'} will be added"
                f"{f', {newer} will replace older versions' if newer else ''}"
                f"{f' and {same} are already up to date' if same else ''}. Nothing is deleted.")

        def do_import() -> None:
            if generation != self.vault.generation or not self.vault.is_unlocked:
                return
            try:
                added, updated, _skipped = self.vault.import_entries(contents.entries)
            except (VaultError, ValidationError, DatabaseError) as exc:
                self.toast(f"Import failed: {exc}")
                return
            self.toast(f"Imported {added} new and {updated} updated {'entry' if added + updated == 1 else 'entries'}")

        if new == 0 and newer == 0:
            self.toast("Everything in this backup is already in your vault")
            return
        confirm(self, "Import Backup?", body, "Import", do_import, destructive=False)

    def export_csv(self) -> None:
        if not self.vault.is_unlocked:
            return
        vault = self.vault
        generation = vault.generation

        def reauth() -> None:
            def work(values: dict[str, str]) -> bytes:
                if not vault.verify_password(values["master"]):
                    raise field_error("master", "The master password is incorrect")
                return backup.export_csv(vault.entries())

            PasswordFormDialog(
                title="Export Unencrypted CSV",
                intro="Confirm with your master password.",
                warning="The CSV file will contain every password, card number and note in plain text. "
                "Anyone or any program that can read the file can read your secrets. Store it only "
                "temporarily and delete it securely when you are done.",
                fields=[PasswordField("master", "Master password")],
                action_label="Export",
                destructive=True,
                work=work,
                on_success=lambda data: self._save_file(
                    data, "lockly-export-UNENCRYPTED.csv", "Save Unencrypted Export",
                    "Unencrypted export saved — delete it when done", "CSV files", "*.csv",
                ),
                is_current=self._guarded(generation),
            ).present(self)

        confirm(
            self,
            "Export Without Encryption?",
            "A CSV export is not protected in any way. Use an encrypted backup unless you need to move your "
            "data to another program. Trash items are not included.",
            "Continue",
            reauth,
        )

    def add_samples(self) -> None:
        generation = self.vault.generation

        def add() -> None:
            if generation == self.vault.generation and self.vault.is_unlocked:
                added, _u, _s = self.vault.import_entries(demo_entries())
                self.toast(f"Added {added} sample entries")

        confirm(self, "Add Sample Entries?", "Six fictional entries tagged “sample” will be added to your vault.",
                "Add", add, destructive=False)

    def open_vault_folder(self) -> None:
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(str(self.vault.path)))
        launcher.open_containing_folder(self, None, lambda src, res: _ignore_errors(src.open_containing_folder_finish, res))


def _ignore_errors(func, result) -> None:
    try:
        func(result)
    except GLib.Error:
        pass
