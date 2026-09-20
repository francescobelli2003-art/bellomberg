"""I-20: dry-run su copia; --apply richiede backend fermo e crea un backup."""
from contextlib import closing
from datetime import datetime
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bellomberg.storage.filing_store import TABLES, ensure_schema
from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint
from tools.migrations.migra_method_records import apri, backend_alive, richiedi_wal, Statements


def _schema(conn):
    keys = {kind + ':' + name for kind, name, table in conn.execute(
        'SELECT type,name,tbl_name FROM sqlite_master') if table in TABLES}
    return {k: v for k, v in schema_fingerprint(conn).items() if k in keys}


def _expected():
    with closing(sqlite3.connect(':memory:')) as conn:
        ensure_schema(conn)
        return _schema(conn)


def _validate(conn, expected, complete=False):
    if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise ValueError('quick_check fallito')
    current = _schema(conn)
    if any(expected.get(k) != v for k, v in current.items()) or (complete and current != expected):
        raise ValueError('schema filing incompatibile')
    if not {'decisions', 'valuation_theses'} <= set(fingerprint(conn)):
        raise ValueError('DB estraneo: mancano decisions/valuation_theses')


def _apply(path, before, schema, expected):
    with closing(apri(path, sola_lettura=False)) as conn:
        trace = Statements()
        conn.set_trace_callback(trace.trace)
        conn.execute('BEGIN IMMEDIATE')
        try:
            if fingerprint(conn) != before or schema_fingerprint(conn) != schema:
                raise ValueError('DB modificato dopo il preflight')
            _validate(conn, expected)
            changes = conn.total_changes
            ensure_schema(conn)
            _validate(conn, expected, complete=True)
            after, after_schema = fingerprint(conn), schema_fingerprint(conn)
            if any(after.get(k) != v for k, v in before.items()) or any(
                    after_schema.get(k) != v for k, v in schema.items()):
                raise ValueError('dati/schema preesistenti variati: rollback')
            conn.commit()
            return {'righe_cambiate': conn.total_changes - changes,
                    'istruzioni_scrittura': trace.writes,
                    'tabelle_preesistenti_invariate': True}
        except BaseException:
            conn.rollback()
            raise


def migra(db_path, *, apply=False):
    path = richiedi_wal(db_path)
    expected = _expected()
    if apply and backend_alive():
        raise RuntimeError('porta 8765 occupata: fermare il backend prima della migrazione')
    with tempfile.TemporaryDirectory(prefix='filing_migration_') as temporary:
        probe = Path(temporary) / 'probe.db'
        backup = None
        with closing(apri(path, sola_lettura=True)) as conn:
            trace = Statements()
            conn.set_trace_callback(trace.trace)
            conn.execute('BEGIN')
            _validate(conn, expected)
            before, schema = fingerprint(conn), schema_fingerprint(conn)
            if apply:
                backup = path.with_name(path.name + '.pre-filing-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak')
                backup.touch(exist_ok=False)
            for destination in [probe] + ([backup] if backup else []):
                with closing(sqlite3.connect(destination)) as copy:
                    conn.backup(copy)
                    if fingerprint(copy) != before or schema_fingerprint(copy) != schema:
                        raise ValueError('backup non corrispondente al preflight')
            source_changes = conn.total_changes
            conn.rollback()
        result = {'modo': 'apply' if apply else 'dry-run', 'db': str(path),
                  'backup': str(backup) if backup else None,
                  'tabelle_mancanti': sorted(set(TABLES) - set(before)),
                  'scritture_sorgente_preflight': trace.writes,
                  'righe_sorgente_preflight': source_changes,
                  'prova': _apply(probe, before, schema, expected)}
        with closing(apri(path, sola_lettura=True)) as conn:
            result['sorgente_invariata'] = fingerprint(conn) == before and schema_fingerprint(conn) == schema
        if not apply:
            return result
        if backend_alive():
            raise RuntimeError('porta 8765 occupata dopo il preflight')
        result['applicazione'] = _apply(path, before, schema, expected)
        with closing(apri(path, sola_lettura=True)) as conn:
            _validate(conn, expected, complete=True)
            after = fingerprint(conn)
            if any(after.get(k) != v for k, v in before.items()):
                raise ValueError('rilettura dati preesistenti diversa')
            result['rilettura'] = {'schema_completo': True, 'tabelle_preesistenti_invariate': True}
        return result


def main(argv=None):
    from bellomberg.core.paths import SQLITE_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=str(SQLITE_PATH))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    print(json.dumps(migra(args.db, apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
