"""Reusable dialogs: password forms (re-authentication, change password,
backup passwords) and the keyboard shortcuts overview."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gi.repository import Adw, Gtk

from .common import StrengthIndicator, run_async, wrap_label

log = logging.getLogger(__name__)


class UserFacingError(Exception):
    """An error whose message is safe and useful to show verbatim."""


@dataclass
class PasswordField:
    key: str
    title: str
    strength: bool = False


class PasswordFormDialog(Adw.Dialog):
    """A small dialog that collects one or more passwords, runs a slow
    operation (e.g. Argon2) on a worker thread and reports errors inline."""

    def __init__(
        self,
        *,
        title: str,
        intro: str,
        fields: list[PasswordField],
        action_label: str,
        work: Callable[[dict[str, str]], Any],
        on_success: Callable[[Any], None],
        validate: Callable[[dict[str, str]], str | None] | None = None,
        destructive: bool = False,
        extra: Gtk.Widget | None = None,
        warning: str | None = None,
        is_current: Callable[[], bool] | None = None,
    ):
        super().__init__(title=title, content_width=440)
        self._work = work
        self._on_success = on_success
        self._validate = validate
        self._is_current = is_current or (lambda: True)
        self._busy = False
        self._rows: dict[str, Adw.PasswordEntryRow] = {}
        self._strength: dict[str, StrengthIndicator] = {}

        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self.action_button = Gtk.Button()
        content = Gtk.Box(spacing=6)
        self.spinner = Adw.Spinner(visible=False)
        content.append(self.spinner)
        content.append(Gtk.Label(label=action_label))
        self.action_button.set_child(content)
        self.action_button.add_css_class("destructive-action" if destructive else "suggested-action")
        self.action_button.connect("clicked", lambda *_: self.submit())
        header.pack_end(self.action_button)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=12, margin_bottom=24, margin_start=18, margin_end=18)
        if warning:
            warn_box = Gtk.Box(spacing=10)
            icon = Gtk.Image(icon_name="dialog-warning-symbolic", valign=Gtk.Align.START)
            icon.add_css_class("warning")
            warn_box.append(icon)
            warn_box.append(wrap_label(warning))
            body.append(warn_box)
        if intro:
            body.append(wrap_label(intro, "dim-label"))

        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        group.add_css_class("boxed-list")
        for index, spec in enumerate(fields):
            row = Adw.PasswordEntryRow(title=spec.title)
            row.connect("changed", self._on_changed)
            is_last = index == len(fields) - 1
            row.connect("entry-activated", lambda *_a, last=is_last, i=index: self.submit() if last else self._focus(i + 1))
            self._rows[spec.key] = row
            group.append(row)
        body.append(group)
        for spec in fields:
            if spec.strength:
                indicator = StrengthIndicator()
                self._strength[spec.key] = indicator
                body.append(indicator)
        if extra is not None:
            body.append(extra)

        self.error_label = wrap_label("", "error-text")
        self.error_label.set_visible(False)
        body.append(self.error_label)

        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.set_child(view)
        self.set_default_widget(self.action_button)
        self._fields = fields
        self.connect("closed", lambda *_: self._wipe())
        self._on_changed()
        if fields:
            self.set_focus(self._rows[fields[0].key])

    def _focus(self, index: int) -> None:
        self._rows[self._fields[index].key].grab_focus()

    def values(self) -> dict[str, str]:
        return {key: row.get_text() for key, row in self._rows.items()}

    def _wipe(self) -> None:
        for row in self._rows.values():
            row.set_text("")

    def _on_changed(self, *_args) -> None:
        values = self.values()
        for key, indicator in self._strength.items():
            indicator.update(values[key])
        self.error_label.set_visible(False)
        for row in self._rows.values():
            row.remove_css_class("error")
        self.action_button.set_sensitive(not self._busy and all(values.values()))

    def show_error(self, message: str, field: str | None = None) -> None:
        self.error_label.set_label(message)
        self.error_label.set_visible(True)
        if field and field in self._rows:
            self._rows[field].add_css_class("error")
            self._rows[field].grab_focus()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.spinner.set_visible(busy)
        for row in self._rows.values():
            row.set_sensitive(not busy)
        self.set_can_close(not busy)
        self._on_changed()

    def submit(self) -> None:
        if self._busy:
            return
        values = self.values()
        if not all(values.values()):
            return
        if self._validate is not None:
            problem = self._validate(values)
            if problem:
                field = None
                if "|" in problem:
                    field, problem = problem.split("|", 1)
                self.show_error(problem, field)
                return
        self._set_busy(True)

        def done(result, error) -> None:
            if self.get_root() is None:
                return  # already force-closed, e.g. because the vault locked
            self._set_busy(False)
            if not self._is_current():
                self.force_close()
                return
            if error is None:
                self.force_close()
                self._on_success(result)
                return
            if isinstance(error, UserFacingError):
                message = str(error)
                field = getattr(error, "field", None)
            else:
                log.warning("Operation failed: %s", type(error).__name__)
                message = str(error) or "The operation failed."
                field = None
            self.show_error(message, field)

        run_async(lambda: self._work(values), done)


def field_error(field: str, message: str) -> UserFacingError:
    error = UserFacingError(message)
    error.field = field  # type: ignore[attr-defined]
    return error


SHORTCUTS = [
    ("Vault", [
        ("<Control>n", "New entry"),
        ("<Control>f", "Search"),
        ("<Control>e", "Edit selected entry"),
        ("<Control>b", "Copy username"),
        ("<Control><Shift>c", "Copy password"),
        ("Delete", "Move selected entry to Trash"),
        ("<Control>g", "Password generator"),
        ("Escape", "Clear search / selection, close dialogs"),
    ]),
    ("Application", [
        ("<Control>l", "Lock vault"),
        ("<Control>comma", "Settings"),
        ("<Control>question", "Keyboard shortcuts"),
        ("<Control>q", "Quit"),
    ]),
]


class ShortcutsDialog(Adw.Dialog):
    def __init__(self):
        super().__init__(title="Keyboard Shortcuts", content_width=420, content_height=560)
        page = Adw.PreferencesPage()
        for section, items in SHORTCUTS:
            group = Adw.PreferencesGroup(title=section)
            for accel, description in items:
                row = Adw.ActionRow(title=description)
                row.add_suffix(Gtk.ShortcutLabel(accelerator=accel, valign=Gtk.Align.CENTER))
                group.add(row)
            page.add(group)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)
