"""
BELLOMBERG - Setup tabelle motore contabile TWR (audit 02 par.3, fix #30)

Lancialo da qualunque cartella (il DB e' ancorato al file):

    python scripts\\setup_twr_tables.py                       -> crea le tabelle (idempotente) + stato
    python scripts\\setup_twr_tables.py --list                -> elenca i movimenti di cassa registrati
    python scripts\\setup_twr_tables.py --add-deposit 2026-01-15 11000 "bonifico gennaio"
    python scripts\\setup_twr_tables.py --add-withdrawal 2026-03-02 5000 "prelievo spese"

Crea (solo se non esistono) in data/consigliere.db:
  cash_movements  - ledger flussi ESTERNI (depositi/prelievi). E' il dato che oggi manca:
                    senza, nessuna metrica puo' distinguere capitale nuovo da rendimento.
  nav_snapshots   - NAV ufficiale persistito 1+ volte/giorno (scritto da price_updater
                    via twr_engine.record_nav_snapshot dopo ogni giro prezzi riuscito).

Niente viene mai cancellato o modificato: solo CREATE TABLE IF NOT EXISTS e INSERT.
"""
import os
import sys
import re
import sqlite3
import argparse
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# DB path robusto: <root progetto>/data/consigliere.db, ovunque venga lanciato lo script
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", str(_ROOT))
from bellomberg.core.paths import SQLITE_PATH
DB_PATH = str(SQLITE_PATH)

