"""Right pane: details of the selected entry."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gio, Gtk

from ..core.models import FIELD_SPECS, Entry, EntryType, FieldSpec, mask_card_number
from .common import format_time, safe_web_url, tile_for, wrap_label

MASK = "••••••••••••"

CopyCallback = Callable[[str, str, bool], None]  # (text, label, sensitive)


class SecretRow(Adw.ActionRow):
    """A row whose value is hidden until the user reveals it."""

    def __init__(self, title: str, value: str, masked: str, on_copy: CopyCallback):
        super().__init__(title=title, use_markup=False)
        self.add_css_class("property")
        self._value = value
        self._masked = masked
        self.set_subtitle(masked)
        self.set_subtitle_selectable(False)

        self.reveal = Gtk.ToggleButton(icon_name="view-reveal-symbolic", valign=Gtk.Align.CENTER, tooltip_text=f"Show {title}")
        self.reveal.add_css_class("flat")
        self.reveal.connect("toggled", self._on_toggled)
        self.reveal.update_property([Gtk.AccessibleProperty.LABEL], [f"Show {title}"])
        self.add_suffix(self.reveal)
        add_copy_button(self, title, lambda: self._value, on_copy, sensitive=True)

    def _on_toggled(self, button: Gtk.ToggleButton) -> None:
        shown = button.get_active()
        self.set_subtitle(self._value if shown else self._masked)
        self.set_subtitle_selectable(shown)
        button.set_icon_name("view-conceal-symbolic" if shown else "view-reveal-symbolic")
        tip = f"{'Hide' if shown else 'Show'} {self.get_title()}"
        button.set_tooltip_text(tip)
        button.update_property([Gtk.AccessibleProperty.LABEL], [tip])
        if shown:
            self.add_css_class("secret-value")
        else:
            self.remove_css_class("secret-value")

    def conceal(self) -> None:
        self.reveal.set_active(False)


def add_copy_button(row: Adw.ActionRow, label: str, value: Callable[[], str], on_copy: CopyCallback, sensitive: bool) -> None:
    button = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text=f"Copy {label}")
    button.add_css_class("flat")
    button.update_property([Gtk.AccessibleProperty.LABEL], [f"Copy {label}"])
    button.connect("clicked", lambda *_: on_copy(value(), label, sensitive))
    row.add_suffix(button)


class EntryDetails(Adw.Bin):
    def __init__(self, on_copy: CopyCallback):
        super().__init__()
        self._on_copy = on_copy
        self._secret_rows: list[SecretRow] = []
        self.entry: Entry | None = None

        self.header = Adw.HeaderBar(show_title=False)
        self.fav_button = Gtk.Button(icon_name="non-starred-symbolic", tooltip_text="Add to Favorites")
        self.fav_button.set_action_name("win.toggle-favorite")
        self.edit_button = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Edit (Ctrl+E)")
        self.edit_button.set_action_name("win.edit-entry")
        self.menu_button = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="More Actions")
        self.header.pack_end(self.menu_button)
        self.header.pack_end(self.edit_button)
        self.header.pack_end(self.fav_button)

        self.trash_banner = Adw.Banner(title="This item is in the Trash", button_label="Restore")
        self.trash_banner.set_action_name("win.restore-entry")

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=24, margin_start=18, margin_end=18)
        clamp = Adw.Clamp(maximum_size=640, child=self.body)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=clamp)
        self.scroller = scroller

        empty = Adw.StatusPage(title="No Entry Selected", description="Choose an entry from the list to see its details.")
        empty.add_css_class("compact")
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(scroller, "entry")

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(self.header)
        view.add_top_bar(self.trash_banner)
        self.set_child(view)
        self.show_entry(None)

    # ------------------------------------------------------------------------
    def clear(self) -> None:
        self.show_entry(None)

    def conceal_all(self) -> None:
        for row in self._secret_rows:
            row.conceal()

    def show_entry(self, entry: Entry | None) -> None:
        self.entry = entry
        self._secret_rows.clear()
        child = self.body.get_first_child()
        while child is not None:
            self.body.remove(child)
            child = self.body.get_first_child()

        has_entry = entry is not None
        for widget in (self.fav_button, self.edit_button, self.menu_button):
            widget.set_visible(has_entry and not entry.in_trash)
        self.trash_banner.set_revealed(bool(entry and entry.in_trash))
        if entry is None:
            self.stack.set_visible_child_name("empty")
            return

        self.fav_button.set_icon_name("starred-symbolic" if entry.favorite else "non-starred-symbolic")
        fav_tip = "Remove from Favorites" if entry.favorite else "Add to Favorites"
        self.fav_button.set_tooltip_text(fav_tip)
        self.fav_button.update_property([Gtk.AccessibleProperty.LABEL], [fav_tip])
        self.menu_button.set_menu_model(self._build_menu(entry))

        self.body.append(self._build_title(entry))
        self._build_fields(entry)
        if entry.custom_fields:
            self._build_custom_fields(entry)
        if entry.tags:
            self._build_tags(entry)
        self._build_meta(entry)
        self.stack.set_visible_child_name("entry")
        self.scroller.get_vadjustment().set_value(0)

    def _build_menu(self, entry: Entry) -> Gio.Menu:
        menu = Gio.Menu()
        section = Gio.Menu()
        if entry.type is EntryType.LOGIN and entry.get("url"):
            section.append("Open Website", "win.open-website")
        section.append("Edit", "win.edit-entry")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Move to Trash", "win.trash-entry")
        menu.append_section(None, section)
        return menu

    def _build_title(self, entry: Entry) -> Gtk.Widget:
        box = Gtk.Box(spacing=14)
        box.append(tile_for(entry, large=True))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=2)
        title = Gtk.Label(label=entry.title, xalign=0, wrap=True, selectable=True, accessible_role=Gtk.AccessibleRole.HEADING)
        title.set_wrap_mode(2)
        title.add_css_class("details-title")
        text.append(title)
        kind = Gtk.Label(label=entry.type.label + (" · Favorite" if entry.favorite else ""), xalign=0)
        kind.add_css_class("dim-label")
        text.append(kind)
        box.append(text)
        return box

    def _value_row(self, spec: FieldSpec, value: str) -> Adw.ActionRow:
        if spec.secret:
            masked = mask_card_number(value) if spec.kind == "card-number" else MASK
            row = SecretRow(spec.label, value, masked, self._on_copy)
            self._secret_rows.append(row)
            return row
        row = Adw.ActionRow(title=spec.label, subtitle=value, subtitle_selectable=True, use_markup=False)
        row.add_css_class("property")
        if spec.kind == "url":
            url = safe_web_url(value)
            if url:
                button = Gtk.Button(icon_name="adw-external-link-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Open in Browser")
                button.add_css_class("flat")
                button.update_property([Gtk.AccessibleProperty.LABEL], ["Open website in browser"])
                button.set_action_name("win.open-website")
                row.add_suffix(button)
        if spec.copyable:
            add_copy_button(row, spec.label, lambda: value, self._on_copy, sensitive=False)
        return row

    def _build_fields(self, entry: Entry) -> None:
        group = Adw.PreferencesGroup()
        multiline: list[tuple[FieldSpec, str]] = []
        for spec in FIELD_SPECS[entry.type]:
            value = entry.get(spec.key)
            if not value:
                continue
            if spec.multiline and spec.key in ("notes", "content"):
                multiline.append((spec, value))
            else:
                group.add(self._value_row(spec, value))
        if group.get_first_child() is not None and _group_has_rows(entry):
            self.body.append(group)
        for spec, value in multiline:
            notes_group = Adw.PreferencesGroup(title=spec.label)
            if spec.key == "content":
                copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy Content", valign=Gtk.Align.CENTER)
                copy.add_css_class("flat")
                copy.update_property([Gtk.AccessibleProperty.LABEL], ["Copy note content"])
                copy.connect("clicked", lambda *_, v=value: self._on_copy(v, "Note", True))
                notes_group.set_header_suffix(copy)
            frame = Gtk.Box()
            frame.add_css_class("card")
            label = wrap_label(value, "notes-view", selectable=True)
            label.set_hexpand(True)
            frame.append(label)
            notes_group.add(frame)
            self.body.append(notes_group)

    def _build_custom_fields(self, entry: Entry) -> None:
        group = Adw.PreferencesGroup(title="Custom Fields")
        for custom in entry.custom_fields:
            spec = FieldSpec(custom.name or "field", custom.name or "Unnamed", secret=custom.hidden)
            group.add(self._value_row(spec, custom.value))
        self.body.append(group)

    def _build_tags(self, entry: Entry) -> None:
        group = Adw.PreferencesGroup(title="Tags")
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6, max_children_per_line=12)
        for tag in entry.tags:
            pill = Gtk.Label(label=tag)
            pill.add_css_class("tag-pill")
            flow.append(pill)
        group.add(flow)
        self.body.append(group)

    def _build_meta(self, entry: Entry) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6)
        lines = [f"Created {format_time(entry.created)}", f"Modified {format_time(entry.modified)}"]
        lines.append("Favorite" if entry.favorite else "Not a favorite")
        if entry.in_trash:
            lines.append(f"Moved to Trash {format_time(entry.deleted_at)}")
        for line in lines:
            label = Gtk.Label(label=line, xalign=0)
            label.add_css_class("dim-label")
            label.add_css_class("meta-label")
            box.append(label)
        self.body.append(box)


def _group_has_rows(entry: Entry) -> bool:
    return any(
        entry.get(spec.key) and not (spec.multiline and spec.key in ("notes", "content"))
        for spec in FIELD_SPECS[entry.type]
    )
