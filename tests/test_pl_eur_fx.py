"""`pl_eur_fx` — il P&L per posizione col cambio STORICO del costo (decisione PM
28/08 «procedi»; Fable 5, chat backend).

Il fatto: `pl_eur`/`pl_pct` di `/portfolio` convertono ANCHE il costo al cambio
di oggi, quindi il guadagno/perdita sulla valuta non esiste: una riga in valuta
estera esce con una percentuale diversa da quella vera e il totale del book ci
perde la componente cambio. Il totale ITD di nav_history ce l'ha (F-CONT-1), le
righe di F1 no.

La cura, chiavi ADDITIVE per posizione: `costo_eur_storico` (pool in EUR: ogni
BUY entra a qty × prezzo × FX del giorno del trade, le vendite tolgono la quota
pro-rata del pool), `pl_eur_fx` = valore di mercato EUR − costo storico,
`pl_pct_fx`, `fx_pl_eur` = pl_eur_fx − pl_eur (la sola componente cambio),
`fx_pl_note` (motivo di ogni n.d. o approssimazione dichiarata). Totali
`totale_pl_eur_fx`/`totale_fx_pl_eur` SOLO a copertura piena, altrimenti None +
`pl_fx_nd` (mai un parziale spacciato per completo). Serie FX e replay in cache
per (ultimo trade, n trade, giorno); yfinance giu' = None + nota, MAI il cambio
di oggi spacciato per storico. 0 rete: `_build_fx_history` e' stubbata.
"""
import pandas as pd
import pytest

import bellomberg.portfolio.portfolio_analytics as pa


def _fx_df(righe):
    """righe: [(giorno, {ccy: rate_ccy_to_eur})]."""
    idx = pd.to_datetime([g for g, _ in righe])
    cols = sorted({c for _, r in righe for c in r})
    return pd.DataFrame({c: [r.get(c) for _, r in righe] for c in cols}, index=idx)


def _t(id_, ticker, action, qty, prezzo, valuta, data):
    return {"id": id_, "ticker": ticker, "action": action, "quantita": qty,
            "prezzo": prezzo, "valuta": valuta, "data": data}


# ------------------------------------------------ il replay a pool EUR

def test_gbx_due_lotti_a_cambi_diversi_e_trim_pro_quota():
    fx = _fx_df([("2026-02-02", {"GBX": 0.0115}), ("2026-03-02", {"GBX": 0.0117}),
                 ("2026-06-04", {"GBX": 0.0116})])
    # dati inventati
    trades = [_t(1, "IOTA.L", "BUY", 20, 5000, "GBX", "2026-02-02T12:00:00"),
              _t(2, "IOTA.L", "BUY", 20, 2500, "GBX", "2026-03-02T12:00:00"),
              _t(3, "IOTA.L", "TRIM", 10, 3300, "GBX", "2026-06-04T12:00:00")]
    out = pa.costo_storico_per_ticker(trades, pa._fx_lookup_storico(fx))
    h = out["IOTA.L"]
    assert h["qty"] == 30
    # 20*5000*0.0115 + 20*2500*0.0117 = 1150 + 585 = 1735; TRIM 10/40 toglie il 25%
    assert h["costo_eur_storico"] == pytest.approx(1301.25)
    assert h["note"] == []


def test_usd_un_lotto():
    fx = _fx_df([("2026-02-02", {"USD": 0.90})])
    out = pa.costo_storico_per_ticker([_t(1, "ALFA", "BUY", 10, 100, "USD", "2026-02-02T12:00:00")],
                                      pa._fx_lookup_storico(fx))
    assert out["ALFA"]["costo_eur_storico"] == pytest.approx(900.0)


def test_eur_non_ha_bisogno_di_serie():
    out = pa.costo_storico_per_ticker([_t(1, "GAMMA.MI", "BUY", 10, 50, "EUR", "2026-02-02T12:00:00")],
                                      pa._fx_lookup_storico(None))
    assert out["GAMMA.MI"]["costo_eur_storico"] == pytest.approx(500.0)
    assert out["GAMMA.MI"]["note"] == []


