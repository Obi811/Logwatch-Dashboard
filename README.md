# LogWatch Dashboard

Zentrales, gehostetes Web-Dashboard für die LogWatch-Clients. Die Windows-Clients
überwachen Logdateien, erkennen Fehler und senden – zusätzlich zum E-Mail-Versand –
einen Fehlerbericht an dieses Dashboard. Mitarbeiter/Admins sehen hier alle Maschinen,
die Fehler-Historie, mögliche Raubkopien und Auswertungen.

## Zweck & Funktionsumfang

- **Interner Login** (Session-Cookie): Admin-/Mitarbeiter-Konten, die **alle**
  Maschinen sehen. Firma/Kunde wird je Maschine gespeichert (für
  Gruppierung/Filter). Zusätzlich gibt es ein optionales **Kunden-Login**
  (Rolle `kunde`), das **nur** die Maschinen des eigenen Kunden sieht.
- **Rollensystem (Admin / Benutzer / Kunde)**: jedes Konto hat eine Rolle
  (`admin`, `user` oder `kunde`, Standard `user`). **Admins** dürfen alles
  inklusive der Benutzer-, Kunden- und SMTP-Verwaltung; **Benutzer** sehen alle
  Seiten (Übersicht, Maschinen, Warnungen, Meldungen, Statistik) und dürfen
  kommentieren, aber **nicht** die Verwaltungsseiten öffnen; ein **Kunde** sieht
  ausschließlich die Daten des ihm zugeordneten Kunden (eigene Maschinen,
  Meldungen, Statistik) und darf nichts schreiben. Details siehe Abschnitt
  „Rollen & Rechte".
- **Kundenverwaltung** (`/customers`, nur Admins): Kunden anlegen/bearbeiten/
  löschen, E-Mail-Einstellungen (Benachrichtigungs-Intervall, Sofort-Mail bei
  Fehler), Maschinen einem Kunden zuordnen und Test-Mail versenden. Details
  siehe Abschnitt „Kundenverwaltung".
- **E-Mail-Versand (SMTP)**: Sofort-Benachrichtigung bei neuen Fehlern einer
  Kundenmaschine sowie wöchentliche/monatliche Sammelberichte (HTML-Digest) je
  Kunde. SMTP-Zugang wird im UI konfiguriert (Passwort verschlüsselt). Details
  siehe Abschnitte „E-Mail-Versand (SMTP)" und „Automatischer Versand / Cronjob".
- **Benutzerverwaltung** (`/users`, nur Admins): Admins können weitere
  Dashboard-Benutzer anlegen (inkl. Rollenwahl), deren Passwort ändern, die Rolle
  umstellen und Konten löschen. Benutzernamen sind eindeutig, Passwörter
  mindestens 8 Zeichen; das eigene Konto, der letzte verbleibende Benutzer und
  der letzte verbleibende Admin sind geschützt (letzterer kann weder gelöscht
  noch herabgestuft werden).
