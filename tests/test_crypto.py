import pytest

from lockly.core import crypto
from lockly.core.crypto import CryptoError, KdfParams

from .conftest import FAST_KDF


def test_round_trip():
    key = crypto.new_key()
    nonce, ct = crypto.encrypt(key, b"sample secret", b"aad")
    assert crypto.decrypt(key, nonce, ct, b"aad") == b"sample secret"
    assert b"sample secret" not in ct


def test_nonces_are_unique():
    key = crypto.new_key()
    nonces = {crypto.encrypt(key, b"x", b"")[0] for _ in range(200)}
    assert len(nonces) == 200


def test_wrong_key_rejected():
    nonce, ct = crypto.encrypt(crypto.new_key(), b"data", b"")
    with pytest.raises(CryptoError):
        crypto.decrypt(crypto.new_key(), nonce, ct, b"")


def test_wrong_associated_data_rejected():
    key = crypto.new_key()
    nonce, ct = crypto.encrypt(key, b"data", b"entry-1")
    with pytest.raises(CryptoError):
        crypto.decrypt(key, nonce, ct, b"entry-2")


@pytest.mark.parametrize("position", [0, 5, -1])
def test_tampered_ciphertext_rejected(position):
    key = crypto.new_key()
    nonce, ct = crypto.encrypt(key, b"some data here", b"")
    tampered = bytearray(ct)
    tampered[position] ^= 0x01
    with pytest.raises(CryptoError):
        crypto.decrypt(key, nonce, bytes(tampered), b"")


def test_kdf_deterministic_and_salted():
    salt = crypto.new_salt()
    a = crypto.derive_key("test password", salt, FAST_KDF)
    b = crypto.derive_key("test password", salt, FAST_KDF)
    c = crypto.derive_key("test password", crypto.new_salt(), FAST_KDF)
    d = crypto.derive_key("test passworD", salt, FAST_KDF)
    assert a == b
    assert a != c
    assert a != d
    assert len(a) == crypto.KEY_SIZE


def test_kdf_params_validation():
    with pytest.raises(CryptoError):
        KdfParams(opslimit=0, memlimit=FAST_KDF.memlimit).validate()
    with pytest.raises(CryptoError):
        KdfParams(opslimit=3, memlimit=crypto.MAX_MEMLIMIT + 1).validate()
    with pytest.raises(CryptoError):
        KdfParams(opslimit=3, memlimit=FAST_KDF.memlimit, algorithm="scrypt").validate()
    with pytest.raises(CryptoError):
        KdfParams.from_dict({"opslimit": "x"})


def test_default_kdf_is_strong():
    assert crypto.DEFAULT_KDF.opslimit >= 3
    assert crypto.DEFAULT_KDF.memlimit >= 256 * 1024 * 1024


def test_wipe():
    buf = bytearray(b"secret")
    crypto.wipe(buf)
    assert buf == bytearray(6)
