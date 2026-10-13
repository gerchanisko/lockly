"""End-to-end smoke test of the GTK interface.

Drives the real window through onboarding, editing, copying, locking and
unlocking. It needs GTK 4, libadwaita and ``gtk4-broadwayd`` (shipped with
GTK) so it can run without a visible desktop; otherwise it is skipped.
"""

import os
import shutil
import socket
import subprocess
import time

import pytest

BROADWAY = shutil.which("gtk4-broadwayd")
pytestmark = pytest.mark.skipif(BROADWAY is None, reason="gtk4-broadwayd not available")

UI_MASTER = "smoke test master password"


def _free_display() -> int:
    for display in range(20, 60):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", 8080 + display)) != 0:
                return display
    pytest.skip("no free Broadway display")


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ui")
    display = _free_display()
    server = subprocess.Popen([BROADWAY, f":{display}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    env = {
        "GDK_BACKEND": "broadway",
        "BROADWAY_DISPLAY": f":{display}",
        "XDG_CONFIG_HOME": str(tmp / "config"),
        "XDG_DATA_HOME": str(tmp / "data"),
        "LOCKLY_VAULT": str(tmp / "data" / "lockly" / "vault.lockly"),
        "GSETTINGS_BACKEND": "memory",
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gio, Gtk

        if not Gtk.init_check():
            pytest.skip("GTK could not connect to Broadway")
        from lockly.application import LocklyApplication

        app = LocklyApplication()
        app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        app.activate()
        yield app
        if app.window is not None:
            app.window.close()
    finally:
        server.terminate()
        server.wait(timeout=5)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def spin(condition, timeout=30.0):
    from gi.repository import GLib

    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_full_flow(ui):
    from lockly.core.clipboard import ClearResult
    from lockly.core.models import EntryType
    from lockly.core.search import Category

    win = ui.window
    assert win.stack.get_visible_child_name() == "onboarding"
    assert not win.lookup_action("new-entry").get_enabled()

    # --- onboarding --------------------------------------------------------
    ob = win.onboarding
    ob._start_create()
    ob.password_row.set_text(UI_MASTER)
    ob.confirm_row.set_text(UI_MASTER + "x")
    assert not ob.password_continue.get_sensitive()
    ob.confirm_row.set_text(UI_MASTER)
    assert ob.password_continue.get_sensitive()
    ob._password_continue()
    assert ob.stack.get_visible_child_name() == "security"
    ob._create_vault()  # refuses without acknowledgement
    assert not win.vault.is_unlocked
    ob.understand_row.set_active(True)
    ob.demo_row.set_active(True)
    ob._create_vault()
    assert spin(lambda: win.stack.get_visible_child_name() == "vault")
    assert len(win.vault.entries()) == 6
    assert win.lookup_action("new-entry").get_enabled()

    # --- create an entry through the editor -----------------------------------
    win.new_entry(EntryType.LOGIN)
    editor = win.get_visible_dialog()
    editor.title_row.set_text("Smoke Test Site")
    editor._field_rows["username"].set_text("smoke-user")
    editor._field_rows["password"].set_text("smoke-pw-not-real")
    editor._save()
    assert spin(lambda: win.get_visible_dialog() is None)
    created = [e for e in win.vault.entries() if e.title == "Smoke Test Site"]
    assert len(created) == 1
    assert win.vault_view.entry_list.selected_id == created[0].id
    assert win.vault_view.details.entry.id == created[0].id

    # validation errors keep the editor open
    win.new_entry(EntryType.CARD)
    editor = win.get_visible_dialog()
    editor.title_row.set_text("Bad card")
    editor._field_rows["number"].set_text("12ab")
    editor._save()
    assert win.get_visible_dialog() is editor
    assert editor.error_label.get_visible()
    editor.force_close()
    assert spin(lambda: win.get_visible_dialog() is None)

    # --- search ------------------------------------------------------------------
    entry_list = win.vault_view.entry_list
    entry_list.search_entry.set_text("smoke-user")
    assert spin(lambda: [e.title for e in entry_list.visible_entries()] == ["Smoke Test Site"])
    entry_list.search_entry.set_text("smoke-pw-not-real")  # secrets are not searchable
    assert spin(lambda: entry_list.visible_entries() == [])
    entry_list.search_entry.set_text("")
    entry_list.select_entry(created[0].id)

    # --- context menu ------------------------------------------------------------
    row = entry_list._rows[created[0].id]
    entry_list.show_context_menu_for(row, 20, 20)
    assert spin(lambda: entry_list.context_menu.get_visible(), timeout=5)
    model = entry_list.context_menu.get_menu_model()
    labels = [
        model.get_item_link(i, "section").get_item_attribute_value(j, "label").get_string()
        for i in range(model.get_n_items())
        for j in range(model.get_item_link(i, "section").get_n_items())
    ]
    assert labels == ["Copy Username", "Copy Password", "Edit", "Add to Favorites", "Move to Trash"]
    entry_list.context_menu.popdown()

    # --- clipboard ---------------------------------------------------------------
    win.copy_password()
    assert win.clipboard.holds_secret

    # --- favourites & trash ------------------------------------------------------
    win.toggle_favorite()
    assert win.vault.get(created[0].id).favorite
    win.trash_entry()
    assert win.vault.get(created[0].id).in_trash
    win.vault_view.select_category(Category.TRASH)
    assert [e.id for e in entry_list.visible_entries()] == [created[0].id]
    entry_list.select_entry(created[0].id)
    win.restore_entry()
    assert not win.vault.get(created[0].id).in_trash
    win.vault_view.select_category(Category.ALL)

    # --- manual lock -------------------------------------------------------------
    win.show_generator()
    assert win.get_visible_dialog() is not None
    win.lock_vault("manual")
    assert not win.vault.is_unlocked
    assert win.stack.get_visible_child_name() == "unlock"
    assert win.get_visible_dialog() is None
    assert entry_list.list.get_first_child() is None
    assert win.vault_view.details.entry is None
    assert not win.clipboard.holds_secret
    assert not win.lookup_action("copy-password").get_enabled()

    # --- unlock: wrong, then right ------------------------------------------------
    unlock = win.unlock_view
    unlock.password_row.set_text("definitely wrong password")
    unlock.unlock()
    assert spin(lambda: unlock.error_label.get_visible())
    assert not win.vault.is_unlocked
    unlock.password_row.set_text(UI_MASTER)
    unlock.unlock()
    assert spin(lambda: win.stack.get_visible_child_name() == "vault")
    assert len(win.vault.entries()) == 7
    assert unlock.password_row.get_text() == ""

    # --- auto-lock after inactivity ----------------------------------------------
    win.settings.auto_lock_minutes = 1
    win._last_activity -= 120
    win._check_inactivity()
    assert not win.vault.is_unlocked
    assert win.stack.get_visible_child_name() == "unlock"

    # --- session lock signal ------------------------------------------------------
    unlock.password_row.set_text(UI_MASTER)
    unlock.unlock()
    assert spin(lambda: win.vault.is_unlocked and win.stack.get_visible_child_name() == "vault")
    win._on_session_lock("screen-locked")
    assert not win.vault.is_unlocked

    # clipboard guard reports honestly when nothing is held
    assert win.clipboard.clear() is ClearResult.NOTHING
