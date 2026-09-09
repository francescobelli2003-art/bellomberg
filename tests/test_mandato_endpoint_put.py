# -*- coding: utf-8 -*-
"""`PUT /mandato` — il primo codice che scrive sul mandato del PM. Criterio (5), lotto B.

Cosa deve essere vero per chi usa il programma:
  - salvare il modulo della pagina Mandato riscrive il file del PM **e la risposta e' cio'
    che il DISCO dice dopo**, non l'eco di quello che il browser ha mandato: se la scrittura
    e' andata storta a meta', il PM lo vede subito invece di credere di aver salvato;
  - un modulo non valido **non tocca il file** e torna col motivo di OGNI campo sbagliato,
    una riga per campo, cosi' la pagina le puo' mettere accanto alle caselle giuste;
  - **`detail` e' sempre una STRINGA**: il frontend la rende verbatim in 41 punti misurati
    (`bellomberg_api.py:74` ne dichiara 14, numero ereditato e mai rimisurato) e un dict
    stamperebbe «[object Object]» al posto del motivo;
  - **`origine` non si accetta dal client**: la MISURA il backend confrontando i valori col
    profilo di esempio. Accettarla vorrebbe dire lasciar spegnere dal browser il banner
    «profilo di esempio, non personalizzato» su un mandato che di esempio lo e' davvero;
  - **il verbo PUT deve stare nel CORS**, o la pagina non arriva nemmeno all'handler: il
    browser rifiuta in preflight e il PM vede un errore di rete senza motivo;
  - un file occupato da un'altra scrittura e' un **503 «riprova»**, non un 500 muto.

Tutti i valori qui sotto sono INVENTATI.
"""
import ast
import copy
import hashlib
import json
import os

import pytest

import bellomberg.core.mandato_pm as mp

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _valido():
    """Un mandato che passa i controlli di campo E le tredici coerenze."""
    m = copy.deepcopy(mp.profilo_esempio())
    m["versione"] = mp.VERSIONE_SCHEMA
    m["dichiarato_il"] = "2026-01-02"
    m["cassa"]["cassa_minima_pct"] = 3          # cosi' non e' piu' il profilo di esempio
    return m


def _blocchi(m):
    """Solo i sette blocchi: e' quello che la pagina rimanda, senza meta ne' `origine`."""
    return {b: copy.deepcopy(m[b]) for b in mp.BLOCCHI}


def _scrivi(path, m):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)


def _punta(monkeypatch, path):
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(path))


def _impronta_del_file(path):
    """L'impronta dei BYTE sul disco: serve a provare che un rifiuto non ha scritto."""
    if not os.path.exists(path):
        return "ASSENTE"
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# --------------------------------------------------------- il giro buono

