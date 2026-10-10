"""Punteggio quant e guardrail beta (decisione PM 06/10): fuori da RECONCILED la beta del book
NON pesa nel punteggio deterministico del desk quant, e il punteggio dichiara perche'
(«beta esclusa: guardrail <verdetto>»). Il verdetto lo calcola la run UNA volta nel priming
(blackboard `_beta_reconcile`); quant_score non lo calcola mai. Assente/None/error = esclusa.
Numeri e ticker INVENTATI.
"""
from types import SimpleNamespace

import pytest

from bellomberg.core.language import language_context
# moduli VERI presi all'import: la fixture della run offline mette dei finti in sys.modules
from bellomberg.portfolio import advanced_metrics as _AM_VERO
from bellomberg.agents import specialist_scores as _SCORER_VERO

_PORT = {"vol_annual_pct": 22.5, "sharpe": 0.78, "beta_vs_spy": 1.18,
         "var_95_1d_pct": -3.2, "max_dd_1y_pct": -17.4}
_POS = {"positions": [{"ticker": "ZZTEST", "valore_mercato_eur": 600.0},
                      {"ticker": "QQSYN.MI", "valore_mercato_eur": 400.0}]}
_OK = {"verdict": "RECONCILED", "beta_per_decisioni": True,
       "betas": {"portfolio_risk_spy": 1.18, "factor_model_mkt": 1.1}}
# fix score 09/10 (Opus 5.5): la vol pesa contro il target del mandato (SINTETICO). Coda e
# cluster non passati = n.d. «non calcolato da questo percorso», fuori dal massimo. Metriche
# punteggiate qui: vol vs target, beta (se RECONCILED), primo nome singolo, HHI.
_MAND = {"rischio": {"volatilita_target_pct": 20, "stress_gfc_pct": 25}}


def _riga_beta(s):
    righe = [r for r in s["lines"] if "Beta" in r[0]]
    assert len(righe) == 1, s["lines"]
    return righe[0]


def test_reconciled_la_beta_pesa():
    from bellomberg.agents.specialist_scores import quant_score
    s = quant_score(_POS, mandato=_MAND, risk_data={"portfolio": dict(_PORT)}, beta_reconcile=_OK)
    r = _riga_beta(s)
    # fix score 09/10: punti CONTINUI, beta 1,18 fra le ancore 0,9 (1 punto) e 1,2 (2 punti)
    assert r[1] == "1.18" and r[2] == pytest.approx(1.93)
    assert s["max_score"] == 4 * 3 and s["excluded"] == {}
    assert s["metrics"]["beta_vs_spy"] == 1.18 and s["metrics"]["beta_guardrail"] == "RECONCILED"


@pytest.mark.parametrize("rb,verdetto,codice", [
    ({"verdict": "UNRELIABLE", "beta_per_decisioni": False}, "UNRELIABLE", "UNRELIABLE"),
    ({"verdict": "INSUFFICIENT_SOURCES", "beta_per_decisioni": False}, "INSUFFICIENT_SOURCES", "INSUFFICIENT_SOURCES"),
    ({"verdict": "RECONCILED"}, "RECONCILED senza via libera", "RECONCILED_SENZA_VIA_LIBERA"),   # payload incoerente
    (None, "non calcolato da questo percorso", "NON_CALCOLATO"),
    ({}, "non disponibile", "NON_DISPONIBILE"),
    ({"error": "RuntimeError"}, "non disponibile (RuntimeError)", "NON_DISPONIBILE"),
])
def test_fuori_da_reconciled_la_beta_non_pesa_e_si_dichiara(rb, verdetto, codice):
    from bellomberg.agents.specialist_scores import quant_score, format_score_block
    with language_context("it"):
        s = quant_score(_POS, mandato=_MAND, risk_data={"portfolio": dict(_PORT)}, beta_reconcile=rb)
        blocco = format_score_block(s)
    r = _riga_beta(s)
    assert r[2] is None
    assert r[1] == "n.d.: beta esclusa: guardrail " + verdetto
    assert "1.18" not in r[1]
    assert s["max_score"] == 3 * 3
    assert list(s["excluded"].values()) == ["beta esclusa: guardrail " + verdetto]
    # non e' un dato mancante: e' un'esclusione (replay e cluster qui non calcolati: n.d. a parte)
    assert not [u for u in s["unscored"] if "Beta" in u]
    assert s["metrics"]["beta_vs_spy"] is None and s["metrics"]["beta_guardrail"] == codice
    # il blocco per lo specialista dice esclusione, non «dato mancante»
    riga = [x for x in blocco.splitlines() if x.startswith("  - Beta")]
    assert len(riga) == 1 and "esclusa dal punteggio" in riga[0] and "dato mancante" not in riga[0], blocco
    assert "1.18" not in blocco
    assert "Massimo ricalcolato su 3 metriche: escluse dal punteggio per regola" in blocco, blocco


