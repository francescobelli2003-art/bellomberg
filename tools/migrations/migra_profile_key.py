# -*- coding: utf-8 -*-
"""migra_profile_key.py — aggiunge a valuation_theses la colonna profile_key
(audit/13 V1.7a): la chiave PULITA del profilo sub-settore (es. 'banks - regional'),
al posto delle 5 forme miste della colonna legacy subsector ('keyword->aerospace',
'default', ...). Le righe storiche restano come sono (dichiarato nel design).

Idempotente: se la colonna esiste gia', non fa nulla. Backup del DB prima di toccare.

USO (dalla radice del repo, backend CHIUSO):
  python scripts\\migra_profile_key.py
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
    if "profile_key" in cols:
        print("Colonna profile_key gia' presente: niente da migrare.")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(DATA_DIR, "backup_pre_profilekey_%s.db" % stamp)
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(bak)
    src.backup(dst)
    dst.close()
    print("Backup: %s" % bak)
    with src:
        src.execute("ALTER TABLE valuation_theses ADD COLUMN profile_key TEXT")
        print("Aggiunta colonna: profile_key TEXT")
        # backfill DICHIARATO delle sole forme gia' pulite: se subsector e' una chiave
        # esatta della tassonomia la si copia; 'keyword->X' si normalizza a X; il resto
        # ('default', forme legacy) resta NULL — n.d. onesto, non un'inferenza.
        n1 = src.execute(
            "UPDATE valuation_theses SET profile_key = subsector WHERE profile_key IS NULL "
            "AND subsector IN ('software - infrastructure','software - application',"
            "'semiconductors','solar','banks - regional','banks - diversified',"
            "'credit services','insurance','asset management','aerospace',"
            "'drug manufacturers','biotechnology','medical devices','healthcare plans',"
            "'oil & gas equipment & services','oil & gas e&p','steel','etf','default')").rowcount
        n2 = src.execute(
            "UPDATE valuation_theses SET profile_key = substr(subsector, 10) "
            "WHERE profile_key IS NULL AND subsector LIKE 'keyword->%'").rowcount
        print("Backfill: %d chiavi esatte copiate, %d normalizzate da 'keyword->'." % (n1, n2))
    src.close()
    print("FATTO. Le prossime tesi porteranno la profile_key pulita.")


if __name__ == "__main__":
    main()
