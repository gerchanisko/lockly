import string

import pytest

from lockly.core import generator
from lockly.core.generator import PassphraseOptions, PasswordOptions


def test_length_and_classes():
    opts = PasswordOptions(length=32)
    for _ in range(100):
        pw = generator.generate_password(opts)
        assert len(pw) == 32
        assert any(c in string.ascii_uppercase for c in pw)
        assert any(c in string.ascii_lowercase for c in pw)
        assert any(c in string.digits for c in pw)
        assert any(c in generator.SYMBOLS for c in pw)


def test_single_class():
    pw = generator.generate_password(PasswordOptions(length=50, uppercase=False, symbols=False, lowercase=False))
    assert pw.isdigit()


def test_exclude_ambiguous():
    opts = PasswordOptions(length=128, exclude_ambiguous=True)
    for _ in range(20):
        assert not set(generator.generate_password(opts)) & generator.AMBIGUOUS


def test_no_class_selected():
    with pytest.raises(ValueError):
        generator.generate_password(PasswordOptions(uppercase=False, lowercase=False, digits=False, symbols=False))


@pytest.mark.parametrize("length", [0, 3, 129])
def test_invalid_length(length):
    with pytest.raises(ValueError):
        generator.generate_password(PasswordOptions(length=length))


def test_outputs_differ():
    results = {generator.generate_password(PasswordOptions(length=16)) for _ in range(500)}
    assert len(results) == 500


def test_distribution_roughly_uniform():
    opts = PasswordOptions(length=128, uppercase=False, symbols=False, digits=False)
    counts = dict.fromkeys(string.ascii_lowercase, 0)
    for _ in range(200):
        for c in generator.generate_password(opts):
            counts[c] += 1
    expected = 200 * 128 / 26
    assert all(0.75 * expected < n < 1.25 * expected for n in counts.values())


def test_uses_secrets_module(monkeypatch):
    calls = []
    real = generator.secrets.choice
    monkeypatch.setattr(generator.secrets, "choice", lambda seq: calls.append(1) or real(seq))
    generator.generate_password(PasswordOptions(length=10))
    assert len(calls) == 10


def test_wordlist():
    words = generator.wordlist()
    assert len(words) == 7776


def test_passphrase():
    phrase = generator.generate_passphrase(PassphraseOptions(words=6, separator="."))
    parts = phrase.split(".")
    assert len(parts) == 6
    assert all(p in generator.wordlist() for p in parts)


def test_passphrase_options():
    phrase = generator.generate_passphrase(PassphraseOptions(words=4, separator=" ", capitalize=True, include_number=True))
    parts = phrase.split(" ")
    assert all(p[0].isupper() for p in parts)
    assert sum(p[-1].isdigit() for p in parts) == 1


def test_entropy():
    assert generator.password_entropy(PasswordOptions(length=20)) > 120
    assert round(generator.passphrase_entropy(PassphraseOptions(words=5))) == 65


def test_strength_estimate():
    assert generator.estimate_strength("")[1] == ""
    assert generator.estimate_strength("aaaaaa")[1] == "Weak"
    assert generator.estimate_strength("x7#Kp2!vQ9@mLz4$Rw8&")[1] == "Strong"
