"""Test OFFLINE quick-win n.12 (26/07 sera, Opus 5): helper 500 unico `_err500`.

Prima: 64 rami `raise HTTPException(500, str(e))`, di cui solo 5 lasciavano
traccia nel log -> un endpoint moriva con una riga secca e zero materiale per
l'autopsia. E `str(e)` puo' essere VUOTO: 500 con detail "" = buco NON
dichiarato in casa (regola PM 14/07).

Qui si verifica che l'helper: (1) mette SEMPRE il tipo dell'eccezione nel
detail, anche quando il messaggio e' vuoto; (2) logga SEMPRE traceback + una
riga `[500] <endpoint>` per l'autopsia; (3) tiene `detail` una STRINGA (il
frontend la rende verbatim in 14 punti: un dict stamperebbe [object Object]);
(4) resta blindato alla pipe morta (changelog (40)); (5) copertura strutturale:
nessun ramo 500 nel file scavalca l'helper.

Zero rete, zero DB.
"""
import ast
import builtins

import pytest

import bellomberg.api.bellomberg_api as api


class _Vuota(Exception):
    """str(e) == "" : il caso che produceva un 500 con detail vuoto."""


def _pipe_morta(*args, **kwargs):
    raise OSError(22, "Invalid argument")


class _StderrMorto:
    def write(self, *a, **k):
        raise OSError(22, "Invalid argument")

    def flush(self):
        raise OSError(22, "Invalid argument")


def _alza(exc, *a, **kw):
    """chiama _err500 da DENTRO un except vero (come i call site)."""
    try:
        raise exc
    except Exception as e:
        return api._err500(e, *a, **kw)


# --- 1. tipo sempre presente -------------------------------------------------

def test_detail_porta_il_tipo_dell_eccezione():
    err = _alza(ValueError("provider esploso"), "get_news_macro")
    assert err.status_code == 500
    assert err.detail == "ValueError: provider esploso"


def test_messaggio_vuoto_non_produce_mai_detail_vuoto():
    # il caso regola 14/07: prima usciva un 500 con detail "" (buco muto)
    err = _alza(_Vuota(), "get_portfolio")
    assert err.detail == "_Vuota"
    assert err.detail.strip() != ""


def test_messaggio_solo_spazi_equivale_a_vuoto():
    err = _alza(RuntimeError("   "), "get_portfolio")
    assert err.detail == "RuntimeError"


def test_hint_resta_davanti_al_tipo():
    err = _alza(OSError("access denied"), "cancel_consigliere", "Failed to kill process")
    assert err.detail == "Failed to kill process — OSError: access denied"


# --- 2. autopsia: log sempre -------------------------------------------------

def test_logga_sempre_traceback_e_riga_di_autopsia(monkeypatch):
    visti, tracce = [], []
    monkeypatch.setattr(api, "_safe_print", lambda m: visti.append(m))
    monkeypatch.setattr(api, "_safe_trace", lambda: tracce.append(1))
    _alza(KeyError("ticker"), "get_market_quote")
    assert len(tracce) == 1, "il traceback deve finire nel log su OGNI ramo 500"
    assert len(visti) == 1
    assert visti[0].startswith("[500] ")
    assert "get_market_quote" in visti[0], "senza il nome dell'endpoint l'autopsia riparte da zero"
    assert "KeyError" in visti[0]


# --- 3. contratto col frontend ----------------------------------------------

def test_detail_e_sempre_una_stringa():
    # app/src legge err.response.data.detail e lo rende VERBATIM (14 punti):
    # qualunque forma strutturata stamperebbe "[object Object]" in pagina.
    for exc in (ValueError("x"), _Vuota(), OSError(22, "Invalid argument")):
        assert isinstance(_alza(exc, "qualsiasi").detail, str)


# --- 4. blindatura pipe morta (changelog (40)) -------------------------------

def test_non_propaga_con_pipe_morta(monkeypatch):
    monkeypatch.setattr(builtins, "print", _pipe_morta)
    monkeypatch.setattr("sys.stderr", _StderrMorto())
    err = _alza(ValueError("boom"), "get_news_macro")  # non deve propagare OSError
    assert err.detail == "ValueError: boom"


# --- 5. copertura strutturale del file --------------------------------------

def _sorgente_api():
    return open(api.__file__.replace(".pyc", ".py"), encoding="utf-8").read()


def _rami_500_diretti(tree):
    """raise HTTPException(500, ...) scritti a mano, che scavalcano l'helper.

    Copre ENTRAMBE le grafie: posizionale `HTTPException(500, ...)` e keyword
    `HTTPException(status_code=500, ...)`. La seconda oggi non esiste nel file,
    ma la guardia deve reggere anche a chi domani la usa (segnalato in review).
    """
    fuori = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)):
            continue
        if getattr(n.exc.func, "id", None) != "HTTPException":
            continue
        args = n.exc.args
        if args and isinstance(args[0], ast.Constant) and args[0].value == 500:
            fuori.append(n.lineno)
        for kw in n.exc.keywords:
            if kw.arg == "status_code" and isinstance(kw.value, ast.Constant) and kw.value.value == 500:
                fuori.append(n.lineno)
    return fuori


