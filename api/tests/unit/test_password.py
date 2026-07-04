"""Unit tests for argon2 password hashing. No DB, no FastAPI."""
import pytest

from pulse_api.auth.password import (
    hash_password,
    hash_password_async,
    verify_password,
    verify_password_async,
)


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


def test_pre_existing_hash_with_different_cost_params_still_verifies() -> None:
    """Argon2 hashes are self-describing (they embed their own time/memory
    cost, not the currently-configured one) — a hash minted under different
    settings than the ones this process runs with must still verify. This
    is what lets us raise the configured work factor for new hashes without
    a migration that touches every stored password."""
    # Generated with PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1),
    # deliberately different from this module's configured cost.
    old_hash = (
        "$argon2id$v=19$m=19456,t=2,p=1$9ErdUa8ga7jNRAthi7vlIA$"
        "nG0R2AMduNElS9oLONb6D/s/lFKj8ZvhMD/SO82X9EM"
    )
    assert verify_password("old-hash-fixture-password", old_hash) is True
    assert verify_password("wrong-password", old_hash) is False


async def test_async_wrappers_roundtrip() -> None:
    h = await hash_password_async("async-correct-horse-battery-staple")
    assert await verify_password_async("async-correct-horse-battery-staple", h) is True
    assert await verify_password_async("wrong-password", h) is False
