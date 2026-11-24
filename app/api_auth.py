"""Token-basierte Authentifizierung für die App-/REST-API (``/api/v1``).

Getrennt vom Session-Cookie-Login des Web-UIs: Mobile-Apps o.ä. melden sich per
``POST /api/v1/auth/login`` an und erhalten einen signierten Bearer-Token
(``itsdangerous``, signiert mit dem vorhandenen ``SECRET_KEY``). Der Token
enthält die ``user_id`` und hat ein Ablaufdatum (Default 30 Tage).

Sicherheit
----------
* Der Token wird NIEMALS geloggt oder in Fehlermeldungen ausgegeben.
* Alle Fehlermeldungen sind deutschsprachig und verraten keine Interna.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .settings import get_settings

# Gültigkeitsdauer eines API-Tokens (Sekunden) – 30 Tage.
TOKEN_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

# Eigener Salt, damit API-Tokens nicht mit anderen Signaturen kollidieren.
_TOKEN_SALT = "logwatch-api-v1-token"


def _serializer() -> URLSafeTimedSerializer:
    """Serializer auf Basis des vorhandenen SECRET_KEY (Session-Secret)."""
    secret = get_settings().secret_key
    return URLSafeTimedSerializer(secret_key=secret, salt=_TOKEN_SALT)


def create_access_token(user_id: int) -> str:
    """Erzeugt einen signierten, zeitgebundenen Bearer-Token für ``user_id``."""
    return _serializer().dumps({"user_id": int(user_id)})


def _parse_token(token: str, max_age: int = TOKEN_MAX_AGE_SECONDS) -> int:
    """Prüft die Signatur + das Alter und liefert die ``user_id`` zurück.

    Wirft ``HTTPException(401)`` mit deutscher Meldung bei ungültigem/abgelaufenem
    Token. Der Token-Wert taucht dabei NICHT in der Meldung auf.
    """
    try:
        data = _serializer().loads(token, max_age=max_age)
    except SignatureExpired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Der Zugriffs-Token ist abgelaufen. Bitte erneut anmelden.",
        )
    except BadSignature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Zugriffs-Token.",
        )
    user_id = data.get("user_id") if isinstance(data, dict) else None
    if not isinstance(user_id, int):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Zugriffs-Token.",
        )
    return user_id


def _extract_bearer(authorization: Optional[str]) -> str:
    """Liest den Token aus dem ``Authorization: Bearer <token>``-Header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nicht angemeldet: Bearer-Token fehlt.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Authorization-Header (erwartet: 'Bearer <token>').",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1].strip()


def get_current_api_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI-Dependency: prüft den Bearer-Token und lädt den Benutzer.

    Liefert das ``User``-Objekt oder wirft ``HTTPException(401)`` mit deutscher
    Meldung (fehlender/ungültiger/abgelaufener Token, oder Benutzer gelöscht).
    """
    token = _extract_bearer(authorization)
    user_id = _parse_token(token)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Der zugehörige Benutzer existiert nicht mehr.",
        )
    return user
