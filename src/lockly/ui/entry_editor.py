"""Create / edit dialog for vault entries."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from ..core.models import (
    FIELD_SPECS,
    SUPPORTS_CUSTOM_FIELDS,
    CustomField,
    Entry,
    EntryType,
    FieldSpec,
    validate_entry_fields,
)
from .common import StrengthIndicator, confirm

INPUT_PURPOSE = {
    "url": Gtk.InputPurpose.URL,
    "email": Gtk.InputPurpose.EMAIL,
    "phone": Gtk.InputPurpose.PHONE,
    "card-number": Gtk.InputPurpose.DIGITS,
    "expiry": Gtk.InputPurpose.DIGITS,
}


class CustomFieldEditor(Gtk.ListBox):
    def __init__(self, field: CustomField, on_remove: Callable[["CustomFieldEditor"], None], on_change: Callable[[], None]):
        super().__init__(selection_mode=Gtk.SelectionMode.NONE)
        self.add_css_class("boxed-list")
        self.name_row = Adw.EntryRow(title="Field name", text=field.name)
        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove Field")
        remove.add_css_class("flat")
        remove.update_property([Gtk.AccessibleProperty.LABEL], ["Remove custom field"])
        remove.connect("clicked", lambda *_: on_remove(self))
        self.name_row.add_suffix(remove)
        self.hidden_row = Adw.SwitchRow(title="Hide value", subtitle="Treat as a secret", active=field.hidden)
        self.value_row = self._make_value_row(field.hidden, field.value)
        self.hidden_row.connect("notify::active", self._on_hidden_changed)
        for row in (self.name_row, self.value_row):
            row.connect("changed", lambda *_: on_change())
        self._on_change = on_change
        self.append(self.name_row)
        self.append(self.value_row)
        self.append(self.hidden_row)

    @staticmethod
    def _make_value_row(hidden: bool, value: str) -> Adw.EntryRow:
        row = Adw.PasswordEntryRow(title="Value") if hidden else Adw.EntryRow(title="Value")
        row.set_text(value)
        return row

    def _on_hidden_changed(self, *_args) -> None:
        value = self.value_row.get_text()
        new_row = self._make_value_row(self.hidden_row.get_active(), value)
        new_row.connect("changed", lambda *_: self._on_change())
        self.remove(self.value_row)
        self.insert(new_row, 1)
        self.value_row = new_row
        self._on_change()

    def get_field(self) -> CustomField:
        return CustomField(self.name_row.get_text().strip(), self.value_row.get_text(), self.hidden_row.get_active())


class EntryEditor(Adw.Dialog):
    def __init__(
        self,
        entry: Entry | None,
        default_type: EntryType,
        on_save: Callable[[Entry], str | None],
        open_generator: Callable[[Callable[[str], None]], None],
    ):
        super().__init__(content_width=540, content_height=680)
        self.original = entry
        self.on_save = on_save
        self.open_generator = open_generator
        self.entry_type = entry.type if entry else default_type
        self._field_rows: dict[str, Adw.EntryRow] = {}
        self._text_views: dict[str, Gtk.TextView] = {}
        self._custom_editors: list[CustomFieldEditor] = []
        self._dirty = False
        self._initial_snapshot: dict | None = None

        self.cancel_button = Gtk.Button(label="Cancel")
        self.cancel_button.connect("clicked", lambda *_: self.close())
        self.save_button = Gtk.Button(label="Save")
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda *_: self._save())
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(self.cancel_button)
        header.pack_end(self.save_button)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12, margin_bottom=24, margin_start=12, margin_end=12)
        clamp = Adw.Clamp(maximum_size=560, child=self.body)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=clamp)
        self.toast_overlay = Adw.ToastOverlay(child=scroller)
        view = Adw.ToolbarView(content=self.toast_overlay)
        view.add_top_bar(header)
        self.set_child(view)

        self._build(entry)
        self.set_can_close(False)
        self.connect("close-attempt", self._on_close_attempt)
        self.set_default_widget(self.save_button)
        self.set_focus(self.title_row)

    # Building ---------------------------------------------------------------
    def _clear_body(self) -> None:
        child = self.body.get_first_child()
        while child is not None:
            self.body.remove(child)
            child = self.body.get_first_child()
        self._field_rows.clear()
        self._text_views.clear()
        self._custom_editors.clear()

    def _build(self, entry: Entry | None, keep_title: str | None = None) -> None:
        self._clear_body()
        self.set_title(f"{'Edit' if self.original else 'New'} {self.entry_type.label}")

        main = Adw.PreferencesGroup()
        if self.original is None:
            self.type_row = Adw.ComboRow(title="Type", model=Gtk.StringList.new([t.label for t in EntryType]))
            self.type_row.set_selected(list(EntryType).index(self.entry_type))
            self.type_row.connect("notify::selected", self._on_type_changed)
            main.add(self.type_row)
        self.title_row = Adw.EntryRow(title="Title")
        self.title_row.set_text(keep_title if keep_title is not None else (entry.title if entry else ""))
        self.title_row.connect("changed", self._on_changed)
        self.title_row.connect("entry-activated", lambda *_: self._save())
        main.add(self.title_row)
        self.body.append(main)

        fields_group = Adw.PreferencesGroup()
        multiline_specs: list[FieldSpec] = []
        self.strength: StrengthIndicator | None = None
        for spec in FIELD_SPECS[self.entry_type]:
            value = entry.get(spec.key) if entry else ""
            if spec.multiline:
                multiline_specs.append(spec)
                continue
            row = self._field_row(spec, value)
            fields_group.add(row)
        self.body.append(fields_group)
        if self.entry_type is EntryType.LOGIN:
            self.strength = StrengthIndicator()
            self.strength.set_margin_start(6)
            self.strength.set_margin_end(6)
            self.strength.set_margin_top(0)
            self.strength.update(self._field_rows["password"].get_text())
            self.body.append(self.strength)

        for spec in multiline_specs:
            group = Adw.PreferencesGroup(title=spec.label)
            view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=4, bottom_margin=4)
            view.add_css_class("editor-text")
            view.set_size_request(-1, 140 if spec.key == "content" else 90)
            view.get_buffer().set_text(entry.get(spec.key) if entry else "")
            view.get_buffer().connect("changed", self._on_changed)
            view.update_property([Gtk.AccessibleProperty.LABEL], [spec.label])
            group.add(view)
            self._text_views[spec.key] = view
            self.body.append(group)

        if self.entry_type in SUPPORTS_CUSTOM_FIELDS:
            self.custom_group = Adw.PreferencesGroup(title="Custom Fields")
            add = Gtk.Button(valign=Gtk.Align.CENTER)
            add.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Add Field"))
            add.add_css_class("flat")
            add.connect("clicked", lambda *_: self._add_custom(CustomField("", "")))
            self.custom_group.set_header_suffix(add)
            self.custom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            self.custom_group.add(self.custom_box)
            self.body.append(self.custom_group)
            for custom in entry.custom_fields if entry else []:
                self._add_custom(custom)

        extra = Adw.PreferencesGroup()
        self.tags_row = Adw.EntryRow(title="Tags (comma separated)")
        self.tags_row.set_text(", ".join(entry.tags) if entry else "")
        self.tags_row.connect("changed", self._on_changed)
        self.favorite_row = Adw.SwitchRow(title="Favorite", active=bool(entry and entry.favorite))
        self.favorite_row.connect("notify::active", self._on_changed)
        extra.add(self.tags_row)
        extra.add(self.favorite_row)
        self.body.append(extra)

        self.error_label = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.error_label.add_css_class("error-text")
        self.body.append(self.error_label)
        self._update_save_sensitivity()
        if self._initial_snapshot is None:
            self._initial_snapshot = self._snapshot()

    def _field_row(self, spec: FieldSpec, value: str) -> Adw.EntryRow:
        if spec.secret:
            row = Adw.PasswordEntryRow(title=spec.label)
        else:
            row = Adw.EntryRow(title=spec.label)
        if spec.kind in INPUT_PURPOSE:
            row.set_input_purpose(INPUT_PURPOSE[spec.kind])
        if spec.kind == "expiry":
            row.set_title("Expiration Date (MM/YY)")
        row.set_text(value)
        row.connect("changed", self._on_changed)
        if spec.kind == "password":
            button = Gtk.Button(icon_name="media-playlist-shuffle-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Generate Password (Ctrl+G)")
            button.add_css_class("flat")
            button.update_property([Gtk.AccessibleProperty.LABEL], ["Generate password"])
            button.connect("clicked", lambda *_: self.generate_password())
            row.add_suffix(button)
        self._field_rows[spec.key] = row
        return row

    def _add_custom(self, custom: CustomField) -> None:
        editor = CustomFieldEditor(custom, self._remove_custom, lambda: self._on_changed())
        self._custom_editors.append(editor)
        self.custom_box.append(editor)
        if not custom.name:
            editor.name_row.grab_focus()
        self._on_changed()

    def _remove_custom(self, editor: CustomFieldEditor) -> None:
        self._custom_editors.remove(editor)
        self.custom_box.remove(editor)
        self._on_changed()

    # State ------------------------------------------------------------------
    def generate_password(self) -> None:
        if "password" not in self._field_rows:
            return

        def use(password: str) -> None:
            self._field_rows["password"].set_text(password)

        self.open_generator(use)

    def _on_type_changed(self, *_args) -> None:
        new_type = list(EntryType)[self.type_row.get_selected()]
        if new_type is self.entry_type:
            return
        title = self.title_row.get_text()
        self.entry_type = new_type
        self._build(None, keep_title=title)
        self.title_row.grab_focus()

    def _on_changed(self, *_args) -> None:
        if self.strength is not None and "password" in self._field_rows:
            self.strength.update(self._field_rows["password"].get_text())
        self._update_save_sensitivity()

    def _update_save_sensitivity(self) -> None:
        self.save_button.set_sensitive(bool(self.title_row.get_text().strip()))

    def _collect(self) -> tuple[str, dict[str, str], list[CustomField]]:
        fields = {key: row.get_text().strip() if key not in ("password", "cvv") else row.get_text() for key, row in self._field_rows.items()}
        for key, view in self._text_views.items():
            buf = view.get_buffer()
            fields[key] = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).rstrip()
        customs = [e.get_field() for e in self._custom_editors]
        return self.title_row.get_text(), fields, customs

    def _snapshot(self) -> dict:
        title, fields, customs = self._collect()
        return {
            "type": self.entry_type,
            "title": title,
            "fields": fields,
            "customs": [c.to_dict() for c in customs],
            "tags": self.tags_row.get_text(),
            "favorite": self.favorite_row.get_active(),
        }

    def _is_dirty(self) -> bool:
        return self._snapshot() != self._initial_snapshot

    def _save(self) -> None:
        title, fields, customs = self._collect()
        errors = validate_entry_fields(self.entry_type, title, fields, customs)
        for key, row in self._field_rows.items():
            if key in errors:
                row.add_css_class("error")
            else:
                row.remove_css_class("error")
        if "title" in errors:
            self.title_row.add_css_class("error")
        else:
            self.title_row.remove_css_class("error")
        if errors:
            self.error_label.set_label("\n".join(errors.values()))
            self.error_label.set_visible(True)
            return
        self.error_label.set_visible(False)

        if self.original is not None:
            entry = self.original.copy()
            entry.title = title
            entry.fields = fields
        else:
            entry = Entry(type=self.entry_type, title=title, fields=fields)
        entry.custom_fields = customs
        entry.tags = [t for t in (part.strip() for part in self.tags_row.get_text().split(",")) if t]
        entry.favorite = self.favorite_row.get_active()
        failure = self.on_save(entry)
        if failure:
            self.error_label.set_label(failure)
            self.error_label.set_visible(True)
            return
        self.force_close()

    def _on_close_attempt(self, *_args) -> None:
        if not self._is_dirty():
            self.force_close()
            return
        confirm(
            self,
            "Discard Changes?",
            "Your changes to this entry have not been saved.",
            "Discard",
            self.force_close,
        )
