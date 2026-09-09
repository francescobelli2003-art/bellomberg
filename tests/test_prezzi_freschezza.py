"""PREZZI USA CONGELATI + FRESCHEZZA COME MISURA (19/08, Opus 5, ok PM
"ok procedi" dopo la diagnosi).

IL GUASTO TROVATO (misurato sul DB vivo il 19/08): tutti e soli i 5 ticker
serviti da polygon (OMEGA, ALFA, KAPPA, RHO, ZETA) avevano **2 soli prezzi
distinti su 73 scritture** in giornata, e il valore coincideva al centesimo
con la CHIUSURA DI IERI. Causa: `_fetch_polygon` legge `get_stock_daily`
(`/v2/aggs/.../range/1/day/`) e ne prende `bars[-1]["c"]` — la chiusura
dell'ULTIMA BARRA CHIUSA, che a mercato aperto e' quella di ieri. Polygon era
PRIMO nell'ordine delle fonti prezzi. Effetto sul book: NAV sotto di
4.054,28 EUR, P&L di giornata +315,59 invece di ~+4.656 (ZETA +30,4% invisibile).

DUE CURE, provate qui:
(A) nessuna fonte a barre giornaliere puo' servire un prezzo LIVE — polygon
    fuori dall'ordine prezzi (resta per opzioni/IV, non toccato).
(B) la freschezza diventa una MISURA sull'eta', non piu' `last_price is None`:
    lo scrittore stampava un timestamp nuovo ogni 15 min sullo stesso valore
    fermo, quindi nulla poteva dire "questo prezzo e' vecchio".
    ⚠️ Fatta SENZA colonne nuove: `MIGRATIONS` non si tocca finche' non arriva
    la lettera A/B del PM (i task schedulati le applicherebbero al primo tick).
"""
import sqlite3

import pytest

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


# ---------- (A) una fonte a barre giornaliere NON e' un prezzo live ----------

def test_polygon_fuori_dalle_fonti_prezzi_di_default():
    """Il default di update_all_prices non deve piu' contenere polygon: la sola
    funzione che abbiamo (`get_stock_daily`) rende aggregati DAILY."""
    import inspect

    from bellomberg.cli.price_updater import update_all_prices
    default = inspect.signature(update_all_prices).parameters["source_order"].default
    assert "polygon" not in default, (
        "polygon serve la chiusura dell'ultima barra daily: come prezzo live "
        "e' la chiusura di ieri (misurato 19/08 su 5 ticker)")
    assert "yfinance" in default


def test_anche_il_percorso_del_TASK_e_pulito():
    """⚠️ IL TEST CHE CONTA. Il task schedulato non chiama la firma: passa da
    main(), che si costruiva la tupla DA SOLA rimettendoci polygon dentro.
    Un test sulla sola firma sarebbe stato VERDE su una produzione ancora
    rotta — misura circolare. Qui si controllano le costanti che main() usa
    davvero, e che ora sono l'unica fonte di verita'."""
    import bellomberg.cli.price_updater as pu
    assert "polygon" not in pu.FONTI_PREZZI
    assert "polygon" not in pu.FONTI_PREZZI_NO_IBKR
    # e main() non deve avere tuple di fonti scritte a mano
    import inspect
    sorgente = inspect.getsource(pu.main)
    assert "polygon" not in sorgente, "main() si e' ricostruito le fonti a mano"
    assert "FONTI_PREZZI" in sorgente


def test_fetch_polygon_non_serve_prezzi_live():
    """Anche chiamata a mano la funzione non deve restituire un prezzo: un
    prezzo vecchio spacciato per live e' peggio di un buco dichiarato
    (regola PM 14/07)."""
    from bellomberg.cli.price_updater import _fetch_polygon
    assert _fetch_polygon("ZETA") is None
    assert _fetch_polygon("ALFA") is None


# ---------- (B) la freschezza e' una MISURA sull'eta' ----------

@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "prezzi.db")
    con = sqlite3.connect(path)
    con.execute(SCHEMA_POS)
    con.execute(SCHEMA_PRICES)
    con.execute("INSERT INTO positions (ticker,nome,quantita,prezzo_medio,valuta,is_active) "
                "VALUES ('TEST','Test SpA',10,100.0,'EUR',1)")
    con.commit()
    con.close()
    return path


def _mk(db_path, righe):
    con = sqlite3.connect(db_path)
    con.executemany("INSERT INTO position_prices (ticker,prezzo,valuta,source,timestamp) "
                    "VALUES (?,?,?,?,?)", righe)
    con.commit()
    con.close()


