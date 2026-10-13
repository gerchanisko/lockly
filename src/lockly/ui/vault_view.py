"""Three-pane layout of the unlocked vault: sidebar | entry list | details.

Below 860sp the sidebar becomes an overlay; below 600sp the list and details
collapse into a navigation stack.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, GObject, Gtk

from ..core.models import Entry
from ..core.search import Category
from .entry_details import CopyCallback, EntryDetails
from .entry_list import EntryList
from .sidebar import Sidebar


class VaultView(Adw.Bin):
    def __init__(
        self,
        get_entries: Callable[[], list[Entry]],
        on_copy: CopyCallback,
        on_settings: Callable[[], None],
    ):
        super().__init__()
        self.sidebar = Sidebar(on_category=self._on_category, on_settings=on_settings)

        self.sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Show Sidebar", visible=False)
        self.sidebar_toggle.update_property([Gtk.AccessibleProperty.LABEL], ["Show sidebar"])
        self.entry_list = EntryList(get_entries, self.sidebar_toggle)
        self.details = EntryDetails(on_copy)

        self.list_page = Adw.NavigationPage(title="Entries", child=self.entry_list, tag="list")
        self.details_page = Adw.NavigationPage(title="Details", child=self.details, tag="details")
        self.inner = Adw.NavigationSplitView(sidebar=self.list_page, content=self.details_page)
        self.inner.set_min_sidebar_width(300)
        self.inner.set_max_sidebar_width(420)
        self.inner.set_sidebar_width_fraction(0.42)

        self.outer = Adw.OverlaySplitView(sidebar=self.sidebar, content=self.inner)
        self.outer.set_min_sidebar_width(210)
        self.outer.set_max_sidebar_width(250)
        self.outer.bind_property("collapsed", self.sidebar_toggle, "visible", GObject.BindingFlags.SYNC_CREATE)
        self.outer.bind_property(
            "show-sidebar", self.sidebar_toggle, "active",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL,
        )
        self.set_child(self.outer)
        self.category = Category.ALL

    def add_breakpoints(self, window: Adw.ApplicationWindow) -> None:
        medium = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 860sp"))
        medium.add_setter(self.outer, "collapsed", True)
        window.add_breakpoint(medium)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        narrow.add_setter(self.outer, "collapsed", True)
        narrow.add_setter(self.inner, "collapsed", True)
        window.add_breakpoint(narrow)

    def _on_category(self, category: Category) -> None:
        self.category = category
        self.entry_list.set_category(category)
        if self.outer.get_collapsed():
            self.outer.set_show_sidebar(False)
        self.inner.set_show_content(False)

    def select_category(self, category: Category) -> None:
        self.sidebar.select(category)
        if self.category is not category:
            self._on_category(category)

    def show_details(self) -> None:
        self.inner.set_show_content(True)

    def show_list(self) -> None:
        self.inner.set_show_content(False)
