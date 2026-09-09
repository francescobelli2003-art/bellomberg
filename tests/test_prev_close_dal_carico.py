"""P&L GG DAL CARICO per le posizioni aperte oggi (decisione PM 27/08 sera, eseguita
28/08, Fable 5, chat backend).

Il fatto: una posizione comprata OGGI non ha nessuno snapshot di prezzo del giorno
prima (l'updater fotografa solo le posizioni in book), quindi `prev_close` era n.d.
e la riga usciva dal P&L GG di F1 col flag ±PARZ — il 27/08 OMEGA (in guadagno dal
carico) e OMICRON. Un broker conta la posizione aperta oggi dal prezzo di carico.

La regola (chiave ADDITIVA `prev_close_source` su OGNI posizione):
  "position_prices"  esiste uno snapshot del giorno prima: prev_close com'era
  "carico"           NESSUNO snapshot del giorno prima E la posizione e' stata
                     aperta il giorno dell'ultimo prezzo: prev_close = prezzo_medio,
                     prev_close_ts = data_apertura
  None               n.d. dichiarato (aperta ieri senza snapshot: e' la voce futura
                     «chiusura dallo storico con fonte dichiarata», NON il carico)
Il carico NON scavalca mai uno snapshot; senza prezzo live non c'e' daily.

Il «giorno» e' quello dell'ultimo prezzo del ticker (stessa convenzione della regola
di prev_close: «il giorno prima dell'ultimo giorno con prezzi», non ieri calendario).
`data_apertura` e' in ora locale, gli snapshot in UTC naive: si confrontano le date.
"""
import sqlite3

import pytest

# I numeri di questi test sono dati inventati.

SCHEMA_POS = """
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, nome TEXT,
    quantita REAL, prezzo_medio REAL, valuta TEXT, data_apertura TEXT,
    tesi TEXT, temi_monitoraggio TEXT, note TEXT, last_updated TEXT,
    is_active INTEGER DEFAULT 1)
"""
SCHEMA_PRICES = """
CREATE TABLE position_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, prezzo REAL,
    valuta TEXT, source TEXT, timestamp TEXT)
"""
OGGI = "2026-08-27"
IERI = "2026-08-26"
ADESSO = "2026-08-27 15:00:00"       # UTC, come `now` di get_portfolio


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "carico.db")
    con = sqlite3.connect(path)
    con.execute(SCHEMA_POS)
    con.execute(SCHEMA_PRICES)
    con.commit()
    con.close()
    return path


def _posizione(db_path, ticker, prezzo_medio, data_apertura, valuta="EUR", qta=13):
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO positions (ticker,nome,quantita,prezzo_medio,valuta,data_apertura,is_active) "
                "VALUES (?,?,?,?,?,?,1)", (ticker, ticker, qta, prezzo_medio, valuta, data_apertura))
    con.commit()
    con.close()


def _prezzi(db_path, righe):
    con = sqlite3.connect(db_path)
    con.executemany("INSERT INTO position_prices (ticker,prezzo,valuta,source,timestamp) "
                    "VALUES (?,?,'EUR','yfinance',?)", righe)
    con.commit()
    con.close()


def _riga(db_path, ticker, now=ADESSO):
    from bellomberg.storage.memory_db import MemoryDB
    return [p for p in MemoryDB(db_path).get_portfolio(now=now) if p["ticker"] == ticker][0]


# ------------------------------------------------------------ i tre stati

def test_aperta_oggi_senza_snapshot_di_ieri_usa_il_carico_e_lo_dice(db):
    _posizione(db, "OMICRON", 111.11, OGGI + "T10:00:00")
    _prezzi(db, [("OMICRON", 108.00, OGGI + " 13:40:00"), ("OMICRON", 105.50, OGGI + " 14:05:00")])
    p = _riga(db, "OMICRON")
    assert p["prezzo_live"] == 105.50
    assert p["prev_close"] == 111.11
    assert p["prev_close_ts"] == OGGI + "T10:00:00"
    assert p["prev_close_source"] == "carico"


