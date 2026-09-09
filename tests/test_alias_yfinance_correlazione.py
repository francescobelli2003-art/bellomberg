# -*- coding: utf-8 -*-
"""Alias Yahoo e proxy di correlazione: solo negozio sintetico, zero rete/DB."""
import json

import pytest

import bellomberg.storage.negozi_privati as np_


def _negozio(tmp_path, contenuto, nome="alias.json"):
    p = tmp_path / nome
    p.write_text(contenuto if isinstance(contenuto, str) else json.dumps(contenuto),
                 encoding="utf-8")
    return str(p)


def test_schema_alias_accetta_le_due_sezioni_opzionali(tmp_path):
    p = _negozio(tmp_path, {
        "finnhub": {"ACME.MI": "ACM"},
        "sec": {"ACME": "ACM"},
        "yfinance": {"ALFA.FRA": "ALFA.DE"},
        "correlazione": {"ALFA.FRA": "ETFALFA"},
    })
    r = np_.carica_alias(p)
    assert r["motivo"] is None
    assert r["alias"]["yfinance"] == {"ALFA.FRA": "ALFA.DE"}
    assert r["alias"]["correlazione"] == {"ALFA.FRA": "ETFALFA"}


def test_alias_privato_non_puo_ridefinire_un_canonico_pubblico(tmp_path):
    p = _negozio(tmp_path, {"yfinance": {"BTC": "ALTRO-USD"}})
    r = np_.carica_alias(p)
    assert r["origine"] == "illeggibile" and "canonica pubblica" in r["motivo"]


def test_data_ticker_rilegge_il_negozio_e_preserva_i_canonici_pubblici(
        tmp_path, monkeypatch):
    import bellomberg.cli.price_updater as pu

    p = _negozio(tmp_path, {"yfinance": {"ALFA.FRA": "ALFA.DE"}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)
    assert pu.data_ticker("ALFA.FRA") == "ALFA.DE"
    assert pu.YFINANCE_PROXY_MAP.get("ALFA.FRA") == "ALFA.DE"

    _negozio(tmp_path, {"yfinance": {"ALFA.FRA": "ALFA.SW"}})
    assert pu.data_ticker("ALFA.FRA") == "ALFA.SW"
    assert pu.YFINANCE_PROXY_MAP.get("ALFA.FRA") == "ALFA.SW"

    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(tmp_path / "manca.json"))
    assert [pu.data_ticker(x) for x in ("BTC", "ETH", "SOL")] == [
        "BTC-USD", "ETH-USD", "SOL-USD"]

    p = _negozio(tmp_path, {"yfinance": {}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)
    assert pu.data_ticker("PLAIN") == "PLAIN"


@pytest.mark.parametrize("ticker", ["ALFA.FRA", "ALFA.DE", "ALFA"])
@pytest.mark.parametrize("stato", ["assente", "illeggibile"])
def test_data_ticker_negozio_non_leggibile_e_un_errore_dichiarato(
        tmp_path, monkeypatch, stato, ticker):
    import bellomberg.cli.price_updater as pu

    p = tmp_path / "alias.json"
    if stato == "illeggibile":
        p.write_text("{", encoding="utf-8")
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(p))
    with pytest.raises(pu.AliasFontiError) as exc:
        pu.data_ticker(ticker)
    assert stato in str(exc.value).lower() and "alias_fonti" in str(exc.value)


def test_data_ticker_fra_senza_voce_nel_negozio_e_mancante_esplicito(
        tmp_path, monkeypatch):
    import bellomberg.cli.price_updater as pu

    p = _negozio(tmp_path, {"yfinance": {}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)
    with pytest.raises(pu.AliasFontiError) as exc:
        pu.data_ticker("ALFA.FRA")
    assert "mancante" in str(exc.value).lower() and "ALFA.FRA" in str(exc.value)


def test_price_updater_dichiara_il_guasto_alias_sul_ticker(tmp_path, monkeypatch, capsys):
    import bellomberg.cli.price_updater as pu

    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(tmp_path / "manca.json"))
    monkeypatch.setattr(pu, "YFINANCE_AVAILABLE", True)
    monkeypatch.setattr(pu, "yf", type("YF", (), {
        "Ticker": staticmethod(lambda _: (_ for _ in ()).throw(
            AssertionError("yfinance non va chiamato dopo il guasto del negozio")))
    })())
    monkeypatch.setattr(pu, "prezzi_speciali", lambda: {
        "prezzi": {"coingecko": {}}, "origine": "fixture", "motivo": None})

    class DB:
        def get_portfolio_summary(self):
            return {"positions": [{"ticker": "ALFA.FRA", "valuta": None}]}

        def update_price(self, *args, **kwargs):
            raise AssertionError("un alias irrisolto non deve scrivere")

    out = pu.update_all_prices(DB(), source_order=("yfinance",), verbose=False)
    assert out["updated"] == 0 and out["failed"] == 1
    assert "alias_fonti" in out["details"][0]["error"]
    assert "ALIAS YFINANCE KO" in capsys.readouterr().out


