"""E-Mail-Versand des LogWatch-Dashboards (SMTP, HTML-Mails).

Enthält:
* ``get_smtp_config`` – lädt die SMTP-Konfiguration (DB-Tabelle ``smtp_settings``
  mit entschlüsseltem Passwort; Fallback auf die Env-Variablen aus ``settings.py``).
* ``build_digest_html`` / ``build_error_html`` – rendern schlichte deutsche
  HTML-Mails (Sammelbericht bzw. Sofort-Fehlermeldung). Enthalten KEINE Kommentare.
* ``send_mail`` – versendet eine Mail per smtplib (STARTTLS/SSL/none).
* ``send_error_notification`` – Sofort-Mail bei neuem Fehler einer Kundenmaschine.
* ``send_customer_digest`` – Sammelbericht je Kunde (für das Zeitplan-Skript).
* ``send_test_mail`` – Testmail an eine beliebige Adresse (Admin-UI).

Robustheit: Jeder Versand ist fehlertolerant – ein SMTP-Problem darf NIE die App
oder die Ingestion zum Absturz bringen. Das SMTP-Passwort wird NIEMALS geloggt.
"""
from __future__ import annotations

import html
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .crypto import decrypt
from .models import (
    DIGEST_MONTHLY, DIGEST_WEEKLY, STATUS_OPEN,
    Customer, ErrorReport, Machine, SmtpSettings,
)
from .settings import get_settings

logger = logging.getLogger("logwatch.mailer")

# Fälligkeits-Schwellen (Tage) für den Sammelversand.
WEEKLY_DAYS = 7
MONTHLY_DAYS = 28


# ===========================================================================
# Zeit-Helfer
# ===========================================================================
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Macht einen (ggf. naiven) Zeitstempel timezone-aware (UTC-Annahme)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _fmt(dt: Optional[datetime]) -> str:
    dt = _aware(dt)
    return dt.strftime("%d.%m.%Y %H:%M") if dt else "–"


# ===========================================================================
# SMTP-Konfiguration
# ===========================================================================
@dataclass
class SmtpConfig:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""       # Klartext (im Speicher) – NIE loggen
    from_email: str = ""
    from_name: str = "LogWatch Dashboard"
    security: str = "starttls"   # "starttls" | "ssl" | "none"
    source: str = "none"         # "db" | "env" | "none" (nur zur Anzeige/Doku)

    @property
    def is_configured(self) -> bool:
        return bool(self.host and (self.from_email or self.username))


def get_smtp_config(db: Session) -> SmtpConfig:
    """Liefert die aktive SMTP-Konfiguration.

    Priorität: DB-Zeile ``smtp_settings`` (Passwort entschlüsselt). Ist dort kein
    Host hinterlegt, greift der Fallback auf die Env-Konfiguration (``settings.py``).
    """
    row = db.get(SmtpSettings, 1)
    if row is not None and (row.host or "").strip():
        return SmtpConfig(
            host=row.host.strip(),
            port=int(row.port or 587),
            username=row.username or "",
            password=decrypt(row.password_enc or ""),
            from_email=(row.from_email or "").strip(),
            from_name=(row.from_name or "LogWatch Dashboard").strip() or "LogWatch Dashboard",
            security=(row.security or "starttls"),
            source="db",
        )
    s = get_settings()
    if s.smtp_host:
        return SmtpConfig(
            host=s.smtp_host, port=s.smtp_port, username=s.smtp_user,
            password=s.smtp_password, from_email=s.smtp_from_email,
            from_name=s.smtp_from_name or "LogWatch Dashboard",
            security=s.smtp_security, source="env",
        )
    return SmtpConfig(source="none")


