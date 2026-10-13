"""Optional sample entries, added only when the user explicitly asks for them.

All data is fictional. Domains use the reserved ``example`` names
(RFC 2606), the card number is the well-known public test number that no
issuer will accept, and the phone number is from a fictional range.
"""

from __future__ import annotations

from .generator import PasswordOptions, generate_password
from .models import CustomField, Entry, EntryType


def demo_entries() -> list[Entry]:
    pw = lambda: generate_password(PasswordOptions(length=20))  # noqa: E731
    return [
        Entry(
            type=EntryType.LOGIN,
            title="Example Mail",
            fields={"username": "alex.sample@example.com", "password": pw(), "url": "https://mail.example.com"},
            tags=["sample", "personal"],
            favorite=True,
        ),
        Entry(
            type=EntryType.LOGIN,
            title="Code Forge",
            fields={
                "username": "alex-sample",
                "password": pw(),
                "url": "https://forge.example.org/login",
                "notes": "Sample entry. Recovery codes are stored in the 'Forge recovery' note.",
            },
            custom_fields=[CustomField("Account ID", "AX-40218"), CustomField("API token", "sample-token-not-real", hidden=True)],
            tags=["sample", "work"],
        ),
        Entry(
            type=EntryType.LOGIN,
            title="Home Router",
            fields={"username": "admin", "password": pw(), "url": "http://192.0.2.1"},
            tags=["sample", "home"],
        ),
        Entry(
            type=EntryType.NOTE,
            title="Forge recovery",
            fields={"content": "Sample recovery codes (not real):\n1111-2222\n3333-4444\n5555-6666"},
            tags=["sample"],
        ),
        Entry(
            type=EntryType.IDENTITY,
            title="Alex Sample",
            fields={
                "full_name": "Alex Sample",
                "email": "alex.sample@example.com",
                "phone": "+1 555 0100",
                "address": "1 Example Street\nSampleton",
            },
            tags=["sample"],
        ),
        Entry(
            type=EntryType.CARD,
            title="Sample Card (not real)",
            fields={
                "cardholder": "Alex Sample",
                "number": "4111 1111 1111 1111",
                "expiry": "12/30",
                "notes": "Public test number. Cannot be used for payments.",
            },
            tags=["sample"],
        ),
    ]
