# -*- coding: utf-8 -*-
"""LOTTO 6 del criterio (1), 05/09 — i simboli senza prezzo yfinance e la mappa CoinGecko
stanno nel NEGOZIO PRIVATO dei prezzi speciali, e la sua assenza si DICHIARA.

Cosa misura questa batteria (decisione del PM, 05/09):
- i sei consumatori rileggono il negozio A OGNI CHIAMATA (non una fotografia all'import);
- dove il negozio assente sposterebbe un NUMERO IN EURO — la base NAV dei VaR
  (portfolio_risk), la scala dei percentili Monte Carlo, la serie del NAV e il campione
  della VaR-contribution (portfolio_analytics), il backtest del VaR (var_backtest) — la
  funzione si FERMA con un KO dichiarato, PRIMA di toccare il DB;
- dove il perimetro si rinormalizza da solo (portfolio_factors, portfolio_garch) il calcolo
  prosegue e l'origine del negozio finisce NEL PAYLOAD, perche' «escluso senza storia» e
  «escluso perche' il negozio manca» non sono la stessa frase;
- price_updater lo stampa nel log a OGNI giro, fuori da `if verbose` (il task schedulato
  gira con --quiet: una dichiarazione nel ramo verboso sarebbe muta per costruzione);
- portfolio_attribution non esclude piu' nessuno e la NOTA lo dice.

Regola di casa: si prova il CABLAGGIO, non l'helper. I test del KO non montano ne' DB ne'
yfinance apposta: se qualcuno spostasse la lettura del negozio DOPO l'accesso al DB, il
tripwire del conftest (ProduzioneToccata) o un errore diverso li farebbe cadere.
La fixture autouse `negozio_prezzi_di_prova` (conftest) punta il negozio all'esempio
tracciato: qui lo si ripunta a un file inesistente per provare l'assenza.
"""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import bellomberg.storage.negozi_privati as np_  # noqa: E402

# i sei che leggono la lista dei simboli da saltare (+ price_updater per la mappa)
MOTORI = ["bellomberg.portfolio.portfolio_analytics",
          "bellomberg.portfolio.portfolio_factors",
          "bellomberg.portfolio.portfolio_garch",
          "bellomberg.portfolio.portfolio_montecarlo",
          "bellomberg.portfolio.portfolio_risk"]


def _scrivi(tmp_path, nome, contenuto):
    p = tmp_path / nome
    p.write_text(json.dumps(contenuto), encoding="utf-8")
    return str(p)


@pytest.fixture
def negozio(tmp_path, monkeypatch):
    """Ritorna una funzione che (ri)scrive il negozio e lo punta: serve a provare che i
    consumatori RILEGGONO, non che leggono una volta."""
    def _punta(contenuto=None):
        if contenuto is None:
            p = str(tmp_path / "non_esiste.json")
        else:
            p = _scrivi(tmp_path, "prezzi_speciali.json", contenuto)
        monkeypatch.setattr(np_, "PERCORSO_PREZZI", p)
        return p
    return _punta


# ------------------------------------------------ la lettura, a ogni chiamata

def test_salta_prezzi_rilegge_il_negozio_a_ogni_chiamata(negozio, tmp_path, monkeypatch):
    """Il difetto che questo test esclude: un insieme riempito all'IMPORT. Con una
    fotografia la seconda chiamata renderebbe ancora ALFA."""
    import bellomberg.portfolio.portfolio_analytics as pa
    negozio({"senza_yfinance": ["ALFA"]})
    assert pa.salta_prezzi() == frozenset({"ALFA"})
    negozio({"senza_yfinance": ["OMEGA", "SIGMA"]})
    assert pa.salta_prezzi() == frozenset({"OMEGA", "SIGMA"})


