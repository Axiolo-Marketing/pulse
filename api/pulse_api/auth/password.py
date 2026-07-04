"""Argon2id password hashing.

Argon2 is the OWASP-recommended primitive for new password storage. We use
argon2-cffi's ``PasswordHasher`` directly (passlib's CryptContext wrapper is
unmaintained upstream and argon2-cffi is already a direct dependency, so the
wrapper bought us nothing but an extra layer to trust). Argon2 encoded
hashes are self-describing — they embed the algorithm variant, version, and
cost parameters (time/memory/parallelism) — so hashes created under an old
cost setting still verify correctly even after ``settings`` raises the work
factor for new hashes; there is nothing to migrate.

Hashing is CPU-bound and, at the configured cost, takes tens of
milliseconds — long enough to stall the event loop if called directly from
an async route handler. The ``_async`` wrappers below push the call onto
``anyio``'s worker thread pool so request handling for other in-flight
requests isn't blocked. Route code should prefer those; the sync functions
stay for unit tests and any non-async callers.
"""
import anyio
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from pulse_api.config import settings

_hasher = PasswordHasher(
    time_cost=settings.password_argon2_time_cost,
    memory_cost=settings.password_argon2_memory_kb,
)


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False


async def hash_password_async(plain: str) -> str:
    """Off-event-loop variant of :func:`hash_password` for async route handlers."""
    return await anyio.to_thread.run_sync(hash_password, plain)


async def verify_password_async(plain: str, hashed: str) -> bool:
    """Off-event-loop variant of :func:`verify_password` for async route handlers."""
    return await anyio.to_thread.run_sync(verify_password, plain, hashed)
