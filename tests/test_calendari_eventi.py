"""Voce 13 di MASTER_TODO §9-quattuortrigies (riparata 01/08/2026, Fable 5).

Il difetto, misurato sulla run V6 del 28/07: il blocco fatti diceva
"FOMC oggi... statement ore 20:00 CET" il 28/07, quando lo statement usciva
il 29/07 — la riga etichettava la distanza sulla data d'INIZIO riunione
(current_facts.py:145-148) pur tenendo in tabella le coppie (inizio, fine)
GIUSTE. E sotto c'era un secondo difetto: bellomberg_api.py:328 aveva un
SECONDO calendario FOMC cablato, divergente su novembre e dicembre.

Fatti cablati qui sotto, presi dalla fonte viva il 01/08/2026:
- federalreserve.gov/monetarypolicy/fomccalendars.htm: le 8 riunioni 2026
  sono 27-28/01, 17-18/03, 28-29/04, 16-17/06, 28-29/07, 15-16/09,
  27-28/10, 8-9/12. Lo statement esce l'ULTIMO giorno (14:00 ET = 20:00 CET).
  Le date 04/11 e 16/12 che stavano nell'API NON esistono su quel calendario.
- ecb.europa.eu (calendario Governing Council): decisioni monetarie restanti
  2026 il 10/09, 29/10, 17/12 (riunioni di due giorni, decisione il secondo).
"""
from datetime import date

import bellomberg.core.current_facts as cf
from bellomberg.api import bellomberg_api

# Le 8 riunioni FOMC 2026 dal sito della Fed (coppie inizio-fine).
FOMC_2026_FED = [
    (date(2026, 1, 27), date(2026, 1, 28)),
    (date(2026, 3, 17), date(2026, 3, 18)),
    (date(2026, 4, 28), date(2026, 4, 29)),
    (date(2026, 6, 16), date(2026, 6, 17)),
    (date(2026, 7, 28), date(2026, 7, 29)),
    (date(2026, 9, 15), date(2026, 9, 16)),
    (date(2026, 10, 27), date(2026, 10, 28)),
    (date(2026, 12, 8), date(2026, 12, 9)),
]


def _blocco_al(monkeypatch, giorno):
    """current_facts_block() con data congelata, live FRED spenti, cache vuota."""

    class _DataFinta(date):
        @classmethod
        def today(cls):
            return giorno

    monkeypatch.setattr(cf, "date", _DataFinta)
    monkeypatch.setattr(cf, "_live_numbers",
                        lambda: ["(numeri live spenti nel test)"])
    monkeypatch.setattr(cf, "_BLOCK_CACHE", {"text": None, "ts": 0.0})
    return cf.current_facts_block()


def _riga_fomc(blocco):
    # la riga del CALENDARIO, non la frase storica nel CONTEXT_SNAPSHOT
    righe = [r for r in blocco.splitlines() if r.startswith("- Prossima riunione FOMC")]
    assert len(righe) == 1, "attesa UNA riga calendario FOMC, trovate: %r" % righe
    return righe[0]


def test_fomc_cablato_combacia_con_la_fed():
    """Il calendario in current_facts deve essere quello della Fed, coppia per coppia."""
    assert cf.FOMC_2026 == FOMC_2026_FED


def test_il_giorno_uno_lo_statement_e_domani(monkeypatch):
    """28/07 (primo giorno di riunione): lo statement esce DOMANI, non oggi.

    Questa e' la frase che la V6 ha reso falsa: "FOMC oggi... statement ore
    20:00" scritta il 28/07."""
    riga = _riga_fomc(_blocco_al(monkeypatch, date(2026, 7, 28)))
    assert "DOMANI" in riga, riga
    assert "OGGI" not in riga, riga


def test_il_giorno_della_decisione_dice_oggi(monkeypatch):
    """29/07 (giorno della decisione): OGGI e' giusto."""
    riga = _riga_fomc(_blocco_al(monkeypatch, date(2026, 7, 29)))
    assert "OGGI" in riga, riga


def test_finestra_evento_usa_la_data_della_decisione(monkeypatch):
    """9/09: BCE il 10/09 + FOMC il 15-16/09 -> FINESTRA EVENTO. Il flag deve
    portare la data della DECISIONE (16/09), non quella d'inizio riunione."""
    blocco = _blocco_al(monkeypatch, date(2026, 9, 9))
    finestre = [r for r in blocco.splitlines() if "FINESTRA EVENTO" in r]
    assert len(finestre) == 1, blocco
    assert "FOMC 16/09" in finestre[0], finestre[0]


def test_api_fomc_deriva_dal_calendario_unico():
    """Il calendario dell'API non deve avere date FOMC proprie: solo le date
    di DECISIONE del calendario unico. Le due divergenti erano 04/11 e 16/12."""
    eventi = bellomberg_api._hardcoded_economic_calendar(date(2026, 10, 1), 91)
    fomc = sorted(e["date"] for e in eventi if "FOMC" in e["title"])
    assert fomc == ["2026-10-28", "2026-12-09"], fomc


def test_api_ecb_deriva_dal_calendario_unico():
    """Stessa classe, calendario BCE: le decisioni restanti 2026 sono
    10/09, 29/10, 17/12 (ecb.europa.eu)."""
    eventi = bellomberg_api._hardcoded_economic_calendar(date(2026, 9, 1), 121)
    ecb = sorted(e["date"] for e in eventi if "ECB" in e["title"])
    assert ecb == ["2026-09-10", "2026-10-29", "2026-12-17"], ecb
