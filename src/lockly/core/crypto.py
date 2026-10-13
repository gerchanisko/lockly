"""Cryptographic primitives used by Lockly.

Nothing in this module implements a cipher or a KDF. Everything is delegated
to libsodium through PyNaCl:

* Key derivation: Argon2id v1.3 (``crypto_pwhash_argon2id``).
* Authenticated encryption: XChaCha20-Poly1305 IETF AEAD
  (``crypto_aead_xchacha20poly1305_ietf_*``) with 192-bit random nonces.

See ``docs/SECURITY.md`` for how these are combined into the vault format.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

import nacl.exceptions
import nacl.utils
from nacl import bindings, pwhash

KEY_SIZE = bindings.crypto_aead_xchacha20poly1305_ietf_KEYBYTES  # 32
NONCE_SIZE = bindings.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES  # 24
SALT_SIZE = pwhash.argon2id.SALTBYTES  # 16

KDF_ALGORITHM = "argon2id13"

# Hard bounds applied to KDF parameters read from disk. They prevent a
# tampered or corrupted file from making Lockly allocate absurd amounts of
# memory, and from silently accepting parameters below libsodium's minimum.
MIN_OPSLIMIT = pwhash.argon2id.OPSLIMIT_MIN
MAX_OPSLIMIT = 20
MIN_MEMLIMIT = pwhash.argon2id.MEMLIMIT_MIN
MAX_MEMLIMIT = 4 * 1024 * 1024 * 1024  # 4 GiB


class CryptoError(Exception):
    """Raised when decryption or authentication fails.

    The message is deliberately generic and never contains key material.
    """


@dataclass(frozen=True)
class KdfParams:
    """Argon2id parameters stored next to the salt in the vault header."""

    opslimit: int
    memlimit: int
    algorithm: str = KDF_ALGORITHM

    def validate(self) -> None:
        if self.algorithm != KDF_ALGORITHM:
            raise CryptoError(f"Unsupported key derivation algorithm: {self.algorithm!r}")
        if not (MIN_OPSLIMIT <= self.opslimit <= MAX_OPSLIMIT):
            raise CryptoError("Key derivation iteration count is out of the accepted range")
        if not (MIN_MEMLIMIT <= self.memlimit <= MAX_MEMLIMIT):
            raise CryptoError("Key derivation memory cost is out of the accepted range")

    def to_dict(self) -> dict:
        return {"algorithm": self.algorithm, "opslimit": self.opslimit, "memlimit": self.memlimit}

    @classmethod
    def from_dict(cls, data: dict) -> "KdfParams":
        try:
            params = cls(
                opslimit=int(data["opslimit"]),
                memlimit=int(data["memlimit"]),
                algorithm=str(data["algorithm"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CryptoError("Malformed key derivation parameters") from exc
        params.validate()
        return params

    @property
    def memory_mib(self) -> int:
        return self.memlimit // (1024 * 1024)


# Named profiles offered in the UI. "moderate" is libsodium's recommended
# interactive-but-careful setting (3 passes, 256 MiB) and takes roughly
# 0.5–1.5 s on current desktop hardware. "sensitive" uses 1 GiB.
KDF_PROFILES: dict[str, KdfParams] = {
    "moderate": KdfParams(
        opslimit=pwhash.argon2id.OPSLIMIT_MODERATE,
        memlimit=pwhash.argon2id.MEMLIMIT_MODERATE,
    ),
    "sensitive": KdfParams(
        opslimit=pwhash.argon2id.OPSLIMIT_SENSITIVE,
        memlimit=pwhash.argon2id.MEMLIMIT_SENSITIVE,
    ),
}
DEFAULT_KDF = KDF_PROFILES["moderate"]


def random_bytes(size: int) -> bytes:
    """Return ``size`` bytes from libsodium's CSPRNG (``randombytes_buf``)."""
    return nacl.utils.random(size)


def new_salt() -> bytes:
    return random_bytes(SALT_SIZE)


def new_key() -> bytearray:
    """Generate a fresh random symmetric key.

    Returned as a ``bytearray`` so the owner can overwrite it with
    :func:`wipe` when it is no longer needed.
    """
    return bytearray(random_bytes(KEY_SIZE))


def derive_key(password: str, salt: bytes, params: KdfParams) -> bytearray:
    """Derive a 256-bit key from ``password`` with Argon2id."""
    params.validate()
    if len(salt) != SALT_SIZE:
        raise CryptoError("Invalid salt length")
    secret = bytearray(password.encode("utf-8"))
    try:
        key = pwhash.argon2id.kdf(
            KEY_SIZE,
            bytes(secret),
            salt,
            opslimit=params.opslimit,
            memlimit=params.memlimit,
        )
    finally:
        wipe(secret)
    return bytearray(key)


def encrypt(key: bytes | bytearray, plaintext: bytes, associated_data: bytes) -> tuple[bytes, bytes]:
    """Encrypt and authenticate ``plaintext``.

    ``associated_data`` is authenticated but not encrypted; it binds the
    ciphertext to its context (vault, entry id, format version) so that
    ciphertexts cannot be swapped between rows or files undetected.

    Returns ``(nonce, ciphertext)``. The nonce is 192 bits from the CSPRNG, so
    random nonce reuse is not a practical concern.
    """
    if len(key) != KEY_SIZE:
        raise CryptoError("Invalid key length")
    nonce = random_bytes(NONCE_SIZE)
    ciphertext = bindings.crypto_aead_xchacha20poly1305_ietf_encrypt(
        plaintext, associated_data, nonce, bytes(key)
    )
    return nonce, ciphertext


def decrypt(key: bytes | bytearray, nonce: bytes, ciphertext: bytes, associated_data: bytes) -> bytes:
    """Verify and decrypt. Raises :class:`CryptoError` on any failure."""
    if len(key) != KEY_SIZE or len(nonce) != NONCE_SIZE:
        raise CryptoError("Decryption failed")
    try:
        return bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(
            ciphertext, associated_data, nonce, bytes(key)
        )
    except (nacl.exceptions.CryptoError, TypeError, ValueError) as exc:
        raise CryptoError("Decryption failed: wrong key or data has been modified") from exc


def wipe(buffer: bytearray | None) -> None:
    """Overwrite a mutable buffer with zeros.

    This is best effort: Python may already have made transient copies of the
    data (for example when converting to ``bytes`` for a libsodium call).
    """
    if buffer is None:
        return
    for i in range(len(buffer)):
        buffer[i] = 0


def constant_time_equal(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)
