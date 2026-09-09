import sys
import types

import pandas as pd
import pytest

from bellomberg.storage import classificazione
from bellomberg.storage import memory_db
import bellomberg.portfolio.portfolio_analytics as analytics
import bellomberg.portfolio.portfolio_attribution as attribution
from bellomberg.cli import price_updater


def _negozio_prezzi_sano():
    return {
        "prezzi": {"senza_yfinance": frozenset(), "coingecko": {}},
        "origine": "fixture sintetica",
        "motivo": None,
    }


def _db_senza_connessione(posizioni):
    class DB:
        scritture = []

        def get_portfolio_summary(self):
            return {"positions": posizioni}

        def update_price(self, ticker, price, valuta, source):
            self.scritture.append((ticker, price, valuta, source))

    return DB()


def _yf_con_prezzo(monkeypatch, tmp_path, prezzo=123.0):
    from bellomberg.storage import negozi_privati
    alias = tmp_path / "alias_fonti.json"
    alias.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(negozi_privati, "PERCORSO_ALIAS", str(alias))
    storico = pd.DataFrame({"Close": [prezzo]})
    finto = types.SimpleNamespace(Ticker=lambda _ticker: types.SimpleNamespace(
        history=lambda **_kwargs: storico))
    monkeypatch.setattr(price_updater, "yf", finto)
    monkeypatch.setattr(price_updater, "YFINANCE_AVAILABLE", True)
    monkeypatch.setitem(
        sys.modules,
        'bellomberg.portfolio.twr_engine',
        types.SimpleNamespace(record_nav_snapshot=lambda _db: None),
    )


def test_fx_none_non_diventa_eur_uno():
    assert price_updater.get_fx_to_eur(None) is None
    assert price_updater.convert_to_eur(100, None) is None
    assert price_updater.get_fx_to_eur_con_fonte(None) == (None, "assente")


def test_price_updater_preferisce_la_valuta_della_posizione(monkeypatch, tmp_path):
    _yf_con_prezzo(monkeypatch, tmp_path)
    monkeypatch.setattr(price_updater, "prezzi_speciali", _negozio_prezzi_sano)
    db = _db_senza_connessione([{"ticker": "FONDO", "valuta": "GBX", "prezzo_medio": 100}])

    out = price_updater.update_all_prices(db, source_order=("yfinance",), verbose=False)

    assert out["updated"] == 1 and out["failed"] == 0
    assert db.scritture == [("FONDO", 123.0, "GBX", "yfinance")]
    assert out["details"][0]["currency_label"]["fonte"] == "misura_dato"


def test_price_updater_non_sovrascrive_il_db_con_la_valuta_del_provider(monkeypatch):
    monkeypatch.setattr(price_updater, "prezzi_speciali", _negozio_prezzi_sano)
    monkeypatch.setattr(price_updater, "_fetch_ibkr", lambda *_a, **_k: (42.0, "USD"))
    monkeypatch.setitem(
        sys.modules,
        'bellomberg.portfolio.twr_engine',
        types.SimpleNamespace(record_nav_snapshot=lambda _db: None),
    )
    db = _db_senza_connessione([{"ticker": "FONDO", "valuta": "GBX", "prezzo_medio": 40}])

    out = price_updater.update_all_prices(db, source_order=("ibkr",), verbose=False)

    assert out["updated"] == 1 and out["failed"] == 0
    assert db.scritture == [("FONDO", 42.0, "GBX", "ibkr")]
    assert out["details"][0]["currency_label"]["fonte"] == "misura_dato"


def test_price_updater_non_scrive_se_la_valuta_e_ignota(monkeypatch, tmp_path):
    _yf_con_prezzo(monkeypatch, tmp_path)
    monkeypatch.setattr(price_updater, "prezzi_speciali", _negozio_prezzi_sano)
    db = _db_senza_connessione([{"ticker": "AMBIGUO", "valuta": None, "prezzo_medio": 100}])

    out = price_updater.update_all_prices(db, source_order=("yfinance",), verbose=False)

    assert out["updated"] == 0 and out["failed"] == 1
    assert db.scritture == []
    dettaglio = out["details"][0]
    assert dettaglio["currency"] is None
    assert "valuta SCONOSCIUTO" in dettaglio["error"]


