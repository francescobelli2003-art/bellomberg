import importlib.util
import json
from pathlib import Path
import threading

import pytest

from bellomberg.storage import memory_db


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", _no_chroma)
    return memory_db.MemoryDB(str(tmp_path / "data" / "test.db"),
                              str(tmp_path / "chroma"))


def _state(db):
    with db._conn() as conn:
        cash = conn.execute("SELECT balance_cents FROM cash_state WHERE singleton_id=1").fetchone()
        trades = conn.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0]
        moves = conn.execute("SELECT COUNT(*) FROM cash_movements").fetchone()[0]
        positions = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    return (cash[0] if cash else None, trades, moves, positions)


def test_cash_assente_e_dichiarata(db):
    out = memory_db.read_cash_state(db.db_path)
    assert out["cash_eur"] == 0 and out["cash_source"] is None
    assert "non inizializzata" in out["cash_source_note"]


def test_primo_deposito_inizializza_esplicitamente(db):
    out = db.apply_cash_movement("DEPOSIT", 123.45, data="2026-09-09")
    assert out["cash_eur"] == 123.45
    assert memory_db.read_cash_state(db.db_path)["cash_source"] == "sqlite:cash_state"


def test_prelievo_non_inizializzato_non_scrive(db):
    before = _state(db)
    with pytest.raises(memory_db.CashNotInitialized):
        db.apply_cash_movement("WITHDRAWAL", 1)
    assert _state(db) == before


def test_errore_dopo_insert_movimento_fa_rollback(db, monkeypatch):
    db.apply_cash_movement("DEPOSIT", 100)
    before = _state(db)
    original = db.log_cash_movement

    def boom(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fault dopo insert")

    monkeypatch.setattr(db, "log_cash_movement", boom)
    with pytest.raises(RuntimeError, match="fault"):
        db.apply_cash_movement("DEPOSIT", 50)
    assert _state(db) == before


def test_errore_dopo_insert_trade_fa_rollback(db, monkeypatch):
    db.apply_cash_movement("DEPOSIT", 1000)
    before = _state(db)
    original = db.log_trade

    def boom(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fault dopo trade")

    monkeypatch.setattr(db, "log_trade", boom)
    with pytest.raises(RuntimeError, match="fault"):
        db.execute_trade(cash_delta_cents=-10000, ticker="AAA.MI", action="BUY",
                         quantita=10, prezzo=10, valuta="EUR")
    assert _state(db) == before


@pytest.mark.parametrize("operation", ["movement", "trade"])
def test_trigger_sqlite_dopo_ledger_forza_rollback_totale(db, operation):
    db.apply_cash_movement("DEPOSIT", 1000)
    before = _state(db)
    with db._conn() as conn:
        conn.execute("""CREATE TRIGGER cash_fault BEFORE UPDATE ON cash_state
                     BEGIN SELECT RAISE(ABORT, 'fault cash_state'); END""")
    with pytest.raises(Exception, match="fault cash_state"):
        if operation == "movement":
            db.apply_cash_movement("DEPOSIT", 50)
        else:
            db.execute_trade(cash_delta_cents=-1000, ticker="AAA.MI", action="BUY",
                             quantita=1, prezzo=10, valuta="EUR")
    assert _state(db) == before


def test_log_trade_storico_non_muove_cassa(db):
    db.apply_cash_movement("DEPOSIT", 1000)
    before = _state(db)[0]
    db.log_trade("AAA.MI", "BUY", 10, 10, "EUR")
    assert _state(db)[0] == before


def test_endpoint_cash_usa_ledger_atomico(db, monkeypatch):
    import bellomberg.api.bellomberg_api as api
    monkeypatch.setattr(api, "get_db", lambda: db)
    out = api.post_cash_movement(api.CashMovementIn(
        tipo="DEPOSIT", importo_eur=25, data="2026-09-09"))
    assert out["cash_disponibile_eur"] == 25
    assert out["cash_source"] == "sqlite:cash_state"


def test_endpoint_cash_non_tocca_filesystem(db, monkeypatch):
    import bellomberg.api.bellomberg_api as api
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api.os, "replace", lambda *_: (_ for _ in ()).throw(
        AssertionError("os.replace non deve essere chiamato")))
    out = api.post_cash_movement(api.CashMovementIn(tipo="DEPOSIT", importo_eur=10))
    assert out["cash_disponibile_eur"] == 10


def test_endpoint_trade_senza_saldo_non_scrive(db, monkeypatch):
    import bellomberg.api.bellomberg_api as api
    from bellomberg.cli import price_updater
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(price_updater, "get_fx_to_eur_con_fonte",
                        lambda _currency: (1.0, "live"))
    before = _state(db)
    with pytest.raises(api.HTTPException) as exc:
        api.log_trade(api.TradeIn(
            ticker="AAA.MI", action="BUY", quantita=1, prezzo=10, valuta="EUR"))
    assert exc.value.status_code == 503
    assert _state(db) == before


def test_endpoint_trade_sub_cent_dichiara_arrotondamento(db, monkeypatch):
    import bellomberg.api.bellomberg_api as api
    from bellomberg.cli import price_updater
    db.apply_cash_movement("DEPOSIT", 10)
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(price_updater, "get_fx_to_eur_con_fonte",
                        lambda _currency: (1.0, "live"))
    before = _state(db)
    out = api.log_trade(api.TradeIn(
        ticker="AAA.MI", action="BUY", quantita=1, prezzo=0.001, valuta="EUR"))
    after = _state(db)
    assert after[0] == before[0] and after[1] == before[1] + 1
    assert "arrotondato" in out["cash_note"]


@pytest.mark.parametrize("fx", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0])
def test_endpoint_trade_fx_non_finito_non_scrive(db, monkeypatch, fx):
    import bellomberg.api.bellomberg_api as api
    from bellomberg.cli import price_updater
    db.apply_cash_movement("DEPOSIT", 10)
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(price_updater, "get_fx_to_eur_con_fonte",
                        lambda _currency: (fx, "live"))
    before = _state(db)
    with pytest.raises(api.HTTPException) as exc:
        api.log_trade(api.TradeIn(
            ticker="AAA.MI", action="BUY", quantita=1, prezzo=1, valuta="EUR"))
    assert exc.value.status_code == 422 and _state(db) == before


