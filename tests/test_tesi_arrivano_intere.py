# -*- coding: utf-8 -*-
"""Le tesi del PM devono arrivare INTERE agli agenti (20/08 sera, Opus 5).

Il difetto: `current_facts.pm_theses_block()` tagliava ogni tesi a **260
caratteri**, e gli addendum delle trimestrali stanno in CODA (dopo la tesi
originale del PM). La forma del guasto, con cifre inventate:

    TESI-A    900 scritti -> 260 letti (29%)  addendum PERSO del tutto
    TESI-B    800 scritti -> 260 letti (33%)
    TESI-C    500 scritti -> 260 letti (52%)
    TOTALE  2.200 scritti ->   780 letti = 35%

Il taglio a 260 era ragionevole quando le tesi erano due righe scritte
all'apertura. Non lo e' piu' da quando il PM le usa come diario: il canale
dedicato era nato nel 16/07 proprio per NON troncare le tesi, e le troncava.

La regola qui e' quella di casa: se un limite deve esistere, il taglio si
DICHIARA coi numeri, non con tre puntini.
"""
import sqlite3

import pytest

from bellomberg.core import current_facts


# testo inventato
TESI_LUNGA = ("Tratta a sconto sul valore degli attivi. Preferisco un veicolo concentrato "
              "con poche posizioni seguite da vicino.\n\n"
              "[addendum 03/03/2030 - relazione semestrale] Lo sconto e' salito "
              "dal 10,0% al 20,0% (22,0% a fine mese). Valore per azione 111,11 dollari da 222,22. "
              "La scommessa sulle utility regolate pesa di piu' fra i detrattori del periodo: "
              "-10% e -20%, contributo -1,1% e -2,2%. Nuovo: obbligazioni perpetue di un "
              "emittente non quotato, prezzate con un modello interno. Cassa da 1,11 mld a "
              "111 mln. [src: relazione semestrale 2030, p. 12]")


@pytest.fixture
def db_finto(tmp_path, monkeypatch):
    """DB minimo con una posizione e una tesi lunga, al posto di quello vero."""
    p = tmp_path / "data"
    p.mkdir()
    path = str(p / "consigliere.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE positions (ticker TEXT, tesi TEXT, is_active INT, "
                "quantita REAL, prezzo_medio REAL)")
    con.execute("CREATE TABLE trade_history (id INTEGER PRIMARY KEY, ticker TEXT, "
                "data TEXT, action TEXT, pm_rationale TEXT)")
    # quantita' e prezzo inventati
    con.execute("INSERT INTO positions VALUES ('IOTA.L', ?, 1, 7, 111.11)", (TESI_LUNGA,))
    con.commit()
    con.close()
    # B4 (02/09): current_facts non costruisce piu' il percorso da __file__ —
    # legge memory_db.SQLITE_PATH, la fonte unica; si reindirizza QUELLA
    from bellomberg.storage import memory_db
    monkeypatch.setattr(memory_db, "SQLITE_PATH", path)
    current_facts._TESI_CACHE["text"] = None      # la cache non deve mascherare il test
    current_facts._TESI_CACHE["ts"] = 0
    yield path
    current_facts._TESI_CACHE["text"] = None
    current_facts._TESI_CACHE["ts"] = 0


def test_la_tesi_lunga_arriva_INTERA(db_finto):
    """Il caso vero: una tesi con addendum non deve perdere la coda, che e' la
    parte NUOVA — i fatti dell'ultima trimestrale."""
    b = current_facts.pm_theses_block()
    assert "[addendum 03/03/2030" in b, "l'addendum e' stato tagliato via"
    assert "relazione semestrale 2030, p. 12" in b, (
        "la FONTE in fondo all'addendum non arriva: e' l'ultima cosa del testo "
        "ed e' quella che rende verificabile il fatto")
    assert "Cassa da 1,11 mld a 111 mln" in b


def test_se_taglia_lo_DICHIARA_coi_numeri(db_finto, monkeypatch):
    """Un limite puo' esistere, ma dev'essere una misura dichiarata: tre puntini
    non dicono quanto manca ne' dove trovarlo."""
    monkeypatch.setattr(current_facts, "MAX_CHAR_TESI", 120)
    current_facts._TESI_CACHE["text"] = None
    current_facts._TESI_CACHE["ts"] = 0
    b = current_facts.pm_theses_block()
    assert "TRONCATA" in b or "troncata" in b
    assert str(len(TESI_LUNGA)) in b, (
        "il blocco deve dire quanto e' lunga la tesi VERA, non solo che e' tagliata")


def test_il_limite_e_una_costante_esplicita():
    """Un numero sepolto nel codice e' un numero che nessuno rivede: qui e' una
    policy con un nome, come PRICE_STALE_AFTER_MIN."""
    assert isinstance(current_facts.MAX_CHAR_TESI, int)
    assert current_facts.MAX_CHAR_TESI >= 900, (
        "sotto i 900 caratteri una tesi con addendum (tesi iniziale del PM piu' "
        "l'aggiornamento della trimestrale in coda) torna mozza")
