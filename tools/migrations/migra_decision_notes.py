# -*- coding: utf-8 -*-
"""migra_decision_notes.py — crea la tabella decision_notes (F10 v3, mandato PM
16/07 notte, mockup A approvato): thread di note PM<->AI sulle decisioni RESEARCH.
Il PM scrive dalla pagina F10; la run legge le note nel blocco research e risponde
col tool add_research_note.

Idempotente: se la tabella esiste gia', non fa nulla. Backup del DB prima di toccare.

USO (dalla radice del repo, backend CHIUSO):
  python scripts\\migra_decision_notes.py
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

DDL = """
CREATE TABLE IF NOT EXISTS decision_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL,
    autore TEXT NOT NULL CHECK(autore IN ('PM','AI')),
    testo TEXT NOT NULL,
    timestamp TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(decision_id) REFERENCES decisions(id)
);
CREATE INDEX IF NOT EXISTS idx_decision_notes ON decision_notes(decision_id, timestamp);
"""


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
    exists = ro.execute("SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='decision_notes'").fetchone()
    ro.close()
    if exists:
        print("Tabella decision_notes gia' presente: niente da migrare.")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(DATA_DIR, "backup_pre_notes_%s.db" % stamp)
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(bak)
    src.backup(dst)
    dst.close()
    print("Backup: %s" % bak)
    with src:
        src.executescript(DDL)
    src.close()
    print("FATTO: tabella decision_notes creata. Riavvia backend+app: in F10 le card "
          "research hanno il thread note; la run rispondera' con add_research_note.")


if __name__ == "__main__":
    main()
