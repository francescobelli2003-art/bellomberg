"""Il Capo e il red team devono vedere TUTTO il book (20/08, Opus 5, ok PM).

Misurato sul payload vero del 20/08, prima del fix:
  `capo.py` serializzava il portafoglio con `json.dumps(..., indent=2)[:4000]`.
  Il dump pesa 25.691 char -> al Capo ne arrivavano 4.000, cioe' il 16%:
  **4 posizioni su 28** (IOTA.L, ZETA, ORUM.MI, ACME.MI) e in coda cadevano
  nav_total_eur, cash_disponibile_eur, totale_valore_mercato_eur, totale_pl_eur.
  Il red team stava peggio: `[:2500]`.

E' la stessa malattia della voce (59) del 26/07 — "i desk leggevano 10 posizioni
su 27 e non sapevano del cash" — che per gli SPECIALISTI fu curata con la vista
compatta di `chat_tools`. Il Capo, che e' quello che DECIDE, era rimasto col dump
grezzo tagliato: decideva trim e add su nomi di cui non vedeva peso, P&L e prezzo.

La cura riusa la vista gia' collaudata (28/28 posizioni in ~5.500 char, totali in
testa), non ne scrive una nuova.
"""
import json
import os
import re

import pytest

import bellomberg.agents.chat_tools as ct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _book(n=28):
    return {
        "source": "memory_db", "fx_incomplete": None, "stale_positions": None,
        "n_positions": n,
        "positions": [{
            "ticker": f"TCK{i}.MI", "nome": f"Societa Numero {i} SpA",
            "quantita": 100.0 + i, "prezzo_medio": 12.34 + i, "prezzo_live": 13.98 + i,
            "valuta": "EUR", "valore_mercato": 1398.76 + i, "pl_eur": 165.43 + i,
            "pl_pct": 13.40 + i, "prev_close": 13.5, "prev_close_ts": "2026-08-19T17:30:00",
            "price_stale": False, "price_source": "snapshot", "data_apertura": "2026-02-01",
            "fx_to_eur": 1.0, "peso_pct": 3.70 + i,
            "tesi": "Tesi lunga del PM registrata nel DB. " * 6,
        } for i in range(n)],
        "totale_valore_mercato_eur": 222222.22,
        "cash_disponibile_eur": 111111.11,
        "nav_total_eur": 333333.33,
        "totale_pl_eur": 1234.56,
        "timestamp": "2026-08-20T22:00:00",
    }


def _sorgente(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("rel", ["src/bellomberg/agents/capo.py",
                                 "src/bellomberg/agents/red_team.py"])
def test_il_dump_troncato_sopravvive_solo_come_fallback_DICHIARATO(rel):
    """Il portafoglio non va piu' serializzato e tagliato a mano sul percorso
    normale: era quello a far sparire 24 posizioni su 28.

    Un taglio resta legittimo SOLO nel ramo di ripiego (vista non importabile), e
    li' dev'essere DICHIARATO nel prompt — un book troncato che si spaccia per
    completo e' peggio di nessun book (regola 14/07). Quindi il test non vieta il
    troncamento: pretende che ogni troncamento dica di esserlo.
    """
    righe = _sorgente(rel).splitlines()
    muti = []
    for n, r in enumerate(righe):
        if r.lstrip().startswith("#"):
            continue
        if not re.search(r"portfolio_data.*\[\s*:\s*\d+\s*\]", r):
            continue
        vicinato = "\n".join(righe[n:n + 8])
        if "VISTA COMPATTA NON DISPONIBILE" not in vicinato:
            muti.append("%s:%d  %s" % (rel, n + 1, r.strip()[:90]))
    assert not muti, (
        f"{rel} tronca il portafoglio senza dichiararlo:\n  " + "\n  ".join(muti))


@pytest.mark.parametrize("rel", ["src/bellomberg/agents/capo.py",
                                 "src/bellomberg/agents/red_team.py"])
def test_usa_la_vista_compatta_gia_collaudata(rel):
    src = _sorgente(rel)
    assert "_compatta_portfolio_live" in src, (
        f"{rel} non usa la vista compatta: il book tornerebbe troncato in coda")


def test_la_vista_da_tutte_le_posizioni_e_i_totali_in_testa():
    """La prova funzionale, sul book da 28 come quello vero."""
    out = ct._compatta_portfolio_live(_book(28))
    assert len(out["positions"]) == 28
    s = json.dumps(out, ensure_ascii=False, default=str)
    off = s.find('"positions"')
    for k in ("nav_total_eur", "cash_disponibile_eur", "totale_pl_eur"):
        assert 0 <= s.find(f'"{k}"') < off, f"{k} deve stare PRIMA delle posizioni"
    assert len(s) + ct._BUSTA_STAMP <= ct.TETTO_TOOL_RESULT


def test_il_dump_grezzo_avrebbe_perso_quasi_tutto():
    """Contro-prova che documenta il difetto: col vecchio taglio a 4.000, sul book
    da 28 posizioni ne sopravvivono pochissime e i totali no. Se un domani qualcuno
    ripristina il dump, questo test spiega cosa si perde."""
    grezzo = json.dumps(_book(28), default=str, indent=2)
    tagliato = grezzo[:4000]
    sopravvissute = sum(1 for i in range(28) if f'"TCK{i}.MI"' in tagliato)
    assert len(grezzo) > 20000
    assert sopravvissute < 10, "fixture non piu' rappresentativa del book vero"
    assert "nav_total_eur" not in tagliato
