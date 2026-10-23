"""Password and device-token hashing."""
import hashlib
import secrets

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def new_device_token() -> str:
    """Random secret a device sends with every upload. Shown once, stored only as a hash."""
    return secrets.token_urlsafe(32)


def hash_device_token(token: str) -> str:
    # Tokens are long random strings, so a fast hash is enough (no salt/bcrypt needed).
    return hashlib.sha256(token.encode()).hexdigest()
