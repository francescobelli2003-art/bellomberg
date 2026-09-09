# -*- coding: utf-8 -*-
"""Voce 8 (05/09, criterio 3): il collaudo dello sconosciuto, `scripts/collaudo_sconosciuto.py`.
Batteria PUBBLICA e OFFLINE: prova le parti PURE dello strumento (classificazione delle risposte,
ambiente ripulito, `.env` dal template, porta, route sondabili, riepilogo, verdetto, rapporto) e
il programma che elenca le route contro un'app FastAPI FINTA in `tmp_path`. Nessuna rete, nessun
backend vero, nessun dato del PM: simboli e cifre inventati.
"""
import json
import os
import re
import socket
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tools.ops import collaudo_sconosciuto as cs


# ============================================================================
# classifica(status, body): il cuore del criterio 3 — DICHIARATO / VUOTO / DATO / CRASH / N.A.
# ============================================================================
@pytest.mark.parametrize("status,body,atteso", [
    (500, {"detail": "boom"}, "CRASH"),
    (502, "bad gateway", "CRASH"),
    (503, {"detail": "PIN non configurato in modo sicuro"}, "DICHIARATO"),
    (404, {"detail": "posizione non trovata"}, "DICHIARATO"),
    (401, {"detail": "Invalid PIN"}, "DICHIARATO"),
    (200, {"error": "FRED_API_KEY assente"}, "DICHIARATO"),
    (200, {"dati": {"fonte": {"status": "stale", "valore": 1}}}, "DICHIARATO"),
    (200, {"ok": False, "motivo": "x"}, "DICHIARATO"),
    (200, {"prezzo": "n.d."}, "DICHIARATO"),
    (200, {"misconfigured": True}, "DICHIARATO"),
    (200, {"righe": [{"v": 1}, {"v": 2, "warning": "fonte muta"}]}, "DICHIARATO"),
    (200, [], "VUOTO"),
    (200, {"trades": []}, "VUOTO"),
    (200, {"a": None, "b": {}, "c": ""}, "VUOTO"),
    (200, None, "VUOTO"),
    (200, {"status": "ok", "version": "1.0"}, "DATO"),
    (200, {"cash_disponibile_eur": 0}, "DATO"),
    (200, {"attivo": False}, "DATO"),
    (200, [{"x": 1}], "DATO"),
])
def test_classifica(status, body, atteso):
    assert cs.classifica(status, body)["bucket"] == atteso


def test_422_per_parametro_mancante_e_colpa_della_sonda_non_del_codice():
    body = {"detail": [{"type": "missing", "loc": ["query", "ticker"], "msg": "Field required"}]}
    assert cs.classifica(422, body)["bucket"] == "N.A."


def test_422_di_una_guardia_e_una_dichiarazione():
    assert cs.classifica(422, {"detail": "GUARDIA PREZZI: prezzo non positivo"})["bucket"] == "DICHIARATO"


def test_timeout_e_errore_di_rete_hanno_un_bucket_proprio():
    assert cs.classifica(None, None, errore="timeout dopo 30 s")["bucket"] == "TIMEOUT"


def test_la_dichiarazione_dice_dove_sta():
    esito = cs.classifica(200, {"dati": {"fonte": {"status": "stale", "valore": 1}}})
    assert "dati.fonte.status" in esito["motivo"] and "stale" in esito["motivo"]


def test_corpo_non_json_con_200_e_dato():
    assert cs.classifica(200, "<html>pagina</html>")["bucket"] == "DATO"


# ============================================================================
# ambiente_pulito: lo sconosciuto NON ha le mie chiavi
# ============================================================================
def test_ambiente_pulito_scarta_le_chiavi_e_tiene_il_sistema():
    base = {"PATH": "p", "SYSTEMROOT": "s", "TEMP": "t", "HOME": "h",
            "ANTHROPIC_API_KEY": "sk-finta", "BELLOMBERG_PIN": "9999", "FRED_API_KEY": "x",
            "EMAIL_PASSWORD": "segreta"}
    env, scartate = cs.ambiente_pulito(base, data_dir="D:/dati-finti")
    for k in ("ANTHROPIC_API_KEY", "BELLOMBERG_PIN", "FRED_API_KEY", "EMAIL_PASSWORD"):
        assert k not in env
    assert env["PATH"] == "p" and env["SYSTEMROOT"] == "s" and env["TEMP"] == "t"
    assert env["BELLOMBERG_DATA_DIR"] == "D:/dati-finti"
    assert sorted(scartate) == ["ANTHROPIC_API_KEY", "BELLOMBERG_PIN", "EMAIL_PASSWORD", "FRED_API_KEY"]


def test_ambiente_pulito_senza_data_dir_non_la_inventa():
    env, _ = cs.ambiente_pulito({"PATH": "p"}, data_dir=None)
    assert "BELLOMBERG_DATA_DIR" not in env


