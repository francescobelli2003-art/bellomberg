"""Add immutable valuation snapshots/links; dry-run by default, no legacy backfill."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import re
import errno
from contextlib import closing
from datetime import datetime

from bellomberg.storage.memory_db import VALUATION_SNAPSHOT_STATEMENTS
from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint

METADATA_TABLES={'valuation_snapshots','valuation_snapshot_links'}


def _metadata_schema(conn):
    names={kind+':'+name for kind,name,table in conn.execute('SELECT type,name,tbl_name FROM sqlite_master')
           if table in METADATA_TABLES}
    return {key:value for key,value in schema_fingerprint(conn).items() if key in names}


def _expected_schema():
    with closing(sqlite3.connect(':memory:')) as conn:
        for statement in VALUATION_SNAPSHOT_STATEMENTS:conn.execute(statement)
        return _metadata_schema(conn)


def _validate(conn,expected,*,complete=False):
    if conn.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:raise ValueError('DB quick_check fallito')
    actual=_metadata_schema(conn)
    if any(expected.get(key)!=value for key,value in actual.items()) or complete and actual!=expected:
        raise ValueError('Metadata schema incompatibile: tabelle, indici o trigger diversi dal contratto')
    for table in METADATA_TABLES:
        if 'table:'+table in actual and conn.execute('PRAGMA foreign_key_check('+table+')').fetchall():
            raise ValueError('Metadata foreign key check fallito: riferimenti orfani')


class Statements:
    def __init__(self):self.observed=0;self.writes=0
    def trace(self,sql):
        self.observed+=1
        sql=re.sub(r'\A(?:\s|--[^\n]*(?:\n|$)|/\*[\s\S]*?\*/)*','',sql).upper()
        if re.match(r'(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|REPLACE|VACUUM|REINDEX|ATTACH|DETACH)\b',sql):self.writes+=1
    def result(self):return {'observed':self.observed,'writes':self.writes}


def _install(conn):
    for statement in VALUATION_SNAPSHOT_STATEMENTS:conn.execute(statement)


def _apply(path,before,schema,expected):
    counter=Statements()
    with closing(sqlite3.connect(Path(path).resolve(strict=True).as_uri()+'?mode=rw',uri=True)) as conn:
        conn.set_trace_callback(counter.trace);conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        changes=conn.total_changes
        try:
            if fingerprint(conn)!=before or schema_fingerprint(conn)!=schema:
                raise ValueError('DB variato dopo preflight/backup: nessuna migrazione applicata')
            _validate(conn,expected)
            _install(conn)
            _validate(conn,expected,complete=True)
            all_after=fingerprint(conn);schema_after=schema_fingerprint(conn)
            after={key:all_after.get(key) for key in before}
            if after!=before or any(schema_after.get(key)!=value for key,value in schema.items()):
                raise ValueError('Dati/schema preesistenti variati durante migrazione: rollback')
            conn.execute('COMMIT')
        except BaseException:
            if conn.in_transaction:conn.execute('ROLLBACK')
            raise
        return {'after':after,'all_tables_after':all_after,'row_changes':conn.total_changes-changes,
                'preexisting_tables_unchanged':after==before,'statements':counter.result()}


def _historical_rows(conn):
    measured = {}
    for table in ("valuation_theses", "decisions"):
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        measured[table] = {"rows": len(rows), "sha256": hashlib.sha256(encoded.encode()).hexdigest()}
    return measured


def backend_alive():
    for host in ('127.0.0.1','::1'):
        try:
            with socket.create_connection((host,8765),timeout=.5):return True
        except OSError as exc:
            if not isinstance(exc,ConnectionRefusedError) and exc.errno not in (errno.ECONNREFUSED,10061):
                raise RuntimeError('Porta 8765 non verificabile: nessuna scrittura consentita') from exc
    return False


def migrate(db_path, *, apply=False):
    path = Path(db_path).resolve(strict=True)
    expected=_expected_schema();counter=Statements();backup=None
    work=Path(tempfile.mkdtemp(prefix='bellomberg_valuation_rehearsal_'))
    snapshot=work/'snapshot.db';rehearsal=work/'rehearsal.db'
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.set_trace_callback(counter.trace);conn.execute('BEGIN')
        _validate(conn,expected)
        _historical_rows(conn)  # Required legacy tables, never initialize an unrelated DB.
        before=fingerprint(conn);schema=schema_fingerprint(conn);tables=set(before)
        if apply and backend_alive():
            raise RuntimeError("porta 8765 occupata: chiudere backend prima della migrazione")
        if apply:
            backup=path.with_name(path.name+'.pre-valuation-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.bak')
            backup.touch(exist_ok=False)
        for target_path in (snapshot,rehearsal,*([backup] if backup else [])):
            with closing(sqlite3.connect(target_path)) as target:
                conn.backup(target)
                _validate(target,expected)
                if fingerprint(target)!=before or schema_fingerprint(target)!=schema:
                    raise ValueError('Backup diverso dallo snapshot preflight')
        conn.execute('ROLLBACK')
    result = {"mode": "apply" if apply else "dry-run", "db": str(path), "before": before,
              "missing_tables": sorted(METADATA_TABLES - tables),
              "legacy_backfill": False, "backup": str(backup) if backup else None,
              'source_schema':schema,'source_statements':counter.result(),
              'snapshot':str(snapshot),'rehearsal_path':str(rehearsal)}
    result['rehearsal']=_apply(rehearsal,before,schema,expected)
    if not apply:
        return result
    if backend_alive():
        raise RuntimeError('porta 8765 occupata dopo rehearsal: nessuna migrazione applicata')
    actual=_apply(path,before,schema,expected)
    result.update(after=actual['after'],legacy_rows_unchanged=before==actual['after'],application=actual)
    return result


def main():
    from bellomberg.core.paths import SQLITE_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(SQLITE_PATH))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.db, apply=args.apply), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
