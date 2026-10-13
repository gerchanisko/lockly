"""Vault format migrations.

Format 1 is the only released format, so the migration *chain* is exercised by
registering temporary steps that pretend the current format is newer.
"""

import json
import sqlite3

import pytest

from lockly.core import migrations
from lockly.core.models import Entry, EntryType
from lockly.core.vault import Vault, WrongPasswordError, entry_aad

from .conftest import MASTER, OTHER


@pytest.fixture
def future_format(monkeypatch):
    """Simulate a Lockly release with format 3 and two migration steps."""
    calls = []

    def v1_to_v2(ctx):
        calls.append((1, ctx.vault_id))
        ctx.conn.execute("INSERT INTO meta(key, value) VALUES ('v2_marker', 'yes')")

    def v2_to_v3(ctx):
        calls.append((2, ctx.vault_id))
        # A real step may need the vault key, e.g. to re-encrypt entries.
        from lockly.core import crypto

        for entry_id, nonce, ct in ctx.conn.execute("SELECT id, nonce, ciphertext FROM entries").fetchall():
            plain = crypto.decrypt(ctx.vault_key, nonce, ct, entry_aad(ctx.vault_id, entry_id))
            n2, c2 = crypto.encrypt(ctx.vault_key, plain, entry_aad(ctx.vault_id, entry_id))
            ctx.conn.execute("UPDATE entries SET nonce = ?, ciphertext = ? WHERE id = ?", (n2, c2, entry_id))

    monkeypatch.setattr(migrations, "CURRENT_FORMAT_VERSION", 3)
    monkeypatch.setattr(migrations, "MIGRATIONS", {1: v1_to_v2, 2: v2_to_v3})
    return calls


def _version(path):
    conn = sqlite3.connect(path)
    value = conn.execute("SELECT value FROM meta WHERE key = 'format_version'").fetchone()[0]
    conn.close()
    return int(value)


def test_current_format_has_no_pending_migrations():
    migrations.check_supported(migrations.CURRENT_FORMAT_VERSION)
    assert not migrations.needs_migration(migrations.CURRENT_FORMAT_VERSION)


def test_invalid_version():
    with pytest.raises(migrations.VaultFormatError):
        migrations.check_supported(0)


def test_migration_chain_runs_in_order(vault, vault_path, future_format):
    entry = vault.add(Entry(type=EntryType.NOTE, title="Keep me", fields={"content": "data"}))
    vault.lock()
    assert _version(vault_path) == 1

    v = Vault(vault_path)
    v.unlock(MASTER)
    assert [c[0] for c in future_format] == [1, 2]
    assert _version(vault_path) == 3
    assert v.get(entry.id).title == "Keep me"
    backups = list(vault_path.parent.glob("*.format-v1.bak"))
    assert len(backups) == 1
    assert (backups[0].stat().st_mode & 0o777) == 0o600

    # Reopening does not migrate again.
    v.lock()
    future_format.clear()
    Vault(vault_path).unlock(MASTER)
    assert future_format == []


def test_no_migration_with_wrong_password(vault, vault_path, future_format):
    vault.lock()
    with pytest.raises(WrongPasswordError):
        Vault(vault_path).unlock(OTHER)
    assert future_format == []
    assert _version(vault_path) == 1
    assert not list(vault_path.parent.glob("*.bak"))


def test_failed_migration_rolls_back(vault, vault_path, monkeypatch):
    vault.add(Entry(type=EntryType.NOTE, title="Keep me", fields={"content": "data"}))
    vault.lock()

    def broken(ctx):
        ctx.conn.execute("DELETE FROM entries")
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(migrations, "CURRENT_FORMAT_VERSION", 2)
    monkeypatch.setattr(migrations, "MIGRATIONS", {1: broken})
    v = Vault(vault_path)
    with pytest.raises(RuntimeError):
        v.unlock(MASTER)
    assert not v.is_unlocked
    assert _version(vault_path) == 1

    monkeypatch.setattr(migrations, "CURRENT_FORMAT_VERSION", 1)
    monkeypatch.setattr(migrations, "MIGRATIONS", {})
    v = Vault(vault_path)
    v.unlock(MASTER)
    assert [e.title for e in v.entries()] == ["Keep me"]


def test_missing_migration_step(vault, vault_path, monkeypatch):
    vault.lock()
    monkeypatch.setattr(migrations, "CURRENT_FORMAT_VERSION", 2)
    monkeypatch.setattr(migrations, "MIGRATIONS", {})
    with pytest.raises(migrations.VaultFormatError, match="No migration"):
        Vault(vault_path).unlock(MASTER)


def test_damaged_header(vault, vault_path):
    vault.lock()
    conn = sqlite3.connect(vault_path)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'kdf'", (json.dumps({"broken": True}),))
    conn.commit()
    conn.close()
    with pytest.raises(migrations.VaultFormatError):
        Vault(vault_path).unlock(MASTER)
