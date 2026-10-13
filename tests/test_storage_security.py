"""Checks that nothing sensitive reaches the database file in plaintext and
that tampering with stored ciphertext is detected."""

import sqlite3

import pytest

from lockly.core.models import CustomField, Entry, EntryType
from lockly.core.vault import Vault, VaultError

from .conftest import MASTER

MARKERS = {
    "password": "PLAINTEXT-MARKER-pw-9f3a",
    "username": "PLAINTEXT-MARKER-user-1c7e",
    "title": "PLAINTEXT-MARKER-title-55b2",
    "note": "PLAINTEXT-MARKER-note-a0d4",
    "card": "4111111111111111",
    "custom": "PLAINTEXT-MARKER-custom-77e1",
    "tag": "PLAINTEXT-MARKER-tag-3b9c",
}


def _all_bytes(path):
    data = path.read_bytes()
    for sidecar in path.parent.glob(path.name + "-*"):
        data += sidecar.read_bytes()
    return data


def _populate(vault):
    vault.add(
        Entry(
            type=EntryType.LOGIN,
            title=MARKERS["title"],
            fields={"username": MARKERS["username"], "password": MARKERS["password"], "notes": MARKERS["note"]},
            custom_fields=[CustomField("Secret", MARKERS["custom"], hidden=True)],
            tags=[MARKERS["tag"]],
            favorite=True,
        )
    )
    vault.add(Entry(type=EntryType.CARD, title="Card", fields={"number": MARKERS["card"]}))


def test_no_plaintext_in_database(vault, vault_path):
    _populate(vault)
    vault.lock()
    raw = _all_bytes(vault_path)
    for marker in MARKERS.values():
        assert marker.encode() not in raw
    assert b"4111 1111" not in raw
    assert MASTER.encode() not in raw
    # Entry type names and flags are encrypted too.
    for word in (b"login", b"favorite", b"card", b"deleted_at"):
        assert word not in raw


def test_no_plaintext_after_edit_and_delete(vault, vault_path):
    _populate(vault)
    for entry in vault.entries():
        vault.trash(entry.id)
        vault.delete_permanently(entry.id)
    vault.lock()
    raw = _all_bytes(vault_path)
    for marker in MARKERS.values():
        assert marker.encode() not in raw


def test_master_password_not_stored(vault, vault_path):
    vault.lock()
    conn = sqlite3.connect(vault_path)
    keys = {row[0] for row in conn.execute("SELECT key FROM meta")}
    conn.close()
    assert keys == {"format_version", "vault_id", "kdf", "salt", "key_nonce", "wrapped_key"}
    assert MASTER.encode() not in vault_path.read_bytes()


def _corrupt(path, sql, params=()):
    conn = sqlite3.connect(path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def test_modified_entry_ciphertext_detected(vault, vault_path):
    _populate(vault)
    vault.lock()
    conn = sqlite3.connect(vault_path)
    entry_id, ct = conn.execute("SELECT id, ciphertext FROM entries LIMIT 1").fetchone()
    conn.close()
    flipped = bytearray(ct)
    flipped[len(flipped) // 2] ^= 0xFF
    _corrupt(vault_path, "UPDATE entries SET ciphertext = ? WHERE id = ?", (bytes(flipped), entry_id))
    with pytest.raises(VaultError, match="integrity"):
        Vault(vault_path).unlock(MASTER)


def test_swapped_ciphertexts_detected(vault, vault_path):
    """Ciphertext is bound to its entry id; moving it to another row fails."""
    _populate(vault)
    vault.lock()
    conn = sqlite3.connect(vault_path)
    (a_id, a_nonce, a_ct), (b_id, b_nonce, b_ct) = conn.execute("SELECT id, nonce, ciphertext FROM entries").fetchall()
    conn.execute("UPDATE entries SET nonce = ?, ciphertext = ? WHERE id = ?", (b_nonce, b_ct, a_id))
    conn.commit()
    conn.close()
    with pytest.raises(VaultError):
        Vault(vault_path).unlock(MASTER)


def test_ciphertext_from_other_vault_rejected(tmp_path, fast_kdf):
    a = Vault.create(tmp_path / "a.lockly", MASTER, fast_kdf)
    b = Vault.create(tmp_path / "b.lockly", MASTER, fast_kdf)
    entry = a.add(Entry(type=EntryType.NOTE, title="N", fields={"content": "x"}))
    a.lock()
    b.lock()
    conn = sqlite3.connect(tmp_path / "a.lockly")
    row = conn.execute("SELECT id, nonce, ciphertext FROM entries").fetchone()
    conn.close()
    _corrupt(tmp_path / "b.lockly", "INSERT INTO entries VALUES (?, ?, ?)", row)
    with pytest.raises(VaultError):
        Vault(tmp_path / "b.lockly").unlock(MASTER)
    assert row[0] == entry.id


def test_truncated_wrapped_key_rejected(vault, vault_path):
    vault.lock()
    _corrupt(vault_path, "UPDATE meta SET value = X'00' WHERE key = 'wrapped_key'")
    with pytest.raises(VaultError):
        Vault(vault_path).unlock(MASTER)


def test_padding_hides_exact_length(vault, vault_path):
    vault.add(Entry(type=EntryType.NOTE, title="a", fields={"content": "x"}))
    vault.add(Entry(type=EntryType.NOTE, title="b", fields={"content": "x" * 40}))
    vault.lock()
    conn = sqlite3.connect(vault_path)
    lengths = {len(r[0]) for r in conn.execute("SELECT ciphertext FROM entries")}
    conn.close()
    assert len(lengths) == 1
