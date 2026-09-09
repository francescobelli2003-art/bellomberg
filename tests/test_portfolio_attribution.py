"""Quant fase 1b LOTTO 2 — contribution attribution (23/07 notte).
Offline: trades/prezzi/FX iniettati, valori attesi CALCOLATI A MANO.
"""
import json

import pandas as pd
import pytest

import bellomberg.storage.classificazione as cl
import bellomberg.portfolio.portfolio_attribution as pa
import bellomberg.portfolio.portfolio_sectors as ps


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "CACHE_PATH", str(tmp_path / "sector_cache.json"))


_D = pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"])
_NOFETCH = lambda tk: {"sector": None, "industry": None}


def _trades(*rows):
    return [{"ticker": t, "action": a, "quantita": q, "prezzo": px,
             "valuta": ccy, "data": d} for t, a, q, px, ccy, d in rows]


def test_due_ticker_eur_carino_a_mano():
    """2 nomi EUR, 2 giorni: contributi Carino verificati a mano.
    AAA 100->110->121 (+10%/+10%), BBB 100->100->90 (0%/-10%).
    V: 2000 -> 2100 -> 2110 => periodo +5,5%. Carino: AAA +10,380%, BBB -4,880%."""
    trades = _trades(("AAA.MI", "BUY", 10, 100, "EUR", "2026-06-15"),
                     ("BBB.MI", "BUY", 10, 100, "EUR", "2026-06-15"))
    prices = pd.DataFrame({"AAA.MI": [100.0, 110.0, 121.0],
                           "BBB.MI": [100.0, 100.0, 90.0]}, index=_D)
    out = pa.compute_attribution(period="MTD", end_date="2026-07-03",
                                 trades=trades, prices=prices,
                                 fx=pd.DataFrame(), fetch=_NOFETCH)
    assert out.get("error") is None
    assert out["portfolio_return_pct"] == pytest.approx(5.5, abs=1e-6)
    by = {r["ticker"]: r for r in out["by_position"]}
    assert by["AAA.MI"]["contribution_pct"] == pytest.approx(10.380, abs=1e-3)
    assert by["BBB.MI"]["contribution_pct"] == pytest.approx(-4.880, abs=1e-3)
    # proprieta' Carino: somma contributi = rendimento composto (sui pct GIA'
    # arrotondati a 3 decimali la tolleranza giusta e' 1e-3 — review BASSA-4;
    # il check esatto non-arrotondato e' l'assenza di "CHECK FALLITO" sotto)
    tot = sum(r["contribution_pct"] for r in out["by_position"])
    assert tot == pytest.approx(out["portfolio_return_pct"], abs=1e-3)
    assert not any("CHECK FALLITO" in n for n in out["notes"])
    # EUR: niente effetto FX
    assert out["totals"]["fx_pct"] == pytest.approx(0.0, abs=1e-9)


def test_scomposizione_fx_locale_cross_a_mano():
    """1 nome USD, 1 giorno: px +10% locale, USD->EUR -5% => EUR +4,5%;
    locale +10%, FX -5%, cross -0,5% (residuo DICHIARATO, non spalmato)."""
    trades = _trades(("CCC", "BUY", 10, 100, "USD", "2026-06-15"))
    prices = pd.DataFrame({"CCC": [100.0, 110.0]}, index=_D[:2])
    fx = pd.DataFrame({"USD": [1.0, 0.95]}, index=_D[:2])
    out = pa.compute_attribution(period="MTD", end_date="2026-07-02",
                                 trades=trades, prices=prices, fx=fx,
                                 fetch=_NOFETCH)
    assert out.get("error") is None
    assert out["portfolio_return_pct"] == pytest.approx(4.5, abs=1e-6)
    row = out["by_position"][0]
    assert row["local_pct"] == pytest.approx(10.0, abs=1e-6)
    assert row["fx_pct"] == pytest.approx(-5.0, abs=1e-6)
    assert row["cross_pct"] == pytest.approx(-0.5, abs=1e-6)
    assert row["local_pct"] + row["fx_pct"] + row["cross_pct"] == pytest.approx(
        row["contribution_pct"], abs=1e-6)
    bycc = {r["currency"]: r for r in out["by_currency"]}
    assert bycc["USD"]["fx_contribution_pct"] == pytest.approx(-5.0, abs=1e-6)