def test_aperta_ieri_senza_snapshot_di_ieri_resta_n_d(db):
    """Il caso OMEGA del 27/08: comprata ieri, registrata oggi. NON e' un carico:
    e' un buco dichiarato (la cura e' un'altra voce: chiusura dallo storico)."""
    _posizione(db, "OMEGA", 222.22, IERI + "T18:30:00")
    _prezzi(db, [("OMEGA", 250.00, OGGI + " 14:05:00")])
    p = _riga(db, "OMEGA")
    assert p["prev_close"] is None
    assert p["prev_close_ts"] is None
    assert p["prev_close_source"] is None


def test_con_uno_snapshot_di_ieri_la_fonte_e_position_prices(db):
    _posizione(db, "AAA", 100.0, "2026-02-01T12:00:00")
    _prezzi(db, [("AAA", 98.0, IERI + " 21:05:00"), ("AAA", 101.0, OGGI + " 14:05:00")])
    p = _riga(db, "AAA")
    assert p["prev_close"] == 98.0
    assert p["prev_close_ts"] == IERI + " 21:05:00"
    assert p["prev_close_source"] == "position_prices"


def test_nel_giorno_di_apertura_il_carico_vince_sugli_snapshot_vecchi(db):
    """Review 28/08: un ticker CHIUSO e ricomprato ha in tabella gli snapshot
    intraday della detenzione vecchia (ALFA: 7 del 27/08, l'ultimo delle 15:05
    UTC). Con la porta `prev_row is None` quello snapshot faceva da falsa
    «chiusura» e il GG diventava un movimento di giorni. Nel suo giorno di
    apertura la posizione conta dal carico."""
    _posizione(db, "ALFA", 230.0, OGGI + "T15:00:00")
    _prezzi(db, [("ALFA", 200.0, "2026-08-21 15:05:46"), ("ALFA", 233.0, OGGI + " 14:05:00")])
    p = _riga(db, "ALFA")
    assert p["prev_close"] == 230.0
    assert p["prev_close_ts"] == OGGI + "T15:00:00"
    assert p["prev_close_source"] == "carico"


@pytest.mark.parametrize("prezzo_medio", [None, 0])
def test_senza_prezzo_medio_niente_carico_e_nessun_crash(db, prezzo_medio):
    """Review 28/08: la guardia `prezzo_medio` non era provata; senza,
    `float(None)` manderebbe /portfolio in 500."""
    _posizione(db, "ZERO", prezzo_medio, OGGI + "T10:00:00")
    _prezzi(db, [("ZERO", 12.0, OGGI + " 14:05:00")])
    p = _riga(db, "ZERO")
    assert p["prezzo_live"] == 12.0
    assert p["prev_close"] is None
    assert p["prev_close_source"] is None


def test_senza_data_apertura_niente_carico(db):
    _posizione(db, "SENZA", 10.0, None)
    _prezzi(db, [("SENZA", 12.0, OGGI + " 14:05:00")])
    p = _riga(db, "SENZA")
    assert p["prev_close"] is None
    assert p["prev_close_source"] is None


# ------------------------------------------------------------ i confini

def test_senza_prezzo_live_niente_carico(db):
    """Nessuno snapshot: prezzo_live n.d., quindi nessun daily da fingere."""
    _posizione(db, "NUOVA", 50.0, OGGI + "T10:00:00")
    p = _riga(db, "NUOVA")
    assert p["prezzo_live"] is None
    assert p["prev_close"] is None
    assert p["prev_close_source"] is None


def test_il_giorno_e_quello_dell_ultimo_prezzo_non_di_oggi(db):
    """Updater fermo da ieri: l'ultimo prezzo e' di ieri e la posizione e' stata
    aperta ieri -> e' il daily di ieri dal carico, coerente con la regola di
    prev_close («il giorno prima dell'ultimo giorno con prezzi»)."""
    _posizione(db, "BBB", 10.0, IERI + "T16:00:00")
    _prezzi(db, [("BBB", 10.4, IERI + " 20:50:00")])
    p = _riga(db, "BBB", now=ADESSO)
    assert p["prezzo_live"] == 10.4
    assert p["prev_close"] == 10.0
    assert p["prev_close_source"] == "carico"


