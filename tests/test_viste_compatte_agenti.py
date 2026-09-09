"""Test OFFLINE delle VISTE COMPATTE per gli agenti (26/07 pre-V6, Opus 5).

Il bug, MISURATO sul payload vero del 26/07 prima del fix: i `tool_result` dei
desk sono tagliati a 6000 char (`specialists/base.py:945`) e il taglio cade in
CODA, cioe' esattamente dove questi payload scrivono le loro dichiarazioni.

    get_portfolio_live   17.252 char -> arrivavano 10 posizioni su 27 (ordine DB,
                         non per valore) e cadevano nav_total_eur,
                         cash_disponibile_eur, totale_valore_mercato_eur,
                         totale_pl_eur. Quattro desk su sei (macro, fundamentals,
                         crypto, eventdesk) non hanno get_portfolio_risk e non
                         avevano NESSUN altro canale per quei numeri.
    get_tearsheet         7.993 char -> perdeva metrics (Sharpe/Sortino/Calmar/VaR),
                         regime_summary, risk_free_used, notes, basis.
    get_attribution       6.616 char -> perdeva notes/basis e tagliava a META' la
                         frase della riconciliazione.

Le viste compatte vivono SOLO sul percorso LLM. Gli endpoint HTTP restano pieni:
c'e' un test apposta qui sotto, perche' il frontend li consuma gia' in F2.

Zero rete, zero DB, zero LLM: le funzioni sono pure (dict -> dict).
"""
import inspect
import json
import os
import re

import pytest

import bellomberg.agents.chat_tools as ct


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _peso(o):
    return len(json.dumps(o, default=str, ensure_ascii=False))


def _posizione(i, valuta="EUR", stale=False, tesi=None):
    """Riga di book come la produce DAVVERO `memory_db.get_portfolio_summary`.

    ⚠️ La prima stesura metteva `valore_mercato_eur` su OGNI riga: e' un input
    che non esiste. `memory_db.py:947-949` fa `continue` sulle posizioni in EUR,
    quindi per loro quella chiave non viene MAI scritta (misurato sul book vero:
    8 righe su 27 ce l'hanno); sulle altre viene scritta e `valore_mercato` viene
    SOVRASCRITTO con lo stesso valore (`memory_db.py:964-965`). Con la fixture
    sbagliata il test non poteva vedere il difetto vero — 19 righe su 27 senza
    controvalore — perche' misurava un input inventato invece di quello reale.
    """
    p = {
        "ticker": f"TCK{i}.MI", "nome": f"Nome Societa Numero {i} SpA",
        "quantita": 100.0 + i, "prezzo_medio": 12.3456789 + i,
        "prezzo_live": 13.987654321 + i, "valuta": valuta,
        "valore_mercato": 1398.7654321 + i, "pl_eur": 165.4321 + i,
        "pl_pct": 13.40982 + i, "prev_close": 13.5, "prev_close_ts": "2026-07-24T17:30:00",
        "price_stale": stale, "price_source": "snapshot",
        "tesi": tesi or ("Tesi lunga del PM su questo nome, registrata a mano nel DB "
                         "e gia' consegnata per intero dal canale dedicato. " * 2),
        "data_apertura": "2026-02-01", "fx_to_eur": 1.0,
        "peso_pct": 3.70123 + i,
        # pl_eur_fx (28/08): chiavi SEMPRE presenti nel payload vero (None sui
        # n.d.); su una riga EUR fx_pl_eur e' 0.0 per costruzione e
        # pl_eur_fx == pl_eur. prev_close_source: su ogni riga dal 28/08 (101).
        "costo_eur_storico": 1233.3333 + i, "pl_eur_fx": 165.4321 + i,
        "pl_pct_fx": 13.40982 + i, "fx_pl_eur": 0.0, "fx_pl_note": None,
        "prev_close_source": "position_prices",
    }
    if valuta != "EUR":          # solo le NON-EUR portano anche la chiave _eur
        p["valore_mercato_eur"] = p["valore_mercato"]
    return p


def _book(n=27, stale_idx=()):
    return {
        "source": "memory_db", "fx_incomplete": None, "stale_positions": None,
        "n_positions": n,
        "positions": [_posizione(i, stale=(i in stale_idx)) for i in range(n)],
        "totale_valore_mercato_eur": 229562.51234,
        "cash_disponibile_eur": 26247.05678,
        # F43(1) 27/08: la fonte della cassa sta nel payload vero
        "cash_source": "portfolio.json", "cash_source_note": None,
        "nav_total_eur": 255809.56912,
        "totale_pl_eur": 1135.98123,
        # pl_eur_fx (28/08) + totale «come il broker» (31/08): in testa nel
        # payload vero, come li scrive memory_db.get_portfolio_summary
        "totale_pl_eur_fx": 1135.98123, "totale_fx_pl_eur": 0.0,
        "pl_fx_nd": None,
        "fx_pl_basis": "storico ricostruito dai trade (FX daily yfinance)",
        "realizzato_eur_vendite": 5587.04, "realizzato_vendite_n": 13,
        "dividendi_eur": 1874.0,
        "totale_aperto_piu_realizzato_eur": 8597.02,
        "totale_aperto_piu_realizzato_note": ("aperto + realizzato vendite + dividendi; "
                                              "il sito del broker ESCLUDE i dividendi"),
        "timestamp": "2026-07-26T17:00:00",
    }


