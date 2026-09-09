"""Il CABLAGGIO della classificazione nel motore di valutazione, non l'helper
(05/09, Fable 5.1, classificazione lotto 2; lezione 25/08: testare la funzione non e'
testare la riga che COLLEGA).

dcf_engine.decidi_percorso() e' la decisione PURA (nessuna rete): dato ticker, .info e
negozio dice dove va il titolo — mNAV, ETF passivo, rifiuto (veicolo / sconosciuto / cintura),
rab, bank, operating — con la natura etichettata. generate_valuation la USA, e lo si prova
iniettando `fetch_info` e `negozio`: il rifiuto dello sconosciuto sta PRIMA delle tre
cinture (scettico 04/09: a valle delle cinture non misurava niente), il payload porta
`profile_source`, e nessun messaggio di rifiuto nomina un altro simbolo del negozio.

Zero rete: ogni `.info` viene da un dict, il negozio da tmp_path, output_dir in tmp_path.
"""
import json

import pytest

import bellomberg.storage.classificazione as cl
import bellomberg.valuation.dcf_engine as de
import bellomberg.portfolio.sizing_engine as se

NEGOZIO = {
    "ETFX.MI": {"tipo": "etf", "provenienza": "dichiarato", "classe_size": "veicolo"},
    "NOTA.FRA": {"tipo": "etn", "provenienza": "dichiarato"},
    "HOLD.MI": {"tipo": "holding", "provenienza": "dichiarato"},
    "FONDO.L": {"tipo": "cef", "provenienza": "dichiarato", "nav_fonte": "sito", "nav_valuta": "USD",
                "nome": "Fondo Chiuso Finto"},
    "FONDO2.L": {"tipo": "cef", "provenienza": "dichiarato"},        # fondo chiuso SENZA fonte NAV
    "TESORO": {"tipo": "dat", "provenienza": "dichiarato", "sottostante": "BTC-USD"},
    "BANCA.MI": {"tipo": "bank", "provenienza": "dichiarato"},       # dichiarata, ma Yahoo muto
}

INFO = {
    "ZZZQ.XX": {},                                                # niente: natura ignota
    "VUOTA": {"quoteType": "EQUITY"},                             # azione senza industry/sector/ricavi
    "ETFX.MI": {"quoteType": "ETF", "shortName": "Etf Finto"},
    "NOTA.FRA": {},                                               # Yahoo 404, come l'ETN vero
    "HOLD.MI": {"quoteType": "EQUITY", "industry": "Asset Management", "sector": "Financial Services",
                "shortName": "Holding Finta", "totalRevenue": 1},
    "FONDO.L": {"quoteType": "EQUITY", "industry": "Asset Management", "sector": "Financial Services"},
    "FONDO2.L": {"quoteType": "EQUITY", "industry": "Asset Management", "sector": "Financial Services",
                 "shortName": "Fondo Senza Fonte"},
    "TESORO": {"quoteType": "EQUITY", "industry": "Software - Application", "sector": "Technology"},
    "BANCA.MI": {},
    "PANIERE": {"quoteType": "MUTUALFUND", "fundFamily": "Casa", "totalAssets": 5},
    "OPER": {"quoteType": "EQUITY", "industry": "Semiconductors", "sector": "Technology",
             "shortName": "Oper Finta", "totalRevenue": 100},
}


def test_dcf_rifiuta_negozio_assente_anche_con_info_operativa(tmp_path):
    negozio = cl.carica_veicoli(str(tmp_path / "assente.json"))
    r = de.decidi_percorso("OPER", INFO["OPER"], negozio=negozio)
    assert r["percorso"] == "rifiuto_guasto"
    assert "ASSENTE" in r["motivo"]
    assert "nessuna valutazione" in r["motivo"]


def _vietati(ticker):
    """I simboli che un motivo di rifiuto per `ticker` NON deve nominare: il negozio finto, le
    VISTE del negozio VERO (vuote in un clone: misura vacua ma onesta, il cancello del repo
    pubblico fa il resto) e il negozio vero intero. Review 05/09: coi soli nomi finti i due
    test passavano anche col messaggio di ieri, che elencava tre simboli del book."""
    tutti = set(NEGOZIO) | set(se.VEHICLE_TICKERS) | set(de.DAT_TICKERS) | set(cl.carica_veicoli()["veicoli"])
    return sorted(tutti - {ticker.upper()})


@pytest.fixture
def negozio(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps(NEGOZIO), encoding="utf-8")
    return cl.carica_veicoli(str(p))


def _fetch(t):
    return dict(INFO[t.upper()])


# --------------------------------------------------------------------------
# 1. la decisione pura
# --------------------------------------------------------------------------

