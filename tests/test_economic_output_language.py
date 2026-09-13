"""Real response builders, cached values and exact language changes, offline."""
from copy import deepcopy

import pytest

from bellomberg.core.language import language_context
from bellomberg.storage import memory_db, preferences


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("engine", ["operating", "managed_care"])
def test_nonfinite_snapshot_hash_and_gate_are_language_independent(engine, nonfinite):
    from bellomberg.valuation.sector_analysis import _hash
    from test_dcf_quality import inputs, assess
    from test_managed_care import documented_case, project

    if engine == "operating":
        args = inputs()
        args[1]["base"]["gross_margin"][2] = nonfinite
        args[2]["base"]["gross_margin"][2] = nonfinite
        build = lambda: assess(args)
    else:
        case, context = documented_case()
        case["scenarios"]["base"]["interest_expense"][2] = nonfinite
        build = lambda: project(case, context)

    with language_context("it"):
        italian = build()
    with language_context("en"):
        english = build()
    quality_it = italian if engine == "operating" else italian["analytical_quality"]
    quality_en = english if engine == "operating" else english["analytical_quality"]
    # Use the production canonical hash, which also rejects invalid JSON numbers.
    assert _hash(quality_it["snapshot"]) == _hash(quality_en["snapshot"])
    assert quality_it["snapshot"] == quality_en["snapshot"]
    assert quality_it["status"] == quality_en["status"] == "INCOMPLETA"
    assert quality_it["revision_bridge"]["status"] == quality_en["revision_bridge"]["status"] == "n.d."
    if engine == "managed_care":
        assert italian["scenarios"]["base"]["status"] == english["scenarios"]["base"]["status"] == "n.d."
        assert italian["scenarios"]["base"]["fair_value_per_share"] is None
        assert english["scenarios"]["base"]["fair_value_per_share"] is None


def test_valuation_gate_language_preserves_source_identity_and_fair_values(db):
    import json
    from test_sector_usability import payload_for
    from bellomberg.valuation import dcf_quality as quality
    from bellomberg.core.presentation import render_payload
    with language_context("it"):
        source = payload_for()
        source["original_note"] = "Fonte originale italiana"
        source_json = json.dumps(source, sort_keys=True)
        valid_it = quality.normalize_valuation_payload(source)
    with language_context("en"):
        valid_en = quality.normalize_valuation_payload(source)
    assert valid_it == valid_en
    assert valid_en["fair_value_weighted"] == 120
    assert valid_en["valuation_usability"]["usable"] is True
    assert json.dumps(source, sort_keys=True) == source_json
    damaged = deepcopy(source)
    damaged["generation_id"] = None
    with language_context("it"):
        refused_it = quality.normalize_valuation_payload(damaged)
    with language_context("en"):
        refused_en = quality.normalize_valuation_payload(damaged)
        relabeled = render_payload(refused_it["valuation_usability"])
    assert refused_en["fair_value_weighted"] is None
    assert refused_en["valuation_usability"] == relabeled
    assert any("generation provenance cannot be verified" in reason for reason in relabeled["reasons"])
    assert refused_en["valuation_decision"] == source["valuation_decision"]
    assert refused_en["acquisition_snapshot"] == source["acquisition_snapshot"]
    assert refused_en["original_note"] == "Fonte originale italiana"


def test_registry_presentation_is_additive_complete_and_read_only(db):
    import json
    from bellomberg.valuation import method_registry as registry
    from bellomberg.core.presentation import render_payload
    before = json.dumps(registry._METHODS, sort_keys=True)
    for method_id, method in registry._METHODS.items():
        decision = {"method_id": method_id, "support_status": method["support_status"],
                    "rationale": "Tesi originale del PM"}
        with language_context("it"):
            italian = registry.method_presentation(decision)
        with language_context("en"):
            english = registry.method_presentation(decision)
            relabeled = render_payload(italian)
        assert english == relabeled
        assert english["requirements_display"] == registry.get_method_requirements(method_id)
        assert english["decision_display"]["method_rationale"] == method["rationale"]
        assert italian["decision_display"]["method_rationale"] != method["rationale"]
        for translated, original in zip(italian["requirements_display"]["fields"], english["requirements_display"]["fields"]):
            assert translated["field"] == original["field"]
            assert translated["required"] is original["required"]
            assert translated["description"] != original["description"]
        assert decision["rationale"] == "Tesi originale del PM"
    assert json.dumps(registry._METHODS, sort_keys=True) == before
    assert registry.method_presentation({"method_id": "missing"})["requirements_display"] is None


