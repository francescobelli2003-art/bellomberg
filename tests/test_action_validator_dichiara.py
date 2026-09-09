# -*- coding: utf-8 -*-
"""Quando il validator NON PUO' controllare il sizing, lo DICE (criterio (5), lotto B,
difetto 1 della coda di A2 — MASTER §9-sexoctogies, assegnato dal PM il 06/09).

Cosa deve essere vero per chi usa il programma:
  - il blocco ACTION VALIDATOR sotto il memo e' un CANCELLO di rischio: se dice niente,
    il PM legge «nessuna obiezione». Quando il controllo non e' stato ESEGUITO, dire
    niente e' una bugia, non un silenzio;
  - da quando i tetti del sizing vengono dal mandato (lotto A2), un mandato assente fa
    tornare al motore `{"error": ...}` e il validator lo ingoiava: gli acquisti non
    venivano piu' confrontati con nessun limite, e il memo usciva pulito;
  - il vuoto GENUINO resta vuoto: se il sizing c'e' e nessuna riga sfora, non si
    aggiunge rumore a un memo che va bene.

Tutti i valori qui sotto sono INVENTATI.
"""
import pytest

from bellomberg.agents.action_validator import build_validator_block


MEMO = """
## ACTION TABLE

| Action | Ticker | EUR | Peso | Motivazione |
|---|---|---|---|---|
| BUY | AAAA | 25.000 | 3% | tesi di prova |
| ADD | BBBB | 10.000 | 1% | tesi di prova |
"""

MEMO_SOLO_VENDITE = """
## ACTION TABLE

| Action | Ticker | EUR | Peso | Motivazione |
|---|---|---|---|---|
| TRIM | CCCC | 5.000 | 1% | tesi di prova |
"""

MEMO_SENZA_AZIONI = """
## ACTION TABLE

| Action | Ticker | EUR | Peso | Motivazione |
|---|---|---|---|---|
| HOLD | DDDD | 0 | 2% | nessuna azione |
"""

SIZING_BUONO = {
    "positions": [{"ticker": "AAAA", "remaining_capacity_eur": 90000.0, "verdict": "OK"}],
    "summary": {"invested_capital_eur": 1000000.0, "params": {"single_base_pct": 10.0}},
}


def _righe(blocco):
    return [r for r in blocco.split("\n") if r.startswith("- ")]


# ------------------------------------------- il buco si dichiara

@pytest.mark.parametrize("sizing, pezzo_della_causa", [
    ({"error": "mandato non dichiarato: manca data/mandato_pm.json"}, "mandato non dichiarato"),
    ({"error": "capitale investito non disponibile"}, "capitale investito"),
    (None, "sizing"),
])
def test_senza_sizing_il_validator_dichiara_invece_di_tacere(sizing, pezzo_della_causa):
    """Il caso vero: mandato assente -> il motore rende un errore -> gli acquisti non
    vengono confrontati con nessun limite. Il blocco NON puo' essere vuoto."""
    blocco = build_validator_block(MEMO, sizing, None)
    assert blocco.strip() != "", (
        "il controllo di sizing non e' stato eseguito e il memo esce senza una parola: "
        "il PM lo legge come «nessuna obiezione»")
    assert "NON ESEGUIT" in blocco.upper(), blocco
    assert pezzo_della_causa.lower() in blocco.lower(), (
        "la dichiarazione deve dire PERCHE'", blocco)


def test_la_dichiarazione_nomina_i_controlli_saltati():
    """Non basta «qualcosa non ha funzionato»: si dice QUALE controllo non c'e' stato,
    se no il PM non sa cosa non e' stato guardato."""
    blocco = build_validator_block(MEMO, {"error": "mandato non dichiarato"}, None)
    testo = blocco.lower()
    assert "buy" in testo or "acquist" in testo, blocco
    assert "sizing" in testo, blocco


def test_vale_anche_per_le_vendite_fuori_book():
    """Il secondo controllo che moriva zitto: TRIM/SELL su un ticker non in book usa la
    stessa lista di posizioni, che senza sizing e' vuota — e il controllo spariva."""
    blocco = build_validator_block(MEMO_SOLO_VENDITE, {"error": "mandato non dichiarato"}, None)
    assert "NON ESEGUIT" in blocco.upper(), blocco


def test_db_illeggibile_dichiara_i_controlli_non_eseguiti():
    class DBRotto:
        def _conn(self):
            raise RuntimeError("db locked")

    blocco = build_validator_block(MEMO, SIZING_BUONO, DBRotto())
    testo = blocco.upper()
    assert "CONTROLLO RIPROPOSTE NON ESEGUITO" in testo
    assert "CONTROLLO FEEDBACK PM NON ESEGUITO" in testo
    assert "DB LOCKED" in testo


# ------------------------------------------- il vuoto genuino resta vuoto

def test_col_sizing_buono_nessuna_dichiarazione_spuria():
    """Un memo che va bene deve restare senza blocco: se ogni memo porta un avviso,
    gli avvisi smettono di volere dire qualcosa."""
    blocco = build_validator_block(MEMO, SIZING_BUONO, None)
    assert "NON ESEGUIT" not in blocco.upper(), blocco


def test_senza_righe_da_controllare_non_si_dichiara_niente():
    """Nessun BUY/ADD/TRIM/SELL: non c'e' nessun controllo da saltare, quindi non c'e'
    niente da dichiarare. La dichiarazione segue il LAVORO non fatto, non il dato mancante."""
    blocco = build_validator_block(MEMO_SENZA_AZIONI, {"error": "mandato non dichiarato"}, None)
    assert "NON ESEGUIT" not in blocco.upper(), blocco


def test_il_limite_per_i_nomi_nuovi_mancante_si_dichiara():
    """Terzo silenzio della stessa famiglia: il sizing c'e' ma non porta il limite base per
    nome, e i BUY su nomi NUOVI non vengono confrontati con niente."""
    sizing = {"positions": [{"ticker": "AAAA", "remaining_capacity_eur": 90000.0}],
              "summary": {"invested_capital_eur": 1000000.0, "params": {}}}
    blocco = build_validator_block(MEMO, sizing, None)
    assert "NON ESEGUIT" in blocco.upper(), blocco
    assert "bbbb" in blocco.lower() or "nuov" in blocco.lower(), blocco


def test_la_dichiarazione_non_si_traveste_da_violazione():
    """Chi legge deve distinguere «ho guardato e c'e' un problema» da «non ho guardato»:
    sono due cose diverse e la seconda non e' colpa del comitato."""
    blocco = build_validator_block(MEMO, {"error": "mandato non dichiarato"}, None)
    righe = _righe(blocco)
    assert righe, blocco
    dichiarazioni = [r for r in righe if "NON ESEGUIT" in r.upper()]
    assert dichiarazioni, righe
    for r in dichiarazioni:
        assert "oltre la policy" not in r.lower(), r
