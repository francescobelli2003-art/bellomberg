# -*- coding: utf-8 -*-
"""Aggiunge l'archivio dei record documentati (method_record_sets, method_record_reviews) a un DB
ESISTENTE. I DB nuovi lo ricevono gia' da MemoryDB all'avvio. Decisione PM 1A, 13/09 (Claude Opus 5).

Sul modello di migra_valuation_metadata.py, con tre differenze dichiarate:
- il DB si apre SOLO con `memory_db.connect_sqlite` e il path NUDO (mai un URI `?mode=ro`, che il
  tripwire dei test non riconosce); la sola lettura e' `PRAGMA query_only=ON`;
- `connect_sqlite` imposta journal_mode=WAL: un DB non in WAL verrebbe CONVERTITO aprendolo
  (scrittura d'intestazione). Si legge l'intestazione dal file (byte 18-19) e si RIFIUTA prima di
  aprire: il dry-run non converte nulla. Il dry-run non fa scritture LOGICHE (istruzioni contate
  e total_changes, sotto), ma non promette il file byte per byte: alla chiusura SQLite puo'
  riportare nel file principale i frame WAL lasciati da altri processi (checkpoint; misurato il
  13/09 su una copia con -wal non riportato: stesso contenuto, file principale cresciuto e -wal
  sparito, con scritture contate 0);
- le scritture sulla sorgente sono CONTATE (trace delle istruzioni + total_changes) e l'impronta
  della sorgente e' RIMISURATA a connessione chiusa: il dry-run dice cosa ha misurato.

Uso (dalla radice del repo):
    python tools/migrations/migra_method_records.py            # dry-run sul DB del progetto
    python tools/migrations/migra_method_records.py --apply    # porta 8765 libera, backup, rilettura
"""
import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bellomberg.storage.method_records_store import TABELLE, crea_tabelle
from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
from tools.migrations import migra_valuation_metadata as _valuation

TABELLE_RICHIESTE = ("decisions", "valuation_theses")   # mai inizializzare un DB estraneo
Statements = _valuation.Statements


def conteggio(contatore):
    """Le istruzioni viste dal trace della connessione: osservate e di scrittura (misura, non letterale)."""
    return {"osservate": contatore.observed, "scritture": contatore.writes}


def backend_alive():
    return _valuation.backend_alive()


def intestazione_wal(percorso):
    """Legge i primi 100 byte: True se il DB e' in WAL (byte 18 e 19 uguali a 2)."""
    with open(percorso, "rb") as file:
        testa = file.read(100)
    if len(testa) < 100 or not testa.startswith(b"SQLite format 3\x00"):
        raise ValueError(f"{percorso}: non e' un database SQLite")
    return testa[18] == 2 and testa[19] == 2


def richiedi_wal(percorso):
    percorso = Path(percorso)
    if not percorso.is_file():
        raise FileNotFoundError(f"DB assente: {percorso} (nessun DB viene creato)")
    if not intestazione_wal(percorso):
        raise ValueError(
            f"{percorso}: DB non in modalita' WAL. connect_sqlite lo convertirebbe aprendolo (scrittura "
            "d'intestazione): rifiutato senza aprirlo. Il DB del progetto e' in WAL dall'hardening #32.")
    return percorso


def apri(percorso, *, sola_lettura):
    from bellomberg.storage import memory_db
    conn = memory_db.connect_sqlite(str(percorso))
    if sola_lettura:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
            conn.close()
            raise RuntimeError("PRAGMA query_only non attivo: nessuna lettura eseguita")
    return conn


def _schema_archivio(conn):
    nomi = {tipo + ":" + nome for tipo, nome, tabella in conn.execute("SELECT type,name,tbl_name FROM sqlite_master")
            if tabella in TABELLE}
    return {chiave: valore for chiave, valore in schema_fingerprint(conn).items() if chiave in nomi}


def _schema_atteso():
    with closing(sqlite3.connect(":memory:")) as conn:
        crea_tabelle(conn)
        return _schema_archivio(conn)


def _valida(conn, atteso, *, completo=False):
    if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("DB quick_check fallito")
    attuale = _schema_archivio(conn)
    if any(atteso.get(k) != v for k, v in attuale.items()) or (completo and attuale != atteso):
        raise ValueError("schema dell'archivio incompatibile: tabelle, indici o trigger diversi dal contratto")
    if "table:method_record_reviews" in attuale and conn.execute(
            "PRAGMA foreign_key_check(method_record_reviews)").fetchall():
        raise ValueError("archivio: revisioni che puntano a set inesistenti (foreign key check fallito)")


def _richiedi_tabelle(conn):
    presenti = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    mancanti = [t for t in TABELLE_RICHIESTE if t not in presenti]
    if mancanti:
        raise ValueError("DB estraneo: mancano le tabelle " + ", ".join(mancanti) + "; nessuna migrazione")


