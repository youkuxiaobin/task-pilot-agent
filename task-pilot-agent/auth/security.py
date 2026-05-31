from __future__ import annotations

import hashlib
import secrets
from typing import Optional


def generate_token(prefix: Optional[str] = None, byte_length: int = 32) -> str:
    token = secrets.token_urlsafe(byte_length)
    return f"{prefix}_{token}" if prefix else token


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left, right)

