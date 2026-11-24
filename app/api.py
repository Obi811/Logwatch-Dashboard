"""App-/REST-API des LogWatch-Dashboards (Präfix ``/api/v1``, JSON).

Diese API ist die Grundlage für eine mobile App (wird in einem separaten Projekt
entwickelt). Sie ist vollständig getrennt vom Session-basierten Web-UI:

* Anmeldung über ``POST /api/v1/auth/login`` → signierter Bearer-Token.
* Alle weiteren Endpunkte verlangen ``Authorization: Bearer <token>``.
* Alle Antworten sind JSON mit Pydantic-Response-Modellen (sauber in ``/docs``).

Die Lese-Endpunkte liefern dieselben Daten wie die entsprechenden Web-Seiten.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .api_auth import create_access_token, get_current_api_user
from .auth import verify_password
from .db import get_db
from .models import (
    ROLE_ADMIN, STATUS_CLOSED, STATUS_OPEN, Comment, ErrorReport,
    HardwareFingerprint, Machine, User,
)

router = APIRouter(prefix="/api/v1")


# ===========================================================================
# Pydantic-Modelle (Request + Response) – erscheinen in /docs
# ===========================================================================
class LoginRequest(BaseModel):
    username: str = Field(..., description="Benutzername", examples=["Obi811"])
    password: str = Field(..., description="Passwort")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="Signierter Bearer-Token")
    token_type: str = Field("bearer", description="Token-Typ (immer 'bearer')")
    expires_in: int = Field(..., description="Gültigkeitsdauer in Sekunden")


class UserResponse(BaseModel):
    id: int
    username: str
    role: str = Field(..., description="Rolle des Benutzers: 'admin' oder 'user'")


class CommentResponse(BaseModel):
    id: int
    report_id: int
    user_id: int
    author_username: str = Field(..., description="Benutzername des Autors")
    body: str = Field(..., description="Kommentartext")
    created_at: Optional[datetime]


class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1, description="Kommentartext (darf nicht leer sein)")


class OverviewResponse(BaseModel):
    machine_count: int = Field(..., description="Anzahl überwachter Maschinen")
    open_count: int = Field(..., description="Anzahl offener Fehlerberichte")
    closed_count: int = Field(..., description="Anzahl abgeschlossener Fehlerberichte")
    total_count: int = Field(..., description="Gesamtzahl der Fehlerberichte")
    warning_count: int = Field(..., description="Anzahl Maschinen mit Kopier-Warnung")
    recent_reports: List["ReportSummary"] = Field(
        ..., description="Die zuletzt eingegangenen Fehlerberichte")


class MachineSummary(BaseModel):
    machine_id: str
    company: str
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    open_reports: int = Field(..., description="Offene Fehlerberichte dieser Maschine")
    total_reports: int = Field(..., description="Alle Fehlerberichte dieser Maschine")
    copy_warning: bool = Field(..., description="Kopier-Warnung aktiv?")


class FingerprintResponse(BaseModel):
    id: int
    hardware_id: str
    teamviewer_id: str
    hostname: str
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]


class ReportSummary(BaseModel):
    id: int
    machine_id: str
    hostname: str
    status: str
    error_count: int
    timestamp: Optional[datetime]
    received_at: Optional[datetime]


class ReportDetail(ReportSummary):
    hardware_id: str
    teamviewer_id: str
    error_lines: List[str] = Field(..., description="Die Fehlerzeilen als Liste")
    log_filename: str
    has_logfile: bool = Field(..., description="Ist eine Logdatei hinterlegt?")
    closed_at: Optional[datetime]
    closed_by: Optional[str]
    note: str


class MachineDetail(MachineSummary):
    notes: str
    copy_warning_ack_at: Optional[datetime]
    copy_warning_ack_by: Optional[str]
    fingerprints: List[FingerprintResponse]
    reports: List[ReportSummary]


class WarningResponse(BaseModel):
    machine_id: str = Field(..., description="ID der betroffenen Maschine (= Warnungs-ID)")
    company: str
    hardware_count: int = Field(..., description="Anzahl verschiedener Hardware-IDs")
    acknowledged: bool = Field(..., description="Wurde die Warnung quittiert?")
    acknowledged_at: Optional[datetime]
    acknowledged_by: Optional[str]
    fingerprints: List[FingerprintResponse]


class ReportListResponse(BaseModel):
    total: int = Field(..., description="Gesamtzahl passender Berichte (ohne Paginierung)")
    limit: int
    offset: int
    items: List[ReportSummary]


# Forward-Reference auflösen (OverviewResponse referenziert ReportSummary)
OverviewResponse.model_rebuild()


# ===========================================================================
# Hilfsfunktionen (Kopier-Warnung) – lokal, um Zirkularimporte zu vermeiden
# ===========================================================================
def _machine_has_copy_warning(db: Session, machine_id: str) -> bool:
    n = db.scalar(
        select(func.count(func.distinct(HardwareFingerprint.hardware_id)))
        .where(HardwareFingerprint.machine_id == machine_id)
        .where(HardwareFingerprint.hardware_id != "")
    )
    return (n or 0) >= 2


def _machines_with_copy_warning(db: Session) -> List[str]:
    rows = db.execute(
        select(HardwareFingerprint.machine_id)
        .where(HardwareFingerprint.hardware_id != "")
        .group_by(HardwareFingerprint.machine_id)
        .having(func.count(func.distinct(HardwareFingerprint.hardware_id)) >= 2)
    ).all()
    return [r[0] for r in rows]


def _report_summary(r: ErrorReport) -> ReportSummary:
    return ReportSummary(
        id=r.id, machine_id=r.machine_id, hostname=r.hostname or "",
        status=r.status, error_count=r.error_count,
        timestamp=r.timestamp, received_at=r.received_at,
    )


def _fingerprint(fp: HardwareFingerprint) -> FingerprintResponse:
    return FingerprintResponse(
        id=fp.id, hardware_id=fp.hardware_id or "",
        teamviewer_id=fp.teamviewer_id or "", hostname=fp.hostname or "",
        first_seen=fp.first_seen, last_seen=fp.last_seen,
    )


# ===========================================================================
# Authentifizierung
# ===========================================================================
@router.post("/auth/login", response_model=TokenResponse, tags=["Authentifizierung"],
             summary="Anmelden und Bearer-Token erhalten")
def api_login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Prüft Benutzername/Passwort gegen die Benutzer-Tabelle und gibt bei Erfolg
    einen signierten Bearer-Token (gültig 30 Tage) zurück. Bei falschen Daten
    antwortet die API mit HTTP 401."""
    from .api_auth import TOKEN_MAX_AGE_SECONDS

    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401,
                            detail="Benutzername oder Passwort ist falsch.")
    token = create_access_token(user.id)
    return TokenResponse(access_token=token, token_type="bearer",
                         expires_in=TOKEN_MAX_AGE_SECONDS)


