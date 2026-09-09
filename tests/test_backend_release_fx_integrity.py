import json

import pandas as pd

from bellomberg.storage import memory_db
from bellomberg.storage.memory_db import MemoryDB


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


def _db_con_posizione(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps({"cash_disponibile_eur": 0}), encoding="utf-8")
    monkeypatch.setattr(memory_db, "PORTFOLIO_JSON_PATH", str(p))
    db = MemoryDB(db_path=str(tmp_path / "db.sqlite"),
                  chroma_path=str(tmp_path / "chroma"))
    monkeypatch.setattr(memory_db, "SQLITE_PATH", db.db_path)
    db.add_or_update_position("CADCO", quantita=10, prezzo_medio=100, valuta="CAD")
    db.update_price("CADCO", 100, valuta="CAD", source="test")
    return db


def test_summary_non_pubblica_totali_eur_se_fx_manca(tmp_path, monkeypatch):
    from bellomberg.cli import price_updater
    db = _db_con_posizione(tmp_path, monkeypatch)
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda _cur: None)
    monkeypatch.setattr(price_updater, "get_fx_sources", lambda: {})

    out = db.get_portfolio_summary()

    assert out["fx_incomplete"] == ["CADCO:CAD"]
    assert out["totale_valore_mercato_eur"] is None
    assert out["nav_total_eur"] is None


def test_snapshot_rifiuta_fx_statico_dichiarato(tmp_path, monkeypatch):
    import sqlite3
    from bellomberg.portfolio import twr_engine
    db = _db_con_posizione(tmp_path, monkeypatch)

    class FakeDB:
        db_path = db.db_path

        def get_portfolio_summary(self):
            return {
                "nav_total_eur": 920.0,
                "totale_valore_mercato_eur": 920.0,
                "cash_disponibile_eur": 0.0,
                "cash_source": "portfolio.json",
                "cash_source_note": None,
                "fx_incomplete": None,
                "fx_sources": {"CAD": "fallback"},
            }

    out = twr_engine.record_nav_snapshot(FakeDB())

    assert out["ok"] is False and "FX" in out["reason"] and "fallback" in out["reason"]
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM nav_snapshots").fetchone()[0] == 0


def test_nav_history_non_usa_fx_corrente_se_manca_la_serie(tmp_path, monkeypatch):
    import bellomberg.portfolio.portfolio_analytics as pa
    from bellomberg.cli import price_updater
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps({"cash_disponibile_eur": 0}), encoding="utf-8")
    monkeypatch.setattr(memory_db, "PORTFOLIO_JSON_PATH", str(p))
    monkeypatch.setattr(pa, "NUMPY_OK", True)
    monkeypatch.setattr(pa, "YF_OK", True)
    monkeypatch.setattr(pa, "_trade_history", lambda: [{
        "ticker": "USDCO", "action": "BUY", "quantita": 1,
        "prezzo": 100.0, "valuta": "USD", "data": "2020-01-02",
    }])
    idx = pd.to_datetime(["2020-01-02", "2020-01-03"])
    monkeypatch.setattr(pa, "_download_prices_for_history",
                        lambda *_a, **_k: pd.DataFrame({"USDCO": [100.0, 101.0]}, index=idx))
    monkeypatch.setattr(pa, "_build_fx_history", lambda *_a, **_k: pd.DataFrame())
    monkeypatch.setattr(price_updater, "get_fx_to_eur",
                        lambda _cur: (_ for _ in ()).throw(AssertionError("FX corrente usato")))
    pa._ANALYTICS_CACHE.clear()

    out = pa.compute_nav_history(force=True)

    assert "error" in out and "FX storico" in out["error"] and "USD" in out["error"]


def test_log_priming_dichiara_nav_nd_senza_formattare_none():
    from bellomberg.agents import consigliere_multi

    line = consigliere_multi._portfolio_priming_log({
        "n_positions": 2,
        "totale_valore_mercato_eur": None,
        "fx_incomplete": ["CADCO:CAD"],
    })

    assert "EUR n.d." in line and "CADCO:CAD" in line


def _summary_fx_incompleto():
    return {
        "fx_incomplete": ["CADCO:CAD"],
        "positions": [{"ticker": "CADCO", "valore_mercato": 1000, "valuta": "CAD"}],
    }


