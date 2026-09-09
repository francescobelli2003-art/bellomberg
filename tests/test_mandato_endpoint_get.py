# -*- coding: utf-8 -*-
"""`GET /mandato` — quello che apre la pagina Mandato (F11). Criterio (5), lotto B.

Cosa deve essere vero per chi usa il programma:
  - il mandato mai compilato e' uno STATO da disegnare, non un errore: la pagina deve
    poter aprire il modulo vuoto, quindi la risposta e' buona e porta lo schema dei campi;
  - **il file «in uso» NON e' «non dichiarato»**: nasce da una scrittura concorrente e
    significa «c'e' ed e' pieno, riprova fra un istante». Se l'API lo appiattisce su
    `dichiarato: false`, la pagina apre il primo accesso a schermo intero SOPRA un
    mandato buono e il PM crede di aver perso tutto. Stessa cosa per un file corrotto:
    e' un guasto, non un modulo da compilare;
  - la lettura del mandato chiede il TOKEN, per deroga dichiarata alla regola di casa
    «i GET restano liberi»: qui esce il profilo di rischio privato del PM;
  - il mandato si legge DAL DISCO a ogni chiamata, mai da una copia in memoria.

Tutti i valori qui sotto sono INVENTATI.
"""
import ast
import copy
import json
import os

import pytest

import bellomberg.core.mandato_pm as mp

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _valido():
    m = copy.deepcopy(mp.profilo_esempio())
    m["versione"] = mp.VERSIONE_SCHEMA
    m["dichiarato_il"] = "2026-01-02"
    m["cassa"]["cassa_minima_pct"] = 3          # cosi' non e' piu' il profilo di esempio
    return m


def _scrivi(path, m):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)


def _punta(monkeypatch, path):
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(path))


# --------------------------------------------------------- lo stato, come lo vede F11

def test_lo_stato_per_la_pagina_esiste():
    assert hasattr(mp, "stato_per_api"), "manca il contratto che la pagina F11 consuma"


def test_mandato_valido_rende_valori_impronta_e_schema(tmp_path, monkeypatch):
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)
    corpo, transitorio = mp.stato_per_api()
    assert transitorio is None
    assert corpo["dichiarato"] is True
    assert corpo["causa"] is None
    assert corpo["valori"]["cassa"]["cassa_minima_pct"] == 3
    assert corpo["origine"] == "personalizzato"
    assert corpo["impronta"] == mp.impronta(mp.carica(str(p)))
    assert corpo["dichiarato_il"] == "2026-01-02"
    assert corpo["campi"] == mp.descrizione_campi(), "la pagina disegna il modulo da qui"


def test_mandato_assente_e_uno_stato_da_disegnare_non_un_errore(tmp_path, monkeypatch):
    _punta(monkeypatch, tmp_path / "non_c_e.json")
    corpo, transitorio = mp.stato_per_api()
    assert transitorio is None, "«mai compilato» non e' un guasto: la pagina apre il modulo"
    assert corpo["dichiarato"] is False
    assert corpo["causa"] == "assente"
    assert corpo["valori"] is None
    assert corpo["campi"] == mp.descrizione_campi(), "senza schema la pagina non sa cosa chiedere"


def test_mandato_incompleto_dice_QUALI_campi_mancano(tmp_path, monkeypatch):
    p = tmp_path / "mandato_pm.json"
    m = _valido()
    m["rischio"]["drawdown_max_pct"] = None
    _scrivi(p, m)
    _punta(monkeypatch, p)
    corpo, transitorio = mp.stato_per_api()
    assert transitorio is None
    assert corpo["dichiarato"] is False
    assert corpo["causa"] == "incompleto"
    assert "drawdown_max_pct" in corpo["campi_mancanti"], corpo["campi_mancanti"]


def test_mandato_incompleto_conserva_valori_errori_meta_ed_esempio(tmp_path, monkeypatch):
    p = tmp_path / "mandato_pm.json"
    m = _valido()
    m["_nota"] = "provenienza sintetica"
    m["rischio"]["drawdown_max_pct"] = None
    m["profilo"]["orizzonte_anni"] = "tre"
    _scrivi(p, m)
    _punta(monkeypatch, p)

    corpo, transitorio = mp.stato_per_api()

    assert transitorio is None
    assert corpo["valori"]["rischio"]["drawdown_max_pct"] is None
    assert corpo["valori"]["profilo"]["orizzonte_anni"] == "tre"
    assert corpo["valori"]["_nota"] == "provenienza sintetica"
    assert any("orizzonte_anni" in e for e in corpo["errori"])
    assert corpo["campi_mancanti"] == ["drawdown_max_pct"]
    assert corpo["esempio"] == mp.profilo_esempio()


def test_chi_copia_il_profilo_di_esempio_E_dichiarato_ma_lo_si_vede(tmp_path, monkeypatch):
    """Copiare l'esempio E' un modo legittimo di dichiarare (la pagina ha il comando
    apposta) e il comitato parte: non lo cambio io. Ma `origine` lo dice, e la pagina
    deve poter mostrare il banner «profilo di esempio, non personalizzato».
    NOTA: la CAUSA `esempio` e' un'altra cosa — vuol dire che l'esempio DEL REPO e'
    rotto, cioe' un guasto dell'installazione (v. il test sui casi non leggibili)."""
    p = tmp_path / "mandato_pm.json"
    m = copy.deepcopy(mp.profilo_esempio())
    m["versione"], m["dichiarato_il"] = mp.VERSIONE_SCHEMA, "2026-01-02"
    _scrivi(p, m)
    _punta(monkeypatch, p)
    corpo, transitorio = mp.stato_per_api()
    assert transitorio is None
    assert corpo["dichiarato"] is True
    assert corpo["origine"] == "esempio", "senza questo la pagina non puo' mostrare il banner"


