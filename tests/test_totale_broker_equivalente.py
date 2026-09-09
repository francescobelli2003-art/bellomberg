"""`totale_aperto_piu_realizzato_eur` — il totale «come il broker» in testa a
/portfolio (decisione PM 31/08: proposta 1 + «devi pure mettere i dividendi nel
calcolo»; Fable 5, chat backend).

Il fatto: il «29 mila» del sito del PM = aperto + realizzato, e il nostro
equivalente (misura 31/08: 22.267,38 + 5.587,04 = 27.854,42) viveva solo nei
documenti — la formula non stava in NESSUN posto del codice.

La cura, chiavi ADDITIVE in testa al summary: `realizzato_eur_vendite` (somma
`realized_eur` su SELL/TRIM) + `realizzato_vendite_n`, `dividendi_eur`
(quantita' x prezzo delle righe DIVIDEND, solo EUR: non-EUR = n.d. dichiarato),
`totale_aperto_piu_realizzato_eur` = aperto (totale_pl_eur_fx; 0 a book vuoto)
+ realizzato + dividendi — SOLO a componenti piene, mai un parziale zitto —
e `totale_aperto_piu_realizzato_note` col metodo e ogni buco dichiarato.
Il sito ESCLUDE i dividendi: la nota dice come tornare al suo numero.
0 rete: `_build_fx_history` e' stubbata come in test_pl_eur_fx.
"""
import sys
import types

import pandas as pd
import pytest

import bellomberg.portfolio.portfolio_analytics as pa


def _fx_df(righe):
    idx = pd.to_datetime([g for g, _ in righe])
    cols = sorted({c for _, r in righe for c in r})
    return pd.DataFrame({c: [r.get(c) for _, r in righe] for c in cols}, index=idx)


def _db_vero(tmp_path, monkeypatch, fx_oggi):
    from bellomberg.storage.memory_db import MemoryDB
    monkeypatch.setattr(MemoryDB, "_init_chroma", lambda self, *a, **k: None)
    pu = types.ModuleType("price_updater")
    pu.get_fx_to_eur = lambda cur: fx_oggi.get(cur.upper(), 1.0)
    pu.get_fx_sources = lambda: {}
    monkeypatch.setitem(sys.modules, 'bellomberg.cli.price_updater', pu)
    pa._FX_DF_CACHE.clear()
    return MemoryDB(db_path=str(tmp_path / "data" / "tot.db"), chroma_path=str(tmp_path / "chroma"))


def _db_eur(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: _fx_df([("2026-02-02", {"USD": 0.90})]))
    return _db_vero(tmp_path, monkeypatch, {"USD": 0.86})


def test_formula_piena_aperto_piu_vendite_piu_dividendi(tmp_path, monkeypatch):
    db = _db_eur(tmp_path, monkeypatch)
    db.log_trade("GAMMA.MI", "BUY", 10, 50.0, valuta="EUR", data="2026-02-02T12:00:00")
    db.log_trade("GAMMA.MI", "TRIM", 5, 60.0, valuta="EUR", data="2026-03-02T12:00:00")   # realized 50
    db.log_trade("GAMMA.MI", "DIVIDEND", 100, 0.5, valuta="EUR", data="2026-05-20T09:00:00")  # 50
    db.update_price("GAMMA.MI", 55.0, valuta="EUR")
    s = db.get_portfolio_summary()
    assert s["realizzato_eur_vendite"] == pytest.approx(50.0)
    assert s["realizzato_vendite_n"] == 1
    assert s["dividendi_eur"] == pytest.approx(50.0)
    # aperto: 5 residue * (55-50) = 25
    assert s["totale_pl_eur_fx"] == pytest.approx(25.0)
    assert s["totale_aperto_piu_realizzato_eur"] == pytest.approx(25.0 + 50.0 + 50.0)


def test_solo_buy_realizzato_e_dividendi_a_zero_totale_uguale_all_aperto(tmp_path, monkeypatch):
    db = _db_eur(tmp_path, monkeypatch)
    db.log_trade("GAMMA.MI", "BUY", 10, 50.0, valuta="EUR", data="2026-02-02T12:00:00")
    db.update_price("GAMMA.MI", 55.0, valuta="EUR")
    s = db.get_portfolio_summary()
    assert s["realizzato_eur_vendite"] == 0.0 and s["realizzato_vendite_n"] == 0
    assert s["dividendi_eur"] == 0.0
    assert s["totale_aperto_piu_realizzato_eur"] == pytest.approx(s["totale_pl_eur_fx"])


def test_vendita_senza_realized_eur_niente_somma_parziale_zitta(tmp_path, monkeypatch):
    db = _db_eur(tmp_path, monkeypatch)
    db.log_trade("GAMMA.MI", "BUY", 10, 50.0, valuta="EUR", data="2026-02-02T12:00:00")
    db.log_trade("GAMMA.MI", "TRIM", 5, 60.0, valuta="EUR", data="2026-03-02T12:00:00")
    with db._conn() as conn:
        conn.execute("UPDATE trade_history SET realized_eur=NULL WHERE action='TRIM'")
    db.update_price("GAMMA.MI", 55.0, valuta="EUR")
    s = db.get_portfolio_summary()
    assert s["realizzato_eur_vendite"] is None
    assert s["totale_aperto_piu_realizzato_eur"] is None
    # review 31/08 (MEDIO): «realized_eur» sta anche nella nota di METODO — qui
    # si pretende la dichiarazione del BUCO, non una sottostringa qualunque
    assert "senza realized_eur" in s["totale_aperto_piu_realizzato_note"]


