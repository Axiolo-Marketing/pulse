"""Argon2id password hashing.

Argon2 is the OWASP-recommended primitive for new password storage. We
use passlib's CryptContext wrapper so the on-disk hash includes the
parameters (memory, time cost) — old hashes can be verified even if we
later raise the work factor for new hashes.
"""
from passlib.context import CryptContext

from pulse_api.config import settings

_pwd = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    argon2__time_cost=settings.password_argon2_time_cost,
    argon2__memory_cost=settings.password_argon2_memory_kb,
)


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)