@router.get("/me", response_model=UserResponse, tags=["Authentifizierung"],
            summary="Angemeldeten Benutzer abrufen")
def api_me(user: User = Depends(get_current_api_user)) -> UserResponse:
    """Gibt den zum Bearer-Token gehörenden Benutzer zurück (inkl. Rolle)."""
    return UserResponse(id=user.id, username=user.username, role=user.role)


# ===========================================================================
# Übersicht
# ===========================================================================
@router.get("/overview", response_model=OverviewResponse, tags=["Übersicht"],
            summary="Kennzahlen und letzte Berichte")
def api_overview(user: User = Depends(get_current_api_user),
                 db: Session = Depends(get_db)) -> OverviewResponse:
    """Liefert dieselben Kennzahlen wie die Übersichtsseite: Anzahl Maschinen,
    offene/abgeschlossene Berichte, Kopier-Warnungen und die 10 neuesten
    Fehlerberichte."""
    machine_count = db.scalar(select(func.count(Machine.machine_id))) or 0
    open_count = db.scalar(
        select(func.count(ErrorReport.id)).where(ErrorReport.status == STATUS_OPEN)) or 0
    closed_count = db.scalar(
        select(func.count(ErrorReport.id)).where(ErrorReport.status == STATUS_CLOSED)) or 0
    total_count = db.scalar(select(func.count(ErrorReport.id))) or 0
    warn_count = len(_machines_with_copy_warning(db))
    recent = db.execute(
        select(ErrorReport).order_by(ErrorReport.received_at.desc()).limit(10)
    ).scalars().all()
    return OverviewResponse(
        machine_count=machine_count, open_count=open_count,
        closed_count=closed_count, total_count=total_count,
        warning_count=warn_count,
        recent_reports=[_report_summary(r) for r in recent],
    )


