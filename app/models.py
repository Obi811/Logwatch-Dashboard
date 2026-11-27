"""SQLAlchemy-Datenmodell des Dashboards.

Hinweis zu "Laufzeiten" (Uptime), die laut Anforderung SPÄTER kommen:
Das Modell erfasst bereits Zeitstempel (first_seen/last_seen, received_at), sodass
eine spätere Laufzeit-/Heartbeat-Auswertung ohne Schemabruch ergänzt werden kann
(z.B. durch eine zusätzliche Tabelle `heartbeats` oder Felder an `machines`).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, String, Text, Integer, DateTime, ForeignKey, Index, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


STATUS_OPEN = "offen"
STATUS_CLOSED = "abgeschlossen"

# Rollen des Rollensystems.
#  * "admin" – volle Rechte inkl. Benutzer-/Kundenverwaltung und SMTP-Einstellungen.
#  * "user"  – sieht alle internen Auswertungs-Seiten und darf kommentieren,
#              aber keine Benutzer/Kunden verwalten.
#  * "kunde" – Kunden-Login: sieht NUR die eigenen Maschinen/Meldungen/Statistik
#              (Übersicht, Maschinen, Fehlerberichte, Statistik). Kein Zugriff auf
#              Warnungen, Benutzer-/Kundenverwaltung oder SMTP.
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_KUNDE = "kunde"
ROLES = (ROLE_ADMIN, ROLE_USER, ROLE_KUNDE)

# Intervalle für den automatischen E-Mail-Sammelversand (Digest) je Kunde.
DIGEST_NONE = "none"
DIGEST_WEEKLY = "weekly"
DIGEST_MONTHLY = "monthly"
DIGEST_INTERVALS = (DIGEST_NONE, DIGEST_WEEKLY, DIGEST_MONTHLY)

# Schweregrade der Maschinen-Fehlercodes (erstes Segment des Codes "S-FFFF-N").
#  3 = Fehler, 2 = Warnung, 1 = Info. Dient nur der Anzeige/Filterung.
SEVERITY_LABELS = {3: "Fehler", 2: "Warnung", 1: "Info"}


def severity_label(severity: int | None) -> str:
    """Menschlich lesbares Label eines Schweregrads (Fallback: "Unbekannt")."""
    return SEVERITY_LABELS.get(severity, "Unbekannt")


class User(Base):
    """Interner Dashboard-Benutzer (Admin/Mitarbeiter) oder Kunden-Login."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Rolle im Rollensystem: "admin", "user" oder "kunde". Standard: "user".
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ROLE_USER, server_default=ROLE_USER)
    # Nur für Rolle "kunde" gesetzt: verknüpft das Login mit genau einem Kunden.
    # Der Kunde sieht dann ausschließlich dessen Maschinen/Meldungen.
    customer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    customer: Mapped["Customer | None"] = relationship(back_populates="users")

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_kunde(self) -> bool:
        return self.role == ROLE_KUNDE


class Customer(Base):
    """Ein Kunde (Firma) als E-Mail-Empfänger für Fehler-/Sammelmeldungen.

    Ein Kunde ist zunächst nur ein Empfänger (reine E-Mail-Adresse). Optional kann
    ihm ein Dashboard-Login (``User`` mit Rolle ``kunde``) zugeordnet werden, das
    dann nur die diesem Kunden zugeordneten Maschinen sieht.
    """
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Firma/Kundenname (Pflicht).
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Haupt-E-Mail-Empfänger (Pflicht).
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    # Optionaler Ansprechpartner.
    contact: Mapped[str] = mapped_column(String(200), default="", server_default="")
    # Freie Notiz.
    note: Mapped[str] = mapped_column(Text, default="", server_default="")

    # Sofort-Benachrichtigung bei neuem Fehler einer zugeordneten Maschine.
    notify_on_error: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true")
    # Sammelversand-Intervall: "none" | "weekly" | "monthly".
    digest_interval: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DIGEST_NONE, server_default=DIGEST_NONE)
    # Zeitpunkt des letzten erfolgreich versendeten Digests (für Fälligkeit).
    last_digest_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    machines: Mapped[list["Machine"]] = relationship(back_populates="customer")
    users: Mapped[list["User"]] = relationship(back_populates="customer")


class SmtpSettings(Base):
    """SMTP-Konfiguration für den E-Mail-Versand (genau eine Zeile, id=1).

    Das Passwort wird NIE im Klartext gespeichert, sondern mit Fernet verschlüsselt
    (Schlüssel aus ``SECRET_KEY`` abgeleitet, siehe ``app/crypto.py``).
    """
    __tablename__ = "smtp_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host: Mapped[str] = mapped_column(String(255), default="", server_default="")
    port: Mapped[int] = mapped_column(Integer, default=587, server_default="587")
    username: Mapped[str] = mapped_column(String(255), default="", server_default="")
    # Verschlüsseltes Passwort (Fernet-Token). Leer = kein Passwort hinterlegt.
    password_enc: Mapped[str] = mapped_column(Text, default="", server_default="")
    from_email: Mapped[str] = mapped_column(String(255), default="", server_default="")
    from_name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    # Transportverschlüsselung: "starttls" | "ssl" | "none".
    security: Mapped[str] = mapped_column(
        String(20), default="starttls", server_default="starttls")
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=_utcnow)


