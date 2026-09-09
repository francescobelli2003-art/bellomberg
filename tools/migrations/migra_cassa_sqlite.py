"""Bootstrap controllato della cassa legacy nel ledger SQLite."""
import argparse, hashlib, json, os, shutil, socket, sqlite3, sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", str(ROOT))
from bellomberg.storage import memory_db


def _cents(raw):
    if raw is None or isinstance(raw, bool):
        raise ValueError("cash_disponibile_eur assente o non numerico")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError("cash_disponibile_eur non numerico") from exc
    if not value.is_finite() or value.as_tuple().exponent < -2:
        raise ValueError("cash_disponibile_eur deve essere finito e avere massimo 2 decimali")
    return int(value * 100)


def _json_unico(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("chiave JSON duplicata: " + key)
            out[key] = value
        return out
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs)


def _uri_ro(path):
    return "file:" + str(path).replace("\\", "/") + "?mode=ro"


def _stato(db_path):
    with sqlite3.connect(_uri_ro(db_path), uri=True) as conn:
        table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cash_state'").fetchone()
        return conn.execute("SELECT 1 FROM cash_state WHERE singleton_id=1").fetchone() if table else None


def _porta_occupata():
    try:
        port = int(os.environ.get("BELLOMBERG_API_PORT", "8765"))
    except ValueError as exc:
        raise ValueError("BELLOMBERG_API_PORT non valida") from exc
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def run(source, db_path, apply=False):
    source, db_path = Path(source).resolve(), Path(db_path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError("DB sorgente assente: " + str(db_path))
    raw = source.read_bytes()
    data = _json_unico(raw)
    if not isinstance(data, dict):
        raise ValueError("portfolio.json non e' un oggetto")
    cents, digest = _cents(data.get("cash_disponibile_eur")), hashlib.sha256(raw).hexdigest()
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    if _stato(db_path):
        raise RuntimeError("cash_state gia' inizializzata: overwrite rifiutato")
    result = {"apply": apply, "balance_cents": cents, "source_sha256": digest}
    if not apply:
        if hashlib.sha256(db_path.read_bytes()).hexdigest() != before_hash:
            raise RuntimeError("dry-run ha modificato il DB")
        return result
    if _porta_occupata():
        raise RuntimeError("porta backend occupata: spegnerlo prima della migrazione")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = db_path.parent / "migration_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    db_backup = backup_dir / f"consigliere_pre_cash_{stamp}.db"
    json_backup = backup_dir / f"portfolio_cash_{stamp}.json"
    if db_backup.exists() or json_backup.exists():
        raise FileExistsError("backup gia' esistente")
    with sqlite3.connect(_uri_ro(db_path), uri=True) as src, sqlite3.connect(db_backup) as dst:
        src.backup(dst)
    shutil.copy2(source, json_backup)
    if hashlib.sha256(json_backup.read_bytes()).hexdigest() != digest:
        raise RuntimeError("backup JSON non byte-identico")
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise RuntimeError("portfolio.json cambiato durante il backup")
    holder = memory_db.MemoryDB.__new__(memory_db.MemoryDB)
    holder.db_path = str(db_path)
    holder._init_sqlite()  # migrazione condivisa, soltanto dopo il backup
    conn = memory_db.connect_sqlite(str(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM cash_state WHERE singleton_id=1").fetchone():
            raise RuntimeError("cash_state inizializzata durante la migrazione")
        conn.execute("INSERT INTO cash_state(singleton_id,balance_cents,updated_at,source) VALUES(1,?,?,?)",
                     (cents, datetime.now().isoformat(timespec="seconds"), "migration:portfolio.json:" + digest))
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("quick_check fallito")
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise RuntimeError("portfolio.json cambiato prima del commit")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    result.update({"db_backup": str(db_backup), "json_backup": str(json_backup)})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=memory_db.PORTFOLIO_JSON_PATH)
    parser.add_argument("--db", default=memory_db.SQLITE_PATH)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.db, args.apply), indent=2))


if __name__ == "__main__":
    main()
