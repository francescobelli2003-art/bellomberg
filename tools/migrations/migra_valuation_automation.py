"""Install the valuation queue, publication and personal-copy schemas explicitly.

Default: rehearse on a temporary SQLite backup; --apply requires a free backend
port, a verified backup beside the source, and an unchanged source at commit.
Never initializes a missing database or backfills economic records.
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

from bellomberg.storage import valuation_jobs, valuation_versions, valuation_variants
from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
from tools.migrations import migra_method_records as _archive
from tools.migrations import migra_valuation_metadata as _metadata


TABLES = ("valuation_jobs", "valuation_job_events", "valuation_model_heads",
          "valuation_publications", "valuation_variants")
THESIS_COLUMNS = {"id", "ticker", "date", "price_at_thesis", "fair_value", "growth_path",
                  "ebitda_margin_target", "terminal_growth", "variant_view", "engine",
                  "subsector", "memo_id"}


def backend_alive():
    return _archive.backend_alive()


def _target_schema(conn):
    names = {kind + ":" + name for kind, name, table in conn.execute(
        "SELECT type,name,tbl_name FROM sqlite_master") if table in TABLES}
    return {key: value for key, value in schema_fingerprint(conn).items() if key in names}


def _install(conn):
    valuation_jobs.ensure_schema(conn)
    valuation_versions.ensure_schema(conn)
    valuation_variants.ensure_schema(conn)


def _expected_schema():
    with closing(sqlite3.connect(":memory:")) as conn:
        for statement in (*valuation_jobs.SCHEMA, *valuation_versions.SCHEMA, *valuation_variants.SCHEMA):
            conn.execute(statement)
        return _target_schema(conn)


def _require_existing_schema(conn):
    _metadata._validate(conn, _metadata._expected_schema(), complete=True)
    _archive._valida(conn, _archive._schema_atteso(), completo=True)
    for table, required in (("valuation_theses", THESIS_COLUMNS),
                            ("decisions", {"id"})):
        columns = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
        if not required <= columns.keys() or columns["id"][5] != 1:
            raise ValueError(f"schema richiesto incompatibile o assente: {table}")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("foreign key check fallito nelle tabelle preesistenti")


def _validate(conn, expected, *, complete=False):
    if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("DB quick_check fallito")
    _require_existing_schema(conn)
    actual = _target_schema(conn)
    if any(expected.get(key) != value for key, value in actual.items()) or (complete and actual != expected):
        raise ValueError("schema valuation automation incompatibile")
    for table in TABLES:
        if "table:" + table in actual and conn.execute(f"PRAGMA foreign_key_check({table})").fetchone():
            raise ValueError(f"foreign key check fallito: {table}")


def _apply(path, before, schema_before, expected):
    counter = _archive.Statements()
    with closing(_archive.apri(path, sola_lettura=False)) as conn:
        conn.set_trace_callback(counter.trace)
        conn.execute("BEGIN IMMEDIATE")
        initial_changes = conn.total_changes
        try:
            if fingerprint(conn) != before or schema_fingerprint(conn) != schema_before:
                raise ValueError("DB variato dopo preflight/backup: nessuna migrazione applicata")
            _validate(conn, expected)
            _install(conn)
            _validate(conn, expected, complete=True)
            all_after = fingerprint(conn)
            schema_after = schema_fingerprint(conn)
            unchanged = {key: all_after.get(key) for key in before} == before and all(
                schema_after.get(key) == value for key, value in schema_before.items())
            if not unchanged:
                raise ValueError("dati o schema preesistenti variati durante migrazione: rollback")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        return {"schema_completo": True, "tabelle_preesistenti_invariate": unchanged,
                "righe_cambiate": conn.total_changes - initial_changes,
                "scritture": _archive.conteggio(counter)}


def _rileggi(path, before, schema_before, expected):
    with closing(_archive.apri(path, sola_lettura=True)) as conn:
        _validate(conn, expected, complete=True)
        data_after = fingerprint(conn)
        schema_after = schema_fingerprint(conn)
        unchanged = {key: data_after.get(key) for key in before} == before and all(
            schema_after.get(key) == value for key, value in schema_before.items())
    if not unchanged:
        raise ValueError("rilettura: dati o schema preesistenti diversi dal preflight")
    return {"schema_completo": True, "tabelle_preesistenti_invariate": True,
            "oggetti_automation": sorted(expected)}


def migra(db_path, *, apply=False):
    path = _archive.richiedi_wal(db_path)
    expected = _expected_schema()
    counter = _archive.Statements()
    backup = None
    with tempfile.TemporaryDirectory(prefix="bellomberg_valuation_automation_", ignore_cleanup_errors=True) as work:
        rehearsal = Path(work) / "rehearsal.db"
        with closing(_archive.apri(path, sola_lettura=True)) as conn:
            conn.set_trace_callback(counter.trace)
            conn.execute("BEGIN")
            _validate(conn, expected)
            before, schema_before = fingerprint(conn), schema_fingerprint(conn)
            if apply and backend_alive():
                raise RuntimeError("porta 8765 occupata: chiudere il backend prima della migrazione")
            if apply:
                backup = path.with_name(path.name + ".pre-valuation-automation-" +
                                        datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".bak")
                backup.touch(exist_ok=False)
            for destination in (rehearsal, *([backup] if backup else [])):
                with closing(sqlite3.connect(destination)) as copy:
                    conn.backup(copy)
                    _validate(copy, expected)
                    if fingerprint(copy) != before or schema_fingerprint(copy) != schema_before:
                        raise ValueError("backup diverso dallo snapshot di preflight")
            conn.execute("ROLLBACK")
            source_changes = conn.total_changes
        with closing(_archive.apri(path, sola_lettura=True)) as conn:
            source_unchanged = fingerprint(conn) == before and schema_fingerprint(conn) == schema_before
        result = {"modo": "apply" if apply else "dry-run", "db": str(path), "backup": str(backup) if backup else None,
                  "tabelle_mancanti": sorted(set(TABLES) - set(before)), "prima": before,
                  "scritture_sorgente": {**_archive.conteggio(counter), "total_changes": source_changes},
                  "sorgente_invariata": source_unchanged,
                  "prova": _apply(rehearsal, before, schema_before, expected)}
    if not apply:
        return result
    if not source_unchanged:
        raise ValueError("DB variato durante il preflight: nessuna migrazione applicata")
    if backend_alive():
        raise RuntimeError("porta 8765 occupata dopo la prova: nessuna migrazione applicata")
    result["applicazione"] = _apply(path, before, schema_before, expected)
    result["rilettura"] = _rileggi(path, before, schema_before, expected)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None, help="default: DB del progetto, risolto al momento dell'esecuzione")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: prova su copia")
    mode.add_argument("--apply", action="store_true", help="backup verificato e apply transazionale")
    args = parser.parse_args(argv)
    if args.db is None:
        from bellomberg.storage import memory_db
        args.db = memory_db.SQLITE_PATH
    print(json.dumps(migra(args.db, apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