class Machine(Base):
    """Eine überwachte Maschine (identifiziert durch die vom Betreiber vergebene ID)."""
    __tablename__ = "machines"

    machine_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    company: Mapped[str] = mapped_column(String(200), default="", index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    notes: Mapped[str] = mapped_column(Text, default="")

    # Zugeordneter Kunde (optional). NULL = keiner Firma zugeordnet ("intern").
    customer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    customer: Mapped["Customer | None"] = relationship(back_populates="machines")

    # Quittierung einer Kopier-Warnung (für die App-/REST-API). Ist ein Zeitpunkt
    # gesetzt, gilt die Kopier-Warnung dieser Maschine als quittiert.
    copy_warning_ack_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    copy_warning_ack_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    fingerprints: Mapped[list["HardwareFingerprint"]] = relationship(
        back_populates="machine", cascade="all, delete-orphan")
    reports: Mapped[list["ErrorReport"]] = relationship(
        back_populates="machine", cascade="all, delete-orphan")


class HardwareFingerprint(Base):
    """Eine auf einer Maschine gesehene Hardware (zur Kopier-Erkennung)."""
    __tablename__ = "hardware_fingerprints"
    __table_args__ = (
        UniqueConstraint("machine_id", "hardware_id", name="uq_machine_hardware"),
        Index("ix_fp_machine", "machine_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("machines.machine_id", ondelete="CASCADE"), nullable=False)
    hardware_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    teamviewer_id: Mapped[str] = mapped_column(String(64), default="")
    hostname: Mapped[str] = mapped_column(String(255), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    machine: Mapped["Machine"] = relationship(back_populates="fingerprints")


class ErrorReport(Base):
    """Ein vom Client hochgeladener Fehlerbericht."""
    __tablename__ = "error_reports"
    __table_args__ = (
        Index("ix_report_machine", "machine_id"),
        Index("ix_report_status", "status"),
        Index("ix_report_timestamp", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("machines.machine_id", ondelete="CASCADE"), nullable=False)
    hardware_id: Mapped[str] = mapped_column(String(128), default="")
    teamviewer_id: Mapped[str] = mapped_column(String(64), default="")
    hostname: Mapped[str] = mapped_column(String(255), default="")

    # Zeitpunkt des Fehlers (vom Client) und Eingang im Dashboard
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    error_count: Mapped[int] = mapped_column(Integer, default=0)
    error_lines: Mapped[str] = mapped_column(Text, default="")
    log_filename: Mapped[str] = mapped_column(String(255), default="")
    log_path: Mapped[str] = mapped_column(String(500), default="")  # Pfad auf der Platte

    status: Mapped[str] = mapped_column(String(20), default=STATUS_OPEN)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")

    machine: Mapped["Machine"] = relationship(back_populates="reports")
    comments: Mapped[list["Comment"]] = relationship(
        back_populates="report", cascade="all, delete-orphan",
        order_by="Comment.created_at")


class Comment(Base):
    """Ein Kommentar eines Benutzers zu einem Fehlerbericht (Meldung)."""
    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comment_report", "report_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("error_reports.id", ondelete="CASCADE"), nullable=False)
    # Autor des Kommentars. Wird der Benutzer gelöscht, werden seine Kommentare
    # mit entfernt (cascade), damit keine verwaisten Einträge zurückbleiben.
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)

    report: Mapped["ErrorReport"] = relationship(back_populates="comments")
    author: Mapped["User"] = relationship()


class ErrorCode(Base):
    """Ein bekannter Maschinen-Fehlercode aus dem admin-pflegbaren Katalog.

    Der Code hat die Form ``S-FFFF-N`` (S = Schweregrad, FFFF = Facility,
    N = Index). Über das ``notify``-Flag steuert der Admin je Code, ob das
    Dashboard bei genau diesem Fehler sofort benachrichtigt / reagiert.
    Der Katalog wird aus ``data/error_codes_seed.json`` befüllt (idempotent,
    siehe ``init_db.py``); Admin-Änderungen am ``notify``-Flag bleiben beim
    erneuten Seed erhalten.
    """
    __tablename__ = "error_codes"
    __table_args__ = (
        Index("ix_errorcode_facility", "facility"),
        Index("ix_errorcode_severity", "severity"),
        Index("ix_errorcode_notify", "notify"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Voller Fehlercode "S-FFFF-N" – eindeutig.
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    # Zerlegte Segmente (für Filter/Anzeige).
    severity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    facility: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ``index`` ist in SQL ein Schlüsselwort – Attribut heißt ``idx``, Spalte "index".
    idx: Mapped[int | None] = mapped_column("index", Integer, nullable=True)
    # Beschreibender Text (deutsch).
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Facility-Name (z.B. "FAC_USERDEFINED").
    facility_name: Mapped[str] = mapped_column(String(100), default="", server_default="")

    # Kern-Flag: Benachrichtigt/reagiert das Dashboard bei diesem Fehler?
    notify: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    # Ist der Katalogeintrag aktiv (soft-delete / ausblenden ohne Löschen)?
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)

    @property
    def severity_label(self) -> str:
        return severity_label(self.severity)
