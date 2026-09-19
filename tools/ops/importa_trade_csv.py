"""BELLOMBERG — importa i trade da un CSV nel DB (pubblicazione 02/09, B6).

CSV con intestazione: data,ticker,azione,quantita,prezzo,valuta,note
  data     ISO (2026-01-15, anche con ora) o DD/MM/YYYY; senza ora = 12:00:00 (la stessa
           convenzione dello storico del PM: un estratto porta solo la data). Mai nel futuro.
  azione   BUY | SELL | TRIM | ADD | DIVIDEND (le stesse di memory_db.log_trade)
  numeri   punto o virgola decimale («10,5» = 10.5); «1.234,5» e' AMBIGUO e si rifiuta
           (lezione F7: la virgola del PM); quantita > 0, prezzo > 0, mai nan/inf
  valuta   EUR | USD | GBX | ... (vuota = EUR). Una valuta diversa da quella della posizione
           gia' aperta si rifiuta PRIMA di scrivere (la guardia valute del DB, anticipata).

Cosa controlla PRIMA di scrivere (review 02/09, allocano soldi veri):
  * il LIBRO: posizioni vive dal DB + righe del CSV in ORDINE DI DATA — una SELL/TRIM senza
    posizione o oltre il posseduto, un ADD/DIVIDEND senza posizione, si rifiutano (log_trade
    da solo scriverebbe una riga orfana e il replay del NAV andrebbe sotto zero);
  * le righe si scrivono in ordine di DATA, non di file: log_trade ricalcola il carico
    medio in ordine di chiamata (stessi 3 trade disordinati = carico e realized diversi).

Uso:  python tools/ops/importa_trade_csv.py file.csv            (DRY-RUN: mostra, non scrive)
      python tools/ops/importa_trade_csv.py file.csv --apply    (scrive, dopo un backup del DB)
      --db <path>  usa un altro DB (default memory_db.SQLITE_PATH); se non esiste: STOP
                   (aprire un libro nuovo e' compito del backend, non dell'importatore)

Il dry-run e' MISURATO: conta le righe di trade_history prima e dopo e le stampa. Apre il
DB con MemoryDB (schema + migrazioni pendenti, come il backend): non e' una lettura pura.
Con --apply: backup con l'API SQLite (il -wal entra, quick_check sulla copia) e rifiuto se
il backend e' attivo sulla porta 8765 (chiudi l'app; e' un'istantanea, i task schedulati
scrivono per conto loro e il DB li serializza con busy_timeout). Nessun rollback: le righe
scritte prima di un rifiuto del DB restano, dichiarate nell'esito; la via di ritorno e' il
backup pre-import. Cosa NON fa, dichiarato: la CASSA (SQLite cash_state) non viene aggiornata
(POST /trade la aggiorna, questo script no: allineala dalla pagina Inserimento operazioni, F16); le vendite
in valuta ricevono realized_eur = n.d. (log_trade userebbe il cambio di OGGI su un trade
retrodatato): tools/migrations/backfill_realized.py --apply mette l'FX storico.
Sostituisce import_user_trades.py (in attic/oneshot: aveva i lotti del PM cablati).
"""
import argparse
import csv
import math
import os
import socket
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

AZIONI = ("BUY", "SELL", "TRIM", "ADD", "DIVIDEND")
COLONNE = ("data", "ticker", "azione", "quantita", "prezzo", "valuta", "note")


class NumeroAmbiguo(ValueError):
    pass


def _numero(s):
    """Punto o virgola decimale; «1.234,5» (entrambi) e' ambiguo → NumeroAmbiguo;
    non numerico → ValueError; nan/inf → ValueError («non finito»)."""
    t = (s if s is not None else "").strip()
    if "," in t and "." in t:
        raise NumeroAmbiguo(f"'{t}' ambiguo (punto E virgola): usa solo il separatore decimale")
    if "," in t:
        if t.count(",") > 1:
            raise NumeroAmbiguo(f"'{t}' ambiguo (piu' virgole)")
        t = t.replace(",", ".")
    v = float(t)   # ValueError sul non numerico
    if not math.isfinite(v):
        raise ValueError(f"'{s}' non finito")
    return v


