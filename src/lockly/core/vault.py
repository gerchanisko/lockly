"""The vault: key management, locking and encrypted entry storage.

Key hierarchy::

    master password ──Argon2id(salt, params)──▶ KEK (key-encryption key)
    KEK ──XChaCha20-Poly1305──▶ wraps a random 256-bit vault key
    vault key ──XChaCha20-Poly1305──▶ each entry (JSON, padded)

A wrong master password produces a different KEK, so unwrapping the vault key
fails authentication and the password is rejected. Changing the master
password only re-wraps the vault key; entries are not re-encrypted.
"""

from __future__ import annotations

import base64
import gc
import json
import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from nacl import bindings

from . import crypto, migrations
from .crypto import CryptoError, KdfParams
from .database import Database, DatabaseError, VaultExistsError
from .models import Entry

log = logging.getLogger(__name__)

PAD_BLOCK = 256
KEY_WRAP_DOMAIN = b"lockly/vault-key\x00"
ENTRY_DOMAIN = b"lockly/entry\x00"


class VaultError(Exception):
    pass


class VaultLockedError(VaultError):
    pass


class WrongPasswordError(VaultError):
    pass


class EntryNotFoundError(VaultError):
    pass


__all__ = [
    "Vault",
    "VaultError",
    "VaultLockedError",
    "WrongPasswordError",
    "EntryNotFoundError",
    "VaultExistsError",
    "DatabaseError",
]

MIN_PASSWORD_LENGTH = 8


@dataclass(frozen=True)
class VaultHeader:
    format_version: int
    vault_id: str
    kdf: KdfParams
    salt: bytes
    key_nonce: bytes
    wrapped_key: bytes

    @classmethod
    def from_meta(cls, meta: dict) -> "VaultHeader":
        try:
            header = cls(
                format_version=int(meta["format_version"]),
                vault_id=str(meta["vault_id"]),
                kdf=KdfParams.from_dict(json.loads(meta["kdf"])),
                salt=bytes(meta["salt"]),
                key_nonce=bytes(meta["key_nonce"]),
                wrapped_key=bytes(meta["wrapped_key"]),
            )
        except CryptoError as exc:
            raise migrations.VaultFormatError(str(exc)) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise migrations.VaultFormatError("The vault header is missing or damaged") from exc
        if len(header.salt) != crypto.SALT_SIZE or len(header.key_nonce) != crypto.NONCE_SIZE:
            raise migrations.VaultFormatError("The vault header is damaged")
        return header

    def key_wrap_aad(self) -> bytes:
        return key_wrap_aad(self.vault_id, self.kdf, self.salt)