def test_valuation_endpoint_adds_current_labels_without_rewriting_sidecar(db, tmp_path):
    from test_sector_valuation_api import endpoint, write_model
    from test_sector_usability import payload_for
    with language_context("it"):
        source = payload_for()
        path = write_model(tmp_path, source)
        italian = endpoint(tmp_path)["models"][0]
    before = path.with_suffix(".payload.json").read_bytes()
    with language_context("en"):
        english = endpoint(tmp_path)["models"][0]
    assert english["presentation"]["decision_display"]["method_rationale"].startswith("Operating cash generation")
    assert italian["presentation"]["decision_display"]["method_rationale"].startswith("Generazione di cassa")
    assert english["valuation_decision"] == italian["valuation_decision"] == source["valuation_decision"]
    for key in ("fair_value", "snapshot_id", "generation_id", "generated_at", "valuation_usability"):
        assert english[key] == italian[key]
    assert path.with_suffix(".payload.json").read_bytes() == before


def test_portfolio_summary_authored_notes_relabel_with_exact_accounting(db, monkeypatch):
    from bellomberg.core.presentation import render_payload
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda *_: pytest.fail("EUR needs no FX fetch"))
    db.log_trade("SYNTH.MI", "BUY", 10, 10., valuta="EUR", data="2001-01-01")
    with language_context("it"):
        italian = db.get_portfolio_summary()
    with language_context("en"):
        english = db.get_portfolio_summary()
        relabeled = render_payload(italian)
    assert english["positions"][0]["price_source"].startswith("prezzo_medio (DECLARED fallback")
    assert english["positions"][0]["fx_pl_note"] == "Live price or market value n/a"
    assert english["totale_aperto_piu_realizzato_note"].startswith("open P&L")
    assert english["source"] == "SQLite database (live, multi-currency normalized to EUR)"
    for key in ("positions", "fx_pl_basis", "totale_aperto_piu_realizzato_note", "source"):
        assert english[key] == relabeled[key]
    assert english["nav_total_eur"] == italian["nav_total_eur"] == 100
    assert english["totale_aperto_piu_realizzato_eur"] is None


@pytest.mark.parametrize("language", ["it", "en"])
def test_thesis_missing_route_and_confirmation_codes_do_not_depend_on_text(db, language):
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    from fastapi import HTTPException
    from bellomberg.core.language import text
    source = Path("src/bellomberg/api/bellomberg_api.py").read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == "put_tesi_posizione")
    node.decorator_list = []
    namespace = {"HTTPException": HTTPException, "TesiIn": SimpleNamespace,
                 "get_db": lambda: db, "_api_text": text}
    coded = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ClassDef) and n.name == "_CodedHTTPException")
    exec(compile(ast.Module(body=[coded, node], type_ignores=[]), "thesis-endpoint", "exec"), namespace)
    body = SimpleNamespace(tesi="New original text", conferma=False, autore="test")
    with language_context(language):
        with pytest.raises(HTTPException) as refusal:
            namespace["put_tesi_posizione"]("MISSING", body)
        assert refusal.value.status_code == 404
        assert refusal.value.code == "thesis_position_missing"
        original = "Tesi originale del PM con addendum e fonti conservati. " * 10
        db.add_or_update_position("SYNTH", quantita=10, prezzo_medio=10, valuta="EUR", tesi=original)
        with db._conn() as conn:
            before = list(conn.iterdump())
        for value, code in [("", "thesis_empty_confirmation"), ("Breve", "thesis_shortening_confirmation")]:
            body.tesi = value
            with pytest.raises(HTTPException) as refusal:
                namespace["put_tesi_posizione"]("SYNTH", body)
            assert refusal.value.status_code == 422
            assert refusal.value.code == code
            if language == "en":
                assert refusal.value.detail.startswith("THESIS GUARD")
        with db._conn() as conn:
            assert list(conn.iterdump()) == before
        assert not Path(db._tesi_history_path()).exists()
        body.conferma = True
        written = namespace["put_tesi_posizione"]("SYNTH", body)
        assert written["scritture"] == 1
        assert db.get_tesi("SYNTH")["storico"][0]["tesi"] == original


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_db.MemoryDB, "_init_chroma", lambda self: None)
    monkeypatch.setattr(preferences, "PREFERENCES_PATH", tmp_path / "preferences.json")
    return memory_db.MemoryDB(str(tmp_path / "economic.db"), str(tmp_path / "chroma"))