DDL = """
CREATE TABLE IF NOT EXISTS cash_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('DEPOSIT','WITHDRAWAL')),
    amount_eur REAL NOT NULL CHECK(amount_eur > 0),
    note TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cash_movements_date ON cash_movements(date);

CREATE TABLE IF NOT EXISTS nav_snapshots (
    date TEXT PRIMARY KEY,
    nav_total_eur REAL NOT NULL,
    invested_eur REAL,
    cash_eur REAL,
    source TEXT,
    -- ATTENZIONE (review 31/08): questo DEFAULT e' UTC con lo SPAZIO, ma l'unico
    -- scrittore vero (twr_engine.record_nav_snapshot) passa SEMPRE created_at
    -- esplicito in ora LOCALE con la T (misurato: 66/66 righe) — ed e' la base
    -- dichiarata al frontend (F43-3). Un backfill via sqlite CLI che ometta
    -- created_at scriverebbe UTC spacciata per locale: passa sempre l'ora.
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def _connect():
    if not os.path.exists(DB_PATH):
        print(f"[ERRORE] DB non trovato: {DB_PATH}")
        print("         Verifica data/consigliere.db (percorso stampato sopra).")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # hardening #32 (audit 01 F-01): PRAGMA inline (script standalone, niente import memory_db)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn


def _valid_iso(d):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d or ""):
        return False
    try:
        datetime.strptime(d, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def setup_tables(conn):
    conn.executescript(DDL)
    conn.commit()


def print_status(conn):
    print("=" * 62)
    print("BELLOMBERG - MOTORE CONTABILE TWR - stato tabelle")
    print(f"DB: {DB_PATH}")
    print("=" * 62)
    for table in ("cash_movements", "nav_snapshots"):
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not row:
            print(f"  [MANCA]  {table}")
            continue
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        extra = ""
        if n > 0:
            if table == "nav_snapshots":
                rng = conn.execute(
                    "SELECT MIN(date), MAX(date) FROM nav_snapshots").fetchone()
                extra = f" (dal {rng[0]} al {rng[1]})"
            else:
                tot = conn.execute(
                    "SELECT COALESCE(SUM(CASE WHEN type='DEPOSIT' THEN amount_eur "
                    "ELSE -amount_eur END),0) FROM cash_movements").fetchone()[0]
                extra = f" (netto versato: EUR {tot:,.2f})"
        print(f"  [OK]     {table}: {n} righe{extra}")
    print("-" * 62)
    print("Prossimi passi:")
    print("  1. (opzionale) registra i versamenti storici con --add-deposit")
    print("  2. riavvia il backend: da ora ogni giro prezzi scrive lo snapshot NAV")
    print("  3. la pagina Performance (F2) usa GET /portfolio/analytics/twr")
    print("=" * 62)


def add_movement(conn, mtype, date_str, amount, note):
    if not _valid_iso(date_str):
        print(f"[ERRORE] data non valida: '{date_str}' (formato richiesto: YYYY-MM-DD)")
        sys.exit(1)
    try:
        # audit/11 §4: '11.000' in formato italiano e' UNDICIMILA, ma float() lo leggeva
        # 11.0 -> deposito 1000x piu' piccolo e TWR falsato per sempre. Normalizzazione
        # europea esplicita: virgola = decimale (punti = migliaia); soli punti in gruppi
        # di 3 cifre = migliaia.
        s = str(amount).strip().replace(" ", "")
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif re.match(r"^\d{1,3}(\.\d{3})+$", s):
            s = s.replace(".", "")
        amount = float(s)
    except ValueError:
        print(f"[ERRORE] importo non valido: '{amount}'")
        sys.exit(1)
    if amount <= 0:
        print("[ERRORE] importo deve essere > 0 (il segno lo decide il tipo DEPOSIT/WITHDRAWAL)")
        sys.exit(1)
    # anti-doppione esatto (stessa data, tipo e importo): chiedi conferma esplicita
    dup = conn.execute(
        "SELECT id FROM cash_movements WHERE date=? AND type=? AND amount_eur=?",
        (date_str, mtype, amount)).fetchone()
    if dup:
        print(f"[ATTENZIONE] esiste gia' un {mtype} di EUR {amount:,.2f} in data {date_str} (id {dup['id']}).")
        resp = input("Inserire comunque? [s/N] ").strip().lower()
        if resp != "s":
            print("Annullato.")
            sys.exit(0)
    cur = conn.execute(
        "INSERT INTO cash_movements (date, type, amount_eur, note) VALUES (?,?,?,?)",
        (date_str, mtype, amount, note or None))
    conn.commit()
    sign = "+" if mtype == "DEPOSIT" else "-"
    print(f"[OK] registrato {mtype} id {cur.lastrowid}: {sign}EUR {amount:,.2f} in data {date_str}"
          + (f" ({note})" if note else ""))


def list_movements(conn):
    rows = conn.execute(
        "SELECT id, date, type, amount_eur, note, created_at "
        "FROM cash_movements ORDER BY date ASC, id ASC").fetchall()
    if not rows:
        print("Nessun movimento di cassa registrato.")
        print("Registra i versamenti storici con: python scripts\\setup_twr_tables.py --add-deposit DATA IMPORTO [nota]")
        return
    print(f"{'ID':>4}  {'DATA':<12}{'TIPO':<12}{'IMPORTO EUR':>14}  NOTA")
    print("-" * 62)
    net = 0.0
    for r in rows:
        sign = 1.0 if r["type"] == "DEPOSIT" else -1.0
        net += sign * r["amount_eur"]
        print(f"{r['id']:>4}  {r['date']:<12}{r['type']:<12}{sign * r['amount_eur']:>14,.2f}  {r['note'] or ''}")
    print("-" * 62)
    print(f"{'NETTO VERSATO':>32}{net:>14,.2f}")


def main():
    parser = argparse.ArgumentParser(
        description="Setup tabelle TWR (cash_movements + nav_snapshots) - idempotente")
    parser.add_argument("--add-deposit", nargs="+", metavar="ARG",
                        help="registra un DEPOSITO: DATA(YYYY-MM-DD) IMPORTO_EUR [nota...]")
    parser.add_argument("--add-withdrawal", nargs="+", metavar="ARG",
                        help="registra un PRELIEVO: DATA(YYYY-MM-DD) IMPORTO_EUR [nota...]")
    parser.add_argument("--list", action="store_true", help="elenca i movimenti registrati")
    args = parser.parse_args()

    conn = _connect()
    try:
        setup_tables(conn)  # sempre, idempotente

        if args.add_deposit:
            if len(args.add_deposit) < 2:
                print("[ERRORE] uso: --add-deposit DATA IMPORTO [nota]")
                sys.exit(1)
            note = " ".join(args.add_deposit[2:]) if len(args.add_deposit) > 2 else None
            add_movement(conn, "DEPOSIT", args.add_deposit[0], args.add_deposit[1], note)
        elif args.add_withdrawal:
            if len(args.add_withdrawal) < 2:
                print("[ERRORE] uso: --add-withdrawal DATA IMPORTO [nota]")
                sys.exit(1)
            note = " ".join(args.add_withdrawal[2:]) if len(args.add_withdrawal) > 2 else None
            add_movement(conn, "WITHDRAWAL", args.add_withdrawal[0], args.add_withdrawal[1], note)
        elif args.list:
            list_movements(conn)
        else:
            print_status(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