def test_giorno_fx_mancante_usa_il_precedente_e_lo_dice():
    fx = _fx_df([("2026-02-02", {"USD": 0.90}), ("2026-02-05", {"USD": 0.95})])
    out = pa.costo_storico_per_ticker([_t(1, "ALFA", "BUY", 10, 100, "USD", "2026-02-04T12:00:00")],
                                      pa._fx_lookup_storico(fx))
    assert out["ALFA"]["costo_eur_storico"] == pytest.approx(900.0)
    assert any("2026-02-02" in n and "2026-02-04" in n for n in out["ALFA"]["note"])


def test_trade_prima_della_prima_osservazione_usa_la_prima_e_lo_dice():
    """F-CONT-1: i trade di apertura del book sono di domenica 01/02, la prima
    osservazione FX e' lunedi' 02/02."""
    fx = _fx_df([("2026-02-02", {"USD": 0.90})])
    out = pa.costo_storico_per_ticker([_t(1, "ALFA", "BUY", 10, 100, "USD", "2026-02-01T12:00:00")],
                                      pa._fx_lookup_storico(fx))
    assert out["ALFA"]["costo_eur_storico"] == pytest.approx(900.0)
    assert any("prima osservazione" in n for n in out["ALFA"]["note"])


def test_valuta_senza_serie_e_n_d_dichiarato():
    fx = _fx_df([("2026-02-02", {"USD": 0.90})])
    out = pa.costo_storico_per_ticker([_t(1, "KAPPA.TO", "BUY", 10, 100, "CAD", "2026-02-02T12:00:00")],
                                      pa._fx_lookup_storico(fx))
    assert out["KAPPA.TO"]["costo_eur_storico"] is None
    assert any("CAD" in n for n in out["KAPPA.TO"]["note"])


def test_vendita_oltre_la_quantita_azzera_il_pool_e_lo_dice():
    fx = _fx_df([("2026-02-02", {"USD": 0.90})])
    trades = [_t(1, "X", "BUY", 10, 100, "USD", "2026-02-02T12:00:00"),
              _t(2, "X", "SELL", 15, 120, "USD", "2026-02-03T12:00:00")]
    out = pa.costo_storico_per_ticker(trades, pa._fx_lookup_storico(fx))
    assert out["X"]["qty"] == 0
    assert out["X"]["costo_eur_storico"] == pytest.approx(0.0)
    assert any("fantasma" in n for n in out["X"]["note"])


# ------------------------------------------------ la cache

def test_la_serie_fx_si_scarica_una_volta_per_giorno_e_il_replay_si_rifa_sempre(monkeypatch):
    """Review 28/08: in cache va la SERIE (immutabile per giorno), non il risultato:
    un trade nuovo o CORRETTO (stesso id, stesso conteggio) entra subito."""
    chiamate = []

    def finto(currencies, start, end):
        chiamate.append((tuple(currencies), start, end))
        return _fx_df([("2026-02-02", {"USD": 0.90}), ("2026-08-27", {"USD": 0.86})])
    monkeypatch.setattr(pa, "_build_fx_history", finto)
    pa._FX_DF_CACHE.clear()
    trades = [_t(1, "ALFA", "BUY", 10, 100, "USD", "2026-02-02T12:00:00")]
    a = pa.pl_fx_per_posizione(trades, "2026-08-28")
    b = pa.pl_fx_per_posizione(trades, "2026-08-28")
    assert len(chiamate) == 1 and a["per_ticker"] == b["per_ticker"]
    assert a["per_ticker"]["ALFA"]["costo_eur_storico"] == pytest.approx(900.0)
    assert chiamate[0] == (("USD",), "2026-02-02", "2026-08-28")     # end = oggi ESCLUSO
    # trade nuovo, stesso giorno: nessun download, ma il risultato lo vede
    c = pa.pl_fx_per_posizione(trades + [_t(2, "ALFA", "BUY", 1, 100, "USD", "2026-08-27T12:00:00")], "2026-08-28")
    assert len(chiamate) == 1 and c["per_ticker"]["ALFA"]["qty"] == 11
    # trade CORRETTO (stesso id e conteggio, quantita' diversa): entra subito
    d = pa.pl_fx_per_posizione([_t(1, "ALFA", "BUY", 12, 100, "USD", "2026-02-02T12:00:00")], "2026-08-28")
    assert len(chiamate) == 1 and d["per_ticker"]["ALFA"]["costo_eur_storico"] == pytest.approx(1080.0)
    # giorno nuovo: la serie si riscarica
    pa.pl_fx_per_posizione(trades, "2026-08-29")
    assert len(chiamate) == 2


