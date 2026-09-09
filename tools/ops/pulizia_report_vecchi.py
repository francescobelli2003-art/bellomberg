# -*- coding: utf-8 -*-
"""pulizia_report_vecchi.py — retention dei VAL/DCF timestampati residui in report/
(decisione PM 16/07, audit/13 §6 n.8 opzione A): i file VECCHI col timestamp nel nome
(regime pre-"modello unico") si SPOSTANO in attic/val_superseded/ — quarantena
reversibile, F17 resta pulita, nulla viene cancellato.

NON tocca: i canonici senza timestamp (VAL_TICKER.xlsx / VAL_TICKER_FLAGGED.xlsx),
che sono i modelli vivi. Dry-run di default.

USO (dalla radice del repo, backend CHIUSO):
  python scripts\\pulizia_report_vecchi.py            <- anteprima
  python scripts\\pulizia_report_vecchi.py --apply    <- sposta
"""
import glob
import os
import re
import shutil
import socket
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", str(ROOT))
from bellomberg.core.paths import REPORT_DIR
REP = str(REPORT_DIR)
DEST = os.path.join(ROOT, "archive", "private", "attic", "val_superseded")
APPLY = "--apply" in sys.argv
# timestampato = _AAAAMMGG o _AAAAMMGG_HHMM nel nome (il canonico non ce l'ha)
PAT = re.compile(r"^(VAL|DCF)_.+_\d{8}(_\d{4})?(_FLAGGED)?\.xlsx$", re.I)


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
    if APPLY and backend_alive():
        print("ABORT: backend acceso sulla 8765 — chiudi app/backend e rilancia.")
        return
    hits = [f for f in glob.glob(os.path.join(REP, "*.xlsx"))
            if PAT.match(os.path.basename(f))]
    if not hits:
        print("Nessun VAL/DCF timestampato in report/: niente da fare.")
        return
    print("Da spostare in attic/val_superseded/ (%d file):" % len(hits))
    for f in sorted(hits):
        print("  ", os.path.basename(f))
    if not APPLY:
        print("\nANTEPRIMA: nessuno spostamento. Rilancia con --apply (backend chiuso).")
        return
    os.makedirs(DEST, exist_ok=True)
    for f in sorted(hits):
        d = os.path.join(DEST, os.path.basename(f))
        # review B10: mai cancellare cio' che e' gia' in quarantena — su collisione
        # si suffissa (reversibilita' PIENA)
        k = 1
        while os.path.exists(d):
            base, ext = os.path.splitext(os.path.basename(f))
            d = os.path.join(DEST, "%s_dup%d%s" % (base, k, ext))
            k += 1
        shutil.move(f, d)
        print("Spostato:", os.path.basename(d))
    print("FATTO: %d file in quarantena reversibile. Riavvia backend/app e verifica F17." % len(hits))


if __name__ == "__main__":
    main()