def test_trade_a_meta_periodo_convenzione_inizio_giorno():
    """BUY a meta' periodo: il giorno del BUY pesa la qty di IERI (il P&L
    same-day del BUY non e' attribuito, convenzione fine-giornata dichiarata)."""
    trades = _trades(("AAA.MI", "BUY", 10, 100, "EUR", "2026-06-15"),
                     ("AAA.MI", "BUY", 10, 110, "EUR", "2026-07-02"))
    prices = pd.DataFrame({"AAA.MI": [100.0, 110.0, 121.0]}, index=_D)
    out = pa.compute_attribution(period="MTD", end_date="2026-07-03",
                                 trades=trades, prices=prices,
                                 fx=pd.DataFrame(), fetch=_NOFETCH)
    assert out.get("error") is None
    # giorno1: 10 az. +10% = +10%; giorno2: 20 az. +10% = +10% => composto +21%
    assert out["portfolio_return_pct"] == pytest.approx(21.0, abs=1e-6)
    assert out["by_position"][0]["contribution_pct"] == pytest.approx(21.0, abs=1e-6)


def test_ticker_senza_prezzi_escluso_dichiarato(tmp_path, monkeypatch):
    """Un simbolo dichiarato nel negozio dei prezzi speciali: detenuto ma senza prezzi
    -> ESCLUSO e dichiarato, mai spalmato; il resto del book calcola normale.
    (Lotto 6, 05/09: prima il simbolo era il token del book, cablato nel test.)"""
    import bellomberg.storage.negozi_privati as np_
    negozio = tmp_path / "prezzi_speciali.json"
    negozio.write_text('{"senza_yfinance": ["ALFA"]}', encoding="utf-8")
    monkeypatch.setattr(np_, "PERCORSO_PREZZI", str(negozio))
    trades = _trades(("AAA.MI", "BUY", 10, 100, "EUR", "2026-06-15"),
                     ("ALFA", "BUY", 5, 10, "USD", "2026-06-15"))
    prices = pd.DataFrame({"AAA.MI": [100.0, 110.0]}, index=_D[:2])
    out = pa.compute_attribution(period="MTD", end_date="2026-07-02",
                                 trades=trades, prices=prices,
                                 fx=pd.DataFrame(), fetch=_NOFETCH)
    assert out.get("error") is None
    assert out["portfolio_return_pct"] == pytest.approx(10.0, abs=1e-6)
    exc = {e["ticker"]: e for e in out["excluded"]}
    assert "ALFA" in exc
    # il MOTIVO dev'essere quello del negozio, non il generico «colonna assente»: se lo
    # skip smettesse di scattare il nome verrebbe escluso lo stesso, per un'altra ragione,
    # e un test che guarda solo «n.d.» resterebbe verde (mutazione M39 del banco)
    motivi = " ".join(exc["ALFA"]["reasons"])
    assert "negozio dei prezzi speciali" in motivi, motivi
    assert "n.d." in motivi
    assert exc["ALFA"]["days_excluded"] == 1 and exc["ALFA"]["partial"] is False


def test_a_negozio_assente_nessuno_e_escluso_e_la_nota_lo_dice(tmp_path, monkeypatch):
    """Il caso che il lotto 6 crea: senza negozio l'insieme e' VUOTO, quindi il simbolo
    NON viene escluso per quel motivo — e il payload deve dire perche', altrimenti la
    nota tace su un buco (regola PM 14/07). Condizione posta da e3 sul suo file."""
    import bellomberg.storage.negozi_privati as np_
    monkeypatch.setattr(np_, "PERCORSO_PREZZI", str(tmp_path / "non_esiste.json"))
    trades = _trades(("AAA.MI", "BUY", 10, 100, "EUR", "2026-06-15"),
                     ("ALFA", "BUY", 5, 10, "USD", "2026-06-15"))
    prices = pd.DataFrame({"AAA.MI": [100.0, 110.0]}, index=_D[:2])
    out = pa.compute_attribution(period="MTD", end_date="2026-07-02",
                                 trades=trades, prices=prices,
                                 fx=pd.DataFrame(), fetch=_NOFETCH)
    assert out.get("error") is None
    exc = {e["ticker"]: e for e in out["excluded"]}
    # l'asserzione dev'essere sul motivo VERO, non su una parola che non esiste piu' in
    # nessun ramo (sarebbe sempre vera): senza negozio il nome esce per la colonna mancante
    motivi = " ".join(exc.get("ALFA", {}).get("reasons", []))
    assert "negozio dei prezzi speciali" not in motivi, motivi
    assert "colonna assente" in motivi, "senza negozio il motivo e' l'altro: %s" % motivi
    note = " ".join(out.get("notes") or [])
    assert "negozio dei prezzi speciali" in note, out.get("notes")
    assert "ASSENTE" in note and "non perche' non ci sia" in note, note