def test_twr_cache_roundtrip_changes_only_authored_presentation(db, monkeypatch):
    from bellomberg.portfolio import twr_engine as twr
    from bellomberg.market_data import market_inputs
    twr.invalidate_cache()
    counts = {"series": 0, "risk_free": 0, "summary": 0}
    ctx = {"dates": ["2001-01-02", "2001-01-03"], "values_eur": [100, 110], "flows_eur": [0, 0],
           "regimes": ["official", "official"], "official_since": "2001-01-02", "notes": [],
           "snapshots": [{"date": "2001-01-02", "nav_total_eur": 100, "created_at": "2001-01-02T12:00:00"},
                         {"date": "2001-01-03", "nav_total_eur": 110, "created_at": "2001-01-03T12:00:00"}],
           "ledger": [{"date": "2001-01-01", "type": "DEPOSIT", "amount_eur": 100, "note": "Deposito originale"}],
           "position_openings": [{"ticker": "TEST", "provenienza": "Documento originale"}],
           "baseline_added_at": "2001-01-01T12:00:00Z", "snapshot_prima_del_baseline": 1}
    def series():
        counts["series"] += 1
        return deepcopy(ctx)
    def risk_free(*_):
        counts["risk_free"] += 1
        return .02
    def summary():
        counts["summary"] += 1
        return {"nav_total_eur": 110, "totale_valore_mercato_eur": 110}
    monkeypatch.setattr(twr, "get_official_series", series)
    monkeypatch.setattr(twr, "MemoryDB", lambda: db)
    monkeypatch.setattr(market_inputs, "get_risk_free", risk_free)
    monkeypatch.setattr(db, "get_portfolio_summary", summary)
    with language_context("it"):
        italian = twr.compute_twr_payload()
    cached = deepcopy(twr._CACHE)
    with language_context("en"):
        english = twr.compute_twr_payload()
    with language_context("it"):
        italian_again = twr.compute_twr_payload()
    assert counts == {"series": 1, "risk_free": 1, "summary": 1}
    assert english["copertura"]["nota"].startswith("Latest opening balance registered")
    assert english["reconciliation"]["note"].startswith("delta = live NAV")
    assert english["metrics"]["irr_basis"].startswith("Observed snapshots")
    assert english["copertura"]["position_openings"][0]["provenienza"] == "Documento originale"
    assert english["external_flows"][0]["note"] == "Deposito originale"
    assert italian_again == italian
    assert twr._CACHE == cached
    for key in ("dates", "values_eur", "flows_eur", "regimes", "twr_index", "timestamp", "regime_summary", "irr_as_of"):
        assert english[key] == italian[key]
    for key, value in italian["metrics"].items():
        if key != "irr_basis":
            assert english["metrics"][key] == value
    assert english["metrics"]["twr_total_pct"] == 10
    assert english["as_of"]["computed_at"] == italian["as_of"]["computed_at"]