def key_wrap_aad(vault_id: str, kdf: KdfParams, salt: bytes) -> bytes:
    # Binding the KDF parameters and salt means tampering with them is
    # detected rather than silently producing a different key.
    descriptor = json.dumps(
        {"vault_id": vault_id, "kdf": kdf.to_dict(), "salt": base64.b64encode(salt).decode()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return KEY_WRAP_DOMAIN + descriptor


def entry_aad(vault_id: str, entry_id: str) -> bytes:
    return ENTRY_DOMAIN + vault_id.encode() + b"\x00" + entry_id.encode()


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise VaultError(f"The master password must be at least {MIN_PASSWORD_LENGTH} characters long")


class Vault:
    """A single encrypted vault file.

    All public methods are thread safe. Key derivation (``unlock``,
    ``verify_password``, ``change_password``) is slow by design and should be
    called off the UI thread.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._db: Database | None = None
        self._header: VaultHeader | None = None
        self._key: bytearray | None = None
        self._entries: dict[str, Entry] = {}
        self._listeners: list[Callable[[str, str | None], None]] = []
        # Incremented on every lock. Long-running operations capture it and
        # discard their result if the vault was locked in the meantime.
        self.generation = 0

    # Creation ------------------------------------------------------------
    @staticmethod
    def exists(path: Path | str) -> bool:
        return Path(path).exists()

    @classmethod
    def create(cls, path: Path | str, password: str, kdf: KdfParams = crypto.DEFAULT_KDF) -> "Vault":
        """Create a new vault and return it unlocked. Never overwrites."""
        check_password_policy(password)
        path = Path(path)
        if path.exists():
            raise VaultExistsError(f"A vault already exists at {path}")
        vault_id = crypto.random_bytes(16).hex()
        salt = crypto.new_salt()
        kek = crypto.derive_key(password, salt, kdf)
        vault_key = crypto.new_key()
        try:
            nonce, wrapped = crypto.encrypt(kek, bytes(vault_key), key_wrap_aad(vault_id, kdf, salt))
        finally:
            crypto.wipe(kek)
        meta = {
            "format_version": migrations.CURRENT_FORMAT_VERSION,
            "vault_id": vault_id,
            "kdf": json.dumps(kdf.to_dict(), sort_keys=True),
            "salt": salt,
            "key_nonce": nonce,
            "wrapped_key": wrapped,
        }
        db = Database.create(path, meta)
        vault = cls(path)
        vault._db = db
        vault._header = VaultHeader.from_meta(db.get_meta())
        vault._key = vault_key
        vault._entries = {}
        log.info("Created new vault")
        return vault

    # Header --------------------------------------------------------------
    def _open_db(self) -> Database:
        if self._db is None:
            self._db = Database.open(self.path)
        return self._db

    def read_header(self) -> VaultHeader:
        with self._lock:
            header = VaultHeader.from_meta(self._open_db().get_meta())
            migrations.check_supported(header.format_version)
            self._header = header
            return header

    @property
    def kdf_params(self) -> KdfParams:
        return (self._header or self.read_header()).kdf

    def needs_kdf_upgrade(self) -> bool:
        current = self.kdf_params
        default = crypto.DEFAULT_KDF
        return current.opslimit < default.opslimit or current.memlimit < default.memlimit

    # Locking -------------------------------------------------------------
    @property
    def is_unlocked(self) -> bool:
        return self._key is not None

    def _unwrap(self, header: VaultHeader, password: str) -> bytearray:
        kek = crypto.derive_key(password, header.salt, header.kdf)
        try:
            raw = crypto.decrypt(kek, header.key_nonce, header.wrapped_key, header.key_wrap_aad())
        except CryptoError:
            raise WrongPasswordError("Incorrect master password") from None
        finally:
            crypto.wipe(kek)
        key = bytearray(raw)
        del raw
        if len(key) != crypto.KEY_SIZE:
            crypto.wipe(key)
            raise migrations.VaultFormatError("The vault key has an invalid length")
        return key

    def unlock(self, password: str) -> None:
        with self._lock:
            if self.is_unlocked:
                return
            header = self.read_header()
            generation = self.generation
        # Key derivation runs without holding the lock so that lock() stays
        # responsive; a lock() in the meantime cancels this unlock.
        key = self._unwrap(header, password)
        with self._lock:
            if generation != self.generation or self.is_unlocked:
                crypto.wipe(key)
                raise VaultLockedError("Unlock was cancelled")
            try:
                if migrations.needs_migration(header.format_version):
                    self._migrate(header, key)
                    header = self.read_header()
                entries = self._load_entries(header.vault_id, key)
            except BaseException:
                crypto.wipe(key)
                raise
            self._key = key
            self._entries = entries
            log.info("Vault unlocked")
        self._emit("unlocked", None)

    def _migrate(self, header: VaultHeader, key: bytearray) -> None:
        db = self._open_db()
        backup = migrations.backup_before_migration(self.path, header.format_version)
        log.info("Migrating vault from format %d (backup at %s)", header.format_version, backup)
        with db.transaction() as conn:
            ctx = migrations.MigrationContext(conn=conn, vault_key=key, vault_id=header.vault_id)
            new_version = migrations.run_migrations(ctx, header.format_version)
            db.set_meta({"format_version": new_version}, conn)

    def _load_entries(self, vault_id: str, key: bytearray) -> dict[str, Entry]:
        entries: dict[str, Entry] = {}
        for entry_id, nonce, ciphertext in self._open_db().iter_entries():
            try:
                padded = crypto.decrypt(key, nonce, ciphertext, entry_aad(vault_id, entry_id))
                plaintext = bindings.sodium_unpad(padded, PAD_BLOCK)
                entry = Entry.from_dict(json.loads(plaintext.decode("utf-8")))
            except Exception as exc:
                # Do not include any decrypted content in the message.
                raise VaultError(
                    "An entry in the vault failed its integrity check. The file may be "
                    "corrupted or has been modified."
                ) from exc
            if entry.id != entry_id:
                raise VaultError("An entry in the vault failed its integrity check.")
            entries[entry.id] = entry
        return entries

    def lock(self) -> None:
        with self._lock:
            was_unlocked = self.is_unlocked
            self.generation += 1
            crypto.wipe(self._key)
            self._key = None
            self._entries.clear()
            self._entries = {}
            if self._db is not None:
                self._db.close()
                self._db = None
        gc.collect()
        if was_unlocked:
            log.info("Vault locked")
            self._emit("locked", None)

    close = lock

    def verify_password(self, password: str) -> bool:
        """Re-authenticate the user without changing state."""
        with self._lock:
            header = self._header or self.read_header()
        try:
            key = self._unwrap(header, password)
        except WrongPasswordError:
            return False
        crypto.wipe(key)
        return True

    def change_password(self, current: str, new: str, kdf: KdfParams | None = None) -> None:
        """Re-wrap the vault key under a new password and/or KDF parameters."""
        check_password_policy(new)
        kdf = kdf or self.kdf_params
        kdf.validate()
        with self._lock:
            self._require_unlocked()
            header = self._header or self.read_header()
            generation = self.generation
        old_key = self._unwrap(header, current)
        salt = crypto.new_salt()
        kek = crypto.derive_key(new, salt, kdf)
        try:
            with self._lock:
                if generation != self.generation or self._key is None:
                    raise VaultLockedError("The vault was locked before the password could be changed")
                if not crypto.constant_time_equal(bytes(old_key), bytes(self._key)):
                    raise VaultError("Vault key mismatch")
                nonce, wrapped = crypto.encrypt(kek, bytes(self._key), key_wrap_aad(header.vault_id, kdf, salt))
                self._open_db().set_meta(
                    {
                        "kdf": json.dumps(kdf.to_dict(), sort_keys=True),
                        "salt": salt,
                        "key_nonce": nonce,
                        "wrapped_key": wrapped,
                    }
                )
                self.read_header()
                log.info("Master password / key derivation parameters changed")
        finally:
            crypto.wipe(kek)
            crypto.wipe(old_key)
        self._emit("password-changed", None)

    # Listeners -----------------------------------------------------------
    def connect(self, callback: Callable[[str, str | None], None]) -> None:
        self._listeners.append(callback)

    def disconnect(self, callback: Callable[[str, str | None], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _emit(self, event: str, entry_id: str | None) -> None:
        for callback in list(self._listeners):
            try:
                callback(event, entry_id)
            except Exception:  # pragma: no cover - listener bugs must not break the vault
                log.exception("Vault listener failed")

    # Entries -------------------------------------------------------------
    def _require_unlocked(self) -> None:
        if self._key is None:
            raise VaultLockedError("The vault is locked")

    def _write(self, entry: Entry, conn=None) -> None:
        assert self._key is not None and self._header is not None
        plaintext = json.dumps(entry.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        padded = bindings.sodium_pad(plaintext, PAD_BLOCK)
        nonce, ciphertext = crypto.encrypt(self._key, padded, entry_aad(self._header.vault_id, entry.id))
        self._open_db().put_entry(entry.id, nonce, ciphertext, conn)

    def entries(self, include_trash: bool = True) -> list[Entry]:
        with self._lock:
            self._require_unlocked()
            return [e for e in self._entries.values() if include_trash or not e.in_trash]

    def get(self, entry_id: str) -> Entry:
        with self._lock:
            self._require_unlocked()
            try:
                return self._entries[entry_id]
            except KeyError:
                raise EntryNotFoundError(entry_id) from None

    def add(self, entry: Entry) -> Entry:
        entry = entry.copy()
        entry.normalise()
        entry.validate()
        with self._lock:
            self._require_unlocked()
            if entry.id in self._entries:
                raise VaultError("An entry with this id already exists")
            now = time.time()
            entry.created = entry.modified = now
            self._write(entry)
            self._entries[entry.id] = entry
        self._emit("added", entry.id)
        return entry

    def update(self, entry: Entry) -> Entry:
        entry = entry.copy()
        entry.normalise()
        entry.validate()
        with self._lock:
            self._require_unlocked()
            existing = self.get(entry.id)
            entry.created = existing.created
            entry.deleted_at = existing.deleted_at
            entry.modified = time.time()
            self._write(entry)
            self._entries[entry.id] = entry
        self._emit("updated", entry.id)
        return entry

    def _set_state(self, entry_id: str, event: str, **changes) -> Entry:
        with self._lock:
            self._require_unlocked()
            entry = self.get(entry_id).copy()
            for name, value in changes.items():
                setattr(entry, name, value)
            self._write(entry)
            self._entries[entry.id] = entry
        self._emit(event, entry.id)
        return entry

    def set_favorite(self, entry_id: str, favorite: bool) -> Entry:
        return self._set_state(entry_id, "updated", favorite=favorite)

    def trash(self, entry_id: str) -> Entry:
        return self._set_state(entry_id, "trashed", deleted_at=time.time())

    def restore(self, entry_id: str) -> Entry:
        return self._set_state(entry_id, "restored", deleted_at=None)

    def delete_permanently(self, entry_id: str) -> None:
        with self._lock:
            self._require_unlocked()
            self.get(entry_id)
            self._open_db().delete_entry(entry_id)
            del self._entries[entry_id]
        self._emit("deleted", entry_id)

    def empty_trash(self) -> int:
        with self._lock:
            self._require_unlocked()
            ids = [e.id for e in self._entries.values() if e.in_trash]
            db = self._open_db()
            with db.transaction() as conn:
                for entry_id in ids:
                    db.delete_entry(entry_id, conn)
            for entry_id in ids:
                del self._entries[entry_id]
        self._emit("deleted", None)
        return len(ids)

    def import_entries(self, entries: Iterable[Entry]) -> tuple[int, int, int]:
        """Merge entries (e.g. from a backup) into the vault atomically.

        Entries with an unknown id are added. For an id that already exists,
        the copy with the newer modification time wins. Returns
        ``(added, updated, skipped)``.
        """
        added = updated = skipped = 0
        with self._lock:
            self._require_unlocked()
            incoming: list[Entry] = []
            for entry in entries:
                entry = entry.copy()
                entry.normalise()
                entry.validate()
                incoming.append(entry)
            db = self._open_db()
            to_store: list[Entry] = []
            for entry in incoming:
                existing = self._entries.get(entry.id)
                if existing is None:
                    added += 1
                elif entry.modified > existing.modified:
                    updated += 1
                else:
                    skipped += 1
                    continue
                to_store.append(entry)
            with db.transaction() as conn:
                for entry in to_store:
                    self._write(entry, conn)
            for entry in to_store:
                self._entries[entry.id] = entry
        self._emit("imported", None)
        return added, updated, skipped