@pytest.mark.parametrize("modulo", MOTORI)
def test_ogni_motore_legge_lo_stesso_negozio(modulo, negozio):
    """Cinque moduli, un solo negozio: prima la stessa costante era scritta cinque volte
    e nessuno garantiva che le cinque copie coincidessero."""
    import importlib
    m = importlib.import_module(modulo)
    negozio({"senza_yfinance": ["ALFA"]})
    esito = m.prezzi_speciali()
    assert esito["prezzi"]["senza_yfinance"] == frozenset({"ALFA"})
    assert esito["motivo"] is None


@pytest.mark.parametrize("modulo", MOTORI + ["bellomberg.cli.price_updater"])
def test_negozio_assente_insieme_vuoto_col_motivo(modulo, negozio):
    import importlib
    m = importlib.import_module(modulo)
    negozio(None)
    esito = m.prezzi_speciali()
    assert esito["origine"] == "assente"
    assert esito["prezzi"]["senza_yfinance"] == frozenset()
    assert "prezzi_speciali.example.json" in esito["motivo"], "il motivo dice cosa copiare"


# ------------------------------------------------ KO dove il numero in euro si sposta

def _messaggio_ko(out):
    assert isinstance(out, dict) and out.get("error"), out
    assert "negozio dei prezzi speciali" in out["error"], out["error"]
    assert "assente" in out["error"], out["error"]
    assert out["negozio_prezzi"]["origine"] == "assente"
    assert out["negozio_prezzi"]["motivo"], "il motivo va nel payload, non solo nel testo"


def test_nav_history_si_ferma_e_dichiara(negozio, monkeypatch):
    """La serie del NAV: senza la lista il simbolo entra nel download e il ffill/bfill
    propaga una sola candela su TUTTA la storia."""
    import bellomberg.portfolio.portfolio_analytics as pa
    negozio(None)
    monkeypatch.setattr(pa, "_ANALYTICS_CACHE", {})
    _messaggio_ko(pa.compute_nav_history(force=True))


def test_la_lista_arriva_al_download_del_nav(negozio, monkeypatch):
    """La riga che COLLEGA: `_download_prices_for_history` riceve l'insieme del negozio.
    Se qualcuno le passasse `frozenset()` il NAV scaricherebbe anche i simboli dichiarati
    e nessun altro test se ne accorgerebbe (mutazione M36 del banco)."""
    import bellomberg.portfolio.portfolio_analytics as pa
    visti = {}

    def _finto(tickers, start, end, salta):
        visti["salta"] = salta
        return None
    monkeypatch.setattr(pa, "_download_prices_for_history", _finto)
    monkeypatch.setattr(pa, "_ANALYTICS_CACHE", {})
    monkeypatch.setattr(pa, "_trade_history",
                        lambda: [{"ticker": "AAA.MI", "tipo": "BUY", "quantita": 1,
                                  "prezzo": 10.0, "valuta": "EUR", "data": "2026-06-15"}])
    negozio({"senza_yfinance": ["ALFA", "OMEGA"]})
    pa.compute_nav_history(force=True)
    assert visti["salta"] == frozenset({"ALFA", "OMEGA"}), visti


def test_var_contribution_si_ferma_e_dichiara(negozio):
    """I numeri in euro scalano sul NAV COPERTO, e un simbolo con serie parziale tronca
    il campione di tutti (dropna sulle righe): sotto le 60 osservazioni l'endpoint muore."""
    import bellomberg.portfolio.portfolio_analytics as pa
    negozio(None)
    _messaggio_ko(pa.compute_var_contribution())


def test_risk_si_ferma_e_dichiara(negozio):
    """total_eur e' la base su cui scalano TUTTI i VaR in euro (nav_basis)."""
    import bellomberg.portfolio.portfolio_risk as pr
    negozio(None)
    _messaggio_ko(pr.compute_portfolio_risk(force=True))


@pytest.mark.parametrize("entry", ["run_monte_carlo", "run_monte_carlo_v3"])
def test_montecarlo_si_ferma_e_dichiara(entry, negozio):
    """Tutt'e due gli entry point: base_nav e' il NAV del perimetro simulabile."""
    import bellomberg.portfolio.portfolio_montecarlo as mc
    negozio(None)
    _messaggio_ko(getattr(mc, entry)())