def _portfolio(db_path, now):
    from bellomberg.storage.memory_db import MemoryDB
    db = MemoryDB(db_path)
    return db.get_portfolio(now=now)


def test_eta_del_prezzo_e_sempre_dichiarata(db):
    _mk(db, [("TEST", 101.0, "EUR", "yfinance", "2026-08-19 21:20:00")])
    p = _portfolio(db, now="2026-08-19 21:35:00")[0]
    assert p["price_age_minutes"] == 15


def test_prezzo_fermo_da_oltre_due_ore_e_STALE(db):
    """Il caso vero: l'updater smette di scrivere e la pagina mostra ancora quel
    prezzo come fresco.

    20/08: l'istante e' stato spostato dentro la seduta. Dal 20/08 la soglia e'
    SENSIBILE AL MERCATO, e le 23:21 UTC del test originale sono un'ora in cui
    nessun mercato del book e' aperto: li' un prezzo fermo e' il prezzo giusto e
    l'allarme NON deve scattare (c'e' un test apposta piu' sotto). Qui servono le
    17:00 UTC = 13:00 a New York, mercoledi': borsa aperta, updater fermo da 121
    minuti = guasto vero."""
    _mk(db, [("TEST", 101.0, "EUR", "yfinance", "2026-08-19 14:59:00")])
    p = _portfolio(db, now="2026-08-19 17:00:00")[0]
    assert p["price_stale"] is True
    assert p["price_age_minutes"] == 121
    assert "vecchio" in p["price_source"].lower()


def test_confine_della_soglia_dichiarato(db):
    """La costante si chiama STALE_AFTER: ESATTAMENTE alla soglia il prezzo
    NON e' ancora vecchio, un minuto dopo si'. Scritto perche' un confine
    lasciato implicito e' il posto dove i numeri litigano."""
    _mk(db, [("TEST", 101.0, "EUR", "yfinance", "2026-08-19 15:00:00")])
    esatto = _portfolio(db, now="2026-08-19 17:00:00")[0]     # 120 min tondi, NY aperta
    assert esatto["price_age_minutes"] == 120
    assert esatto["price_stale"] is False


def test_prezzo_fresco_non_e_stale(db):
    _mk(db, [("TEST", 101.0, "EUR", "yfinance", "2026-08-19 23:15:00")])
    p = _portfolio(db, now="2026-08-19 23:20:00")[0]
    assert p["price_stale"] is False
    assert p["price_source"] == "snapshot"


def test_prezzo_assente_resta_stale_col_fallback_dichiarato(db):
    """Il comportamento vecchio non si perde: senza NESSUN prezzo la posizione
    vale il costo e lo dichiara."""
    p = _portfolio(db, now="2026-08-19 23:20:00")[0]
    assert p["price_stale"] is True
    assert p["price_age_minutes"] is None
    assert "prezzo_medio" in p["price_source"]
    assert p["valore_mercato"] == 10 * 100.0     # valutata al costo


def test_prezzo_vecchio_vale_ancora_per_la_valutazione(db):
    """Un prezzo vecchio resta MIGLIORE del costo: si usa, ma dichiarato.
    (Se cambiasse anche questo, il NAV salterebbe a ogni buco dell'updater.)"""
    _mk(db, [("TEST", 130.0, "EUR", "yfinance", "2026-08-19 10:00:00")])
    p = _portfolio(db, now="2026-08-19 17:00:00")[0]      # NY aperta: 420 min = guasto
    assert p["prezzo_live"] == 130.0
    assert p["valore_mercato"] == 10 * 130.0
    assert p["price_stale"] is True


def test_soglia_esplicita_e_non_cablata(db):
    """La soglia e' una policy dichiarata, non un numero sepolto nel codice."""
    from bellomberg.storage.memory_db import MemoryDB
    assert isinstance(MemoryDB.PRICE_STALE_AFTER_MIN, int)
    _mk(db, [("TEST", 101.0, "EUR", "yfinance", "2026-08-19 16:00:00")])
    db_obj = MemoryDB(db)
    db_obj.PRICE_STALE_AFTER_MIN = 30
    p = db_obj.get_portfolio(now="2026-08-19 17:00:00")[0]   # 60 min, NY aperta
    assert p["price_stale"] is True