def test_endpoint_trade_rifiuta_fx_fallback_senza_scrivere(db, monkeypatch):
    import bellomberg.api.bellomberg_api as api
    from bellomberg.cli import price_updater
    db.apply_cash_movement("DEPOSIT", 10)
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(price_updater, "get_fx_to_eur_con_fonte",
                        lambda _currency: (0.92, "fallback"))
    before = _state(db)
    with pytest.raises(api.HTTPException) as exc:
        api.log_trade(api.TradeIn(
            ticker="AAA", action="BUY", quantita=1, prezzo=1, valuta="USD"))
    assert exc.value.status_code == 503
    assert "fonte fallback" in exc.value.detail
    assert _state(db) == before


def test_buy_puo_portare_cassa_negativa_politica_esistente(db):
    db.apply_cash_movement("DEPOSIT", 10)
    out = db.execute_trade(cash_delta_cents=-10000, ticker="AAA.MI", action="BUY",
                           quantita=1, prezzo=100, valuta="EUR")
    assert out["cash_eur"] == -90 and _state(db)[0] == -9000


def test_execute_trade_non_converte_i_centesimi_sqlite_in_float(db):
    original = 9_007_199_254_740_993
    with db._conn() as conn:
        conn.execute("INSERT INTO cash_state(singleton_id,balance_cents,updated_at,source) "
                     "VALUES(1,?,'2026-09-09','test')", (original,))
    db.execute_trade(cash_delta_cents=1, ticker="SYN", action="BUY",
                     quantita=1, prezzo=1, valuta="EUR")
    assert _state(db)[0] == original + 1


def test_movimento_non_converte_i_centesimi_sqlite_in_float(db):
    original = 9_007_199_254_740_993
    with db._conn() as conn:
        conn.execute("INSERT INTO cash_state(singleton_id,balance_cents,updated_at,source) "
                     "VALUES(1,?,'2026-09-09','test')", (original,))
    db.apply_cash_movement("DEPOSIT", 0.01)
    assert _state(db)[0] == original + 1


def test_due_prelievi_concorrenti_non_spendono_due_volte(db):
    db.apply_cash_movement("DEPOSIT", 100)
    barrier = threading.Barrier(2)
    outcomes = []

    def worker():
        other = memory_db.MemoryDB(db_path=db.db_path, chroma_path=db.chroma_path)
        barrier.wait()
        try:
            other.apply_cash_movement("WITHDRAWAL", 80, conferma_duplicato=True)
            outcomes.append("ok")
        except ValueError:
            outcomes.append("guard")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["guard", "ok"]
    assert _state(db)[0] == 2000
    with db._conn() as conn:
        assert conn.execute("SELECT version FROM cash_state WHERE singleton_id=1").fetchone()[0] == 2


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 0.001])
def test_movimento_non_finito_non_scrive(db, bad):
    before = _state(db)
    with pytest.raises(ValueError, match="finito"):
        db.apply_cash_movement("DEPOSIT", bad)
    assert _state(db) == before


def _migration_module():
    path = Path(__file__).parents[1] / "tools" / "migrations" / "migra_cassa_sqlite.py"
    spec = importlib.util.spec_from_file_location("migra_cassa_sqlite", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migrazione_dry_run_e_apply_con_backup(tmp_path, monkeypatch):
    module = _migration_module()
    source = tmp_path / "portfolio.json"
    source.write_text(json.dumps({"cash_disponibile_eur": "123.45"}), encoding="utf-8")
    db_path = tmp_path / "cash.db"
    first = memory_db.MemoryDB(db_path=str(db_path), chroma_path=str(tmp_path / "chroma"))
    dry = module.run(source, db_path, apply=False)
    assert dry["balance_cents"] == 12345 and _state(first)[0] is None
    monkeypatch.setattr(module, "_porta_occupata", lambda: False)
    applied = module.run(source, db_path, apply=True)
    assert Path(applied["db_backup"]).exists() and Path(applied["json_backup"]).exists()
    assert memory_db.read_cash_state(str(db_path))["cash_eur"] == 123.45
    with pytest.raises(RuntimeError, match="gia' inizializzata"):
        module.run(source, db_path, apply=True)


def test_migrazione_dry_run_su_db_pre_schema_non_scrive(tmp_path):
    import sqlite3
    module = _migration_module()
    source = tmp_path / "portfolio.json"
    source.write_text('{"cash_disponibile_eur":-12.34}', encoding="utf-8")
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE legacy(id INTEGER)")
    before = db_path.read_bytes()
    out = module.run(source, db_path, apply=False)
    assert out["balance_cents"] == -1234 and db_path.read_bytes() == before
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='cash_state'").fetchone() is None


def test_migrazione_rifiuta_chiave_json_duplicata(tmp_path):
    import sqlite3
    module = _migration_module()
    source = tmp_path / "portfolio.json"
    source.write_text('{"cash_disponibile_eur":1,"cash_disponibile_eur":2}', encoding="utf-8")
    db_path = tmp_path / "old.db"
    sqlite3.connect(db_path).close()
    with pytest.raises(ValueError, match="duplicata"):
        module.run(source, db_path, apply=False)
