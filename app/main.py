"""LogWatch Dashboard – zentrale FastAPI-Anwendung.

Serverseitig gerendert (Jinja2). Interner Admin-Login (Session-Cookie), eine
Upload-API für die Windows-Clients (Token-geschützt, KEIN Login), Fehler-Historie,
Kopier-Erkennung und Auswertungen.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    current_user, hash_password, login_user, logout_user, verify_password,
)
from .api import router as api_router
from .db import SessionLocal, get_db
from .models import (
    ROLE_ADMIN, ROLE_USER, ROLES, STATUS_CLOSED, STATUS_OPEN,
    Comment, ErrorReport, HardwareFingerprint, Machine, User,
)
from .settings import get_settings
from .version_check import cached_update_check

_settings = get_settings()
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# /docs + /openapi.json sind aktiv, damit App-Entwickler eine klare API-Referenz
# haben (Swagger-UI). Das Web-UI bleibt unverändert session-basiert.
app = FastAPI(
    title="LogWatch Dashboard",
    description="Dashboard-Weboberfläche plus App-/REST-API unter `/api/v1`.",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
app.add_middleware(SessionMiddleware, secret_key=_settings.secret_key, max_age=60 * 60 * 12)

# CORS für die App-/REST-API: erlaubt Zugriff aus einer mobilen App o.ä. von einer
# anderen Origin. Erlaubte Origins über API_CORS_ORIGINS konfigurierbar.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.api_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# App-/REST-API (JSON, Token-Auth) einhängen.
app.include_router(api_router)

templates = Jinja2Templates(directory=os.path.join(_BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(_BASE_DIR, "static")), name="static")


# ----------------------------------------------------------------------------
# Hilfsfunktionen / Auth
# ----------------------------------------------------------------------------
class NotAuthenticated(Exception):
    """Wird geworfen, wenn eine geschützte Seite ohne Login aufgerufen wird."""


class Forbidden(Exception):
    """Wird geworfen, wenn eine Admin-Seite von einem Nicht-Admin aufgerufen wird."""


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise NotAuthenticated()
    return user


def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    """Dependency: verlangt eine angemeldete Sitzung MIT Admin-Rolle.

    Liefert das ``User``-Objekt des eingeloggten Admins. Nicht angemeldet →
    Weiterleitung zum Login; angemeldet aber keine Admin-Rolle → Flash-Fehler
    und Weiterleitung zur Startseite (``Forbidden``).
    """
    username = current_user(request)
    if not username:
        raise NotAuthenticated()
    u = db.scalar(select(User).where(User.username == username))
    if u is None:
        raise NotAuthenticated()
    if u.role != ROLE_ADMIN:
        raise Forbidden()
    return u


@app.exception_handler(NotAuthenticated)
async def _not_auth_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden_handler(request: Request, exc: Forbidden):
    flash(request, "Diese Seite ist nur für Administratoren zugänglich.", "error")
    return RedirectResponse("/", status_code=303)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_FLASH_KEY = "_flash"


def flash(request: Request, message: str, category: str = "success") -> None:
    """Hinterlegt eine einmalige Meldung in der Session (Post/Redirect/Get)."""
    msgs = request.session.get(_FLASH_KEY, [])
    msgs.append({"message": message, "category": category})
    request.session[_FLASH_KEY] = msgs


def _pop_flashes(request: Request) -> list[dict]:
    """Liest und entfernt die Flash-Meldungen aus der Session."""
    msgs = request.session.pop(_FLASH_KEY, [])
    return msgs if isinstance(msgs, list) else []


def _is_admin_username(username: str | None) -> bool:
    """Prüft (per kurzlebiger Session), ob ``username`` die Admin-Rolle hat."""
    if not username:
        return False
    db = SessionLocal()
    try:
        u = db.scalar(select(User).where(User.username == username))
        return bool(u and u.role == ROLE_ADMIN)
    finally:
        db.close()


def _ctx(request: Request, user: str, **extra) -> dict:
    ctx = {
        "request": request,
        "user": user,
        "is_admin": _is_admin_username(user),
        "flashes": _pop_flashes(request),
        "update": cached_update_check(
            enabled=_settings.update_check_enabled,
            token=_settings.github_pat or None,
        ),
    }
    ctx.update(extra)
    return ctx


def _machine_has_copy_warning(db: Session, machine_id: str) -> bool:
    """True, wenn für eine Maschine >= 2 unterschiedliche Hardware-IDs bekannt sind."""
    n = db.scalar(
        select(func.count(func.distinct(HardwareFingerprint.hardware_id)))
        .where(HardwareFingerprint.machine_id == machine_id)
        .where(HardwareFingerprint.hardware_id != "")
    )
    return (n or 0) >= 2


def _machines_with_copy_warning(db: Session) -> list[str]:
    """Liste aller Maschinen-IDs mit >= 2 verschiedenen Hardware-IDs."""
    rows = db.execute(
        select(HardwareFingerprint.machine_id)
        .where(HardwareFingerprint.hardware_id != "")
        .group_by(HardwareFingerprint.machine_id)
        .having(func.count(func.distinct(HardwareFingerprint.hardware_id)) >= 2)
    ).all()
    return [r[0] for r in rows]


# ----------------------------------------------------------------------------
# Healthcheck
# ----------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ----------------------------------------------------------------------------
# Login / Logout
# ----------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username))
    if user and verify_password(password, user.password_hash):
        login_user(request, user.username)
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": "Benutzername oder Passwort ist falsch."},
        status_code=401,
    )


@app.get("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


# ----------------------------------------------------------------------------
# Benutzerverwaltung (nur eingeloggte Admins/Mitarbeiter)
# ----------------------------------------------------------------------------
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@+-]{3,100}$")
_MIN_PASSWORD_LEN = 8


def _admin_count(db: Session) -> int:
    """Anzahl der Benutzer mit Admin-Rolle (für den Letzter-Admin-Schutz)."""
    return db.scalar(
        select(func.count(User.id)).where(User.role == ROLE_ADMIN)) or 0


@app.get("/users", response_class=HTMLResponse)
def users_list(request: Request, admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    users = db.execute(select(User).order_by(User.username)).scalars().all()
    return templates.TemplateResponse(request, "users.html", _ctx(
        request, admin.username, users=users, min_pw_len=_MIN_PASSWORD_LEN,
        ROLE_ADMIN=ROLE_ADMIN, ROLE_USER=ROLE_USER))


@app.post("/users/create")
def users_create(request: Request,
                 username: str = Form(...),
                 password: str = Form(...),
                 password2: str = Form(...),
                 role: str = Form(ROLE_USER),
                 admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    username = (username or "").strip()
    if not _USERNAME_RE.match(username):
        flash(request, "Ungültiger Benutzername: 3–100 Zeichen, erlaubt sind "
                       "Buchstaben, Ziffern und . _ @ + -", "error")
        return RedirectResponse("/users", status_code=303)
    if role not in ROLES:
        flash(request, "Ungültige Rolle.", "error")
        return RedirectResponse("/users", status_code=303)
    if len(password) < _MIN_PASSWORD_LEN:
        flash(request, f"Das Passwort muss mindestens {_MIN_PASSWORD_LEN} "
                       "Zeichen lang sein.", "error")
        return RedirectResponse("/users", status_code=303)
    if password != password2:
        flash(request, "Die beiden Passwörter stimmen nicht überein.", "error")
        return RedirectResponse("/users", status_code=303)
    exists = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if exists is not None:
        flash(request, f"Der Benutzer „{username}“ existiert bereits.", "error")
        return RedirectResponse("/users", status_code=303)

    db.add(User(username=username, password_hash=hash_password(password), role=role))
    db.commit()
    rolle = "Administrator" if role == ROLE_ADMIN else "Benutzer"
    flash(request, f"Benutzer „{username}“ ({rolle}) wurde angelegt.", "success")
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{user_id}/password")
def users_change_password(user_id: int, request: Request,
                          password: str = Form(...),
                          password2: str = Form(...),
                          admin: User = Depends(require_admin),
                          db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    if len(password) < _MIN_PASSWORD_LEN:
        flash(request, f"Das Passwort muss mindestens {_MIN_PASSWORD_LEN} "
                       "Zeichen lang sein.", "error")
        return RedirectResponse("/users", status_code=303)
    if password != password2:
        flash(request, "Die beiden Passwörter stimmen nicht überein.", "error")
        return RedirectResponse("/users", status_code=303)
    target.password_hash = hash_password(password)
    db.commit()
    flash(request, f"Passwort für „{target.username}“ wurde geändert.", "success")
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{user_id}/role")
def users_change_role(user_id: int, request: Request,
                      role: str = Form(...),
                      admin: User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    if role not in ROLES:
        flash(request, "Ungültige Rolle.", "error")
        return RedirectResponse("/users", status_code=303)
    # Letzter-Admin-Schutz: der einzige verbleibende Admin darf nicht herabgestuft
    # werden, sonst käme niemand mehr an die Benutzerverwaltung.
    if (target.role == ROLE_ADMIN and role != ROLE_ADMIN
            and _admin_count(db) <= 1):
        flash(request, "Der letzte verbleibende Administrator kann nicht "
                       "herabgestuft werden.", "error")
        return RedirectResponse("/users", status_code=303)
    if target.role == role:
        flash(request, f"„{target.username}“ hat diese Rolle bereits.", "error")
        return RedirectResponse("/users", status_code=303)
    target.role = role
    db.commit()
    rolle = "Administrator" if role == ROLE_ADMIN else "Benutzer"
    flash(request, f"Rolle von „{target.username}“ auf {rolle} geändert.", "success")
    return RedirectResponse("/users", status_code=303)


@app.post("/users/{user_id}/delete")
def users_delete(user_id: int, request: Request,
                 admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    if target.username == admin.username:
        flash(request, "Du kannst dein eigenes Konto nicht löschen.", "error")
        return RedirectResponse("/users", status_code=303)
    total = db.scalar(select(func.count(User.id))) or 0
    if total <= 1:
        flash(request, "Der letzte verbleibende Benutzer kann nicht gelöscht werden.", "error")
        return RedirectResponse("/users", status_code=303)
    # Letzter-Admin-Schutz: den einzigen Admin nicht löschen.
    if target.role == ROLE_ADMIN and _admin_count(db) <= 1:
        flash(request, "Der letzte verbleibende Administrator kann nicht "
                       "gelöscht werden.", "error")
        return RedirectResponse("/users", status_code=303)
    name = target.username
    db.delete(target)
    db.commit()
    flash(request, f"Benutzer „{name}“ wurde gelöscht.", "success")
    return RedirectResponse("/users", status_code=303)


# ----------------------------------------------------------------------------
# Übersicht (Dashboard-Startseite)
# ----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def overview(request: Request, user: str = Depends(require_user), db: Session = Depends(get_db)):
    machine_count = db.scalar(select(func.count(Machine.machine_id))) or 0
    open_count = db.scalar(
        select(func.count(ErrorReport.id)).where(ErrorReport.status == STATUS_OPEN)) or 0
    closed_count = db.scalar(
        select(func.count(ErrorReport.id)).where(ErrorReport.status == STATUS_CLOSED)) or 0
    warn_machines = _machines_with_copy_warning(db)

    recent = db.execute(
        select(ErrorReport).order_by(ErrorReport.received_at.desc()).limit(10)
    ).scalars().all()

    return templates.TemplateResponse(request, "overview.html", _ctx(
        request, user,
        machine_count=machine_count,
        open_count=open_count,
        closed_count=closed_count,
        warn_count=len(warn_machines),
        recent=recent,
    ))


# ----------------------------------------------------------------------------
# Maschinen
# ----------------------------------------------------------------------------
@app.get("/machines", response_class=HTMLResponse)
def machines_list(request: Request, user: str = Depends(require_user),
                  q: str = "", db: Session = Depends(get_db)):
    stmt = select(Machine).order_by(Machine.last_seen.desc())
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where((Machine.machine_id.ilike(like)) | (Machine.company.ilike(like)))
    machines = db.execute(stmt).scalars().all()

    warn_set = set(_machines_with_copy_warning(db))
    rows = []
    for m in machines:
        open_n = db.scalar(
            select(func.count(ErrorReport.id))
            .where(ErrorReport.machine_id == m.machine_id)
            .where(ErrorReport.status == STATUS_OPEN)) or 0
        total_n = db.scalar(
            select(func.count(ErrorReport.id))
            .where(ErrorReport.machine_id == m.machine_id)) or 0
        rows.append({"m": m, "open": open_n, "total": total_n,
                     "warn": m.machine_id in warn_set})
    return templates.TemplateResponse(request, "machines.html", _ctx(
        request, user, rows=rows, q=q))


@app.get("/machines/{machine_id}", response_class=HTMLResponse)
def machine_detail(machine_id: str, request: Request,
                   user: str = Depends(require_user), db: Session = Depends(get_db)):
    machine = db.get(Machine, machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Maschine nicht gefunden")
    fingerprints = db.execute(
        select(HardwareFingerprint)
        .where(HardwareFingerprint.machine_id == machine_id)
        .order_by(HardwareFingerprint.last_seen.desc())
    ).scalars().all()
    reports = db.execute(
        select(ErrorReport)
        .where(ErrorReport.machine_id == machine_id)
        .order_by(ErrorReport.received_at.desc())
    ).scalars().all()
    return templates.TemplateResponse(request, "machine_detail.html", _ctx(
        request, user,
        machine=machine,
        fingerprints=fingerprints,
        reports=reports,
        warn=_machine_has_copy_warning(db, machine_id),
    ))


# ----------------------------------------------------------------------------
# Fehlerberichte (Historie)
# ----------------------------------------------------------------------------
@app.get("/reports", response_class=HTMLResponse)
def reports_list(request: Request, user: str = Depends(require_user),
                 status: str = "", machine_id: str = "", company: str = "",
                 db: Session = Depends(get_db)):
    stmt = select(ErrorReport)
    if status in (STATUS_OPEN, STATUS_CLOSED):
        stmt = stmt.where(ErrorReport.status == status)
    if machine_id.strip():
        stmt = stmt.where(ErrorReport.machine_id == machine_id.strip())
    if company.strip():
        like = f"%{company.strip()}%"
        stmt = stmt.join(Machine, Machine.machine_id == ErrorReport.machine_id)\
            .where(Machine.company.ilike(like))
    stmt = stmt.order_by(ErrorReport.received_at.desc()).limit(500)
    reports = db.execute(stmt).scalars().all()

    # Firma je Report für die Anzeige nachladen
    companies = {m.machine_id: m.company for m in db.execute(select(Machine)).scalars().all()}
    return templates.TemplateResponse(request, "reports.html", _ctx(
        request, user,
        reports=reports,
        companies=companies,
        status=status,
        machine_id=machine_id,
        company=company,
        STATUS_OPEN=STATUS_OPEN,
        STATUS_CLOSED=STATUS_CLOSED,
    ))


@app.get("/reports/{report_id}", response_class=HTMLResponse)
def report_detail(report_id: int, request: Request,
                  user: str = Depends(require_user), db: Session = Depends(get_db)):
    report = db.get(ErrorReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Fehlerbericht nicht gefunden")
    machine = db.get(Machine, report.machine_id)
    lines = report.error_lines.split("\n") if report.error_lines else []
    comments = db.execute(
        select(Comment).where(Comment.report_id == report_id)
        .order_by(Comment.created_at)
    ).scalars().all()
    # Autor-Benutzernamen für die Anzeige (auch wenn ein Autor zwischenzeitlich
    # gelöscht wurde, zeigt das Template dann „unbekannt“).
    me = db.scalar(select(User).where(User.username == user))
    return templates.TemplateResponse(request, "report_detail.html", _ctx(
        request, user,
        report=report,
        machine=machine,
        lines=lines,
        comments=comments,
        current_user_id=(me.id if me else None),
        STATUS_OPEN=STATUS_OPEN,
        STATUS_CLOSED=STATUS_CLOSED,
    ))


@app.post("/reports/{report_id}/comments")
def comment_add(report_id: int, request: Request,
                body: str = Form(...), user: str = Depends(require_user),
                db: Session = Depends(get_db)):
    report = db.get(ErrorReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Fehlerbericht nicht gefunden")
    me = db.scalar(select(User).where(User.username == user))
    if me is None:
        raise NotAuthenticated()
    text_body = (body or "").strip()
    if not text_body:
        flash(request, "Der Kommentar darf nicht leer sein.", "error")
        return RedirectResponse(f"/reports/{report_id}", status_code=303)
    db.add(Comment(report_id=report_id, user_id=me.id, body=text_body))
    db.commit()
    flash(request, "Kommentar wurde hinzugefügt.", "success")
    return RedirectResponse(f"/reports/{report_id}", status_code=303)


@app.post("/comments/{comment_id}/delete")
def comment_delete(comment_id: int, request: Request,
                   user: str = Depends(require_user),
                   db: Session = Depends(get_db)):
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Kommentar nicht gefunden")
    report_id = comment.report_id
    me = db.scalar(select(User).where(User.username == user))
    if me is None:
        raise NotAuthenticated()
    # Löschen darf nur der Autor selbst oder ein Administrator.
    if comment.user_id != me.id and me.role != ROLE_ADMIN:
        flash(request, "Du darfst nur eigene Kommentare löschen.", "error")
        return RedirectResponse(f"/reports/{report_id}", status_code=303)
    db.delete(comment)
    db.commit()
    flash(request, "Kommentar wurde gelöscht.", "success")
    return RedirectResponse(f"/reports/{report_id}", status_code=303)


@app.post("/reports/{report_id}/close")
def report_close(report_id: int, request: Request,
                 note: str = Form(""), user: str = Depends(require_user),
                 db: Session = Depends(get_db)):
    report = db.get(ErrorReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Fehlerbericht nicht gefunden")
    report.status = STATUS_CLOSED
    report.closed_at = _utcnow()
    report.closed_by = user
    if note.strip():
        report.note = note.strip()
    db.commit()
    return RedirectResponse(f"/reports/{report_id}", status_code=303)


@app.post("/reports/{report_id}/reopen")
def report_reopen(report_id: int, request: Request,
                  user: str = Depends(require_user), db: Session = Depends(get_db)):
    report = db.get(ErrorReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Fehlerbericht nicht gefunden")
    report.status = STATUS_OPEN
    report.closed_at = None
    report.closed_by = None
    db.commit()
    return RedirectResponse(f"/reports/{report_id}", status_code=303)


@app.get("/reports/{report_id}/logfile")
def report_logfile(report_id: int, request: Request,
                   user: str = Depends(require_user), db: Session = Depends(get_db)):
    report = db.get(ErrorReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Fehlerbericht nicht gefunden")
    if not report.log_path or not os.path.isfile(report.log_path):
        raise HTTPException(status_code=404, detail="Keine Logdatei vorhanden")
    filename = report.log_filename or os.path.basename(report.log_path)
    return FileResponse(report.log_path, media_type="application/octet-stream", filename=filename)


# ----------------------------------------------------------------------------
# Kopier-Warnungen
# ----------------------------------------------------------------------------
@app.get("/warnings", response_class=HTMLResponse)
def warnings_page(request: Request, user: str = Depends(require_user),
                  db: Session = Depends(get_db)):
    warn_ids = _machines_with_copy_warning(db)
    items = []
    for mid in warn_ids:
        machine = db.get(Machine, mid)
        fps = db.execute(
            select(HardwareFingerprint)
            .where(HardwareFingerprint.machine_id == mid)
            .where(HardwareFingerprint.hardware_id != "")
            .order_by(HardwareFingerprint.last_seen.desc())
        ).scalars().all()
        items.append({"machine": machine, "fingerprints": fps})
    return templates.TemplateResponse(request, "warnings.html", _ctx(
        request, user, items=items))


# ----------------------------------------------------------------------------
# Statistiken / Auswertungen
# ----------------------------------------------------------------------------
@app.get("/statistik", response_class=HTMLResponse)
def statistik_page(request: Request, user: str = Depends(require_user),
                   db: Session = Depends(get_db)):
    machine_count = db.scalar(select(func.count(Machine.machine_id))) or 0
    open_count = db.scalar(
        select(func.count(ErrorReport.id)).where(ErrorReport.status == STATUS_OPEN)) or 0
    closed_count = db.scalar(
        select(func.count(ErrorReport.id)).where(ErrorReport.status == STATUS_CLOSED)) or 0
    total_count = db.scalar(select(func.count(ErrorReport.id))) or 0
    warn_count = len(_machines_with_copy_warning(db))

    # Top-Maschinen nach Fehleranzahl
    top_rows = db.execute(
        select(ErrorReport.machine_id, func.count(ErrorReport.id).label("n"))
        .group_by(ErrorReport.machine_id)
        .order_by(func.count(ErrorReport.id).desc())
        .limit(10)
    ).all()
    companies = {m.machine_id: m.company for m in db.execute(select(Machine)).scalars().all()}
    top_machines = [{"machine_id": r[0], "company": companies.get(r[0], ""), "n": r[1]}
                    for r in top_rows]

    return templates.TemplateResponse(request, "statistik.html", _ctx(
        request, user,
        machine_count=machine_count,
        open_count=open_count,
        closed_count=closed_count,
        total_count=total_count,
        warn_count=warn_count,
        top_machines=top_machines,
    ))


@app.get("/api/stats/errors-per-day")
def stats_errors_per_day(request: Request, user: str = Depends(require_user),
                         days: int = 30, db: Session = Depends(get_db)):
    days = max(1, min(days, 365))
    since = _utcnow() - timedelta(days=days - 1)
    rows = db.execute(
        select(func.date(ErrorReport.received_at).label("d"),
               func.count(ErrorReport.id).label("n"))
        .where(ErrorReport.received_at >= since)
        .group_by(func.date(ErrorReport.received_at))
        .order_by(func.date(ErrorReport.received_at))
    ).all()
    counts = {str(r[0]): r[1] for r in rows}

    labels: list[str] = []
    values: list[int] = []
    base = since.date()
    for i in range(days):
        d = (base + timedelta(days=i)).isoformat()
        labels.append(d)
        values.append(int(counts.get(d, 0)))
    return JSONResponse({"labels": labels, "values": values})


# ----------------------------------------------------------------------------
# Upload-API (Token-geschützt, KEIN Login)
# ----------------------------------------------------------------------------
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _parse_timestamp(value: str | None) -> datetime:
    if not value:
        return _utcnow()
    try:
        s = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return _utcnow()


@app.post("/api/v1/reports")
async def api_upload_report(
    request: Request,
    payload: str = Form(...),
    logfile: UploadFile | None = None,
    db: Session = Depends(get_db),
):
    # 1) Token prüfen
    token = request.headers.get("X-Upload-Token", "")
    if not _settings.upload_token or token != _settings.upload_token:
        raise HTTPException(status_code=401, detail="Ungültiges Upload-Token")

    # 2) Payload parsen
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("payload ist kein JSON-Objekt")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Ungültiges payload-JSON: {exc}")

    machine_id = str(data.get("machine_id") or "").strip()
    if not machine_id:
        raise HTTPException(status_code=400, detail="machine_id fehlt")

    company = str(data.get("company") or "").strip()
    hardware_id = str(data.get("hardware_id") or "").strip()
    teamviewer_id = str(data.get("teamviewer_id") or "").strip()
    hostname = str(data.get("hostname") or "").strip()
    timestamp = _parse_timestamp(data.get("timestamp"))
    error_lines = data.get("error_lines") or []
    if isinstance(error_lines, str):
        error_lines = [error_lines]
    error_lines_text = "\n".join(str(x) for x in error_lines)
    try:
        error_count = int(data.get("error_count") or len(error_lines))
    except (ValueError, TypeError):
        error_count = len(error_lines)
    log_filename = str(data.get("log_filename") or "").strip()

    now = _utcnow()

    # 3) Maschine upserten
    machine = db.get(Machine, machine_id)
    if machine is None:
        machine = Machine(machine_id=machine_id, company=company,
                          first_seen=now, last_seen=now)
        db.add(machine)
    else:
        machine.last_seen = now
        if company:
            machine.company = company

    # 4) Hardware-Fingerprint upserten (eindeutig je machine_id + hardware_id)
    if hardware_id:
        fp = db.scalar(
            select(HardwareFingerprint)
            .where(HardwareFingerprint.machine_id == machine_id)
            .where(HardwareFingerprint.hardware_id == hardware_id)
        )
        if fp is None:
            fp = HardwareFingerprint(
                machine_id=machine_id, hardware_id=hardware_id,
                teamviewer_id=teamviewer_id, hostname=hostname,
                first_seen=now, last_seen=now)
            db.add(fp)
        else:
            fp.last_seen = now
            if teamviewer_id:
                fp.teamviewer_id = teamviewer_id
            if hostname:
                fp.hostname = hostname

    # 5) Logdatei speichern (optional)
    saved_path = ""
    saved_name = log_filename
    if logfile is not None and logfile.filename:
        raw = await logfile.read()
        if raw:
            base = log_filename or logfile.filename or "logfile.log"
            base = _SAFE_NAME_RE.sub("_", os.path.basename(base))[:120] or "logfile.log"
            unique = f"{machine_id}_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}_{base}"
            unique = _SAFE_NAME_RE.sub("_", unique)
            saved_path = os.path.join(_settings.upload_dir, unique)
            with open(saved_path, "wb") as fh:
                fh.write(raw)
            saved_name = base

    # 6) Fehlerbericht anlegen
    report = ErrorReport(
        machine_id=machine_id, hardware_id=hardware_id, teamviewer_id=teamviewer_id,
        hostname=hostname, timestamp=timestamp, received_at=now,
        error_count=error_count, error_lines=error_lines_text,
        log_filename=saved_name, log_path=saved_path, status=STATUS_OPEN,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    copy_warning = _machine_has_copy_warning(db, machine_id)
    return JSONResponse(
        {"status": "ok", "report_id": report.id, "copy_warning": copy_warning},
        status_code=201,
    )
