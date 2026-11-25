"""Tests für Kundenverwaltung, Rollen-Scoping ('kunde'), SMTP-Settings,
Mail-Rendering und die Fälligkeitslogik des Digest-Skripts.

Wie die übrigen Test-Dateien wird die Umgebung VOR dem Import der App gesetzt,
sodass eine isolierte SQLite-DB verwendet wird. SMTP-Versand wird gemockt – es
geht nie echter Netzwerkverkehr hinaus.
"""
from __future__ import annotations

import os
import tempfile
from datetime import timedelta

import pytest

# --- Isolierte Testumgebung einrichten, bevor die App importiert wird ---
_TMP = tempfile.mkdtemp(prefix="logwatch_cust_test_")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{os.path.join(_TMP, 'test.db')}")
os.environ.setdefault("UPLOAD_TOKEN", "test-upload-token")
os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pw")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("UPLOAD_DIR", os.path.join(_TMP, "uploads"))
os.environ.setdefault("UPDATE_CHECK_ENABLED", "0")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import mailer  # noqa: E402
from app import send_digests  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.crypto import decrypt  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    DIGEST_MONTHLY, DIGEST_NONE, DIGEST_WEEKLY, ROLE_ADMIN, ROLE_KUNDE,
    Customer, ErrorReport, Machine, SmtpSettings, User,
)

ADMIN_PW = "test-admin-pw"
KUNDE_PW = "kunde-passwort-123"


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.add(User(username="admin", password_hash=hash_password(ADMIN_PW),
                    role=ROLE_ADMIN))
        db.commit()
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _mock_smtp(monkeypatch):
    """Fängt JEDEN SMTP-Versand ab – nie echter Netzwerkverkehr."""
    sent = []

    def _fake_send(config, to_email, subject, html_body):
        sent.append({"to": to_email, "subject": subject, "html": html_body})
        return True, ""

    monkeypatch.setattr(mailer, "send_mail", _fake_send)
    return sent


@pytest.fixture(autouse=True)
def _clean_tables():
    """Hält die Kundentabellen je Test sauber (Admin bleibt bestehen)."""
    yield
    db = SessionLocal()
    try:
        db.query(ErrorReport).delete()
        db.query(Machine).delete()
        db.query(SmtpSettings).delete()
        db.query(User).filter(User.username != "admin").delete()
        db.query(Customer).delete()
        db.commit()
    finally:
        db.close()


def _admin() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": "admin", "password": ADMIN_PW},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _mk_customer(name="Muster GmbH", email="info@muster.de",
                 digest_interval=DIGEST_NONE, notify_on_error=True,
                 last_sent=None) -> int:
    db = SessionLocal()
    try:
        c = Customer(name=name, email=email, digest_interval=digest_interval,
                     notify_on_error=notify_on_error, last_digest_sent_at=last_sent)
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def _mk_machine(machine_id, customer_id=None, company="Acme") -> None:
    db = SessionLocal()
    try:
        db.add(Machine(machine_id=machine_id, company=company, customer_id=customer_id))
        db.commit()
    finally:
        db.close()


def _mk_report(machine_id, text="ERROR kaputt", status="offen") -> None:
    db = SessionLocal()
    try:
        db.add(ErrorReport(machine_id=machine_id, error_lines=text, error_count=1,
                           status=status))
        db.commit()
    finally:
        db.close()


def _mk_kunde_login(username, customer_id) -> TestClient:
    db = SessionLocal()
    try:
        db.add(User(username=username, password_hash=hash_password(KUNDE_PW),
                    role=ROLE_KUNDE, customer_id=customer_id))
        db.commit()
    finally:
        db.close()
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": KUNDE_PW},
               follow_redirects=False)
    assert r.status_code == 303
    return c