def _data_iso(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            d = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            d = d.replace(hour=12, minute=0, second=0)
        return d.isoformat(timespec="seconds")
    return None


def leggi_csv(path):
    """Righe come dict (colonne del CSV + `_riga` = numero di riga nel file, 1-based)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        lettore = csv.DictReader(f)
        mancanti = [c for c in COLONNE if c not in (lettore.fieldnames or [])]
        if mancanti:
            raise ValueError(f"intestazione CSV senza le colonne {mancanti}; attese {list(COLONNE)}")
        return [dict(r, _riga=i) for i, r in enumerate(lettore, start=2)]


def _chiave_cronologica(r):
    return (_data_iso(r.get("data")) or "", r["_riga"])


def valida(righe, db=None):
    """Lista di errori (vuota = tutto valido). Prima i controlli di forma su TUTTE le
    righe; poi, se `db` e' dato e la forma e' a posto, il replay del LIBRO in ordine
    di data (posizioni vive dal DB + righe del CSV). Si controlla tutto PRIMA di scrivere."""
    errori = []
    oggi = datetime.now().isoformat(timespec="seconds")
    for r in righe:
        n = r["_riga"]
        if (r.get("azione") or "").strip().upper() not in AZIONI:
            errori.append(f"riga {n}: azione '{r.get('azione')}' non ammessa ({'/'.join(AZIONI)})")
        if not (r.get("ticker") or "").strip():
            errori.append(f"riga {n}: ticker vuoto")
        try:
            if _numero(r.get("quantita")) <= 0:
                errori.append(f"riga {n}: quantita deve essere > 0")
        except NumeroAmbiguo as e:
            errori.append(f"riga {n}: quantita {e}")
        except (TypeError, ValueError):
            errori.append(f"riga {n}: quantita non numerica '{r.get('quantita')}'")
        try:
            if _numero(r.get("prezzo")) <= 0:
                errori.append(f"riga {n}: prezzo deve essere > 0")
        except NumeroAmbiguo as e:
            errori.append(f"riga {n}: prezzo {e}")
        except (TypeError, ValueError):
            errori.append(f"riga {n}: prezzo non numerico '{r.get('prezzo')}'")
        iso = _data_iso(r.get("data"))
        if iso is None:
            errori.append(f"riga {n}: data '{r.get('data')}' non e' ISO ne' DD/MM/YYYY")
        elif iso > oggi:
            errori.append(f"riga {n}: data {iso[:10]} nel futuro")
    if db is None or errori:
        return errori
    # --- replay del libro (review 02/09, F1): mai una SELL orfana o oltre il posseduto
    with db._conn() as c:
        libro = {row["ticker"]: (float(row["quantita"] or 0), (row["valuta"] or "EUR").upper())
                 for row in c.execute("SELECT ticker, quantita, valuta FROM positions WHERE is_active=1")}
    for r in sorted(righe, key=_chiave_cronologica):
        n = r["_riga"]
        t = r["ticker"].strip().upper()
        az = r["azione"].strip().upper()
        q = _numero(r["quantita"])
        v = (r.get("valuta") or "EUR").strip().upper() or "EUR"
        qty, v_pos = libro.get(t, (0.0, None))
        if az != "DIVIDEND" and v_pos and v != v_pos:
            errori.append(f"riga {n}: valuta {v} diversa dalla posizione {t} ({v_pos}): "
                          "nessuna conversione automatica")
        if az in ("SELL", "TRIM", "ADD", "DIVIDEND") and qty <= 0:
            errori.append(f"riga {n}: {az} su {t} senza posizione (prima serve un BUY)")
        if az in ("SELL", "TRIM") and q > qty + 1e-9:
            errori.append(f"riga {n}: {az} {q:g} > posseduto {qty:g} {t}")
        if az in ("BUY", "ADD"):
            libro[t] = (qty + q, v_pos or v)
        elif az in ("SELL", "TRIM"):
            libro[t] = (qty - q, v_pos)
    return errori


def _conta(db):
    with db._conn() as c:
        return c.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0]


NOTA_CASSA = ("cassa (SQLite cash_state) NON aggiornata dall'import: allineala dalla "
              "pagina Inserimento operazioni (F16)")


def importa(db, righe, apply=False):
    """Esito misurato: {lette, scritte, prima, dopo, errori, vendite_in_valuta, nota}.
    Senza --apply non scrive nulla; con --apply scrive in ordine di DATA via
    MemoryDB.log_trade e si ferma alla prima riga rifiutata dal DB, dichiarandola."""
    prima = _conta(db)
    errori = valida(righe, db)
    esito = {"lette": len(righe), "scritte": 0, "prima": prima, "dopo": prima,
             "errori": errori, "vendite_in_valuta": 0, "nota": NOTA_CASSA}
    if errori or not apply:
        return esito
    in_valuta = []
    for r in sorted(righe, key=_chiave_cronologica):
        az = r["azione"].strip().upper()
        val = (r.get("valuta") or "EUR").strip().upper() or "EUR"
        try:
            tid = db.log_trade(ticker=r["ticker"].strip().upper(), action=az,
                               quantita=_numero(r["quantita"]), prezzo=_numero(r["prezzo"]),
                               valuta=val, data=_data_iso(r["data"]),
                               note=(r.get("note") or "").strip() or "import CSV")
            esito["scritte"] += 1
            if az in ("SELL", "TRIM") and val != "EUR":
                in_valuta.append(tid)
        except ValueError as e:      # rifiuto del DB non anticipato: si ferma e DICE dove
            esito["errori"].append(f"riga {r['_riga']}: rifiutata dal DB: {e}")
            break
    if in_valuta:
        # review 02/09 (F5): log_trade mette realized_eur col cambio di OGGI (rete) su un
        # trade retrodatato — n.d. dichiarato batte un numero col cambio sbagliato
        with db._conn() as c:
            c.execute("UPDATE trade_history SET realized_eur=NULL WHERE id IN (%s)"
                      % ",".join("?" * len(in_valuta)), in_valuta)
        esito["vendite_in_valuta"] = len(in_valuta)
        esito["nota"] += (f"; {len(in_valuta)} vendite in valuta: realized_eur n.d., lancia "
                          "tools/migrations/backfill_realized.py --apply per l'FX storico")
    esito["dopo"] = _conta(db)
    return esito


def _porta_8765_occupata():
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", 8765)) == 0


def _backup(db_path):
    """Copia con l'API di backup di SQLite (il -wal entra; shutil.copyfile lo salterebbe)
    + quick_check sulla copia: un backup non sano = nessuna scrittura."""
    bk = db_path + ".pre_import_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
    src, dst = sqlite3.connect(db_path, timeout=15), sqlite3.connect(bk)
    try:
        src.backup(dst)
        qc = dst.execute("PRAGMA quick_check").fetchone()
    finally:
        dst.close()
        src.close()
    if not qc or str(qc[0]).lower() != "ok":
        raise SystemExit(f"[STOP] backup non sano ({qc}): nessuna scrittura")
    return bk


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--apply", action="store_true", help="scrive davvero (default: dry-run)")
    ap.add_argument("--db", default=None, help="path del DB (default: memory_db.SQLITE_PATH)")
    a = ap.parse_args(argv)
    from bellomberg.storage.memory_db import MemoryDB, SQLITE_PATH
    db_path = a.db or SQLITE_PATH
    if not os.path.exists(db_path):
        print(f"[STOP] DB non trovato: {db_path} (il libro lo crea il backend al primo avvio)")
        return 2
    if a.apply and _porta_8765_occupata():
        print("[STOP] backend attivo sulla porta 8765: chiudi l'app prima di scrivere sul DB")
        return 2
    righe = leggi_csv(a.csv)
    for r in righe:
        print(f"{r['data']:<20} {r['ticker']:<10} {r['azione']:<8} {r['quantita']:>10} "
              f"{r['prezzo']:>12} {(r.get('valuta') or 'EUR'):<4}")
    db = MemoryDB(db_path=db_path)
    errori = valida(righe, db)
    if errori:
        print("\n[ERRORI] nessuna scrittura:")
        for e in errori:
            print("  " + e)
        return 1
    bk = None
    if a.apply:
        if _porta_8765_occupata():   # di nuovo, subito prima di scrivere
            print("[STOP] backend attivo sulla porta 8765: chiudi l'app prima di scrivere sul DB")
            return 2
        bk = _backup(db_path)
        print(f"[BACKUP] {bk}")
    e = importa(db, righe, apply=a.apply)
    modo = "APPLY" if a.apply else "DRY-RUN"
    print(f"\n[{modo}] lette {e['lette']} - scritte {e['scritte']} - "
          f"trade_history prima {e['prima']} -> dopo {e['dopo']}")
    print("[NOTA] " + e["nota"])
    for err in e["errori"]:
        print("  [!] " + err)
    if e["errori"] and bk:
        print(f"  [!] righe gia' scritte restano: via di ritorno = il backup {bk}")
    return 0 if not e["errori"] else 1


if __name__ == "__main__":
    sys.exit(main())
