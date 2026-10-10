"""A7 (04/10, Opus 5.5) — beta del book dichiarato e data reale del NAV nel log.

a) `compute_portfolio_risk` partiva da `beta_spy = 0.0`: se SPY non arrivava dal download
   (o la serie era corta) il beta del book usciva 0.00 come se fosse misurato. Ora il beta
   e' None e `beta_error` dice perche'; il PDF istituzionale stampa n.d. col motivo.
f) `compute_nav_history` stampava come fine serie il limite ESCLUSO passato a yfinance
   (domani): il log deve portare l'ultima data REALE della serie.

Ticker, pesi e serie INVENTATI (ZZTEST, QQSYN.MI), nessuna rete: il download e' uno stub.
L'oracolo del beta e' costruito nella serie: i due nomi rendono esattamente k volte SPY,
quindi il beta del book e' k (FX costante: la conversione EUR non tocca i rendimenti).
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import bellomberg.portfolio.portfolio_risk as pr

N_GIORNI = 260
GIORNI = pd.bdate_range("2030-01-02", periods=N_GIORNI)


def _spy_prezzi():
    rng = np.random.default_rng(7)
    r = rng.normal(0.0004, 0.011, N_GIORNI - 1)
    return 100.0 * np.cumprod(np.r_[1.0, 1.0 + r])


def _prezzi_finti(k):
    spy = _spy_prezzi()
    r_spy = spy[1:] / spy[:-1] - 1.0
    nome = 50.0 * np.cumprod(np.r_[1.0, 1.0 + k * r_spy])
    return {"SPY": spy, "ZZTEST": nome, "QQSYN.MI": nome * 0.5}


def _scarica_finto(prezzi, *, spy="ok", fx="ok"):
    """Stub di yf.download. spy: ok | nan (colonna vuota, come yfinance su un simbolo
    fallito) | assente | corta (solo gli ultimi 15 giorni). fx: ok | assente."""
    def download(simboli, **kw):
        if isinstance(simboli, str):
            simboli = [simboli]
        cols = {}
        for s in simboli:
            if s == "EURUSD=X":
                if fx == "ok":
                    cols[s] = np.full(N_GIORNI, 1.10)
                continue
            if s == "SPY":
                if spy == "assente":
                    continue
                serie = prezzi["SPY"].astype(float).copy()
                if spy == "nan":
                    serie[:] = np.nan
                elif spy == "corta":
                    serie[:-15] = np.nan
                cols[s] = serie
                continue
            cols[s] = prezzi[s]
        close = pd.DataFrame(cols, index=GIORNI)
        close.columns = pd.MultiIndex.from_product([["Close"], close.columns])
        return close
    return download


@pytest.fixture
def motore(monkeypatch):
    """Prepara compute_portfolio_risk con DB, negozio, alias e download finti."""
    import bellomberg.cli.price_updater as pu

    def prepara(k=1.5, **modo):
        monkeypatch.setattr(pr, "prezzi_speciali", lambda: {
            "origine": "file", "motivo": None, "prezzi": {"senza_yfinance": frozenset()}})
        summary = {"positions": [
            {"ticker": "ZZTEST", "valore_mercato": 6000.0, "valuta": "EUR", "peso_pct": 60.0},
            {"ticker": "QQSYN.MI", "valore_mercato": 4000.0, "valuta": "EUR", "peso_pct": 40.0}],
            "totale_valore_mercato_eur": 10000.0}
        monkeypatch.setattr(pr, "MemoryDB",
                            lambda: type("DB", (), {"get_portfolio_summary": lambda self: summary})())
        monkeypatch.setattr(pu, "data_ticker_map", lambda simboli, riservati=(): {s: s for s in simboli})
        monkeypatch.setattr(pr.yf, "download", _scarica_finto(_prezzi_finti(k), **modo))
        pr.invalidate_cache()
        return pr.compute_portfolio_risk(force=True)
    yield prepara
    pr.invalidate_cache()


# ---------------------------------------------------------------- a) beta del book

@pytest.mark.parametrize("modo", ["nan", "assente"])
def test_spy_non_scaricato_beta_none_e_motivo(motore, modo):
    out = motore(spy=modo)
    assert "error" not in out, out
    port = out["portfolio"]
    assert port["beta_vs_spy"] is None, "SPY assente non puo' dare un beta misurato"
    assert "SPY non disponibile" in out["beta_error"], out["beta_error"]
    assert out["beta_obs"] == 0
    # il resto del payload resta misurato: il buco e' SOLO del benchmark
    assert port["vol_annual_pct"] is not None and port["var_95_1d_pct"] is not None
    assert not [a for a in out["alerts"] if a["metric"] == "Beta"]


def test_spy_corto_beta_none_con_i_giorni_dichiarati(motore):
    out = motore(spy="corta")
    assert out["portfolio"]["beta_vs_spy"] is None
    assert "giorni in comune" in out["beta_error"], out["beta_error"]
    assert 0 < out["beta_obs"] <= 20
    assert str(out["beta_obs"]) in out["beta_error"]


@pytest.mark.parametrize("k", [1.5, 0.7])
def test_beta_misurato_esce_col_valore_vero_e_senza_errore(motore, k):
    out = motore(k=k)
    assert out["portfolio"]["beta_vs_spy"] == pytest.approx(k, abs=0.01)
    assert out["beta_error"] is None
    assert out["beta_obs"] > 20
    assert "convertito in EUR" in out["beta_basis"]
    beta_alert = [a for a in out["alerts"] if a["metric"] == "Beta"]
    assert bool(beta_alert) == (k > 1.3)


def test_fx_spy_non_applicato_la_base_lo_dice(motore):
    """Se EUR/USD non arriva, SPY resta in USD: la base del beta non puo' dire «convertito»."""
    out = motore(fx="assente")
    assert out["portfolio"]["beta_vs_spy"] is not None
    assert "NON convertito" in out["beta_basis"], out["beta_basis"]


