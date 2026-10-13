import json
import sqlite3
import threading

import pytest

from lockly.core import migrations
from lockly.core.crypto import KdfParams
from lockly.core.database import DatabaseError, VaultExistsError
from lockly.core.models import CustomField, Entry, EntryType, ValidationError
from lockly.core.vault import (
    EntryNotFoundError,
    Vault,
    VaultError,
    VaultLockedError,
    WrongPasswordError,
)

from .conftest import FAST_KDF, MASTER, OTHER

SECRET_PASSWORD = "Zq8!sample-only-pw-7731"


def make_login(**kwargs):
    fields = {"username": "sample.user", "password": SECRET_PASSWORD, "url": "https://site.example"}
    fields.update(kwargs.pop("fields", {}))
    return Entry(type=EntryType.LOGIN, title=kwargs.pop("title", "Example Site"), fields=fields, **kwargs)


def reopen(path, password=MASTER):
    v = Vault(path)
    v.unlock(password)
    return v


# Creation & unlocking --------------------------------------------------


def test_create_and_unlock(vault, vault_path):
    assert vault.is_unlocked
    vault.lock()
    assert not vault.is_unlocked
    v = reopen(vault_path)
    assert v.is_unlocked
    assert v.entries() == []


def test_file_permissions(vault, vault_path):
    assert (vault_path.stat().st_mode & 0o777) == 0o600
    assert (vault_path.parent.stat().st_mode & 0o777) == 0o700


def test_wrong_password_rejected(vault, vault_path):
    vault.lock()
    v = Vault(vault_path)
    with pytest.raises(WrongPasswordError):
        v.unlock(OTHER)
    assert not v.is_unlocked
    v.unlock(MASTER)
    assert v.is_unlocked


def test_create_refuses_to_overwrite(vault, vault_path):
    vault.add(make_login())
    with pytest.raises(VaultExistsError):
        Vault.create(vault_path, MASTER, FAST_KDF)
    vault.lock()
    assert len(reopen(vault_path).entries()) == 1


def test_short_master_password_rejected(tmp_path):
    with pytest.raises(VaultError):
        Vault.create(tmp_path / "v.lockly", "short", FAST_KDF)
    assert not (tmp_path / "v.lockly").exists()


def test_not_a_vault(tmp_path):
    path = tmp_path / "random.lockly"
    path.write_bytes(b"this is not a database")
    with pytest.raises(DatabaseError):
        Vault(path).unlock(MASTER)


def test_newer_format_refused(vault, vault_path):
    vault.lock()
    conn = sqlite3.connect(vault_path)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'format_version'", (migrations.CURRENT_FORMAT_VERSION + 1,))
    conn.commit()
    conn.close()
    with pytest.raises(migrations.VaultFormatError, match="newer"):
        Vault(vault_path).unlock(MASTER)


def test_tampered_kdf_params_detected(vault, vault_path):
    vault.lock()
    conn = sqlite3.connect(vault_path)
    conn.execute(
        "UPDATE meta SET value = ? WHERE key = 'kdf'",
        (json.dumps(KdfParams(opslimit=1, memlimit=FAST_KDF.memlimit).to_dict()),),
    )
    conn.commit()
    conn.close()
    with pytest.raises(WrongPasswordError):
        Vault(vault_path).unlock(MASTER)


# CRUD ---------------------------------------------------------------------


def test_add_persists(vault, vault_path):
    entry = vault.add(make_login(tags=["work", " ", "Work2"]))
    vault.lock()
    v = reopen(vault_path)
    stored = v.get(entry.id)
    assert stored.title == "Example Site"
    assert stored.get("password") == SECRET_PASSWORD
    assert stored.tags == ["work", "Work2"]


def test_update(vault, vault_path):
    entry = vault.add(make_login())
    edited = entry.copy()
    edited.title = "Renamed"
    edited.fields["password"] = "another-sample-pw"
    edited.custom_fields = [CustomField("PIN", "0000", hidden=True)]
    vault.update(edited)
    vault.lock()
    stored = reopen(vault_path).get(entry.id)
    assert stored.title == "Renamed"
    assert stored.get("password") == "another-sample-pw"
    assert stored.custom_fields[0].hidden
    assert stored.created == entry.created
    assert stored.modified >= entry.modified


def test_trash_restore_delete(vault, vault_path):
    entry = vault.add(make_login())
    vault.trash(entry.id)
    assert vault.get(entry.id).in_trash
    vault.restore(entry.id)
    assert not vault.get(entry.id).in_trash
    vault.trash(entry.id)
    vault.lock()
    v = reopen(vault_path)
    assert v.get(entry.id).in_trash
    v.delete_permanently(entry.id)
    with pytest.raises(EntryNotFoundError):
        v.get(entry.id)
    v.lock()
    assert reopen(vault_path).entries() == []


