"""Datenbank-Setup: Engine mit Connection-Pool + Session-Factory.

Beachtet die Vorgaben der gehosteten DB: max. 25 Verbindungen, Query-Timeout 5s.
Daher ein kleiner Pool (pool_size + max_overflow <= 25) und ein serverseitiges
statement_timeout.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .settings import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_url = _settings.sqlalchemy_url

if _url.startswith("postgresql"):
    # statement_timeout = 5000 ms, passend zum Query-Limit der gehosteten DB.
    _connect_args = {
        "options": "-c statement_timeout=5000",
        "connect_timeout": 15,
    }
    engine = create_engine(
        _url,
        pool_size=5,
        max_overflow=5,          # => höchstens 10 gleichzeitige Verbindungen (< 25)
        pool_pre_ping=True,      # tote Verbindungen automatisch erkennen
        pool_recycle=1800,       # Verbindungen nach 30 min erneuern
        connect_args=_connect_args,
        future=True,
    )
else:
    # Fallback (z.B. SQLite in Tests): ohne Postgres-spezifische Optionen.
    engine = create_engine(_url, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    """FastAPI-Dependency: liefert eine Session und schließt sie zuverlässig."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