# --------------------------------------------------------------------------
# 1. get_portfolio_live: il libro intero, coi totali, sotto il tetto
# --------------------------------------------------------------------------

def test_book_intero_sotto_il_tetto():
    out = ct._compatta_portfolio_live(_book(27))
    assert _peso(out) <= ct.TETTO_TOOL_RESULT, (
        f"la vista compatta sfora ancora il tetto ({_peso(out)} char): il desk "
        "tornerebbe a leggere un book troncato")
    assert len(out["positions"]) == 27, "nessuna posizione deve sparire"


def test_book_realistico_tiene_prezzo_medio():
    """Review Fable 5 del 01/08 (rilievo ALTA sulla voce 59): sul book VERO la
    degradazione scattava A TORTO — la soglia confrontava il peso SENZA la busta
    `_stamp` (~103 char) e con 200 di margine, e toglieva `prezzo_medio` da
    tutte le 27 righe quando il payload intero (vista+busta, 5.957) stava sotto
    i 6000. V6 e V7 hanno letto il book senza prezzo d'ingresso per questo —
    e senza prezzo d'ingresso la regola PM 16/07 (drawdown bilaterale) non e'
    applicabile. Il criterio vero e': vista + busta > tetto."""
    out = ct._compatta_portfolio_live(_book(27))
    con_pm = [r for r in out["positions"] if "prezzo_medio" in r]
    assert len(con_pm) == 27, (
        f"prezzo_medio presente su {len(con_pm)}/27 righe: la vista e' degradata "
        "su un book che, contata la busta, sta sotto il tetto")
    assert _peso(out) + ct._BUSTA_STAMP <= ct.TETTO_TOOL_RESULT, (
        "se questo fallisce, il book realistico sfora davvero: allora la "
        "degradazione e' legittima e il test va aggiornato con una misura nuova")


def test_i_totali_sopravvivono_al_taglio():
    """Non basta che ci siano: devono stare PRIMA delle posizioni, o un domani
    che il payload sforasse sarebbero di nuovo i primi a cadere."""
    out = ct._compatta_portfolio_live(_book(27))
    s = json.dumps(out, ensure_ascii=False)
    off_pos = s.find('"positions"')
    for k in ("nav_total_eur", "cash_disponibile_eur",
              "totale_valore_mercato_eur", "totale_pl_eur"):
        assert out[k] is not None, f"{k} mancante: era il buco del payload vecchio"
        assert 0 <= s.find(f'"{k}"') < off_pos, f"{k} deve stare PRIMA di positions"
    # F43(1) 27/08: la dichiarazione della cassa (fonte + motivo) viaggia con i
    # totali, prima delle posizioni — anche quando e' None (chiave presente)
    for k in ("cash_source", "cash_source_note"):
        assert k in out, f"{k} mancante: gli agenti non vedrebbero il buco di lettura della cassa"
        assert 0 <= s.find(f'"{k}"') < off_pos, f"{k} deve stare PRIMA di positions"


def test_ogni_riga_porta_il_suo_controvalore():
    """Il difetto trovato dalla review pre-commit: tenendo `valore_mercato_eur`
    (che esiste solo sulle righe NON-EUR) invece di `valore_mercato`, 19 posizioni
    su 27 arrivavano al desk senza valore in euro. Una vista nata per ridare i
    totali che si portava via il controvalore per riga sul 70% del book."""
    b = _book(10)
    b["positions"][2]["valuta"] = "USD"          # una riga con entrambe le chiavi
    b["positions"][2]["valore_mercato_eur"] = b["positions"][2]["valore_mercato"]
    out = ct._compatta_portfolio_live(b)
    senza = [r["ticker"] for r in out["positions"] if r.get("valore_mercato") is None]
    assert not senza, f"righe senza controvalore: {senza}"
    assert "fx_incomplete" in out["_vista"], (
        "va detto che `valore_mercato` e' in EUR TRANNE per i ticker in fx_incomplete")


def test_le_tesi_non_sono_duplicate_nel_payload():
    """Le tesi del PM viaggiano dal 16/07 su canale dedicato (pm_theses_block):
    dentro il book erano 3.909 char di duplicato che facevano sforare il tetto."""
    out = ct._compatta_portfolio_live(_book(27))
    assert all("tesi" not in r for r in out["positions"])
    assert "pm_theses_block" in out["_vista"] or "tesi" in out["_vista"], (
        "togliere un campo senza dichiararlo e' un fallback silenzioso")


def test_price_stale_dichiarato_solo_dove_e_vero():
    out = ct._compatta_portfolio_live(_book(27, stale_idx=(3, 9)))
    stali = [r["ticker"] for r in out["positions"] if r.get("price_stale")]
    assert stali == ["TCK3.MI", "TCK9.MI"]
    assert all("price_stale" not in r for r in out["positions"]
               if r["ticker"] not in stali), (
        "27 ripetizioni di price_stale=false sono zavorra: la dichiarazione serve "
        "dove il prezzo E' stantio")