# ===========================================================================
# Maschinen
# ===========================================================================
@router.get("/machines", response_model=List[MachineSummary], tags=["Maschinen"],
            summary="Alle Maschinen auflisten")
def api_machines(user: User = Depends(get_current_api_user),
                 q: str = Query("", description="Optionaler Suchtext (Maschine/Firma)"),
                 db: Session = Depends(get_db)) -> List[MachineSummary]:
    """Liste aller überwachten Maschinen inkl. Bericht-Zählern und Kopier-Warnung."""
    stmt = select(Machine).order_by(Machine.last_seen.desc())
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where((Machine.machine_id.ilike(like)) | (Machine.company.ilike(like)))
    machines = db.execute(stmt).scalars().all()
    warn_set = set(_machines_with_copy_warning(db))
    result: List[MachineSummary] = []
    for m in machines:
        open_n = db.scalar(
            select(func.count(ErrorReport.id))
            .where(ErrorReport.machine_id == m.machine_id)
            .where(ErrorReport.status == STATUS_OPEN)) or 0
        total_n = db.scalar(
            select(func.count(ErrorReport.id))
            .where(ErrorReport.machine_id == m.machine_id)) or 0
        result.append(MachineSummary(
            machine_id=m.machine_id, company=m.company or "",
            first_seen=m.first_seen, last_seen=m.last_seen,
            open_reports=open_n, total_reports=total_n,
            copy_warning=m.machine_id in warn_set,
        ))
    return result


@router.get("/machines/{machine_id}", response_model=MachineDetail, tags=["Maschinen"],
            summary="Maschinen-Detail inkl. Berichten/Fingerprints")
def api_machine_detail(machine_id: str,
                       user: User = Depends(get_current_api_user),
                       db: Session = Depends(get_db)) -> MachineDetail:
    """Detaildaten einer Maschine inkl. Hardware-Fingerprints und Fehlerberichten."""
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(status_code=404, detail="Maschine nicht gefunden.")
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
    open_n = sum(1 for r in reports if r.status == STATUS_OPEN)
    return MachineDetail(
        machine_id=m.machine_id, company=m.company or "",
        first_seen=m.first_seen, last_seen=m.last_seen,
        open_reports=open_n, total_reports=len(reports),
        copy_warning=_machine_has_copy_warning(db, machine_id),
        notes=m.notes or "",
        copy_warning_ack_at=m.copy_warning_ack_at,
        copy_warning_ack_by=m.copy_warning_ack_by,
        fingerprints=[_fingerprint(fp) for fp in fingerprints],
        reports=[_report_summary(r) for r in reports],
    )


# ===========================================================================
# Kopier-Warnungen
# ===========================================================================
@router.get("/warnings", response_model=List[WarningResponse], tags=["Warnungen"],
            summary="Kopier-Warnungen auflisten")