def test_risk_cache_roundtrip_preserves_values_and_fetch_count(db, monkeypatch):
    import numpy as np
    import pandas as pd
    from bellomberg.portfolio import portfolio_risk as risk
    from bellomberg.cli import price_updater
    risk.invalidate_cache()
    counts = {"download": 0, "summary": 0, "prices_guard": 0}
    rng = np.random.default_rng(32)
    dates = pd.bdate_range("2001-01-01", periods=300)
    prices = pd.DataFrame({("Close", ticker): 100 * np.cumprod(1 + rng.normal(0, .03, 300))
                           for ticker in ("AAA", "BBB", "SPY")}, index=dates)
    fx = pd.DataFrame({("Close", "EURUSD=X"): np.ones(300)}, index=dates)
    def download(tickers, **_):
        counts["download"] += 1
        return (fx if tickers == ["EURUSD=X"] else prices).copy()
    def summary():
        counts["summary"] += 1
        return {"positions": [{"ticker": "AAA", "valuta": "EUR", "valore_mercato": 600, "peso_pct": 60},
                              {"ticker": "BBB", "valuta": "EUR", "valore_mercato": 400, "peso_pct": 40}],
                "totale_valore_mercato_eur": 1000}
    def price_guard():
        counts["prices_guard"] += 1
        return {"origine": "locale", "prezzi": {"senza_yfinance": frozenset()}}
    monkeypatch.setattr(risk, "MemoryDB", lambda: db)
    monkeypatch.setattr(db, "get_portfolio_summary", summary)
    monkeypatch.setattr(risk, "prezzi_speciali", price_guard)
    monkeypatch.setattr(risk.yf, "download", download)
    monkeypatch.setattr(price_updater, "data_ticker_map", lambda tickers, **_: dict(zip(tickers, tickers)))
    with language_context("it"):
        italian = risk.compute_portfolio_risk()
    assert "error" not in italian
    cached = deepcopy(risk._CACHE)
    with language_context("en"):
        english = risk.compute_portfolio_risk()
    with language_context("it"):
        italian_again = risk.compute_portfolio_risk()
    assert counts == {"download": 2, "summary": 3, "prices_guard": 3}
    assert english["sample_meta"]["method"].startswith("Daily weighted mean")
    assert english["nav_basis"].startswith("ANALYZED scope")
    assert english["returns_basis"] == "EUR (FX converted per series)"
    assert "high concentration" in english["alerts"][-1]["message"]
    assert english["var_hierarchy"]["var99_note"].startswith("Historical VaR99")
    assert italian_again == italian
    assert risk._CACHE == cached
    for key in ("timestamp", "nav_eur", "portfolio", "per_asset", "correlation", "cached_for_sec"):
        assert english[key] == italian[key]
    assert [row["level"] for row in english["alerts"]] == [row["level"] for row in italian["alerts"]]


def test_garch_cache_roundtrip_keeps_fit_and_forecasts(db, monkeypatch):
    import numpy as np
    import pandas as pd
    from bellomberg.portfolio import portfolio_garch as garch
    from bellomberg.core.presentation import message
    garch.invalidate_cache()
    counts = {"returns": 0, "fit": 0, "summary": 0}
    original_model = garch.arch_model
    def model(*args, **kwargs):
        counts["fit"] += 1
        return original_model(*args, **kwargs)
    def returns(**_):
        counts["returns"] += 1
        rng = np.random.default_rng(2026)
        series = pd.Series(rng.normal(0, .01, 500), index=pd.bdate_range("2001-01-01", periods=500))
        return series, {"returns_basis": message("valuta locale", "local currency")}
    def summary():
        counts["summary"] += 1
        return {}
    monkeypatch.setattr(garch, "MemoryDB", lambda: db)
    monkeypatch.setattr(db, "get_portfolio_summary", summary)
    monkeypatch.setattr(garch, "arch_model", model)
    monkeypatch.setattr(garch, "_get_portfolio_returns", returns)
    monkeypatch.setattr(garch, "prezzi_speciali", lambda: {
        "origine": "locale", "motivo": "Original source text", "prezzi": {"senza_yfinance": frozenset()}})
    with language_context("it"):
        italian = garch.compute_portfolio_garch()
    assert "error" not in italian
    cached = deepcopy(garch._CACHE)
    with language_context("en"):
        english = garch.compute_portfolio_garch()
    with language_context("it"):
        italian_again = garch.compute_portfolio_garch()
    assert counts == {"returns": 1, "fit": 2, "summary": 3}
    assert "stationary process" in english["persistence_interpretation"]
    assert "ONLY day t+h" in english["forecast_semantics"]
    assert english["returns_basis"] == "local currency"
    assert italian_again == italian
    assert garch._CACHE == cached
    assert italian["distribution"] == "Student-t (code pesanti)"
    assert english["distribution"] == "Student-t (fat-tails)"
    for key in ("timestamp", "parameters", "pvalues", "model_chosen", "forecast_vol", "comparison", "negozio_prezzi"):
        assert english[key] == italian[key]