def test_un_mandato_valido_viene_scritto_sul_disco(tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    nuovo = _blocchi(_valido())
    nuovo["cassa"]["cassa_minima_pct"] = 7
    bellomberg_api.put_mandato(nuovo)

    dal_disco = mp.carica(str(p))
    assert dal_disco["cassa"]["cassa_minima_pct"] == 7, "il PUT non ha scritto sul file"


def test_la_risposta_e_la_RILETTURA_del_disco_non_l_eco_del_corpo(tmp_path, monkeypatch):
    """Se la risposta fosse l'eco del payload, una scrittura fallita a meta' sembrerebbe
    riuscita. Qui la sostituisco sotto: la risposta deve portare cio' che c'e' sul DISCO."""
    from bellomberg.api import bellomberg_api
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    altro = _valido()
    altro["cassa"]["cassa_minima_pct"] = 9

    def _salva_ma_scrive_altro(_mandato, path=None):
        _scrivi(p, altro)
        return mp.carica(str(p))
    monkeypatch.setattr(mp, "salva", _salva_ma_scrive_altro)

    nuovo = _blocchi(_valido())
    nuovo["cassa"]["cassa_minima_pct"] = 7
    out = bellomberg_api.put_mandato(nuovo)
    assert out["valori"]["cassa"]["cassa_minima_pct"] == 9, (
        "la risposta ripete il corpo ricevuto invece di rileggere il disco")


def test_la_risposta_del_put_ha_la_STESSA_FORMA_del_get(tmp_path, monkeypatch):
    """Una forma sola per i due verbi: la pagina disegna lo stesso stato dopo aver letto
    e dopo aver salvato, senza due strade da tenere allineate a mano."""
    from bellomberg.api import bellomberg_api
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    dopo_get = bellomberg_api.get_mandato()
    dopo_put = bellomberg_api.put_mandato(_blocchi(_valido()))
    assert set(dopo_put) == set(dopo_get), (sorted(dopo_put), sorted(dopo_get))
    assert dopo_put["dichiarato"] is True
    assert dopo_put["campi"] == mp.descrizione_campi(), "la pagina ridisegna il modulo da qui"
    assert dopo_put["impronta"] == mp.impronta(mp.carica(str(p)))


def test_le_chiavi_meta_del_file_precedente_SOPRAVVIVONO_al_salvataggio(tmp_path, monkeypatch):
    """La pagina rimanda i sette blocchi, non le meta. Il `_nota` di provenienza dei valori
    del PM sta nel file vero: un salvataggio non e' il posto dove si perde una nota."""
    from bellomberg.api import bellomberg_api
    p = tmp_path / "mandato_pm.json"
    prima = _valido()
    prima["_nota"] = "da dove vengono questi numeri (nota inventata di prova)"
    _scrivi(p, prima)
    _punta(monkeypatch, p)

    bellomberg_api.put_mandato(_blocchi(_valido()))

    with open(p, encoding="utf-8") as fh:
        sul_disco = json.load(fh)
    assert sul_disco.get("_nota") == prima["_nota"], (
        "il salvataggio ha cancellato la provenienza scritta dal PM")


# --------------------------------------------------------- i rifiuti NON scrivono

def test_origine_mandata_dal_client_viene_RIFIUTATA(tmp_path, monkeypatch):
    """`origine` la misura il backend confrontando i valori col profilo di esempio.
    Accettarla dal browser vuol dire poter spegnere il banner «profilo di esempio»
    su un mandato che di esempio lo e' davvero."""
    from bellomberg.api import bellomberg_api
    from fastapi import HTTPException
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)
    prima = _impronta_del_file(str(p))

    corpo = _blocchi(_valido())
    corpo["origine"] = "personalizzato"
    with pytest.raises(HTTPException) as e:
        bellomberg_api.put_mandato(corpo)
    assert e.value.status_code == 422, e.value.status_code
    assert "origine" in e.value.detail
    assert _impronta_del_file(str(p)) == prima, "un rifiuto ha scritto sul file"


@pytest.mark.parametrize("guasto,atteso", [
    ("fuori_intervallo", "var99_1g_pct"),
    ("campo_mancante", "drawdown_max_pct"),
    ("tipo_sbagliato", "orizzonte_anni"),
    ("blocco_ignoto", "campi sconosciuti"),
])
def test_un_modulo_non_valido_NON_TOCCA_IL_FILE(guasto, atteso, tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api
    from fastapi import HTTPException
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)
    prima = _impronta_del_file(str(p))

    corpo = _blocchi(_valido())
    if guasto == "fuori_intervallo":
        corpo["rischio"]["var99_1g_pct"] = 99
    elif guasto == "campo_mancante":
        corpo["rischio"]["drawdown_max_pct"] = None
    elif guasto == "tipo_sbagliato":
        corpo["profilo"]["orizzonte_anni"] = "tre"
    elif guasto == "blocco_ignoto":
        corpo["rischio"]["campo_che_non_esiste"] = 1

    with pytest.raises(HTTPException) as e:
        bellomberg_api.put_mandato(corpo)
    assert e.value.status_code == 422, e.value.status_code
    assert atteso in e.value.detail, e.value.detail
    assert _impronta_del_file(str(p)) == prima, (
        "il file e' cambiato nonostante il modulo fosse rifiutato")