def test_beta_error_in_inglese(motore):
    from bellomberg.core.language import language_context
    with language_context("en"):
        out = motore(spy="nan")
    assert out["beta_error"].startswith("SPY unavailable"), out["beta_error"]


# ---------------------------------------------------------------- a) resa nel PDF

def _testo_pdf(tmp_path, risk, monkeypatch):
    from pypdf import PdfReader
    from bellomberg.reporting import pdf_institutional
    monkeypatch.setattr(pdf_institutional, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(pdf_institutional, "_gen_charts", lambda *a: (None, None, None))
    out = pdf_institutional.build_institutional_memo("# Memo di prova\n\nBLUF: testo finto.", risk_data=risk,
                                   output_path=str(tmp_path / "memo.pdf"))
    assert out, "PDF non generato"
    return " ".join((p.extract_text() or "") for p in PdfReader(out).pages)


def test_pdf_beta_none_stampa_nd_col_motivo(tmp_path, monkeypatch):
    risk = {"portfolio": {"vol_annual_pct": 12.3, "sharpe": 0.9, "beta_vs_spy": None,
                          "var_95_1d_pct": -1.4, "max_dd_1y_pct": -7.7},
            "beta_error": "SPY non disponibile: motivo ZZFINTO"}
    testo = " ".join(_testo_pdf(tmp_path, risk, monkeypatch).split())
    assert "S&P 500 n.d. SPY non disponibile" in testo, testo[:2000]
    assert "ZZFINTO" in testo
    assert "None" not in testo


def test_pdf_beta_misurato_stampa_il_numero(tmp_path, monkeypatch):
    risk = {"portfolio": {"vol_annual_pct": 12.3, "sharpe": 0.9, "beta_vs_spy": 1.23,
                          "var_95_1d_pct": -1.4, "max_dd_1y_pct": -7.7},
            "beta_error": None}
    testo = " ".join(_testo_pdf(tmp_path, risk, monkeypatch).split())
    # 09/10 (Opus 5.5): con portfolio_data=None i KPI e l'allocazione in cover dicono
    # giustamente «n.d.» (R13); l'intento qui e' il beta: numero stampato, mai n.d.
    assert "S&P 500 1.23" in testo, testo[:2000]
    assert "S&P 500 n.d." not in testo and "motivo n.d." not in testo, testo[:2000]


# ---------------------------------------------------------------- f) data del NAV nel log

def test_log_nav_porta_l_ultima_data_reale_non_il_limite_escluso(monkeypatch, capsys):
    from bellomberg.storage import memory_db
    import bellomberg.portfolio.portfolio_analytics as pa
    giorni = pd.to_datetime(["2030-03-04", "2030-03-05", "2030-03-06"])
    df = pd.DataFrame({"ZZTEST": [10.0, 10.5, 11.0]}, index=giorni)
    trades = [{"id": 1, "ticker": "ZZTEST", "action": "BUY", "quantita": 3,
               "prezzo": 10.0, "valuta": "EUR", "data": "2030-03-04"}]
    monkeypatch.setattr(pa, "_opening_positions", lambda: [])
    monkeypatch.setattr(pa, "_trade_history", lambda: trades)
    monkeypatch.setattr(pa, "_download_prices_for_history", lambda tk, s, e, salta, **_kw: df)
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: pd.DataFrame())
    monkeypatch.setattr(memory_db, "leggi_cassa_portfolio",
                        lambda path=None: {"cash_eur": 0.0, "cash_source": "portfolio.json",
                                           "cash_source_note": None})
    pa._ANALYTICS_CACHE.clear()
    domani = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    out = pa.compute_nav_history(force=True)
    pa._ANALYTICS_CACHE.clear()
    assert "error" not in out, out
    log = capsys.readouterr().out
    righe = [r for r in log.splitlines() if "NAV history" in r]
    assert righe, log
    # il limite escluso non deve piu' comparire come fine della serie
    assert not any(("-> " + domani) in r for r in righe), righe
    fatto = [r for r in righe if "done" in r]
    assert fatto and "2030-03-06" in fatto[0] and "al 2030-03-06" in fatto[0], fatto
    assert out["dates"][-1] == "2030-03-06"


