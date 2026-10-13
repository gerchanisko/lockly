"""In-memory search, filtering and sorting over decrypted entries.

The search index lives only in process memory, is built lazily from the
unlocked vault and must be cleared (``SearchIndex.clear``) when the vault
locks. Secret fields (passwords, card numbers, security codes, hidden custom
fields) are never indexed.
"""

from __future__ import annotations

from enum import Enum

from .models import FIELD_SPECS, Entry, EntryType


class Category(str, Enum):
    ALL = "all"
    FAVORITES = "favorites"
    LOGINS = "login"
    NOTES = "note"
    IDENTITIES = "identity"
    CARDS = "card"
    TRASH = "trash"

    @property
    def label(self) -> str:
        return {
            Category.ALL: "All Items",
            Category.FAVORITES: "Favorites",
            Category.LOGINS: "Logins",
            Category.NOTES: "Secure Notes",
            Category.IDENTITIES: "Identities",
            Category.CARDS: "Credit Cards",
            Category.TRASH: "Trash",
        }[self]

    @property
    def entry_type(self) -> EntryType | None:
        try:
            return EntryType(self.value)
        except ValueError:
            return None


class SortOrder(str, Enum):
    NAME = "name"
    CREATED = "created"
    MODIFIED = "modified"

    @property
    def label(self) -> str:
        return {SortOrder.NAME: "Name", SortOrder.CREATED: "Date Created", SortOrder.MODIFIED: "Recently Modified"}[self]


def in_category(entry: Entry, category: Category) -> bool:
    if category is Category.TRASH:
        return entry.in_trash
    if entry.in_trash:
        return False
    if category is Category.ALL:
        return True
    if category is Category.FAVORITES:
        return entry.favorite
    return entry.type.value == category.value


def haystack(entry: Entry) -> str:
    parts = [entry.title, entry.type.label, entry.type.plural, " ".join(entry.tags)]
    parts += [f"#{t}" for t in entry.tags]
    for spec in FIELD_SPECS[entry.type]:
        if spec.searchable and not spec.secret:
            parts.append(entry.get(spec.key))
    for custom in entry.custom_fields:
        parts.append(custom.name)
        if not custom.hidden:
            parts.append(custom.value)
    return "\n".join(p for p in parts if p).casefold()


class SearchIndex:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, str]] = {}

    def text_for(self, entry: Entry) -> str:
        cached = self._cache.get(entry.id)
        if cached and cached[0] == entry.modified:
            return cached[1]
        text = haystack(entry)
        self._cache[entry.id] = (entry.modified, text)
        return text

    def matches(self, entry: Entry, query: str) -> bool:
        terms = query.casefold().split()
        if not terms:
            return True
        text = self.text_for(entry)
        return all(term in text for term in terms)

    def invalidate(self, entry_id: str | None = None) -> None:
        if entry_id is None:
            self._cache.clear()
        else:
            self._cache.pop(entry_id, None)

    def clear(self) -> None:
        self._cache.clear()


def sort_key(order: SortOrder):
    if order is SortOrder.NAME:
        return lambda e: (e.title.casefold(), e.created)
    if order is SortOrder.CREATED:
        return lambda e: -e.created
    return lambda e: -e.modified


def query_entries(
    entries: list[Entry],
    category: Category = Category.ALL,
    query: str = "",
    order: SortOrder = SortOrder.NAME,
    type_filter: EntryType | None = None,
    index: SearchIndex | None = None,
) -> list[Entry]:
    index = index or SearchIndex()
    result = [
        e
        for e in entries
        if in_category(e, category)
        and (type_filter is None or e.type is type_filter)
        and index.matches(e, query)
    ]
    result.sort(key=sort_key(order))
    return result


def category_counts(entries: list[Entry]) -> dict[Category, int]:
    return {c: sum(1 for e in entries if in_category(e, c)) for c in Category}