def _funzione_ospitante(tree):
    """riga -> nome della funzione PIU' INTERNA che la contiene."""
    owner = {}
    for f in ast.walk(tree):
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ln in range(f.lineno, (f.end_lineno or f.lineno) + 1):
                prev = owner.get(ln)
                if prev is None or f.lineno > prev[1]:
                    owner[ln] = (f.name, f.lineno)
    return {ln: v[0] for ln, v in owner.items()}


def _call_site_err500(tree):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_err500"]


def test_nessun_ramo_500_scavalca_lhelper():
    fuori = _rami_500_diretti(ast.parse(_sorgente_api()))
    assert fuori == [], f"rami 500 scritti a mano (usa _err500): righe {fuori}"


def test_ogni_chiamata_dichiara_un_where_non_vuoto():
    siti = _call_site_err500(ast.parse(_sorgente_api()))
    for n in siti:
        assert len(n.args) >= 2, f"_err500 senza `where` a riga {n.lineno}"
        w = n.args[1]
        assert isinstance(w, ast.Constant) and w.value.strip(), f"where vuoto a riga {n.lineno}"
    # 64 al lotto (50); 65 dal 26/07 sera-5 (Opus 5): +1 per GET /agents/scorecard
    # (voce I-3); 66 dal 26/07 sera-6 (Opus 5): +1 per PATCH /chat/sessions/{id}
    # (lotto F3, richiesta n.1 del frontend); 67 dal 12/08 (Fable 5): +1 per
    # GET /cash/movements (canale versamenti, changelog (70)). Il numero e' un
    # CENSIMENTO voluto: se cresce senza che nessuno lo dichiari, e' un ramo
    # 500 nuovo entrato di straforo. (Ha funzionato di nuovo il 12/08: ha
    # pescato l'endpoint cassa da solo. E di nuovo il 06/09, sul mio.)
    # 68 dal 06/09 (Opus 5, chat `bellomberg-90`, criterio (5) lotto B): +1 per
    # GET /mandato. L'API non ha nessun exception_handler, quindi ogni ramo del
    # mandato cattura per conto suo: senza, un guasto di lettura diventerebbe un
    # 500 muto che scavalca anche la riga di log.
    # 70 dal 06/09 pomeriggio (Opus 5, chat `bellomberg-c4`, criterio (5) lotto B):
    # +2 per PUT /mandato, che ha DUE rami di guasto inatteso e non uno — la
    # scrittura e la RILETTURA che la segue. Sono separati apposta: se si perde il
    # mandato, «non sono riuscito a scrivere» e «ho scritto ma non rileggo» mandano
    # il PM in due posti diversi. Il censimento ha pescato anche questo (era a 68).
    # 73 dal 09/09 (Codex GPT-6): guardia mandato della run e due anteprime
    # GET/POST. Ogni nuovo ramo continua a loggare causa e funzione reale.
    assert len(siti) == 73, f"call site attesi 73, trovati {len(siti)}"


def test_il_where_e_il_nome_vero_della_funzione_ospitante():
    # Il difetto pescato dalla review pre-commit del 26/07: il sito fatto a mano
    # diceva "cancel_consigliere_run", funzione che NON esiste — chi grepp[a] il
    # log dell'autopsia cercherebbe un fantasma. Un `where` che mente costa
    # esattamente quello che l'helper doveva far risparmiare.
    tree = ast.parse(_sorgente_api())
    owner = _funzione_ospitante(tree)
    bugiardi = [(n.lineno, n.args[1].value, owner.get(n.lineno))
                for n in _call_site_err500(tree)
                if isinstance(n.args[1], ast.Constant) and n.args[1].value != owner.get(n.lineno)]
    assert bugiardi == [], f"`where` diverso dalla funzione ospitante: {bugiardi}"


def test_ogni_chiamata_sta_dentro_un_except():
    # Fuori da un except, traceback.print_exc() stampa "NoneType: None": l'helper
    # continuerebbe a rispondere 500 ma il log d'autopsia sarebbe vuoto.
    tree = ast.parse(_sorgente_api())
    dentro = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Try):
            for h in n.handlers:
                for stmt in h.body:
                    dentro.update(range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1))
    fuori = [n.lineno for n in _call_site_err500(tree) if n.lineno not in dentro]
    assert fuori == [], f"_err500 chiamato FUORI da un except: righe {fuori}"
