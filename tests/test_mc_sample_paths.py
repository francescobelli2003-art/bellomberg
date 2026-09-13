"""Test OFFLINE 26/07 sera (Opus 5): `sample_paths_n` sul Monte Carlo.

Richiesta PM via ponte ("perche' solo 10 traiettorie?"): F5 ne vuole 100-200.
Il punto delicato NON e' spedirne di piu' — sono righe di `cum` gia' calcolate —
ma DOVE finiscono: `sample_paths` alimenta anche il tool result degli agenti
(chat_tools.get_portfolio_montecarlo) e il fan chart del memo PDF
(charts_quant.chart_mc_fan, che disegna OGNI path ricevuto). Duecento traiettorie
la' dentro = contesto gonfiato in una run da ~10 EUR + grafico del memo illeggibile.

Quindi: default del motore fermo a 10, alzano solo gli endpoint di F5. Qui si
verifica che questa separazione regga davvero, e che non sia una cortesia da
ricordarsi a mano.

Zero rete: `_download_returns` e le holding sono stubbate.
"""
import ast
import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from bellomberg.core.language import language_context

import bellomberg.portfolio.portfolio_montecarlo as pm

REPO = Path(__file__).resolve().parents[1]


class _FakeDB:
    def get_portfolio_summary(self):
        return {"positions": [], "totale_valore_mercato_eur": 100000.0}


@pytest.fixture
def mc_offline(monkeypatch):
    rng = np.random.default_rng(404)
    idx = pd.bdate_range("2022-01-03", periods=400)
    rdf = pd.DataFrame({"AAA": rng.normal(0, 0.01, 400),
                        "BBB": rng.normal(0, 0.015, 400)}, index=idx)
    monkeypatch.setattr(pm, "MemoryDB", _FakeDB)
    monkeypatch.setattr(pm, "_get_holdings_weights",
                        lambda salta: ({"AAA": 0.6, "BBB": 0.4}, 100000.0))
    monkeypatch.setattr(pm, "_download_returns", lambda *a, **k: rdf)
    pm.invalidate_cache()
    yield lambda **kw: pm.run_monte_carlo(
        n_sims=1000, horizon_days=60, method="block_bootstrap", **kw)
    pm.invalidate_cache()


# --- 1. il default deve restare basso: e' quello che vedono agenti e PDF -------

def test_default_del_motore_resta_10():
    assert inspect.signature(pm.run_monte_carlo).parameters["sample_paths_n"].default == 10
    assert inspect.signature(pm.run_monte_carlo_v3).parameters["sample_paths_n"].default == 10


def test_col_default_arrivano_10_traiettorie(mc_offline):
    out = mc_offline(force_refresh=True)
    assert "error" not in out, out.get("error")
    assert len(out["sample_paths"]) == 10
    assert out["sample_paths_n"] == 10


# --- 2. chi ne chiede di piu' le riceve, e il payload lo DICHIARA -------------

def test_duecento_traiettorie_su_richiesta(mc_offline):
    out = mc_offline(force_refresh=True, sample_paths_n=200)
    assert len(out["sample_paths"]) == 200
    # dichiarato: il consumatore puo' scrivere "200 su 1000 simulate", non far
    # credere che il campione sia tutto
    assert out["sample_paths_n"] == 200
    assert out["n_sims"] == 1000
    # ogni traiettoria resta sulla griglia di giorni dichiarata (contratto invariato)
    assert all(len(p) == len(out["sample_paths_days"]) for p in out["sample_paths"])


def test_tetto_a_500_e_pavimento_a_1(mc_offline):
    assert len(mc_offline(force_refresh=True, sample_paths_n=99999)["sample_paths"]) == 500
    assert len(mc_offline(force_refresh=True, sample_paths_n=0)["sample_paths"]) == 1


# --- 3. i NUMERI non si muovono: e' solo quante se ne spediscono --------------

def test_i_numeri_non_dipendono_da_quante_traiettorie_si_spediscono(mc_offline):
    # stesso seed -> stessa simulazione: cambiare sample_paths_n non puo' toccare
    # un solo numero del payload (garanzia chiesta prima della run V6)
    a = mc_offline(force_refresh=True, seed=11, sample_paths_n=10)
    b = mc_offline(force_refresh=True, seed=11, sample_paths_n=200)
    for k in ("median_return_pct", "expected_return_pct", "var_99_pct", "es_99_pct",
              "max_drawdown_median_pct", "prob_negative_pct", "prob_loss_20pct"):
        assert a[k] == b[k], f"{k} cambiato: {a[k]} vs {b[k]}"
    assert a["percentiles_eur"] == b["percentiles_eur"]
    assert a["fan_bands"] == b["fan_bands"]


# --- 4. la cache non deve scambiare le due chiamate --------------------------

