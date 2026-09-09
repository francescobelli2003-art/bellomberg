"""Guardia valute DENTRO memory_db.log_trade (blocco cassa/valute, 03/08).

La guardia dell'endpoint (POST /trade -> guardia_prezzi) copre solo il
percorso HTTP: log_trade ha altri chiamanti (script, import, automazioni
future) ed e' da li' che il carico ALFA si e' contaminato (#46/#47:
150 EUR fusi nel prezzo medio di un book USD). Difesa in profondita':
valuta discorde dalla posizione -> ValueError PRIMA dell'INSERT.

I numeri dei lotti in questi test sono dati inventati.

DIVIDEND esente, stessa regola dichiarata di guardia_prezzi: il "prezzo"
e' il dividendo per azione, e IOTA.L paga dividendi USD su quotazione GBX.
"""
import sys
import types

import pytest

from bellomberg.storage.memory_db import MemoryDB


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    pu = types.ModuleType("price_updater")
    pu.get_fx_to_eur = lambda cur: 1.0
    pu.get_fx_sources = lambda: {}
    monkeypatch.setitem(sys.modules, 'bellomberg.cli.price_updater', pu)
    return MemoryDB(db_path=str(tmp_path / "data" / "test.db"),
                    chroma_path=str(tmp_path / "chroma"))


def _conta_trade(db):
    with db._conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0]


def _posizione(db, ticker):
    with db._conn() as conn:
        return conn.execute(
            "SELECT quantita, prezzo_medio, valuta, is_active "
            "FROM positions WHERE ticker=?", (ticker,)).fetchone()


def test_buy_valuta_discorde_rifiutato_e_nulla_scritto(db):
    # il caso VERO del carico contaminato: #46 era BUY 1 @ 150 EUR su book USD
    db.log_trade("ALFA", "BUY", 7, 250.00, valuta="USD")
    prima = _conta_trade(db)
    with pytest.raises(ValueError) as exc:
        db.log_trade("ALFA", "BUY", 1, 150.0, valuta="EUR")
    assert "EUR" in str(exc.value) and "USD" in str(exc.value)
    # la garanzia e' una MISURA: conteggio identico e prezzo medio intatto
    assert _conta_trade(db) == prima
    pos = _posizione(db, "ALFA")
    assert pos["quantita"] == 7
    assert pos["prezzo_medio"] == pytest.approx(250.00)


def test_sell_e_trim_valuta_discorde_rifiutati(db):
    db.log_trade("ALFA", "BUY", 7, 250.00, valuta="USD")
    prima = _conta_trade(db)
    for azione in ("SELL", "TRIM"):
        with pytest.raises(ValueError):
            db.log_trade("ALFA", azione, 1, 150.0, valuta="EUR")
    assert _conta_trade(db) == prima
    assert _posizione(db, "ALFA")["quantita"] == 7


def test_dividend_esente_dalla_guardia(db):
    # IOTA.L: quotazione GBX, dividendo per azione in EUR — esenzione dichiarata
    db.log_trade("IOTA.L", "BUY", 100, 4200, valuta="GBX")
    tid = db.log_trade("IOTA.L", "DIVIDEND", 100, 0.47, valuta="EUR")
    assert tid is not None
    pos = _posizione(db, "IOTA.L")
    assert pos["quantita"] == 100
    assert pos["prezzo_medio"] == pytest.approx(4200)


def test_valuta_concorde_e_case_insensitive_passano(db):
    db.log_trade("ALFA", "BUY", 7, 250.00, valuta="USD")
    db.log_trade("ALFA", "BUY", 13, 111.11, valuta="usd")  # minuscolo, stessa valuta
    pos = _posizione(db, "ALFA")
    assert pos["quantita"] == 20
    assert pos["prezzo_medio"] == pytest.approx((7 * 250.00 + 13 * 111.11) / 20)


def test_prima_posizione_apre_con_qualunque_valuta(db):
    tid = db.log_trade("KAPPA.L", "BUY", 10, 350, valuta="GBX")
    assert tid is not None
    assert _posizione(db, "KAPPA.L")["valuta"] == "GBX"


def test_ticker_non_canonico_non_bypassa_la_guardia(db):
    # review 03/08: "alfa" o " ALFA " con la SELECT esatta saltavano la guardia
    # e aprivano una riga positions PARALLELA — ora il ticker e' canonico
    db.log_trade("ALFA", "BUY", 7, 250.00, valuta="USD")
    for grafia in ("alfa", " ALFA "):
        with pytest.raises(ValueError):
            db.log_trade(grafia, "BUY", 1, 150.0, valuta="EUR")
    with db._conn() as conn:
        n_pos = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    assert n_pos == 1  # nessuna riga parallela creata


def test_ticker_e_valuta_scritti_canonici(db):
    # review 03/08: "usd" passava la guardia (giusto) ma finiva RAW in
    # trade_history, spaccando ogni GROUP BY valuta futuro
    db.log_trade(" alfa ", "BUY", 7, 250.00, valuta="usd")
    with db._conn() as conn:
        r = conn.execute("SELECT ticker, valuta FROM trade_history").fetchone()
    assert (r["ticker"], r["valuta"]) == ("ALFA", "USD")
    assert _posizione(db, "ALFA")["valuta"] == "USD"


def test_valuta_null_in_positions_passa_dichiarato(db):
    # riga legacy senza valuta (0 in produzione, misurato 03/08): la guardia
    # non ha un riferimento da confrontare e NON blocca — pass-through
    # dichiarato qui, non un buco scoperto per caso
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO positions (ticker, quantita, prezzo_medio, valuta, "
            "data_apertura, last_updated, is_active) "
            "VALUES ('NULLCCY', 1, 10, NULL, '2026-01-01', '2026-01-01', 1)")
    assert db.log_trade("NULLCCY", "BUY", 1, 10, valuta="EUR") is not None


def test_riapertura_su_posizione_chiusa_con_valuta_diversa_rifiutata(db):
    # la riga positions conserva la valuta VECCHIA (l'UPDATE di riapertura non
    # la tocca): un BUY discorde lascerebbe pm in una valuta e label in
    # un'altra, e il NAV convertirebbe col cambio sbagliato. Si rifiuta: se la
    # quotazione e' cambiata davvero, e' un'operazione manuale sul DB.
    db.log_trade("XYZ", "BUY", 10, 100, valuta="USD")
    db.log_trade("XYZ", "SELL", 10, 110, valuta="USD")
    assert _posizione(db, "XYZ")["is_active"] == 0
    prima = _conta_trade(db)
    with pytest.raises(ValueError):
        db.log_trade("XYZ", "BUY", 5, 90, valuta="EUR")
    assert _conta_trade(db) == prima
