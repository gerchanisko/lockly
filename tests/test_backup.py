import base64
import csv
import io
import json

import pytest

from lockly.core import backup
from lockly.core.backup import BackupError
from lockly.core.models import Entry, EntryType
from lockly.core.vault import Vault

from .conftest import FAST_KDF, MASTER, OTHER

BACKUP_PW = "sample backup password"


def entries():
    return [
        Entry(type=EntryType.LOGIN, title="One", fields={"username": "u1", "password": "MARKER-pw-1"}),
        Entry(type=EntryType.NOTE, title="Two", fields={"content": "MARKER-note"}),
    ]


def test_round_trip():
    data = backup.create_backup(entries(), BACKUP_PW, "test", FAST_KDF)
    assert b"MARKER" not in data
    assert backup.is_backup_file(data)
    contents = backup.read_backup(data, BACKUP_PW)
    assert [e.title for e in contents.entries] == ["One", "Two"]
    assert contents.entries[0].get("password") == "MARKER-pw-1"


def test_wrong_password():
    data = backup.create_backup(entries(), BACKUP_PW, "test", FAST_KDF)
    with pytest.raises(BackupError, match="Wrong backup password"):
        backup.read_backup(data, OTHER)


@pytest.mark.parametrize("field", ["ciphertext", "nonce", "salt"])
def test_tampering_detected(field):
    doc = json.loads(backup.create_backup(entries(), BACKUP_PW, "test", FAST_KDF))
    raw = bytearray(base64.b64decode(doc[field]))
    raw[0] ^= 1
    doc[field] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(BackupError):
        backup.read_backup(json.dumps(doc).encode(), BACKUP_PW)


def test_header_tampering_detected():
    doc = json.loads(backup.create_backup(entries(), BACKUP_PW, "test", FAST_KDF))
    doc["kdf"]["opslimit"] = 3
    with pytest.raises(BackupError):
        backup.read_backup(json.dumps(doc).encode(), BACKUP_PW)


@pytest.mark.parametrize(
    "data",
    [b"", b"not json", b"[]", b'{"format": "other"}', b'{"format": "lockly-backup", "version": 99}',
     b'{"format": "lockly-backup", "version": 1, "kdf": {}}', b"\xff\xfe"],
)
def test_invalid_files(data):
    with pytest.raises(BackupError):
        backup.read_backup(data, BACKUP_PW)
    assert not backup.is_backup_file(data) or data.startswith(b'{"format": "lockly-backup", "version": 1')


def test_short_backup_password():
    with pytest.raises(BackupError):
        backup.create_backup(entries(), "short", "test", FAST_KDF)


def test_write_backup_atomic_no_overwrite(tmp_path):
    target = tmp_path / "b.lockly-backup"
    backup.write_backup(target, b"first")
    with pytest.raises(FileExistsError):
        backup.write_backup(target, b"second")
    assert target.read_bytes() == b"first"
    backup.write_backup(target, b"second", overwrite=True)
    assert target.read_bytes() == b"second"
    assert (target.stat().st_mode & 0o777) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["b.lockly-backup"]


def test_restore_into_vault(tmp_path):
    source = Vault.create(tmp_path / "a.lockly", MASTER, FAST_KDF)
    for e in entries():
        source.add(e)
    data = backup.create_backup(source.entries(), BACKUP_PW, "test", FAST_KDF)

    target = Vault.create(tmp_path / "b.lockly", MASTER, FAST_KDF)
    target.add(Entry(type=EntryType.NOTE, title="Existing", fields={"content": "x"}))
    contents = backup.read_backup(data, BACKUP_PW)
    assert target.import_entries(contents.entries) == (2, 0, 0)
    # Importing the same backup again changes nothing.
    assert target.import_entries(contents.entries) == (0, 0, 2)
    target.lock()
    restored = Vault(tmp_path / "b.lockly")
    restored.unlock(MASTER)
    assert sorted(e.title for e in restored.entries()) == ["Existing", "One", "Two"]


def test_import_newer_version_wins(vault):
    original = vault.add(Entry(type=EntryType.NOTE, title="Old", fields={"content": "x"}))
    newer = original.copy()
    newer.title = "New"
    newer.modified = original.modified + 100
    assert vault.import_entries([newer]) == (0, 1, 0)
    assert vault.get(original.id).title == "New"


def test_import_is_atomic_on_invalid_entry(vault):
    bad = Entry(type=EntryType.LOGIN, title="")
    with pytest.raises(Exception):
        vault.import_entries([Entry(type=EntryType.NOTE, title="ok"), bad])
    assert vault.entries() == []


def test_csv_export():
    items = entries() + [Entry(type=EntryType.LOGIN, title="=cmd|' /C calc'!A0", fields={"username": "+evil"})]
    items.append(Entry(type=EntryType.NOTE, title="Trashed", deleted_at=1.0))
    rows = list(csv.DictReader(io.StringIO(backup.export_csv(items).decode())))
    assert [r["title"] for r in rows] == ["One", "Two", "'=cmd|' /C calc'!A0"]
    assert rows[0]["password"] == "MARKER-pw-1"
    assert rows[2]["username"] == "'+evil"
