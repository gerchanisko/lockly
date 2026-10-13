"""Vault format versioning and migrations.

Every vault records ``format_version`` in its header. When Lockly opens a vault
written by an older release, the registered migration steps are applied in
order, one version at a time, inside a single transaction, after the master
password has been verified (so steps may re-encrypt data with the vault key).
A copy of the original file is kept next to the vault before anything changes.

A vault from a *newer* release is never touched; Lockly refuses to open it.

Format history
--------------
1  Initial format: Argon2id-wrapped random vault key, one
   XChaCha20-Poly1305 ciphertext per entry (see docs/SECURITY.md).
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

CURRENT_FORMAT_VERSION = 1


class VaultFormatError(Exception):
    pass


@dataclass
class MigrationContext:
    conn: sqlite3.Connection
    vault_key: bytes | bytearray
    vault_id: str


# from_version -> function migrating the vault to from_version + 1
MigrationStep = Callable[[MigrationContext], None]
MIGRATIONS: dict[int, MigrationStep] = {}


def check_supported(version: int) -> None:
    if version < 1:
        raise VaultFormatError(f"Invalid vault format version {version}")
    if version > CURRENT_FORMAT_VERSION:
        raise VaultFormatError(
            f"This vault uses format version {version}, which was created by a newer "
            f"version of Lockly. This version supports up to format {CURRENT_FORMAT_VERSION}."
        )
    missing = [v for v in range(version, CURRENT_FORMAT_VERSION) if v not in MIGRATIONS]
    if missing:
        raise VaultFormatError(f"No migration available from vault format {missing[0]}")


def needs_migration(version: int) -> bool:
    return version < CURRENT_FORMAT_VERSION


def backup_before_migration(path: Path, version: int) -> Path:
    target = path.with_name(f"{path.name}.format-v{version}.bak")
    counter = 1
    while target.exists():
        counter += 1
        target = path.with_name(f"{path.name}.format-v{version}.{counter}.bak")
    shutil.copy2(path, target)
    target.chmod(0o600)
    return target


def run_migrations(ctx: MigrationContext, from_version: int) -> int:
    """Apply all steps from ``from_version`` to the current version.

    The caller wraps this in a transaction. Returns the new version.
    """
    check_supported(from_version)
    version = from_version
    while version < CURRENT_FORMAT_VERSION:
        MIGRATIONS[version](ctx)
        version += 1
    return version
