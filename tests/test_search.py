from lockly.core.models import CustomField, Entry, EntryType
from lockly.core.search import Category, SearchIndex, SortOrder, category_counts, query_entries


def sample():
    a = Entry(type=EntryType.LOGIN, title="Beta Mail", fields={"username": "bob", "password": "hunter2-sample", "url": "https://mail.example"}, tags=["work"], created=1, modified=50)
    b = Entry(type=EntryType.LOGIN, title="alpha forge", fields={"username": "carol"}, favorite=True, created=2, modified=10,
              custom_fields=[CustomField("token", "hidden-value", hidden=True), CustomField("team", "core")])
    c = Entry(type=EntryType.NOTE, title="Gamma note", fields={"content": "wifi passphrase lives here"}, created=3, modified=20)
    d = Entry(type=EntryType.CARD, title="Delta card", fields={"number": "4111 1111 1111 1111"}, created=4, modified=30, deleted_at=99)
    return [a, b, c, d]


def titles(entries):
    return [e.title for e in entries]


def test_categories():
    entries = sample()
    assert titles(query_entries(entries, Category.ALL)) == ["alpha forge", "Beta Mail", "Gamma note"]
    assert titles(query_entries(entries, Category.FAVORITES)) == ["alpha forge"]
    assert titles(query_entries(entries, Category.NOTES)) == ["Gamma note"]
    assert titles(query_entries(entries, Category.CARDS)) == []
    assert titles(query_entries(entries, Category.TRASH)) == ["Delta card"]
    counts = category_counts(entries)
    assert counts[Category.ALL] == 3 and counts[Category.TRASH] == 1 and counts[Category.LOGINS] == 2


def test_sorting():
    entries = sample()
    assert titles(query_entries(entries, order=SortOrder.CREATED)) == ["Gamma note", "alpha forge", "Beta Mail"]
    assert titles(query_entries(entries, order=SortOrder.MODIFIED)) == ["Beta Mail", "Gamma note", "alpha forge"]


def test_search_fields():
    entries = sample()
    assert titles(query_entries(entries, query="BOB")) == ["Beta Mail"]
    assert titles(query_entries(entries, query="mail.example")) == ["Beta Mail"]
    assert titles(query_entries(entries, query="work")) == ["Beta Mail"]
    assert titles(query_entries(entries, query="wifi")) == ["Gamma note"]
    assert titles(query_entries(entries, query="secure note")) == ["Gamma note"]
    assert titles(query_entries(entries, query="core")) == ["alpha forge"]
    assert titles(query_entries(entries, query="alpha carol")) == ["alpha forge"]
    assert titles(query_entries(entries, query="alpha bob")) == []


def test_secrets_not_searchable():
    entries = sample()
    assert query_entries(entries, query="hunter2") == []
    assert query_entries(entries, query="hidden-value") == []
    assert query_entries(entries, Category.TRASH, query="4111") == []


def test_index_cache_invalidates_on_modify():
    index = SearchIndex()
    e = sample()[0]
    assert index.matches(e, "beta")
    e.title = "Renamed"
    e.modified += 1
    assert not index.matches(e, "beta")
    index.clear()
    assert index._cache == {}