def test_prev_close_source_c_e_su_ogni_posizione(db):
    _posizione(db, "VEC", 100.0, "2026-02-01T12:00:00")
    _prezzi(db, [("VEC", 98.0, IERI + " 21:05:00"), ("VEC", 101.0, OGGI + " 14:05:00")])
    _posizione(db, "NUOVA", 111.11, OGGI + "T10:00:00")
    _prezzi(db, [("NUOVA", 105.50, OGGI + " 14:05:00")])
    _posizione(db, "BUCO", 222.22, IERI + "T18:30:00")
    _prezzi(db, [("BUCO", 250.00, OGGI + " 14:05:00")])
    from bellomberg.storage.memory_db import MemoryDB
    fonti = {p["ticker"]: p["prev_close_source"] for p in MemoryDB(db).get_portfolio(now=ADESSO)}
    assert fonti == {"VEC": "position_prices", "NUOVA": "carico", "BUCO": None}


# ------------------------------------------ la premessa: data_apertura vera

def _db_vero(tmp_path, monkeypatch):
    """MemoryDB con lo schema VERO (log_trade + positions), senza chroma ne' rete."""
    import sys
    import types
    from bellomberg.storage.memory_db import MemoryDB
    monkeypatch.setattr(MemoryDB, "_init_chroma", lambda self, *a, **k: None)
    pu = types.ModuleType("price_updater")
    pu.get_fx_to_eur = lambda cur: 1.0
    pu.get_fx_sources = lambda: {}
    monkeypatch.setitem(sys.modules, 'bellomberg.cli.price_updater', pu)
    return MemoryDB(db_path=str(tmp_path / "data" / "vero.db"), chroma_path=str(tmp_path / "chroma"))


def _apertura(db, ticker):
    with db._conn() as conn:
        r = conn.execute("SELECT quantita, is_active, data_apertura FROM positions WHERE ticker=?",
                         (ticker,)).fetchone()
    return r["quantita"], r["is_active"], r["data_apertura"]


def test_riapertura_di_una_riga_chiusa_rinnova_data_apertura(tmp_path, monkeypatch):
    """Review 28/08: `log_trade` BUY su una riga CHIUSA riusava la data_apertura
    della detenzione vecchia per sempre: «aperta oggi» non sarebbe mai stato vero
    per un ri-acquisto (ALFA chiusa il 27/08)."""
    db = _db_vero(tmp_path, monkeypatch)
    db.log_trade("ALFA", "BUY", 40, 250.00, valuta="USD", data="2026-08-26T09:00:00")
    db.log_trade("ALFA", "SELL", 40, 300.00, valuta="USD", data="2026-08-27T11:00:00")
    assert _apertura(db, "ALFA")[:2] == (0, 0)
    db.log_trade("ALFA", "BUY", 7, 111.11, valuta="USD", data="2026-09-02T15:00:00")
    assert _apertura(db, "ALFA") == (7, 1, "2026-09-02T15:00:00")


def test_add_su_una_riga_aperta_non_tocca_data_apertura(tmp_path, monkeypatch):
    db = _db_vero(tmp_path, monkeypatch)
    db.log_trade("NORD", "BUY", 30, 240.00, valuta="USD", data="2026-06-10T12:00:00")
    db.log_trade("NORD", "BUY", 11, 190.00, valuta="USD", data="2026-07-15T12:00:00")
    assert _apertura(db, "NORD") == (41, 1, "2026-06-10T12:00:00")


# ------------------------------------------------------------ cablaggio

def test_il_summary_di_portfolio_porta_la_fonte(db, monkeypatch):
    """`/portfolio` restituisce get_portfolio_summary(): la chiave deve arrivare
    intera al frontend, non fermarsi in get_portfolio."""
    _posizione(db, "OMICRON", 111.11, OGGI + "T10:00:00")
    _prezzi(db, [("OMICRON", 105.50, OGGI + " 14:05:00")])
    from bellomberg.cli import price_updater
    monkeypatch.setattr(price_updater, "get_fx_to_eur", lambda cur: 1.0)
    from bellomberg.storage.memory_db import MemoryDB
    s = MemoryDB(db).get_portfolio_summary()
    p = [x for x in s["positions"] if x["ticker"] == "OMICRON"][0]
    assert p["prev_close"] == 111.11 and p["prev_close_source"] == "carico"