def test_esclusione_e_dato_mancante_insieme_contano_giusto():
    # beta esclusa + vol n.d. (fix score 09/10: lo Sharpe e' informativo, non piu' punteggiato):
    # misurate 2 (primo nome, HHI); n.d. vol + replay e cluster non calcolati; le frasi dicono 2
    from bellomberg.agents.specialist_scores import quant_score, format_score_block
    with language_context("it"):
        s = quant_score(_POS, mandato=_MAND, risk_data={"portfolio": {**_PORT, "vol_annual_pct": None}}, beta_reconcile={"verdict": "UNRELIABLE"})
        blocco = format_score_block(s)
    assert s["max_score"] == 2 * 3
    assert "Massimo ricalcolato su 2 metriche misurate: escluse perché n.d." in blocco
    assert "Massimo ricalcolato su 2 metriche: escluse dal punteggio per regola" in blocco


def test_quant_score_non_calcola_mai_il_guardrail(monkeypatch):
    # niente effetti nascosti: senza payload la beta e' esclusa, reconcile_betas non viene chiamato
    from bellomberg.agents.specialist_scores import quant_score
    from bellomberg.portfolio import advanced_metrics as am
    monkeypatch.setattr(am, "reconcile_betas", lambda *a, **k: pytest.fail("guardrail calcolato dentro quant_score"))
    with language_context("it"):
        s = quant_score(_POS, mandato=_MAND, risk_data={"portfolio": dict(_PORT)})
    assert _riga_beta(s)[1] == "n.d.: beta esclusa: guardrail non calcolato da questo percorso"


def test_beta_non_misurata_resta_nd_col_suo_motivo():
    from bellomberg.agents.specialist_scores import quant_score
    s = quant_score(_POS, mandato=_MAND, risk_data={"portfolio": {**_PORT, "beta_vs_spy": None}, "beta_error": "SPY finto assente"},
                    beta_reconcile={"verdict": "UNRELIABLE"})
    r = _riga_beta(s)
    assert r[2] is None and "SPY finto assente" in r[1] and s["excluded"] == {}
    assert s["metrics"]["beta_guardrail"] is None


def test_in_inglese_la_dichiarazione_e_tradotta():
    from bellomberg.agents.specialist_scores import quant_score
    with language_context("en"):
        s = quant_score(_POS, mandato=_MAND, risk_data={"portfolio": dict(_PORT)}, beta_reconcile=None)
    assert _riga_beta(s)[1] == "n/a: beta excluded: guardrail not computed by this path"


@pytest.mark.parametrize("dati,atteso", [
    ({"_beta_reconcile": {"verdict": "INSUFFICIENT_SOURCES", "beta_per_decisioni": False}},
     "n.d.: beta esclusa: guardrail INSUFFICIENT_SOURCES"),
    ({"_beta_reconcile": dict(_OK)}, "1.18"),
    ({}, "n.d.: beta esclusa: guardrail non calcolato da questo percorso"),
])
def test_lo_specialista_quant_legge_il_guardrail_dalla_blackboard(monkeypatch, dati, atteso):
    from bellomberg.agents.specialists.quant import QuantSpecialist
    from bellomberg.agents import agent_tools
    from bellomberg.portfolio import portfolio_risk
    monkeypatch.setattr(agent_tools, "tool_get_portfolio_live", lambda: _POS)
    monkeypatch.setattr(portfolio_risk, "compute_portfolio_risk", lambda: {"portfolio": dict(_PORT)})
    # fix score 09/10: lo specialista passa anche mandato, replay GFC e settori; qui sintetici
    from bellomberg.core import mandato_pm
    from bellomberg.portfolio import portfolio_montecarlo, portfolio_sectors
    monkeypatch.setattr(mandato_pm, "carica", lambda: dict(_MAND))
    monkeypatch.setattr(portfolio_montecarlo, "run_monte_carlo", lambda **_k: {"error": "replay finto assente"})
    monkeypatch.setattr(portfolio_sectors, "compute_sector_exposure", lambda **_k: {"error": "settori finti assenti"})
    spec = object.__new__(QuantSpecialist)
    spec.blackboard = SimpleNamespace(data=dati)
    with language_context("it"):
        s = spec.compute_score()
    assert _riga_beta(s)[1] == atteso