def test_il_422_porta_UNA_RIGA_PER_CAMPO_separate_da_a_capo(tmp_path, monkeypatch):
    """La pagina divide su `\\n` e mette ogni motivo accanto alla sua casella."""
    from bellomberg.api import bellomberg_api
    from fastapi import HTTPException
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    corpo = _blocchi(_valido())
    corpo["rischio"]["var99_1g_pct"] = 99
    corpo["profilo"]["orizzonte_anni"] = "tre"
    with pytest.raises(HTTPException) as e:
        bellomberg_api.put_mandato(corpo)

    righe = [r for r in e.value.detail.split("\n") if r.strip()]
    assert len(righe) >= 2, ("attese almeno due righe, una per campo", e.value.detail)
    assert all(":" in r for r in righe), ("ogni riga e' «campo: motivo»", righe)
    teste = [r.split(":")[0].strip() for r in righe]
    assert "var99_1g_pct" in teste and "orizzonte_anni" in teste, teste


@pytest.mark.parametrize("corpo", [
    None,
    "una stringa",
    [1, 2, 3],
    {},
    {"rischio": "non un oggetto"},
    {"chiave_di_primo_livello_inventata": 1},
])
def test_detail_e_SEMPRE_UNA_STRINGA_mai_un_dict(corpo, tmp_path, monkeypatch):
    """`bellomberg_api.py:74-75`: il frontend rende `detail` verbatim; un dict o una lista
    diventano «[object Object]» e il PM legge quello al posto del motivo."""
    from bellomberg.api import bellomberg_api
    from fastapi import HTTPException
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    with pytest.raises(HTTPException) as e:
        bellomberg_api.put_mandato(corpo)
    assert e.value.status_code == 422, e.value.status_code
    assert isinstance(e.value.detail, str), type(e.value.detail)
    assert e.value.detail.strip(), "un detail vuoto e' un buco non dichiarato (regola 14/07)"


# --------------------------------------------------------- i guasti si dichiarano

def test_il_file_in_uso_e_un_503_RIPROVA_non_un_500(tmp_path, monkeypatch):
    from bellomberg.api import bellomberg_api
    from fastapi import HTTPException
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    def _occupato(_m, path=None):
        raise mp.MandatoMancante(str(p), "in_uso", "PermissionError inventata di prova")
    monkeypatch.setattr(mp, "salva", _occupato)

    with pytest.raises(HTTPException) as e:
        bellomberg_api.put_mandato(_blocchi(_valido()))
    assert e.value.status_code == 503, e.value.status_code
    assert isinstance(e.value.detail, str)
    assert "in_uso" in e.value.detail


def test_il_permission_error_NUDO_del_salvataggio_e_un_503_non_un_500(tmp_path, monkeypatch):
    """`salva()` rilegge il file precedente per il backup SENZA la ritenta a 3 tentativi
    che protegge `carica()`: su Windows quel punto puo' sollevare un PermissionError nudo
    mentre un'altra scrittura e' in corso. E' un «riprova», non un guasto del programma."""
    from bellomberg.api import bellomberg_api
    from fastapi import HTTPException
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    def _occupato(_m, path=None):
        raise PermissionError(13, "file in uso da un altro processo")
    monkeypatch.setattr(mp, "salva", _occupato)

    with pytest.raises(HTTPException) as e:
        bellomberg_api.put_mandato(_blocchi(_valido()))
    assert e.value.status_code == 503, e.value.status_code
    assert isinstance(e.value.detail, str)


def test_l_esempio_del_repo_rotto_NON_nomina_il_file_del_pm(tmp_path, monkeypatch):
    """Trappola misurata: `MandatoMancante` con causa «esempio» porta in `.percorso` il
    file DELL'ESEMPIO del repo, non il mandato del PM. Un messaggio che concatena
    «il tuo mandato a <percorso>» direbbe una cosa falsa."""
    from bellomberg.api import bellomberg_api
    from fastapi import HTTPException
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    def _esempio_rotto(_m, path=None):
        raise mp.MandatoMancante(mp.ESEMPIO_MANDATO, "esempio",
                                 "il profilo di esempio del repo non si legge")
    monkeypatch.setattr(mp, "salva", _esempio_rotto)

    with pytest.raises(HTTPException) as e:
        bellomberg_api.put_mandato(_blocchi(_valido()))
    assert e.value.status_code == 503, e.value.status_code
    assert "esempio" in e.value.detail
    assert str(p) not in e.value.detail, (
        "il messaggio incolpa il file del PM per un guasto dell'installazione")


