"""Tests für den admin-pflegbaren Fehlercode-Katalog.

Deckt ab:
  * RBAC: /error-codes nur für Admin (Nicht-Login → Login, user/kunde → Startseite).
  * CRUD: Anlegen, Bearbeiten, Löschen, notify-Umschalter.
  * Liste: Suche, Filter (Schweregrad, notify), Pagination.
  * Benachrichtigungslogik in mailer.send_error_notification (Katalog-Verknüpfung,
    rückwärtskompatibel).

Wie die übrigen Test-Dateien wird die Umgebung VOR dem Import der App gesetzt,
sodass eine isolierte SQLite-DB verwendet wird. SMTP-Versand wird gemockt.
"""
from __future__ import annotations

import os
import tempfile

import pytest

# --- Isolierte Testumgebung einrichten, bevor die App importiert wird ---
_TMP = tempfile.mkdtemp(prefix="logwatch_ec_test_")
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
from app.auth import hash_password  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    ROLE_ADMIN, ROLE_KUNDE, ROLE_USER,
    Customer, ErrorCode, ErrorReport, Machine, SmtpSettings, User,
)

ADMIN_PW = "test-admin-pw"
OTHER_PW = "other-pw-123"


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
    """Hält die Tabellen je Test sauber (Admin bleibt bestehen)."""
    yield
    db = SessionLocal()
    try:
        db.query(ErrorCode).delete()
        db.query(ErrorReport).delete()
        db.query(Machine).delete()
        db.query(SmtpSettings).delete()
        db.query(User).filter(User.username != "admin").delete()
        db.query(Customer).delete()
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------
def _admin() -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": "admin", "password": ADMIN_PW},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _login_as(username, role, customer_id=None) -> TestClient:
    db = SessionLocal()
    try:
        db.add(User(username=username, password_hash=hash_password(OTHER_PW),
                    role=role, customer_id=customer_id))
        db.commit()
    finally:
        db.close()
    c = TestClient(app)
    r = c.post("/login", data={"username": username, "password": OTHER_PW},
               follow_redirects=False)
    assert r.status_code == 303
    return c


def _mk_code(code, severity=3, facility=2048, idx=1, text="Testfehler",
             facility_name="FAC_TEST", notify=False, active=True) -> int:
    db = SessionLocal()
    try:
        ec = ErrorCode(code=code, severity=severity, facility=facility, idx=idx,
                       text=text, facility_name=facility_name, notify=notify,
                       active=active)
        db.add(ec)
        db.commit()
        return ec.id
    finally:
        db.close()


def _get_code(code) -> ErrorCode | None:
    db = SessionLocal()
    try:
        return db.scalar(select(ErrorCode).where(ErrorCode.code == code))
    finally:
        db.close()


def _mk_customer(notify_on_error=True, email="info@muster.de") -> int:
    db = SessionLocal()
    try:
        c = Customer(name="Muster GmbH", email=email,
                     notify_on_error=notify_on_error)
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def _mk_machine(machine_id, customer_id=None) -> None:
    db = SessionLocal()
    try:
        db.add(Machine(machine_id=machine_id, company="Acme", customer_id=customer_id))
        db.commit()
    finally:
        db.close()


def _mk_report(machine_id, text) -> ErrorReport:
    db = SessionLocal()
    try:
        r = ErrorReport(machine_id=machine_id, error_lines=text, error_count=1,
                        status="offen")
        db.add(r)
        db.commit()
        db.refresh(r)
        db.expunge(r)
        return r
    finally:
        db.close()


def _mk_smtp() -> None:
    db = SessionLocal()
    try:
        db.add(SmtpSettings(id=1, host="smtp.test", port=587,
                            from_email="noreply@test.de", security="starttls"))
        db.commit()
    finally:
        db.close()