# ------------------------------------------------- cablaggio: run offline vera

from test_cablaggio_consigliere_multi import run_offline, _DeskQuant  # noqa: E402,F401


def test_la_run_calcola_il_guardrail_una_volta_e_il_quant_lo_vede(run_offline, monkeypatch):
    import sys
    from bellomberg.agents import consigliere_multi as cm, agent_tools
    from bellomberg.agents.specialists.quant import QuantSpecialist
    vero = _AM_VERO
    finto = sys.modules["bellomberg.portfolio.advanced_metrics"]
    chiamate = []
    rb = {"verdict": "UNRELIABLE", "beta_per_decisioni": False, "betas": {}, "threshold": .35}
    monkeypatch.setattr(finto, "reconcile_betas", lambda: chiamate.append(1) or rb, raising=False)
    monkeypatch.setattr(finto, "testo_guardrail_beta_capo", vero.testo_guardrail_beta_capo, raising=False)
    monkeypatch.setattr(sys.modules["bellomberg.portfolio.portfolio_risk"], "compute_portfolio_risk",
                        lambda: {"portfolio": dict(_PORT)})
    monkeypatch.setattr(agent_tools, "tool_get_portfolio_live", lambda: _POS)
    # fix score 09/10: lo specialista chiede anche replay GFC e settori: finti, zero rete (il mandato e' quello della fixture)
    import importlib
    monkeypatch.setattr(sys.modules.get("bellomberg.portfolio.portfolio_montecarlo")
                        or importlib.import_module("bellomberg.portfolio.portfolio_montecarlo"),
                        "run_monte_carlo", lambda **_k: {"error": "replay finto assente"}, raising=False)
    monkeypatch.setattr(sys.modules.get("bellomberg.portfolio.portfolio_sectors")
                        or importlib.import_module("bellomberg.portfolio.portfolio_sectors"),
                        "compute_sector_exposure", lambda **_k: {"error": "settori finti assenti"}, raising=False)
    # la fixture sostituisce specialist_scores con un finto: il quant usa lo scorer VERO
    scorer_vero = _SCORER_VERO
    monkeypatch.setattr(sys.modules["bellomberg.agents.specialist_scores"], "quant_score",
                        scorer_vero.quant_score, raising=False)
    visti = []
    orig_run = _DeskQuant.run

    def _run_con_score(self, round_n):
        if round_n == 1:    # in R1 il desk quant calcola il suo punteggio come il vero
            spec = object.__new__(QuantSpecialist)
            spec.blackboard = self.bb
            visti.append(spec.compute_score())
        return orig_run(self, round_n)
    monkeypatch.setattr(_DeskQuant, "run", _run_con_score)
    with language_context("it"):
        cm.run_multi_agent()
    assert chiamate == [1], "il guardrail va calcolato UNA volta per run (priming)"
    assert visti and _riga_beta(visti[0])[1] == "n.d.: beta esclusa: guardrail UNRELIABLE"
    ctx = run_offline.catturato["sizing_context"]
    assert "verdetto: UNRELIABLE" in ctx and "vietati verdetti di hedge basati sul beta" in ctx
    assert run_offline.catturato["bb"].data["_beta_reconcile"]["verdict"] == "UNRELIABLE"


class _Crash(BaseException):
    pass