def test_decidi_percorso_senza_dati_e_rifiuto_sconosciuto(negozio):
    d = de.decidi_percorso("ZZZQ.XX", INFO["ZZZQ.XX"], negozio)
    assert d["percorso"] == "rifiuto_sconosciuto"
    assert d["natura"].valore is None and d["natura"].fonte == "nessuna"
    assert "SCONOSCIUT" in d["motivo"].upper() and "esposizione" in d["motivo"].lower()


def test_decidi_percorso_azione_vuota_e_rifiuto_prima_delle_cinture(negozio):
    """quoteType EQUITY senza industry ne' sector: ieri lo prendeva la terza cintura
    (:613) con engine 'unknown'; ora il profilo e' sconosciuto e il rifiuto viene prima."""
    d = de.decidi_percorso("VUOTA", INFO["VUOTA"], negozio)
    assert d["percorso"] == "rifiuto_sconosciuto"
    assert d["profilo"]["engine"] == "sconosciuto"


@pytest.mark.parametrize("tk", ["ETFX.MI", "NOTA.FRA"])
def test_decidi_percorso_etf_ed_etn_del_negozio(negozio, tk):
    d = de.decidi_percorso(tk, INFO[tk], negozio)
    assert d["percorso"] == "etf_passive"
    assert d["natura"].fonte == "registro_pm"


def test_decidi_percorso_paniere_da_quotetype(negozio):
    d = de.decidi_percorso("PANIERE", INFO["PANIERE"], negozio)
    assert d["percorso"] == "etf_passive" and d["natura"].fonte == "quote_type"


def test_decidi_percorso_holding_e_rifiuto_veicolo(negozio):
    d = de.decidi_percorso("HOLD.MI", INFO["HOLD.MI"], negozio)
    assert d["percorso"] == "rifiuto_veicolo" and d["natura"].valore == "holding"
    assert "DCF" in d["motivo"]


def test_decidi_percorso_dat_e_cef_vanno_a_mnav(negozio):
    for tk in ("TESORO", "FONDO.L"):
        d = de.decidi_percorso(tk, INFO[tk], negozio)
        assert d["percorso"] == "mnav", tk
        assert d["natura"].fonte == "registro_pm"


def test_decidi_percorso_operativa(negozio):
    d = de.decidi_percorso("OPER", INFO["OPER"], negozio)
    assert d["percorso"] == "operating" and d["profilo"]["_profile_key"] == "semiconductors"
    assert d["natura"].valore == "operating"


def test_i_motivi_di_rifiuto_non_nominano_gli_altri_simboli_del_negozio(negozio):
    """Il messaggio che _stamp consegna al modello non deve elencare la copertura: ieri
    dcf_engine.py:593 cablava tre simboli del book in un rifiuto."""
    d = de.decidi_percorso("HOLD.MI", INFO["HOLD.MI"], negozio)
    for t in _vietati("HOLD.MI"):
        assert t not in d["motivo"], t
    d2 = de.decidi_percorso("ZZZQ.XX", INFO["ZZZQ.XX"], negozio)
    for t in _vietati("ZZZQ.XX"):
        assert t not in d2["motivo"], t
    d3 = de.decidi_percorso("FONDO2.L", INFO["FONDO2.L"], negozio)
    for t in _vietati("FONDO2.L"):
        assert t not in d3["motivo"], t


def test_cef_dichiarato_senza_fonte_nav_e_rifiuto_veicolo(negozio):
    d = de.decidi_percorso("FONDO2.L", INFO["FONDO2.L"], negozio)
    assert d["percorso"] == "rifiuto_veicolo" and d["natura"].valore == "cef"
    assert "nav_fonte" in d["motivo"] and "DCF" in d["motivo"]


def test_il_rifiuto_dello_sconosciuto_non_nega_una_natura_dichiarata(negozio):
    """Una banca dichiarata nel negozio ma muta su Yahoo: il profilo e' SCONOSCIUTO (senza
    industry il DCF non parte), ma il motivo non puo' dire «nessuna fonte dice cosa sia»
    (review 05/09: lo diceva)."""
    d = de.decidi_percorso("BANCA.MI", INFO["BANCA.MI"], negozio)
    assert d["percorso"] == "rifiuto_sconosciuto" and d["natura"].valore == "bank"
    assert "bank" in d["motivo"] and "nessuna fonte" not in d["motivo"].lower()


def test_negozio_illeggibile_rifiuta_ogni_valutazione_e_lo_dice(tmp_path):
    """Percorso `rifiuto_guasto` (aggiunto al progetto): con un negozio ROTTO la natura di
    nessun titolo e' determinabile (la voce del PM potrebbe dire «veicolo»): nessuna
    valutazione, nemmeno di un'operativa fuori negozio, finche' il file non e' corretto."""
    rotto = tmp_path / "rotto.json"
    rotto.write_text("{", encoding="utf-8")
    n = cl.carica_veicoli(str(rotto))
    d = de.decidi_percorso("OPER", INFO["OPER"], n)
    assert d["percorso"] == "rifiuto_guasto" and "ILLEGGIBILE" in d["motivo"].upper()
    assert d["natura"].valore is None and "ROTT" in d["natura"].dichiarazione.upper()
    r = de.generate_valuation("OPER", output_dir=str(tmp_path), fetch_info=_fetch, negozio=n)
    assert r["ok"] is False and r["engine"] == "unknown" and "ILLEGGIBILE" in r["error"].upper()
    assert r["natura"]["valore"] is None


