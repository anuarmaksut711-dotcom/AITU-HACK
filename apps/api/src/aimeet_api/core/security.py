import hashlib
import secrets
from threading import BoundedSemaphore

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=2)
# Bound Argon2's memory use under concurrent login traffic (2 x 64 MiB per worker).
_password_slots = BoundedSemaphore(2)
# A valid process-local dummy hash keeps unknown-account verification on the same expensive path.
_dummy_hash = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    with _password_slots:
        return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        with _password_slots:
            valid = _hasher.verify(password_hash or _dummy_hash, password)
        return bool(password_hash) and valid
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