def test_attribution_unknown_currency_reason_key_is_stable_on_fresh_runs(monkeypatch):
    import pandas as pd
    from bellomberg.portfolio import portfolio_attribution as attribution
    from bellomberg.storage import classificazione as cl
    dates = pd.to_datetime(["2001-01-01", "2001-01-02", "2001-01-03"])
    trades = [{"ticker": ticker, "action": "BUY", "quantita": 10, "prezzo": 100, "valuta": "EUR", "data": "2000-12-01"} for ticker in ("AAA", "BBB")]
    monkeypatch.setattr(attribution, "_currency_labels_for_tickers", lambda *_: {
        "AAA": cl.valuta("AAA", valuta_posizione="EUR"), "BBB": cl.sconosciuto("valuta", "Documento originale")})
    monkeypatch.setattr(cl, "carica_veicoli", lambda: {"origine": "locale", "veicoli": {}})
    monkeypatch.setattr(attribution, "prezzi_speciali", lambda: {"origine": "locale", "motivo": None, "prezzi": {"senza_yfinance": frozenset()}})
    monkeypatch.setattr(attribution, "get_sector_map", lambda *a, **k: {})
    args = dict(period="MTD", end_date="2001-01-03", trades=trades,
                prices=pd.DataFrame({"AAA": [100., 110., 121.], "BBB": [100., 100., 100.]}, index=dates),
                fx=pd.DataFrame(), official_series={"error": "Fonte originale"})
    with language_context("it"):
        italian = attribution.compute_attribution(**args)
    with language_context("en"):
        english = attribution.compute_attribution(**args)
    assert english["excluded"][0]["reasons"] == italian["excluded"][0]["reasons"]
    assert "UNKNOWN" in english["excluded"][0]["reason_details"][0]["label"]
    assert english["portfolio_return_pct"] == italian["portfolio_return_pct"] == 21


def test_factors_real_regression_and_cache_are_language_independent(db, monkeypatch):
    import numpy as np
    import pandas as pd
    from bellomberg.portfolio import portfolio_factors as factors
    from bellomberg.cli import price_updater
    factors.invalidate_cache()
    counts = {"prices": 0, "factors": 0, "summary": 0}
    rng = np.random.default_rng(903)
    dates = pd.bdate_range("2001-01-01", periods=301)
    ff = pd.DataFrame(rng.normal(0, .006, (301, 7)), index=dates,
                      columns=["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom", "RF"])
    px = pd.DataFrame({"Close": 100 * np.cumprod(1 + ff["Mkt-RF"] + ff["RF"] + .0001)}, index=dates)
    def prices(*_a, **_k):
        counts["prices"] += 1
        return px.copy()
    def factor_download(**_):
        counts["factors"] += 1
        return ff.copy()
    def summary():
        counts["summary"] += 1
        return {"positions": [{"ticker": "AAA.MI", "valore_mercato": 1000}]}
    monkeypatch.setattr(factors, "MemoryDB", lambda: db)
    monkeypatch.setattr(db, "get_portfolio_summary", summary)
    monkeypatch.setattr(factors.yf, "download", prices)
    monkeypatch.setattr(factors, "download_ff5_returns", factor_download)
    monkeypatch.setattr(price_updater, "data_ticker", lambda ticker: ticker)
    monkeypatch.setattr(factors, "_fattori", lambda: {"origine": "locale", "motivo": "Fonte originale", "fattori": {"crypto_correlati": [], "regioni": {}}})
    monkeypatch.setattr(factors, "prezzi_speciali", lambda: {"origine": "locale", "motivo": "Fonte originale", "prezzi": {"senza_yfinance": frozenset()}})
    with language_context("it"):
        italian = factors.compute_portfolio_factors()
    assert "error" not in italian
    cached = deepcopy(factors.FACTORS_RESULT_CACHE)
    with language_context("en"):
        english = factors.compute_portfolio_factors()
    with language_context("it"):
        italian_again = factors.compute_portfolio_factors()
    assert counts == {"prices": 1, "factors": 1, "summary": 3}
    assert english["filters"]["note_aggregate"].startswith("Cross-region aggregate betas")
    assert english["per_holding"]["AAA.MI"]["fx_caveat"].startswith("Asset returns in local currency")
    assert italian_again == italian
    assert factors.FACTORS_RESULT_CACHE == cached
    for key in ("timestamp", "portfolio_aggregate", "ff_data_last_date", "ff_data_n_obs", "ff_data_by_region", "region_errors", "coverage_weight_pct"):
        assert english[key] == italian[key]
    assert english["per_holding"]["AAA.MI"]["factor_region"] == "europe"
    assert english["filters"]["negozio_fattori"]["motivo"] == "Fonte originale"


