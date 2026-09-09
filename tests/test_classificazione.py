"""Il contratto della classificazione e il negozio dei veicoli (05/09, Fable 5.1, lotto 1).

Direttiva PM 04/09: «il ripiego della classificazione non deve essere operating ma sconosciuto e
dichiarato; ogni classificazione porta la sua provenienza». Il modulo `classificazione.py` e'
il contratto: un'etichetta immutabile, UNA porta per costruirla, vocabolari chiusi, confidenza
DERIVATA dalla fonte. E il negozio `data/veicoli.json` e' l'unico posto in cui vive il TIPO di
veicolo di un simbolo: assente o illeggibile e' dichiarato con origine e motivo, una voce
malformata rende illeggibile il negozio intero (mezzo negozio caricato e' un ripiego muto).

Zero rete, zero DB, zero LLM. Nessun simbolo del book: i negozi di prova sono finti e stanno
in tmp_path. Le parole asserite nei motivi NON compaiono nel percorso temporaneo (lezione 03/09).
"""
import dataclasses
import inspect
import json
import os
import re

import pytest

import bellomberg.storage.classificazione as cl

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _scrivi(tmp_path, nome, contenuto):
    p = tmp_path / nome
    if isinstance(contenuto, str):
        p.write_text(contenuto, encoding="utf-8")
    else:
        p.write_text(json.dumps(contenuto, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(p)


NEGOZIO_FINTO = {
    "_leggimi": "negozio di prova, simboli inventati",
    "ACME.MI": {"tipo": "operating", "provenienza": "dichiarato", "settore_policy": "banks",
                "classe_size": "single", "verificato_il": "2026-09-05", "note": ""},
    "ETFX.MI": {"tipo": "etf", "provenienza": "dichiarato", "settore_tema": "ETF Tema (tema)",
                "bucket_economico": "Technology", "classe_size": "veicolo"},
    "FONDO.L": {"tipo": "cef", "provenienza": "dichiarato", "nav_fonte": "sito del gestore",
                "classe_size": "veicolo"},
    "TESORO": {"tipo": "dat", "provenienza": "dichiarato", "sottostante": "BTC-USD",
               "classe_size": "single"},
    "DEDOTTO": {"tipo": "etf", "provenienza": "derivato:quote_type"},
}


# --------------------------------------------------------------------------
# 1. Il contratto: vocabolari chiusi, confidenza derivata, due ignoranze diverse
# --------------------------------------------------------------------------

def test_vocabolari_chiusi_alzano_valueerror():
    e = cl.etichetta("natura", "etf", "quote_type", "quoteType=ETF")
    assert e.valore == "etf" and e.dominio == "natura"
    with pytest.raises(ValueError):
        cl.etichetta("dominio_inventato", "etf", "quote_type", "x")
    with pytest.raises(ValueError):
        cl.etichetta("natura", "etf", "fonte_inventata", "x")


def test_confidenza_derivata_dalla_fonte_e_non_dichiarabile():
    assert cl.etichetta("natura", "etf", "quote_type", "x").confidenza == "misurata"
    assert cl.etichetta("natura", "cef", "registro_pm", "x").confidenza == "dichiarata_pm"
    assert cl.etichetta("natura", "etf", "euristica_simbolo", "x").confidenza == "dedotta"
    # la fabbrica NON accetta una confidenza: non si puo' dichiarare «misurata» un'euristica
    assert "confidenza" not in inspect.signature(cl.etichetta).parameters
    assert set(cl._CONFIDENZA_DA_FONTE) == set(cl.FONTI)


def test_sconosciuto_e_guasto_sono_due_ignoranze_diverse():
    s = cl.sconosciuto("natura", "quoteType assente, industry e sector vuoti")
    assert s.valore is None and s.fonte == "nessuna" and s.confidenza == "nessuna"
    assert "SCONOSCIUT" in s.dichiarazione.upper()
    g = cl.guasto("natura", "negozio illeggibile: virgola in piu'")
    assert g.valore is None and g.fonte == "nessuna"
    assert "ROTT" in g.dichiarazione.upper()
    assert "SCONOSCIUT" not in g.dichiarazione.upper()
    r = cl.ripiego("classe_size", "single", "non in lista veicoli")
    assert r.valore == "single" and r.fonte == "ripiego" and r.confidenza == "nessuna"
    assert "RIPIEGO" in r.dichiarazione.upper()


def test_etichetta_e_immutabile():
    e = cl.etichetta("natura", "etf", "quote_type", "x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.valore = "operating"
    assert isinstance(e.as_dict(), dict) and e.as_dict()["dichiarazione"] == e.dichiarazione
    assert str(e).startswith("quote_type")


def test_nessuno_costruisce_etichette_fuori_dalla_fabbrica():
    """Scan del repo: il costruttore del tipo compare SOLO in classificazione.py. Se una riga
    lo chiama altrove, l'invariante «una porta sola» e' rotto anche se i test passano."""
    chiamata = re.compile(r"\b" + "Etichetta" + r"\(")
    colpevoli = []
    for cartella, sotto, file in os.walk(RADICE):
        sotto[:] = [d for d in sotto if d not in ("attic", "node_modules", "app", ".git",
                                                    "mappa", "__pycache__")]
        for f in file:
            if not f.endswith(".py") or f == "classificazione.py":
                continue
            p = os.path.join(cartella, f)
            with open(p, encoding="utf-8", errors="replace") as fh:
                for n, riga in enumerate(fh, 1):
                    if chiamata.search(riga):
                        colpevoli.append("%s:%d" % (os.path.relpath(p, RADICE), n))
    assert not colpevoli, "etichette costruite fuori dalla fabbrica: %s" % colpevoli


def test_registro_non_vuoto_e_ogni_classificatore_dichiara_lo_sconosciuto(tmp_path):
    assert cl.CLASSIFICATORI, "registro vuoto: un verde su un registro vuoto asserisce il nulla"
    # lotto 2 (MASTER §9-novemsexagies): sector_taxonomy registrera' `profilo_valutazione`;
    # allora questo test importa anche quel modulo prima di iterare il registro
    assert {"natura", "classe_size"} <= set(cl.CLASSIFICATORI)
    negozio = cl.carica_veicoli(_scrivi(tmp_path, "v.json", NEGOZIO_FINTO))
    for dominio, fn in cl.CLASSIFICATORI.items():
        e = fn("ZZZQ.XX", negozio=negozio)
        assert isinstance(e, cl.Etichetta) and e.dominio == dominio
        assert e.valore is None and e.fonte == "nessuna", dominio
        assert "SCONOSCIUT" in e.dichiarazione.upper(), dominio


# --------------------------------------------------------------------------
# 2. Il negozio: quattro stati, nessun mezzo caricamento
# --------------------------------------------------------------------------

def test_negozio_assente_dichiarato_con_esempio_da_copiare(tmp_path):
    c = cl.carica_veicoli(str(tmp_path / "manca.json"))
    assert c["veicoli"] == {} and c["origine"] == "assente"
    assert os.path.basename(cl.ESEMPIO_VEICOLI) in c["motivo"]


def test_negozio_illeggibile_json_rotto(tmp_path):
    c = cl.carica_veicoli(_scrivi(tmp_path, "rotto.json", '{"ACME.MI": {"tipo": "etf",}'))
    assert c["veicoli"] == {} and c["origine"] == "illeggibile"
    assert "JSONDecodeError" in c["motivo"]


def test_negozio_non_oggetto(tmp_path):
    c = cl.carica_veicoli(_scrivi(tmp_path, "lista.json", ["ACME.MI"]))
    assert c["origine"] == "illeggibile" and "oggetto JSON" in c["motivo"]


@pytest.mark.parametrize("chiave", [" acme.mi", "acme.mi", "ACME.MI "])
def test_chiave_non_canonica_rende_illeggibile_col_nome(tmp_path, chiave):
    c = cl.carica_veicoli(_scrivi(tmp_path, "k.json", {chiave: {"tipo": "etf", "provenienza": "dichiarato"}}))
    assert c["origine"] == "illeggibile" and repr(chiave) in c["motivo"]


def test_chiave_doppia_dopo_normalizzazione(tmp_path):
    testo = '{"ACME.MI": {"tipo": "etf", "provenienza": "dichiarato"}, "ACME.MI": {"tipo": "cef", "provenienza": "dichiarato"}}'
    # json.load tiene l'ultima: la doppia si vede solo contando le chiavi grezze
    c = cl.carica_veicoli(_scrivi(tmp_path, "d.json", testo))
    assert c["origine"] == "illeggibile" and "doppia" in c["motivo"]


@pytest.mark.parametrize("voce, parola", [
    ({"tipo": "azione", "provenienza": "dichiarato"}, "tipo"),
    ({"provenienza": "dichiarato"}, "tipo"),
    ({"tipo": "etf", "provenienza": "yahoo"}, "provenienza"),
    ({"tipo": "etf"}, "provenienza"),
    ({"tipo": "etf", "provenienza": "dichiarato", "classe_size": "grande"}, "classe_size"),
    ({"tipo": "etf", "provenienza": "dichiarato", "verificato_il": "ieri"}, "verificato_il"),
    ({"tipo": "etf", "provenienza": "dichiarato", "sottostante": 7}, "sottostante"),
    ({"tipo": "etf", "provenienza": "dichiarato", "campo_inventato": 1}, "campo_inventato"),
    ("etf", "oggetto"),
])
def test_una_voce_malformata_rende_illeggibile_il_negozio_intero(tmp_path, voce, parola):
    negozio = {"BUONA.MI": {"tipo": "etf", "provenienza": "dichiarato"}, "GUASTA.MI": voce}
    c = cl.carica_veicoli(_scrivi(tmp_path, "m.json", negozio))
    assert c["veicoli"] == {}, "mezzo negozio caricato e' un ripiego muto"
    assert c["origine"] == "illeggibile"
    assert "GUASTA.MI" in c["motivo"] and parola in c["motivo"]


def test_negozio_valido_si_carica_e_le_chiavi_di_servizio_sono_ignorate(tmp_path):
    c = cl.carica_veicoli(_scrivi(tmp_path, "ok.json", NEGOZIO_FINTO))
    assert c["origine"].endswith("ok.json") and c["motivo"] is None
    assert set(c["veicoli"]) == {"ACME.MI", "ETFX.MI", "FONDO.L", "TESORO", "DEDOTTO"}
    v = c["veicoli"]["ACME.MI"]
    assert v["tipo"] == "operating" and v["settore_policy"] == "banks"
    # i campi facoltativi assenti escono None: il consumatore non deve fare .get con default
    assert c["veicoli"]["DEDOTTO"]["classe_size"] is None
    assert c["veicoli"]["DEDOTTO"]["verificato_il"] is None


def test_esempio_tracciato_e_un_negozio_leggibile():
    c = cl.carica_veicoli(cl.ESEMPIO_VEICOLI)
    assert c["motivo"] is None, c["motivo"]
    tipi = {v["tipo"] for v in c["veicoli"].values()}
    assert {"operating", "etf", "cef", "dat"} <= tipi


@pytest.mark.parametrize("kind, sottostante, canonico", [
    ("dat_bitcoin", "BTC", "BTC-USD"), ("dat_hype", "HYPE-USD", "HYPE"),
])
def test_kind_dat_dichiarato_e_sottostante_canonico(tmp_path, kind, sottostante, canonico):
    c = cl.carica_veicoli(_scrivi(tmp_path, "kind.json", {
        "TESORO": {"tipo": "dat", "provenienza": "dichiarato", "kind": kind,
                   "sottostante": sottostante}}))
    assert c["motivo"] is None
    assert c["veicoli"]["TESORO"]["kind"] == kind
    assert c["veicoli"]["TESORO"]["sottostante"] == canonico


@pytest.mark.parametrize("tipo, sottostante", [("dat", "HYPE"), ("dat", None), ("cef", "BTC-USD")])
def test_kind_incoerente_con_tipo_o_sottostante_rifiutato(tmp_path, tipo, sottostante):
    c = cl.carica_veicoli(_scrivi(tmp_path, "kind.json", {
        "TESORO": {"tipo": tipo, "provenienza": "dichiarato", "kind": "dat_bitcoin",
                   "sottostante": sottostante}}))
    assert c["origine"] == "illeggibile"
    assert "incoerent" in c["motivo"]


# --------------------------------------------------------------------------
# 3. Le viste e il classificatore di natura: la provenienza nasce col valore
# --------------------------------------------------------------------------

def test_viste_per_tipo_e_classe(tmp_path):
    negozio = cl.carica_veicoli(_scrivi(tmp_path, "v.json", NEGOZIO_FINTO))
    v = cl.veicoli_per_tipo("etf", negozio)
    assert v["tickers"] == ["DEDOTTO", "ETFX.MI"] and v["motivo"] is None
    assert cl.veicoli_per_tipo("commodity", negozio)["tickers"] == []
    with pytest.raises(ValueError):
        cl.veicoli_per_tipo("azione", negozio)
    assert cl.classe_size_di("FONDO.L", negozio) == "veicolo"
    assert cl.classe_size_di("DEDOTTO", negozio) is None
    assert cl.classe_size_di("ZZZQ.XX", negozio) is None
    assert cl.voce("acme.mi", negozio)["tipo"] == "operating"   # lookup canonico
    assert cl.voce("ZZZQ.XX", negozio) is None


def test_vista_su_negozio_rotto_porta_origine_e_motivo(tmp_path):
    negozio = cl.carica_veicoli(_scrivi(tmp_path, "rotto.json", "{"))
    v = cl.veicoli_per_tipo("dat", negozio)
    assert v["tickers"] == [] and v["origine"] == "illeggibile" and v["motivo"]


def test_natura_dal_negozio_dichiarato_e_derivato(tmp_path):
    negozio = cl.carica_veicoli(_scrivi(tmp_path, "v.json", NEGOZIO_FINTO))
    e = cl.natura("FONDO.L", negozio)
    assert e.valore == "cef" and e.fonte == "registro_pm" and e.confidenza == "dichiarata_pm"
    assert "FONDO.L" in e.evidenza
    d = cl.natura("DEDOTTO", negozio)
    assert d.valore == "etf" and d.fonte == "quote_type" and d.confidenza == "misurata"


def test_natura_assente_e_sconosciuto_mai_operating(tmp_path):
    negozio = cl.carica_veicoli(_scrivi(tmp_path, "v.json", NEGOZIO_FINTO))
    e = cl.natura("ZZZQ.XX", negozio)
    assert e.valore is None and e.fonte == "nessuna"
    assert "SCONOSCIUT" in e.dichiarazione.upper() and "operating" not in e.dichiarazione


def test_classe_size_dal_negozio_e_sempre_policy_del_pm(tmp_path):
    negozio = cl.carica_veicoli(_scrivi(tmp_path, "v.json", NEGOZIO_FINTO))
    e = cl.classe_size("FONDO.L", negozio)
    assert e.dominio == "classe_size" and e.valore == "veicolo" and e.fonte == "registro_pm"
    s = cl.classe_size("DEDOTTO", negozio)          # voce senza classe dichiarata
    assert s.valore is None and s.fonte == "nessuna" and "classe_size" in s.evidenza
    a = cl.classe_size("ZZZQ.XX", negozio)
    assert a.valore is None and "SCONOSCIUT" in a.dichiarazione.upper()
    rotto = cl.carica_veicoli(_scrivi(tmp_path, "rotto.json", "{"))
    g = cl.classe_size("FONDO.L", rotto)
    assert g.valore is None and "ROTT" in g.dichiarazione.upper()


def test_natura_da_dato_misurata_dedotta_o_sconosciuta():
    assert cl.natura_da_dato("ETF").valore == "etf"
    assert cl.natura_da_dato("etf").fonte == "quote_type"
    e = cl.natura_da_dato("EQUITY", "Semiconductors", "Technology")
    assert e.valore == "operating" and e.confidenza == "misurata"
    d = cl.natura_da_dato("", "Semiconductors", "")
    assert d.valore == "operating" and d.fonte == "euristica_simbolo" and d.confidenza == "dedotta"
    n = cl.natura_da_dato("", "", "")
    assert n.valore is None and "SCONOSCIUT" in n.dichiarazione.upper()
    i = cl.natura_da_dato("INDEX")
    assert i.valore is None and "detenibile" in i.evidenza
    assert cl.natura_da_dato("CRYPTOCURRENCY").valore == "crypto"


def test_natura_risolta_il_dichiarato_vince_e_il_rotto_resta_rotto(tmp_path):
    negozio = cl.carica_veicoli(_scrivi(tmp_path, "v.json", NEGOZIO_FINTO))
    # il fondo chiuso che Yahoo marca EQUITY con industry: vince il negozio
    r = cl.natura_risolta("FONDO.L", "EQUITY", "Asset Management", "Financial Services", negozio)
    assert r.valore == "cef" and r.fonte == "registro_pm"
    # simbolo assente: decide il dato
    a = cl.natura_risolta("ZZZQ.XX", "ETF", "", "", negozio)
    assert a.valore == "etf" and a.fonte == "quote_type"
    # negozio assente (clone pulito): decide il dato, dichiarato come tale
    assente = cl.carica_veicoli(str(tmp_path / "manca.json"))
    b = cl.natura_risolta("ZZZQ.XX", "EQUITY", "Semiconductors", "", assente)
    assert b.valore == "operating" and b.fonte == "quote_type"
    # negozio ILLEGGIBILE: la fonte e' rotta e il dato NON la sovrascrive
    rotto = cl.carica_veicoli(_scrivi(tmp_path, "rotto.json", "{"))
    g = cl.natura_risolta("FONDO.L", "EQUITY", "Asset Management", "", rotto)
    assert g.valore is None and "ROTT" in g.dichiarazione.upper()


def test_natura_su_negozio_rotto_e_guasto_non_sconosciuto(tmp_path):
    """L'asimmetria: negozio assente/illeggibile = fonte ROTTA, non simbolo ignoto. Se le
    due cose avessero la stessa etichetta, una virgola in piu' nel negozio farebbe sembrare
    sconosciuto ogni veicolo del PM."""
    negozio = cl.carica_veicoli(_scrivi(tmp_path, "rotto.json", "{"))
    e = cl.natura("FONDO.L", negozio)
    assert e.valore is None and e.fonte == "nessuna"
    assert "ROTT" in e.dichiarazione.upper() and "SCONOSCIUT" not in e.dichiarazione.upper()
    assert "illeggibile" in e.evidenza
