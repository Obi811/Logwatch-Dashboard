# Changelog

Alle nennenswerten Änderungen am LogWatch-Dashboard werden in dieser Datei
dokumentiert.

Das Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
und das Projekt folgt der [Semantischen Versionierung](https://semver.org/lang/de/).

## [Unveröffentlicht]
### Hinzugefügt
- **Kommentarfunktion für Meldungen:** Jeder Fehlerbericht (Entität
  `ErrorReport`, im UI „Meldung") kann nun kommentiert werden. Auf der
  Detailseite einer Meldung (`/reports/{id}`) erscheinen alle Kommentare mit
  Autor und Zeitstempel sowie ein Eingabefeld zum Hinzufügen. Neue Tabelle
  `comments` (Fremdschlüssel auf `error_reports` und `users`, Kaskaden-Löschung).
  Web-Routen `POST /reports/{id}/comments` (anlegen) und
  `POST /comments/{id}/delete` (löschen). Leere Kommentare werden abgelehnt;
  löschen darf nur der **Autor** oder ein **Admin** (sonst Flash-Fehler bzw.
  HTTP 403 in der API). REST-API: `GET /api/v1/reports/{id}/comments`,
  `POST /api/v1/reports/{id}/comments` und `DELETE /api/v1/comments/{id}`.
- **Rollensystem (Admin / Benutzer):** Neue Spalte `role` an der Tabelle `users`
  (Werte `admin`/`user`, Standard `user`, rückwärtskompatibel per
  `ADD COLUMN IF NOT EXISTS` nachgezogen). **Admin** darf alles inkl. der
  kompletten Benutzerverwaltung (`/users`: anlegen, Passwort ändern, Rolle
  umstellen, löschen) und sieht den Nav-Punkt „Benutzer". Ein **Benutzer** sieht
  alle Seiten (Übersicht, Maschinen, Warnungen, Meldungen, Statistik) und darf
  kommentieren, aber **nicht** die Benutzerverwaltung öffnen (Weiterleitung mit
  Flash-Fehler bzw. HTTP 403 in der API). Zentrale, wiederverwendbare
  Absicherung `require_admin` (Web über Session-User, API über Token-User).
  Die Benutzeranlage erlaubt die Rollenwahl (Dropdown, Standard „Benutzer"),
  die Benutzertabelle zeigt die Rolle und ein Inline-Formular zum Umstellen.
  Safeguard: der **letzte verbleibende Admin** kann weder herabgestuft noch
  gelöscht werden. Bestehende Konten wurden bei der Migration zu Admins
  befördert (Aussperr-Schutz). `GET /api/v1/me` liefert jetzt zusätzlich `role`.
- **App-/REST-API (`/api/v1`, JSON)** als Backend für eine mobile App
  (`app/api.py`, `app/api_auth.py`). Getrennt vom Session-basierten Web-UI und
  **token-authentifiziert**: `POST /api/v1/auth/login` prüft Benutzername/Passwort
  gegen die bestehende Benutzer-Tabelle und liefert einen signierten Bearer-Token
  (`itsdangerous`/`SECRET_KEY`, 30 Tage gültig, enthält die `user_id`). Alle
  weiteren Endpunkte verlangen `Authorization: Bearer <token>` (sonst HTTP 401
  mit deutscher Meldung; der Token wird nie geloggt). Endpunkte: `GET /me`,
  `GET /overview`, `GET /machines` (+ `?q=`), `GET /machines/{id}`,
  `GET /warnings` (+ `?status=open|acknowledged`),
  `POST /warnings/{machine_id}/acknowledge` (Kopier-Warnung quittieren),
  `GET /reports` (paginierbar via `?limit=&offset=`, optional `?status=&machine_id=`)
  und `GET /reports/{id}`. Alle Antworten nutzen **Pydantic-Response-Modelle**.
- **Quittieren von Kopier-Warnungen:** Neue, rückwärtskompatible Spalten
  `copy_warning_ack_at` / `copy_warning_ack_by` an der Tabelle `machines`
  (per `ADD COLUMN IF NOT EXISTS` in `init_db.py` nachgezogen).
- **OpenAPI/Swagger aktiviert** (`/docs`, `/redoc`, `/openapi.json`): alle
  `/api/v1`-Routen erscheinen dort mit deutschen Beschreibungen, Tags und
  Response-Modellen – als Referenz für die App-Entwicklung.
- **CORS für die App-API** (`CORSMiddleware`) mit neuer Umgebungsvariable
  `API_CORS_ORIGINS` (komma-separiert, Standard `*`), konfigurierbar in
  `settings.py`.
- **Benutzerverwaltung im UI** (`/users`): eingeloggte Admins/Mitarbeiter können
  weitere Dashboard-Konten **anlegen**, deren **Passwort ändern** und Konten
  **löschen**. Schutzregeln: Benutzernamen sind eindeutig (unabhängig von
  Groß-/Kleinschreibung), Passwörter mindestens 8 Zeichen, das eigene Konto und
  der letzte verbleibende Benutzer können nicht gelöscht werden. Neuer Nav-Punkt
  „Benutzer".
- **Versions-/Update-Hinweis (Variante 1)** (`app/version_check.py`): das
  Dashboard prüft über die GitHub-API, ob eine neuere Version veröffentlicht
  wurde, und blendet dann einen dezenten Hinweis mit Link zum Release ein. Die
  Prüfung ist robust (zeigt bei Problemen einfach nichts), wird
  zwischengespeichert (Cache, kein Abruf bei jedem Seitenaufruf) und
  unterstützt per optionalem `GITHUB_PAT` auch ein privates Repo. Über
  `UPDATE_CHECK_ENABLED=0` abschaltbar. Ein echtes Self-Update (Code ersetzen)
  ist bewusst **nicht** enthalten – ein Web-Dashboard wird durch erneutes
  Deployen (`git pull` + Dienst-Neustart) aktualisiert.
- **CI-Tests** (`.github/workflows/tests.yml`): führen die Integrationstests
  (SQLite-isoliert) bei jedem Push und Pull-Request aus.
- **Doku-Check** (`.github/workflows/docs-check.yml`): schlägt fehl, wenn Code
  geändert wurde, ohne den CHANGELOG zu pflegen.
- **Automatisierte Abhängigkeits-Pflege (Dependabot):** `.github/dependabot.yml`
  prüft wöchentlich die Python-Pakete (`requirements.txt`) und die
  GitHub-Actions und schlägt Aktualisierungen als **gesammelte Pull-Requests**
  vor. Zusammengeführt wird nichts automatisch – die Kontrolle bleibt bei dir.
  Sicherheits-Updates für bekannte Schwachstellen werden zusätzlich automatisch
  als PR eröffnet.
- **Sicherheits-Audit `Security-Audit`** (`.github/workflows/security-audit.yml`):
  prüft die Abhängigkeiten bei jedem Push/PR und wöchentlich mit `pip-audit`
  auf bekannte Sicherheitslücken.
- **Automatisierte Doku-Pflege:** Release-Skript `scripts/release.py`, das beim
  Release den Abschnitt „Unveröffentlicht" des CHANGELOG in einen neuen
  Versionsabschnitt überführt, `VERSION` setzt und (mit `--push`) Tag/Commit auslöst.

### Geändert
- Abhängigkeiten aktualisiert (Dependabot): u. a. `fastapi` (>=0.163.1),
  `uvicorn` (>=0.54.0), `sqlalchemy` (>=2.1.9), `psycopg` (>=3.3.9),
  `bcrypt` (>=5.0.0), `python-multipart` (>=0.0.34), `itsdangerous` (>=2.2.0),
  `jinja2` (>=3.1.6) und `python-dotenv` (>=1.2.4). Tests und Sicherheits-Audit grün.

## [0.1.0] – 2026-11-19
### Hinzugefügt
- Interner Login (Session-Cookie, bcrypt) – nur Admin-/Mitarbeiter-Konten,
  die alle Maschinen sehen; kein Kunden-Self-Service.
- Upload-API `POST /api/v1/reports` (Token-geschützt, ohne Login) zum
  Entgegennehmen von Fehlerberichten samt Logdatei; Healthcheck `/healthz`.
- Maschinenübersicht mit Firma, Zeitpunkten sowie offenen/gesamten Fehlern.
- Fehler-Historie global und je Maschine, Detailansicht mit Fehlerzeilen und
  **Logdatei-Download**.
- Status-System pro Fehlerbericht: **Abschließen / Wieder öffnen**
  (speichert Zeitpunkt und Benutzer); Filter nach Status, Maschine, Firma.
- **Kopier-Erkennung**: Treten für eine Maschinen-ID zwei oder mehr
  unterschiedliche Hardware-IDs auf, erscheint eine Warnung an der Maschine
  sowie auf einer eigenen Warnungs-Seite (mit allen Hardware-IDs/Zeitpunkten).
- **Statistik/Auswertungen**: Kennzahlen und Chart.js-Diagramm „Fehler pro Tag“.
- Deployment: systemd-Unit + nginx-vhost (unter `deploy/`), idempotentes
  DB-Init (`init_db.py`), `.env.example`.

### Hinweise
- „Laufzeiten/Uptime“ sind noch nicht umgesetzt; das Datenmodell erfasst bereits
  die nötigen Zeitstempel, damit das später ohne Schemabruch ergänzbar ist.

[Unveröffentlicht]: https://github.com/Obi811/Logwatch-Dashboard/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Obi811/Logwatch-Dashboard/releases/tag/v0.1.0