def test_montecarlo_roundtrip_reuses_simulation_and_keeps_whatif_amounts(db, monkeypatch):
    import numpy as np
    import pandas as pd
    from bellomberg.portfolio import portfolio_montecarlo as mc
    mc.invalidate_cache()
    counts = {"returns": 0, "simulation": 0, "summary": 0}
    simulate = mc._simulate_block_bootstrap
    def simulator(*args, **kwargs):
        counts["simulation"] += 1
        return simulate(*args, **kwargs)
    def returns(tickers, **_):
        counts["returns"] += 1
        return pd.DataFrame(np.random.default_rng(71).normal(0, .01, (100, len(tickers))),
                            columns=tickers, index=pd.bdate_range("2001-01-01", periods=100))
    def summary():
        counts["summary"] += 1
        return {"positions": [{"ticker": "AAA", "valore_mercato": 600}, {"ticker": "BBB", "valore_mercato": 400}, {"ticker": "CCC", "valore_mercato": 200}]}
    monkeypatch.setattr(mc, "MemoryDB", lambda: db)
    monkeypatch.setattr(db, "get_portfolio_summary", summary)
    monkeypatch.setattr(mc, "_download_returns", returns)
    monkeypatch.setattr(mc, "_simulate_block_bootstrap", simulator)
    monkeypatch.setattr(mc, "prezzi_speciali", lambda: {"origine": "locale", "prezzi": {"senza_yfinance": frozenset()}})
    args = dict(horizon_days=10, n_sims=1000, method="block_bootstrap", seed=812, stress_scenario="shock_3sigma",
                modifications=[{"action": "remove", "ticker": "AAA", "amount_eur": 1000}])
    with language_context("it"):
        italian = mc.run_monte_carlo_v3(**args)
    assert "error" not in italian
    cached = deepcopy(mc._CACHE)
    with language_context("en"):
        english = mc.run_monte_carlo_v3(**args)
    with language_context("it"):
        italian_again = mc.run_monte_carlo_v3(**args)
    assert counts == {"returns": 1, "simulation": 1, "summary": 3}
    assert english["returns_basis"].startswith("LOCAL currency per asset")
    assert "simultaneous" in english["stress_meta"]["shock_note"]
    assert english["modifications_applied"][0]["note"].startswith("Requested 1000.00 EUR")
    assert english["modifications_applied"][0]["amount_eur_effettivo"] == 600
    assert english["nav_post_eur"] == 600
    assert italian_again == italian
    assert mc._CACHE == cached
    for key in ("timestamp", "method", "stress_scenario", "drift_mode", "weights", "fan_bands", "percentiles_eur", "sample_paths", "sample_paths_days", "terminal_hist"):
        assert english[key] == italian[key]


def test_tearsheet_composed_cache_keeps_monthly_drawdowns_and_metrics(monkeypatch):
    from bellomberg.portfolio import portfolio_tearsheet as tearsheet, twr_engine
    from bellomberg.core.presentation import message
    tearsheet._CACHE.clear()
    calls = []
    def twr(**_):
        calls.append("twr")
        return {"dates": ["2001-01-01", "2001-01-02", "2001-01-03"], "twr_index": [100, 110, 100]}
    monkeypatch.setattr(twr_engine, "compute_twr_payload", twr)
    with language_context("it"):
        metrics = {"note": message("Metrica documentata", "Documented metric"), "value": 123, "original": "Fonte originale"}
        italian = tearsheet.compute_tearsheet(metrics=metrics, rf_annual=0)
    cached = deepcopy(tearsheet._CACHE)
    with language_context("en"):
        english = tearsheet.compute_tearsheet(metrics=metrics, rf_annual=0)
    with language_context("it"):
        italian_again = tearsheet.compute_tearsheet(metrics=metrics, rf_annual=0)
    assert calls == ["twr"]
    assert english["basis"].startswith("TEARSHEET on the OFFICIAL TWR")
    assert english["notes"][0].startswith("Rolling 30 days")
    assert english["metrics"]["note"] == "Documented metric"
    assert english["metrics"]["original"] == "Fonte originale"
    assert italian_again == italian and tearsheet._CACHE == cached
    for key in ("timestamp", "period", "monthly", "yearly", "drawdowns", "rolling", "risk_free_used"):
        assert english[key] == italian[key]


