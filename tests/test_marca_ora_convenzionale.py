"""Presidi della marcatura legacy: soltanto SQLite sintetici, nessuna porta vera."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from bellomberg.storage.sqlite_checks import fingerprint, schema_fingerprint


def _script():
    path = Path(__file__).parents[1] / "tools/migrations/marca_ora_convenzionale.py"
    spec = importlib.util.spec_from_file_location("marca_ora_convenzionale_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def m(monkeypatch):
    module = _script()
    monkeypatch.setattr(module, "_porta_occupata", lambda: False)
    return module


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE trade_history(id INTEGER PRIMARY KEY, data TEXT,
                ora_convenzionale INTEGER, prezzo REAL, prova BLOB);
            CREATE TABLE positions(id INTEGER PRIMARY KEY, amount INTEGER);
            INSERT INTO positions VALUES(1,17);
            INSERT INTO trade_history VALUES(1,'2026-02-01T12:00:00',NULL,10,x'010203');
            INSERT INTO trade_history VALUES(2,'2026-02-02T16:58:25',NULL,11,x'040506');
            INSERT INTO trade_history VALUES(3,'2026-02-03T12:00:00',0,12,x'070809');
            INSERT INTO trade_history VALUES(4,'2026-02-04T12:00:00',1,13,x'0a0b0c');
            INSERT INTO trade_history VALUES(5,'2026-02-05T12:00:00.000',NULL,14,NULL);
            INSERT INTO trade_history VALUES(6,'2026-02-06T12:00:00+02:00',NULL,15,NULL);
        """)
    return path


def _stato(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        return fingerprint(conn), schema_fingerprint(conn)


def test_dry_run_misura_sola_lettura_e_non_crea_backup(m, db, tmp_path, monkeypatch):
    prima = db.read_bytes()
    def vietato():
        raise AssertionError("il dry-run non interroga la porta")
    monkeypatch.setattr(m, "_porta_occupata", vietato)
    out = m.run(db, backup_dir=tmp_path / "bk")
    assert out["candidate"] == [1] and out["scritte"] == 0
    assert out["prima"] == out["dopo"]
    assert out["source_statements"]["observed"] > 0
    assert out["source_statements"]["writes"] == 0
    assert db.read_bytes() == prima
    assert out["backup"] is None and out["ricevuta"] is None
    assert not (tmp_path / "bk").exists()


@pytest.mark.parametrize("apply", [False, True])
def test_db_mancante_non_viene_creato(m, tmp_path, apply):
    path = tmp_path / "assente.db"
    with pytest.raises(FileNotFoundError):
        m.run(path, apply=apply, backup_dir=tmp_path / "bk")
    assert not path.exists() and not (tmp_path / "bk").exists()


def test_schema_estraneo_non_viene_migrato(m, tmp_path):
    path = tmp_path / "altro.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE altro(id INTEGER)")
    prima = _stato(path)
    with pytest.raises(ValueError, match="schema"):
        m.run(path, apply=True)
    assert _stato(path) == prima


def test_apply_backup_e_ripristino_puntuale_preservano_tutte_le_colonne(m, db, tmp_path):
    prima = _stato(db)
    out = m.run(db, apply=True, backup_dir=tmp_path / "bk")
    assert out["scritte"] == 1
    assert _stato(Path(out["backup"])) == prima
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT ora_convenzionale FROM trade_history ORDER BY id").fetchall() == [
            (1,), (None,), (0,), (1,), (None,), (None,)]
        assert conn.execute("SELECT prezzo,prova FROM trade_history WHERE id=1").fetchone() == (10, b"\x01\x02\x03")
    applicato = _stato(db)
    restore = m.ripristina(db, out["ricevuta"])
    assert restore["scritte"] == 1 and _stato(db) == prima
    assert Path(restore["backup"]) != Path(out["backup"])
    assert _stato(Path(restore["backup"])) == applicato


def test_noop_non_crea_ricevute_ne_backup_e_misura_zero(m, db, tmp_path):
    m.run(db, apply=True, backup_dir=tmp_path / "bk")
    prima = _stato(db)
    out = m.run(db, apply=True, backup_dir=tmp_path / "nessun-backup")
    assert out["candidate"] == [] and out["scritte"] == 0
    assert out["backup"] is None and out["ricevuta"] is None
    assert not (tmp_path / "nessun-backup").exists() and _stato(db) == prima


@pytest.mark.parametrize("kind", ["trade", "position", "schema", "new_trade"])
def test_restore_rifiuta_drift_senza_sovrascrivere_dati_nuovi(m, db, tmp_path, kind):
    out = m.run(db, apply=True, backup_dir=tmp_path / "bk")
    sql = {"trade": "UPDATE trade_history SET prezzo=99 WHERE id=1",
           "position": "UPDATE positions SET amount=18",
           "schema": "CREATE INDEX nuovo ON trade_history(prezzo)",
           "new_trade": "INSERT INTO trade_history(id,data,prezzo) VALUES(7,'2026-04-01',20)"}[kind]
    with sqlite3.connect(db) as conn:
        conn.execute(sql)
    prima = _stato(db)
    with pytest.raises(ValueError, match="variat|drift"):
        m.ripristina(db, out["ricevuta"])
    assert _stato(db) == prima