def test_la_cache_serve_conteggi_diversi_dalla_stessa_simulazione(mc_offline):
    # `sample_paths_n` NON sta nella chiave di cache, di proposito (review 26/07):
    # la cache tiene il pool e il campione si affetta all'uscita. Cosi' F5 (200) e
    # il tool degli agenti (10), identici in tutto il resto, leggono LA STESSA
    # simulazione — altrimenti senza seed sarebbero due Monte Carlo diversi e il
    # PM leggerebbe un ES99 su F5 e un altro nel memo.
    grande = mc_offline(force_refresh=True, sample_paths_n=200)
    piccolo = mc_offline(sample_paths_n=10)           # NON forza: deve pescare in cache
    assert len(grande["sample_paths"]) == 200
    assert len(piccolo["sample_paths"]) == 10
    # stessa simulazione: ogni numero coincide
    for k in ("median_return_pct", "var_99_pct", "es_99_pct", "prob_negative_pct"):
        assert grande[k] == piccolo[k], f"{k}: due simulazioni diverse ({grande[k]} vs {piccolo[k]})"
    assert grande["fan_bands"] == piccolo["fan_bands"]
    # e le 10 traiettorie sono un sottoinsieme vero delle 200, non un altro campione
    assert all(p in grande["sample_paths"] for p in piccolo["sample_paths"])
    # la vista non intacca l'entry in cache: chi richiede 200 dopo li ritrova
    ancora = mc_offline(sample_paths_n=200)
    assert len(ancora["sample_paths"]) == 200


def test_la_vista_non_muta_lentry_in_cache(mc_offline):
    grande = mc_offline(force_refresh=True, sample_paths_n=200)
    piccolo = mc_offline(sample_paths_n=1)
    assert len(piccolo["sample_paths"]) == 1
    assert piccolo["sample_paths_n"] == 1
    # l'oggetto restituito prima non deve essere stato tosato
    assert len(grande["sample_paths"]) == 200
    assert grande["sample_paths_n"] == 200


# --- 5. guardie strutturali: chi NON deve gonfiarsi, non si gonfia -----------

def _kwargs_della_chiamata(path, fname):
    """nomi dei kwargs passati a `fname` in un file, per ogni call site."""
    src = (REPO / path).read_text(encoding="utf-8")
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call):
            nome = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if nome == fname:
                out.append({kw.arg for kw in n.keywords})
    return out


@pytest.mark.parametrize("path", ["src/bellomberg/agents/chat_tools.py",
                                  "src/bellomberg/reporting/charts_quant.py",
                                  "src/bellomberg/agents/consigliere_multi.py",
                                  "src/bellomberg/cli/regenerate_memo.py"])
def test_agenti_e_pdf_non_chiedono_traiettorie_in_piu(path):
    # Il tool degli agenti e il fan chart del memo devono restare al default.
    # Se un domani qualcuno aggiunge sample_paths_n qui, questo test lo ferma:
    # nel primo caso si pagano token veri in una run da ~10 EUR, nel secondo il
    # grafico del memo diventa una macchia grigia.
    for kwargs in _kwargs_della_chiamata(path, "run_monte_carlo"):
        assert "sample_paths_n" not in kwargs, (
            f"{path}: run_monte_carlo chiede sample_paths_n — "
            "agenti e PDF devono restare al default 10")


def test_gli_endpoint_di_f5_invece_le_chiedono():
    # il rovescio della guardia sopra: se qualcuno toglie il pass-through dagli
    # endpoint, F5 tornerebbe a 10 traiettorie in silenzio (nessun errore, solo
    # un grafico piu' povero — la classe di guasto che non si nota)
    for fname in ("run_monte_carlo", "run_monte_carlo_v3"):
        path = "src/bellomberg/api/bellomberg_api.py"
        siti = [kw for kw in _kwargs_della_chiamata(path, fname)]
        assert siti, f"nessuna chiamata a {fname} in bellomberg_api.py"
        assert all("sample_paths_n" in kw for kw in siti), \
            f"{fname}: endpoint senza sample_paths_n -> F5 tornerebbe a 10 zitto"


@pytest.mark.parametrize("quante,attese", [(200, 10), (500, 10), (10, 10), (4, 4)])
@pytest.mark.parametrize('language,xlabel', [('it', 'Giorni di negoziazione'), ('en', 'Trading days')])
def test_il_pdf_disegna_al_massimo_10_tracce(monkeypatch, quante, attese, language, xlabel):
    # difesa in profondita': anche se un domani al chart arrivasse un payload
    # ricco, il fan chart del memo non deve trasformarsi in una macchia grigia.
    # `_save` chiude la figura e restituisce un path: la si intercetta prima.
    import bellomberg.reporting.charts_quant as cq
    if not cq.MPL_OK:
        pytest.skip("matplotlib assente")
    catturata = {}
    monkeypatch.setattr(cq, "_save", lambda fig, name: catturata.setdefault("fig", fig) and "x")

    giorni = list(range(1, 21))
    mc = {
        "base_nav_eur": 100000.0,
        "fan_bands": {"days": giorni,
                      **{k: [100000.0 + i * 10 for i in range(20)]
                         for k in ("p5", "p10", "p25", "p50", "p75", "p90", "p95")}},
        "sample_paths": [[1.0 + j * 0.001 for j in range(20)] for _ in range(quante)],
        "sample_paths_days": giorni,
    }
    with language_context(language):
        cq.chart_mc_fan(mc)
    fig = catturata.get("fig")
    assert fig is not None, "chart_mc_fan non ha prodotto la figura"
    # le tracce campione sono le uniche disegnate con lw=0.45
    ax = [a for a in fig.axes if a.get_xlabel() == xlabel][0]
    tracce = [ln for ln in ax.get_lines() if ln.get_linewidth() == 0.45]
    assert len(tracce) == attese, f"il PDF ha disegnato {len(tracce)} tracce su {quante}"
    import matplotlib.pyplot as plt
    plt.close(fig)
