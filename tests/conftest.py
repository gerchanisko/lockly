import pytest

from lockly.core.crypto import KdfParams

# Deliberately cheap parameters so the test suite runs quickly. Production
# vaults use crypto.DEFAULT_KDF (Argon2id, 3 passes, 256 MiB).
FAST_KDF = KdfParams(opslimit=2, memlimit=8 * 1024 * 1024)

# Obviously fake test credentials. Never use real passwords in tests.
MASTER = "correct horse test battery"
OTHER = "not the master password"


@pytest.fixture
def fast_kdf():
    return FAST_KDF


@pytest.fixture
def vault_path(tmp_path):
    return tmp_path / "data" / "vault.lockly"


@pytest.fixture
def vault(vault_path):
    from lockly.core.vault import Vault

    v = Vault.create(vault_path, MASTER, FAST_KDF)
    yield v
    v.lock()
