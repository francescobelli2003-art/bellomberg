"""F43 (4) — la lista `valorizzati_al_costo` in `compute_nav_history`
(31/08, Fable 5, chat backend; ordine deciso dal PM il 25/08).

Il fatto: un giorno senza prezzo per un ticker in portafoglio non fa sparire la
posizione dalla serie — vale il suo COSTO (205-fix, giusto contro i tuffi
finti) — ma NESSUN campo lo dichiarava: chi legge la serie non sa quali punti
sono proxy (classe 14/07; altrove il pattern c'e' gia': `stale_positions`).

La cura: chiave ADDITIVA `valorizzati_al_costo` = lista di
{ticker, n_giorni, primo, ultimo} ordinata per ticker, `None` a serie pulita.
`ultimo == dates[-1]` dice al consumatore che anche il punto di OGGI e' proxy
(caso reale: LAMBDA.DE comprata il 31/08 senza chiusure).

Zero rete e zero DB: `_trade_history`, `_download_prices_for_history`,
`_build_fx_history` e la cassa sono stubbate; ticker .MI (EUR) cosi' il
lookup FX non tocca price_updater.

ONESTA' sull'input vero (misura 31/08): in produzione il download fa
`ffill().bfill()`, quindi il buco per-GIORNO delle fixture 1/2/5 non arriva
al loop — il caso fedele e' la colonna interamente NaN/assente (simbolo
fallito o SKIP_TICKERS), che e' il test 3. Sul book vero del 31/08 il
registro e' None (serie pulita, LAMBDA.DE ha gia' la barra di oggi); i test
per-giorno restano come cintura sul loop, che guarda ogni NaN comunque.
"""
import pandas as pd
import pytest

from bellomberg.storage import memory_db
import bellomberg.portfolio.portfolio_analytics as pa


def _t(id_, ticker, qty, prezzo, data):
    return {"id": id_, "ticker": ticker, "action": "BUY", "quantita": qty,
            "prezzo": prezzo, "valuta": "EUR", "data": data}


def _nav(monkeypatch, trades, prezzi):
    """prezzi: {ticker: {giorno_iso: close|None}} -> DataFrame con NaN sui None."""
    giorni = sorted({g for serie in prezzi.values() for g in serie})
    idx = pd.to_datetime(giorni)
    df = pd.DataFrame({t: [serie.get(g) for g in giorni] for t, serie in prezzi.items()},
                      index=idx, dtype=float)
    monkeypatch.setattr(pa, "_trade_history", lambda: trades)
    monkeypatch.setattr(pa, "_download_prices_for_history", lambda tk, s, e, salta: df)
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: pd.DataFrame())
    monkeypatch.setattr(memory_db, "leggi_cassa_portfolio",
                        lambda path=None: {"cash_eur": 0.0, "cash_source": "portfolio.json",
                                           "cash_source_note": None})
    pa._ANALYTICS_CACHE.clear()
    return pa.compute_nav_history(force=True)


TRADES = [_t(1, "AAA.MI", 10, 100.0, "2026-02-02T10:00:00"),
          _t(2, "BBB.MI", 10, 50.0, "2026-02-03T10:00:00")]


def test_il_proxy_e_dichiarato_con_ticker_giorni_e_date(monkeypatch):
    out = _nav(monkeypatch, TRADES, {
        "AAA.MI": {"2026-02-02": 100.0, "2026-02-03": 101.0, "2026-02-04": 102.0},
        "BBB.MI": {"2026-02-02": None, "2026-02-03": None, "2026-02-04": 55.0},
    })
    assert out["valorizzati_al_costo"] == [
        {"ticker": "BBB.MI", "n_giorni": 1, "primo": "2026-02-03", "ultimo": "2026-02-03"}]


def test_serie_pulita_none_dichiarato_non_lista_vuota(monkeypatch):
    out = _nav(monkeypatch, TRADES, {
        "AAA.MI": {"2026-02-02": 100.0, "2026-02-03": 101.0},
        "BBB.MI": {"2026-02-02": None, "2026-02-03": 50.5},
    })
    assert out["valorizzati_al_costo"] is None      # BBB il 02/02 non era in carico: qty 0


def test_l_ultimo_giorno_proxy_e_riconoscibile_dal_consumatore(monkeypatch):
    """Il contratto del ponte: `ultimo == dates[-1]` = anche il punto di oggi
    e' al costo (LAMBDA.DE comprata oggi senza chiusure)."""
    out = _nav(monkeypatch, TRADES, {
        "AAA.MI": {"2026-02-02": 100.0, "2026-02-03": 101.0, "2026-02-04": 102.0},
        "BBB.MI": {"2026-02-02": None, "2026-02-03": None, "2026-02-04": None},
    })
    reg = out["valorizzati_al_costo"]
    assert reg == [{"ticker": "BBB.MI", "n_giorni": 2, "primo": "2026-02-03", "ultimo": "2026-02-04"}]
    assert reg[0]["ultimo"] == out["dates"][-1]


def test_la_chiave_e_additiva_il_nav_non_cambia(monkeypatch):
    out = _nav(monkeypatch, TRADES, {
        "AAA.MI": {"2026-02-02": 100.0, "2026-02-03": 101.0},
        "BBB.MI": {"2026-02-02": None, "2026-02-03": None},
    })
    # 03/02: AAA a mercato (10 x 101) + BBB al costo (10 x 50) — com'era prima
    assert out["nav_eur"][-1] == pytest.approx(10 * 101.0 + 10 * 50.0)
    assert out["pnl_eur"][-1] == pytest.approx(10.0)     # solo AAA: 10 x (101-100)


def test_due_ticker_proxy_ordinati_per_ticker(monkeypatch):
    trades = TRADES + [_t(3, "CCC.MI", 5, 20.0, "2026-02-02T11:00:00")]
    out = _nav(monkeypatch, trades, {
        "AAA.MI": {"2026-02-02": 100.0, "2026-02-03": 101.0},
        "BBB.MI": {"2026-02-02": None, "2026-02-03": None},
        "CCC.MI": {"2026-02-02": None, "2026-02-03": 21.0},
    })
    assert [r["ticker"] for r in out["valorizzati_al_costo"]] == ["BBB.MI", "CCC.MI"]
    assert out["valorizzati_al_costo"][1] == {
        "ticker": "CCC.MI", "n_giorni": 1, "primo": "2026-02-02", "ultimo": "2026-02-02"}