# ------------------------------------------- a) review RV-R: calendario, FX vero, pochi giorni

def _serie_eur(k, fx_vol, seme=5):
    """SPY in USD, EUR/USD che si muove (USD per EUR) e due nomi EUR che rendono
    esattamente k volte SPY CONVERTITO IN EUR: l'oracolo vale solo se il motore converte."""
    rng = np.random.default_rng(seme)
    spy_usd = 100 * np.cumprod(np.r_[1, 1 + rng.normal(0.0004, 0.011, N_GIORNI - 1)])
    fx = 1.10 * np.cumprod(np.r_[1, 1 + rng.normal(0, fx_vol, N_GIORNI - 1)])
    spy_eur = spy_usd / fx
    r_eur = spy_eur[1:] / spy_eur[:-1] - 1
    nome = 50 * np.cumprod(np.r_[1, 1 + k * r_eur])
    return spy_usd, fx, {"ZZTEST": nome, "QQSYN.MI": nome * 0.5}


def _scarica_eur(spy_usd, fx, nomi, buchi=(), primi_nan=0):
    def download(simboli, **kw):
        if isinstance(simboli, str):
            simboli = [simboli]
        cols = {}
        for s in simboli:
            if s == "EURUSD=X":
                cols[s] = fx
            elif s == "SPY":
                v = spy_usd.astype(float).copy()
                v[list(buchi)] = np.nan
                v[:primi_nan] = np.nan
                cols[s] = v
            elif s in nomi:
                cols[s] = nomi[s]
        close = pd.DataFrame(cols, index=GIORNI)
        close.columns = pd.MultiIndex.from_product([["Close"], close.columns])
        return close
    return download


