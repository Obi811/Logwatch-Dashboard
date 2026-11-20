# Changelog

Alle nennenswerten Änderungen am LogWatch-Dashboard werden in dieser Datei
dokumentiert.

Das Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
und das Projekt folgt der [Semantischen Versionierung](https://semver.org/lang/de/).

## [Unveröffentlicht]
### Hinzugefügt
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
