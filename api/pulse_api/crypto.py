"""Column-level encryption for in-DB secrets.

Pulse stores third-party credentials (currently the ClickUp OAuth access
token + the ClickUp webhook signing secret) as Fernet ciphertext. Plain
text storage is intentionally not supported — if a DB dump leaks or an
SQL-injection escapes RLS, the encrypted columns are still useless
without the key.

Key rotation: `ENCRYPTION_KEYS` is a comma-separated list. The first key
is used to encrypt new values; all keys are tried in order to decrypt.
To rotate: prepend a new key, run a re-encrypt data migration that
re-writes every `*_enc` column, then drop the old key from the list.

This module is the ONLY place that touches Fernet directly. Repository
functions call `encrypt(...)` before INSERT and `decrypt(...)` after
SELECT; the wire layer never sees the encrypted bytes.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from pulse_api.config import settings


class InvalidCiphertext(Exception):
    """Raised when ciphertext cannot be decrypted with any known key.

    Either the value is corrupt, was tampered with, or was encrypted with
    a key that's no longer in `ENCRYPTION_KEYS` (operator error during
    rotation). Caller must NEVER swallow this — leaking plaintext
    elsewhere on decrypt failure would be worse than the failure itself.
    """


class EncryptionKeyMissing(Exception):
    """Raised when encrypt/decrypt is called but `ENCRYPTION_KEYS` is empty.

    Settings doesn't raise at startup (dev convenience), but the first
    code path that actually needs encryption fails loudly.
    """


@lru_cache(maxsize=1)
def _multi_fernet() -> MultiFernet:
    """Parse `ENCRYPTION_KEYS` into a MultiFernet. Cached for the process
    lifetime; the cache busts only on a restart (which is when the env
    var would change anyway)."""
    raw = (settings.encryption_keys or "").strip()
    if not raw:
        raise EncryptionKeyMissing(
            "ENCRYPTION_KEYS is empty. Generate one with: python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    keys = [Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()]
    if not keys:
        raise EncryptionKeyMissing("ENCRYPTION_KEYS contained only blank entries.")
    return MultiFernet(keys)


def encrypt(plaintext: str) -> str:
    """Encrypt a string; return base64 ASCII ciphertext suitable for a
    `text` column."""
    return _multi_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a value previously produced by `encrypt`. Tries each key
    in `ENCRYPTION_KEYS` in order so rotation works without code change.
    """
    try:
        return _multi_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise InvalidCiphertext("ciphertext could not be decrypted") from exc


def reset_keys_cache() -> None:
    """Test-only: re-read settings after monkeypatching. Production code
    should never call this — keys are intended to be process-stable."""
    _multi_fernet.cache_clear()
