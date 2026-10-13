"""Navigation sidebar: brand, categories with counts, new entry, settings."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gio, Gtk

from .. import APP_ID, APP_NAME
from ..core.models import EntryType
from ..core.search import Category

CATEGORY_ICONS = {
    Category.ALL: "view-grid-symbolic",
    Category.FAVORITES: "starred-symbolic",
    Category.LOGINS: "dialog-password-symbolic",
    Category.NOTES: "text-x-generic-symbolic",
    Category.IDENTITIES: "avatar-default-symbolic",
    Category.CARDS: "lockly-card-symbolic",
    Category.TRASH: "user-trash-symbolic",
}

MAIN_CATEGORIES = [Category.ALL, Category.FAVORITES]
TYPE_CATEGORIES = [Category.LOGINS, Category.NOTES, Category.IDENTITIES, Category.CARDS]


class CategoryRow(Gtk.ListBoxRow):
    def __init__(self, category: Category):
        super().__init__()
        self.category = category
        box = Gtk.Box(spacing=12, margin_start=4, margin_end=4)
        box.append(Gtk.Image(icon_name=CATEGORY_ICONS[category]))
        label = Gtk.Label(label=category.label, xalign=0, hexpand=True, ellipsize=3)
        box.append(label)
        self.count_label = Gtk.Label(xalign=1)
        self.count_label.add_css_class("dim-label")
        self.count_label.add_css_class("sidebar-count")
        box.append(self.count_label)
        self.set_child(box)
        self.update_property([Gtk.AccessibleProperty.LABEL], [category.label])

    def set_count(self, count: int | None) -> None:
        self.count_label.set_label("" if not count else str(count))
        label = self.category.label if count is None else f"{self.category.label}, {count} items"
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])


class Sidebar(Adw.Bin):
    def __init__(self, on_category: Callable[[Category], None], on_settings: Callable[[], None]):
        super().__init__()
        self.add_css_class("lockly-sidebar")
        self._on_category = on_category
        self._rows: dict[Category, CategoryRow] = {}

        # Header: brand + lock + primary menu
        brand = Gtk.Box(spacing=8)
        brand.append(Gtk.Image(icon_name=APP_ID, pixel_size=22))
        title = Gtk.Label(label=APP_NAME)
        title.add_css_class("brand-title")
        brand.append(title)
        header = Adw.HeaderBar(title_widget=brand)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Password Generator", "win.generator")
        section.append("Settings", "win.preferences")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Keyboard Shortcuts", "win.show-shortcuts")
        section.append(f"About {APP_NAME}", "app.about")
        menu.append_section(None, section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main Menu")
        menu_button.set_primary(True)
        header.pack_end(menu_button)
        lock_button = Gtk.Button(icon_name="system-lock-screen-symbolic", tooltip_text="Lock Vault (Ctrl+L)")
        lock_button.set_action_name("win.lock")
        header.pack_end(lock_button)

        # New entry split button
        new_menu = Gio.Menu()
        for entry_type in EntryType:
            new_menu.append(f"New {entry_type.label}", f"win.new-entry-of-type::{entry_type.value}")
        self.new_button = Adw.SplitButton(menu_model=new_menu, tooltip_text="New Entry (Ctrl+N)")
        self.new_button.set_dropdown_tooltip("Choose Entry Type")
        content = Adw.ButtonContent(icon_name="list-add-symbolic", label="New Entry")
        self.new_button.set_child(content)
        self.new_button.add_css_class("suggested-action")
        self.new_button.add_css_class("new-entry-button")
        self.new_button.set_action_name("win.new-entry")

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.update_property([Gtk.AccessibleProperty.LABEL], ["Categories"])
        for category in (*MAIN_CATEGORIES, *TYPE_CATEGORIES, Category.TRASH):
            self._add_row(category)
        self.list.set_header_func(self._header_func)
        self.list.connect("row-selected", self._on_row_selected)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.append(self.new_button)
        inner.append(self.list)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=inner)

        bottom = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        bottom.add_css_class("navigation-sidebar")
        settings_row = Gtk.ListBoxRow(activatable=True)
        row_box = Gtk.Box(spacing=12, margin_start=4)
        row_box.append(Gtk.Image(icon_name="emblem-system-symbolic"))
        row_box.append(Gtk.Label(label="Settings", xalign=0, hexpand=True))
        settings_row.set_child(row_box)
        settings_row.update_property([Gtk.AccessibleProperty.LABEL], ["Settings"])
        bottom.append(settings_row)
        bottom.connect("row-activated", lambda *_: on_settings())

        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        view.add_bottom_bar(bottom)
        self.set_child(view)

    def _add_row(self, category: Category) -> None:
        row = CategoryRow(category)
        self._rows[category] = row
        self.list.append(row)

    @staticmethod
    def _header_func(row: Gtk.ListBoxRow, _before: Gtk.ListBoxRow | None) -> None:
        category = getattr(row, "category", None)
        if category is TYPE_CATEGORIES[0]:
            label = Gtk.Label(label="Types", xalign=0)
            label.add_css_class("dim-label")
            label.add_css_class("sidebar-section-label")
            row.set_header(label)
        elif category is Category.TRASH:
            row.set_header(Gtk.Separator(margin_top=6, margin_bottom=6, margin_start=12, margin_end=12))
        else:
            row.set_header(None)

    def _on_row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if isinstance(row, CategoryRow):
            self._on_category(row.category)

    def select(self, category: Category) -> None:
        row = self._rows[category]
        if self.list.get_selected_row() is not row:
            self.list.select_row(row)

    def set_counts(self, counts: dict[Category, int] | None) -> None:
        for category, row in self._rows.items():
            row.set_count(None if counts is None else counts.get(category, 0))
