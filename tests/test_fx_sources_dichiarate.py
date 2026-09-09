"""Fonte FX dichiarata su GET /fx (blocco cassa/valute, 03/08).

`get_fx_to_eur` ha un fallback statico di maggio 2026 (rete di sicurezza
voluta) ma `GET /fx` rendeva solo {timestamp, rates}: la cassa poteva
muoversi su un cambio vecchio di mesi senza che nessuno lo dichiarasse —
violazione della regola 14/07 proprio sul percorso dei soldi.

Qui si inchioda `fx_sources_for`: per OGNI valuta richiesta esce una fonte
('live' | 'fallback' | 'assente' | 'n.d.'), mai un buco zitto e mai una
fonte indovinata. GBX deriva da GBP/100 e ne eredita la fonte.
"""
import pytest

import bellomberg.cli.price_updater as pu
from bellomberg.cli.price_updater import fx_sources_for

VALUTE = ["USD", "GBP", "GBX", "CHF", "JPY", "HKD"]


def test_live_e_fallback_dichiarati(monkeypatch):
    monkeypatch.setattr(pu, "_FX_SOURCE_LAST",
                        {"USD": "live", "GBP": "live", "CHF": "fallback",
                         "JPY": "live", "HKD": "fallback"})
    rates = {"USD": 0.86, "GBP": 1.15, "GBX": 0.0115, "CHF": 1.03,
             "JPY": 0.0058, "HKD": 0.11}
    src = fx_sources_for(VALUTE, rates)
    assert src["USD"] == "live"
    assert src["CHF"] == "fallback"
    assert src["HKD"] == "fallback"


def test_gbx_eredita_la_fonte_di_gbp(monkeypatch):
    # GBX non ha MAI una chiave propria in _FX_SOURCE_LAST (deriva da GBP/100)
    monkeypatch.setattr(pu, "_FX_SOURCE_LAST", {"GBP": "fallback"})
    src = fx_sources_for(["GBX"], {"GBX": 0.0117})
    assert src["GBX"] == "fallback"
    monkeypatch.setattr(pu, "_FX_SOURCE_LAST", {"GBP": "live"})
    assert fx_sources_for(["GBX"], {"GBX": 0.0115})["GBX"] == "live"


def test_tasso_mancante_e_buco_dichiarato(monkeypatch):
    monkeypatch.setattr(pu, "_FX_SOURCE_LAST", {"USD": "live"})
    src = fx_sources_for(["USD", "JPY"], {"USD": 0.86})  # JPY senza tasso
    assert src["JPY"] == "assente"
    assert src["USD"] == "live"


def test_fonte_non_tracciata_non_viene_indovinata(monkeypatch):
    # tasso presente ma _FX_SOURCE_LAST vuoto (percorso imprevisto):
    # 'n.d.' dichiarato, MAI un default 'live' o 'fallback' inventato
    monkeypatch.setattr(pu, "_FX_SOURCE_LAST", {})
    src = fx_sources_for(["USD"], {"USD": 0.86})
    assert src["USD"] == "n.d."


def test_tutte_le_valute_richieste_hanno_una_risposta(monkeypatch):
    monkeypatch.setattr(pu, "_FX_SOURCE_LAST", {})
    src = fx_sources_for(VALUTE, {})
    assert set(src.keys()) == set(VALUTE)
    assert all(v == "assente" for v in src.values())


def test_con_fonte_cattura_per_valuta(monkeypatch):
    # review 03/08: la fonte va catturata SUBITO dopo la risoluzione della
    # singola valuta — a fine giro una richiesta concorrente puo' averla
    # sovrascritta (il fallback non si cacha) e 'live' uscirebbe su un
    # tasso statico di maggio
    monkeypatch.setattr(pu, "get_fx_to_eur", lambda cur: 0.92)
    monkeypatch.setattr(pu, "_FX_SOURCE_LAST", {"USD": "fallback"})
    rate, fonte = pu.get_fx_to_eur_con_fonte("USD")
    assert rate == 0.92 and fonte == "fallback"


def test_con_fonte_gbx_eredita_gbp(monkeypatch):
    monkeypatch.setattr(pu, "get_fx_to_eur", lambda cur: 0.0115)
    monkeypatch.setattr(pu, "_FX_SOURCE_LAST", {"GBP": "live"})
    rate, fonte = pu.get_fx_to_eur_con_fonte("GBX")
    assert rate == 0.0115 and fonte == "live"


def test_con_fonte_assente_su_tasso_none(monkeypatch):
    monkeypatch.setattr(pu, "get_fx_to_eur", lambda cur: None)
    monkeypatch.setattr(pu, "_FX_SOURCE_LAST", {})
    rate, fonte = pu.get_fx_to_eur_con_fonte("XXX")
    assert rate is None and fonte == "assente"


def test_eur_ha_fonte_identity():
    assert pu.get_fx_to_eur_con_fonte("EUR") == (1.0, "identity")


def test_cache_live_scade_e_un_failure_non_riusa_il_tasso(monkeypatch):
    class Close:
        iloc = [2.0]

    class History(dict):
        def __init__(self):
            super().__init__(Close=Close())

        def __len__(self):
            return 1

    class Ticker:
        fail = False

        def __init__(self, _pair):
            pass

        def history(self, **_kwargs):
            if self.fail:
                raise RuntimeError("offline")
            return History()

    clock = [0.0]
    monkeypatch.setattr(pu, "YFINANCE_AVAILABLE", True)
    monkeypatch.setattr(pu.yf, "Ticker", Ticker)
    monkeypatch.setattr(pu.time, "monotonic", lambda: clock[0])
    pu._FX_CACHE.clear()
    pu._FX_CACHE_AT.clear()
    rate, source = pu.get_fx_to_eur_con_fonte("USD")
    assert (rate, source) == (0.5, "live")
    Ticker.fail = True
    clock[0] = pu._FX_CACHE_TTL_SECONDS + 1
    rate, source = pu.get_fx_to_eur_con_fonte("USD")
    assert source == "fallback" and rate == pu._FX_FALLBACK_TO_EUR["USD"]


@pytest.mark.parametrize("invalid", [float("inf"), float("nan"), 0.0, -2.0])
def test_quote_yfinance_invalida_non_diventa_live(monkeypatch, invalid):
    class Close:
        iloc = [invalid]

    class History(dict):
        def __init__(self):
            super().__init__(Close=Close())

        def __len__(self):
            return 1

    class Ticker:
        def __init__(self, _pair):
            pass

        def history(self, **_kwargs):
            return History()

    monkeypatch.setattr(pu, "YFINANCE_AVAILABLE", True)
    monkeypatch.setattr(pu.yf, "Ticker", Ticker)
    pu._FX_CACHE.clear()
    pu._FX_CACHE_AT.clear()
    rate, source = pu.get_fx_to_eur_con_fonte("USD")
    assert rate == pu._FX_FALLBACK_TO_EUR["USD"] and source == "fallback"


@pytest.mark.parametrize("invalid", [float("inf"), float("nan"), 0.0, -1.0])
def test_cache_corrotto_non_viene_restituito_come_live(monkeypatch, invalid):
    monkeypatch.setattr(pu, "YFINANCE_AVAILABLE", False)
    pu._FX_CACHE["USD"] = invalid
    pu._FX_CACHE_AT["USD"] = pu.time.monotonic()
    rate, source = pu.get_fx_to_eur_con_fonte("USD")
    assert rate == pu._FX_FALLBACK_TO_EUR["USD"] and source == "fallback"