def test_un_guasto_inatteso_non_e_un_500_MUTO(tmp_path, monkeypatch):
    """L'API non ha exception_handler: ogni ramo cattura per conto suo, o si perde
    anche la riga di log."""
    from bellomberg.api import bellomberg_api
    from fastapi import HTTPException
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    def _esplode(_m, path=None):
        raise RuntimeError("guasto inventato che nessuno si aspetta")
    monkeypatch.setattr(mp, "salva", _esplode)

    with pytest.raises(HTTPException) as e:
        bellomberg_api.put_mandato(_blocchi(_valido()))
    assert e.value.status_code in (500, 503), e.value.status_code
    assert isinstance(e.value.detail, str)
    assert "RuntimeError" in e.value.detail, "la causa vera deve restare nel messaggio"


# --------------------------------------------------------- il cablaggio (dal SORGENTE)

def _sorgente_api():
    return open(os.path.join(REPO, "src", "bellomberg", "api", "bellomberg_api.py"), encoding="utf-8").read()


def _decoratori_della_rotta(path):
    """I decoratori @app.<verbo>(path, ...) letti dal SORGENTE: chiamare la funzione non
    esercita le `dependencies`, quindi il token si prova qui."""
    fuori = []
    for nodo in ast.walk(ast.parse(_sorgente_api())):
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


def test_la_rotta_e_dichiarata_come_put_sul_percorso_giusto():
    rotte = _decoratori_della_rotta("/mandato")
    verbi = {v for v, _n, _d in rotte}
    assert "put" in verbi, ("nessun @app.put(\"/mandato\") nel sorgente", rotte)


def test_la_scrittura_del_mandato_chiede_il_token():
    """Il GET del mandato chiede il token per deroga dichiarata del PM. La SCRITTURA a
    maggior ragione: senza, chiunque arrivi sulla porta 8765 riscrive il profilo di
    rischio su cui gira il comitato."""
    rotte = [d for v, _n, d in _decoratori_della_rotta("/mandato") if v == "put"]
    assert rotte, "rotta assente"
    assert any("require_session" in d for d in rotte), (
        "PUT /mandato senza require_session: il mandato del PM sarebbe riscrivibile "
        "da chiunque arrivi sulla porta 8765")


def _allow_methods_dal_sorgente():
    """La lista `allow_methods` del CORSMiddleware, letta dal sorgente con l'AST."""
    for nodo in ast.walk(ast.parse(_sorgente_api())):
        if not isinstance(nodo, ast.Call):
            continue
        for kw in nodo.keywords:
            if kw.arg == "allow_methods" and isinstance(kw.value, ast.List):
                return [el.value for el in kw.value.elts if isinstance(el, ast.Constant)]
    return None


def test_il_cors_ammette_il_verbo_put():
    """LA RIGA CHE FA FUNZIONARE IL BROWSER, e che finora non aveva nessuna rete
    (`allow_methods` aveva 0 riscontri in tests/ e in prove_frontend/).

    Senza PUT in `allow_methods` il browser rifiuta in PREFLIGHT: l'handler non viene
    mai chiamato, la suite resta verde, i banchi restano verdi, e la pagina Mandato
    fallisce lo stesso con un errore di rete senza motivo. E' la lezione «testare
    l'helper non e' testare il cablaggio» applicata a un endpoint che scrive sul file
    vero del PM."""
    metodi = _allow_methods_dal_sorgente()
    assert metodi is not None, "non trovo `allow_methods` nel CORSMiddleware"
    assert "PUT" in metodi, (
        "il CORS non ammette PUT: la pagina Mandato non arriva nemmeno all'handler", metodi)