def test_var_backtest_si_ferma_e_dichiara(negozio):
    import bellomberg.portfolio.var_backtest as vb
    negozio(None)
    _messaggio_ko(vb.backtest_var())


def test_il_ko_arriva_prima_del_DB(negozio, monkeypatch):
    """Se la lettura del negozio finisse DOPO l'accesso al DB, il KO dipenderebbe da quanto
    lontano e' arrivato il calcolo. Qui MemoryDB esplode: il KO deve arrivare lo stesso."""
    import bellomberg.portfolio.portfolio_risk as pr

    def _mai(*a, **k):
        raise AssertionError("il DB e' stato toccato prima di leggere il negozio")
    monkeypatch.setattr(pr, "MemoryDB", _mai)
    negozio(None)
    _messaggio_ko(pr.compute_portfolio_risk(force=True))


def test_col_negozio_a_posto_il_calcolo_riparte(negozio, monkeypatch):
    """Il contrario del KO: col negozio leggibile la funzione supera la guardia e arriva
    al DB (qui finto, vuoto) — cosi' il KO non e' un «si ferma sempre» travestito."""
    import bellomberg.portfolio.portfolio_risk as pr

    class _DB:
        def get_portfolio_summary(self):
            return {"positions": []}
    monkeypatch.setattr(pr, "MemoryDB", _DB)
    negozio({"senza_yfinance": ["ALFA"]})
    out = pr.compute_portfolio_risk(force=True)
    assert out.get("error") == "no positions", out


def test_il_ramo_ILLEGGIBILE_ferma_i_motori_come_l_assente(negozio, monkeypatch, tmp_path):
    """Il KO copre DUE stati, non uno: un negozio che c'e' ma e' rotto (JSON malformato,
    una voce sbagliata) deve fermare come l'assente. Se la guardia si restringesse ad
    `origine == "assente"` nessun altro test se ne accorgerebbe."""
    import bellomberg.portfolio.portfolio_risk as pr
    rotto = tmp_path / "prezzi_speciali.json"
    rotto.write_text('{ non json', encoding="utf-8")
    import bellomberg.storage.negozi_privati as np_
    monkeypatch.setattr(np_, "PERCORSO_PREZZI", str(rotto))

    def _mai(*a, **k):
        raise AssertionError("DB toccato con un negozio illeggibile")
    monkeypatch.setattr(pr, "MemoryDB", _mai)
    out = pr.compute_portfolio_risk(force=True)
    assert out.get("error") and "illeggibile" in out["error"], out
    assert out["negozio_prezzi"]["origine"] == "illeggibile"


def test_una_voce_malformata_e_illeggibile_non_mezzo_negozio(negozio, monkeypatch):
    """Una sola voce sbagliata non deve lasciar passare le altre: mezzo negozio caricato
    sarebbe un ripiego muto, e i motori girerebbero su una lista parziale."""
    import bellomberg.portfolio.portfolio_risk as pr
    negozio({"senza_yfinance": ["ALFA", "minuscolo"]})
    out = pr.compute_portfolio_risk(force=True)
    assert out.get("error") and "illeggibile" in out["error"], out


def test_la_sezione_senza_yfinance_e_obbligatoria(negozio):
    """Ometterla si leggerebbe come «nessun simbolo da saltare» — la stessa cosa che dice
    un negozio ASSENTE — ma senza far scattare il KO: un refuso spegnerebbe lo skip in
    silenzio. Una lista vuota va DICHIARATA con []."""
    import bellomberg.portfolio.portfolio_risk as pr
    negozio({"coingecko": {"KRYPTO": "krypto-coin"}})
    out = pr.compute_portfolio_risk(force=True)
    assert out.get("error") and "illeggibile" in out["error"], out
    negozio({"senza_yfinance": [], "coingecko": {"KRYPTO": "krypto-coin"}})
    assert pr.prezzi_speciali()["motivo"] is None, "vuota ma DICHIARATA si legge"


