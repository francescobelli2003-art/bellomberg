"""sector_taxonomy.classify legge il negozio dei veicoli e la provenienza nasce col valore
(05/09, Fable 5.1, classificazione lotto 2).

Prima: un set di ETF del PM era cablato dentro classify(), il fondo chiuso usciva
`operating` (quoteType EQUITY) e un simbolo senza industry, sector ne' quoteType riceveva il
profilo generico con engine operating, beta 1.0 e growth 6%: un DCF su un veicolo. Ora ogni
return porta `_etichetta` (come si valuta) e `_natura` (cosa e'), il ripiego e' SCONOSCIUTO
e dichiarato, e le due ignoranze (6a: industry non mappata; 6b: nessun dato) sono diverse.

`_matched` e `_profile_key` NON cambiano forma per i rami di ieri: scripts/migra_profile_key.py
li parsa in SQL. Zero rete, zero DB: il negozio e' un file finto in tmp_path, nessun simbolo
del book.
"""
import json

import pytest

import bellomberg.storage.classificazione as cl
import bellomberg.market_data.sector_taxonomy as st

NEGOZIO = {
    "ETFX.MI": {"tipo": "etf", "provenienza": "dichiarato", "classe_size": "veicolo"},
    "NOTA.FRA": {"tipo": "etn", "provenienza": "dichiarato"},
    "FONDO.L": {"tipo": "cef", "provenienza": "dichiarato", "nav_fonte": "sito", "classe_size": "veicolo"},
    "TESORO": {"tipo": "dat", "provenienza": "dichiarato", "sottostante": "BTC-USD"},
    "HOLD.MI": {"tipo": "holding", "provenienza": "dichiarato"},
    "BANCA.MI": {"tipo": "bank", "provenienza": "dichiarato", "settore_policy": "banks"},
    "ACME": {"tipo": "operating", "provenienza": "dichiarato", "profilo_valutazione": "credit services"},
    "BOH.X": {"tipo": "sconosciuto", "provenienza": "sconosciuto"},
}


@pytest.fixture
def negozio(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps(NEGOZIO), encoding="utf-8")
    return cl.carica_veicoli(str(p))


def _lab(p):
    assert isinstance(p.get("_etichetta"), cl.Etichetta), "manca _etichetta"
    assert isinstance(p.get("_natura"), cl.Etichetta), "manca _natura"
    return p["_etichetta"], p["_natura"]


# --------------------------------------------------------------------------
# i rami di ieri: stesso _matched, stessa _profile_key, e in piu' la provenienza
# --------------------------------------------------------------------------

def test_quotetype_fondo_resta_etf_con_fonte_misurata(negozio):
    p = st.classify("", "", "ZZZQ.XX", quote_type="ETF", negozio=negozio)
    assert p["engine"] == "etf_passive" and p["_matched"] == "quoteType=ETF" and p["_profile_key"] == "etf"
    e, n = _lab(p)
    assert e.valore == "etf" and e.fonte == "quote_type" and e.confidenza == "misurata"
    assert n.valore == "etf" and n.fonte == "quote_type"


def test_industry_esatta_e_keyword_dichiarano_quale_evidenza(negozio):
    p = st.classify("Semiconductors", "Technology", "ZZZQ.XX", quote_type="EQUITY", negozio=negozio)
    assert p["_matched"] == "semiconductors" and p["_profile_key"] == "semiconductors"
    e, n = _lab(p)
    assert e.fonte == "tassonomia_esatta" and e.confidenza == "dedotta" and "Semiconductors" in e.evidenza
    assert n.valore == "operating" and n.fonte == "quote_type"
    k = st.classify("Aerospace & Defense", "Industrials", "ZZZQ.XX", quote_type="EQUITY", negozio=negozio)
    assert k["_matched"] == "keyword->aerospace" and k["_profile_key"] == "aerospace"
    ek, _ = _lab(k)
    assert ek.fonte == "tassonomia_keyword" and "INDUSTRY" in ek.evidenza and "aerospace" in ek.evidenza
    s = st.classify("Boh", "Bank Holding", "ZZZQ.XX", quote_type="EQUITY", negozio=negozio)
    assert s["_matched"] == "keyword->banks - regional"
    assert "SECTOR" in s["_etichetta"].evidenza


