"""SQLAlchemy-Datenmodell des Dashboards.

Hinweis zu "Laufzeiten" (Uptime), die laut Anforderung SPÄTER kommen:
Das Modell erfasst bereits Zeitstempel (first_seen/last_seen, received_at), sodass
eine spätere Laufzeit-/Heartbeat-Auswertung ohne Schemabruch ergänzt werden kann
(z.B. durch eine zusätzliche Tabelle `heartbeats` oder Felder an `machines`).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, DateTime, ForeignKey, Index, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


STATUS_OPEN = "offen"
STATUS_CLOSED = "abgeschlossen"

# Rollen des Rollensystems. "admin" darf die Benutzerverwaltung nutzen,
# "user" darf alle Seiten ansehen und kommentieren, aber keine Benutzer verwalten.
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)


class User(Base):
    """Interner Dashboard-Benutzer (Admin/Mitarbeiter)."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Rolle im Rollensystem: "admin" (volle Rechte inkl. Benutzerverwaltung)
    # oder "user" (nur Ansehen + Kommentieren). Standard: "user".
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ROLE_USER, server_default=ROLE_USER)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class Machine(Base):
    """Eine überwachte Maschine (identifiziert durch die vom Betreiber vergebene ID)."""
    __tablename__ = "machines"

    machine_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    company: Mapped[str] = mapped_column(String(200), default="", index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    notes: Mapped[str] = mapped_column(Text, default="")

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
