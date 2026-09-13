"""tests/test_heartbeat_atomico.py - il heartbeat di F4 non si legge mai a meta' (27/08, run V9)

Il fatto (visto dal vivo dalla chat frontend alle 18:42:51 del 26/08, N7 di F44):
`Blackboard._write_heartbeat` e `mark_run_complete` scrivevano `current_run.json`
con `open("w")` diretto, quindi `GET /agents/live` poteva leggere un file vuoto
o a meta' e rispondere `{"running": false, "message": "read error"}` - per un
giro F4 perdeva stato e orologio e cancellava `activeTaskId`.

La proprieta' che conta, e che qui si prova sul comportamento (non sul
meccanismo): SE la scrittura fallisce a meta', il heartbeat precedente resta
intatto e leggibile. Con `open("w")` diretto il file veniva troncato PRIMA di
scrivere: i due test "a meta'" cadono con JSONDecodeError sul codice vecchio;
gli altri sorvegliano il contratto nuovo (chiavi, temporaneo, meccanismo
tmp+replace, retry) e il LETTORE (`get_agents_live`), che nella finestra del
replace (stimata ~0,01-0,3 ms dalla review 27/08) prende PermissionError e ora
ritenta invece di dichiarare «read error».

Secondo fatto (N8 di F44): il heartbeat vivo manda `tool_log[-50:]` senza dire
il totale, e la pagina presentava il tappo come totale ("50 su 50" a 2' come a
49'). Ora il vivo porta `n_tool_calls` (la chiave che il payload finale ha gia'
e che AgentsLive.tsx legge gia': `state.n_tool_calls ?? P.calls.length`) e
`tool_log_tappato`.

`HEARTBEAT_PATH` e' rediretto per OGNI test dal presidio (a-quater) del
conftest: qui si verifica solo che il presidio ci sia (lezione 25/08: una
Blackboard costruita e' un heartbeat scritto).
"""
import builtins
import io
import json
import os

import pytest
from bellomberg.core.language import language_context

from bellomberg.agents.specialists.base import Blackboard


