# LogWatch Dashboard

Zentrales, gehostetes Web-Dashboard für die LogWatch-Clients. Die Windows-Clients
überwachen Logdateien, erkennen Fehler und senden – zusätzlich zum E-Mail-Versand –
einen Fehlerbericht an dieses Dashboard. Mitarbeiter/Admins sehen hier alle Maschinen,
die Fehler-Historie, mögliche Raubkopien und Auswertungen.

## Zweck & Funktionsumfang

- **Interner Login** (Session-Cookie): nur wenige Admin-/Mitarbeiter-Konten, die
  **alle** Maschinen sehen. Kein Kunden-Self-Service, kein Mandanten-Modell.
  Firma/Kunde wird je Maschine gespeichert (für Gruppierung/Filter).
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

- `users` – interne Dashboard-Benutzer.
- `machines` – eine Maschine je `machine_id` (PK), mit `company`, `first_seen`,
  `last_seen`, `notes`.
- `hardware_fingerprints` – je `(machine_id, hardware_id)` eindeutig; Basis der
  Kopier-Erkennung; speichert zusätzlich `teamviewer_id`, `hostname`, Zeitstempel.
- `error_reports` – einzelne Fehlerberichte mit `status` (`offen`/`abgeschlossen`),
  `closed_at`/`closed_by`/`note`, `timestamp` (Fehler) + `received_at` (Eingang),
  `error_lines`, `error_count`, `log_filename`/`log_path`.

## Projektstruktur

```
logwatch-dashboard/
├─ app/
│  ├─ main.py            # FastAPI-App, Routen, Upload-API
│  ├─ settings.py        # Einstellungen aus .env
│  ├─ db.py              # Engine + Connection-Pool + Session
│  ├─ models.py          # SQLAlchemy-Datenmodell
│  ├─ auth.py            # bcrypt + Session-Helfer
│  ├─ templates/         # Jinja2-Templates (deutsch)
│  └─ static/style.css
├─ deploy/
│  ├─ logwatch-dashboard.service   # systemd-Unit
│  └─ logwatch-dash.conf           # nginx-vhost
├─ init_db.py            # idempotentes Schema-Init + Seed-Admin
├─ tests/test_dashboard.py
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