def _installa(conn):
    crea_tabelle(conn)


def _applica(percorso, prima, schema, atteso):
    contatore = Statements()
    with closing(apri(percorso, sola_lettura=False)) as conn:
        conn.set_trace_callback(contatore.trace)
        conn.execute("BEGIN IMMEDIATE")
        cambi = conn.total_changes
        try:
            if fingerprint(conn) != prima or schema_fingerprint(conn) != schema:
                raise ValueError("DB variato dopo preflight/backup: nessuna migrazione applicata")
            _valida(conn, atteso)
            _installa(conn)
            _valida(conn, atteso, completo=True)
            tutte_dopo = fingerprint(conn)
            schema_dopo = schema_fingerprint(conn)
            dopo = {k: tutte_dopo.get(k) for k in prima}
            if dopo != prima or any(schema_dopo.get(k) != v for k, v in schema.items()):
                raise ValueError("dati o schema preesistenti variati durante la migrazione: rollback")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        return {"righe_cambiate": conn.total_changes - cambi, "tabelle_preesistenti_invariate": dopo == prima,
                "scritture": conteggio(contatore)}


def _rileggi(percorso, prima, atteso):
    with closing(apri(percorso, sola_lettura=True)) as conn:
        _valida(conn, atteso, completo=True)
        tutte = fingerprint(conn)
        invariate = {k: tutte.get(k) for k in prima} == prima
    if not invariate:
        raise ValueError("rilettura: tabelle preesistenti diverse dal preflight dopo la migrazione")
    return {"schema_completo": True, "tabelle_preesistenti_invariate": invariate,
            "oggetti_archivio": sorted(atteso)}


def migra(db_path, *, apply=False):
    percorso = richiedi_wal(db_path)
    atteso = _schema_atteso()
    contatore = Statements()
    backup = None
    with tempfile.TemporaryDirectory(prefix="bellomberg_method_records_", ignore_cleanup_errors=True) as lavoro:
        prova = Path(lavoro) / "prova.db"
        with closing(apri(percorso, sola_lettura=True)) as conn:
            conn.set_trace_callback(contatore.trace)
            conn.execute("BEGIN")
            _valida(conn, atteso)
            _richiedi_tabelle(conn)
            prima = fingerprint(conn)
            schema = schema_fingerprint(conn)
            if apply and backend_alive():
                raise RuntimeError("porta 8765 occupata: chiudere il backend prima della migrazione")
            if apply:
                backup = percorso.with_name(
                    percorso.name + ".pre-method-records-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".bak")
                backup.touch(exist_ok=False)
            for destinazione in (prova, *([backup] if backup else [])):
                with closing(sqlite3.connect(destinazione)) as copia:
                    conn.backup(copia)
                    _valida(copia, atteso)
                    if fingerprint(copia) != prima or schema_fingerprint(copia) != schema:
                        raise ValueError("backup diverso dallo snapshot di preflight")
            conn.execute("ROLLBACK")
            cambi_sorgente = conn.total_changes
        # «sono stato io?» lo dicono query_only e il contatore; questa rimisura dice solo se
        # QUALCUNO ha scritto nel frattempo (uno scrittore concorrente non e' un'accusa al
        # dry-run: in apply lo ferma il confronto col preflight dentro _applica)
        with closing(apri(percorso, sola_lettura=True)) as conn:
            sorgente_invariata = fingerprint(conn) == prima and schema_fingerprint(conn) == schema
        esito = {
            "modo": "apply" if apply else "dry-run", "db": str(percorso), "intestazione_wal": True,
            "tabelle_mancanti": sorted(set(TABELLE) - set(prima)), "prima": prima,
            "backup": str(backup) if backup else None,
            "scritture_sorgente": {"osservate": contatore.observed, "scritture": contatore.writes,
                                   "total_changes": cambi_sorgente},
            "sorgente_invariata": sorgente_invariata,
            "prova": _applica(prova, prima, schema, atteso),
        }
    if not sorgente_invariata:
        esito["nota_sorgente"] = ("il DB e' cambiato durante il preflight ma non da questa connessione "
                                  "(query_only, scritture contate sopra): scrittore concorrente")
    if not apply:
        return esito
    if backend_alive():
        raise RuntimeError("porta 8765 occupata dopo la prova: nessuna migrazione applicata")
    esito["applicazione"] = _applica(percorso, prima, schema, atteso)
    esito["rilettura"] = _rileggi(percorso, prima, atteso)
    return esito


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None, help="default: il DB del progetto (memory_db.SQLITE_PATH)")
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--dry-run", action="store_true", help="default: prova su copia, nessuna scrittura")
    modo.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.db is None:
        from bellomberg.storage import memory_db
        args.db = memory_db.SQLITE_PATH
    print(json.dumps(migra(args.db, apply=args.apply), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
