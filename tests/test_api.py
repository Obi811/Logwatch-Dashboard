"""Tests für die App-/REST-API (``/api/v1``) mit Token-Auth + JSON.

Nutzt wie ``test_dashboard.py`` eine isolierte SQLite-DB (Setup VOR dem Import
der App). Kein echter Netzwerkzugriff.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

# --- Isolierte Testumgebung einrichten, bevor die App importiert wird ---
_TMP = tempfile.mkdtemp(prefix="logwatch_api_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ["UPLOAD_TOKEN"] = "test-upload-token"
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-admin-pw"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ["UPDATE_CHECK_ENABLED"] = "0"
os.environ["API_CORS_ORIGINS"] = "*"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ROLE_ADMIN, ROLE_USER, Comment, User  # noqa: E402

UPLOAD_TOKEN = "test-upload-token"


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.add(User(username="admin", password_hash=hash_password("test-admin-pw"),
                    role=ROLE_ADMIN))
        db.commit()
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


def _token(client) -> str:
    r = client.post("/api/v1/auth/login",
                    json={"username": "admin", "password": "test-admin-pw"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(client):
    return {"Authorization": f"Bearer {_token(client)}"}


def _upload(client, machine_id, hardware_id, with_logfile=True):
    payload = {
        "machine_id": machine_id,
        "company": "Acme GmbH",
        "hardware_id": hardware_id,
        "teamviewer_id": "123456789",
        "hostname": "PC-01",
        "timestamp": "2026-01-15T10:00:00+00:00",
        "error_count": 1,
        "error_lines": ["ERROR etwas ist kaputt"],
        "log_filename": "app.log",
    }
    files = {}
    if with_logfile:
        files["logfile"] = ("app.log", b"line1\nERROR etwas ist kaputt\nline3\n", "text/plain")
    return client.post("/api/v1/reports",
                       data={"payload": json.dumps(payload)},
                       files=files or None,
                       headers={"X-Upload-Token": UPLOAD_TOKEN})


# --------------------------------------------------------------------------
# Auth-Flow
# --------------------------------------------------------------------------
def test_login_success_returns_token(client):
    r = client.post("/api/v1/auth/login",
                    json={"username": "admin", "password": "test-admin-pw"})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0


def test_login_wrong_password(client):
    r = client.post("/api/v1/auth/login",
                    json={"username": "admin", "password": "falsch"})
    assert r.status_code == 401
    assert "falsch" in r.json()["detail"].lower()


def test_login_unknown_user(client):
    r = client.post("/api/v1/auth/login",
                    json={"username": "gibtsnicht", "password": "egal12345"})
    assert r.status_code == 401


def test_me_requires_token(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401


def test_me_with_invalid_token(client):
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer kaputt.token.xyz"})
    assert r.status_code == 401


def test_me_with_valid_token(client):
    r = client.get("/api/v1/me", headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin"
    assert isinstance(body["id"], int)
    assert body["role"] == ROLE_ADMIN


def test_protected_without_token(client):
    for path in ["/api/v1/overview", "/api/v1/machines", "/api/v1/warnings",
                 "/api/v1/reports"]:
        r = client.get(path)
        assert r.status_code == 401, path


# --------------------------------------------------------------------------
# Lese-Endpunkte (Happy Path, JSON)
# --------------------------------------------------------------------------
def test_overview_json(client):
    _upload(client, "API-OV", "HW-1")
    r = client.get("/api/v1/overview", headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert body["machine_count"] >= 1
    assert "recent_reports" in body
    assert isinstance(body["recent_reports"], list)


def test_machines_json(client):
    _upload(client, "API-M1", "HW-M1")
    r = client.get("/api/v1/machines", headers=_auth(client))
    assert r.status_code == 200
    machines = r.json()
    assert any(m["machine_id"] == "API-M1" for m in machines)


def test_machine_detail_json(client):
    _upload(client, "API-DET", "HW-DET")
    r = client.get("/api/v1/machines/API-DET", headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert body["machine_id"] == "API-DET"
    assert len(body["reports"]) >= 1
    assert len(body["fingerprints"]) >= 1


def test_machine_detail_404(client):
    r = client.get("/api/v1/machines/GIBTSNICHT", headers=_auth(client))
    assert r.status_code == 404


def test_reports_json_pagination(client):
    _upload(client, "API-R1", "HW-R1")
    _upload(client, "API-R2", "HW-R2")
    r = client.get("/api/v1/reports?limit=1&offset=0", headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 1
    assert len(body["items"]) == 1
    assert body["total"] >= 2


def test_report_detail_json(client):
    up = _upload(client, "API-RD", "HW-RD")
    rid = up.json()["report_id"]
    r = client.get(f"/api/v1/reports/{rid}", headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == rid
    assert isinstance(body["error_lines"], list)
    assert body["has_logfile"] is True


# --------------------------------------------------------------------------
# Warnungen + Acknowledge
# --------------------------------------------------------------------------
def test_warnings_and_acknowledge(client):
    # Zwei verschiedene Hardware-IDs für dieselbe Maschine => Kopier-Warnung
    _upload(client, "API-COPY", "HW-ORIG")
    _upload(client, "API-COPY", "HW-OTHER")

    r = client.get("/api/v1/warnings", headers=_auth(client))
    assert r.status_code == 200
    warnings = r.json()
    entry = next(w for w in warnings if w["machine_id"] == "API-COPY")
    assert entry["acknowledged"] is False
    assert entry["hardware_count"] == 2

    # Filter: nur offene Warnungen enthält die Maschine
    r_open = client.get("/api/v1/warnings?status=open", headers=_auth(client))
    assert any(w["machine_id"] == "API-COPY" for w in r_open.json())

    # Quittieren
    ack = client.post("/api/v1/warnings/API-COPY/acknowledge", headers=_auth(client))
    assert ack.status_code == 200
    assert ack.json()["acknowledged"] is True
    assert ack.json()["acknowledged_by"] == "admin"

    # Jetzt taucht sie bei status=open NICHT mehr auf, bei acknowledged schon
    r_open2 = client.get("/api/v1/warnings?status=open", headers=_auth(client))
    assert not any(w["machine_id"] == "API-COPY" for w in r_open2.json())
    r_ack = client.get("/api/v1/warnings?status=acknowledged", headers=_auth(client))
    assert any(w["machine_id"] == "API-COPY" for w in r_ack.json())


def test_acknowledge_without_warning_400(client):
    _upload(client, "API-NOWARN", "HW-SINGLE")
    r = client.post("/api/v1/warnings/API-NOWARN/acknowledge", headers=_auth(client))
    assert r.status_code == 400


def test_acknowledge_unknown_machine_404(client):
    r = client.post("/api/v1/warnings/GIBTSNICHT/acknowledge", headers=_auth(client))
    assert r.status_code == 404


# --------------------------------------------------------------------------
# Kommentare (REST-API)
# --------------------------------------------------------------------------
def _make_user_token(client, username, role=ROLE_USER, password="geheim12345"):
    """Legt (falls nötig) einen Benutzer an und liefert dessen Bearer-Header."""
    db = SessionLocal()
    try:
        u = db.scalar(select(User).where(User.username == username))
        if u is None:
            u = User(username=username, password_hash=hash_password(password), role=role)
            db.add(u)
            db.commit()
    finally:
        db.close()
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_api_comment_add_and_list(client):
    rid = _upload(client, "API-CMT", "HW-C").json()["report_id"]
    r = client.post(f"/api/v1/reports/{rid}/comments",
                    json={"body": "API-Kommentar"}, headers=_auth(client))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["body"] == "API-Kommentar"
    assert body["author_username"] == "admin"

    lst = client.get(f"/api/v1/reports/{rid}/comments", headers=_auth(client))
    assert lst.status_code == 200
    assert any(c["body"] == "API-Kommentar" for c in lst.json())


def test_api_comment_empty_rejected(client):
    rid = _upload(client, "API-CMT-E", "HW-CE").json()["report_id"]
    r = client.post(f"/api/v1/reports/{rid}/comments",
                    json={"body": "   "}, headers=_auth(client))
    assert r.status_code == 400


def test_api_comment_author_deletes_own(client):
    rid = _upload(client, "API-CMT-D", "HW-CD").json()["report_id"]
    author = _make_user_token(client, "api_autor")
    cr = client.post(f"/api/v1/reports/{rid}/comments",
                     json={"body": "lösch mich"}, headers=author)
    cid = cr.json()["id"]
    r = client.delete(f"/api/v1/comments/{cid}", headers=author)
    assert r.status_code == 200
    db = SessionLocal()
    try:
        assert db.get(Comment, cid) is None
    finally:
        db.close()


def test_api_comment_stranger_forbidden(client):
    rid = _upload(client, "API-CMT-S", "HW-CS").json()["report_id"]
    owner = _make_user_token(client, "api_owner")
    cr = client.post(f"/api/v1/reports/{rid}/comments",
                     json={"body": "nicht anfassen"}, headers=owner)
    cid = cr.json()["id"]
    stranger = _make_user_token(client, "api_fremder")
    r = client.delete(f"/api/v1/comments/{cid}", headers=stranger)
    assert r.status_code == 403
    db = SessionLocal()
    try:
        assert db.get(Comment, cid) is not None
    finally:
        db.close()


def test_api_comment_admin_deletes_any(client):
    rid = _upload(client, "API-CMT-A", "HW-CA").json()["report_id"]
    author = _make_user_token(client, "api_autor2")
    cr = client.post(f"/api/v1/reports/{rid}/comments",
                     json={"body": "admin löscht"}, headers=author)
    cid = cr.json()["id"]
    r = client.delete(f"/api/v1/comments/{cid}", headers=_auth(client))  # admin
    assert r.status_code == 200


# --------------------------------------------------------------------------
# OpenAPI / Swagger
# --------------------------------------------------------------------------
def test_openapi_contains_api_routes(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/overview" in paths
    assert "/api/v1/warnings/{machine_id}/acknowledge" in paths


def test_docs_reachable(client):
    r = client.get("/docs")
    assert r.status_code == 200
