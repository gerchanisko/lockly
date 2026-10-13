"""Vault entry data model.

Entries are plain dataclasses. They are serialised to JSON and encrypted as a
whole before they reach the database, so every field here, including the
title, type, timestamps and favourite flag, is ciphertext at rest.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class EntryType(str, Enum):
    LOGIN = "login"
    NOTE = "note"
    IDENTITY = "identity"
    CARD = "card"

    @property
    def label(self) -> str:
        return ENTRY_TYPE_LABELS[self][0]

    @property
    def plural(self) -> str:
        return ENTRY_TYPE_LABELS[self][1]

    @property
    def icon_name(self) -> str:
        return ENTRY_TYPE_LABELS[self][2]


ENTRY_TYPE_LABELS = {
    EntryType.LOGIN: ("Login", "Logins", "dialog-password-symbolic"),
    EntryType.NOTE: ("Secure Note", "Secure Notes", "text-x-generic-symbolic"),
    EntryType.IDENTITY: ("Identity", "Identities", "avatar-default-symbolic"),
    EntryType.CARD: ("Credit Card", "Credit Cards", "lockly-card-symbolic"),
}


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    secret: bool = False
    multiline: bool = False
    kind: str = "text"  # text | url | email | phone | card-number | expiry | password
    copyable: bool = True
    searchable: bool = True


# Field layout per entry type. The editor and details panel are generated from
# this table. Secret fields are never included in the search index.
FIELD_SPECS: dict[EntryType, list[FieldSpec]] = {
    EntryType.LOGIN: [
        FieldSpec("username", "Username"),
        FieldSpec("password", "Password", secret=True, kind="password", searchable=False),
        FieldSpec("url", "Website", kind="url"),
        FieldSpec("notes", "Notes", multiline=True, copyable=False),
    ],
    EntryType.NOTE: [
        FieldSpec("content", "Content", multiline=True),
    ],
    EntryType.IDENTITY: [
        FieldSpec("full_name", "Full Name"),
        FieldSpec("email", "Email", kind="email"),
        FieldSpec("phone", "Phone", kind="phone"),
        FieldSpec("address", "Address", multiline=True),
        FieldSpec("notes", "Notes", multiline=True, copyable=False),
    ],
    EntryType.CARD: [
        FieldSpec("cardholder", "Cardholder"),
        FieldSpec("number", "Card Number", secret=True, kind="card-number", searchable=False),
        FieldSpec("expiry", "Expiration Date", kind="expiry", searchable=False),
        FieldSpec("cvv", "Security Code", secret=True, searchable=False),
        FieldSpec("notes", "Notes", multiline=True, copyable=False),
    ],
}

# Types that support user-defined extra fields.
SUPPORTS_CUSTOM_FIELDS = {EntryType.LOGIN, EntryType.IDENTITY}

# The field shown as the second line in the entry list.
SUBTITLE_FIELD = {
    EntryType.LOGIN: "username",
    EntryType.NOTE: None,
    EntryType.IDENTITY: "email",
    EntryType.CARD: "cardholder",
}


class ValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


@dataclass
class CustomField:
    name: str
    value: str
    hidden: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "value": self.value, "hidden": self.hidden}

    @classmethod
    def from_dict(cls, data: dict) -> "CustomField":
        return cls(str(data.get("name", "")), str(data.get("value", "")), bool(data.get("hidden", False)))


def _now() -> float:
    return time.time()


@dataclass
class Entry:
    type: EntryType
    title: str
    fields: dict[str, str] = field(default_factory=dict)
    custom_fields: list[CustomField] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    favorite: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created: float = field(default_factory=_now)
    modified: float = field(default_factory=_now)
    deleted_at: float | None = None

    @property
    def in_trash(self) -> bool:
        return self.deleted_at is not None

    def get(self, key: str) -> str:
        return self.fields.get(key, "")

    @property
    def subtitle(self) -> str:
        key = SUBTITLE_FIELD.get(self.type)
        if key:
            return self.get(key)
        if self.type is EntryType.NOTE:
            first = self.get("content").strip().splitlines()
            return first[0][:80] if first else ""
        return ""

    def copy(self) -> "Entry":
        return Entry.from_dict(self.to_dict())

    # Serialisation -------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "title": self.title,
            "fields": dict(self.fields),
            "custom_fields": [c.to_dict() for c in self.custom_fields],
            "tags": list(self.tags),
            "favorite": self.favorite,
            "created": self.created,
            "modified": self.modified,
            "deleted_at": self.deleted_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Entry":
        try:
            entry_type = EntryType(data["type"])
            entry = cls(
                id=str(data["id"]),
                type=entry_type,
                title=str(data.get("title", "")),
                fields={str(k): str(v) for k, v in dict(data.get("fields", {})).items()},
                custom_fields=[CustomField.from_dict(c) for c in data.get("custom_fields", [])],
                tags=[str(t) for t in data.get("tags", [])],
                favorite=bool(data.get("favorite", False)),
                created=float(data.get("created", _now())),
                modified=float(data.get("modified", _now())),
                deleted_at=None if data.get("deleted_at") is None else float(data["deleted_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Malformed entry data") from exc
        if not re.fullmatch(r"[0-9a-f]{32}", entry.id):
            raise ValueError("Malformed entry id")
        return entry

    # Validation ----------------------------------------------------------
    def normalise(self) -> None:
        self.title = self.title.strip()
        self.tags = sorted({t.strip() for t in self.tags if t.strip()}, key=str.casefold)
        allowed = {spec.key for spec in FIELD_SPECS[self.type]}
        self.fields = {k: v for k, v in self.fields.items() if k in allowed and v != ""}
        if self.type is EntryType.CARD and "number" in self.fields:
            self.fields["number"] = format_card_number(self.fields["number"])
        if self.type is EntryType.LOGIN and "url" in self.fields:
            self.fields["url"] = self.fields["url"].strip()
        if self.type not in SUPPORTS_CUSTOM_FIELDS:
            self.custom_fields = []
        self.custom_fields = [c for c in self.custom_fields if c.name.strip() or c.value]

    def validate(self) -> None:
        errors = validate_entry_fields(self.type, self.title, self.fields, self.custom_fields)
        if errors:
            raise ValidationError(errors)


def validate_entry_fields(
    entry_type: EntryType,
    title: str,
    fields: dict[str, str],
    custom_fields: list[CustomField] | None = None,
) -> dict[str, str]:
    """Return a mapping of field key -> human readable error message."""
    errors: dict[str, str] = {}
    if not title.strip():
        errors["title"] = "A title is required"
    elif len(title) > 256:
        errors["title"] = "Title is too long"

    if entry_type is EntryType.LOGIN:
        url = fields.get("url", "").strip()
        if url and (" " in url or len(url) > 2048):
            errors["url"] = "Enter a valid web address"
    elif entry_type is EntryType.IDENTITY:
        email = fields.get("email", "").strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            errors["email"] = "Enter a valid email address"
        phone = fields.get("phone", "").strip()
        if phone and not re.fullmatch(r"[+0-9 ()\-./]{3,32}", phone):
            errors["phone"] = "Enter a valid phone number"
    elif entry_type is EntryType.CARD:
        number = re.sub(r"[\s-]", "", fields.get("number", ""))
        if number and (not number.isdigit() or not 12 <= len(number) <= 19):
            errors["number"] = "Card numbers have 12–19 digits"
        expiry = fields.get("expiry", "").strip()
        if expiry and not parse_expiry(expiry):
            errors["expiry"] = "Use the format MM/YY"
        cvv = fields.get("cvv", "").strip()
        if cvv and not re.fullmatch(r"\d{3,4}", cvv):
            errors["cvv"] = "Security codes have 3 or 4 digits"

    for i, custom in enumerate(custom_fields or []):
        if custom.value and not custom.name.strip():
            errors[f"custom:{i}"] = "Custom fields need a name"
    return errors


def parse_expiry(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{2}|\d{4})\s*", value)
    if not match:
        return None
    month, year = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    if year < 100:
        year += 2000
    return month, year


def format_card_number(value: str) -> str:
    digits = re.sub(r"[\s-]", "", value)
    if not digits.isdigit():
        return value.strip()
    return " ".join(digits[i : i + 4] for i in range(0, len(digits), 4))


def mask_card_number(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 4:
        return "••••"
    return f"•••• {digits[-4:]}"


def luhn_valid(value: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", value)]
    if not digits:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0