def test_un_trade_datato_oggi_prende_l_ultima_chiusura_e_lo_dice(monkeypatch):
    monkeypatch.setattr(pa, "_build_fx_history",
                        lambda c, s, e: _fx_df([("2026-08-26", {"USD": 0.88}), ("2026-08-27", {"USD": 0.86})]))
    pa._FX_DF_CACHE.clear()
    out = pa.pl_fx_per_posizione([_t(1, "ALFA", "BUY", 10, 100, "USD", "2026-08-28T15:00:00")], "2026-08-28")
    h = out["per_ticker"]["ALFA"]
    assert h["costo_eur_storico"] == pytest.approx(860.0)
    assert any("2026-08-27" in n and "2026-08-28" in n for n in h["note"])


def test_yfinance_giu_e_un_errore_dichiarato_e_non_resta_in_cache(monkeypatch):
    chiamate = []

    def rotto(currencies, start, end):
        chiamate.append(1)
        return pd.DataFrame()
    monkeypatch.setattr(pa, "_build_fx_history", rotto)
    pa._FX_DF_CACHE.clear()
    trades = [_t(1, "ALFA", "BUY", 10, 100, "USD", "2026-02-02T12:00:00")]
    a = pa.pl_fx_per_posizione(trades, "2026-08-28")
    assert "error" in a and "FX" in a["error"]
    b = pa.pl_fx_per_posizione(trades, "2026-08-28")
    assert "error" in b and len(chiamate) == 2      # l'errore non si cachea: si riprova


def test_eccezione_di_yfinance_e_un_errore_dichiarato(monkeypatch):
    def esplode(*a, **k):
        raise ConnectionError("rete giu'")
    monkeypatch.setattr(pa, "_build_fx_history", esplode)
    pa._FX_DF_CACHE.clear()
    out = pa.pl_fx_per_posizione([_t(1, "ALFA", "BUY", 10, 100, "USD", "2026-02-02T12:00:00")], "2026-08-28")
    assert "error" in out and "ConnectionError" in out["error"]


def test_book_solo_eur_non_scarica_niente(monkeypatch):
    def esplode(*a, **k):
        raise AssertionError("rete vietata: un book in EUR non ha serie da scaricare")
    monkeypatch.setattr(pa, "_build_fx_history", esplode)
    pa._FX_DF_CACHE.clear()
    out = pa.pl_fx_per_posizione([_t(1, "GAMMA.MI", "BUY", 10, 50, "EUR", "2026-02-02T12:00:00")], "2026-08-28")
    assert out["per_ticker"]["GAMMA.MI"]["costo_eur_storico"] == pytest.approx(500.0)
    assert out["fx_serie"] is None


# ------------------------------------------------ altri rami del replay (review)

def test_dividend_non_tocca_quantita_ne_pool():
    fx = _fx_df([("2026-02-02", {"USD": 0.90})])
    # dati inventati
    trades = [_t(1, "SIGMA.MI", "BUY", 700, 11.11, "EUR", "2026-02-02T12:00:00"),
              _t(2, "SIGMA.MI", "DIVIDEND", 130, 2.50, "EUR", "2026-05-20T12:00:00")]
    h = pa.costo_storico_per_ticker(trades, pa._fx_lookup_storico(fx))["SIGMA.MI"]
    assert h["qty"] == 700 and h["costo_eur_storico"] == pytest.approx(7777.0)