# ===========================================================================
# Versand (smtplib)
# ===========================================================================
def send_mail(config: SmtpConfig, to_email: str, subject: str,
              html_body: str) -> Tuple[bool, str]:
    """Versendet eine HTML-Mail. Liefert ``(ok, fehlermeldung)``.

    Fehlertolerant: fängt alle Fehler ab und gibt eine deutsche Meldung zurück
    (ohne Passwort/Interna). Wirft KEINE Exception nach außen.
    """
    if not config.is_configured:
        return False, "Keine SMTP-Konfiguration hinterlegt."
    if not (to_email or "").strip():
        return False, "Keine Empfängeradresse angegeben."

    sender = config.from_email or config.username
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((config.from_name or "LogWatch Dashboard", sender))
    msg["To"] = to_email.strip()
    msg.set_content(
        "Diese Nachricht enthält HTML-Inhalte. Bitte in einem HTML-fähigen "
        "E-Mail-Programm öffnen.")
    msg.add_alternative(html_body, subtype="html")

    try:
        if config.security == "ssl":
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(config.host, config.port, timeout=20,
                                  context=context) as server:
                if config.username:
                    server.login(config.username, config.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(config.host, config.port, timeout=20) as server:
                server.ehlo()
                if config.security == "starttls":
                    context = ssl.create_default_context()
                    server.starttls(context=context)
                    server.ehlo()
                if config.username:
                    server.login(config.username, config.password)
                server.send_message(msg)
        logger.info("E-Mail an %s versendet (Betreff: %s).", to_email, subject)
        return True, ""
    except smtplib.SMTPAuthenticationError:
        logger.warning("SMTP-Authentifizierung fehlgeschlagen (Host %s).", config.host)
        return False, "SMTP-Anmeldung fehlgeschlagen (Benutzername/Passwort prüfen)."
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        # Nur den Fehlertyp/Text loggen – niemals Zugangsdaten.
        logger.warning("SMTP-Versand an %s fehlgeschlagen: %s: %s",
                       to_email, type(exc).__name__, exc)
        return False, f"SMTP-Versand fehlgeschlagen: {type(exc).__name__}."


# ===========================================================================
# Datensammlung für den Sammelbericht (Digest)
# ===========================================================================
@dataclass
class MachineDigest:
    machine_id: str
    company: str
    reports: List[ErrorReport] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.reports)

    @property
    def open_count(self) -> int:
        return sum(1 for r in self.reports if r.status == STATUS_OPEN)


@dataclass
class DigestData:
    period_start: datetime
    period_end: datetime
    machines: List[MachineDigest] = field(default_factory=list)

    @property
    def machine_count(self) -> int:
        return len(self.machines)

    @property
    def report_count(self) -> int:
        return sum(m.total for m in self.machines)

    @property
    def open_count(self) -> int:
        return sum(m.open_count for m in self.machines)


