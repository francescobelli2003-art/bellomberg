"""Provider ``method_inputs``: i set di record approvati dal PM, letti dall'archivio privato.

Decisione PM 1A (13/09): i record documentati li prepara Claude dai documenti ufficiali,
il PM li rivede, il comitato li legge a ogni run. Questo provider legge l'archivio
(``bellomberg.storage.method_records_store``) e restituisce TUTTI i set approvati del
titolo, per metodo: il provider riceve solo ``(ticker, as_of)`` e non conosce il metodo,
quindi il set si sceglie DOPO la decisione del metodo (``sector_analysis``).

Regole:
- il DB e' quello del progetto risolto A OGNI CHIAMATA (``memory_db.SQLITE_PATH``), aperto
  con ``apri_lettura`` e il path NUDO (coperto dal tripwire dei test), mai creato;
- ogni guasto e' dichiarato con il suo stato (source_error / data_missing), mai un
  ripiego: ``PermissionError`` e lock si catturano QUI, altrimenti ``_acquire`` li
  scambierebbe per credenziali mancanti;
- l'envelope non contiene conteggi della coda di revisione: entrerebbero nello
  ``snapshot_id`` e l'identita' dello snapshot cambierebbe con la coda, non con le evidenze.
"""
import os
import sqlite3

SOURCE_ID = "method_records_archive"
MIGRAZIONE = "tools/migrations/migra_method_records.py"
STORE_MODULE = "bellomberg.storage.method_records_store"
SET_KEYS = ("method_version", "records_sha256", "provenance", "prepared_by", "prepared_at",
            "earliest_valid_until", "latest_as_of", "scenario_rationale")


def _errore(message):
    return {"status": "source_error", "source_id": SOURCE_ID, "data": None, "records": [],
            "message": message}


def method_inputs(ticker, *, as_of):
    """Set approvati dal PM con revisione e preparazione <= ``as_of``, per metodo."""
    from bellomberg.storage import memory_db
    path = memory_db.SQLITE_PATH
    try:
        from bellomberg.storage import method_records_store as store
    except ImportError as exc:
        return _errore("archivio record non letto: modulo " + STORE_MODULE
                       + " non importabile (" + type(exc).__name__ + ": " + str(exc) + ")")
    if not os.path.isfile(path):
        return _errore("archivio record non letto: DB del progetto assente al percorso di "
                       "memory_db.SQLITE_PATH (il DB non viene creato)")
    symbol = str(ticker).strip().upper()
    conn = None
    try:
        conn = store.apri_lettura(path)
        sets = store.leggi_approvati(conn, symbol, as_of)
    except store.ArchivioNonMigrato as exc:
        return _errore("archivio non migrato: " + MIGRAZIONE + " (" + str(exc) + ")")
    except store.SetAlterato as exc:
        return _errore("archivio record: set approvato alterato (" + str(exc) + ")")
    except (OSError, sqlite3.Error) as exc:
        # PermissionError e' un OSError: DB bloccato o negato = fonte non letta, non credenziali
        return _errore("archivio record non letto: " + type(exc).__name__ + ": " + str(exc))
    finally:
        if conn is not None:
            conn.close()
    if not sets:
        return {"status": "data_missing", "source_id": SOURCE_ID, "data": None, "records": [],
                "message": "nessun set approvato per " + symbol}
    by_method = {}
    for item in sets:
        method = item["method_id"]
        if method in by_method:
            return _errore("archivio record: piu' set approvati restituiti per " + symbol + " / "
                           + str(method) + ": lettura ambigua, nessun set scelto")
        by_method[method] = {"set_id": item["id"], **{key: item[key] for key in SET_KEYS},
                             "review": dict(item["review"]), "records": item["records"]}
    reviewed = max(str(entry["review"]["reviewed_at"])[:10] for entry in by_method.values())
    return {"status": "ok", "source_id": SOURCE_ID, "as_of": reviewed,
            "data": {"sets": by_method}, "records": [],
            "message": "Set approvati dal PM per " + ", ".join(sorted(by_method))
                       + ": i record si scelgono dopo la decisione del metodo"}