def test_add_dopo_trim_e_riapertura_di_una_riga_chiusa():
    fx = _fx_df([("2026-02-02", {"USD": 0.90}), ("2026-06-01", {"USD": 0.85}),
                 ("2026-06-03", {"USD": 0.85})])
    trades = [_t(1, "X", "BUY", 10, 100, "USD", "2026-02-02T12:00:00"),   # 900
              _t(2, "X", "TRIM", 5, 120, "USD", "2026-03-01T12:00:00"),   # -> 450, 5
              _t(3, "X", "ADD", 5, 200, "USD", "2026-06-01T12:00:00"),    # +850 -> 1300, 10
              _t(4, "X", "SELL", 10, 210, "USD", "2026-06-02T12:00:00"),  # chiusa -> 0
              _t(5, "X", "BUY", 2, 300, "USD", "2026-06-03T12:00:00")]    # riapre: 510
    h = pa.costo_storico_per_ticker(trades, pa._fx_lookup_storico(fx))["X"]
    assert h["qty"] == 2 and h["costo_eur_storico"] == pytest.approx(510.0)
    assert h["costo_nativo"] == pytest.approx(600.0)
    assert h["note"] == []


def test_l_ordine_dei_trade_lo_da_la_data_non_la_lista():
    fx = _fx_df([("2026-02-02", {"USD": 0.90})])
    in_disordine = [_t(2, "X", "TRIM", 5, 120, "USD", "2026-03-01T12:00:00"),
                    _t(1, "X", "BUY", 10, 100, "USD", "2026-02-02T12:00:00")]
    h = pa.costo_storico_per_ticker(in_disordine, pa._fx_lookup_storico(fx))["X"]
    assert h["qty"] == 5 and h["costo_eur_storico"] == pytest.approx(450.0)


def test_data_illeggibile_e_una_nota_per_quel_lotto_non_un_crash():
    fx = _fx_df([("2026-02-02", {"USD": 0.90})])
    trades = [_t(1, "X", "BUY", 10, 100, "USD", "non-una-data"),
              _t(2, "Y", "BUY", 10, 100, "USD", "2026-02-02T12:00:00")]
    out = pa.costo_storico_per_ticker(trades, pa._fx_lookup_storico(fx))
    assert out["X"]["costo_eur_storico"] is None and any("illeggibile" in n for n in out["X"]["note"])
    assert out["Y"]["costo_eur_storico"] == pytest.approx(900.0)


def test_le_note_uguali_non_si_ripetono_per_ogni_lotto():
    fx = _fx_df([("2026-02-02", {"USD": 0.90})])
    trades = [_t(1, "X", "BUY", 10, 100, "USD", "2026-02-01T12:00:00"),
              _t(2, "X", "BUY", 10, 100, "USD", "2026-02-01T13:00:00")]
    h = pa.costo_storico_per_ticker(trades, pa._fx_lookup_storico(fx))["X"]
    assert len(h["note"]) == 1


# ------------------------------------------------ cablaggio nel summary

def _db_vero(tmp_path, monkeypatch, fx_oggi):
    from bellomberg.storage.memory_db import MemoryDB
    from bellomberg.cli import price_updater
    monkeypatch.setattr(MemoryDB, "_init_chroma", lambda self, *a, **k: None)
    monkeypatch.setattr(price_updater, "get_fx_to_eur",
                        lambda cur: fx_oggi.get(cur.upper()))
    monkeypatch.setattr(price_updater, "get_fx_sources", lambda: {})
    pa._FX_DF_CACHE.clear()
    return MemoryDB(db_path=str(tmp_path / "data" / "fx.db"), chroma_path=str(tmp_path / "chroma"))


def _riga(s, ticker):
    return [p for p in s["positions"] if p["ticker"] == ticker][0]