# ==========================================================================
# (a) Kunden-CRUD (nur Admin)
# ==========================================================================
def test_customers_requires_admin():
    c = TestClient(app)
    r = c.get("/customers", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_customer_create_edit_delete():
    admin = _admin()
    r = admin.post("/customers/create", data={
        "name": "Neue Firma", "email": "kontakt@neu.de",
        "digest_interval": "weekly", "notify_on_error": "1"},
        follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        cust = db.scalar(select(Customer).where(Customer.name == "Neue Firma"))
        assert cust is not None
        assert cust.email == "kontakt@neu.de"
        assert cust.digest_interval == "weekly"
        assert cust.notify_on_error is True
        cid = cust.id
    finally:
        db.close()

    # Bearbeiten
    r = admin.post(f"/customers/{cid}/edit", data={
        "name": "Geänderte Firma", "email": "neu@neu.de",
        "digest_interval": "monthly"}, follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        cust = db.get(Customer, cid)
        assert cust.name == "Geänderte Firma"
        assert cust.digest_interval == "monthly"
        assert cust.notify_on_error is False  # Checkbox nicht gesetzt
    finally:
        db.close()

    # Löschen
    r = admin.post(f"/customers/{cid}/delete", follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        assert db.get(Customer, cid) is None
    finally:
        db.close()


def test_customer_create_invalid_email_rejected():
    admin = _admin()
    r = admin.post("/customers/create", data={
        "name": "X", "email": "keine-email", "digest_interval": "none"},
        follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        assert db.scalar(select(Customer).where(Customer.name == "X")) is None
    finally:
        db.close()


def test_machine_assignment_and_unassign():
    admin = _admin()
    cid = _mk_customer()
    _mk_machine("M-100")
    r = admin.post("/customers/assign-machine",
                   data={"machine_id": "M-100", "customer_id": str(cid)},
                   follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        assert db.get(Machine, "M-100").customer_id == cid
    finally:
        db.close()
    # Wieder intern (kein Kunde)
    r = admin.post("/customers/assign-machine",
                   data={"machine_id": "M-100", "customer_id": ""},
                   follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        assert db.get(Machine, "M-100").customer_id is None
    finally:
        db.close()


def test_delete_customer_unassigns_machine():
    admin = _admin()
    cid = _mk_customer()
    _mk_machine("M-DEL", customer_id=cid)
    r = admin.post(f"/customers/{cid}/delete", follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        assert db.get(Machine, "M-DEL").customer_id is None
    finally:
        db.close()


# ==========================================================================
# (b) Rollen-Scoping für 'kunde'
# ==========================================================================
def test_kunde_sees_only_own_machines():
    cid_a = _mk_customer(name="Kunde A", email="a@a.de")
    cid_b = _mk_customer(name="Kunde B", email="b@b.de")
    _mk_machine("A-1", customer_id=cid_a)
    _mk_machine("B-1", customer_id=cid_b)
    _mk_machine("INTERN", customer_id=None)
    kunde = _mk_kunde_login("kunde_a", cid_a)

    r = kunde.get("/machines")
    assert r.status_code == 200
    assert "A-1" in r.text
    assert "B-1" not in r.text
    assert "INTERN" not in r.text


def test_kunde_foreign_machine_detail_forbidden():
    cid_a = _mk_customer(name="Kunde A", email="a@a.de")
    cid_b = _mk_customer(name="Kunde B", email="b@b.de")
    _mk_machine("A-1", customer_id=cid_a)
    _mk_machine("B-1", customer_id=cid_b)
    kunde = _mk_kunde_login("kunde_a", cid_a)

    # eigene Maschine ok
    assert kunde.get("/machines/A-1").status_code == 200
    # fremde Maschine → Redirect (Forbidden-Handler)
    r = kunde.get("/machines/B-1", follow_redirects=False)
    assert r.status_code == 303


def test_kunde_blocked_from_admin_and_warning_pages():
    cid = _mk_customer()
    kunde = _mk_kunde_login("kunde_x", cid)
    for path in ("/warnings", "/users", "/customers", "/settings/smtp"):
        r = kunde.get(path, follow_redirects=False)
        assert r.status_code == 303, f"{path} sollte für Kunden gesperrt sein"


def test_kunde_report_scoping():
    cid_a = _mk_customer(name="Kunde A", email="a@a.de")
    cid_b = _mk_customer(name="Kunde B", email="b@b.de")
    _mk_machine("A-1", customer_id=cid_a)
    _mk_machine("B-1", customer_id=cid_b)
    _mk_report("A-1", text="ERROR bei A")
    _mk_report("B-1", text="ERROR bei B")
    kunde = _mk_kunde_login("kunde_a", cid_a)

    r = kunde.get("/reports")
    assert r.status_code == 200
    assert "ERROR bei A" in r.text or "A-1" in r.text
    assert "B-1" not in r.text


# ==========================================================================
# (c) SMTP-Settings: Passwort verschlüsselt, nie im Klartext
# ==========================================================================
def test_smtp_settings_password_encrypted():
    admin = _admin()
    secret_pw = "super-geheimes-smtp-pw"
    r = admin.post("/settings/smtp", data={
        "host": "smtp.example.com", "port": "587", "username": "user@example.com",
        "password": secret_pw, "from_email": "logwatch@example.com",
        "from_name": "LogWatch", "security": "starttls"}, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    try:
        row = db.get(SmtpSettings, 1)
        assert row is not None
        assert row.host == "smtp.example.com"
        # Klartext darf NICHT in der DB stehen
        assert secret_pw not in (row.password_enc or "")
        # ... lässt sich aber wieder entschlüsseln
        assert decrypt(row.password_enc) == secret_pw
    finally:
        db.close()

    # GET-Formular enthält das Passwort nicht im Klartext
    page = admin.get("/settings/smtp")
    assert page.status_code == 200
    assert secret_pw not in page.text


def test_smtp_password_not_overwritten_when_blank():
    admin = _admin()
    admin.post("/settings/smtp", data={
        "host": "smtp.example.com", "port": "587", "username": "u",
        "password": "erst-pw", "from_email": "x@example.com",
        "from_name": "LW", "security": "starttls"}, follow_redirects=False)
    # Zweiter Save ohne Passwort → bestehendes bleibt erhalten
    admin.post("/settings/smtp", data={
        "host": "smtp2.example.com", "port": "465", "username": "u",
        "password": "", "from_email": "x@example.com",
        "from_name": "LW", "security": "ssl"}, follow_redirects=False)
    db = SessionLocal()
    try:
        row = db.get(SmtpSettings, 1)
        assert row.host == "smtp2.example.com"
        assert decrypt(row.password_enc) == "erst-pw"
    finally:
        db.close()


def test_smtp_test_mail_uses_mock(_mock_smtp):
    admin = _admin()
    admin.post("/settings/smtp", data={
        "host": "smtp.example.com", "port": "587", "username": "u",
        "password": "pw", "from_email": "x@example.com",
        "from_name": "LW", "security": "starttls"}, follow_redirects=False)
    r = admin.post("/settings/smtp/test", data={"test_email": "ziel@example.com"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert any(m["to"] == "ziel@example.com" for m in _mock_smtp)


# ==========================================================================
# (d) Mail-Rendering: Kennzahlen vorhanden, KEINE Kommentare
# ==========================================================================
def test_digest_html_has_kpis_and_no_comments():
    cid = _mk_customer(name="Render GmbH", email="r@r.de")
    _mk_machine("R-1", customer_id=cid)
    _mk_report("R-1", text="ERROR Festplatte voll")
    db = SessionLocal()
    try:
        customer = db.get(Customer, cid)
        start, end = mailer.period_bounds(customer)
        data = mailer.collect_digest_data(db, customer, start, end)
        html_out = mailer.build_digest_html(customer, data)
    finally:
        db.close()
    # Kennzahlen
    assert "Überwachte Maschinen" in html_out
    assert "Fehlermeldungen im Zeitraum" in html_out
    assert "R-1" in html_out
    # KEINE Kommentare im Kundenbericht
    assert "Kommentar" not in html_out


def test_error_html_renders():
    cid = _mk_customer(name="Err GmbH", email="e@e.de")
    _mk_machine("E-1", customer_id=cid)
    _mk_report("E-1", text="ERROR Lüfter")
    db = SessionLocal()
    try:
        customer = db.get(Customer, cid)
        machine = db.get(Machine, "E-1")
        report = db.scalar(select(ErrorReport).where(ErrorReport.machine_id == "E-1"))
        html_out = mailer.build_error_html(customer, machine, report)
    finally:
        db.close()
    assert "E-1" in html_out
    assert "Kommentar" not in html_out


# ==========================================================================
# (e) Sofort-Fehler-Mail über Upload-API (gemockt)
# ==========================================================================
def test_error_notification_on_upload(_mock_smtp):
    import json
    cid = _mk_customer(name="Notify GmbH", email="notify@n.de", notify_on_error=True)
    _mk_machine("N-1", customer_id=cid)
    # SMTP-Konfig anlegen (sonst kein Versand)
    admin = _admin()
    admin.post("/settings/smtp", data={
        "host": "smtp.example.com", "port": "587", "username": "u",
        "password": "pw", "from_email": "x@example.com",
        "from_name": "LW", "security": "starttls"}, follow_redirects=False)

    c = TestClient(app)
    payload = {"machine_id": "N-1", "company": "Acme", "error_count": 1,
               "error_lines": ["ERROR kritisch"]}
    r = c.post("/api/v1/reports", data={"payload": json.dumps(payload)},
               headers={"X-Upload-Token": "test-upload-token"})
    assert r.status_code == 201
    assert any(m["to"] == "notify@n.de" for m in _mock_smtp)


def test_no_error_mail_for_unassigned_machine(_mock_smtp):
    import json
    admin = _admin()
    admin.post("/settings/smtp", data={
        "host": "smtp.example.com", "port": "587", "username": "u",
        "password": "pw", "from_email": "x@example.com",
        "from_name": "LW", "security": "starttls"}, follow_redirects=False)
    c = TestClient(app)
    payload = {"machine_id": "UNASSIGNED", "company": "Acme", "error_count": 1,
               "error_lines": ["ERROR"]}
    r = c.post("/api/v1/reports", data={"payload": json.dumps(payload)},
               headers={"X-Upload-Token": "test-upload-token"})
    assert r.status_code == 201
    assert _mock_smtp == []


# ==========================================================================
# (f) Fälligkeitslogik + kein Doppelversand (Digest-Skript)
# ==========================================================================
def test_is_digest_due_weekly_first_time():
    cid = _mk_customer(digest_interval=DIGEST_WEEKLY, last_sent=None)
    db = SessionLocal()
    try:
        assert mailer.is_digest_due(db.get(Customer, cid)) is True
    finally:
        db.close()


def test_is_digest_due_weekly_not_due_recent():
    from app.mailer import _utcnow
    cid = _mk_customer(digest_interval=DIGEST_WEEKLY,
                       last_sent=_utcnow() - timedelta(days=2))
    db = SessionLocal()
    try:
        assert mailer.is_digest_due(db.get(Customer, cid)) is False
    finally:
        db.close()


def test_is_digest_due_none_never():
    cid = _mk_customer(digest_interval=DIGEST_NONE)
    db = SessionLocal()
    try:
        assert mailer.is_digest_due(db.get(Customer, cid)) is False
    finally:
        db.close()


def test_send_digests_no_double_send(_mock_smtp):
    cid = _mk_customer(name="Weekly GmbH", email="w@w.de",
                       digest_interval=DIGEST_WEEKLY, last_sent=None)
    _mk_machine("W-1", customer_id=cid)
    _mk_report("W-1", text="ERROR wöchentlich")
    # SMTP-Konfig
    _admin().post("/settings/smtp", data={
        "host": "smtp.example.com", "port": "587", "username": "u",
        "password": "pw", "from_email": "x@example.com",
        "from_name": "LW", "security": "starttls"}, follow_redirects=False)

    db = SessionLocal()
    try:
        # Erster Lauf → versendet
        res1 = send_digests.run(db, dry_run=False)
        assert any(r.get("sent") for r in res1)
        assert len(_mock_smtp) == 1
        # Zweiter Lauf am selben Tag → nicht mehr fällig, kein Doppelversand
        res2 = send_digests.run(db, dry_run=False)
        assert not any(r.get("sent") for r in res2)
        assert len(_mock_smtp) == 1
    finally:
        db.close()


def test_send_digests_dry_run_does_not_send(_mock_smtp):
    cid = _mk_customer(name="Dry GmbH", email="d@d.de",
                       digest_interval=DIGEST_WEEKLY, last_sent=None)
    _mk_machine("D-1", customer_id=cid)
    _mk_report("D-1")
    db = SessionLocal()
    try:
        res = send_digests.run(db, dry_run=True)
        assert any(r.get("dry_run") for r in res)
        assert _mock_smtp == []
        # last_digest_sent_at bleibt None im Dry-Run
        assert db.get(Customer, cid).last_digest_sent_at is None
    finally:
        db.close()