def api_warnings(user: User = Depends(get_current_api_user),
                 status: str = Query("", description="Filter: 'open' = nur "
                                     "nicht quittierte, 'acknowledged' = nur quittierte"),
                 db: Session = Depends(get_db)) -> List[WarningResponse]:
    """Maschinen mit Kopier-Warnung (>= 2 verschiedene Hardware-IDs).

    Mit ``?status=open`` werden nur noch nicht quittierte Warnungen zurückgegeben,
    mit ``?status=acknowledged`` nur die bereits quittierten."""
    warn_ids = _machines_with_copy_warning(db)
    items: List[WarningResponse] = []
    for mid in warn_ids:
        m = db.get(Machine, mid)
        if m is None:
            continue
        acknowledged = m.copy_warning_ack_at is not None
        if status == "open" and acknowledged:
            continue
        if status == "acknowledged" and not acknowledged:
            continue
        fps = db.execute(
            select(HardwareFingerprint)
            .where(HardwareFingerprint.machine_id == mid)
            .where(HardwareFingerprint.hardware_id != "")
            .order_by(HardwareFingerprint.last_seen.desc())
        ).scalars().all()
        items.append(WarningResponse(
            machine_id=mid, company=m.company or "",
            hardware_count=len({fp.hardware_id for fp in fps}),
            acknowledged=acknowledged,
            acknowledged_at=m.copy_warning_ack_at,
            acknowledged_by=m.copy_warning_ack_by,
            fingerprints=[_fingerprint(fp) for fp in fps],
        ))
    return items


@router.post("/warnings/{machine_id}/acknowledge", response_model=WarningResponse,
             tags=["Warnungen"], summary="Kopier-Warnung quittieren")
def api_acknowledge_warning(machine_id: str,
                            user: User = Depends(get_current_api_user),
                            db: Session = Depends(get_db)) -> WarningResponse:
    """Quittiert die Kopier-Warnung einer Maschine (vermerkt Zeitpunkt + Benutzer)."""
    m = db.get(Machine, machine_id)
    if not m:
        raise HTTPException(status_code=404, detail="Maschine nicht gefunden.")
    if not _machine_has_copy_warning(db, machine_id):
        raise HTTPException(status_code=400,
                            detail="Für diese Maschine liegt keine Kopier-Warnung vor.")
    m.copy_warning_ack_at = datetime.now().astimezone()
    m.copy_warning_ack_by = user.username
    db.commit()
    db.refresh(m)
    fps = db.execute(
        select(HardwareFingerprint)
        .where(HardwareFingerprint.machine_id == machine_id)
        .where(HardwareFingerprint.hardware_id != "")
        .order_by(HardwareFingerprint.last_seen.desc())
    ).scalars().all()
    return WarningResponse(
        machine_id=machine_id, company=m.company or "",
        hardware_count=len({fp.hardware_id for fp in fps}),
        acknowledged=True,
        acknowledged_at=m.copy_warning_ack_at,
        acknowledged_by=m.copy_warning_ack_by,
        fingerprints=[_fingerprint(fp) for fp in fps],
    )


# ===========================================================================
# Fehlerberichte
# ===========================================================================
@router.get("/reports", response_model=ReportListResponse, tags=["Fehlerberichte"],
            summary="Fehlerberichte auflisten (paginierbar)")
