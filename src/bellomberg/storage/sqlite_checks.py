"""Read-only SQLite fingerprints shared by additive migration procedures."""
import hashlib
import json


def fingerprint(conn):
    result = {}
    tables = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for name, sql in tables:
        quoted = '"' + name.replace('"', '""') + '"'
        rows = conn.execute('SELECT * FROM ' + quoted).fetchall()
        serial = sorted(repr(tuple(row)) for row in rows)
        payload = json.dumps([sql, serial], ensure_ascii=False).encode('utf-8')
        result[name] = {'rows': len(rows), 'sha256': hashlib.sha256(payload).hexdigest()}
    return result


def schema_fingerprint(conn):
    # Includes indices/triggers/views, without page numbers that backup may change.
    rows = conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
    return {kind + ':' + name: hashlib.sha256(repr((kind, table, sql)).encode('utf-8')).hexdigest()
            for kind, name, table, sql in rows}