@pytest.mark.parametrize("causa", ["in_uso", "illeggibile"])
def test_il_file_non_leggibile_NON_si_appiattisce_su_non_dichiarato(causa, tmp_path, monkeypatch):
    """IL PUNTO CHE PROTEGGE IL PM: «in uso» vuol dire che il file c'e' ed e' pieno.
    Trattarlo come «non dichiarato» fa aprire il primo accesso sopra un mandato buono."""
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    def _esplode(_path=None):
        raise mp.MandatoMancante(str(p), causa, "guasto inventato di prova")
    monkeypatch.setattr(mp, "carica", _esplode)

    corpo, transitorio = mp.stato_per_api()
    assert transitorio == causa, (
        "un file non leggibile ADESSO deve essere dichiarato come tale, non come "
        "«mai compilato»: chi serve l'API ne fa un 503, non un modulo vuoto")
    assert corpo["dichiarato"] is False
    assert corpo["causa"] == causa


def test_si_rilegge_dal_disco_a_ogni_chiamata(tmp_path, monkeypatch):
    """«A poi B»: se cambio il file fra due chiamate, la seconda porta B."""
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)
    a, _ = mp.stato_per_api()
    m = _valido()
    m["cassa"]["cassa_minima_pct"] = 4
    _scrivi(p, m)
    b, _ = mp.stato_per_api()
    assert a["valori"]["cassa"]["cassa_minima_pct"] == 3
    assert b["valori"]["cassa"]["cassa_minima_pct"] == 4
    assert a["impronta"] != b["impronta"]


# --------------------------------------------------------- l'endpoint e il suo cablaggio

def test_l_endpoint_esiste_e_rende_lo_stato(tmp_path, monkeypatch):
    """Non basta che l'helper funzioni: si prova la funzione CHE E' l'endpoint."""
    from bellomberg.api import bellomberg_api
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)
    out = bellomberg_api.get_mandato()
    assert out["dichiarato"] is True
    assert out["campi"] == mp.descrizione_campi()


@pytest.mark.parametrize("causa", ["in_uso", "illeggibile"])
def test_l_endpoint_rende_503_sul_file_non_leggibile(causa, tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api
    from fastapi import HTTPException
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    def _esplode(_path=None):
        raise mp.MandatoMancante(str(p), causa, "guasto inventato di prova")
    monkeypatch.setattr(mp, "carica", _esplode)

    with pytest.raises(HTTPException) as e:
        bellomberg_api.get_mandato()
    assert e.value.status_code == 503, e.value.status_code
    assert isinstance(e.value.detail, str), "il frontend rende `detail` verbatim: mai un dict"
    assert causa in e.value.detail


def test_l_endpoint_non_e_un_500_se_il_mandato_esplode_in_modo_inatteso(tmp_path, monkeypatch):
    """L'API non ha nessun exception_handler: un guasto che sfugge diventa un 500 muto
    che scavalca anche la riga di log. Ogni ramo cattura per conto suo."""
    from bellomberg.api import bellomberg_api
    from fastapi import HTTPException
    def _esplode(_path=None):
        raise RuntimeError("guasto inventato che nessuno si aspetta")
    monkeypatch.setattr(mp, "stato_per_api", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(HTTPException) as e:
        bellomberg_api.get_mandato()
    assert e.value.status_code in (500, 503), e.value.status_code
    assert isinstance(e.value.detail, str)
    assert "RuntimeError" in e.value.detail, "la causa vera deve restare nel messaggio"


# --------------------------------------------------------- il token: deroga dichiarata

def _decoratori_della_rotta(path):
    """I decoratori @app.<verbo>(path, ...) letti dal SORGENTE: chiamare la funzione non
    esercita le `dependencies`, quindi il token si prova qui."""
    src = open(os.path.join(REPO, "src", "bellomberg", "api", "bellomberg_api.py"), encoding="utf-8").read()
    fuori = []
    for nodo in ast.walk(ast.parse(src)):
        if not isinstance(nodo, ast.FunctionDef):
            continue
        for d in nodo.decorator_list:
            if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)):
                continue
            if not d.args or not isinstance(d.args[0], ast.Constant):
                continue
            if d.args[0].value == path:
                fuori.append((d.func.attr, nodo.name, ast.dump(d)))
    return fuori


def test_la_rotta_e_dichiarata_come_get_sul_percorso_giusto():
    rotte = _decoratori_della_rotta("/mandato")
    verbi = {v for v, _n, _d in rotte}
    assert "get" in verbi, ("nessun @app.get(\"/mandato\") nel sorgente", rotte)


def test_la_lettura_del_mandato_chiede_il_token():
    """Deroga DICHIARATA alla regola «i GET restano liberi»: qui esce il profilo di
    rischio privato del PM. Se qualcuno la toglie per coerenza, questo test cade."""
    rotte = [d for v, _n, d in _decoratori_della_rotta("/mandato") if v == "get"]
    assert rotte, "rotta assente"
    assert any("require_session" in d for d in rotte), (
        "GET /mandato senza require_session: il profilo di rischio del PM sarebbe "
        "leggibile da chiunque arrivi sulla porta 8765")
