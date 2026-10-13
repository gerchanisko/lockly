"""Non-sensitive user preferences and standard file locations.

Preferences are stored as JSON in ``$XDG_CONFIG_HOME/lockly/settings.json``,
completely separate from the vault. Nothing secret (keys, passwords, entry
data) is ever written here.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .database import atomic_write, ensure_private_dir
from .generator import PassphraseOptions, PasswordOptions

log = logging.getLogger(__name__)

APP_DIR_NAME = "lockly"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR_NAME


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_DIR_NAME


def default_vault_path() -> Path:
    override = os.environ.get("LOCKLY_VAULT")
    if override:
        return Path(override).expanduser()
    return data_dir() / "vault.lockly"


AUTO_LOCK_CHOICES = [1, 2, 5, 10, 15, 30, 60, 0]  # minutes; 0 = never
CLIPBOARD_CLEAR_CHOICES = [10, 20, 30, 60, 90, 0]  # seconds; 0 = never
COLOR_SCHEMES = ["system", "light", "dark"]


@dataclass
class Settings:
    color_scheme: str = "dark"
    auto_lock_minutes: int = 5
    lock_on_screen_lock: bool = True
    clipboard_clear_seconds: int = 30
    generator_mode: str = "password"  # password | passphrase
    password: PasswordOptions = field(default_factory=PasswordOptions)
    passphrase: PassphraseOptions = field(default_factory=PassphraseOptions)
    sort_order: str = "name"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        settings = cls()
        if not isinstance(data, dict):
            return settings
        simple = {f.name for f in fields(cls)} - {"password", "passphrase"}
        for name in simple:
            if name in data and isinstance(data[name], type(getattr(settings, name))):
                setattr(settings, name, data[name])
        for name, kind in (("password", PasswordOptions), ("passphrase", PassphraseOptions)):
            sub = data.get(name)
            if isinstance(sub, dict):
                current = getattr(settings, name)
                for f in fields(kind):
                    if f.name in sub and isinstance(sub[f.name], type(getattr(current, f.name))):
                        setattr(current, f.name, sub[f.name])
        settings._sanitise()
        return settings

    def _sanitise(self) -> None:
        if self.color_scheme not in COLOR_SCHEMES:
            self.color_scheme = "dark"
        if self.auto_lock_minutes not in AUTO_LOCK_CHOICES:
            self.auto_lock_minutes = 5
        if self.clipboard_clear_seconds not in CLIPBOARD_CLEAR_CHOICES:
            self.clipboard_clear_seconds = 30
        if self.generator_mode not in ("password", "passphrase"):
            self.generator_mode = "password"
        self.password.length = max(8, min(128, self.password.length))
        self.passphrase.words = max(3, min(20, self.passphrase.words))
        if len(self.passphrase.separator) > 3:
            self.passphrase.separator = "-"


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "settings.json"
        self.settings = self.load()

    def load(self) -> Settings:
        try:
            return Settings.from_dict(json.loads(self.path.read_text("utf-8")))
        except FileNotFoundError:
            return Settings()
        except (OSError, ValueError):
            log.warning("Could not read settings file; using defaults")
            return Settings()

    def save(self) -> None:
        try:
            ensure_private_dir(self.path.parent)
            data = json.dumps(self.settings.to_dict(), indent=2).encode("utf-8")
            atomic_write(self.path, data, overwrite=True)
        except OSError:
            log.warning("Could not save settings")