# ============================================================================
# testo_env: `copy .env.example .env` + il solo PIN, come fa chi clona
# ============================================================================
TEMPLATE = "# commento\nANTHROPIC_API_KEY=\nBELLOMBERG_PIN=\nPM_NAME=PM\nCONSIGLIERE_PARALLEL=4\n"


def test_testo_env_mette_solo_il_pin():
    testo = cs.testo_env(TEMPLATE, pin="4821")
    assert "BELLOMBERG_PIN=4821\n" in testo
    assert "ANTHROPIC_API_KEY=\n" in testo
    assert cs.righe_valorizzate(testo) == cs.righe_valorizzate(TEMPLATE) + 1


def test_testo_env_senza_pin_e_il_template_verbatim():
    assert cs.testo_env(TEMPLATE, pin=None) == TEMPLATE


def test_testo_env_pretende_la_riga_del_pin_nel_template():
    with pytest.raises(ValueError, match="BELLOMBERG_PIN"):
        cs.testo_env("ANTHROPIC_API_KEY=\n", pin="4821")


def test_pin_del_collaudo_ha_quattro_cifre_e_non_e_quello_debole():
    assert re.fullmatch(r"[0-9]{4}", cs.PIN_COLLAUDO) and cs.PIN_COLLAUDO != "1234"


# ============================================================================
# controlla_porta: mai la 8765 (l'app del PM), mai una porta occupata
# ============================================================================
def _porta_libera():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_porta_8765_rifiutata_sempre():
    with pytest.raises(ValueError, match="8765"):
        cs.controlla_porta(8765)


def test_porta_occupata_rifiutata_e_detta():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        with pytest.raises(ValueError, match="OCCUPATA"):
            cs.controlla_porta(s.getsockname()[1])
    finally:
        s.close()


def test_porta_libera_passa():
    p = _porta_libera()
    assert cs.controlla_porta(p) == p


# ============================================================================
# route_sondabili: i GET senza parametri obbligatori; i parametri di percorso ricevono un valore
# INVENTATO e lo dicono; una query obbligatoria si salta dichiarando perche'
# ============================================================================
ROUTES = [
    {"path": "/health", "methods": ["GET"], "path_params": [], "query_required": []},
    {"path": "/trades", "methods": ["GET"], "path_params": [], "query_required": []},
    {"path": "/position/{ticker}", "methods": ["GET"], "path_params": ["ticker"], "query_required": []},
    {"path": "/decision/{id}", "methods": ["GET"], "path_params": ["id"], "query_required": []},
    {"path": "/search", "methods": ["GET"], "path_params": [], "query_required": ["q"]},
    {"path": "/trade", "methods": ["POST"], "path_params": [], "query_required": []},
]


def test_route_sondabili_prende_i_get_e_scarta_i_post():
    urls = [r["url"] for r in cs.route_sondabili(ROUTES)]
    assert "/health" in urls and "/trades" in urls
    assert "/trade" not in urls


def test_parametro_di_percorso_riceve_un_valore_inventato_e_lo_dice():
    r = {x["path"]: x for x in cs.route_sondabili(ROUTES)}
    assert r["/position/{ticker}"]["url"] == "/position/%s" % cs.SIMBOLO_INVENTATO
    assert r["/position/{ticker}"]["inventato"] is True
    assert r["/decision/{id}"]["url"] == "/decision/%d" % cs.ID_INVENTATO
    assert r["/health"]["inventato"] is False


def test_query_obbligatoria_si_salta_dichiarando_il_parametro():
    r = {x["path"]: x for x in cs.route_sondabili(ROUTES)}
    assert r["/search"]["salta"] == "query obbligatoria: q"
    assert r["/health"]["salta"] is None


# ============================================================================
# riepilogo, verdetto, rapporto
# ============================================================================
def _esiti(*bucket):
    return [{"metodo": "GET", "path": "/r%d" % i, "url": "/r%d" % i, "status": 200,
             "bucket": b, "motivo": "", "secondi": 0.01} for i, b in enumerate(bucket)]


def test_riepilogo_conta_ogni_bucket_anche_a_zero():
    assert cs.riepilogo(_esiti("DATO", "VUOTO", "VUOTO", "CRASH")) == {
        "DATO": 1, "VUOTO": 2, "CRASH": 1, "DICHIARATO": 0, "N.A.": 0, "TIMEOUT": 0}


def _fasi(**kw):
    base = {"pip": {"rc": 0, "mancanti": []}, "senza_pin": {"ok": True}, "login": {"ok": True},
            "sonda": _esiti("DATO", "VUOTO"), "flusso": {"ok": True}, "suite": {"rc": 0}}
    base.update(kw)
    return base