def period_bounds(customer: Customer, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """Zeitraum des nächsten Sammelberichts (seit letztem Versand bzw. Default-Fenster)."""
    now = now or _utcnow()
    last = _aware(customer.last_digest_sent_at)
    if last is not None:
        return last, now
    if customer.digest_interval == DIGEST_MONTHLY:
        return now - timedelta(days=MONTHLY_DAYS), now
    return now - timedelta(days=WEEKLY_DAYS), now


def collect_digest_data(db: Session, customer: Customer,
                        period_start: datetime, period_end: datetime) -> DigestData:
    """Sammelt die Maschinen des Kunden und deren Fehlermeldungen im Zeitraum."""
    machines = db.execute(
        select(Machine).where(Machine.customer_id == customer.id)
        .order_by(Machine.machine_id)
    ).scalars().all()
    data = DigestData(period_start=period_start, period_end=period_end)
    for m in machines:
        reports = db.execute(
            select(ErrorReport)
            .where(ErrorReport.machine_id == m.machine_id)
            .where(ErrorReport.received_at >= period_start)
            .where(ErrorReport.received_at <= period_end)
            .order_by(ErrorReport.received_at.desc())
        ).scalars().all()
        data.machines.append(MachineDigest(
            machine_id=m.machine_id, company=m.company or "", reports=list(reports)))
    return data


# ===========================================================================
# Fälligkeit (für das Zeitplan-Skript)
# ===========================================================================
def is_digest_due(customer: Customer, now: Optional[datetime] = None) -> bool:
    """True, wenn für den Kunden ein Sammelbericht fällig ist.

    weekly: beim ersten Mal, sonst wenn >= 7 Tage seit letztem Versand.
    monthly: beim ersten Mal, sonst wenn >= 28 Tage seit letztem Versand.
    Dadurch führen mehrere Läufe pro Tag NICHT zu Doppelversand.
    """
    now = now or _utcnow()
    iv = customer.digest_interval
    if iv not in (DIGEST_WEEKLY, DIGEST_MONTHLY):
        return False
    last = _aware(customer.last_digest_sent_at)
    if last is None:
        return True
    delta = now - last
    threshold = WEEKLY_DAYS if iv == DIGEST_WEEKLY else MONTHLY_DAYS
    return delta >= timedelta(days=threshold)


# ===========================================================================
# HTML-Rendering (schlicht, deutsch, KEINE Kommentare)
# ===========================================================================
_STYLE = (
    "font-family:Arial,Helvetica,sans-serif;color:#222;line-height:1.5;")
_TABLE = (
    "border-collapse:collapse;width:100%;margin:8px 0 18px;font-size:14px;")
_TH = ("text-align:left;background:#f0f3f7;border:1px solid #d7dde6;"
       "padding:6px 8px;")
_TD = "border:1px solid #d7dde6;padding:6px 8px;vertical-align:top;"


def _excerpt(report: ErrorReport, limit: int = 160) -> str:
    """Erste Fehlerzeile (gekürzt) als Vorschau."""
    raw = (report.error_lines or "").strip()
    first = raw.split("\n", 1)[0] if raw else ""
    if len(first) > limit:
        first = first[:limit] + "…"
    return first


def _status_label(status: str) -> str:
    return "offen" if status == STATUS_OPEN else "abgeschlossen"


def build_digest_html(customer: Customer, data: DigestData) -> str:
    """Rendert den HTML-Sammelbericht für einen Kunden (ohne Kommentare)."""
    e = html.escape
    name = e(customer.name or "Kunde")
    anrede = f"Hallo {e(customer.contact)}," if (customer.contact or "").strip() else "Hallo,"
    zeitraum = f"{_fmt(data.period_start)} – {_fmt(data.period_end)}"

    parts: List[str] = []
    parts.append(f'<div style="{_STYLE}">')
    parts.append(f"<h2>LogWatch – Sammelbericht für {name}</h2>")
    parts.append(f"<p>{anrede}</p>")
    parts.append(f"<p>anbei die Zusammenfassung für den Zeitraum <strong>{e(zeitraum)}</strong>.</p>")

    # KPI-Übersicht
    parts.append(f'<table style="{_TABLE}">')
    parts.append(f'<tr><th style="{_TH}">Kennzahl</th><th style="{_TH}">Wert</th></tr>')
    parts.append(f'<tr><td style="{_TD}">Überwachte Maschinen</td>'
                 f'<td style="{_TD}">{data.machine_count}</td></tr>')
    parts.append(f'<tr><td style="{_TD}">Fehlermeldungen im Zeitraum</td>'
                 f'<td style="{_TD}">{data.report_count}</td></tr>')
    parts.append(f'<tr><td style="{_TD}">davon offen</td>'
                 f'<td style="{_TD}">{data.open_count}</td></tr>')
    parts.append("</table>")

    if data.report_count == 0:
        parts.append("<p>Im genannten Zeitraum sind keine neuen Fehlermeldungen "
                     "eingegangen. 🎉</p>")
    else:
        for md in data.machines:
            if md.total == 0:
                continue
            titel = e(md.machine_id)
            if md.company:
                titel += f" ({e(md.company)})"
            parts.append(f"<h3>{titel} – {md.total} Meldung(en), "
                         f"{md.open_count} offen</h3>")
            parts.append(f'<table style="{_TABLE}">')
            parts.append(f'<tr><th style="{_TH}">Zeit</th>'
                         f'<th style="{_TH}">Maschine</th>'
                         f'<th style="{_TH}">Fehler</th>'
                         f'<th style="{_TH}">Status</th></tr>')
            for r in md.reports:
                fehler = e(_excerpt(r)) or f"{r.error_count} Zeile(n)"
                parts.append(
                    f'<tr><td style="{_TD}">{_fmt(r.received_at)}</td>'
                    f'<td style="{_TD}">{e(r.machine_id)}</td>'
                    f'<td style="{_TD}">{fehler}</td>'
                    f'<td style="{_TD}">{_status_label(r.status)}</td></tr>')
            parts.append("</table>")

    parts.append('<p style="color:#888;font-size:12px;margin-top:24px;">'
                 "Diese E-Mail wurde automatisch vom LogWatch Dashboard erzeugt.</p>")
    parts.append("</div>")
    return "".join(parts)


def build_error_html(customer: Customer, machine: Machine, report: ErrorReport) -> str:
    """Rendert eine kompakte HTML-Sofortmeldung zu EINEM neuen Fehlerbericht."""
    e = html.escape
    anrede = f"Hallo {e(customer.contact)}," if (customer.contact or "").strip() else "Hallo,"
    firma = e(machine.company or customer.name or "")

    parts: List[str] = []
    parts.append(f'<div style="{_STYLE}">')
    parts.append("<h2>LogWatch – neue Fehlermeldung</h2>")
    parts.append(f"<p>{anrede}</p>")
    parts.append("<p>für eine Ihrer überwachten Maschinen ist eine neue "
                 "Fehlermeldung eingegangen:</p>")
    parts.append(f'<table style="{_TABLE}">')
    parts.append(f'<tr><th style="{_TH}">Maschine</th>'
                 f'<td style="{_TD}">{e(machine.machine_id)}</td></tr>')
    if firma:
        parts.append(f'<tr><th style="{_TH}">Firma</th>'
                     f'<td style="{_TD}">{firma}</td></tr>')
    if report.hostname:
        parts.append(f'<tr><th style="{_TH}">Hostname</th>'
                     f'<td style="{_TD}">{e(report.hostname)}</td></tr>')
    parts.append(f'<tr><th style="{_TH}">Zeitpunkt</th>'
                 f'<td style="{_TD}">{_fmt(report.received_at)}</td></tr>')
    parts.append(f'<tr><th style="{_TH}">Anzahl Fehlerzeilen</th>'
                 f'<td style="{_TD}">{report.error_count}</td></tr>')
    parts.append(f'<tr><th style="{_TH}">Status</th>'
                 f'<td style="{_TD}">{_status_label(report.status)}</td></tr>')
    parts.append("</table>")

    excerpt = _excerpt(report, limit=600)
    if excerpt:
        parts.append("<p><strong>Auszug:</strong></p>")
        parts.append(
            '<pre style="background:#f6f8fa;border:1px solid #d7dde6;padding:10px;'
            'border-radius:4px;white-space:pre-wrap;font-size:13px;">'
            f"{e(excerpt)}</pre>")

    parts.append('<p style="color:#888;font-size:12px;margin-top:24px;">'
                 "Diese E-Mail wurde automatisch vom LogWatch Dashboard erzeugt.</p>")
    parts.append("</div>")
    return "".join(parts)


# ===========================================================================
# Orchestrierung
# ===========================================================================
def send_test_mail(db: Session, to_email: str) -> Tuple[bool, str]:
    """Sendet eine kleine Testmail an ``to_email`` mit der aktiven SMTP-Konfig."""
    config = get_smtp_config(db)
    if not config.is_configured:
        return False, ("Es ist keine SMTP-Konfiguration hinterlegt. Bitte zuerst "
                       "Host, Absender usw. speichern.")
    body = (
        f'<div style="{_STYLE}"><h2>LogWatch – SMTP-Test</h2>'
        "<p>Diese Testmail bestätigt, dass die SMTP-Konfiguration des "
        "LogWatch Dashboards funktioniert.</p>"
        f"<p>Gesendet am {_fmt(_utcnow())} (UTC).</p></div>")
    return send_mail(config, to_email, "LogWatch Dashboard – SMTP-Test", body)


def send_error_notification(db: Session, report: ErrorReport) -> bool:
    """Best-effort Sofort-Mail bei neuem Fehler einer Kundenmaschine.

    Sendet nur, wenn die Maschine einem Kunden mit ``notify_on_error=True``
    zugeordnet ist und eine SMTP-Konfiguration vorhanden ist. Wirft NIE eine
    Exception (Ingestion darf nicht beeinträchtigt werden).
    """
    try:
        machine = db.get(Machine, report.machine_id)
        if machine is None or machine.customer_id is None:
            return False
        customer = db.get(Customer, machine.customer_id)
        if customer is None or not customer.notify_on_error:
            return False
        if not (customer.email or "").strip():
            return False
        config = get_smtp_config(db)
        if not config.is_configured:
            logger.info("Sofort-Mail übersprungen: keine SMTP-Konfiguration.")
            return False
        subject = f"LogWatch: neue Fehlermeldung – {machine.machine_id}"
        ok, err = send_mail(config, customer.email, subject,
                            build_error_html(customer, machine, report))
        if not ok:
            logger.warning("Sofort-Mail an Kunde %s fehlgeschlagen: %s",
                           customer.id, err)
        return ok
    except Exception as exc:  # noqa: BLE001 – Ingestion niemals stören
        logger.warning("Sofort-Mail-Hook mit Fehler übersprungen: %s: %s",
                       type(exc).__name__, exc)
        return False


def send_customer_digest(db: Session, customer: Customer, *, dry_run: bool = False,
                         now: Optional[datetime] = None) -> dict:
    """Erzeugt (und versendet, außer bei ``dry_run``) den Sammelbericht eines Kunden.

    Bei Erfolg wird ``last_digest_sent_at`` aktualisiert (nicht im Dry-Run).
    Liefert ein Ergebnis-Dict mit ``sent``/``skipped``/``error`` und Kennzahlen.
    """
    now = now or _utcnow()
    start, end = period_bounds(customer, now)
    data = collect_digest_data(db, customer, start, end)

    if not (customer.email or "").strip():
        return {"customer_id": customer.id, "name": customer.name, "sent": False,
                "error": "Keine E-Mail-Adresse hinterlegt.", "reports": data.report_count}

    if dry_run:
        return {"customer_id": customer.id, "name": customer.name, "sent": False,
                "dry_run": True, "reports": data.report_count,
                "machines": data.machine_count}

    config = get_smtp_config(db)
    if not config.is_configured:
        return {"customer_id": customer.id, "name": customer.name, "sent": False,
                "error": "Keine SMTP-Konfiguration hinterlegt.",
                "reports": data.report_count}

    subject = f"LogWatch – Sammelbericht ({customer.name})"
    ok, err = send_mail(config, customer.email, subject,
                        build_digest_html(customer, data))
    if ok:
        customer.last_digest_sent_at = now
        db.commit()
        return {"customer_id": customer.id, "name": customer.name, "sent": True,
                "reports": data.report_count, "machines": data.machine_count}
    return {"customer_id": customer.id, "name": customer.name, "sent": False,
            "error": err, "reports": data.report_count}