def test_fonte_prezzi_aggregata_e_dichiarata():
    b = _book(5)
    b["positions"][0]["price_source"] = "proxy"
    out = ct._compatta_portfolio_live(b)
    assert "proxy (1/5)" in out["prezzi_fonte"] and "snapshot (4/5)" in out["prezzi_fonte"], (
        "se le fonti sono MISTE il payload deve dirlo: un proxy non dichiarato "
        "e' esattamente il fallback silenzioso vietato dal 14/07")


def test_il_pl_col_cambio_storico_arriva_al_desk():
    """Decisione PM 31/08: gli agenti allocano soldi veri e vedevano solo il P&L
    al cambio di oggi (IOTA -5,60% dove il numero vero era -4,50%)."""
    b = _book(5)
    b["positions"][1].update(valuta="USD", pl_eur_fx=100.0, pl_pct_fx=9.99,
                             fx_pl_eur=-40.0,
                             fx_pl_note="FX USD del 2026-02-02 usato per il trade del 2026-02-01")
    out = ct._compatta_portfolio_live(b)
    r = out["positions"][1]
    assert r["pl_eur_fx"] == 100.0 and r["pl_pct_fx"] == 9.99 and r["fx_pl_eur"] == -40.0
    assert "2026-02-02" in r["fx_pl_note"]


def test_fx_pl_note_dichiarata_solo_dove_ce():
    out = ct._compatta_portfolio_live(_book(6))          # fixture: tutte None
    assert all("fx_pl_note" not in r for r in out["positions"]), (
        "6 ripetizioni di fx_pl_note=null sono zavorra: la nota serve dove il "
        "numero e' n.d. o approssimato")


def test_i_totali_fx_e_il_totale_broker_stanno_prima_delle_posizioni():
    out = ct._compatta_portfolio_live(_book(27))
    s = json.dumps(out, ensure_ascii=False)
    off_pos = s.find('"positions"')
    for k in ("totale_pl_eur_fx", "totale_fx_pl_eur", "pl_fx_nd", "fx_pl_basis",
              "realizzato_eur_vendite", "realizzato_vendite_n", "dividendi_eur",
              "totale_aperto_piu_realizzato_eur", "totale_aperto_piu_realizzato_note"):
        assert k in out, f"{k} mancante: la decisione PM 31/08 non e' arrivata alla vista"
        assert 0 <= s.find(f'"{k}"') < off_pos, f"{k} deve stare PRIMA di positions"


def test_un_totale_nd_resta_nd_non_diventa_zero():
    """pl_fx_nd: i totali fx sono None DICHIARATO — se la vista li rendesse 0.0
    il desk leggerebbe «P&L zero» dove il dato non esiste (classe 14/07)."""
    b = _book(5)
    b["totale_pl_eur_fx"] = None
    b["totale_fx_pl_eur"] = None
    b["pl_fx_nd"] = ["TCK1.MI"]
    b["totale_aperto_piu_realizzato_eur"] = None
    # review 31/08 (finding 2): anche realizzato e dividendi possono essere None
    # veri (ramo except, realized_eur NULL, dividendo non-EUR)
    b["realizzato_eur_vendite"] = None
    b["dividendi_eur"] = None
    out = ct._compatta_portfolio_live(b)
    assert out["totale_pl_eur_fx"] is None
    assert out["totale_fx_pl_eur"] is None
    assert out["totale_aperto_piu_realizzato_eur"] is None
    assert out["realizzato_eur_vendite"] is None
    assert out["dividendi_eur"] is None
    assert out["pl_fx_nd"] == ["TCK1.MI"]


def test_un_nd_per_riga_resta_una_chiave_null_dichiarata():
    """Review 31/08 (MEDIO): la differenza fra «n.d. dichiarato» (chiave null +
    nota) e «campo inesistente» deve sopravvivere alla vista — un futuro trim
    anti-zavorra dei null per riga la cancellerebbe senza che nessun test cada."""
    b = _book(5)
    b["positions"][2].update(pl_eur_fx=None, pl_pct_fx=None, fx_pl_eur=None,
                             fx_pl_note="serie FX storica non disponibile (yfinance): costo storico n.d.")
    b["pl_fx_nd"] = ["TCK2.MI"]
    out = ct._compatta_portfolio_live(b)
    r = out["positions"][2]
    for k in ("pl_eur_fx", "pl_pct_fx", "fx_pl_eur"):
        assert k in r and r[k] is None, f"{k}: il n.d. dichiarato deve restare una chiave null"
    assert "yfinance" in r["fx_pl_note"]


def test_i_campi_fx_esclusi_sono_nominati_e_la_decisione_non_e_piu_in_sospeso():
    v = ct._compatta_portfolio_live(_book(5))["_vista"]
    assert "costo_eur_storico" in v, "un campo tolto senza nome e' un taglio zitto"
    assert "in sospeso" not in v, (
        "la decisione PM e' presa (31/08): la vista non puo' dichiararla in sospeso")


def test_book_gigante_degrada_dichiarando():
    """Se il book cresce fino a sforare comunque, si toglie un campo e LO SI DICE."""
    out = ct._compatta_portfolio_live(_book(200))
    assert all("prezzo_medio" not in r for r in out["positions"])
    assert "prezzo_medio" in out["_vista"] and "ATTENZIONE" in out["_vista"]
    assert len(out["positions"]) == 200, "degradare non vuol dire perdere posizioni"