def api_reports(user: User = Depends(get_current_api_user),
                status: str = Query("", description="Filter: 'offen' oder 'abgeschlossen'"),
                machine_id: str = Query("", description="Nur Berichte dieser Maschine"),
                limit: int = Query(50, ge=1, le=500, description="Max. Anzahl (1–500)"),
                offset: int = Query(0, ge=0, description="Versatz für die Paginierung"),
                db: Session = Depends(get_db)) -> ReportListResponse:
    """Liste der Fehlerberichte, neueste zuerst. Optional nach Status/Maschine
    gefiltert und über ``limit``/``offset`` paginierbar."""
    base = select(ErrorReport)
    count_stmt = select(func.count(ErrorReport.id))
    if status in (STATUS_OPEN, STATUS_CLOSED):
        base = base.where(ErrorReport.status == status)
        count_stmt = count_stmt.where(ErrorReport.status == status)
    if machine_id.strip():
        base = base.where(ErrorReport.machine_id == machine_id.strip())
        count_stmt = count_stmt.where(ErrorReport.machine_id == machine_id.strip())
    total = db.scalar(count_stmt) or 0
    rows = db.execute(
        base.order_by(ErrorReport.received_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return ReportListResponse(
        total=total, limit=limit, offset=offset,
        items=[_report_summary(r) for r in rows],
    )


@router.get("/reports/{report_id}", response_model=ReportDetail, tags=["Fehlerberichte"],
            summary="Fehlerbericht-Detail")
def api_report_detail(report_id: int,
                      user: User = Depends(get_current_api_user),
                      db: Session = Depends(get_db)) -> ReportDetail:
    """Vollständige Detaildaten eines Fehlerberichts inkl. der Fehlerzeilen."""
    import os as _os

    r = db.get(ErrorReport, report_id)
    if not r:
        raise HTTPException(status_code=404, detail="Fehlerbericht nicht gefunden.")
    lines = r.error_lines.split("\n") if r.error_lines else []
    has_logfile = bool(r.log_path and _os.path.isfile(r.log_path))
    return ReportDetail(
        id=r.id, machine_id=r.machine_id, hostname=r.hostname or "",
        status=r.status, error_count=r.error_count,
        timestamp=r.timestamp, received_at=r.received_at,
        hardware_id=r.hardware_id or "", teamviewer_id=r.teamviewer_id or "",
        error_lines=lines, log_filename=r.log_filename or "",
        has_logfile=has_logfile,
        closed_at=r.closed_at, closed_by=r.closed_by, note=r.note or "",
    )


# ===========================================================================
# Kommentare (an Fehlerberichte/Meldungen)
# ===========================================================================
def _comment_response(c: Comment) -> CommentResponse:
    return CommentResponse(
        id=c.id, report_id=c.report_id, user_id=c.user_id,
        author_username=(c.author.username if c.author else "unbekannt"),
        body=c.body, created_at=c.created_at,
    )


@router.get("/reports/{report_id}/comments", response_model=List[CommentResponse],
            tags=["Kommentare"], summary="Kommentare eines Fehlerberichts auflisten")
def api_report_comments(report_id: int,
                        user: User = Depends(get_current_api_user),
                        db: Session = Depends(get_db)) -> List[CommentResponse]:
    """Alle Kommentare eines Fehlerberichts (chronologisch, älteste zuerst)."""
    r = db.get(ErrorReport, report_id)
    if not r:
        raise HTTPException(status_code=404, detail="Fehlerbericht nicht gefunden.")
    rows = db.execute(
        select(Comment).where(Comment.report_id == report_id)
        .order_by(Comment.created_at)
    ).scalars().all()
    return [_comment_response(c) for c in rows]


@router.post("/reports/{report_id}/comments", response_model=CommentResponse,
             status_code=201, tags=["Kommentare"],
             summary="Kommentar zu einem Fehlerbericht anlegen")
def api_add_comment(report_id: int, body: CommentCreate,
                    user: User = Depends(get_current_api_user),
                    db: Session = Depends(get_db)) -> CommentResponse:
    """Legt einen Kommentar am Fehlerbericht an. Autor ist der Token-Benutzer."""
    r = db.get(ErrorReport, report_id)
    if not r:
        raise HTTPException(status_code=404, detail="Fehlerbericht nicht gefunden.")
    text_body = (body.body or "").strip()
    if not text_body:
        raise HTTPException(status_code=400,
                            detail="Der Kommentar darf nicht leer sein.")
    comment = Comment(report_id=report_id, user_id=user.id, body=text_body)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _comment_response(comment)


@router.delete("/comments/{comment_id}", tags=["Kommentare"],
               summary="Kommentar löschen (Autor oder Admin)")
def api_delete_comment(comment_id: int,
                       user: User = Depends(get_current_api_user),
                       db: Session = Depends(get_db)) -> dict:
    """Löscht einen Kommentar. Erlaubt nur dem Autor selbst oder einem Admin;
    andernfalls HTTP 403 mit deutscher Meldung."""
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Kommentar nicht gefunden.")
    if comment.user_id != user.id and user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Du darfst nur eigene Kommentare löschen.")
    db.delete(comment)
    db.commit()
    return {"status": "ok"}
