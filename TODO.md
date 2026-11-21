# TODO / Aufgabenliste – LogWatch Dashboard

Hier können offene Aufgaben, Ideen und Fehler eingetragen werden.
Zum Abhaken einfach `[ ]` in `[x]` ändern. Neue Punkte unten anhängen.

---

## 🔧 Offen

- [ ] Laufzeiten/Uptime-Auswertung je Maschine (Heartbeat auswerten)
- [ ] Aufbewahrungs-/Löschkonzept für alte Logdateien (Speicherplatz)
- [ ] Self-Update/Deploy-Knopf (nur sinnvoll bei eigenem Server mit SSH/systemd)
- [ ] 
- [ ] 

## 💡 Ideen / Verbesserungen

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
