#!/usr/bin/env python3
"""Release-Automatisierung für das LogWatch Dashboard.

Pflegt VERSION und CHANGELOG automatisch und setzt (optional) den Release-Tag.

Arbeitsweise:
  - Während der Entwicklung werden Änderungen einfach unter der Überschrift
    "## [Unveröffentlicht]" im CHANGELOG.md gesammelt.
  - Beim Release verschiebt dieses Skript diesen Block in einen neuen
    Versionsabschnitt "## [X.Y.Z] – <Datum>", setzt "Unveröffentlicht" zurück,
    aktualisiert die Vergleichs-Links am Dateiende und schreibt VERSION.
  - Mit --push wird committet, der Tag vX.Y.Z gesetzt und gepusht.

Beispiele:
  python scripts/release.py 0.2.0            # Doku aktualisieren (ohne Push)
  python scripts/release.py 0.2.0 --push     # inkl. Commit, Tag und Push
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import subprocess
import sys

REPO = "Obi811/Logwatch-Dashboard"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
VERSION_FILE = os.path.join(ROOT, "VERSION")

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(1)


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def previous_version() -> str:
    return read(VERSION_FILE).strip()


def update_changelog(version: str, date: str) -> None:
    text = read(CHANGELOG)

    m = re.search(r"## \[Unver\u00f6ffentlicht\]\s*\n(.*?)(?=\n## \[)", text, re.S)
    if not m:
        _fail("Abschnitt '## [Unveröffentlicht]' nicht im CHANGELOG gefunden.")
    body = m.group(1).strip()

    if not body or body == "- \u2014" or body == "-":
        _fail(
            "Der Abschnitt '## [Unveröffentlicht]' ist leer. Trage zuerst die "
            "Änderungen dort ein, bevor du einen Release erstellst."
        )

    new_unreleased = "## [Unver\u00f6ffentlicht]\n- \u2014\n\n"
    new_section = f"## [{version}] \u2013 {date}\n{body}\n\n"
    text = re.sub(
        r"## \[Unver\u00f6ffentlicht\]\s*\n.*?(?=\n## \[)",
        new_unreleased + new_section.rstrip("\n") + "\n",
        text,
        count=1,
        flags=re.S,
    )

    prev = previous_version()
    text = re.sub(
        r"\[Unver\u00f6ffentlicht\]: .*",
        f"[Unver\u00f6ffentlicht]: https://github.com/{REPO}/compare/v{version}...HEAD",
        text,
        count=1,
    )
    new_link = f"[{version}]: https://github.com/{REPO}/compare/v{prev}...v{version}"
    text = re.sub(
        r"(\[Unver\u00f6ffentlicht\]: .*\n)",
        r"\1" + new_link + "\n",
        text,
        count=1,
    )
    write(CHANGELOG, text)
    print(f"CHANGELOG.md aktualisiert: Abschnitt [{version}] – {date} angelegt.")


def git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Release-Automatisierung für das LogWatch Dashboard")
    ap.add_argument("version", help="Neue Version, z. B. 0.2.0")
    ap.add_argument("--push", action="store_true",
                    help="Committen, Tag setzen und pushen")
    ap.add_argument("--date", default=_dt.date.today().isoformat(),
                    help="Datum für den CHANGELOG-Eintrag (Default: heute)")
    args = ap.parse_args()

    version = args.version.lstrip("v")
    if not SEMVER.match(version):
        _fail(f"Ungültige Version '{args.version}'. Erwartet X.Y.Z, z. B. 0.2.0")

    update_changelog(version, args.date)
    write(VERSION_FILE, version + "\n")
    print(f"VERSION -> {version}")

    if args.push:
        git("add", "VERSION", "CHANGELOG.md")
        git("commit", "-m", f"Release v{version}")
        git("tag", "-a", f"v{version}", "-m", f"LogWatch Dashboard v{version}")
        git("push", "origin", "HEAD")
        git("push", "origin", f"v{version}")
        print(f"\nGetaggt und gepusht: v{version}")
    else:
        print("\nHinweis: Änderungen sind nur lokal. Mit --push wird getaggt und "
              "gepusht. Denk daran, auch TODO.md und README.md zu pflegen.")


if __name__ == "__main__":
    main()
