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
from app.models import Comment, Customer, SmtpSettings  # noqa: F401
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
        # Rollensystem: neue Spalte `role` an der Benutzer-Tabelle.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) "
        "NOT NULL DEFAULT 'user'",
        # Kundenverwaltung: Maschine <-> Kunde und Login <-> Kunde verknüpfen.
        # Die Tabellen `customers` und `smtp_settings` werden von create_all()
        # angelegt; hier nur die neuen Fremdschlüssel-Spalten an bestehenden
        # Tabellen idempotent nachziehen (bestehende Zeilen bleiben NULL = "intern").
        "ALTER TABLE machines ADD COLUMN IF NOT EXISTS customer_id INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS customer_id INTEGER",
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _ensure_admin_role() -> None:
    """Sperrschutz bei der Rollen-Migration.

    Beim erstmaligen Hinzufügen der Spalte ``role`` bekämen alle bestehenden
    Benutzer den Default ``'user'`` – dann gäbe es keinen Admin mehr und niemand
    käme noch an die Benutzerverwaltung. Daher: existiert noch KEIN Admin, werden
    alle vorhandenen Benutzer einmalig zu ``'admin'`` hochgestuft. Idempotent –
    sobald mindestens ein Admin existiert, passiert nichts mehr.
    """
    from app.models import ROLE_ADMIN, User

    db = SessionLocal()
    try:
        admin_exists = db.scalar(
            select(User).where(User.role == ROLE_ADMIN).limit(1))
        if admin_exists is not None:
            return
        users = db.execute(select(User)).scalars().all()
        if not users:
            return  # Seed-Admin wird später in main() mit Admin-Rolle angelegt.
        for u in users:
            u.role = ROLE_ADMIN
        db.commit()
        print(f"Rollen-Migration: {len(users)} bestehende(r) Benutzer "
              "zu 'admin' hochgestuft (Sperrschutz).")
    finally:
        db.close()


def main() -> int:
    settings = get_settings()
    if not settings.database_url:
        print("FEHLER: DATABASE_URL ist nicht gesetzt (.env prüfen).", file=sys.stderr)
        return 2

    print("Lege Tabellen an (falls nicht vorhanden) ...")
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _ensure_admin_role()
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

        from app.models import ROLE_ADMIN
        admin = User(username=settings.admin_user,
                     password_hash=hash_password(settings.admin_password),
                     role=ROLE_ADMIN)
        db.add(admin)
        db.commit()
        print(f"Seed-Admin '{settings.admin_user}' wurde angelegt.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