def test_ricevuta_modificata_e_db_diverso_vengono_rifiutati(m, db, tmp_path):
    out = m.run(db, apply=True, backup_dir=tmp_path / "bk")
    altro = tmp_path / "copia.db"
    with sqlite3.connect(db) as source, sqlite3.connect(altro) as target:
        source.backup(target)
    prima = _stato(altro)
    with pytest.raises(ValueError, match="DB|db|ricevuta"):
        m.ripristina(altro, out["ricevuta"])
    assert _stato(altro) == prima
    path = Path(out["ricevuta"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["candidate"] = [3]
    path.write_text(json.dumps(payload), encoding="utf-8")
    prima = _stato(db)
    with pytest.raises(ValueError, match="ricevuta|Ricevuta"):
        m.ripristina(db, path)
    assert _stato(db) == prima


def test_backup_alterato_blocca_ripristino(m, db, tmp_path):
    out = m.run(db, apply=True, backup_dir=tmp_path / "bk")
    with sqlite3.connect(out["backup"]) as conn:
        conn.execute("UPDATE trade_history SET prezzo=99 WHERE id=1")
    prima = _stato(db)
    with pytest.raises(ValueError, match="backup|Backup"):
        m.ripristina(db, out["ricevuta"])
    assert _stato(db) == prima


def test_porta_occupata_blocca_anche_restore(m, db, tmp_path, monkeypatch):
    out = m.run(db, apply=True, backup_dir=tmp_path / "bk")
    monkeypatch.setattr(m, "_porta_occupata", lambda: True)
    prima = _stato(db)
    with pytest.raises(RuntimeError, match="porta"):
        m.ripristina(db, out["ricevuta"])
    assert _stato(db) == prima


def test_guardia_porta_riusa_probe_che_rifiuta_stato_sconosciuto(monkeypatch):
    from tools.migrations import migra_valuation_metadata as shared
    def incerta():
        raise RuntimeError("porta non verificabile")
    monkeypatch.setattr(shared, "backend_alive", incerta)
    with pytest.raises(RuntimeError, match="non verificabile"):
        _script()._porta_occupata()


def test_drift_dopo_backup_blocca_prima_di_scrivere(m, db, tmp_path, monkeypatch):
    backup = m._backup
    def concorrente(*args, **kwargs):
        result = backup(*args, **kwargs)
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE positions SET amount=18")
        return result
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(m, "_backup", concorrente)
    with pytest.raises(ValueError, match="variat|drift"):
        m.run(db, apply=True, backup_dir=tmp_path / "bk")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT ora_convenzionale FROM trade_history WHERE id=1").fetchone() == (None,)
        assert conn.execute("SELECT amount FROM positions").fetchone() == (18,)


@pytest.mark.parametrize("effect", [
    "UPDATE positions SET amount=18;",
    "UPDATE trade_history SET prezzo=99 WHERE id=NEW.id;",
    "UPDATE positions SET amount=18; UPDATE positions SET amount=17;",
])
def test_trigger_con_scritture_estranee_fa_rollback_anche_se_ripristina_i_valori(m, db, tmp_path, effect):
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TRIGGER extra AFTER UPDATE OF ora_convenzionale ON trade_history BEGIN " + effect + " END")
    prima = _stato(db)
    with pytest.raises(ValueError, match="scritt|variat|estrane"):
        m.run(db, apply=True, backup_dir=tmp_path / "bk")
    assert _stato(db) == prima


def test_ricevuta_non_scrivibile_impedisce_commit(m, db, tmp_path, monkeypatch):
    def fallisce(*args, **kwargs):
        raise OSError("ricevuta non scrivibile")
    monkeypatch.setattr(m, "_scrivi_ricevuta", fallisce)
    prima = _stato(db)
    with pytest.raises(OSError, match="ricevuta"):
        m.run(db, apply=True, backup_dir=tmp_path / "bk")
    assert _stato(db) == prima


def test_drift_durante_restore_viene_rifiutato_nella_transazione(m, db, tmp_path, monkeypatch):
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    out = m.run(db, apply=True, backup_dir=tmp_path / "bk")
    backup = m._backup
    def concorrente(*args, **kwargs):
        result = backup(*args, **kwargs)
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO positions VALUES(2,25)")
        return result
    monkeypatch.setattr(m, "_backup", concorrente)
    with pytest.raises(ValueError, match="variat|drift"):
        m.ripristina(db, out["ricevuta"])
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT ora_convenzionale FROM trade_history WHERE id=1").fetchone() == (1,)
        assert conn.execute("SELECT amount FROM positions WHERE id=2").fetchone() == (25,)


def test_cli_dry_run_da_cartella_estranea_e_db_assente_non_creato(db, tmp_path):
    script = Path(__file__).parents[1] / "tools/migrations/marca_ora_convenzionale.py"
    environment = {**os.environ, "BELLOMBERG_DATA_DIR": str(tmp_path / "runtime"), "PYTHONUTF8": "1"}
    prima = _stato(db)
    proc = subprocess.run([sys.executable, str(script), "--db", str(db)], cwd=tmp_path,
                          env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["candidate"] == [1] and _stato(db) == prima
    missing = tmp_path / "mancante.db"
    proc = subprocess.run([sys.executable, str(script), "--db", str(missing)], cwd=tmp_path,
                          env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert proc.returncode == 1 and "error" in json.loads(proc.stdout)
    assert not missing.exists()
