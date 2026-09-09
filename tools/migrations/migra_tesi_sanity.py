# -*- coding: utf-8 -*-
"""migra_tesi_sanity.py — aggiunge a valuation_theses le colonne sanity_severity e
sanity_headline (audit/12 V0.3-V0.4): il giudizio del motore (OK/WARN + motivo)
viaggia con la tesi e F17 puo' etichettare i numeri da trattare con cautela.

Idempotente: se le colonne esistono gia', non fa nulla. Backup del DB prima di toccare.

USO (dalla radice del repo, backend CHIUSO):
  python scripts\\migra_tesi_sanity.py
"""
import os
import socket
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", str(ROOT))
from bellomberg.core.paths import DATA_DIR, SQLITE_PATH
DB = str(SQLITE_PATH)
NUOVE = [("sanity_severity", "TEXT"), ("sanity_headline", "TEXT")]


def backend_alive():
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 8765))
        s.close()
        return True
    except Exception:
        return False


def main():
    if not os.path.exists(DB):
        print("ABORT: DB non trovato: " + DB)
        return
    if backend_alive():
        print("ABORT: backend acceso sulla 8765 — chiudi app/backend e rilancia.")
        return

    ro = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
    cols = {r[1] for r in ro.execute("PRAGMA table_info(valuation_theses)")}
    ro.close()
    da_fare = [(n, t) for n, t in NUOVE if n not in cols]
    if not da_fare:
        print("Colonne sanity gia' presenti: niente da migrare.")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(DATA_DIR, "backup_pre_sanity_%s.db" % stamp)
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(bak)
    src.backup(dst)
    dst.close()
    print("Backup: %s" % bak)
    with src:
        for nome, tipo in da_fare:
            src.execute("ALTER TABLE valuation_theses ADD COLUMN %s %s" % (nome, tipo))
            print("Aggiunta colonna: %s %s" % (nome, tipo))
        # Review V0: BACKFILL dichiarato — le righe storiche oltre la soglia BLOCK del
        # motore (|fv/prezzo - 1| > 0.5, dcf_engine.sanity_check) vengono etichettate
        # BLOCK, cosi' la cintura di F17 le nasconde SUBITO anche prima della pulizia.
        n = src.execute(
            "UPDATE valuation_theses SET sanity_severity='BLOCK', "
            "sanity_headline='backfill migrazione: divergenza FV/prezzo >50% (soglia BLOCK del motore)' "
            "WHERE sanity_severity IS NULL AND price_at_thesis > 0 AND fair_value IS NOT NULL "
            "AND abs(fair_value / price_at_thesis - 1.0) > 0.5").rowcount
        print("Backfill: %d tesi storiche etichettate BLOCK (F17 le nasconde da subito)." % n)
    src.close()
    print("FATTO. Le prossime tesi porteranno il giudizio sanity (OK/WARN + motivo).")


if __name__ == "__main__":
    main()
