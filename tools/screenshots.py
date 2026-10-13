"""Render screenshots of Lockly for the README and for visual checks.

Runs the real application against a throw-away vault in a temporary
directory, drives it through its main screens and saves PNGs.

    python tools/screenshots.py docs/screenshots

It can run against your normal desktop (a window appears briefly) or fully
headless: start ``gtk4-broadwayd :5``, connect any browser (for example a
headless Chromium) to http://127.0.0.1:8085 so frames are drawn, and run with
``GDK_BACKEND=broadway BROADWAY_DISPLAY=:5``. The PNGs are rendered
in-process with GSK, not taken from the screen.

Never point this at a real vault.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots").resolve()
TMP = Path(tempfile.mkdtemp(prefix="lockly-shots-"))
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
os.environ["XDG_DATA_HOME"] = str(TMP / "data")
os.environ["LOCKLY_VAULT"] = str(TMP / "data" / "lockly" / "vault.lockly")
os.environ["LOCKLY_ALLOW_DEBUG"] = "1"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from lockly.application import LocklyApplication  # noqa: E402
from lockly.core.demo import demo_entries  # noqa: E402
from lockly.core.search import Category  # noqa: E402
from lockly.core.vault import Vault  # noqa: E402

DEMO_MASTER = "screenshot demo master"  # throw-away vault only


def render(window: Gtk.Window, name: str, done) -> None:
    """Render the window in-process right after the next real frame."""
    clock = window.get_frame_clock()
    handler = {}

    def after_paint(_clock):
        if handler.get("done"):
            return False
        handler["done"] = True
        clock.disconnect(handler["id"])
        widget = window.get_first_child()
        snapshot = Gtk.Snapshot()
        # Panes without their own background show the window's, which is not
        # part of the child's render; paint it first.
        dark = Adw.StyleManager.get_default().get_dark()
        background = Gdk.RGBA()
        background.parse("#1c1d20" if dark else "#fafafb")
        snapshot.append_color(background, Graphene.Rect().init(0, 0, widget.get_width(), widget.get_height()))
        window.snapshot_child(widget, snapshot)
        node = snapshot.to_node()
        renderer = Gsk.CairoRenderer()
        renderer.realize_for_display(window.get_display())
        bounds = Graphene.Rect().init(0, 0, widget.get_width(), widget.get_height())
        texture = renderer.render_texture(node, bounds)
        renderer.unrealize()
        OUT.mkdir(parents=True, exist_ok=True)
        texture.save_to_png(str(OUT / f"{name}.png"))
        print("saved", name, flush=True)
        # Optional browser-side capture by tools/broadway_client.py.
        request = OUT / f"{name}.request"
        if os.environ.get("SHOTS_BROWSER"):
            request.write_text(name)

        def wait() -> bool:
            if request.exists() and os.environ.get("SHOTS_BROWSER"):
                return GLib.SOURCE_CONTINUE
            done()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, wait)

    handler["id"] = clock.connect("after-paint", after_paint)
    # Broadway sometimes skips frames when nothing changed; don't wait forever.
    GLib.timeout_add(1500, lambda: after_paint(clock) if not handler.get("done") else False)
    window.queue_draw()
    window.get_first_child().queue_draw()


def main() -> int:
    app = LocklyApplication()
    steps = []

    win_ref = {}

    def window():
        return win_ref["w"]

    shot = lambda name: ("shot", name)  # noqa: E731

    def fill_password():
        ob = w().onboarding
        ob.password_row.set_text("example passphrase only")
        ob.confirm_row.set_text("example passphrase only")

    def create_vault():
        # Default KDF parameters, so the UI shows a normal vault.
        vault = Vault.create(os.environ["LOCKLY_VAULT"], DEMO_MASTER)
        vault.import_entries(demo_entries())
        w()._on_vault_created(vault, "Vault created")

    def select_row(index):
        def run():
            el = w().vault_view.entry_list
            el.list.select_row(el.list.get_row_at_index(index))
        return run

    def close_dialog():
        dialog = w().get_visible_dialog()
        if dialog is not None:
            dialog.force_close()

    plan = [
        (600, shot("01-welcome")),
        (300, lambda: w().onboarding._start_create()),
        (400, fill_password),
        (300, shot("02-master-password")),
        (300, lambda: w().onboarding._password_continue()),
        (400, shot("03-security")),
        (300, create_vault),
        (2500, select_row(1)),
        (400, shot("04-vault")),
        (300, lambda: w().edit_entry()),
        (600, shot("05-editor")),
        (300, close_dialog),
        (300, lambda: w().show_generator()),
        (600, shot("06-generator")),
        (300, close_dialog),
        (300, lambda: w().vault_view.select_category(Category.CARDS)),
        (400, select_row(0)),
        (400, shot("07-card")),
        (300, lambda: w().show_preferences()),
        (600, shot("08-settings")),
        (300, close_dialog),
        (300, lambda: w().vault_view.select_category(Category.ALL)),
        (500, lambda: w().lock_vault()),
        (700, shot("09-locked")),
        (500, lambda: app.quit()),
    ]
    steps.extend(plan)

    def run_steps(index=0):
        if index >= len(steps):
            return False
        delay, func = steps[index]

        def fire():
            if os.environ.get("SHOTS_DEBUG"):
                print("step", index, getattr(func, "__name__", func), flush=True)
            try:
                if isinstance(func, tuple):
                    render(w(), func[1], lambda: run_steps(index + 1))
                    return False
                func()
            except Exception:
                import traceback

                traceback.print_exc()
                app.quit()
                return False
            run_steps(index + 1)
            return False

        GLib.timeout_add(delay, fire)
        return False

    w = window

    def on_activate(_app):
        # Screenshots must not catch transitions half-way.
        Gtk.Settings.get_default().set_property("gtk-enable-animations", False)

        def start():
            win_ref["w"] = app.window
            run_steps()
            return False

        GLib.idle_add(start)

    app.connect("activate", on_activate)
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