def test_risk_rifiuta_pesi_in_valute_miste(monkeypatch):
    import bellomberg.portfolio.portfolio_risk as mod
    monkeypatch.setattr(mod, "prezzi_speciali", lambda: {
        "origine": "file", "prezzi": {"senza_yfinance": frozenset()}})
    monkeypatch.setattr(mod, "MemoryDB",
                        lambda: type("DB", (), {"get_portfolio_summary": lambda self: _summary_fx_incompleto()})())
    mod.invalidate_cache()
    out = mod.compute_portfolio_risk(force=True)
    assert "error" in out and "FX incompleto" in out["error"]


def test_garch_rifiuta_pesi_in_valute_miste(monkeypatch):
    import bellomberg.portfolio.portfolio_garch as mod
    monkeypatch.setattr(mod, "MemoryDB",
                        lambda: type("DB", (), {"get_portfolio_summary": lambda self: _summary_fx_incompleto()})())
    returns, meta = mod._get_portfolio_returns(salta=frozenset())
    assert returns is None and "FX incompleto" in meta["error"]


def test_garch_non_serve_cache_sana_se_fx_diventa_incompleto(monkeypatch):
    import bellomberg.portfolio.portfolio_garch as mod
    stato = {"fx_incomplete": None}
    monkeypatch.setattr(mod, "MemoryDB", lambda: type("DB", (), {
        "get_portfolio_summary": lambda self: {
            "fx_incomplete": stato["fx_incomplete"], "positions": []}})())
    mod._CACHE.update({"data": {"ok": True, "cached": True}, "ts": __import__("time").time()})
    stato["fx_incomplete"] = ["CADCO:CAD"]

    out = mod.compute_portfolio_garch(force=False)

    assert "error" in out and "FX incompleto" in out["error"]


def test_factors_rifiuta_pesi_in_valute_miste(monkeypatch):
    import bellomberg.portfolio.portfolio_factors as mod
    monkeypatch.setattr(mod, "MemoryDB",
                        lambda: type("DB", (), {"get_portfolio_summary": lambda self: _summary_fx_incompleto()})())
    mod.invalidate_cache()
    out = mod.compute_portfolio_factors(force=True)
    assert "error" in out and "FX incompleto" in out["error"]


def test_montecarlo_rifiuta_pesi_in_valute_miste(monkeypatch):
    import bellomberg.portfolio.portfolio_montecarlo as mod
    monkeypatch.setattr(mod, "MemoryDB",
                        lambda: type("DB", (), {"get_portfolio_summary": lambda self: _summary_fx_incompleto()})())
    weights, total = mod._get_holdings_weights(frozenset())
    assert weights is None and total is None


def test_email_non_invia_se_un_allegato_diventa_illeggibile(tmp_path, monkeypatch):
    import builtins
    import bellomberg.reporting.email_sender as mod
    a = tmp_path / "memo.pdf"
    b = tmp_path / "appendix.pdf"
    a.write_bytes(b"memo")
    b.write_bytes(b"appendix")
    monkeypatch.setattr(mod, "email_configurata", lambda: True)
    real_open = builtins.open

    def flaky_open(path, *args, **kwargs):
        if str(path) == str(b):
            raise PermissionError("locked")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", flaky_open)
    called = []
    monkeypatch.setattr(mod.smtplib, "SMTP_SSL",
                        lambda *a, **k: called.append(True))

    out = mod.invia_email_multi_allegati([str(a), str(b)])

    assert out is False and called == []


def test_sector_exposure_non_calcola_pesi_con_fx_incompleto(monkeypatch):
    import bellomberg.portfolio.portfolio_sectors as ps
    monkeypatch.setattr(ps.cl, "carica_veicoli", lambda: {
        "veicoli": {}, "origine": "test", "motivo": None})
    out = ps.compute_sector_exposure(summary=_summary_fx_incompleto(),
                                     fetch=lambda _ticker: {"sector": "Test", "industry": "Test"})
    assert "FX incompleto" in out["error"]
    assert "by_sector" not in out


def test_sizing_non_usa_valore_nativo_se_fx_incompleto():
    import bellomberg.portfolio.sizing_engine as se
    params = {
        "base_single": .1, "cap_single": .12, "base_veicolo": .3,
        "cap_veicolo": .33, "cap_settore": .3, "limite_minimo": .02,
        "posizione_minima_pct": .5, "budget_stress_nav_pct": -25,
        "budget_var99_nav_pct": -4, "size_nuova_posizione": (.02, .04),
        "max_posizioni": None, "top3_max": None, "impronta": "test",
    }
    out = se.compute_sizing(_summary_fx_incompleto(), negozio={
        "veicoli": {}, "origine": "test", "motivo": None}, parametri=params)
    assert "FX incompleto" in out["error"]
    assert "summary" not in out