@pytest.fixture
def motore_eur(motore, monkeypatch):
    """Come `motore`, ma col download a FX variabile e buchi di calendario su SPY."""
    def prepara(k, fx_vol, **kw):
        motore(k=k)                      # DB, negozio, alias finti
        monkeypatch.setattr(pr.yf, "download", _scarica_eur(*_serie_eur(k, fx_vol), **kw))
        pr.invalidate_cache()
        return pr.compute_portfolio_risk(force=True)
    return prepara


@pytest.mark.parametrize("k", [1.5, 0.7])
def test_beta_contro_spy_in_eur_con_fx_che_si_muove(motore_eur, k):
    """EUR/USD con vol 0,6%/giorno: un beta contro SPY in USD (pre-FX) non darebbe k."""
    out = motore_eur(k, 0.006)
    assert out["portfolio"]["beta_vs_spy"] == pytest.approx(k, abs=0.01), out["portfolio"]
    assert "convertito in EUR" in out["beta_basis"]


def test_beta_allinea_per_data_con_festivita_usa(motore_eur):
    """SPY bucato a meta' serie (festivita' USA): l'allineamento per POSIZIONE sfaserebbe
    portafoglio e benchmark dal primo buco in poi e il beta non sarebbe piu' 1,5."""
    out = motore_eur(1.5, 0.0, buchi=(30, 31, 90, 150, 200))
    assert out["portfolio"]["beta_vs_spy"] == pytest.approx(1.5, abs=0.01), out["portfolio"]
    assert out["beta_obs"] < N_GIORNI - 1 - 5   # i giorni bucati NON entrano nel conteggio


def test_beta_su_pochi_giorni_esce_con_la_riserva(motore_eur):
    """>20 giorni basta per calcolarlo, non per fidarsene: sotto la soglia la nota lo dice."""
    out = motore_eur(1.5, 0.0, primi_nan=N_GIORNI - 41)    # 40 rendimenti comuni
    assert out["portfolio"]["beta_vs_spy"] == pytest.approx(1.5, abs=0.01)
    assert 20 < out["beta_obs"] < pr.BETA_OBS_AFFIDABILE
    assert out["beta_note"] and str(out["beta_obs"]) in out["beta_note"]
    assert "indicativo" in out["beta_note"]


def test_beta_su_storia_piena_senza_riserva(motore_eur):
    out = motore_eur(1.5, 0.0)
    assert out["beta_obs"] >= pr.BETA_OBS_AFFIDABILE
    assert out["beta_note"] is None


def test_pdf_beta_con_riserva_la_stampa_in_lettura(tmp_path, monkeypatch):
    risk = {"portfolio": {"vol_annual_pct": 12.3, "sharpe": 0.9, "beta_vs_spy": 1.23,
                          "var_95_1d_pct": -1.4, "max_dd_1y_pct": -7.7},
            "beta_error": None, "beta_note": "beta su 33 giorni comuni: indicativo ZZNOTA"}
    testo = " ".join(_testo_pdf(tmp_path, risk, monkeypatch).split())
    assert "S&P 500 1.23 beta su 33 giorni comuni: indicativo ZZNOTA" in testo, testo[:2000]