def test_sector_authored_notes_leave_sector_and_bucket_source_verbatim(tmp_path, monkeypatch):
    from bellomberg.portfolio import portfolio_sectors as sectors
    from bellomberg.storage import classificazione
    monkeypatch.setattr(sectors, "CACHE_PATH", str(tmp_path / "sectors.json"))
    monkeypatch.setattr(classificazione, "carica_veicoli", lambda: {"origine": "locale", "veicoli": {}})
    calls = []
    def fetch(ticker):
        calls.append(ticker)
        return {"sector": "Settore originale", "industry": "Industria originale"}
    snap = {"positions": [{"ticker": "SYNTH", "valore_mercato": 100}], "stale_positions": ["SYNTH"]}
    with language_context("it"):
        italian = sectors.compute_sector_exposure(snap, fetch=fetch)
    from bellomberg.core.presentation import render_payload
    composed = render_payload({"sectors": italian}, language="en")
    assert composed["sectors"]["basis"].startswith("Weights on EUR market value")
    with language_context("en"):
        english = sectors.compute_sector_exposure(snap, fetch=fetch)
    assert calls == ["SYNTH"]
    assert english["basis"].startswith("Weights on EUR market value")
    assert english["econ_axis"]["basis"].startswith("SINGLE AXIS")
    assert any("STALE prices" in note for note in english["notes"])
    assert english["by_sector"] == italian["by_sector"]
    assert english["by_sector"][0]["sector"] == "Settore originale"
    assert english["econ_axis"]["by_bucket"] == italian["econ_axis"]["by_bucket"]


def test_attribution_cache_keeps_reason_keys_and_adds_localized_labels(monkeypatch):
    import pandas as pd
    from bellomberg.portfolio import portfolio_attribution as attribution
    from bellomberg.storage import classificazione
    attribution._CACHE.clear()
    calls = []
    dates = pd.to_datetime(["2001-01-01", "2001-01-02", "2001-01-03"])
    trades = [{"ticker": ticker, "action": "BUY", "quantita": 10, "prezzo": 100, "valuta": "EUR", "data": "2000-12-01"} for ticker in ("AAA.MI", "BBB.MI")]
    def history():
        calls.append("trades")
        return deepcopy(trades)
    monkeypatch.setattr(attribution, "_trade_history", history)
    monkeypatch.setattr(attribution, "_opening_positions", lambda: [])
    monkeypatch.setattr(attribution, "get_sector_map", lambda *a, **k: {"AAA.MI": {"sector": "Fonte originale", "source": "yfinance"}})
    monkeypatch.setattr(attribution, "prezzi_speciali", lambda: {"origine": "locale", "motivo": None, "prezzi": {"senza_yfinance": frozenset()}})
    monkeypatch.setattr(classificazione, "carica_veicoli", lambda: {"origine": "locale", "veicoli": {}})
    args = dict(period="MTD", end_date="2001-01-03", prices=pd.DataFrame({"AAA.MI": [100., 110., 121.]}, index=dates),
                fx=pd.DataFrame(), official_series={"error": "Messaggio originale fonte"})
    with language_context("it"):
        italian = attribution.compute_attribution(**args)
    assert "error" not in italian
    cached = deepcopy(attribution._CACHE)
    with language_context("en"):
        english = attribution.compute_attribution(**args)
    with language_context("it"):
        italian_again = attribution.compute_attribution(**args)
    assert calls == ["trades"]
    assert english["basis"].startswith("Absolute CONTRIBUTION")
    assert english["excluded"][0]["reasons"] == italian["excluded"][0]["reasons"]
    assert english["excluded"][0]["reason_details"][0]["label"].startswith("Historical prices unavailable")
    assert english["excluded"][0]["reason_details"][0]["days"] == 2
    assert english["reconciliation"]["error"] == "Messaggio originale fonte"
    assert italian_again == italian and attribution._CACHE == cached
    for key in ("timestamp", "period", "portfolio_return_pct", "by_bucket", "by_currency", "totals"):
        assert english[key] == italian[key]
    assert english["portfolio_return_pct"] == 21


def test_analytics_opening_cost_has_localized_limits_without_invented_fx(monkeypatch):
    from bellomberg.portfolio import portfolio_analytics as analytics
    from bellomberg.core.presentation import render_payload
    opening = {"ticker": "SYNTH", "quantita": 10, "prezzo_medio": 20, "valuta": "USD",
               "as_of": "2001-01-01T12:00:00Z", "created_at": "2001-02-01T12:00:00Z", "provenienza": "Estratto originale"}
    calls = []
    monkeypatch.setattr(analytics, "_build_fx_history", lambda *_: calls.append("FX"))
    monkeypatch.setattr(analytics, "_opening_positions", lambda: [opening])
    with language_context("it"):
        italian = analytics.pl_fx_per_posizione([], "2001-03-01", openings=[opening])
    english = render_payload(italian, language="en")
    assert english["per_ticker"]["SYNTH"]["note"][0].startswith("Documented opening balance")
    assert english["fx_basis"].startswith("Cost = EUR pool")
    assert english["per_ticker"]["SYNTH"]["costo_eur_storico"] is None
    assert english["per_ticker"]["SYNTH"]["costo_nativo"] == 200
    assert calls == []
    with language_context("en"):
        history = analytics.compute_nav_history()
    assert history["error_code"] == "OPENING_HISTORY_INCOMPLETE"
    assert history["error"].startswith("Documented opening balances")
    assert history["position_openings"] == [opening]


