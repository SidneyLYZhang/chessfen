from __future__ import annotations

import hashlib
import hmac
import secrets

API_KEY_PREFIX_LENGTH = 8
API_KEY_SECRET_LENGTH = 32


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, key_hash).

    The full key is only ever returned to the caller here; only the hash is
    persisted. The secret carries 128 bits of entropy, so an unsalted SHA-256
    is sufficient (no dictionary attack surface) and lookups stay cheap.
    """
    prefix = secrets.token_hex(API_KEY_PREFIX_LENGTH // 2)
    secret = secrets.token_hex(API_KEY_SECRET_LENGTH // 2)
    full_key = f"cf_{prefix}_{secret}"
    return full_key, prefix, hash_api_key(full_key)


def parse_api_key(raw_key: str) -> tuple[str, str] | None:
    """Split a raw key into (prefix, secret), or None if malformed."""
    parts = raw_key.split("_")
    if len(parts) != 3 or parts[0] != "cf":
        return None
    prefix, secret = parts[1], parts[2]
    if not prefix or not secret:
        return None
    return prefix, secret


def key_matches(raw_key: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(raw_key), key_hash)