def test_override_profilo_cambia_soltanto_con_il_negozio(tmp_path):
    def _negozio(nome, profilo):
        p = tmp_path / nome
        p.write_text(json.dumps({
            "ACME": {
                "tipo": "operating",
                "provenienza": "dichiarato",
                "profilo_valutazione": profilo,
            }
        }), encoding="utf-8")
        return cl.carica_veicoli(str(p))

    a = st.classify("Non mappata", "Altro", "ACME", quote_type="EQUITY",
                    negozio=_negozio("a.json", "credit services"))
    b = st.classify("Non mappata", "Altro", "ACME", quote_type="EQUITY",
                    negozio=_negozio("b.json", "computer hardware"))

    assert a["_profile_key"] == "credit services"
    assert b["_profile_key"] == "computer hardware"
    assert a["_etichetta"].fonte == b["_etichetta"].fonte == "registro_pm"


@pytest.mark.parametrize("stato", ["assente", "rotto"])
def test_senza_negozio_leggibile_non_esiste_override_ticker_personale(
        monkeypatch, tmp_path, stato):
    import inspect

    percorso = tmp_path / "veicoli.json"
    if stato == "rotto":
        percorso.write_text("{", encoding="utf-8")
    negozio = cl.carica_veicoli(str(percorso))
    monkeypatch.setitem(st.TICKER_PROFILE_OVERRIDES, "PRIVATE_X", "credit services")

    p = st.classify("Software - Application", "Technology", "PRIVATE_X",
                    quote_type="EQUITY", negozio=negozio)

    assert p["_profile_key"] == "software - application"
    assert "TICKER_PROFILE_OVERRIDES" not in inspect.getsource(st.classify)


def test_peer_seed_sani_restano_e_i_parametri_finanziari_non_cambiano():
    expected = {
        "banks - regional": ["JPM", "BAC", "ISP.MI", "UCG.MI"],
        "credit services": ["SOFI", "AFRM", "V", "MA"],
        "asset management": ["BLK", "BX", "AMUN.PA"],
        "aerospace": ["LMT", "RTX", "NOC", "GD", "RHM.DE", "HO.PA", "BA.L"],
        "drug manufacturers": ["JNJ", "MRK", "PFE", "NVO", "AZN", "SNY"],
        "oil & gas equipment & services": ["SLB", "HAL", "BKR", "SUBC.OL"],
        "steel": ["X", "NUE", "TKA.DE"],
    }
    for profilo, peers in expected.items():
        assert st.SUBSECTORS[profilo]["peers"] == peers

    assert "CRM" in st.SUBSECTORS["software - application"]["peers"]
    assert "NVDA" in st.SUBSECTORS["semiconductors"]["peers"]
    assert "FSLR" in st.SUBSECTORS["solar"]["peers"]
    assert "BA.L" in st.SUBSECTORS["aerospace"]["peers"]
    assert "NVO" in st.SUBSECTORS["drug manufacturers"]["peers"]
    assert "MOH" in st.SUBSECTORS["healthcare plans"]["peers"]
    assert st.SUBSECTORS["banks - regional"]["franchise_cap"] == 0.04
    assert st.SUBSECTORS["credit services"]["franchise_cap"] == 0.08
    assert st.SUBSECTORS["credit services"]["fade_years_default"] == 8


def test_profilo_dal_negozio_per_ticker_dichiarato(negozio):
    """ACME ha `profilo_valutazione` nel negozio: vale come un override di profilo, con la
    provenienza del negozio."""
    p = st.classify("Boh", "", "ACME", quote_type="EQUITY", negozio=negozio)
    assert p["_profile_key"] == "credit services" and p["engine"] == st.SUBSECTORS["credit services"]["engine"]
    assert p["_matched"] == "override ticker->credit services"   # forma fissata dal progetto
    e, n = _lab(p)
    assert e.fonte == "registro_pm" and "negozio" in e.evidenza
    assert n.valore == "operating" and n.fonte == "registro_pm"