def _leggi(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class _ScritturaAMeta:
    """File che scrive META del testo e poi esplode: disco pieno, lock, kill."""

    def __init__(self, f):
        self._f = f

    def write(self, s):
        self._f.write(s[: len(s) // 2])
        self._f.flush()
        raise OSError("guasto simulato a meta' scrittura")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._f.close()
        return False


@pytest.fixture
def bb(tmp_path):
    b = Blackboard(memory_db=None, memo_id=51)
    assert os.path.normpath(Blackboard.HEARTBEAT_PATH).startswith(
        os.path.normpath(str(tmp_path))), "presidio conftest (a-quater) assente"
    return b


def _rompi_le_scritture_sul_heartbeat(monkeypatch):
    """Ogni `open(..., "w")` su current_run.json (o sul suo temporaneo accanto)
    scrive meta' e poi esplode."""
    vero = builtins.open
    bersaglio = os.path.normpath(Blackboard.HEARTBEAT_PATH)

    def finto(path, mode="r", *a, **k):
        f = vero(path, mode, *a, **k)
        if "w" in str(mode) and os.path.normpath(str(path)).startswith(bersaglio):
            return _ScritturaAMeta(f)
        return f

    monkeypatch.setattr(builtins, "open", finto)


def _cartella(path):
    return os.path.dirname(os.path.normpath(path))


def test_scrittura_a_meta_lascia_il_heartbeat_precedente_intatto(bb, monkeypatch):
    bb.current_round = 1
    bb.specialist_status["quant"] = "running"
    bb._write_heartbeat()
    prima = _leggi(Blackboard.HEARTBEAT_PATH)
    assert prima["current_round"] == 1 and prima["running"] is True
    _rompi_le_scritture_sul_heartbeat(monkeypatch)
    bb.current_round = 2
    bb._write_heartbeat()  # deve fallire SENZA lasciare un file a meta'
    dopo = _leggi(Blackboard.HEARTBEAT_PATH)  # JSON valido: con open("w") era troncato
    assert dopo == prima


def test_mark_run_complete_a_meta_lascia_il_vivo_intatto(bb, monkeypatch):
    bb._write_heartbeat()
    prima = _leggi(Blackboard.HEARTBEAT_PATH)
    assert prima["running"] is True
    _rompi_le_scritture_sul_heartbeat(monkeypatch)
    bb.mark_run_complete()
    dopo = _leggi(Blackboard.HEARTBEAT_PATH)
    assert dopo == prima, "a run finita con scrittura guasta resta il VIVO, non un file a meta'"


def test_nessun_temporaneo_residuo_e_file_finale_valido(bb):
    bb.tool_log.append({"specialist": "quant", "round": 0, "tool": "x",
                        "input": "{}", "time": "10:00:00"})
    bb._write_heartbeat()
    bb.mark_run_complete()
    residui = [f for f in os.listdir(_cartella(Blackboard.HEARTBEAT_PATH))
               if f.startswith("current_run.json") and f != "current_run.json"]
    assert residui == []
    fin = _leggi(Blackboard.HEARTBEAT_PATH)
    assert fin["running"] is False
    assert fin["n_tool_calls"] == 1 and fin["tool_log_tappato"] is False


def test_dopo_un_guasto_il_temporaneo_non_resta(bb, monkeypatch):
    _rompi_le_scritture_sul_heartbeat(monkeypatch)
    bb._write_heartbeat()
    assert not any(f.endswith(".tmp")
                   for f in os.listdir(_cartella(Blackboard.HEARTBEAT_PATH)))


@pytest.mark.parametrize("n, tappato", [(10, False), (50, False), (51, True), (60, True)])
def test_il_heartbeat_vivo_dichiara_il_totale_delle_tool_call(bb, n, tappato):
    bb.tool_log = [{"specialist": "quant", "round": 1, "tool": "t%d" % i,
                    "input": "{}", "time": "10:00:00"} for i in range(n)]
    bb._write_heartbeat()
    s = _leggi(Blackboard.HEARTBEAT_PATH)
    assert s["n_tool_calls"] == n
    assert s["tool_log_tappato"] is tappato
    assert len(s["tool_log"]) == min(n, Blackboard.HEARTBEAT_TOOL_LOG_MAX)
    assert s["tool_log"][-1]["tool"] == "t%d" % (n - 1), "le ULTIME, non le prime"


def test_il_tetto_del_tool_log_vivo_e_il_contratto_dichiarato_al_frontend_nel_ponte():
    # "ultime 50 di N" (ponte 27/08): il numero vive in UN posto solo.
    assert Blackboard.HEARTBEAT_TOOL_LOG_MAX == 50


# ------------------------------------------------------------ il MECCANISMO
# (review 27/08, finding 2-3): i test "a meta'" provano la proprieta', ma uno
# scrittore tmp + copyfile (che tronca e riscrive il bersaglio) li passava
# lo stesso; e il retry sul replace non era misurato da nessuno.

def test_la_scrittura_riuscita_non_apre_mai_il_file_vero_in_scrittura(bb, monkeypatch):
    import os as _os
    bersaglio = os.path.normpath(Blackboard.HEARTBEAT_PATH)
    vero_open = builtins.open
    aperture_w = []

    def open_spia(path, mode="r", *a, **k):
        if any(c in str(mode) for c in "wa+") and os.path.normpath(str(path)) == bersaglio:
            aperture_w.append(str(mode))
        return vero_open(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", open_spia)
    vero_replace = _os.replace
    replace = []

    def replace_spia(src, dst):
        replace.append((os.path.normpath(str(src)), os.path.normpath(str(dst))))
        return vero_replace(src, dst)

    monkeypatch.setattr(_os, "replace", replace_spia)
    bb.current_round = 2
    bb._write_heartbeat()
    assert aperture_w == [], "il file vero non si apre MAI in scrittura: %r" % aperture_w
    assert [d for _, d in replace] == [bersaglio], replace
    assert replace[0][0] != bersaglio, "il temporaneo e' un ALTRO file"
    assert _leggi(Blackboard.HEARTBEAT_PATH)["current_round"] == 2


def test_il_replace_che_cade_una_volta_viene_ritentato(bb, monkeypatch):
    """La PermissionError di Windows col lettore che tiene il file aperto:
    un tentativo solo la trasformerebbe in un heartbeat perso."""
    import os as _os
    vero_replace = _os.replace
    colpi = {"n": 0}

    def replace_zoppo(src, dst):
        colpi["n"] += 1
        if colpi["n"] == 1:
            raise PermissionError(5, "Access is denied (lettore col file aperto, simulato)")
        return vero_replace(src, dst)

    monkeypatch.setattr(_os, "replace", replace_zoppo)
    assert bb._scrivi_heartbeat_atomico('{"running": true, "current_round": 3}') is True
    assert colpi["n"] == 2
    assert _leggi(Blackboard.HEARTBEAT_PATH)["current_round"] == 3
    assert not any(f.endswith(".tmp")
                   for f in os.listdir(_cartella(Blackboard.HEARTBEAT_PATH)))


# ---------------------------------------------------------------- il LETTORE
# (review 27/08, finding 1): nella finestra del replace `open("r")` prende
# PermissionError e `get_agents_live` rispondeva «read error» come prima.

VIVO = '{"running": true, "current_round": 1, "updated_at": "%s"}'


def _adesso(delta_s=0):
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(seconds=delta_s)).isoformat(timespec="seconds")


def _api_su(tmp_path, monkeypatch, contenuto):
    import bellomberg.api.bellomberg_api as api
    p = tmp_path / "current_run.json"
    if contenuto is not None:
        p.write_text(contenuto, encoding="utf-8")
    monkeypatch.setattr(api, "AGENTS_LIVE_PATH", str(p))
    return api, p


def _open_che_inciampa(monkeypatch, p, quante, cosa):
    """Le prime `quante` `open(p, "r")` fanno `cosa` (eccezione o file finto),
    le altre leggono davvero."""
    vero = builtins.open
    colpi = {"n": 0}

    def finto(path, mode="r", *a, **k):
        if (os.path.normpath(str(path)) == os.path.normpath(str(p))
                and "r" in str(mode) and colpi["n"] < quante):
            colpi["n"] += 1
            r = cosa()
            if isinstance(r, BaseException):
                raise r
            return r
        return vero(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", finto)
    return colpi


def test_lettore_permission_error_transitorio_non_e_un_read_error(tmp_path, monkeypatch):
    api, p = _api_su(tmp_path, monkeypatch, VIVO % _adesso())
    colpi = _open_che_inciampa(monkeypatch, p, 1,
                               lambda: PermissionError(13, "Permission denied (finestra del replace)"))
    r = api.get_agents_live()
    assert r["running"] is True and r["current_round"] == 1, r
    assert colpi["n"] == 1


def test_lettore_file_a_meta_transitorio_viene_riletto(tmp_path, monkeypatch):
    api, p = _api_su(tmp_path, monkeypatch, VIVO % _adesso())
    _open_che_inciampa(monkeypatch, p, 1, lambda: io.StringIO('{"running": tr'))
    r = api.get_agents_live()
    assert r["running"] is True, r


@pytest.mark.parametrize('language,prefix', [('it', 'errore lettura'), ('en', 'Read error')])
def test_lettore_illeggibile_tre_volte_lo_dice_col_suo_nome(tmp_path, monkeypatch, language, prefix):
    api, p = _api_su(tmp_path, monkeypatch, VIVO % _adesso())
    colpi = _open_che_inciampa(monkeypatch, p, 99,
                               lambda: PermissionError(13, "Permission denied"))
    with language_context(language):
        r = api.get_agents_live()
    assert r["running"] is False
    assert r["heartbeat"] == "illeggibile", r
    assert r["message"].startswith(prefix) and "PermissionError" in r["message"], r
    assert colpi["n"] == 3, "tre letture, non una e non infinite"


@pytest.mark.parametrize('language,message', [('it', 'Nessuna run attiva.'), ('en', 'No active run.')])
def test_lettore_senza_file_resta_nessuna_run(tmp_path, monkeypatch, language, message):
    api, p = _api_su(tmp_path, monkeypatch, None)
    with language_context(language):
        assert api.get_agents_live() == {"running": False, "message": message}


@pytest.mark.parametrize('language,message', [('it', 'Nessuna run attiva.'), ('en', 'No active run.')])
def test_lettore_file_sparito_dopo_un_reset_e_nessuna_run_non_un_guasto(tmp_path, monkeypatch, language, message):
    """`POST /agents/live/reset` cancella il file: se arriva fra exists() e
    open(), tre riletture e poi «illeggibile» direbbero un guasto che non c'e'
    (review 27/08, seconda passata)."""
    api, p = _api_su(tmp_path, monkeypatch, VIVO % _adesso())
    vero = builtins.open

    def finto(path, mode="r", *a, **k):
        if os.path.normpath(str(path)) == os.path.normpath(str(p)) and "r" in str(mode):
            if p.exists():
                p.unlink()  # il reset arriva proprio qui
            raise FileNotFoundError(2, "No such file or directory")
        return vero(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", finto)
    with language_context(language):
        assert api.get_agents_live() == {"running": False, "message": message}


@pytest.mark.parametrize('language,prefix,object_word', [('it', 'errore lettura', 'oggetto'), ('en', 'Read error', 'object')])
def test_lettore_heartbeat_che_non_e_un_oggetto_e_illeggibile_dichiarato(tmp_path, monkeypatch, language, prefix, object_word):
    """JSON valido ma non un oggetto: prima l'endpoint lo RESTITUIVA com'era
    (il try interno inghiottiva l'AttributeError) e il frontend riceveva una
    lista al posto dello stato."""
    api, p = _api_su(tmp_path, monkeypatch, "[1, 2, 3]")
    with language_context(language):
        r = api.get_agents_live()
    assert isinstance(r, dict), r
    assert r["running"] is False and r["heartbeat"] == "illeggibile", r
    assert r["message"].startswith(prefix) and object_word in r["message"], r


@pytest.mark.parametrize('language,prefix', [('it', 'Errore lettura'), ('en', 'read error')])
def test_lettore_altro_errore_di_lettura_e_illeggibile_dichiarato(tmp_path, monkeypatch, language, prefix):
    """Il ramo esterno (un OSError fuori dalla tupla del retry) porta la stessa
    chiave: il frontend distingue SEMPRE «illeggibile» da «nessuna run»."""
    api, p = _api_su(tmp_path, monkeypatch, VIVO % _adesso())
    _open_che_inciampa(monkeypatch, p, 99, lambda: OSError(22, "Invalid argument (finto)"))
    with language_context(language):
        r = api.get_agents_live()
    assert r["running"] is False and r["heartbeat"] == "illeggibile", r
    assert prefix in r["message"] and "Invalid argument" in r["message"], r


def test_lettore_stale_warning_intatto(tmp_path, monkeypatch):
    api, p = _api_su(tmp_path, monkeypatch, VIVO % _adesso(700))
    r = api.get_agents_live()
    assert r["stale_warning"] is True and r["stale_seconds"] >= 700, r
