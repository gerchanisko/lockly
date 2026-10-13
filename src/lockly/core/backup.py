"""Encrypted backups and (explicitly confirmed) plaintext CSV export.

Encrypted backup file (``.lockly-backup``), a UTF-8 JSON document::

    {
      "format": "lockly-backup",
      "version": 1,
      "kdf": {"algorithm": "argon2id13", "opslimit": ..., "memlimit": ...},
      "salt": "<base64>",
      "nonce": "<base64>",
      "ciphertext": "<base64>"
    }

The payload is the JSON list of entries, encrypted with XChaCha20-Poly1305
under a key derived with Argon2id from a backup password chosen at export
time. Every header field above (except the ciphertext itself) is bound into
the AEAD as associated data, so tampering is detected.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path

from . import crypto
from .crypto import CryptoError, KdfParams
from .database import atomic_write
from .models import FIELD_SPECS, Entry

BACKUP_FORMAT = "lockly-backup"
BACKUP_VERSION = 1
BACKUP_SUFFIX = ".lockly-backup"
MAX_BACKUP_SIZE = 64 * 1024 * 1024
BACKUP_DOMAIN = b"lockly/backup\x00"


class BackupError(Exception):
    pass


@dataclass
class BackupContents:
    entries: list[Entry]
    exported_at: float
    app_version: str


def _aad(header: dict) -> bytes:
    return BACKUP_DOMAIN + json.dumps(header, sort_keys=True, separators=(",", ":")).encode()


def create_backup(
    entries: list[Entry],
    password: str,
    app_version: str,
    kdf: KdfParams = crypto.DEFAULT_KDF,
) -> bytes:
    if len(password) < 8:
        raise BackupError("The backup password must be at least 8 characters long")
    salt = crypto.new_salt()
    key = crypto.derive_key(password, salt, kdf)
    header = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "kdf": kdf.to_dict(),
        "salt": base64.b64encode(salt).decode(),
    }
    payload = json.dumps(
        {
            "exported_at": time.time(),
            "app_version": app_version,
            "entries": [e.to_dict() for e in entries],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        nonce, ciphertext = crypto.encrypt(key, payload, _aad(header))
    finally:
        crypto.wipe(key)
    document = dict(header)
    document["nonce"] = base64.b64encode(nonce).decode()
    document["ciphertext"] = base64.b64encode(ciphertext).decode()
    return json.dumps(document, indent=2).encode("utf-8")


def write_backup(path: Path, data: bytes, *, overwrite: bool = False) -> None:
    atomic_write(Path(path), data, overwrite=overwrite)


def _parse_document(data: bytes) -> dict:
    if len(data) > MAX_BACKUP_SIZE:
        raise BackupError("The file is too large to be a Lockly backup")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("The file is not a Lockly backup") from exc
    if not isinstance(document, dict) or document.get("format") != BACKUP_FORMAT:
        raise BackupError("The file is not a Lockly backup")
    version = document.get("version")
    if version != BACKUP_VERSION:
        raise BackupError(f"Unsupported backup version {version!r}. It may have been created by a newer Lockly.")
    return document


def read_backup(data: bytes, password: str) -> BackupContents:
    """Validate and decrypt a backup. Nothing is imported here."""
    document = _parse_document(data)
    try:
        kdf = KdfParams.from_dict(document["kdf"])
        salt = base64.b64decode(document["salt"], validate=True)
        nonce = base64.b64decode(document["nonce"], validate=True)
        ciphertext = base64.b64decode(document["ciphertext"], validate=True)
    except (KeyError, TypeError, binascii.Error, CryptoError) as exc:
        raise BackupError("The backup file is damaged") from exc
    if len(salt) != crypto.SALT_SIZE:
        raise BackupError("The backup file is damaged")
    header = {k: document[k] for k in ("format", "version", "kdf", "salt")}
    key = crypto.derive_key(password, salt, kdf)
    try:
        payload = crypto.decrypt(key, nonce, ciphertext, _aad(header))
    except CryptoError:
        raise BackupError("Wrong backup password, or the file has been modified") from None
    finally:
        crypto.wipe(key)
    try:
        body = json.loads(payload.decode("utf-8"))
        entries = [Entry.from_dict(item) for item in body["entries"]]
        for entry in entries:
            entry.normalise()
            entry.validate()
    except Exception as exc:
        raise BackupError("The backup contents are invalid") from exc
    ids = [e.id for e in entries]
    if len(ids) != len(set(ids)):
        raise BackupError("The backup contains duplicate entries")
    return BackupContents(
        entries=entries,
        exported_at=float(body.get("exported_at", 0)),
        app_version=str(body.get("app_version", "")),
    )


def is_backup_file(data: bytes) -> bool:
    try:
        _parse_document(data)
    except BackupError:
        return False
    return True


# Plaintext CSV ----------------------------------------------------------

CSV_COLUMNS = [
    "type", "title", "username", "password", "url", "content", "full_name", "email",
    "phone", "address", "cardholder", "number", "expiry", "cvv", "notes", "tags",
    "favorite", "custom_fields",
]


def _csv_safe(value: str) -> str:
    # Prevent spreadsheet formula injection when the file is opened in
    # LibreOffice Calc / Excel.
    if value and value[0] in "=+-@\t\r":
        return "'" + value
    return value


def export_csv(entries: list[Entry]) -> bytes:
    """Serialise entries as UNENCRYPTED CSV.

    The UI only calls this after explicit confirmation and re-authentication.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for entry in entries:
        if entry.in_trash:
            continue
        row = {"type": entry.type.value, "title": entry.title}
        for spec in FIELD_SPECS[entry.type]:
            row[spec.key] = entry.get(spec.key)
        row["tags"] = ", ".join(entry.tags)
        row["favorite"] = "yes" if entry.favorite else ""
        row["custom_fields"] = "; ".join(f"{c.name}={c.value}" for c in entry.custom_fields)
        writer.writerow({k: _csv_safe(str(v)) for k, v in row.items()})
    return buffer.getvalue().encode("utf-8")