@pytest.mark.parametrize("checkpoint_vecchio", [False, True])
def test_in_ripresa_il_guardrail_del_priming_si_riusa(run_offline, monkeypatch, checkpoint_vecchio):
    # ripresa dopo un crash oltre il priming: il verdetto del checkpoint si RIUSA (nessun
    # ricalcolo); checkpoint scritto prima della regola 06/10 (senza la chiave) = ricalcolato UNA volta
    import sys
    from bellomberg.agents import consigliere_multi as cm
    vero = _AM_VERO
    from bellomberg.storage.weekly_run_store import WeeklyRunStore
    from test_weekly_recovery import _research_contract, _store
    _research_contract(run_offline, monkeypatch)
    finto = sys.modules["bellomberg.portfolio.advanced_metrics"]
    chiamate = []
    rb = {"verdict": "UNRELIABLE", "beta_per_decisioni": False, "betas": {}, "threshold": .35}
    monkeypatch.setattr(finto, "reconcile_betas", lambda: chiamate.append(1) or rb, raising=False)
    monkeypatch.setattr(finto, "testo_guardrail_beta_capo", vero.testo_guardrail_beta_capo, raising=False)
    if checkpoint_vecchio:
        nativo = WeeklyRunStore.complete

        def complete_senza_chiave(self, stage, payload, bb=None):
            if stage == "priming":
                payload = {k: v for k, v in payload.items() if k != "beta_reconcile"}
            return nativo(self, stage, payload, bb)
        monkeypatch.setattr(WeeklyRunStore, "complete", complete_senza_chiave)
    run_round_nativo = cm.run_round
    caduti = []

    def run_round_che_cade(bb, round_n):
        if not caduti:
            caduti.append(round_n)
            if checkpoint_vecchio:   # la blackboard salvata non deve salvare la run: simula il vecchio
                bb.data.pop("_beta_reconcile", None)
            raise _Crash("crash sintetico dopo il priming")
        return run_round_nativo(bb, round_n)
    monkeypatch.setattr(cm, "run_round", run_round_che_cade)
    with pytest.raises(_Crash):
        cm.run_multi_agent(send_email=False)
    assert chiamate == [1]
    store = _store()
    assert store.get("priming") is not None
    assert ("beta_reconcile" in store.get("priming")) is (not checkpoint_vecchio)
    with language_context("it"):
        cm.run_multi_agent(resume_memo_id=store.memo_id, authorize_new_ai=True, send_email=False)
    assert chiamate == ([1, 1] if checkpoint_vecchio else [1])
    assert "verdetto: UNRELIABLE" in run_offline.catturato["sizing_context"]


# ------------------------------------------------- review R-SEG (06/10)

_RB_SENZA_RISCHIO = {"verdict": "RECONCILED", "beta_per_decisioni": True,
                     "betas": {"advanced_metrics_twr": 0.9, "factor_model_mkt": 1.0}}


@pytest.mark.parametrize("extra,perche", [
    ({"sources_insufficient": {"portfolio_risk_spy": {"beta": 1.6, "n_obs": 30, "min_obs": 60, "reason": "x"}}},
     "insufficiente: 30/60 osservazioni"),
    ({"sources_insufficient": {"portfolio_risk_spy": {"beta": 1.6, "n_obs": None, "min_obs": 60, "reason": "x"}}},
     "insufficiente: n.d./60 osservazioni"),
    ({"sources_failed": {"portfolio_risk_spy": "rischio finto KO"}}, "fallita"),
    ({}, "assente"),
])
def test_reconciled_senza_il_motore_del_rischio_la_beta_non_pesa(extra, perche):
    # C1: RECONCILED fra gli ALTRI due motori non certifica la beta di portfolio_risk
    from bellomberg.agents.specialist_scores import quant_score
    with language_context("it"):
        s = quant_score(_POS, mandato=_MAND, risk_data={"portfolio": {**_PORT, "beta_vs_spy": 1.6}}, beta_reconcile={**_RB_SENZA_RISCHIO, **extra})
    r = _riga_beta(s)
    assert r[2] is None and "1.6" not in r[1]
    assert s["metrics"]["beta_guardrail"] == "RECONCILED_SENZA_FONTE_RISCHIO"
    assert r[1] == ("n.d.: beta esclusa: guardrail RECONCILED senza la fonte della beta di rischio "
                    "(portfolio_risk_spy " + perche + ")")
    assert s["max_score"] == 3 * 3 and s["metrics"]["beta_vs_spy"] is None