def test_verdetto_zero_e_il_vuoto_resta_osservazione():
    codice, motivi = cs.verdetto(_fasi())
    assert codice == 0
    assert any("VUOTO: 1" in m and "osservazione" in m for m in motivi)


def test_verdetto_uno_con_un_crash():
    codice, motivi = cs.verdetto(_fasi(sonda=_esiti("DATO", "CRASH")))
    assert codice == 1 and any("CRASH: 1" in m for m in motivi)


def test_verdetto_uno_se_pip_fallisce_o_manca_un_pacchetto():
    assert cs.verdetto(_fasi(pip={"rc": 1, "mancanti": []}))[0] == 1
    assert cs.verdetto(_fasi(pip={"rc": 0, "mancanti": ["arch"]}))[0] == 1


def test_verdetto_uno_se_la_suite_e_rossa_o_il_flusso_cade():
    assert cs.verdetto(_fasi(suite={"rc": 1}))[0] == 1
    assert cs.verdetto(_fasi(flusso={"ok": False, "motivo": "posizione assente"}))[0] == 1
    assert cs.verdetto(_fasi(senza_pin={"ok": False, "motivo": "login 200 senza PIN"}))[0] == 1


def test_verdetto_due_dichiara_la_fase_non_eseguita():
    codice, motivi = cs.verdetto(_fasi(suite=None))
    assert codice == 2 and any("suite" in m and "NON ESEGUITA" in m for m in motivi)


def test_rapporto_ha_una_riga_per_route_e_i_contatori():
    fasi = _fasi(sonda=[dict(_esiti("VUOTO")[0], path="/trades", url="/trades"),
                        dict(_esiti("DICHIARATO")[0], path="/macro", url="/macro",
                             status=200, motivo="error=FRED_API_KEY assente")])
    testo = cs.rapporto_md(fasi, verdetto=(0, ["ok"]))
    assert "| GET | /trades |" in testo and "VUOTO" in testo
    assert "| GET | /macro |" in testo and "FRED_API_KEY assente" in testo
    assert "DICHIARATO: 1" in testo and "VUOTO: 1" in testo


# ============================================================================
# requirements: i nomi, e i mancanti dopo pip (una lista, non una frase)
# ============================================================================
def test_nomi_requirements_ignora_extra_versioni_e_commenti():
    testo = "anthropic>=0.40.0\nuvicorn[standard]>=0.32.0  # nota\n# commento\n\npypdf>=4.0.0\n"
    assert cs.nomi_requirements(testo) == ["anthropic", "uvicorn", "pypdf"]


def test_pacchetti_mancanti_confronta_senza_maiuscole_ne_trattini():
    assert cs.pacchetti_mancanti(["anthropic", "python-dotenv", "pypdf"],
                                 [{"name": "Anthropic"}, {"name": "python_dotenv"}]) == ["pypdf"]


# ============================================================================
# PROGRAMMA_ROUTE: eseguito nel venv dello sconosciuto, importa l'app e stampa le route in JSON —
# provato su un'app FastAPI FINTA, con lo stesso interprete di questa suite
# ============================================================================
APP_FINTA = '''
from fastapi import FastAPI
app = FastAPI(openapi_url=None)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/position/{ticker}")
def position(ticker: str, limit: int = 10):
    return {"ticker": ticker}

@app.get("/search")
def search(q: str):
    return {"q": q}

@app.post("/trade")
def trade():
    return {}
'''