@pytest.mark.parametrize("modulo,funzione", [
    ("bellomberg.portfolio.portfolio_risk", "compute_portfolio_risk"),
    ("bellomberg.portfolio.portfolio_montecarlo", "run_monte_carlo"),
])
def test_la_lista_arriva_al_motore_non_solo_la_dichiarazione(modulo, funzione,
                                                             negozio, monkeypatch):
    """Il cablaggio nei motori dove si spostano gli euro: `_yf_ticker` deve ricevere
    l'insieme del NEGOZIO. Se qualcuno gli passasse frozenset() il KO continuerebbe a
    scattare a negozio assente e nessun test se ne accorgerebbe."""
    import importlib
    m = importlib.import_module(modulo)
    visti = {}
    vero = m._yf_ticker

    def _spia(t, salta):
        visti["salta"] = salta
        return vero(t, salta)
    monkeypatch.setattr(m, "_yf_ticker", _spia)

    class _DB:
        def get_portfolio_summary(self):
            return {"positions": [{"ticker": "AAA.MI", "valore_mercato": 1000.0}]}
    monkeypatch.setattr(m, "MemoryDB", _DB)
    negozio({"senza_yfinance": ["ALFA", "OMEGA"]})
    getattr(m, funzione)(**({"force": True} if funzione == "compute_portfolio_risk" else {}))
    assert visti.get("salta") == frozenset({"ALFA", "OMEGA"}), visti


# ------------------------------------------------ dichiarazione nel payload

def test_factors_dichiara_il_negozio_nel_payload(negozio, monkeypatch):
    """factors non si ferma: il simbolo non saltato finirebbe in `skipped` con un motivo
    DIVERSO (dataset/regressione), e quello e' l'unico motivo che arriva in pagina.
    Stesse parti pesanti sostituite di tests/test_negozi_privati (DB, FF, OLS): resta il
    cablaggio del negozio nel loop e nel payload."""
    pf = pytest.importorskip("bellomberg.portfolio.portfolio_factors")
    pd = pytest.importorskip("pandas")
    for flag in ("NUMPY_OK", "YF_OK", "SM_OK", "REQ_OK"):
        monkeypatch.setattr(pf, flag, True)
    monkeypatch.setattr(pf, "FACTORS_RESULT_CACHE", {"ts": 0, "key": "", "data": None})

    class _DB:
        def get_portfolio_summary(self):
            return {"positions": [{"ticker": "THETA.L", "valore_mercato": 1000.0},
                                  {"ticker": "ALFA", "valore_mercato": 500.0}]}
    monkeypatch.setattr(pf, "MemoryDB", _DB)
    ff = pd.DataFrame({"Mkt-RF": [0.0, 0.0]},
                      index=pd.to_datetime(["2026-01-02", "2026-01-05"]))
    monkeypatch.setattr(pf, "download_ff5_returns", lambda force=False, region="us": ff)

    def _esposizione(ticker, period="3y", ff_returns=None, add_btc_factor=False,
                     factor_region="us", salta=None):
        base = {k: 0.0 for k in ("alpha_annualized_pct", "beta_market", "beta_smb",
                                 "beta_hml", "beta_rmw", "beta_cma", "beta_mom")}
        return dict(base, ticker=ticker, has_btc_factor=add_btc_factor, r_squared=0.5,
                    alpha_tstat=0.0, n_obs=100, factor_region=factor_region)
    monkeypatch.setattr(pf, "compute_holding_exposure", _esposizione)

    negozio(None)
    out = pf.compute_portfolio_factors(force=True)
    assert "error" not in out, out.get("error")
    assert out["filters"]["negozio_prezzi"]["origine"] == "assente"
    assert "prezzi_speciali.example.json" in out["filters"]["negozio_prezzi"]["motivo"]

    # e col negozio a posto la dichiarazione cambia: non e' una costante travestita
    negozio({"senza_yfinance": ["ALFA"]})
    monkeypatch.setattr(pf, "FACTORS_RESULT_CACHE", {"ts": 0, "key": "", "data": None})
    out2 = pf.compute_portfolio_factors(force=True)
    assert out2["filters"]["negozio_prezzi"]["motivo"] is None
    saltati = out2.get("skipped_tickers", [])
    assert "ALFA" in saltati, "il simbolo del negozio non e' stato escluso: %s" % saltati
    motivi = [x["reason"] for x in out2.get("skipped_detail", []) if x["ticker"] == "ALFA"]
    assert motivi and "SKIP" in motivi[0], motivi


