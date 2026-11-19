"""Authentifizierung: Passwort-Hashing (bcrypt) und Session-Helfer."""
from __future__ import annotations

import bcrypt
from fastapi import Request

SESSION_USER_KEY = "user"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def login_user(request: Request, username: str) -> None:
    request.session[SESSION_USER_KEY] = username


def logout_user(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)


def current_user(request: Request) -> str | None:
    return request.session.get(SESSION_USER_KEY)
