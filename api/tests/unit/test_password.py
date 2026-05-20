"""Unit tests for argon2 password hashing. No DB, no FastAPI."""
import pytest

from pulse_api.auth.password import hash_password, verify_password


def test_hash_roundtrips() -> None:
    h = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", h) is True


def test_hash_rejects_wrong_password() -> None:
    h = hash_password("right-password")
    assert verify_password("wrong-password", h) is False


def test_hash_is_unique_per_call() -> None:
    """argon2 hashes include a per-call salt — same input must produce
    distinct hashes."""
    a = hash_password("same-input")
    b = hash_password("same-input")
    assert a != b
    assert verify_password("same-input", a)
    assert verify_password("same-input", b)


def test_hash_is_argon2id() -> None:
    """Sanity: the hash uses argon2id (OWASP-recommended variant)."""
    h = hash_password("anything")
    assert h.startswith("$argon2id$")


@pytest.mark.parametrize("plain", ["", "a", "x" * 1024, "🦀 unicode", "a b\tc\nd"])
def test_hash_accepts_a_variety_of_inputs(plain: str) -> None:
    h = hash_password(plain)
    assert verify_password(plain, h)
    assert not verify_password(plain + "x", h)