def test_il_summary_porta_il_pl_col_cambio_storico_e_la_componente_cambio(tmp_path, monkeypatch):
    # storico: USD 0,90 al carico; oggi 0,86 -> il dollaro e' sceso
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: _fx_df([("2026-02-02", {"USD": 0.90})]))
    db = _db_vero(tmp_path, monkeypatch, {"USD": 0.86})
    db.log_trade("ALFA", "BUY", 10, 100.0, valuta="USD", data="2026-02-02T12:00:00")
    db.log_trade("GAMMA.MI", "BUY", 10, 50.0, valuta="EUR", data="2026-02-02T12:00:00")
    db.update_price("ALFA", 120.0, valuta="USD")
    db.update_price("GAMMA.MI", 55.0, valuta="EUR")
    s = db.get_portfolio_summary()
    n = _riga(s, "ALFA")
    assert n["costo_eur_storico"] == pytest.approx(900.0)
    assert n["pl_eur"] == pytest.approx((120 - 100) * 10 * 0.86)          # 172: solo prezzo
    assert n["pl_eur_fx"] == pytest.approx(120 * 10 * 0.86 - 900.0)        # 132: col cambio
    assert n["fx_pl_eur"] == pytest.approx(132.0 - 172.0)                  # -40: la componente cambio
    assert n["pl_pct_fx"] == pytest.approx((1032.0 - 900.0) / 900.0 * 100, abs=0.005)   # 2 decimali in produzione
    assert n["fx_pl_note"] is None
    l = _riga(s, "GAMMA.MI")
    assert l["pl_eur_fx"] == pytest.approx(l["pl_eur"]) and l["fx_pl_eur"] == 0.0
    assert s["totale_pl_eur_fx"] == pytest.approx(132.0 + 50.0)
    assert s["totale_fx_pl_eur"] == pytest.approx(-40.0)
    assert s["pl_fx_nd"] is None
    assert "nav_history" in s["fx_pl_basis"] and "fx_to_eur" in s["fx_pl_basis"]


def test_la_componente_cambio_e_zero_esatto_su_una_riga_eur_anche_se_il_prezzo_medio_e_stato_ritoccato(tmp_path, monkeypatch):
    """Review 28/08: prezzo_medio del DB e costo medio del replay differiscono di
    centesimi quando il seed storico non coincide col replay: prima quel residuo
    usciva come «cambio» su una riga gia' in EUR. La componente e' pool nativo x
    FX oggi − pool EUR."""
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: _fx_df([("2026-02-02", {"USD": 0.90})]))
    db = _db_vero(tmp_path, monkeypatch, {"USD": 0.86})
    # dati inventati
    db.log_trade("THETA.MI", "BUY", 40, 12.345, valuta="EUR", data="2026-02-02T12:00:00")
    with db._conn() as conn:
        conn.execute("UPDATE positions SET prezzo_medio=12.36 WHERE ticker='THETA.MI'")   # seed diverso
    db.update_price("THETA.MI", 15.00, valuta="EUR")
    c = _riga(db.get_portfolio_summary(), "THETA.MI")
    assert c["fx_pl_eur"] == 0.0
    assert c["pl_eur_fx"] == pytest.approx(c["pl_eur"])          # ancorato al prezzo_medio del DB


def test_posizione_estera_senza_fx_di_oggi_e_n_d_dichiarato_non_valuta_mista(tmp_path, monkeypatch):
    """Review 28/08 (ALTO): con fx_incomplete valore_mercato e pl_eur restano in
    valuta NATIVA; prima pl_eur_fx usciva come 1200 USD − 900 EUR = 300 «EUR»."""
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: _fx_df([("2026-02-02", {"USD": 0.90})]))
    db = _db_vero(tmp_path, monkeypatch, {})          # get_fx_to_eur("USD") -> 1.0? no: assente
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda cur: None)
    db.log_trade("ALFA", "BUY", 10, 100.0, valuta="USD", data="2026-02-02T12:00:00")
    db.update_price("ALFA", 120.0, valuta="USD")
    s = db.get_portfolio_summary()
    assert s["fx_incomplete"] == ["ALFA:USD"]
    n = _riga(s, "ALFA")
    assert n["pl_eur_fx"] is None and n["fx_pl_eur"] is None and n["costo_eur_storico"] is None
    assert "FX di oggi" in n["fx_pl_note"] and "USD" in n["fx_pl_note"]
    assert s["pl_fx_nd"] == ["ALFA"] and s["totale_pl_eur_fx"] is None