def test_errore_passa_intatto():
    err = {"error": "DB non raggiungibile"}
    assert ct._compatta_portfolio_live(err) is err, "non si compatta un guasto"
    assert ct._compatta_tearsheet(err) is err
    assert ct._compatta_attribution(err) is err


# --------------------------------------------------------------------------
# 2. get_tearsheet: le serie rolling riassunte, i caveat intatti
# --------------------------------------------------------------------------

def _tearsheet():
    n = 102
    return {
        "period": {"da": "2026-02-01", "a": "2026-07-24"},
        "monthly": [{"mese": "2026-02", "ret_pct": 1.234567}],
        "drawdowns": {"top": [{"depth_pct": -11.771234, "open": False}]},
        "rolling": {
            "w30": {"window_days": 30,
                    "dates": [f"2026-03-{(i % 28) + 1:02d}" for i in range(n)],
                    "vol_annual_pct": [25.0 + i * 0.01 for i in range(n)],
                    "sharpe": [-2.29 + i * 0.02 for i in range(n)]},
        },
        "metrics": {"sharpe": 1.4812345, "sortino": 2.11, "calmar": 0.9,
                    "var_95_1d_pct": -1.87},
        "regime_summary": "vol in calo",
        "risk_free_used": 2.15,
        "notes": ["prima nota"],
        "basis": "serie = twr_index di twr_engine, transizione ricostruito->official",
    }


def test_tearsheet_sotto_il_tetto_coi_caveat_dentro():
    out = ct._compatta_tearsheet(_tearsheet())
    assert _peso(out) <= ct.TETTO_TOOL_RESULT
    for k in ("metrics", "notes", "basis", "regime_summary", "risk_free_used"):
        assert k in out, f"{k} era proprio cio' che il taglio mangiava"


def test_il_tearsheet_non_altera_nessun_numero():
    """Trovato dalla review pre-commit: l'arrotondamento a 2 decimali valeva 32
    char e trasformava `risk_free_used` — che il modulo scrive come FRAZIONE —
    da 0,0324 a 0,03, cioe' dichiarava al modello un risk-free del 3,00% invece
    del 3,24%. Qui si pretende che il tearsheet esca coi numeri del modulo."""
    t = _tearsheet()
    t["risk_free_used"] = 0.0324
    out = ct._compatta_tearsheet(t)
    assert out["risk_free_used"] == 0.0324, "un tasso non si arrotonda per 32 char"
    assert out["metrics"]["sharpe"] == 1.4812345, "le metriche escono come le produce il modulo"
    assert out["monthly"][0]["ret_pct"] == 1.234567


def test_rolling_riassunto_non_amputato():
    out = ct._compatta_tearsheet(_tearsheet())
    w = out["rolling"]["w30"]
    assert w["n_punti"] == 102 and w["da"] and w["a"]
    vol = w["vol_annual_pct"]
    assert set(vol) >= {"ultimo", "min", "max", "medio", "n"}
    assert vol["min"] == 25.0 and vol["n"] == 102
    assert "_vista" in out and "rolling" in out["_vista"]


def test_serie_con_buchi_non_diventa_zero():
    t = _tearsheet()
    t["rolling"]["w30"]["sharpe"] = [1.0, None, 3.0]
    out = ct._compatta_tearsheet(t)
    s = out["rolling"]["w30"]["sharpe"]
    assert s["n"] == 2 and s["n_nd"] == 1, "i n.d. si contano, non si spalmano a zero"


# --------------------------------------------------------------------------
# 3. get_attribution: codifica SENZA PERDITA
# --------------------------------------------------------------------------

def _attribution():
    return {
        "period": {"label": "YTD"}, "portfolio_return_pct": 5.622,
        "by_position": [
            {"ticker": "ACME.MI", "contribution_pct": 5.09, "local_pct": 5.09,
             "fx_pct": 0.0, "cross_pct": 0.0, "avg_weight_pct": 6.69, "currency": "EUR"},
            {"ticker": "IOTA.L", "contribution_pct": -4.532, "local_pct": -4.839,
             "fx_pct": 0.32, "cross_pct": -0.012, "avg_weight_pct": 22.53,
             "currency": "GBX"},
            {"ticker": "XXX.MI", "contribution_pct": 1.0, "local_pct": 1.0,
             "fx_pct": None, "cross_pct": None, "avg_weight_pct": 2.0,
             "currency": "EUR"},
        ],
        "totals": {"local_pct": 4.547, "fx_pct": 1.07, "cross_pct": 0.004},
        "reconciliation": {"delta_pp": 0.849, "note": "basi DIVERSE dichiarate ..."},
        "notes": [], "basis": "pesi a inizio giorno, cash escluso",
        "_source": "portfolio_attribution.compute_attribution",
    }