def test_analytics_liquidity_and_concentration_translate_only_descriptions(db, monkeypatch):
    import pandas as pd
    from bellomberg.portfolio import portfolio_analytics as analytics
    from bellomberg.cli import price_updater
    from bellomberg.core.presentation import render_payload
    calls = []
    monkeypatch.setattr(analytics, "MemoryDB", lambda: db)
    monkeypatch.setattr(db, "get_portfolio_summary", lambda: {"positions": [{"ticker": "AAA.MI", "valuta": "EUR", "valore_mercato": 1000}]})
    monkeypatch.setattr(analytics, "prezzi_speciali", lambda: {"origine": "locale", "motivo": None, "prezzi": {"senza_yfinance": frozenset()}})
    monkeypatch.setattr(price_updater, "data_ticker", lambda ticker: ticker)
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda currency: 1.)
    class Ticker:
        def history(self, **_):
            calls.append("history")
            return pd.DataFrame({"Close": [10.] * 20, "Volume": [1000.] * 20})
    monkeypatch.setattr(analytics.yf, "Ticker", lambda _: Ticker())
    with language_context("it"):
        italian = {"liquidity": analytics.compute_liquidity_scores(), "concentration": analytics.compute_concentration()}
    english = render_payload(italian, language="en")
    assert italian["liquidity"]["note"].startswith("I giorni assumono")
    assert english["liquidity"]["note"].startswith("Days assume")
    assert english["liquidity"]["items"][0]["days_to_liquidate"] == .5
    assert english["liquidity"]["items"][0]["score"] == "green"
    assert italian["concentration"]["interpretation"]["concentrated"].endswith("concentrato")
    assert english["concentration"]["interpretation"]["concentrated"].endswith("concentrated")
    assert english["concentration"]["by_ticker"] == italian["concentration"]["by_ticker"]
    assert calls == ["history"]


def test_classification_authored_evidence_relabels_but_codes_and_source_text_do_not():
    from bellomberg.storage import classificazione as cl
    from bellomberg.core.presentation import render_payload
    with language_context("it"):
        known = cl.valuta("AAA.MI", valuta_posizione="EUR")
        unknown = cl.sconosciuto("natura", "Provenienza originale")
        payload = {"known": known.as_dict(), "source": str(known), "unknown": unknown.as_dict()}
    english = render_payload(payload, language="en")
    assert english["known"]["evidenza"] == "Explicit position currency"
    assert "Explicit position currency" in english["source"]
    assert "UNKNOWN" in english["unknown"]["dichiarazione"]
    assert english["unknown"]["evidenza"] == "Provenienza originale"
    for field in ("dominio", "valore", "fonte", "confidenza", "verificato_il"):
        assert english["known"][field] == payload["known"][field]


@pytest.mark.parametrize("family", ["vehicles", "prices"])
def test_store_missing_and_corrupt_reasons_keep_explicit_causes_in_english(tmp_path, family):
    from bellomberg.storage import classificazione as cl, negozi_privati as stores
    from bellomberg.core.presentation import render_payload
    path = tmp_path / (family + ".json")
    loader = cl.carica_veicoli if family == "vehicles" else stores.carica_prezzi_speciali
    with language_context("it"):
        missing = loader(str(path))
        path.write_text('{"DUP":1,"DUP":2}', encoding="utf-8")
        corrupted = loader(str(path))
    payload = render_payload({"missing": missing, "corrupt": corrupted}, language="en")
    assert payload["missing"]["origine"] == "assente"
    assert payload["missing"]["motivo"].startswith("Store not found:")
    assert payload["corrupt"]["origine"] == "illeggibile"
    assert "Duplicate JSON key" in payload["corrupt"]["motivo"]
    assert '"DUP":1,"DUP":2' in path.read_text(encoding="utf-8")