def test_garch_dichiara_il_negozio_nel_payload(negozio, monkeypatch):
    """Il cablaggio sta in compute_portfolio_garch, NON dentro _get_portfolio_returns:
    le prove che sostituiscono quella funzione (test_quant_garch_mc_factors) salterebbero
    la lettura del negozio e proverebbero l'helper invece del cablaggio."""
    pg = pytest.importorskip("bellomberg.portfolio.portfolio_garch")
    pd = pytest.importorskip("pandas")
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(20260905)
    serie = pd.Series(rng.normal(0, 0.01, 400),
                      index=pd.bdate_range("2024-01-01", periods=400))
    meta = {"returns_basis": "EUR (prova)", "fx_conversion": {}, "sample_meta": {},
            "excluded_tickers": []}
    monkeypatch.setattr(pg, "_get_portfolio_returns", lambda *a, **k: (serie, meta))
    monkeypatch.setattr(pg, "MemoryDB", lambda: type("DB", (), {
        "get_portfolio_summary": lambda self: {"fx_incomplete": None}})())
    negozio(None)
    out = pg.compute_portfolio_garch(force=True)
    assert "error" not in out, out.get("error")
    assert out["negozio_prezzi"]["origine"] == "assente"
    assert out["negozio_prezzi"]["motivo"]


def test_garch_passa_la_lista_a_get_portfolio_returns(negozio, monkeypatch):
    """La riga che COLLEGA: se sparisce, `salta` non arriva piu' giu'. `_get_portfolio_returns`
    senza `salta` alza TypeError apposta — un insieme vuoto di default sarebbe muto."""
    pg = pytest.importorskip("bellomberg.portfolio.portfolio_garch")
    with pytest.raises(TypeError) as ei:
        pg._get_portfolio_returns()
    assert "salta" in str(ei.value)

    visti = {}

    def _finto(lookback_days=None, salta=None):
        visti["salta"] = salta
        return None, {}
    monkeypatch.setattr(pg, "_get_portfolio_returns", _finto)
    monkeypatch.setattr(pg, "MemoryDB", lambda: type("DB", (), {
        "get_portfolio_summary": lambda self: {"fx_incomplete": None}})())
    negozio({"senza_yfinance": ["ALFA"]})
    pg.compute_portfolio_garch(force=True)
    assert visti["salta"] == frozenset({"ALFA"}), visti


def test_liquidity_dichiara_il_negozio_nella_nota(negozio, monkeypatch):
    import bellomberg.portfolio.portfolio_analytics as pa
    monkeypatch.setattr(pa, "YF_OK", True)

    class _DB:
        def get_portfolio_summary(self):
            return {"positions": []}
    monkeypatch.setattr(pa, "MemoryDB", _DB)
    negozio(None)
    out = pa.compute_liquidity_scores()
    assert out.get("error") == "no positions"   # perimetro vuoto: il KO qui non c'e'


# ------------------------------------------------ price_updater

