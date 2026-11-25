"""Symmetrische Verschlüsselung für sensible Konfigurationswerte (SMTP-Passwort).

Der Fernet-Schlüssel wird deterministisch aus dem vorhandenen ``SECRET_KEY``
abgeleitet (SHA-256 → 32 Byte → urlsafe-base64). So muss kein zusätzlicher
Schlüssel verwaltet werden; dreht man den ``SECRET_KEY``, muss das SMTP-Passwort
einmal neu gesetzt werden (alte Tokens lassen sich dann nicht mehr entschlüsseln –
das wird robust als „kein Passwort" behandelt).

Sicherheit: Der Klartext (das Passwort) wird NIE geloggt oder ausgegeben.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .settings import get_settings


def _fernet() -> Fernet:
    """Fernet-Instanz auf Basis des aus dem SECRET_KEY abgeleiteten Schlüssels."""
    secret = (get_settings().secret_key or "").encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """Verschlüsselt ``plaintext`` und liefert ein urlsafe Fernet-Token (str).

    Leerer Text ergibt einen leeren String (es wird „kein Wert" gespeichert).
    """
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Entschlüsselt ein Fernet-Token zurück zum Klartext.

    Robust: Bei leerem/ungültigem Token (z.B. nach SECRET_KEY-Wechsel) wird ein
    leerer String zurückgegeben – niemals eine Exception in Richtung Aufrufer.
    """
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""