def test_programma_route_elenca_le_route_con_i_parametri(tmp_path):
    pytest.importorskip("fastapi")
    (tmp_path / "app_finta.py").write_text(APP_FINTA, encoding="utf-8")
    env = dict(os.environ, COLLAUDO_MODULO="app_finta")
    r = subprocess.run([sys.executable, "-c", cs.PROGRAMMA_ROUTE], cwd=str(tmp_path), env=env,
                       capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr
    route = {x["path"]: x for x in json.loads(r.stdout)}
    assert route["/health"]["methods"] == ["GET"] and route["/health"]["path_params"] == []
    assert route["/position/{ticker}"]["path_params"] == ["ticker"]
    assert route["/position/{ticker}"]["query_required"] == []
    assert route["/search"]["query_required"] == ["q"]
    assert route["/trade"]["methods"] == ["POST"]


# ============================================================================
# forma(body): la FORMA del corpo nella riga del rapporto (chiavi e dimensioni, mai i valori),
# per giudicare a mano un 200 senza avviso: un `{"items": [], "count": 0}` e' DATO per il
# classificatore (0 e' un numero) ma per chi legge e' una fonte muta
# ============================================================================
def test_forma_di_un_dizionario_elenca_chiavi_tipi_e_dimensioni():
    assert cs.forma({"items": [], "count": 0, "sources": {"a": 1}}) == "items:list(0) count:int sources:dict(1)"


def test_forma_di_lista_stringa_e_null():
    assert cs.forma([1, 2, 3]) == "list(3)"
    assert cs.forma("abc") == "str(3)"
    assert cs.forma(None) == "null"


def test_forma_si_ferma_a_dodici_chiavi_e_lo_dice():
    d = {"k%02d" % i: i for i in range(20)}
    f = cs.forma(d)
    assert f.count(":") == 12 and f.endswith("... (+8)")


# ============================================================================
# inizializza_come_clone: lo sconosciuto CLONA, quindi ha .git; il tree esportato no. Cinque
# batterie enumerano i file con `git ls-files` (misurato il 05/09: 5 failed su 1435 per questo).
# ============================================================================
def _git_out(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True,
                          encoding="utf-8", errors="replace").stdout


def test_inizializza_come_clone_traccia_i_file_e_non_il_venv(tmp_path):
    (tmp_path / "a.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("data/\n", encoding="utf-8")
    (tmp_path / ".venv-collaudo").mkdir()
    (tmp_path / ".venv-collaudo" / "x.txt").write_text("v", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "d.db").write_text("d", encoding="utf-8")
    creato, n = cs.inizializza_come_clone(str(tmp_path), escludi=[".venv-collaudo/"])
    assert creato is True and n == 2
    tracciati = _git_out(tmp_path, "ls-files").split()
    assert sorted(tracciati) == [".gitignore", "a.py"]
    assert _git_out(tmp_path, "status", "--short").strip() == ""   # il commit c'e': niente in sospeso


def test_inizializza_come_clone_lascia_stare_un_clone_vero(tmp_path):
    (tmp_path / "a.py").write_text("X = 1\n", encoding="utf-8")
    cs.inizializza_come_clone(str(tmp_path), escludi=[])
    (tmp_path / "b.py").write_text("Y = 2\n", encoding="utf-8")       # un file NON tracciato dopo
    creato, n = cs.inizializza_come_clone(str(tmp_path), escludi=[])
    assert creato is False and n == 1                                  # niente add: il clone e' suo


# ============================================================================
# riuso del tree: il collaudo rimuove SOLO cio' che ha scritto lui, e lo sa dal marcatore
# ============================================================================
def test_pulisci_per_riuso_rifiuta_un_tree_senza_marcatore(tmp_path):
    (tmp_path / ".env").write_text("BELLOMBERG_PIN=1111\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="marcatore"):
        cs.pulisci_per_riuso(str(tmp_path))
    assert (tmp_path / ".env").exists()


def test_pulisci_per_riuso_rimuove_solo_le_scritture_del_collaudo(tmp_path):
    (tmp_path / ".env").write_text("BELLOMBERG_PIN=1111\n", encoding="utf-8")
    (tmp_path / "portfolio.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.db").write_text("x", encoding="utf-8")
    (tmp_path / "mio.txt").write_text("resta", encoding="utf-8")
    cs.scrivi_marcatore(str(tmp_path))
    rimossi = cs.pulisci_per_riuso(str(tmp_path))
    assert sorted(rimossi) == [".env", "data", "portfolio.json"]
    assert (tmp_path / "mio.txt").exists() and not (tmp_path / ".env").exists()
    assert not (tmp_path / "data").exists()


# ============================================================================
# Affinamenti dal giro 2 del 05/09 (forme lette a mano): una chiave che CONTIENE la parola
# (stale_positions, sources_failed, fonti_mute) e' una dichiarazione se ha un valore; un 200 in
# cui OGNI collezione e' vuota e nessuno avvisa e' VUOTO anche se porta contatori a zero e timestamp
# ============================================================================
@pytest.mark.parametrize("body,atteso", [
    ({"stale_positions": ["ZZTEST"], "positions": [{"t": 1}]}, "DICHIARATO"),
    ({"sources_failed": {"a": "KO"}, "betas": {}}, "DICHIARATO"),
    ({"fonti_mute": ["x"], "items": [1]}, "DICHIARATO"),
    ({"stale_positions": [], "positions": [{"t": 1}]}, "DATO"),
    ({"fonti_mute": None, "count": 0, "items": [], "timestamp": "2026-09-05"}, "VUOTO"),
    ({"count": 2, "items": [1, 2]}, "DATO"),
    ({"by_ticker": {}, "n_tickers": 0, "avviso": None}, "VUOTO"),
    ({"status": "ok", "version": "1.0"}, "DATO"),
])
def test_classifica_affinamenti_giro_2(body, atteso):
    assert cs.classifica(200, body)["bucket"] == atteso


def test_ogni_collezione_vuota_lo_dice_nel_motivo():
    assert "collezion" in cs.classifica(200, {"count": 0, "items": []})["motivo"]
