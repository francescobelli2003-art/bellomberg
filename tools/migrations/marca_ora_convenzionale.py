"""Marca soltanto i trade legacy NULL con data ISO esatta a mezzogiorno.

L'anteprima e' in sola lettura. --apply conferma esplicitamente le candidate:
l'ora 12:00:00 e' una convenzione proposta, non una misura dell'ora del trade.
La ricevuta consente un UPDATE inverso puntuale soltanto sullo stesso DB e se
nessun dato/schema e' cambiato. Non sostituisce mai il DB con il backup.
"""
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
from tools.migrations import migra_valuation_metadata as _valuation


def _porta_occupata():
    return _valuation.backend_alive()


def _guardia_porta():
    if _porta_occupata():
        raise RuntimeError("porta 8765 occupata: nessuna scrittura consentita")


def _apri(path, mode):
    return sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True, timeout=5)


def _snapshot(conn):
    if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("DB quick_check fallito")
    cols = conn.execute("PRAGMA table_info(trade_history)").fetchall()
    columns = [row[1] for row in cols]
    if not {"id", "data", "ora_convenzionale"} <= set(columns):
        raise ValueError("schema trade_history incompatibile: mancano le colonne richieste")
    if [row[1] for row in cols if row[5]] != ["id"]:
        raise ValueError("schema trade_history incompatibile: id deve essere la chiave primaria")
    return {"dati": fingerprint(conn), "schema": schema_fingerprint(conn),
            "columns": columns, "rows": conn.execute("SELECT * FROM trade_history ORDER BY id").fetchall()}


def _candidate(snapshot):
    columns = snapshot["columns"]
    id_col, data_col, flag_col = (columns.index(name) for name in ("id", "data", "ora_convenzionale"))
    ids = []
    for row in snapshot["rows"]:
        value = row[data_col]
        if row[flag_col] is not None or not isinstance(value, str):
            continue
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T12:00:00", value):
            continue
        try:
            datetime.fromisoformat(value)
        except ValueError:
            continue
        ids.append(row[id_col])
    return ids


def _uguale(left, right):
    return left["dati"] == right["dati"] and left["schema"] == right["schema"]


def _verifica_modifica(before, after, candidate, flag):
    if before["schema"] != after["schema"]:
        raise ValueError("schema variato durante la marcatura: rollback")
    if {k: v for k, v in before["dati"].items() if k != "trade_history"} != {
            k: v for k, v in after["dati"].items() if k != "trade_history"}:
        raise ValueError("tabelle estranee variate durante la marcatura: rollback")
    columns = before["columns"]
    id_col, flag_col = columns.index("id"), columns.index("ora_convenzionale")
    ids = set(candidate)
    expected = []
    for row in before["rows"]:
        values = list(row)
        if row[id_col] in ids:
            values[flag_col] = flag
        expected.append(tuple(values))
    if expected != after["rows"]:
        raise ValueError("colonne o righe estranee variate durante la marcatura: rollback")


def _artefatti(directory, label):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    stem = "ora-convenzionale-" + label + "-" + stamp
    return directory / (stem + ".db"), directory / (stem + ".json")


def _backup(conn, path, before):
    path.touch(exist_ok=False)
    with closing(sqlite3.connect(path)) as target:
        conn.backup(target)
        if not _uguale(_snapshot(target), before):
            raise ValueError("backup diverso dallo snapshot di origine")