def test_attribution_omette_solo_gli_zeri_esatti():
    out = ct._compatta_attribution(_attribution())
    eur, gbx, buco = out["by_position"]
    assert "fx_pct" not in eur and "cross_pct" not in eur and "local_pct" not in eur
    assert "currency" not in eur, "EUR e' la valuta base: si dichiara nella regola"
    assert gbx["fx_pct"] == 0.32 and gbx["local_pct"] == -4.839
    assert gbx["currency"] == "GBX", "una valuta diversa non si omette mai"
    # il caso che conta: un buco DICHIARATO non e' uno zero
    assert buco["fx_pct"] is None and buco["cross_pct"] is None, (
        "None == 0 e' falso: un dato mancante deve restare visibile, "
        "altrimenti sparisce dentro la regola di omissione")


def test_attribution_dichiara_la_regola_in_testa():
    out = ct._compatta_attribution(_attribution())
    assert list(out.keys())[0] == "_vista", (
        "la chiave per leggere le righe compatte deve essere la prima cosa che "
        "sopravvive, non l'ultima a cadere")
    for pezzo in ("fx_pct", "local_pct", "currency", "EUR"):
        assert pezzo in out["_vista"]


def test_attribution_non_perde_i_caveat():
    out = ct._compatta_attribution(_attribution())
    assert _peso(out) <= ct.TETTO_TOOL_RESULT
    assert out["reconciliation"]["delta_pp"] == 0.849
    assert out["basis"] and out["totals"]["cross_pct"] == 0.004


# --------------------------------------------------------------------------
# 4. La regola sull'arrotondamento (regola 14/07 applicata ai decimali)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("v", [0.004, -0.0009, 1e-7])
def test_un_non_zero_non_diventa_mai_zero(v):
    assert ct._arr(v) != 0.0, (
        f"{v} arrotondato a zero sparirebbe: e' il residuo cross che "
        "l'attribution dichiara APPOSTA per non spalmarlo")


def test_arrotondamento_normale_e_tipi_non_float():
    assert ct._arr(1.23456) == 1.23
    assert ct._arr(None) is None and ct._arr(True) is True and ct._arr("x") == "x"
    assert ct._arr(0.0) == 0.0


# --------------------------------------------------------------------------
# 5. GUARDIE DI CLASSE (le due che valgono piu' dei test sopra)
# --------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# get_portfolio_montecarlo (B.9 dossier 24, 03/08): a hz=63/252 il taglio in
# coda si mangiava TUTTE le metriche di rischio (2 chiamate su 29 nella run
# viva a metriche ZERO) — che la descrizione del tool promette esplicitamente.
# ---------------------------------------------------------------------------

_METRICHE_MC = [
    "expected_return_pct", "median_return_pct", "stdev_pct", "sharpe_simulated",
    "prob_negative_pct", "prob_loss_10pct", "prob_loss_20pct", "prob_gain_10pct",
    "prob_gain_20pct", "var_95_pct", "var_99_pct", "var_99_cornish_fisher_pct",
    "es_95_pct", "es_99_pct", "es_95_eur", "es_99_eur", "max_drawdown_p5_pct",
    "max_drawdown_median_pct", "max_drawdown_p95_pct",
]


