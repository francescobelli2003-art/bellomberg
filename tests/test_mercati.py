# -*- coding: utf-8 -*-
"""Orari di borsa (20/08/2026, Opus 5, ok PM "rendila sensibile").

Il punto: `price_stale` sull'eta' pura accendeva l'allarme su TUTTO il book ogni
lunedi' mattina e ogni weekend, quando un prezzo fermo e' il prezzo giusto.
Zero rete, date esplicite: nessun test dipende da quando viene eseguito.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bellomberg.market_data import mercati


def _roma(g, m, a, hh, mm):
    return datetime(a, m, g, hh, mm, tzinfo=ZoneInfo("Europe/Rome"))


def test_milano_aperta_in_seduta():
    # giovedi' 20 agosto 2026, 11:00 a Milano
    assert mercati.aperto("ALFA.MI", _roma(20, 8, 2026, 11, 0)) is True


def test_milano_chiusa_la_sera():
    assert mercati.aperto("ALFA.MI", _roma(20, 8, 2026, 18, 30)) is False


def test_milano_chiusa_nel_weekend():
    # sabato 22 agosto 2026, in pieno orario di seduta
    assert mercati.aperto("ALFA.MI", _roma(22, 8, 2026, 11, 0)) is False
    # domenica
    assert mercati.aperto("GAMM.MI", _roma(23, 8, 2026, 11, 0)) is False


def test_lunedi_presto_milano_e_chiusa():
    """Il caso che ha motivato tutto: lunedi' 07:00, prima del primo giro
    dell'updater. Il prezzo e' fermo da venerdi' sera ed e' GIUSTO cosi'."""
    assert mercati.aperto("ALFA.MI", _roma(24, 8, 2026, 7, 0)) is False


def test_usa_apre_quando_milano_ha_gia_chiuso():
    """Le 16:00 di Roma: Milano aperta, New York aperta da mezz'ora (15:30 CEST).
    Le 18:00: Milano CHIUSA, New York ancora aperta — due verita' diverse nello
    stesso istante, ed e' esattamente il motivo per cui la soglia dev'essere
    per-ticker e non globale."""
    t = _roma(20, 8, 2026, 18, 0)
    assert mercati.aperto("ALFA.MI", t) is False
    assert mercati.aperto("MSTR", t) is True


def test_ticker_senza_suffisso_e_usa():
    assert mercati.mercato_di("PURR")[0] == "NYSE/Nasdaq"
    assert mercati.mercato_di("ZENT")[0] == "NYSE/Nasdaq"


def test_purr_non_e_trattata_come_crypto():
    """PURR e' Hyperliquid Strategies Inc, DAT equity USA (conferma PM 22/07):
    se finisse fra le 24/7 non verrebbe MAI dichiarata stantia."""
    assert "PURR" not in mercati.CRYPTO_24_7
    assert mercati.aperto("PURR", _roma(23, 8, 2026, 11, 0)) is False   # domenica


def test_crypto_sempre_aperta():
    assert mercati.aperto("BTC", _roma(23, 8, 2026, 3, 0)) is True      # domenica notte
    assert mercati.mercato_di("BTC") is None


def test_londra_ha_il_suo_fuso():
    """Londra apre alle 08:00 locali = 09:00 a Roma: alle 08:30 di Roma Milano
    e' chiusa e Londra pure (sono le 07:30 da loro)."""
    t = _roma(20, 8, 2026, 8, 30)
    assert mercati.aperto("BETA.L", t) is False
    # alle 09:30 di Roma sono le 08:30 a Londra: aperta
    assert mercati.aperto("BETA.L", _roma(20, 8, 2026, 9, 30)) is True


# Un campione INVENTATO per ogni famiglia di mercato dichiarata in `mercati.MERCATI`,
# piu' i due casi senza suffisso (USA) e il 24/7. Fino al 03/09 qui c'era uno SNAPSHOT
# del book vero del 20/08 — 28 nomi reali che pubblicavano il portafoglio del PM e che
# coprivano 5 famiglie su 15: le altre 10 non erano esercitate da nessuno. Questa lista
# non e' di nessuno e le copre tutte. MSTR e PURR restano per deroga del PM (stanno
# nelle descrizioni dei tool: toglierli significa togliere il tool).
CAMPIONI = ["ALFA.MI", "RHOM.DE", "KAPP.F", "PHIX.FRA", "THET.VI", "BETA.L",
            "IOTA.AS", "SIGM.PA", "OMEG.BR", "TAUX.HE", "PSIQ.AT", "CHIA.SW",
            "XIVA.HK", "NUEX.T", "LAMB.TO", "ZENT", "MSTR", "PURR"]


def test_ogni_famiglia_di_mercato_ha_un_campione():
    """Se domani si aggiunge una borsa a `MERCATI` senza mettere un campione qui,
    questo test cade: la copertura del modulo non puo' scendere in silenzio.
    E' la garanzia che lo snapshot del book NON dava (copriva 5 famiglie su 15)."""
    coperte = {t[t.index("."):] for t in CAMPIONI if "." in t}
    mancanti = set(mercati.MERCATI) - coperte
    assert not mancanti, "famiglie di mercato senza campione nel test: %s" % sorted(mancanti)


def test_ogni_campione_ha_un_mercato():
    """Nessun ticker deve cadere in un ramo non previsto."""
    for tk in CAMPIONI:
        s = mercati.stato(tk, _roma(20, 8, 2026, 11, 0))
        assert s["aperto"] in (True, False), f"{tk}: stato indeterminato {s}"
        assert s["mercato"], f"{tk} senza mercato"


def test_i_limiti_sono_DICHIARATI():
    """Un modulo che non gestisce i festivi deve dirlo, non lasciarlo scoprire."""
    s = mercati.stato("ALFA.MI", _roma(20, 8, 2026, 11, 0))
    assert "festivi" in s["limiti"]
    assert s["motivo"]


def test_stato_non_solleva_mai():
    for brutto in (None, "", 123, "  ", "XX.ZZZ"):
        s = mercati.stato(brutto)
        assert "aperto" in s and "mercato" in s