# --------------------------------------------------------------------------
# il negozio decide la natura: i veicoli non finiscono piu' nel profilo operativo
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tk", ["ETFX.MI", "NOTA.FRA"])
def test_etf_ed_etn_del_negozio_sono_etf_passive_anche_senza_quotetype(negozio, tk):
    p = st.classify("", "", tk, quote_type="", negozio=negozio)
    assert p["engine"] == "etf_passive" and p["_profile_key"] == "etf"
    assert p["_matched"] == "etf (override ticker)"   # stessa forma di ieri per i parser SQL
    e, n = _lab(p)
    assert e.fonte == "registro_pm" and n.fonte == "registro_pm" and n.valore in ("etf", "etn")


@pytest.mark.parametrize("tk, tipo", [("FONDO.L", "cef"), ("TESORO", "dat"), ("HOLD.MI", "holding")])
def test_cef_dat_holding_del_negozio_non_sono_operating(negozio, tk, tipo):
    """Il fondo chiuso con quoteType EQUITY e industry 'Asset Management' ieri usciva
    operating: ora la natura dichiarata vince sull'industry."""
    p = st.classify("Asset Management", "Financial Services", tk, quote_type="EQUITY", negozio=negozio)
    assert p["engine"] == "mnav" and p["_profile_key"] == "veicolo_nav"
    assert p.get("growth") is None and p.get("beta_u") is None
    e, n = _lab(p)
    assert n.valore == tipo and n.fonte == "registro_pm"
    assert e.valore == "veicolo_nav" and e.fonte == "registro_pm"


def test_bank_del_negozio_passa_dalla_tassonomia_ma_porta_la_natura(negozio):
    p = st.classify("Banks - Regional", "Financial Services", "BANCA.MI", quote_type="EQUITY", negozio=negozio)
    assert p["engine"] == "bank" and p["_profile_key"] == "banks - regional"
    e, n = _lab(p)
    assert n.valore == "bank" and n.fonte == "registro_pm"
    assert e.fonte == "tassonomia_esatta"


def test_voce_sconosciuta_nel_negozio_non_decide(negozio):
    p = st.classify("Semiconductors", "Technology", "BOH.X", quote_type="EQUITY", negozio=negozio)
    assert p["_profile_key"] == "semiconductors"
    _, n = _lab(p)
    assert n.valore == "operating" and n.fonte == "quote_type"


# --------------------------------------------------------------------------
# le due ignoranze
# --------------------------------------------------------------------------

def test_6a_industry_non_mappata_resta_operating_ma_lo_dice(negozio):
    p = st.classify("Utilities - Independent Power Producers", "Utilities", "ZZZQ.XX",
                    quote_type="EQUITY", negozio=negozio)
    assert p["engine"] == "operating" and p["_matched"] == "default" and p["_profile_key"] == "default"
    assert p["growth"] == st.DEFAULT_PROFILE["growth"] and p["beta_u"] == st.DEFAULT_PROFILE["beta_u"]
    e, n = _lab(p)
    assert e.valore is None and e.fonte == "nessuna" and "SCONOSCIUT" in e.dichiarazione.upper()
    assert "Independent Power Producers" in e.evidenza
    # cio' che il modello LEGGE e' str(etichetta) = «fonte — evidenza» (profile_source), non
    # la dichiarazione: la parola deve stare li' (review 05/09)
    assert "SCONOSCIUT" in str(e).upper()
    assert n.valore == "operating" and n.fonte == "quote_type"


def test_6b_nessun_dato_e_natura_sconosciuta_con_numeri_a_none(negozio):
    p = st.classify("", "", "ZZZQ.XX", quote_type="", negozio=negozio)
    assert p["engine"] == "sconosciuto" and p["_profile_key"] == "default"
    for k in ("growth", "gm", "ebitda_m", "beta_u"):
        assert p.get(k) is None, k
    e, n = _lab(p)
    assert n.valore is None and n.fonte == "nessuna" and "SCONOSCIUT" in n.dichiarazione.upper()
    assert e.valore is None and e.fonte == "nessuna"
    assert "SCONOSCIUT" in str(e).upper() and "vuot" in e.evidenza.lower()