def test_price_updater_usa_la_mappa_del_negozio(negozio, monkeypatch):
    """La mappa arriva dal chiamante: la firma senza `mappa` non esiste piu'."""
    import bellomberg.cli.price_updater as pu
    negozio({"senza_yfinance": [], "coingecko": {"KRYPTO": "krypto-coin"}})
    chiamate = {}

    def _get(url, params=None, timeout=None):
        chiamate["ids"] = (params or {}).get("ids")

        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"krypto-coin": {"usd": 12.5}}
        return _R()
    monkeypatch.setattr(pu, "REQUESTS_AVAILABLE", True)
    monkeypatch.setattr(pu.requests, "get", _get)
    esito = pu.prezzi_speciali()
    out = pu._fetch_coingecko("KRYPTO", esito["prezzi"]["coingecko"])
    assert chiamate["ids"] == "krypto-coin"
    assert out and out[0] == 12.5

    with pytest.raises(TypeError):
        pu._fetch_coingecko("KRYPTO")   # senza mappa non si chiama piu'


def test_price_updater_dichiara_il_negozio_nel_risultato(negozio, monkeypatch):
    """`update_all_prices` mette origine/motivo nel risultato: e' da li' che __main__
    stampa la riga INCONDIZIONATA nel log (il task gira con --quiet)."""
    import bellomberg.cli.price_updater as pu
    negozio(None)

    class _DB:
        def get_portfolio_summary(self):
            return {"positions": []}
    out = pu.update_all_prices(_DB(), verbose=False)
    assert out["negozio_prezzi"]["origine"] == "assente"
    assert out["negozio_prezzi"]["motivo"]


def test_la_riga_del_log_esce_col_task_in_QUIET(negozio, monkeypatch, capsys):
    """A RUNTIME, non con un grep sul sorgente: `main()` con --quiet (come il task Windows)
    deve stampare la dichiarazione del negozio e NON la riga «updated/failed». La prova
    vecchia guardava l'indentazione nel testo del file e restava verde anche con il print
    commentato (mutazione M35): un'euristica sul sorgente non e' una misura."""
    import sys as _sys
    import types
    import bellomberg.cli.price_updater as pu
    negozio(None)

    class _DB:
        def get_portfolio_summary(self):
            return {"positions": []}
    monkeypatch.setattr(pu, "MemoryDB", _DB)
    # iv_history tocca il DB vero: lo sostituiamo con un modulo finto (il tripwire del
    # conftest deriva da BaseException e non verrebbe preso dall'except del blocco IV)
    finto = types.ModuleType("iv_history")
    finto.save_daily_snapshot = lambda: {}
    monkeypatch.setitem(_sys.modules, "bellomberg.market_data.iv_history", finto)
    monkeypatch.setattr(_sys, "argv", ["price_updater.py", "--quiet", "--no-ibkr"])
    pu.main()
    uscita = capsys.readouterr().out
    assert "[PREZZI] KO dichiarato" in uscita, uscita[-400:]
    assert "negozio dei prezzi speciali" in uscita
    assert "updated:" not in uscita, "questo giro DEVE essere quiet, se no non prova nulla"


def test_col_negozio_a_posto_il_log_non_dichiara_niente(negozio, monkeypatch, capsys):
    """Frase di stato al presente: la riga compare SOLO quando il negozio manca davvero."""
    import sys as _sys
    import types
    import bellomberg.cli.price_updater as pu
    negozio({"senza_yfinance": [], "coingecko": {}})

    class _DB:
        def get_portfolio_summary(self):
            return {"positions": []}
    monkeypatch.setattr(pu, "MemoryDB", _DB)
    finto = types.ModuleType("iv_history")
    finto.save_daily_snapshot = lambda: {}
    monkeypatch.setitem(_sys.modules, "bellomberg.market_data.iv_history", finto)
    monkeypatch.setattr(_sys, "argv", ["price_updater.py", "--quiet", "--no-ibkr"])
    pu.main()
    assert "[PREZZI] KO dichiarato" not in capsys.readouterr().out