def test_fx_mancante_escluso_dichiarato_mai_nativo():
    """FX assente per una valuta: il nome e' ESCLUSO e dichiarato — mai la
    valuta nativa sommata come EUR (regola no-fallback)."""
    trades = _trades(("AAA.MI", "BUY", 10, 100, "EUR", "2026-06-15"),
                     ("CCC", "BUY", 10, 100, "USD", "2026-06-15"))
    prices = pd.DataFrame({"AAA.MI": [100.0, 110.0], "CCC": [100.0, 150.0]},
                          index=_D[:2])
    out = pa.compute_attribution(period="MTD", end_date="2026-07-02",
                                 trades=trades, prices=prices,
                                 fx=pd.DataFrame(), fetch=_NOFETCH)   # niente USD
    assert out.get("error") is None
    assert out["portfolio_return_pct"] == pytest.approx(10.0, abs=1e-6)  # solo AAA
    exc = {e["ticker"]: e for e in out["excluded"]}
    assert "FX USD n.d." in exc["CCC"]["reasons"]


def test_bucket_economico_stesso_asse_dell_esposizione(tmp_path, monkeypatch):
    """I bucket vengono da econ_bucket_for: un ETF settoriale (override tema -> Technology) e
    un single-stock GICS Technology aggregano nello STESSO bucket. Le viste sono iniettate con
    simboli inventati (05/09, lotto 2b: vengono dal negozio dei veicoli, non dal codice)."""
    negozio = tmp_path / "veicoli.json"
    negozio.write_text(json.dumps({
        "CHIPX.MI": {"tipo": "etf", "provenienza": "dichiarato",
                     "settore_tema": "ETF Chip (tema)",
                     "bucket_economico": "Technology"},
    }), encoding="utf-8")
    monkeypatch.setattr(cl, "PERCORSO_VEICOLI", str(negozio))
    trades = _trades(("CHIPX.MI", "BUY", 10, 100, "EUR", "2026-06-15"),
                     ("SEMI.MI", "BUY", 10, 100, "EUR", "2026-06-15"))
    prices = pd.DataFrame({"CHIPX.MI": [100.0, 110.0], "SEMI.MI": [100.0, 105.0]},
                          index=_D[:2])
    fetch = lambda tk: ({"sector": "Technology", "industry": "Semis"}
                        if tk == "SEMI.MI" else {"sector": None, "industry": None})
    out = pa.compute_attribution(period="MTD", end_date="2026-07-02",
                                 trades=trades, prices=prices,
                                 fx=pd.DataFrame(), fetch=fetch)
    assert out.get("error") is None
    assert len(out["by_bucket"]) == 1
    b = out["by_bucket"][0]
    assert b["bucket"] == "Technology"
    assert b["tickers"] == ["CHIPX.MI", "SEMI.MI"]
    assert b["contribution_pct"] == pytest.approx(7.5, abs=1e-6)   # 0.5*10 + 0.5*5