# ==========================================================================
# (a) RBAC
# ==========================================================================
def test_error_codes_requires_login():
    c = TestClient(app)
    r = c.get("/error-codes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_error_codes_forbidden_for_user():
    c = _login_as("mitarbeiter", ROLE_USER)
    r = c.get("/error-codes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_error_codes_forbidden_for_kunde():
    cid = _mk_customer()
    c = _login_as("kunde1", ROLE_KUNDE, customer_id=cid)
    r = c.get("/error-codes", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_error_codes_create_forbidden_for_user():
    c = _login_as("mitarbeiter", ROLE_USER)
    r = c.post("/error-codes/create", data={"code": "3-1-1"},
               follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert _get_code("3-1-1") is None


# ==========================================================================
# (b) CRUD
# ==========================================================================
def test_error_code_create_parses_segments():
    admin = _admin()
    r = admin.post("/error-codes/create", data={
        "code": "2-2048-4097", "text": "DLL nicht gefunden",
        "facility_name": "FAC_USERDEFINED", "notify": "1", "active": "1"},
        follow_redirects=False)
    assert r.status_code == 303
    ec = _get_code("2-2048-4097")
    assert ec is not None
    assert ec.severity == 2 and ec.facility == 2048 and ec.idx == 4097
    assert ec.notify is True and ec.active is True
    assert ec.text == "DLL nicht gefunden"


def test_error_code_create_duplicate_rejected():
    _mk_code("3-2048-1")
    admin = _admin()
    r = admin.post("/error-codes/create", data={"code": "3-2048-1"},
                   follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        n = db.query(ErrorCode).filter(ErrorCode.code == "3-2048-1").count()
    finally:
        db.close()
    assert n == 1


def test_error_code_edit():
    cid = _mk_code("3-2048-2", text="alt", notify=False)
    admin = _admin()
    r = admin.post(f"/error-codes/{cid}/edit", data={
        "text": "neu", "facility_name": "FAC_X", "notify": "1", "active": "1"},
        follow_redirects=False)
    assert r.status_code == 303
    ec = _get_code("3-2048-2")
    assert ec.text == "neu" and ec.facility_name == "FAC_X" and ec.notify is True


def test_error_code_edit_can_unset_notify_and_active():
    cid = _mk_code("3-2048-3", notify=True, active=True)
    admin = _admin()
    # Keine Checkbox-Werte -> notify/active werden False.
    r = admin.post(f"/error-codes/{cid}/edit", data={"text": "x"},
                   follow_redirects=False)
    assert r.status_code == 303
    ec = _get_code("3-2048-3")
    assert ec.notify is False and ec.active is False


def test_error_code_toggle_notify():
    cid = _mk_code("3-2048-4", notify=False)
    admin = _admin()
    r = admin.post(f"/error-codes/{cid}/toggle-notify", follow_redirects=False)
    assert r.status_code == 303
    assert _get_code("3-2048-4").notify is True
    admin.post(f"/error-codes/{cid}/toggle-notify", follow_redirects=False)
    assert _get_code("3-2048-4").notify is False


def test_error_code_delete():
    cid = _mk_code("3-2048-5")
    admin = _admin()
    r = admin.post(f"/error-codes/{cid}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert _get_code("3-2048-5") is None


# ==========================================================================
# (c) Liste: Suche, Filter, Pagination
# ==========================================================================
def test_error_codes_list_shows_codes():
    _mk_code("3-2048-10", text="Spezialfehler XYZ")
    admin = _admin()
    r = admin.get("/error-codes")
    assert r.status_code == 200
    assert "3-2048-10" in r.text
    assert "Spezialfehler XYZ" in r.text


def test_error_codes_search_by_code_and_text():
    _mk_code("3-2048-11", text="Alpha Meldung")
    _mk_code("3-4096-12", text="Beta Meldung")
    admin = _admin()
    r = admin.get("/error-codes", params={"q": "4096"})
    assert "3-4096-12" in r.text and "3-2048-11" not in r.text
    r = admin.get("/error-codes", params={"q": "Alpha"})
    assert "3-2048-11" in r.text and "3-4096-12" not in r.text


def test_error_codes_filter_severity_and_notify():
    _mk_code("3-2048-13", severity=3, notify=True, text="Fehlereintrag")
    _mk_code("2-2048-14", severity=2, notify=False, text="Warneintrag")
    admin = _admin()
    r = admin.get("/error-codes", params={"severity": "2"})
    assert "2-2048-14" in r.text and "3-2048-13" not in r.text
    r = admin.get("/error-codes", params={"notify": "on"})
    assert "3-2048-13" in r.text and "2-2048-14" not in r.text
    r = admin.get("/error-codes", params={"notify": "off"})
    assert "2-2048-14" in r.text and "3-2048-13" not in r.text


def test_error_codes_pagination():
    db = SessionLocal()
    try:
        for i in range(120):
            db.add(ErrorCode(code=f"3-9000-{i:04d}", severity=3, facility=9000,
                             idx=i, text="x", notify=False, active=True))
        db.commit()
    finally:
        db.close()
    admin = _admin()
    r = admin.get("/error-codes", params={"q": "3-9000"})
    assert "Seite 1 von 3" in r.text
    r2 = admin.get("/error-codes", params={"q": "3-9000", "page": 2})
    assert "Seite 2 von 3" in r2.text


# ==========================================================================
# (d) Benachrichtigungslogik (mailer.send_error_notification)
# ==========================================================================
def test_notify_sends_when_catalog_code_notify_true(_mock_smtp):
    cid = _mk_customer(notify_on_error=True)
    _mk_machine("M1", customer_id=cid)
    _mk_smtp()
    _mk_code("3-2048-4097", notify=True)
    report = _mk_report("M1", "Fehler 3-2048-4097 aufgetreten")
    db = SessionLocal()
    try:
        ok = mailer.send_error_notification(db, report)
    finally:
        db.close()
    assert ok is True
    assert len(_mock_smtp) == 1


def test_notify_suppressed_when_catalog_code_notify_false(_mock_smtp):
    cid = _mk_customer(notify_on_error=True)
    _mk_machine("M2", customer_id=cid)
    _mk_smtp()
    _mk_code("3-2048-4098", notify=False)
    report = _mk_report("M2", "Fehler 3-2048-4098 aufgetreten")
    db = SessionLocal()
    try:
        ok = mailer.send_error_notification(db, report)
    finally:
        db.close()
    assert ok is False
    assert len(_mock_smtp) == 0


def test_notify_sends_if_any_known_code_is_notify_true(_mock_smtp):
    cid = _mk_customer(notify_on_error=True)
    _mk_machine("M3", customer_id=cid)
    _mk_smtp()
    _mk_code("3-2048-5000", notify=False)
    _mk_code("3-2048-5001", notify=True)
    report = _mk_report("M3", "Codes: 3-2048-5000 und 3-2048-5001")
    db = SessionLocal()
    try:
        ok = mailer.send_error_notification(db, report)
    finally:
        db.close()
    assert ok is True
    assert len(_mock_smtp) == 1


def test_notify_old_behavior_when_no_known_code(_mock_smtp):
    """Reiner Freitext ohne bekannte Codes -> altes Verhalten (Kunden-Flag)."""
    cid = _mk_customer(notify_on_error=True)
    _mk_machine("M4", customer_id=cid)
    _mk_smtp()
    # Katalog leer bzgl. dieses Berichts.
    report = _mk_report("M4", "ERROR etwas ist kaputt")
    db = SessionLocal()
    try:
        ok = mailer.send_error_notification(db, report)
    finally:
        db.close()
    assert ok is True
    assert len(_mock_smtp) == 1


def test_notify_unknown_code_not_in_catalog_uses_old_behavior(_mock_smtp):
    """Ein codeartiges Token, das NICHT im Katalog steht -> altes Verhalten."""
    cid = _mk_customer(notify_on_error=True)
    _mk_machine("M5", customer_id=cid)
    _mk_smtp()
    _mk_code("3-2048-6000", notify=False)  # anderer Code im Katalog
    report = _mk_report("M5", "Unbekannter 9-9999-9999 Fehler")
    db = SessionLocal()
    try:
        ok = mailer.send_error_notification(db, report)
    finally:
        db.close()
    assert ok is True
    assert len(_mock_smtp) == 1


def test_notify_respects_customer_flag_even_with_notify_true_code(_mock_smtp):
    """Kunden-Flag bleibt zusätzliche Bedingung: notify_on_error=False -> kein Versand."""
    cid = _mk_customer(notify_on_error=False)
    _mk_machine("M6", customer_id=cid)
    _mk_smtp()
    _mk_code("3-2048-7000", notify=True)
    report = _mk_report("M6", "Fehler 3-2048-7000")
    db = SessionLocal()
    try:
        ok = mailer.send_error_notification(db, report)
    finally:
        db.close()
    assert ok is False
    assert len(_mock_smtp) == 0