- **Kommentare an Meldungen**: jeder Fehlerbericht (im UI „Meldung") kann auf der
  Detailseite kommentiert werden. Kommentare erscheinen mit Autor und Zeitstempel;
  löschen darf nur der Autor oder ein Admin. Auch über die REST-API verfügbar.
- **Versions-/Update-Hinweis**: das Dashboard prüft über die GitHub-API, ob eine
  neuere Version veröffentlicht wurde, und zeigt dann einen dezenten Hinweis mit
  Link zum Release. Kein automatisches Self-Update – aktualisiert wird durch
  erneutes Deployen (`git pull` + Dienst-Neustart). Details siehe unten.
- **Upload-API** für die Clients (Token-geschützt, **ohne** Login).
- **Maschinenübersicht** mit Firma, Zeitpunkten, offenen/gesamten Fehlern.
- **Kopier-Erkennung**: Meldet dieselbe Maschinen-ID unterschiedliche Hardware-IDs
  (≥ 2 verschiedene), erscheint eine prominente Warnung – an der Maschine und auf
  einer eigenen Warnungs-Seite, inkl. beteiligter Hardware-IDs + Zeitpunkte.
- **Fehler-Historie**: global und je Maschine, mit Detailansicht (vollständige
  Fehlerzeilen), **Logdatei-Download**, **Abschließen/Wieder öffnen** (speichert
  Zeit + Benutzer), Filter nach Status und Maschine/Firma.
- **Auswertungen/Statistiken**: Kennzahlen + Diagramm „Fehler pro Tag“ (Chart.js).
- **Laufzeiten/Betriebszeiten** sind bewusst noch **nicht** umgesetzt. Das
  Datenmodell erfasst aber bereits Zeitstempel (`first_seen`/`last_seen`,
  `received_at`), sodass eine Heartbeat-/Laufzeit-Auswertung später ohne
  Schemabruch ergänzt werden kann (z.B. eigene Tabelle `heartbeats`).

## Architektur

- **FastAPI** + **Uvicorn**, serverseitig gerendert mit **Jinja2** (keine SPA).
- **SQLAlchemy 2.0** auf **PostgreSQL** (Treiber **psycopg v3**), Connection-Pool
  (max. 10 gleichzeitige Verbindungen, serverseitiges `statement_timeout = 5s`,
  passend zu den Limits der gehosteten DB: max. 25 Verbindungen, 5s Query-Timeout).
- **bcrypt** für Passwort-Hashes, **Starlette SessionMiddleware** (signierte Cookies)
  für den Login.
- Hochgeladene Logdateien liegen unter `data/uploads/` (nicht im Git).

### Datenmodell (`app/models.py`)

- `users` – interne Dashboard-Benutzer, mit `role` (`admin`/`user`/`kunde`) und
  optionalem `customer_id` (nur bei Rolle `kunde` gesetzt – bindet das Login an
  einen Kunden).
- `machines` – eine Maschine je `machine_id` (PK), mit `company`, `first_seen`,
  `last_seen`, `notes` und optionalem `customer_id` (Zuordnung zu einem Kunden;
  `NULL` = „intern", ohne Kunde).
- `customers` – Kunden/Empfänger für Benachrichtigungen: `name` (Firma) und
  `email` (beide Pflicht), `contact` (Ansprechpartner), `note`,
  `notify_on_error` (Sofort-Mail an/aus), `digest_interval`
  (`none`/`weekly`/`monthly`), `last_digest_sent_at`, `created_at`.
- `smtp_settings` – eine Zeile mit dem SMTP-Zugang: `host`, `port`, `username`,
  `password_enc` (**Fernet-verschlüsselt**), `from_email`, `from_name`,
  `security` (`starttls`/`ssl`/`none`).
- `hardware_fingerprints` – je `(machine_id, hardware_id)` eindeutig; Basis der
  Kopier-Erkennung; speichert zusätzlich `teamviewer_id`, `hostname`, Zeitstempel.
- `error_reports` – einzelne Fehlerberichte mit `status` (`offen`/`abgeschlossen`),
  `closed_at`/`closed_by`/`note`, `timestamp` (Fehler) + `received_at` (Eingang),
  `error_lines`, `error_count`, `log_filename`/`log_path`.
- `comments` – Kommentare an einem Fehlerbericht; Fremdschlüssel auf
  `error_reports` und `users` (Kaskaden-Löschung), mit `body` und `created_at`.

## Rollen & Rechte

Jeder Dashboard-Benutzer hat eine Rolle (Spalte `role`):

- **Admin** – voller Zugriff, inklusive der Benutzerverwaltung unter `/users`
  (Konten anlegen inkl. Rollenwahl, Passwort ändern, Rolle umstellen, löschen)
  und dem Nav-Punkt „Benutzer".
- **Benutzer** – sieht alle Auswertungs-Seiten (Übersicht, Maschinen, Warnungen,
  Meldungen, Statistik) und darf Meldungen kommentieren, hat aber **keinen**
  Zugriff auf die Verwaltungsseiten (Benutzer/Kunden/SMTP; Weiterleitung mit
  Flash-Fehler im Web, HTTP 403 in der API) und sieht deren Nav-Punkte nicht.
- **Kunde** – an genau einen Kunden gebunden (`users.customer_id`) und sieht
  **ausschließlich** die diesem Kunden zugeordneten Maschinen samt deren
  Meldungen und Statistik (serverseitiges Scoping aller Lese-Routen). Keine
  Warnungen-Seite, keine Benutzer-/Kunden-/SMTP-Verwaltung und **keine**
  Schreibaktionen (Kommentieren, Abschließen/Wieder öffnen) – sonst
  Weiterleitung zur Startseite mit Flash-Fehler. Ruft ein Kunde eine fremde
  Maschine/Meldung direkt auf, erhält er HTTP 404. Kunden-Logins werden
  pragmatisch über das bestehende Formular „Benutzer anlegen" (`/users`) mit der
  Rolle „Kunde" und Kundenauswahl erstellt. Kunden **ohne** Login (reine
  E-Mail-Empfänger) werden nur als Kunden-Datensatz unter `/customers` angelegt.

Die Rollenprüfung ist in der wiederverwendbaren Abhängigkeit `require_admin`
gebündelt (Web-Variante über den Session-User, API-Variante über den
Token-User). Standardrolle neuer Konten ist „Benutzer". Der **letzte
verbleibende Admin** kann weder herabgestuft noch gelöscht werden, damit das
System nie ohne Admin bleibt. Bei der Migration auf das Rollensystem wurden
bestehende Konten zu Admins befördert (Aussperr-Schutz).

**Kommentare:** Jede Meldung (`error_reports`) kann auf ihrer Detailseite
kommentiert werden. Ein Kommentar darf nur von seinem **Autor** oder einem
**Admin** gelöscht werden; leere Kommentare werden abgelehnt.

## Kundenverwaltung

Unter `/customers` (nur Admins; Nav-Punkt „Kunden") werden **Kunden** verwaltet.
Ein Kunde bündelt Maschinen und ist Empfänger der E-Mail-Benachrichtigungen:

- **Anlegen/Bearbeiten/Löschen:** Pflichtfelder sind **Firma/Name** und
  **E-Mail**; optional Ansprechpartner und Notiz. Je Kunde lässt sich einstellen,
  ob bei neuen Fehlern eine **Sofort-Mail** verschickt wird (`notify_on_error`)
  und in welchem **Intervall** ein Sammelbericht kommt (keiner / wöchentlich /
  monatlich). Beim Löschen eines Kunden bleiben seine Maschinen erhalten und
  fallen auf „intern" (ohne Kunde) zurück.
- **Maschinen zuordnen:** Jede Maschine kann genau einem Kunden zugeordnet werden
  (Dropdown). Maschinen ohne Zuordnung gelten als **intern** und lösen keine
  Kundenmails aus.
- **Test-Mail:** Button „Test-Mail senden" verschickt den aktuellen
  Sammelbericht des Kunden sofort (nützlich zum Prüfen von Vorlage und
  SMTP-Zugang).
- **Kunden-Login (optional):** Soll ein Kunde sich selbst einloggen, legt ein
  Admin unter `/users` einen Benutzer mit Rolle **„Kunde"** an und wählt den
  Kunden aus. Dieser sieht dann nur die Daten seiner Maschinen (siehe „Rollen &
  Rechte"). Kunden, die sich **nicht** einloggen, brauchen nur den
  Kunden-Datensatz (reine E-Mail-Empfänger).

> **Oberfläche:** Die Admin-Seiten (`/customers`, `/users`, `/settings/smtp`)
> nutzen ein **zweispaltiges, responsives Layout** – das schmale Anlege- bzw.
> Status-Panel steht links, die breiten Tabellen/Formulare rechts, sodass die
> verfügbare Seitenbreite genutzt wird. Auf schmalen Viewports (Smartphone)
> stapeln sich die Spalten sauber untereinander.

## E-Mail-Versand (SMTP)

Das Dashboard verschickt zwei Arten von E-Mails (HTML, deutsch):

1. **Sofort-Fehler-Mail** – sobald der Windows-Client eine neue Meldung für eine
   Maschine hochlädt, die einem Kunden mit aktivem `notify_on_error` zugeordnet
   ist, geht eine sofortige Benachrichtigung an die Kunden-E-Mail. Der Versand
   ist **best effort**: schlägt er fehl (oder ist kein SMTP konfiguriert),
   beeinträchtigt das die Upload-Verarbeitung/API-Antwort nie.
2. **Sammelbericht (Digest)** – pro Kunde eine HTML-Mail mit Kennzahlen
   (überwachte Maschinen, Meldungen im Zeitraum, davon offen) und einer
   Aufschlüsselung je Maschine (Zeit, Maschine, Fehleranzahl, Status). Enthält
   **keine** Kommentare.

**SMTP-Konfiguration** erfolgt unter `/settings/smtp` (nur Admins; Nav-Punkt
„SMTP"): Host, Port, Benutzer, Passwort, Absenderadresse/-name und Sicherheit
(**STARTTLS**, **SSL/TLS** oder **keine**). Das Passwort wird **Fernet-
verschlüsselt** gespeichert (Schlüssel aus `SECRET_KEY` abgeleitet) und **nie**
im Klartext ausgegeben – das Passwortfeld im Formular bleibt leer und
überschreibt nur bei Eingabe. Ist in der DB kein Host hinterlegt, greift ein
Fallback auf die `SMTP_*`-Umgebungsvariablen (siehe Env-Tabelle). Über „Test-Mail
senden" lässt sich die Konfiguration prüfen.

## Automatischer Versand / Cronjob

Die Sammelberichte verschickt das Kommando `python -m app.send_digests`. Es
ermittelt fällige Kunden anhand von `digest_interval` und `last_digest_sent_at`
(**wöchentlich** ≥ 7 Tage, **monatlich** ≥ 28 Tage seit letztem Versand;
Erstversand sofort) und ist **idempotent** – ein zweiter Lauf am selben Tag
verschickt nicht erneut. Optionen: `--dry-run` (nichts senden, nur anzeigen) und
`--customer-id <ID>` (nur ein Kunde).

**Variante A – systemd-Timer (auf diesem Server, empfohlen):** Die Units liegen
in `deploy/` und werden wie der Dienst per Symlink eingebunden:

```bash
sudo ln -sf $PWD/deploy/logwatch-digests.service /etc/systemd/system/logwatch-digests.service
sudo ln -sf $PWD/deploy/logwatch-digests.timer   /etc/systemd/system/logwatch-digests.timer
sudo systemctl daemon-reload && sudo systemctl enable --now logwatch-digests.timer
systemctl list-timers | grep logwatch        # nächste Fälligkeit prüfen
journalctl -u logwatch-digests.service -n 50  # letzten Lauf ansehen
```

Der Timer läuft täglich um 06:00 Uhr (`Persistent=true` holt verpasste Läufe nach
einem Neustart nach). Das eigentliche Intervall (wöchentlich/monatlich) steuert
das Skript selbst je Kunde – der Timer darf ruhig täglich laufen.

**Variante B – Cron (z.B. auf einem klassischen Webspace):**

```cron
0 6 * * * cd /pfad/zum/logwatch-dashboard && /pfad/zum/logwatch-dashboard/venv/bin/python -m app.send_digests >> logs/digests.log 2>&1
```

## Projektstruktur

```
logwatch-dashboard/
├─ app/
│  ├─ main.py            # FastAPI-App, Web-Routen, Upload-API, Kommentare, Kunden/SMTP
│  ├─ api.py             # REST-API (/api/v1): App-Backend + Kommentar-Endpunkte
│  ├─ api_auth.py        # Token-Auth + require_admin (API)
│  ├─ settings.py        # Einstellungen aus .env (inkl. SMTP_*-Fallback)
│  ├─ db.py              # Engine + Connection-Pool + Session
│  ├─ models.py          # SQLAlchemy-Datenmodell (users.role, comments, customers, smtp_settings)
│  ├─ auth.py            # bcrypt + Session-Helfer
│  ├─ crypto.py          # Fernet-Ver-/Entschlüsselung (SMTP-Passwort)
│  ├─ mailer.py          # SMTP-Versand + HTML-Vorlagen (Digest/Fehler)
│  ├─ send_digests.py    # Kommando für fällige Sammelberichte (Cron/Timer)
│  ├─ version_check.py   # Update-Hinweis (GitHub-Release-Prüfung, gecacht)
│  ├─ templates/         # Jinja2-Templates (deutsch)
│  └─ static/style.css
├─ deploy/
│  ├─ logwatch-dashboard.service   # systemd-Unit (Web-Dienst)
│  ├─ logwatch-dash.conf           # nginx-vhost
│  ├─ logwatch-digests.service     # systemd-oneshot (Sammelbericht-Versand)
│  └─ logwatch-digests.timer       # systemd-Timer (täglich)
├─ init_db.py            # idempotentes Schema-Init + Seed-Admin + Migrationen
├─ tests/test_dashboard.py         # Web-UI-Tests (inkl. Kommentare + Rollen)
├─ tests/test_api.py               # REST-API-Tests (inkl. Kommentare + role)
├─ tests/test_customers.py         # Kunden/SMTP/Mail/Scoping-Tests (SMTP gemockt)
├─ requirements.txt
├─ .env.example
└─ .gitignore
```

## Umgebungsvariablen (`.env`)

Siehe `.env.example`. Die echte `.env` wird **nicht** eingecheckt.

| Variable | Beschreibung |
|---|---|
| `DATABASE_URL` | PostgreSQL-URL (`postgresql://user:pass@host:5432/db`). Wird intern auf `postgresql+psycopg://` normalisiert. |
| `ADMIN_USER` | Benutzername des Seed-Admins (Standard `admin`). |
| `ADMIN_PASSWORD` | Passwort des Seed-Admins. Wird nur beim ersten Init genutzt (wenn noch kein Benutzer existiert). |
| `UPLOAD_TOKEN` | Shared Secret für die Upload-API. Muss im Client identisch eingetragen werden. |
| `SECRET_KEY` | Schlüssel zum Signieren der Session-Cookies (lang & zufällig). |
| `PUBLIC_HOST` | (optional) öffentlicher Host, nur für Links/Logs. |
| `UPLOAD_DIR` | (optional) Verzeichnis für Logdateien (Standard `data/uploads`). |
| `GITHUB_PAT` | (optional) GitHub-Token für den Update-Hinweis. Nur nötig, wenn das Dashboard-Repo **privat** ist. Wird nie geloggt. |
| `UPDATE_CHECK_ENABLED` | (optional) Update-Hinweis an/aus (Standard `1`). Auf `0` setzen, um die GitHub-Abfrage abzuschalten. |
| `API_CORS_ORIGINS` | (optional) Erlaubte Ursprünge (Origins) für die App-/REST-API, komma-separiert. Standard `*` (jeder Ursprung). Für den Produktivbetrieb eng setzen, z.B. `https://app.example.com,https://admin.example.com`. |
| `SMTP_HOST` | (optional) SMTP-Server als **Fallback**, falls unter `/settings/smtp` nichts hinterlegt ist. Die UI-Konfiguration (DB) hat Vorrang. |
| `SMTP_PORT` | (optional) SMTP-Port (Standard `587`). |
| `SMTP_USER` | (optional) SMTP-Benutzername. |
| `SMTP_PASSWORD` | (optional) SMTP-Passwort (nur Fallback; in der DB wird es verschlüsselt gespeichert). |
| `SMTP_FROM_EMAIL` | (optional) Absenderadresse der E-Mails. |
| `SMTP_FROM_NAME` | (optional) Absendername (Standard `LogWatch Dashboard`). |
| `SMTP_SECURITY` | (optional) Transportsicherheit: `starttls` (Standard), `ssl` oder `none`. |

## Lokale Entwicklung

```bash
cd logwatch-dashboard
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env     # Werte eintragen (DATABASE_URL, UPLOAD_TOKEN, SECRET_KEY, ADMIN_PASSWORD)
./venv/bin/python init_db.py            # Schema anlegen + Seed-Admin
./venv/bin/uvicorn app.main:app --reload --port 8070
# -> http://127.0.0.1:8070/login
```

### Tests

```bash
./venv/bin/pip install pytest httpx
./venv/bin/python -m pytest -q
```

Die Tests nutzen eine temporäre SQLite-DB und ein Test-Token; die echte DB wird
nicht berührt.

## Deployment (Abacus Supercomputer)

1. venv anlegen und Abhängigkeiten installieren (siehe oben).
2. `.env` mit echter `DATABASE_URL` (aus `LOGWATCH_DATABASE_URL`), zufälligem
   `SECRET_KEY` und `UPLOAD_TOKEN` sowie `ADMIN_PASSWORD` füllen.
3. `./venv/bin/python init_db.py` ausführen (idempotent).
4. systemd-Unit & nginx-vhost aus `deploy/` nach `/etc/systemd/system/` bzw.
   `/etc/nginx/conf.d/` **symlinken**:
   ```bash
   sudo ln -sf $PWD/deploy/logwatch-dashboard.service /etc/systemd/system/logwatch-dashboard.service
   sudo ln -sf $PWD/deploy/logwatch-dash.conf /etc/nginx/conf.d/logwatch-dash.conf
   sudo systemctl daemon-reload && sudo systemctl enable --now logwatch-dashboard
   sudo nginx -t && sudo systemctl reload nginx
   ```
5. Verifizieren:
   ```bash
   curl -s http://127.0.0.1:8070/healthz
   curl -s -H 'Host: logwatch-dash.vm.internal' http://localhost/login | grep "Interne Anmeldung"
   ```

Die App läuft auf `127.0.0.1:8070`; nginx terminiert die öffentliche HTTPS-URL und
reicht sie als Plain-HTTP weiter. Der echte Public-Host kommt über `X-Original-Host`.

## API für Apps (REST, `/api/v1`)

Zusätzlich zum Web-UI (Session-Login) stellt das Dashboard eine **token-basierte
JSON-REST-API** bereit, die als Backend für eine mobile App dient. Sie ist
vollständig getrennt vom Web-UI; letzteres bleibt unverändert.

- **Basis-URL:** `https://logwatch-dash.abacusai.cloud/api/v1`
- **Interaktive Referenz (Swagger):** `https://logwatch-dash.abacusai.cloud/docs`
  (OpenAPI-JSON unter `/openapi.json`). Alle Endpunkte sind dort mit deutschen
  Beschreibungen, Tags und Response-Modellen dokumentiert.

### Authentifizierung (Bearer-Token)

1. **Anmelden:** `POST /api/v1/auth/login` mit JSON-Body
   `{"username": "...", "password": "..."}`. Geprüft wird gegen dieselbe
   Benutzer-Tabelle wie beim Web-Login.
2. Antwort: `{"access_token": "...", "token_type": "bearer", "expires_in": 2592000}`
   (Token signiert mit `itsdangerous`/`SECRET_KEY`, gültig **30 Tage**,
   enthält die `user_id`).
3. **Alle weiteren Aufrufe** senden den Header `Authorization: Bearer <token>`.
   Fehlt der Token oder ist er ungültig/abgelaufen, antwortet die API mit
   **HTTP 401** und einer deutschen Fehlermeldung. Der Token wird nie geloggt.

Beispiel:

```bash
# Token holen
TOKEN=$(curl -s -X POST https://logwatch-dash.abacusai.cloud/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"Obi811","password":"<passwort>"}' | jq -r .access_token)

# Geschützten Endpunkt abrufen
curl -s https://logwatch-dash.abacusai.cloud/api/v1/overview \
  -H "Authorization: Bearer $TOKEN"
```

### Endpunkte

| Methode & Pfad | Beschreibung |
|---|---|
| `POST /api/v1/auth/login` | Anmelden, liefert Bearer-Token (öffentlich). |
| `GET /api/v1/me` | Angemeldeten Benutzer (`id`, `username`, `role`) abrufen. |
| `GET /api/v1/overview` | Kennzahlen: Maschinen, offene/abgeschlossene Berichte, Kopier-Warnungen, letzte Berichte. |
| `GET /api/v1/machines` | Liste aller Maschinen (optional `?q=` Suche) inkl. Bericht-Zählern. |
| `GET /api/v1/machines/{id}` | Maschinen-Detail inkl. Hardware-Fingerprints und Berichten. |
| `GET /api/v1/warnings` | Kopier-Warnungen (optional `?status=open` / `?status=acknowledged`). |
| `POST /api/v1/warnings/{machine_id}/acknowledge` | Kopier-Warnung einer Maschine quittieren. |
| `GET /api/v1/reports` | Fehlerberichte, paginierbar (`?limit=&offset=`, optional `?status=&machine_id=`). |
| `GET /api/v1/reports/{id}` | Fehlerbericht-Detail inkl. Fehlerzeilen. |
| `GET /api/v1/reports/{id}/comments` | Kommentare einer Meldung (mit Autor + Zeitstempel). |
| `POST /api/v1/reports/{id}/comments` | Kommentar zu einer Meldung anlegen (leerer Text → 400). |
| `DELETE /api/v1/comments/{id}` | Kommentar löschen (nur Autor oder Admin, sonst 403). |

> Hinweis: `POST /api/v1/reports` ist weiterhin der **Upload-Endpunkt der
> Windows-Clients** (per `X-Upload-Token`, siehe nächster Abschnitt) – er ist von
> der App-Authentifizierung unberührt.

### CORS

Die API erlaubt den Zugriff aus einer App von einer anderen Origin. Die erlaubten
Ursprünge werden über die Umgebungsvariable `API_CORS_ORIGINS` gesteuert
(Standard `*`; siehe Tabelle oben). Für den Produktivbetrieb eng setzen.

## Upload-API-Vertrag (Client ↔ Dashboard)

Dieser Vertrag muss in Client und Dashboard identisch sein (im Client in
`logwatch/uploader.py`, im Dashboard in `app/main.py`).

```
POST {base_url}/api/v1/reports
Header:  X-Upload-Token: <UPLOAD_TOKEN>
Body:    multipart/form-data
  - Feld  "payload"  : JSON-Objekt (siehe unten)
  - Datei "logfile"  : die Logdatei (optional)
```

`payload`-JSON:

| Feld | Typ | Beschreibung |
|---|---|---|
| `machine_id` | string (Pflicht) | Vom Betreiber vergebene Maschinen-ID. |
| `company` | string | Firma/Kunde (wird an der Maschine gespeichert). |
| `hardware_id` | string | Hardware-Fingerprint (Basis der Kopier-Erkennung). |
| `teamviewer_id` | string | TeamViewer-ID (Fernwartung). |
| `hostname` | string | Rechnername. |
| `timestamp` | string | ISO-8601 (UTC), Zeitpunkt des Fehlers. |
| `error_count` | int | Anzahl Fehlerzeilen. |
| `error_lines` | string[] | Die auslösenden Logzeilen. |
| `log_filename` | string | Ursprünglicher Dateiname der Logdatei. |

**Antwort:** `201 Created`,
`{"status":"ok","report_id":<int>,"copy_warning":<bool>}`.
`copy_warning=true`, sobald die Maschine ≥ 2 verschiedene Hardware-IDs gemeldet hat.

**Fehler:** `401` (Token fehlt/falsch), `400` (ungültiges payload / fehlende
`machine_id`).

Serverseitiges Verhalten: Maschine upsert (aktualisiert `last_seen`, setzt `company`
falls mitgegeben) → Hardware-Fingerprint upsert (eindeutig `machine_id+hardware_id`)
→ Logdatei unter `data/uploads/` ablegen (eindeutiger Name) → Fehlerbericht anlegen.

## Hinweis Kopier-Erkennung

Eine Warnung ist ein **Hinweis**, kein Beweis: legitime Hardware-Wechsel (neue
Platte, Mainboard-Tausch) erzeugen ebenfalls eine neue Hardware-ID. Die Warnung
zeigt alle beteiligten Hardware-IDs samt Zeitpunkten, damit ein Mensch den Fall
bewerten kann.

## Projektpflege

- **Versionen/Änderungen:** siehe [`CHANGELOG.md`](CHANGELOG.md) (Semantische Versionierung).
- **Offene Aufgaben/Ideen:** siehe [`TODO.md`](TODO.md).

Konvention: Bei jeder Änderung werden `CHANGELOG.md`, `TODO.md` und diese
`README.md` mitgepflegt.

### CI & automatisierte Pflege (GitHub Actions)

- **Tests (`.github/workflows/tests.yml`):** laufen bei jedem Push/PR
  (SQLite-isoliert, kein PostgreSQL nötig).
- **Doku-Check (`.github/workflows/docs-check.yml`):** schlägt fehl, wenn Code
  geändert wurde, ohne den `CHANGELOG.md` zu pflegen.
- **Dependabot (`.github/dependabot.yml`):** prüft wöchentlich die
  Python-Pakete und die GitHub-Actions und schlägt Aktualisierungen als
  **gesammelte Pull-Requests** vor. Es wird nichts automatisch zusammengeführt
  – jeder Update-PR wird von dir geprüft und bestätigt. Bekannte
  Sicherheitslücken werden zusätzlich automatisch als PR gemeldet.
- **Sicherheits-Audit (`.github/workflows/security-audit.yml`):** prüft die
  Abhängigkeiten bei jedem Push/PR und wöchentlich mit `pip-audit`.
- **Release:** `python scripts/release.py X.Y.Z --push` überführt den Block
  „Unveröffentlicht" des CHANGELOG in einen Versionsabschnitt, setzt `VERSION`
  und (mit `--push`) Commit und Tag `vX.Y.Z`.
- **Tipp – Tests als Pflicht-Merge-Bedingung:** einmalig in GitHub unter
  *Settings → Branches → Protection rule* für `main` „Require status checks to
  pass" aktivieren und `Tests`, `Security-Audit` und `Doku-Check` auswählen.