def test_pdf_beta_nd_in_inglese_non_lascia_italiano(tmp_path, monkeypatch):
    from pypdf import PdfReader
    from bellomberg.reporting import pdf_institutional
    monkeypatch.setattr(pdf_institutional, "REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(pdf_institutional, "_gen_charts", lambda *a: (None, None, None))
    risk = {"portfolio": {"vol_annual_pct": 12.3, "sharpe": 0.9, "beta_vs_spy": None,
                          "var_95_1d_pct": -1.4, "max_dd_1y_pct": -7.7}}
    out = pdf_institutional.build_institutional_memo("# Test memo\n\nBLUF: fake.", risk_data=risk,
                                                     output_path=str(tmp_path / "en.pdf"), language="en")
    testo = " ".join(" ".join((p.extract_text() or "") for p in PdfReader(out).pages).split())
    assert "S&P 500 n/a reason unavailable" in testo, testo[:2000]
    assert "motivo" not in testo


# ------------------------------------------- a) quant_score: la riga Beta non sparisce

_RISK_SENZA_BETA = {"portfolio": {"vol_annual_pct": 22.5, "sharpe": 0.78, "beta_vs_spy": None,
                                  "var_95_1d_pct": -3.2, "max_dd_1y_pct": -17.4},
                    "beta_error": "SPY non disponibile: motivo ZZFINTO"}
# fix score 09/10 (Opus 5.5): la vol si legge contro il TARGET del mandato e la coda contro il
# budget di stress; senza mandato la vol e' n.d. dichiarata. Mandato SINTETICO.
_MANDATO_Q = {"rischio": {"volatilita_target_pct": 20, "stress_gfc_pct": 25}}


def test_quant_score_dichiara_beta_nd_col_motivo_senza_punti():
    from bellomberg.agents.specialist_scores import quant_score
    s = quant_score({"positions": []}, _RISK_SENZA_BETA, mandato=_MANDATO_Q)
    beta = [r for r in s["lines"] if "Beta" in r[0]]
    assert len(beta) == 1, s["lines"]
    assert "n.d." in beta[0][1] and "ZZFINTO" in beta[0][1]
    assert beta[0][2] is None
    # fix score 09/10: Sharpe, VaR e max DD sono INFORMATIVI (performance trailing / stessa
    # dispersione della vol): l'unica metrica punteggiata qui e' la vol contro il target
    assert s["max_score"] == 1 * 3


def test_quant_score_beta_misurato_resta_punteggiato():
    from bellomberg.agents.specialist_scores import quant_score
    risk = {**_RISK_SENZA_BETA, "portfolio": {**_RISK_SENZA_BETA["portfolio"], "beta_vs_spy": 1.18}}
    s = quant_score({"positions": []}, risk, beta_reconcile=_GUARDRAIL_OK, mandato=_MANDATO_Q)
    beta = [r for r in s["lines"] if "Beta" in r[0]]
    assert beta and beta[0][1] == "1.18" and beta[0][2] is not None
    assert s["max_score"] == 2 * 3


def test_blocco_contesto_quant_porta_la_riga_nd_senza_typeerror():
    from bellomberg.agents.specialist_scores import quant_score, format_score_block
    blocco = format_score_block(quant_score({"positions": []}, _RISK_SENZA_BETA, mandato=_MANDATO_Q))
    riga = [r for r in blocco.splitlines() if "Beta" in r and "non punteggiata" in r]
    assert len(riga) == 1 and "ZZFINTO" in riga[0], blocco


# ------------------------------- quant_score: NESSUNA metrica sparisce in silenzio (main 04/10)

_PORT_PIENO = {"vol_annual_pct": 22.5, "sharpe": 0.78, "beta_vs_spy": 1.18,
               "var_95_1d_pct": -3.2, "max_dd_1y_pct": -17.4}
# 06/10 (PM): la beta pesa nel punteggio quant SOLO con guardrail RECONCILED: questi test
# misurano il rubric con la beta in gioco, quindi passano il verdetto esplicito
_GUARDRAIL_OK = {"verdict": "RECONCILED", "beta_per_decisioni": True,
                 "betas": {"portfolio_risk_spy": 1.18, "factor_model_mkt": 1.1}}  # R-SEG: il motore del rischio e' fra i riconciliati
_POSIZIONI = {"positions": [{"ticker": "ZZTEST", "valore_mercato_eur": 600.0},
                            {"ticker": "QQSYN.MI", "valore_mercato_eur": 400.0}],
              "cash_disponibile_eur": 0.0}
# fix score 09/10: coda e cluster SINTETICI (replay GFC -15% del book, primo cluster 60%)
_STRESS_Q = {"stress_scenario": "gfc_2008", "stress_fallback": False,
             "stress_meta": {"window_loss_pct": -15.0, "proxied": {}}}
_SETTORI_Q = {"econ_axis": {"by_bucket": [{"bucket": "ZZSettore", "weight_pct": 60.0},
                                          {"bucket": "QQSettore", "weight_pct": 40.0}], "coverage_pct": 100.0}}
_NEGOZIO_Q = {"veicoli": {}, "origine": "sintetico"}
_TUTTO_Q = dict(mandato=_MANDATO_Q, stress_data=_STRESS_Q, sector_data=_SETTORI_Q, negozio=_NEGOZIO_Q)


def test_quant_score_vol_mancante_dichiarata_senza_punti():
    from bellomberg.agents.specialist_scores import quant_score, format_score_block
    from bellomberg.core.language import language_context
    with language_context("it"):
        s = quant_score(_POSIZIONI, {"portfolio": {**_PORT_PIENO, "vol_annual_pct": None}},
                        beta_reconcile=_GUARDRAIL_OK, **_TUTTO_Q)
        blocco = format_score_block(s)
    riga = [r for r in s["lines"] if "Vol" in r[0]]
    assert len(riga) == 1, s["lines"]
    assert riga[0][1] == "n.d.: dato non fornito dal tool" and riga[0][2] is None
    assert len(s["lines"]) == 6 and s["max_score"] == 5 * 3
    assert len(s["unscored"]) == 1 and "Vol" in s["unscored"][0]
    assert "Massimo ricalcolato su 5 metriche misurate" in blocco, blocco
    assert "dato non fornito dal tool" in blocco and "non punteggiata" in blocco


@pytest.mark.parametrize("chiave, etichetta", [
    ("sharpe", "Sharpe"), ("var_95_1d_pct", "VaR 95"), ("max_dd_1y_pct", "Max Drawdown")])
def test_quant_score_informativa_mancante_dichiarata(chiave, etichetta):
    # fix score 09/10: Sharpe/VaR/DD fuori punteggio ma MAI spariti: n.d. dichiarato nel blocco
    from bellomberg.agents.specialist_scores import quant_score, format_score_block
    from bellomberg.core.language import language_context
    with language_context("it"):
        s = quant_score(_POSIZIONI, {"portfolio": {**_PORT_PIENO, chiave: None}},
                        beta_reconcile=_GUARDRAIL_OK, **_TUTTO_Q)
        blocco = format_score_block(s)
    assert not [r for r in s["lines"] if etichetta in r[0]]          # mai punteggiata
    info = [r for r in s["info"] if etichetta in r[0]]
    assert len(info) == 1 and info[0][1] == "n.d.: dato non fornito dal tool", s["info"]
    assert s["unscored"] == [] and s["max_score"] == 6 * 3
    riga = [r for r in blocco.splitlines() if r.startswith("  - ") and etichetta in r]
    assert len(riga) == 1 and "fuori punteggio" in riga[0] and "n.d." in riga[0], blocco


def test_quant_score_senza_pesi_top_e_hhi_dichiarati_col_motivo():
    from bellomberg.agents.specialist_scores import quant_score
    from bellomberg.core.language import language_context
    with language_context("it"):
        s = quant_score({"positions": []}, {"portfolio": dict(_PORT_PIENO)}, beta_reconcile=_GUARDRAIL_OK,
                        **_TUTTO_Q)
    for etichetta in ("Primo nome singolo", "HHI"):
        riga = [r for r in s["lines"] if etichetta in r[0]]
        assert len(riga) == 1 and riga[0][2] is None, s["lines"]
        assert "pesi delle posizioni non disponibili" in riga[0][1]
    # misurate: vol, replay, beta, cluster
    assert s["max_score"] == 4 * 3 and len(s["unscored"]) == 2


def test_quant_score_tutto_misurato_niente_unscored():
    from bellomberg.agents.specialist_scores import quant_score, format_score_block
    s = quant_score(_POSIZIONI, {"portfolio": dict(_PORT_PIENO)}, beta_reconcile=_GUARDRAIL_OK, **_TUTTO_Q)
    assert s["unscored"] == [] and s["max_score"] == 6 * 3 and len(s["lines"]) == 6
    assert "ricalcolato" not in format_score_block(s)
