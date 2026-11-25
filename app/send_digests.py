"""Versendet die Kunden-Sammelberichte (Digests) nach Fälligkeit.

Als Modul aufrufbar::

    python -m app.send_digests [--dry-run] [--customer-id ID]

Prüft alle Kunden mit ``digest_interval`` in (``weekly``, ``monthly``) auf
Fälligkeit (siehe :func:`app.mailer.is_digest_due`) und versendet bei Bedarf den
Sammelbericht. Der Versand ist idempotent: über ``last_digest_sent_at`` wird ein
Doppelversand am selben Tag vermieden. Vorgesehen für einen externen Cronjob
bzw. einen systemd-Timer (KEIN interner Scheduler).
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .mailer import is_digest_due, send_customer_digest
from .models import DIGEST_MONTHLY, DIGEST_WEEKLY, Customer

logger = logging.getLogger("logwatch.send_digests")


def run(db: Session, *, dry_run: bool = False,
        customer_id: Optional[int] = None,
        now: Optional[datetime] = None) -> list[dict]:
    """Führt den Fälligkeits-Check + Versand aus und liefert Ergebnis-Dicts.

    Für Tests direkt aufrufbar (eigene Session/Zeit injizierbar).
    """
    stmt = select(Customer).where(
        Customer.digest_interval.in_([DIGEST_WEEKLY, DIGEST_MONTHLY])
    ).order_by(Customer.id)
    if customer_id is not None:
        stmt = stmt.where(Customer.id == customer_id)

    results: list[dict] = []
    for customer in db.execute(stmt).scalars().all():
        if not is_digest_due(customer, now):
            results.append({"customer_id": customer.id, "name": customer.name,
                            "sent": False, "skipped": "nicht fällig"})
            continue
        try:
            res = send_customer_digest(db, customer, dry_run=dry_run, now=now)
        except Exception as exc:  # noqa: BLE001 – ein Kunde darf den Lauf nicht abbrechen
            logger.exception("Digest-Versand für Kunde %s fehlgeschlagen", customer.id)
            res = {"customer_id": customer.id, "name": customer.name,
                   "sent": False, "error": f"{type(exc).__name__}: {exc}"}
        results.append(res)
    return results


def _format_result(res: dict) -> str:
    name = res.get("name", "?")
    cid = res.get("customer_id", "?")
    if res.get("skipped"):
        return f"  [übersprungen] #{cid} {name}: {res['skipped']}"
    if res.get("dry_run"):
        return (f"  [dry-run]    #{cid} {name}: würde {res.get('reports', 0)} "
                f"Meldung(en) / {res.get('machines', 0)} Maschine(n) senden")
    if res.get("sent"):
        return (f"  [gesendet]   #{cid} {name}: {res.get('reports', 0)} "
                f"Meldung(en) / {res.get('machines', 0)} Maschine(n)")
    return f"  [FEHLER]     #{cid} {name}: {res.get('error', 'unbekannt')}"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Versendet fällige Kunden-Sammelberichte (Digests).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur anzeigen, nichts versenden.")
    parser.add_argument("--customer-id", type=int, default=None,
                        help="Nur diesen Kunden prüfen/versenden.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db = SessionLocal()
    try:
        results = run(db, dry_run=args.dry_run, customer_id=args.customer_id)
    finally:
        db.close()

    sent = sum(1 for r in results if r.get("sent"))
    errors = sum(1 for r in results if r.get("error"))
    print(f"Digest-Lauf{' (dry-run)' if args.dry_run else ''}: "
          f"{len(results)} Kunde(n) geprüft, {sent} gesendet, {errors} Fehler.")
    for res in results:
        print(_format_result(res))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