def test_update_all_prices_usa_la_mappa_DEL_NEGOZIO(negozio, monkeypatch):
    """Il CABLAGGIO, non l'helper: e' `update_all_prices` che deve passare la mappa del
    negozio a _fetch_coingecko. Con una posizione che yfinance non prezza, l'id chiesto a
    CoinGecko dev'essere quello scritto nel negozio."""
    import bellomberg.cli.price_updater as pu
    negozio({"senza_yfinance": [], "coingecko": {"KRYPTO": "krypto-coin"}})
    visti = {}

    def _fetch(symbol, mappa):
        visti["mappa"] = mappa
        return None
    monkeypatch.setattr(pu, "_fetch_coingecko", _fetch)
    monkeypatch.setattr(pu, "_fetch_yfinance", lambda t: None)

    class _DB:
        def get_portfolio_summary(self):
            return {"positions": [{"ticker": "KRYPTO", "valore_mercato": 100.0}]}

        def update_price(self, *a, **k):
            raise AssertionError("nessun prezzo trovato: non si scrive")
    out = pu.update_all_prices(_DB(), source_order=("yfinance", "coingecko"), verbose=False)
    assert visti["mappa"] == {"KRYPTO": "krypto-coin"}, visti
    assert out["failed"] == 1 and out["negozio_prezzi"]["motivo"] is None


def test_la_dichiarazione_dipende_dalla_CAUSA_non_dal_motivo(negozio, monkeypatch):
    """Il predicato delle tre dichiarazioni e' `origine in ("assente","illeggibile")`, non
    `motivo`. Qui il caricatore rende un guasto SENZA motivo (un ramo che oggi non esiste, ma
    che domani puo' nascere): la frase deve uscire lo stesso. Col predicato sul motivo questo
    test cade — ed e' il punto: `motivo` e' la conseguenza, `origine` e' la causa."""
    import bellomberg.storage.negozi_privati as np_
    import bellomberg.portfolio.portfolio_attribution as pa_attr
    import bellomberg.cli.price_updater as pu
    import bellomberg.portfolio.portfolio_analytics as pa

    def _guasto_muto(path=None):
        return {"prezzi": {"senza_yfinance": frozenset(), "coingecko": {}},
                "origine": "illeggibile", "motivo": None}
    monkeypatch.setattr(np_, "carica_prezzi_speciali", _guasto_muto)

    # attribution: la nota c'e' lo stesso
    assert pa_attr.prezzi_speciali()["origine"] == "illeggibile"
    src = open(os.path.join(REPO, "src", "bellomberg", "portfolio",
                            "portfolio_attribution.py"), encoding="utf-8").read()
    assert 'if _prezzi["origine"] in ("assente", "illeggibile")' in src,         "il predicato dell'attribution non e' sulla causa"

    # liquidita': idem
    monkeypatch.setattr(pa, "YF_OK", True)

    class _DB:
        def get_portfolio_summary(self):
            return {"positions": [{"ticker": "ALFA", "valore_mercato": 1000.0}]}
    monkeypatch.setattr(pa, "MemoryDB", _DB)
    out = pa.compute_liquidity_scores()
    assert "ATTENZIONE" in out["note"], "la nota della liquidita' e' sparita con motivo=None"

    # price_updater: il ramo del log
    src_pu = open(os.path.join(REPO, "src", "bellomberg", "cli", "price_updater.py"),
                  encoding="utf-8").read()
    assert 'if _neg.get("origine") in ("assente", "illeggibile")' in src_pu


# ------------------------------------------------ attribution (file di e3, hunk concordati)

def test_attribution_a_negozio_assente_non_esclude_e_lo_DICE(negozio, monkeypatch):
    """La nota: l'insieme e' vuoto PERCHE' il negozio manca, non perche' non ci sia niente
    da saltare. Senza questa frase il payload direbbe il falso per omissione."""
    pa_attr = pytest.importorskip("bellomberg.portfolio.portfolio_attribution")
    negozio(None)
    esito = pa_attr.prezzi_speciali()
    assert esito["origine"] == "assente"
    src = open(os.path.join(REPO, "src", "bellomberg", "portfolio",
                            "portfolio_attribution.py"), encoding="utf-8").read()
    assert "negozio dei prezzi speciali %s (%s)" in src
    assert "non perche' non ci sia" in src
    assert "SKIP_TICKERS" not in src, "l'import della costante non esiste piu'"
