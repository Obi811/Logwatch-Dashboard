"""Idempotentes Initialisierungsskript für das LogWatch Dashboard.

- Legt alle Tabellen an (falls noch nicht vorhanden).
- Legt einen Seed-Admin an, falls noch KEIN Benutzer existiert
  (Benutzername aus ADMIN_USER, Passwort aus ADMIN_PASSWORD).

Mehrfaches Ausführen ist unschädlich.
"""
from __future__ import annotations

import sys

from sqlalchemy import select, text

from app.auth import hash_password
from app.db import Base, SessionLocal, engine
from app.models import User  # noqa: F401 – sorgt für Tabellen-Registrierung
from app.models import ErrorReport, HardwareFingerprint, Machine  # noqa: F401
from app.settings import get_settings


def _ensure_columns() -> None:
    """Rückwärtskompatible Mini-Migrationen für bereits bestehende Tabellen.

    ``create_all`` legt nur fehlende Tabellen an, ergänzt aber KEINE neuen
    Spalten in vorhandenen Tabellen. Hier werden daher neu hinzugekommene
    Spalten idempotent per ``ADD COLUMN IF NOT EXISTS`` nachgezogen (PostgreSQL).
    """
    if not engine.url.get_backend_name().startswith("postgresql"):
        return  # SQLite (Tests) bekommt die Spalten über create_all().
    statements = [
        "ALTER TABLE machines ADD COLUMN IF NOT EXISTS copy_warning_ack_at TIMESTAMPTZ",
        "ALTER TABLE machines ADD COLUMN IF NOT EXISTS copy_warning_ack_by VARCHAR(100)",
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def main() -> int:
    settings = get_settings()
    if not settings.database_url:
        print("FEHLER: DATABASE_URL ist nicht gesetzt (.env prüfen).", file=sys.stderr)
        return 2

    print("Lege Tabellen an (falls nicht vorhanden) ...")
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    print("Schema ist aktuell.")

    db = SessionLocal()
    try:
        existing = db.scalar(select(User).limit(1))
        if existing is not None:
            print("Es existiert bereits mindestens ein Benutzer – kein Seed-Admin nötig.")
            return 0

        if not settings.admin_password:
            print("WARNUNG: ADMIN_PASSWORD ist leer – es wird KEIN Admin angelegt.",
                  file=sys.stderr)
            return 1

        admin = User(username=settings.admin_user,
                     password_hash=hash_password(settings.admin_password))
        db.add(admin)
        db.commit()
        print(f"Seed-Admin '{settings.admin_user}' wurde angelegt.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
