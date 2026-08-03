"""The app's one shared Fernet encryption primitive for secrets/settings
stored at rest in SQLite (AppSetting.value when is_secret=True).

Ported from a validated pattern in a sibling project (see PROJECT-PLAN.md
"Settings & secrets storage" section for the full writeup) rather than
inventing a new scheme. Design in one paragraph: Fernet (AES-128-CBC +
HMAC, via the `cryptography` package) with a key file generated on first
run and persisted (0600) in the same Docker volume as the database --
losing that file is equivalent to losing the whole data volume, since
every encrypted value becomes permanently unrecoverable. Built in from
day one with *versioned* envelope encryption so key rotation is possible
later without a bulk re-encrypt: version 1 ciphertext is unprefixed
(the common, never-rotated case); rotate_key() adds version N to a
separate keyring file, all new encrypts use it, and old ciphertext keeps
decrypting under its original key forever.
"""

from __future__ import annotations

import json
import os
import time

from cryptography.fernet import Fernet, InvalidToken

SECRETS_KEY_FILE = os.environ.get("SECRETS_KEY_FILE", "/app/data/secrets.key")
SECRETS_KEYRING_FILE = os.environ.get("SECRETS_KEYRING_FILE", "/app/data/secrets_keyring.json")


def _atomic_write_bytes(path: str, data: bytes, mode: int | None = None) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def _load_or_create_key() -> bytes:
    try:
        with open(SECRETS_KEY_FILE, "rb") as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass
    key = Fernet.generate_key()
    _atomic_write_bytes(SECRETS_KEY_FILE, key, mode=0o600)
    return key


def _load_keyring_meta() -> dict:
    """Versions >= 2 only -- version 1 always comes from SECRETS_KEY_FILE,
    never duplicated here. A missing/corrupt file means an unrotated
    deployment (the expected default), not an error."""
    try:
        with open(SECRETS_KEYRING_FILE, encoding="utf-8") as f:
            meta = json.loads(f.read())
        if isinstance(meta, dict) and "current_version" in meta:
            return meta
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[secrets_crypto] keyring file unreadable, treating as unrotated: {e}", flush=True)
    return {"current_version": 1, "keys": {}, "created_at": {}}


def _save_keyring_meta(meta: dict) -> None:
    _atomic_write_bytes(SECRETS_KEYRING_FILE, json.dumps(meta, indent=2).encode("utf-8"), mode=0o600)


def _build_keyring() -> tuple[dict[int, Fernet], int]:
    meta = _load_keyring_meta()
    ring: dict[int, Fernet] = {1: Fernet(_load_or_create_key())}
    for v_str, b64key in (meta.get("keys") or {}).items():
        try:
            ring[int(v_str)] = Fernet(b64key.encode() if isinstance(b64key, str) else b64key)
        except Exception as e:
            print(f"[secrets_crypto] keyring version {v_str} unusable, skipping: {e}", flush=True)
    return ring, int(meta.get("current_version") or 1)


_KEYRING, _CURRENT_VERSION = _build_keyring()


def rotate_key() -> int:
    """Generate a new Fernet key as version (current max + 1), persist it,
    and make it current for all FUTURE encrypt() calls. Prior versions are
    kept forever so everything already encrypted keeps decrypting."""
    meta = _load_keyring_meta()
    existing_versions = [1] + [int(v) for v in (meta.get("keys") or {})]
    new_version = max(existing_versions) + 1
    new_key = Fernet.generate_key()
    meta.setdefault("keys", {})[str(new_version)] = new_key.decode()
    meta.setdefault("created_at", {})[str(new_version)] = int(time.time())
    meta["current_version"] = new_version
    _save_keyring_meta(meta)
    global _KEYRING, _CURRENT_VERSION
    _KEYRING, _CURRENT_VERSION = _build_keyring()
    return new_version


def key_status() -> dict:
    """For a future Settings -> Security panel."""
    meta = _load_keyring_meta()
    versions = sorted(_KEYRING.keys())
    created = {"1": None}
    created.update(meta.get("created_at") or {})
    return {
        "current_version": _CURRENT_VERSION,
        "versions": versions,
        "created_at": {str(v): created.get(str(v)) for v in versions},
    }


def _versioned_token(raw_token_str: str) -> str:
    if _CURRENT_VERSION == 1:
        return raw_token_str
    return f"v{_CURRENT_VERSION}:{raw_token_str}"


def _parse_version(token_str: str) -> tuple[int, str]:
    if token_str.startswith("v") and ":" in token_str[:6]:
        prefix, _, rest = token_str.partition(":")
        try:
            v = int(prefix[1:])
            if v in _KEYRING:
                return v, rest
        except ValueError:
            pass
    return 1, token_str


def encrypt(plaintext: str | None) -> str | None:
    """str -> versioned Fernet token (str). Empty/None passes through
    unchanged -- no point encrypting "unset"."""
    if not plaintext:
        return plaintext
    token = _KEYRING[_CURRENT_VERSION].encrypt(str(plaintext).encode()).decode()
    return _versioned_token(token)


def decrypt(token: str) -> str:
    """Raises InvalidToken/ValueError on anything that isn't a valid
    token. Callers use decrypt_or_legacy() instead unless they
    specifically want that to propagate."""
    v, raw = _parse_version(str(token))
    return _KEYRING[v].decrypt(raw.encode()).decode()


def decrypt_or_legacy(value: str | None) -> str | None:
    """Best-effort decrypt for values that might predate encryption (e.g.
    a value seeded straight from an env var before this module ran).
    Never raises -- returns the input unchanged if it isn't a valid
    Fernet token for any known key version."""
    if not value:
        return value
    try:
        return decrypt(value)
    except (InvalidToken, ValueError, Exception):
        return value