def test_negozio_dei_veicoli_rotto_dichiarato_anche_qui(tmp_path, monkeypatch):
    """Review 05/09 (lotto 2b): con negozio assente/illeggibile le viste sono vuote e una DAT
    finiva nel bucket di listino senza una parola; l'esposizione lo dichiara, l'attribution deve
    fare lo stesso (stessa nota, stessa fonte)."""
    negozio = tmp_path / "veicoli.json"
    negozio.write_text('{"CHIPX.MI": {"tipo": "etf",}}', encoding="utf-8")
    monkeypatch.setattr(cl, "PERCORSO_VEICOLI", str(negozio))
    trades = _trades(("CHIPX.MI", "BUY", 10, 100, "EUR", "2026-06-15"))
    prices = pd.DataFrame({"CHIPX.MI": [100.0, 110.0]}, index=_D[:2])
    out = pa.compute_attribution(period="MTD", end_date="2026-07-02", trades=trades, prices=prices,
                                 fx=pd.DataFrame(), fetch=lambda tk: {"sector": "Technology", "industry": "Software"})
    assert out.get("error") is None
    joined = " ".join(out["notes"])
    assert "negozio dei veicoli ILLEGGIBILE" in joined and "JSONDecodeError" in joined


def test_riconciliazione_iniettata_e_dichiarata():
    """Serie ufficiale iniettata identica alla base -> delta 0; assente -> errore
    DICHIARATO, mai un numero inventato."""
    trades = _trades(("AAA.MI", "BUY", 10, 100, "EUR", "2026-06-15"),
                     ("BBB.MI", "BUY", 10, 100, "EUR", "2026-06-15"))
    prices = pd.DataFrame({"AAA.MI": [100.0, 110.0, 121.0],
                           "BBB.MI": [100.0, 100.0, 90.0]}, index=_D)
    official = {"dates": ["2026-07-01", "2026-07-02", "2026-07-03"],
                "values_eur": [2000.0, 2100.0, 2110.0], "flows_eur": [0.0, 0.0, 0.0]}
    out = pa.compute_attribution(period="MTD", end_date="2026-07-03",
                                 trades=trades, prices=prices, fx=pd.DataFrame(),
                                 official_series=official, fetch=_NOFETCH)
    rec = out["reconciliation"]
    assert rec["official_twr_pct"] == pytest.approx(5.5, abs=1e-3)
    assert rec["delta_pp"] == pytest.approx(0.0, abs=1e-3)
    # senza serie: buco dichiarato
    out2 = pa.compute_attribution(period="MTD", end_date="2026-07-03",
                                  trades=trades, prices=prices, fx=pd.DataFrame(),
                                  fetch=_NOFETCH)
    assert "error" in out2["reconciliation"]


def test_sell_totale_a_meta_periodo_a_mano():
    """Review MEDIA-3: SELL totale a meta' periodo. Il nome resta in by_position
    col contributo maturato; dal giorno dopo esce da pesi e denominatore.
    A mano: g1 AAA+BBB (V 2000, AAA +10% -> R1 +5%); g2 solo BBB +10% -> R2 +10%;
    periodo +15,5%; Carino: AAA +5,248%, BBB +10,252%."""
    trades = _trades(("AAA.MI", "BUY", 10, 100, "EUR", "2026-06-15"),
                     ("BBB.MI", "BUY", 10, 100, "EUR", "2026-06-15"),
                     ("AAA.MI", "SELL", 10, 110, "EUR", "2026-07-02"))
    prices = pd.DataFrame({"AAA.MI": [100.0, 110.0, 110.0],
                           "BBB.MI": [100.0, 100.0, 110.0]}, index=_D)
    out = pa.compute_attribution(period="MTD", end_date="2026-07-03",
                                 trades=trades, prices=prices,
                                 fx=pd.DataFrame(), fetch=_NOFETCH)
    assert out.get("error") is None
    assert out["portfolio_return_pct"] == pytest.approx(15.5, abs=1e-6)
    by = {r["ticker"]: r for r in out["by_position"]}
    assert by["AAA.MI"]["contribution_pct"] == pytest.approx(5.248, abs=1e-3)
    assert by["BBB.MI"]["contribution_pct"] == pytest.approx(10.252, abs=1e-3)
    assert by["AAA.MI"]["avg_weight_pct"] == pytest.approx(25.0, abs=0.01)  # 50% x 1g su 2
    assert not any("CHECK FALLITO" in n for n in out["notes"])


