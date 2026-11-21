"""Versions-/Update-Hinweis für das LogWatch-Dashboard (Variante 1).

Prüft über die GitHub-API, ob eine neuere Version des Dashboard-Repos
veröffentlicht wurde, und stellt dem Admin einen dezenten Hinweis bereit.

Design
------
* **Nur Hinweis, kein Self-Update:** Ein Web-Dashboard aktualisiert man durch
  erneutes Deployen (``git pull`` + Dienst-Neustart), nicht durch Ersetzen einer
  laufenden Datei. Dieses Modul meldet lediglich, dass eine neue Version da ist.
* **Robust:** Wirft nie eine Exception in Richtung der Seiten-Views. Bei jedem
  Problem (kein Netz, Limit, kein Release) wird einfach kein Hinweis angezeigt.
* **Schonend:** Das Ergebnis wird zwischengespeichert (Cache mit TTL), damit die
  GitHub-API NICHT bei jedem Seitenaufruf angefragt wird.
* **Öffentliche UND private Repos:** Ist ein GitHub-Token (``GITHUB_PAT``)
  hinterlegt, wird es als ``Authorization: Bearer <token>`` gesendet. Das Token
  wird NIEMALS geloggt oder ausgegeben.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
GITHUB_OWNER = "Obi811"
GITHUB_REPO = "Logwatch-Dashboard"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
GITHUB_API_VERSION = "2022-11-28"
HTTP_TIMEOUT = 6  # Sekunden – kurz, damit ein Seitenaufruf nie lange hängt

# Cache-Lebensdauer: Erfolg lange, Fehler kurz (damit Störungen schnell abklingen)
CACHE_TTL_OK = 3600      # 1 Stunde
CACHE_TTL_FAIL = 300     # 5 Minuten

# Signatur des injizierbaren GET-Transports: (url, headers, timeout) -> Response-artig
GetFunc = Callable[[str, dict, int], "object"]


# ---------------------------------------------------------------------------
# Ergebnis-Datenstruktur
# ---------------------------------------------------------------------------
@dataclass
class UpdateInfo:
    """Ergebnis einer Update-Prüfung (für die Template-Anzeige)."""
    ok: bool = False                 # technische Prüfung erfolgreich?
    local_version: str = ""
    remote_version: str = ""
    update_available: bool = False
    release_name: str = ""
    release_url: str = ""
    error: str = ""                  # deutschsprachig, ohne Token


# ---------------------------------------------------------------------------
# Lokale Version lesen
# ---------------------------------------------------------------------------
def _version_file_path() -> str:
    # app/version_check.py -> Projektwurzel ist eine Ebene über 'app/'.
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(pkg_dir), "VERSION")


def read_local_version(default: str = "0.0.0") -> str:
    """Liest die lokale Version aus der VERSION-Datei (robust)."""
    try:
        path = _version_file_path()
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                return text
    except OSError:
        pass
    return default


# ---------------------------------------------------------------------------
# Versionsvergleich (semantisch, tolerant gegenüber führendem "v")
# ---------------------------------------------------------------------------
def normalize_version(value: str) -> str:
    v = (value or "").strip()
    if v[:1] in ("v", "V"):
        v = v[1:]
    return v.strip()


def _version_tuple(value: str) -> Tuple[int, ...]:
    v = normalize_version(value)
    parts = v.split(".") if v else []
    nums: List[int] = []
    for part in parts:
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def compare_versions(a: str, b: str) -> int:
    """-1 (a<b), 0 (a==b), 1 (a>b)."""
    ta, tb = _version_tuple(a), _version_tuple(b)
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


def is_newer(remote: str, local: str) -> bool:
    return compare_versions(remote, local) > 0


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def build_headers(token: Optional[str] = None) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "LogWatch-Dashboard-UpdateCheck",
    }
    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def _requests_get(url: str, headers: dict, timeout: int):
    import requests
    return requests.get(url, headers=headers, timeout=timeout)


def check_for_update(local_version: Optional[str] = None,
                     token: Optional[str] = None,
                     get_func: Optional[GetFunc] = None) -> UpdateInfo:
    """Einmalige Update-Prüfung (ohne Cache). Wirft KEINE Exception."""
    local = (local_version or read_local_version()).strip()
    transport = get_func or _requests_get
    try:
        resp = transport(GITHUB_API_LATEST, build_headers(token), HTTP_TIMEOUT)
    except Exception:  # noqa: BLE001 – jeder Netzwerkfehler
        return UpdateInfo(ok=False, local_version=local,
                          error="Keine Verbindung zu GitHub möglich.")

    status = getattr(resp, "status_code", None)
    if status != 200:
        if status in (401, 403):
            msg = "Zugriff verweigert oder Anfrage-Limit erreicht."
        elif status == 404:
            msg = "Kein Release gefunden."
        else:
            msg = f"GitHub-Anfrage fehlgeschlagen (HTTP {status})."
        return UpdateInfo(ok=False, local_version=local, error=msg)

    try:
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError
    except Exception:  # noqa: BLE001
        return UpdateInfo(ok=False, local_version=local,
                          error="Unerwartete Antwort von GitHub.")

    tag = str(data.get("tag_name") or data.get("name") or "").strip()
    if not tag:
        return UpdateInfo(ok=False, local_version=local,
                          error="Release ohne Versions-Tag erhalten.")
    remote = normalize_version(tag)
    return UpdateInfo(
        ok=True,
        local_version=local,
        remote_version=remote,
        update_available=is_newer(remote, local),
        release_name=str(data.get("name") or tag),
        release_url=str(data.get("html_url") or
                        f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"),
    )


# ---------------------------------------------------------------------------
# Gecachte Variante (für den Einsatz in den Views)
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_cache_value: Optional[UpdateInfo] = None
_cache_expires: float = 0.0


def cached_update_check(enabled: bool = True,
                        token: Optional[str] = None,
                        get_func: Optional[GetFunc] = None,
                        now: Optional[float] = None) -> Optional[UpdateInfo]:
    """Liefert das (zwischengespeicherte) Prüfergebnis.

    Gibt ``None`` zurück, wenn die Prüfung deaktiviert ist – dann wird auch kein
    Netzwerkzugriff gemacht (wichtig für Tests/Offline-Betrieb).
    """
    if not enabled:
        return None
    global _cache_value, _cache_expires
    ts = now if now is not None else time.time()
    with _cache_lock:
        if _cache_value is not None and ts < _cache_expires:
            return _cache_value
    # Prüfung außerhalb des Locks (Netzwerk), danach Ergebnis speichern.
    info = check_for_update(token=token, get_func=get_func)
    with _cache_lock:
        _cache_value = info
        _cache_expires = ts + (CACHE_TTL_OK if info.ok else CACHE_TTL_FAIL)
    return info


def reset_cache() -> None:
    """Cache leeren (v. a. für Tests)."""
    global _cache_value, _cache_expires
    with _cache_lock:
        _cache_value = None
        _cache_expires = 0.0
