# -*- coding: utf-8 -*-
"""db_backup_direct.py - Backup DIRETTO dei DB (fallback quando il backend e' giu').

DB igiene §9-bis n.6 (21/07): sostituisce il one-liner del run_db_backup.bat che
zippava il file WAL VIVO a copia nuda (snapshot potenzialmente strappato, -wal
perso dal glob) — proprio nello scenario peggiore, backend giu'.

Fa la stessa cosa dell'endpoint POST /db/backup:
  1. snapshot consistente di ogni data/*.db|*.sqlite con la sqlite3 backup API;
  2. PRAGMA quick_check sullo SNAPSHOT: KO = dichiarato, file NON zippato;
  3. zip di snapshot + data/*.json + data/*.csv in data/backups/ (stesso naming).
Retention: resta nel .bat (ultimi 30), qui non si cancella nulla.

Exit code: 0 = tutti i DB ok; 1 = almeno un DB KO/saltato (dichiarato a stdout).
Sicuro con backend su (la backup API convive col WAL), ma il suo mestiere e' il
fallback schedulato quando il backend non risponde.
"""
import glob
import os
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", str(ROOT))
from bellomberg.core.paths import DATA_DIR


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    zip_path = os.path.join(backup_dir, f"bellomberg_backup_{ts}.zip")
    files_added = []
    db_checks = {}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for pattern in (os.path.join(DATA_DIR, "*.db"), os.path.join(DATA_DIR, "*.sqlite")):
            for f in glob.glob(pattern):
                tmp = None
                base = os.path.basename(f)
                try:
                    fd, tmp = tempfile.mkstemp(suffix=".db")
                    os.close(fd)
                    src = sqlite3.connect(f, timeout=15)
                    dst = sqlite3.connect(tmp)
                    try:
                        src.backup(dst)
                        qc = dst.execute("PRAGMA quick_check").fetchone()
                        qc_ok = bool(qc) and str(qc[0]).lower() == "ok"
                    finally:
                        dst.close()
                        src.close()
                    db_checks[base] = "ok" if qc_ok else (
                        "KO: " + (str(qc[0])[:200] if qc else "quick_check senza esito"))
                    if not qc_ok:
                        print(f"[BACKUP_DIRECT] {base}: quick_check FALLITO sullo snapshot "
                              "- NON incluso nel backup (dichiarato)")
                        continue
                    zf.write(tmp, base)
                    files_added.append(base)
                except Exception as e:
                    db_checks[base] = f"KO: {type(e).__name__}: {e}"
                    print(f"[BACKUP_DIRECT] skip {base}: {e}")
                finally:
                    if tmp and os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
        for pattern in (os.path.join(DATA_DIR, "*.json"), os.path.join(DATA_DIR, "*.csv")):
            for f in glob.glob(pattern):
                try:
                    zf.write(f, os.path.basename(f))
                    files_added.append(os.path.basename(f))
                except Exception as e:
                    print(f"[BACKUP_DIRECT] skip {os.path.basename(f)}: {e}")
    size_kb = os.path.getsize(zip_path) // 1024
    all_ok = bool(db_checks) and all(v == "ok" for v in db_checks.values())
    print(f"[BACKUP_DIRECT] {zip_path} - {size_kb} KB, {len(files_added)} file")
    for k, v in db_checks.items():
        print(f"[BACKUP_DIRECT] quick_check {k}: {v}")
    if not db_checks:
        print(f"[BACKUP_DIRECT] ATTENZIONE: nessun DB trovato in {DATA_DIR} (dichiarato)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
