"""Integrationstests für das LogWatch Dashboard (TestClient + temporäre SQLite-DB).

Die Tests setzen Umgebungsvariablen VOR dem Import der App, damit eine isolierte
SQLite-Datenbank und ein bekanntes Upload-Token verwendet werden – die echte
PostgreSQL-DB wird dadurch nicht berührt.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

# --- Isolierte Testumgebung einrichten, bevor die App importiert wird ---
_TMP = tempfile.mkdtemp(prefix="logwatch_dash_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ["UPLOAD_TOKEN"] = "test-upload-token"
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-admin-pw"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["UPDATE_CHECK_ENABLED"] = "0"  # keine GitHub-Abfrage in Tests

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import STATUS_CLOSED, STATUS_OPEN, ErrorReport, User  # noqa: E402

UPLOAD_TOKEN = "test-upload-token"


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.add(User(username="admin", password_hash=hash_password("test-admin-pw")))
        db.commit()
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def logged_in(client):
    r = client.post("/login", data={"username": "admin", "password": "test-admin-pw"},
                    follow_redirects=False)
    assert r.status_code == 303
    return client


def _upload(client, machine_id, hardware_id, with_logfile=True, error_lines=None):
    payload = {
        "machine_id": machine_id,
        "company": "Acme GmbH",
        "hardware_id": hardware_id,
        "teamviewer_id": "123456789",
        "hostname": "PC-01",
        "timestamp": "2026-01-15T10:00:00+00:00",
        "error_count": len(error_lines or ["ERROR etwas ist kaputt"]),
        "error_lines": error_lines or ["ERROR etwas ist kaputt"],
        "log_filename": "app.log",
    }
    files = {}
    if with_logfile:
        files["logfile"] = ("app.log", b"line1\nERROR etwas ist kaputt\nline3\n", "text/plain")
    return client.post(
        "/api/v1/reports",
        data={"payload": json.dumps(payload)},
        files=files or None,
        headers={"X-Upload-Token": UPLOAD_TOKEN},
    )


# --------------------------------------------------------------------------
# (a) Login-Seite erreichbar + Healthcheck
# --------------------------------------------------------------------------
def test_health(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_page_reachable(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Anmelden" in r.text


def test_protected_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_login_wrong_password(client):
    r = client.post("/login", data={"username": "admin", "password": "falsch"},
                    follow_redirects=False)
    assert r.status_code == 401


# --------------------------------------------------------------------------
# (b) Upload-API nimmt Report + Logdatei an, legt in DB + Platte ab
# --------------------------------------------------------------------------
def test_upload_requires_token(client):
    r = client.post("/api/v1/reports",
                    data={"payload": json.dumps({"machine_id": "M1"})})
    assert r.status_code in (401, 422)  # fehlender/falscher Token


def test_upload_wrong_token(client):
    r = client.post("/api/v1/reports",
                    data={"payload": json.dumps({"machine_id": "M1"})},
                    headers={"X-Upload-Token": "falsch"})
    assert r.status_code == 401


def test_upload_creates_report_and_logfile(client):
    r = _upload(client, "MACHINE-A", "HW-AAA")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "ok"
    rid = body["report_id"]

    db = SessionLocal()
    try:
        report = db.get(ErrorReport, rid)
        assert report is not None
        assert report.machine_id == "MACHINE-A"
        assert report.error_count == 1
        assert "etwas ist kaputt" in report.error_lines
        assert report.log_path and os.path.isfile(report.log_path)
        with open(report.log_path, "rb") as fh:
            assert b"ERROR etwas ist kaputt" in fh.read()
    finally:
        db.close()


# --------------------------------------------------------------------------
# (c) Zwei Uploads: gleiche machine_id, unterschiedliche hardware_id => Warnung
# --------------------------------------------------------------------------
def test_copy_warning(logged_in):
    client = logged_in
    r1 = _upload(client, "COPY-1", "HW-ORIGINAL")
    assert r1.status_code == 201
    assert r1.json()["copy_warning"] is False

    r2 = _upload(client, "COPY-1", "HW-DIFFERENT")
    assert r2.status_code == 201
    assert r2.json()["copy_warning"] is True

    # Warnungsseite zeigt die Maschine
    w = client.get("/warnings")
    assert w.status_code == 200
    assert "COPY-1" in w.text
    assert "HW-ORIGINAL" in w.text
    assert "HW-DIFFERENT" in w.text


# --------------------------------------------------------------------------
# (d) Abschließen eines Reports funktioniert (und wieder öffnen)
# --------------------------------------------------------------------------
def test_close_and_reopen(logged_in):
    client = logged_in
    r = _upload(client, "CLOSE-ME", "HW-X")
    rid = r.json()["report_id"]

    # Abschließen
    resp = client.post(f"/reports/{rid}/close", data={"note": "erledigt"},
                       follow_redirects=False)
    assert resp.status_code == 303
    db = SessionLocal()
    try:
        rep = db.get(ErrorReport, rid)
        assert rep.status == STATUS_CLOSED
        assert rep.closed_by == "admin"
        assert rep.closed_at is not None
        assert rep.note == "erledigt"
    finally:
        db.close()

    # Wieder öffnen
    resp = client.post(f"/reports/{rid}/reopen", follow_redirects=False)
    assert resp.status_code == 303
    db = SessionLocal()
    try:
        rep = db.get(ErrorReport, rid)
        assert rep.status == STATUS_OPEN
        assert rep.closed_at is None
    finally:
        db.close()


def test_logfile_download(logged_in):
    client = logged_in
    r = _upload(client, "DL-1", "HW-DL")
    rid = r.json()["report_id"]
    resp = client.get(f"/reports/{rid}/logfile")
    assert resp.status_code == 200
    assert b"ERROR etwas ist kaputt" in resp.content


def test_stats_endpoint(logged_in):
    client = logged_in
    r = client.get("/api/stats/errors-per-day?days=7")
    assert r.status_code == 200
    data = r.json()
    assert len(data["labels"]) == 7
    assert len(data["values"]) == 7


def test_pages_render(logged_in):
    client = logged_in
    for path in ["/", "/machines", "/reports", "/warnings", "/statistik"]:
        resp = client.get(path)
        assert resp.status_code == 200, path


# --------------------------------------------------------------------------
# (e) Benutzerverwaltung: anlegen, Login, Validierung, Passwort, Löschen
# --------------------------------------------------------------------------
def test_users_requires_login(client):
    r = client.get("/users", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_create_user_and_login(logged_in):
    client = logged_in
    r = client.post("/users/create",
                    data={"username": "neuer", "password": "geheim12345",
                          "password2": "geheim12345"},
                    follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    try:
        u = db.scalar(select(User).where(User.username == "neuer"))
        assert u is not None
    finally:
        db.close()

    # Der neue Benutzer kann sich einloggen
    fresh = TestClient(app)
    lr = fresh.post("/login", data={"username": "neuer", "password": "geheim12345"},
                    follow_redirects=False)
    assert lr.status_code == 303


def test_create_user_duplicate_rejected(logged_in):
    client = logged_in
    before = _user_count()
    r = client.post("/users/create",
                    data={"username": "admin", "password": "geheim12345",
                          "password2": "geheim12345"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _user_count() == before  # kein neuer Benutzer


def test_create_user_password_mismatch(logged_in):
    client = logged_in
    before = _user_count()
    r = client.post("/users/create",
                    data={"username": "xyzuser", "password": "geheim12345",
                          "password2": "anders98765"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _user_count() == before


def test_create_user_short_password(logged_in):
    client = logged_in
    before = _user_count()
    r = client.post("/users/create",
                    data={"username": "kurzpw", "password": "123", "password2": "123"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _user_count() == before


def test_change_password(logged_in):
    client = logged_in
    client.post("/users/create",
                data={"username": "pwuser", "password": "altpw12345",
                      "password2": "altpw12345"}, follow_redirects=False)
    db = SessionLocal()
    try:
        uid = db.scalar(select(User.id).where(User.username == "pwuser"))
    finally:
        db.close()
    r = client.post(f"/users/{uid}/password",
                    data={"password": "neupw67890", "password2": "neupw67890"},
                    follow_redirects=False)
    assert r.status_code == 303
    fresh = TestClient(app)
    lr = fresh.post("/login", data={"username": "pwuser", "password": "neupw67890"},
                    follow_redirects=False)
    assert lr.status_code == 303


def test_cannot_delete_self(logged_in):
    client = logged_in
    db = SessionLocal()
    try:
        admin_id = db.scalar(select(User.id).where(User.username == "admin"))
    finally:
        db.close()
    before = _user_count()
    r = client.post(f"/users/{admin_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    # admin existiert weiterhin
    db = SessionLocal()
    try:
        assert db.scalar(select(User).where(User.username == "admin")) is not None
    finally:
        db.close()
    assert _user_count() == before


def test_delete_user(logged_in):
    client = logged_in
    client.post("/users/create",
                data={"username": "loeschmich", "password": "geheim12345",
                      "password2": "geheim12345"}, follow_redirects=False)
    db = SessionLocal()
    try:
        uid = db.scalar(select(User.id).where(User.username == "loeschmich"))
    finally:
        db.close()
    r = client.post(f"/users/{uid}/delete", follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        assert db.scalar(select(User).where(User.username == "loeschmich")) is None
    finally:
        db.close()


def _user_count() -> int:
    from sqlalchemy import func as _f
    db = SessionLocal()
    try:
        return db.scalar(select(_f.count(User.id))) or 0
    finally:
        db.close()


# --------------------------------------------------------------------------
# (f) Versions-/Update-Check (Variante 1) – reine Logik, ohne Netzwerk
# --------------------------------------------------------------------------
from app import version_check as vc  # noqa: E402


def test_version_compare():
    assert vc.compare_versions("0.1.0", "0.2.0") == -1
    assert vc.compare_versions("1.0.0", "1.0.0") == 0
    assert vc.compare_versions("0.3.0", "0.2.9") == 1
    assert vc.is_newer("v0.2.0", "0.1.0") is True
    assert vc.is_newer("0.1.0", "0.1.0") is False


class _FakeResp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


def test_check_for_update_available():
    def fake_get(url, headers, timeout):
        return _FakeResp(200, {"tag_name": "v0.2.0", "name": "v0.2.0",
                               "html_url": "https://example/releases/v0.2.0"})
    info = vc.check_for_update(local_version="0.1.0", get_func=fake_get)
    assert info.ok is True
    assert info.update_available is True
    assert info.remote_version == "0.2.0"


def test_check_for_update_up_to_date():
    def fake_get(url, headers, timeout):
        return _FakeResp(200, {"tag_name": "v0.1.0", "name": "v0.1.0"})
    info = vc.check_for_update(local_version="0.1.0", get_func=fake_get)
    assert info.ok is True
    assert info.update_available is False


def test_check_for_update_handles_error():
    def fake_get(url, headers, timeout):
        return _FakeResp(403, {})
    info = vc.check_for_update(local_version="0.1.0", get_func=fake_get)
    assert info.ok is False
    assert info.update_available is False


def test_cached_check_disabled_returns_none():
    vc.reset_cache()
    assert vc.cached_update_check(enabled=False) is None
