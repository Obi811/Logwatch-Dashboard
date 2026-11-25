# TODO / Aufgabenliste – LogWatch Dashboard

Hier können offene Aufgaben, Ideen und Fehler eingetragen werden.
Zum Abhaken einfach `[ ]` in `[x]` ändern. Neue Punkte unten anhängen.

---

## 🔧 Offen

- [ ] Laufzeiten/Uptime-Auswertung je Maschine (Heartbeat auswerten)
- [ ] Aufbewahrungs-/Löschkonzept für alte Logdateien (Speicherplatz)
- [ ] Self-Update/Deploy-Knopf (nur sinnvoll bei eigenem Server mit SSH/systemd)
- [ ] E-Mail-Versand im Windows-Client entfernen (Zentralisierung im Dashboard), sobald der Dashboard-SMTP-Versand live verifiziert ist
- [ ] Mehrere E-Mail-Empfänger pro Kunde (aktuell genau eine Adresse je Kunde)
- [ ] 
- [ ] 

## 💡 Ideen / Verbesserungen

- [ ] Push-Benachrichtigungen an die mobile App (z.B. bei neuer Kopier-Warnung/neuem Fehler) – bewusst noch nicht Teil der REST-API
- [ ] App-API: Refresh-Token / Token-Widerruf (aktuell nur fester 30-Tage-Token)
- [ ] Benachrichtigung/Erwähnungen bei neuem Kommentar (bewusst noch nicht umgesetzt)
- [ ] Rollenverwaltung über die REST-API (aktuell nur im Web-UI; API liefert nur die eigene Rolle über `/api/v1/me`)
- [ ] E-Mail-/Push-Benachrichtigung bei neuer Kopier-Warnung
- [ ] Export der Fehler-Historie (CSV/Excel)
- [ ] Mehr Diagramme (Fehler je Maschine/Firma, Trend über Wochen)
- [ ] 
- [ ] 

## 🐞 Fehler / Bugs

- [ ] 
- [ ] 
- [ ] 

---

## ✅ Erledigt

- [x] Interner Login (bcrypt, Session-Cookie)
- [x] Upload-API (Token-geschützt) + Healthcheck
- [x] Maschinenübersicht, Fehler-Historie, Detailansicht, Logdatei-Download
- [x] Abschließen/Wieder öffnen von Fehlerberichten
- [x] Kopier-Erkennung (eine Maschinen-ID, mehrere Hardware-IDs)
- [x] Statistik mit Chart.js
- [x] Deployment (systemd + nginx), DB-Init, live unter logwatch-dash.abacusai.cloud
- [x] CI: Integrationstests + Doku-Check bei Push/PR (GitHub Actions)
- [x] Automatisierte Abhängigkeits-Updates per Dependabot (Python + GitHub-Actions, wöchentliche PRs)
- [x] Automatische Sicherheits-Updates bei bekannten Schwachstellen (Dependabot Security)
- [x] Sicherheits-Audit der Abhängigkeiten per `pip-audit` (CI-Workflow `Security-Audit`)
- [x] Automatisierte Doku-Pflege: Release-Skript `scripts/release.py`
- [x] Benutzerverwaltung im UI (Konten anlegen, Passwort ändern, löschen) unter `/users`
- [x] Versions-/Update-Hinweis (GitHub-Release-Prüfung, Variante 1)
- [x] App-Unterstützung / REST-API (`/api/v1`): token-authentifizierte JSON-API (Login, me, overview, machines, warnings+acknowledge, reports), CORS, Swagger-Doku unter `/docs`
- [x] Kommentarfunktion für Meldungen (Web-UI + REST-API): anlegen/löschen, nur Autor oder Admin darf löschen, leere Kommentare abgelehnt
- [x] Rollensystem (Admin/Benutzer): `role`-Spalte, `require_admin`-Absicherung der Benutzerverwaltung (Web + API), Rollenwahl/-umstellung im UI, Schutz des letzten Admins, `role` in `/api/v1/me`
- [x] Kundenverwaltung (`/customers`, nur Admin): Kunden anlegen/bearbeiten/löschen, Maschinen-Zuordnung, E-Mail-Einstellungen (Intervall, Sofort-Mail), Test-Mail – Tabellen `customers` + `machines.customer_id`
- [x] Rolle `kunde` + Kunden-Login (`users.customer_id`): serverseitiges Scoping aller Lese-Routen, keine Schreibaktionen/Verwaltung, 404 bei fremden Maschinen/Meldungen
- [x] SMTP-Konfiguration im UI (`/settings/smtp`, nur Admin): Passwort Fernet-verschlüsselt (`password_enc`), nie im Klartext ausgegeben, Fallback auf `SMTP_*`-Env, Test-Mail
- [x] E-Mail-Versand (HTML, deutsch): Sofort-Fehler-Mail beim Upload + Sammelbericht (Digest) je Kunde mit Kennzahlen und Aufschlüsselung je Maschine (ohne Kommentare)
- [x] Automatischer Digest-Versand: Kommando `python -m app.send_digests` (idempotent, `--dry-run`/`--customer-id`) + systemd-Timer/-Service in `deploy/` sowie dokumentierte Cron-Zeile
- [x] Layout der Admin-Seiten (`/customers`, `/users`, `/settings/smtp`): zweispaltiges, responsives CSS-Grid – nutzt die Seitenbreite (schmales Panel links, breite Tabellen/Formulare rechts), stapelt auf dem Smartphone sauber untereinander
- [x] Dashboard-weites Layout aller Seiten: wiederverwendbare Muster `.table-wrap` (breite Tabellen scrollen horizontal statt zu brechen – Übersicht, Maschinen, Fehlerberichte, Warnungen, Statistik) und `.detail-grid` (2-spaltiges Detail-Layout für Maschinen-/Bericht-Detail, stapelt ≤ 880 px) – nutzt die volle Breite auf Desktop, sauber responsiv auf dem Smartphone
- [x] Navigationsleiste (Topbar) für Smartphones responsiv: bricht ab ≤ 820 px um (Marke + Benutzer oben, Navigationslinks mehrzeilig darunter) statt die Links über den Bildschirmrand hinaus abzuschneiden; Desktop bleibt einzeilig