def test_senza_prezzo_live_niente_pl_fx_e_nota(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: _fx_df([("2026-02-02", {"USD": 0.90})]))
    db = _db_vero(tmp_path, monkeypatch, {"USD": 0.86})
    db.log_trade("ALFA", "BUY", 10, 100.0, valuta="USD", data="2026-02-02T12:00:00")
    n = _riga(db.get_portfolio_summary(), "ALFA")          # nessuno snapshot prezzo
    assert n["pl_eur"] is None
    assert n["pl_eur_fx"] is None and "n.d." in n["fx_pl_note"]


def test_yfinance_giu_nel_summary_e_null_con_nota_e_totali_non_parziali(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: pd.DataFrame())
    db = _db_vero(tmp_path, monkeypatch, {"USD": 0.86})
    db.log_trade("ALFA", "BUY", 10, 100.0, valuta="USD", data="2026-02-02T12:00:00")
    db.update_price("ALFA", 120.0, valuta="USD")
    s = db.get_portfolio_summary()
    n = _riga(s, "ALFA")
    assert n["pl_eur"] == pytest.approx(172.0)                             # il vecchio numero resta
    assert n["pl_eur_fx"] is None and n["fx_pl_eur"] is None and n["costo_eur_storico"] is None
    assert "FX" in n["fx_pl_note"]
    assert s["totale_pl_eur_fx"] is None and s["totale_fx_pl_eur"] is None
    assert s["pl_fx_nd"] == ["ALFA"]


def test_posizione_senza_trade_e_n_d_dichiarato(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: _fx_df([("2026-02-02", {"USD": 0.90})]))
    db = _db_vero(tmp_path, monkeypatch, {"USD": 0.86})
    db.log_trade("ALFA", "BUY", 10, 100.0, valuta="USD", data="2026-02-02T12:00:00")
    with db._conn() as conn:
        conn.execute("INSERT INTO positions (ticker, quantita, prezzo_medio, valuta, data_apertura, last_updated, is_active) "
                     "VALUES ('IMPORT', 5, 10, 'USD', '2026-01-01', '2026-01-01', 1)")
    db.update_price("ALFA", 120.0, valuta="USD")
    db.update_price("IMPORT", 12.0, valuta="USD")
    s = db.get_portfolio_summary()
    i = _riga(s, "IMPORT")
    assert i["pl_eur_fx"] is None and "trade" in i["fx_pl_note"]
    assert _riga(s, "ALFA")["pl_eur_fx"] is not None
    assert s["pl_fx_nd"] == ["IMPORT"] and s["totale_pl_eur_fx"] is None


def test_quantita_che_non_torna_col_replay_e_n_d_dichiarato(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "_build_fx_history", lambda c, s, e: _fx_df([("2026-02-02", {"USD": 0.90})]))
    db = _db_vero(tmp_path, monkeypatch, {"USD": 0.86})
    db.log_trade("ALFA", "BUY", 10, 100.0, valuta="USD", data="2026-02-02T12:00:00")
    with db._conn() as conn:
        conn.execute("UPDATE positions SET quantita=12 WHERE ticker='ALFA'")   # ritocco a mano
    db.update_price("ALFA", 120.0, valuta="USD")
    n = _riga(db.get_portfolio_summary(), "ALFA")
    assert n["pl_eur_fx"] is None
    assert "10" in n["fx_pl_note"] and "12" in n["fx_pl_note"]