def test_il_cors_copre_TUTTI_i_verbi_che_l_api_espone_davvero():
    """Difetto misurato il 06/09, piu' vecchio del mandato: `@app.put(/positions/{ticker}/tesi)`
    e `@app.patch(/chat/sessions/{id})` esistono in HEAD e NON sono nel CORS. Non e' mai
    esploso solo perche' nessuna pagina li chiama ancora — cioe' il difetto e' latente,
    non assente. Questo test lo tiene chiuso per tutti i verbi, non solo per il mio."""
    esposti = set()
    for nodo in ast.walk(ast.parse(_sorgente_api())):
        if not isinstance(nodo, ast.FunctionDef):
            continue
        for d in nodo.decorator_list:
            if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                    and d.func.attr in ("get", "post", "put", "patch", "delete")):
                esposti.add(d.func.attr.upper())
    ammessi = set(_allow_methods_dal_sorgente() or [])
    mancanti = sorted(esposti - ammessi)
    assert not mancanti, (
        "verbi esposti dall'API e rifiutati dal CORS in preflight: %s" % ", ".join(mancanti))
def test_salvato_ma_NON_RILEGGIBILE_lo_dice_invece_di_rendere_un_corpo_vuoto(tmp_path, monkeypatch):
    """Il caso peggiore del salvataggio: la scrittura riesce e la rilettura no (il file
    finisce sotto un'altra scrittura nello stesso istante). Rendere il corpo com'e' —
    con `dichiarato: false` — farebbe disegnare alla pagina «mandato mai compilato»
    SUBITO DOPO che il PM ha salvato: crederebbe di aver perso tutto quello che ha
    appena scritto. Si dichiara, e si dice che il salvataggio E' andato a buon fine."""
    from bellomberg.api import bellomberg_api
    from fastapi import HTTPException
    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)

    vero_salva = mp.salva
    monkeypatch.setattr(mp, "salva", lambda m, path=None: vero_salva(m, str(p)))
    monkeypatch.setattr(mp, "stato_per_api", lambda *a, **k: (
        {"dichiarato": False, "causa": "in_uso", "dettaglio": "occupato di prova",
         "campi_mancanti": [], "valori": None, "origine": None, "impronta": None,
         "dichiarato_il": None, "campi": {}}, "in_uso"))

    with pytest.raises(HTTPException) as e:
        bellomberg_api.put_mandato(_blocchi(_valido()))
    assert e.value.status_code == 503, e.value.status_code
    assert isinstance(e.value.detail, str)
    assert "SALVATO" in e.value.detail, (
        "il messaggio non dice che il salvataggio e' riuscito: il PM crede di aver perso "
        "il modulo appena scritto", e.value.detail)


# --------------------------------------------------------- su HTTP VERO (TestClient)
#
# Le prove qui sotto sono le PRIME del repo che passano dal protocollo invece che
# dalla funzione: `grep -rn TestClient tests/` dava ZERO prima di oggi, e la riga
# `allow_methods` del CORS non aveva nessuna rete ne' in tests/ ne' in prove_frontend/.
# La differenza non e' di stile. Chiamare `put_mandato(...)` non esercita ne' le
# `dependencies` ne' il middleware: il preflight del browser puo' rifiutare la
# richiesta e l'handler non viene mai chiamato — suite verde, banchi verdi, pagina
# che non funziona. E' la lezione «testare l'helper non e' testare il cablaggio».


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Il client HTTP col token scavalcato e il mandato in una cartella temporanea."""
    from fastapi.testclient import TestClient
    from bellomberg.api import bellomberg_api

    p = tmp_path / "mandato_pm.json"
    _scrivi(p, _valido())
    _punta(monkeypatch, p)
    # `require_session` si scavalca: il token e' gia' provato dall'AST sui decoratori
    # (test_la_scrittura_del_mandato_chiede_il_token). Qui si prova cosa fa l'handler.
    bellomberg_api.app.dependency_overrides[bellomberg_api.require_session] = lambda: None
    try:
        yield TestClient(bellomberg_api.app, base_url="http://127.0.0.1"), p
    finally:
        bellomberg_api.app.dependency_overrides.clear()


def test_il_preflight_del_browser_ammette_davvero_il_put(client):
    """LA PROVA CHE FA FUNZIONARE LA PAGINA. Senza PUT in `allow_methods` il browser
    non manda nemmeno la richiesta: il PM vede un errore di rete senza motivo, e
    nessun test dell'handler se ne accorge."""
    c, _p = client
    r = c.options("/mandato", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "content-type,x-bb-token",
    })
    assert r.status_code == 200, r.status_code
    ammessi = (r.headers.get("access-control-allow-methods") or "").upper()
    assert "PUT" in ammessi, ("il preflight non ammette PUT", ammessi)
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_su_http_vero_il_giro_buono_scrive_e_rende_lo_stato(client):
    c, p = client
    corpo = _blocchi(_valido())
    corpo["cassa"]["cassa_minima_pct"] = 7
    r = c.put("/mandato", json=corpo)
    assert r.status_code == 200, (r.status_code, r.text[:300])
    assert r.json()["dichiarato"] is True
    assert r.json()["errori"] == []
    assert r.json()["esempio"] == mp.profilo_esempio()
    with open(p, encoding="utf-8") as fh:
        assert json.load(fh)["cassa"]["cassa_minima_pct"] == 7