def test_empty_trash(vault):
    a = vault.add(make_login(title="A"))
    b = vault.add(make_login(title="B"))
    vault.trash(a.id)
    assert vault.empty_trash() == 1
    assert [e.id for e in vault.entries()] == [b.id]


def test_favorite(vault, vault_path):
    entry = vault.add(make_login())
    vault.set_favorite(entry.id, True)
    vault.lock()
    assert reopen(vault_path).get(entry.id).favorite


def test_all_entry_types(vault, vault_path):
    vault.add(Entry(type=EntryType.NOTE, title="Note", fields={"content": "sample note body"}))
    vault.add(Entry(type=EntryType.IDENTITY, title="Me", fields={"email": "me@example.com", "phone": "+1 555 0100"}))
    vault.add(Entry(type=EntryType.CARD, title="Card", fields={"number": "4111111111111111", "expiry": "12/30", "cvv": "123"}))
    vault.lock()
    v = reopen(vault_path)
    by_type = {e.type: e for e in v.entries()}
    assert by_type[EntryType.CARD].get("number") == "4111 1111 1111 1111"
    assert by_type[EntryType.NOTE].get("content") == "sample note body"


@pytest.mark.parametrize(
    "entry",
    [
        Entry(type=EntryType.LOGIN, title="   "),
        Entry(type=EntryType.CARD, title="Card", fields={"number": "12ab"}),
        Entry(type=EntryType.CARD, title="Card", fields={"expiry": "13/30"}),
        Entry(type=EntryType.CARD, title="Card", fields={"cvv": "12345"}),
        Entry(type=EntryType.IDENTITY, title="Me", fields={"email": "not-an-email"}),
        Entry(type=EntryType.LOGIN, title="X", custom_fields=[CustomField("", "value")]),
    ],
)
def test_invalid_input_rejected(vault, entry):
    with pytest.raises(ValidationError):
        vault.add(entry)
    assert vault.entries() == []


def test_unknown_fields_dropped(vault):
    entry = vault.add(Entry(type=EntryType.NOTE, title="N", fields={"content": "c", "password": "x"}))
    assert "password" not in entry.fields


# Locking --------------------------------------------------------------------


def test_locked_vault_refuses_access(vault):
    entry = vault.add(make_login())
    vault.lock()
    with pytest.raises(VaultLockedError):
        vault.entries()
    with pytest.raises(VaultLockedError):
        vault.get(entry.id)
    with pytest.raises(VaultLockedError):
        vault.add(make_login())
    assert vault._key is None
    assert vault._entries == {}


def test_lock_wipes_key_buffer(vault):
    key_ref = vault._key
    vault.lock()
    assert key_ref == bytearray(len(key_ref))


def test_lock_emits_event_and_bumps_generation(vault):
    events = []
    vault.connect(lambda event, entry_id: events.append(event))
    gen = vault.generation
    vault.lock()
    assert events == ["locked"]
    assert vault.generation == gen + 1


def test_lock_during_unlock_cancels(vault, vault_path, monkeypatch):
    vault.lock()
    v = Vault(vault_path)
    original = v._unwrap

    def slow_unwrap(header, password):
        v.lock()  # simulates auto-lock / quit while Argon2 is running
        return original(header, password)

    monkeypatch.setattr(v, "_unwrap", slow_unwrap)
    with pytest.raises(VaultLockedError):
        v.unlock(MASTER)
    assert not v.is_unlocked


def test_verify_password(vault):
    assert vault.verify_password(MASTER)
    assert not vault.verify_password(OTHER)


def test_change_password(vault, vault_path):
    entry = vault.add(make_login())
    new_password = "a different sample master"
    with pytest.raises(WrongPasswordError):
        vault.change_password(OTHER, new_password)
    vault.change_password(MASTER, new_password)
    vault.lock()
    with pytest.raises(WrongPasswordError):
        reopen(vault_path, MASTER)
    v = reopen(vault_path, new_password)
    assert v.get(entry.id).get("password") == SECRET_PASSWORD


def test_change_kdf_params(vault, vault_path):
    stronger = KdfParams(opslimit=3, memlimit=16 * 1024 * 1024)
    vault.change_password(MASTER, MASTER, stronger)
    vault.lock()
    v = Vault(vault_path)
    assert v.read_header().kdf == stronger
    v.unlock(MASTER)


def test_needs_kdf_upgrade(vault):
    assert vault.needs_kdf_upgrade()  # test params are below the default


def test_concurrent_reads(vault):
    for i in range(20):
        vault.add(make_login(title=f"E{i}"))
    errors = []

    def reader():
        try:
            for _ in range(50):
                assert len(vault.entries()) == 20
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