def test_6b_con_quotetype_equity_ma_senza_industry_e_natura_dedotta_non_misurata(negozio):
    """quoteType EQUITY senza industry ne' sector: Yahoo dice «azione» ma non dice di cosa —
    la natura resta operating per il quoteType (misurata), il profilo e' sconosciuto."""
    p = st.classify("", "", "ZZZQ.XX", quote_type="EQUITY", negozio=negozio)
    assert p["engine"] == "sconosciuto"
    e, n = _lab(p)
    assert n.valore == "operating" and n.fonte == "quote_type"
    assert e.valore is None
    # l'evidenza dice il vero: il quoteType c'era (review 05/09: diceva «quoteType vuoto»)
    assert "EQUITY" in e.evidenza


def test_negozio_assente_decide_il_dato_ma_lo_dichiara(tmp_path):
    """Clone senza negozio: decide il solo dato Yahoo (contratto del lotto 1), ma etichetta e
    natura DICONO che il negozio manca — un fondo chiuso non dichiarato finirebbe al DCF e il
    payload deve poterlo dire (review 05/09: prima non c'era traccia dell'assenza)."""
    assente = cl.carica_veicoli(str(tmp_path / "manca.json"))
    p = st.classify("Asset Management", "Financial Services", "FONDO.L", quote_type="EQUITY",
                    negozio=assente)
    assert p["_profile_key"] == "asset management"     # il dato da solo non sa che e' un fondo
    e, n = _lab(p)
    assert n.valore == "operating" and n.fonte == "quote_type" and "ASSENTE" in n.evidenza.upper()
    assert "ASSENTE" in e.evidenza.upper() and "ASSENTE" in str(e).upper()
    q = st.classify("", "", "ZZZQ.XX", quote_type="ETF", negozio=assente)
    assert "ASSENTE" in q["_etichetta"].evidenza.upper()


def test_negozio_rotto_da_guasto_non_sconosciuto(tmp_path):
    p_rotto = tmp_path / "rotto.json"
    p_rotto.write_text("{", encoding="utf-8")
    negozio = cl.carica_veicoli(str(p_rotto))
    p = st.classify("Semiconductors", "Technology", "ETFX.MI", quote_type="EQUITY", negozio=negozio)
    # senza negozio leggibile il ticker si valuta come dice Yahoo, ma la natura DICE che la
    # fonte e' rotta, non che il simbolo e' ignoto
    assert p["_profile_key"] == "semiconductors"
    _, n = _lab(p)
    assert "ROTT" in n.dichiarazione.upper()


def test_classify_senza_negozio_esplicito_legge_quello_corrente(monkeypatch, tmp_path):
    """Il kwarg e' facoltativo: senza, classify rilegge il negozio del percorso corrente."""
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"ETFX.MI": {"tipo": "etf", "provenienza": "dichiarato"}}), encoding="utf-8")
    monkeypatch.setattr(cl, "PERCORSO_VEICOLI", str(p))
    r = st.classify("", "", "ETFX.MI")
    assert r["engine"] == "etf_passive" and r["_natura"].fonte == "registro_pm"


def test_il_set_di_etf_non_e_piu_cablato_nel_sorgente():
    import inspect
    src = inspect.getsource(st.classify)
    assert "override ticker)" in src   # la forma resta per i parser
    # nessun set letterale di simboli col suffisso di borsa dentro la funzione
    import re
    assert not re.search(r'\{"[A-Z]+\.[A-Z]+"', src), "classify() cabla ancora un set di ticker"


def test_profilo_veicolo_nav_esiste_e_non_ha_numeri_operativi():
    v = st.SUBSECTORS["veicolo_nav"]
    assert v["engine"] == "mnav" and v.get("peers") == []
    for k in ("growth", "gm", "ebitda_m", "beta_u"):
        assert v.get(k) is None
