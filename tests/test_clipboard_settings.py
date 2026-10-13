import json

from lockly.core.clipboard import ClearResult, ClipboardGuard
from lockly.core.models import format_card_number, luhn_valid, mask_card_number, parse_expiry
from lockly.core.settings import Settings, SettingsStore


class FakeBackend:
    def __init__(self):
        self.content = None
        self.owner = None
        self.fail = False

    def set_text(self, text, sensitive):
        self.content = text
        self.owner = object()
        return self.owner

    def owns(self, token):
        return token is self.owner

    def clear(self):
        if self.fail:
            return False
        self.content = None
        self.owner = None
        return True

    def other_app_copies(self):
        self.content = "something else"
        self.owner = object()


class FakeScheduler:
    def __init__(self):
        self.pending = []

    def __call__(self, delay, callback):
        item = [delay, callback, True]
        self.pending.append(item)

        def cancel():
            item[2] = False

        return cancel

    def fire(self):
        for item in list(self.pending):
            if item[2]:
                item[2] = False
                item[1]()


def make():
    backend, scheduler, results = FakeBackend(), FakeScheduler(), []
    return backend, scheduler, results, ClipboardGuard(backend, scheduler, results.append)


def test_clears_after_timeout():
    backend, scheduler, results, guard = make()
    guard.copy("sample-secret", sensitive=True, clear_after=30)
    assert backend.content == "sample-secret"
    assert scheduler.pending[0][0] == 30
    scheduler.fire()
    assert backend.content is None
    assert results == [ClearResult.CLEARED]


def test_does_not_clear_foreign_content():
    backend, scheduler, results, guard = make()
    guard.copy("sample-secret", sensitive=True, clear_after=30)
    backend.other_app_copies()
    scheduler.fire()
    assert backend.content == "something else"
    assert results == [ClearResult.REPLACED]


def test_reports_failure_honestly():
    backend, scheduler, results, guard = make()
    backend.fail = True
    guard.copy("sample-secret", sensitive=True, clear_after=10)
    scheduler.fire()
    assert results == [ClearResult.FAILED]


def test_new_copy_cancels_previous_timer():
    backend, scheduler, results, guard = make()
    guard.copy("first", sensitive=True, clear_after=10)
    guard.copy("second", sensitive=True, clear_after=10)
    scheduler.fire()
    assert results == [ClearResult.CLEARED]


def test_never_clear_option():
    backend, scheduler, results, guard = make()
    guard.copy("sample-secret", sensitive=True, clear_after=0)
    assert scheduler.pending == []
    assert guard.clear() is ClearResult.CLEARED  # e.g. on lock


def test_non_sensitive_not_tracked():
    backend, scheduler, results, guard = make()
    guard.copy("username", sensitive=False, clear_after=30)
    assert not guard.holds_secret
    assert guard.clear() is ClearResult.NOTHING
    assert backend.content == "username"


def test_settings_round_trip(tmp_path):
    store = SettingsStore(tmp_path / "cfg" / "settings.json")
    store.settings.auto_lock_minutes = 15
    store.settings.password.length = 32
    store.save()
    data = json.loads((tmp_path / "cfg" / "settings.json").read_text())
    assert data["auto_lock_minutes"] == 15
    assert SettingsStore(tmp_path / "cfg" / "settings.json").settings.password.length == 32


def test_settings_reject_garbage(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"auto_lock_minutes": 7, "color_scheme": "neon", "password": {"length": 2}}')
    s = SettingsStore(path).settings
    assert s.auto_lock_minutes == 5 and s.color_scheme == "dark" and s.password.length == 8
    path.write_text("not json")
    assert SettingsStore(path).settings == Settings()


def test_card_helpers():
    assert format_card_number("4111-1111 11111111") == "4111 1111 1111 1111"
    assert mask_card_number("4111 1111 1111 1234") == "•••• 1234"
    assert luhn_valid("4111 1111 1111 1111")
    assert not luhn_valid("4111 1111 1111 1112")
    assert parse_expiry("7/2031") == (7, 2031)
    assert parse_expiry("13/30") is None