def test_import_json_rifiuta_solo_la_voce_senza_valuta(tmp_path, monkeypatch):
    percorso = tmp_path / "portfolio.json"
    percorso.write_text(
        '{"azioni":[{"ticker":"AMBIGUO","nome":"Ambiguo"}],"cash_disponibile_eur":7}',
        encoding="utf-8",
    )
    db = object.__new__(memory_db.MemoryDB)
    scritture = []
    monkeypatch.setattr(db, "add_or_update_position", lambda **kwargs: scritture.append(kwargs))
    monkeypatch.setattr(
        classificazione,
        "valuta",
        lambda *_args, **_kwargs: classificazione.sconosciuto(
            "valuta", "fixture: nessuna valuta disponibile"
        ),
    )

    out = db.import_from_json(str(percorso))

    assert out["imported"] == 0 and scritture == []
    assert len(out["errors"]) == 1
    assert "AMBIGUO" in out["errors"][0] and "SCONOSCIUTO" in out["errors"][0]


def test_import_hybrid_rifiuta_solo_la_voce_senza_valuta(tmp_path, monkeypatch):
    json_path = tmp_path / "portfolio.json"
    json_path.write_text(
        '{"azioni":[{"ticker":"AMBIGUO","nome":"Alpha","tesi":"x"}]}',
        encoding="utf-8",
    )
    excel_path = tmp_path / "portfolio.xlsx"
    excel_path.touch()

    class Foglio:
        def iter_rows(self, min_row=None, max_row=None, values_only=False):
            righe = [("Nome", "Quantita", "Prezzo medio"), ("Alpha", 10, 12)]
            if min_row == 1 and max_row == 1:
                return iter(righe[:1])
            if min_row == 2:
                return iter(righe[1:])
            return iter(righe)

    class Libro:
        sheetnames = ["Holdings"]

        def __getitem__(self, _key):
            return Foglio()

    monkeypatch.setitem(sys.modules, "openpyxl", types.SimpleNamespace(load_workbook=lambda *_a, **_k: Libro()))
    monkeypatch.setattr(
        classificazione,
        "valuta",
        lambda *_args, **_kwargs: classificazione.sconosciuto(
            "valuta", "fixture: nessuna valuta disponibile"
        ),
    )
    db = object.__new__(memory_db.MemoryDB)
    scritture = []
    monkeypatch.setattr(db, "reset_positions", lambda: {"reset": True})
    monkeypatch.setattr(db, "add_or_update_position", lambda **kwargs: scritture.append(kwargs))

    out = db.import_hybrid(str(excel_path), str(json_path))

    assert out["n_imported"] == 0 and scritture == []
    assert len(out["errors"]) == 1
    assert "AMBIGUO" in out["errors"][0] and "SCONOSCIUTO" in out["errors"][0]