def test_agent_tool_non_ripiega_sul_ticker_se_il_negozio_alias_manca(
        tmp_path, monkeypatch):
    import bellomberg.agents.agent_tools as at

    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(tmp_path / "manca.json"))
    monkeypatch.setattr(at, "YFINANCE_AVAILABLE", True)

    class YF:
        @staticmethod
        def Ticker(_):
            raise AssertionError("yfinance non va chiamato dopo il guasto del negozio")

    monkeypatch.setattr(at, "yf", YF())
    out = at.tool_get_market_data("ALFA.FRA")
    assert "alias_fonti" in out["error"] and "assente" in out["error"].lower()

    confronto = at.tool_compare_assets(["ALFA.FRA"])
    errore = confronto["comparison"][0]["error"]
    assert "alias_fonti" in errore and "assente" in errore.lower()


def test_download_e_rename_usano_la_stessa_istantanea_alias(tmp_path, monkeypatch):
    import pandas as pd
    import bellomberg.portfolio.portfolio_analytics as pa

    p = _negozio(tmp_path, {"yfinance": {"ALFA.FRA": "PROXYA"}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)

    def download(tickers, **_):
        assert tickers == ["PROXYA"]
        _negozio(tmp_path, {"yfinance": {"ALFA.FRA": "PROXYB"}})
        colonne = pd.MultiIndex.from_tuples([("Close", "PROXYA")])
        return pd.DataFrame([[10.0], [11.0]], columns=colonne,
                            index=pd.to_datetime(["2026-01-02", "2026-01-05"]))

    monkeypatch.setattr(pa.yf, "download", download)
    prezzi = pa._download_prices_for_history(
        ["ALFA.FRA"], "2026-01-01", "2026-01-06", frozenset())
    assert list(prezzi.columns) == ["ALFA.FRA"]


def test_montecarlo_non_rilegge_alias_dopo_il_download(tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd
    import bellomberg.portfolio.portfolio_montecarlo as pm

    p = _negozio(tmp_path, {"yfinance": {"ALFA.FRA": "PROXYA"}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)

    def download(tickers, **_):
        assert tickers == ["PROXYA"]
        _negozio(tmp_path, {"yfinance": {"ALFA.FRA": "PROXYB"}})
        colonne = pd.MultiIndex.from_tuples([("Close", "PROXYA")])
        valori = 100.0 * np.cumprod(np.full(90, 1.001))
        return pd.DataFrame(valori, columns=colonne,
                            index=pd.bdate_range("2026-01-02", periods=len(valori)))

    monkeypatch.setattr(pm.yf, "download", download)
    rendimenti = pm._download_returns(["ALFA.FRA"], years=1)
    assert list(rendimenti.columns) == ["ALFA.FRA"]


def test_correlazione_rilegge_proxy_e_riporta_le_etichette_reali(
        tmp_path, monkeypatch):
    import bellomberg.agents.consigliere_multi as cm

    p = _negozio(tmp_path, {"correlazione": {"ALFA.MI": "PROXYA"}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)
    chiamate = []

    def tool(_, tickers, period):
        chiamate.append(list(tickers))
        proxy = tickers[0]
        return {"matrix": {proxy: {proxy: 1.0, "BETA": 0.2},
                           "BETA": {proxy: 0.2, "BETA": 1.0}}}

    monkeypatch.setattr(cm, "tool_quant_compute", tool)
    posizioni = [{"ticker": "ALFA.MI", "peso_pct": 60},
                 {"ticker": "BETA", "peso_pct": 40}]
    assert "ALFA.MI" in cm._try_correlation_matrix(posizioni)
    assert chiamate[-1] == ["PROXYA", "BETA"]

    _negozio(tmp_path, {"correlazione": {"ALFA.MI": "PROXYB"}})
    assert "ALFA.MI" in cm._try_correlation_matrix(posizioni)
    assert chiamate[-1] == ["PROXYB", "BETA"]


@pytest.mark.parametrize("contenuto,posizioni", [
    ({"correlazione": {"ALFA": "PROXY", "BETA": "PROXY"}},
     [{"ticker": "ALFA", "peso_pct": 60}, {"ticker": "BETA", "peso_pct": 40}]),
    ({"correlazione": {"ALFA": "BETA"}},
     [{"ticker": "ALFA", "peso_pct": 60}, {"ticker": "BETA", "peso_pct": 40}]),
])
def test_correlazione_rifiuta_reverse_mapping_ambiguo(
        tmp_path, monkeypatch, contenuto, posizioni):
    import bellomberg.agents.consigliere_multi as cm

    p = _negozio(tmp_path, contenuto)
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)
    chiamate = []
    log = []
    monkeypatch.setattr(cm, "tool_quant_compute", lambda *a, **k: chiamate.append(k))
    monkeypatch.setattr(cm, "_log", log.append)
    assert cm._try_correlation_matrix(posizioni) is None
    assert not chiamate
    assert any("ambigu" in x.lower() or "collision" in x.lower() for x in log)


@pytest.mark.parametrize("contenuto,stato", [(None, "assente"), ("{", "illeggibile")])
def test_correlazione_negozio_guasto_e_dichiarato_senza_chiamare_il_tool(
        tmp_path, monkeypatch, contenuto, stato):
    import bellomberg.agents.consigliere_multi as cm

    p = tmp_path / "alias.json"
    if contenuto is not None:
        p.write_text(contenuto, encoding="utf-8")
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(p))
    chiamate = []
    log = []
    monkeypatch.setattr(cm, "tool_quant_compute", lambda *a, **k: chiamate.append(k))
    monkeypatch.setattr(cm, "_log", log.append)
    assert cm._try_correlation_matrix([{"ticker": "ALFA", "peso_pct": 100}]) is None
    assert not chiamate
    assert any(stato in x.lower() and "alias_fonti" in x for x in log)


def _prezzi_validi_vuoti():
    return {"prezzi": {"senza_yfinance": frozenset(), "coingecko": {}},
            "origine": "fixture", "motivo": None}


def test_risk_rifiuta_alias_che_collide_col_benchmark_prima_del_download(
        tmp_path, monkeypatch):
    import bellomberg.portfolio.portfolio_risk as pr

    p = _negozio(tmp_path, {"yfinance": {"ALFA": "SPY"}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)
    monkeypatch.setattr(pr, "prezzi_speciali", _prezzi_validi_vuoti)
    monkeypatch.setattr(pr, "MemoryDB", lambda: type("DB", (), {
        "get_portfolio_summary": lambda self: {"positions": [{
            "ticker": "ALFA", "peso_pct": 100, "valore_mercato": 1000,
            "valuta": "EUR"}]}})())
    chiamate = []
    monkeypatch.setattr(pr.yf, "download", lambda *a, **k: chiamate.append((a, k)))

    out = pr.compute_portfolio_risk(force=True)
    assert not chiamate
    assert "alias" in out["error"].lower() and "SPY" in out["error"]


def test_payload_pubblici_conservano_la_causa_del_negozio_alias_assente(
        tmp_path, monkeypatch):
    import bellomberg.portfolio.portfolio_analytics as pa
    import bellomberg.portfolio.portfolio_garch as pg
    import bellomberg.portfolio.portfolio_montecarlo as pm

    monkeypatch.setattr(np_, "PERCORSO_ALIAS", str(tmp_path / "manca.json"))
    monkeypatch.setattr(pa, "prezzi_speciali", _prezzi_validi_vuoti)
    monkeypatch.setattr(pa, "_trade_history", lambda: [
        {"ticker": "ALFA", "data": "2026-01-02"}])
    monkeypatch.setattr(pa, "_build_position_timeline", lambda _: {"ALFA": []})

    class Valuta:
        valore = "EUR"
        dichiarazione = "fixture"

        def as_dict(self):
            return {"valore": self.valore, "dichiarazione": self.dichiarazione}

    monkeypatch.setattr(pa, "_currency_labels_for_tickers",
                        lambda *a, **k: {"ALFA": Valuta()})

    monkeypatch.setattr(pg, "prezzi_speciali", _prezzi_validi_vuoti)
    monkeypatch.setattr(pg, "MemoryDB", lambda: type("DB", (), {
        "get_portfolio_summary": lambda self: {"positions": [{
            "ticker": "ALFA", "valore_mercato": 1000, "valuta": "EUR"}]}})())

    monkeypatch.setattr(pm, "prezzi_speciali", _prezzi_validi_vuoti)
    pm._CACHE.clear()

    risultati = [
        pa.compute_nav_history(force=True),
        pg.compute_portfolio_garch(force=True),
        pm.run_monte_carlo(force_refresh=True, method="block_bootstrap",
                           _override_weights={"ALFA": 0.5, "BETA": 0.5},
                           _override_nav=1000),
    ]
    assert all("alias_fonti assente" in x.get("error", "") for x in risultati), risultati


def test_stress_cache_include_alias_e_non_maschera_un_guasto_successivo(
        tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd
    import bellomberg.portfolio.portfolio_montecarlo as pm

    alias_path = _negozio(tmp_path, {"yfinance": {"ALFA": "PROXYA"}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", alias_path)
    monkeypatch.setattr(pm, "STRESS_CACHE_DIR", str(tmp_path / "stress-cache"))
    monkeypatch.setattr(pm, "_spy_recent_returns_2y",
                        lambda: pd.Series(dtype=float))
    chiamate = []

    def download(tickers, **_):
        chiamate.append(list(tickers))
        proxy = next(t for t in tickers if t != "SPY")
        idx = pd.bdate_range("2008-09-01", periods=30)
        cols = pd.MultiIndex.from_tuples([("Close", proxy), ("Close", "SPY")])
        valori = np.column_stack((np.linspace(100, 90, len(idx)),
                                  np.linspace(100, 85, len(idx))))
        return pd.DataFrame(valori, index=idx, columns=cols)

    monkeypatch.setattr(pm.yf, "download", download)
    primo, _ = pm._stress_window_returns(["ALFA"], "gfc_2008", None)
    assert primo is not None and chiamate == [["PROXYA", "SPY"]]

    _negozio(tmp_path, {"yfinance": {"ALFA": "PROXYB"}})
    secondo, _ = pm._stress_window_returns(["ALFA"], "gfc_2008", None)
    assert secondo is not None and chiamate[-1] == ["PROXYB", "SPY"]

    (tmp_path / "alias.json").write_text("{", encoding="utf-8")
    terzo, motivo = pm._stress_window_returns(["ALFA"], "gfc_2008", None)
    assert terzo is None and "alias_fonti illeggibile" in motivo


def test_stress_rifiuta_collisione_benchmark_ma_ammette_spy_reale(
        tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd
    import bellomberg.portfolio.portfolio_montecarlo as pm

    p = _negozio(tmp_path, {"yfinance": {"ALFA": "SPY"}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)
    monkeypatch.setattr(pm, "STRESS_CACHE_DIR", str(tmp_path / "stress-cache"))
    chiamate = []
    monkeypatch.setattr(pm.yf, "download", lambda *a, **k: chiamate.append((a, k)))
    out, motivo = pm._stress_window_returns(["ALFA"], "gfc_2008", None)
    assert out is None and not chiamate
    assert "alias" in motivo.lower() and "SPY" in motivo

    _negozio(tmp_path, {"yfinance": {"SPY": "ALTRO"}})
    out, motivo = pm._stress_window_returns(["SPY"], "gfc_2008", None)
    assert out is None and not chiamate
    assert "alias" in motivo.lower() and "SPY" in motivo

    _negozio(tmp_path, {"yfinance": {}})
    idx = pd.bdate_range("2008-09-01", periods=30)
    close = pd.DataFrame(np.linspace(100, 85, len(idx)), index=idx,
                         columns=pd.MultiIndex.from_tuples([("Close", "SPY")]))
    monkeypatch.setattr(pm.yf, "download", lambda *a, **k: close.copy())
    monkeypatch.setattr(pm, "_spy_recent_returns_2y",
                        lambda: pd.Series(dtype=float))
    sano, _ = pm._stress_window_returns(["SPY"], "gfc_2008", None)
    assert sano is not None and "SPY" in sano.columns


def test_mapper_non_puo_rimappare_un_simbolo_riservato(tmp_path, monkeypatch):
    import bellomberg.cli.price_updater as pu

    p = _negozio(tmp_path, {"yfinance": {"SPY": "ALTRO"}})
    monkeypatch.setattr(np_, "PERCORSO_ALIAS", p)
    with pytest.raises(pu.AliasFontiError) as exc:
        pu.data_ticker_map(["SPY"], riservati=("SPY",))
    assert "SPY" in str(exc.value) and "riservato" in str(exc.value).lower()