def _digest(payload):
    encoded = json.dumps({key: value for key, value in payload.items() if key != "sha256"},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _scrivi_ricevuta(path, payload):
    payload["sha256"] = _digest(payload)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def _applica(path, before, candidate, flag, backup, receipt):
    with closing(_apri(path, "rw")) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        changes = conn.total_changes
        try:
            if not _uguale(_snapshot(conn), before):
                raise ValueError("DB variato dopo preflight/backup: nessuna modifica applicata")
            old_flag = None if flag == 1 else 1
            for trade_id in candidate:
                conn.execute("UPDATE trade_history SET ora_convenzionale=? "
                             "WHERE id=? AND ora_convenzionale IS ?", (flag, trade_id, old_flag))
            written = conn.total_changes - changes
            if written != len(candidate):
                raise ValueError(f"scritture misurate {written}, attese {len(candidate)}: rollback")
            after = _snapshot(conn)
            _verifica_modifica(before, after, candidate, flag)
            payload = {"tipo": "bellomberg-ora-convenzionale-v1", "db": str(path),
                       "operazione": "marca" if flag == 1 else "ripristina",
                       "candidate": candidate, "prima": before["dati"], "dopo": after["dati"],
                       "schema": before["schema"], "backup": str(backup), "ricevuta": str(receipt),
                       "scritte": written}
            # Ricevuta persistita PRIMA del commit: un errore qui annulla le modifiche.
            # Se il commit fallisce, la ricevuta non e' utilizzabile: lo stato non coincide.
            _scrivi_ricevuta(receipt, payload)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return {"candidate": candidate, "scritte": written, "prima": before["dati"],
            "dopo": after["dati"], "backup": str(backup), "ricevuta": str(receipt)}


def run(db_path, apply=False, backup_dir=None):
    path = Path(db_path).resolve(strict=True)
    if apply:
        _guardia_porta()
    counter = _valuation.Statements()
    with closing(_apri(path, "ro")) as conn:
        conn.set_trace_callback(counter.trace)
        conn.execute("BEGIN")
        changes = conn.total_changes
        before = _snapshot(conn)
        candidate = _candidate(before)
        if apply and candidate:
            backup, receipt = _artefatti(backup_dir or path.parent / "migration_backups", "marca")
            _backup(conn, backup, before)
        after = _snapshot(conn)
        written = conn.total_changes - changes
        if written or not _uguale(before, after):
            raise ValueError("preflight ha modificato il DB")
        conn.execute("ROLLBACK")
    result = {"candidate": candidate, "scritte": written, "prima": before["dati"],
              "dopo": after["dati"], "backup": None, "ricevuta": None,
              "mode": "apply" if apply else "dry-run", "db": str(path),
              "criterio": "NULL legacy con YYYY-MM-DDT12:00:00 esatto: convenzione da confermare con --apply",
              "source_statements": counter.result()}
    if not apply or not candidate:
        return result
    _guardia_porta()
    result.update(_applica(path, before, candidate, 1, backup, receipt))
    return result


def ripristina(db_path, ricevuta):
    path = Path(db_path).resolve(strict=True)
    receipt = Path(ricevuta).resolve(strict=True)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    if (not isinstance(payload, dict) or payload.get("sha256") != _digest(payload)
            or payload.get("tipo") != "bellomberg-ora-convenzionale-v1"
            or payload.get("operazione") != "marca"):
        raise ValueError("ricevuta alterata o incompatibile")
    if payload.get("db") != str(path) or payload.get("ricevuta") != str(receipt):
        raise ValueError("ricevuta non valida per questo DB/percorso")
    _guardia_porta()
    original_backup = Path(payload["backup"]).resolve(strict=True)
    if original_backup.parent != receipt.parent or original_backup == path:
        raise ValueError("backup non coerente con la ricevuta")
    with closing(_apri(original_backup, "ro")) as saved:
        saved.execute("BEGIN")
        original = _snapshot(saved)
        if (original["dati"] != payload["prima"] or original["schema"] != payload["schema"]
                or _candidate(original) != payload["candidate"]):
            raise ValueError("backup alterato o diverso dalla ricevuta")
    with closing(_apri(path, "ro")) as conn:
        conn.execute("BEGIN")
        before = _snapshot(conn)
        if before["dati"] != payload["dopo"] or before["schema"] != payload["schema"]:
            raise ValueError("DB variato dopo la marcatura: ripristino rifiutato")
        _verifica_modifica(original, before, payload["candidate"], 1)
        backup, new_receipt = _artefatti(receipt.parent, "ripristina")
        _backup(conn, backup, before)
        conn.execute("ROLLBACK")
    _guardia_porta()
    return _applica(path, before, payload["candidate"], None, backup, new_receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="DB esistente; il percorso deve essere esplicito")
    parser.add_argument("--backup-dir")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--ripristina", metavar="RICEVUTA")
    args = parser.parse_args()
    try:
        result = (ripristina(args.db, args.ripristina) if args.ripristina
                  else run(args.db, apply=args.apply, backup_dir=args.backup_dir))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