def _mc_payload(hz=252, n_assets=27):
    """Payload come lo produce DAVVERO run_monte_carlo (portfolio_montecarlo.py:
    843-913): stesse chiavi, stesso ORDINE (fan_bands/terminal_hist PRIMA delle
    metriche, sample_paths in coda), taglie realistiche (band_step=hz//60 →
    64 punti a hz=252, 63 a hz=63; istogramma a 48 bin; 10 sample path)."""
    band_step = max(1, hz // 60)
    band_idx = sorted(set(list(range(0, hz, band_step)) + [hz - 1]))
    days = [d + 1 for d in band_idx]
    fan = {"days": days}
    for j, p in enumerate(("p5", "p10", "p25", "p50", "p75", "p90", "p95")):
        fan[p] = [round(74000.0 + j * 3100 + i * 17.0, 0) for i in range(len(days))]
    tick = [f"TCK{i}.MI" for i in range(n_assets)]
    return {
        "timestamp": "2026-08-03T10:00:00", "version": "v2", "method": "fhs",
        "method_description": ("Filtered Historical Simulation (GARCH + bootstrap "
                               "residuals) - bank-grade"),
        "drift_mode": "risk_neutral_rf", "stress_scenario": "none",
        "stress_requested": "none", "stress_fallback": False,
        "stress_meta": {"applied": "none", "fallback": False,
                        "window_loss_pct": None,
                        "note": "nessuno stress richiesto: distribuzione incondizionata"},
        "lookback_years": 3, "lookback_days_calibration": 756,
        "n_sims": 10000, "horizon_days": hz, "horizon_years": round(hz / 252, 2),
        "n_assets": n_assets,
        "calibration_note": "GARCH(1,1) per-asset; residui bootstrap a blocchi",
        "returns_basis": ("valuta LOCALE per-asset (FX non convertito, dichiarato): "
                          "i campi *_eur scalano sul NAV EUR"),
        "tickers_analyzed": tick, "removed_tickers": [], "added_tickers": [],
        "weights": {t: round(1.0 / n_assets, 4) for t in tick},
        "base_nav_eur": 93456.78,
        "base_nav_note": ("NAV del perimetro SIMULATO (somma valore_mercato dei "
                          "ticker analizzabili, posizioni SKIP escluse)"),
        "garch_fallback_assets": [],
        "percentiles_ratio": {"p5": 0.81, "p25": 0.93, "p50": 1.02,
                              "p75": 1.12, "p95": 1.28},
        "percentiles_eur": {"p5": 75700.0, "p25": 86900.0, "p50": 95300.0,
                            "p75": 104700.0, "p95": 119600.0},
        "fan_bands": fan,
        "terminal_hist": {"counts": [200 + i for i in range(48)],
                          "edges_eur": [round(60000.0 + 1500.0 * i, 0)
                                        for i in range(49)]},
        "expected_return_pct": 4.31, "median_return_pct": 2.05, "stdev_pct": 18.77,
        "sharpe_simulated": 0.23, "prob_negative_pct": 44.1, "prob_loss_10pct": 27.3,
        "prob_loss_20pct": 14.2, "prob_gain_10pct": 31.9, "prob_gain_20pct": 18.4,
        "var_95_pct": -24.6, "var_99_pct": -37.2, "var_99_cornish_fisher_pct": -41.8,
        "es_95_pct": -31.4, "es_99_pct": -44.9,
        "es_95_eur": -29345.0, "es_99_eur": -41963.0,
        "max_drawdown_p5_pct": -8.1, "max_drawdown_median_pct": -19.7,
        "max_drawdown_p95_pct": -38.4,
        "sample_paths": [[round(1.0 + 0.001 * j + 0.01 * i, 4)
                          for j in range(len(days))] for i in range(10)],
        "sample_paths_days": days, "sample_paths_n": 10,
    }


def test_montecarlo_compatto_tiene_tutte_le_metriche():
    """La premessa del bug e la sua cura, misurate insieme: il payload pieno a
    hz=252 sfora il tetto (il taglio mangiava le metriche); la vista compatta
    sta sotto CONTANDO la busta _stamp e le 19 metriche arrivano identiche."""
    pieno = _mc_payload(252)
    assert _peso(pieno) + ct._BUSTA_STAMP > ct.TETTO_TOOL_RESULT, (
        "la fixture non riproduce piu' il bug: payload pieno sotto il tetto")
    c = ct._compatta_montecarlo(pieno)
    assert _peso(c) + ct._BUSTA_STAMP <= ct.TETTO_TOOL_RESULT
    for k in _METRICHE_MC:
        assert c[k] == pieno[k], f"metrica {k} persa o alterata"
    for k in ("percentiles_ratio", "percentiles_eur", "stress_meta", "weights",
              "n_sims", "returns_basis", "base_nav_eur"):
        assert c[k] == pieno[k], f"{k} perso o alterato"
    for k in ("fan_bands", "terminal_hist", "sample_paths", "sample_paths_days"):
        assert k not in c
    assert "fan_bands" in c["_vista"], "i campi tolti vanno NOMINATI (regola 14/07)"


def test_montecarlo_hz63_sotto_il_tetto():
    # l'altro caso vivo della run (band_step=1 -> 63 punti per banda)
    c = ct._compatta_montecarlo(_mc_payload(63))
    assert _peso(c) + ct._BUSTA_STAMP <= ct.TETTO_TOOL_RESULT
    for k in _METRICHE_MC:
        assert k in c


def test_montecarlo_gigante_degrada_dichiarando():
    """Book enorme: se nemmeno la compatta sta nel tetto si tolgono i pesi
    per-ticker DICENDOLO (il book vive su get_portfolio_live) — mai un taglio
    zitto a valle."""
    # 20/08: il tetto e' passato da 6000 a 12000 e i 220 asset cablati qui non
    # sforavano piu' — il test verificava la degradazione su un payload che ormai
    # ci stava dentro. Ora il book cresce finche' la degradazione DEVE scattare,
    # cosi' la prova resta valida a qualunque tetto.
    n = 220
    c = ct._compatta_montecarlo(_mc_payload(252, n_assets=n))
    while "weights" in c and n <= 20000:
        n *= 2
        c = ct._compatta_montecarlo(_mc_payload(252, n_assets=n))
    assert "weights" not in c, (
        f"nemmeno con {n} asset la vista degrada: il tetto "
        f"({ct.TETTO_TOOL_RESULT}) non viene mai raggiunto")
    assert "ATTENZIONE" in c["_vista"]
    for k in _METRICHE_MC:
        assert k in c


def test_montecarlo_errore_passa_intatto():
    e = {"error": "download prezzi fallito"}
    assert ct._compatta_montecarlo(e) is e


def test_le_viste_sono_agganciate_al_dispatcher():
    """Se qualcuno stacca la compattazione, i desk tornano al payload troncato
    senza che nessuno se ne accorga fino alla run dopo."""
    src = inspect.getsource(ct.dispatch)
    for f in ("_compatta_portfolio_live", "_compatta_attribution",
              "_compatta_tearsheet", "_compatta_montecarlo"):
        assert f in src, f"{f} non e' piu' agganciata a chat_tools.dispatch"


def test_il_percorso_http_resta_pieno():
    """Il frontend consuma /portfolio, /portfolio/attribution e /portfolio/tearsheet
    (F2 Performance): le viste compatte sono per gli AGENTI e non devono finire li'."""
    with open(os.path.join(ROOT, "src", "bellomberg", "api", "bellomberg_api.py"),
              encoding="utf-8") as fh:
        api = fh.read()
    assert "_compatta_" not in api, (
        "una vista compatta e' finita sul percorso HTTP: al frontend arriverebbe "
        "un payload amputato senza che nessuno gliel'abbia detto")


def test_il_taglio_vero_LEGGE_il_tetto_invece_di_copiarlo():
    """Prima (fino al 20/08) questo test legava DUE numeri: la costante qui e il
    letterale che tagliava in `specialists/base.py` / `red_team.py`. Era la cintura
    giusta per un'architettura sbagliata — il numero viveva in tre posti, e i due
    letterali erano quelli che tagliavano davvero (stessa classe delle fonti prezzi
    del 19/08, dove la firma e `main()` divergevano).

    Dal 20/08 la duplicazione non esiste: chi taglia IMPORTA `TETTO_TOOL_RESULT`.
    Il test verifica la proprieta' nuova, che e' piu' forte — non "i due numeri
    coincidono oggi" ma "non ci sono due numeri".
    """
    for rel in (os.path.join("src", "bellomberg", "agents", "specialists", "base.py"),
                os.path.join("src", "bellomberg", "agents", "red_team.py")):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            src = fh.read()
        assert "TETTO_TOOL_RESULT" in src, (
            f"{rel} non legge piu' il tetto da chat_tools: il numero e' tornato "
            "a vivere in due posti")
        var = "result_str" if "base.py" in rel else "r_str"
        assert not re.search(r"len\(" + var + r"\)\s*>\s*\d", src), (
            f"{rel} confronta la lunghezza del tool_result con un numero cablato")
        # voce 11: il taglio deve restare DICHIARATO, mai muto
        assert "...[truncated]" in src, (
            f"{rel} ha perso il marker di taglio dichiarato (voce 11)")


def test_il_quant_ha_i_tool_che_il_suo_prompt_ORDINA():
    """Stessa classe della guardia I-1: prompt e arsenale devono combaciare."""
    from bellomberg.agents.specialists.quant import QuantSpecialist
    nomi = {t["name"] for t in ct.get_tools_for_agent("quant")}
    for tool in ("get_attribution", "get_tearsheet"):
        assert tool in QuantSpecialist.system_prompt, (
            f"{tool} non e' piu' istruito nel prompt del quant: torna a essere "
            "un'analitica consegnata e mai usata (voce §J P2)")
        assert tool in nomi, f"{tool} ordinato nel prompt ma assente dall'arsenale"


def test_il_prompt_dichiara_che_l_attribution_non_e_il_TWR():
    """La trappola dei due numeri: 5,62% (attribution) vs 4,77% (TWR ufficiale).
    Senza questa riga il desk puo' presentare l'uno per l'altro."""
    from bellomberg.agents.specialists.quant import QuantSpecialist
    sp = QuantSpecialist.system_prompt
    assert "reconciliation" in sp and "TWR" in sp
    assert "NON e' il TWR ufficiale" in sp


# --------------------------------------------------------------------------
# 6. IL CAMPO TIPO (05/09, lotto 3, Opus 5 chat e3 — ordine del PM)
#
# Le 17 righe dei desk che nominano le posizioni del PM sono ferme da giorni su
# una ragione tecnica sola: riscritte «per classe» darebbero al modello un ordine
# che non puo' eseguire, perche' la vista che riceve non dice CHE COSA sia ogni
# titolo. Questo campo e' la chiave che le sblocca.
#
# Misurato sul book vero prima di scriverlo. Le CIFRE stanno nel registro privato e
# non qui (06/09): «N posizioni su M» e' una misura DEL PORTAFOGLIO, e questo file
# esce nel perimetro pubblico — il cancello non la vede perche' e' un conteggio, non
# un valore di colonna. Cio' che conta e resta dicibile: il campo costa pochi punti
# percentuali della vista e rimane dentro il tetto dei tool_result con margine; le
# posizioni senza un tipo dichiarato nel negozio esistono, e per quelle il campo dice
# «non dichiarato» invece di dedurre.
#
# La regola di casa applicata qui: `classificazione.natura()` ha DUE ignoranze
# diverse (il simbolo non e' dichiarato / il negozio non si legge) che dai campi
# strutturati sono indistinguibili — valore None, fonte 'nessuna' per entrambe.
# Nella vista restano separate, o il modello le legge come la stessa cosa.
# --------------------------------------------------------------------------

def _negozio(voci, origine="data/veicoli.json", motivo=None):
    """La forma che rende `classificazione.carica_veicoli` (voci gia' validate)."""
    return {"veicoli": voci, "origine": origine, "motivo": motivo}


def _voce(tipo):
    return {"tipo": tipo, "provenienza": "dichiarato", "verificato_il": "2026-09-01",
            "nome": None, "sottostante": None, "nav_fonte": None, "nav_valuta": None,
            "classe_size": None, "settore_tema": None, "bucket_economico": None}


def test_ogni_riga_porta_il_tipo_dichiarato_nel_negozio():
    book = _book(3)
    neg = _negozio({"TCK0.MI": _voce("cef"), "TCK1.MI": _voce("etf")})
    out = ct._compatta_portfolio_live(book, negozio=neg)
    per_ticker = {r["ticker"]: r.get("tipo") for r in out["positions"]}
    assert per_ticker["TCK0.MI"] == "cef"
    assert per_ticker["TCK1.MI"] == "etf"


def test_il_simbolo_fuori_dal_negozio_e_dichiarato_non_dedotto():
    """Il buco si DICHIARA e non si indovina: senza voce nel negozio la natura
    non esiste, e il campo non deve inventarla ne' sparire (una chiave assente
    farebbe leggere al modello «riga senza particolarita'»)."""
    out = ct._compatta_portfolio_live(_book(2), negozio=_negozio({"TCK0.MI": _voce("dat")}))
    fuori = [r for r in out["positions"] if r["ticker"] == "TCK1.MI"][0]
    assert "tipo" in fuori, "il campo non puo' sparire sulle righe scoperte"
    assert fuori["tipo"] == "non dichiarato"
    assert fuori["tipo"] not in ct_TIPI(), "nessun tipo dedotto dal simbolo"


def ct_TIPI():
    import bellomberg.storage.classificazione as cl
    return cl.TIPI


def test_negozio_assente_dichiarato_una_volta_sola_nella_vista():
    """Il negozio rotto non si ripete su 30 righe (costa e non aggiunge nulla):
    le righe dicono n.d., la vista dice PERCHE', una volta."""
    neg = _negozio({}, origine="assente", motivo="negozio non trovato: data/veicoli.json")
    out = ct._compatta_portfolio_live(_book(3), negozio=neg)
    assert all(r.get("tipo") == "n.d." for r in out["positions"])
    v = out["_vista"]
    assert "ASSENTE" in v.upper() and "negozio dei veicoli" in v
    assert "negozio non trovato" in v, "il motivo del caricatore arriva al modello"
    assert v.count("negozio dei veicoli") == 1, "la dichiarazione non si ripete"


def test_negozio_illeggibile_non_si_confonde_con_assente():
    neg = _negozio({}, origine="illeggibile", motivo="voce BETA malformata")
    v = ct._compatta_portfolio_live(_book(2), negozio=neg)["_vista"]
    assert "ILLEGGIBILE" in v.upper() and "voce BETA malformata" in v
    assert "ASSENTE" not in v.upper(), "le due ignoranze restano distinte"


def test_la_vista_spiega_il_campo_e_CONTA_i_non_dichiarati():
    """La copertura si CONTA, non si elenca (lotto 2b): l'elenco dei simboli
    scoperti sarebbe di nuovo il book del PM scritto in chiaro."""
    neg = _negozio({"TCK0.MI": _voce("etf")})
    out = ct._compatta_portfolio_live(_book(4), negozio=neg)
    v = out["_vista"]
    assert "tipo" in v
    assert "3 su 4" in v or "3/4" in v, "il conteggio dei non dichiarati manca"
    for t in ("TCK1.MI", "TCK2.MI", "TCK3.MI"):
        assert t not in v, "la vista elenca i simboli scoperti invece di contarli"


def test_il_negozio_si_legge_UNA_volta_per_chiamata_non_per_riga(monkeypatch):
    """`carica_veicoli` apre un file: una lettura per riga sarebbe 30 aperture a
    ogni chiamata di tool. La vista lo legge una volta e passa il negozio."""
    import bellomberg.storage.classificazione as cl
    letture = []
    vero = cl.carica_veicoli

    def spia(*a, **k):
        letture.append(1)
        return _negozio({})

    monkeypatch.setattr(cl, "carica_veicoli", spia)
    ct._compatta_portfolio_live(_book(12))
    assert len(letture) == 1, "letture del negozio: %d (una per riga?)" % len(letture)
    assert vero is not cl.carica_veicoli


def test_il_campo_tipo_non_fa_sforare_il_tetto_col_book_vero():
    """Misura, non fiducia: col book realistico la vista col campo resta sotto il
    tetto e NON degrada (il prezzo medio resta su tutte le righe)."""
    out = ct._compatta_portfolio_live(_book(27), negozio=_negozio({"TCK0.MI": _voce("cef")}))
    assert _peso(out) + ct._BUSTA_STAMP <= ct.TETTO_TOOL_RESULT
    assert all("prezzo_medio" in r for r in out["positions"])


def test_i_due_registri_PROMETTONO_il_campo_e_la_vista_lo_CONSEGNA():
    """Regola di casa (§9-quinsexagies): una description dice un comportamento e il
    comportamento e' cablato. Qui la promessa e la consegna sono legate in un test
    solo: se il campo sparisce dalla vista, la promessa resta e questo test cade."""
    import bellomberg.agents.agent_tools as at
    voce_chat = [t for t in ct.TOOL_DEFINITIONS if t["name"] == "get_portfolio_live"][0]
    voce_agenti = [t for t in at.TOOLS_SCHEMA if t["name"] == "get_portfolio_live"][0]
    for voce in (voce_chat, voce_agenti):
        assert "tipo" in voce["description"], "il registro non promette il campo"
        assert "dichiarat" in voce["description"].lower(), (
            "la description non dice che la natura e' DICHIARATA, non dedotta")
    out = ct._compatta_portfolio_live(_book(2), negozio=_negozio({"TCK0.MI": _voce("cef")}))
    assert all("tipo" in r for r in out["positions"]), "promesso e non consegnato"