def test_periodi_ytd_30d_inception():
    """Review MEDIA-3: copertura dei periodi non-MTD (confini finestra + base)."""
    # YTD: base = ultimo giorno di borsa dell'anno vecchio, il 02/01 conta
    dates = pd.to_datetime(["2025-12-31", "2026-01-02"])
    trades = _trades(("AAA.MI", "BUY", 10, 100, "EUR", "2025-06-15"))
    prices = pd.DataFrame({"AAA.MI": [100.0, 110.0]}, index=dates)
    out = pa.compute_attribution(period="YTD", end_date="2026-01-02",
                                 trades=trades, prices=prices,
                                 fx=pd.DataFrame(), fetch=_NOFETCH)
    assert out["period"]["base_day"] == "2025-12-31"
    assert out["portfolio_return_pct"] == pytest.approx(10.0, abs=1e-6)
    # 30D: finestra di 30 gg di calendario (end-29 .. end), base subito prima
    dates30 = pd.to_datetime(["2026-06-30", "2026-07-15", "2026-07-30"])
    prices30 = pd.DataFrame({"AAA.MI": [100.0, 110.0, 121.0]}, index=dates30)
    out30 = pa.compute_attribution(period="30D", end_date="2026-07-30",
                                   trades=trades, prices=prices30,
                                   fx=pd.DataFrame(), fetch=_NOFETCH)
    assert out30["period"]["base_day"] == "2026-06-30"
    assert out30["portfolio_return_pct"] == pytest.approx(21.0, abs=1e-6)
    # INCEPTION: base = prima candela (fallback ATTESO, nessuna nota di degrado)
    outi = pa.compute_attribution(period="INCEPTION", end_date="2026-07-30",
                                  trades=trades, prices=prices30,
                                  fx=pd.DataFrame(), fetch=_NOFETCH)
    assert outi["period"]["base_day"] == "2026-06-30"
    assert outi["portfolio_return_pct"] == pytest.approx(21.0, abs=1e-6)
    assert not any("non e' misurato" in n for n in outi["notes"])


def test_mtd_conta_il_rendimento_del_giorno_1_del_mese():
    """Bug trovato al collaudo live: base <= 01/07 mangiava il rendimento del
    1° luglio. La base MTD e' l'ultimo giorno di borsa PRIMA del mese."""
    dates = pd.to_datetime(["2026-06-30", "2026-07-01", "2026-07-02"])
    trades = _trades(("AAA.MI", "BUY", 10, 100, "EUR", "2026-06-15"))
    prices = pd.DataFrame({"AAA.MI": [100.0, 110.0, 121.0]}, index=dates)
    out = pa.compute_attribution(period="MTD", end_date="2026-07-02",
                                 trades=trades, prices=prices,
                                 fx=pd.DataFrame(), fetch=_NOFETCH)
    assert out.get("error") is None
    assert out["period"]["base_day"] == "2026-06-30"
    assert out["period"]["n_trading_days"] == 2
    assert out["portfolio_return_pct"] == pytest.approx(21.0, abs=1e-6)


def test_tool_get_attribution_registrato_e_dispatch(monkeypatch):
    """Lotto 3: tool nel registro, nel desk quant (capo=tutto), dispatch che
    normalizza il period e arriva alla funzione (monkeypatch: niente rete)."""
    import bellomberg.agents.chat_tools as ct
    assert "get_attribution" in [t["name"] for t in ct.TOOL_DEFINITIONS]
    assert "get_attribution" in [t["name"] for t in ct.get_tools_for_agent("quant")]
    assert "get_attribution" in [t["name"] for t in ct.get_tools_for_agent("capo")]
    seen = {}

    def fake(period="YTD", **kw):
        seen["period"] = period
        return {"ok": True}

    monkeypatch.setattr(pa, "compute_attribution", fake)
    out = ct.dispatch("get_attribution", {"period": "mtd"})
    assert seen["period"] == "MTD"                     # normalizzato upper
    assert out["data"]["ok"] is True                   # _stamp avvolge in data
    assert out.get("_source") == "portfolio_attribution.compute_attribution"


def test_periodo_senza_giorni_dichiarato():
    trades = _trades(("AAA.MI", "BUY", 10, 100, "EUR", "2026-06-15"))
    prices = pd.DataFrame({"AAA.MI": [100.0]}, index=_D[:1])
    out = pa.compute_attribution(period="MTD", end_date="2026-07-01",
                                 trades=trades, prices=prices,
                                 fx=pd.DataFrame(), fetch=_NOFETCH)
    assert "error" in out