def test_nav_storico_usa_gbx_salvata_nel_trade(monkeypatch):
    trades = [{"id": 1, "ticker": "FONDO", "action": "BUY", "quantita": 10,
               "prezzo": 100, "valuta": "GBX", "data": "2026-07-01"}]
    giorni = pd.to_datetime(["2026-07-01", "2026-07-02"])
    prezzi = pd.DataFrame({"FONDO": [100.0, 110.0]}, index=giorni)
    viste = []

    monkeypatch.setattr(analytics, "prezzi_speciali", _negozio_prezzi_sano)
    monkeypatch.setattr(analytics, "_trade_history", lambda: trades)
    monkeypatch.setattr(analytics, "_download_prices_for_history",
                        lambda *_args: prezzi)

    def fx_storico(valute, _start, _end):
        viste.append(valute)
        return pd.DataFrame({"GBX": [0.01, 0.01]}, index=giorni)

    monkeypatch.setattr(analytics, "_build_fx_history", fx_storico)
    monkeypatch.setattr(memory_db, "leggi_cassa_portfolio", lambda path=None: {
        "cash_eur": 0.0, "cash_source": "fixture", "cash_source_note": None})
    analytics._ANALYTICS_CACHE.clear()

    out = analytics.compute_nav_history(start_date="2026-07-01",
                                        end_date="2026-07-03", force=True)

    assert "error" not in out, out
    assert viste == [["GBX"]]
    assert out["nav_eur"][-1] == pytest.approx(11.0)
    assert out["currency_labels"]["FONDO"]["fonte"] == "misura_dato"


def test_attribution_usa_gbx_salvata_nel_trade(monkeypatch):
    monkeypatch.setattr(attribution, "prezzi_speciali", _negozio_prezzi_sano)
    trades = [{"ticker": "FONDO", "action": "BUY", "quantita": 10,
               "prezzo": 100, "valuta": "GBX", "data": "2026-06-15"}]
    giorni = pd.to_datetime(["2026-07-01", "2026-07-02"])
    prezzi = pd.DataFrame({"FONDO": [100.0, 110.0]}, index=giorni)
    fx = pd.DataFrame({"GBX": [0.01, 0.01]}, index=giorni)

    out = attribution.compute_attribution(
        period="MTD", end_date="2026-07-02", trades=trades, prices=prezzi,
        fx=fx, official_series={"error": "fixture"}, fetch=lambda _tk: {},
    )

    assert "error" not in out, out
    assert out["by_position"][0]["currency"] == "GBX"
    assert out["portfolio_return_pct"] == pytest.approx(10.0)
    assert out["currency_labels"]["FONDO"]["fonte"] == "misura_dato"


def test_attribution_esclude_solo_il_ticker_con_valuta_ignota(monkeypatch):
    monkeypatch.setattr(attribution, "prezzi_speciali", _negozio_prezzi_sano)
    trades = [
        {"ticker": "SANO.MI", "action": "BUY", "quantita": 10,
         "prezzo": 100, "valuta": "EUR", "data": "2026-06-15"},
        {"ticker": "AMBIGUO", "action": "BUY", "quantita": 10,
         "prezzo": 100, "valuta": None, "data": "2026-06-15"},
    ]
    giorni = pd.to_datetime(["2026-07-01", "2026-07-02"])
    prezzi = pd.DataFrame({"SANO.MI": [100.0, 110.0], "AMBIGUO": [100.0, 150.0]},
                          index=giorni)

    out = attribution.compute_attribution(
        period="MTD", end_date="2026-07-02", trades=trades, prices=prezzi,
        fx=pd.DataFrame(), official_series={"error": "fixture"}, fetch=lambda _tk: {},
    )

    assert "error" not in out, out
    assert [r["ticker"] for r in out["by_position"]] == ["SANO.MI"]
    escluso = next(r for r in out["excluded"] if r["ticker"] == "AMBIGUO")
    assert "valuta SCONOSCIUTO" in " ".join(escluso["reasons"])


def test_concentrazione_valuta_preferisce_il_db(monkeypatch):
    posizioni = [
        {"ticker": "ALFA", "valuta": "USD", "valore_mercato": 50.0},
        {"ticker": "BETA", "valuta": "GBX", "valore_mercato": 50.0},
    ]
    monkeypatch.setattr(analytics, "MemoryDB", lambda: _db_senza_connessione(posizioni))

    out = analytics.compute_concentration()

    assert out["by_currency"]["weights_pct"] == {"GBX": 50.0, "USD": 50.0}
    assert out["by_currency"]["hhi"] == 5000.0
    assert out["currency_labels"]["ALFA"]["fonte"] == "misura_dato"