def test_negozio_assente_e_dichiarato_nel_payload(tmp_path):
    """Decisione PM 06/09: senza negozio nessun motore parte, anche con dati Yahoo.
    Restano le provenienze della classificazione informativa nel payload di rifiuto."""
    assente = cl.carica_veicoli(str(tmp_path / "manca.json"))
    d = de.decidi_percorso("HOLD.MI", INFO["HOLD.MI"], assente)
    assert d["percorso"] == "rifiuto_guasto"
    assert "ASSENTE" in str(d["profilo"]["_etichetta"]).upper()
    assert "ASSENTE" in d["natura"].evidenza.upper()
    r = de.generate_valuation("ZZZQ.XX", output_dir=str(tmp_path), fetch_info=_fetch, negozio=assente)
    assert r["ok"] is False and "ASSENTE" in r["profile_source"].upper()
    assert "ASSENTE" in r["natura"]["evidenza"].upper()


# --------------------------------------------------------------------------
# 2. generate_valuation usa la decisione: il cablaggio
# --------------------------------------------------------------------------

def test_generate_valuation_rifiuta_lo_sconosciuto_con_provenienza(negozio, tmp_path):
    r = de.generate_valuation("ZZZQ.XX", output_dir=str(tmp_path), fetch_info=_fetch, negozio=negozio)
    assert r["ok"] is False and r["engine"] == "sconosciuto"
    assert r["profile_source"].startswith("nessuna")
    assert "esposizione" in r["error"].lower()
    assert "natura" in r and r["natura"]["valore"] is None


def test_generate_valuation_azione_vuota_rifiutata_come_sconosciuta(negozio, tmp_path):
    r = de.generate_valuation("VUOTA", output_dir=str(tmp_path), fetch_info=_fetch, negozio=negozio)
    assert r["ok"] is False and r["engine"] == "sconosciuto"


def test_generate_valuation_etn_del_negozio_e_etf_passive_con_provenienza(negozio, tmp_path):
    r = de.generate_valuation("NOTA.FRA", output_dir=str(tmp_path), fetch_info=_fetch, negozio=negozio)
    assert r["ok"] is True and r["engine"] == "etf_passive"
    assert r["profile_source"].startswith("registro_pm")
    assert "ETN" in r["message"] or "paniere" in r["message"]


def test_generate_valuation_passa_il_negozio_al_motore_mnav(negozio, tmp_path):
    """Review 05/09 (lotto 2b): decidi_percorso decideva sul negozio iniettato e il motore mNAV
    rileggeva quello del DISCO: con un negozio finto il rifiuto portava i conteggi del negozio
    vero. Qui FONDO.L ha nav_fonte 'sito' (non nel registro): il motore deve rifiutare col
    motivo che nomina QUELLA fonte, cioe' deve aver ricevuto il negozio finto."""
    r = de.generate_valuation("FONDO.L", output_dir=str(tmp_path), fetch_info=_fetch, negozio=negozio)
    assert r["ok"] is False and r["engine"] == "mnav", r
    assert "'sito'" in r["error"] and "FETCH_NAV" in r["error"], r["error"]
    assert r["natura"]["valore"] == "cef"


def test_generate_valuation_holding_rifiutata_senza_nominare_altri(negozio, tmp_path):
    r = de.generate_valuation("HOLD.MI", output_dir=str(tmp_path), fetch_info=_fetch, negozio=negozio)
    assert r["ok"] is False and r["engine"] == "unknown"
    assert "VEICOLO" in r["error"]
    for t in _vietati("HOLD.MI"):
        assert t not in r["error"], t
    assert r["profile_source"].startswith("registro_pm")


def test_sidecar_porta_profile_source():
    assert "profile_source" in de._SIDECAR_KEYS


def test_beta_u_none_nel_profilo_non_diventa_una_finta_cintura():
    """`profile.get("beta_u", X)` su chiave presente-a-None non protegge niente (scettico
    04/09): l'helper deve tornare il default quando il profilo porta None."""
    assert de._beta_u_da_profilo({"beta_u": None}, 0.9) == 0.9
    assert de._beta_u_da_profilo({}, 0.9) == 0.9
    assert de._beta_u_da_profilo({"beta_u": 1.3}, 0.9) == 1.3


def test_dat_tickers_e_una_vista_del_negozio(negozio):
    assert de.dat_del_negozio(negozio) == {"TESORO"}
    assert isinstance(de.DAT_TICKERS, set)