def test_errore_e_non_calcolato_sono_frasi_diverse():
    # C2: «non calcolato da questo percorso» (nessun guasto) != «non disponibile (errore)»
    from bellomberg.agents.specialist_scores import quant_score
    with language_context("it"):
        a = _riga_beta(quant_score(_POS, mandato=_MAND, risk_data={"portfolio": dict(_PORT)}, beta_reconcile=None))[1]
        b = _riga_beta(quant_score(_POS, mandato=_MAND, risk_data={"portfolio": dict(_PORT)}, beta_reconcile={"error": "TimeoutError"}))[1]
    assert a == "n.d.: beta esclusa: guardrail non calcolato da questo percorso"
    assert b == "n.d.: beta esclusa: guardrail non disponibile (TimeoutError)"


def test_calcolo_del_guardrail_data_e_log_prima(monkeypatch):
    # S2 + S3: calcolato_il nel payload (anche di errore); riga di log PRIMA della chiamata
    from datetime import datetime
    from bellomberg.agents import consigliere_multi as cm
    eventi = []
    monkeypatch.setattr(cm, "_log", lambda m: eventi.append("log:" + m))
    monkeypatch.setattr(_AM_VERO, "reconcile_betas", lambda: eventi.append("CALCOLO") or {"verdict": "UNRELIABLE"})
    rb = cm._calcola_guardrail_beta()
    assert rb["calcolato_il"][:10] == datetime.now().date().isoformat()
    i = eventi.index("CALCOLO")
    assert i >= 1 and "calcolo" in eventi[i - 1], eventi

    def _rotto():
        raise TimeoutError("rete finta lenta")
    monkeypatch.setattr(_AM_VERO, "reconcile_betas", _rotto)
    rb = cm._calcola_guardrail_beta()
    assert rb["error"] == "TimeoutError" and rb["calcolato_il"][:10] == datetime.now().date().isoformat()


@pytest.mark.parametrize("quando,citato", [("2026-01-02T10:00:00", True), (None, False)])
def test_capo_dichiara_guardrail_di_un_altro_giorno(run_offline, monkeypatch, quando, citato):
    import sys
    from datetime import datetime
    from bellomberg.agents import consigliere_multi as cm
    finto = sys.modules["bellomberg.portfolio.advanced_metrics"]
    monkeypatch.setattr(finto, "testo_guardrail_beta_capo", _AM_VERO.testo_guardrail_beta_capo, raising=False)
    monkeypatch.setattr(finto, "reconcile_betas", lambda: {"verdict": "UNRELIABLE", "beta_per_decisioni": False,
                                                           "betas": {}, "threshold": .35}, raising=False)
    nativo = cm._calcola_guardrail_beta
    data = quando or datetime.now().isoformat(timespec="seconds")
    monkeypatch.setattr(cm, "_calcola_guardrail_beta", lambda: dict(nativo(), calcolato_il=data))
    cm.run_multi_agent()
    ctx = run_offline.catturato["sizing_context"]
    assert ("NB: guardrail calcolato il 2026-01-02" in ctx) is citato, ctx[-500:]
    assert "vietati verdetti di hedge basati sul beta" in ctx


@pytest.mark.parametrize("rb", [None, {}, {"error": "RuntimeError"}, {"verdict": "UNRELIABLE"},
                                {"verdict": "RECONCILED"}, _RB_SENZA_RISCHIO, _OK])
def test_metrics_identici_in_ogni_lingua(rb):
    # metrics = codici stabili; il testo localizzato solo nelle righe/blocco
    from bellomberg.agents.specialist_scores import quant_score
    it = quant_score(_POS, mandato=_MAND, risk_data={"portfolio": dict(_PORT)}, beta_reconcile=rb, language="it")
    en = quant_score(_POS, mandato=_MAND, risk_data={"portfolio": dict(_PORT)}, beta_reconcile=rb, language="en")
    assert it["metrics"] == en["metrics"]
    assert it["metrics"]["beta_guardrail"] in ("RECONCILED", "UNRELIABLE", "NON_CALCOLATO", "NON_DISPONIBILE",
                                               "RECONCILED_SENZA_VIA_LIBERA", "RECONCILED_SENZA_FONTE_RISCHIO")