def test_niente_now_usa_orologio_vero_senza_esplodere(db):
    """`now` iniettabile per i test, ma il default resta l'orologio."""
    _mk(db, [("TEST", 101.0, "EUR", "yfinance", "2026-08-19 21:20:00")])
    p = _portfolio(db, now=None)[0]
    assert "price_age_minutes" in p


def test_il_confronto_e_in_UTC_come_il_DB(db):
    """⚠️ REGRESSIONE VERA del 19/08. `position_prices.timestamp` e' UTC naive
    (misurato: le 23:38:52 locali stanno a disco come 21:38:53). Con
    `datetime.now()` locale un prezzo appena scritto usciva vecchio di 120
    minuti e 21 posizioni su 28 finivano marchiate VECCHIE subito dopo
    l'aggiornamento. Qui si scrive una riga con l'ora UTC di ADESSO: se
    qualcuno torna all'ora locale, in Italia l'eta' salta a ~120 e il test
    cade."""
    from datetime import datetime, timezone
    ora_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _mk(db, [("TEST", 101.0, "EUR", "yfinance", ora_utc)])
    p = _portfolio(db, now=None)[0]
    assert p["price_age_minutes"] is not None
    assert abs(p["price_age_minutes"]) <= 2, (
        f"eta' {p['price_age_minutes']} min su un prezzo appena scritto: "
        "il confronto non e' in UTC come il DB")
    assert p["price_stale"] is False


# ---------------------------------------------------------------------------
# 20/08 (ok PM "rendila sensibile"): la soglia guarda anche SE IL MERCATO E' APERTO
# ---------------------------------------------------------------------------

def test_a_mercato_chiuso_il_prezzo_fermo_NON_e_stale(db):
    """Il difetto che ha motivato la modifica: con la soglia a tempo puro, alle
    23:21 UTC (nessun mercato del book aperto) un prezzo fermo da due ore veniva
    dichiarato VECCHIO. Ma a borsa chiusa quello E' il prezzo: l'allarme si
    spegne, l'eta' resta esposta, e `price_source` dice perche'."""
    _mk(db, [("TEST", 101.0, "EUR", "yfinance", "2026-08-19 21:20:00")])
    p = _portfolio(db, now="2026-08-19 23:21:00")[0]
    assert p["price_age_minutes"] == 121          # l'eta' NON viene nascosta
    assert p["price_stale"] is False
    assert p["mercato_aperto"] is False
    assert "CHIUSO" in p["price_source"]


def test_il_lunedi_presto_il_book_non_e_tutto_STALE(db):
    """Il caso che il PM avrebbe visto ogni settimana: lunedi' 24/08 alle 05:00
    UTC (07:00 in Italia, 01:00 a New York) l'updater non ha ancora girato e
    l'ultimo prezzo e' di venerdi' sera. Con la soglia a tempo puro sarebbe stato tutto vecchio."""
    _mk(db, [("TEST", 101.0, "EUR", "yfinance", "2026-08-21 20:00:00")])
    p = _portfolio(db, now="2026-08-24 05:00:00")[0]   # lunedi' 07:00 in Italia
    assert p["price_age_minutes"] > 3000          # piu' di due giorni
    assert p["price_stale"] is False
    assert p["mercato_aperto"] is False


def test_a_mercato_aperto_l_allarme_resta(db):
    """Il rovescio: se l'updater e' morto DURANTE la seduta, l'allarme deve
    suonare — e' il guasto vero che il 19/08 nessuno ha visto."""
    _mk(db, [("TEST", 101.0, "EUR", "yfinance", "2026-08-20 14:00:00")])
    p = _portfolio(db, now="2026-08-20 17:00:00")[0]    # 13:00 a New York, aperta
    assert p["price_stale"] is True
    assert p["mercato_aperto"] is True
    assert "APERTO" in p["price_source"]


def test_mercato_indeterminabile_tiene_l_allarme(db, monkeypatch):
    """Se il modulo mercati non e' disponibile, si TIENE l'allarme: un falso
    allarme e' visibile e si corregge, un guasto nascosto no."""
    import builtins
    vero = builtins.__import__

    def finto(name, *a, **k):
        if name == "bellomberg.market_data.mercati":
            raise ImportError("simulato")
        return vero(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", finto)
    _mk(db, [("TEST", 101.0, "EUR", "yfinance", "2026-08-19 21:20:00")])
    p = _portfolio(db, now="2026-08-19 23:21:00")[0]
    assert p["price_stale"] is True
    assert p["mercato_aperto"] is None
