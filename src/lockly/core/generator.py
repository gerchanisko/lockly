"""Password and passphrase generation.

All randomness comes from :mod:`secrets` (the operating system CSPRNG).
"""

from __future__ import annotations

import math
import secrets
import string
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib import resources

UPPER = string.ascii_uppercase
LOWER = string.ascii_lowercase
DIGITS = string.digits
SYMBOLS = "!#$%&()*+,-./:;<=>?@[]^_{|}~"
AMBIGUOUS = set("Il1O0o|`'\"{}[]()/\\;:.,")

MIN_LENGTH = 4
MAX_LENGTH = 128
MIN_WORDS = 3
MAX_WORDS = 20


@dataclass
class PasswordOptions:
    length: int = 20
    uppercase: bool = True
    lowercase: bool = True
    digits: bool = True
    symbols: bool = True
    exclude_ambiguous: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PassphraseOptions:
    words: int = 5
    separator: str = "-"
    capitalize: bool = False
    include_number: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _filtered(chars: str, exclude_ambiguous: bool) -> str:
    return "".join(c for c in chars if not (exclude_ambiguous and c in AMBIGUOUS))


def character_classes(options: PasswordOptions) -> list[str]:
    classes = []
    if options.uppercase:
        classes.append(_filtered(UPPER, options.exclude_ambiguous))
    if options.lowercase:
        classes.append(_filtered(LOWER, options.exclude_ambiguous))
    if options.digits:
        classes.append(_filtered(DIGITS, options.exclude_ambiguous))
    if options.symbols:
        classes.append(_filtered(SYMBOLS, options.exclude_ambiguous))
    return [c for c in classes if c]


def generate_password(options: PasswordOptions) -> str:
    """Generate a password containing at least one character of every
    selected class. Positions are shuffled with the CSPRNG."""
    if not MIN_LENGTH <= options.length <= MAX_LENGTH:
        raise ValueError(f"Length must be between {MIN_LENGTH} and {MAX_LENGTH}")
    classes = character_classes(options)
    if not classes:
        raise ValueError("Select at least one character set")
    if options.length < len(classes):
        raise ValueError("Password is too short for the selected character sets")
    alphabet = "".join(classes)
    chars = [secrets.choice(c) for c in classes]
    chars += [secrets.choice(alphabet) for _ in range(options.length - len(classes))]
    # Fisher–Yates shuffle with secrets.randbelow.
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


@lru_cache(maxsize=1)
def wordlist() -> tuple[str, ...]:
    """The EFF large wordlist (7776 words, CC BY 3.0 US)."""
    text = resources.files("lockly.resources").joinpath("eff_large_wordlist.txt").read_text("utf-8")
    words = tuple(w.strip() for w in text.splitlines() if w.strip())
    if len(words) != 7776 or len(set(words)) != 7776:
        raise RuntimeError("The bundled passphrase wordlist is damaged")
    return words


def generate_passphrase(options: PassphraseOptions) -> str:
    if not MIN_WORDS <= options.words <= MAX_WORDS:
        raise ValueError(f"Use between {MIN_WORDS} and {MAX_WORDS} words")
    words = wordlist()
    chosen = [secrets.choice(words) for _ in range(options.words)]
    if options.capitalize:
        chosen = [w.capitalize() for w in chosen]
    if options.include_number:
        index = secrets.randbelow(len(chosen))
        chosen[index] = chosen[index] + str(secrets.randbelow(10))
    return options.separator.join(chosen)


def password_entropy(options: PasswordOptions) -> float:
    """Approximate entropy in bits of a password from :func:`generate_password`."""
    alphabet = len("".join(character_classes(options)))
    if alphabet < 2:
        return 0.0
    return options.length * math.log2(alphabet)


def passphrase_entropy(options: PassphraseOptions) -> float:
    bits = options.words * math.log2(len(wordlist()))
    if options.include_number:
        bits += math.log2(10 * options.words)
    return bits


def estimate_strength(password: str) -> tuple[float, str]:
    """Rough strength estimate for a password the user typed.

    This is a simple character-pool heuristic, not a cracking-resistance
    guarantee: it cannot detect dictionary words or reuse. Returns
    ``(score 0..1, label)``.
    """
    if not password:
        return 0.0, ""
    pool = 0
    if any(c in LOWER for c in password):
        pool += 26
    if any(c in UPPER for c in password):
        pool += 26
    if any(c in DIGITS for c in password):
        pool += 10
    if any(not c.isalnum() for c in password):
        pool += 33
    if any(ord(c) > 127 for c in password):
        pool += 100
    unique = len(set(password))
    effective_length = min(len(password), unique * 2)
    bits = effective_length * math.log2(max(pool, 2))
    if bits < 40:
        label = "Weak"
    elif bits < 64:
        label = "Fair"
    elif bits < 90:
        label = "Good"
    else:
        label = "Strong"
    return min(bits / 110, 1.0), label