def test_su_http_vero_il_422_ha_detail_STRINGA_e_una_riga_per_campo(client):
    """Il punto per cui `detail` deve restare una stringa: qui si vede cosa arriva
    DAVVERO al frontend, che la rende verbatim."""
    c, _p = client
    corpo = _blocchi(_valido())
    corpo["rischio"]["var99_1g_pct"] = 99
    corpo["profilo"]["orizzonte_anni"] = "tre"
    r = c.put("/mandato", json=corpo)
    assert r.status_code == 422, (r.status_code, r.text[:300])
    detail = r.json()["detail"]
    assert isinstance(detail, str), (type(detail).__name__, detail)
    righe = [x for x in detail.split("\n") if x.strip()]
    assert len(righe) == 2, righe
    assert {x.split(":")[0].strip() for x in righe} == {"var99_1g_pct", "orizzonte_anni"}


def test_su_http_vero_un_corpo_che_non_e_un_oggetto_da_comunque_una_stringa(client):
    """Un JSON valido ma non un oggetto (una stringa, una lista) arriva all'handler e
    riceve il messaggio di casa, non quello di FastAPI."""
    c, _p = client
    for corpo in ("non un oggetto", [1, 2, 3], 42):
        r = c.put("/mandato", json=corpo)
        assert r.status_code == 422, (corpo, r.status_code)
        assert isinstance(r.json()["detail"], str), (corpo, r.json()["detail"])


def test_su_http_vero_origine_dal_client_viene_rifiutata_senza_scrivere(client):
    c, p = client
    prima = _impronta_del_file(str(p))
    r = c.put("/mandato", json=dict(_blocchi(_valido()), origine="personalizzato"))
    assert r.status_code == 422, r.status_code
    assert "origine" in r.json()["detail"]
    assert _impronta_del_file(str(p)) == prima


def test_IL_LIMITE_DICHIARATO_NELLA_DOCSTRING_E_VERO_non_una_frase(client):
    """La docstring del PUT dichiara: «un corpo che non e' JSON valido non arriva qui —
    lo rifiuta FastAPI col suo 422, il cui `detail` e' una LISTA».

    Una garanzia dichiarata dev'essere una MISURA, non una frase: questo test la misura.
    Se un domani l'app prendesse un exception_handler che rende `detail` una stringa
    anche per il JSON malformato, questo test cade e la docstring va corretta — cioe'
    il limite non puo' restare scritto dopo essere smesso di essere vero."""
    c, _p = client
    for corpo, come in [("{ questo non e json", "JSON malformato"), (None, "nessun corpo")]:
        if corpo is None:
            r = c.put("/mandato")
        else:
            r = c.put("/mandato", content=corpo, headers={"Content-Type": "application/json"})
        assert r.status_code == 422, (come, r.status_code)
        assert isinstance(r.json()["detail"], list), (
            come, "il limite dichiarato nella docstring del PUT non e' piu' vero: "
                  "aggiornala invece di lasciarla mentire", r.json()["detail"])
