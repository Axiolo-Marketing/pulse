"""Unit tests for `pulse_api.crypto`.

Roundtrip, tamper-detection, rotation, and fail-loud-on-missing-key.
All four matter — losing any one would let secrets leak silently.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from pulse_api import crypto
from pulse_api.config import settings


@pytest.fixture
def with_keys(monkeypatch: pytest.MonkeyPatch):
    """Generate fresh keys per test so the lru_cache from prior tests
    doesn't bleed across cases. Returns a helper that sets the keys."""
    def _set(keys_csv: str) -> None:
        monkeypatch.setattr(settings, "encryption_keys", keys_csv)
        crypto.reset_keys_cache()

    yield _set
    # Clear after the test so the next case sees a clean slate.
    monkeypatch.setattr(settings, "encryption_keys", "")
    crypto.reset_keys_cache()


def test_roundtrip_returns_original_plaintext(with_keys) -> None:
    with_keys(Fernet.generate_key().decode())
    ct = crypto.encrypt("a sensitive token value")
    assert ct != "a sensitive token value"
    assert crypto.decrypt(ct) == "a sensitive token value"


def test_ciphertext_is_unique_per_call(with_keys) -> None:
    """Fernet uses a fresh IV per encryption — two encrypts of the same
    plaintext produce different ciphertext."""
    with_keys(Fernet.generate_key().decode())
    a = crypto.encrypt("same value")
    b = crypto.encrypt("same value")
    assert a != b
    assert crypto.decrypt(a) == "same value"
    assert crypto.decrypt(b) == "same value"


def test_tampered_ciphertext_raises(with_keys) -> None:
    with_keys(Fernet.generate_key().decode())
    ct = crypto.encrypt("don't change me")
    tampered = ct[:-1] + ("A" if ct[-1] != "A" else "B")
    with pytest.raises(crypto.InvalidCiphertext):
        crypto.decrypt(tampered)


def test_garbage_ciphertext_raises(with_keys) -> None:
    with_keys(Fernet.generate_key().decode())
    with pytest.raises(crypto.InvalidCiphertext):
        crypto.decrypt("not a real fernet token")


def test_missing_key_raises_on_encrypt(with_keys) -> None:
    with_keys("")
    with pytest.raises(crypto.EncryptionKeyMissing):
        crypto.encrypt("anything")


def test_missing_key_raises_on_decrypt(with_keys) -> None:
    with_keys("")
    with pytest.raises(crypto.EncryptionKeyMissing):
        crypto.decrypt("anything")


def test_blank_only_keys_raises(with_keys) -> None:
    with_keys("   ,  ,  ")
    with pytest.raises(crypto.EncryptionKeyMissing):
        crypto.encrypt("anything")


def test_rotation_decrypts_with_older_key(with_keys) -> None:
    """Value encrypted with key A still decrypts after a new key B is
    rotated in as primary, as long as A stays in the list."""
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    with_keys(key_a)
    ct = crypto.encrypt("legacy value")

    # Rotate: B is now primary, A stays for legacy ciphertext
    with_keys(f"{key_b},{key_a}")
    assert crypto.decrypt(ct) == "legacy value"

    # New encryption uses key B
    ct_new = crypto.encrypt("new value")
    assert crypto.decrypt(ct_new) == "new value"


def test_rotation_drop_old_key_breaks_old_ciphertext(with_keys) -> None:
    """Operator confidence check: once the old key is removed from
    `ENCRYPTION_KEYS`, ciphertext encrypted with it stops decrypting.
    This is by design — it's how the operator knows the rotation
    actually happened."""
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    with_keys(key_a)
    old_ct = crypto.encrypt("from before rotation")

    # Drop key A; only key B remains
    with_keys(key_b)
    with pytest.raises(crypto.InvalidCiphertext):
        crypto.decrypt(old_ct)


@pytest.mark.parametrize(
    "plaintext",
    [
        "",
        "a",
        "a" * 10_000,
        "🔐 unicode key",
        "value\nwith\nnewlines",
        "trailing whitespace   ",
        '{"json": "shape", "n": 42}',
    ],
)
def test_roundtrip_across_input_shapes(with_keys, plaintext: str) -> None:
    with_keys(Fernet.generate_key().decode())
    assert crypto.decrypt(crypto.encrypt(plaintext)) == plaintext
