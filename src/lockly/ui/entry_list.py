"""Middle pane: search, filters, sorting and the list of entries."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk

from ..core.models import Entry, EntryType
from ..core.search import Category, SearchIndex, SortOrder, query_entries
from .common import tile_for

TYPE_FILTERS: list[EntryType | None] = [None, *EntryType]


class EntryRow(Gtk.ListBoxRow):
    def __init__(self, entry: Entry, show_type: bool):
        super().__init__()
        self.entry_id = entry.id
        box = Gtk.Box(spacing=12)
        box.append(tile_for(entry))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=entry.title or "Untitled", xalign=0, ellipsize=3)
        title.add_css_class("entry-row-title")
        text.append(title)
        subtitle_text = entry.subtitle
        if subtitle_text:
            subtitle = Gtk.Label(label=subtitle_text, xalign=0, ellipsize=3)
            subtitle.add_css_class("dim-label")
            subtitle.add_css_class("entry-row-subtitle")
            text.append(subtitle)
        box.append(text)

        if entry.favorite:
            star = Gtk.Image(icon_name="starred-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Favorite")
            star.add_css_class("dim-label")
            box.append(star)
        if show_type:
            chip = Gtk.Label(label=entry.type.label, valign=Gtk.Align.CENTER)
            chip.add_css_class("type-chip")
            chip.add_css_class("dim-label")
            box.append(chip)
        self.set_child(box)
        description = ", ".join(p for p in (entry.title, subtitle_text, entry.type.label, "favorite" if entry.favorite else "") if p)
        self.update_property([Gtk.AccessibleProperty.LABEL], [description])


class EntryList(Adw.Bin):
    __gsignals__ = {
        "entry-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, get_entries: Callable[[], list[Entry]], sidebar_toggle: Gtk.Widget):
        super().__init__()
        self._get_entries = get_entries
        self.index = SearchIndex()
        self.category = Category.ALL
        self.sort_order = SortOrder.NAME
        self.type_filter: EntryType | None = None
        self._rows: dict[str, EntryRow] = {}
        self._suppress_selection = False

        self.title = Adw.WindowTitle(title=Category.ALL.label)
        header = Adw.HeaderBar(title_widget=self.title)
        header.pack_start(sidebar_toggle)

        sort_menu = Gio.Menu()
        for order in SortOrder:
            sort_menu.append(order.label, f"win.sort-order::{order.value}")
        sort_button = Gtk.MenuButton(icon_name="view-sort-descending-symbolic", menu_model=sort_menu, tooltip_text="Sort")
        header.pack_end(sort_button)

        # Search + type filter
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search vault", hexpand=True)
        self.search_entry.update_property([Gtk.AccessibleProperty.LABEL], ["Search entries"])
        self.search_entry.connect("search-changed", lambda *_: self.refresh(keep_selection=True))
        self.search_entry.connect("activate", self._on_search_activate)
        self.search_entry.connect("stop-search", self._on_stop_search)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_search_key)
        self.search_entry.add_controller(key)

        labels = ["All Types", *[t.plural for t in EntryType]]
        self.type_dropdown = Gtk.DropDown.new_from_strings(labels)
        self.type_dropdown.set_tooltip_text("Filter by type")
        self.type_dropdown.update_property([Gtk.AccessibleProperty.LABEL], ["Filter by type"])
        self.type_dropdown.connect("notify::selected", self._on_type_filter)

        search_box = Gtk.Box(spacing=6)
        search_box.add_css_class("search-bar-box")
        search_box.append(self.search_entry)
        search_box.append(self.type_dropdown)

        self.trash_banner = Adw.Banner(title="Items in the Trash can be restored until you empty it", button_label="Empty Trash")
        self.trash_banner.set_action_name("win.empty-trash")

        # List
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("entry-list")
        self.list.update_property([Gtk.AccessibleProperty.LABEL], ["Entries"])
        self.list.connect("row-selected", self._on_row_selected)
        self.list.connect("row-activated", self._on_row_activated)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.list)

        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._on_context_click)
        self.list.add_controller(click)
        long_press = Gtk.GestureLongPress(touch_only=True)
        long_press.connect("pressed", lambda g, x, y: self._show_context_menu(x, y))
        self.list.add_controller(long_press)
        list_keys = Gtk.EventControllerKey()
        list_keys.connect("key-pressed", self._on_list_key)
        self.list.add_controller(list_keys)

        self.empty_page = Adw.StatusPage()
        self.empty_page.add_css_class("compact")
        self.stack = Gtk.Stack()
        self.stack.add_named(scroller, "list")
        self.stack.add_named(self.empty_page, "empty")

        # Parented to the Bin, not the ListBox: ListBox.remove_all() would
        # otherwise try to remove the popover as if it were a row.
        self.context_menu = Gtk.PopoverMenu(has_arrow=False, halign=Gtk.Align.START)
        self.context_menu.set_parent(self)

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        view.add_top_bar(search_box)
        view.add_top_bar(self.trash_banner)
        self.set_child(view)

    # Public API -------------------------------------------------------------
    @property
    def selected_id(self) -> str | None:
        row = self.list.get_selected_row()
        return row.entry_id if isinstance(row, EntryRow) else None

    def set_category(self, category: Category) -> None:
        self.category = category
        self.title.set_title(category.label)
        self.trash_banner.set_revealed(category is Category.TRASH)
        type_filter_useful = category in (Category.ALL, Category.FAVORITES, Category.TRASH)
        self.type_dropdown.set_visible(type_filter_useful)
        if not type_filter_useful:
            self.type_filter = None
        else:
            self.type_filter = TYPE_FILTERS[self.type_dropdown.get_selected()]
        self.refresh(keep_selection=False)

    def set_sort_order(self, order: SortOrder) -> None:
        self.sort_order = order
        self.refresh(keep_selection=True)

    def focus_search(self) -> None:
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)

    def clear(self) -> None:
        """Remove every decrypted value from the widget tree (on lock)."""
        self._suppress_selection = True
        self.list.remove_all()
        self._rows.clear()
        self._suppress_selection = False
        self.search_entry.set_text("")
        self.index.clear()
        self.context_menu.popdown()

    def visible_entries(self) -> list[Entry]:
        return query_entries(
            self._get_entries(),
            category=self.category,
            query=self.search_entry.get_text(),
            order=self.sort_order,
            type_filter=self.type_filter,
            index=self.index,
        )

    def refresh(self, keep_selection: bool = True, select: str | None = None) -> None:
        previous = select or (self.selected_id if keep_selection else None)
        entries = self.visible_entries()
        self._suppress_selection = True
        self.list.remove_all()
        self._rows.clear()
        show_type = self.category in (Category.ALL, Category.FAVORITES, Category.TRASH)
        for entry in entries:
            row = EntryRow(entry, show_type)
            self._rows[entry.id] = row
            self.list.append(row)
        self._suppress_selection = False
        count = len(entries)
        self.title.set_subtitle(f"{count} {'item' if count == 1 else 'items'}")

        if entries:
            self.stack.set_visible_child_name("list")
        else:
            self._update_empty_page()
            self.stack.set_visible_child_name("empty")

        if previous and previous in self._rows:
            self.list.select_row(self._rows[previous])
        else:
            self.list.unselect_all()
            self.emit("entry-selected", "")

    def select_entry(self, entry_id: str) -> None:
        row = self._rows.get(entry_id)
        if row is not None:
            self.list.select_row(row)
            row.grab_focus()

    def unselect(self) -> None:
        self.list.unselect_all()

    def invalidate(self, entry_id: str | None) -> None:
        self.index.invalidate(entry_id)

    # Internals --------------------------------------------------------------
    def _update_empty_page(self) -> None:
        query = self.search_entry.get_text().strip()
        if query:
            self.empty_page.set_icon_name("edit-find-symbolic")
            self.empty_page.set_title("No Results")
            self.empty_page.set_description(f"Nothing matches “{query}”. Search covers titles, usernames, websites, tags and notes.")
        elif self.category is Category.TRASH:
            self.empty_page.set_icon_name("user-trash-symbolic")
            self.empty_page.set_title("Trash Is Empty")
            self.empty_page.set_description("Deleted items appear here until you remove them permanently.")
        elif self.category is Category.FAVORITES:
            self.empty_page.set_icon_name("starred-symbolic")
            self.empty_page.set_title("No Favorites")
            self.empty_page.set_description("Star an entry to keep it here.")
        else:
            entry_type = self.category.entry_type
            noun = entry_type.plural.lower() if entry_type else "entries"
            self.empty_page.set_icon_name("list-add-symbolic")
            self.empty_page.set_title(f"No {noun.title() if entry_type else 'Entries'} Yet")
            self.empty_page.set_description(f"Press Ctrl+N or use “New Entry” to add {noun}.")

    def _on_type_filter(self, *_args) -> None:
        self.type_filter = TYPE_FILTERS[self.type_dropdown.get_selected()]
        self.refresh(keep_selection=True)

    def _on_row_selected(self, _list, row) -> None:
        if self._suppress_selection:
            return
        self.emit("entry-selected", row.entry_id if isinstance(row, EntryRow) else "")

    def _on_row_activated(self, _list, row) -> None:
        if isinstance(row, EntryRow):
            self.emit("entry-selected", row.entry_id)
            self.activate_action("win.show-details", None)

    def _on_search_activate(self, *_args) -> None:
        first = self.list.get_row_at_index(0)
        if isinstance(first, EntryRow):
            self.list.select_row(first)
            first.grab_focus()

    def _on_stop_search(self, *_args) -> None:
        if self.search_entry.get_text():
            self.search_entry.set_text("")
        else:
            self.unselect()

    def _on_search_key(self, _ctrl, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Down:
            self._on_search_activate()
            return True
        return False

    def _on_list_key(self, _ctrl, keyval, _code, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.unselect()
            self.search_entry.grab_focus()
            return True
        if keyval == Gdk.KEY_Menu or (keyval == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK):
            row = self.list.get_selected_row()
            if row is not None:
                ok, bounds = row.compute_bounds(self.list)
                if ok:
                    self._show_context_menu(bounds.origin.x + 40, bounds.origin.y + bounds.size.height / 2)
                    return True
        return False

    def _on_context_click(self, gesture, _n_press, x, y) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._show_context_menu(x, y)

    def _show_context_menu(self, x: float, y: float) -> None:
        row = self.list.get_row_at_y(int(y))
        if isinstance(row, EntryRow):
            self.show_context_menu_for(row, x, y)

    def show_context_menu_for(self, row: EntryRow, x: float, y: float) -> None:
        """Pop up the context menu for ``row`` at list coordinates (x, y)."""
        self.list.select_row(row)
        entries = {e.id: e for e in self._get_entries()}
        entry = entries.get(row.entry_id)
        if entry is None:
            return
        menu = Gio.Menu()
        copy_section = Gio.Menu()
        if entry.type is EntryType.LOGIN:
            if entry.get("username"):
                copy_section.append("Copy Username", "win.copy-username")
            if entry.get("password"):
                copy_section.append("Copy Password", "win.copy-password")
            if entry.get("url"):
                copy_section.append("Open Website", "win.open-website")
        elif entry.type is EntryType.CARD and entry.get("number"):
            copy_section.append("Copy Card Number", "win.copy-password")
        elif entry.type is EntryType.IDENTITY and entry.get("email"):
            copy_section.append("Copy Email", "win.copy-username")
        if copy_section.get_n_items():
            menu.append_section(None, copy_section)
        edit_section = Gio.Menu()
        if entry.in_trash:
            edit_section.append("Restore", "win.restore-entry")
            edit_section.append("Delete Permanently…", "win.delete-entry")
        else:
            edit_section.append("Edit", "win.edit-entry")
            edit_section.append("Remove from Favorites" if entry.favorite else "Add to Favorites", "win.toggle-favorite")
            edit_section.append("Move to Trash", "win.trash-entry")
        menu.append_section(None, edit_section)
        self.context_menu.set_menu_model(menu)
        ok, point = self.list.compute_point(self, Graphene.Point().init(x, y))
        if ok:
            x, y = point.x, point.y
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.context_menu.set_pointing_to(rect)
        GLib.idle_add(lambda: self.context_menu.popup() and False)