def test_dividendo_non_eur_e_nd_dichiarato_non_valuta_mista(tmp_path, monkeypatch):
    db = _db_eur(tmp_path, monkeypatch)
    db.log_trade("GAMMA.MI", "BUY", 10, 50.0, valuta="EUR", data="2026-02-02T12:00:00")
    db.log_trade("OMICRON", "DIVIDEND", 100, 0.1, valuta="USD", data="2026-05-20T09:00:00")
    db.log_trade("OMICRON", "DIVIDEND", 100, 0.1, valuta="USD", data="2026-08-20T09:00:00")
    db.update_price("GAMMA.MI", 55.0, valuta="EUR")
    s = db.get_portfolio_summary()
    assert s["dividendi_eur"] is None
    assert s["totale_aperto_piu_realizzato_eur"] is None
    assert "OMICRON" in s["totale_aperto_piu_realizzato_note"] and "USD" in s["totale_aperto_piu_realizzato_note"]
    # review 31/08: note deduplicate (standard di casa dal 28/08)
    assert s["totale_aperto_piu_realizzato_note"].count("OMICRON in USD") == 1


def test_aperto_nd_propaga_il_totale_nd_ma_le_componenti_restano(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: pd.DataFrame())   # yfinance giu'
    db = _db_vero(tmp_path, monkeypatch, {"USD": 0.86})
    db.log_trade("ALFA", "BUY", 10, 100.0, valuta="USD", data="2026-02-02T12:00:00")
    db.log_trade("ALFA", "TRIM", 2, 120.0, valuta="USD", data="2026-03-02T12:00:00")
    db.update_price("ALFA", 120.0, valuta="USD")
    s = db.get_portfolio_summary()
    assert s["pl_fx_nd"] == ["ALFA"] and s["totale_pl_eur_fx"] is None
    assert s["totale_aperto_piu_realizzato_eur"] is None
    assert s["realizzato_eur_vendite"] is not None          # la componente sana resta dichiarata
    # review 31/08 (MEDIO): «aperto» apre anche la nota di metodo — si pretende
    # la dichiarazione del buco
    assert "aperto n.d." in s["totale_aperto_piu_realizzato_note"]


def test_book_vuoto_l_aperto_vale_zero_e_il_totale_e_il_realizzato(tmp_path, monkeypatch):
    db = _db_eur(tmp_path, monkeypatch)
    db.log_trade("GAMMA.MI", "BUY", 10, 50.0, valuta="EUR", data="2026-02-02T12:00:00")
    db.log_trade("GAMMA.MI", "SELL", 10, 60.0, valuta="EUR", data="2026-03-02T12:00:00")  # realized 100
    db.log_trade("GAMMA.MI", "DIVIDEND", 100, 0.5, valuta="EUR", data="2026-05-20T09:00:00")
    s = db.get_portfolio_summary()
    assert s["positions"] == [] and s["totale_pl_eur_fx"] is None
    assert s["totale_aperto_piu_realizzato_eur"] == pytest.approx(100.0 + 50.0)


def test_lettura_trade_history_fallita_e_nd_dichiarato_non_un_crash(tmp_path, monkeypatch):
    """test_ordine_book_eur usa un DB finto senza _conn: il summary deve reggere
    anche quando la lettura dei trade fallisce, dichiarando il buco."""
    db = _db_eur(tmp_path, monkeypatch)
    db.log_trade("GAMMA.MI", "BUY", 10, 50.0, valuta="EUR", data="2026-02-02T12:00:00")
    db.update_price("GAMMA.MI", 55.0, valuta="EUR")
    calls = {"n": 0}
    vera = type(db)._conn
    def _conn_rotto(self):
        calls["n"] += 1
        if calls["n"] > 2:            # le prime due letture (portfolio + trades fx) passano
            raise OSError("disco rotto")
        return vera(self)
    monkeypatch.setattr(type(db), "_conn", _conn_rotto)
    s = db.get_portfolio_summary()
    assert s["realizzato_eur_vendite"] is None and s["dividendi_eur"] is None
    assert s["totale_aperto_piu_realizzato_eur"] is None
    assert "fallita" in s["totale_aperto_piu_realizzato_note"]


def test_la_nota_dichiara_il_metodo_e_il_confronto_col_sito(tmp_path, monkeypatch):
    db = _db_eur(tmp_path, monkeypatch)
    db.log_trade("GAMMA.MI", "BUY", 10, 50.0, valuta="EUR", data="2026-02-02T12:00:00")
    db.update_price("GAMMA.MI", 55.0, valuta="EUR")
    nota = db.get_portfolio_summary()["totale_aperto_piu_realizzato_note"]
    assert "dividendi" in nota and "sito" in nota
    assert "sottra" in nota          # come tornare al numero del sito (che li esclude)
